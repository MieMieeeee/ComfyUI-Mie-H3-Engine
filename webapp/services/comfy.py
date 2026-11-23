"""The ComfyUI client: status, queue, submit, history, files.

The web app never starts ComfyUI - the launcher does that (see
F:/Mie-Skills/AGENTS.md). The LAN box is often off, so every call has to
fail soft: a web app that cannot reach 8188 must still answer, saying
so.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from webapp import config

#: Long enough for a model load, short enough that a dead box answers
#: quickly.
TIMEOUT = 20


class ComfyUnavailable(RuntimeError):
    """ComfyUI could not be reached."""


def _request(url: str, payload=None, method=None, timeout=TIMEOUT):
    """One HTTP call. Raises ComfyUnavailable on any transport failure."""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise ComfyUnavailable("HTTP %s %s: %s" % (exc.code, exc.reason, body[:400]))
    except Exception as exc:  # noqa: BLE001 - transport failures are one bucket
        raise ComfyUnavailable("%s: %s" % (type(exc).__name__, exc))


def _json(url: str, payload=None, method=None, timeout=TIMEOUT):
    raw = _request(url, payload, method, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def base_url() -> str:
    """The ComfyUI address, overridable from the app's settings."""
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # A settings file that cannot be read is not a reason to fail:
        # the built-in address is the fallback.
        saved = {}
    return str(saved.get("comfyui_url") or config.COMFYUI_URL).rstrip("/")


def status() -> dict:
    """Is ComfyUI up, and what is it serving?"""
    try:
        data = _json(base_url() + "/system_stats")
    except ComfyUnavailable as exc:
        return {"online": False, "error": str(exc), "url": base_url()}
    if not data:
        return {"online": False, "error": "no answer", "url": base_url()}
    system = data.get("system", {})
    devices = data.get("devices") or []
    return {
        "online": True,
        "url": base_url(),
        "comfyui_version": system.get("comfyui_version"),
        "python_version": system.get("python_version", "").split()[0],
        "ram_total": system.get("ram_total"),
        "ram_free": system.get("ram_free"),
        "devices": [
            {"name": d.get("name"), "vram_total": d.get("vram_total"),
             "vram_free": d.get("vram_free")}
            for d in devices
        ],
    }


def queue() -> dict:
    """What is running and what is waiting."""
    try:
        data = _json(base_url() + "/queue")
    except ComfyUnavailable as exc:
        return {"running": [], "pending": [], "error": str(exc)}
    if not data:
        return {"running": [], "pending": [], "error": "no answer"}
    return {
        "running": [item[1] for item in data.get("queue_running", [])],
        "pending": [item[1] for item in data.get("queue_pending", [])],
    }


def submit(prompt: dict, client_id: str = "") -> dict:
    """Queue a prompt. Returns {prompt_id, number}."""
    url = base_url()
    registry = object_info()
    if not registry:
        raise ComfyUnavailable("ComfyUI returned an invalid node registry")
    missing = sorted({node["class_type"] for node in prompt.values()} - registry.keys())
    if missing:
        raise ComfyUnavailable("ComfyUI 缺少工作流节点: " + ", ".join(missing))
    body = {"prompt": prompt, "client_id": client_id}
    result = _json(url + "/prompt", body, timeout=180)
    if not result or "prompt_id" not in result:
        raise ComfyUnavailable("the prompt was rejected: %s" % json.dumps(
            result, ensure_ascii=False)[:600])
    # The queue answer we may be holding describes the queue BEFORE this
    # prompt was added: it cannot know about a run that did not exist
    # yet. Judging the new run by it would call it "gone" while it is
    # still running - and a run that is called gone is never asked about
    # again. So the remembered answer goes, and the next read is a fresh
    # one that includes this prompt.
    forget_queue_cache()
    return result


#: The node registry is a big answer (every node of every plugin), and
#: the page asks for it whenever it wants to know which models exist.
#: It is kept for a few seconds: the page polls, and the list of
#: installed models does not change between two polls.
_REGISTRY_TTL = 5.0
_registry_cache: dict = {"at": 0.0, "value": {}}


