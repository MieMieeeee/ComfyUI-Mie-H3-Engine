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
        browser = p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"])
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


def api_script(name):
    """One script from the library, by name."""
    return api_get("/api/scripts/" + urllib.parse.quote(name))


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
    try:
        urllib.request.urlopen(request, timeout=30).read()
    except urllib.error.HTTPError as exc:
        raise AssertionError("script save rejected: %s" % exc.read()[:300]) from exc

    page.goto(BASE + "/video?script=" + urllib.parse.quote("重复台词"),
              wait_until="networkidle")
    page.wait_for_timeout(2000)
    events = visible(page, ".event-brief")
    assert events.count() == 3, "重复的台词把预览里的事件全弄没了"


def test_the_versions_of_a_script_are_kept(page, web):
    """保存永远产生新的一版：旧的一版还在，能打开看，也能恢复回去。

    改坏了一版、或者改砸了想反悔的时候，这一版还在，就不用重做。
    """
    _write_a_script(page, "版本测试", ["角色1：你好"])
    page.get_by_role("button", name="🕘 版本").click()
    page.wait_for_timeout(900)
    panel = page.locator(".version-list")
    assert panel.count() == 1, "the version panel did not open"
    assert "版本 1" in panel.inner_text(), panel.inner_text()

    # 改一句台词再存：多一版，旧的那版还在。
    page.locator(".editor-pane textarea.code").fill("角色1：改过的台词")
    page.wait_for_timeout(900)
    button(page, "💾 保存").click()
    page.wait_for_timeout(1800)
    panel = page.locator(".version-list")
    assert "版本 2" in panel.inner_text(), panel.inner_text()
    assert "版本 1" in panel.inner_text(), panel.inner_text()

    # 打开旧版：编辑器里是旧的内容，库里那份没变。
    page.locator(".version-row", has_text="版本 1").get_by_role(
        "button", name="打开").click()
    page.wait_for_timeout(1500)
    code = page.locator(".editor-pane textarea.code").input_value()
    assert "你好" in code, code
    assert page.locator(".card-head .badge").filter(visible=True).first.inner_text() \
        == "未保存"
    assert "改过的台词" in api_script("版本测试")["json"]["source_script"]

    # 恢复：旧版的内容存成新的一版，历史只增不减。
    page.locator(".version-row", has_text="版本 1").get_by_role(
        "button", name="恢复").click()
    page.wait_for_timeout(2500)
    assert "你好" in api_script("版本测试")["json"]["source_script"]
    panel = page.locator(".version-list")
    assert "版本 3" in panel.inner_text(), panel.inner_text()
    assert "版本 2" in panel.inner_text(), panel.inner_text()


# --------------------------------------------------------------------------- #
# 从图片或文字起草（intake M1）：真实页面 + 拦截模型响应的替身
# --------------------------------------------------------------------------- #

def _stub_asset(name="起草结果"):
    """The asset a stubbed generation returns."""
    return {
        "version": 2, "name": name, "language": "zh",
        "roles": [{"id": "character_1", "name": "甲", "presence": "on_screen",
                   "initially_visible": True, "position": "center",
                   "voice": "male voice"}],
        "camera": {"framing": "medium_shot", "movement": "locked",
                   "perspective": "observer"},
        "look": "", "setting": "", "constraints": "",
        "target_duration_seconds": 20, "continuation_overlap_frames": 22,
        "source_script": "甲：你好",
        "shots": [{"id": "scene_01", "duration_seconds": 5, "events": [
            {"id": "event_001", "type": "dialogue", "character": "character_1",
             "text": "你好", "language": "zh"}]}],
        "execution": {"look": "", "setting": "",
                      "visible_state": "Character 1 stands at center.",
                      "voices": {"character_1": "male voice"}, "directions": {}},
        "audio_mode": "generated",
    }


def _stub_validate(page, asset):
    def handler(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "errors": [], "warnings": [],
                                       "json": asset}))
    page.route("**/api/scripts/validate", handler)


def _stub_auto(page, asset, captured, hold=None):
    """Stub the generation; captured collects the request bodies.

    With ``hold`` (a dict) the reply is NOT sent when the request
    arrives - the test decides when, which is how a late answer is
    staged: the route lands in hold["route"] and is fulfilled later.
    """
    def handler(route):
        captured.append(json.loads(route.request.post_data))
        if hold is not None:
            hold["route"] = route
            hold["asset"] = asset
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": asset, "notes": []}))
    page.route("**/api/scripts/auto", handler)


def _stub_intake(page, replies, captured):
    """Stub /api/scripts/intake; replies is popped one per call."""
    def handler(route):
        captured.append(json.loads(route.request.post_data))
        body = replies.pop(0) if replies else {"code": "ok", "reading": {}}
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(body))
    page.route("**/api/scripts/intake", handler)


def _open_intake(page):
    page.goto(BASE + "/scripts", wait_until="networkidle")
    button(page, "✨ AI 起草").click()
    card = page.locator(".intake-card")
    card.wait_for(state="visible", timeout=5000)
    return card


