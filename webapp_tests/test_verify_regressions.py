"""Regressions for the defects the 2026-11-15 verification run found.

Each test names the defect it pins down, so a re-introduction is not a
mystery. The verification report lives in notes/verify-2026-11-15.md.
"""

from __future__ import annotations

import io
import json

import pytest

from conftest import PNG_1x1, WAV_1S
from test_storyboard_intake import ReadingStub
from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# 3.1 the scene asset has to reach the sampler, not just the plan
# --------------------------------------------------------------------------- #

def test_the_scene_asset_reaches_the_sampler_as_a_reference_picture(
        plugin_root):
    """A wired scene asset is the LAST picture in the slot's picture
    numbering - and it must reach the sampler.

    The engine's ``to_runtime`` drops the text scene when a scene asset
    is wired ("the scene picture owns the visual world"), so a scene
    that never reaches the sampler leaves the shot with NO environment
    at all. Found in a real render on 2026-11-15: the plan knew the
    scene, the sampler had only the two characters."""
    from webapp.services import library, workflow

    library.save_scene("天台", "夜晚的天台", ambience="wind",
                       image_raw=PNG_1x1)
    script = json.loads(json.dumps(SCRIPT))
    graph = workflow.build("测试剧本", script, {"character_1": "甲"},
                           run_name="scene_probe", scene_asset="天台")
    sampler = next(n for n in graph.values()
                   if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    pictures = {key: value for key, value in sampler["inputs"].items()
                if key.startswith("ref_images.")}
    # One on-screen character + the scene: two reference pictures.
    assert "ref_images.ref_image_0" in pictures
    assert "ref_images.ref_image_1" in pictures, pictures
    # The second one is the SCENE: it comes from the slot node, one slot
    # past the character.
    assert pictures["ref_images.ref_image_1"] == [workflow._ID_SLOTS, 2]


def test_without_a_scene_the_sampler_keeps_only_the_characters(plugin_root):
    from webapp.services import workflow

    script = json.loads(json.dumps(SCRIPT))
    graph = workflow.build("测试剧本", script, {"character_1": "甲"},
                           run_name="no_scene_probe")
    sampler = next(n for n in graph.values()
                   if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    pictures = {key for key in sampler["inputs"]
                if key.startswith("ref_images.")}
    assert pictures == {"ref_images.ref_image_0"}


# --------------------------------------------------------------------------- #
# 3.3 the scene reference has to survive a restart
# --------------------------------------------------------------------------- #

def test_the_scene_reference_of_a_record_survives_a_restart(plugin_root):
    """A record that names a scene asset must still name it after the
    workbench was restarted - otherwise "continue" and "remake" drop
    the scene the author picked (found 2026-11-15)."""
    import pathlib

    from webapp import config
    from webapp.services import runner

    record = {"job_id": "job-900", "number": 1, "script": "测试剧本",
              "run_name": "sb_scene_ref", "title": "测试剧本 · 第 1 版",
              "attempt": 1, "continued": False, "submitted_at": 1.0,
              "status": "success", "error": "", "nodes": 33,
              "role_assets": {"character_1": "甲"}, "asset_hashes": {},
              "script_hash": "h", "params": {}, "snapshot_dir": "",
              "scene_asset": "咖啡厅"}
    with runner._jobs_lock:
        runner._jobs[record["job_id"]] = record
    runner._save_jobs()
    try:
        on_disk = json.loads(pathlib.Path(config.JOBS_FILE).read_text(
            encoding="utf-8"))
        saved = [j for j in on_disk["jobs"]
                 if j.get("job_id") == "job-900"][0]
        assert saved.get("scene_asset") == "咖啡厅"
    finally:
        with runner._jobs_lock:
            runner._jobs.pop("job-900", None)
        runner._save_jobs()


# --------------------------------------------------------------------------- #
# 3.4 prose with a colon is not a role-labelled event line
# --------------------------------------------------------------------------- #

def test_prose_with_a_colon_is_not_parsed_as_event_lines(client, monkeypatch):
    """A colon in a sentence does not make it a script.

    "末班车上只剩两个人：一个抱着吉他箱的年轻人，和一个抱着药盒的老人。"
    parsed as ONE line of dialogue by the name "末班车上只剩两个人" - and
    without a model call (found 2026-11-15)."""
    from webapp.services import autodraft, intake

    stub = ReadingStub(replies=[json.dumps({
        "roles": [{"name": "年轻人", "presence": "on_screen",
                   "position": "left"}],
        "events": [{"role": 0, "type": "dialogue", "text": "你好。",
                    "language": "zh", "source": ""}],
    }, ensure_ascii=False)])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    prose = ("末班车上只剩两个人：一个抱着吉他箱的年轻人，"
             "和一个抱着药盒的老人。")
    response = client.post("/api/scripts/intake", json={
        "request_id": "req-prose", "revision": 1, "action": "events",
        "text": prose})
    assert response.status_code == 200, response.get_json()
    events = response.get_json()["events"]
    # It went to the model, and the whole paragraph is not one line.
    assert events["mode"] == "proposed"
    assert events["events"][0]["text"] != prose
    assert len(events["events"][0]["text"]) < len(prose)


def test_a_role_labelled_script_still_parses_without_a_model(client, monkeypatch):
    """The deterministic fast path stays: real role lines need no model."""
    from webapp.services import autodraft

    stub = ReadingStub(replies=["{}"])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)
    response = client.post("/api/scripts/intake", json={
        "request_id": "req-labelled", "revision": 1, "action": "events",
        "text": "小明：今天天气不错。\n小红：好，我们出发吧。"})
    assert response.status_code == 200, response.get_json()
    events = response.get_json()["events"]
    assert events["mode"] == "parsed"
    assert not stub.calls
    assert [e["text"] for e in events["events"]] == [
        "今天天气不错。", "好，我们出发吧。"]


# --------------------------------------------------------------------------- #
# 3.5 punctuation left over is not content to rule on
# --------------------------------------------------------------------------- #

def test_punctuation_left_over_is_not_an_unassigned_piece(client, monkeypatch):
    """The model's source quote stops at the sentence end; the leftover
    "，" is not a story piece the author has to rule on (2026-11-15:
    every sentence left one, and the button stayed dead)."""
    from webapp.services import autodraft

    stub = ReadingStub(replies=[json.dumps({
        "roles": [{"name": "小明", "presence": "on_screen", "position": "left"}],
        "events": [{"role": 0, "type": "dialogue", "text": "你好。",
                    "language": "zh", "source": "你好"}],
    }, ensure_ascii=False)])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)
    response = client.post("/api/scripts/intake", json={
        "request_id": "req-punct", "revision": 1, "action": "events",
        "text": "你好"})
    assert response.status_code == 200, response.get_json()
    events = response.get_json()["events"]
    # Only the trailing "。" is left over - punctuation is not content.
    assert events["unmapped"] == [], events["unmapped"]


