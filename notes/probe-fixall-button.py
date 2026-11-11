"""点一下「全部按建议改」，看页面上真的变了没有。"""
import json, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
NAME = "七岁养伤的一年"

def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=30).read())

before_lib = get("/api/scripts/" + urllib.parse.quote(NAME))
with sync_playwright() as p:
    browser = p.chromium.launch()
    screen = browser.new_page(viewport={"width": 1500, "height": 1000})
    screen.goto(BASE + "/scripts?script=" + urllib.parse.quote(NAME), wait_until="networkidle")
    screen.wait_for_timeout(2500)
    app = "() => Alpine.$data(document.querySelector('section[x-data]'))"
    before = screen.evaluate(app + ".model.target_duration_seconds")
    shots_before = screen.evaluate(app + ".model.shots.length")
    print("打开时：目标 %s 秒 | %s 镜" % (before, shots_before))
    risk = screen.locator(".validation-box.risk").filter(visible=True)
    print("风险框:", risk.count(), "|", risk.first.inner_text().replace("\n", " / "))
    risk.first.get_by_role("button", name="说明").click()
    screen.wait_for_timeout(400)
    for row in screen.locator(".risk-row").all():
        print("   -", row.inner_text().replace("\n", " ")[:110])
    risk.first.get_by_role("button", name="全部按建议改").click()
    screen.wait_for_timeout(2000)
    print("点击后按钮状态:", screen.locator(".validation-box.risk .btn.primary").first.inner_text())
    screen.wait_for_timeout(90000)
    after = screen.evaluate(app + ".model.target_duration_seconds")
    shots_after = screen.evaluate(app + ".model.shots.length")
    print("\n改完：目标 %s -> %s 秒 | %s -> %s 镜" % (before, after, shots_before, shots_after))
    risk = screen.locator(".validation-box.risk").filter(visible=True)
    print("剩下的风险:", risk.first.inner_text().replace("\n", " / ") if risk.count() else "无")
    ok = screen.locator(".validation-box.ok").filter(visible=True)
    print("回执:", ok.first.inner_text() if ok.count() else "无")
    badge = screen.locator(".card-head .badge").filter(visible=True).first.inner_text()
    print("卡头:", badge)
    browser.close()

after_lib = get("/api/scripts/" + urllib.parse.quote(NAME))
print("\n库里那份变了吗:", json.dumps(before_lib["json"], ensure_ascii=False, sort_keys=True)
      != json.dumps(after_lib["json"], ensure_ascii=False, sort_keys=True))
