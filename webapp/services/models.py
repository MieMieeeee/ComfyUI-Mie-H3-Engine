"""What ComfyUI can load, and the presets the author saved.

The render is a graph of nodes; the models are inputs of a few of
them (the diffusion model, the text encoder, the video VAE, the audio
VAE, the attention backend). Which node plays which part is read from
THE GRAPH, not from a table of file names: the video VAE is the one
wired into the sampler's ``vae`` input, and the audio VAE is the one
wired into ``audio_vae``. A machine that installs different files
therefore needs no code change, and nothing here guesses a file name.

The choices themselves come from ComfyUI's own ``/object_info``: the
enum of every input is what that ComfyUI build really accepts. A value
that is not in that list is never sent - a prompt naming a model the
engine does not have would fail minutes later, in the queue.

Offline, the list is empty and the page keeps the workflow's own
defaults: the shipped preset still says what it always said.
"""

from __future__ import annotations

import json

from webapp import config
from webapp.services import comfy, jsonfile

#: The model inputs the workbench exposes, in the order the page shows
#: them.
MODEL_SLOTS = ("unet", "clip", "video_vae", "audio_vae", "attention")

#: What the page calls each of them.
SLOT_LABELS = {
    "unet": "扩散模型（UNET）",
    "clip": "文本编码器（CLIP）",
    "video_vae": "视频 VAE",
    "audio_vae": "音频 VAE",
    "attention": "注意力后端",
    "base_seed": "基础种子",
}

#: The engine's own seed bound. The live numbers come from
#: ``/object_info``; this is what is left when ComfyUI cannot be asked.
DEFAULT_SEED_MIN = 0
DEFAULT_SEED_MAX = 0xFFFFFFFFFFFFFFFF

#: The engine's own step bound (``default_steps`` on the plan node).
MIN_STEPS = 1
MAX_STEPS = 10000

#: The quality presets that ship with the workbench. They only set the
#: sampler steps - the one knob that trades time for quality - and
#: leave the models alone, because the models are named differently on
#: every machine.
BUILTIN_PRESETS = (
    {"id": "quick", "name": "快速", "builtin": True,
     "desc": "采样 12 步：出片快，先看效果",
     "settings": {"steps": 12}},
    {"id": "standard", "name": "标准", "builtin": True,
     "desc": "采样 20 步：引擎的默认质量",
     "settings": {"steps": 20}},
    {"id": "high", "name": "高质", "builtin": True,
     "desc": "采样 32 步：更慢更细，适合定稿",
     "settings": {"steps": 32}},
)

#: The keys a saved preset may hold.
PRESET_KEYS = set(MODEL_SLOTS) | {"base_seed", "steps"}


# --------------------------------------------------------------------------- #
# the graph
# --------------------------------------------------------------------------- #

def _link_id(value) -> str:
    """The node id a link points at, or "" when it is not a link."""
    if isinstance(value, (list, tuple)) and len(value) == 2 \
            and isinstance(value[0], str):
        return value[0]
    return ""


def _slot(node_id: str, input_name: str, current) -> dict:
    return {"node_id": str(node_id), "input": input_name,
            "current": current if current is not None else ""}


def _vae_roles(graph: dict) -> dict:
    """Which VAE node is the video one and which is the audio one.

    The node that takes BOTH a video VAE and an audio VAE says which
    is which - that is the sampler. The audio decoder says so too, and
    is used as a second opinion.
    """
    roles: dict = {}
    for node in graph.values():
        inputs = node.get("inputs") or {}
        if "audio_vae" in inputs and "vae" in inputs:
            video = _link_id(inputs.get("vae"))
            audio = _link_id(inputs.get("audio_vae"))
            if video:
                roles[video] = "video_vae"
            if audio:
                roles[audio] = "audio_vae"
    for node in graph.values():
        if node.get("class_type") == "VAEDecodeAudio":
            link = _link_id((node.get("inputs") or {}).get("vae"))
            if link:
                roles.setdefault(link, "audio_vae")
    return roles


