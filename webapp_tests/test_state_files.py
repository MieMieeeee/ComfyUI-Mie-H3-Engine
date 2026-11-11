"""The workbench's own state: written whole or not at all, reused but
never stale.

jobs.json, history.json and execution_sync.json are small files, but
they are the workbench's memory: the task log that has to survive a
restart, the dismissed runs and favourites, the version counter that
must never go back. A crash in the middle of a plain write leaves half
a file behind, and half a file parses as nothing - the next start would
quietly begin from an empty table, hand out version numbers that were
already used, and write new runs into old folders.

So every one of them goes through one atomic helper, and the tests here
break the write on purpose: the previous content has to survive, and no
temporary litter may be left behind.

The polling caches (the disk-run scan, the history file, the run
manifests) carry the other half of the contract: an answer may be
reused between polls, but it must never go stale.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from webapp import config
from webapp.services import comfy, history, library, runner


def _record(job_id="job-1", **extra):
    record = {
        "job_id": job_id, "number": 1, "script": "股神",
        "run_name": "sb_web_001e24bb", "role_assets": {},
        "submitted_at": 1.0, "status": "queued", "error": "", "nodes": 34,
    }
    record.update(extra)
    return record


# --------------------------------------------------------------------------- #
# a write that dies mid-flight keeps what was there
# --------------------------------------------------------------------------- #

def _boom(*_args, **_kwargs):
    raise OSError("the disk went away mid-write")


def test_a_failed_job_log_write_keeps_the_previous_log(plugin_root, monkeypatch):
    """The log dies after the new content was built. What is on disk
    must still be the last good write - a restart reads this file."""
    runner._jobs.clear()
    runner._jobs["job-1"] = _record()
    runner._save_jobs()
    before = config.JOBS_FILE.read_text(encoding="utf-8")

    runner._jobs["job-2"] = _record(job_id="job-2", status="running")
    # 只撤这一处补丁：monkeypatch.undo() 会连 fixture 的路径重定向
    # 一起撤掉，读到用户的真实文件。
    with pytest.MonkeyPatch.context() as only_the_replace:
        only_the_replace.setattr("os.replace", _boom)
        runner._save_jobs()   # a log we cannot write must not break the render

    assert config.JOBS_FILE.read_text(encoding="utf-8") == before, \
        "a failed write replaced the task log"
    assert not list(config.JOBS_FILE.parent.glob(".%s.*" % config.JOBS_FILE.name)), \
        "a failed write left temporary files behind"
    runner._jobs.clear()


def test_a_failed_history_write_keeps_the_favourites(plugin_root, monkeypatch):
    """Half a history file would drop the dismissed runs and reset the
    version counter - the counter going back is the folder-reuse bug."""
    history.toggle_favorite("run_a")
    path = config.SETTINGS_FILE.parent / "history.json"
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        history.toggle_favorite("run_b")

    assert path.read_text(encoding="utf-8") == before
    assert history.is_favorite("run_a")
    assert not history.is_favorite("run_b")
    assert not list(path.parent.glob(".history.json.*")), \
        "a failed write left temporary files behind"


def test_a_failed_confirmation_write_keeps_the_record(plugin_root, monkeypatch):
    script = {"name": "股神", "roles": [], "shots": []}
    library.confirm_execution("股神", script)
    path = config.SETTINGS_FILE.parent / "execution_sync.json"
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        library.confirm_execution("另一部", {"name": "另一部",
                                             "roles": [], "shots": []})

    assert path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# the duplicate check walks the table under its lock
# --------------------------------------------------------------------------- #

def test_the_duplicate_check_reads_the_table_under_its_lock(plugin_root,
                                                             monkeypatch):
    """The check lists the job table while other threads write to it -
    the page polls exactly as a submission lands. Every other walk in
    the runner holds the table's lock; this one has to as well, or a
    walk that dies mid-stride turns a submission into a 500."""

    class Yielding(dict):
        """A table that lets the other thread in while it is listed."""

        def values(self):
            out = []
            for key in dict.keys(self):
                time.sleep(0.0005)   # the window the lock has to close
                out.append(dict.__getitem__(self, key))
            return out

    monkeypatch.setattr(runner, "_jobs", Yielding())
    stop = threading.Event()

    def churn():
        counter = 0
        while not stop.is_set():
            runner._remember({"job_id": "churn-%d" % counter,
                              "status": "queued", "run_name": "别的运行",
                              "error": ""})
            counter += 1
            time.sleep(0.0005)

    writer = threading.Thread(target=churn)
    writer.start()
    try:
        for _ in range(20):
            runner._refuse_a_duplicate("sb_web_001e24bb", "股神")
    finally:
        stop.set()
        writer.join()


# --------------------------------------------------------------------------- #
# the polling caches: reused between polls, never stale
# --------------------------------------------------------------------------- #

def test_the_disk_run_scan_is_reused_between_polls(plugin_root, tmp_path,
                                                    monkeypatch):
    """The page polls every three seconds and every poll used to walk
    every run folder on the disk. Within one scan's lifetime the answer
    is the one the page already has; when the time is up, a folder that
    appeared is picked up - not sooner, not never."""
    segments = tmp_path / "output" / "h3_chains" / "run_a" / "segments"
    segments.mkdir(parents=True)
    (segments / "clip_01.mp4").write_bytes(b"x")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    assert [r["run_name"] for r in comfy.recent_runs()] == ["run_a"]

    final = tmp_path / "output" / "h3_chains" / "run_b" / "final"
    final.mkdir(parents=True)
    (final / "film.mp4").write_bytes(b"x")

    assert [r["run_name"] for r in comfy.recent_runs()] == ["run_a"], \
        "within the scan's lifetime the answer is reused"
    comfy._runs_cache["at"] -= comfy._RUNS_TTL + 1
    assert sorted(r["run_name"] for r in comfy.recent_runs()) == \
        ["run_a", "run_b"]


def test_the_history_file_is_reread_when_it_changes(plugin_root):
    """The favourite and dismissed marks are asked about per record per
    poll, so the file is parsed once and reused - but a change on disk
    has to be seen on the very next question."""
    history.toggle_favorite("run_a")
    assert history.is_favorite("run_a")

    path = config.SETTINGS_FILE.parent / "history.json"
    time.sleep(0.01)   # 不给同一个 mtime 两次机会
    data = json.loads(path.read_text(encoding="utf-8"))
    data["favorites"] = ["a_name_that_is_longer_than_the_last_one"]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    assert not history.is_favorite("run_a")
    assert history.is_favorite("a_name_that_is_longer_than_the_last_one")


def test_the_run_manifest_follows_the_file(plugin_root, tmp_path, monkeypatch):
    """The manifest is asked about per row per poll; it may be reused,
    but a run that finishes must not be described by the manifest that
    was there before it finished."""
    run = tmp_path / "output" / "h3_chains" / "run_a"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"duration_seconds": 10, "segments": [1, 2]}),
        encoding="utf-8")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    assert comfy.run_manifest("run_a")["duration_seconds"] == 10

    time.sleep(0.01)
    (run / "manifest.json").write_text(
        json.dumps({"duration_seconds": 42.5, "segments": []}),
        encoding="utf-8")
    assert comfy.run_manifest("run_a")["duration_seconds"] == 42.5


# --------------------------------------------------------------------------- #
# the file routes: a sibling directory is not "inside"
# --------------------------------------------------------------------------- #

def test_the_media_route_does_not_serve_a_sibling_directory(plugin_root,
                                                             tmp_path,
                                                             monkeypatch,
                                                             client):
    """``output_public`` starts with ``output`` and that is all it has
    in common with it. A prefix check lets it through; the tree check
    must not."""
    final = tmp_path / "output" / "h3_chains" / "sb_x" / "final"
    final.mkdir(parents=True)
    (final / "film.mp4").write_bytes(b"film")
    sibling = tmp_path / "output_public"
    sibling.mkdir()
    (sibling / "secret.mp4").write_bytes(b"secret")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    served = client.get("/media/h3_chains/sb_x/final/film.mp4")
    assert served.status_code == 200

    escaped = client.get(
        "/media/h3_chains/../../output_public/secret.mp4")
    assert escaped.status_code == 403, \
        "the route's own boundary has to refuse, not the file server's"


def test_the_library_route_does_not_serve_a_sibling_directory(plugin_root,
                                                              client):
    root = plugin_root / "asset_library"
    (root / "characters" / "hero.png").write_bytes(b"png")

    served = client.get("/library/characters/hero.png")
    assert served.status_code == 200

    sibling = plugin_root / "asset_library_other"
    sibling.mkdir()
    (sibling / "secret.png").write_bytes(b"png")
    escaped = client.get("/library/../asset_library_other/secret.png")
    assert escaped.status_code == 403, \
        "the route's own boundary has to refuse, not the file server's"
