"""The shot review gate, as the workbench sees it.

The engine owns the review. Its Review Gate node pauses a run after a
saved shot, publishes a pending review (token, scene, candidates,
native video previews) and waits for a decision; its own browser UI
drives that decision. The workbench does not re-implement any of it:
it asks the engine's own routes what is pending and hands the
decisions back to them, so the engine's rules - the candidate must be
part of this batch, one decision per review, the project's ownership
fence - apply to the workbench exactly as they apply to the engine's
own UI.

Two things are decided here, and only these two:

* which pending review belongs to which attempt of this workbench
  (the engine publishes a prompt id and a run name; both have to
  match), and
* which decisions this workbench already sent, so that a repeated or
  stale click cannot apply the same decision twice.
"""

from __future__ import annotations

import json
import threading
import time

from webapp.services import comfy, settings

#: The engine's own routes. Nothing else may answer a review.
REVIEWS_ROUTE = "/minimax_h3_context_loop/reviews"
REVIEW_ROUTE = "/minimax_h3_context_loop/review"
CANDIDATE_BATCH_ROUTE = "/minimax_h3_context_loop/review-candidate-batch"
CHECKPOINTS_ROUTE = "/minimax_h3_context_loop/checkpoints"

#: The decisions the workbench offers. They are the engine's own
#: actions: "approve" takes the selected candidate and lets the chain
#: continue, "reroll" re-samples the same shot with a new seed.
ACTIONS = ("approve", "reroll")

#: How many candidates the engine may generate for one shot.
MAX_CANDIDATES = 20

#: The setting key in the settings file.
SETTINGS_KEY = "review"

_lock = threading.Lock()
#: The decisions this process already sent: {token: {...}}.
_decided: dict = {}

#: The pending-review list is cached for a couple of seconds: the
#: history page polls every three seconds and the engine's own route
#: walks the run folders to find the durable snapshots.
#:
#: The cache belongs to ONE engine: a changed URL is a different engine,
#: and its reviews - or its silence - must not be served out of the old
#: one's cache.
_cache: dict = {"url": "", "at": 0.0, "value": [], "error": ""}
_CACHE_TTL = 2.0

#: How long a FAILED read is remembered. The history page polls every
#: few seconds; a disconnected engine must not be paid for on every
#: poll - one timeout, and then the answer comes from the cache until
#: the backoff is over.
_ERROR_TTL = 15.0

#: How long the engine gets to answer the pending-review question.
#: The engine is on the same machine and the question is cheap, but a
#: disconnected one would otherwise hold the whole history page for
#: the full timeout - on every poll.
READ_TIMEOUT = 1.0


class ReviewError(ValueError):
    """A review request the workbench refuses (or the engine refused)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        #: "decided" / "stale" are refusals of a request that has already
        #: been answered; "invalid" is a request that was never right.
        self.code = str(code or "invalid")


class ReviewUnavailable(RuntimeError):
    """The engine's review routes could not be reached."""


def clear_cache() -> None:
    """Forget the cached pending-review list."""
    with _lock:
        _cache["url"] = ""
        _cache["at"] = 0.0
        _cache["value"] = []
        _cache["error"] = ""


# --------------------------------------------------------------------------- #
# the opt-in setting
# --------------------------------------------------------------------------- #

def review_settings() -> dict:
    """The saved review setting.

    The default is OFF: an unattended render must not stop and wait for
    a human. The author turns it on deliberately, and the choice is
    remembered across restarts.
    """
    saved = settings.get(SETTINGS_KEY)
    saved = saved if isinstance(saved, dict) else {}
    enabled = bool(saved.get("enabled"))
    try:
        count = int(saved.get("candidate_count") or 1)
    except (TypeError, ValueError):
        count = 1
    return {"enabled": enabled,
            "candidate_count": max(1, min(MAX_CANDIDATES, count))}


def save_review_settings(enabled: bool, candidate_count: int = 1) -> dict:
    """Persist the review setting; returns what was stored."""
    try:
        count = int(candidate_count or 1)
    except (TypeError, ValueError):
        count = 1
    value = {"enabled": bool(enabled),
             "candidate_count": max(1, min(MAX_CANDIDATES, count))}
    settings.set_value(SETTINGS_KEY, value)
    return value


