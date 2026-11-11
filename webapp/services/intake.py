"""The storyboard intake service: reading the materials before drafting.

The intake plan's first step is to understand what the author brought -
a text, a picture, or both - BEFORE any long generation is paid for.
This module serves ``POST /api/scripts/intake`` (action ``read``):

  * what is VISIBLE in the picture (``observed``) is kept strictly apart
    from what the model GUESSES (``proposed``); identity, relationships
    and psychology are guesses by definition and are labelled as such;
  * concrete conflicts between the picture and the author's text are
    named field by field, so the page can ask the author to rule on
    them;
  * a platform that cannot see images is reported as the business state
    ``needs_caption`` - the author's caption then carries the reading,
    and its human origin is never overwritten by the model's opinion;
  * upstream failures (auth, quota, rate limit, timeout) are classified
    into a stable code, and the author's materials always survive.

Nothing here writes to the library. A reading is draft data; the author
confirms it, and only a later explicit save creates an asset.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re


#: The request body ceiling (the plan's product default: an 8 MB body
#: leaves room for a 4 MB normalized picture plus the text).
MAX_BODY_BYTES = 8 * 1024 * 1024

#: The decoded pixel ceiling the server checks on its own, whatever the
#: client claims.
MAX_DECODED_PIXELS = 20_000_000

#: Reading groups: caps keep a model flood editable on one page.
_MAX_OBSERVED = 10
_MAX_PROPOSED = 6
_MAX_CONFLICTS = 8
_MAX_ITEM_CHARS = 500


class IntakeError(ValueError):
    """An intake failure the API answers with a code and a status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# --------------------------------------------------------------------------- #
# the picture: trust bytes, not declarations
# --------------------------------------------------------------------------- #

def sniff_image_kind(data: bytes) -> str:
    """What the bytes actually are - JPEG, PNG or WebP, or not a picture.

    The declared MIME type in a data URL is a claim; the magic number is
    the fact. An SVG or an animation container is not accepted here and
    has to be re-picked.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _decode_image_bytes(data: bytes):
    """Decode with Pillow, or refuse: a truncated or corrupt file must
    fail HERE, not deep inside a provider call."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decoder complaint is ours
        raise IntakeError(
            "bad_image", "这张图打不开（解码失败）：%s - 请重新选择一张"
            % type(exc).__name__, 415) from exc
    return image


