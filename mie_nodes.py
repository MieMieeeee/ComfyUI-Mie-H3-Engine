"""MieNodes-side nodes for the H3 engine.

These are thin ComfyUI shells over ComfyUI-Mie-Core. The engine owns the
generation; the core owns the authoring - the asset library, the script
contract, the zero-LLM assembler and the LLM clients. Nothing here holds
logic of its own, so the engine and the MieNodes pack cannot drift apart:
both call the same implementation, and both use the same asset roster
socket type.

The node type names carry the ``|MieH3`` suffix, which is what lets this
pack and the upstream pack be installed side by side.
"""

from __future__ import annotations

from mie_craft.script.builder import (  # noqa: F401
    ASSET_ROSTER_TYPE,
    BOARD_KIND_DIALOGUE,
    MAX_CHARACTER_SLOTS,
    assemble_from_sockets,
    storyboard_cache_key,
)
from mie_craft.assets import script_asset, library


CATEGORY = "conditioning/minimax/context_loop"


class MieH3LoadScriptAsset:
    """Read one script asset from the shared library.

    The same document the MieNodes pack loads, read through the core, so a
    script authored in one pack is the same script in the other.
    """

    CATEGORY = "MieNodes/assets"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "library_dir": ("STRING", {"default": ""}),
                "name": ("STRING", {
                    "default": "",
                    "tooltip": ("剧本名 · Script name\n"
                                "The script asset to load."),
                }),
            },
        }

    # MIE_STORYBOARD, not MIE_SCRIPT_ASSET: the plan assembler's
    # ``storyboard`` input is that type, and a socket has to match by name
    # for ComfyUI to let the two connect. The MieNodes pack's own
    # Load Storyboard Asset says the same thing.
    RETURN_TYPES = ("MIE_STORYBOARD", "STRING", "STRING")
    RETURN_NAMES = ("storyboard", "status", "text")
    FUNCTION = "load"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, library_dir, name):
        return storyboard_cache_key(library_dir, name)

    def load(self, library_dir="", name=""):
        cleaned, path = script_asset.load_storyboard(library_dir, name)
        bundle = dict(cleaned)
        bundle["_path"] = path
        preview = script_asset.storyboard_preview(cleaned)
        return {"ui": {"text": [preview]},
                "result": (bundle, "ok (%s)" % path, preview)}


class MieH3ScriptToPlan:
    """Zero-LLM plan assembler.

    A hand-written script asset plus the wired character and scene assets
    become a byte-stable ``plan_json`` - the same assembler the MieNodes
    pack uses, so both packs produce the same plan for the same script.
    """

    MAX_SLOTS = MAX_CHARACTER_SLOTS

    @classmethod
    def INPUT_TYPES(cls):
        from mie_craft.script.builder import (
            _storyboard_required_inputs,
            _storyboard_optional_slots,
        )
        return {
            "required": _storyboard_required_inputs(),
            "optional": _storyboard_optional_slots(cls.MAX_SLOTS),
        }

    RETURN_TYPES = ("STRING", "STRING", "IMAGE", ASSET_ROSTER_TYPE)
    RETURN_NAMES = ("plan_json", "summary", "images", "assets")

    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, storyboard, **kwargs):
        out = assemble_from_sockets(
            storyboard=storyboard,
            kwargs=kwargs,
            max_slots=self.MAX_SLOTS,
        )
        return (out["plan_json"], out["summary"], out["images"], out["assets"])


class MieH3LoadAssetRoster:
    """List the characters and scenes the shared library holds.

    The roster is what the plan assembler consumes: one entry per character
    in slot order, then the scenes.
    """

    CATEGORY = "MieNodes/assets"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "library_dir": ("STRING", {"default": ""}),
                "kind": (["character", "scene"], {"default": "character"}),
            },
        }

    RETURN_TYPES = ("MIE_ASSET_ROSTER", "STRING")
    RETURN_NAMES = ("assets", "status")
    FUNCTION = "load"
    OUTPUT_NODE = True

    def load(self, library_dir="", kind="character"):
        if kind == "scene":
            payload = library.list_scenes(library_dir)
        else:
            payload = library.list_characters(library_dir)
        names = payload.get("names", "") if isinstance(payload, dict) else str(payload)
        roster = [name for name in str(names).splitlines() if name.strip()]
        return {"ui": {"text": [str(names)]},
                "result": (roster, "%d %s(s)" % (len(roster), kind))}
