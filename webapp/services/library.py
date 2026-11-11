"""Character and scene assets: list, read, write, delete.

Everything goes through ``mie_craft.assets.library`` - the same
implementation the ComfyUI nodes use, so the web app and the plugin can
never drift apart. The web app adds only the two things the library
deliberately leaves out: turning an uploaded file into the bytes the
library stores (PNG for images, WAV for audio).

Two things to keep in mind:

  * ``plugin_root`` must be passed on every call. The library's default
    is the package's own parent directory, which for a pip-installed
    mie_craft is site-packages - the user's library would vanish.
  * The library writes audio as ``NN.wav``, so an uploaded MP3 has to be
    converted before it is handed over, or the file on disk lies about
    its contents.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import time

from mie_craft.assets import library as _library
from mie_craft.assets import script_asset as _script_asset

from webapp import config
from webapp.services import jsonfile, versions


def plugin_root() -> pathlib.Path:
    """The ComfyUI pack that owns the library."""
    return config.PLUGIN_ROOT


def library_dir() -> str:
    """The library root, as the library itself resolves it."""
    return _library.resolve_library_dir("", plugin_root=plugin_root())


def _library_kwargs() -> dict:
    return {"plugin_root": plugin_root()}


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #

def list_assets() -> dict:
    """Every character, scene and storyboard, with their counts.

    ``list_assets_payload`` never raises - it reports the problem in the
    payload - so a broken library still produces a page.
    """
    payload = _library.list_assets_payload(library_dir(), **_library_kwargs())
    payload = _script_asset.augment_assets_payload(payload, library_dir())
    payload["library_dir"] = library_dir()
    payload["broken"] = broken_assets()
    return payload


def broken_assets() -> dict:
    """The assets the library could not read, per kind.

    The library SKIPS a broken folder instead of listing it, so without
    this it would be invisible on the page - and impossible to delete,
    because the delete button lives on the asset it cannot open.
    """
    result = {}
    for kind, lister in (("character", _library.list_characters),
                          ("scene", _library.list_scenes)):
        try:
            report = lister(library_dir(), **_library_kwargs())
        except (ValueError, OSError):
            report = {}
        result[kind] = _skipped_rows(report.get("status", ""))
    try:
        boards = _script_asset.list_storyboards(library_dir())
    except (ValueError, OSError):
        boards = {}
    result["storyboard"] = _skipped_rows(boards.get("status", ""))
    return result


def _skipped_rows(status: str) -> list:
    """The library's skip report, as rows: [{name, error}, ...]."""
    rows = []
    for line in (status or "").splitlines():
        text = line.strip()
        if text.startswith("skipped ") and ": " in text:
            name, reason = text[len("skipped "):].split(": ", 1)
            rows.append({"name": name, "error": reason})
    return rows


def list_characters() -> list[str]:
    """The character names, as a list (the library returns a string)."""
    result = _library.list_characters(library_dir(), **_library_kwargs())
    return [n for n in result.get("names", "").splitlines() if n.strip()]


def list_scenes() -> list[str]:
    result = _library.list_scenes(library_dir(), **_library_kwargs())
    return [n for n in result.get("names", "").splitlines() if n.strip()]


def list_storyboards() -> list[str]:
    result = _script_asset.list_storyboards(library_dir())
    return [n for n in result.get("names", "").splitlines() if n.strip()]


# --------------------------------------------------------------------------- #
# one asset
# --------------------------------------------------------------------------- #

def _png_data_url(png_bytes: bytes) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def get_character(name: str) -> dict:
    """One character's metadata, its portrait and its audio clips."""
    data = _library.load_character(library_dir(), name, **_library_kwargs())
    images = data.get("png_bytes_list") or []
    return {
        "kind": "character",
        "name": data.get("name", name),
        "description": data.get("description", ""),
        "voice": data.get("voice", ""),
        "image_count": len(images),
        "audio_count": len(data.get("audio_bytes_list") or []),
        "image": _png_data_url(images[0]) if images else "",
        "images": [_png_data_url(b) for b in images],
        "audio_exts": data.get("audio_exts") or [],
    }


