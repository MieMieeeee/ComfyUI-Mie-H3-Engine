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
import threading
import time
import uuid

from webapp import config
from webapp.services import comfy, library, runname, workflow

#: The submit lock. It covers "look at the queue, then submit" - the
#: window in which two submissions could interleave.
_submit_lock = threading.Lock()

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
    slim = [{key: record.get(key) for key in
             ("job_id", "number", "script", "run_name", "submitted_at",
              "status", "error", "nodes")}
            for record in records]
    try:
        config.ensure_dirs()
        config.JOBS_FILE.write_text(
            json.dumps({"jobs": slim}, ensure_ascii=False, indent=2),
            encoding="utf-8")
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


def submit(script_name: str, role_assets: dict[str, str], run_name: str = "",
           width: int = 960, height: int = 544, steps: int = 20,
           duration: int = 18) -> dict:
    """Build the workflow for a script and queue it.

    Returns the job record. The render itself takes minutes; the caller
    polls :func:`job`.
    """
    script = library.get_storyboard(script_name)
    # One name for both the graph and the job record: the engine takes
    # it verbatim, so the record and the run on disk must agree.
    effective = runname.effective_run_name(run_name, script_name)
    graph = workflow.build(
        script_name, script["json"], role_assets, run_name=effective,
        width=width, height=height, steps=steps, duration=duration)

    client_id = "mie-web-%s" % uuid.uuid4().hex[:12]
    with _submit_lock:
        result = comfy.submit(graph, client_id=client_id)

    record = {
        "job_id": result.get("prompt_id"),
        "number": result.get("number"),
        "script": script_name,
        "run_name": effective,
        "role_assets": role_assets,
        "submitted_at": _now(),
        "status": "queued",
        "error": "",
        "nodes": len(graph),
    }
    with _jobs_lock:
        _jobs[record["job_id"]] = record
    _save_jobs()
    return record


def job(job_id: str, queued: set = None) -> dict:
    """One job, with its current state read from ComfyUI."""
    with _jobs_lock:
        record = _jobs.get(job_id)
    if record is None:
        return {"job_id": job_id, "status": "unknown"}
    record = dict(record)
    entry = comfy.history(job_id)
    if not entry:
        # No history entry: the prompt is either still in flight or it
        # is gone. Only the queue can tell - a job that ComfyUI has
        # forgotten is NOT still running, and saying so is what keeps
        # a failed render from sitting in the list as "running" forever.
        if queued is None:
            queued = comfy.queue_ids()
        if job_id in queued:
            record["status"] = "running"
        else:
            record["status"] = "error"
            record["error"] = record.get("error") or (
                "ComfyUI 里已找不到这次运行：可能提交后立刻失败、被中断，"
                "或历史已被清理。")
        _remember(record)
        return record
    status = entry.get("status", {})
    record["status"] = status.get("status_str", "success")
    for message in status.get("messages") or []:
        if message[0] == "execution_error":
            record["status"] = "error"
            record["error"] = message[1].get("exception_message", "")
    outputs = entry.get("outputs") or {}
    texts = {}
    for node_id, node_out in outputs.items():
        pieces = node_out.get("text") or []
        if pieces:
            texts[node_id] = "".join(
                p if isinstance(p, str) else str(p) for p in pieces)
    record["texts"] = texts
    record["artifacts"] = comfy.collect_artifacts(record["run_name"])
    for node_out in outputs.values():
        if node_out.get("images"):
            record["artifacts"] = record.get("artifacts", []) + [
                {"name": image.get("filename", ""),
                 "relpath": "%s/%s" % (image.get("subfolder", ""),
                                       image.get("filename", "")),
                 "kind": "video" if image.get("type") == "output" else "image",
                 "size": 0}
                for image in node_out["images"]
            ]
    _remember(record)
    return record


def jobs() -> list[dict]:
    """Every job this process has started, newest first, plus the runs
    that are already on disk.

    The live table is in memory, so a restart empties it - but the renders
    are on disk and do not go anywhere. They are indexed from the chain
    directory, so the page keeps showing what was made.
    """
    with _jobs_lock:
        records = list(_jobs.values())
    records.sort(key=lambda r: r.get("submitted_at", 0), reverse=True)
    # One queue read for the whole list: a job ComfyUI has forgotten is
    # not running any more, and saying "running" would be a lie.
    try:
        queued = comfy.queue_ids()
    except Exception:  # noqa: BLE001 - ComfyUI unreachable: keep the list
        queued = set()
    live = [job(r["job_id"], queued) for r in records]

    known = {r["run_name"] for r in live}
    for run in comfy.recent_runs():
        if run["run_name"] in known:
            continue
        live.append({
            # A synthetic id: the disk runs have no prompt id, and an
            # empty one would collide in the page's x-for key.
            "job_id": "disk:" + run["run_name"],
            "run_name": run["run_name"],
            "status": run["status"],
            "script": "",
            "video": run["video"],
            "clips": run["clips"],
            "artifacts": ([{"name": os.path.basename(run["video"]),
                            "relpath": run["video"], "kind": "video", "size": 0}]
                           if run["video"] else []),
            "from_disk": True,
        })
    return live


def plan_preview(script_name: str, role_assets: dict[str, str]) -> dict:
    """The plan a run WOULD use, without running anything.

    The plan is assembled by the same builder the render uses, so what the
    preview shows is what the render will do.
    """
    script = library.get_storyboard(script_name)
    graph = workflow.build(
        script_name, script["json"], role_assets, run_name="preview")
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
