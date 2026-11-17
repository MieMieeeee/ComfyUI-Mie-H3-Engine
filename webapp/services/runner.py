"""Run a render and keep track of it.

A job is a prompt submitted to ComfyUI. The web app does not wait for
it - ComfyUI owns the queue - so the job table here records what was
submitted and the status comes from ComfyUI's own history.

The queue is shared: a person submitting at 8188 and this app submitting
do not exclude each other. The plan's R21/R22 say the product layer
needs its own lock around "look at /queue + submit", and it must NOT be
held while waiting for an LLM. Here the lock only guards the submit
call itself.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
import uuid

from webapp import config
from webapp.services import (comfy, history, jsonfile, library, runname,
                             snapshot, workflow)

#: The submit lock. It covers "look at the queue, then submit" - the
#: window in which two submissions could interleave. It also covers the
#: name check, the reservation and the snapshot, so two requests with
#: the same run name cannot both get through.
_submit_lock = threading.Lock()

#: The run names being submitted right now. A name in here is taken.
_reserved: set = set()

#: The jobs this process has started: {job_id: record}.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

#: How many job records are kept on disk. The list is a log of what was
#: submitted, not an archive.
_JOB_HISTORY = 50


def _load_jobs() -> dict:
    """The job records of the last run of this app.

    The table used to live in memory only, so restarting the web app
    forgot every submission - including the ones that failed. A failed
    render that leaves no trace is the worst kind of trace.
    """
    try:
        data = json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    records = {}
    for item in data.get("jobs") or []:
        job_id = item.get("job_id")
        if job_id:
            records[job_id] = item
    return records


def _save_jobs() -> None:
    with _jobs_lock:
        records = list(_jobs.values())[-_JOB_HISTORY:]
    # Only what the list needs: the outputs are re-read from ComfyUI.
    # The provenance fields DO have to be saved - without the asset names
    # and their hashes, a restarted app cannot tell that the inputs
    # changed under a queued run.
    slim = [{key: record.get(key) for key in
             ("job_id", "number", "script", "run_name", "title", "attempt",
              "continued", "submitted_at",
              "status", "error", "nodes", "role_assets", "asset_hashes",
              "script_hash", "params", "snapshot_dir", "scene_asset")}
            for record in records]
    try:
        config.ensure_dirs()
        jsonfile.write_json(config.JOBS_FILE, {"jobs": slim})
    except OSError:
        pass  # a log we cannot write must not break the render


def _remember(record: dict) -> None:
    """Keep one record's latest state, in memory and on disk."""
    with _jobs_lock:
        previous = _jobs.get(record["job_id"])
        changed = (previous is None
                   or previous.get("status") != record.get("status")
                   or previous.get("error") != record.get("error"))
        _jobs[record["job_id"]] = dict(record)
    if changed:
        _save_jobs()


def _now() -> float:
    return time.time()


def _migrate_attempts() -> None:
    """Seed the version counter from the tasks that already exist.

    The counter of its own was introduced after the first versions had
    been handed out. Without this, taking the newest task out of the
    list would hand its number to the next one - the number would come
    from the records that are left, and the highest one is the one
    that was just removed.
    """
    with _jobs_lock:
        records = list(_jobs.values())
    highest = {}
    for record in records:
        script = record.get("script")
        try:
            attempt = int(record.get("attempt") or 0)
        except (TypeError, ValueError):
            continue
        if not script or not attempt:
            continue
        if attempt > highest.get(script, 0):
            highest[script] = attempt
    for script, attempt in highest.items():
        history.claim_attempt(script, attempt)


def next_attempt(script_name: str) -> int:
    """Which version of this script comes next.

    Every generation of one script is its own run, with its own folder
    on disk and its own result to keep. The number comes from a counter
    of its own, so it never goes back: taking a run out of the list
    must not hand its number to the next one, because that would reuse
    its folder and hide the new run behind the old one's "put away"
    mark.
    """
    with _jobs_lock:
        used = [record.get("attempt") or 0 for record in _jobs.values()
                if record.get("script") == script_name]
    highest = max(used) if used else 0
    return max(highest, history.last_attempt(script_name)) + 1