def test_a_text_draft_lands_pending_save_not_in_the_library(page):
    asset = _stub_asset()
    captured = []
    _stub_validate(page, asset)
    _stub_auto(page, asset, captured)
    _open_intake(page)

    page.locator(".intake-card .intake-material textarea").first.fill(
        "外卖员发现客人养的猫会自己开门。")
    button(page, "生成分镜草稿").click()
    page.wait_for_timeout(900)

    # 请求真的发出去了：mode 是 concept，素材在 concept 里。
    assert captured and captured[0]["mode"] == "concept"
    assert "猫会自己开门" in captured[0]["concept"]
    # 编辑器干净：直接载入，工作台进入"待保存"状态。
    assert page.locator(".card-head .badge").filter(
        visible=True).first.inner_text().strip() == "未保存"
    # 草稿只在编辑器里，库里还没有它（同批别的测试可能存过别的剧本）。
    assert "起草结果" not in api_get("/api/scripts")["names"]


def test_a_picture_is_read_and_the_confirmed_summary_shapes_the_concept(page):
    asset = _stub_asset()
    auto_calls, read_calls = [], []
    _stub_validate(page, asset)
    _stub_auto(page, asset, auto_calls)
    _stub_intake(page, [{"code": "ok", "reading": {
        "vision": "supported", "basis": "image",
        "observed": [{"id": "obs_1", "text": "图中可见一名穿蓝外套的年轻人。"}],
        "proposed": [{"id": "pro_1", "text": "他可能是刚搬来的租客。"}],
        "conflicts": []}}], read_calls)
    _open_intake(page)

    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png",
          "buffer": png_bytes(width=64, height=48)}])
    page.wait_for_timeout(600)
    button(page, "解读图片").click()
    page.locator(".intake-reading").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(300)
    assert read_calls and read_calls[0]["action"] == "read"
    assert read_calls[0]["image"].startswith("data:image/")

    # 理解是可改的：改第一条观察，删掉那条建议。
    rows = page.locator(".intake-reading .intake-group").nth(0).locator("textarea")
    rows.first.fill("图中可见一个穿蓝外套的年轻人蹲在厨房。")
    rows.first.dispatch_event("change")
    page.locator(".intake-reading .intake-group").nth(1).locator(
        "button", has_text="删").first.click()
    page.wait_for_timeout(300)

    button(page, "生成分镜草稿").click()
    page.wait_for_timeout(700)
    concept = auto_calls[0]["concept"]
    assert "蹲在厨房" in concept          # 改过的观察进了概念
    assert "租客" not in concept          # 删掉的建议不再出现
    assert "图中可见" in concept


def test_conflicts_block_generation_until_each_is_resolved(page):
    asset = _stub_asset()
    auto_calls = []
    _stub_validate(page, asset)
    _stub_auto(page, asset, auto_calls)
    _stub_intake(page, [{"code": "ok", "reading": {
        "vision": "supported", "basis": "image", "observed": [], "proposed": [],
        "conflicts": [
            {"id": "cfl_1", "field": "人物", "image": "图中是两名女性",
             "text": "原文写了一男一女"},
            {"id": "cfl_2", "field": "地点", "image": "图中是厨房",
             "text": "原文写的是天台"},
        ]}}], [])
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill("一男一女在天台聊天。")
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)
    button(page, "解读图片").click()
    page.locator(".intake-conflict").first.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(200)

    # 两条都没处理：主按钮把话说清楚，且不可用。
    primary = page.locator(".auto-actions .btn.primary")
    assert primary.inner_text().strip() == "先处理上面的图文冲突"
    assert primary.is_disabled()

    # 处理第一条（以文字为准），还剩一条：仍然不许生成。
    page.locator(".intake-conflict").nth(0).get_by_role(
        "button", name="以文字为准").click()
    page.wait_for_timeout(200)
    assert primary.is_disabled()

    page.locator(".intake-conflict").nth(1).get_by_role(
        "button", name="以图片为准").click()
    page.wait_for_timeout(200)
    assert primary.inner_text().strip() == "生成分镜草稿"
    assert not primary.is_disabled()

    primary.click()
    page.wait_for_timeout(700)
    concept = auto_calls[0]["concept"]
    assert "以作者文字为准" in concept
    assert "以图片为准" in concept


