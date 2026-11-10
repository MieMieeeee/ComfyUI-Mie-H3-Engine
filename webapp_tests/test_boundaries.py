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
from webapp.services import runner, snapshot, workflow

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

    # 两次提交是两个版本，本来就各有各的目录，都该通过。
    assert len(results) == 2, [r.get("run_name") for r in results]
    assert not errors, errors
    names = {r["run_name"] for r in results}
    assert len(names) == 2, names

    # 真正要挡的是同一个名字：它写同一个目录。
    runner._jobs.clear()
    runner._reserved.clear()
    results.clear()
    errors.clear()

    def attempt_same():
        try:
            results.append(runner.submit("测试剧本", {"character_1": "甲"},
                                         run_name="fixed_name"))
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=attempt_same) for _ in range(2)]
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


# --------------------------------------------------------------------------- #
# a re-review found six things; these are their reproductions
# --------------------------------------------------------------------------- #

def test_deleting_one_version_leaves_the_next_one_alone(plugin_root, tmp_path,
                                                         monkeypatch):
    """The flat directory holds "<run>_<date>.mp4". Deleting "take" must
    not take "take_v2" with it - the name is a prefix of the other's."""
    from webapp.services import comfy
    flat = tmp_path / "output" / "live_storyboards"
    flat.mkdir(parents=True)
    (flat / "take.mp4").write_bytes(b"0")
    (flat / "take_v2.mp4").write_bytes(b"0")
    (flat / "take_v20.mp4").write_bytes(b"0")
    (flat / "take_2026-11-10.mp4").write_bytes(b"0")
    (flat / "take_v2_2026-11-10.mp4").write_bytes(b"0")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    # 应用走的就是这条路：先收这次任务的清单，再按清单删。
    relpaths = [a["relpath"] for a in comfy.collect_artifacts("take")]
    assert sorted(relpaths) == ["live_storyboards/take.mp4",
                                 "live_storyboards/take_2026-11-10.mp4"], relpaths
    removed = comfy.delete_run_files("take", relpaths)
    left = sorted(p.name for p in flat.iterdir())
    assert "take_v2.mp4" in left, left
    assert "take_v20.mp4" in left, left
    assert "take_v2_2026-11-10.mp4" in left, left
    assert "take.mp4" not in left, left
    assert "take_2026-11-10.mp4" not in left, left
    assert removed, "nothing was removed"


def test_the_version_number_never_goes_back(plugin_root, monkeypatch):
    """Taking the second version out of the list must not hand its
    number to the next one: that would reuse its folder on disk."""
    from webapp.services import history, library, runner
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    runner._jobs.clear()
    runner._reserved.clear()
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})

    runner.submit("测试剧本", {"character_1": "甲"})
    second = runner.submit("测试剧本", {"character_1": "甲"})
    assert second["attempt"] == 2, second

    runner.delete_record(second["job_id"])
    third = runner.submit("测试剧本", {"character_1": "甲"})
    assert third["attempt"] == 3, \
        "the number was given back and the old folder would be reused"
    assert third["run_name"] != second["run_name"]
    assert not history.is_dismissed(third["run_name"]), \
        "the new run hides behind the old one's put-away mark"
    runner._jobs.clear()
    runner._reserved.clear()


def test_the_history_writes_the_status_back_to_the_job_table(plugin_root,
                                                              monkeypatch):
    """The page says the run is over; the job table has to agree, or the
    same-name check refuses to let the author carry on."""
    from webapp.services import library, runner
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()

    record = runner.submit("测试剧本", {"character_1": "甲"})
    assert runner._jobs[record["job_id"]]["status"] == "queued"

    # ComfyUI 现在说这次运行被中断了。
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {
        "status": {"status_str": "error", "messages": [
            ["execution_interrupted", {"node_id": "13"}]]}, "outputs": {}})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))
    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "collect_artifacts",
                        lambda name, index=None: [])

    listed = runner.jobs()
    assert listed["items"][0]["status"] == "interrupted", listed["items"][0]
    assert runner._jobs[record["job_id"]]["status"] == "interrupted", \
        "the job table still thinks it is queued"
    runner._jobs.clear()
    runner._reserved.clear()


