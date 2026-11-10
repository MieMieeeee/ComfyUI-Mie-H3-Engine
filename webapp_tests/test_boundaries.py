"""The boundary cases a re-review found.

Five things were wrong with the first pass of the workbench fixes, and
none of them had a test: the draft was deleted before it could be
offered, a half-written JSON edit did not count as unsaved, the
snapshot sweep could delete a queued run's input, the duplicate-name
check was outside the lock, and the job log forgot the provenance.
"""

from __future__ import annotations

import json
import threading

import pytest

from webapp import config
from webapp.services import runner, snapshot

from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# the snapshot sweep must not delete what a waiting task needs
# --------------------------------------------------------------------------- #

def test_a_queued_tasks_snapshot_survives_the_sweep(plugin_root, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    import os
    import time

    oldest = snapshot.write_script_snapshot("sb_old", "测试剧本", SCRIPT)
    protected = snapshot.write_script_snapshot("sb_mid", "测试剧本", SCRIPT)
    newest = snapshot.write_script_snapshot("sb_new", "测试剧本", SCRIPT)
    root = tmp_path / "snapshots"
    # 按时间排：最老 → 最新
    os.utime(root / os.path.basename(oldest), (time.time() - 300, time.time() - 300))
    os.utime(root / os.path.basename(protected), (time.time() - 200, time.time() - 200))

    snapshot.prune(keep=1, protect={protected})
    assert (root / os.path.basename(protected)).is_dir(), "the protected one went"
    assert (root / os.path.basename(newest)).is_dir(), "the newest one went"
    assert not (root / os.path.basename(oldest)).exists(), \
        "the unprotected old one should have been swept"


def test_the_protected_name_is_matched_by_the_directory_name(plugin_root,
                                                              tmp_path, monkeypatch):
    """The job record holds a path; the sweep compares names."""
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    root = snapshot.write_script_snapshot("sb_x", "测试剧本", SCRIPT)
    name = root.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    assert name.startswith("sb_x")


def test_every_submission_gets_its_own_snapshot(plugin_root, tmp_path, monkeypatch):
    """Two submissions under one run name must not share a folder."""
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    first = snapshot.write_script_snapshot("sb_same", "测试剧本", SCRIPT)
    second = snapshot.write_script_snapshot("sb_same", "测试剧本", SCRIPT)
    assert first != second
    assert (tmp_path / "snapshots").is_dir()


# --------------------------------------------------------------------------- #
# the duplicate-name check has to be atomic
# --------------------------------------------------------------------------- #

def test_two_submissions_with_one_name_cannot_both_get_through(plugin_root,
                                                                 monkeypatch):
    """The check, the reservation and the queueing are one critical
    section: two requests with the same name used to both pass."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})

    barrier = threading.Barrier(2, timeout=5)
    entered = []

    def slow_submit(graph, client_id=""):
        # Both threads have to be inside the critical section at once for
        # the race to be real.
        entered.append(client_id)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return {"prompt_id": "p-%s" % client_id, "number": len(entered)}

    monkeypatch.setattr(runner.comfy, "submit", slow_submit)
    runner._jobs.clear()
    runner._reserved.clear()

    results, errors = [], []

    def attempt():
        try:
            results.append(runner.submit("测试剧本", {"character_1": "甲"}))
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 1, "both submissions were accepted"
    assert errors and "同名" in errors[0], errors
    runner._jobs.clear()
    runner._reserved.clear()


def test_a_failed_submission_gives_the_name_back(plugin_root, monkeypatch):
    """A submission that never reached ComfyUI must not hold the name."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})

    def refuse(graph, client_id=""):
        raise RuntimeError("ComfyUI said no")

    monkeypatch.setattr(runner.comfy, "submit", refuse)
    runner._jobs.clear()
    runner._reserved.clear()
    with pytest.raises(RuntimeError):
        runner.submit("测试剧本", {"character_1": "甲"})
    assert "sb_web_测试剧本" not in runner._reserved
    runner._jobs.clear()


def test_the_name_is_free_again_once_the_run_is_over(plugin_root, monkeypatch):
    """The reservation covers the submission, not the whole life of the
    name. Holding it forever would refuse the same segment name for good
    - and say "being submitted" about a run that finished hours ago."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})

    runner._jobs.clear()
    runner._reserved.clear()
    record = runner.submit("测试剧本", {"character_1": "甲"})
    assert record["run_name"] not in runner._reserved, \
        "the name is still reserved after the submission"

    # While it is queued the name is taken - by the job record now.
    with pytest.raises(ValueError) as caught:
        runner.submit("测试剧本", {"character_1": "甲"})
    assert "已经在跑" in str(caught.value), caught.value

    # And once it is over, the name can be used again.
    runner._jobs[record["job_id"]]["status"] = "success"
    again = runner.submit("测试剧本", {"character_1": "甲"})
    assert again["run_name"] == record["run_name"]
    runner._jobs.clear()
    runner._reserved.clear()


# --------------------------------------------------------------------------- #
# the job log has to keep the provenance
# --------------------------------------------------------------------------- #
def test_the_fingerprint_is_taken_of_the_library_the_app_uses(plugin_root):
    """The blank default resolves against mie_craft's own idea of where
    the library is - which is not the pack this app is pointed at. A hash
    of "not found" would make every asset look changed."""
    from conftest import PNG_1x1
    from webapp.services import library
    name = "指纹测试角色"
    library.save_character(name, "一个角色", "male voice", image_raw=PNG_1x1)
    mark = snapshot.asset_fingerprint("character", name)
    assert mark != "mie-asset-missing", mark
    # And it follows the content.
    library.save_character(name, "改了描述", "male voice")
    assert snapshot.asset_fingerprint("character", name) != mark


def test_the_job_log_keeps_what_the_inputs_were(plugin_root):
    """Without the names and the hashes, a restarted app cannot tell
    that the assets changed under a queued run."""
    runner._jobs.clear()
    runner._jobs["job-1"] = {
        "job_id": "job-1", "number": 1, "script": "测试剧本",
        "run_name": "sb_web_x", "role_assets": {"character_1": "甲"},
        "submitted_at": 1.0, "status": "queued", "error": "", "nodes": 34,
        "script_hash": "abc123", "asset_hashes": {"character_1": "def456"},
        "params": {"width": 960, "height": 544, "steps": 20, "duration": 18},
        "snapshot_dir": "/tmp/snap",
    }
    runner._save_jobs()
    saved = json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))["jobs"][0]
    assert saved["role_assets"] == {"character_1": "甲"}
    assert saved["asset_hashes"] == {"character_1": "def456"}
    assert saved["script_hash"] == "abc123"
    assert saved["params"]["width"] == 960
    assert saved["snapshot_dir"] == "/tmp/snap"

    # And it comes back after a restart.
    runner._jobs.clear()
    restored = runner._load_jobs()
    assert restored["job-1"]["asset_hashes"] == {"character_1": "def456"}
    assert restored["job-1"]["role_assets"] == {"character_1": "甲"}


# --------------------------------------------------------------------------- #
# a board that cannot be read has to say why
# --------------------------------------------------------------------------- #

def test_a_board_with_the_wrong_file_name_says_what_is_in_the_folder(
        plugin_root):
    """"missing storyboard.json" leaves the reader guessing. The folder
    is right there - and a JSON file under another name is usually the
    very board that is wanted."""
    from webapp.services import library
    folder = plugin_root / "asset_library" / "storyboards" / "坏剧本"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "坏剧本.h3_asset.json").write_text(
        json.dumps({"version": 2, "name": "坏剧本"}), encoding="utf-8")

    reason = library.unreadable_reason("坏剧本", "missing storyboard.json")
    assert "storyboard.json" in reason
    assert "坏剧本.h3_asset.json" in reason, reason


def test_a_board_that_is_not_there_at_all_still_explains(plugin_root):
    from webapp.services import library
    reason = library.unreadable_reason("没有的剧本", "missing storyboard.json")
    assert "storyboard.json" in reason
    assert "没有的剧本.h3_asset.json" not in reason


def test_the_list_carries_the_reason(plugin_root):
    """The list row says "损坏" - the reason has to be in there too, or
    the badge is just noise."""
    from webapp.services import library
    folder = plugin_root / "asset_library" / "storyboards" / "坏剧本"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "坏剧本.h3_asset.json").write_text(
        json.dumps({"version": 2, "name": "坏剧本"}), encoding="utf-8")

    rows = {row["name"]: row for row in library.list_storyboards_meta()}
    assert "坏剧本" in rows, rows.keys()
    assert "storyboard.json" in rows["坏剧本"]["error"]