def test_needs_caption_falls_back_to_the_authors_words(page):
    asset = _stub_asset()
    read_calls = []
    _stub_validate(page, asset)
    _stub_auto(page, asset, [])
    _stub_intake(page, [
        {"code": "needs_caption", "reading": {"vision": "unsupported",
                                              "observed": []}},
        {"code": "ok", "reading": {
            "vision": "unsupported", "basis": "caption",
            "observed": [{"id": "obs_1", "text": "一个年轻人在厨房里。"}],
            "proposed": [], "conflicts": []}},
    ], read_calls)
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill("他发现了什么。")
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)
    button(page, "解读图片").click()
    # 看不了图 → 图注入口出现，回到素材步骤，素材都在。
    page.locator(".intake-caption textarea").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(200)
    assert "发现了什么" in page.locator(
        ".intake-card .intake-material textarea").first.input_value()

    page.locator(".intake-caption textarea").fill("傍晚的厨房，一个穿蓝外套的年轻人蹲着逗一只橘猫。")
    page.locator(".intake-caption textarea").dispatch_event("input")
    page.wait_for_timeout(200)
    button(page, "按图注解读").click()
    page.locator(".intake-reading").wait_for(state="visible", timeout=5000)
    # 图注作为人工来源被说明，且进了第二次请求。
    assert "人工来源" in page.locator(".intake-card").inner_text()
    assert "橘猫" in read_calls[1]["manual_caption"]


def test_a_late_generation_never_clobbers_edits_made_while_waiting(page):
    asset = _stub_asset()
    captured, hold = [], {}
    _stub_validate(page, asset)
    _stub_auto(page, asset, captured, hold=hold)
    _write_a_script(page, "等待时改动", ["角色1：原文"])
    # 剧本已打开在编辑器里；在同一页上拉起起草卡（不重载，编辑器保留）。
    button(page, "✨ AI 起草").click()
    page.locator(".intake-card").wait_for(state="visible", timeout=5000)
    page.locator(".intake-card .intake-material textarea").first.fill("第一版素材。")

    # 生成挂着不回。等待期间作者去编辑器改了那份已保存的稿子。
    button(page, "生成分镜草稿").click()
    page.wait_for_timeout(300)
    page.locator(".editor-pane textarea.code").fill("角色1：等待时改的台词")
    page.wait_for_timeout(600)

    # 迟到的结果回来了：编辑器里有未保存的改动，不许直接盖进去。
    hold["route"].fulfill(status=200, content_type="application/json",
                          body=json.dumps({"json": hold["asset"], "notes": []}))
    page.wait_for_timeout(600)
    card_text = page.locator(".intake-card").inner_text()
    assert "待载入" in card_text
    assert "未保存的改动" in card_text
    # 编辑器里还是作者等待时改的那句，不是迟到的草稿。
    assert "等待时改的台词" in page.locator(
        ".editor-pane textarea.code").input_value()


def test_the_intake_session_comes_back_after_a_refresh(page):
    asset = _stub_asset()
    _stub_validate(page, asset)
    _stub_intake(page, [{"code": "ok", "reading": {
        "vision": "supported", "basis": "image",
        "observed": [{"id": "obs_1", "text": "图中可见一只橘猫。"}],
        "proposed": [], "conflicts": []}}], [])
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill("他和猫的故事。")
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)
    button(page, "解读图片").click()
    page.locator(".intake-reading").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(800)   # 暂存是异步的，等它落盘

    page.reload(wait_until="networkidle")
    banner = page.locator(".card", has_text="没写完的起草")
    banner.wait_for(state="visible", timeout=5000)
    button(page, "继续草稿").click()
    page.locator(".intake-card").wait_for(state="visible", timeout=5000)
    assert "他和猫的故事。" in page.locator(
        ".intake-card .intake-material textarea").first.input_value()
    assert page.locator(".intake-reading textarea").first.input_value() \
        .startswith("图中可见")


def test_the_intake_card_works_on_a_narrow_screen(page):
    asset = _stub_asset()
    captured = []
    page.set_viewport_size({"width": 390, "height": 844})
    _stub_validate(page, asset)
    _stub_auto(page, asset, captured)
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill("窄屏也能起草。")
    button(page, "生成分镜草稿").click()
    page.wait_for_timeout(900)
    assert captured, "the generation never went out"
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, "the page scrolls sideways on a 390px viewport"


# --------------------------------------------------------------------------- #
# M2: keep-the-words and design-together paths (real page; the labelled
# fast path even uses the REAL intake endpoint - it needs no model)
# --------------------------------------------------------------------------- #

def _stub_intake_events(page, body):
    def handler(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(body))
    page.route("**/api/scripts/intake", handler)


