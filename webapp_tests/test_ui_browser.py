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
import urllib.parse
import urllib.request

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBAPP_MAIN = PLUGIN_ROOT / "webapp" / "__main__.py"
# NOT 8199: that is the port the workbench itself runs on, and the test
# server would take the port over - the person using the workbench would
# be talking to a server with a throwaway library.
PORT = 8299
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
    """A web app on a throwaway plugin root.

    The ComfyUI output directory is thrown away too: the task list
    reads the finished renders off the disk, and a test has no business
    reading - let alone writing - the user's real output folder.
    """
    root = tmp_path_factory.mktemp("plugin")
    (root / "asset_library" / "characters").mkdir(parents=True)
    (root / "asset_library" / "scenes").mkdir(parents=True)
    (root / "asset_library" / "storyboards").mkdir(parents=True)
    # 一份做出来的片子：正式成片、片段、技术文件。
    run = root / "comfyui" / "output" / "h3_chains" / "sb_demo"
    (run / "final").mkdir(parents=True)
    (run / "segments").mkdir(parents=True)
    (run / "checkpoints").mkdir(parents=True)
    (run / "final" / "sb_demo.mp4").write_bytes(b"0")
    (run / "final" / "sb_demo.generated.wav").write_bytes(b"0")
    (run / "segments" / "clip_0001.mp4").write_bytes(b"0")
    (run / "checkpoints" / "model.safetensors").write_bytes(b"0")
    (run / "manifest.json").write_text(json.dumps(
        {"duration_seconds": 12.5, "clip_count": 1}), encoding="utf-8")
    # 任务记录：app 启动时读它。
    (root / "jobs.json").write_text(json.dumps({"jobs": [{
        "job_id": "p-demo", "number": 1, "script": "测试剧本",
        "run_name": "sb_demo", "title": "测试剧本 · 第 1 版", "attempt": 1,
        "submitted_at": 1.0, "status": "success", "error": "", "nodes": 33,
        "role_assets": {"character_1": "甲"},
        "params": {"width": 960, "height": 544, "steps": 20, "duration": 12},
    }]}), encoding="utf-8")
    env = dict(os.environ)
    env["MIE_PLUGIN_ROOT"] = str(root)
    env["MIE_WEB_SETTINGS"] = str(root / "settings.json")
    env["MIE_COMFYUI_ROOT"] = str(root / "comfyui")
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
    """A page in a browser.

    Every dialog is answered "yes" by default, so the confirmation
    boxes do not block a test. A test that wants to look at the
    question, or to say no, sets ``screen.answer`` to ``"no"`` (or to
    a callable) before the click; the questions are collected in
    ``screen.questions``.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        screen = browser.new_page(viewport={"width": 1500, "height": 1000})
        screen.questions = []
        screen.answer = "yes"

        def on_dialog(dialog):
            screen.questions.append(dialog.message)
            answer = screen.answer
            if callable(answer):
                answer(dialog)
            elif answer == "no":
                dialog.dismiss()
            else:
                dialog.accept()

        screen.on("dialog", on_dialog)
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

    # 新建：落在编辑标签，直接填表单而不是手写 JSON。
    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    page.get_by_placeholder("剧本名字").fill("测试剧本")
    page.locator(".editor-pane textarea.code").fill("角色1：你好")
    page.wait_for_timeout(400)
    button(page, "💾 保存").click()
    assert "已保存" in message(page, ".validation-box.ok")
    assert row_named(page, "测试剧本") is not None
    saved = api_get("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC")
    assert saved["json"]["source_script"] == "角色1：你好"
    assert saved["json"]["shots"][0]["events"][0]["text"] == "你好"

    # 重新打开：预览标签有时间轴和分镜卡。
    row_named(page, "测试剧本").click()
    page.wait_for_timeout(1200)
    assert visible(page, ".timeline-seg").count() == 1
    assert "你好" in visible(page, ".shot-card").first.inner_text()
    assert "分镜1" in row_named(page, "测试剧本").inner_text()

    # 编辑标签：追加一个动作事件，用 ✂ 把它分段成第二镜；两镜各占
    # 至少 ~1 秒，目标时长 2 秒装不下，按提示调到 6 秒。
    button(page, "✏️ 编辑").click()
    page.wait_for_timeout(300)
    page.locator(".editor-pane textarea.code").fill("角色1：你好\n角色1动作：他挥了挥手。")
    page.wait_for_timeout(600)
    assert visible(page, ".shot-card").count() == 1
    visible(page, ".event-split").first.click()
    page.wait_for_timeout(600)
    assert visible(page, ".shot-card").count() == 2
    assert "偏短" in visible(page, ".editor-pane p.muted.small").first.inner_text()
    target_box = page.locator(".editor-pane input[type=number]").first
    target_box.fill("6")
    page.wait_for_timeout(600)
    assert "偏短" not in visible(page, ".editor-pane p.muted.small").first.inner_text()

    # JSON 源码标签：改约束、校验、保存。
    button(page, "🧾 JSON 源码").click()
    page.wait_for_timeout(400)
    json_box = page.locator(".json-pane textarea.code")
    current = json.loads(json_box.input_value())
    current["constraints"] = "改过的约束"
    json_box.fill(json.dumps(current, ensure_ascii=False, indent=2))
    button(page, "✔️ 校验").click()
    page.wait_for_timeout(1200)
    boxes = visible(page, ".validation-box").all_inner_texts()
    assert not any("契约不通过" in b for b in boxes), boxes

    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    data = api_get("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC")
    assert data["json"]["constraints"] == "改过的约束"
    assert len(data["json"]["shots"]) == 2
    assert [e["type"] for e in data["json"]["shots"][1]["events"]] == ["action"]
    assert data["json"]["shots"][1]["events"][0]["text"] == "他挥了挥手。"

    # 预览时间轴现在有两段。
    row_named(page, "测试剧本").click()
    page.wait_for_timeout(1200)
    assert visible(page, ".timeline-seg").count() == 2

    row_named(page, "测试剧本").click()
    page.wait_for_timeout(800)
    button(page, "🗑️ 删除").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "测试剧本") is None
    assert api_get("/api/scripts")["names"] == []


def test_an_unsaved_draft_is_offered_back(page):
    """The draft has to survive the page closing, and be OFFERED - not
    deleted before anyone can see it."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(500)
    page.get_by_placeholder("剧本名字").fill("草稿剧本")
    page.locator(".editor-pane textarea.code").fill("角色1：草稿台词")
    # 等防抖把草稿写进本地
    page.wait_for_timeout(1400)

    # 关掉页面（换一页），再回来：草稿还在，而且有人给。
    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(400)
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    stored = page.evaluate(
        "() => window.localStorage.getItem('mie-script-draft:new')")
    assert stored, "the draft was never stored"

    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    banner = visible(page, ".draft-box")
    assert banner.count() >= 1, "nobody was offered the draft"
    assert "发现未保存的草稿" in banner.first.inner_text()
    button(page, "↩ 恢复草稿").click()
    page.wait_for_timeout(700)
    assert page.get_by_placeholder("剧本名字").input_value() == "草稿剧本"
    assert "草稿台词" in page.locator(".editor-pane textarea.code").input_value()


