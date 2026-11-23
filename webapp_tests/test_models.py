"""Model discovery, the render presets, and the base seed.

What ComfyUI can load is read from ComfyUI's own node registry - never
from a table of file names - and only a value that registry offers is
ever sent.
"""

from __future__ import annotations

import io
import json

import pytest

from webapp.services import comfy, models, workflow

from conftest import PNG_1x1
from test_library import SCRIPT


FAKE_OBJECT_INFO = {
    "UNETLoader": {"input": {"required": {
        "unet_name": [["MiniMax-H3/h3.safetensors", "other/h3b.safetensors"]],
        "weight_dtype": [["default", "fp8_e4m3fn"], {}]}}},
    "CLIPLoader": {"input": {"required": {
        "clip_name": [["MiniMax-H3/qwen3vl.safetensors"]],
        "type": [["minimax", "wan"], {}]}}},
    "VAELoader": {"input": {"required": {
        "vae_name": [["MiniMax-H3/video_vae.safetensors",
                      "MiniMax-H3/audio_vae.safetensors"]]}}},
    "ModelAttentionBackend": {"input": {"required": {
        "attention": [["pytorch attention", "comfy kitchen attention"], {}]}}},
    "MiniMaxH3ChainPlanModern|MieH3": {"input": {"required": {
        "base_seed": ["INT", {"default": 0, "min": 0,
                              "max": 18446744073709551615}]}}},
}


@pytest.fixture()
def online(monkeypatch):
    """ComfyUI is up and says what it has."""
    monkeypatch.setattr(comfy, "object_info", lambda *a, **k: FAKE_OBJECT_INFO)
    return FAKE_OBJECT_INFO


# --------------------------------------------------------------------------- #
# the slots come from the graph
# --------------------------------------------------------------------------- #

def test_the_slots_are_read_from_the_graph():
    """Which node plays which part is the graph's business, not a table
    of file names."""
    graph = workflow.preset()
    found = models.slots(graph)
    assert found["unet"]["node_id"] == "5"
    assert found["clip"]["node_id"] == "20"
    assert found["attention"]["node_id"] == "27"
    assert found["base_seed"]["node_id"] == "3"
    # The video VAE is the one wired into the sampler's "vae" input, and
    # the audio VAE the one wired into "audio_vae".
    assert found["video_vae"]["node_id"] == "22"
    assert found["audio_vae"]["node_id"] == "21"
    assert found["video_vae"]["current"].endswith("video_vae_fp16.safetensors")
    assert found["audio_vae"]["current"].endswith("audio_vae_fp32.safetensors")


def test_the_options_are_the_ones_comfyui_offers(online):
    found = models.discover(workflow.preset())
    assert found["online"] is True
    assert found["slots"]["unet"]["options"] == [
        "MiniMax-H3/h3.safetensors", "other/h3b.safetensors"]
    assert found["slots"]["attention"]["options"] == [
        "pytorch attention", "comfy kitchen attention"]
    assert "MiniMax-H3/video_vae.safetensors" in found["slots"]["video_vae"]["options"]


def test_the_seed_bounds_come_from_the_node(online):
    found = models.discover(workflow.preset())
    assert found["seed"]["min"] == "0"
    assert found["seed"]["max"] == "18446744073709551615"


def test_offline_the_list_is_empty_and_the_reason_is_said(monkeypatch):
    monkeypatch.setattr(comfy, "object_info", lambda *a, **k: {})
    found = models.discover(workflow.preset())
    assert found["online"] is False
    assert found["slots"]["unet"]["options"] == []
    assert "ComfyUI" in found["error"]


# --------------------------------------------------------------------------- #
# the settings
# --------------------------------------------------------------------------- #

def test_an_unsupported_model_is_refused(online):
    with pytest.raises(ValueError) as error:
        models.check({"unet": "not/a-model.safetensors"})
    assert "扩散模型" in str(error.value)


def test_a_supported_model_passes(online):
    result = models.check({"unet": "other/h3b.safetensors"})
    assert result["settings"]["unet"] == "other/h3b.safetensors"