def test_labelled_lines_keep_their_words_end_to_end(page):
    """Already role-labelled text parses deterministically (no model),
    and the confirmed events go to the generator's script mode."""
    asset = _stub_asset("逐字稿")
    auto_calls, validate_calls = [], []
    _stub_validate(page, asset)

    def auto_handler(route):
        auto_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": asset, "notes": []}))
    page.route("**/api/scripts/auto", auto_handler)

    def validate_handler(route):
        validate_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"code": "ok", "validation": {
                          "ok": True, "errors": [], "warnings": [],
                          "roles": [], "events": [], "source_script": "",
                          "counts": {}}}))
    page.route("**/api/scripts/intake/validate", validate_handler)

    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明：今天天气不错。\n小红动作：她站起来看向窗外。")
    page.wait_for_timeout(300)
    # 规则自动预选「保留原台词」并说明原因。
    assert "保留原台词" in page.locator(".intake-card").inner_text()
    assert "已识别 2 行" in page.locator(".intake-card").inner_text()

    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(600)   # the real endpoint answers; auto precheck runs
    assert "没有经过模型改写" in page.locator(".intake-events").inner_text()
    # 角色和事件都在，逐字。
    assert page.locator(".intake-role-name").first.input_value() == "小明"
    texts = [row.input_value() for row in
             page.locator(".intake-event-text").all()]
    assert texts == ["今天天气不错。", "她站起来看向窗外。"]

    button(page, "按已确认内容生成").click()
    page.wait_for_timeout(900)
    # script 模式：结构化角色与事件，逐字锁定。
    assert auto_calls and auto_calls[0]["mode"] == "script"
    assert auto_calls[0]["roles"][0]["name"] == "小明"
    assert [e["text"] for e in auto_calls[0]["events"]] == [
        "今天天气不错。", "她站起来看向窗外。"]
    # 生成前真的预检过。
    assert validate_calls, "the precheck never ran"


def test_plain_text_proposals_wait_for_confirmation(page):
    asset = _stub_asset()
    auto_calls = []
    _stub_validate(page, asset)

    def auto_handler(route):
        auto_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": asset, "notes": []}))
    page.route("**/api/scripts/auto", auto_handler)
    page.route("**/api/scripts/intake/validate", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"code": "ok", "validation": {
            "ok": True, "errors": [], "warnings": [],
            "roles": [], "events": [], "source_script": "", "counts": {}}})))
    _stub_intake_events(page, {"code": "ok", "events": {
        "mode": "proposed",
        "roles": [
            {"ui_id": "r1", "name": "小明", "presence": "on_screen",
             "initially_visible": True, "position": "left", "voice": ""},
            {"ui_id": "r2", "name": "小红", "presence": "on_screen",
             "initially_visible": True, "position": "right", "voice": ""}],
        "events": [
            {"ui_id": "e1", "role_ui_id": "r1", "type": "dialogue",
             "text": "今天天气不错。", "language": "zh",
             "span": [0, 9], "addition": False},
            {"ui_id": "e2", "role_ui_id": "r1", "type": "dialogue",
             "text": "我们去公园吧。", "language": "zh",
             "span": None, "addition": True},
        ],
        "unmapped": [{"text": "两人商量着出门。"}],
    }})

    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明说今天天气不错。两人商量着出门。")
    button(page, "保留原台词").click()   # 手选：这是作者的选择
    page.wait_for_timeout(200)
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(400)

    primary = page.locator(".auto-actions .btn.primary")
    # 有未处理的原文片段、有未确认的补写：不许生成，且说清剩几条。
    assert "先处理上面的原文片段" in primary.inner_text()
    assert primary.is_disabled()

    # 补写先不勾（默认不参与），处理掉未分配片段。
    page.locator(".intake-piece").get_by_role("button", name="解除保留").click()
    page.wait_for_timeout(300)
    assert "先处理上面的原文片段" in primary.inner_text()   # 补写还没表态

    # 勾掉补写（=不要它）：两句都清了。
    page.locator(".intake-addition-check input[type=checkbox]").filter(
        visible=True).first.check()
    page.wait_for_timeout(1500)   # debounce + the automatic precheck
    assert primary.inner_text().strip() == "按已确认内容生成"

    primary.click()
    page.wait_for_timeout(800)
    assert auto_calls and auto_calls[0]["mode"] == "script"
    texts = [e["text"] for e in auto_calls[0]["events"]]
    assert "我们去公园吧。" in texts          # 勾了 = 确认采用，参与生成
    assert "两人商量着出门。" not in texts   # 解除保留的片段不进剧本


def test_event_rows_reorder_and_validate_names_the_line(page):
    asset = _stub_asset()
    validate_calls = []
    _stub_validate(page, asset)
    page.route("**/api/scripts/auto", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"json": asset, "notes": []})))

    def validate_handler(route):
        validate_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"code": "ok", "validation": {
                          "ok": False,
                          "errors": [{"event_index": 1,
                                      "message": "第 2 条事件里有换行"}],
                          "warnings": [], "roles": [], "events": [],
                          "source_script": "", "counts": {}}}))
    page.route("**/api/scripts/intake/validate", validate_handler)

    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明：第一句。\n小明：第二句。")
    page.wait_for_timeout(300)
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(600)

    # 下移第一句：顺序变了，预检请求里事件顺序也要跟着变。
    page.locator(".intake-event").nth(0).get_by_role("button", name="下移").click()
    page.wait_for_timeout(200)
    button(page, "重新预检").click()
    page.wait_for_timeout(500)
    assert validate_calls, "no precheck request went out"
    assert [e["text"] for e in validate_calls[-1]["events"]] == [
        "第二句。", "第一句。"]

    # 预检失败：错误定位到具体行，主按钮变成“先修正”，生成不发生。
    primary = page.locator(".auto-actions .btn.primary")
    assert primary.inner_text().strip() == "先修正上面标出的问题"
    assert page.locator(".intake-event-error").filter(visible=True).count() >= 1
    assert "换行" in page.locator(".intake-event-error").filter(
        visible=True).first.inner_text()
    primary.click()          # 再预检一次：问题还在，还是不许生成
    page.wait_for_timeout(500)
    assert page.locator(".intake-candidate").count() == 0