# --------------------------------------------------------------------------- #
# the engine's pending reviews
# --------------------------------------------------------------------------- #

def live_reviews(cached: bool = True, timeout: float = READ_TIMEOUT) -> list:
    """Every pending review the engine knows about.

    The engine answers with the live ones and, after a restart, with
    the durable snapshots it recovered from disk. A durable one is
    read-only inventory: its candidates can still be looked at, but no
    decision can be applied to it.

    The read is short-timeout on purpose: the history page polls every
    few seconds, and an engine that is down must not make the page
    wait for the whole timeout on every poll. A failed read is
    remembered for :data:`_ERROR_TTL` seconds, so the polls in between
    answer from the cache instead of paying the timeout again.
    """
    now = time.time()
    url = comfy.base_url()
    if cached:
        with _lock:
            age = now - _cache["at"]
            ttl = _ERROR_TTL if _cache.get("error") else _CACHE_TTL
            if _cache.get("url") == url and age < ttl:
                if _cache.get("error"):
                    raise ReviewUnavailable(str(_cache["error"]))
                return list(_cache["value"])
    try:
        status, body = comfy.native_request(REVIEWS_ROUTE, timeout=timeout)
    except comfy.ComfyUnavailable as exc:
        with _lock:
            _cache["url"] = url
            _cache["at"] = now
            _cache["value"] = []
            _cache["error"] = "引擎的审片接口没有应答：%s" % exc
        raise ReviewUnavailable(str(_cache["error"])) from exc
    if status != 200:
        with _lock:
            _cache["url"] = url
            _cache["at"] = now
            _cache["value"] = []
            _cache["error"] = "引擎的审片接口拒绝了这次查询（HTTP %s）。" % status
        raise ReviewUnavailable(str(_cache["error"]))
    reviews = [item for item in (body.get("reviews") or [])
               if isinstance(item, dict)]
    with _lock:
        _cache["url"] = url
        _cache["at"] = now
        _cache["value"] = list(reviews)
        _cache["error"] = ""
    return reviews


def belongs_to(review: dict, record: dict) -> bool:
    """Is this pending review the one THIS attempt is waiting on?

    Two things have to match. The run name says which project it is;
    the prompt id says which attempt inside that project. A continued
    attempt shares the run name with the attempt it continues, so the
    run name alone is not enough - and an engine that does not publish
    the prompt id leaves only the run name, which is then accepted only
    for an attempt that is still live (a pending review means the run
    is waiting for a human, so it is running).
    """
    run_name = str(record.get("run_name") or "")
    if not run_name or str(review.get("run_name") or "") != run_name:
        return False
    prompt_id = str(review.get("prompt_id") or "")
    job_id = str(record.get("job_id") or "")
    if prompt_id and job_id:
        return prompt_id == job_id
    return str(record.get("status") or "") in ("running", "queued")


def pending_for(record: dict, reviews: list = None) -> dict:
    """The pending review of this attempt, or {}."""
    if reviews is None:
        try:
            reviews = live_reviews()
        except ReviewUnavailable:
            return {}
    for review in reviews:
        if belongs_to(review, record):
            return review
    return {}


def _media_url(video: dict) -> str:
    """The workbench's own URL for one native video preview."""
    if not isinstance(video, dict):
        return ""
    filename = str(video.get("filename") or "")
    if not filename:
        return ""
    subfolder = str(video.get("subfolder") or "").strip("/")
    return "/media/%s/%s" % (subfolder, filename) if subfolder \
        else "/media/%s" % filename


def checkpoint_videos(run_name: str) -> dict:
    """The saved checkpoint videos of one run, keyed by "scene:revision".

    A durable review (recovered after a restart) has no live preview:
    its media lives in the saved segment/checkpoint inventory, and the
    engine's own checkpoint listing is where that inventory is.
    """
    try:
        status, body = comfy.native_request(
            CHECKPOINTS_ROUTE + "?run_name=" + str(run_name), timeout=5.0)
    except comfy.ComfyUnavailable:
        return {}
    if status != 200:
        return {}
    found = {}
    for item in (body.get("checkpoints") or []):
        if not isinstance(item, dict):
            continue
        try:
            scene = int(item.get("scene"))
        except (TypeError, ValueError):
            continue
        revision = str(item.get("revision") or "")
        video = item.get("video")
        if revision and isinstance(video, dict):
            found["%d:%s" % (scene, revision)] = video
    return found


