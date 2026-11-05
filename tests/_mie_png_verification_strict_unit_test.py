#!/usr/bin/env python3
"""Mie's regression test for the PNG export checkpoint verification guard.

Why this file exists
--------------------
Upstream's cached mode treats a ``(size, mtime_ns)`` pair as the *content
identity* of a path. Once a checkpoint has been hashed and recorded, a later
export trusts the recorded hash and never re-hashes the file. An in-place
overwrite that preserves both size and modification time is therefore
invisible -- and a restore, a ``robocopy /COPYALL`` mirror, or a synced
folder can produce exactly that.

This test pins three facts:

1. the cached mode misses a same-stat byte edit (the upstream behaviour);
2. the strict mode catches it;
3. the export node's widget default is ``strict``, so the safe mode is what a
   user gets without touching the widget at all.

Point 3 is the fix we ship (PLAN.md 8.4 -- "P0-a"); points 1 and 2 are the
evidence that the fix means something.
"""

import hashlib
import importlib.util
import os
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "mie_png_verification_strict"

folder_paths = types.ModuleType("folder_paths")
folder_paths.output_directory = str(ROOT)
folder_paths.get_output_directory = lambda: folder_paths.output_directory
folder_paths.get_temp_directory = lambda: folder_paths.output_directory
folder_paths.get_input_directory = lambda: folder_paths.output_directory
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test"
shared_nodes._prepare_native_guide_conditioning = lambda *args: None
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


def digest(path):
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def overwrite_in_place_keeping_stat(path, data):
    """Replace the bytes in place, then restore the original mtime.

    This is what a restore, a robocopy /COPYALL mirror, or a synced folder
    does: size stays the same, and the modification time is preserved (or
    lands in the same tick). The file id changes only for a replace-style
    write, so it is deliberately not touched here -- the guard is not
    supposed to rely on it.
    """
    stat = os.stat(path)
    with open(path, "r+b") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def main():
    import tempfile

    # The temp folder must live under the output root: the guard builds its
    # cache key with os.path.relpath against that root, which cannot cross
    # drive letters on Windows.
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as folder:
        checkpoint = pathlib.Path(folder) / "clip_0001.json"
        original = b"A" * 64
        edited = b"B" * 64                      # same length on purpose
        checkpoint.write_bytes(original)
        recorded = digest(checkpoint)

        # --- 1. the first verification always hashes the file -------------
        cache = {"format": chain.PNG_EXPORT_HASH_CACHE_FORMAT, "entries": {}}
        result, changed = chain._verify_png_export_checkpoint(
            str(checkpoint), recorded, "strict", cache)
        assert result == "verified SHA-256" and changed, (result, changed)
        entry = cache["entries"][chain._png_export_checkpoint_cache_key(
            str(checkpoint))]
        assert entry["sha256"] == recorded, entry
        assert entry["size"] == len(original), entry

        # --- 2. the bytes change, size and mtime do not --------------------
        overwrite_in_place_keeping_stat(checkpoint, edited)
        stat = os.stat(checkpoint)
        assert stat.st_size == entry["size"], (stat.st_size, entry["size"])
        assert stat.st_mtime_ns == entry["mtime_ns"], (
            stat.st_mtime_ns, entry["mtime_ns"])

        # cached mode: the guard trusts the recorded (size, mtime) pair and
        # never re-hashes, so the edit passes unnoticed.
        result, changed = chain._verify_png_export_checkpoint(
            str(checkpoint), recorded, "cached", cache)
        assert result == "cached SHA-256" and not changed, (result, changed)

        # strict mode: the cache is not consulted, the file is re-hashed, and
        # the edit is rejected.
        try:
            chain._verify_png_export_checkpoint(
                str(checkpoint), recorded, "strict", cache)
        except ValueError as error:
            assert "SHA-256 integrity check" in str(error), str(error)
        else:
            raise AssertionError(
                "strict mode accepted a checkpoint whose bytes changed "
                "without changing size or modification time")

    # --- 3. the shipped default is the safe mode --------------------------
    node = chain.MiniMaxH3ChainExportPNG()
    inputs = node.INPUT_TYPES()
    widget = inputs["optional"]["checkpoint_verification"]
    options, config = widget[0], widget[1]
    assert options == ["cached", "strict"], options
    assert config["default"] == "strict", config["default"]
    assert "Strict (default)" in config["tooltip"], config["tooltip"]

    print(
        "Mie PNG export verification: the same-stat byte edit is invisible to "
        "cached mode and rejected by strict mode; the export node defaults to "
        "strict.")


if __name__ == "__main__":
    main()