def test_the_status_filter_is_applied_before_the_page(plugin_root,
                                                       monkeypatch):
    """Filtering after the page was cut would hide the runs that are on
    a later page."""
    from webapp.services import runner
    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "collect_artifacts",
                        lambda name, index=None: [])
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: ({"job-8"}, set()))
    runner._jobs.clear()
    for index in range(9):
        runner._jobs["job-%d" % index] = {
            "job_id": "job-%d" % index, "number": index, "script": "测试剧本",
            "run_name": "sb_run_%d" % index, "attempt": index + 1,
            "submitted_at": 1000.0 + index, "status": "success",
            "error": "", "nodes": 33, "role_assets": {},
        }
    # 跑着的那个排在最后一页。
    runner._jobs["job-8"]["status"] = "running"

    filtered = runner.jobs(page=1, per_page=5, status="running")
    assert filtered["total"] == 1, filtered["total"]
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["run_name"] == "sb_run_8"
    # 全局在跑的数量与筛选无关。
    assert filtered["active"] == 1, filtered["active"]
    runner._jobs.clear()


def test_a_continuation_uses_the_script_the_task_had_pinned(plugin_root,
                                                               monkeypatch):
    """Carrying on means carrying on THAT run: the script it had pinned,
    not whatever the library holds now."""
    from webapp.services import library, runner
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()

    original = runner.submit("测试剧本", {"character_1": "甲"})

    # 历史页轮询：ComfyUI 说这次运行被中断了，任务表要跟着改。
    monkeypatch.setattr(runner.comfy, "history", lambda pid: {
        "status": {"status_str": "error", "messages": [
            ["execution_interrupted", {"node_id": "13"}]]}, "outputs": {}})
    monkeypatch.setattr(runner.comfy, "queue_ids", lambda: (set(), set()))
    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])
    monkeypatch.setattr(runner.comfy, "collect_artifacts",
                        lambda name, index=None: [])
    runner.jobs()
    assert runner._jobs[original["job_id"]]["status"] == "interrupted"

    pinned = json.loads(json.dumps(SCRIPT))
    pinned["look"] = "第一版的画风"

    # 资产库里的剧本后来改了。
    changed = json.loads(json.dumps(SCRIPT))
    changed["look"] = "改过的画风"
    monkeypatch.setattr(library, "get_storyboard",
                        lambda name: {"json": changed})

    continued = runner.submit("测试剧本", {"character_1": "甲"},
                              run_name=original["run_name"],
                              continue_run=True, continue_from=original["job_id"])
    assert continued["run_name"] == original["run_name"]
    assert continued["attempt"] == original["attempt"], \
        "a continuation is the same version, not a new one"

    # 图里带的是原任务固定的那份剧本。
    graph = workflow.build("测试剧本", pinned, {"character_1": "甲"},
                           run_name=original["run_name"],
                           library_dir=original["snapshot_dir"])
    loader = graph["900"]
    assert loader["inputs"]["library_dir"] == original["snapshot_dir"]
    runner._jobs.clear()
    runner._reserved.clear()


def test_a_continuation_without_its_snapshot_says_so(plugin_root, monkeypatch):
    """The snapshot can be swept away. Then the run cannot be carried
    on as it was - and the author has to be told that, not given a
    quiet different script."""
    from webapp.services import library, runner
    monkeypatch.setattr(library, "get_storyboard", lambda name: {"json": SCRIPT})
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()

    original = runner.submit("测试剧本", {"character_1": "甲"})
    runner._jobs[original["job_id"]]["snapshot_dir"] = "/nowhere/at/all"

    with pytest.raises(ValueError) as caught:
        runner.submit("测试剧本", {"character_1": "甲"},
                      run_name=original["run_name"],
                      continue_run=True, continue_from=original["job_id"])
    assert "无法原样继续" in str(caught.value), caught.value
    runner._jobs.clear()
    runner._reserved.clear()


# --------------------------------------------------------------------------- #
# deleting must never reach outside the output tree
# --------------------------------------------------------------------------- #

def test_a_forged_disk_task_cannot_delete_outside_the_output(plugin_root,
                                                               tmp_path,
                                                               monkeypatch):
    """A disk task's id can be made up. Only a run that is really in the
    list may be removed, and the delete itself must stay inside the
    output tree."""
    from webapp.services import comfy, runner
    root = tmp_path / "comfyui"
    output = root / "output"
    (output / "h3_chains").mkdir(parents=True)
    victim = root / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", root)
    monkeypatch.setattr(runner.comfy, "recent_runs", lambda limit=20: [])

    # 伪造一个磁盘任务名，带着父目录。
    for forged in (r"disk:..\..\victim", "disk:../victim",
                   "disk:" + str(victim).replace("\\", "/")):
        with pytest.raises(ValueError):
            runner.delete_record(forged, delete_files=True)
    assert (victim / "keep.txt").is_file(), "something outside was deleted"

    # 连删除函数自己也不许越界。
    removed = comfy.delete_run_files(r"..\..\victim",
                                     ["../victim/keep.txt"])
    assert removed == [], removed
    assert (victim / "keep.txt").is_file(), "something outside was deleted"


