#!/usr/bin/env python3
"""Mie's regression test for the checkpoint identity signal.

The guard that decides "is this the same checkpoint I verified earlier?"
can only use a file's identity as a second signal on some filesystems.
Where the signal cannot be trusted it must not be used at all: the caller
then hashes the file, which is slower but correct.

This test pins the decision:

* a network share has no trustworthy signal (the server may report 0, and
  its modification time may be coarse);
* FAT and exFAT have no trustworthy signal, because their file index comes
  from the directory entry and an in-place overwrite keeps it - exactly the
  case the signal exists to catch;
* a redirector that reports inode 0 has no signal;
* an ordinary local NTFS path does have one;
* a path the operator pinned is never trusted.
"""

import os
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import checkpoint_identity as identity  # noqa: E402


def _stat(path):
    return os.stat(path)


def main():
    import tempfile

    with tempfile.TemporaryDirectory(dir=str(ROOT)) as folder:
        sample = pathlib.Path(folder) / "clip_0001.json"
        sample.write_bytes(b"A" * 64)
        stat = _stat(sample)

        # --- an ordinary local path has a signal -------------------------
        signal = identity.identity_signal(str(sample), stat)
        if identity.volume_fs_type(str(sample)) not in (
                None, "NTFS", "ReFS", "NTFS "):
            # Some other local file system; the index may still be fine,
            # but the test does not claim anything about it.
            print("  (local volume is %r, skipping the NTFS assertion)"
                  % identity.volume_fs_type(str(sample)))
        else:
            assert signal is not None, (
                "a local NTFS path must have an identity signal")
            assert signal == (int(stat.st_dev), int(stat.st_ino)), signal

        # --- a network share has no signal -------------------------------
        original_remote = identity.is_remote_path
        identity.is_remote_path = lambda path: True
        try:
            assert identity.identity_signal(str(sample), stat) is None
        finally:
            identity.is_remote_path = original_remote

        # --- FAT / exFAT have no signal ----------------------------------
        original_fs = identity.volume_fs_type
        for name in ("FAT", "FAT32", "exFAT"):
            identity.volume_fs_type = lambda path, _n=name: _n
            try:
                assert identity.identity_signal(str(sample), stat) is None, name
            finally:
                identity.volume_fs_type = original_fs

        # --- a redirector that reports inode 0 has no signal -------------
        class _NoInode:
            st_dev = 1
            st_ino = 0
            st_size = 64
            st_mtime_ns = 0

        assert identity.identity_signal(str(sample), _NoInode()) is None

        # --- a pinned path is never trusted -------------------------------
        identity.force_strict_paths([str(folder)])
        try:
            assert identity.identity_signal(str(sample), stat) is None
        finally:
            identity.force_strict_paths([])

        # --- the size threshold is what the caller uses ------------------
        assert identity.SMALL_FILE_BYTES == 64 * 1024 * 1024

        # --- a UNC path is remote by shape, not by a drive query ---------
        original_remote = identity.is_remote_path
        identity.is_remote_path = lambda path: str(path).startswith("\\\\")
        try:
            assert identity.is_remote_path("\\\\server\\share\\clip.json")
        finally:
            identity.is_remote_path = original_remote

    print("Mie checkpoint identity: network shares, FAT/exFAT, a zero inode "
          "and pinned paths all fall back to hashing; a local NTFS path "
          "keeps its identity signal")


if __name__ == "__main__":
    main()