def summarize(review: dict, record: dict = None) -> dict:
    """The review as the history page needs it.

    Only the fields the page shows: which shot is waiting, which
    candidates exist, the native video preview of each candidate and
    whether a decision can still be applied.
    """
    if not review:
        return {}
    candidates = []
    videos = {}
    durable = bool(review.get("durable"))
    if durable:
        videos = checkpoint_videos(str(review.get("run_name") or ""))
    for item in (review.get("candidates") or []):
        if not isinstance(item, dict):
            continue
        video = item.get("video")
        if durable and not isinstance(video, dict):
            try:
                scene = int(review.get("clip_index"))
            except (TypeError, ValueError):
                scene = 0
            video = videos.get("%d:%s" % (scene, str(item.get("revision") or "")))
        candidates.append({
            "number": int(item.get("number") or 0),
            "revision": str(item.get("revision") or ""),
            "seed": str(item.get("seed") or ""),
            "created_at": str(item.get("created_at") or ""),
            "prompt": str(item.get("scene_prompt") or ""),
            "frames": int(item.get("raw_frames") or 0),
            "has_audio": bool(item.get("has_audio")),
            "warning": str(item.get("warning") or ""),
            "video": video if isinstance(video, dict) else None,
            "url": _media_url(video),
        })
    actionable = bool(review.get("actionable", True)) and not durable
    return {
        "token": str(review.get("token") or ""),
        "actionable": actionable,
        "durable": durable,
        "run_name": str(review.get("run_name") or ""),
        "job_id": str(record.get("job_id") or "") if record else "",
        "shot": {
            "index": review.get("clip_index"),
            "count": review.get("clip_count"),
            "id": str(review.get("shot_id") or ""),
            "prompt": str(review.get("scene_prompt") or ""),
            "seed": str(review.get("seed") or ""),
            "frames": int(review.get("raw_frames") or 0),
            "seconds": review.get("duration_seconds"),
        },
        "candidates": candidates,
        "candidate_count": int(review.get("candidate_count") or 0),
        "candidate_generated_count": len(candidates),
        "candidate_generation_complete": bool(
            review.get("candidate_generation_complete")),
        "candidate_batch_active": bool(review.get("candidate_batch_active")),
        "pending_decision": bool(review.get("pending_decision", True)),
        "warning": str(review.get("warning") or ""),
        "deadline": review.get("deadline"),
        "server_now": review.get("server_now"),
        "recovery_instructions": str(review.get("recovery_instructions") or ""),
    }


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #

def _candidate_revisions(review: dict) -> list:
    return [str(item.get("revision") or "")
            for item in (review.get("candidates") or [])
            if isinstance(item, dict) and item.get("revision")]


def already_decided(token: str) -> dict:
    """The decision this workbench already sent for this token, or {}."""
    with _lock:
        return dict(_decided.get(str(token)) or {})


