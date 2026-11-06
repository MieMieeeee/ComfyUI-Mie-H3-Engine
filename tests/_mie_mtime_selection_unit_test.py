#!/usr/bin/env python3
"""Mie's regression tests for the three "pick the newest by mtime" sites.

Three places in the upstream pack chose a file by its modification time:

  1. the legacy checkpoint-pointer restore fallback, which promoted the
     newest ``clip_NNNN.json.restore.<id>.tmp`` to the canonical pointer;
  2. the sealed-chapter manifest loader, which picked the newest
     ``<id>.json`` snapshot;
  3. the reference-cache lookup, which picked the newest cache descriptor.

A modification time is not a content identity: a restore, a robocopy
/COPYALL mirror, or a synced folder can preserve it, so an older file can
look the newest. Each test below pins the behaviour we changed:

  * site 1 refuses to choose between several candidates and keeps them;
  * site 2 orders by the ``sealed_at`` timestamp the manifest carries
    INSIDE its own content;
  * site 3 rejects a descriptor whose content no longer matches the
    signature in its own file name.

The upstream suite is the baseline; these tests are additive.
"""

import importlib.util
import os
import pathlib
import sys
import tempfile
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

folder_paths = types.ModuleType("folder_paths")
folder_paths.output_directory = str(ROOT)
folder_paths.get_output_directory = lambda: folder_paths.output_directory
folder_paths.get_temp_directory = lambda: folder_paths.output_directory
folder_paths.get_input_directory = lambda: folder_paths.output_directory
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

# Reuse the upstream chapter-delivery helpers: they build real segments and
# real manifests, and they load the same chain_nodes module we patch.
delivery = importlib.import_module("_chapter_delivery_unit_test")
chain = delivery.chain
# chain_nodes binds to the folder_paths stub the upstream bootstrap
# installed, so that is the one whose output directory we must move.
output_stub = chain.folder_paths
make_segment = delivery.make_segment
make_manifest = delivery.make_manifest


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_legacy_pointer_restore_refuses_to_guess():
    """Site 1: several interrupted legacy pointers, no way to tell which.

    The temporary name carries a random transaction id (uuid4), not a
    content digest, so nothing on disk says which one was meant to become
    the canonical pointer. The newest-mtime rule could promote a stale
    pointer, so the restore must refuse and keep every file.
    """
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as temporary:
        output_stub.output_directory = temporary
        run = "legacy_pointer_run"
        checkpoints = pathlib.Path(temporary) / "h3_chains" / run / "checkpoints"
        checkpoints.mkdir(parents=True, exist_ok=True)

        # One candidate: the ordinary legacy case must still be restored.
        single = _write(
            checkpoints / ("clip_0001.json.restore.%s.tmp" % ("a" * 32)),
            b"{\"single\": true}")
        single_bytes = single.read_bytes()
        chain._recover_checkpoint_pointer_transactions(run)
        canonical = checkpoints / "clip_0001.json"
        assert canonical.is_file(), "a single legacy pointer must be restored"
        assert canonical.read_bytes() == single_bytes
        os.remove(canonical)

        # Two candidates: the older one carries the newer mtime.
        older = _write(
            checkpoints / ("clip_0002.json.restore.%s.tmp" % ("b" * 32)),
            b"{\"older\": true}")
        newer = _write(
            checkpoints / ("clip_0002.json.restore.%s.tmp" % ("c" * 32)),
            b"{\"newer\": true}")
        now = os.stat(older).st_mtime_ns
        os.utime(newer, ns=(now - 10_000_000_000, now - 10_000_000_000))

        chain._recover_checkpoint_pointer_transactions(run)
        canonical = checkpoints / "clip_0002.json"
        assert not canonical.exists(), (
            "an ambiguous legacy pointer must not be promoted")
        assert older.is_file() and newer.is_file(), (
            "an ambiguous legacy pointer must not be deleted either")