def test_a_half_written_json_is_unsaved_work(page):
    """JSON that does not parse yet is still the author's work: it must
    count as unsaved, and it must not be thrown away."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(500)
    button(page, "🧾 JSON 源码").click()
    page.wait_for_timeout(400)
    page.locator(".json-pane textarea.code").fill('{"version": 2, "name": "没写完')
    page.wait_for_timeout(1400)   # 等草稿的防抖写进本地

    badge = visible(page, ".card-head .badge").first.inner_text()
    assert badge == "未保存", badge
    stored = page.evaluate(
        "() => window.localStorage.getItem('mie-script-draft:new')")
    assert stored and "没写完" in stored, "the raw text was not kept"


def test_a_broken_board_says_why_instead_of_going_blank(page, web):
    """Clicking a board that cannot be read must not leave the pane
    looking like nothing was clicked."""
    folder = web / "asset_library" / "storyboards" / "坏剧本"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "坏剧本.h3_asset.json").write_text(
        json.dumps({"version": 2, "name": "坏剧本"}), encoding="utf-8")

    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(900)
    row = row_named(page, "坏剧本")
    assert row is not None, "the broken board is not even in the list"
    row.click()
    page.wait_for_timeout(1200)

    box = visible(page, ".split-detail .validation-box")
    assert box.count() >= 1, "the pane said nothing about the failure"
    text = box.first.inner_text()
    assert "storyboard.json" in text, text
    assert "坏剧本.h3_asset.json" in text, text


def test_a_broken_board_can_still_be_deleted(page, web):
    """A board that cannot be read must not be stuck in the library:
    the delete button lives on the card, and a broken board has no
    card."""
    folder = web / "asset_library" / "storyboards" / "坏剧本可删"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "坏剧本可删.h3_asset.json").write_text(
        json.dumps({"version": 2, "name": "坏剧本可删"}), encoding="utf-8")

    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(900)
    row = row_named(page, "坏剧本可删")
    assert row is not None
    row.click()
    page.wait_for_timeout(1200)

    button(page, "🗑️ 删掉这份剧本").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "坏剧本可删") is None, "it is still in the list"
    assert not folder.exists(), "the folder is still on disk"


def test_a_broken_character_shows_up_and_can_be_deleted(page, web):
    """The library skips a broken character, so it used to be nowhere on
    the page - and a character that is nowhere cannot be deleted."""
    folder = web / "asset_library" / "characters" / "坏角色"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(900)
    row = row_named(page, "坏角色")
    assert row is not None, "the broken character is not on the page"
    assert "损坏" in row.inner_text(), row.inner_text()

    row.click()
    page.wait_for_timeout(1000)
    assert "读取失败" in message(page), "the page did not say why"

    button(page, "🗑️ 删除").click()
    page.wait_for_timeout(1500)
    assert row_named(page, "坏角色") is None, "it is still in the list"
    assert not folder.exists(), "the folder is still on disk"


def test_the_task_card_leads_with_the_finished_film(page):
    """Nineteen files in a flat list answers none of the author's
    questions. The film comes first, in a player, with a download."""
    page.goto(BASE + "/history", wait_until="networkidle")
    page.wait_for_timeout(1500)
    card = visible(page, ".job-row").first
    assert card.count() > 0, "the task list is empty"

    # 播放器是点开才挂的：一次挂二十几个，每个都会去拉一次 mp4 的
    # 元数据，页面会卡住。
    assert card.locator("video.delivery-player").count() == 0,         "the player is mounted before anyone asked for it"
    card.locator("button", has_text="播放").first.click()
    page.wait_for_timeout(400)
    player = card.locator("video.delivery-player")
    assert player.count() == 1, "there is no player for the film"
    assert "sb_demo.mp4" in (player.get_attribute("src") or "")

    download = card.locator("a.btn.primary")
    assert download.count() == 1, "there is no download button"
    assert download.get_attribute("download") is not None
    assert "sb_demo.mp4" in (download.get_attribute("href") or "")

    # 时长来自 manifest，不是编的。
    assert "12.5" in card.inner_text(), card.inner_text()

    # 片段和技术文件收进次级区，不平铺。
    summaries = [d.locator("summary").inner_text()
                 for d in card.locator("details.preview-block").all()]
    assert any("分镜片段" in s for s in summaries), summaries
    assert any("技术文件" in s for s in summaries), summaries
    # 技术文件不直接出现在页面上。
    assert "model.safetensors" not in card.inner_text()


def test_a_finished_task_can_be_continued(page):
    """Carrying on is a button, not something the author has to
    reconstruct by hand."""
    page.goto(BASE + "/history", wait_until="networkidle")
    page.wait_for_timeout(1500)
    card = visible(page, ".job-row").first
    button = card.locator("button", has_text="从这次继续")
    assert button.count() == 1, "there is no way to carry on"


def test_the_history_page_pages_favourites_and_deletes(page, web):
    """The history is a list the author can work with: page through it,
    keep the good ones, put the rest away."""
    page.goto(BASE + "/history", wait_until="networkidle")
    page.wait_for_timeout(1200)

    assert visible(page, ".job-row").count() >= 1, "the history is empty"
    card = visible(page, ".job-row").first

    # 收藏：点一下就变实心，再点一下就回去。
    star = card.locator("button.btn.star")
    assert star.count() == 1, "there is no way to keep a run"
    star.click()
    page.wait_for_timeout(700)
    assert "★" in card.locator("button.btn.star").inner_text()

    # 只看收藏。
    page.get_by_role("button", name="★ 收藏").click()
    page.wait_for_timeout(900)
    assert visible(page, ".job-row").count() == 1, \
        "the favourite filter kept the wrong rows"

    # 移出列表：文件不动。
    page.get_by_role("button", name="★ 收藏").click()
    page.wait_for_timeout(900)
    card = visible(page, ".job-row").first
    card.get_by_role("button", name="🗑️ 移出列表").click()
    page.wait_for_timeout(1200)
    assert visible(page, ".job-row").count() == 0, "the run is still there"
    # 硬盘上的文件还在。
    run_dir = web / "comfyui" / "output" / "h3_chains" / "sb_demo"
    assert (run_dir / "final" / "sb_demo.mp4").is_file(), \
        "the film was deleted along with the entry"


def test_the_history_page_has_pages(page, web):
    page.goto(BASE + "/history", wait_until="networkidle")
    page.wait_for_timeout(1000)
    # 只有一条记录时翻页条不出现，但每页条数是可以选的。
    assert visible(page, ".card select").count() >= 1


def test_a_new_mirror_is_not_confirmed_against_a_moved_source(page, web):
    """Applying a fresh English mirror, then changing the Chinese and
    saving, must NOT confirm the mirror: it was written for the
    Chinese as it was, and the Chinese has moved on."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    page.get_by_placeholder("剧本名字").fill("镜像测试")
    page.locator(".editor-pane textarea.code").fill("角色1：你好")
    page.wait_for_timeout(600)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1200)

    # 打开高级区，把执行镜像当成"刚换过"的样子：记下源文本指纹。
    page.evaluate("""() => {
        const app = Alpine.$data(document.querySelector('section[x-data]'));
        app.executionApplied = app.sourceSignature();
    }""")
    applied = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).executionApplied")
    assert applied, "the source signature was empty"

    # 改中文：源文本动了。
    page.get_by_role("button", name="✏️ 编辑").click()
    page.wait_for_timeout(300)
    ta = page.locator(".editor-pane textarea.code").first
    ta.fill(ta.input_value() + "\n角色1动作：他点了点头。")
    page.wait_for_timeout(900)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)

    # 存盘不该把过期镜像确认掉。问服务端：它有没有把"确认"记下来。
    data = api_get("/api/scripts/%E9%95%9C%E5%83%8F%E6%B5%8B%E8%AF%95")
    assert data["execution_state"] != "confirmed",         "the mirror was confirmed against a source it was not written for"


