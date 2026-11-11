"""真剧本上走一遍版本管理：修订 → 保存 → 旧版还在 → 打开 → 恢复。"""
import json, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
NAME = "七岁养伤的一年"

def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read())

def script_now():
    return get("/api/scripts/" + urllib.parse.quote(NAME))["json"]

before = script_now()
print("改之前：", before["source_script"].splitlines()[0], "| 共", len(before["source_script"].splitlines()), "行")

with sync_playwright() as p:
    browser = p.chromium.launch()
    screen = browser.new_page(viewport={"width": 1500, "height": 1000})
    questions = []
    screen.on("dialog", lambda d: (questions.append(d.message), d.accept()))
    screen.goto(BASE + "/scripts?script=" + urllib.parse.quote(NAME), wait_until="networkidle")
    screen.wait_for_timeout(2500)
    screen.get_by_role("button", name="🕘 版本").click()
    screen.wait_for_timeout(800)
    panel = screen.locator(".version-list")
    print("版本面板:", panel.inner_text().replace("\n", " | "))

    # 在编辑器里改一句，再存：应该多一版，旧版还在。
    screen.evaluate("""() => {
        const app = Alpine.$data(document.querySelector('section[x-data]'));
        app.model.look = '水墨风';
        app.syncDerived();
        app.queueValidate();
    }""")
    screen.wait_for_timeout(900)
    screen.get_by_role("button", name="💾 保存").click()
    screen.wait_for_timeout(2000)
    print("\n存完之后:", panel.inner_text().replace("\n", " | "))

    # 打开旧版
    rows = screen.locator(".version-row")
    print("行数:", rows.count())
    screen.locator(".version-row", has_text="版本 1").get_by_role("button", name="打开").click()
    screen.wait_for_timeout(1500)
    look = screen.evaluate("() => Alpine.$data(document.querySelector('section[x-data]')).model.look")
    badge = screen.locator(".card-head .badge").filter(visible=True).first.inner_text()
    print("\n打开旧版后：画风 =", repr(look), "| 徽章 =", badge)
    print("库里那份的画风:", repr(script_now()["look"]))

    # 恢复
    screen.locator(".version-row", has_text="版本 1").get_by_role("button", name="恢复").click()
    screen.wait_for_timeout(2500)
    print("\n恢复之后：", panel.inner_text().replace("\n", " | "))
    print("库里那份的画风:", repr(script_now()["look"]))
    print("问过的确认:", questions[-1].replace("\n", " ") if questions else "无")
    browser.close()