def object_info() -> dict:
    """ComfyUI's node registry: what every node accepts.

    The answer is empty when ComfyUI cannot be reached - the caller
    decides what to do about that. It is kept for a few seconds, so a
    page that polls does not ask ComfyUI the same heavy question over
    and over.
    """
    now = time.time()
    if now - _registry_cache["at"] < _REGISTRY_TTL:
        return _registry_cache["value"]
    try:
        data = _json(base_url() + "/object_info")
    except ComfyUnavailable:
        _registry_cache["at"] = now
        _registry_cache["value"] = {}
        return {}
    value = data if isinstance(data, dict) else {}
    _registry_cache["at"] = now
    _registry_cache["value"] = value
    return value


def native_request(path: str, payload=None, method: str = "GET",
                   timeout: float = TIMEOUT) -> tuple:
    """Call one of the engine's OWN routes (the H3 chain's own API).

    The engine's routes answer with a JSON body and a status code. A
    refusal is information, not a transport failure: "this review
    already has a decision" (409) and "there is no such review" (404)
    are answers the caller has to read, so the status code comes back
    instead of an exception. Only a transport failure raises.
    """
    url = base_url() + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - the body is a nicety
            raw = b""
        status = int(exc.code)
    except Exception as exc:  # noqa: BLE001 - transport failures are one bucket
        raise ComfyUnavailable("%s: %s" % (type(exc).__name__, exc))
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {"data": body}
    return status, body


def history(prompt_id: str) -> dict:
    """One prompt's history entry, or {} while it is still running."""
    data = _json(base_url() + "/history/" + prompt_id)
    if not data:
        return {}
    return data.get(prompt_id, {})


#: The queue answer is reused for a couple of seconds. The task list
#: polls every three seconds and asks the same question every time; when
#: ComfyUI is NOT reachable the question costs a full connect timeout,
#: and the page must not pay it on every poll.
_QUEUE_TTL = 2.0

#: The cached queue answer, the engine it came from, and WHY it is what
#: it is.
#:
#: A failed read is cached AS A FAILURE, never as an empty queue: "the
#: engine could not be reached" and "nothing is running" are two
#: different facts, and the second one must never be invented.
#:
#: The cache belongs to ONE engine: a changed URL is a different engine,
#: and its answer must not be served out of the old one's cache.
_queue_cache: dict = {"url": "", "at": 0.0, "value": (set(), set()),
                      "error": ""}


def queue_ids(timeout: float = 1.5) -> tuple:
    """The prompt ids ComfyUI is running, and the ones it is waiting for.

    Two sets, not one: a prompt that is WAITING is not running, and the
    task list has to say so. A prompt that is in neither set and has no
    history entry is gone - it failed before its history was written, was
    cancelled, or was evicted.

    The answer is cached for a couple of seconds, and the call gets a
    SHORT timeout on purpose: this is a read of a queue that changes
    slowly, not a command that has to succeed. A page that waits out
    the full timeout on every poll feels broken when ComfyUI is down.

    An engine that cannot be reached RAISES - both when the read fails
    and when the cached answer is a remembered failure. It never
    answers with two empty sets: that would say "nothing is running"
    about an engine nobody was able to ask.
    """
    url = base_url()
    now = time.time()
    if (_queue_cache.get("url") == url
            and now - _queue_cache.get("at", 0.0) < _QUEUE_TTL):
        if _queue_cache.get("error"):
            # The remembered answer is a failure. Saying "nothing is
            # running" here would be a lie about an engine that was
            # never reached.
            raise ComfyUnavailable(str(_queue_cache["error"]))
        return _queue_cache["value"]
    try:
        data = _json(url + "/queue", timeout=timeout)
    except ComfyUnavailable as exc:
        _queue_cache.update({"url": url, "at": now, "value": (set(), set()),
                             "error": str(exc) or "ComfyUI 没有应答"})
        raise
    if not isinstance(data, dict):
        # The engine answered, but not with a queue. Nothing is known.
        error = "ComfyUI 没有给出可读的队列答案"
        _queue_cache.update({"url": url, "at": now, "value": (set(), set()),
                             "error": error})
        raise ComfyUnavailable(error)
    running, pending = set(), set()
    for entry in (data or {}).get("queue_running") or []:
        if len(entry) > 1 and entry[1]:
            running.add(str(entry[1]))
    for entry in (data or {}).get("queue_pending") or []:
        if len(entry) > 1 and entry[1]:
            pending.add(str(entry[1]))
    _queue_cache.update({"url": url, "at": now, "value": (running, pending),
                         "error": ""})
    return running, pending


