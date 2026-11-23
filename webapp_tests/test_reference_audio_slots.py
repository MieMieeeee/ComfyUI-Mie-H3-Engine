"""The voices the sampler is wired with.

The slot node COMPACTS the voices: it keeps the characters that carry a
voice sample and drops the ones that have none, so voice 1 is the first
character WITH a sample - not the first character. The graph has to be
wired in that same order, and it has to wire the voices that really
exist - a voice slot that resolves to nothing is not a reference.

The engine's own contract decides the rest: in reference-audio mode
every wired character has to carry a voice sample (the plan assembler
refuses the board otherwise), and there are only three voice slots.
"""

from __future__ import annotations

import json

import pytest

from webapp import config
from webapp.services import library, workflow

from conftest import PNG_1x1, WAV_1S
from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _character(name: str, with_voice: bool = True) -> None:
    """A character in the throwaway library, with or without a sample."""
    library.save_character(name, "一个测试角色", "male voice", image_raw=PNG_1x1,
                           audio_raw=WAV_1S if with_voice else None)


def _broken_character(name: str) -> None:
    """A character whose metadata cannot be read.

    The asset IS there - the folder and the file are where the library
    expects them - but the file is not JSON. That is what a half
    written or corrupted asset looks like, and it must not be read as
    "this character has a voice".
    """
    folder = config.PLUGIN_ROOT / "asset_library" / "characters" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "character.json").write_text("{ this is not json",
                                           encoding="utf-8")


def _script(audio_mode: str = "reference", roles: int = 2) -> dict:
    """A board with ``roles`` characters: 甲 on screen, the rest voices."""
    script = json.loads(json.dumps(SCRIPT))
    script["audio_mode"] = audio_mode
    for index in range(2, roles + 1):
        script["roles"].append({
            "id": "character_%d" % index,
            "name": "角色%d" % index,
            "presence": "off_screen",
            "initially_visible": False,
            "position": "none",
            "voice": "female voice",
        })
    return script


def _assets(count: int = 2) -> dict:
    return {"character_%d" % index: "角色%d" % index
            for index in range(1, count + 1)}


def _voice_inputs(graph: dict) -> list:
    return sorted(key for key in graph["47"]["inputs"]
                  if key.startswith("ref_audios."))


# --------------------------------------------------------------------------- #
# the wiring
# --------------------------------------------------------------------------- #

def test_every_voice_gets_exactly_one_slot(plugin_root):
    _character("角色1")
    _character("角色2")
    script = _script("reference")
    graph = workflow.build("测试剧本", script, _assets())
    sampler = graph["47"]
    assert sampler["inputs"]["ref_audios.ref_audio_0"] == ["902", 11]
    assert sampler["inputs"]["ref_audios.ref_audio_1"] == ["902", 12]
    # A third slot that resolves to nothing is not a reference voice.
    assert "ref_audios.ref_audio_2" not in sampler["inputs"]


def test_a_character_without_a_sample_gets_no_voice_slot(plugin_root):
    """The voice slots follow the assets that really have a sample."""
    _character("角色1")
    _character("角色2", with_voice=False)
    script = _script("generated")
    graph = workflow.build("测试剧本", script, _assets())
    assert _voice_inputs(graph) == ["ref_audios.ref_audio_0"]


def test_the_voices_follow_the_slot_nodes_compact_order(plugin_root):
    """The slot node drops the characters that have no sample, so the
    first voice is the first character WITH one - not the first
    character."""
    _character("角色1", with_voice=False)
    _character("角色2")
    script = _script("generated")
    nodes = [(role, str(910 + index), "角色%d" % (index + 1))
             for index, role in enumerate(script["roles"])]
    voices = workflow.reference_voices(nodes, script)
    assert [name for _role, name in voices] == ["角色2"]
    # And the graph is wired that way: voice 1 is the second character.
    graph = workflow.build("测试剧本", script, _assets())
    sampler = graph["47"]
    assert sampler["inputs"]["ref_audios.ref_audio_0"] == ["902", 11]
    assert "ref_audios.ref_audio_1" not in sampler["inputs"]


def test_a_missing_voice_sample_is_refused_in_reference_mode(plugin_root):
    """The engine refuses a reference board whose character has no
    sample. The graph says so here, where the author can act on it -
    it does NOT quietly wire the next character's voice as voice 1."""
    _character("角色1", with_voice=False)
    _character("角色2")
    script = _script("reference")
    with pytest.raises(ValueError) as caught:
        workflow.build("测试剧本", script, _assets())
    message = str(caught.value)
    assert "角色1" in message, message
    assert "声音样本" in message, message