def decide(record: dict, token: str, action: str,
           candidate_revision: str = "") -> dict:
    """Hand one decision to the engine's own review route.

    The workbench checks three things before it posts anything: that
    the token belongs to a review of THIS attempt, that the candidate
    is part of that review's batch, and that this workbench has not
    already decided this review. The engine then applies its own rules
    on top - one decision per review, the project's ownership fence.
    """
    token = str(token or "")
    action = str(action or "")
    if action not in ACTIONS:
        raise ReviewError("工作台只支持两种决定：采纳候选、重掷这一镜。")
    if not token:
        raise ReviewError("没有说清是哪一次审片。")
    done = already_decided(token)
    if done:
        raise ReviewError("这次审片已经决定过了（%s），不会重复执行。"
                          % (done.get("action") or "已处理"), "decided")

    try:
        reviews = live_reviews(cached=False)
    except ReviewUnavailable as exc:
        raise ReviewError(str(exc)) from exc

    review = None
    for item in reviews:
        if str(item.get("token") or "") != token:
            continue
        if record is not None and not belongs_to(item, record):
            # The token exists, but it belongs to another run or another
            # attempt: it is not this workbench's to decide.
            raise ReviewError(
                "这次审片不属于当前这条任务，拒绝代它做决定。")
        review = item
        break
    if review is None:
        raise ReviewError(
            "这次审片已经不在等待中：可能已经决定过，或者引擎重启后只剩存档。",
            "stale")
    if review.get("durable") or review.get("actionable") is False:
        raise ReviewError(
            "这次审片只剩存档（引擎重启后恢复的），只能人工从检查点恢复，"
            "不能再提交决定。", "stale")

    candidates = _candidate_revisions(review)
    revision = str(candidate_revision or "")
    if action == "approve":
        if candidates:
            if not revision:
                if len(candidates) != 1:
                    raise ReviewError("先选一个候选，再决定要不要它。")
                revision = candidates[0]
            if revision not in candidates:
                raise ReviewError("选中的候选不属于这次审片。")
        elif revision:
            raise ReviewError("这次审片没有候选可采纳。")

    payload = {
        "token": token,
        "action": action,
        "scene_prompt": str(review.get("scene_prompt") or ""),
        "seed": str(review.get("seed") or ""),
    }
    if review.get("basic_prompt"):
        payload["basic_prompt"] = str(review.get("basic_prompt"))
    if action == "approve":
        payload["candidate_revision"] = revision
    else:
        # A reroll re-samples THE SAME shot: the prompt it was waiting
        # with, the length it was waiting with, a new seed.
        frames = int(review.get("raw_frames") or 0)
        if frames > 0:
            payload["length"] = frames

    try:
        status, body = comfy.native_request(
            REVIEW_ROUTE, payload, method="POST")
    except comfy.ComfyUnavailable as exc:
        raise ReviewError("引擎的审片接口没有应答：%s" % exc) from exc
    if status == 409:
        raise ReviewError(
            str(body.get("error") or "这次审片已经决定过了。"), "decided")
    if status == 423:
        raise ReviewError(
            str(body.get("error")
                or "这次运行在引擎侧被别的流程占用，工作台没有它的凭据。"),
            "owned")
    if status != 200:
        raise ReviewError(
            str(body.get("error")
                or "引擎拒绝了这次决定（HTTP %s）。" % status))
    with _lock:
        _decided[token] = {"action": action, "candidate_revision": revision,
                            "job_id": str((record or {}).get("job_id") or ""),
                            "at": time.time()}
    clear_cache()
    result = dict(body)
    result.setdefault("ok", True)
    result["action"] = action
    return result


# --------------------------------------------------------------------------- #
# assembling what was already accepted
# --------------------------------------------------------------------------- #

def saved_checkpoints(run_name: str) -> list:
    """The engine's saved checkpoints of one run."""
    try:
        status, body = comfy.native_request(
            CHECKPOINTS_ROUTE + "?run_name=" + str(run_name), timeout=20.0)
    except comfy.ComfyUnavailable as exc:
        raise ReviewUnavailable(
            "引擎的检查点接口没有应答：%s" % exc) from exc
    if status == 404:
        raise ReviewError(
            "这个引擎版本没有检查点恢复接口（HTTP 404），"
            "只能用引擎自己的界面拼片。")
    if status != 200:
        raise ReviewError(
            str(body.get("error")
                or "引擎读不出这次运行的检查点（HTTP %s）。" % status))
    return [item for item in (body.get("checkpoints") or [])
            if isinstance(item, dict)]


def accepted_lineage(checkpoints: list) -> list:
    """The saved takes that can be joined: scene 1..N, no gaps.

    A scene that was never saved (or whose file is gone) ends the
    chain: the engine joins a contiguous prefix, and joining past a
    hole would produce a film with a missing shot in it.
    """
    by_scene = {}
    for item in checkpoints:
        try:
            scene = int(item.get("scene"))
        except (TypeError, ValueError):
            continue
        revision = str(item.get("revision") or "")
        if scene < 1 or not revision:
            continue
        if item.get("ready") is False:
            continue
        by_scene.setdefault(scene, revision)
    lineage = []
    scene = 1
    while scene in by_scene:
        lineage.append({"scene": scene, "revision": by_scene[scene]})
        scene += 1
    return lineage
