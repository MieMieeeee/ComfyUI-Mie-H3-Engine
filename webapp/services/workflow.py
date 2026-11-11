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
(the assembler counts portraits, not people), voices over every wired
character in cast order. The sampler's ``ref_images`` / ``ref_audios``
inputs autogrow, so they are wired by name.
"""

from __future__ import annotations

import json
import pathlib

from webapp import config
from webapp.services import runname

#: Node ids for the generated asset section. They are high enough not to
#: clash with the preset's chain nodes.
_ID_SCRIPT = "900"
_ID_PLAN = "901"
_ID_SLOTS = "902"
_ID_ROSTER = "903"
_ID_SCENE = "908"
_ID_ASSET_BASE = 910


def _preset() -> dict:
    """The shipped workflow, as {node_id: node}."""
    path = pathlib.Path(config.DEFAULT_WORKFLOW)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _node(class_type: str, inputs: dict, title: str, cnr_id: str) -> dict:
    return {
        "class_type": class_type,
        "inputs": inputs,
        "_meta": {"title": title, "cnr_id": cnr_id, "ver": "1"},
    }


def script_roles(script_json: dict) -> list[dict]:
    """The script's roles, in slot order."""
    return list(script_json.get("roles") or [])


def build(script_name: str, script_json: dict,
          role_assets: dict[str, str],
          run_name: str = "",
          width: int = 960, height: int = 544,
          steps: int = 20, duration: int = 18,
          workflow: dict = None, library_dir: str = "",
          scene_asset: str = "") -> dict:
    """The API-format workflow for one run.

    ``role_assets`` maps a role id (``character_1``) to the asset name
    wired into that slot. ``library_dir`` is where the script loader
    reads from - the snapshot library, so a queued run renders the
    script that was submitted, not the one in the library by then.
    ``scene_asset`` wires a scene asset into the plan's scene_1 slot
    (U02): the scene's picture and ambience override the script's own
    scene copy, exactly as the wired characters do for their slots.
    """
    nodes = json.loads(json.dumps(workflow if workflow is not None else _preset()))
    roles = script_roles(script_json)

    # The preset's asset section is replaced wholesale. Which node
    # replaces which is remembered: every link the preset made to one of
    # them has to follow, or it dangles and ComfyUI rejects that node on
    # validation (the display node pointed at the old plan node, and the
    # submission came back with a validation error for it).
    replaced_types = (
        "MieH3LoadScriptAsset", "MieH3ScriptToPlan",
        "AssetToSlots|Mie", "MieH3LoadAssetRoster", "LoadAssetV2|Mie",
    )
    old_ids = {}
    for node_id in list(nodes):
        if nodes[node_id]["class_type"] in replaced_types:
            old_ids.setdefault(nodes[node_id]["class_type"], []).append(node_id)
            del nodes[node_id]

    engine = "comfyui-minimaxh3-contex-loop"
    mienodes = "ComfyUI_MieNodes"

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
            "LoadAssetV2|Mie",
            {"kind": "character", "library_dir": "", "name": name},
            "角色资产 · Load %s" % name, mienodes)
        asset_nodes.append((role, node_id))

    plan_inputs = {"storyboard": [_ID_SCRIPT, 0]}
    for index, (role, node_id) in enumerate(asset_nodes, 1):
        plan_inputs["character_%d" % index] = [node_id, 5]
    scene_name = (scene_asset or "").strip()
    if scene_name:
        nodes[_ID_SCENE] = _node(
            "LoadAssetV2|Mie", {"kind": "scene", "library_dir": "",
                                "name": scene_name},
            "场景资产 · Load %s" % scene_name, mienodes)
        plan_inputs["scene_1"] = [_ID_SCENE, 5]
    nodes[_ID_PLAN] = _node(
        "MieH3ScriptToPlan", plan_inputs,
        "剧本转计划 · Script → Plan (MieH3)", engine)

    nodes[_ID_SLOTS] = _node(
        "AssetToSlots|Mie", {"assets": [_ID_PLAN, 3]},
        "资产转槽位 · Asset → Slots", mienodes)

    nodes[_ID_ROSTER] = _node(
        "MieH3LoadAssetRoster", {"library_dir": "", "kind": "character"},
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

    # The sampler: pictures over the on-screen roles, voices over every
    # wired character in cast order.
    sampler = next(
        (n for n in nodes.values()
         if n["class_type"] == "MiniMaxH3ReferenceToVideo"), None)
    if sampler is None:
        raise ValueError("the preset has no ReferenceToVideo node")
    picture_index = 0
    for role, node_id in asset_nodes:
        if role.get("presence") != "on_screen":
            continue
        picture_index += 1
        sampler["inputs"]["ref_images.ref_image_%d" % (picture_index - 1)] = [
            _ID_SLOTS, picture_index]
    for index, (role, node_id) in enumerate(asset_nodes):
        sampler["inputs"]["ref_audios.ref_audio_%d" % index] = [
            _ID_SLOTS, 11 + index]

    # Follow the replaced nodes: a link the preset made to one of them
    # now points at its replacement. The slot stays the same - the
    # replacement has the same class, so it has the same outputs.
    replacement = {}
    for class_type, new_ids in (
            ("MieH3LoadScriptAsset", [_ID_SCRIPT]),
            ("MieH3ScriptToPlan", [_ID_PLAN]),
            ("AssetToSlots|Mie", [_ID_SLOTS]),
            ("MieH3LoadAssetRoster", [_ID_ROSTER]),
            ("LoadAssetV2|Mie", [node_id for _role, node_id in asset_nodes])):
        for old_id, new_id in zip(old_ids.get(class_type, []), new_ids):
            replacement[old_id] = new_id
    if replacement:
        for node in nodes.values():
            for key, value in (node.get("inputs") or {}).items():
                if (isinstance(value, list) and len(value) == 2
                        and isinstance(value[0], str) and value[0] in replacement):
                    node["inputs"][key] = [replacement[value[0]], value[1]]

    return nodes