def test_the_seed_keeps_zero(online):
    """0 is a seed, not "no seed"."""
    assert models.normalize({"base_seed": 0})["base_seed"] == "0"
    assert models.normalize({"base_seed": "0"})["base_seed"] == "0"


def test_a_big_seed_keeps_every_digit(online):
    """The engine's seed goes up to 2^64-1, which no double can hold."""
    big = "18446744073709551615"
    assert models.normalize({"base_seed": big})["base_seed"] == big
    models.check({"base_seed": big})
    with pytest.raises(ValueError):
        models.check({"base_seed": "18446744073709551616"})


def test_a_seed_that_is_not_a_number_is_refused():
    with pytest.raises(ValueError):
        models.normalize({"base_seed": "十二"})


def test_the_chosen_models_reach_the_graph(online):
    graph = workflow.preset()
    models.apply(graph, {"unet": "other/h3b.safetensors",
                         "attention": "pytorch attention",
                         "base_seed": "42"}, models.discover(graph))
    assert graph["5"]["inputs"]["unet_name"] == "other/h3b.safetensors"
    assert graph["27"]["inputs"]["attention"] == "pytorch attention"
    assert graph["3"]["inputs"]["base_seed"] == 42


def test_an_unknown_model_is_not_written_into_the_graph(online):
    """Only what ComfyUI offers is sent."""
    graph = workflow.preset()
    before = graph["5"]["inputs"]["unet_name"]
    models.apply(graph, {"unet": "not/a-model.safetensors"},
                 models.discover(graph))
    assert graph["5"]["inputs"]["unet_name"] == before


def test_the_graph_edges_are_not_touched(online):
    """The chosen models change inputs, never the wiring."""
    graph = workflow.preset()
    before = {node_id: dict(node.get("inputs") or {})
              for node_id, node in graph.items()}
    models.apply(graph, {"unet": "other/h3b.safetensors",
                         "base_seed": "7"}, models.discover(graph))
    for node_id, node in graph.items():
        after = node.get("inputs") or {}
        for key, value in before[node_id].items():
            if key in ("unet_name", "base_seed"):
                continue
            assert after.get(key) == value, (node_id, key)


# --------------------------------------------------------------------------- #
# the presets
# --------------------------------------------------------------------------- #

def test_the_quality_presets_ship_with_the_workbench():
    presets = models.presets()
    names = [item["name"] for item in presets]
    assert "快速" in names and "标准" in names and "高质" in names
    quick = next(item for item in presets if item["name"] == "快速")
    assert quick["settings"]["steps"] == 12
    assert quick["builtin"] is True


def test_a_preset_is_saved_and_read_back(client, plugin_root):
    from webapp import config

    response = client.post("/api/render/presets", json={
        "name": "我的高质", "settings": {"unet": "other/h3b.safetensors",
                                        "steps": 28, "base_seed": "0"}})
    assert response.status_code == 200, response.get_json()
    saved = response.get_json()
    assert saved["name"] == "我的高质"
    assert saved["settings"]["steps"] == 28
    listing = client.get("/api/render/presets").get_json()["presets"]
    mine = [item for item in listing if item["name"] == "我的高质"]
    assert mine and mine[0]["settings"]["unet"] == "other/h3b.safetensors"
    # It is on disk, next to the other settings.
    on_disk = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert "我的高质" in on_disk["render_presets"]


def test_a_preset_without_a_name_is_refused(client):
    response = client.post("/api/render/presets",
                            json={"name": "  ", "settings": {"steps": 20}})
    assert response.status_code == 400
    assert "名字" in response.get_json()["error"]


def test_a_preset_can_be_deleted(client):
    client.post("/api/render/presets", json={"name": "临时",
                                              "settings": {"steps": 12}})
    assert client.delete("/api/render/presets/%E4%B8%B4%E6%97%B6"
                          ).status_code == 200
    listing = client.get("/api/render/presets").get_json()["presets"]
    assert "临时" not in [item["name"] for item in listing]


def test_deleting_a_preset_that_is_not_there_is_a_404(client):
    assert client.delete("/api/render/presets/nope").status_code == 404


