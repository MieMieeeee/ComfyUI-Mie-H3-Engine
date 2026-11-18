"""The gaps the 2026-11-15 handoff asked for (V4).

Old records, lost drafts, a long history, the keyboard, and a 320px
screen.  Everything here runs against a throwaway plugin root - the
real library, the real job log and the real ComfyUI output are never
touched.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBAPP_MAIN = PLUGIN_ROOT / "webapp" / "__main__.py"
# Not 8200 (the person's workbench) and not 8299 (the other browser
# suite's server): this file gets its own.
PORT = 8297
BASE = "http://127.0.0.1:%d" % PORT
STASH_KEY = "storyboard-intake:v1"
SESSION_KEY = "mie-video-session:v1"


def png_bytes(width: int = 4, height: int = 4) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def wav_bytes(seconds: float = 0.2, rate: int = 16000) -> bytes:
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(2 * int(seconds * rate)))
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


def job_record(number: int, run_name: str = "", **fields) -> dict:
    record = {
        "job_id": "job-%03d" % number,
        "number": number,
        "script": "剧本%03d" % number,
        "run_name": run_name or "sb_%03d" % number,
        "title": "剧本%03d · 第 1 版" % number,
        "attempt": 1,
        "continued": False,
        "submitted_at": 1700000000.0 + number,
        "status": "success",
        "error": "",
        "nodes": 33,
        "role_assets": {"character_1": "甲"},
        "params": {"width": 960, "height": 544, "steps": 20, "duration": 12},
    }
    record.update(fields)
    return record


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    """A workbench on a throwaway plugin root, with a long history."""
    root = tmp_path_factory.mktemp("plugin")
    (root / "asset_library" / "characters").mkdir(parents=True)
    (root / "asset_library" / "scenes").mkdir(parents=True)
    (root / "asset_library" / "storyboards").mkdir(parents=True)
    run = root / "comfyui" / "output" / "h3_chains"
    (run / "sb_demo" / "final").mkdir(parents=True)
    (run / "sb_demo" / "final" / "sb_demo.mp4").write_bytes(b"0")
    # 200 records: the history has to stay quick when it is long.
    records = []
    for number in range(1, 201):
        run_name = "sb_%03d" % number if number % 3 else "sb_multi_%03d" % (number // 3)
        records.append(job_record(number, run_name))
    # One old record, written before the scene reference existed.
    records.append(job_record(500, "sb_old", status="success"))
    (root / "jobs.json").write_text(
        json.dumps({"jobs": records}), encoding="utf-8")
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
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"])
        screen = browser.new_page(viewport={"width": 1500, "height": 1000})
        screen.on("dialog", lambda dialog: dialog.accept())
        yield screen
        browser.close()


def visible(locator):
    return locator.filter(visible=True)


def api_get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read())


# --------------------------------------------------------------------------- #
# compatibility: records written before the scene reference existed
# --------------------------------------------------------------------------- #

def test_an_old_record_without_a_scene_asset_still_shows_in_the_works_view(web):
    """An old job record has no scene_asset field.  It must still be
    found, grouped and listed - the missing field is not a crash."""
    data = api_get("/api/render/jobs?grouped=1&per_page=200")
    names = [work["run_name"] for work in data["groups"]]
    assert "sb_old" in names, "旧记录没出现在作品视图里"
    work = [w for w in data["groups"] if w["run_name"] == "sb_old"][0]
    assert work["attempts"], "旧记录没有带出它的尝试"
    assert work["latest_status"] == "success"
    # 页面读到的是没有 scene_asset 的记录：不能是 undefined 崩掉。
    assert work["attempts"][0].get("scene_asset") in (None, "")


def test_the_scene_reference_of_a_record_survives_a_restart(plugin_root):
    """A record that names a scene asset must still name it after the
    workbench was restarted - otherwise "从这次继续" and "用本次配置再做
    一版" silently drop the scene the author picked.

    (Found by the 2026-11-15 verification run: the persistent job
    record dropped the field, so a restart lost the scene. Fixed - the
    field is saved with the record now.)"""
    from webapp import config
    from webapp.services import runner

    record = job_record(900, "sb_scene_ref", scene_asset="咖啡厅")
    with runner._jobs_lock:
        runner._jobs[record["job_id"]] = record
    runner._save_jobs()
    try:
        on_disk = json.loads(pathlib.Path(config.JOBS_FILE).read_text(
            encoding="utf-8"))
        saved = [j for j in on_disk["jobs"]
                 if j.get("job_id") == "job-900"][0]
        assert saved.get("scene_asset") == "咖啡厅", (
            "落盘的记录里没有 scene_asset：重启后继续/再做一版就没有场景了")
    finally:
        with runner._jobs_lock:
            runner._jobs.pop("job-900", None)
        runner._save_jobs()


def test_the_history_of_two_hundred_records_pages_and_answers_quickly(web):
    """200+ records: the grouped view must answer in well under a
    second and must not walk the output tree for every page."""
    started = time.time()
    data = api_get("/api/render/jobs?grouped=1&per_page=20")
    elapsed = time.time() - started
    assert elapsed < 1.0, "作品视图用了 %.2fs 才回答" % elapsed
    assert data["total"] > 150, data["total"]
    assert len(data["groups"]) <= 20, "一页给了太多作品"
    assert data["pages"] >= 2, "200 条记录只分了一页？"
    second = api_get("/api/render/jobs?grouped=1&per_page=20&page=2")
    assert second["page"] == 2
    first_names = {w["run_name"] for w in data["groups"]}
    second_names = {w["run_name"] for w in second["groups"]}
    assert not (first_names & second_names), "两页给了同一批作品"


# --------------------------------------------------------------------------- #
# recovery: drafts that come back, and drafts that should not
# --------------------------------------------------------------------------- #

def _stash(page, meta):
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.evaluate(
        "([key, value]) => window.localStorage.setItem(key, value)",
        [STASH_KEY, json.dumps(meta, ensure_ascii=False)])


def test_an_old_draft_without_a_schema_version_is_ignored(page):
    """A draft from before the stash had a version must not be offered
    as if it were a real one - it is ignored, and the page still works."""
    _stash(page, {"updated_at": time.time() * 1000, "text": "老版本的草稿",
                  "flowMode": "direct", "textPolicy": "adapt"})
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(1200)
    assert page.locator("text=有一份没写完的起草").count() == 0, \
        "没有 schema_version 的旧草稿被当成了可继续的会话"
    # 页面本身照常工作。
    assert page.locator(".panel").count() == 1


def test_a_draft_older_than_seven_days_is_dropped(page):
    """Seven days without a touch: the draft is cleaned up, not offered."""
    _stash(page, {"schema_version": 1,
                  "updated_at": (time.time() - 8 * 24 * 3600) * 1000,
                  "text": "八天前的草稿", "flowMode": "direct",
                  "textPolicy": "adapt"})
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(1200)
    assert page.locator("text=有一份没写完的起草").count() == 0, \
        "过期七天的草稿还在等人继续"
    kept = page.evaluate("key => window.localStorage.getItem(key)", STASH_KEY)
    assert kept is None, "过期草稿没有被清掉"


def test_a_stashed_draft_comes_back_with_its_text(page):
    _stash(page, {"schema_version": 1, "updated_at": time.time() * 1000,
                  "text": "回来之后我还在", "flowMode": "direct",
                  "textPolicy": "adapt", "step": "material"})
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(1200)
    assert page.locator("text=有一份没写完的起草").count() == 1
    page.get_by_role("button", name="继续草稿").click()
    page.wait_for_timeout(1200)
    assert page.locator(".intake-card").is_visible()
    assert page.locator(".intake-material textarea").first.input_value() \
        == "回来之后我还在"


def test_a_missing_stashed_image_restores_the_text_and_says_the_image_is_gone(page):
    """The picture lives in IndexedDB and the text in localStorage.  When
    the picture is gone, the text must come back AND the page must say
    the picture is gone - not quietly pretend there never was one."""
    _stash(page, {"schema_version": 1, "updated_at": time.time() * 1000,
                  "text": "文字还在", "flowMode": "direct",
                  "textPolicy": "adapt", "step": "material",
                  "reading": {"vision": "supported", "basis": "image",
                              "observed": [], "proposed": [], "conflicts": []}})
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="继续草稿").click()
    page.wait_for_timeout(1500)
    assert page.locator(".intake-material textarea").first.input_value() \
        == "文字还在", "文字没有回来"
    card = page.locator(".intake-card").inner_text()
    assert "图没了" in card, \
        "图片没了，页面没有明说：%s" % card[:300]


def test_the_intake_stash_and_the_video_session_do_not_collide(page):
    """Two different drafts, two different keys: restoring one must not
    wipe the other."""
    _stash(page, {"schema_version": 1, "updated_at": time.time() * 1000,
                  "text": "起草这边的", "flowMode": "direct",
                  "textPolicy": "adapt", "step": "material"})
    page.goto(BASE + "/video", wait_until="networkidle")
    page.evaluate(
        "([key, value]) => window.localStorage.setItem(key, value)",
        [SESSION_KEY, json.dumps({"script": "剧本甲", "roles": [],
                                  "sceneAsset": "咖啡厅", "width": 960,
                                  "height": 544, "steps": 20, "duration": 18,
                                  "savedAt": 1})])
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(1000)
    session = page.evaluate("key => window.localStorage.getItem(key)",
                            SESSION_KEY)
    stash = page.evaluate("key => window.localStorage.getItem(key)", STASH_KEY)
    assert session is not None, "制作页的会话被起草的暂存冲掉了"
    assert stash is not None, "起草的暂存不见了"
    assert json.loads(session)["sceneAsset"] == "咖啡厅"
    assert json.loads(stash)["text"] == "起草这边的"


def test_the_scene_asset_reaches_the_sampler_as_a_reference_picture(plugin_root):
    """A wired scene asset must reach the sampler as a reference picture.

    (Found by the 2026-11-15 verification run: the plan said
    "<Picture 3>" was the scene, but the sampler had only the two
    characters, so the scene reference never reached the model and the
    shot had no environment at all. Fixed: the scene is the last picture
    in the slot numbering and is wired to the sampler.)"""
    """A wired scene asset must reach the sampler as a reference picture:
    the engine's plan text calls it <Picture N>, so the sampler has to
    carry that picture, or the promise is a lie."""
    from webapp.services import workflow

    script = {"roles": [{"id": "character_1", "name": "甲",
                          "presence": "on_screen"},
                         {"id": "character_2", "name": "乙",
                          "presence": "on_screen"}],
              "shots": [], "language": "zh"}
    graph = workflow.build("剧本", script,
                           {"character_1": "甲", "character_2": "乙"},
                           run_name="sb_scene_probe", scene_asset="咖啡厅")
    sampler = next(node for node in graph.values()
                   if node["class_type"] == "MiniMaxH3ReferenceToVideo")
    pictures = sorted(key for key in sampler["inputs"]
                      if key.startswith("ref_images.ref_image_"))
    assert "ref_images.ref_image_2" in pictures, (
        "场景的图没有接进采样器，参考图槽只有：%s" % pictures)


# --------------------------------------------------------------------------- #
# stress
# --------------------------------------------------------------------------- #

def test_a_two_hundred_event_draft_prechecks_quickly(client):
    """200 events is the contract's ceiling.  The pre-check must answer
    in well under a second, or the page feels broken at the limit."""
    events = [{"role": 0, "type": "dialogue", "text": "第%d句。" % index,
               "language": "zh"} for index in range(1, 201)]
    body = {"request_id": "req-stress", "revision": 1,
            "name": "两百句", "roles": [{"name": "甲", "presence": "on_screen",
                                        "initially_visible": True,
                                        "position": "center", "voice": ""}],
            "events": events, "language": "zh", "audio_mode": "generated",
            "shot_count": 0, "target_duration_seconds": 300}
    started = time.time()
    response = client.post("/api/scripts/intake/validate", json=body)
    elapsed = time.time() - started
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload.get("code") == "ok", payload
    validation = payload.get("validation") or {}
    assert validation.get("ok") is True, validation
    assert elapsed < 2.0, "200 条事件的预检用了 %.2fs" % elapsed


def _make_character(root, name, description, voice, images=(), audios=()):
    """Write a character straight into the throwaway library."""
    folder = pathlib.Path(root) / "asset_library" / "characters" / name
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "audio").mkdir(parents=True, exist_ok=True)
    for index, data in enumerate(images, start=1):
        (folder / "images" / ("%02d.png" % index)).write_bytes(data)
    for index, data in enumerate(audios, start=1):
        (folder / "audio" / ("%02d.wav" % index)).write_bytes(data)
    (folder / "character.json").write_text(json.dumps(
        {"name": name, "description": description, "voice": voice},
        ensure_ascii=False), encoding="utf-8")
    return folder


def test_the_sample_order_matches_the_order_the_engine_loads(page, web):
    """A character with several voice samples: the page must offer them
    in the same order the engine loads them - a swapped pair is a
    different voice on the wrong line."""
    _make_character(web, "样本顺序", "两段样本", "voice",
                    images=(png_bytes(),),
                    audios=(wav_bytes(0.1), wav_bytes(0.2, rate=8000)))
    detail = api_get("/api/assets/character/" + urllib.parse.quote("样本顺序"))
    clips = detail.get("audios") or []
    assert len(clips) == 2, "这个角色应该有 2 段样本：%s" % clips
    indexes = [clip.get("index") for clip in clips]
    assert indexes == sorted(indexes), "引擎读到的顺序本身就不对：%s" % indexes

    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="角色", exact=True).click()
    page.wait_for_timeout(800)
    row = None
    for candidate in visible(page.locator(".list-row")).all():
        if "样本顺序" in candidate.inner_text():
            row = candidate
            break
    assert row is not None, "资产列表里没有这个角色"
    row.click()
    page.wait_for_timeout(1200)
    labels = [button.get_attribute("aria-label")
              for button in visible(page.locator(".sample-list button")).all()
              if (button.get_attribute("aria-label") or "").startswith("播放样本")]
    assert labels, "页面上没有列出声音样本"
    shown = [int(label.replace("播放样本", "")) for label in labels]
    assert shown == indexes, \
        "页面上的样本顺序和引擎读到的顺序不一样：%s vs %s" % (shown, indexes)


# --------------------------------------------------------------------------- #
# keyboard
# --------------------------------------------------------------------------- #

def test_the_intake_can_be_driven_with_the_keyboard_alone(page):
    """起草 → 整理角色与事件, without the mouse."""
    page.goto(BASE + "/scripts", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.get_by_role("button", name="✨ AI 起草").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(1000)
    assert page.locator(".intake-card").is_visible()

    # Tab into the text area and type.
    page.locator(".intake-material textarea").first.focus()
    page.keyboard.type("角色1：你好")
    page.keyboard.press("Tab")
    page.wait_for_timeout(300)

    # The primary button is reachable by keyboard and reacts to Enter.
    page.get_by_role("button", name="整理角色与事件").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    card = page.locator(".intake-card").inner_text()
    assert "角色" in card, "键盘没有把事件稿整理出来"


def test_a_sample_plays_from_the_keyboard(page, web):
    """The sample player: space on the button starts it, the seek bar
    becomes usable, and the progress is shown."""
    _make_character(web, "键盘样本", "一段样本", "voice",
                    images=(png_bytes(),), audios=(wav_bytes(3.0),))
    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="角色", exact=True).click()
    page.wait_for_timeout(800)
    row = None
    for candidate in visible(page.locator(".list-row")).all():
        if "键盘样本" in candidate.inner_text():
            row = candidate
            break
    assert row is not None, "资产列表里没有这个角色"
    row.click()
    page.wait_for_timeout(1200)

    play = page.locator("button[aria-label='播放样本 1']").first
    assert play.count(), "没有播放样本的按钮"
    play.focus()
    page.keyboard.press("Space")
    page.wait_for_timeout(700)
    state = page.evaluate(
        """() => {
            const el = document.querySelector('section[x-data]');
            const s = Alpine.$data(el);
            return {playing: s.audioPreviewState.playing,
                    key: s.audioPreviewState.key,
                    duration: s.audioPreviewState.duration};
        }""")
    assert state["playing"] is True, "空格没有开始播放：%s" % state
    assert state["duration"] > 0, "播放器没有拿到时长：%s" % state
    seek = page.locator("input.sample-seek").first
    assert not seek.is_disabled(), "播放之后进度条还是不能动"


def test_escape_closes_the_lightbox(page, web):
    """The image lightbox on the assets page closes with Escape."""
    _make_character(web, "带图角色", "有立绘", "voice",
                    images=(png_bytes(),))
    page.goto(BASE + "/assets", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="角色", exact=True).click()
    page.wait_for_timeout(800)
    row = None
    for candidate in visible(page.locator(".list-row")).all():
        if "带图角色" in candidate.inner_text():
            row = candidate
            break
    assert row is not None, "资产列表里没有这个角色"
    row.click()
    page.wait_for_timeout(1200)
    page.locator("button.thumb").first.click()
    page.wait_for_timeout(800)
    assert page.locator(".lightbox").is_visible(), "灯箱没有打开"
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    assert not page.locator(".lightbox").is_visible(), "Esc 没有关掉灯箱"


# --------------------------------------------------------------------------- #
# narrow screens
# --------------------------------------------------------------------------- #

def test_narrow_screens_at_320px_have_no_horizontal_overflow(page):
    """320px is narrower than the 390px the plan asked for: five pages,
    no sideways scrolling."""
    page.set_viewport_size({"width": 320, "height": 800})
    for path in ("/llm", "/assets", "/scripts", "/video", "/history"):
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(900)
        overflow = page.evaluate(
            """() => ({
                scroll: document.documentElement.scrollWidth,
                inner: window.innerWidth,
                widest: (() => {
                    let worst = 0, who = "";
                    document.querySelectorAll("*").forEach((el) => {
                        const right = el.getBoundingClientRect().right;
                        if (right > worst) { worst = right; who = el.className || el.tagName; }
                    });
                    return {right: Math.round(worst), who: String(who).slice(0, 60)};
                })(),
            })""")
        assert overflow["scroll"] <= overflow["inner"] + 1, (
            "%s 在 320px 下横向溢出 %s > %s（最宽的是 %s）"
            % (path, overflow["scroll"], overflow["inner"],
               overflow["widest"]["who"]))
