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
from webapp.services import jsonfile


def _file() -> pathlib.Path:
    return config.SETTINGS_FILE.parent / "history.json"


#: The parsed file, with the stat that produced it. The task list asks
#: about the favourites and the dismissed runs of every record on every
#: poll; reading and parsing the file once per question was a hundred
#: reads every three seconds. The stat travels with the answer, so a
#: change on disk is seen on the very next question.
_cache: dict = {"key": None, "data": {}}


def _read() -> dict:
    path = _file()
    try:
        info = path.stat()
    except OSError:
        return {}
    key = (str(path), info.st_mtime_ns, info.st_size)
    if _cache["key"] == key:
        # a copy, not the cache itself: the callers edit what they got,
        # and a failed write must not leave the edit behind in the cache
        return dict(_cache["data"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        data = {}
    _cache["key"] = key
    _cache["data"] = data
    return dict(data)


def _write(data: dict) -> None:
    config.ensure_dirs()
    jsonfile.write_json(_file(), data)
    # What was just written is the truth now; say so before anyone asks.
    try:
        info = _file().stat()
        _cache["key"] = (str(_file()), info.st_mtime_ns, info.st_size)
    except OSError:
        _cache["key"] = None
    _cache["data"] = dict(data) if isinstance(data, dict) else {}


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


# --------------------------------------------------------------------------- #
# the version counter
# --------------------------------------------------------------------------- #

def last_attempt(script_name: str) -> int:
    """The highest version number this script has been given."""
    attempts = _read().get("attempts") or {}
    try:
        return int(attempts.get(str(script_name)) or 0)
    except (TypeError, ValueError):
        return 0


def claim_attempt(script_name: str, number: int) -> None:
    """Record the version number a run has taken.

    The counter only ever goes up. Taking a run out of the list must
    not hand its number to the next one: that would reuse its folder
    on disk - and the new run would hide behind the old one's "put
    away" mark, so the author would never see it.
    """
    name = str(script_name or "")
    if not name:
        return
    data = _read()
    attempts = dict(data.get("attempts") or {})
    try:
        current = int(attempts.get(name) or 0)
    except (TypeError, ValueError):
        current = 0
    if int(number) > current:
        attempts[name] = int(number)
        data["attempts"] = attempts
        _write(data)
