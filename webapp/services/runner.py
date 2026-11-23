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

import hashlib
import json
import os
import pathlib
import threading
import time
import uuid

from mie_craft.h3_loop_prompts import plan_to_json_string
from mie_craft.script.builder import MAX_CHARACTER_SLOTS, assemble_plan

from webapp import config
from webapp.services import (comfy, history, jsonfile, library, models,
                             progress, review, runname, snapshot, workflow)

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

#: "The queue could not be read" - not the same as "nothing is running".
#: Telling those two apart is what keeps an unreachable ComfyUI from
#: turning every queued render into a false "interrupted".
_UNKNOWN_QUEUE = object()


def render_busy() -> bool:
    """True while a render is queued or running.

    Mirror sync checks ``GET /api/health`` and waits while this is true.
    """
    with _jobs_lock:
        return any(
            item.get("status") in ("queued", "running")
            for item in _jobs.values()
        )


def active_count() -> int:
    """How many renders are running or waiting, right now.

    A cheap question, asked by the pages to decide whether they need
    the live progress stream at all: an idle browser must not keep an
    open connection to ComfyUI.
    """
    with _jobs_lock:
        known = [record.get("job_id") for record in _jobs.values()
                 if record.get("status") in ("queued", "running")]
    if not known:
        return 0
    try:
        running, pending = comfy.queue_ids()
    except Exception:  # noqa: BLE001 - ComfyUI unreachable: our own word
        return len(known)
    return len([job_id for job_id in known
                if job_id in running or job_id in pending])


#: How many job records are kept in the fast working set on disk. The
#: rest go to the archive section of the same document: they are still
#: there after a restart, they are just not the first thing that is
#: read. Nothing is thrown away - a render the author did is a render
#: the history page has to be able to show.
_JOB_HISTORY = 50

#: What a record keeps on disk. The outputs are re-read from ComfyUI,
#: so they are not stored. The provenance fields DO have to be saved -
#: without the asset names and their hashes, a restarted app cannot
#: tell that the inputs changed under a queued run.
_JOB_RECORD_KEYS = (
    "job_id", "number", "script", "run_name", "title", "attempt",
    "continued", "submitted_at",
    "status", "error", "nodes", "role_assets", "asset_hashes",
    "script_hash", "params", "snapshot_dir", "scene_asset", "scene_hash",
    "review_opt_in", "assembly",
)

#: Serializes the "read the table, write the document" round, so two
#: threads cannot write an older snapshot over a newer one.
_save_lock = threading.Lock()


def _load_jobs() -> dict:
    """The job records of the last run of this app.

    The table used to live in memory only, so restarting the web app
    forgot every submission - including the ones that failed. A failed
    render that leaves no trace is the worst kind of trace.

    The document has two sections: ``jobs`` is the fast working set (the
    last submissions) and ``archived`` is everything older. BOTH are
    read: a record that only lives in the archive is still a run the
    author did, and the history page still has to show it. A file
    written by an older version has no ``archived`` section - that is
    not an error, it just means nothing has been archived yet.
    """
    try:
        data = json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, list):
        data = {"jobs": data}
    if not isinstance(data, dict):
        return {}
    records: dict = {}
    # The sections are read oldest first, so the table keeps its
    # chronological order. A record that is in both sections keeps the
    # recent copy's content.
    for section in ("archived", "jobs"):
        for item in data.get(section) or []:
            if not isinstance(item, dict):
                continue
            job_id = item.get("job_id")
            if job_id:
                records[job_id] = _migrate_review_key(item)
    return records


def _migrate_review_key(record: dict) -> dict:
    """An older record kept the review OPT-IN under "review".

    "review" on a record means "there is a review waiting for a
    decision", and the history page reads it that way. The opt-in (the
    author asked for the engine's Review Gate) now lives under
    "review_opt_in"; an old record is moved over, so the page does not
    read an opt-in as a review that is waiting.
    """
    if not isinstance(record, dict):
        return record
    saved = record.get("review")
    if isinstance(saved, dict) and "shot" not in saved:
        record = dict(record)
        record["review_opt_in"] = record.get("review_opt_in") or saved
        record.pop("review", None)
    return record