def test_sealed_manifest_is_ordered_by_content():
    """Site 2: the sealed_at inside the manifest wins over the file mtime.

    ``sealed_at`` is excluded from the manifest digest (it is rewritten on
    every save), so a test may set it without invalidating the manifest.
    """
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as temporary:
        output_stub.output_directory = temporary
        output = pathlib.Path(temporary)
        segments = [make_segment(output, index) for index in range(1, 4)]
        manifest = make_manifest(segments, complete=False)
        # Two DIFFERENT chapter snapshots, so the loader really has two
        # candidates to choose between.
        other = dict(manifest)
        other["prompt_prefix"] = "a different shared prompt"

        first, first_path = chain._chapter_manifest_from_manifest(manifest, 1)
        second, second_path = chain._chapter_manifest_from_manifest(other, 1)
        assert first_path != second_path, (first_path, second_path)

        # The first snapshot is the newer one by content; give the OTHER file
        # the newer modification time, which is what a mirror or a restore
        # with preserved metadata does.
        documents = {}
        for snapshot, path in ((first, first_path), (second, second_path)):
            documents[path] = __import__("json").loads(
                pathlib.Path(path).read_text(encoding="utf-8"))
        older_path, newer_path = first_path, second_path
        older = documents[older_path]
        newer = documents[newer_path]
        older["sealed_at"] = "2020-01-01T00:00:00Z"
        newer["sealed_at"] = "2030-01-01T00:00:00Z"
        pathlib.Path(older_path).write_text(
            __import__("json").dumps(older), encoding="utf-8")
        pathlib.Path(newer_path).write_text(
            __import__("json").dumps(newer), encoding="utf-8")

        stamp = os.stat(older_path).st_mtime_ns
        os.utime(newer_path, ns=(stamp - 10_000_000_000,
                                 stamp - 10_000_000_000))

        loaded, loaded_path = chain._load_chapter_manifest(
            older.get("run_name"), 1)
        assert loaded["sealed_at"] == "2030-01-01T00:00:00Z", (
            "the snapshot with the newer sealed_at must win, not the file "
            "with the newer mtime: %r" % loaded.get("sealed_at"))
        assert loaded_path == newer_path


def test_reference_cache_rejects_a_swapped_descriptor():
    """Site 3: the descriptor's signature must match its file name.

    The file name carries ``signature[:24]``. If a restore or a mirror
    swaps a descriptor's bytes for another variant's, the name and the
    content disagree - and the lookup must not accept it.
    """
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as temporary:
        output_stub.output_directory = temporary
        fingerprint = "f" * 64
        scene, scene_count = 8, 13
        prompt = "@subject stands beside #setting."
        root = pathlib.Path(
            chain._reference_cache_paths(fingerprint, scene, "lookup")["root"])
        root.mkdir(parents=True, exist_ok=True)

        metadata = {
            "format": chain.REFERENCE_CACHE_FORMAT,
            "signature": "a" * 64,
            "reference_fingerprint": fingerprint,
            "scene": scene,
            "scene_count": scene_count,
            "prompt": prompt,
            "compiled_prompt": "compiled",
            "width": 32,
            "height": 32,
            "length": 5,
            "ref_image_size": "match",
            "presentation_contract": None,
        }
        # The name says "b"*24, the content says "a"*64: a swapped body.
        _write(root / ("scene_%04d.%s.json" % (scene, "b" * 24)),
               __import__("json").dumps(metadata).encode("utf-8"))

        candidates = chain._reference_cache_candidates(
            fingerprint, scene, scene_count, prompt, 32, 32, 5)
        assert not candidates, (
            "a descriptor whose signature does not match its file name must "
            "be rejected")

        # The matching one is accepted.
        metadata["signature"] = "c" * 64
        _write(root / ("scene_%04d.%s.json" % (scene, "c" * 24)),
               __import__("json").dumps(metadata).encode("utf-8"))
        candidates = chain._reference_cache_candidates(
            fingerprint, scene, scene_count, prompt, 32, 32, 5)
        assert len(candidates) == 1, candidates
        assert candidates[0][1]["signature"] == "c" * 64


def main():
    test_legacy_pointer_restore_refuses_to_guess()
    print("  legacy checkpoint-pointer restore: one candidate restored, "
          "several refused and kept")
    test_sealed_manifest_is_ordered_by_content()
    print("  sealed chapter manifest: ordered by the sealed_at inside the "
          "document")
    test_reference_cache_rejects_a_swapped_descriptor()
    print("  reference cache: a descriptor that disagrees with its file name "
          "is rejected")
    print("Mie mtime-selection guards: 3 checks passed")


if __name__ == "__main__":
    main()
