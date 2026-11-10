"""The HTTP surface: every page and every API route."""

from __future__ import annotations

import io
import json
import pathlib

import pytest

from webapp import config
from webapp.services import comfy, library

from conftest import PNG_1x1
from test_library import SCRIPT


def test_every_page_serves(client):
    for path in ("/", "/llm", "/assets", "/scripts", "/video"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"ComfyUI-Mie" in response.data, path


def test_the_theme_is_in_the_page(client):
    body = client.get("/").data.decode("utf-8")
    assert "theme-injector" in body
    assert "--bg-app" in body, "the design tokens must be injected"


def test_the_theme_is_not_double_wrapped(client):
    """A nested <style> is parsed as an invalid selector and dropped, so
    the page would render with the default tokens."""
    body = client.get("/").data.decode("utf-8")
    injector = body.split('<style id="theme-injector">')[1].split("</style>")[0]
    assert "<style" not in injector, injector[:120]
    assert injector.strip().startswith(":root")


def test_the_theme_can_be_chosen(client):
    response = client.post("/api/theme", json={"theme": "cyber-blue"})
    assert response.get_json()["theme"] == "cyber-blue"
    assert 'data-theme="cyber-blue"' in client.get("/").data.decode("utf-8")


def test_an_unknown_theme_falls_back(client):
    assert client.post("/api/theme", json={"theme": "nope"}).get_json()[
        "theme"] == "forest-island"


def test_the_asset_listing(client):
    payload = client.get("/api/assets").get_json()
    assert payload["ok"] is True
    assert "characters" in payload and "scenes" in payload


def test_a_character_can_be_created_and_read_back(client):
    response = client.post(
        "/api/assets/character",
        data={"name": "接口角色", "description": "描述", "voice": "male voice",
              "image": (io.BytesIO(PNG_1x1), "portrait.png")},
        content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "created"

    data = client.get("/api/assets/character/接口角色").get_json()
    assert data["description"] == "描述"
    assert data["image"].startswith("data:image/png;base64,")


def test_a_missing_asset_is_a_404(client):
    assert client.get("/api/assets/character/不存在").status_code == 404


def test_a_script_can_be_saved_and_validated(client):
    response = client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "created"

    listing = client.get("/api/scripts").get_json()
    assert "测试剧本" in listing["names"]
    # items 是给列表页的元数据：正常板有摘要，坏的也能看到原因。
    row = next(i for i in listing["items"] if i["name"] == "测试剧本")
    assert row["roles"] == 1 and row["shots"] == 1
    assert row["audio_mode"] == "generated"

    check = client.post("/api/scripts/validate", json={"json": json.dumps(SCRIPT)})
    assert check.get_json()["ok"] is True
    assert check.get_json()["json"]["shots"][0]["duration_seconds"] > 0


def test_a_broken_script_reports_the_contract_error(client):
    broken = json.loads(json.dumps(SCRIPT))
    broken["shots"][0]["events"][0]["text"] = "别的"
    result = client.post("/api/scripts/validate",
                          json={"json": json.dumps(broken)}).get_json()
    assert result["ok"] is False
    assert "source events differ" in result["errors"][0]


def test_a_punctuation_line_reports_a_render_risk(client):
    """Punctuation-only dialogue is contract-valid but renders as a chime:
    the risk list must flag it while validation still passes."""
    risky = json.loads(json.dumps(SCRIPT))
    risky["source_script"] = "甲：？？？"
    risky["shots"][0]["events"][0]["text"] = "？？？"
    result = client.post("/api/scripts/validate",
                          json={"json": json.dumps(risky)}).get_json()
    assert result["ok"] is True
    assert result["errors"] == []
    assert any("notification chime" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# auto drafts (stub connector: no real LLM is ever called)
# --------------------------------------------------------------------------- #

class _StubConnector:
    timeout = 30

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.replies:
            return "ok"
        return self.replies.pop(0)


def test_a_concept_draft_lands_in_the_editor_not_the_library(client, monkeypatch):
    from webapp.services import autodraft
    draft = json.loads(json.dumps(SCRIPT))
    draft["continuation_overlap_frames"] = 22  # concept 模式的 caller-owned 默认值
    stub = _StubConnector([json.dumps(draft)])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/scripts/auto", json={
        "mode": "concept", "concept": "一只猫打车", "name": "测试剧本",
        "shot_count": 1, "critic": "off"})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["json"]["name"] == "测试剧本"
    assert data["notes"] == []
    assert len(stub.calls) == 1  # critic 关掉后只有一次生成调用
    # 草稿不落库：库里还是空的，保存要用户确认后走正常保存。
    assert client.get("/api/scripts").get_json()["names"] == []


def test_a_revision_draft_keeps_the_base_wiring(client, monkeypatch):
    from webapp.services import autodraft
    stub = _StubConnector([json.dumps(SCRIPT)])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/scripts/auto", json={
        "mode": "revise", "concept": "结尾改成她笑而不语",
        "name": "测试剧本", "base_asset": SCRIPT, "critic": "off"})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    # 接线事实从底稿继承，草稿原样带回。
    assert data["json"]["audio_mode"] == SCRIPT["audio_mode"]
    assert data["json"]["continuation_overlap_frames"] == \
        SCRIPT["continuation_overlap_frames"]


