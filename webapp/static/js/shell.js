/* 外壳：导航、ComfyUI 状态，和几个好几个页面都要用的小东西。
 *
 * 依赖的浏览器原生能力：fetch（网络）、Audio（声音试听）、
 * document.body.dataset（当前是哪一页）、localStorage（记住本机
 * 的选择）。
 *
 * 这里也放着剧本契约的固定词汇（事件名、说话动词）：剧本页和制作页
 * 都要说同一套话，放在一处才不会各说各的。
 */

/* ------------------------------------------------------------------ */
/* 统一的声音试听（U01）：同一时间只播一个样本，切角色/换文件即停。
 * 页面组件只调 preview.play(key, src) / preview.stop()，并读它的状态。 */
/* ------------------------------------------------------------------ */

const audioPreview = (() => {
  const element = new Audio();
  element.preload = "none";
  let currentKey = "";
  let listener = null;

  function notify() {
    if (listener) listener({
      key: currentKey,
      playing: !element.paused && !element.ended,
      position: element.currentTime || 0,
      duration: Number.isFinite(element.duration) ? element.duration : 0,
    });
  }
  const originalListener = () => notify();
  ["play", "pause", "ended", "timeupdate", "loadedmetadata"].forEach(
    (name) => element.addEventListener(name, originalListener));

  return {
    /* 谁在听（当前页面的组件）；换页面/换组件就换回来。 */
    listen(callback) { listener = callback; notify(); },

    /* 播一个（或点同一个暂停）；key 区分“哪个样本在放”。 */
    toggle(key, src) {
      if (currentKey === key && !element.paused) {
        element.pause();
        return;
      }
      if (currentKey !== key || element.src !== src) {
        element.src = src;
        currentKey = key;
      }
      element.play().catch(() => { currentKey = ""; notify(); });
    },

    replay() {
      element.currentTime = 0;
      if (element.paused) element.play().catch(() => {});
    },

    seek(seconds) {
      element.currentTime = seconds;
      notify();
    },

    stop() {
      element.pause();
      element.removeAttribute("src");
      element.load();
      currentKey = "";
      notify();
    },

    key() { return currentKey; },
  };
})();

/* ------------------------------------------------------------------ */
/* 外壳                                                                 */
/* ------------------------------------------------------------------ */

function appShell() {
  return {
    currentTab: document.body.dataset.tab || "video",
    comfyStatusUrl: "",
    comfyOnline: false,
    comfyText: "检查中…",
    themeSwitching: false,
    themeError: "",
    progressLine: "",      /* 顶栏的实时进度 */
    progressOffline: false,
    activeTimer: null,

    async init() {
      renderProgress.listen((state, one) => {
        this.progressOffline = !state.connected;
        this.progressLine = progressText(one);
      });
      await this.refreshStatus();
      this.startActivityWatch();
    },

    /* 有没有任务在跑：一次便宜的问话。别的标签页发起的任务也靠它
     * 发现——所以哪怕这一页什么都没做，也知道该不该开着进度流。 */
    startActivityWatch() {
      if (this.activeTimer) return;
      this.activeTimer = setInterval(() => this.checkActivity(), 5000);
      this.checkActivity();
    },

    async checkActivity() {
      try {
        const data = await api.get("/api/render/active");
        // 活动查询也带着上一次跑完的那一次：迟到的页面靠它知道
        // 渲染是怎么结束的，而不是什么都看不到。
        renderProgress.sync(data.active || 0, data.last || null);
      } catch (error) {
        /* 问不到就不动：已经开着的进度流保持原样 */
      }
    },

    async switchTheme(themeId) {
      this.themeSwitching = true;
      this.themeError = "";
      try {
        const saved = await api.post("/api/theme", { theme: themeId });
        const themes = await api.get("/api/themes");
        const selected = themes.find((theme) => theme.id === saved.theme);
        const declarations = Object.entries(selected?.tokens || {})
          .map(([name, value]) => name + ": " + value + ";")
          .join("\n");
        document.getElementById("theme-injector").textContent =
          declarations ? ":root{\n" + declarations + "\n}" : "";
        document.body.dataset.theme = saved.theme;
      } catch (error) {
        this.themeError = "切换主题失败：" + error.message;
      } finally {
        this.$refs.themeSelect.value = document.body.dataset.theme;
        this.themeSwitching = false;
      }
    },

    async refreshStatus() {
      try {
        const data = await api.get("/api/comfy_status");
        this.comfyOnline = !!data.online;
        this.comfyStatusUrl = data.online ? data.url : "";
        if (data.online) {
          const device = (data.devices || [])[0];
          const vram = device && device.vram_total
            ? ` · 显存 ${Math.round(device.vram_free / 1e9)}/${Math.round(device.vram_total / 1e9)}G`
            : "";
          this.comfyText = `ComfyUI ${data.comfyui_version || ""}${vram}`;
        } else {
          this.comfyText = "ComfyUI 未连接";
        }
      } catch (error) {
        this.comfyOnline = false;
        this.comfyText = "ComfyUI 未连接";
      }
    },

    get comfyStatusShort() {
      return this.comfyOnline ? "已连接" : "未连接";
    },
    get comfyStatusClass() {
      return this.comfyOnline ? "ok" : "err";
    },
    get comfyStatusTitle() {
      return this.comfyText;
    },
  };
}

/* ------------------------------------------------------------------ */
/* 剧本契约的固定取值，与 mie_craft.script.contract 的常量保持一致。  */
/* ------------------------------------------------------------------ */

/* v2 契约的固定取值，与 mie_craft.script.contract 的常量保持一致。 */
const SCRIPT_SPEECH_VERBS = ["说道", "问道", "喊道", "答道", "回答", "笑道", "怒道",
                             "轻声说", "低声说", "说", "问", "喊", "叫", "道", "答",
                             "嘀咕", "吐槽", "开口"];
const SCRIPT_EVENT_NAMES = { dialogue: "对白", action: "动作", enter: "入场", exit: "离场" };
