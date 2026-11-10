"""LLM-drafted assets: the web front end's version of the auto nodes.

The ComfyUI pack has ``SaveAssetAutoV2`` / the storyboard generator
nodes; the workbench exposes the same three abilities over HTTP:

  * a storyboard draft from an idea (concept mode) or a revision of an
    existing board (revise mode) — ``mie_craft.script.generator`` does
    the actual work, contract validation included;
  * character / scene fields drafted the way the auto save nodes do
    them: description from the reference image, voice from the
    description, ambience for scenes. Fields the author already filled
    are passed through untouched — drafting only fills EMPTY fields,
    and the result lands in the form for review, never straight into
    the library.

The LLM platform is whatever the 大模型 page currently has selected;
a missing key is reported as a friendly error before any call.
"""

from __future__ import annotations

import json

from mie_craft.assets.library import (
    AMBIENCE_MAX_CHARS,
    DESCRIPTION_MAX_CHARS,
    VOICE_MAX_CHARS,
)
from mie_prompt.h3_prompts import caption_reference_prompt

from webapp.services import llm


def _connector():
    """The current platform's connector, with the key pre-checked."""
    key = llm.current_platform()
    if not key:
        raise ValueError("没有选择当前大模型平台：先到大模型页选一个平台并配置 Key")
    if not llm.read_keys().get(key):
        raise ValueError("平台 %s 还没有配置 API Key：先到大模型页设置" % key)
    row = next((r for r in llm.list_platforms() if r["key"] == key), None)
    if row is None:
        raise ValueError("当前平台 %s 不在平台列表里" % key)
    return llm.build_connector(row)


# --------------------------------------------------------------------------- #
# storyboard drafts
# --------------------------------------------------------------------------- #

def storyboard(mode: str, concept: str, *, base_asset=None, name: str = "新剧本",
               shot_count: int = 2, language: str = "zh",
               target_duration_seconds: float = 20, audio_mode: str = "generated",
               character_settings: str = "", critic: str = "on") -> dict:
    """One checked draft pass; returns the validated asset and its notes.

    The enhancer re-rolls up to three times on its own; a contract
    failure surfaces as RuntimeError, a provider failure as whatever the
    connector raised. Both become the endpoint's 400 body verbatim.
    """
    from mie_craft.script.generator import AssetStoryboardEnhancer

    mode = "revise" if mode == "revise" else "concept"
    concept = str(concept or "").strip()
    if not concept:
        raise ValueError("点子/修改要求是空的：写一段话再生成")
    if mode == "revise" and not isinstance(base_asset, dict):
        raise ValueError("修订模式需要当前剧本作为底稿：先打开它再点修订")
    planner = AssetStoryboardEnhancer(_connector(), timeout=300)
    asset = planner(concept, int(shot_count) if shot_count else 0,
                    mode=mode, base_asset=base_asset,
                    name=str(name or "新剧本").strip() or "新剧本",
                    language=language if language in ("zh", "en") else "zh",
                    target_duration_seconds=float(target_duration_seconds or 20),
                    audio_mode=audio_mode if audio_mode in ("generated", "reference")
                    else "generated",
                    character_settings=str(character_settings or ""),
                    critic="on" if critic != "off" else "off",
                    seed=0)
    return {"json": asset, "notes": list(planner.warnings)}


# --------------------------------------------------------------------------- #
# character / scene field drafts (the auto save nodes' semantics)
# --------------------------------------------------------------------------- #

_SCENE_DESCRIPTION_HINT = (
    "Describe the PLACE in one paragraph (60-100 words, English): the "
    "space, its key furniture/objects, the lighting, and the atmosphere. "
    "State spatial facts an image reference must preserve (what stands "
    "where). Output only the description, no preamble."
)

_CHARACTER_TEXT_HINT = (
    "Describe the CHARACTER in one paragraph (60-100 words, English): "
    "age build, hair, clothing, and the impression they give. Output "
    "only the description, no preamble."
)

