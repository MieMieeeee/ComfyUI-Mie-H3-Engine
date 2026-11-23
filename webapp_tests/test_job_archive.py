"""The job history survives a restart - ALL of it, not only the last fifty.

The job log used to keep the last fifty submissions and drop the rest
on the floor: after a restart the older runs were gone from the table,
and their snapshots were no longer protected, so a run the author could
still see on disk could not be resumed from the page.

The document now has two sections - the fast working set (``jobs``) and
the archive (``archived``) - and both come back.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from webapp import config
from webapp.services import runner, snapshot


@pytest.fixture()
def clean_jobs(plugin_root):
    """The job table is module-level; every test starts empty."""
    runner._jobs.clear()
    yield
    runner._jobs.clear()


def _record(index, **extra):
    record = {
        "job_id": "job-%03d" % index,
        "number": index,
        "script": "股神",
        "run_name": "sb_web_%03d" % index,
        "title": "股神 · 第 %d 版" % index,
        "attempt": index,
        "continued": False,
        "role_assets": {},
        "submitted_at": float(index),
        "status": "success",
        "error": "",
        "nodes": 34,
        "params": {"width": 960, "height": 544, "steps": 20, "duration": 18},
        "snapshot_dir": "",
    }
    record.update(extra)
    return record


def _fill(count, **extra):
    for index in range(1, count + 1):
        runner._jobs["job-%03d" % index] = _record(index, **extra)


def _document() -> dict:
    return json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))


def test_more_than_fifty_runs_survive_a_restart(clean_jobs):
    """Fifty is the fast working set, not the size of the history."""
    _fill(60)
    runner._save_jobs()

    document = _document()
    assert len(document["jobs"]) == 50
    assert len(document["archived"]) == 10

    runner._jobs.clear()          # what a restart does to the memory
    runner._restore_jobs()
    assert len(runner._jobs) == 60
    assert runner._jobs["job-001"]["run_name"] == "sb_web_001"
    assert runner._jobs["job-060"]["run_name"] == "sb_web_060"


def test_writing_the_document_again_keeps_the_archive(clean_jobs):
    """A restart followed by the next write must not drop the archive."""
    _fill(60)
    runner._save_jobs()
    runner._restore_jobs()
    runner._save_jobs()

    document = _document()
    assert len(document["jobs"]) + len(document["archived"]) == 60
    assert len(runner._jobs) == 60


def test_the_legacy_job_file_is_still_read(clean_jobs):
    """A file written by an older version has no archive section."""
    legacy = {"jobs": [_record(1)]}
    config.JOBS_FILE.write_text(json.dumps(legacy), encoding="utf-8")

    restored = runner._load_jobs()
    assert "job-001" in restored
    assert restored["job-001"]["run_name"] == "sb_web_001"


def test_the_history_route_shows_the_archived_runs(clean_jobs, client,
                                                   monkeypatch):
    """The page has to show every run the author did - and to know which
    of them are live, so the stop button is there when it must be."""
    _fill(60)
    runner._jobs["job-001"]["status"] = "running"
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    # ComfyUI is rendering it: the prompt is in the running queue.
    monkeypatch.setattr(runner.comfy, "queue_ids",
                        lambda: ({"job-001"}, set()))
    monkeypatch.setattr(runner.comfy, "scan_output", lambda: [])
    monkeypatch.setattr(runner.comfy, "collect_artifacts",
                        lambda name, index=None: [])

    payload = client.get("/api/render/jobs?per_page=100").get_json()
    assert payload["total"] == 60
    assert payload["active"] == 1
    assert "job-001" in {item["job_id"] for item in payload["items"]}


def test_an_archived_run_still_has_its_details(clean_jobs, monkeypatch):
    """The details route answers for a run that only lives in the
    archive - the page opens it from the list."""
    _fill(60)
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))
    monkeypatch.setattr(runner.comfy, "collect_artifacts",
                        lambda name, index=None: [])

    detail = runner.job("job-001")
    assert detail["job_id"] == "job-001"
    assert detail["run_name"] == "sb_web_001"
    assert detail["status"] == "success"


def test_an_archived_run_can_still_be_continued(clean_jobs):
    """The resume reads the record the task table holds. A record that
    only lives in the archive is still that record."""
    _fill(60)
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    # The snapshot of this run is gone, so the resume cannot work - but
    # the RECORD was found: the complaint is about the snapshot, not
    # about a task that is not there.
    with pytest.raises(ValueError) as caught:
        runner.submit("股神", {}, continue_run=True, continue_from="job-001")
    message = str(caught.value)
    assert "找不到要继续的那个任务了" not in message
    assert "固定" in message


def test_a_live_run_beyond_the_cap_is_still_live(clean_jobs):
    """A queued run is a live run: the sync waits for it, and the
    same-name check refuses a second one."""
    _fill(60)
    runner._jobs["job-001"]["status"] = "queued"
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    assert runner.render_busy() is True
    with pytest.raises(ValueError) as caught:
        runner._refuse_a_duplicate("sb_web_001", "股神")
    assert "已经在跑" in str(caught.value)


def test_a_failed_submission_beyond_the_cap_is_kept(clean_jobs):
    """A submission that never reached the engine still HAPPENED."""
    _fill(60)
    runner._jobs["job-001"] = _record(1, status="error",
                                      error="提交失败：ComfyUI 没在")
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    assert runner._jobs["job-001"]["status"] == "error"
    assert runner._jobs["job-001"]["error"] == "提交失败：ComfyUI 没在"


def test_the_snapshots_of_the_archived_runs_are_not_pruned(clean_jobs):
    """A run the history page shows must be able to say "resume from
    here" - the snapshot it pinned is what that resume reads."""
    folders = []
    for index in range(1, 61):
        folder = config.SNAPSHOT_DIR / ("snap-%03d" % index)
        folder.mkdir(parents=True)
        folders.append(folder)
        runner._jobs["job-%03d" % index] = _record(
            index, snapshot_dir=str(folder))
    runner._save_jobs()
    runner._jobs.clear()
    runner._restore_jobs()

    stray = config.SNAPSHOT_DIR / "stray"
    stray.mkdir()
    # keep=0: everything that is not protected goes, so the protection
    # is what the assertion is about.
    snapshot.prune(keep=0, protect=runner._active_snapshots())

    assert all(folder.is_dir() for folder in folders)
    assert not stray.exists(), "the sweep did not run at all"


def test_a_run_without_its_snapshot_is_not_offered_as_resumable(clean_jobs,
                                                                monkeypatch):
    """The button has to work when it is there."""
    alive = config.SNAPSHOT_DIR / "alive"
    (alive / "storyboards" / "股神").mkdir(parents=True)
    runner._jobs["job-alive"] = _record(1, job_id="job-alive",
                                         snapshot_dir=str(alive))
    runner._jobs["job-gone"] = _record(2, job_id="job-gone",
                                       snapshot_dir=str(
                                           config.SNAPSHOT_DIR / "gone"))

    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))

    records = {item["job_id"]: item for item in runner.all_records()}
    assert records["job-alive"]["can_resume"] is True
    assert records["job-gone"]["can_resume"] is False
    assert pathlib.Path(alive).is_dir()