def _name_in_use(run_name: str) -> bool:
    """Is this run name being used right now?"""
    if run_name in _reserved:
        return True
    with _jobs_lock:
        for record in _jobs.values():
            if (record.get("run_name") == run_name
                    and record.get("status") in ("running", "queued")):
                return True
    return False


def _name_used_before(run_name: str) -> bool:
    """Has a run with this name been here before?"""
    with _jobs_lock:
        for record in _jobs.values():
            if record.get("run_name") == run_name:
                return True
    try:
        return any(run["run_name"] == run_name
                   for run in comfy.recent_runs())
    except Exception:  # noqa: BLE001 - the disk is not the point here
        return False


def _effective_run_name(script_name: str, run_name: str, attempt: int) -> str:
    """The technical name this generation gets.

    A name the author typed is the author's: it is used as it is. But
    a name that has been used before would write into the folder of
    the run that is already there, and overwrite it - so it gets its
    version number instead. A name that is in use RIGHT NOW is left
    alone, for the same-name check to refuse.
    """
    typed = runname.safe_run_name(run_name)
    if typed:
        if _name_in_use(typed):
            return typed
        if _name_used_before(typed):
            return runname.versioned_run_name(typed, attempt)
        return typed
    return runname.effective_run_name("", script_name, attempt)


def planned_run_name(script_name: str, run_name: str = "") -> dict:
    """The name and version a submission WOULD use right now.

    The page shows this, so it has to come from the same place the
    submission gets its name from - otherwise the page promises one
    thing and the run gets another.
    """
    attempt = next_attempt(script_name)
    return {
        "run_name": _effective_run_name(script_name, run_name, attempt),
        "attempt": attempt,
        "title": runname.work_title(script_name, attempt),
    }


def _check_reference_audio(script: dict, role_assets: dict) -> None:
    """Reference audio renders from the character's sample; no sample,
    no voice. Checked here (not just on the page) because the asset may
    have changed between the page's check and the submit (F08).
    """
    if not isinstance(script, dict) \
            or script.get("audio_mode") != "reference":
        return
    for role in script.get("roles") or []:
        asset = (role_assets or {}).get(role.get("id") or "") or ""
        if not asset:
            continue  # the missing-asset case is caught elsewhere
        detail = library.get_character(asset)
        if not detail.get("audios"):
            raise ValueError(
                "角色「%s」接的资产「%s」没有声音样本：参考配音要用它生成声音，"
                "先去资产页上传。" % (role.get("name") or role.get("id"), asset))