_VOICE_SYSTEM = (
    "You write one-line English voice (timbre) descriptors for video "
    "characters, in the exact style: 'adult female, clear and slightly "
    "warm timbre, low-mid register, unhurried pace'. Given a character "
    "name and their appearance description, infer the most plausible "
    "voice (age, gender, register, pace, texture). You CANNOT hear any "
    "audio — this is a draft from the name and appearance only, and the "
    "user will edit it after hearing their sample. Output ONLY the one "
    "descriptor sentence, at most 200 characters, no quotes, no "
    "explanation."
)

_AMBIENCE_SYSTEM = (
    "You write one-line English ambience (room tone) descriptors for "
    "video scenes, in the exact style: 'Quiet room tone, faint clock "
    "ticking'. Given the scene description, infer the background sound "
    "of the place with no people and no music. Output ONLY the one "
    "descriptor line, at most %d characters, no quotes, no explanation."
    % AMBIENCE_MAX_CHARS
)


def _clean(text: str, limit: int) -> str:
    """Collapse whitespace, trim at the last sentence end inside the cap."""
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    window = body[:limit]
    cut = max(window.rfind(". "), window.rfind(".…"), window.rfind("。"))
    if cut < int(limit * 0.5):
        cut = window.rfind(" ")
    return (window[:cut + 1] if cut >= 0 else window).rstrip()


def _invoke(connector, system: str, user_text: str, image_data_url: str = None,
            max_tokens: int = 2048) -> str:
    content = []
    if image_data_url:
        content.append({"type": "image_url",
                        "image_url": {"url": image_data_url, "detail": "auto"}})
    content.append({"type": "text", "text": user_text})
    reply = connector.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ], max_tokens=max_tokens)
    return " ".join(str(reply or "").split())


def asset_fields(kind: str, name: str, hint: str = "", current: dict = None,
                 image_data_url: str = None) -> dict:
    """Draft the EMPTY fields of one character/scene asset.

    Mirrors the auto save nodes: a filled field is the author's word and
    passes through untouched. With a reference image the description is
    drafted from the image (multimodal call); without one, from the
    name and the author's hint alone.
    """
    kind = "scene" if kind == "scene" else "character"
    current = dict(current or {})
    out = {
        "description": str(current.get("description") or ""),
        "voice": str(current.get("voice") or ""),
        "ambience": str(current.get("ambience") or ""),
    }
    drafted = []
    connector = _connector()
    hint = str(hint or "").strip()

    if not out["description"]:
        if kind == "scene":
            system, task = _SCENE_DESCRIPTION_HINT, "Describe this place for a scene reference image."
        else:
            system, task = caption_reference_prompt(), "Describe this character for the identity reference image."
        if not image_data_url:
            system = (_CHARACTER_TEXT_HINT if kind == "character"
                      else _SCENE_DESCRIPTION_HINT)
            task = "No reference image is available; draft from the name and notes alone."
        user_text = "Name: %s\n%s" % (name, task)
        if hint:
            user_text += ("\n\nAuthor notes (facts you must respect; keep the output "
                          "style the system prompt defines): " + hint)
        description = _clean(_invoke(connector, system, user_text,
                                     image_data_url, max_tokens=16384),
                             DESCRIPTION_MAX_CHARS)
        if not description:
            raise ValueError("LLM 返回了空的描述草稿")
        out["description"] = description
        drafted.append("description")

    if kind == "character" and not out["voice"]:
        user_text = "Name: %s\nAppearance: %s" % (name, out["description"][:400])
        if hint:
            user_text += ("\nAuthor voice notes (ground truth from the author, who "
                          "has heard the sample; follow them over your inference): "
                          + hint)
        user_text += "\nDraft the one-line voice descriptor."
        voice = _clean(_invoke(connector, _VOICE_SYSTEM, user_text,
                               max_tokens=512), VOICE_MAX_CHARS)
        if not voice:
            raise ValueError("LLM 返回了空的音色草稿")
        out["voice"] = voice
        drafted.append("voice")

    if kind == "scene" and not out["ambience"]:
        user_text = "Scene: %s" % (out["description"][:400] or name)
        if hint:
            user_text += "\nAuthor notes: " + hint
        ambience = _clean(_invoke(connector, _AMBIENCE_SYSTEM, user_text,
                                  max_tokens=512), AMBIENCE_MAX_CHARS)
        if not ambience:
            raise ValueError("LLM 返回了空的环境音草稿")
        out["ambience"] = ambience
        drafted.append("ambience")

    return {"drafted": drafted, **out}