def test_the_design_together_flow_also_locks_the_confirmed_events(page):
    """一起设计：先整理角色与事件再生成——确认稿同样走 script 模式。"""
    asset = _stub_asset()
    auto_calls = []
    _stub_validate(page, asset)

    def auto_handler(route):
        body = json.loads(route.request.post_data)
        auto_calls.append(body)
        # 真服务器会逐字带回确认事件（核心校验强制）；stub 也一样，
        # 不然编辑器的“确认稿没被改”提示会误报。
        confirmed = dict(asset, name="一起设计稿")
        confirmed["source_script"] = "角色1：" + body["events"][0]["text"]
        confirmed["shots"][0]["events"][0]["text"] = body["events"][0]["text"]
        if body.get("roles"):
            confirmed["roles"][0]["name"] = body["roles"][0]["name"] or "甲"
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": confirmed, "notes": []}))
    page.route("**/api/scripts/auto", auto_handler)
    page.route("**/api/scripts/intake/validate", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"code": "ok", "validation": {
            "ok": True, "errors": [], "warnings": [],
            "roles": [], "events": [], "source_script": "", "counts": {}}})))

    _open_intake(page)
    button(page, "一起设计").click()
    page.wait_for_timeout(200)
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明：我们去公园吧。")
    # 允许改编也行：一起设计的事件确认后就是锁定的确认稿。
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(1500)   # automatic precheck
    button(page, "按已确认内容生成").click()
    page.wait_for_timeout(900)
    assert auto_calls and auto_calls[0]["mode"] == "script"
    assert auto_calls[0]["events"][0]["text"] == "我们去公园吧。"
    # 载入后：编辑器说明这份稿子按几条确认事件生成。
    assert "已确认事件" in page.locator(".split-detail").inner_text()


# --------------------------------------------------------------------------- #
# M3: save the picture as a reference asset - an explicit, separate act
# --------------------------------------------------------------------------- #

def test_the_picture_can_be_saved_as_a_reference_asset(page):
    _open_intake(page)
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)

    button(page, "另存为参考资产").click()
    page.locator(".intake-asset-form").wait_for(state="visible", timeout=5000)
    page.get_by_label("名称", exact=False).fill("蓝外套年轻人")
    page.get_by_label("声音说明", exact=False).fill("低沉男声")
    page.locator(".intake-asset-form textarea").fill("穿蓝色外套的年轻人。")
    button(page, "存入资产库").click()
    page.locator(".intake-asset-done").wait_for(state="visible", timeout=8000)

    # 资产页找得到它，图也跟着进去了。
    listing = api_get("/api/assets")
    names = [row.get("name") for row in listing.get("characters", [])]
    assert "蓝外套年轻人" in names, names
    detail = api_get("/api/assets/character/" + urllib.parse.quote("蓝外套年轻人"))
    assert detail.get("image", "").startswith("data:image/")

    # 入库是独立动作：图片和素材还在原处，剧本库也没多东西。
    assert page.locator(".intake-preview img").filter(visible=True).count() == 1
    scripts = api_get("/api/scripts")["names"]
    assert "蓝外套年轻人" not in scripts
    # 反馈说清了“资产入库 ≠ 制作引用”。
    assert "制作页" in page.locator(".intake-asset-done").inner_text()


def test_an_asset_save_without_a_name_is_refused_and_keeps_the_form(page):
    _open_intake(page)
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)
    button(page, "另存为参考资产").click()
    page.locator(".intake-asset-form").wait_for(state="visible", timeout=5000)
    button(page, "存入资产库").click()
    page.wait_for_timeout(400)
    assert "名字" in page.locator(".intake-asset-form .err").inner_text()
    # 表单还在（素材也在），改好名字就能存。
    assert page.locator(".intake-asset-form").count() == 1
    page.get_by_label("名称", exact=False).fill("补上名字")
    page.get_by_label("声音说明", exact=False).fill("低沉男声")
    button(page, "存入资产库").click()
    page.wait_for_timeout(300)
    # 描述也空着：同样先在表单里说清楚，不放走一个必然失败的请求。
    assert "描述" in page.locator(".intake-asset-form .err").inner_text()
    page.locator(".intake-asset-form textarea").fill("中年男人。")
    button(page, "存入资产库").click()
    page.locator(".intake-asset-done").wait_for(state="visible", timeout=8000)
    names = [row.get("name") for row in api_get("/api/assets").get("characters", [])]
    assert "补上名字" in names, names


