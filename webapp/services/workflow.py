"""Build the render workflow for a script and a set of assets.

The chain (plan -> preflight -> loop -> sample -> assemble) is fixed and
comes from the shipped preset. The part that changes per run is the
asset section: which script, which character asset in which slot, and
which reference picture and voice go to the sampler.

The slots follow the SCRIPT's roles, not a hard-coded pair. A role's
asset is picked by the user; a role with no asset wired is a slot the
assembler will not accept (the plan assembler refuses to run without a
character asset per slot), so the caller has to fill every on-screen
role and every voice role.

Reference slots: pictures are numbered over the ON-SCREEN roles only
(the assembler counts portraits, not people), voices over the characters
that actually carry a voice sample, in the order the slot node numbers
them. The sampler's ``ref_images`` / ``ref_audios`` inputs autogrow, so
they are wired by name.
"""

from __future__ import annotations

import json
import pathlib

from webapp import config
from webapp.services import library, runname

#: Node ids for the generated asset section. They are high enough not to
#: clash with the preset's chain nodes.
_ID_SCRIPT = "900"
_ID_PLAN = "901"
_ID_SLOTS = "902"
_ID_ROSTER = "903"
_ID_SCENE = "908"
_ID_ASSET_BASE = 910

#: How many reference voices the sampler takes. The engine's own limit
#: (the slot node's ``MAX_AUDIOS``) is the same number, and the script
#: contract refuses a reference-audio board with more than three roles.
MAX_REFERENCE_AUDIOS = 3

#: The slot node's first voice output. Its outputs are: the picture
#: count, nine pictures, the voice count, then the voices.
_VOICE_SLOT_BASE = 11

#: The engine's own Review Gate node. Its type name carries the fork
#: suffix, but a preset written before the rename carries the plain
#: one - both are accepted, the way the engine accepts both.
_REVIEW_NODE_TYPES = ("MiniMaxH3ChainReview|MieH3", "MiniMaxH3ChainReview")


def _preset() -> dict:
    """The shipped workflow, as {node_id: node}."""
    path = pathlib.Path(config.DEFAULT_WORKFLOW)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def preset() -> dict:
    """The shipped workflow, as {node_id: node}.

    A copy, so a caller may look at it (which node loads which model)
    without touching the file on disk.
    """
    return json.loads(json.dumps(_preset()))


def _node(class_type: str, inputs: dict, title: str, cnr_id: str) -> dict:
    return {
        "class_type": class_type,
        "inputs": inputs,
        "_meta": {"title": title, "cnr_id": cnr_id, "ver": "1"},
    }


def script_roles(script_json: dict) -> list[dict]:
    """The script's roles, in slot order."""
    return list(script_json.get("roles") or [])


def audio_mode(script_json: dict) -> str:
    """The board's audio mode.

    ``generated`` means the engine makes the voices itself; ``reference``
    means every wired character has to carry a voice sample. A board
    that says neither is read the way the script contract reads it: an
    authored (v2) board generates its voices, an older template board
    references them.
    """
    script = script_json or {}
    mode = str(script.get("audio_mode") or "").strip()
    if mode in ("generated", "reference"):
        return mode
    try:
        version = int(script.get("version") or 2)
    except (TypeError, ValueError):
        version = 2
    return "generated" if version >= 2 else "reference"


def no_voice_sample(role_name: str, asset: str) -> str:
    """What a character without a voice sample is told.

    One wording for the graph builder and for the submission check: the
    author has to be able to act on it, and the second refusal of the
    same board must not say something different than the first one.
    """
    return ("参考配音模式：角色「%s」接的资产「%s」没有声音样本："
            "参考配音要用角色的样本生成声音，先去资产页上传。"
            % (role_name, asset))


def unreadable_asset(asset: str, reason: str) -> str:
    """What an asset the app cannot read is told.

    An asset whose metadata cannot be read is not a character without a
    voice - it is an asset the app knows nothing about. The reason is
    in the message, because the author is the one who can fix it.
    """
    return ("角色资产「%s」读不出来（%s）：参考配音要用角色自己的声音样本，"
            "先去资产页确认这个资产还在、并且能打开，再回来渲染。"
            % (asset, reason))