# --------------------------------------------------------------------------- #
# the English execution mirror
# --------------------------------------------------------------------------- #

_EXECUTION_SYSTEM = (
    "You write the English EXECUTION MIRROR of a Chinese storyboard. The "
    "mirror is what the video compiler actually reads: the staging, what "
    "is visible, the voice of every character and the action of every "
    "non-dialogue event.\n"
    "Rules:\n"
    "- English only, one or two sentences per field.\n"
    "- Output ONE JSON object and nothing else: no prose, no code fence.\n"
    "- voices maps EVERY role id exactly once, using the ids you are given.\n"
    "- directions maps EVERY non-dialogue event id exactly once, using the "
    "ids you are given. Each direction MUST begin with the actor exactly as "
    "the id list writes it, for example \"Character 2 ...\".\n"
    "- Describe only what the source says. Never invent a character, a prop "
    "or a place the source does not mention.\n"
    "- Write directions as positive visual descriptions of what is on "
    "screen. \"Character 2 does not move\" is not a description.\n"
)


def _json_object(text: str) -> dict:
    """The JSON object in a model reply, fences and all."""
    body = str(text or "").strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1] if body.count("```") >= 2 else body
        if body.lower().startswith("json"):
            body = body[4:]
    body = body.strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON：%s" % body[:120])
    try:
        payload = json.loads(body[start:end + 1])
    except ValueError as exc:
        raise ValueError("模型返回的 JSON 解析不了：%s" % exc) from exc
    if not isinstance(payload, dict):
        raise ValueError("模型返回的不是一个 JSON 对象")
    return payload


def _actor_label(role_id: str, roles: list) -> str:
    """How a direction has to open: "Character 2".

    The contract wants the ROLE INDEX in English, not the role id
    ("character_2") and not the role's name ("Claude娘"). The model
    guesses wrong here often enough that the prompt has to spell it
    out.
    """
    for index, role in enumerate(roles, 1):
        if role.get("id") == role_id:
            return "Character %d" % index
    return ""


