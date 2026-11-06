"""Decide whether a file's identity can be trusted - and how heavy the check should be.

Why this module exists
----------------------
The PNG export guard decides "is this the same checkpoint I verified earlier?"
by comparing a size and a modification time. Both are metadata, not content,
and both can be preserved by something that is not this program: a restore,
a ``robocopy /COPYALL`` mirror, a synced folder. When that happens the
guard accepts a file whose bytes changed.

Two things make the file ID useful as a second signal - and two things make
it useless:

* **NTFS** replaces a file by writing a new MFT record, so the file index
  changes. An overwrite-in-place keeps the record. The index is therefore
  a good "this file was replaced" signal, and a poor "these bytes changed"
  signal.
* **FAT and exFAT** derive the file index from the DIRECTORY ENTRY, so an
  in-place overwrite does not change it. On a USB stick or an SD card the
  index is worthless for exactly the case we care about.
* **SMB / UNC** may report 0, or a server-specific value.

So the rule here is deliberately conservative: **if the identity signal
cannot be trusted, it is not used at all.** The caller then falls back to
hashing the file, which is slower but correct.

Size layering
-------------
Hashing is cheap for small files and expensive for large ones. A PNG export
checkpoint that is a few megabytes can be hashed every time for a cost
nobody notices, so the identity cache only earns its keep on the big
files. Files below :data:`SMALL_FILE_BYTES` are therefore always hashed.
"""

from __future__ import annotations

import os


#: Below this size a file is always hashed. Hashing a few megabytes costs
#: milliseconds; the identity cache only pays for itself on large files.
SMALL_FILE_BYTES = 64 * 1024 * 1024

#: Volume file systems whose file index does not survive an in-place
#: overwrite, so it cannot be used as a content identity.
# Stored upper case: the volume reports "exFAT", and the comparison
# upper-cases what it gets.
UNTRUSTWORTH_INDEX_FILESYSTEMS = frozenset({"FAT", "FAT32", "EXFAT"})

DRIVE_REMOTE = 4

#: Path prefixes that are always verified by hash, whatever the volume
#: says. Set by :func:`force_strict_paths`; also readable from the
#: environment so an operator can pin a directory without a code change.
_STRICT_PATHS: list[str] = []


def force_strict_paths(paths) -> None:
    """Pin directories whose checkpoints must always be hashed.

    The comparison is on the resolved path prefix, so a directory can be
    pinned without naming every file under it.
    """
    global _STRICT_PATHS
    _STRICT_PATHS = [str(item) for item in (paths or ())]


def strict_paths() -> list[str]:
    """The pinned directories, plus anything the environment adds."""
    extra = os.environ.get("H3_STRICT_CHECKPOINT_PATHS", "")
    from_env = [item.strip() for item in extra.split(os.pathsep) if item.strip()]
    return list(_STRICT_PATHS) + from_env


def volume_fs_type(path: str) -> str | None:
    """Return the file system type of the volume holding ``path``.

    ``None`` when it cannot be determined - which the caller treats as
    "not trustworthy", never as "fine".
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        root = os.path.splitdrive(os.path.abspath(path))[0] + os.sep
        if not root:
            return None
        name = ctypes.create_unicode_buffer(64)
        fs = ctypes.create_unicode_buffer(64)
        serial = ctypes.c_uint()
        maximum = ctypes.c_uint()
        flags = ctypes.c_uint()
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name, 64, ctypes.byref(serial),
            ctypes.byref(maximum), ctypes.byref(flags), fs, 64)
        return fs.value if ok else None
    except (OSError, AttributeError, ImportError, ValueError):
        return None


def is_remote_path(path: str) -> bool:
    """True when the path lives on a network share.

    Two shapes count: a UNC path (``\\\\server\\share``) and a mapped
    drive. A mapped drive is the one that hides the share - ``Z:\\`` looks
    local but its file index comes from the server.
    """
    text = str(path or "")
    if text.startswith("\\\\"):
        return True
    if os.name != "nt":
        return False
    try:
        import ctypes

        root = os.path.splitdrive(os.path.abspath(text))[0] + os.sep
        if not root:
            return False
        return ctypes.windll.kernel32.GetDriveTypeW(
            ctypes.c_wchar_p(root)) == DRIVE_REMOTE
    except (OSError, AttributeError, ImportError, ValueError):
        return False


def is_pinned_strict(path: str) -> bool:
    """True when the operator pinned this path for always-strict checking."""
    try:
        resolved = os.path.realpath(os.path.abspath(str(path or "")))
    except (OSError, ValueError):
        return False
    for pinned in strict_paths():
        try:
            root = os.path.realpath(os.path.abspath(pinned))
        except (OSError, ValueError):
            continue
        if resolved == root or resolved.startswith(root + os.sep):
            return True
    return False


def identity_signal(path: str, stat_result: os.stat_result) -> tuple[int, int] | None:
    """Return the (device, inode) pair if it can be trusted as an identity.

    ``None`` means "no trustworthy signal": the caller must hash the file
    rather than trust a cached verdict.

    The file index is only a signal for the replacement case - it changes
    when the file is replaced and stays the same when it is overwritten in
    place. It is therefore used to REJECT a cached verdict, never to
    establish one.
    """
    if is_pinned_strict(path):
        return None
    if is_remote_path(path):
        # The index may be 0, or a server-specific value, and the server
        # may also report a coarse modification time.
        return None
    fs_type = volume_fs_type(path)
    if fs_type is not None and fs_type.upper() in UNTRUSTWORTH_INDEX_FILESYSTEMS:
        # The index comes from the directory entry, so an in-place
        # overwrite keeps it - exactly the case we need to catch.
        return None
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    if not inode:
        # Some redirectors report 0.
        return None
    return int(getattr(stat_result, "st_dev", 0) or 0), inode


def file_digest(path: str) -> str | None:
    """The SHA-256 of a file, or None when it cannot be read."""
    import hashlib

    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def bytes_digest(data: bytes) -> str:
    """The SHA-256 of a byte string."""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def publish_once(destination: str, write, digest: str, *, exempt: bool = False) -> None:
    """Perform a write that may only ever happen once.

    A published revision's artifacts are named after the revision, so their
    content is fixed the moment they are written. Writing the same bytes
    again is allowed - the flow is idempotent, and a re-run of the same
    revision must not fail - but different bytes under the same name mean
    something outside this program changed them, which is exactly what the
    metadata-based identity checks cannot see.

    The restore path is exempt: restoring an interrupted pointer is by
    definition a replacement of an existing file, and it selects its
    source by content rather than by timestamp.

    The check and the write are not one atomic step; the window between
    them is tiny and its only consequence is a lost update, not a
    corrupted file, because the write itself is atomic.
    """
    if exempt:
        write()
        return
    existing = file_digest(destination)
    if existing is not None and existing != digest:
        raise ValueError(
            "H3 refused to replace an already published artifact with "
            "different bytes: %s. A published revision's content is fixed; "
            "something outside this program changed it." % destination)
    write()
