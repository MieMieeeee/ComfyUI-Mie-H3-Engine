"""复查 2f084f2 提的两件事：制作页的重复台词、风险修复盖掉手工编辑。"""
import json, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
NAME = "七岁养伤的一年"

def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read())

before_lib = get("/api/scripts/" + urllib.parse.quote(NAME))
with sync_playwright() as p:
    browser = p.chromium.launch()
    screen = browser.new_page(viewport={"width": 1500, "height": 1000})
    questions = []
    screen.on("dialog", lambda d: (questions.append(d.message), d.dismiss()))

    # --- 制作页：同一角色说同一句话，预览要一条不少 ---
    screen.goto(BASE + "/video?script=" + urllib.parse.quote(NAME), wait_until="networkidle")
    screen.wait_for_timeout(2500)
    events = screen.locator(".event-brief").filter(visible=True)
    print("制作页事件条数:", events.count())
    texts = [events.nth(i).inner_text().split("\n")[-1] for i in range(events.count())]
    print("有没有重复的台词:", len(texts) != len(set(texts)))
    print("举例:", texts[:3])

    # --- 剧本页：模型在写的时候改了画风，得问一句 ---
    screen.goto(BASE + "/scripts?script=" + urllib.parse.quote(NAME), wait_until="networkidle")
    screen.wait_for_timeout(2500)
    screen.evaluate("""() => {
        const real = window.fetch;
        window.fetch = async (url, options) => {
            const response = await real(url, options);
            if (String(url).indexOf('/api/scripts/risk/fix') >= 0) {
                await new Promise((resolve) => setTimeout(resolve, 1500));
                const app = Alpine.$data(document.querySelector('section[x-data]'));
                app.model.look = '水墨风';
            }
            return response;
        };
    }""")
    risk = screen.locator(".validation-box.risk").filter(visible=True)
    risk.first.get_by_role("button", name="全部按建议改").click()
    screen.wait_for_timeout(150000)
    print("\n问了吗:", bool(questions))
    if questions:
        print("问的是:", questions[-1].replace("\n", " "))
    look = screen.evaluate("() => Alpine.$data(document.querySelector('section[x-data]')).model.look")
    target = screen.evaluate("() => Alpine.$data(document.querySelector('section[x-data]')).model.target_duration_seconds")
    print("画风:", look, "| 目标时长:", target)
    browser.close()

after_lib = get("/api/scripts/" + urllib.parse.quote(NAME))
print("\n库里那份变了吗:",
      json.dumps(before_lib["json"], ensure_ascii=False, sort_keys=True)
      != json.dumps(after_lib["json"], ensure_ascii=False, sort_keys=True))
