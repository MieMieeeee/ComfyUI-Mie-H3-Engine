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
    except (FileNotFoundError, json.JSONDecodeError):
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
    body = {"prompt": prompt, "client_id": client_id}
    result = _json(base_url() + "/prompt", body, timeout=180)
    if not result or "prompt_id" not in result:
        raise ComfyUnavailable("the prompt was rejected: %s" % json.dumps(
            result, ensure_ascii=False)[:600])
    return result


def history(prompt_id: str) -> dict:
    """One prompt's history entry, or {} while it is still running."""
    data = _json(base_url() + "/history/" + prompt_id)
    if not data:
        return {}
    return data.get(prompt_id, {})


def queue_ids() -> tuple:
    """The prompt ids ComfyUI is running, and the ones it is waiting for.

    Two sets, not one: a prompt that is WAITING is not running, and the
    task list has to say so. A prompt that is in neither set and has no
    history entry is gone - it failed before its history was written, was
    cancelled, or was evicted.
    """
    data = _json(base_url() + "/queue")
    running, pending = set(), set()
    for entry in data.get("queue_running") or []:
        if len(entry) > 1 and entry[1]:
            running.add(str(entry[1]))
    for entry in data.get("queue_pending") or []:
        if len(entry) > 1 and entry[1]:
            pending.add(str(entry[1]))
    return running, pending


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


def interrupt() -> bool:
    """Ask ComfyUI to stop the running prompt."""
    try:
        _request(base_url() + "/interrupt", {"prompt_id": ""}, method="POST")
        return True
    except ComfyUnavailable:
        return False


def output_files(subfolder: str = "") -> list[dict]:
    """The files in ComfyUI's output directory."""
    url = base_url() + "/view?filename="
    try:
        data = _json(base_url() + "/object_info")
    except ComfyUnavailable:
        return []
    if not data:
        return []
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
        for dirpath, _dirnames, filenames in os.walk(root):
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
        return run_name in parts[:-1]
    name = entry["name"]
    if name.startswith(run_name + "."):
        return True
    stem = name.rsplit(".", 1)[0]
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
    if len(parts) >= 2 and parts[-2] == "final" and name.endswith(
            (".mp4", ".webm", ".mkv", ".mov")):
        return "deliverable"
    if "segments" in parts or "blend_segments" in parts:
        return "segment"
    if name.endswith((".wav", ".mp3", ".m4a", ".flac")):
        return "audio"
    return "technical"


def run_manifest(run_name: str) -> dict:
    """What the run says about itself: how long it is, how many shots."""
    path = (config.COMFYUI_ROOT / "output" / "h3_chains" / run_name
            / "manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    segments = payload.get("segments") or []
    return {
        "duration_seconds": payload.get("duration_seconds"),
        "clip_count": payload.get("clip_count") or len(segments),
        "delivered_frames": payload.get("total_delivered_frames"),
        "plan_hash": payload.get("plan_hash"),
    }


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
    """
    root = config.COMFYUI_ROOT / "output" / "h3_chains"
    if not root.is_dir():
        return []
    runs = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        final = run_dir / "final"
        videos = sorted(final.glob("*.mp4")) if final.is_dir() else []
        images = sorted(final.glob("*.png")) if final.is_dir() else []
        clips = sorted((run_dir / "segments").glob("*.mp4")) \
            if (run_dir / "segments").is_dir() else []
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
    return runs[:limit]


def delete_run_files(run_name: str, relpaths: list = None) -> list:
    """Remove a run's files from the output tree.

    The files are taken from the exact list the task recorded, plus the
    run's own directory. Nothing is matched by a name prefix: a run
    name is a prefix of the next version's name, so "sb_run" must not
    take "sb_run_v2" with it.
    """
    import shutil

    name = str(run_name or "")
    if not name:
        return []
    removed = []
    output = config.COMFYUI_ROOT / "output"
    # 1) 任务记录里的那几份文件，一份一份按路径删。
    for relpath in (relpaths or []):
        path = output / str(relpath)
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(relpath))
    # 2) 这次运行自己的目录（目录名是整个名字，不是前缀）。
    for sub in config.OUTPUT_SUBDIRS:
        folder = output / sub / name
        if not folder.is_dir():
            continue
        count = sum(1 for p in folder.rglob("*") if p.is_file())
        shutil.rmtree(folder, ignore_errors=True)
        removed.append("%s/%s（%d 个文件）" % (sub, name, count))
    return removed