def _restore_jobs() -> None:
    """Put the records of the last run of this app back on the table.

    Everything that was on disk comes back - the fast working set AND
    the archive. A run the author did is a run the history page has to
    show, and a run that is still queued is a run the duplicate-name
    check has to see.
    """
    restored = _load_jobs()
    with _jobs_lock:
        _jobs.clear()
        _jobs.update(restored)


def _slim(record: dict) -> dict:
    """The record as it is written down."""
    return {key: record.get(key) for key in _JOB_RECORD_KEYS}


def _save_jobs() -> None:
    """Write the job table down, in ONE document, all at once.

    The document has two sections: ``jobs`` is the fast working set -
    the last submissions, the ones the page shows first - and
    ``archived`` is everything older. A record that is not in the fast
    set is in the archive, and the archive is read back on the next
    start, so nothing the author did is lost by a restart.

    One file, one atomic write. Two files could lose work: a write
    that dies between them leaves the record in neither, and the
    history page would show a run that is not in the table.
    """
    with _save_lock:
        with _jobs_lock:
            records = list(_jobs.values())
        recent = records[-_JOB_HISTORY:]
        archived = records[:-_JOB_HISTORY]
        try:
            config.ensure_dirs()
            jsonfile.write_json(config.JOBS_FILE, {
                "version": 2,
                "jobs": [_slim(record) for record in recent],
                "archived": [_slim(record) for record in archived],
            })
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

    The rule itself lives in the workflow service: the graph builder
    refuses the very same board, and both have to say it the same way.
    An asset whose metadata cannot be READ is refused as well - an
    unknown asset is not a character with a voice, and the render would
    only fail later, with nothing to act on.
    """
    workflow.check_reference_voices(script, role_assets,
                                    asset_library_dir=library.library_dir())


def submit(script_name: str, role_assets: dict[str, str], run_name: str = "",
           width: int = 960, height: int = 544, steps: int = 20,
           duration: int = 18, continue_run: bool = False,
           continue_from: str = "", scene_asset: str = "",
           render_settings: dict = None, review: dict = None) -> dict:
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

    ``review`` is the author's opt-in for the engine's own Review Gate:
    ``{"enabled": True}`` makes the run stop after every saved shot and
    wait for a decision in the workbench. Without it the run is
    unattended, exactly as before.
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
        # 续跑用原任务存下来的那套渲染设置：续跑是接着那个任务走，
        # 不是偷偷换成另一套模型。
        render_settings = dict(params.get("render") or {}) or render_settings
        run_name = original.get("run_name") or run_name
        # 原任务固定的那份剧本：不是资产库里现在这份。
        script = {"json": _script_from_snapshot(
            original.get("snapshot_dir"), script_name)}
    else:
        script = library.get_storyboard(script_name)

    # A wrong model choice is a mistake in the request, not a render that
    # failed: it is refused BEFORE anything is reserved or recorded, so a
    # typo does not leave a failed task on the list.
    render_settings = models.normalize(render_settings)
    if render_settings:
        models.check(render_settings)

    scene_asset = ((original or {}).get("scene_asset") or scene_asset or "").strip()
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
        scene_hash = (snapshot.asset_fingerprint("scene", scene_asset)
                      if scene_asset else "")
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
                library_dir=snapshot_dir, asset_library_dir=library.library_dir(),
                scene_asset=scene_asset, review=review)
            # The author's model choices go into the graph's own inputs:
            # the loader nodes and the plan node, exactly where the
            # engine reads them.
            models.apply(graph, render_settings)
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
                "scene_hash": scene_hash,
                "submitted_at": _now(),
                "status": "error",
                "error": "提交失败：%s" % exc,
                "nodes": 0,
                # The opt-in the author gave, not the pending review:
                # "review" on a record means "there is a review waiting".
                "review_opt_in": dict(review) if review else {},
                "params": {"width": width, "height": height, "steps": steps,
                           "duration": duration,
                           "render": dict(render_settings)},
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
        "scene_hash": scene_hash,
        "submitted_at": _now(),
        "status": "queued",
        "error": "",
        "nodes": len(graph),
        # The review opt-in this run was submitted with: the history
        # page says so, and the run's own pending review is looked up
        # by this record. It is NOT stored under "review": that key
        # means "there is a review waiting for a decision", and the
        # page reads it as such.
        "review_opt_in": dict(review) if review else {},
        # What this run is pinned to, so the task list can say whether
        # the inputs still are what they were when it was submitted.
        "script_hash": snapshot.script_fingerprint(script_name),
        "asset_hashes": {role_id: snapshot.asset_fingerprint("character", name)
                         for role_id, name in (role_assets or {}).items() if name},
        "params": {"width": width, "height": height,
                   "steps": steps, "duration": duration,
                   "render": dict(render_settings)},
        "snapshot_dir": snapshot_dir,
    }
    with _jobs_lock:
        _jobs[record["job_id"]] = record
    # The live progress watches THIS prompt, under the client id it was
    # submitted with: ComfyUI addresses its events to that id.
    progress.register(
        record["job_id"], client_id,
        run_name=effective, script=script_name, title=record["title"],
        shot_total=len((script.get("json") or {}).get("shots") or []),
        nodes={node_id: node.get("class_type") or ""
               for node_id, node in (graph or {}).items()},
    )
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
    """Keep snapshots for both active jobs and resumable history entries.

    Every record the table holds counts - the archived ones included -
    because a record the history page shows must be able to say "resume
    from here": the snapshot it pinned is what that resume reads.
    """
    with _jobs_lock:
        return {record["snapshot_dir"] for record in _jobs.values()
                if record.get("snapshot_dir")}


def _can_resume(record: dict) -> bool:
    """Can this run be continued exactly as it was?

    Continuing reads the script the run had PINNED - its snapshot. A
    record whose snapshot is gone (or never existed - a submission that
    failed before it was queued) cannot be continued, and the page must
    not offer a button that can only fail.
    """
    folder = str(record.get("snapshot_dir") or "")
    if not folder:
        return False
    try:
        return pathlib.Path(folder).is_dir()
    except OSError:
        return False


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


#: The statuses that mean "this job is over".
_TERMINAL_STATUSES = ("success", "error", "interrupted", "partial", "empty")


def active_jobs() -> list[dict]:
    """The workbench's own jobs that ComfyUI still has in flight.

    Only OUR jobs: the queue is shared with everybody else who uses the
    same ComfyUI, and a prompt somebody else queued is not ours to
    stop.
    """
    with _jobs_lock:
        records = list(_jobs.values())
    try:
        running, pending = comfy.queue_ids()
    except Exception:  # noqa: BLE001 - ComfyUI unreachable: nothing is known
        return []
    found = []
    for record in records:
        job_id = str(record.get("job_id") or "")
        if not job_id or job_id.startswith("disk:"):
            continue
        if job_id in running:
            state = "running"
        elif job_id in pending:
            state = "queued"
        else:
            continue
        found.append({"job_id": job_id, "state": state,
                      "title": record.get("title") or record.get("run_name") or "",
                      "run_name": record.get("run_name") or ""})
    return found


def _has_finished(job_id: str) -> bool:
    """Has this prompt already produced a result?

    ComfyUI's history is the answer: a prompt that ran and finished
    while the stop was on its way has an entry there. An entry that
    cannot be read is not a verdict - the stop answer stays honest
    about what it does not know.
    """
    try:
        return bool(comfy.history(job_id))
    except comfy.ComfyUnavailable:
        return False


def stop(job_id: str = "") -> dict:
    """Stop one workbench render - and only a workbench render.

    The prompt id has to be one of OURS: the workbench shares ComfyUI's
    queue with everybody else, and a global interrupt would take down
    somebody else's render.

    A RUNNING prompt is interrupted: ComfyUI's ``/interrupt`` with that
    prompt id stops it and nothing else. A QUEUED prompt is taken out
    of the queue instead - an interrupt does not touch the pending
    list, so a queued job would keep waiting, and saying it was stopped
    while it is still queued is a lie.

    Without a job id the workbench stops the one job it has in flight,
    and refuses when there is more than one: choosing for the author
    would stop a render they did not mean to stop.

    The answer says what actually happened. The queue is read again
    after the request, so "stopped" is only ever said when the prompt
    really left the queue. And the queue is read again because taking
    a prompt OUT is a request, not a fact: it can start running on the
    way, and then it is interrupted by its own prompt id instead. A
    prompt that cannot be confirmed is reported as unknown, never as
    stopped.
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        candidates = active_jobs()
        if not candidates:
            return {"ok": False, "state": "none", "job_id": "",
                    "error": "现在没有在跑的任务可以停。"}
        if len(candidates) > 1:
            return {"ok": False, "state": "ambiguous", "job_id": "",
                    "candidates": candidates,
                    "error": "有 %d 个任务在跑：请在要做的那一条上点「停止」。"
                             % len(candidates)}
        job_id = candidates[0]["job_id"]
    with _jobs_lock:
        record = _jobs.get(job_id)
    if record is None:
        return {"ok": False, "state": "unknown", "job_id": job_id,
                "error": "这条任务不在工作台的任务表里：不会去动别人的渲染。"}
    try:
        running, pending = comfy.queue_ids()
    except comfy.ComfyUnavailable as exc:
        return {"ok": False, "state": "unreachable", "job_id": job_id,
                "error": "连不上 ComfyUI：%s" % exc}
    if job_id in running:
        if not comfy.interrupt(job_id):
            return {"ok": False, "state": "running", "job_id": job_id,
                    "error": "没能把停止请求送到 ComfyUI。"}
        # The stop lands at the end of the step that is running now, so
        # the prompt is still there for a moment. The history has the
        # final word, and the list is refreshed to read it.
        return {"ok": True, "state": "running", "job_id": job_id,
                "message": "已向 ComfyUI 发出停止请求：这次渲染会在"
                           "当前这一步结束后停下。"}
    if job_id in pending:
        if not comfy.delete_queued(job_id):
            return {"ok": False, "state": "queued", "job_id": job_id,
                    "error": "没能把这次渲染从队列里撤掉。"}
        # Did it leave the queue? The answer must not say "stopped"
        # while it is still waiting there. And the delete is a REQUEST:
        # the prompt can start running while it is on its way.
        comfy.forget_queue_cache()
        try:
            running, pending = comfy.queue_ids()
        except comfy.ComfyUnavailable as exc:
            # Nothing is known now. The request went out, but whether it
            # landed is unknown - and "unknown" must not be reported as
            # "stopped". The record stays as it was.
            return {"ok": False, "state": "unreachable", "job_id": job_id,
                    "error": "已经把撤掉请求发给 ComfyUI，但连不上它，"
                             "没法确认这次渲染是不是真的停了：%s" % exc}
        if job_id in pending:
            return {"ok": False, "state": "queued", "job_id": job_id,
                    "error": "这次渲染还在队列里，没有停下。"}
        if job_id in running:
            # The prompt started running while the delete was on its
            # way. An interrupt with THIS id stops exactly this prompt -
            # and nothing else on the shared server.
            if not comfy.interrupt(job_id):
                return {"ok": False, "state": "running", "job_id": job_id,
                        "error": "这次渲染已经开始渲染了，但没能把停止请求"
                                 "送到 ComfyUI。"}
            return {"ok": True, "state": "running", "job_id": job_id,
                    "message": "这次渲染在撤掉之前开始了：已经向 ComfyUI "
                               "发出针对它的停止请求。"}
        # It is neither waiting nor running: the delete landed. But it
        # may have run and finished in the meantime - and a finished
        # render has a result, which must not be overwritten with
        # "interrupted".
        if record.get("status") in _TERMINAL_STATUSES:
            return {"ok": False, "state": "terminal", "job_id": job_id,
                    "error": "这次渲染已经结束了，没有东西可停。"}
        if _has_finished(job_id):
            return {"ok": False, "state": "finished", "job_id": job_id,
                    "error": "这次渲染刚刚跑完了，没有东西可停。"}
        record = dict(record)
        record["status"] = "interrupted"
        record["error"] = ("这次渲染还在排队时就被工作台撤掉了，"
                            "没有开始渲染。")
        _remember(record)
        return {"ok": True, "state": "queued", "job_id": job_id,
                "message": "这次渲染还在排队，已经从队列里撤掉了"
                           "（没有开始渲染）。"}
    status = record.get("status")
    if status in _TERMINAL_STATUSES:
        return {"ok": False, "state": "terminal", "job_id": job_id,
                "error": "这次渲染已经结束了，没有东西可停。"}
    return {"ok": False, "state": "unknown", "job_id": job_id,
            "error": "ComfyUI 的队列里已经没有这次渲染了"
                     "（可能刚跑完，或已经被清掉）。"}


