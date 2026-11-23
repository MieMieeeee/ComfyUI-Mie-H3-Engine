"""Compile a script asset into a plan before the graph runs.

The Scene Prompt Editor can read a Plan node's widget, but not the value
sitting on ``plan_json_input``. That socket is what ``MieH3ScriptToPlan``
fills at execution time, so a preview has to be compiled here, from the
script file, by the same assembler the node calls.

``script_ref`` is an asset id or a path relative to the asset library.
It is never allowed to leave that library, including through a symlink.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mie_craft.assets import library as asset_library
from mie_craft.assets import script_asset
from mie_craft.h3_loop_prompts import plan_to_json_string
from mie_craft.script.builder import assemble_plan

try:
    from . import asset_root
except ImportError:
    import asset_root


class ScriptRefError(ValueError):
    """The caller asked for a script the route must not open."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _library_dir(explicit: str) -> str:
    return str(explicit or "").strip() or str(asset_root.asset_library_dir())


def resolve_script_file(library_dir: str, script_ref: str) -> Path:
    """Return the storyboard file for ``script_ref``, still inside the library.

    An asset id is ``storyboards/<id>/storyboard.json``. A relative path
    must name that same file. Absolute paths, ``..``, and any symlink
    along the way are rejected.
    """
    root = Path(asset_library.validate_library_dir(_library_dir(library_dir)))
    ref = str(script_ref or "").strip().replace("\\", "/")
    if not ref:
        raise ScriptRefError("script_ref is required")
    if ref.startswith("/") or ref.startswith("//") or _is_windows_absolute(ref):
        raise ScriptRefError(
            "script_ref must be an asset id or a path under the library")
    parts = [part for part in ref.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ScriptRefError("script_ref must not contain '..'")

    if len(parts) == 1:
        name = asset_library.validate_name(parts[0])
        relative = Path("storyboards") / name / script_asset.STORYBOARD_JSON_NAME
    else:
        relative = Path(*parts)
        if (relative.name != script_asset.STORYBOARD_JSON_NAME
                or len(relative.parts) != 3
                or relative.parts[0] != "storyboards"):
            raise ScriptRefError(
                "script_ref must be an asset id or "
                "storyboards/<name>/storyboard.json")
        asset_library.validate_name(relative.parts[1])

    _reject_symlinks(root, relative)
    path = root.joinpath(relative)
    real_root = root.resolve()
    real_file = path.resolve()
    try:
        inside = os.path.commonpath([str(real_root), str(real_file)]) == str(real_root)
    except ValueError:
        inside = False
    if not inside:
        raise ScriptRefError(
            "script_ref resolves outside the asset library", status=403)
    return path


def build_preview(library_dir: str, script_ref: str) -> dict:
    """Compile ``script_ref`` and describe the file the compilation read.

    Slot bundles are empty: this is the script as stored, before a graph
    wires portraits. ``assemble_plan`` is the same function the node uses,
    so a script that cannot be compiled unwired fails here the same way.
    """
    path = resolve_script_file(library_dir, script_ref)
    if not path.is_file():
        raise FileNotFoundError("script %r was not found at %s" % (script_ref, path))
    name = path.parent.name
    root = path.parent.parent.parent
    cleaned, _stored = script_asset.load_storyboard(str(root), name)
    assembled = assemble_plan(storyboard=cleaned, slot_bundles={})
    plan_json = plan_to_json_string(assembled["plan"])
    raw = path.read_bytes()
    stat = path.stat()
    return {
        "plan_json": plan_json,
        "plan_sha256": hashlib.sha256(plan_json.encode("utf-8")).hexdigest()[:8],
        "source_mtime_ns": int(stat.st_mtime_ns),
        "source_etag": hashlib.sha256(raw).hexdigest(),
    }


def register_with_prompt_server() -> None:
    """Attach ``POST /mieh3/plan_preview`` when ComfyUI has already started."""
    try:
        from server import PromptServer
    except Exception:
        return
    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        return
    instance.routes.post("/mieh3/plan_preview")(_handle)


async def _handle(request):
    from aiohttp import web

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "JSON body required"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON object required"}, status=400)
    try:
        payload = build_preview(
            str(body.get("library_dir") or ""),
            str(body.get("script_ref") or ""),
        )
    except ScriptRefError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=422)
    return web.json_response(payload)


def _is_windows_absolute(ref: str) -> bool:
    return len(ref) >= 2 and ref[1] == ":"


def _reject_symlinks(root: Path, relative: Path) -> None:
    current = root
    if current.is_symlink():
        raise ScriptRefError("asset library root is a symlink", status=403)
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ScriptRefError(
                "script_ref walks through a symlink", status=403)
