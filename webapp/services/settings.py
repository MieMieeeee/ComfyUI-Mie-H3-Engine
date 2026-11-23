"""The workbench's own settings, in one file.

The theme, the ComfyUI address and the review opt-in all live in the
same JSON file next to the app's data. A read is cheap and happens on
every page; a write goes through the shared lock, so a theme change and
a review change cannot overwrite each other.

Nothing here decides anything about a render - it only remembers what
the author chose.
"""

from __future__ import annotations

import json

from webapp import config
from webapp.services import jsonfile


def read() -> dict:
    """Every setting the workbench has saved."""
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return {}
    return saved if isinstance(saved, dict) else {}


def write(values: dict) -> dict:
    """Save some settings, keeping the ones that are not in ``values``."""
    config.ensure_dirs()
    with jsonfile.settings_lock:
        saved = read()
        for key, value in (values or {}).items():
            saved[str(key)] = value
        jsonfile.write_json(config.SETTINGS_FILE, saved)
    return saved


def get(name: str, default=None):
    """One setting."""
    return read().get(name, default)


def set_value(name: str, value):
    """Save one setting; returns the whole file."""
    return write({str(name): value})