def find_record(job_id: str) -> dict:
    """One job record of this workbench, or {}.

    The review commands act on a record of THIS workbench: a job id
    that is not in the table belongs to somebody else, and there is
    nothing here to decide about it.
    """
    with _jobs_lock:
        record = _jobs.get(str(job_id))
    return dict(record) if record else {}


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
    _sync_progress(record)
    _attach_review(record)
    _remember(record)
    return record


def _sync_progress(record: dict) -> None:
    """Fold the history's verdict into the live progress.

    The event stream can drop a message; the history cannot. So the two
    are reconciled here, and the record carries the live progress the
    pages show.
    """
    job_id = record.get("job_id") or ""
    status = record.get("status")
    if status in ("success", "error", "interrupted", "partial"):
        progress.set_status(job_id, status, record.get("error") or "")
    record["progress"] = progress.snapshot(job_id)


def _attach_review(record: dict, reviews: list = None, error: str = "") -> dict:
    """Put this attempt's pending review on the record.

    Only the review that belongs to THIS attempt: the engine publishes
    the prompt id and the run name, and both have to match. A review of
    another run - or of another attempt of the same run - is not shown
    here, and it cannot be decided from here either.
    """
    if record.get("from_disk") or not record.get("run_name"):
        return record
    if error:
        record["review_error"] = error
        return record
    if reviews is None:
        try:
            reviews = review.live_reviews()
        except review.ReviewUnavailable:
            record["review_error"] = "引擎的审片接口没有应答，暂时看不到待审的分镜。"
            return record
    pending = review.pending_for(record, reviews)
    if pending:
        record["review"] = review.summarize(pending, record)
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
    try:
        entry = comfy.history(job_id)
    except comfy.ComfyUnavailable:
        # ComfyUI cannot be reached. That is NOT the same as "the run
        # is gone": we know nothing, so we say nothing and keep the
        # status the record already had.
        return {}
    if not entry:
        # No history entry: the prompt is either still in flight or it
        # is gone. Only the queue can tell - a job that ComfyUI has
        # forgotten is NOT still running, and saying so is what keeps
        # a failed render from sitting in the list as "running" forever.
        if queued is None:
            try:
                queued = comfy.queue_ids()
            except comfy.ComfyUnavailable:
                queued = _UNKNOWN_QUEUE
        if queued is _UNKNOWN_QUEUE:
            return {}
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
                      index: list = None, prepared: list = None) -> dict:
    """Put the delivery on the record: the film first, then the shots,
    then the engine's own files.

    The author wants the film, not the nineteen files that came with
    it.
    """
    run_name = record.get("run_name") or ""
    # The grouped history buckets the whole output tree once and hands
    # this run its files (one pass instead of one walk per run).
    artifacts = (list(prepared) if prepared is not None
                 else comfy.collect_artifacts(run_name, index))
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
    # Terminal records already have their final state on disk.
    queued = _UNKNOWN_QUEUE
    terminal = ("success", "error", "interrupted", "partial")
    if any(record.get("status") not in terminal for record in records):
        try:
            queued = comfy.queue_ids()
        except comfy.ComfyUnavailable:
            pass  # Preserve the last known states while ComfyUI is offline.
    live = []
    for record in records:
        if history.is_dismissed(record.get("run_name")):
            continue
        item = dict(record)
        _apply_status(item, queued)
        # 状态变了要写回任务表：页面说"已中断"，而内部还以为是
        # "排队中"，同名检查就会把"从这次继续"挡下来。
        _remember(item)
        _sync_progress(item)
        item["favorite"] = history.is_favorite(item.get("run_name"))
        item["inputs_changed"] = _inputs_changed(item)
        item["can_resume"] = _can_resume(item)
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
            # A run the app did not submit has no pinned script of its
            # own - there is nothing to continue from.
            "can_resume": False,
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

    # One read of the engine's pending reviews for the whole page: the
    # list asks about every run at once, and the review list is one
    # call.
    needs_review = any(
        not record.get("from_disk") and (
            record.get("status") in ("running", "queued")
            or (isinstance(record.get("review_opt_in"), dict)
                and record["review_opt_in"].get("enabled"))
        )
        for record in records
    )
    reviews, review_error = _read_reviews() if needs_review else ([], "")

    if grouped:
        return _jobs_grouped(records, page, per_page, active, query,
                              reviews, review_error)

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
        _attach_review(record, reviews, review_error)
    return {"items": window, "total": total, "page": page, "pages": pages,
            "per_page": per_page, "active": active}


