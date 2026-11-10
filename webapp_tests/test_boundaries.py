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
        runner.submit("测试剧本", {"character_1": "甲"},
                      run_name=record["run_name"], continue_run=True)
    assert "已经在跑" in str(caught.value), caught.value

    # And once it is over, the name can be used again.
    runner._jobs[record["job_id"]]["status"] = "success"
    again = runner.submit("测试剧本", {"character_1": "甲"},
                          run_name=record["run_name"], continue_run=True)
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


# --------------------------------------------------------------------------- #
# an asset the library skipped must still be reachable
# --------------------------------------------------------------------------- #

def test_a_broken_character_is_still_listed(plugin_root):
    """The library SKIPS a broken asset, so it is not in the names at
    all - and a name that is not listed cannot be opened or deleted."""
    from webapp.services import library
    folder = plugin_root / "asset_library" / "characters" / "坏角色"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    broken = library.broken_assets()
    names = [row["name"] for row in broken["character"]]
    assert "坏角色" in names, broken
    reason = [r["error"] for r in broken["character"] if r["name"] == "坏角色"][0]
    assert "character.json" in reason, reason

    # And it reaches the page.
    payload = library.list_assets()
    assert "坏角色" in [row["name"] for row in payload["broken"]["character"]]


def test_a_broken_scene_is_still_listed(plugin_root):
    from webapp.services import library
    folder = plugin_root / "asset_library" / "scenes" / "坏场景"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    names = [row["name"] for row in library.broken_assets()["scene"]]
    assert "坏场景" in names


def test_the_broken_storyboard_is_still_listed(plugin_root):
    from webapp.services import library
    folder = plugin_root / "asset_library" / "storyboards" / "坏剧本"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "坏剧本.h3_asset.json").write_text(
        json.dumps({"version": 2, "name": "坏剧本"}), encoding="utf-8")

    names = [row["name"] for row in library.broken_assets()["storyboard"]]
    assert "坏剧本" in names


# --------------------------------------------------------------------------- #
# every generation is its own version
# --------------------------------------------------------------------------- #

def test_two_generations_get_two_folders(plugin_root, monkeypatch):
    """Re-rendering used to land in the same folder and overwrite the
    previous result. The author keeps both without inventing a name."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    counter = {"n": 0}

    def submit(graph, client_id=""):
        counter["n"] += 1
        return {"prompt_id": "p-%d" % counter["n"], "number": counter["n"]}

    monkeypatch.setattr(runner.comfy, "submit", submit)
    runner._jobs.clear()
    runner._reserved.clear()

    first = runner.submit("测试剧本", {"character_1": "甲"})
    second = runner.submit("测试剧本", {"character_1": "甲"})
    assert first["run_name"] != second["run_name"], \
        "the second generation overwrites the first"
    assert first["attempt"] == 1 and second["attempt"] == 2
    assert "第 2 版" in second["title"], second["title"]
    runner._jobs.clear()
    runner._reserved.clear()


def test_a_continuation_keeps_the_very_same_name(plugin_root, monkeypatch):
    """"From here on" means the same folder: that is how the engine can
    pick up where it stopped."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()

    first = runner.submit("测试剧本", {"character_1": "甲"})
    runner._jobs[first["job_id"]]["status"] = "error"
    again = runner.submit("测试剧本", {"character_1": "甲"},
                          run_name=first["run_name"], continue_run=True)
    assert again["run_name"] == first["run_name"]
    assert again["continued"] is True
    runner._jobs.clear()
    runner._reserved.clear()


def test_the_version_number_survives_a_restart(plugin_root, monkeypatch):
    """The job table is on disk, so the numbering does not start over
    after the app is restarted - the old result keeps its folder."""
    from webapp.services import library
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()
    runner.submit("测试剧本", {"character_1": "甲"})
    runner._save_jobs()

    runner._jobs.clear()          # what a restart does
    runner._jobs.update(runner._load_jobs())
    assert runner.next_attempt("测试剧本") == 2
    runner._jobs.clear()
    runner._reserved.clear()