def submit(script_name: str, role_assets: dict[str, str], run_name: str = "",
           width: int = 960, height: int = 544, steps: int = 20,
           duration: int = 18, continue_run: bool = False,
           continue_from: str = "", scene_asset: str = "") -> dict:
    """Build the workflow for a script and queue it.

    Returns the job record. The render itself takes minutes; the caller
    polls :func:`job`.

    ``continue_run`` says: this is the SAME run as before - the author
    wants to carry on from where it stopped, into the same folder. Every
    other submission is a new version, with its own folder, so the
    previous result stays where it was.

    ``continue_from`` names the task to carry on from. Its inputs are
    the ones that task was given - the script it had pinned, its roles,
    its parameters - not whatever the library holds now: continuing a
    run means continuing THAT run.
    """
    client_id = "mie-web-%s" % uuid.uuid4().hex[:12]
    original = None
    if continue_run and continue_from:
        with _jobs_lock:
            original = _jobs.get(continue_from)
        if original is None:
            raise ValueError("找不到要继续的那个任务了，它可能已经被移出历史。")
        script_name = original.get("script") or script_name
        role_assets = original.get("role_assets") or role_assets
        params = original.get("params") or {}
        width = params.get("width", width)
        height = params.get("height", height)
        steps = params.get("steps", steps)
        duration = params.get("duration", duration)
        run_name = original.get("run_name") or run_name
        # 原任务固定的那份剧本：不是资产库里现在这份。
        script = {"json": _script_from_snapshot(
            original.get("snapshot_dir"), script_name)}
    else:
        script = library.get_storyboard(script_name)

    # Everything that can collide is inside one critical section: the
    # name check, the reservation, the snapshot and the queueing. Two
    # requests with the same name used to both pass the check and both
    # land in the queue, and they write one folder.
    with _submit_lock:
        # F08: the page's readiness check is a moment in time - the asset
        # may have changed since. Reference audio needs an actual sample
        # per role, and that is knowable here, right before the queue.
        _check_reference_audio(script["json"], role_assets)
        attempt = (original.get("attempt") or 1) if original \
            else next_attempt(script_name)
        if not original:
            history.claim_attempt(script_name, attempt)
        # One name for both the graph and the job record: the engine
        # takes it verbatim, so the record and the run on disk must
        # agree. A continuation keeps the old name exactly - that is
        # what makes it a continuation.
        if continue_run:
            effective = runname.safe_run_name(run_name)
        else:
            effective = _effective_run_name(script_name, run_name, attempt)
        _refuse_a_duplicate(effective, script_name)
        _reserve(effective, script_name)
        try:
            # The loader reads the library when the prompt RUNS, so the
            # script is pinned into a snapshot now: editing it while this
            # waits in the queue must not change what gets rendered.
            # A continuation keeps the ORIGINAL snapshot - it is the same
            # run, and it must see the same script.
            if original:
                snapshot_dir = original.get("snapshot_dir") or ""
            else:
                snapshot_dir = snapshot.write_script_snapshot(
                    effective, script_name, script["json"])
            graph = workflow.build(
                script_name, script["json"], role_assets, run_name=effective,
                width=width, height=height, steps=steps, duration=duration,
                library_dir=snapshot_dir,
                scene_asset=(original or {}).get("scene_asset") or scene_asset)
            result = comfy.submit(graph, client_id=client_id)
        except Exception as exc:
            _release(effective)
            # A submission that never reached the engine still HAPPENED:
            # the author pressed "render" and something went wrong. The
            # record says so, with the reason - otherwise the click
            # leaves no trace at all and the task list cannot tell them
            # apart from never having tried (2026-11-15 verification:
            # with ComfyUI down, the render left nothing behind).
            failed = {
                "job_id": "failed-%s" % uuid.uuid4().hex[:12],
                "number": None,
                "script": script_name,
                "run_name": effective,
                "title": runname.work_title(script_name, attempt),
                "attempt": attempt,
                "continued": bool(continue_run),
                "role_assets": role_assets,
                "scene_asset": scene_asset,
                "submitted_at": _now(),
                "status": "error",
                "error": "提交失败：%s" % exc,
                "nodes": 0,
                "params": {"width": width, "height": height, "steps": steps,
                           "duration": duration},
                "snapshot_dir": "",
                "finished_at": _now(),
            }
            _remember(failed)
            raise

    record = {
        "job_id": result.get("prompt_id"),
        "number": result.get("number"),
        "script": script_name,
        "run_name": effective,
        # The author's name for the work, and which version it is. The
        # run name is the engine's folder; this is what the list shows.
        "title": runname.work_title(script_name, attempt),
        "attempt": attempt,
        "continued": bool(continue_run),
        "role_assets": role_assets,
        "scene_asset": scene_asset,
        "submitted_at": _now(),
        "status": "queued",
        "error": "",
        "nodes": len(graph),
        # What this run is pinned to, so the task list can say whether
        # the inputs still are what they were when it was submitted.
        "script_hash": snapshot.script_fingerprint(script_name),
        "asset_hashes": {role_id: snapshot.asset_fingerprint("character", name)
                         for role_id, name in (role_assets or {}).items() if name},
        "params": {"width": width, "height": height,
                   "steps": steps, "duration": duration},
        "snapshot_dir": snapshot_dir,
    }
    with _jobs_lock:
        _jobs[record["job_id"]] = record
    # The reservation only had to cover the window between the check and
    # the record being in the table - from here on the running/queued
    # records keep the name taken. Holding it forever would refuse the
    # same name for good, and say "being submitted" about a run that
    # finished hours ago.
    _release(effective)
    _save_jobs()
    snapshot.prune(protect=_active_snapshots())
    return record


def _active_snapshots() -> set:
    """The snapshot directories the running and waiting tasks need.

    A queued task reads its snapshot when it STARTS, so the snapshot has
    to outlive the queue, not just the submission.
    """
    with _jobs_lock:
        records = list(_jobs.values())
    return {record.get("snapshot_dir") for record in records
            if record.get("status") in ("running", "queued")
            and record.get("snapshot_dir")}


