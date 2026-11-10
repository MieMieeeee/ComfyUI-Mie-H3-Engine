"""What the author keeps, and what they put away.

Two small pieces of the workbench's own bookkeeping:

``favorites`` - the runs the author wants to keep in sight. The film is
on disk either way; this is about the list, not about the file.

``dismissed`` - the runs the author took out of the history. The files
stay on disk (deleting a film is a separate, deliberate act), but the
run must not come back under the author's nose the next time the list
is read - the engine's output folder would put it right back in.

Both live in the settings folder, next to the job log: they are the
workbench's business, not the asset's.
"""

from __future__ import annotations

import json
import pathlib

from webapp import config


def _file() -> pathlib.Path:
    return config.SETTINGS_FILE.parent / "history.json"


def _read() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    config.ensure_dirs()
    _file().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# favourites
# --------------------------------------------------------------------------- #

def favorites() -> list:
    """The favourited run names."""
    return [str(n) for n in (_read().get("favorites") or []) if n]


def is_favorite(run_name: str) -> bool:
    return str(run_name or "") in favorites()


def toggle_favorite(run_name: str) -> bool:
    """Mark or unmark a run; returns the new state."""
    name = str(run_name or "")
    if not name:
        return False
    data = _read()
    names = [str(n) for n in (data.get("favorites") or []) if n]
    if name in names:
        names.remove(name)
        state = False
    else:
        names.append(name)
        state = True
    data["favorites"] = names
    _write(data)
    return state


# --------------------------------------------------------------------------- #
# dismissed runs
# --------------------------------------------------------------------------- #

def dismissed() -> list:
    """The runs the author took out of the history."""
    return [str(n) for n in (_read().get("dismissed") or []) if n]


def is_dismissed(run_name: str) -> bool:
    return str(run_name or "") in dismissed()


def dismiss(run_name: str) -> None:
    """Take a run out of the history. The files stay where they are."""
    name = str(run_name or "")
    if not name:
        return
    data = _read()
    names = [str(n) for n in (data.get("dismissed") or []) if n]
    if name not in names:
        names.append(name)
    data["dismissed"] = names
    _write(data)


def undismiss(run_name: str) -> None:
    """Put a run back into the history."""
    name = str(run_name or "")
    data = _read()
    names = [str(n) for n in (data.get("dismissed") or []) if n]
    data["dismissed"] = [n for n in names if n != name]
    _write(data)
