"""Enable the short-drama web front end from inside ComfyUI.

The web app is part of this plugin (see the ``webapp/`` directory), so it
runs from the plugin's own directory and with the plugin's own
interpreter - ComfyUI's embedded python. One interpreter, one set of
packages, one asset library: the web app and the nodes read and write the
same files, with nothing to keep in step.

It is still a SEPARATE PROCESS (the plan's D2): the web app must not
share ComfyUI's event loop, because a slow page would then stall the
render queue. ComfyUI keeps port 8188; the web app takes 8200.

If the server is already up, the node just reports the URL.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

from .asset_root import find_asset_owner
from .fork_identity import display_name as _display_name

#: Where the web app listens. Kept in step with webapp.config.
DEFAULT_PORT = 8200
DEFAULT_HOST = "0.0.0.0"

#: How long to wait for the server to answer before giving up.
START_TIMEOUT_SECONDS = 40

#: The web app's own directory - the plugin root.
_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__)))


def settings_path() -> str:
    """Where the web app keeps its settings, jobs and snapshots.

    The launcher (ComfyUI-Mie-Package-Launcher) starts the very same web app
    as a companion service, and it points MIE_WEB_SETTINGS at
    ``<package root>/launcher/h3-engine/settings.json`` so the settings, the
    job list and the snapshots live with the launcher state and are **per
    environment**. This node has to agree with it, otherwise the user sees a
    different job list depending on who started the app.

    Falls back to ``<plugin root>/web_settings.json`` when the plugin does
    not sit in the usual ``<package>/ComfyUI/custom_nodes/<plugin>`` layout -
    a junction pointing somewhere else cannot be mapped back to a package
    root, and guessing one would scatter state over the disk.
    """
    try:
        custom_nodes = os.path.dirname(_PLUGIN_ROOT)
        if os.path.basename(custom_nodes) == "custom_nodes":
            pkg = os.path.dirname(os.path.dirname(custom_nodes))
            if os.path.isdir(os.path.join(pkg, "ComfyUI", "custom_nodes")):
                return os.path.join(pkg, "launcher", "h3-engine", "settings.json")
    except Exception:
        pass
    return os.path.join(_PLUGIN_ROOT, "web_settings.json")


def _probe(port: int, timeout: float = 1.5) -> dict:
    """Ask the web app who it is. An empty dict means it is not there."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except Exception:  # noqa: BLE001 - any failure means "not up"
        return {}