def _mirror_prompt(script: dict) -> str:
    """The source, laid out so the reply has one obvious shape."""
    roles = script.get("roles") or []
    events = [event for shot in (script.get("shots") or [])
              for event in (shot.get("events") or [])]
    lines = ["Source (Chinese):"]
    for key, label in (("look", "look"), ("setting", "setting")):
        if script.get(key):
            lines.append("%s: %s" % (label, script[key]))
    if script.get("scene"):
        lines.append("scene: %s" % script["scene"])
    if script.get("light") == "custom":
        lines.append("light: %s" % (script.get("light_text") or ""))

    lines.append("")
    lines.append("Roles (id | name | presence | position | voice):")
    for role in roles:
        lines.append("- %s | %s | %s | %s | %s" % (
            role.get("id"), role.get("name"), role.get("presence"),
            role.get("position"), role.get("voice") or ""))

    lines.append("")
    lines.append("Events (id | type | character | text):")
    for event in events:
        lines.append("- %s | %s | %s | %s" % (
            event.get("id"), event.get("type"), event.get("character"),
            event.get("text") or ""))

    actions = [e for e in events if e.get("type") != "dialogue"]
    if actions:
        lines.append("")
        lines.append("Directions are required for these events, and each one "
                     "has to OPEN with the exact words given here:")
        for event in actions:
            label = _actor_label(event.get("character"), roles)
            lines.append("- %s: must start with %r, then say what is on screen"
                         % (event.get("id"), label + " "))

    shape = {
        "look": "...",
        "setting": "...",
        "visible_state": "...",
        "voices": {role.get("id"): "..." for role in roles},
        "directions": {event.get("id"): "%s ..." % _actor_label(
            event.get("character"), roles)
                       for event in actions},
    }
    if script.get("scene"):
        shape["scene"] = "..."
    if script.get("light") == "custom":
        shape["light"] = "..."

    lines.append("")
    lines.append("Output JSON with exactly this shape (fill in every value):")
    lines.append(json.dumps(shape, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def execution_mirror(script: dict, attempts: int = 3) -> dict:
    """Rewrite ONLY the English mirror, from the Chinese source.

    The mirror is the text the compiler reads. When the Chinese source
    moves, the mirror has to follow - but it is written by hand (or by a
    model) and is never overwritten behind the author's back. This is
    the explicit action that brings it back in step, and it touches
    nothing else in the script.

    The contract is the judge, and it is strict: a direction has to
    open with "Character 2", a voice has to be a bare phrase, and so
    on. A model misses those often enough that the mistake is fed back
    to it and the answer is asked for again - the same thing the
    storyboard enhancer does.
    """
    from mie_craft.script import contract

    connector = _connector()
    prompt = _mirror_prompt(script)
    complaint = ""
    for _ in range(max(1, int(attempts))):
        reply = _invoke(connector, _EXECUTION_SYSTEM, prompt,
                        max_tokens=8192)
        try:
            proposed = _json_object(reply)
        except ValueError as exc:
            complaint = str(exc)
            prompt = (_mirror_prompt(script)
                      + "\n\nYour previous answer could not be used: %s\n"
                        "Answer again, with the JSON only." % complaint)
            continue
        try:
            _check_mirror(script, proposed, contract)
        except ValueError as exc:
            complaint = str(exc)
            prompt = (_mirror_prompt(script)
                      + "\n\nYour previous answer was rejected: %s\n"
                        "Fix exactly that and answer again, with the JSON only."
                      % complaint)
            continue
        return {"execution": proposed, "diff": execution_diff(
            script.get("execution") or {}, proposed)}
    raise ValueError("生成的执行描述不符合契约：%s" % complaint)


def _check_mirror(script: dict, proposed: dict, contract) -> None:
    """The contract's opinion on a proposed mirror."""
    candidate = dict(script)
    candidate["execution"] = proposed
    events = [event for shot in (script.get("shots") or [])
              for event in (shot.get("events") or [])]
    validator = getattr(contract, "_validate_execution", None)
    try:
        if validator is not None:
            validator(candidate, events)
        else:
            contract.validate_v2(candidate, _check_timing=False)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def execution_diff(before: dict, after: dict) -> list:
    """What changed between two mirrors, field by field.

    The author reviews this before the new text replaces the old one:
    the mirror is theirs, and a silent rewrite would throw away a
    hand-written direction.
    """
    rows = []
    for key in ("look", "setting", "visible_state", "scene", "light"):
        old, new = str(before.get(key) or ""), str(after.get(key) or "")
        if old != new:
            rows.append({"field": key, "before": old, "after": new})
    for key in ("voices", "directions"):
        old_map = before.get(key) or {}
        new_map = after.get(key) or {}
        for name in sorted(set(old_map) | set(new_map)):
            old, new = str(old_map.get(name) or ""), str(new_map.get(name) or "")
            if old != new:
                rows.append({"field": "%s.%s" % (key, name),
                             "before": old, "after": new})
    return rows