# --------------------------------------------------------------------------- #
# the history must answer a page of works quickly
# --------------------------------------------------------------------------- #

def test_the_grouped_history_answers_without_waiting_on_comfyui(
        client, monkeypatch):
    """The task list polls every three seconds; it must not wait out a
    connect timeout to ComfyUI on every poll (2026-11-15: 2 seconds for
    a page of works)."""
    import time

    from webapp.services import comfy, runner

    def slow_queue(*args, **kwargs):
        time.sleep(0.2)
        return set(), set()

    monkeypatch.setattr(comfy, "queue_ids", slow_queue)
    records = [{"job_id": "j%d" % i, "number": i, "script": "剧本",
                "run_name": "run_%03d" % i, "title": "作品 %d" % i,
                "attempt": 1, "continued": False, "submitted_at": float(i),
                "status": "success", "error": "", "nodes": 33,
                "role_assets": {}, "asset_hashes": {}, "script_hash": "h",
                "params": {}, "snapshot_dir": "", "scene_asset": ""}
               for i in range(200)]
    monkeypatch.setattr(runner, "all_records", lambda: records)

    started = time.time()
    data = runner.jobs(grouped=True, per_page=20)
    elapsed = time.time() - started
    assert data["total"] == 200
    assert len(data["groups"]) == 20
    # The stubbed queue costs 0.2s on its own; the budget is about the
    # work of 200 records, not about the queue.
    assert elapsed < 2.0, "作品视图用了 %.2fs 才回答" % elapsed


# --------------------------------------------------------------------------- #
# the asset endpoints still behave (the agent's probes touched them)
# --------------------------------------------------------------------------- #

def test_an_asset_can_be_created_read_back_and_deleted(client):
    response = client.post(
        "/api/assets/character",
        data={"name": "验证角色", "description": "描述", "voice": "male voice",
              "image": (io.BytesIO(PNG_1x1), "portrait.png"),
              "audio": (io.BytesIO(WAV_1S), "sample.wav")},
        content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    detail = client.get("/api/assets/character/验证角色").get_json()
    assert detail["audio_count"] == 1
    assert detail["audios"][0]["url"].startswith("/library/characters/")
    removed = client.post("/api/assets/character/验证角色/delete")
    assert removed.status_code == 200


# --------------------------------------------------------------------------- #
# a submission that never reached the engine still happened
# --------------------------------------------------------------------------- #

def test_a_failed_submission_leaves_a_record_with_the_reason(
        client, monkeypatch):
    """The author pressed "render" and it did not work. The task list
    must say so - a click that leaves no trace cannot be told apart
    from never having tried (2026-11-15: with ComfyUI down, the render
    left nothing behind)."""
    from webapp.services import comfy, runner

    def offline(graph, client_id=""):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    monkeypatch.setattr(comfy, "submit", offline)
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    with pytest.raises(Exception):
        runner.submit("测试剧本", {"character_1": "甲"}, run_name="failed_probe")
    records = runner.all_records()
    mine = [r for r in records if r.get("run_name") == "failed_probe"]
    assert mine, "失败的提交没有留下任何记录"
    assert mine[0]["status"] == "error"
    assert "提交失败" in (mine[0].get("error") or "")