def slots(graph: dict) -> dict:
    """Where each model input lives in THIS graph.

    ``{key: {"node_id", "input", "current"}}``. A key that this graph
    does not have is simply absent - the page then shows nothing for
    it instead of a control that cannot work.
    """
    vae_roles = _vae_roles(graph)
    found: dict = {}
    for node_id, node in (graph or {}).items():
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") or {}
        if class_type == "UNETLoader" and "unet" not in found:
            found["unet"] = _slot(node_id, "unet_name", inputs.get("unet_name"))
        elif class_type == "CLIPLoader" and "clip" not in found:
            found["clip"] = _slot(node_id, "clip_name", inputs.get("clip_name"))
        elif class_type == "VAELoader":
            role = vae_roles.get(node_id)
            if role and role not in found:
                found[role] = _slot(node_id, "vae_name", inputs.get("vae_name"))
        elif class_type == "ModelAttentionBackend" and "attention" not in found:
            found["attention"] = _slot(node_id, "attention",
                                       inputs.get("attention"))
        elif class_type.endswith("ChainPlanModern|MieH3") \
                and "base_seed" not in found:
            found["base_seed"] = _slot(node_id, "base_seed",
                                       inputs.get("base_seed"))
    return found


def preset_graph() -> dict:
    """The shipped workflow, as the graph the page describes."""
    from webapp.services import workflow

    return workflow.preset()


# --------------------------------------------------------------------------- #
# what ComfyUI offers
# --------------------------------------------------------------------------- #

def _input_spec(registry: dict, class_type: str, input_name: str):
    """The raw spec of one input, as ``/object_info`` gives it."""
    node = registry.get(class_type) or {}
    inputs = node.get("input") or {}
    for section in ("required", "optional"):
        spec = (inputs.get(section) or {}).get(input_name)
        if spec is not None:
            return spec
    return None


def _options(registry: dict, class_type: str, input_name: str) -> list:
    """The values one input accepts, as a list of strings."""
    spec = _input_spec(registry, class_type, input_name)
    if spec is None:
        return []
    first = spec[0] if isinstance(spec, (list, tuple)) and spec else spec
    if isinstance(first, (list, tuple)):
        return [str(item) for item in first]
    return []


def _int_bounds(registry: dict, class_type: str, input_name: str) -> dict:
    """The min/max of an INT input, as strings (they can be huge)."""
    spec = _input_spec(registry, class_type, input_name)
    options = {}
    if isinstance(spec, (list, tuple)) and len(spec) > 1 \
            and isinstance(spec[1], dict):
        options = spec[1]
    low = options.get("min", DEFAULT_SEED_MIN)
    high = options.get("max", DEFAULT_SEED_MAX)
    try:
        return {"min": str(int(low)), "max": str(int(high)),
                "default": str(int(options.get("default", low)))}
    except (TypeError, ValueError):
        return {"min": str(DEFAULT_SEED_MIN), "max": str(DEFAULT_SEED_MAX),
                "default": str(DEFAULT_SEED_MIN)}


def discover(graph: dict = None) -> dict:
    """What ComfyUI offers for this graph's model inputs.

    Offline the options are empty and ``online`` is False: the page
    then keeps the workflow's own defaults and says why.
    """
    graph = preset_graph() if graph is None else graph
    found = slots(graph)
    registry = comfy.object_info()
    online = bool(registry)
    result = {"online": online, "slots": {}, "error": "",
              "seed": {"min": str(DEFAULT_SEED_MIN),
                       "max": str(DEFAULT_SEED_MAX),
                       "default": str(DEFAULT_SEED_MIN)}}
    if not online:
        result["error"] = "连不上 ComfyUI：模型清单暂时拿不到，用的是工作流自带的默认值。"
    for key, slot in found.items():
        class_type = str((graph.get(slot["node_id"]) or {}).get("class_type") or "")
        options = _options(registry, class_type, slot["input"]) if online else []
        entry = dict(slot)
        entry["class_type"] = class_type
        entry["label"] = SLOT_LABELS.get(key, key)
        entry["options"] = options
        entry["supported"] = (not options) or (str(slot.get("current")) in options)
        result["slots"][key] = entry
    seed_slot = found.get("base_seed")
    if seed_slot is not None and online:
        class_type = str((graph.get(seed_slot["node_id"]) or {}).get("class_type") or "")
        result["seed"] = _int_bounds(registry, class_type, "base_seed")
    return result


# --------------------------------------------------------------------------- #
# the settings the page sends
# --------------------------------------------------------------------------- #

def normalize(settings: dict) -> dict:
    """The author's choices, cleaned and typed.

    Unknown keys are dropped, the seed becomes a decimal string (it
    can be larger than a double can hold), and the steps become an
    integer.
    """
    clean: dict = {}
    for key, value in (settings or {}).items():
        if key not in PRESET_KEYS:
            continue
        if key == "base_seed":
            text = str(value).strip()
            if not text:
                continue
            if not text.isdigit():
                raise ValueError("基础种子只能是非负整数：%r" % text)
            clean[key] = str(int(text))
        elif key == "steps":
            try:
                clean[key] = int(value)
            except (TypeError, ValueError):
                raise ValueError("步数只能是整数：%r" % (value,))
        else:
            text = str(value).strip()
            if text:
                clean[key] = text
    return clean


