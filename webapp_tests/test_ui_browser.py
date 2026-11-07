"""The pages themselves: drive them in a browser and click the buttons.

The API has its own tests, but they cannot see what a person sees. Two
bugs got past them and were caught here: the confirmation box was inside
the card that saving hides, and the message was cleared by the reload
that followed the save.

The library these run against is a TEMPORARY one - the real asset
library is the user's, and a test has no business writing to it.

Skipped when Playwright is not installed.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBAPP_MAIN = PLUGIN_ROOT / "webapp" / "__main__.py"
PORT = 8199
BASE = "http://127.0.0.1:%d" % PORT


def png_bytes(width: int = 4, height: int = 4) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


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
    """A web app on a throwaway plugin root."""
    root = tmp_path_factory.mktemp("plugin")
    (root / "asset_library" / "characters").mkdir(parents=True)
    (root / "asset_library" / "scenes").mkdir(parents=True)
    (root / "asset_library" / "storyboards").mkdir(parents=True)
    env = dict(os.environ)
    env["MIE_PLUGIN_ROOT"] = str(root)
    env["MIE_WEB_SETTINGS"] = str(root / "settings.json")
    env["MIE_WEB_PORT"] = str(PORT)
    env["MIE_WEB_HOST"] = "127.0.0.1"
    process = subprocess.Popen(
        [sys.executable, str(WEBAPP_MAIN)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_for(BASE + "/api/health"):
        process.kill()
        pytest.skip("the web app did not come up")
    yield root
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture()
def page(web):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        screen = browser.new_page(viewport={"width": 1500, "height": 1000})
        screen.on("dialog", lambda d: d.accept())
        yield screen
        browser.close()


def visible(page, selector):
    return page.locator(selector).filter(visible=True)


def button(page, text):
    return page.get_by_role("button", name=text)


def row_named(page, name):
    for row in visible(page, ".list-row").all():
        if row.inner_text().strip().startswith(name):
            return row
    return None


def message(page, selector=".result-box", timeout=8000):
    """The visible message box's text, or a marker if it never showed."""
    try:
        page.locator(selector).filter(visible=True).first.wait_for(
            state="visible", timeout=timeout)
        return page.locator(selector).filter(visible=True).first.inner_text().strip()
    except Exception:
        return "(没有出现)"


def api_get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=30).read())


def test_a_character_can_be_created_edited_and_deleted(page):
    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(700)
    assert visible(page, ".list-row").count() == 0, "a fresh library is empty"

    button(page, "＋ 新建").click()
    page.wait_for_timeout(400)
    fields = visible(page, ".card .field .input")
    fields.nth(0).fill("测试角色")
    fields.nth(1).fill("第一版描述")
    fields.nth(2).fill("young female")
    page.locator("input[type=file]").first.set_input_files(
        {"name": "p.png", "mimeType": "image/png", "buffer": png_bytes()})
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)

    # The confirmation must be VISIBLE. It used to live inside the card
    # that saving hides, so it was never seen.
    assert "已保存" in message(page), "the save must say so"
    assert row_named(page, "测试角色") is not None

    row_named(page, "测试角色").click()
    page.wait_for_timeout(1000)
    assert page.locator(".preview-img").first.get_attribute("src").startswith(
        "data:image/png")

    button(page, "✏️ 编辑").click()
    page.wait_for_timeout(400)
    fields = visible(page, ".card .field .input")
    fields.nth(1).fill("第二版描述")
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)

    data = api_get("/api/assets/character/%E6%B5%8B%E8%AF%95%E8%A7%92%E8%89%B2")
    assert data["description"] == "第二版描述"
    assert data["voice"] == "young female", "a text-only edit keeps the voice"
    assert data["image_count"] == 1, "a text-only edit keeps the portrait"

    row_named(page, "测试角色").click()
    page.wait_for_timeout(800)
    button(page, "🗑️ 删除").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "测试角色") is None
    assert api_get("/api/assets")["characters"] == []


def test_a_scene_can_be_created_edited_and_deleted(page):
    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.get_by_role("button", name="场景").click()
    page.wait_for_timeout(700)

    button(page, "＋ 新建").click()
    page.wait_for_timeout(400)
    fields = visible(page, ".card .field .input")
    fields.nth(0).fill("测试场景")
    fields.nth(1).fill("第一版描述")
    page.locator("input[type=file]").first.set_input_files(
        {"name": "p.png", "mimeType": "image/png", "buffer": png_bytes()})
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    assert "已保存" in message(page)
    assert row_named(page, "测试场景") is not None

    row_named(page, "测试场景").click()
    page.wait_for_timeout(800)
    # Managing it: the detail and its picture.
    row_named(page, "测试场景").click()
    page.wait_for_timeout(1000)
    assert visible(page, ".card-head h3").first.inner_text().strip() == "测试场景"
    assert page.locator(".preview-img").first.get_attribute("src").startswith(
        "data:image/png")
    assert "第一版描述" in visible(page, ".meta-list").inner_text()

    button(page, "✏️ 编辑").click()
    page.wait_for_timeout(400)
    fields = visible(page, ".card .field .input")
    fields.nth(1).fill("第二版描述")
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    data = api_get("/api/assets/scene/%E6%B5%8B%E8%AF%95%E5%9C%BA%E6%99%AF")
    assert data["description"] == "第二版描述"
    assert data["image_count"] == 1

    row_named(page, "测试场景").click()
    page.wait_for_timeout(800)
    button(page, "🗑️ 删除").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "测试场景") is None
    assert api_get("/api/assets")["scenes"] == []


def test_a_script_can_be_created_edited_and_deleted(page):
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    assert visible(page, ".list-row").count() == 0

    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    skeleton = json.loads(page.locator("textarea.code").input_value())
    skeleton["name"] = "测试剧本"
    skeleton["source_script"] = "角色1：你好"
    skeleton["shots"][0]["events"][0]["text"] = "你好"
    page.locator("textarea.code").fill(
        json.dumps(skeleton, ensure_ascii=False, indent=2))
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    assert "已保存" in message(page, ".validation-box.ok")
    assert row_named(page, "测试剧本") is not None

    row_named(page, "测试剧本").click()
    page.wait_for_timeout(1200)
    current = json.loads(page.locator("textarea.code").input_value())
    current["constraints"] = "改过的约束"
    page.locator("textarea.code").fill(
        json.dumps(current, ensure_ascii=False, indent=2))
    button(page, "✔️ 校验").click()
    page.wait_for_timeout(1200)
    boxes = visible(page, ".validation-box").all_inner_texts()
    assert not any("契约不通过" in b for b in boxes), boxes

    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    data = api_get("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC")
    assert data["json"]["constraints"] == "改过的约束"

    row_named(page, "测试剧本").click()
    page.wait_for_timeout(800)
    button(page, "🗑️ 删除").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "测试剧本") is None
    assert api_get("/api/scripts")["names"] == []