def _refuse_a_duplicate(run_name: str, script_name: str) -> None:
    """One run name per script at a time.

    Two runs with the same name write the same folder, and the second
    one overwrites the first one's segments while it is still running.
    The names being submitted RIGHT NOW count as taken too: the check
    and the reservation happen in the same critical section, so two
    concurrent requests cannot both pass it.
    """
    if run_name in _reserved:
        raise ValueError(
            "同名任务 %r 正在提交中：等它完成，或者换一个片段名。" % run_name)
    # The table's lock, like every other walk of it: the page polls
    # exactly as a submission lands, and a walk that dies mid-stride
    # (the table changed under it) turns the submission into a 500.
    with _jobs_lock:
        records = list(_jobs.values())
    for record in records:
        if record.get("run_name") != run_name:
            continue
        if record.get("status") in ("running", "queued"):
            raise ValueError(
                "同名任务 %r 已经在跑（#%s）：两个运行会写同一个目录，"
                "先等它结束，或者换一个片段名。"
                % (run_name, record.get("number")))


def _reserve(run_name: str, script_name: str) -> None:
    """Claim a run name for the submission in flight."""
    _reserved.add(run_name)


def _release(run_name: str) -> None:
    """Give the name back when the submission did not happen."""
    _reserved.discard(run_name)


def job(job_id: str, queued: tuple = None, index: list = None) -> dict:
    """One job, with its current state read from ComfyUI.

    ``index`` is a scan of the output tree, shared by the callers
    that ask about many runs at once.
    """
    with _jobs_lock:
        record = _jobs.get(job_id)
    if record is None:
        return {"job_id": job_id, "status": "unknown"}
    record = dict(record)
    outputs = _apply_status(record, queued)
    _attach_artifacts(record, outputs, index=index)
    _remember(record)
    return record


def _apply_status(record: dict, queued: tuple = None) -> dict:
    """What is happening to this run, right now.

    A run that has finished does not change, so it is not asked about
    again: with fifty runs in the list, asking ComfyUI about each of
    them on every poll is fifty round trips for nothing.
    """
    job_id = record.get("job_id")
    if record.get("status") in ("success", "error", "interrupted", "partial"):
        # A finished run: the answer is already on the record.
        return {}
    entry = comfy.history(job_id)
    if not entry:
        # No history entry: the prompt is either still in flight or it
        # is gone. Only the queue can tell - a job that ComfyUI has
        # forgotten is NOT still running, and saying so is what keeps
        # a failed render from sitting in the list as "running" forever.
        if queued is None:
            queued = comfy.queue_ids()
        running, pending = queued
        if job_id in running:
            record["status"] = "running"
        elif job_id in pending:
            record["status"] = "queued"
        else:
            record["status"] = "interrupted"
            record["error"] = record.get("error") or (
                "ComfyUI 里已找不到这次运行：可能提交后立刻失败、被中断，"
                "或历史已被清理。")
        return {}
    status = entry.get("status", {})
    record["status"] = status.get("status_str", "success")
    interrupted = False
    failed = False
    for message in status.get("messages") or []:
        if message[0] == "execution_error":
            failed = True
            record["status"] = "error"
            record["error"] = message[1].get("exception_message", "")
        elif message[0] == "execution_interrupted":
            interrupted = True
    if interrupted and not failed:
        # ComfyUI reports an interrupted run as status_str "error" with
        # no exception - it was stopped, not broken. Saying "error" with
        # an empty reason would be a lie in both halves.
        record["status"] = "interrupted"
        record["error"] = ("这次运行被中断了（可能是在工作台里点了停止，"
                           "或 ComfyUI 被打断），不是渲染出错。")
    if record["status"] == "error" and not record.get("error"):
        record["error"] = "ComfyUI 报告这次运行失败，但没有给出具体原因。"
    outputs = entry.get("outputs") or {}
    texts = {}
    for node_id, node_out in outputs.items():
        pieces = node_out.get("text") or []
        if pieces:
            texts[node_id] = "".join(
                p if isinstance(p, str) else str(p) for p in pieces)
    record["texts"] = texts
    return outputs


#: Files already collected for a run, waiting to be attached.
_READY_ARTIFACTS: dict = {}