def test_the_making_page_shows_the_script_it_will_render(page, web):
    """Picking a script from a list means nothing unless you can see what
    you picked."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    page.get_by_placeholder("剧本名字").fill("预览测试")
    page.locator(".editor-pane textarea.code").fill("角色1：你好\n角色1动作：他点了点头。")
    page.wait_for_timeout(600)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)

    page.goto(BASE + "/video", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.locator("select").first.select_option("预览测试")
    page.wait_for_timeout(1800)

    card = page.locator(".split-detail .card").first
    assert card.count() > 0, "the chosen script is not shown at all"
    assert "预览测试" in card.locator("h3").inner_text()
    body = card.inner_text()
    assert "你好" in body, "the lines are not visible"
    assert "他点了点头" in body, "the actions are not visible"
    # 角色要写名字，不是编号。
    assert "角色1" in body, body
    assert "character_1" not in body, body
    # 还要能跳到剧本页看完整的那份。
    link = card.locator("a.btn")
    assert "/scripts?script=" in (link.get_attribute("href") or "")


def test_the_script_page_opens_the_script_it_was_asked_for(page, web):
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    page.get_by_placeholder("剧本名字").fill("跳转测试")
    page.locator(".editor-pane textarea.code").fill("角色1：你好")
    page.wait_for_timeout(600)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)

    page.goto(BASE + "/scripts?script=%E8%B7%B3%E8%BD%AC%E6%B5%8B%E8%AF%95",
              wait_until="networkidle")
    page.wait_for_timeout(1800)
    assert visible(page, ".card-head h3").first.inner_text() == "跳转测试"


def _write_a_script(page, name, lines):
    """A saved script whose source is the given lines."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(700)
    button(page, "＋ 新建").click()
    page.wait_for_timeout(600)
    page.get_by_placeholder("剧本名字").fill(name)
    page.locator(".editor-pane textarea.code").fill("\n".join(lines))
    page.wait_for_timeout(700)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="✏️ 编辑").click()
    page.wait_for_timeout(400)