def queue_ok() -> bool:
    """Did the engine answer the last queue question?

    The queue answer is cached for a couple of seconds, so a second
    question inside that window comes from the cache - including the
    "could not reach it" answer. This says which one it was, so a
    caller can tell "nothing is running" from "we cannot know".

    A remembered failure is NOT a reachable empty queue: this says False
    for it, and for a cache that belongs to another engine.
    """
    if _queue_cache.get("url") != base_url():
        return False
    return not _queue_cache.get("error")


def prompt_outputs(prompt_id: str) -> dict:
    """The text outputs of a finished prompt, joined.

    ComfyUI stores a node's text in the history as a LIST OF CHARACTERS,
    so the pieces have to be joined before anything can be parsed.
    """
    entry = history(prompt_id)
    out = {}
    for node_id, node_out in (entry.get("outputs") or {}).items():
        pieces = node_out.get("text") or []
        if pieces:
            out[node_id] = "".join(
                p if isinstance(p, str) else str(p) for p in pieces)
    return out


def interrupt(prompt_id: str) -> bool:
    """Ask ComfyUI to stop ONE prompt.

    ComfyUI's ``/interrupt`` is GLOBAL when the body carries no prompt
    id: the server reads ``json_data.get('prompt_id')`` and, when that
    is empty, stops whatever happens to be running. An empty id is
    therefore not "stop this one" - it is "stop everything", which
    would take down somebody else's render on the same server. So the
    id has to be a real one, and this refuses to send one that is not.

    The server answers 200 even when it did nothing (the prompt was not
    running), so the answer means "the request went through", not "the
    prompt was stopped".
    """
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        return False
    try:
        _request(base_url() + "/interrupt", {"prompt_id": prompt_id},
                 method="POST")
        return True
    except ComfyUnavailable:
        return False


def delete_queued(prompt_id: str) -> bool:
    """Take ONE pending prompt out of the queue.

    ``/queue`` with ``{"delete": [id]}`` removes the pending entry with
    that id - and only that one. A running prompt is not in the pending
    list, so this does nothing for it; and a prompt that is not ours is
    not in the list either.
    """
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        return False
    try:
        _request(base_url() + "/queue", {"delete": [prompt_id]},
                 method="POST")
        return True
    except ComfyUnavailable:
        return False


def forget_queue_cache() -> None:
    """Drop the cached queue answer, so the next read goes to ComfyUI.

    The queue is cached for a couple of seconds, which is right for a
    page that polls it - and wrong for a stop request, which has to see
    the queue as it is NOW. The remembered FAILURE goes with it: a stop
    request must not be refused because of a failure that is two
    seconds old.
    """
    _queue_cache.update({"url": "", "at": 0.0, "value": (set(), set()),
                         "error": ""})


def output_files(subfolder: str = "") -> list[dict]:
    """The files in ComfyUI's output directory."""
    return []


def scan_output(max_age: float = None) -> list[dict]:
    """Every file in the output tree, read ONCE.

    The tree holds thousands of files and the task list asks about
    every run at once. Walking it once per run made the list take
    seconds - long enough for the page to feel stuck.

    The result is reused for a few seconds: the page polls every three
    seconds, and a render's files do not change in between.
    """
    now = time.time()
    root = str(config.COMFYUI_ROOT)
    if (max_age is None and _scan_cache["entries"]
            and _scan_cache["root"] == root
            and now - _scan_cache["at"] < _SCAN_TTL):
        return _scan_cache["entries"]
    entries = _walk_output()
    _scan_cache["root"] = root
    _scan_cache["at"] = now
    _scan_cache["entries"] = entries
    return entries


#: The last scan of the output tree, when it was made, and which root it
#: was of - a changed root must not be served from the old scan.
_scan_cache: dict = {"root": None, "at": 0.0, "entries": []}

#: How long a scan stays good for. The page polls every three seconds;
#: a file appearing a few seconds late is nothing next to walking the
#: whole tree on every poll.
_SCAN_TTL = 5.0