def test_more_voices_than_the_slots_are_refused(plugin_root):
    """The engine has three voice slots and drops the rest without a
    word. Refuse here instead, and say which characters."""
    for index in range(1, 5):
        _character("角色%d" % index)
    script = _script("reference", roles=4)
    with pytest.raises(ValueError) as caught:
        workflow.build("测试剧本", script, _assets(4))
    message = str(caught.value)
    assert "at most 3 character slots" in message, message
    assert "角色4" in message, message


def test_generated_audio_does_not_need_a_voice_sample(plugin_root):
    """Generated audio: the engine makes the voice, so a character
    without a sample is not a reason to refuse the board."""
    _character("角色1", with_voice=False)
    _character("角色2", with_voice=False)
    script = _script("generated")
    graph = workflow.build("测试剧本", script, _assets())
    assert _voice_inputs(graph) == []


def test_the_preset_is_not_left_with_its_own_voices(plugin_root):
    """The shipped preset carries two voices of its own. A board with
    one character must not inherit them."""
    _character("角色1")
    script = _script("reference", roles=1)
    graph = workflow.build("测试剧本", script, {"character_1": "角色1"})
    assert _voice_inputs(graph) == ["ref_audios.ref_audio_0"]


def test_a_legacy_preset_is_rewired_the_same_way(plugin_root):
    """The same board, built on an older preset that has its own
    references: the stale ones go, the real ones come."""
    _character("角色1")
    _character("角色2")
    script = _script("reference")
    preset = workflow._preset()
    preset["47"]["inputs"]["ref_images.ref_image_5"] = ["57", 6]
    preset["47"]["inputs"]["ref_audios.ref_audio_2"] = ["57", 13]
    graph = workflow.build("测试剧本", script, _assets(), workflow=preset)
    sampler = graph["47"]
    assert "ref_images.ref_image_5" not in sampler["inputs"]
    assert "ref_audios.ref_audio_2" not in sampler["inputs"]
    assert sampler["inputs"]["ref_audios.ref_audio_0"] == ["902", 11]
    assert sampler["inputs"]["ref_audios.ref_audio_1"] == ["902", 12]
    # The pictures are still the on-screen characters' - and the scene's.
    assert sampler["inputs"]["ref_images.ref_image_0"] == ["902", 1]


# --------------------------------------------------------------------------- #
# an asset the app cannot read
# --------------------------------------------------------------------------- #

def test_an_unreadable_asset_is_refused_in_reference_mode(plugin_root):
    """A character whose metadata cannot be read is not a character with
    a voice. The board is refused - with the reason, so the author can
    go and fix the asset."""
    _broken_character("角色1")
    _character("角色2")
    script = _script("reference")
    with pytest.raises(ValueError) as caught:
        workflow.build("测试剧本", script, _assets())
    message = str(caught.value)
    assert "角色1" in message, message
    assert "读不出来" in message, message


def test_an_asset_that_is_not_in_the_library_is_refused_in_reference_mode(
        plugin_root):
    """A name that is not in the library is not a character either. The
    board is refused instead of being built on air."""
    script = _script("reference")
    with pytest.raises(ValueError) as caught:
        workflow.build("测试剧本", script, _assets())
    message = str(caught.value)
    assert "不在资产库里" in message, message


def test_the_submission_check_says_the_same_thing(plugin_root):
    """The queue check and the graph builder refuse the same board in
    the same words: the author is not left guessing which one spoke."""
    from webapp.services import runner

    _broken_character("角色1")
    script = _script("reference")
    with pytest.raises(ValueError) as caught:
        runner._check_reference_audio(script, {"character_1": "角色1"})
    message = str(caught.value)
    assert "角色1" in message, message
    assert "读不出来" in message, message


def test_generated_audio_wires_no_voice_it_could_not_read(plugin_root):
    """Generated audio: the engine makes the voices. An asset the app
    cannot read carries no voice - it is not a voice slot, and it is
    not a reason to refuse the board."""
    _broken_character("角色1")
    _character("角色2")
    script = _script("generated")
    graph = workflow.build("测试剧本", script, _assets())
    assert _voice_inputs(graph) == ["ref_audios.ref_audio_0"]
