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
    # The pid goes next to the plugin so the stop node can find it again -
    # the web app outlives the node that started it, and ComfyUI has no
    # handle on it.
    try:
        with open(pid_file(), "w", encoding="utf-8") as handle:
            handle.write(str(process.pid))
    except OSError:
        pass
    return process.pid


def pid_file() -> str:
    """Where the running web app's pid is recorded."""
    return os.path.join(_PLUGIN_ROOT, "webui.pid")


def _pid_on_port(port: int):
    """The pid listening on a port, or None."""
    if sys.platform == "win32":
        try:
            # The bytes are decoded leniently on purpose: netstat prints
            # the service names in the system codepage, which is not
            # necessarily UTF-8, and a decode error would leave us with
            # no answer at all. The pid is ASCII, so a lossy decode is
            # fine.
            raw = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True,
                timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        out = raw.decode("utf-8", "replace")
        for line in out.splitlines():
            if ":%d " % port in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
        return None
    try:
        out = subprocess.run(["ss", "-lptn", "sport = :%d" % port],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for token in out.split("pid="):
        if "," in token:
            digits = token.split(",")[0]
            if digits.isdigit():
                return int(digits)
    return None


def _terminate(pid: int) -> bool:
    """Stop a process and everything it started."""
    if sys.platform == "win32":
        # Same reason as netstat: the output is in the system codepage.
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                capture_output=True, timeout=30)
        return result.returncode == 0
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


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
        if not _probe(port):
            message = "它本来就没在跑。"
            print(message)
            return (message, False)

        # The pid file first: it is the process this plugin started, so
        # nothing else can be caught by mistake.
        pid = None
        try:
            with open(pid_file(), encoding="utf-8") as handle:
                text = handle.read().strip()
            if text.isdigit():
                pid = int(text)
        except OSError:
            pid = None

        if pid is None:
            pid = _pid_on_port(port)

        stopped = False
        if pid is not None:
            stopped = _terminate(pid)
            if not stopped:
                # The pid file can be stale (the app was killed by hand).
                # Fall back to whoever holds the port.
                other = _pid_on_port(port)
                if other and other != pid:
                    stopped = _terminate(other)

        try:
            os.remove(pid_file())
        except OSError:
            pass

        # Give the socket a moment to come back.
        for _ in range(20):
            if not _probe(port):
                break
            time.sleep(0.25)

        if not _probe(port):
            message = "短剧工作台已停止（pid %s）。" % pid
            print(message)
            return (message, True)
        message = "停不掉：端口 %d 上还有东西（pid %s）。" % (port, pid)
        print(message)
        return (message, False)


NODE_CLASS_MAPPINGS = {
    "MieH3StartWebUI": MieH3StartWebUI,
    "MieH3StopWebUI": MieH3StopWebUI,
    "MieH3WebUIStatus": MieH3WebUIStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MieH3StartWebUI": _display_name("MieH3StartWebUI"),
    "MieH3StopWebUI": _display_name("MieH3StopWebUI"),
    "MieH3WebUIStatus": _display_name("MieH3WebUIStatus"),
}