def test_an_empty_concept_is_a_friendly_error(client):
    response = client.post("/api/scripts/auto", json={"mode": "concept", "concept": "  "})
    assert response.status_code == 400
    assert "空" in response.get_json()["error"]


def test_asset_auto_drafts_only_empty_fields(client, monkeypatch):
    from webapp.services import autodraft
    stub = _StubConnector(["adult male, warm timbre, unhurried pace"])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/assets/auto", json={
        "kind": "character", "name": "卖家", "hint": "胖胖的中年男人",
        "current": {"description": "已有描述", "voice": ""}})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["description"] == "已有描述"      # 作者写过的字不动
    assert "adult male" in data["voice"]         # 空的被起草
    assert data["drafted"] == ["voice"]
    assert len(stub.calls) == 1


def test_scene_auto_drafts_the_ambience(client, monkeypatch):
    from webapp.services import autodraft
    stub = _StubConnector(["Quiet room tone, faint clock ticking"])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/assets/auto", json={
        "kind": "scene", "name": "客厅",
        "current": {"description": "夜晚的客厅", "ambience": ""}})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["description"] == "夜晚的客厅"
    assert "clock" in data["ambience"]
    assert data["drafted"] == ["ambience"]


def test_auto_without_a_platform_is_a_friendly_error(client, monkeypatch):
    from webapp.services import autodraft
    monkeypatch.setattr(autodraft.llm, "current_platform", lambda: "")
    for path, body in (
            ("/api/scripts/auto", {"mode": "concept", "concept": "点子"}),
            ("/api/assets/auto", {"kind": "character", "name": "卖家"})):
        response = client.post(path, json=body)
        assert response.status_code == 400, (path, response.get_json())
        assert "大模型页" in response.get_json()["error"]


def test_the_page_can_ask_which_run_name_a_submit_would_use(client):
    """The video page shows the name the engine will actually get - a
    Chinese script name has to become a legal one."""
    response = client.post("/api/render/run_name",
                           json={"script": "股神", "run_name": ""})
    assert response.status_code == 200
    name = response.get_json()["run_name"]
    assert name and len(name) <= 96
    assert all(ch.isalnum() or ch in "._-" for ch in name), name
    assert name[0].isalnum() and name[-1].isalnum(), name

    typed = client.post("/api/render/run_name",
                        json={"script": "股神", "run_name": "my_run-1"})
    assert typed.get_json()["run_name"] == "my_run-1"


def test_the_platforms_are_listed(client):
    payload = client.get("/api/llm/platforms").get_json()
    assert len(payload["platforms"]) >= 20
    assert payload["keys_file"].endswith("mie_llm_keys.json")


def test_a_key_is_saved_through_the_api(client):
    response = client.post("/api/llm/key", json={"key": "minimax", "token": "sk-x"})
    assert response.get_json()["has_key"] is True
    assert client.post("/api/llm/model",
                       json={"key": "minimax", "model": "MiniMax-M3"}).get_json()[
                           "model"] == "MiniMax-M3"
    assert client.post("/api/llm/current",
                       json={"key": "minimax"}).get_json()[
                           "current_platform"] == "minimax"


def test_the_plan_preview_lists_the_roles(client):
    library.save_storyboard(dict(SCRIPT))
    response = client.post("/api/scripts/测试剧本/plan",
                           json={"role_assets": {"character_1": "甲"}})
    assert response.status_code == 200
    body = response.get_json()
    assert body["nodes"] > 20
    sockets = [r["socket"] for r in body["requirements"]]
    assert "character_1" in sockets


def test_a_reference_voice_role_must_be_wired(client):
    """The rule the assembler enforces: a role that borrows its voice
    from an asset needs that asset. A generated-voice role does not."""
    reference = json.loads(json.dumps(SCRIPT))
    reference["audio_mode"] = "reference"
    library.save_storyboard(reference)
    response = client.post("/api/render/submit",
                           json={"script": "测试剧本", "role_assets": {}})
    assert response.status_code == 400
    assert "character_1" in response.get_json()["error"]


def test_a_render_without_every_role_is_refused(client):
    library.save_storyboard(dict(SCRIPT))
    response = client.post("/api/render/submit",
                           json={"script": "测试剧本", "role_assets": {}})
    assert response.status_code == 400
    assert "character_1" in response.get_json()["error"]