# --------------------------------------------------------------------------- #
# the English mirror must not fall behind the Chinese source
# --------------------------------------------------------------------------- #

def test_a_confirmation_is_bound_to_the_version_it_was_made_against(
        plugin_root):
    """Editing the Chinese after the confirmation makes the mirror
    stale - that is the whole point of the confirmation."""
    from webapp.services import library
    script = json.loads(json.dumps(SCRIPT))
    script["execution"] = {
        "look": "A hand-drawn look.",
        "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    }
    library.confirm_execution("测试剧本", script)
    assert library.execution_state("测试剧本", script) == "confirmed"

    moved = json.loads(json.dumps(script))
    moved["look"] = "A changed look."
    assert library.execution_state("测试剧本", moved) == "changed"


def test_editing_the_mirror_after_the_confirmation_is_noticed(plugin_root):
    """The confirmation says "this mirror matches this source". A mirror
    written after that says no such thing."""
    from webapp.services import library
    script = json.loads(json.dumps(SCRIPT))
    script["execution"] = {
        "look": "A hand-drawn look.",
        "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    }
    library.confirm_execution("测试剧本", script)

    edited = json.loads(json.dumps(script))
    edited["execution"]["look"] = "Something else entirely."
    assert library.execution_state("测试剧本", edited) == "edited"


def test_the_two_states_say_different_things(plugin_root):
    from webapp.services import library
    assert "旧" in library.execution_reason("changed")
    assert "改过" in library.execution_reason("edited")


# --------------------------------------------------------------------------- #
# the finished film comes first
# --------------------------------------------------------------------------- #

def test_the_finished_film_is_told_apart_from_the_engine_files(
        plugin_root, tmp_path, monkeypatch):
    """A run's files are not one pile: the film, the shots and the
    engine's own paperwork are three different things."""
    from webapp.services import comfy
    root = tmp_path / "output" / "h3_chains" / "sb_run"
    (root / "final").mkdir(parents=True)
    (root / "segments").mkdir(parents=True)
    (root / "checkpoints").mkdir(parents=True)
    (root / "final" / "sb_run.mp4").write_bytes(b"0")
    (root / "final" / "sb_run.generated.wav").write_bytes(b"0")
    (root / "segments" / "clip_0001.mp4").write_bytes(b"0")
    (root / "checkpoints" / "model.safetensors").write_bytes(b"0")
    (root / "manifest.json").write_text(
        json.dumps({"duration_seconds": 12.5, "clip_count": 3}),
        encoding="utf-8")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    artifacts = comfy.collect_artifacts("sb_run")
    roles = {a["name"]: a["role"] for a in artifacts}
    assert roles["sb_run.mp4"] == "deliverable"
    assert roles["sb_run.generated.wav"] == "audio"
    assert roles["clip_0001.mp4"] == "segment"
    assert roles["model.safetensors"] == "technical"

    manifest = comfy.run_manifest("sb_run")
    assert manifest["duration_seconds"] == 12.5
    assert manifest["clip_count"] == 3


def test_one_version_does_not_collect_another_versions_files(
        plugin_root, tmp_path, monkeypatch):
    """The run name is a prefix of the next version's name. Matching on
    a substring would hand version one version two's clips."""
    from webapp.services import comfy
    root = tmp_path / "output" / "h3_chains"
    for name in ("sb_run", "sb_run_v2"):
        (root / name / "final").mkdir(parents=True)
        (root / name / "final" / (name + ".mp4")).write_bytes(b"0")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    first = {a["name"] for a in comfy.collect_artifacts("sb_run")}
    second = {a["name"] for a in comfy.collect_artifacts("sb_run_v2")}
    assert first == {"sb_run.mp4"}, first
    assert second == {"sb_run_v2.mp4"}, second