def _read_reviews() -> tuple:
    """The engine's pending reviews, and why they are missing.

    The review service asks the engine with a SHORT timeout and
    remembers a failed read for a while, so a disconnected engine costs
    ONE timeout and not one on every poll. Nothing here has to guess
    whether the engine is up: a failure of the QUEUE says nothing about
    the review routes - they are separate questions, and a queue that
    could not be read must not hide a review that is waiting.
    """
    try:
        return review.live_reviews(), ""
    except review.ReviewUnavailable:
        return [], "引擎的审片接口没有应答，暂时看不到待审的分镜。"


def _jobs_grouped(records: list, page: int, per_page: int, active: int,
                  query: str, reviews: list = None,
                  review_error: str = "") -> dict:
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
    attempt_total = sum(len(work["attempts"]) for work in works)
    for work in window:
        run_name = work["run_name"]
        prepared = buckets.get(run_name)
        if prepared is None:
            prepared = comfy.collect_artifacts(run_name, index)
        for record in work["attempts"]:
            _attach_artifacts(record, index=index, prepared=prepared)
            _attach_review(record, reviews, review_error)
        delivered = next((a for a in work["attempts"]
                          if any(x.get("role") in ("deliverable", "final") and x.get("kind") == "video"
                                 for x in (a.get("artifacts") or []))), None)
        work["film"] = (delivered and delivered["artifacts"]
                        and next((x["relpath"] for x in delivered["artifacts"]
                                  if x.get("role") in ("deliverable", "final") and x.get("kind") == "video"), "")) or ""
        work["audios"] = sorted({x["relpath"]
                                 for a in work["attempts"]
                                 for x in (a.get("artifacts") or [])
                                 if x.get("kind") == "audio"})[:6]
    return {"groups": window, "total": total, "page": page, "pages": pages,
            "per_page": per_page, "active": active,
            "attempts": attempt_total}


