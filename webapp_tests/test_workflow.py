"""The workflow builder and the LLM platform settings."""

from __future__ import annotations

import pytest

from webapp.services import llm, library, workflow

from conftest import PNG_1x1, WAV_1S
from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# the workflow builder
# --------------------------------------------------------------------------- #

def test_the_slots_follow_the_scripts_roles(plugin_root):
    graph = workflow.build("测试剧本", SCRIPT,
                           {"character_1": "甲"}, run_name="run")
    plan = graph["901"]
    assert plan["class_type"] == "MieH3ScriptToPlan"
    assert plan["inputs"]["storyboard"] == ["900", 0]
    assert plan["inputs"]["character_1"] == ["910", 5]
    assert "character_2" not in plan["inputs"]


def test_a_role_without_an_asset_is_refused(plugin_root):
    with pytest.raises(ValueError) as caught:
        workflow.build("测试剧本", SCRIPT, {})
    assert "character_1" in str(caught.value)


def test_the_plan_node_takes_the_assembled_plan(plugin_root):
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"},
                           run_name="run")
    plan_node = graph["3"]
    assert plan_node["inputs"]["plan_json_input"] == ["901", 0]
    assert plan_node["inputs"]["run_name"] == "run"


def test_pictures_count_the_on_screen_roles_only(plugin_root):
    # Real characters: a voice slot is only wired for an asset that
    # really carries a sample - a name alone carries nothing.
    library.save_character("甲", "一个测试角色", "male voice",
                           image_raw=PNG_1x1, audio_raw=WAV_1S)
    library.save_character("乙", "一个测试角色", "female voice",
                           image_raw=PNG_1x1, audio_raw=WAV_1S)
    two = json_copy(SCRIPT)
    two["roles"].append({
        "id": "character_2", "name": "乙", "presence": "off_screen",
        "initially_visible": False, "position": "none", "voice": "female voice",
    })
    graph = workflow.build("测试剧本", two,
                           {"character_1": "甲", "character_2": "乙"})
    sampler = graph["47"]
    # one picture (甲 is on screen), two voices (both roles speak)
    assert sampler["inputs"]["ref_images.ref_image_0"] == ["902", 1]
    assert "ref_images.ref_image_1" not in sampler["inputs"]
    assert sampler["inputs"]["ref_audios.ref_audio_0"] == ["902", 11]
    assert sampler["inputs"]["ref_audios.ref_audio_1"] == ["902", 12]


def test_the_finished_file_is_named_after_the_run(plugin_root):
    """The Assemble node's filename must follow the run name, or the file
    lands under one name while the run lives under another."""
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"},
                           run_name="sb_web_20261107")
    assembler = next(n for n in graph.values()
                     if n["class_type"].endswith("ChainAssemble|MieH3"))
    assert assembler["inputs"]["filename"].startswith("sb_web_20261107")


def test_the_preset_is_not_mutated_between_builds(plugin_root):
    workflow.build("测试剧本", SCRIPT, {"character_1": "甲"})
    second = workflow.build("测试剧本", SCRIPT, {"character_1": "乙"})
    assert second["910"]["inputs"]["name"] == "乙"


def test_no_link_points_at_a_node_the_build_replaced(plugin_root):
    """The build replaces the preset's asset section, so the links that
    pointed at the old nodes have to follow their replacements. A
    dangling link is not a warning: ComfyUI rejects that node when it
    validates the prompt, and the submission comes back with a
    validation error for it."""
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"})
    dangling = []
    for node_id, node in graph.items():
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 \
                    and isinstance(value[0], str) and value[0] not in graph:
                dangling.append((node_id, key, value))
    assert dangling == [], dangling


def test_legacy_preset_output_follows_the_new_plan_node(plugin_root):
    preset = workflow._preset()
    old_plan = next(key for key, node in preset.items()
                    if node["class_type"] == "MieH3ScriptToPlan")
    preset["50"] = {"class_type": "ShowAndSaveAnything|Mie",
                    "inputs": {"anything": [old_plan, 0]}}
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"}, workflow=preset)
    assert graph["50"]["inputs"]["anything"] == ["901", 0]


def json_copy(value):
    import json
    return json.loads(json.dumps(value))


# --------------------------------------------------------------------------- #
# the LLM platforms
# --------------------------------------------------------------------------- #

def test_every_platform_is_listed(plugin_root):
    rows = llm.list_platforms()
    assert len(rows) >= 20
    keys = {row["key"] for row in rows}
    assert {"minimax", "siliconflow", "openai", "anthropic", "ollama"} <= keys
    for row in rows:
        assert "name" in row and "connector" in row
        assert row["has_key"] is False, "a fresh install has no keys"


def test_a_key_is_written_to_the_plugins_file(plugin_root):
    llm.set_key("minimax", "sk-test-123")
    raw = (plugin_root / "mie_llm_keys.json").read_text(encoding="utf-8")
    assert "sk-test-123" in raw
    assert llm.read_keys()["minimax"] == "sk-test-123"
    row = next(r for r in llm.list_platforms() if r["key"] == "minimax")
    assert row["has_key"] is True
    assert "sk-test-123" not in row["key_masked"], "the key must not be shown whole"


def test_the_model_and_the_current_platform_are_stored(plugin_root):
    llm.set_model("minimax", "MiniMax-M3")
    llm.set_current("minimax")
    assert llm.model_for("minimax") == "MiniMax-M3"
    assert llm.current_platform() == "minimax"
    row = next(r for r in llm.list_platforms() if r["key"] == "minimax")
    assert row["model"] == "MiniMax-M3"
    assert row["current"] is True


def test_a_platform_falls_back_to_its_own_default_model(plugin_root):
    row = next(r for r in llm.list_platforms() if r["key"] == "kimi")
    assert llm.model_for("kimi") == row["default_model"]


def test_a_token_is_masked(plugin_root):
    assert llm.mask("") == ""
    assert llm.mask("short") == "*****"
    masked = llm.mask("abcdefghijklmnop")
    assert masked.startswith("abcdef") and masked.endswith("(16)")


def test_default_render_has_no_mienodes_requirement(plugin_root):
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"}, scene_asset="客厅")
    assert graph["910"]["class_type"] == "MieH3LoadAsset"
    assert graph["908"]["class_type"] == "MieH3LoadAsset"
    assert graph["902"]["class_type"] == "MieH3AssetToSlots"
    assert not any(node["class_type"].endswith("|Mie") for node in graph.values())


def test_render_preflight_names_missing_nodes_without_submitting(monkeypatch):
    from webapp.services import comfy

    def request(url):
        assert url.endswith("/object_info")
        return {"ExistingNode": {}}

    monkeypatch.setattr(comfy, "_json", request)
    with pytest.raises(comfy.ComfyUnavailable, match="MissingNode"):
        comfy.submit({"1": {"class_type": "MissingNode"}})