def test_the_runs_on_disk_are_listed(client, tmp_path, monkeypatch):
    """The live job table is in memory, so a restart empties it. The
    renders are on disk and do not go anywhere, so they are indexed from
    there."""
    import json as _json
    root = tmp_path / "comfy"
    run = root / "output" / "h3_chains" / "sb_web_test" / "final"
    run.mkdir(parents=True)
    (run / "sb_web_test.mp4").write_bytes(b"movie")
    (run.parent.parent / "manifest.json").write_text(
        _json.dumps({"shots": [{"id": "scene_01"}]}), encoding="utf-8")
    monkeypatch.setattr(config, "COMFYUI_ROOT", root)

    runs = comfy.recent_runs()
    assert len(runs) == 1
    assert runs[0]["run_name"] == "sb_web_test"
    assert runs[0]["status"] == "success"
    assert runs[0]["video"].endswith("sb_web_test.mp4")


def test_the_launcher_script_is_in_the_codepage_cmd_reads():
    """cmd.exe reads a .bat in the system ANSI codepage (GBK on a Chinese
    Windows). A UTF-8 file gets its Chinese comments parsed as commands
    and the script falls apart before it starts anything."""
    script = pathlib.Path(__file__).resolve().parents[1] / "start_webui.bat"
    raw = script.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    text = raw.decode("gbk")
    assert "webapp" in text
    # The script must use ComfyUI's own interpreter and must run the
    # web app as a FILE: the embedded python is in isolated mode, where
    # "-m webapp" cannot find the package.
    assert "python_embeded" in text
    assert "__main__.py" in text
    # Only the lines that actually run matter: the comment that explains
    # why "-m webapp" does not work mentions it too.
    commands = "\n".join(
        line for line in text.splitlines()
        if not line.strip().startswith("REM"))
    assert " -m webapp" not in commands


def test_editing_only_the_description_keeps_the_voice(client):
    """A partial update must not wipe the fields the caller did not send.

    The library requires a non-empty voice, so sending an empty one
    instead of "keep what is on disk" turned an edit into a 400.
    """
    created = client.post(
        "/api/assets/character",
        data={"name": "部分更新", "description": "第一版", "voice": "male voice",
              "image": (io.BytesIO(PNG_1x1), "portrait.png")},
        content_type="multipart/form-data")
    assert created.status_code == 200, created.get_json()

    edited = client.post("/api/assets/character",
                         json={"name": "部分更新", "description": "第二版"})
    assert edited.status_code == 200, edited.get_json()
    data = client.get("/api/assets/character/部分更新").get_json()
    assert data["description"] == "第二版"
    assert data["voice"] == "male voice", "the voice must survive a text-only edit"
    assert data["image_count"] == 1, "the portrait must survive too"


# --------------------------------------------------------------------------- #
# the English mirror can be brought back in step, and only on request
# --------------------------------------------------------------------------- #