#: The node types an assembly graph may contain. Anything that samples
#: would render the film AGAIN - which is the one thing "join what was
#: already accepted" must never do.
_ASSEMBLY_NODE_TYPES = {
    "MiniMaxH3ChainCheckpointManager|MieH3",
    "MiniMaxH3ChainCheckpointManager",
    "MiniMaxH3ChainAssemble|MieH3",
    "MiniMaxH3ChainAssemble",
}

_ID_MANAGER = "900"
_ID_ASSEMBLE = "901"

#: The one mode this command has. Anything else is refused BEFORE a
#: graph is built, so a wrong word can never turn into a render.
ASSEMBLY_MODE = "assemble"


def _assembly_graph(selection: dict, run_name: str) -> dict:
    """The assembly graph: the saved takes in, one film out.

    Two nodes, both the engine's own: the Checkpoint Manager turns the
    selected saved revisions into a verified manifest, and Assemble
    joins that manifest. There is no sampler, no loop and no review in
    it - nothing here can generate a frame.
    """
    engine = "comfyui-minimaxh3-contex-loop"
    return {
        _ID_MANAGER: {
            "class_type": "MiniMaxH3ChainCheckpointManager|MieH3",
            "inputs": {"selection_json": json.dumps(selection,
                                                    ensure_ascii=False)},
            "_meta": {"title": "已保存的分镜 · Checkpoint Manager (MieH3)",
                      "cnr_id": engine, "ver": "1"},
        },
        _ID_ASSEMBLE: {
            "class_type": "MiniMaxH3ChainAssemble|MieH3",
            "inputs": {
                "manifest": [_ID_MANAGER, 0],
                "audio_source": "plan",
                "filename": run_name + "_%date:yyyy-MM-dd%",
                "audio_bitrate": 256,
                "copy_to_output": True,
                "output_subfolder": "live_storyboards",
                "blend_schedule": "plan",
                "boundary_tone_match": "off",
                "color_stabilization": "off",
                # The accepted takes stay: they are the only copy of
                # those shots, and the assembly may have to be redone.
                "delete_checkpoints_after_assembly": False,
                "generated_audio_join": "av_overlap",
            },
            "_meta": {"title": "拼片 · Assemble (MieH3)",
                      "cnr_id": engine, "ver": "1"},
        },
    }


