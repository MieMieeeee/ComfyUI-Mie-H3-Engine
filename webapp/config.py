"""Paths and settings for the ComfyUI-Mie web front end.

This app is part of the H3 engine plugin, so it runs from the plugin's own
directory and with the plugin's own interpreter (ComfyUI's embedded
python). Everything it touches on disk is named here, so a deployment is a
matter of environment variables rather than code edits.
"""

from __future__ import annotations

import os
import pathlib

#: The web app's own root (the repository, when running from a checkout).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The package's own directory - where the templates, the static files,
#: the themes and the workflow preset live.
PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent


def _plugin_root_asis() -> pathlib.Path:
    """插件根目录, **不解析 junction**.

    整合包里 custom_nodes/ComfyUI-Mie-H3-Engine 常常是指向源码目录的 junction
    (实测: C:\\PP\\V9\\...\\custom_nodes\\ComfyUI-Mie-H3-Engine → F:\\ComfyUI-Mie-H3-Engine).
    pathlib.Path(...).resolve() 会把 junction 解析成**目标**, 于是:
      - 插件根变成 F:\\ComfyUI-Mie-H3-Engine → 旁边的 custom_nodes 兄弟目录
        (MieNodes 资产包) 就找不到了;
      - 从插件位置反推 ComfyUI 目录也失效 (parent.parent 变成盘根).
    所以凡是「靠目录位置判断布局」的地方, 一律用 abspath (不解析 reparse point).
    """
    return pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_asset_pack() -> pathlib.Path:
    """The pack that owns the asset library and the LLM keys.

    That is **this plugin** (2026-11-18): the H3 engine owns its own asset
    library - its characters, scenes and storyboards - and its own LLM keys
    file. It does not depend on the MieNodes pack for them.

    A launcher can point it elsewhere with MIE_PLUGIN_ROOT. Standalone runs
    fall back to our own directory, and - for the older layout where the
    library still lived in the sibling MieNodes pack - to that sibling.

    Note: there is deliberately **no hard-coded machine path** here any more.
    The old fallback pointed at one particular installation of this package:
    on any other machine it silently named a directory that does not exist,
    and where it did exist it was somebody else's asset library.
    """
    from_env = os.environ.get("MIE_PLUGIN_ROOT")
    if from_env:
        return pathlib.Path(from_env)

    # 不用 resolve: 整合包里这个插件常常是 junction, resolve 会把它变成源码目录,
    # 于是「同一个 custom_nodes 下的兄弟包」(老布局里的 MieNodes 资产包) 就找不到了.
    here = _plugin_root_asis()
    # Our own asset library (the current layout).
    if (here / "asset_library").is_dir():
        return here
    # The older layout: the library still lived in the sibling MieNodes pack.
    for name in ("comfyui_mienodes", "ComfyUI_MieNodes", "ComfyUI-MieNodes"):
        candidate = here.parent / name
        if (candidate / "asset_library").is_dir():
            return candidate
    # Neither yet: ours. The directory is created on first use.
    return here


#: The pack that owns the asset library and the LLM keys. The library lives
#: in <pack>/asset_library, and the keys file sits next to it.
PLUGIN_ROOT = _find_asset_pack()

#: The LLM platform keys. It lives in the pack, NOT next to the installed
#: ``mie_llm`` package: ``mie_llm.utils.load_plugin_config`` resolves a
#: bare file name against the package's parent directory, which for a
#: pip-installed package is site-packages. The web app therefore reads and
#: writes the file itself and hands the connectors an absolute path.
LLM_KEYS_FILE = pathlib.Path(
    os.environ.get("MIE_LLM_KEYS_FILE", str(PLUGIN_ROOT / "mie_llm_keys.json"))
)

#: The web app's own settings (theme choice, ComfyUI URL, ...).
SETTINGS_FILE = pathlib.Path(
    os.environ.get(
        "MIE_WEB_SETTINGS",
        str(pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home()))
            / "ComfyUI-Mie-Web" / "settings.json"),
    )
)

#: The submitted jobs, so a render that failed is still on the list
#: after the app restarts.
JOBS_FILE = SETTINGS_FILE.parent / "jobs.json"

#: The pinned copies of the scripts a queued render will read.
SNAPSHOT_DIR = SETTINGS_FILE.parent / "snapshots"

#: ComfyUI's address. The web app never starts ComfyUI itself; the
#: launcher does (see F:/Mie-Skills/AGENTS.md).
COMFYUI_URL = os.environ.get("MIE_COMFYUI_URL", "http://127.0.0.1:8188")

#: The LAN box. It is often off, so the app must treat it as optional.
LAN_COMFYUI_URL = os.environ.get("MIE_LAN_COMFYUI_URL", "http://192.168.31.150:8188")

def _default_comfyui_root() -> pathlib.Path:
    """ComfyUI 的目录 (工作台往那儿写上传素材、收集成片).

    插件装在 <ComfyUI>/custom_nodes/<插件> 时, 从自己的位置反推 —— 别再钉死
    某台机器的路径 (换台机器就指向一个不存在的目录, 而工作台会「安静地」收不到
    成片). 启动器总会显式注入 MIE_COMFYUI_ROOT, 这条只是独立运行时的兜底.
    """
    plugin_root = _plugin_root_asis()
    candidates = [
        plugin_root.parent.parent,        # <ComfyUI>/custom_nodes/<插件> → <ComfyUI>
        plugin_root.parent / "ComfyUI",   # 开发检出: 旁边就是 ComfyUI
        plugin_root.parent / "Comfyui",
    ]
    for cand in candidates:
        if (cand / "comfy").is_dir() or (cand / "main.py").is_file():
            return cand
    return candidates[0]


#: Where uploaded assets go. Same-machine deployment (the plan's D8): the
#: role portraits and voice samples land in ComfyUI's input/ directory.
COMFYUI_ROOT = pathlib.Path(
    os.environ.get("MIE_COMFYUI_ROOT", str(_default_comfyui_root()))
)

#: The web app listens here. ComfyUI keeps 8188; the plan's D2 says the
#: product layer picks a free port and binds 0.0.0.0 for the LAN.
WEB_HOST = os.environ.get("MIE_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("MIE_WEB_PORT", "8200"))

#: Never reload: a second process would fight over the render queue.
WEB_DEBUG = os.environ.get("MIE_WEB_DEBUG", "0") == "1"

#: The workflow the video page runs. It is the one the engine ships, and
#: it is the new-architecture one: the engine reads the script asset and
#: assembles the plan, and the chain runs the |MieH3 nodes.
DEFAULT_WORKFLOW = pathlib.Path(
    os.environ.get(
        "MIE_WEB_WORKFLOW",
        str(PACKAGE_ROOT / "data" / "mie_h3_storyboard_movie.api.json"),
    )
)

#: Where a finished render's files are collected from.
OUTPUT_SUBDIRS = ("live_storyboards", "h3_chains")


def ensure_dirs() -> None:
    """Create the directories the app writes into."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
