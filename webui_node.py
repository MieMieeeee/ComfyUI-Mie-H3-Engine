"""Enable the short-drama web front end from inside ComfyUI.

The web app is part of this plugin (see the ``webapp/`` directory), so it
runs from the plugin's own directory and with the plugin's own
interpreter - ComfyUI's embedded python. One interpreter, one set of
packages, one asset library: the web app and the nodes read and write the
same files, with nothing to keep in step.

It is still a SEPARATE PROCESS (the plan's D2): the web app must not
share ComfyUI's event loop, because a slow page would then stall the
render queue. ComfyUI keeps port 8188; the web app takes 8199.

If the server is already up, the node just reports the URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

from .fork_identity import display_name as _display_name

#: Where the web app listens. Kept in step with webapp.config.
DEFAULT_PORT = 8199
DEFAULT_HOST = "0.0.0.0"

#: How long to wait for the server to answer before giving up.
START_TIMEOUT_SECONDS = 40

#: The web app's own directory - the plugin root.
_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__)))


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
    env.setdefault("MIE_WEB_PORT", str(port))
    env.setdefault("MIE_WEB_HOST", DEFAULT_HOST)
    # The web app's own settings live beside the plugin, not in the user
    # profile of whoever happens to be logged in.
    env.setdefault("MIE_WEB_SETTINGS",
                   os.path.join(_PLUGIN_ROOT, "web_settings.json"))

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


class MieH3StartWebUI:
    """Start the short-drama web front end (or report that it is up).

    The web app is a separate process on its own port - ComfyUI keeps
    8188, the web app takes 8199 - so a slow page never stalls a render.
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

        if not restart:
            health = _probe(int(port))
            if health:
                message = "短剧工作台已经在跑：%s" % url
                print(message)
                return (url, message, False)

        try:
            pid = _spawn(int(port))
        except Exception as exc:  # noqa: BLE001 - the reason IS the answer
            message = "起不来：%s: %s" % (type(exc).__name__, exc)
            print(message)
            return (url, message, False)

        # Wait for it to answer, so the node's output is not a promise.
        deadline = time.time() + START_TIMEOUT_SECONDS
        while time.time() < deadline:
            if _probe(int(port)):
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
        health = _probe(int(port))
        if health:
            return ("在线：%s" % health.get("status", "ok"), True)
        return ("没在跑。跑一次「Start Short Drama Web UI」节点。", False)


NODE_CLASS_MAPPINGS = {
    "MieH3StartWebUI": MieH3StartWebUI,
    "MieH3WebUIStatus": MieH3WebUIStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MieH3StartWebUI": _display_name("MieH3StartWebUI"),
    "MieH3WebUIStatus": _display_name("MieH3WebUIStatus"),
}