def _walk_output() -> list[dict]:
    """The output tree, file by file."""
    entries = []
    for sub in config.OUTPUT_SUBDIRS:
        root = config.COMFYUI_ROOT / "output" / sub
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # 链接目录不是这次运行的文件：里面的东西属于它指着的地方。
            for name in list(dirnames):
                if _is_link(pathlib.Path(dirpath) / name):
                    dirnames.remove(name)
            for filename in filenames:
                path = pathlib.Path(dirpath) / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                relpath = str(
                    path.relative_to(config.COMFYUI_ROOT / "output")).replace("\\", "/")
                entries.append({
                    "path": path,
                    "root": root,
                    # 相对目录的各段，算一次就够：匹配每个任务时都要用。
                    "parts": path.relative_to(root).parts,
                    "name": filename,
                    "relpath": relpath,
                    "size": size,
                    "kind": _kind_of(path),
                    "role": _role_of(relpath),
                })
    return entries


def collect_artifacts(run_name: str, index: list = None) -> list[dict]:
    """The finished render's files, read straight off the disk.

    The web app runs on the same machine as ComfyUI (the plan's D8), so
    the output directory is a local path - no HTTP round trip, and the
    files are served by the app's own /media route.

    ``index`` is a scan of the whole tree (see :func:`scan_output`); pass
    it when asking about several runs at once, so the tree is walked
    once instead of once per run.

    Every file gets a ROLE as well as a kind: the finished film, the
    per-shot clips, the audio and the technical files (json, locks,
    checkpoints) are different things to the author, and a flat list of
    nineteen of them answers none of the questions.
    """
    entries = index if index is not None else scan_output()
    found = []
    for entry in entries:
        if run_name and not _belongs_to_run(entry, run_name):
            continue
        found.append({key: entry[key] for key in
                      ("name", "relpath", "size", "kind", "role")})
    return found


def group_artifacts_by_run(index: list = None) -> dict:
    """The whole output tree, bucketed by the run each file belongs to.

    ``collect_artifacts`` walks the tree per run: with two hundred runs
    on the list that is two hundred walks of a tree with thousands of
    entries, and the answer takes seconds. Bucketing walks it ONCE and
    hands every run its own files - same matching rules, one pass.

    The flat directory (live_storyboards) has no run directory, so its
    files are bucketed under the run name they carry.
    """
    entries = index if index is not None else scan_output()
    buckets: dict = {}
    for entry in entries:
        parts = entry.get("parts") or []
        if len(parts) >= 2:
            owner = parts[0]
        else:
            # The flat directory: the file is named "<run>_<date>.mp4".
            stem = entry["name"].rsplit(".", 1)[0]
            head, _, tail = stem.rpartition("_")
            owner = stem if not head else (head if _looks_like_date(tail) else stem)
        buckets.setdefault(owner, []).append(
            {key: entry[key] for key in
             ("name", "relpath", "size", "kind", "role")})
    return buckets


def _belongs_to_run(entry: dict, run_name: str) -> bool:
    """Whether this file belongs to THIS run.

    Matching on a prefix would give one version another version's
    files: "sb_run" is a prefix of "sb_run_v2", so the first version's
    list would grow the second version's clips - and deleting the
    first would take the second with it.

    Inside a run directory the name has to be a whole directory name.
    The flat directory (live_storyboards) has no run directory: there
    the file is named "<run>_<date>.mp4", so the run name is what comes
    before the date, and it has to match exactly.
    """
    parts = entry["parts"]
    if len(parts) > 1:
        return run_name == parts[0]
    name = entry["name"]
    stem = name.rsplit(".", 1)[0]
    if stem == run_name:
        return True
    head, _, tail = stem.rpartition("_")
    return head == run_name and _looks_like_date(tail)


def _looks_like_date(text: str) -> bool:
    """Is this the date a finished file carries?

    The engine writes "<run>_<YYYY-MM-DD>.mp4" into the flat directory.
    The date is what tells one version's file from the next one's.
    """
    import re

    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(text or "")))


def _role_of(relpath: str) -> str:
    """What a file is to the author.

    ``deliverable`` - the finished film. ``segment`` - one shot, before
    the shots are joined. ``audio`` - the soundtrack. ``technical`` -
    everything the engine wrote for itself.
    """
    parts = relpath.split("/")
    name = parts[-1].lower()
    if ((len(parts) >= 2 and parts[-2] == "final")
            or ("exports" in parts and "videos" in parts)) and name.endswith(
                (".mp4", ".webm", ".mkv", ".mov")):
        return "deliverable"
    if ("segments" in parts or "blend_segments" in parts
            or ("generation" in parts and "clips" in parts)):
        return "segment"
    if name.endswith((".wav", ".mp3", ".m4a", ".flac")):
        return "audio"
    return "technical"