def voice_sample_count(asset_name: str, asset_library_dir: str = "") -> int:
    """How many voice samples this character carries.

    The metadata read is the cheap one: it counts the clips without
    encoding the pictures (the picture bytes are megabytes, and this
    path only needs to know whether there IS a voice).

    A read that FAILS is raised, not guessed away: guessing "it has a
    voice" wires the wrong voice onto a character, and guessing "it has
    none" refuses a board that would have rendered. The failure says
    what went wrong, so the author can act on it.
    """
    try:
        meta = library.get_asset_meta(asset_name, "character",
                                      asset_dir=asset_library_dir)
    except FileNotFoundError as exc:
        raise ValueError(
            unreadable_asset(asset_name, "资产不在资产库里")) from exc
    except (ValueError, OSError) as exc:
        raise ValueError(unreadable_asset(asset_name, exc)) from exc
    try:
        return int(meta.get("audio_count") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            unreadable_asset(asset_name, "声音样本数读不出来")) from exc


def has_voice(asset_name: str, asset_library_dir: str = "") -> bool:
    """Does this character carry a voice sample?

    An asset whose metadata cannot be read is NOT a character with a
    voice: the answer would be a guess, and a guess puts a voice on the
    wrong character. The read failure is raised - see
    :func:`voice_sample_count`.
    """
    return voice_sample_count(asset_name, asset_library_dir) > 0


def check_reference_voices(script_json: dict, role_assets: dict,
                           asset_library_dir: str = "") -> None:
    """Reference audio needs a voice sample for every wired character.

    This is the ONE place that decides it: the graph builder refuses
    the very same board, and the submission checks it before the queue.
    Both come here, so both say the same thing - a refusal the author
    cannot act on is not a refusal, it is a mystery.

    An asset whose metadata cannot be READ is refused as well: an
    unknown asset is not a character with a voice, and saying so is the
    difference between a fixable mistake and a broken render.
    """
    if not isinstance(script_json, dict) \
            or audio_mode(script_json) != "reference":
        return
    for role in script_json.get("roles") or []:
        asset = str((role_assets or {}).get(role.get("id") or "") or "").strip()
        if not asset:
            continue  # a role without an asset is refused by the builder
        if not voice_sample_count(asset, asset_library_dir):
            raise ValueError(no_voice_sample(
                role.get("name") or role.get("id"), asset))


def reference_voices(asset_nodes: list, script_json: dict,
                     asset_library_dir: str = "") -> list:
    """The voices the sampler is to be wired with, in the slot node's order.

    The slot node COMPACTS the voices: it keeps the characters that have
    a voice sample and drops the ones that have none, so voice 1 is the
    first character WITH a sample - not the first character. The sampler
    has to be wired in that same compact order, or a voice lands on the
    wrong character.

    In generated-audio mode the engine makes the voices itself and the
    runtime roster carries none, so these slots resolve to nothing there;
    the board's own contract still wires one slot per voice sample, and
    that is what this list is.

    An asset whose metadata cannot be read carries NO voice: it is not a
    character with a sample, and a slot wired to it resolves to nothing.
    In reference mode that is refused outright (see
    :func:`check_reference_voices`); in generated mode it only means
    there is no slot to wire.
    """
    strict = audio_mode(script_json) == "reference"
    voices = []
    for role, _node_id, name in asset_nodes:
        try:
            voiced = has_voice(name, asset_library_dir)
        except ValueError:
            if strict:
                raise
            continue
        if voiced:
            voices.append((role, name))
    return voices


def apply_review(nodes: dict, review: dict) -> bool:
    """Turn the engine's own Review Gate on, if the author asked for it.

    The node is already in the preset and already wired; the only thing
    that changes is its ``enabled`` switch. Nothing new is invented
    here: the gate, its candidate rules and its decisions all belong
    to the engine.

    A preset without a Review Gate node is refused rather than quietly
    ignored - the author turned the review on, and a graph that quietly
    renders through anyway would be a lie.
    """
    review = review or {}
    if not review.get("enabled"):
        return False
    node = next((n for n in nodes.values()
                 if n.get("class_type") in _REVIEW_NODE_TYPES), None)
    if node is None:
        raise ValueError("这个工作流里没有审片节点（Review Gate），"
                         "开不了逐镜审片。")
    inputs = node.setdefault("inputs", {})
    inputs["enabled"] = True
    try:
        count = int(review.get("candidate_count") or 1)
    except (TypeError, ValueError):
        count = 1
    inputs["candidate_count"] = max(1, min(20, count))
    return True