# --------------------------------------------------------------------------- #
# the round trip: page -> submit -> history -> remake
# --------------------------------------------------------------------------- #

def _capture_submit(monkeypatch):
    captured = {}

    def submit(graph, client_id=""):
        captured["graph"] = graph
        captured["client_id"] = client_id
        return {"prompt_id": "p-1", "number": 1}

    monkeypatch.setattr(comfy, "submit", submit)
    return captured


def test_the_submission_carries_the_chosen_models(client, monkeypatch, online):
    from webapp.services import runner

    captured = _capture_submit(monkeypatch)
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    name = client.get("/api/scripts").get_json()["names"][0]
    runner._jobs.clear()
    runner._reserved.clear()
    response = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "settings": {"unet": "other/h3b.safetensors",
                     "attention": "pytorch attention",
                     "base_seed": "12345"}})
    assert response.status_code == 200, response.get_json()
    graph = captured["graph"]
    assert graph["5"]["inputs"]["unet_name"] == "other/h3b.safetensors"
    assert graph["27"]["inputs"]["attention"] == "pytorch attention"
    assert graph["3"]["inputs"]["base_seed"] == 12345
    # The task record keeps them, so the history can show them.
    record = response.get_json()
    assert record["params"]["render"]["unet"] == "other/h3b.safetensors"
    assert record["params"]["render"]["base_seed"] == "12345"
    runner._jobs.clear()
    runner._reserved.clear()


def test_an_unsupported_model_is_refused_before_anything_is_recorded(
        client, monkeypatch, online):
    from webapp.services import runner

    _capture_submit(monkeypatch)
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    name = client.get("/api/scripts").get_json()["names"][0]
    runner._jobs.clear()
    runner._reserved.clear()
    response = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "settings": {"unet": "not/a-model.safetensors"}})
    assert response.status_code == 400
    assert "扩散模型" in response.get_json()["error"]
    assert not runner._jobs, "a wrong choice must not leave a task behind"
    runner._jobs.clear()
    runner._reserved.clear()


def test_a_continuation_uses_the_saved_settings(client, monkeypatch, online):
    """Continuing a run must not silently switch models."""
    from webapp.services import runner

    captured = _capture_submit(monkeypatch)
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    name = client.get("/api/scripts").get_json()["names"][0]
    runner._jobs.clear()
    runner._reserved.clear()
    first = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "settings": {"unet": "other/h3b.safetensors"}}).get_json()
    runner._jobs[first["job_id"]]["status"] = "error"
    # The continuation asks for a DIFFERENT model on purpose: the saved
    # one must win.
    second = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"},
        "continue_run": True, "continue_from": first["job_id"],
        "settings": {"unet": "MiniMax-H3/h3.safetensors"}}).get_json()
    assert second["params"]["render"]["unet"] == "other/h3b.safetensors"
    assert captured["graph"]["5"]["inputs"]["unet_name"] == \
        "other/h3b.safetensors"
    runner._jobs.clear()
    runner._reserved.clear()


def test_the_plan_preview_still_works_with_the_render_settings(
        client, monkeypatch, online):
    """The compiled preview must not break when the render settings
    come along."""
    client.post("/api/assets/character", data={
        "name": "甲", "description": "一个角色", "voice": "male voice",
        "image": (io.BytesIO(PNG_1x1), "portrait.png")},
        content_type="multipart/form-data")
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    name = client.get("/api/scripts").get_json()["names"][0]
    response = client.post("/api/scripts/%s/plan" % name, json={
        "role_assets": {"character_1": "甲"},
        "settings": {"unet": "other/h3b.safetensors", "base_seed": "9"}})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["compiled_plan"]
    assert len(body["plan_sha256"]) == 8
    assert body["render"]["unet"] == "other/h3b.safetensors"


def test_the_models_endpoint_answers(client, monkeypatch, online):
    body = client.get("/api/render/models").get_json()
    assert body["online"] is True
    assert body["slots"]["unet"]["options"]
    assert body["seed"]["max"] == "18446744073709551615"
    assert any(item["name"] == "标准" for item in body["presets"])
