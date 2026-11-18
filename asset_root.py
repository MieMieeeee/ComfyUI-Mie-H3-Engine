"""Where the asset library lives - one rule, and every side follows it.

Three processes read the library: the ComfyUI process (the nodes), the web
front end (a separate Flask process) and the launcher (which starts that
process). When they resolve it differently, the workbench lists assets the
nodes cannot find - the page shows a character, the render says "not
found" - and the two sides quietly write to different libraries.

So the rule lives here, in a module with no dependencies (the ComfyUI
process imports it before anything heavy), and every side calls it.

The rule:

1. Our own ``asset_library`` directory exists -> it is ours. This pack
   owns its library (2026-11-18).
2. Otherwise the older layout: the library still lives in the sibling
   MieNodes pack next to us in ``custom_nodes``. Keep using it. The
   user's data is there (220MB on the reference machine) and moving it
   without saying so would look like losing it.
3. Otherwise ours: a fresh install. The directory is created on first use.

The launcher applies the same rule when it starts the web front end and
passes the answer down as ``MIE_PLUGIN_ROOT`` / ``MIE_ASSET_LIBRARY``.

Known limit: ``MIE_ASSET_LIBRARY`` is per process, not per pack. Once
this pack owns a library of its own (rule 1), the MieNodes pack's own
nodes - same ComfyUI process - would follow this variable too, because
``mie_craft`` reads the environment before the plugin root. Fixing that
means every pack passing its own root explicitly instead of relying on
the environment; it is worth doing when the library is actually moved.
"""

from __future__ import annotations

import os
import pathlib

#: The directory that holds the library inside the pack that owns it.
LIBRARY_DIRNAME = "asset_library"

#: The packs that may own the library in the older layout. They are
#: siblings of this plugin inside ComfyUI's ``custom_nodes`` directory.
SIBLING_PACK_NAMES = ("comfyui_mienodes", "ComfyUI_MieNodes", "ComfyUI-MieNodes")


def plugin_root_asis() -> pathlib.Path:
    """This plugin's root, **without resolving junctions**.

    In a package install ``custom_nodes/ComfyUI-Mie-H3-Engine`` is often a
    junction to the source checkout (measured:
    ``C:\\PP\\V9\\...\\custom_nodes\\ComfyUI-Mie-H3-Engine`` ->
    ``F:\\ComfyUI-Mie-H3-Engine``). ``Path.resolve()`` resolves the
    junction to its target, and then the sibling packs next to us in
    ``custom_nodes`` are no longer next to us at all.

    Everything that decides "which layout is this?" goes through
    ``abspath`` (which does not resolve reparse points), never
    ``resolve()``.
    """
    return pathlib.Path(os.path.dirname(os.path.abspath(__file__)))


def _sibling_candidates(root: pathlib.Path):
    """The packs that could own the library, next to us.

    Both the path as it is and the path with junctions resolved are
    tried: which one this process sees depends on how the plugin was
    imported, and the sibling must be found either way.
    """
    bases = [root.parent]
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    if resolved.parent != root.parent:
        bases.append(resolved.parent)
    for base in bases:
        for name in SIBLING_PACK_NAMES:
            yield base / name


def find_asset_owner(root=None) -> pathlib.Path:
    """The directory that owns the asset library - see the module docstring."""
    root = pathlib.Path(root) if root is not None else plugin_root_asis()
    if (root / LIBRARY_DIRNAME).is_dir():
        return root
    for candidate in _sibling_candidates(root):
        if (candidate / LIBRARY_DIRNAME).is_dir():
            return candidate
    return root


def asset_library_dir(root=None) -> pathlib.Path:
    """The asset library itself: ``<owner>/asset_library``."""
    return find_asset_owner(root) / LIBRARY_DIRNAME