def get_scene(name: str) -> dict:
    """One scene's metadata and its picture."""
    data = _library.load_scene(library_dir(), name, **_library_kwargs())
    images = data.get("png_bytes_list") or []
    return {
        "kind": "scene",
        "name": data.get("name", name),
        "description": data.get("description", ""),
        "ambience": data.get("ambience", ""),
        "image_count": len(images),
        "image": _png_data_url(images[0]) if images else "",
        "images": [_png_data_url(b) for b in images],
    }


def get_asset_meta(name: str, kind: str) -> dict:
    """The cheap metadata read - no image bytes."""
    return _library.read_asset_meta(library_dir(), name, kind, **_library_kwargs())


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def to_png_bytes(raw: bytes) -> bytes:
    """Turn an uploaded image into PNG bytes.

    The library stores PNG and checks that all the frames of one asset
    share a resolution, so anything else has to be converted.
    """
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") else img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def to_wav_bytes(raw: bytes) -> bytes:
    """Turn an uploaded clip into WAV bytes.

    The library names the file ``NN.wav`` no matter what it is given, so
    an MP3 handed over as-is would be a lie on disk.
    """
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return raw
    try:
        import soundfile
    except ImportError:
        raise ValueError(
            "reading this audio format needs the soundfile package "
            "(pip install soundfile)")
    data, rate = soundfile.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    buf = io.BytesIO()
    soundfile.write(buf, data, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def save_character(name: str, description: str = None, voice: str = None,
                   image_raw: bytes = None, audio_raw: bytes = None) -> dict:
    """Create or update a character.

    ``image_raw`` / ``audio_raw`` of None mean "keep what is on disk", so
    editing the description never drops the portrait.

    ``description`` / ``voice`` of None mean the same thing: keep what is
    on disk. The library requires both to be non-empty, so a caller that
    only wants to change one of them would otherwise have to read the
    asset first and send both back.
    """
    if description is None or voice is None:
        try:
            current = _library.load_character(library_dir(), name, **_library_kwargs())
        except (FileNotFoundError, ValueError):
            current = {}
        if description is None:
            description = current.get("description", "")
        if voice is None:
            voice = current.get("voice", "")
    png_bytes = None
    if image_raw is not None:
        png_bytes = [to_png_bytes(image_raw)]
    wav_bytes = None
    if audio_raw is not None:
        wav_bytes = [to_wav_bytes(audio_raw)]
    return _library.save_character(
        library_dir(), name, description, voice,
        png_bytes_list=png_bytes, wav_bytes_list=wav_bytes, **_library_kwargs())


def save_scene(name: str, description: str, ambience: str = None,
               image_raw: bytes = None) -> dict:
    """Create or update a scene.

    ``ambience`` of None keeps the stored value; an empty string clears
    it. That is the library's convention, not the usual one.
    """
    png_bytes = None
    if image_raw is not None:
        png_bytes = [to_png_bytes(image_raw)]
    return _library.save_scene(
        library_dir(), name, description, png_bytes_list=png_bytes,
        ambience=ambience, **_library_kwargs())


def delete_character(name: str) -> dict:
    return _library.delete_character(library_dir(), name, **_library_kwargs())


def delete_scene(name: str) -> dict:
    return _library.delete_scene(library_dir(), name, **_library_kwargs())


# --------------------------------------------------------------------------- #
# script assets
# --------------------------------------------------------------------------- #

def unreadable_reason(name: str, reason: str) -> str:
    """Say what is actually wrong with a board, in a way to act on.

    "missing storyboard.json" on its own leaves the reader guessing. The
    folder is right there, so the message names what IS in it - a JSON
    file under another name is usually the very board that is wanted.
    """
    if "storyboard.json" not in str(reason):
        return str(reason)
    if "missing" not in str(reason) and "not found" not in str(reason):
        return str(reason)
    folder = pathlib.Path(library_dir()) / "storyboards" / name
    others = sorted(p.name for p in folder.glob("*.json")) if folder.is_dir() else []
    head = ("缺少 storyboard.json：引擎只读 <剧本名>/storyboard.json 这一个文件。")
    if not others:
        return head + "这个文件夹里也没有别的 json。"
    return head + ("这个文件夹里现在只有：%s —— 里面很可能就是这份剧本，"
                   "只是文件名不对。" % "、".join(others))


def get_storyboard(name: str) -> dict:
    """One script asset: its JSON, its preview and its requirements."""
    try:
        cleaned, path = _script_asset.load_storyboard(library_dir(), name)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(unreadable_reason(name, str(exc))) from exc
    meta = _script_asset.read_storyboard_meta(library_dir(), name)
    state = execution_state(name, cleaned)
    return {
        "name": cleaned.get("name", name),
        "path": path,
        "version": cleaned.get("version"),
        "json": cleaned,
        "preview": _script_asset.storyboard_preview(cleaned),
        "requirements": meta.get("requirements") or [],
        "sections": meta.get("sections") or [],
        "source_hash": source_fingerprint(cleaned),
        "execution_state": state,
        "execution_reason": execution_reason(state),
    }


# --------------------------------------------------------------------------- #
# the source / execution-mirror pair
# --------------------------------------------------------------------------- #

def source_fingerprint(script: dict) -> str:
    """A hash of everything the English execution mirror describes.

    The mirror is written by hand (or by a model), so nothing keeps it in
    step with the Chinese source it was made from. This hash is what
    tells the page that the source moved and the mirror did not.
    """
    import hashlib

    source = {
        "look": script.get("look") or "",
        "setting": script.get("setting") or "",
        "scene": script.get("scene") or "",
        "light": script.get("light") or "",
        "light_text": script.get("light_text") or "",
        "roles": [{
            "id": role.get("id"), "name": role.get("name"),
            "presence": role.get("presence"), "position": role.get("position"),
            "voice": role.get("voice"),
        } for role in script.get("roles") or []],
        "events": [{
            "id": event.get("id"), "type": event.get("type"),
            "character": event.get("character"), "text": event.get("text"),
            "language": event.get("language"),
        } for shot in script.get("shots") or []
            for event in shot.get("events") or []],
        "designs": script.get("character_designs") or {},
    }
    blob = json.dumps(source, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _sync_file() -> pathlib.Path:
    return config.SETTINGS_FILE.parent / "execution_sync.json"


def _load_sync() -> dict:
    try:
        data = json.loads(_sync_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def confirm_execution(name: str, script: dict) -> dict:
    """Record that the mirror matches the source, as of now.

    The record is the app's own business: the contract has no room for
    it (the payload rejects unknown fields), and it belongs to the work
    session rather than to the asset.

    Two fingerprints are kept, because "confirmed" is a claim about a
    PAIR: the mirror was confirmed against this source, and it has not
    been touched since. The source hash says the first half; the mirror
    hash says the second.
    """
    data = _load_sync()
    data[name] = {"hash": source_fingerprint(script),
                  "execution_hash": execution_fingerprint(script),
                  "confirmed_at": time.time()}
    config.ensure_dirs()
    jsonfile.write_json(_sync_file(), data)
    return {"name": name, "confirmed": True}


def execution_fingerprint(script: dict) -> str:
    """A hash of the English mirror itself.

    Without it, a hand-edit of the English text after the confirmation
    would still read as "confirmed" - the confirmation says nothing
    about a text that was written after it.

    The optional keys are dropped when they are empty: the contract
    removes them on save, and a hash that changed because a key was
    dropped would report a stale mirror that is in fact current.
    """
    import hashlib

    execution = dict(script.get("execution") or {})
    for key in ("scene", "light", "character_designs"):
        if not execution.get(key):
            execution.pop(key, None)
    blob = json.dumps(execution, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def execution_state(name: str, script: dict) -> str:
    """How the English mirror stands against the Chinese source.

    ``confirmed`` - the mirror was confirmed against this source and
                    has not been touched since.
    ``changed``   - it was confirmed against an EARLIER source, so the
                    source moved and the mirror may not describe it.
    ``edited``    - the mirror was changed after the confirmation, so
                    what was confirmed is not what is there now.
    ``unknown``   - nobody ever confirmed it (every script written
                    before this feature existed).
    """
    record = _load_sync().get(name) or {}
    if not record.get("hash"):
        return "unknown"
    if record["hash"] != source_fingerprint(script):
        return "changed"
    confirmed_mirror = record.get("execution_hash")
    if confirmed_mirror and confirmed_mirror != execution_fingerprint(script):
        return "edited"
    return "confirmed"


def execution_reason(state: str) -> str:
    """What the state means, in words the reader can act on."""
    return {
        "confirmed": "英文镜像与当前中文设定一致。",
        "changed": "中文设定改过了，英文镜像还是旧的：生成会用旧描述。",
        "edited": "英文镜像在确认之后被改过，这次确认不再作数。",
        "unknown": "这份剧本的英文镜像还没有确认过。",
    }.get(state, "")


def save_storyboard(payload: dict, note: str = "") -> dict:
    """Validate and write a script asset.

    The write goes through the version store: the copy that is being
    replaced is kept, so nothing that was ever saved is lost. ``note``
    says how this version came about ("AI 修订", "手工修改"), and it
    shows up in the version list.
    """
    result = versions.save_versioned(str(payload.get("name") or ""), payload,
                                     note=note)
    cleaned = result.get("json") or {}
    return {
        "name": cleaned.get("name", ""),
        "path": result.get("path", ""),
        "status": result.get("status", ""),
        "preview": _script_asset.storyboard_preview(cleaned),
        "content_version": result.get("content_version", 0),
    }


def delete_storyboard(name: str) -> dict:
    return _script_asset.delete_storyboard(library_dir(), name)


def list_storyboards_meta() -> list[dict]:
    """One light summary row per script asset, for the list view.

    Loads every board (they are small JSON files) so the list can show
    roles/shots/duration without a second request per selection. Boards
    the library skips (broken JSON, name mismatch) still get a row - with
    the reason - so they can be spotted in the list instead of silently
    vanishing from it.
    """
    result = _script_asset.list_storyboards(library_dir())
    rows = []
    for line in (result.get("status") or "").splitlines():
        text = line.strip()
        if text.startswith("skipped ") and ": " in text:
            name, reason = text[len("skipped "):].split(": ", 1)
            rows.append({"name": name, "error": unreadable_reason(name, reason)})
    for name in [n for n in result.get("names", "").splitlines() if n.strip()]:
        try:
            cleaned, _path = _script_asset.load_storyboard(library_dir(), name)
        except (ValueError, FileNotFoundError) as exc:
            rows.append({"name": name,
                         "error": unreadable_reason(name, str(exc))})
            continue
        rows.append({
            "name": name,
            "roles": len(cleaned.get("roles", [])),
            "shots": len(cleaned.get("shots", [])),
            "duration_seconds": cleaned.get("target_duration_seconds"),
            "audio_mode": cleaned.get("audio_mode", "generated"),
            # 这一份剧本现在到第几版（内容版本，不是渲染次数）。
            "content_version": versions.content_version(name),
        })
    return rows


def validate_storyboard(payload: dict) -> dict:
    """Check a script asset without writing it.

    Returns the errors and the render-risk warnings separately: an error
    means the contract rejects it, a warning means it will render but
    something is tight.
    """
    from mie_craft.script import contract
    try:
        cleaned = contract.validate_v2(payload)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": []}
    warnings = contract.storyboard_warnings(cleaned)
    return {"ok": True, "errors": [], "warnings": warnings, "json": cleaned}