def _attach_artifacts(record: dict, outputs: dict = None,
                       index: list = None) -> None:
    """Put the delivery on the record: the film first, then the shots,
    then the engine's own files.

    The author wants the film, not the nineteen files that came with
    it.
    """
    run_name = record.get("run_name") or ""
    # The grouped history buckets the whole output tree once and hands
    # this run its files (one pass instead of one walk per run).
    artifacts = _READY_ARTIFACTS.pop(run_name, None)
    if artifacts is None:
        artifacts = comfy.collect_artifacts(run_name, index)
    for node_out in (outputs or {}).values():
        if node_out.get("images"):
            artifacts = artifacts + [
                {"name": image.get("filename", ""),
                 "relpath": "%s/%s" % (image.get("subfolder", ""),
                                       image.get("filename", "")),
                 "kind": "video" if image.get("type") == "output" else "image",
                 "role": "deliverable" if image.get("type") == "output"
                 else "technical",
                 "size": 0}
                for image in node_out["images"]
            ]
    record["artifacts"] = artifacts
    record["deliverable"] = next(
        (a for a in artifacts if a.get("role") == "deliverable"), None)
    record["segments"] = [a for a in artifacts if a.get("role") == "segment"]
    record["audio"] = [a for a in artifacts if a.get("role") == "audio"]
    record["technical"] = [a for a in artifacts if a.get("role") == "technical"]
    record["manifest"] = comfy.run_manifest(record.get("run_name") or "")
    return record


def all_records() -> list[dict]:
    """Every run the workbench knows about, newest first, WITHOUT the
    files.

    The files are the expensive part - the output tree holds thousands
    of them - and the history only needs them for the page it shows.
    """
    with _jobs_lock:
        records = list(_jobs.values())
    # One queue read for the whole list: a job ComfyUI has forgotten is
    # not running any more, and saying "running" would be a lie.
    try:
        queued = comfy.queue_ids()
    except Exception:  # noqa: BLE001 - ComfyUI unreachable: keep the list
        queued = (set(), set())
    live = []
    for record in records:
        if history.is_dismissed(record.get("run_name")):
            continue
        item = dict(record)
        _apply_status(item, queued)
        # 状态变了要写回任务表：页面说"已中断"，而内部还以为是
        # "排队中"，同名检查就会把"从这次继续"挡下来。
        _remember(item)
        item["favorite"] = history.is_favorite(item.get("run_name"))
        item["inputs_changed"] = _inputs_changed(item)
        live.append(item)

    known = {r.get("run_name") for r in live}
    for run in comfy.recent_runs():
        if run["run_name"] in known:
            continue
        if history.is_dismissed(run["run_name"]):
            continue
        live.append({
            # A synthetic id: the disk runs have no prompt id, and an
            # empty one would collide in the page's x-for key.
            "job_id": "disk:" + run["run_name"],
            "run_name": run["run_name"],
            "title": run["run_name"],
            "status": run["status"],
            "script": "",
            "video": run["video"],
            "clips": run["clips"],
            "favorite": history.is_favorite(run["run_name"]),
            "from_disk": True,
            "inputs_changed": [],
        })
    live.sort(key=lambda r: r.get("submitted_at") or r.get("finished_at") or 0,
              reverse=True)
    return live


#: Which statuses each filter means.
STATUS_FILTERS = {
    "running": ("running", "queued"),
    "done": ("success", "partial"),
    "failed": ("error", "interrupted", "empty"),
}


def jobs(page: int = 1, per_page: int = 20, favorites_only: bool = False,
         status: str = "", query: str = "", grouped: bool = False) -> dict:
    """The task history, ONE PAGE at a time.

    The history grows without bound - every render is a row - and the
    files of every row make the answer heavy. The list is cut into
    pages here, and only the rows on the page are given their files.

    The filter is applied BEFORE the page is cut: filtering after it
    would hide the runs that are on a later page, and the total would
    still count them.

    ``grouped`` (U06) pages by WORK instead of by attempt: one run_name
    is one work, its attempts ride inside it, and the totals count
    works. A search ``query`` matches the work's run name, script or
    title and keeps whole works together.
    """
    records = all_records()
    active = sum(1 for r in records if r.get("status") in ("running", "queued"))
    if favorites_only:
        records = [r for r in records if r.get("favorite")]
    wanted = STATUS_FILTERS.get(str(status or ""))
    if wanted:
        records = [r for r in records if r.get("status") in wanted]

    if grouped:
        return _jobs_grouped(records, page, per_page, active, query)

    total = len(records)
    per_page = max(1, int(per_page or 20))
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    window = records[(page - 1) * per_page: page * per_page]
    # One walk of the output tree for the whole page: the tree holds
    # thousands of files and the list asks about every run at once.
    index = comfy.scan_output()
    for record in window:
        _attach_artifacts(record, index=index)
    return {"items": window, "total": total, "page": page, "pages": pages,
            "per_page": per_page, "active": active}


