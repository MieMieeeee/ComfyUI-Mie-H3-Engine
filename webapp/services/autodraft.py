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
