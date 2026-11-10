"""Screenshot evidence for the M1 intake journey (run once, not a test).

Starts the webapp on a throwaway root, walks the intake card through
its main journey with a stubbed model, and saves what the page looked
like at each step into notes/intake-m1-shots/.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBAPP_MAIN = PLUGIN_ROOT / "webapp" / "__main__.py"
PORT = 8297
BASE = "http://127.0.0.1:%d" % PORT
OUT = PLUGIN_ROOT / "notes" / "intake-m1-shots"

ASSET = {
    "version": 2, "name": "猫与外卖员", "language": "zh",
    "roles": [{"id": "character_1", "name": "外卖员", "presence": "on_screen",
               "initially_visible": True, "position": "center",
               "voice": "male voice"},
              {"id": "character_2", "name": "橘猫", "presence": "on_screen",
               "initially_visible": True, "position": "right", "voice": ""}],
    "camera": {"framing": "medium_shot", "movement": "locked",
               "perspective": "observer"},
    "look": "", "setting": "", "constraints": "",
    "target_duration_seconds": 20, "continuation_overlap_frames": 22,
    "source_script": "character_1 dialogue：你好呀。",
    "shots": [{"id": "scene_01", "duration_seconds": 5, "events": [
        {"id": "event_001", "type": "dialogue", "character": "character_1",
         "text": "你好呀。", "language": "zh"}]}],
    "execution": {"look": "", "setting": "",
                  "visible_state": "A man crouches by a cat in a kitchen.",
                  "voices": {"character_1": "male voice", "character_2": ""},
                  "directions": {}},
    "audio_mode": "generated",
}


def png_bytes(width=96, height=64):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 60)).save(buf, format="PNG")
    return buf.getvalue()


def wait_for(url, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    root = OUT / "_run"
    for sub in ("asset_library/characters", "asset_library/scenes",
                "asset_library/storyboards", "comfyui/output"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"MIE_PLUGIN_ROOT": str(root),
                "MIE_WEB_SETTINGS": str(root / "settings.json"),
                "MIE_COMFYUI_ROOT": str(root / "comfyui"),
                "MIE_WEB_PORT": str(PORT), "MIE_WEB_HOST": "127.0.0.1"})
    process = subprocess.Popen([sys.executable, str(WEBAPP_MAIN)], env=env,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        assert wait_for(BASE + "/api/health"), "webapp did not come up"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})

            page.route("**/api/scripts/validate", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "errors": [], "warnings": [],
                                 "json": ASSET})))
            page.route("**/api/scripts/auto", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"json": ASSET, "notes": []})))
            reading = {"code": "ok", "reading": {
                "vision": "supported", "basis": "image",
                "observed": [
                    {"id": "obs_1", "text": "图中可见一名穿蓝色外套的年轻人蹲在厨房。"},
                    {"id": "obs_2", "text": "图中可见一只橘猫趴在地板上。"}],
                "proposed": [
                    {"id": "pro_1", "text": "他可能是外卖员，正给这只猫送餐。"}],
                "conflicts": [
                    {"id": "cfl_1", "field": "人物身份", "image": "图中的人像房东",
                     "text": "原文写他是外卖员"}]}}
            page.route("**/api/scripts/intake", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(reading)))

            page.goto(BASE + "/scripts", wait_until="networkidle")
            page.get_by_role("button", name="✨ AI 起草").click()
            page.locator(".intake-card").wait_for(state="visible")
            page.locator(".intake-card .intake-material textarea").first.fill(
                "外卖员发现客人养的猫会自己开门，两人成了朋友。")
            page.locator(".intake-pick input[type=file]").set_input_files(
                [{"name": "pic.png", "mimeType": "image/png",
                  "buffer": png_bytes()}])
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / "1-material.png"), full_page=True)

            page.get_by_role("button", name="解读图片").click()
            page.locator(".intake-reading").wait_for(state="visible")
            page.wait_for_timeout(300)
            page.get_by_role("button", name="以文字为准").click()
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "2-understanding.png"), full_page=True)

            page.get_by_role("button", name="生成分镜草稿").click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "3-draft-loaded.png"), full_page=True)

            # 窄屏
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(BASE + "/scripts", wait_until="networkidle")
            page.get_by_role("button", name="✨ AI 起草").click()
            page.locator(".intake-card").wait_for(state="visible")
            page.locator(".intake-card .intake-material textarea").first.fill(
                "窄屏旅程。")
            page.screenshot(path=str(OUT / "4-narrow.png"), full_page=True)

            browser.close()
        print("shots saved to", OUT)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