def _jobs_grouped(records: list, page: int, per_page: int, active: int,
                  query: str) -> dict:
    """The same history, told as WORKS: one run_name, many attempts.

    The attempt rows stay intact (continue / remake / dismiss act on an
    attempt); the work card carries the finished film, the counts and
    the collapsed-at-first attempt list. Favorites follow the attempt
    the star was set on - a starred attempt keeps its work on the
    favorites page.
    """
    needle = str(query or "").strip().lower()
    groups: dict = {}
    order: list = []
    for record in records:
        run_name = record.get("run_name") or ""
        if not run_name:
            continue
        if needle and not any(
                needle in str(field or "").lower()
                for field in (run_name, record.get("script"),
                              record.get("title"), record.get("error"))):
            continue
        group = groups.get(run_name)
        if group is None:
            group = groups[run_name] = {"run_name": run_name, "attempts": [],
                                        "favorite": False}
            order.append(run_name)
        group["attempts"].append(record)
        if record.get("favorite"):
            group["favorite"] = True

    works = []
    # 作品按“最新一次尝试的时间”倒序：最近的作品在最上面。
    order.sort(key=lambda name: max(
        r.get("submitted_at") or 0 for r in groups[name]["attempts"]),
        reverse=True)
    for run_name in order:
        group = groups[run_name]
        attempts = sorted(group["attempts"],
                          key=lambda r: r.get("submitted_at") or 0,
                          reverse=True)
        newest = attempts[0]
        finished = next((a for a in attempts
                         if (a.get("artifacts") or []
                             and any(x.get("kind") == "video"
                                     for x in a["artifacts"]))), None)
        statuses = sorted({a.get("status") or "" for a in attempts})
        works.append({
            "run_name": run_name,
            "script": newest.get("script") or "",
            "title": newest.get("title") or run_name,
            "attempt_count": len(attempts),
            "statuses": statuses,
            "latest_status": newest.get("status") or "",
            "favorite": group["favorite"],
            "finished_at": newest.get("finished_at") or 0,
            "submitted_at": newest.get("submitted_at") or 0,
            "attempts": attempts,
            "job_id": attempts[0].get("job_id") or "",
        })

    total = len(works)
    per_page = max(1, int(per_page or 10))
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    window = works[(page - 1) * per_page: page * per_page]
    # The files of the page's works: ONE pass over the output tree,
    # bucketed by run (per-run walks made a 200-record page take seconds).
    index = comfy.scan_output()
    buckets = comfy.group_artifacts_by_run(index)
    attempt_total = 0
    for work in window:
        for record in work["attempts"]:
            run_name = record.get("run_name") or ""
            prepared = buckets.get(run_name)
            if prepared is None:
                prepared = comfy.collect_artifacts(run_name, index)
            _READY_ARTIFACTS[run_name] = prepared
            _attach_artifacts(record, index=index)
            attempt_total += 1
        delivered = next((a for a in work["attempts"]
                          if any(x.get("kind") == "video"
                                 for x in (a.get("artifacts") or []))), None)
        work["film"] = (delivered and delivered["artifacts"]
                        and next((x["relpath"] for x in delivered["artifacts"]
                                  if x.get("kind") == "video"), "")) or ""
        work["audios"] = sorted({x["relpath"]
                                 for a in work["attempts"]
                                 for x in (a.get("artifacts") or [])
                                 if x.get("kind") == "audio"})[:6]
    return {"groups": window, "total": total, "page": page, "pages": pages,
            "per_page": per_page, "active": active,
            "attempts": attempt_total}


