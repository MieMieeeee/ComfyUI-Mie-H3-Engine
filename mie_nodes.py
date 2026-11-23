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
    ASSET_BUNDLE_TYPE,
    ASSET_ROSTER_TYPE,
    BOARD_KIND_DIALOGUE,
    MAX_CHARACTER_SLOTS,
    assemble_from_sockets,
)
from mie_craft.assets import script_asset, library

from . import asset_root


CATEGORY = "conditioning/minimax/context_loop"


def _library_dir(explicit):
    return explicit.strip() or str(asset_root.asset_library_dir())


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
        # The FILE's content, not the widget values: editing the script
        # busts the cache and nothing else does. The builder's
        # storyboard_cache_key hashes a storyboard DICT; the two-argument
        # one in script_asset hashes the file on disk, and that is the one
        # this node wants. Calling the wrong one raised a TypeError, which
        # ComfyUI turned into "always changed" plus a warning on every
        # single queue.
        return script_asset.storyboard_cache_key(_library_dir(library_dir), name)

    def load(self, library_dir="", name=""):
        cleaned, path = script_asset.load_storyboard(_library_dir(library_dir), name)
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
            payload = library.list_scenes(_library_dir(library_dir))
        else:
            payload = library.list_characters(_library_dir(library_dir))
        names = payload.get("names", "") if isinstance(payload, dict) else str(payload)
        roster = [name for name in str(names).splitlines() if name.strip()]
        return {"ui": {"text": [str(names)]},
                "result": (roster, "%d %s(s)" % (len(roster), kind))}


class MieH3LoadAsset:
    """Read the Craft asset bundle and decode its reference media for ComfyUI."""

    CATEGORY = MieH3LoadAssetRoster.CATEGORY

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "kind": (["character", "scene"], {"default": "character"}),
            "library_dir": ("STRING", {"default": ""}),
            "name": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "IMAGE", "AUDIO", ASSET_BUNDLE_TYPE)
    RETURN_NAMES = ("name", "description", "voice", "image", "audio", "asset")
    FUNCTION = "load"

    @classmethod
    def IS_CHANGED(cls, kind, library_dir, name):
        cache_key = library.scene_cache_key if kind == "scene" else library.character_cache_key
        return cache_key(_library_dir(library_dir), name)

    def load(self, kind="character", library_dir="", name=""):
        import io
        import numpy as np
        import torch
        from PIL import Image

        loader = library.load_scene if kind == "scene" else library.load_character
        loaded = loader(_library_dir(library_dir), name)
        images = loaded["png_bytes_list"]
        clips = loaded.get("audio_bytes_list") or []
        if not images:
            raise ValueError(f"{kind} {name!r} must have at least one reference image")
        with Image.open(io.BytesIO(images[0])) as source:
            pixels = np.asarray(source.convert("RGB"), dtype=np.float32) / 255.0
        image = torch.from_numpy(pixels).unsqueeze(0)
        audio = _decode_reference_audio(clips[0]) if clips else None
        voice = loaded.get("voice") or ""
        description = loaded["description"]
        bundle = {"kind": kind, "name": loaded["name"], "description": description,
                  "image": image}
        if kind == "scene":
            bundle["ambience"] = loaded.get("ambience") or ""
        else:
            bundle.update(voice=voice, audio=audio)
        return (loaded["name"], description, voice, image, audio, bundle)


def _decode_reference_audio(data):
    import io
    import av
    import numpy as np
    import torch

    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        converter = av.AudioResampler(format="fltp")
        chunks = [frame.to_ndarray() for original in container.decode(stream)
                  for frame in converter.resample(original)]
        chunks.extend(frame.to_ndarray() for frame in converter.resample(None))
        if not chunks:
            raise ValueError("reference audio contains no samples")
        waveform = torch.from_numpy(np.concatenate(chunks, axis=1)).unsqueeze(0)
        return {"waveform": waveform, "sample_rate": stream.rate}


class MieH3AssetToSlots:
    """Number pictures and voices in the same order as the assembled plan."""

    CATEGORY = MieH3LoadAssetRoster.CATEGORY
    MAX_PICTURES = 9
    MAX_AUDIOS = 3

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"assets": (ASSET_ROSTER_TYPE,)}}

    RETURN_TYPES = ("INT",) + ("IMAGE",) * MAX_PICTURES + ("INT",) + ("AUDIO",) * MAX_AUDIOS
    RETURN_NAMES = ("count",) + tuple(f"picture_{i}" for i in range(1, MAX_PICTURES + 1))
    RETURN_NAMES += ("audio_count",) + tuple(f"audio_{i}" for i in range(1, MAX_AUDIOS + 1))
    FUNCTION = "split"

    def split(self, assets):
        if not isinstance(assets, (list, tuple)) or any(
                not isinstance(asset, dict) or asset.get("kind") not in ("character", "scene")
                for asset in assets):
            raise ValueError("wire the plan's MIE_ASSET_ROSTER output to assets")
        pictures = [asset["image"] for asset in assets if asset.get("image") is not None]
        if len(pictures) > self.MAX_PICTURES:
            raise ValueError(f"at most {self.MAX_PICTURES} reference pictures are supported")
        audios = [asset["audio"] for asset in assets
                  if asset["kind"] == "character" and asset.get("audio") is not None]
        audios = audios[:self.MAX_AUDIOS]
        return (len(pictures), *pictures, *([None] * (self.MAX_PICTURES - len(pictures))),
                len(audios), *audios, *([None] * (self.MAX_AUDIOS - len(audios))))
