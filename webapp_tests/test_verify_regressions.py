"""Regressions for the defects the 2026-11-15 verification run found.

Each test names the defect it pins down, so a re-introduction is not a
mystery. The verification report lives in notes/verify-2026-11-15.md.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from conftest import PNG_1x1, WAV_1S
from test_storyboard_intake import ReadingStub
from test_library import SCRIPT


WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def test_llm_inputs_submit_the_entered_values(client):
    html = client.get("/llm").get_data(as_text=True)
    assert 'mie_llm_keys.json' in html
    assert ':data-key="p.key"' in html
    assert ':data-host="p.key"' in html
    assert ':value="p.endpoint ||' in html
    assert '@change="saveKey(p, $event.target.value);' in html
    assert '@change="saveHost(p, $event.target.value)"' in html
    assert ':value="p.has_key ? p.key_masked' not in html


def test_autodraft_accepts_a_keyless_local_platform(plugin_root):
    from webapp.services import autodraft, llm

    llm.set_current("ollama")
    assert not llm.read_keys().get("ollama")
    assert autodraft._connector() is not None


def test_mobile_list_hiding_is_scoped_to_the_scripts_picker():
    css = (WEBAPP / "static" / "app.css").read_text(encoding="utf-8")
    assert ".narrow-picker + .split-list { display: none; }" in css


def test_history_attempt_labels_are_alpine_string_literals(client):
    html = client.get("/history").get_data(as_text=True)
    assert """x-text="' · 续跑'">""" in html
    assert """x-text="' · 磁盘记录'">""" in html


def test_grouped_attempt_total_spans_all_pages(monkeypatch):
    from webapp.services import comfy, runner

    monkeypatch.setattr(runner, "all_records", lambda: [
        {"run_name": "first", "job_id": "a", "submitted_at": 3},
        {"run_name": "first", "job_id": "b", "submitted_at": 2},
        {"run_name": "second", "job_id": "c", "submitted_at": 1},
    ])
    monkeypatch.setattr(comfy, "scan_output", lambda: [])
    monkeypatch.setattr(comfy, "run_manifest", lambda _: {})
    for page in (1, 2):
        result = runner.jobs(grouped=True, page=page, per_page=1)
        assert len(result["groups"]) == 1
        assert result["total"] == 2
        assert result["attempts"] == 3


def test_artifacts_are_grouped_and_discovered_in_both_output_layouts(
        tmp_path, monkeypatch):
    from webapp import config
    from webapp.services import comfy

    monkeypatch.setattr(config, "COMFYUI_ROOT", tmp_path)
    expected = {
        "legacy": {"final/a.mp4": "deliverable", "segments/a.mp4": "segment"},
        "modern": {"exports/videos/chapter/b.mp4": "deliverable",
                   "generation/clips/b.mp4": "segment"},
    }
    for run, artifacts in expected.items():
        for name in artifacts:
            path = tmp_path / "output" / "h3_chains" / run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    flat = tmp_path / "output" / "live_storyboards" / "flat_2026-11-23.mp4"
    flat.parent.mkdir()
    flat.touch()

    buckets = comfy.group_artifacts_by_run(comfy.scan_output())
    for run, artifacts in expected.items():
        assert {item["relpath"]: item["role"] for item in buckets[run]} == {
            f"h3_chains/{run}/{name}": role for name, role in artifacts.items()}
    assert comfy.collect_artifacts("final") == []
    assert [item["name"] for item in buckets["flat"]] == [flat.name]
    runs = {run["run_name"]: run for run in comfy.recent_runs()}
    assert {name: (run["status"], run["clips"])
            for name, run in runs.items()} == {
                "legacy": ("success", 1), "modern": ("success", 1)}


def test_output_files_returns_without_querying_comfyui(monkeypatch):
    from webapp.services import comfy

    def unexpected_query(*args, **kwargs):
        pytest.fail("output_files queried ComfyUI")

    monkeypatch.setattr(comfy, "_json", unexpected_query)
    assert comfy.output_files() == []


def _method_body(js: str, name: str) -> str:
    """The body of one method in a script file.

    The front end is split into several files now and the indentation is
    not the same everywhere, so the method is found by its definition
    (``  name(`` at the start of a line) and bounded by the next one.
    """
    import re
    match = re.search(r"^\s*%s\(" % re.escape(name), js, re.M)
    assert match, "method %s not found" % name
    return js[match.end():]


def test_intake_camera_and_visible_event_controls(client):
    html = client.get("/scripts").get_data(as_text=True)
    camera = html.split('x-model="intake.design.camera.framing"', 1)[1]
    camera = camera.split('x-model="intake.design.camera.perspective"', 1)[0]
    assert 'value="two_shot"' in camera
    assert 'value="locked"' in camera
    assert 'value="gentle_follow"' in camera
    assert 'value="slow_zoom_' not in camera

    # The intake flow lives in its own file now (scripts-intake.js).
    js = (WEBAPP / "static" / "js" / "scripts-intake.js").read_text(
        encoding="utf-8")
    for method, following in zip(
            ("Move", "Duplicate", "Remove"), ("Duplicate", "Remove", "Add")):
        body = _method_body(js, "intake%sEvent" % method)
        assert "this.intakeVisibleEvents" in body
        assert "indexOf(" in body
        # the next method in the file bounds this one
        assert _method_body(js, "intake%sEvent" % following)
    assert r'"\\n"' not in js
    assert r'"\\n\\n"' not in js


def test_workflow_replaces_all_preset_references(plugin_root):
    from webapp.services import library, workflow

    # A REAL character: a name that is not in the library carries no
    # voice, and a graph that promises a voice slot has to point at a
    # sample that really exists.
    library.save_character("甲", "一个测试角色", "male voice",
                           image_raw=PNG_1x1, audio_raw=WAV_1S)
    preset = workflow._preset()
    sampler = next(n for n in preset.values()
                   if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    sampler["inputs"].update({
        "ref_images.ref_image_4": ["stale-image", 0],
        "ref_audios.ref_audio_2": ["stale-audio", 0],
    })
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"},
                           workflow=preset)
    wired = next(n["inputs"] for n in graph.values()
                 if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    assert {key: val for key, val in wired.items()
            if key.startswith(("ref_images.", "ref_audios."))} == {
        "ref_images.ref_image_0": [workflow._ID_SLOTS, 1],
        "ref_audios.ref_audio_0": [workflow._ID_SLOTS, 11],
    }


def test_workflow_enforces_the_audio_slot_limit(plugin_root):
    from webapp.services import library, workflow

    library.save_character("甲", "一个测试角色", "male voice",
                           image_raw=PNG_1x1, audio_raw=WAV_1S)
    roles = [dict(SCRIPT["roles"][0], id=f"character_{slot}")
             for slot in range(1, 5)]
    script = dict(SCRIPT, roles=roles, audio_mode="generated")
    assets = {role["id"]: "甲" for role in roles}
    graph = workflow.build("测试剧本", script, assets)
    sampler = next(n["inputs"] for n in graph.values()
                   if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    assert [value[1] for key, value in sampler.items()
            if key.startswith("ref_audios.")] == [11, 12, 13]
    with pytest.raises(ValueError, match="at most 3 character slots"):
        workflow.build("测试剧本", dict(script, audio_mode="reference"), assets)


def test_asset_load_uses_the_first_image_and_audio(monkeypatch):
    from PIL import Image

    pytest.importorskip("torch")
    package = ModuleType("_mie_asset_test")
    package.__path__ = [str(WEBAPP.parent)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    nodes = importlib.import_module(f"{package.__name__}.mie_nodes")
    second = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 255)).save(second, format="PNG")
    monkeypatch.setattr(nodes.library, "load_character", lambda *_: {
        "name": "甲", "description": "角色",
        "png_bytes_list": [PNG_1x1, second.getvalue()],
        "audio_bytes_list": [b"first", b"second"],
    })
    decoded = {"sample_rate": 24000}

    def decode(data):
        assert data == b"first"
        return decoded

    monkeypatch.setattr(nodes, "_decode_reference_audio", decode)
    _, _, _, image, audio, bundle = nodes.MieH3LoadAsset().load(
        library_dir="isolated", name="甲")
    assert image[0, 0, 0].tolist() == [1.0, 0.0, 0.0]
    assert audio is bundle["audio"] is decoded


def test_running_attempt_detects_scene_edits(plugin_root):
    from webapp.services import library, runner, snapshot

    library.save_scene("天台", "原场景", image_raw=PNG_1x1)
    record = {"status": "queued", "scene_asset": "天台",
              "scene_hash": snapshot.asset_fingerprint("scene", "天台")}
    assert record["scene_hash"]
    assert runner._inputs_changed(record) == []
    library.save_scene("天台", "改过的场景")
    assert runner._inputs_changed(record) == ["天台"]


def test_completed_attempt_keeps_its_snapshot(monkeypatch):
    from webapp.services import runner

    monkeypatch.setattr(runner, "_jobs", {
        "finished": {"status": "success", "snapshot_dir": "pinned"},
    })
    assert runner._active_snapshots() == {"pinned"}


def test_settings_and_keys_preserve_previous_data_if_atomic_replace_fails(
        plugin_root, monkeypatch):
    from webapp.services import jsonfile, llm, theme

    llm.set_current("ollama")
    llm.set_key("remote", "original")

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(jsonfile.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        llm.set_current("remote")
    with pytest.raises(OSError, match="replace failed"):
        theme.save_theme(theme.DEFAULT_THEME_ID)
    with pytest.raises(OSError, match="replace failed"):
        llm.set_key("remote", "replacement")
    assert llm.current_platform() == "ollama"
    assert llm.read_keys()["remote"] == "original"


def test_render_interrupt_stops_the_named_job(client, monkeypatch):
    """The endpoint stops the job it is given - and only that one.

    A stop request with no prompt id is a GLOBAL stop on ComfyUI's side,
    so the workbench always names the prompt it means.
    """
    from webapp.services import comfy, runner

    calls = []

    def interrupt(prompt_id):
        calls.append(prompt_id)
        return True

    monkeypatch.setattr(comfy, "interrupt", interrupt)
    monkeypatch.setattr(comfy, "queue_ids", lambda: ({"job-1"}, set()))
    runner._jobs["job-1"] = {
        "job_id": "job-1", "number": 1, "script": "股神", "run_name": "sb_x",
        "role_assets": {}, "submitted_at": 1.0, "status": "running",
        "error": "", "nodes": 34,
    }
    try:
        response = client.post("/api/render/interrupt", json={"job_id": "job-1"})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["ok"] is True
        assert calls == ["job-1"]
    finally:
        runner._jobs.clear()


def test_render_interrupt_refuses_when_there_is_nothing_to_stop(client, monkeypatch):
    """Nothing in flight: the endpoint says so instead of stopping
    whatever happens to be running on the shared ComfyUI."""
    from webapp.services import comfy, runner

    calls = []

    def interrupt(prompt_id):  # pragma: no cover - must not be called
        calls.append(prompt_id)
        return True

    monkeypatch.setattr(comfy, "interrupt", interrupt)
    monkeypatch.setattr(comfy, "queue_ids", lambda: (set(), set()))
    runner._jobs.clear()
    response = client.post("/api/render/interrupt", json={})
    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["ok"] is False
    assert calls == []


def test_plan_endpoint_returns_the_compiled_shots_and_hash(client):
    from webapp.services import library

    library.save_storyboard(SCRIPT)
    library.save_character("甲", "A man in a grey jacket", "A quiet voice",
                           image_raw=PNG_1x1, audio_raw=WAV_1S)
    library.save_scene("天台", "A rooftop", ambience="Soft wind", image_raw=PNG_1x1)
    url = "/api/scripts/测试剧本/plan"
    wired = {"role_assets": {"character_1": "甲"}, "scene_asset": "天台"}
    response = client.post(url, json=wired)
    assert response.status_code == 200
    body = response.get_json()
    plan = body["compiled_plan"]
    assert plan["prompt_prefix"]
    shot, = plan["shots"]
    assert shot["id"] == "scene_01"
    assert shot["length"] > 0
    assert any("你好" in line for line in shot["prompt"])
    digest = body["plan_sha256"]
    assert len(digest) == 8
    assert all(char in "0123456789abcdef" for char in digest)
    assert client.post(url, json=wired).get_json()["plan_sha256"] == digest
    wired.pop("scene_asset")
    assert client.post(url, json=wired).get_json()["plan_sha256"] != digest


def test_partial_reference_plan_preserves_preview_fields(client):
    from webapp.services import library

    library.save_storyboard(dict(SCRIPT, audio_mode="reference"))
    response = client.post("/api/scripts/测试剧本/plan", json={})
    assert response.status_code == 200
    body = response.get_json()
    assert body["compile_error"]
    assert body["roles"] and body["requirements"] and body["preview"]
    assert "fallback_plan" in body and "nodes" in body
    assert "compiled_plan" not in body


def test_theme_selector_reflects_the_saved_theme(client):
    themes = client.get("/api/themes").get_json()
    previously_selected = client.get("/video").get_data(as_text=True)
    selected = next(t for t in themes
                    if f'data-theme="{t["id"]}"' not in previously_selected)
    assert client.post("/api/theme", json={"theme": selected["id"]}).status_code == 200
    html = client.get("/video").get_data(as_text=True)
    assert 'aria-label="切换主题"' in html
    assert f'<option value="{selected["id"]}" selected>' in html


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
              "scene_asset": "咖啡厅", "scene_hash": "scene-at-submit"}
    with runner._jobs_lock:
        runner._jobs[record["job_id"]] = record
    runner._save_jobs()
    try:
        on_disk = json.loads(pathlib.Path(config.JOBS_FILE).read_text(
            encoding="utf-8"))
        saved = [j for j in on_disk["jobs"]
                 if j.get("job_id") == "job-900"][0]
        assert saved.get("scene_asset") == "咖啡厅"
        assert saved.get("scene_hash") == "scene-at-submit"
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