def check_image_data_url(data_url: str) -> None:
    """The server's own opinion of an image data URL.

    Type by magic number, decodability for real, pixel count decoded -
    a browser that was lied to (or bypassed) cannot smuggle a 40
    megapixel raw file through a small-looking prefix.
    """
    match = re.match(r"^data:image/[a-z+.-]+;base64,(.*)$",
                     str(data_url or ""), re.S)
    if not match:
        raise IntakeError("bad_image",
                          "图片要用 data URL（base64）提交", 415)
    try:
        data = base64.b64decode(match.group(1), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise IntakeError("bad_image", "图片数据解码不了：%s" % exc, 415) from exc
    kind = sniff_image_kind(data)
    if not kind:
        raise IntakeError(
            "bad_image",
            "只接受 JPEG、PNG 或 WebP 图片；SVG 和动图请重新选择一张静态图",
            415)
    image = _decode_image_bytes(data)
    pixels = image.width * image.height
    if pixels > MAX_DECODED_PIXELS:
        raise IntakeError(
            "too_large",
            "这张图解码后有 %d 万像素，超过 %d 万像素上限：换一张小一点的"
            % (pixels // 10000, MAX_DECODED_PIXELS // 10000), 413)


# --------------------------------------------------------------------------- #
# upstream failures: one stable code each
# --------------------------------------------------------------------------- #

def classify_provider_error(exc: Exception) -> str:
    """A provider failure as one of the stable intake codes.

    The connectors wrap upstream answers in plain ``Exception`` with the
    HTTP status and a body snippet in the text, so the classification
    reads the text. The order matters: an auth failure mentioning an
    image endpoint is an auth failure, not a vision limitation.
    """
    text = str(exc)
    lowered = text.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if ("HTTP 401" in text or "HTTP 403" in text
            or "unauthorized" in lowered or "api_key" in lowered
            or "api key" in lowered or "authentication" in lowered
            or "鉴权" in text or "密钥" in text):
        return "auth"
    if ("HTTP 402" in text or "quota" in lowered or "balance" in lowered
            or "insufficient" in lowered or "余额" in text
            or "欠费" in text or "arrears" in lowered):
        return "quota"
    if "HTTP 429" in text or "rate limit" in lowered or "限流" in text:
        return "rate_limited"
    picture = ("image" in lowered or "vision" in lowered
               or "multimodal" in lowered or "图片" in text or "图像" in text)
    four_xx = re.search(r"HTTP 4\d\d", text)
    if picture and four_xx:
        return "unsupported_image"
    return "provider"


# --------------------------------------------------------------------------- #
# the reading itself
# --------------------------------------------------------------------------- #

_READ_SYSTEM = (
    "你为一部短剧读素材，把「看见的」和「猜想的」严格分开。输出一个 JSON 对象，"
    "不要输出任何其他文字：\n"
    '{"observed": ["…"], "proposed": ["…"], '
    '"conflicts": [{"field": "人物身份", "image": "图中…", "text": "原文…"}]}\n'
    "规则：\n"
    "- observed 只写画面上确实可见的事实：人数、外观、衣着、场景、动作、可见的"
    "文字。每条一句话，以「图中可见」开头。身份、关系、心理、前后剧情一律不进"
    "observed。\n"
    "- proposed 写创作建议：这张图可能在讲什么故事、人物可能是什么身份/关系/"
    "心理、前后可能发生什么。每条一句话。这些只是建议，作者会逐条修改。\n"
    "- conflicts 只在同时给了原文时写：图中信息与原文在人物身份、人物数量、"
    "关键事件上的具体矛盾，逐条写清图里是什么、原文说什么。没有矛盾给空数组。\n"
    "- observed 最多 10 条，proposed 最多 6 条，conflicts 最多 8 条，每条不超过"
    "120 字。\n"
    "- 没有图片时 observed 给空数组，把对故事的理解写进 proposed。\n"
    "输出只有这个 JSON 对象。"
)


def _reading_user_text(text: str, caption: str, has_image: bool) -> str:
    lines = []
    if has_image:
        lines.append("（图片见上）")
    if str(text or "").strip():
        lines.append("作者写的原文：")
        lines.append(str(text).strip())
    if str(caption or "").strip():
        lines.append("")
        lines.append("作者提供的人工图注（人工来源，作为图片内容的事实，"
                     "优先于你对图片的任何判断）：")
        lines.append(str(caption).strip())
    if not lines:
        lines.append("（作者只给了一张图，没有写文字）")
    return "\n".join(lines)


def _connector():
    """The current platform's connector - resolved late so tests (and a
    platform switch) take effect without re-importing this module."""
    from webapp.services.autodraft import _connector as current
    return current()


def _invoke_text(connector, user_text: str) -> str:
    reply = connector.invoke([
        {"role": "system", "content": _READ_SYSTEM},
        {"role": "user", "content": user_text},
    ], max_tokens=2048)
    return str(reply or "")


def _invoke_with_image(connector, user_text: str, image_data_url: str) -> str:
    content = [
        {"type": "image_url",
         "image_url": {"url": image_data_url, "detail": "auto"}},
        {"type": "text", "text": user_text},
    ]
    reply = connector.invoke([
        {"role": "system", "content": _READ_SYSTEM},
        {"role": "user", "content": content},
    ], max_tokens=2048)
    return str(reply or "")


def _string_list(payload: dict, key: str, cap: int) -> list:
    value = payload.get(key)
    if not isinstance(value, list):
        raise IntakeError("bad_reply",
                          "模型没有按格式返回 %s（要一个字符串数组）" % key)
    out = []
    for item in value[:cap]:
        text = str(item or "").strip()
        if text:
            out.append(text[:_MAX_ITEM_CHARS])
    return out


def _conflict_list(payload: dict) -> list:
    value = payload.get("conflicts")
    if not isinstance(value, list):
        raise IntakeError("bad_reply", "模型没有按格式返回 conflicts")
    out = []
    for item in value[:_MAX_CONFLICTS]:
        if not isinstance(item, dict):
            raise IntakeError("bad_reply", "conflicts 的每一项要是一个对象")
        row = {key: str(item.get(key) or "").strip()[:_MAX_ITEM_CHARS]
               for key in ("field", "image", "text")}
        if row["field"] or row["image"] or row["text"]:
            out.append(row)
    return out


def _parse_reading(raw: str) -> dict:
    from webapp.services.autodraft import _json_object

    try:
        payload = _json_object(raw)
    except ValueError as exc:
        raise IntakeError("bad_reply", str(exc)) from exc
    observed = _string_list(payload, "observed", _MAX_OBSERVED)
    proposed = _string_list(payload, "proposed", _MAX_PROPOSED)
    conflicts = _conflict_list(payload)
    return {
        "observed": [{"id": "obs_%d" % n, "text": t}
                     for n, t in enumerate(observed, 1)],
        "proposed": [{"id": "pro_%d" % n, "text": t}
                     for n, t in enumerate(proposed, 1)],
        "conflicts": [dict(row, id="cfl_%d" % n)
                      for n, row in enumerate(conflicts, 1)],
    }


def _needs_caption() -> dict:
    return {"code": "needs_caption",
            "reading": {"vision": "unsupported", "basis": "",
                        "observed": [], "proposed": [], "conflicts": []}}


def read_materials(text: str, image_data_url: str = None,
                   manual_caption: str = "") -> dict:
    """Understand the materials; never touch the library.

    Returns the response body (code/reading) without the envelope - the
    route adds request_id and revision, which are the page's concern,
    not the reading's.
    """
    text = str(text or "")
    caption = str(manual_caption or "").strip()
    image = str(image_data_url or "").strip() or None
    if not text.strip() and not image and not caption:
        raise IntakeError("bad_request",
                          "至少给一项素材：写一段文字，或放一张图", 400)
    if image:
        check_image_data_url(image)

    connector = _connector()
    user_text = _reading_user_text(text, caption, bool(image))
    if image:
        try:
            raw = _invoke_with_image(connector, user_text, image)
        except Exception as exc:  # noqa: BLE001 - classified below
            code = classify_provider_error(exc)
            if code == "unsupported_image":
                if caption:
                    raw = _invoke_text(connector, user_text)
                    reading = _parse_reading(raw)
                    return {"code": "ok", "reading": dict(
                        reading, vision="unsupported", basis="caption")}
                return _needs_caption()
            raise IntakeError(code, str(exc)) from exc
        reading = _parse_reading(raw)
        return {"code": "ok", "reading": dict(
            reading, vision="supported", basis="image")}

    try:
        raw = _invoke_text(connector, user_text)
    except Exception as exc:  # noqa: BLE001 - classified below
        raise IntakeError(classify_provider_error(exc), str(exc)) from exc
    reading = _parse_reading(raw)
    return {"code": "ok", "reading": dict(reading, vision="", basis="text")}


# --------------------------------------------------------------------------- #
# action=events: structure the text into roles and event lines
# --------------------------------------------------------------------------- #

#: One line that already looks like the shared parser's event format:
#: a role label, an optional type, an optional language tag, a colon.
_LABELLED_LINE = re.compile(
    r"^\s*(?P<name>[^\s：:|，,、]{1,40}?)\s*"
    r"(?:(?P<kind>动作|对白|入场|离场)\s*)?"
    r"(?:\[(?P<lang>zh|en)\]\s*)?[：:]\s*(?P<text>\S.*)$")

_EVENTS_SYSTEM = (
    "你把一段普通的故事文字整理成短剧的事件稿。输出一个 JSON 对象，"
    "不要输出任何其他文字：\n"
    '{"roles": [{"name": "…", "presence": "on_screen|off_screen",'
    ' "position": "left|right|center|none", "voice": ""}],'
    '\n "events": [{"role": 0, "type": "dialogue|action|enter|exit",'
    ' "text": "…", "language": "zh|en", "source": "…"}]}\n'
    "规则：\n"
    "- roles 是故事里实际开口或行动的人物，1..8 个，按出场顺序；"
    "旁白也算一个角色（presence 用 off_screen，position 用 none）。"
    "name 是人物名字（不超过 40 字，不要带标点）。\n"
    "- events 按发生顺序，1..200 条。role 是这条事件属于第几个角色"
    "（从 0 数）。台词用 dialogue，无声动作用 action，登场用 enter，"
    "退场用 exit。\n"
    "- text 是这一条的内容：台词就写台词本身（不加引号、不带角色名），"
    "动作写成一句描述。每条一段，不要换行。\n"
    "- source 写这句话（或这个动作）在原文里对应的片段，逐字摘录；"
    "原文里没有、由你补写的，source 留空字符串——补写会单独标出来等作者确认。\n"
    "- 原文的叙述、场景说明不要硬塞进事件；没有对应事件的片段会被单独列出。\n"
    "- language：这句是英文就写 en，否则 zh。\n"
    "输出只有这个 JSON 对象。"
)


def try_labelled_parse(text: str):
    """The deterministic fast path (§4.1): text that is already
    role-labelled event lines parses with the SHARED parser, and the
    model is never asked to rewrite what the author already wrote.

    Returns (roles, events, spans) or None when any line is not
    label-shaped - then the text is ordinary prose and needs the model.
    """
    from mie_craft.script.contract import parse_roles, parse_script

    names, spans = [], []
    offset = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            match = _LABELLED_LINE.match(line)
            if not match:
                return None
            name = match.group("name").strip()
            if not name:
                return None
            if name not in names:
                names.append(name)
            # 事件认领的是整行（含角色标签）：标签不是“没分配的故事内容”。
            spans.append((offset, offset + len(line)))
        else:
            spans.append(None)
        offset += len(line) + 1
    if not names:
        return None
    settings = "\n".join("角色%d：%s" % (number, name)
                         for number, name in enumerate(names, 1))
    try:
        roles = parse_roles(len(names), settings)
        events = parse_script(text, roles, "zh")
    except ValueError:
        return None
    event_spans = [span for span in spans if span is not None]
    return roles, events, event_spans


def _ui_roles(roles: list) -> list:
    return [{"ui_id": "r%d" % number, **{
        "name": role.get("name") or "",
        "presence": role.get("presence") or "on_screen",
        "initially_visible": role.get("initially_visible", True),
        "position": role.get("position") or "center",
        "voice": role.get("voice") or "",
    }} for number, role in enumerate(roles, 1)]


def _mapped_pieces(text: str, spans: list) -> list:
    """The pieces of the source no event claimed (§4.1 未分配片段)."""
    claimed = sorted(span for span in spans if span)
    pieces, cursor = [], 0

    def unclaimed(block_text):
        block = block_text.strip()
        if block and not block.startswith("#"):   # a comment is not content
            pieces.append({"text": block[:200]})

    for start, end in claimed:
        if start > cursor:
            unclaimed(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        unclaimed(text[cursor:])
    return pieces


def _parse_model_events(raw: str, text: str) -> dict:
    """Check the model's proposal field by field; map pieces to spans."""
    from webapp.services.autodraft import _json_object

    try:
        payload = _json_object(raw)
    except ValueError as exc:
        raise IntakeError("bad_reply", str(exc)) from exc
    raw_roles = payload.get("roles")
    raw_events = payload.get("events")
    if not isinstance(raw_roles, list) or not 1 <= len(raw_roles) <= 8:
        raise IntakeError("bad_reply", "模型没有给出 1..8 个角色")
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 200:
        raise IntakeError("bad_reply", "模型没有给出 1..200 条事件")

    roles = []
    for row in raw_roles:
        if not isinstance(row, dict):
            raise IntakeError("bad_reply", "角色的每一项要是一个对象")
        presence = row.get("presence") or "on_screen"
        if presence not in ("on_screen", "off_screen"):
            raise IntakeError("bad_reply",
                              "角色状态只能是 on_screen/off_screen：%r" % presence)
        roles.append({
            "name": str(row.get("name") or "").strip()[:40],
            "presence": presence,
            "initially_visible": row.get("initially_visible",
                                         presence == "on_screen"),
            "position": str(row.get("position") or ""),
            "voice": str(row.get("voice") or "")[:200],
        })
    if not any(role["name"] for role in roles):
        raise IntakeError("bad_reply", "模型没有给出任何角色名字")

    events, spans = [], []
    cursor = 0
    for number, row in enumerate(raw_events):
        if not isinstance(row, dict):
            raise IntakeError("bad_reply", "事件的每一项要是一个对象")
        index = row.get("role")
        if type(index) is not int or not 0 <= index < len(roles):
            raise IntakeError(
                "bad_reply",
                "第 %d 条事件引用的角色不存在（role 要是 0..%d 的序号）"
                % (number + 1, len(roles) - 1))
        kind = str(row.get("type") or "")
        if kind not in ("dialogue", "action", "enter", "exit"):
            raise IntakeError(
                "bad_reply", "第 %d 条事件的类型无法识别：%r" % (number + 1, kind))
        body = str(row.get("text") or "").strip().replace("\n", " ")
        if not body:
            raise IntakeError("bad_reply", "第 %d 条事件是空的" % (number + 1))
        language = str(row.get("language") or "zh")
        if language not in ("zh", "en"):
            language = "zh"
        source = str(row.get("source") or "").strip()
        span = None
        if source and text:
            found = text.find(source, cursor)
            if found < 0:              # maybe it appears earlier
                found = text.find(source)
            if found >= 0:
                span = [found, found + len(source)]
                cursor = max(cursor, found + len(source))
        # 没有原文时（纯图片整理）就不存在“补写”：一切都是建议，
        # 由 mode=proposed 表达；有原文时，认领不到片段的才算补写。
        events.append({
            "ui_id": "e%d_%d" % (number + 1, len(events) + 1),
            "role_ui_id": "r%d" % (index + 1),
            "type": kind, "text": body[:2000], "language": language,
            "span": span,
            "addition": bool(text) and span is None,
        })
        if span:
            spans.append(span)
    return {"roles": _ui_roles(roles), "events": events,
            "unmapped": _mapped_pieces(text, spans)}


def _reading_context_block(reading: dict) -> str:
    """The confirmed picture understanding as prompt context (F03).

    The author's TEXT stays the protected source for lines that must be
    kept verbatim; the picture facts and creative suggestions travel as
    their own labelled block so the model cannot blur them into
    dialogue the author never typed.
    """
    if not isinstance(reading, dict):
        return ""
    blocks = []
    observed = [str(t or "").strip() for t in reading.get("observed") or []
                if str(t or "").strip()]
    proposed = [str(t or "").strip() for t in reading.get("proposed") or []
                if str(t or "").strip()]
    caption = str(reading.get("caption") or "").strip()
    if caption:
        blocks.append("作者提供的人工图注（人工来源，当作图片事实）：" + caption)
    if observed:
        blocks.append("已确认的图片可见信息：\n" + "\n".join(
            "- " + t for t in observed))
    if proposed:
        blocks.append("已确认的创作建议（猜想，作者已核对）：\n" + "\n".join(
            "- " + t for t in proposed))
    for conflict in reading.get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        how = str(conflict.get("resolution") or "")
        if how == "text":
            blocks.append("图文冲突裁定（以文字为准）：图：%s／文：%s"
                          % (conflict.get("image") or "", conflict.get("text") or ""))
        elif how == "image":
            blocks.append("图文冲突裁定（以图片为准）：%s"
                          % (conflict.get("image") or ""))
        elif how == "manual":
            blocks.append("图文冲突裁定（作者改写）：%s"
                          % (conflict.get("override") or ""))
    if not blocks:
        return ""
    return "图片与理解（不是作者写的台词；台词保留只看作者原文）：\n" \
           + "\n".join(blocks)


def structure_events(text: str, reading: dict = None) -> dict:
    """Turn the author's materials into an editable event list.

    Already-labelled lines go through the shared parser with no model
    (the author wrote event lines; nobody rewrites them). Ordinary
    prose - or a PICTURE with its confirmed reading and no text at all
    (F03) - gets model PROPOSALS: every event carries the source piece
    it came from, and pieces nobody claimed are listed so the author
    can assign, mark as scene notes, or drop the keep-mark.
    """
    text = str(text or "").strip()
    if len(text) > 30000:
        raise IntakeError("bad_request", "文字太长：最多 30000 字", 400)
    context = _reading_context_block(reading)
    if not text and not context:
        raise IntakeError("bad_request",
                          "素材是空的：写一段文字，或放一张图并解读后再整理", 400)

    parsed = try_labelled_parse(text) if text else None
    if parsed is not None:
        roles, events, spans = parsed
        ui_roles = _ui_roles(roles)
        return {
            "mode": "parsed",
            "roles": ui_roles,
            "events": [{
                "ui_id": "e%d_%d" % (number, number),
                "role_ui_id": ui_roles[int(event["character"].split("_")[1]) - 1]["ui_id"],
                "type": event["type"], "text": event["text"],
                "language": event["language"],
                "span": [span[0], span[1]],
                "addition": False,
            } for number, (event, span) in enumerate(zip(events, spans), 1)],
            "unmapped": _mapped_pieces(text, spans),
        }

    connector = _connector()
    parts = ["整理这份素材，产出角色和事件稿。"]
    if text:
        parts.append("作者写的原文（要保留的台词以此为准）：\n" + text)
    if context:
        parts.append(context)
    if not text:
        parts.append("（作者没有写文字，只提供了上面的图片信息。）")
    user_text = "\n\n".join(parts)
    try:
        raw = connector.invoke([
            {"role": "system", "content": _EVENTS_SYSTEM},
            {"role": "user", "content": user_text},
        ], max_tokens=8192)
        raw = str(raw or "")
    except Exception as exc:  # noqa: BLE001 - classified below
        raise IntakeError(classify_provider_error(exc), str(exc)) from exc
    result = _parse_model_events(raw, text)
    return dict(result, mode="proposed")


# --------------------------------------------------------------------------- #
# POST /api/scripts/intake/validate: deterministic checks, no model
# --------------------------------------------------------------------------- #

#: The H3 grid's per-shot ceiling and the overlap slack: with at most 20
#: shots and a 22-frame overlap, NO grouping can buy more than about
#: this many seconds of total duration below the summed content floor -
#: which is what makes a duration conflict PROVABLE before grouping
#: exists (the generator's own probe deliberately claims less).
_PROVABLE_SLACK_SECONDS = 18.0

_SHOT_CEILING_SECONDS = 30.0


def validate_submission(roles: list, events: list, *, name: str,
                        language: str, shot_count: int,
                        target_duration_seconds: float, audio_mode: str,
                        camera: dict, look: str, setting: str, scene: str,
                        constraints: str, light: str, light_text: str) -> dict:
    """The deterministic half of the checks, with field-level answers.

    Everything here is provable without a model: the serialization, the
    shared parser, the header contract (via the same probe the
    generator uses) and duration arithmetic on the content floor.
    """
    from mie_craft.script.contract import (allocate_frames, estimate_shot_seconds,
                                           parse_roles, parse_script, validate_v2)
    from webapp.services.autodraft import (_event_line, _role_settings_line,
                                           _round_trip_problems)

    errors = []

    def header_error(message):
        errors.append({"event_index": None, "message": message})

    if not isinstance(roles, list) or not 1 <= len(roles) <= 8:
        header_error("角色数要在 1..8 个")
        roles = roles if isinstance(roles, list) else []
    if not isinstance(events, list) or not 1 <= len(events) <= 200:
        header_error("事件数要在 1..200 条")
        events = events if isinstance(events, list) else []
    language = language if language in ("zh", "en") else "zh"

    settings_lines = []
    for number, role in enumerate(roles, 1):
        try:
            settings_lines.append(_role_settings_line(number, role))
        except ValueError as exc:
            errors.append({"event_index": None,
                           "message": str(exc)})
    source_lines = []
    for number, event in enumerate(events, 1):
        try:
            source_lines.append(_event_line(number, event, len(roles), language))
        except ValueError as exc:
            errors.append({"event_index": number - 1, "message": str(exc)})
    if errors:
        return {"ok": False, "errors": errors, "warnings": [],
                "roles": [], "events": [], "source_script": ""}

    settings = "\n".join(settings_lines)
    source = "\n".join(source_lines)
    try:
        parsed_roles = parse_roles(len(roles), settings)
        parsed_events = parse_script(source, parsed_roles, language)
    except ValueError as exc:
        return {"ok": False,
                "errors": [{"event_index": None, "message": str(exc)}],
                "warnings": [], "roles": [], "events": [],
                "source_script": source}
    for problem in _round_trip_problems(events, parsed_events, language):
        errors.append(problem)

    try:
        shots_wanted = int(shot_count) if shot_count else 0
    except (TypeError, ValueError):
        shots_wanted = -1
    if shots_wanted not in range(0, min(20, len(events)) + 1):
        errors.append({"event_index": None,
                       "message": "段数（分镜数）shot_count 要么是 0（自动），"
                                  "要么是 1..20 且不超过事件数（现在有 %d 条事件）"
                                  % len(events)})
        shots_wanted = 0

    # The header contract, checked exactly the way the generator probes
    # it - camera enums, light, scene, name, duration range, ... .
    duration = float(target_duration_seconds or 20)
    header = {"version": 2, "name": str(name or "新剧本"), "language": language,
              "roles": parsed_roles,
              "camera": {"framing": str((camera or {}).get("framing") or "medium_shot"),
                         "movement": str((camera or {}).get("movement") or "locked"),
                         "perspective": str((camera or {}).get("perspective") or "observer")},
              "look": str(look or ""), "setting": str(setting or ""),
              "constraints": str(constraints or ""),
              "target_duration_seconds": duration,
              "continuation_overlap_frames": 22,
              "source_script": source}
    if str(scene or "").strip():
        header["scene"] = str(scene).strip()
    if str(light or "follow_image") != "follow_image":
        header["light"] = str(light)
    if str(light or "") == "custom":
        header["light_text"] = str(light_text or "")
    probe_count = shots_wanted or min(len(parsed_events), 20)
    groups = [parsed_events[i * len(parsed_events) // probe_count:
                            (i + 1) * len(parsed_events) // probe_count]
              for i in range(probe_count)]
    probe = dict(header, shots=[{"id": "scene_%02d" % (i + 1),
                                 "duration_seconds":
                                     min(30, duration / probe_count + 1),
                                 "events": group}
                                for i, group in enumerate(groups)])
    try:
        validate_v2(probe, _check_timing=False, _check_execution=False)
    except ValueError as exc:
        header_error(str(exc))

    # Duration arithmetic that holds for EVERY grouping (the content
    # floor is additive; no grouping can beat it by more than the
    # overlap slack), so it can block the model call.
    if isinstance(events, list) and events:
        floors = [estimate_shot_seconds({"events": [event]})
                  for event in parsed_events]
        for number, floor in enumerate(floors, 1):
            if floor > _SHOT_CEILING_SECONDS:
                errors.append({
                    "event_index": number - 1,
                    "message": "第 %d 条事件按语速下限估算约 %.0f 秒，超过单镜 "
                               "30 秒上限：精简这条，或拆成几条"
                               % (number, floor)})
        total_floor = sum(floors)
        if total_floor - _PROVABLE_SLACK_SECONDS > duration:
            errors.append({
                "event_index": None,
                "message": "这些事件按语速下限估算共约 %.0f 秒，目标时长只有 "
                           "%.0f 秒：加长目标时长，或精简事件"
                           % (total_floor, duration)})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [],
        "roles": parsed_roles,
        "events": parsed_events,
        "source_script": source,
        "counts": {"roles": len(parsed_roles), "events": len(parsed_events),
                   "shot_count": shots_wanted},
    }