def build(script_name: str, script_json: dict,
          role_assets: dict[str, str],
          run_name: str = "",
          width: int = 960, height: int = 544,
          steps: int = 20, duration: int = 18,
          workflow: dict = None, library_dir: str = "",
          scene_asset: str = "", asset_library_dir: str = "",
          review: dict = None) -> dict:
    """The API-format workflow for one run.

    ``role_assets`` maps a role id (``character_1``) to the asset name
    wired into that slot. ``library_dir`` is where the script loader
    reads from - the snapshot library, so a queued run renders the
    script that was submitted, not the one in the library by then.
    ``scene_asset`` wires a scene asset into the plan's scene_1 slot
    (U02): the scene's picture and ambience override the script's own
    scene copy, exactly as the wired characters do for their slots.
    ``review`` is the author's opt-in: ``{"enabled": True}`` turns the
    engine's own Review Gate on, so the run stops after every saved
    shot and waits for a decision. Off (the default) leaves the
    unattended behaviour exactly as it was.
    """
    nodes = json.loads(json.dumps(workflow if workflow is not None else _preset()))
    roles = script_roles(script_json)

    # Understand both shipped and older presets. A custom output referring to
    # the old asset section keeps the same output indices after the rebuild.
    asset_types = {"MieH3LoadAsset", "LoadAssetV2|Mie"}
    slot_types = {"MieH3AssetToSlots", "AssetToSlots|Mie"}
    old_cast_ids = []
    old_scene_ids = []
    replacement = {}
    fixed_ids = {
        "MieH3LoadScriptAsset": _ID_SCRIPT,
        "MieH3ScriptToPlan": _ID_PLAN,
        "MieH3LoadAssetRoster": _ID_ROSTER,
        **dict.fromkeys(slot_types, _ID_SLOTS),
    }
    for node_id, node in list(nodes.items()):
        class_type = node["class_type"]
        if class_type in fixed_ids:
            replacement[node_id] = fixed_ids[class_type]
        elif class_type in asset_types:
            ids = old_scene_ids if node["inputs"].get("kind") == "scene" else old_cast_ids
            ids.append(node_id)
        else:
            continue
        del nodes[node_id]

    engine = "comfyui-minimaxh3-contex-loop"

    nodes[_ID_SCRIPT] = _node(
        "MieH3LoadScriptAsset",
        {"library_dir": library_dir, "name": script_name},
        "剧本资产 · Load Script Asset (MieH3)", engine)

    # One loader per role, in cast order.
    asset_nodes = []
    for index, role in enumerate(roles):
        node_id = str(_ID_ASSET_BASE + index)
        name = (role_assets.get(role["id"]) or "").strip()
        if not name:
            raise ValueError(
                "role %s (%s) has no asset wired" % (role["id"], role["name"]))
        nodes[node_id] = _node(
            "MieH3LoadAsset",
            {"kind": "character", "library_dir": asset_library_dir, "name": name},
            "角色资产 · Load %s" % name, engine)
        asset_nodes.append((role, node_id, name))

    plan_inputs = {"storyboard": [_ID_SCRIPT, 0]}
    for index, (role, node_id, _name) in enumerate(asset_nodes, 1):
        plan_inputs["character_%d" % index] = [node_id, 5]
    scene_name = (scene_asset or "").strip()
    if scene_name:
        nodes[_ID_SCENE] = _node(
            "MieH3LoadAsset", {"kind": "scene", "library_dir": asset_library_dir,
                                "name": scene_name},
            "场景资产 · Load %s" % scene_name, engine)
        plan_inputs["scene_1"] = [_ID_SCENE, 5]
    nodes[_ID_PLAN] = _node(
        "MieH3ScriptToPlan", plan_inputs,
        "剧本转计划 · Script → Plan (MieH3)", engine)

    nodes[_ID_SLOTS] = _node(
        "MieH3AssetToSlots", {"assets": [_ID_PLAN, 3]},
        "资产转槽位 · Asset → Slots", engine)

    nodes[_ID_ROSTER] = _node(
        "MieH3LoadAssetRoster", {"library_dir": asset_library_dir, "kind": "character"},
        "库内角色清单 · Library Roster (MieH3)", engine)

    # The plan node: the assembled plan wins over the literal.
    plan_node = next(
        (n for n in nodes.values()
         if n["class_type"].endswith("ChainPlanModern|MieH3")), None)
    if plan_node is None:
        raise ValueError("the preset has no ChainPlanModern node")
    plan_node["inputs"]["plan_json_input"] = [_ID_PLAN, 0]
    plan_node["inputs"]["plan_json"] = json.dumps(
        {"shots": [{"id": "intro", "prompt": "Describe the opening shot."}]},
        ensure_ascii=False)
    # The engine takes the run name verbatim - a name it cannot accept
    # is an error, not something it rewrites. So the name is made legal
    # here, where the app is the author of it.
    plan_node["inputs"]["run_name"] = runname.effective_run_name(run_name, script_name)
    plan_node["inputs"]["width"] = width
    plan_node["inputs"]["height"] = height
    plan_node["inputs"]["default_steps"] = steps
    plan_node["inputs"]["default_duration_seconds"] = duration
    effective_run_name = plan_node["inputs"]["run_name"]

    # The Assemble node names the finished file. Leaving the preset's name
    # on it means the file lands under one name and the run lives under
    # another, and the two stop being findable by the same key.
    assembler = next(
        (n for n in nodes.values()
         if n["class_type"].endswith("ChainAssemble|MieH3")), None)
    if assembler is not None:
        assembler["inputs"]["filename"] = effective_run_name + "_%date:yyyy-MM-dd%"

    # The sampler: pictures over the on-screen roles, voices over the
    # characters that really carry a voice sample.
    sampler = next(
        (n for n in nodes.values()
         if n["class_type"] == "MiniMaxH3ReferenceToVideo"), None)
    if sampler is None:
        raise ValueError("the preset has no ReferenceToVideo node")
    # The preset's own references are stale the moment the script
    # changes: a board with fewer roles than the preset would leave the
    # old pictures and voices hanging on the sampler.
    for key in list(sampler["inputs"]):
        if key.startswith(("ref_images.ref_image_", "ref_audios.ref_audio_")):
            del sampler["inputs"][key]
    picture_index = 0
    for role, _node_id, _name in asset_nodes:
        if role.get("presence") != "on_screen":
            continue
        picture_index += 1
        sampler["inputs"]["ref_images.ref_image_%d" % (picture_index - 1)] = [
            _ID_SLOTS, picture_index]
    # The scene asset is the LAST picture in the slot's picture numbering
    # (on-screen characters first, then the scene) - and it has to reach
    # the sampler. The engine's ``to_runtime`` drops the text scene when
    # a scene asset is wired: "the scene picture owns the visual world".
    # A scene that never reaches the sampler leaves the shot with NO
    # environment at all (found 2026-11-15 in a real render).
    if scene_name:
        sampler["inputs"]["ref_images.ref_image_%d" % picture_index] = [
            _ID_SLOTS, picture_index + 1]
    # The voices: the ones the character assets really have, in the slot
    # node's own compact order.
    if audio_mode(script_json) == "reference":
        # Reference audio is a HARD contract on the engine side: every
        # wired character has to carry a voice sample, or the plan
        # assembler refuses the board. Say so here, where the author can
        # act on it - and do NOT quietly wire the next character's voice
        # as voice 1: that would put a voice on the wrong character.
        # The rule and its wording are the submission check's too.
        check_reference_voices(script_json, role_assets, asset_library_dir)
        voices = [(role, name) for role, _node_id, name in asset_nodes]
        # More voices than the sampler has slots: the engine would drop
        # the last ones without a word, so refuse here instead.
        if len(voices) > MAX_REFERENCE_AUDIOS:
            raise ValueError(
                "reference audio mode supports at most %d character slots: "
                "这次有 %d 个角色的声音样本（%s）：请减少角色，"
                "或改成引擎生成声音。"
                % (MAX_REFERENCE_AUDIOS, len(voices),
                   "、".join(str(role.get("name") or role.get("id"))
                             for role, _name in voices)))
    else:
        # Generated audio: the engine makes the voices itself. The board
        # still wires the samples the characters really have - and an
        # asset whose metadata cannot be read is not one of them.
        voices = reference_voices(asset_nodes, script_json, asset_library_dir)
    for index, (_role, _name) in enumerate(voices[:MAX_REFERENCE_AUDIOS]):
        sampler["inputs"]["ref_audios.ref_audio_%d" % index] = [
            _ID_SLOTS, _VOICE_SLOT_BASE + index]

    replacement.update(zip(old_cast_ids,
                           (node_id for _role, node_id, _name in asset_nodes)))
    if scene_name:
        replacement.update(dict.fromkeys(old_scene_ids, _ID_SCENE))
    if replacement:
        for node in nodes.values():
            for key, value in (node.get("inputs") or {}).items():
                if (isinstance(value, list) and len(value) == 2
                        and isinstance(value[0], str) and value[0] in replacement):
                    node["inputs"][key] = [replacement[value[0]], value[1]]

    # The review gate is the engine's own node; the opt-in only flips
    # its switch. Unattended runs keep the preset's disabled gate.
    apply_review(nodes, review)

    return nodes
