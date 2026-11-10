"""A queued render must render what was submitted.

The engine's loader nodes read the library when they RUN, not when the
prompt is queued. A script edited while its render waits would
otherwise change under the render - and the roles could change order
with it.
"""

from __future__ import annotations

import json

import pytest

from webapp import config
from webapp.services import snapshot, workflow

from test_library import SCRIPT


@pytest.fixture()
def snapshot_dir(plugin_root, tmp_path, monkeypatch):
    """The snapshot folder lives in the temp dir with the rest."""
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    return config.SNAPSHOT_DIR


def test_the_snapshot_is_a_library_the_loader_can_read(snapshot_dir):
    """The layout is the library's own, because that is what the loader
    resolves ``library_dir`` against."""
    import os
    root = snapshot.write_script_snapshot("sb_run", "测试剧本", SCRIPT)
    path = (snapshot_dir / os.path.basename(root) / "storyboards"
            / "测试剧本" / "storyboard.json")
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "测试剧本"
    # The loader reads it back through the library's own reader.
    from mie_craft.assets import script_asset
    cleaned, _path = script_asset.load_storyboard(root, "测试剧本")
    assert cleaned["source_script"] == SCRIPT["source_script"]
    assert len(cleaned["roles"]) == len(SCRIPT["roles"])


def test_the_snapshot_keeps_the_version_that_was_submitted(snapshot_dir):
    """Editing the script afterwards must not change the pinned copy."""
    root = snapshot.write_script_snapshot("sb_run", "测试剧本", SCRIPT)
    edited = json.loads(json.dumps(SCRIPT))
    edited["source_script"] = "甲：改过了"
    edited["shots"][0]["events"][0]["text"] = "改过了"

    from mie_craft.assets import script_asset
    cleaned, _path = script_asset.load_storyboard(root, "测试剧本")
    assert cleaned["source_script"] == SCRIPT["source_script"]
    assert cleaned["shots"][0]["events"][0]["text"] == "你好"


def test_the_graph_points_the_loader_at_the_snapshot(snapshot_dir):
    root = snapshot.write_script_snapshot("sb_run", "测试剧本", SCRIPT)
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"},
                           run_name="sb_run", library_dir=root)
    loader = graph["900"]
    assert loader["class_type"] == "MieH3LoadScriptAsset"
    assert loader["inputs"]["library_dir"] == root
    assert loader["inputs"]["name"] == "测试剧本"


def test_a_changed_asset_is_detected_by_its_hash(plugin_root, monkeypatch):
    """The character loader ignores ``library_dir``, so the assets cannot
    be pinned - their content hash is what tells us they changed."""
    marks = {"character_1": "hash-at-submit"}
    monkeypatch.setattr(snapshot, "asset_fingerprint",
                        lambda kind, name: "hash-later")
    changed = snapshot.changed_assets("character", {"character_1": "甲"}, marks)
    assert changed == ["甲"]

    monkeypatch.setattr(snapshot, "asset_fingerprint",
                        lambda kind, name: "hash-at-submit")
    assert snapshot.changed_assets("character", {"character_1": "甲"}, marks) == []


def test_an_asset_without_a_recorded_hash_is_not_reported(plugin_root, monkeypatch):
    """No hash recorded means no verdict, not a false alarm."""
    monkeypatch.setattr(snapshot, "asset_fingerprint", lambda kind, name: "x")
    assert snapshot.changed_assets("character", {"character_1": "甲"}, {}) == []


def test_the_old_snapshots_are_pruned(snapshot_dir):
    import os
    import time
    for index in range(snapshot.KEEP + 3):
        run = snapshot_dir / ("run_%02d" % index)
        (run / "storyboards" / "x").mkdir(parents=True)
        # Older runs first, so the pruning can tell them apart.
        os.utime(run, (time.time() - 1000 + index, time.time() - 1000 + index))
    snapshot.prune()
    left = sorted(p.name for p in snapshot_dir.iterdir())
    assert len(left) == snapshot.KEEP
    assert "run_%02d" % (snapshot.KEEP + 2) in left
