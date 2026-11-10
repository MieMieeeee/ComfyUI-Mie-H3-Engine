"""Pinned copies of what a submitted run will render.

The engine's loader nodes read the library WHEN THEY RUN, not when the
prompt is queued. A script edited while its render waits in the queue
would therefore render something other than what the page previewed -
and the roles could even change order under it.

So a submission pins its script: the JSON that was validated at submit
time is written into a snapshot library and the loader node is pointed
at that. The run reads the snapshot, and editing the script afterwards
does not touch the queued render.

The character and scene assets cannot be pinned the same way: the
MieNodes loader accepts a ``library_dir`` and ignores it (it loads from
the default library by name). Their content hash is recorded instead,
and the task list says so when one of them changed while the render
was waiting.
"""

from __future__ import annotations

import json
import pathlib
import shutil

from mie_craft.assets import library as _library
from mie_craft.assets import script_asset as _script_asset

from webapp import config

#: How many snapshots are kept. Each one is a small JSON file.
KEEP = 20


def write_script_snapshot(run_name: str, script_name: str, payload: dict) -> str:
    """Pin one script into the run's snapshot library.

    The layout is the library's own (``storyboards/<name>/storyboard.json``),
    because that is what the loader resolves ``library_dir`` against. The
    JSON's ``name`` has to equal the directory name - the loader checks
    that, the same way the library does.
    """
    root = config.SNAPSHOT_DIR / run_name
    target = root / "storyboards" / script_name
    target.mkdir(parents=True, exist_ok=True)
    pinned = dict(payload)
    pinned["name"] = script_name
    (target / "storyboard.json").write_text(
        json.dumps(pinned, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(root)


def script_fingerprint(script_name: str) -> str:
    """The content hash of the library's script, or "" if unreadable."""
    try:
        return _script_asset.storyboard_cache_key("", script_name)
    except Exception:  # noqa: BLE001 - a missing script has no hash
        return ""


def asset_fingerprint(kind: str, name: str) -> str:
    """The content hash of one character or scene asset."""
    try:
        if kind == "scene":
            return _library.scene_cache_key("", name)
        return _library.character_cache_key("", name)
    except Exception:  # noqa: BLE001
        return ""


def changed_assets(kind: str, names: dict, marks: dict) -> list:
    """The assets whose content differs from what was recorded.

    ``names`` maps a role id to the asset that was wired at submit time,
    ``marks`` the content hash recorded for it then. An asset with no
    hash recorded cannot be judged, so it is not reported.
    """
    changed = []
    for role_id, name in (names or {}).items():
        if not name or role_id not in (marks or {}):
            continue
        if asset_fingerprint(kind, name) != marks[role_id]:
            changed.append(name)
    return changed


def prune(keep: int = KEEP) -> None:
    """Drop the oldest snapshots, so the folder does not grow forever."""
    root = config.SNAPSHOT_DIR
    if not root.is_dir():
        return
    runs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