def test_a_risk_can_be_fixed_in_one_click(page, web):
    """A render risk that is arithmetic is fixed by arithmetic: the
    shot was too short for its lines, so the shot gets longer. No model,
    no waiting - and the library is not touched until it is saved."""
    line = "这是一句需要慢慢说的台词"
    _write_a_script(page, "风险测试", ["角色1：" + line for _ in range(3)])

    # 把这一镜改短一点，让它装不下那几句话。
    page.evaluate("""() => {
        const app = Alpine.$data(document.querySelector('section[x-data]'));
        app.shotDurations = [6.5];
        app.model.target_duration_seconds = 6.5;
        app.syncDerived();
        app.queueValidate();
    }""")
    page.wait_for_timeout(2000)
    risk = page.locator(".validation-box.risk")
    assert risk.count() == 1, "the fixture did not produce a risk"
    risk.first.get_by_role("button", name="说明").click()
    page.wait_for_timeout(500)

    before = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    risk.first.locator('.risk-row button', has_text="按这条改").first.click()
    page.wait_for_timeout(2500)

    after = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    assert after > before, "the shot was not made longer"
    # 风险没了，而且这一版契约还收得住。
    assert not visible(page, ".validation-box.risk").count(), "the risk is still there"
    assert not visible(page, ".validation-box.err").count(), "the contract refuses the result"
    # 存盘之前，库里那份不能变。
    assert page.locator(".card-head .badge").filter(visible=True).first.inner_text() == "未保存"