def delete_record(job_id: str, delete_files: bool = False) -> dict:
    """Take a run out of the history.

    The record goes, and the run is marked as put away so the engine's
    output folder does not put it straight back into the list. The
    FILES stay on disk unless the author asks for them to go as well -
    a film is the one thing here that cannot be written again.

    The version number is NOT given back: the next generation of this
    script gets a fresh number, so it cannot land in the folder of the
    run that was just put away.
    """
    run_name = ""
    with _jobs_lock:
        record = _jobs.get(job_id)
        if record is not None:
            run_name = record.get("run_name") or ""
            _jobs.pop(job_id, None)
    if not run_name and job_id.startswith("disk:"):
        # 磁盘任务的编号是可以编的：只有真的在清单里的那次运行
        # 才能被移出历史。这次磁盘走查在锁外做：锁护的是任务表，
        # 不该拿它等硬盘。
        name = job_id[len("disk:"):]
        if name not in {r["run_name"] for r in comfy.recent_runs()}:
            raise ValueError("这条任务不在历史里。")
        run_name = name
    if not run_name:
        raise ValueError("这条任务不在历史里。")
    history.dismiss(run_name)
    _save_jobs()
    removed = []
    if delete_files:
        # 删的是这一次的文件清单，不是"名字开头像"的文件：另一个
        # 版本的名字是这一个的前缀，按前缀删会把它一起删掉。
        relpaths = [a.get("relpath") for a in
                    comfy.collect_artifacts(run_name)
                    if a.get("relpath")]
        removed = comfy.delete_run_files(run_name, relpaths)
    return {"ok": True, "run_name": run_name,
            "files_removed": removed,
            "files_kept": not delete_files}


def _inputs_changed(record: dict) -> list:
    """The assets that changed since this run was submitted.

    The script is pinned into a snapshot, so it cannot change under the
    render. The assets cannot be pinned (the MieNodes loader ignores
    ``library_dir``), so they are watched by content hash instead.
    """
    if record.get("status") not in ("running", "queued"):
        return []
    try:
        return snapshot.changed_assets(
            "character", record.get("role_assets") or {},
            record.get("asset_hashes") or {})
    except Exception:  # noqa: BLE001 - a hash failure is not a verdict
        return []


def plan_preview(script_name: str, role_assets: dict[str, str],
                 scene_asset: str = "") -> dict:
    """The plan a run WOULD use, without running anything.

    The plan is assembled by the same builder the render uses, so what the
    preview shows is what the render will do.
    """
    script = library.get_storyboard(script_name)
    graph = workflow.build(
        script_name, script["json"], role_assets, run_name="preview",
        scene_asset=scene_asset)
    plan_node = next(
        (n for n in graph.values()
         if n["class_type"].endswith("ChainPlanModern|MieH3")), None)
    literal = plan_node["inputs"]["plan_json"] if plan_node else "{}"
    return {
        "script": script_name,
        "roles": workflow.script_roles(script["json"]),
        "fallback_plan": literal,
        "preview": script["preview"],
        "requirements": script["requirements"],
        "nodes": len(graph),
    }


# The jobs of the last run of this app are restored at import time, so a
# render that failed before the restart is still on the list - with the
# failure - instead of vanishing without a trace.
_jobs.update(_load_jobs())
_migrate_attempts()


def _script_from_snapshot(snapshot_dir: str, script_name: str) -> dict:
    """The script a task had pinned, read back from its snapshot.

    A continuation has to see the script the original task was given.
    Reading the library instead would quietly change what is being
    rendered - and the run's folder would hold a mix of two different
    scripts.
    """
    from mie_craft.assets import script_asset as _script_asset

    folder = str(snapshot_dir or "")
    if not folder:
        raise ValueError("这个任务没有固定剧本快照，无法原样继续："
                         "请从剧本页重新发起一次生成。")
    if not pathlib.Path(folder).is_dir():
        raise ValueError("这个任务固定的那份剧本已经不在了，无法原样继续："
                         "请从剧本页重新发起一次生成。")
    try:
        cleaned, _path = _script_asset.load_storyboard(folder, script_name)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("读不回这个任务固定的那份剧本，无法原样继续：%s"
                         % exc) from exc
    return cleaned