def _spawn(port: int) -> int:
    """Start the web app in its own process group.

    DETACHED_PROCESS on Windows so the server survives ComfyUI closing and
    never inherits the console - ComfyUI's own output stays clean.
    """
    env = dict(os.environ)
    env["MIE_WEB_PORT"] = str(port)
    env.setdefault("MIE_WEB_HOST", DEFAULT_HOST)
    # The web app's own settings live with the launcher state, not in the
    # user profile of whoever happens to be logged in - and the launcher
    # points at the same file (see settings_path).
    env.setdefault("MIE_WEB_SETTINGS", settings_path())
    owner = find_asset_owner(_PLUGIN_ROOT)
    env["MIE_PLUGIN_ROOT"] = str(owner)
    env["MIE_ASSET_LIBRARY"] = str(owner / "asset_library")
    env["MIE_LLM_KEYS_FILE"] = str(owner / "mie_llm_keys.json")

    kwargs = {"cwd": _PLUGIN_ROOT, "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True

    log = open(os.path.join(_PLUGIN_ROOT, "webui.log"), "ab")
    try:
        # The file, not "-m webapp": the embedded python runs in isolated
        # mode, where the package next to the script is not importable.
        # __main__.py puts it back on sys.path itself.
        process = subprocess.Popen(
            [sys.executable, os.path.join(_PLUGIN_ROOT, "webapp", "__main__.py")],
            stdout=log, stderr=subprocess.STDOUT, **kwargs)
    finally:
        log.close()
    return process.pid


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _identity_path(path) -> str:
    # Keep junction paths: two package environments can use the same checkout.
    return os.path.normcase(os.path.abspath(path))


def _pid_on_port(port: int):
    import psutil

    pids = {
        conn.pid for conn in psutil.net_connections(kind="tcp4")
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port
        and conn.laddr.ip in ("127.0.0.1", "0.0.0.0")
    }
    return next(iter(pids)) if len(pids) == 1 else None


def _owned_listener(port: int):
    try:
        import psutil
    except ImportError:
        return None
    try:
        pid = _pid_on_port(port)
        if not pid:
            return None
        process = psutil.Process(pid)
        health = _probe(port)
        if not isinstance(health, dict) or health.get("status") != "ok" \
                or health.get("app") != "comfyui-mie-web":
            return None
        owner = find_asset_owner(_PLUGIN_ROOT)
        settings = os.environ.get("MIE_WEB_SETTINGS") or settings_path()
        if not health.get("plugin_root") or (
                _identity_path(health["plugin_root"]) != _identity_path(owner)):
            return None
        if health.get("settings_file") and (
                _identity_path(health["settings_file"]) != _identity_path(settings)):
            return None
        if health.get("pid", pid) != pid:
            return None
        entry = _identity_path(os.path.join(_PLUGIN_ROOT, "webapp", "__main__.py"))
        args = process.cmdline()[1:]
        script = next((arg for arg in args if not arg.startswith("-")), "")
        if not script or _identity_path(script) != entry or not process.is_running():
            return None
        return process, health
    except (OSError, psutil.Error, TypeError, ValueError):
        return None


def _terminate(process) -> bool:
    import psutil

    try:
        process.terminate()
        process.wait(timeout=5)
        return True
    except psutil.NoSuchProcess:
        return True
    except (OSError, psutil.Error):
        return False


class MieH3StartWebUI:
    """Start the short-drama web front end (or report that it is up).

    The web app is a separate process on its own port - ComfyUI keeps
    8188, the web app takes 8200 - so a slow page never stalls a render.
    """

    CATEGORY = "MieNodes/web"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "port": ("INT", {"default": DEFAULT_PORT, "min": 1024, "max": 65535}),
            },
            "optional": {
                "restart": ("BOOLEAN", {"default": False,
                                        "tooltip": "已经在跑的时候也重启一次。"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("url", "status", "started")
    FUNCTION = "enable"
    OUTPUT_NODE = True

    def enable(self, port=DEFAULT_PORT, restart=False):
        url = "http://127.0.0.1:%d" % int(port)

        port = int(port)
        owned = _owned_listener(port)
        if owned:
            if not restart:
                return (url, "短剧工作台已经在跑：%s" % url, False)
            if owned[1].get("idle") is False:
                return (url, "短剧工作台正在渲染，无法重启。", False)
            message, stopped = MieH3StopWebUI().disable(port)
            if not stopped:
                return (url, message, False)
        if _port_listening(port):
            return (url, "端口 %d 已占用，无法确认属于当前 H3 工作台。" % port, False)

        try:
            pid = _spawn(int(port))
        except Exception as exc:  # noqa: BLE001 - the reason IS the answer
            message = "起不来：%s: %s" % (type(exc).__name__, exc)
            print(message)
            return (url, message, False)

        # Wait for it to answer, so the node's output is not a promise.
        deadline = time.time() + START_TIMEOUT_SECONDS
        while time.time() < deadline:
            listener = _owned_listener(port)
            if listener and listener[0].pid == pid:
                message = "短剧工作台已启动：%s（pid %d）" % (url, pid)
                print(message)
                return (url, message, True)
            time.sleep(0.5)

        message = ("等了 %d 秒它还没应答。看看插件目录下的 webui.log，"
                   "多半是 flask 没装上（插件的 requirements.txt 里声明了）。"
                   % START_TIMEOUT_SECONDS)
        print(message)
        return (url, message, False)


class MieH3WebUIStatus:
    """Is the short-drama web front end up? What is it serving?"""

    CATEGORY = "MieNodes/web"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "port": ("INT", {"default": DEFAULT_PORT, "min": 1024, "max": 65535}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("status", "online")
    FUNCTION = "check"
    OUTPUT_NODE = True

    def check(self, port=DEFAULT_PORT):
        port = int(port)
        owned = _owned_listener(port)
        if owned:
            detail = "在线 · 正在渲染" if owned[1].get("idle") is False else "在线"
            return (detail, True)
        if _port_listening(port):
            return ("端口 %d 已占用，无法确认属于当前 H3 工作台。" % port, False)
        return ("没在跑。跑一次「Start Short Drama Web UI」节点。", False)


class MieH3StopWebUI:
    """Stop the short-drama web front end.

    The web app outlives the node that started it - it is its own process,
    so ComfyUI closing does not close it. This is how you close it on
    purpose.
    """

    CATEGORY = "MieNodes/web"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "port": ("INT", {"default": DEFAULT_PORT, "min": 1024, "max": 65535}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("status", "stopped")
    FUNCTION = "disable"
    OUTPUT_NODE = True

    def disable(self, port=DEFAULT_PORT):
        port = int(port)
        owned = _owned_listener(port)
        if owned is None:
            if _port_listening(port):
                return ("端口 %d 已占用，无法确认属于当前 H3 工作台。" % port, False)
            return ("它本来就没在跑。", False)

        process, _ = owned
        if not _terminate(process):
            return ("无法停止 H3 工作台（pid %d）。" % process.pid, False)
        return ("短剧工作台已停止（pid %d）。" % process.pid, True)
