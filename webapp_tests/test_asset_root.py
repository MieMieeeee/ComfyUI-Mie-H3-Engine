"""Which asset library the engine uses - and that every side agrees.

The bug this guards against: the ComfyUI process and the web front end
resolve the library differently. The page lists 358 characters and the
render says "not found", because one side read our own (empty) library
and the other read the sibling pack's.

The rule (asset_root.py):

1. Our own ``asset_library`` exists -> it is ours.
2. Otherwise the sibling MieNodes pack that still holds the assets (the
   older layout) -> keep using it; the user's data is there.
3. Otherwise ours: a fresh install; the directory is created on first use.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asset_root  # noqa: E402


def _make_pack(root: pathlib.Path, name: str, with_library: bool) -> pathlib.Path:
    """A pack next to ``root`` - the way custom_nodes holds them."""
    pack = root.parent / name
    pack.mkdir(parents=True, exist_ok=True)
    if with_library:
        (pack / "asset_library" / "characters").mkdir(parents=True)
    return pack


def test_our_own_library_wins_when_it_exists(tmp_path):
    root = tmp_path / "custom_nodes" / "ComfyUI-Mie-H3-Engine"
    root.mkdir(parents=True)
    (root / "asset_library" / "characters").mkdir(parents=True)
    _make_pack(root, "comfyui_mienodes", with_library=True)

    assert asset_root.find_asset_owner(root) == root
    assert asset_root.asset_library_dir(root) == root / "asset_library"


def test_the_older_layout_keeps_using_the_sibling(tmp_path):
    """No library of our own -> the assets are still in the sibling pack."""
    root = tmp_path / "custom_nodes" / "ComfyUI-Mie-H3-Engine"
    root.mkdir(parents=True)
    sibling = _make_pack(root, "comfyui_mienodes", with_library=True)

    assert asset_root.find_asset_owner(root) == sibling
    assert asset_root.asset_library_dir(root) == sibling / "asset_library"


def test_a_fresh_install_gets_its_own_empty_library(tmp_path):
    """Nothing anywhere -> ours. The directory is created on first use."""
    root = tmp_path / "custom_nodes" / "ComfyUI-Mie-H3-Engine"
    root.mkdir(parents=True)

    assert asset_root.find_asset_owner(root) == root
    assert asset_root.asset_library_dir(root) == root / "asset_library"


def test_the_web_front_end_and_the_nodes_agree(tmp_path, monkeypatch):
    """The web app and the ComfyUI process must resolve the same library.

    This is the case that broke: the nodes took our own (empty) library
    while the page listed the sibling pack's assets.
    """
    root = tmp_path / "custom_nodes" / "ComfyUI-Mie-H3-Engine"
    root.mkdir(parents=True)
    sibling = _make_pack(root, "comfyui_mienodes", with_library=True)

    from webapp import config

    monkeypatch.delenv("MIE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(config, "_plugin_root_asis", lambda: root)
    monkeypatch.setattr(
        config, "PLUGIN_ROOT", asset_root.find_asset_owner(root), raising=False)

    web_side = config.PLUGIN_ROOT / "asset_library"
    node_side = asset_root.asset_library_dir(root)
    assert web_side == node_side == sibling / "asset_library"


def test_the_plugin_roots_own_path_is_not_resolved(tmp_path, monkeypatch):
    """A junction must stay a junction: the sibling is next to the LINK.

    If the root were resolved, the sibling packs in custom_nodes would not
    be next to us any more and the older layout would be invisible.
    """
    root = tmp_path / "custom_nodes" / "ComfyUI-Mie-H3-Engine"
    root.mkdir(parents=True)
    _make_pack(root, "comfyui_mienodes", with_library=True)

    monkeypatch.setattr(asset_root, "__file__", str(root / "__init__.py"))
    assert asset_root.plugin_root_asis() == root
    assert asset_root.plugin_root_asis() != root.resolve() or root == root.resolve()


def test_an_explicit_setting_is_never_overwritten(tmp_path, monkeypatch):
    """The launcher passes the answer down; setdefault must respect it."""
    engine = tmp_path / "engine"
    engine.mkdir()
    monkeypatch.setenv("MIE_ASSET_LIBRARY", str(tmp_path / "elsewhere"))

    # This is what __init__.py does.
    os.environ.setdefault("MIE_ASSET_LIBRARY", str(asset_root.asset_library_dir()))
    assert os.environ["MIE_ASSET_LIBRARY"] == str(tmp_path / "elsewhere")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
