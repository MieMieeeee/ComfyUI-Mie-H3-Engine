"""The render progress, seen in a browser.

A fake ComfyUI - real HTTP, real websocket - publishes a realistic
event sequence. The workbench reads it over its own websocket, and the
page shows what it says: which shot, which sampler step, which node,
and that the run is over.

Nothing here touches a real ComfyUI or a real asset library.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBAPP_MAIN = PLUGIN_ROOT / "webapp" / "__main__.py"
PRESET = PLUGIN_ROOT / "webapp" / "data" / "mie_h3_storyboard_movie.api.json"
# NOT 8200, and not the other browser suite's port: this instance has a
# throwaway library AND a fake ComfyUI.
PORT = 8298
BASE = "http://127.0.0.1:%d" % PORT

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def png_bytes(width: int = 4, height: int = 4) -> bytes:
    from PIL import Image
    buf = __import__("io").BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _registry() -> dict:
    """A node registry shaped like the real one.

    Every node class the shipped workflow uses has to be in it: the
    workbench refuses a prompt whose nodes ComfyUI does not have.
    """
    graph = json.loads(PRESET.read_text(encoding="utf-8"))
    registry = {node["class_type"]: {"input": {"required": {}}}
                for node in graph.values()}
    registry["UNETLoader"] = {"input": {"required": {
        "unet_name": [["fake/h3.safetensors", "fake/h3b.safetensors"]],
        "weight_dtype": [["default"], {}]}}}
    registry["CLIPLoader"] = {"input": {"required": {
        "clip_name": [["fake/qwen3vl.safetensors"]],
        "type": [["minimax"], {}]}}}
    registry["VAELoader"] = {"input": {"required": {
        "vae_name": [["fake/video_vae.safetensors",
                      "fake/audio_vae.safetensors"]]}}}
    registry["ModelAttentionBackend"] = {"input": {"required": {
        "attention": [["pytorch attention", "comfy kitchen attention"], {}]}}}
    registry["MiniMaxH3ChainPlanModern|MieH3"] = {"input": {"required": {
        "base_seed": ["INT", {"default": 0, "min": 0,
                              "max": 18446744073709551615}]}}}
    return registry


class FakeComfy:
    """A ComfyUI that answers the calls the workbench makes.

    The interesting part is ``/ws``: it speaks the real websocket
    protocol and publishes the same events ComfyUI publishes.
    """

    def __init__(self, registry: dict):
        self.registry = registry
        self.prompt_id = ""
        self.runs = {}          # prompt_id -> {"sent": bool, "finished": bool}
        self.interrupted = False
        self.last_prompt = None      # the graph the workbench sent
        self.clients = []
        self.lock = threading.Lock()
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port = self.socket.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def stop(self) -> None:
        self.running = False
        try:
            self.socket.close()
        except OSError:
            pass

    # -- the socket ------------------------------------------------- #

    def _accept_loop(self) -> None:
        while self.running:
            try:
                conn, _addr = self.socket.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn) -> None:
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk
            head, _, rest = request.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            parts = (lines[0].split(" ") + ["", "", ""])
            method, path = parts[0], parts[1]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, _, value = line.partition(":")
                    headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length") or 0)
            body = rest
            while len(body) < length:
                body += conn.recv(65536)
            if path.startswith("/ws"):
                self._websocket(conn, headers)
                return
            self._http(conn, method, path, body[:length])
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _json(self, conn, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\n\r\n" % len(body) + body)

    def _http(self, conn, method: str, path: str, body: bytes) -> None:
        if path.startswith("/object_info"):
            return self._json(conn, self.registry)
        if path == "/prompt" and method == "POST":
            try:
                self.last_prompt = json.loads(body.decode("utf-8"))
            except ValueError:
                self.last_prompt = None
            with self.lock:
                self.prompt_id = "p-fake-%d" % (len(self.runs) + 1)
                self.runs[self.prompt_id] = {"sent": False, "finished": False}
                prompt_id = self.prompt_id
            return self._json(conn, {"prompt_id": prompt_id, "number": 1,
                                     "node_errors": {}})
        if path == "/system_stats":
            return self._json(conn, {
                "system": {"comfyui_version": "0.0.0-fake",
                           "python_version": "3.12 64bit",
                           "ram_total": 1, "ram_free": 1},
                "devices": [{"name": "cpu", "vram_total": 0, "vram_free": 0}]})
        if path == "/queue":
            with self.lock:
                run = self.runs.get(self.prompt_id) or {}
                running = [] if run.get("finished") else [[0, self.prompt_id]]
            return self._json(conn, {"queue_running": running,
                                     "queue_pending": []})
        if path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[-1]
            with self.lock:
                run = self.runs.get(prompt_id) or {}
                done = bool(run.get("finished"))
            if prompt_id == self.prompt_id and done:
                return self._json(conn, {prompt_id: {
                    "status": {"status_str": "success", "completed": True,
                               "messages": []},
                    "outputs": {}}})
            return self._json(conn, {})
        if path == "/interrupt":
            with self.lock:
                self.interrupted = True
            return self._json(conn, {})
        return self._json(conn, {})

    # -- the websocket ----------------------------------------------- #

    def _websocket(self, conn, headers) -> None:
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1(
            (key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n")
        with self.lock:
            self.clients.append(conn)
        try:
            with self.lock:
                prompt_id = self.prompt_id
                run = self.runs.setdefault(prompt_id, {"sent": False,
                                                       "finished": False})
                first = not run.get("sent")
                run["sent"] = True
            if first:
                for message in self._sequence(prompt_id):
                    conn.sendall(_frame(json.dumps(message).encode("utf-8")))
                    time.sleep(0.15)
                # The run is over: the terminal events go out last.
                time.sleep(1.2)
                with self.lock:
                    self.runs.setdefault(prompt_id, {})["finished"] = True
                conn.sendall(_frame(json.dumps({
                    "type": "executing",
                    "data": {"node": None, "prompt_id": prompt_id}}
                ).encode("utf-8")))
                conn.sendall(_frame(json.dumps({
                    "type": "execution_success",
                    "data": {"prompt_id": prompt_id}}
                ).encode("utf-8")))
            time.sleep(5.0)
        except OSError:
            pass

    def _sequence(self, prompt_id: str) -> list:
        """The events ComfyUI publishes while a render runs."""
        return [
            {"type": "status", "data": {"status": {"queue_remaining": 0}}},
            {"type": "executing", "data": {"node": "13", "display_node": "13",
                                           "prompt_id": prompt_id}},
            {"type": "progress", "data": {"value": 4, "max": 20,
                                          "node": "13", "prompt_id": prompt_id}},
            {"type": "progress", "data": {"value": 11, "max": 20,
                                          "node": "13", "prompt_id": prompt_id}},
            {"type": "progress_state", "data": {
                "prompt_id": prompt_id,
                "nodes": {"13": {"value": 11, "max": 20, "state": "running",
                                 "node_id": "13"}}}},
            {"type": "executing", "data": {"node": "25", "display_node": "25",
                                           "prompt_id": prompt_id}},
            {"type": "executed", "data": {"node": "25", "output": {},
                                          "prompt_id": prompt_id}},
        ]


def _frame(payload: bytes) -> bytes:
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", length))
    return bytes(header) + payload


def _wait_for(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    """A workbench on a throwaway library, pointed at a fake ComfyUI."""
    root = tmp_path_factory.mktemp("plugin")
    (root / "asset_library" / "characters").mkdir(parents=True)
    (root / "asset_library" / "scenes").mkdir(parents=True)
    (root / "asset_library" / "storyboards").mkdir(parents=True)
    comfy = FakeComfy(_registry())
    env = dict(os.environ)
    env["MIE_PLUGIN_ROOT"] = str(root)
    env["MIE_WEB_SETTINGS"] = str(root / "settings.json")
    env["MIE_COMFYUI_ROOT"] = str(root / "comfyui")
    env["MIE_COMFYUI_URL"] = comfy.url
    env["MIE_WEB_PORT"] = str(PORT)
    env["MIE_WEB_HOST"] = "127.0.0.1"
    process = subprocess.Popen(
        [sys.executable, str(WEBAPP_MAIN)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_for(BASE + "/api/health"):
        process.kill()
        comfy.stop()
        pytest.skip("the web app did not come up")
    yield root, comfy
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    comfy.stop()


@pytest.fixture()
def page(web):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        screen = browser.new_page(viewport={"width": 1500, "height": 1000})
        screen.on("dialog", lambda dialog: dialog.accept())
        yield screen
        browser.close()


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def _seed_a_render() -> str:
    """A character, a script, and a submitted render."""
    from test_library import SCRIPT

    boundary = "----mie"

    def field(name: str, value: str) -> bytes:
        return ("--%s\r\n"
                'Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (boundary, name, value)).encode("utf-8")

    body = b"".join([
        field("name", "甲"),
        field("description", "一个角色"),
        field("voice", "male voice"),
        ("--%s\r\n"
         'Content-Disposition: form-data; name="image"; filename="p.png"\r\n'
         "Content-Type: image/png\r\n\r\n" % boundary).encode("utf-8"),
        png_bytes(),
        ("\r\n--%s--\r\n" % boundary).encode("utf-8"),
    ])
    request = urllib.request.Request(
        BASE + "/api/assets/character", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    urllib.request.urlopen(request, timeout=30).read()
    _post("/api/scripts", {"json": json.dumps(SCRIPT)})
    record = _post("/api/render/submit", {
        "script": "测试剧本", "role_assets": {"character_1": "甲"}})
    return record["job_id"]



def test_the_page_shows_the_progress_comfyui_publishes(page, web):
    """The sampler steps and the active node reach the page."""
    _root, comfy = web
    job_id = _seed_a_render()
    assert job_id == comfy.prompt_id
    page.goto(BASE + "/history", wait_until="load")
    badge = page.locator("#render-progress")
    badge.wait_for(state="visible", timeout=15000)
    # The sampler steps: the page says which step of how many.
    page.wait_for_function(
        "() => { const el = document.getElementById('render-progress');"
        " return el && el.textContent.includes('11/20'); }", timeout=15000)
    text = badge.inner_text()
    assert "11/20" in text, text
    assert "采样" in text, text
    # The node that is running is named.
    assert "采样" in text or "视频生成" in text, text


def test_the_top_status_area_shows_the_progress_too(page, web):
    page.goto(BASE + "/history", wait_until="load")
    _seed_a_render()
    top = page.locator("#top-progress")
    top.wait_for(state="visible", timeout=15000)
    assert top.inner_text().strip(), "the top area must say something"


def test_the_page_says_when_the_render_is_over(page, web):
    """The terminal event is the end of the story: the page says so."""
    _root, comfy = web
    _seed_a_render()
    page.goto(BASE + "/history", wait_until="load")
    page.wait_for_function(
        "() => { const el = document.getElementById('render-progress');"
        " return el && el.textContent.includes('渲染完成'); }", timeout=20000)


def test_a_page_that_opens_after_the_end_still_says_how_it_ended(page, web):
    """A page that arrives too late gets no event stream - there is
    nothing live to watch - but the ending is still true, and the page
    has to be able to say it."""
    _root, comfy = web
    _seed_a_render()
    # The render ends while this page is watching.
    page.goto(BASE + "/history", wait_until="load")
    page.wait_for_function(
        "() => { const el = document.getElementById('render-progress');"
        " return el && el.textContent.includes('渲染完成'); }", timeout=20000)
    # Now a page that arrives after the fact, in a tab of its own.
    late = page.context.browser.new_page(viewport={"width": 1500, "height": 1000})
    try:
        late.goto(BASE + "/history", wait_until="load")
        late.wait_for_function(
            "() => { const el = document.getElementById('render-progress');"
            " return el && el.textContent.includes('渲染完成'); }",
            timeout=20000)
    finally:
        late.close()


def test_the_task_list_still_polls_and_finishes(page, web):
    """The progress stream does not replace the history polling: the
    task still ends up "完成"."""
    _root, comfy = web
    _seed_a_render()
    page.goto(BASE + "/history", wait_until="load")
    page.wait_for_function(
        "() => document.body.innerText.includes('完成')", timeout=20000)


def test_the_stop_button_still_reaches_comfyui(page, web):
    """The targeted stop is not lost: the button still asks ComfyUI to
    stop."""
    _root, comfy = web
    _seed_a_render()
    page.goto(BASE + "/history", wait_until="load")
    page.wait_for_timeout(1200)
    stop = page.get_by_role("button", name="⏹ 停止当前渲染")
    if stop.count():
        stop.first.click()
        page.wait_for_timeout(1500)
    assert comfy.interrupted, "the stop request must reach ComfyUI"


def test_the_video_page_lists_the_models_this_comfyui_has(page, web):
    """The model list comes from ComfyUI, so it is what this machine
    really has."""
    page.goto(BASE + "/video", wait_until="load")
    page.wait_for_timeout(2000)
    page.locator("details", has_text="渲染设置").locator("summary").first.click()
    page.wait_for_timeout(800)
    unet = page.locator("label.field", has_text="扩散模型").locator("select")
    assert unet.count() == 1, "the UNET picker must be on the page"
    values = unet.locator("option").all()
    names = [option.get_attribute("value") for option in values]
    assert "fake/h3.safetensors" in names, names


def test_the_video_page_carries_the_chosen_model_and_seed(page, web):
    """What the author picked really travels with the request."""
    _root, comfy = web
    _seed_a_render()
    seen = []

    def grab(route):
        try:
            seen.append(json.loads(route.request.post_data or "{}"))
        except ValueError:
            pass
        route.continue_()

    page.route("**/api/scripts/*/plan", grab)
    page.goto(BASE + "/video?script=" +
              urllib.parse.quote("测试剧本"), wait_until="load")
    page.wait_for_timeout(2500)
    # 接上资产，预览才会跑。
    role_select = page.locator(".roles-block select").first
    role_select.select_option("甲")
    page.wait_for_timeout(2000)
    page.locator("details", has_text="渲染设置").locator("summary").first.click()
    page.wait_for_timeout(800)
    # 一个 double 装不下的种子，写成文本。
    seed = page.locator("label.field", has_text="基础种子").locator("input")
    seed.fill("18446744073709551615")
    seed.dispatch_event("change")
    page.wait_for_timeout(500)
    unet = page.locator("label.field", has_text="扩散模型").locator("select")
    unet.select_option("fake/h3b.safetensors")
    page.wait_for_timeout(2500)
    assert seen, "the preview must have been asked"
    settings = seen[-1].get("settings") or {}
    assert settings.get("unet") == "fake/h3b.safetensors", settings
    assert settings.get("base_seed") == "18446744073709551615", settings


def test_the_pages_raise_no_client_errors(page, web):
    """A new page must not throw in the browser: an Alpine expression
    that is not valid breaks the whole update, not just one line."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text)
            if message.type == "error" else None)
    for path in ("/history", "/video", "/assets", "/scripts", "/llm"):
        page.goto(BASE + path, wait_until="load")
        page.wait_for_timeout(1200)
    assert not errors, errors