def run_manifest(run_name: str) -> dict:
    """What the run says about itself: how long it is, how many shots.

    The task list asks for the manifest of every row on every poll, so
    the answer is kept with the stat of the file that produced it - the
    same deal the file scan gets. A manifest that changes (a run that
    finishes) is read again; one that did not is not read at all.
    """
    path = (config.COMFYUI_ROOT / "output" / "h3_chains" / run_name
            / "manifest.json")
    try:
        info = path.stat()
    except OSError:
        return {}
    key = (str(path), info.st_mtime_ns, info.st_size)
    hit = _manifest_cache.get(str(path))
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    segments = payload.get("segments") or []
    result = {
        "duration_seconds": payload.get("duration_seconds"),
        "clip_count": payload.get("clip_count") or len(segments),
        "delivered_frames": payload.get("total_delivered_frames"),
        "plan_hash": payload.get("plan_hash"),
    }
    if len(_manifest_cache) > 1000:   # one entry per run; runs are hundreds
        _manifest_cache.clear()
    _manifest_cache[str(path)] = (key, result)
    return result


#: The manifests already read, with the stat that produced them.
_manifest_cache: dict = {}


def _kind_of(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".mp4", ".webm", ".mkv", ".mov"):
        return "video"
    if suffix in (".wav", ".mp3", ".m4a", ".flac"):
        return "audio"
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return "image"
    if suffix in (".json", ".txt"):
        return "text"
    return "other"


def media_root() -> pathlib.Path:
    """The directory the /media route serves from."""
    return config.COMFYUI_ROOT / "output"


def recent_runs(limit: int = 20) -> list[dict]:
    """The finished runs on disk, newest first.

    The web app's own job table lives in memory, so a restart empties it -
    but the renders do not go anywhere. They are indexed from the chain
    directory instead, so the page still shows what was made.

    The walk is reused for a few seconds, the same deal the file scan
    gets: the page polls every three seconds, and a folder that appears
    between polls is picked up when the walk's time is up - walking
    every run folder on every poll is what made the list feel stuck.
    """
    root = config.COMFYUI_ROOT / "output" / "h3_chains"
    now = time.time()
    if (_runs_cache["runs"] and _runs_cache["root"] == root
            and now - _runs_cache["at"] < _RUNS_TTL):
        return _runs_cache["runs"][:limit]
    runs = _walk_runs(root)
    _runs_cache["root"] = root
    _runs_cache["at"] = now
    _runs_cache["runs"] = runs
    return runs[:limit]


#: The last walk of the run folders, when it was made, and which root it
#: was of - a changed root must not be served from the old walk.
_runs_cache: dict = {"root": None, "at": 0.0, "runs": []}

#: How long a walk stays good for - the same number the file scan uses.
_RUNS_TTL = _SCAN_TTL


def _walk_runs(root: pathlib.Path) -> list[dict]:
    """The chain directory, run folder by run folder."""
    if not root.is_dir():
        return []
    runs = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        final = run_dir / "final"
        videos = sorted(final.glob("*.mp4")) if final.is_dir() else []
        exports = run_dir / "exports" / "videos"
        if exports.is_dir():
            videos.extend(sorted(exports.rglob("*.mp4")))
        images = sorted(final.glob("*.png")) if final.is_dir() else []
        clips = sorted((run_dir / "segments").glob("*.mp4")) \
            if (run_dir / "segments").is_dir() else []
        generation = run_dir / "generation" / "clips"
        if generation.is_dir():
            clips.extend(sorted(generation.glob("*.mp4")))
        if not videos and not clips:
            continue
        manifest = run_dir / "manifest.json"
        shots = []
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            shots = payload.get("shots") or payload.get("clips") or []
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            pass
        stamp = max((p.stat().st_mtime for p in (videos + clips)), default=0)
        runs.append({
            "run_name": run_dir.name,
            # Files on disk prove there WERE results, not that something is
            # running now: a run with clips but no final video was
            # interrupted, and calling it "running" is a lie.
            "status": "success" if videos else ("partial" if clips else "empty"),
            "finished_at": stamp,
            "video": str(videos[0].relative_to(config.COMFYUI_ROOT / "output")).replace("\\", "/") if videos else "",
            "clips": len(clips),
            "shots": len(shots),
        })
    runs.sort(key=lambda r: r["finished_at"], reverse=True)
    return runs


