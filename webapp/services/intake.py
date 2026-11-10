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