def test_a_run_name_with_a_dot_does_not_take_the_next_one(plugin_root,
                                                           tmp_path,
                                                           monkeypatch):
    """A dot is a legal character in a run name, so "take.v2" is a
    different run from "take" - and its file must not be deleted with
    "take"."""
    from webapp.services import comfy
    flat = tmp_path / "output" / "live_storyboards"
    flat.mkdir(parents=True)
    (flat / "take_2026-11-10.mp4").write_bytes(b"0")
    (flat / "take.v2_2026-11-10.mp4").write_bytes(b"0")
    (flat / "take.v2.mp4").write_bytes(b"0")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", tmp_path)

    relpaths = [a["relpath"] for a in comfy.collect_artifacts("take")]
    assert relpaths == ["live_storyboards/take_2026-11-10.mp4"], relpaths

    comfy.delete_run_files("take", relpaths)
    left = sorted(p.name for p in flat.iterdir())
    assert left == ["take.v2.mp4", "take.v2_2026-11-10.mp4"], left


def test_the_version_counter_is_seeded_from_the_old_tasks(plugin_root,
                                                          monkeypatch):
    """The tasks that were written before the counter existed still
    hold the highest version. Taking one of them out must not hand its
    number to the next one - so the counter is seeded when the app
    starts, not only when a new task is submitted."""
    import importlib

    from webapp.services import history, runner
    runner._jobs.clear()
    # 旧数据：任务表里有版本号，计数器是空的。
    runner._jobs["old-1"] = {
        "job_id": "old-1", "number": 1, "script": "旧剧本",
        "run_name": "sb_old_v1", "attempt": 1, "submitted_at": 1.0,
        "status": "success", "error": "", "nodes": 33, "role_assets": {},
    }
    runner._jobs["old-2"] = {
        "job_id": "old-2", "number": 2, "script": "旧剧本",
        "run_name": "sb_old_v2", "attempt": 2, "submitted_at": 2.0,
        "status": "success", "error": "", "nodes": 33, "role_assets": {},
    }
    runner._save_jobs()

    # 重启：模块重新装一遍，任务表从磁盘回来，迁移也在那时发生。
    history.claim_attempt("旧剧本", 0)
    importlib.reload(runner)
    assert history.last_attempt("旧剧本") == 2, \
        "the counter was not seeded from the tasks on disk"

    assert runner.next_attempt("旧剧本") == 3
    runner.delete_record("old-2")
    assert runner.next_attempt("旧剧本") == 3, \
        "the number went back and the old folder would be reused"
    runner._jobs.clear()


# --------------------------------------------------------------------------- #
# a link inside the output tree is never followed
# --------------------------------------------------------------------------- #

def _make_junction(link, target):
    """A junction: a directory that points at another directory.

    Windows can make one without any special rights, unlike a
    symbolic link. If this cannot be made, the test says so rather
    than passing for the wrong reason.
    """
    import os
    import subprocess

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True)   # 输出是本地代码页，不去解它
    return result.returncode == 0 and link.exists()


def test_a_junction_inside_the_output_is_not_followed(plugin_root, tmp_path,
                                                       monkeypatch):
    """A junction looks like a directory, but it points at one. Deleting
    it the usual way deletes the place it points at - another work."""
    from webapp.services import comfy
    root = tmp_path / "comfyui"
    chains = root / "output" / "h3_chains"
    chains.mkdir(parents=True)
    other = chains / "other_run"
    other.mkdir()
    keep = other / "film.mp4"
    keep.write_bytes(b"0")
    junction = chains / "take"
    if not _make_junction(junction, other):
        pytest.skip("this machine cannot make a junction")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", root)

    removed = comfy.delete_run_files("take")
    assert keep.is_file(), "the other work was deleted through the junction"
    assert not junction.exists(), "the link itself was not removed"
    assert removed, "nothing was reported"


def test_a_junction_inside_the_run_is_not_followed(plugin_root, tmp_path,
                                                    monkeypatch):
    """A link inside the run's own folder must not take the walk out of
    it either."""
    from webapp.services import comfy
    root = tmp_path / "comfyui"
    chains = root / "output" / "h3_chains"
    chains.mkdir(parents=True)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    keep = elsewhere / "keep.txt"
    keep.write_text("x", encoding="utf-8")
    run = chains / "take"
    run.mkdir()
    (run / "film.mp4").write_bytes(b"0")
    junction = run / "inner"
    if not _make_junction(junction, elsewhere):
        pytest.skip("this machine cannot make a junction")
    monkeypatch.setattr(comfy.config, "COMFYUI_ROOT", root)

    comfy.delete_run_files("take")
    assert keep.is_file(), "the delete walked out through the junction"
    assert not run.exists(), "the run's own folder is still there"