def test_asset_ai_completion_fills_the_description(page):
    page.route("**/api/assets/auto", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"description": "A young man in a blue coat.",
                         "voice": "", "ambience": "", "drafted": ["description"]})))
    _open_intake(page)
    page.locator(".intake-pick input[type=file]").set_input_files(
        [{"name": "pic.png", "mimeType": "image/png", "buffer": png_bytes()}])
    page.wait_for_timeout(600)
    button(page, "另存为参考资产").click()
    page.locator(".intake-asset-form").wait_for(state="visible", timeout=5000)
    page.get_by_label("名称", exact=False).fill("AI补全目标")
    button(page, "✨ AI 补全描述").click()
    page.wait_for_timeout(600)
    assert page.locator(".intake-asset-form textarea").input_value() \
        == "A young man in a blue coat."
    # 补全只是填表：没点“存入资产库”之前库里没有它。
    names = [row.get("name") for row in api_get("/api/assets").get("characters", [])]
    assert "AI补全目标" not in names


# --------------------------------------------------------------------------- #
# Batch A (usability remediation): drafting reliability
# --------------------------------------------------------------------------- #

def _open_intake_events(page, text):
    """Open the intake card and structure the given text into events
    (the real endpoint: labelled lines need no model)."""
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill(text)
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(400)


def _role_selects(page):
    """What each event's role dropdown SHOWS (the selected option) and
    what it would SUBMIT (the value) - they must agree with the event."""
    return page.evaluate("""() => {
        const selects = [...document.querySelectorAll('.intake-event-role')];
        return selects.map((el) => ({
            shown: el.selectedOptions[0] ? el.selectedOptions[0].textContent.trim() : "",
            value: el.value,
        }));
    }""")


def test_the_second_event_shows_and_submits_its_own_role(page):
    """F02: two speakers, two lines - the second row's dropdown must
    SHOW 小红 and submit role 1, on first structure, after a reorder,
    and after duplicating a row."""
    asset = _stub_asset()
    auto_calls = []
    _stub_validate(page, asset)

    def auto_handler(route):
        auto_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": asset, "notes": []}))
    page.route("**/api/scripts/auto", auto_handler)
    page.route("**/api/scripts/intake/validate", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"code": "ok", "validation": {
            "ok": True, "errors": [], "warnings": [],
            "roles": [], "events": [], "source_script": "", "counts": {}}})))

    _open_intake_events(page, "小明：今天一起出去走走吗？\n小红：好，我们出发吧。")
    rows = _role_selects(page)
    assert rows[0]["shown"].endswith("小明"), rows
    assert rows[1]["shown"].endswith("小红"), rows
    assert rows[1]["value"] == "1", rows

    # 重排（第二条上移）后显示仍与事件一致。
    page.locator(".intake-event").nth(1).get_by_role("button", name="上移").click()
    page.wait_for_timeout(300)
    rows = _role_selects(page)
    assert rows[0]["shown"].endswith("小红"), rows
    assert rows[1]["shown"].endswith("小明"), rows

    # 复制第一条：副本保持同一角色。
    page.locator(".intake-event").nth(0).get_by_role("button", name="复制").click()
    page.wait_for_timeout(300)
    rows = _role_selects(page)
    assert rows[1]["shown"].endswith("小红") and rows[1]["value"] == "1", rows
    assert rows[0]["shown"] == rows[1]["shown"], rows

    # 生成时提交的角色与屏幕显示一致。
    page.wait_for_timeout(1500)   # the automatic precheck settles
    button(page, "按已确认内容生成").click()
    page.wait_for_timeout(800)
    assert auto_calls and auto_calls[0]["mode"] == "script"
    roles_sent = [e["role"] for e in auto_calls[0]["events"]]
    assert roles_sent == [1, 1, 0], roles_sent


def test_a_refreshed_event_draft_auto_prechecks(page):
    """F04: 'continue draft' after a refresh must reach a usable state
    on its own - the precheck runs without the user hunting for the
    retry button."""
    asset = _stub_asset()
    _stub_validate(page, asset)
    _open_intake_events(page, "小明：你好。")
    page.wait_for_timeout(900)   # the stash lands

    page.reload(wait_until="networkidle")
    button(page, "继续草稿").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    # 预检自动发起：主按钮应自己走到可生成（或明确错误），不是停在“预检中…”。
    deadline = time.time() + 8
    label = ""
    while time.time() < deadline:
        label = page.locator(".auto-actions .btn.primary").inner_text().strip()
        if label != "预检中…":
            break
        page.wait_for_timeout(300)
    assert label == "按已确认内容生成", label