def delete_run_files(run_name: str, relpaths: list = None) -> list:
    """Remove a run's files from the output tree.

    The files are taken from the exact list the task recorded, plus the
    run's own directory. Nothing is matched by a name prefix: a run
    name is a prefix of the next version's name, so "sb_run" must not
    take "sb_run_v2" with it.

    Every path is checked to be inside the output tree before it is
    touched. A name is not a path: a parent directory is not a run
    name, and a path that resolves outside the output directory is not
    ours to delete - whatever the caller says.
    """
    import shutil

    name = str(run_name or "")
    if not _LEGAL_RUN_NAME.match(name):
        return []
    removed = []
    output = config.COMFYUI_ROOT / "output"
    root = output.resolve()
    # 1) 任务记录里的那几份文件，一份一份按路径删。
    for relpath in (relpaths or []):
        raw = output / str(relpath)
        # 路径上的任何一段是链接，就不碰它指着的地方：文件本身可以是
        # 普普通通的一个文件，而它上面的那一级是个 junction。
        if _link_in_path(raw, output):
            continue
        if _is_link(raw):
            # 链接：删链接本身，不动它指着的地方。
            if _remove_link(raw):
                removed.append(str(relpath))
            continue
        path = raw.resolve()
        if not inside_tree(root, path) or not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(relpath))
    # 2) 这次运行自己的目录（目录名是整个名字，不是前缀）。
    for sub in config.OUTPUT_SUBDIRS:
        folder = output / sub / name
        # lexists 不跟着链接走：断了的链接也算数。
        if not os.path.lexists(folder):
            continue
        if _is_link(folder):
            # 这是一个 junction（指向另一个目录的"文件夹"）：看着像
            # 目录，删的却是它指着的那个地方——别人的作品。
            # 只摘掉链接本身，它下面的东西一件都不碰。
            if _remove_link(folder):
                removed.append("%s/%s（是个链接，只删了链接本身）"
                               % (sub, name))
            continue
        resolved = folder.resolve()
        if not inside_tree(root, resolved) or not resolved.is_dir():
            continue
        # 里面还有链接的话，先把链接摘掉：绝不让删除顺着它走进
        # 另一个目录去。
        for link in _links_inside(resolved):
            _remove_link(link)
        count = sum(1 for p in resolved.rglob("*") if p.is_file())
        shutil.rmtree(resolved, ignore_errors=True)
        removed.append("%s/%s（%d 个文件）" % (sub, name, count))
    return removed


def _link_in_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Is any part of this path a link?

    The file itself may be an ordinary file while the folder above it
    is a junction pointing somewhere else: resolving that path lands
    on another work's file. The whole path is checked, not just the
    leaf.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            return True
    return False


def _is_link(path: pathlib.Path) -> bool:
    """Is this a link rather than a real file or directory?

    A junction - a directory that points at another directory - looks
    like a directory from every angle, and the path it resolves to is
    the place it points at. Deleting that would delete ANOTHER work's
    files, so a link is never followed.
    """
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        info = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(info, "st_file_attributes", 0) & reparse:
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return True
    return False


def _remove_link(path: pathlib.Path) -> bool:
    """Remove the link itself, never what it points at."""
    try:
        if path.is_symlink():
            os.unlink(path)
        elif path.is_dir():
            os.rmdir(path)
        else:
            os.unlink(path)
        return True
    except OSError:
        return False


def _links_inside(folder: pathlib.Path) -> list:
    """The links inside this tree.

    The walk does not follow them - but a junction looks like a
    directory, so it is taken out of the way first.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in list(dirnames) + list(filenames):
            path = pathlib.Path(dirpath) / name
            if not _is_link(path):
                continue
            found.append(path)
            if name in dirnames:
                dirnames.remove(name)
    return found


#: The engine's own rule for a run name. A name is a name: it holds no
#: separator, no parent directory and no absolute path.
_LEGAL_RUN_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,94}[A-Za-z0-9]$|^[A-Za-z0-9]$")


def inside_tree(root: pathlib.Path, path: pathlib.Path) -> bool:
    """Is this path inside that tree?

    The paths are resolved first, so ".." and a symbolic link that
    points outside are both caught here. A string prefix says nothing:
    a sibling directory whose name merely starts the same ("output2"
    next to "output") is not inside, and prefix-matching would let it
    through.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