def test_the_mirror_draft_carries_the_difference(client, monkeypatch):
    """The author sees what would change before anything is replaced."""
    from webapp.services import autodraft
    script = json.loads(json.dumps(SCRIPT))
    script["look"] = "手绘动画风格"
    script["setting"] = "角色1站在中间"
    script["source_script"] = "角色1：你好"
    script["shots"] = [{"id": "shot_1", "events": [
        {"id": "event_001", "type": "dialogue", "character": "character_1",
         "text": "你好", "language": "zh"}]}]
    script["execution"] = {
        "look": "An old look.", "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    }
    stub = _StubConnector([json.dumps({
        "look": "A hand-drawn animation look.",
        "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center, facing the camera.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    })])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/scripts/测试剧本/execution/draft",
                           json={"json": script})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["execution"]["look"] == "A hand-drawn animation look."
    fields = {row["field"]: row for row in data["diff"]}
    assert "look" in fields, data["diff"]
    assert fields["look"]["before"] == "An old look."
    assert fields["look"]["after"] == "A hand-drawn animation look."
    # 没变的字段不进差异表。
    assert "setting" not in fields, data["diff"]


def test_a_mirror_that_breaks_the_contract_is_refused(client, monkeypatch):
    """An English mirror the contract rejects would be rejected by the
    engine later, with a message nobody here would understand."""
    from webapp.services import autodraft
    script = json.loads(json.dumps(SCRIPT))
    script["shots"] = [{"id": "shot_1", "events": [
        {"id": "event_001", "type": "action", "character": "character_1",
         "text": "他挥了挥手", "language": "zh"}]}]
    stub = _StubConnector([json.dumps({
        "look": "", "setting": "",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": ""},
        "directions": {},          # the action event has no direction
    })])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    response = client.post("/api/scripts/测试剧本/execution/draft",
                           json={"json": script})
    assert response.status_code == 400, response.get_json()
    assert "契约" in response.get_json()["error"], response.get_json()


def test_the_draft_never_writes_to_the_library(client, monkeypatch):
    from webapp.services import autodraft
    script = json.loads(json.dumps(SCRIPT))
    script["shots"] = [{"id": "shot_1", "events": [
        {"id": "event_001", "type": "dialogue", "character": "character_1",
         "text": "你好", "language": "zh"}]}]
    script["execution"] = {
        "look": "Old.", "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    }
    stub = _StubConnector([json.dumps({
        "look": "New.", "setting": "Character 1 stands at center.",
        "visible_state": "Character 1 stands at center.",
        "voices": {"character_1": "young female, clear timbre"},
        "directions": {},
    })])
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)

    client.post("/api/scripts/测试剧本/execution/draft", json={"json": script})
    # 库里还是空的：草稿要作者看过、再按正常的保存走。
    assert client.get("/api/scripts").get_json()["names"] == []


# --------------------------------------------------------------------------- #
# the mirror's staleness is the server's truth, not the page's memory
# --------------------------------------------------------------------------- #

def test_editing_the_source_after_the_confirmation_makes_it_stale(client):
    """The complaint this answers: "I changed the look, saved, and the
    stale notice went away - but the English never changed."

    The confirmation is bound to the version it was made against, so
    the state stays "changed" no matter what the page remembers.
    """
    script = json.loads(json.dumps(SCRIPT))
    script["look"] = "手绘动画风格"
    # 英文镜像留着旧文案：这就是作者改完中文、还没更新英文的那份。
    script["execution"]["look"] = "An older animation look."
    saved = client.post("/api/scripts", json={"json": script}).get_json()
    assert saved.get("name"), saved
    name = saved["name"]

    confirmed = client.post("/api/scripts/%s/confirm_execution" % name)
    assert confirmed.status_code == 200, confirmed.get_json()
    state = client.get("/api/scripts/%s" % name).get_json()
    assert state["execution_state"] == "confirmed", state["execution_state"]

    moved = json.loads(json.dumps(script))
    moved["look"] = "换成了另一种画风"
    client.post("/api/scripts", json={"json": moved})
    state = client.get("/api/scripts/%s" % name).get_json()
    assert state["execution_state"] == "changed", state["execution_state"]
    assert "旧" in state["execution_reason"], state["execution_reason"]


def test_saving_alone_does_not_confirm_the_mirror(client):
    """Saving writes the source. It says nothing about the English."""
    script = json.loads(json.dumps(SCRIPT))
    script["look"] = "手绘动画风格"
    script["execution"]["look"] = "An older animation look."
    saved = client.post("/api/scripts", json={"json": script}).get_json()
    assert saved.get("name"), saved
    name = saved["name"]
    client.post("/api/scripts/%s/confirm_execution" % name)

    moved = json.loads(json.dumps(script))
    moved["look"] = "又改了"
    client.post("/api/scripts", json={"json": moved})
    state = client.get("/api/scripts/%s" % name).get_json()
    assert state["execution_state"] != "confirmed"


# --------------------------------------------------------------------------- #
# every generation is a version of its own
# --------------------------------------------------------------------------- #

def test_two_generations_get_two_run_names(client, monkeypatch):
    from webapp.services import runner
    script = json.loads(json.dumps(SCRIPT))
    client.post("/api/scripts", json={"json": script})
    name = client.get("/api/scripts").get_json()["names"][0]

    calls = {"n": 0}

    def submit(graph, client_id=""):
        calls["n"] += 1
        return {"prompt_id": "p-%d" % calls["n"], "number": calls["n"]}

    monkeypatch.setattr(runner.comfy, "submit", submit)
    runner._jobs.clear()
    runner._reserved.clear()

    first = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"}}).get_json()
    second = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"}}).get_json()
    assert first["run_name"] != second["run_name"], \
        "the second generation overwrites the first"
    assert "第 2 版" in second["title"], second["title"]
    runner._jobs.clear()
    runner._reserved.clear()


def test_a_continuation_keeps_the_name_and_the_inputs(client, monkeypatch):
    from webapp.services import runner
    script = json.loads(json.dumps(SCRIPT))
    client.post("/api/scripts", json={"json": script})
    name = client.get("/api/scripts").get_json()["names"][0]
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-1",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()

    first = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "width": 960, "height": 544, "steps": 20, "duration": 18}).get_json()
    runner._jobs[first["job_id"]]["status"] = "error"
    again = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "run_name": first["run_name"], "continue_run": True,
        "width": 960, "height": 544, "steps": 20, "duration": 18}).get_json()
    assert again["run_name"] == first["run_name"]
    assert again["params"]["duration"] == 18
    runner._jobs.clear()
    runner._reserved.clear()