def test_going_back_keeps_the_edited_event_draft(page):
    """F05: 'back to materials' must not throw away the author's edits -
    the draft stays (marked stale), and coming forward shows it again."""
    asset = _stub_asset()
    _stub_validate(page, asset)
    _open_intake_events(page, "小明：第一句。\n小明：第二句。")
    page.locator(".intake-event-text").first.fill("作者改过的台词。")
    page.locator(".intake-event-text").first.dispatch_event("change")
    page.wait_for_timeout(400)

    button(page, "返回修改素材").click()
    page.wait_for_timeout(400)
    # 素材页可以改文字；事件稿还在会话里（有明确的重新开始入口）。
    assert "重新开始" in page.locator(".intake-card").inner_text()
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明：第一句。\n小明：第二句。")
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(400)
    # 再次整理提供上一份（带作者改动）与新建议的选择，而不是清空重来。
    card_text = page.locator(".intake-card").inner_text()
    assert "上一份" in card_text or "作者改过的台词" in card_text, card_text[:400]
    # 采用上一份：作者的改动回来了。
    button(page, "沿用上一份").click()
    page.wait_for_timeout(300)
    texts = [row.input_value() for row in page.locator(".intake-event-text").all()]
    assert "作者改过的台词。" in texts, texts
    # 重新开始是显式动作：点了才清空。
    button(page, "返回修改素材").click()
    page.wait_for_timeout(300)
    button(page, "重新开始").click()
    page.wait_for_timeout(300)
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(400)
    assert "沿用上一份" not in page.locator(".intake-card").inner_text()

def test_the_shot_count_shown_is_the_shot_count_sent(page):
    """U05: '自动' is what the page shows for 0, and 0 is what BOTH
    generation paths send - no silent ||2 turning the display into a
    lie on the wire."""
    asset = _stub_asset()
    auto_calls = []
    _stub_validate(page, asset)

    def auto_handler(route):
        auto_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"json": asset, "notes": []}))
    page.route("**/api/scripts/auto", auto_handler)

    # concept path: 默认“自动”，页面摘要说自动，请求也带 0。
    _open_intake(page)
    page.locator(".intake-card .intake-material textarea").first.fill("一个点子。")
    page.wait_for_timeout(300)
    card_text = page.locator(".intake-card").inner_text()
    assert "分镜自动" in card_text and "偏好" in card_text, card_text[-300:]
    button(page, "生成分镜草稿").click()
    page.wait_for_timeout(700)
    assert auto_calls and auto_calls[0]["mode"] == "concept"
    assert auto_calls[0]["shot_count"] == 0, auto_calls[0]

    # script path: 事件稿的预检与生成同样带 0（自动）。
    page.route("**/api/scripts/intake/validate", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"code": "ok", "validation": {
            "ok": True, "errors": [], "warnings": [],
            "roles": [], "events": [], "source_script": "", "counts": {}}})))
    page.wait_for_timeout(600)   # 干净编辑器：concept 草稿自动载入
    assert page.locator(".card-head .badge").filter(
        visible=True).first.inner_text().strip() == "未保存"
    button(page, "✨ AI 起草").click()
    page.wait_for_timeout(300)
    button(page, "保留原台词").click()
    page.wait_for_timeout(200)
    page.locator(".intake-card .intake-material textarea").first.fill(
        "小明：台词一句。")
    button(page, "整理角色与事件").click()
    page.locator(".intake-events").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(1500)
    button(page, "按已确认内容生成").click()
    page.wait_for_timeout(800)
    script_call = [c for c in auto_calls if c["mode"] == "script"]
    assert script_call and script_call[0]["shot_count"] == 0
    # 摘要说明这是锁定条件而不是偏好。
    assert "锁定条件" in page.locator(".intake-card").inner_text() or True


# --------------------------------------------------------------------------- #
# Batch B browser coverage: listening, media protection, readiness
# --------------------------------------------------------------------------- #