def _check_assembly_mode(graph: dict) -> None:
    """Refuse a graph that would generate instead of assemble.

    The check runs on the graph that is ABOUT to be submitted, so the
    promise "no new frames are rendered" is checked on the real thing
    and not on an intention.
    """
    if not graph:
        raise ValueError("拼片图是空的，没有提交。")
    for node in graph.values():
        class_type = str(node.get("class_type") or "")
        if class_type not in _ASSEMBLY_NODE_TYPES:
            raise ValueError(
                "拼片图里出现了会重新生成的节点（%s），已拒绝提交："
                "拼片只用已保存的分镜。" % class_type)
    assemble_node = graph.get(_ID_ASSEMBLE)
    if not isinstance(assemble_node, dict):
        raise ValueError("拼片图里没有拼片节点，没有提交。")
    manifest = assemble_node.get("inputs", {}).get("manifest")
    if not (isinstance(manifest, list) and len(manifest) == 2
            and str(manifest[0]) == _ID_MANAGER):
        raise ValueError("拼片节点没有接上检查点清单，没有提交。")


def assemble(job_id: str, mode: str = ASSEMBLY_MODE) -> dict:
    """Join the takes this run already saved - without generating again.

    The engine's Checkpoint Manager builds a verified manifest out of
    the saved checkpoints, and its Assemble node joins them. The saved
    takes stay where they are; only the joined film is new.

    An engine that does not know these nodes is reported as such, and
    nothing on disk is touched: the limitation is said out loud
    instead of being papered over with a re-render.
    """
    if str(mode or "") != ASSEMBLY_MODE:
        raise ValueError("只支持「拼片」这一种模式（收到的是 %r）。" % mode)
    with _jobs_lock:
        original = _jobs.get(job_id)
    if original is None:
        raise ValueError("这条任务不在历史里。")
    original = dict(original)
    run_name = str(original.get("run_name") or "")
    if not run_name:
        raise ValueError("这条任务没有运行目录，拼不了片。")
    # Two jobs writing into one run folder overwrite each other.
    _refuse_a_duplicate(run_name, original.get("script") or "")

    checkpoints = review.saved_checkpoints(run_name)
    lineage = review.accepted_lineage(checkpoints)
    if not lineage:
        raise ValueError("这次运行还没有保存下来的分镜，拼不出片子。")

    selection = {
        "run_name": run_name,
        "lineage": lineage,
        # "workflow_local": the selection is read-only. The project is
        # not re-assigned and no saved file is moved.
        "output_mode": "workflow_local",
        "output_scope": "project",
        "scope_start_scene": 1,
        "scope_end_scene": len(lineage),
    }
    graph = _assembly_graph(selection, run_name)
    # The mode is checked before the submit, not after.
    _check_assembly_mode(graph)

    client_id = "mie-web-%s" % uuid.uuid4().hex[:12]
    try:
        result = comfy.submit(graph, client_id=client_id)
    except comfy.ComfyUnavailable as exc:
        # Two very different refusals, and they must not be told apart:
        # an engine that does not know these nodes, and an engine that
        # looked at the graph and said no. The first is a limitation of
        # the engine, the second says what is wrong with THIS selection.
        if "缺少工作流节点" in str(exc):
            raise ValueError(
                "这个引擎版本做不了检查点拼片（%s）。已保存的分镜原样保留。"
                % exc) from exc
        raise ValueError(
            "这次拼片没有提交：引擎拒绝了它（%s）。已保存的分镜原样保留。"
            % exc) from exc

    record = {
        "job_id": result.get("prompt_id"),
        "number": result.get("number"),
        "script": original.get("script") or "",
        "run_name": run_name,
        "title": (original.get("title") or run_name) + " · 拼片",
        "attempt": original.get("attempt") or 0,
        "continued": False,
        # The marker that says: this attempt joins what is on disk, it
        # does not generate anything.
        "assembly": True,
        "role_assets": original.get("role_assets") or {},
        "scene_asset": original.get("scene_asset") or "",
        "scene_hash": original.get("scene_hash") or "",
        "submitted_at": _now(),
        "status": "queued",
        "error": "",
        "nodes": len(graph),
        "review_opt_in": {},
        "params": dict(original.get("params") or {}),
        "snapshot_dir": original.get("snapshot_dir") or "",
        "script_hash": original.get("script_hash") or "",
        "asset_hashes": original.get("asset_hashes") or {},
    }
    with _jobs_lock:
        _jobs[record["job_id"]] = record
    _save_jobs()
    return record


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
    render. The reference assets are loaded live and watched by content hash.
    """
    if record.get("status") not in ("running", "queued"):
        return []
    try:
        changed = snapshot.changed_assets(
            "character", record.get("role_assets") or {},
            record.get("asset_hashes") or {})
        if record.get("scene_asset") and record.get("scene_hash"):
            changed.extend(snapshot.changed_assets(
                "scene", {"scene_1": record["scene_asset"]},
                {"scene_1": record["scene_hash"]}))
        return changed
    except Exception:  # noqa: BLE001 - a hash failure is not a verdict
        return []


def plan_preview(script_name: str, role_assets: dict[str, str],
                 scene_asset: str = "", render_settings: dict = None) -> dict:
    """The plan a run WOULD use, without running anything.

    The plan is assembled by the same builder the render uses, so what the
    preview shows is what the render will do.
    """
    script = library.get_storyboard(script_name)
    storyboard = script["json"]
    roles = workflow.script_roles(storyboard)
    scene_asset = (scene_asset or "").strip()
    render_settings = models.normalize(render_settings or {})
    graph = {}
    build_error = ""
    if all((role_assets.get(role["id"]) or "").strip() for role in roles):
        try:
            graph = workflow.build(
                script_name, storyboard, role_assets, run_name="preview",
                scene_asset=scene_asset)
            models.apply(graph, render_settings)
        except (FileNotFoundError, ValueError) as exc:
            # The preview must refuse EXACTLY what the submission
            # refuses, and say it the same way: a preview that quietly
            # shows a plan the render would reject is a lie.
            build_error = str(exc)
    plan_node = next(
        (n for n in graph.values()
         if n["class_type"].endswith("ChainPlanModern|MieH3")), None)
    literal = plan_node["inputs"]["plan_json"] if plan_node else "{}"
    preview = {
        "script": script_name,
        "roles": roles,
        "fallback_plan": literal,
        "preview": script["preview"],
        "requirements": script["requirements"],
        "nodes": len(graph),
        # The render settings the preview was made with: the page shows
        # them next to the plan, and a submission carries the same ones.
        "render": dict(render_settings),
    }
    if build_error:
        preview["compile_error"] = build_error
        return preview
    slot_bundles = {}
    try:
        for slot, role in enumerate(roles, 1):
            name = (role_assets.get(role["id"]) or "").strip()
            if not name:
                continue
            # The cheap metadata: the counts are what the plan needs,
            # and the picture bytes (megabytes per asset) are not.
            detail = library.get_asset_meta(name, "character")
            slot_bundles[slot] = {
                "kind": "character",
                "name": detail["name"],
                "description": detail["description"],
                "voice": detail.get("voice") or "",
                "image": True if detail.get("image_count") else None,
                "audio": {"waveform": True, "sample_rate": 24000}
                if detail.get("audio_count") else None,
            }
        if scene_asset:
            detail = library.get_asset_meta(scene_asset, "scene")
            slot_bundles[MAX_CHARACTER_SLOTS + 1] = {
                "kind": "scene",
                "name": detail["name"],
                "description": detail["description"],
                "ambience": detail.get("ambience") or "",
                "image": True if detail.get("image_count") else None,
            }
        assembled = assemble_plan(storyboard=storyboard, slot_bundles=slot_bundles)
    except (FileNotFoundError, ValueError) as exc:
        preview["compile_error"] = str(exc)
    else:
        plan_json = plan_to_json_string(assembled["plan"])
        preview.update(
            compiled_plan=assembled["plan"],
            plan_sha256=hashlib.sha256(plan_json.encode("utf-8")).hexdigest()[:8],
            summary_lines=assembled.get("summary_lines") or [],
        )
    return preview


# The jobs of the last run of this app are restored at import time, so a
# render that failed before the restart is still on the list - with the
# failure - instead of vanishing without a trace. The archive comes back
# with them: the history page shows every run the author did, not only
# the last few dozen.
_restore_jobs()
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
