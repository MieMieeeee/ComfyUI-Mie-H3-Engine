"""The versions of a script.

This is about the CONTENT of a script: the copy you are editing, the
one before it, the one before that. It is not about how many times a
script was rendered - that counter lives in ``history.py`` and numbers
the renders of a script, not the script.

Where the versions live
-----------------------

Next to the script itself::

    <library>/storyboards/<name>/
        storyboard.json      <- the current version (the engine reads this one)
        versions/
            index.json       <- the list: which version is current, and what each was
            0001.json        <- one version, whole and unchanged

The engine only ever reads ``<name>/storyboard.json``; it never looks
inside the folder, so a ``versions/`` subdirectory is invisible to it
and the script keeps loading exactly as before. The version metadata
cannot go inside ``storyboard.json`` either: the contract rejects
unknown top-level fields.

The rule
--------

**Saving always makes a new version; an old version is never touched.**
A version's content is written the moment it becomes the current one,
so the copy that is being replaced is already on disk before the
replacement happens. Restoring an old version does not move a pointer
back: it saves that content as a new version, so the history only ever
grows and the current version always has the highest number.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import time

from mie_craft.assets import script_asset as _script_asset
from mie_craft.script import contract

#: The folder inside a script's own directory.
VERSIONS_DIRNAME = "versions"
#: The file inside that folder that lists the versions.
INDEX_FILENAME = "index.json"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

def _library_dir() -> pathlib.Path:
    """Where the asset library lives (the same place the engine reads)."""
    from webapp.services import library
    return pathlib.Path(library.library_dir())


def _resolve_name(name: str) -> str:
    """The directory name the engine will actually use.

    It matches an existing folder without regard to case, so the
    versions of a script live in the same folder as the script itself.
    """
    base = _library_dir() / "storyboards"
    if base.is_dir():
        folded = str(name).casefold()
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.casefold() == folded:
                return entry.name
    return str(name)


def script_dir(name: str) -> pathlib.Path:
    """The folder that holds one script."""
    return _library_dir() / "storyboards" / name


def live_path(name: str) -> pathlib.Path:
    """The file the engine reads: the current version."""
    return script_dir(name) / "storyboard.json"


def versions_dir(name: str) -> pathlib.Path:
    """The folder that holds the version history of one script."""
    return script_dir(name) / VERSIONS_DIRNAME


def index_path(name: str) -> pathlib.Path:
    return versions_dir(name) / INDEX_FILENAME


def version_path(name: str, number: int) -> pathlib.Path:
    return versions_dir(name) / ("%04d.json" % int(number))


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def _write_json(path: pathlib.Path, value) -> None:
    """Write JSON the way the rest of the app does, atomically.

    A half-written index would lose the history, so the file is written
    to a temporary name and moved into place in one step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def fingerprint(board: dict) -> str:
    """A short hash of a whole board: "has this content changed?"."""
    blob = json.dumps(board, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime(path: pathlib.Path) -> str:
    """When a file was last written, in a form a person can read."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read_index(name: str) -> dict:
    """The version list of one script.

    A script that was never edited through the app has no index: it is
    still one version - the one on disk.
    """
    index = _read_json(index_path(name))
    if isinstance(index, dict) and isinstance(index.get("items"), list):
        return index
    live = live_path(name)
    if not live.is_file():
        return {"current": 0, "items": []}
    return {"current": 1, "items": [{
        "number": 1,
        "time": _mtime(live),
        "note": "（还没有历史）",
        "hash": "",
        "current": True,
    }]}


def list_versions(name: str) -> dict:
    """Every version of one script, newest first."""
    index = read_index(name)
    current = int(index.get("current") or 0)
    items = []
    for item in index.get("items") or []:
        number = int(item.get("number") or 0)
        items.append({
            "number": number,
            "time": item.get("time") or "",
            "note": item.get("note") or "",
            "hash": item.get("hash") or "",
            "current": number == current,
        })
    items.sort(key=lambda row: row["number"], reverse=True)
    return {"current": current, "items": items}


def read_version(name: str, number: int) -> dict:
    """One version's board, exactly as it was saved."""
    board = _read_json(version_path(name, number))
    if not isinstance(board, dict):
        # The current version may live only in the live file (a script
        # that was saved before the history existed).
        if int(number) == int(read_index(name).get("current") or 0):
            board = _read_json(live_path(name))
        if not isinstance(board, dict):
            raise FileNotFoundError("版本 %s 不在了。" % number)
    return board


def count_versions(name: str) -> int:
    """How many versions this script has (used in the delete warning)."""
    return len(list_versions(name)["items"])


def content_version(name: str) -> int:
    """Which version of this script is current (0 when there is none)."""
    return int(read_index(name).get("current") or 0)


# --------------------------------------------------------------------------- #
# the version numbers
# --------------------------------------------------------------------------- #

def _next_number(name: str, index: dict) -> int:
    """The next version number.

    It never reuses a number: not even one that was deleted, and not
    even one that is on the disk but missing from the index (a save
    that died halfway through).
    """
    highest = 0
    for item in index.get("items") or []:
        highest = max(highest, int(item.get("number") or 0))
    folder = versions_dir(name)
    if folder.is_dir():
        for entry in folder.glob("*.json"):
            if entry.name == INDEX_FILENAME:
                continue
            if entry.stem.isdigit():
                highest = max(highest, int(entry.stem))
    return highest + 1


# --------------------------------------------------------------------------- #
# saving
# --------------------------------------------------------------------------- #

def save_versioned(name: str, payload: dict, note: str = "") -> dict:
    """Save a board as a new version of a script.

    The order matters. The version file is written before the live
    file is replaced, so the copy that is being replaced is already on
    disk when the replacement happens; and the index is written last,
    so it only ever points at a version that really is the live one.
    Nothing that was ever saved can be lost by a crash in between.
    """
    cleaned = contract.validate_v2(payload)          # 不通过就什么都不写
    name = _resolve_name(cleaned.get("name") or name)
    target = live_path(name)
    if target.exists() and not target.is_file():
        # 那个名字被一个文件占着：让引擎去报这个错，别在这里建文件夹。
        raise ValueError("%s 已经被一个文件占着了，存不进去。" % name)
    existed = target.is_file()
    live = _read_json(target)
    index = read_index(name)

    # The very first save of a script that already exists on disk: its
    # content becomes version 1 before it is replaced.
    if not index_path(name).is_file() and isinstance(live, dict):
        _write_json(version_path(name, 1), live)
        index = {"current": 1, "items": [{
            "number": 1, "time": _mtime(live_path(name)),
            "note": "旧版本", "hash": fingerprint(live),
        }]}

    digest = fingerprint(cleaned)
    current = int(index.get("current") or 0)
    if isinstance(live, dict) and fingerprint(live) == digest:
        # Nothing changed: no new version, no new number.
        return {"name": cleaned.get("name", name), "path": str(live_path(name)),
                "status": "unchanged", "content_version": current,
                "changed": False, "json": cleaned}

    number = _next_number(name, index)
    _write_json(version_path(name, number), cleaned)

    # The live file is the engine's business: it validates and writes
    # it atomically, exactly where the engine expects to find it.
    # The status is worked out here: the version file above has already
    # created the folder, so the engine would call a brand new script
    # "overwritten".
    _, path, _engine_status = _script_asset.save_storyboard(_library_dir(), cleaned)
    status = "overwritten" if existed else "created"

    index.setdefault("items", []).append({
        "number": number, "time": _now(), "note": note or "保存",
        "hash": digest,
    })
    index["current"] = number
    _write_json(index_path(name), index)
    return {"name": cleaned.get("name", name), "path": path, "status": status,
            "content_version": number, "changed": True, "json": cleaned}


def restore_version(name: str, number: int) -> dict:
    """Make an old version the current one - as a NEW version.

    The history only grows: the copy that is current now is not
    touched, and what comes back is a new version on top of it.
    """
    board = read_version(name, number)
    result = save_versioned(name, board, note="恢复自版本 %s" % number)
    result["json"] = board
    return result


def delete_version(name: str, number: int) -> dict:
    """Throw an archived version away.

    The current version is not deletable this way: deleting it would
    mean deleting the script.
    """
    index = read_index(name)
    current = int(index.get("current") or 0)
    if int(number) == current:
        raise ValueError("当前版本不能这样删：删它等于删掉这份剧本。")
    path = version_path(name, number)
    if not path.is_file():
        raise FileNotFoundError("版本 %s 不在了。" % number)
    path.unlink()
    index["items"] = [item for item in index.get("items") or []
                       if int(item.get("number") or 0) != int(number)]
    _write_json(index_path(name), index)
    return {"ok": True, "content_version": current}
