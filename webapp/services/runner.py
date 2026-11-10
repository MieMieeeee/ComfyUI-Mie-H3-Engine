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
from webapp.services import comfy, history, library, runname, snapshot, workflow

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
              "script_hash", "params", "snapshot_dir")}
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


def next_attempt(script_name: str) -> int:
    """Which version of this script comes next.

    Every generation of one script is its own run, with its own folder
    on disk and its own result to keep. The number counts the runs this
    app has started for the script, so it survives a restart (the job
    table is on disk).
    """
    with _jobs_lock:
        used = [record.get("attempt") or 0 for record in _jobs.values()
                if record.get("script") == script_name]
    return (max(used) if used else 0) + 1


def submit(script_name: str, role_assets: dict[str, str], run_name: str = "",
           width: int = 960, height: int = 544, steps: int = 20,
           duration: int = 18, continue_run: bool = False) -> dict:
    """Build the workflow for a script and queue it.

    Returns the job record. The render itself takes minutes; the caller
    polls :func:`job`.

    ``continue_run`` says: this is the SAME run as before - the author
    wants to carry on from where it stopped, into the same folder. Every
    other submission is a new version, with its own folder, so the
    previous result stays where it was.
    """
    script = library.get_storyboard(script_name)
    client_id = "mie-web-%s" % uuid.uuid4().hex[:12]

    # Everything that can collide is inside one critical section: the
    # name check, the reservation, the snapshot and the queueing. Two
    # requests with the same name used to both pass the check and both
    # land in the queue, and they write one folder.
    with _submit_lock:
        attempt = 1 if continue_run else next_attempt(script_name)
        # One name for both the graph and the job record: the engine
        # takes it verbatim, so the record and the run on disk must
        # agree. A continuation keeps the old name exactly - that is
        # what makes it a continuation.
        effective = (runname.safe_run_name(run_name)
                     if continue_run
                     else runname.effective_run_name(
                         run_name, script_name, attempt))
        _refuse_a_duplicate(effective, script_name)
        _reserve(effective, script_name)
        try:
            # The loader reads the library when the prompt RUNS, so the
            # script is pinned into a snapshot now: editing it while this
            # waits in the queue must not change what gets rendered.
            snapshot_dir = snapshot.write_script_snapshot(
                effective, script_name, script["json"])
            graph = workflow.build(
                script_name, script["json"], role_assets, run_name=effective,
                width=width, height=height, steps=steps, duration=duration,
                library_dir=snapshot_dir)
            result = comfy.submit(graph, client_id=client_id)
        except Exception:
            _release(effective)
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
    for record in list(_jobs.values()):
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


def _attach_artifacts(record: dict, outputs: dict = None,
                       index: list = None) -> None:
    """Put the delivery on the record: the film first, then the shots,
    then the engine's own files.

    The author wants the film, not the nineteen files that came with
    it.
    """
    artifacts = comfy.collect_artifacts(record.get("run_name") or "", index)
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


def jobs(page: int = 1, per_page: int = 20, favorites_only: bool = False) -> dict:
    """The task history, ONE PAGE at a time.

    The history grows without bound - every render is a row - and the
    files of every row make the answer heavy. The list is cut into
    pages here, and only the rows on the page are given their files.
    """
    records = all_records()
    if favorites_only:
        records = [r for r in records if r.get("favorite")]
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
            "per_page": per_page}


def delete_record(job_id: str, delete_files: bool = False) -> dict:
    """Take a run out of the history.

    The record goes, and the run is marked as put away so the engine's
    output folder does not put it straight back into the list. The
    FILES stay on disk unless the author asks for them to go as well -
    a film is the one thing here that cannot be written again.
    """
    run_name = ""
    with _jobs_lock:
        record = _jobs.get(job_id)
        if record is not None:
            run_name = record.get("run_name") or ""
            _jobs.pop(job_id, None)
        elif job_id.startswith("disk:"):
            run_name = job_id[len("disk:"):]
    if not run_name:
        raise ValueError("这条任务不在历史里。")
    history.dismiss(run_name)
    _save_jobs()
    removed = []
    if delete_files:
        removed = comfy.delete_run_files(run_name)
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