def test_every_risk_of_a_script_can_be_fixed_at_once(page, web):
    """「全部按建议改」: one click, every risk, one board.

    Two shots, both too short for their lines: the button has to fix
    both, not the one that happens to be first.
    """
    line = "这是一句需要慢慢说的台词"
    _write_a_script(page, "全改测试", ["角色1：" + line for _ in range(6)])

    # 拆成两镜，每镜三句，都给得太短。
    page.evaluate("""() => {
        const app = Alpine.$data(document.querySelector('section[x-data]'));
        app.grouping = [[0, 1, 2], [3, 4, 5]];
        app.shotDurations = [6, 6];
        app.model.target_duration_seconds = 12;
        app.syncDerived();
        app.queueValidate();
    }""")
    page.wait_for_timeout(2000)
    risk = page.locator(".validation-box.risk")
    assert risk.count() == 1, "the fixture did not produce a risk"
    assert "×2" in risk.first.inner_text(), risk.first.inner_text()

    before = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    risk.first.get_by_role("button", name="全部按建议改").click()
    page.wait_for_timeout(3000)

    after = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    assert after > before, "the film was not made longer"
    # 算得出来的两条都消了。
    assert "慢速塞满会丢句" not in risk.first.inner_text(), risk.first.inner_text()
    # 剩下的那条要模型判断：这台机器上没有配模型，页面得说实话，
    # 不能假装全改好了。
    assert "单人续接缺陷入形" in risk.first.inner_text(), risk.first.inner_text()
    assert "需要判断的那几条没有改" in message(page, ".validation-box.ok"), \
        message(page, ".validation-box.ok")
    assert not visible(page, ".validation-box.err").count(), "the contract refuses the result"
    # 存盘之前，库里那份不能变。
    assert page.locator(".card-head .badge").filter(visible=True).first.inner_text() == "未保存"