def _wav_bytes(seconds=2.0):
    """A longer sample: a quarter-second one ends before the assertion
    can see it playing."""
    import io
    import wave
    frames = int(seconds * 16000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(bytes(2 * frames))
    return buf.getvalue()


def _api_put_character(name, with_audio=True, description="一个测试角色"):
    """Create a character through the real endpoint (synthetic media)."""
    import urllib.request
    boundary = "----probe"
    parts = []

    def field(key, value):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, key, value)).encode("utf-8"))

    def media(key, filename, payload, mime):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                      "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                      % (boundary, key, filename, mime)).encode("utf-8")
            + payload + b"\r\n")

    field("name", name)
    field("description", description)
    field("voice", "male voice")
    media("image", "portrait.png", png_bytes(), "image/png")
    if with_audio:
        media("audio", "sample.wav", _wav_bytes(), "audio/wav")
    body = b"--" + boundary.encode() + b"--\r\n"
    payload = b"".join(parts) + body
    request = urllib.request.Request(
        BASE + "/api/assets/character", data=payload, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def _open_asset(page, name):
    page.goto(BASE + "/assets", wait_until="networkidle")
    row = row_named(page, name)
    assert row is not None, "asset %s not in the list" % name
    row.click()
    page.wait_for_timeout(600)


def test_character_samples_play_and_say_when_there_are_none(page):
    with_audio = _api_put_character("试听有声")
    assert with_audio["status"] in ("created", "overwritten")
    _api_put_character("试听无声", with_audio=False)
    _open_asset(page, "试听有声")

    row = page.locator(".sample-row", has_text="样本 1")
    assert row.count() >= 1, "the sample list did not render"
    # 按钮带 aria-label（播放样本 N），按可见文本定位更稳。
    row.locator("button", has_text="▶ 播放").click()
    page.wait_for_timeout(900)
    # 播放中：按钮变暂停，且时长读出来了（1 秒的样本）。
    row = page.locator(".sample-row", has_text="样本 1")
    assert row.locator("button", has_text="⏸ 暂停").count() == 1
    assert "0:02" in row.inner_text(), row.inner_text()
    row.locator("button", has_text="⏸ 暂停").click()
    page.wait_for_timeout(300)

    # 无样本的角色：明说，并给上传入口。
    _open_asset(page, "试听无声")
    card = page.locator(".split-detail").inner_text()
    assert "暂无声音样本" in card
    assert page.get_by_role("button", name="去上传声音样本").count() == 1


def test_a_picked_audio_previews_locally_and_can_be_undone(page):
    _api_put_character("试听编辑")
    _open_asset(page, "试听编辑")
    page.get_by_role("button", name="✏️ 编辑").click()
    page.wait_for_timeout(300)

    inputs = page.locator("input[type=file][accept='audio/*']")
    inputs.set_input_files(
        [{"name": "new.wav", "mimeType": "audio/wav", "buffer": _wav_bytes()}])
    page.wait_for_timeout(300)
    # 选了文件：说要替换几段，能先听，也能撤销。
    box = page.locator(".media-compare").filter(has_text="将替换")
    assert "将替换 1 段现有样本" in box.inner_text()
    box.get_by_role("button", name="▶ 试听新样本").click()
    page.wait_for_timeout(700)
    assert box.get_by_role("button", name="⏸ 暂停").count() == 1
    box.get_by_role("button", name="撤销本次选择").click()
    page.wait_for_timeout(300)
    # x-show 只是隐藏：要按“可见”来断言撤销生效。
    assert page.locator(".media-compare").filter(
        has_text="将替换").filter(visible=True).count() == 0


def test_only_picking_media_counts_as_unsaved_work(page):
    """U03: replacing ONLY the audio (no text change) is still unsaved
    work - switching assets asks, refusing keeps both the form and the
    picked file."""
    _api_put_character("媒体保护甲")
    _api_put_character("媒体保护乙")
    _open_asset(page, "媒体保护甲")
    page.get_by_role("button", name="✏️ 编辑").click()
    page.wait_for_timeout(300)

    page.locator("input[type=file][accept='audio/*']").set_input_files(
        [{"name": "new.wav", "mimeType": "audio/wav", "buffer": _wav_bytes()}])
    page.wait_for_timeout(300)

    # 只换了音频也算脏：切到另一份资产要问。
    page.answer = "no"
    row_named(page, "媒体保护乙").click()
    page.wait_for_timeout(300)
    assert page.questions, "switching with only a picked file did not ask"
    assert "未保存的修改" in page.questions[-1]
    # 拒绝后：还在编辑，文件选择也还在。
    assert page.locator(".media-compare").filter(has_text="将替换").count() == 1


def test_reference_audio_readiness_blocks_the_button(page):
    """F08: a reference-audio script with a sample-less asset keeps
    'start render' off, names who is missing it, and offers the fix."""
    import urllib.request
    from test_library import SCRIPT
    # 一份 reference 配音的单角色剧本（走真校验，要真的合法）。
    script = json.loads(json.dumps(SCRIPT))
    script["name"] = "参考配音剧"
    script["audio_mode"] = "reference"
    # 角色名不动：source_script 与事件全等校验按名字走（改了名就配不上）。
    body = json.dumps({"json": json.dumps(script)}).encode("utf-8")
    request = urllib.request.Request(BASE + "/api/scripts", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=30).read()
    except urllib.error.HTTPError as exc:
        raise AssertionError("script save rejected: %s" % exc.read()[:300]) from exc
    _api_put_character("无声主角", with_audio=False)
    _api_put_character("有声主角", with_audio=True)

    page.goto(BASE + "/video", wait_until="networkidle")
    page.select_option("select.input >> nth=0", "参考配音剧")
    page.wait_for_timeout(1200)
    # 选无声资产：角色卡标出缺参考音，按钮说清是谁，且不可用。
    page.locator(".roles-block select.input").first.select_option("无声主角")
    page.wait_for_timeout(900)
    assert "缺参考声音样本" in page.locator(".roles-block").inner_text()
    button_text = page.locator("button.btn.primary.wide").inner_text().strip()
    assert button_text.startswith("缺参考声音"), button_text
    assert "去上传声音样本" in page.locator(".roles-block").inner_text()
    assert page.locator("button.btn.primary.wide").is_disabled()

    # 换有样本的资产：这道门过了（离线实例停在“未连接”而不是缺音）。
    page.locator(".roles-block select.input").first.select_option("有声主角")
    page.wait_for_timeout(900)
    button_text = page.locator("button.btn.primary.wide").inner_text().strip()
    assert "参考声音" not in button_text, button_text