def check(settings: dict) -> dict:
    """Refuse anything this ComfyUI does not offer.

    Raises ValueError with a message the page can show. The check
    happens before anything is reserved or recorded: a wrong choice is
    a mistake in the request, not a render that failed.
    """
    clean = normalize(settings)
    if not clean:
        return {"settings": clean,
                "discovery": {"online": False, "slots": {}, "seed": {}}}
    discovery = discover()
    if not discovery.get("online"):
        raise comfy.ComfyUnavailable(
            "连不上 ComfyUI：无法确认模型是否可用，请先让 ComfyUI 跑起来。")
    problems = []
    for key, value in clean.items():
        if key == "base_seed":
            bounds = discovery.get("seed") or {}
            low = int(bounds.get("min") or DEFAULT_SEED_MIN)
            high = int(bounds.get("max") or DEFAULT_SEED_MAX)
            if not low <= int(value) <= high:
                problems.append("基础种子要在 %d 到 %d 之间" % (low, high))
            continue
        if key == "steps":
            if not MIN_STEPS <= int(value) <= MAX_STEPS:
                problems.append("步数要在 %d 到 %d 之间" % (MIN_STEPS, MAX_STEPS))
            continue
        slot = (discovery.get("slots") or {}).get(key)
        if slot is None:
            continue      # this graph has no such input: nothing to check
        options = slot.get("options") or []
        if options and value not in options:
            problems.append("ComfyUI 里没有这个%s：%s" % (SLOT_LABELS.get(key, key), value))
    if problems:
        raise ValueError("；".join(problems))
    return {"settings": clean, "discovery": discovery}


def apply(graph: dict, settings: dict, discovery: dict = None) -> dict:
    """Write the author's choices into the graph.

    Only values ComfyUI offers are written: an unknown one is skipped
    rather than sent, because a prompt naming a model the engine does
    not have fails minutes later, in the queue.
    """
    found = slots(graph)
    known = ((discovery or {}).get("slots") or {})
    changed: dict = {}
    for key, value in (settings or {}).items():
        slot = found.get(key)
        if slot is None or value in (None, ""):
            continue
        options = (known.get(key) or {}).get("options") or []
        if options and str(value) not in options:
            continue
        node = graph.get(slot["node_id"])
        if node is None:
            continue
        inputs = node.setdefault("inputs", {})
        if key == "base_seed":
            try:
                inputs[slot["input"]] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            inputs[slot["input"]] = str(value)
        changed[key] = value
    return changed


# --------------------------------------------------------------------------- #
# the saved presets
# --------------------------------------------------------------------------- #

def _saved() -> dict:
    try:
        data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    saved = data.get("render_presets")
    return dict(saved) if isinstance(saved, dict) else {}


def presets() -> list:
    """The built-in quality presets and the author's own."""
    out = [dict(item) for item in BUILTIN_PRESETS]
    for name, settings in sorted(_saved().items()):
        out.append({"id": name, "name": name, "builtin": False,
                    "desc": "自己保存的预设",
                    "settings": dict(settings or {})})
    return out


def save_preset(name: str, settings: dict) -> dict:
    """Save one named preset."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("预设得有个名字。")
    if len(name) > 40:
        raise ValueError("预设名字太长了（最多 40 个字）。")
    clean = normalize(settings)
    if not clean:
        raise ValueError("这个预设什么也没设。")
    with jsonfile.settings_lock:
        try:
            saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        table = dict(saved.get("render_presets") or {})
        table[name] = clean
        saved["render_presets"] = table
        config.ensure_dirs()
        jsonfile.write_json(config.SETTINGS_FILE, saved)
    return {"name": name, "settings": clean}


def delete_preset(name: str) -> dict:
    """Throw one saved preset away."""
    name = str(name or "").strip()
    with jsonfile.settings_lock:
        try:
            saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        table = dict(saved.get("render_presets") or {})
        if name not in table:
            raise ValueError("没有这个预设：%s" % name)
        table.pop(name)
        saved["render_presets"] = table
        config.ensure_dirs()
        jsonfile.write_json(config.SETTINGS_FILE, saved)
    return {"name": name, "deleted": True}


def overview(graph: dict = None) -> dict:
    """Everything the /video page needs in one answer."""
    discovery = discover(graph)
    return {
        "online": discovery.get("online"),
        "error": discovery.get("error", ""),
        "slots": discovery.get("slots") or {},
        "seed": discovery.get("seed") or {},
        "presets": presets(),
    }