def test_the_fix_asks_before_it_overrides_what_you_edited(page, web):
    """模型在写的那几十秒里，作者可能又改了。

    改好的那一版是按改之前的那一版算的：盖上去会把你刚改的弄没了，
    所以要先问一声，说清改了哪儿。
    """
    line = "这是一句需要慢慢说的台词"
    _write_a_script(page, "别盖掉我", ["角色1：" + line for _ in range(3)])
    page.evaluate("""() => {
        const app = Alpine.$data(document.querySelector('section[x-data]'));
        app.shotDurations = [6.5];
        app.model.target_duration_seconds = 6.5;
        app.syncDerived();
        app.queueValidate();
    }""")
    page.wait_for_timeout(2000)
    risk = page.locator(".validation-box.risk")
    assert risk.count() == 1, "the fixture did not produce a risk"

    # 让"模型"慢一点：回话之前，作者改了画风。
    page.evaluate("""() => {
        const real = window.fetch;
        window.fetch = async (url, options) => {
            const response = await real(url, options);
            if (String(url).indexOf('/api/scripts/risk/fix') >= 0) {
                await new Promise((resolve) => setTimeout(resolve, 1200));
                const app = Alpine.$data(document.querySelector('section[x-data]'));
                app.model.look = '水墨风';
            }
            return response;
        };
    }""")
    page.answer = "no"
    risk.first.get_by_role("button", name="全部按建议改").click()
    page.wait_for_timeout(5000)

    assert page.questions, "作者改了之后，一句不问就要盖上去"
    assert "画风" in page.questions[-1], page.questions[-1]
    look = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.look")
    assert look == "水墨风", "作者刚改的画风被盖掉了"
    target = page.evaluate(
        "() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    assert target == 6.5, "作者说了不盖，时长却还是被改了"
    assert "作废" in message(page, ".validation-box.ok"), message(page, ".validation-box.ok")


def test_the_making_page_shows_a_script_with_repeated_lines(page, web):
    """同一角色说同一句话是常事。

    预览里每一行都要有key：拿台词当key，重复的台词就把整个列表
    渲染不出来，页面上一条都不剩。
    """
    script = {
        "version": 2, "name": "重复台词", "language": "zh",
        "roles": [{"id": "character_1", "name": "角色1", "presence": "on_screen",
                   "initially_visible": True, "position": "center",
                   "voice": "male voice"}],
        "camera": {"framing": "medium_close_up", "movement": "locked",
                   "perspective": "observer"},
        "look": "", "setting": "", "constraints": "",
        "target_duration_seconds": 5, "continuation_overlap_frames": 0,
        "source_script": "角色1：同一句话\n角色1：同一句话\n角色1：同一句话",
        "shots": [{"id": "scene_01", "duration_seconds": 5, "events": [
            {"id": "event_%03d" % (index + 1), "type": "dialogue",
             "character": "character_1", "text": "同一句话", "language": "zh"}
            for index in range(3)]}],
        "execution": {"look": "", "setting": "",
                      "visible_state": "Character 1 stands at center.",
                      "voices": {"character_1": "male voice"}, "directions": {}},
        "audio_mode": "generated",
    }
    body = json.dumps({"json": json.dumps(script)}).encode("utf-8")
    request = urllib.request.Request(BASE + "/api/scripts", data=body,
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=30).read()

    page.goto(BASE + "/video?script=" + urllib.parse.quote("重复台词"),
              wait_until="networkidle")
    page.wait_for_timeout(2000)
    events = visible(page, ".event-brief")
    assert events.count() == 3, "重复的台词把预览里的事件全弄没了"
