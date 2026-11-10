"""The task list: what it says, and what survives a restart.

A render that fails must not sit in the list as "running" forever - the
page used to snapshot the list at submit time and never look again, so
a job that died half a second later kept claiming to be alive.
"""

from __future__ import annotations

import json

import pytest

from webapp import config
from webapp.services import runner


@pytest.fixture()
def clean_jobs(plugin_root):
    """The job table is module-level; every test starts empty.

    ``plugin_root`` is what redirects JOBS_FILE into the temp dir -
    without it a test would write into the user's real job log.
    """
    runner._jobs.clear()
    yield
    runner._jobs.clear()


def _record(job_id="job-1", **extra):
    record = {
        "job_id": job_id, "number": 1, "script": "股神",
        "run_name": "sb_web_001e24bb", "role_assets": {},
        "submitted_at": 1.0, "status": "queued", "error": "", "nodes": 34,
    }
    record.update(extra)
    return record


def test_a_job_comfyui_has_forgotten_is_not_running(clean_jobs, monkeypatch):
    """No history entry and not in the queue: the prompt is gone."""
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    job = runner.job("job-1")
    assert job["status"] == "interrupted"
    assert job["error"], "a dead job must say why"


def test_a_job_still_in_the_queue_is_running(clean_jobs, monkeypatch):
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: ({"job-1"}, set()))
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    assert runner.job("job-1")["status"] == "running"


def test_a_job_waiting_in_the_queue_is_not_running(clean_jobs, monkeypatch):
    """ComfyUI's pending queue is not the running one: a job that is
    waiting must say so instead of claiming to be rendering."""
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), {"job-1"}))
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    assert runner.job("job-1")["status"] == "queued"


def test_a_failed_run_reports_the_error_comfyui_gave(clean_jobs, monkeypatch):
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {
        "status": {"status_str": "error", "messages": [
            ["execution_error", {"exception_message": "H3 run_name must be ..."}]]},
        "outputs": {},
    })
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    job = runner.job("job-1")
    assert job["status"] == "error"
    assert "run_name" in job["error"]


def test_an_interrupted_run_is_not_reported_as_an_error(clean_jobs, monkeypatch):
    """ComfyUI files an interrupted run as status_str "error" with no
    exception - it was stopped, not broken. Reporting it as a failure
    with an empty reason is wrong in both halves."""
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {
        "status": {"status_str": "error", "completed": False, "messages": [
            ["execution_start", {"prompt_id": "job-1"}],
            ["execution_interrupted", {"node_id": "13"}]]},
        "outputs": {},
    })
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    job = runner.job("job-1")
    assert job["status"] == "interrupted"
    assert "中断" in job["error"]
    assert job["error"].strip(), "an interrupted run still has to say why"


def test_a_failure_without_a_reason_still_says_something(clean_jobs, monkeypatch):
    """An error with no message is still an error - but the list must not
    show a blank reason."""
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {
        "status": {"status_str": "error", "messages": []}, "outputs": {},
    })
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    job = runner.job("job-1")
    assert job["status"] == "error"
    assert job["error"].strip()


def test_the_jobs_survive_a_restart(clean_jobs, plugin_root):
    """A failed render must not vanish when the web app restarts."""
    runner._jobs["job-1"] = _record(status="error", error="提交后立刻失败")
    runner._save_jobs()

    runner._jobs.clear()          # what a restart does to the memory
    restored = runner._load_jobs()
    assert "job-1" in restored
    assert restored["job-1"]["status"] == "error"
    assert restored["job-1"]["error"] == "提交后立刻失败"


def test_the_saved_jobs_are_readable_json(clean_jobs, plugin_root):
    runner._jobs["job-1"] = _record()
    runner._save_jobs()
    data = json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))
    assert data["jobs"][0]["run_name"] == "sb_web_001e24bb"
    # The heavy fields are re-read from ComfyUI; they are not stored.
    assert "artifacts" not in data["jobs"][0]


def test_clips_without_a_final_video_are_not_called_running(plugin_root, tmp_path,
                                                             monkeypatch):
    """A folder with segments but no final video was interrupted. Files on
    disk prove there were results, not that something is executing."""
    from webapp.services import comfy
    chains = tmp_path / "output" / "h3_chains" / "sb_old"
    (chains / "segments").mkdir(parents=True)
    (chains / "segments" / "clip_01.mp4").write_bytes(b"x")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)
    runs = comfy.recent_runs()
    assert runs and runs[0]["status"] == "partial"
    assert runs[0]["status"] != "running"


def test_a_second_run_of_the_same_name_is_refused(clean_jobs, plugin_root):
    """Two runs with one name write one folder, and the second one
    overwrites the first one's segments while it is still running."""
    runner._jobs["job-1"] = _record(status="running")
    with pytest.raises(ValueError) as caught:
        runner._refuse_a_duplicate("sb_web_001e24bb", "股神")
    assert "已经在跑" in str(caught.value)
    # A finished run does not block a new one.
    runner._jobs["job-1"] = _record(status="success")
    runner._refuse_a_duplicate("sb_web_001e24bb", "股神")


def test_the_page_shows_a_dead_job_as_an_error(clean_jobs, client, monkeypatch):
    runner._jobs["job-1"] = _record()
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))
    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "collect_artifacts", lambda name: [])

    payload = client.get("/api/render/jobs").get_json()
    job = next(j for j in payload["jobs"] if j["job_id"] == "job-1")
    assert job["status"] == "interrupted"
