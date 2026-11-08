"""End to end: the nodes start the web app, the pages manage the assets,
the node stops it again.

The whole chain in one test: a ComfyUI node starts the web front end, a
browser drives the pages through the asset lifecycle, and a node stops
the server.

Two levels:

  * through the NODES - the node classes are called directly, so this
    runs anywhere (the CI included). The web app is pointed at a
    TEMPORARY plugin root, so the user's asset library is never touched.
  * through ComfyUI - the node is submitted to a running ComfyUI's /prompt,
    which is what a person actually does. It needs a live ComfyUI, so it
    skips when there is none.

The ComfyUI runtime comes from the adjacent checkout, the same way the
engine's other tests find it: COMFYUI_PATH, or a sibling directory.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The engine's own modules are a package (the plugin root has an
# __init__.py), so they are loaded under a name rather than imported.
_pkg = types_module = None


def _load_engine_module(name: str):
    """Import one module of the engine pack."""
    import types
    if "h3_engine" not in sys.modules:
        package = types.ModuleType("h3_engine")
        package.__path__ = [str(ROOT)]
        package.__package__ = "h3_engine"
        sys.modules["h3_engine"] = package
    spec = importlib.util.spec_from_file_location(
        "h3_engine." + name, ROOT / (name + ".py"),
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _comfy_root():
    """The adjacent ComfyUI checkout, if there is one."""
    candidates = ([pathlib.Path(os.environ["COMFYUI_PATH"])]
                  if os.environ.get("COMFYUI_PATH") else [])
    candidates += [ROOT.parent / "Comfyui", ROOT.parent / "ComfyUI",
                   pathlib.Path(r"C:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\ComfyUI")]
    for path in candidates:
        if (path / "comfy" / "options.py").is_file():
            return path
    return None


COMFY = _comfy_root()
if COMFY is not None and str(COMFY) not in sys.path:
    sys.path.insert(0, str(COMFY))

webui_node = _load_engine_module("webui_node")

PORT = 8199
BASE = "http://127.0.0.1:%d" % PORT


def _probe(timeout: float = 1.5) -> dict:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return {}


def _wait_gone(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _probe():
            return True
        time.sleep(0.3)
    return False


@pytest.fixture()
def temp_plugin_root(tmp_path, monkeypatch):
    """A throwaway plugin root, and the environment that points at it.

    The start node hands its environment to the web app, so this is what
    keeps the test off the user's real library.
    """
    root = tmp_path / "plugin"
    for kind in ("characters", "scenes", "storyboards"):
        (root / "asset_library" / kind).mkdir(parents=True)
    monkeypatch.setenv("MIE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("MIE_WEB_SETTINGS", str(tmp_path / "settings.json"))
    # Whatever the plugin's own settings say, the test's own root wins.
    monkeypatch.setenv("MIE_WEB_PORT", str(PORT))
    yield root
    # Never leave the server running, whatever happened above.
    try:
        webui_node.MieH3StopWebUI().disable(PORT)
    except Exception:
        pass


def test_the_nodes_start_and_stop_the_web_app(temp_plugin_root):
    """Start with the node, stop with the node."""
    # A clean slate: whatever was running is stopped first, and the port is
    # ours for the duration.
    webui_node.MieH3StopWebUI().disable(PORT)
    assert not _probe(), "the port must be free before the test starts"

    url, status, started = webui_node.MieH3StartWebUI().enable(PORT)
    assert started is True, status
    assert _probe().get("status") == "ok", "the web app must answer"
    assert "已启动" in status, status

    # The web app really is pointed at the temporary root.
    assets = json.loads(urllib.request.urlopen(
        BASE + "/api/assets", timeout=30).read())
    assert assets["characters"] == [], "the test must not see the user's assets"

    url, stopped = webui_node.MieH3StopWebUI().disable(PORT)
    assert stopped is True, url
    assert _wait_gone(), "the web app must be gone"
    assert not _probe()


def test_the_pages_manage_the_assets_while_the_node_holds_it_up(temp_plugin_root):
    """The node starts it, the pages do the work, the node stops it.

    The browser part is the same lifecycle the page tests cover; what is
    new here is that the SERVER is the one the node started.
    """
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="Playwright is not installed")
    from playwright.sync_api import sync_playwright

    webui_node.MieH3StopWebUI().disable(PORT)
    url, status, started = webui_node.MieH3StartWebUI().enable(PORT)
    assert started is True, status

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.on("dialog", lambda d: d.accept())
            try:
                # A character: create, see it, edit it, delete it.
                page.goto(BASE + "/assets", wait_until="networkidle")
                page.wait_for_timeout(700)
                assert page.locator(".list-row").filter(visible=True).count() == 0

                page.get_by_role("button", name="＋ 新建").click()
                page.wait_for_timeout(400)
                fields = page.locator(".card .field .input").filter(visible=True)
                fields.nth(0).fill("E2E角色")
                fields.nth(1).fill("第一版")
                fields.nth(2).fill("young female")
                page.locator("input[type=file]").first.set_input_files(
                    {"name": "p.png", "mimeType": "image/png",
                     "buffer": _png_bytes()})
                page.get_by_role("button", name="💾 保存").click()
                page.wait_for_timeout(1500)
                assert "已保存" in _message(page), "the page must say it saved"

                listing = json.loads(urllib.request.urlopen(
                    BASE + "/api/assets", timeout=30).read())
                assert [c["name"] for c in listing["characters"]] == ["E2E角色"]
                assert listing["characters"][0]["image_count"] == 1

                # Edit it.
                page.locator(".list-row").filter(visible=True).first.click()
                page.wait_for_timeout(800)
                page.get_by_role("button", name="✏️ 编辑").click()
                page.wait_for_timeout(400)
                fields = page.locator(".card .field .input").filter(visible=True)
                fields.nth(1).fill("第二版")
                page.get_by_role("button", name="💾 保存").click()
                page.wait_for_timeout(1500)
                data = json.loads(urllib.request.urlopen(
                    BASE + "/api/assets/character/" + _q("E2E角色"),
                    timeout=30).read())
                assert data["description"] == "第二版"
                assert data["voice"] == "young female", "the voice must survive"
                assert data["image_count"] == 1, "the portrait must survive"

                # Delete it.
                page.locator(".list-row").filter(visible=True).first.click()
                page.wait_for_timeout(800)
                page.get_by_role("button", name="🗑️ 删除").click()
                page.wait_for_timeout(1500)
                listing = json.loads(urllib.request.urlopen(
                    BASE + "/api/assets", timeout=30).read())
                assert listing["characters"] == []
            finally:
                browser.close()
    finally:
        webui_node.MieH3StopWebUI().disable(PORT)

    assert _wait_gone(), "the node must be able to stop the web app"


@pytest.mark.skipif(not os.environ.get("MIE_LIVE_COMFYUI"),
                    reason="set MIE_LIVE_COMFYUI=1 to drive a running ComfyUI")
def test_the_nodes_work_through_comfyui(temp_plugin_root):
    """Submit the node to a running ComfyUI, the way a person would."""
    comfy_url = os.environ.get("MIE_COMFYUI_URL", "http://127.0.0.1:8188")

    def submit(graph):
        body = json.dumps({"prompt": graph, "client_id": "e2e"}).encode()
        req = urllib.request.Request(
            comfy_url + "/prompt", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # ComfyUI answers a rejected prompt with the reason - most
            # often "the node type is not registered".
            raise AssertionError(
                "ComfyUI rejected the prompt: %s" % exc.read().decode(
                    "utf-8", "replace")[:400]) from exc

    def wait(prompt_id, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with urllib.request.urlopen(
                    comfy_url + "/history/" + prompt_id, timeout=30) as response:
                history = json.loads(response.read())
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(0.5)
        raise AssertionError("the prompt never finished")

    webui_node.MieH3StopWebUI().disable(PORT)

    result = submit({"1": {"class_type": "MieH3StartWebUI",
                           "inputs": {"port": PORT}}})
    entry = wait(result["prompt_id"])
    assert entry.get("status", {}).get("status_str") == "success", entry
    assert _probe().get("status") == "ok", "the node must have started it"

    result = submit({"1": {"class_type": "MieH3StopWebUI",
                           "inputs": {"port": PORT}}})
    entry = wait(result["prompt_id"])
    assert entry.get("status", {}).get("status_str") == "success", entry
    assert _wait_gone(), "the node must have stopped it"


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _message(page, timeout=8000):
    try:
        page.locator(".result-box").filter(visible=True).first.wait_for(
            state="visible", timeout=timeout)
        return page.locator(".result-box").filter(visible=True).first.inner_text()
    except Exception:
        return "(没有出现)"


def _q(text):
    import urllib.parse
    return urllib.parse.quote(text, safe="")
