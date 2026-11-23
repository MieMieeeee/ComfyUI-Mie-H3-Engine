/* ComfyUI-Mie 短剧工作台 —— 网络层。
 *
 * 纯浏览器脚本，没有构建步骤：Alpine 是 vendored 的一个文件。
 * 这些文件按「哪一页、哪一块基础设施」分开，页面自己引用需要的那
 * 几份；引用的顺序就是依赖的顺序（Alpine 最后加载）。
 *
 * 依赖的浏览器原生能力：fetch（网络）、EventSource（服务端事件流）。
 */

const api = {
  async get(url) {
    const response = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!response.ok) {
      // The server says WHY in the body ("missing storyboard.json").
      // Throwing away that body leaves the page saying "404 Not Found",
      // which tells the reader nothing about what to do.
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `${response.status} ${response.statusText}`);
    }
    return response.json();
  },
  async post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `${response.status}`);
      // The status travels with the message: a refusal (409) is not a
      // failure, and the page says so differently.
      error.status = response.status;
      throw error;
    }
    return data;
  },
  async del(url) {
    const response = await fetch(url, {
      method: "DELETE",
      headers: { "Accept": "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${response.status}`);
    return data;
  },
};

/* ------------------------------------------------------------------ */
/* 渲染进度：一条 SSE 流，所有页面共用                                    */
/* ------------------------------------------------------------------ */

/* ComfyUI 的事件流由服务端读一次（SSE 只是把它转发出来），所以这里
 * 一页只开一条 EventSource：换个标签页不会多出一条流。
 *
 * 但只在**有任务在跑**的时候开：一条常开的流会让页面永远等不到
 * networkidle（页面的就绪判断会卡住），也会让一个闲着的浏览器白占
 * 一条连接。任务跑完就关掉；别的标签页发起的任务，靠一次便宜的活动
 * 查询发现。断线时页面如实说「进度流断开」，不假装一切正常。
 *
 * 收尾不能跟着流一起消失：一条跑完的渲染还是刚刚发生过的事，页面
 * 得能说得出它结束了。所以最后那条终态留着（流关了也留着），迟到的
 * 页面也能从活动查询里拿到它。 */
const renderProgress = (() => {
  const listeners = new Set();
  let source = null;
  let state = { prompts: [], active: "", connected: false };
  let wanted = false;
  let closeTimer = null;
  /* 最后一次看到的终态：跑完的那一次，以及它是怎样结束的。 */
  let lastTerminal = null;

  const LIVE_STATUSES = ["queued", "running"];

  function isTerminal(one) {
    return !!one && !!one.status && LIVE_STATUSES.indexOf(one.status) < 0;
  }

  function remember(one) {
    if (isTerminal(one)) lastTerminal = one;
  }

  function current() {
    const prompts = state.prompts || [];
    const found = prompts.find((p) => p.prompt_id === state.active);
    if (found) return found;
    if (prompts.length) return prompts[0];
    /* 没有在跑的，也没有流了：最后那条终态就是最新的事实。 */
    return lastTerminal;
  }

  function notify() {
    const one = current();
    remember(one);
    listeners.forEach((fn) => {
      try { fn(state, one); } catch (error) {
        /* 一个坏订阅者不影响别人，但也不许把错误藏起来 */
        console.error("渲染进度订阅者出错", error);
      }
    });
  }

  function close() {
    if (!source) return;
    source.close();
    source = null;
  }

  function open() {
    if (source) return;
    source = new EventSource("/api/render/progress");
    source.onmessage = (event) => {
      try {
        state = Object.assign({ connected: true }, JSON.parse(event.data));
      } catch (error) {
        console.error("渲染进度读不出来", error);
        return;
      }
      notify();
    };
    source.onerror = () => {
      /* 断了：关掉，等下一次活动查询再连。EventSource 自己重连的话，
       * 一条断掉的流会一直重试下去。终态留着：断的是流，不是事实。 */
      close();
      state = { prompts: [], active: "", connected: false };
      notify();
    };
  }

  return {
    listen(fn) {
      listeners.add(fn);
      fn(state, current());
      return () => listeners.delete(fn);
    },
    /* 有任务在跑才连，没任务就关。
     *
     * `last` 是活动查询带来的终态：服务器记得上一次跑完的那一次是
     * 怎样结束的，迟到的页面靠它说话。 */
    sync(activeCount, last) {
      if (last && last.prompt_id) {
        lastTerminal = last;
        notify();
      }
      if (Number(activeCount) > 0) {
        wanted = true;
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
        if (!source) open();
        return;
      }
      wanted = false;
      if (closeTimer) clearTimeout(closeTimer);
      /* 收尾的宽限：任务刚结束的那一刻，最后那条「完成」还得送到
       * 页面上——关早了，页面就永远看不到结束。 */
      closeTimer = setTimeout(() => {
        closeTimer = null;
        if (wanted) return;
        close();
        /* 流关了，结束这件事没有变：把最后那条终态留下。 */
        remember(current());
        state = { prompts: [], active: "", connected: false };
        notify();
      }, 8000);
    },
    state() { return state; },
    current() { return current(); },
    terminal() { return lastTerminal; },
  };
})();

/* 一条进度怎么说话：只说知道的，不知道的不编。 */
function progressText(one) {
  if (!one) return "";
  const bits = [];
  if (one.shot && one.shot_total) bits.push(`镜头 ${one.shot}/${one.shot_total}`);
  else if (one.shot) bits.push(`镜头 ${one.shot}`);
  if (one.step && one.step_total) bits.push(`采样 ${one.step}/${one.step_total} 步`);
  else if (typeof one.percent === "number") bits.push(`${one.percent}%`);
  if (one.node_label) bits.push(one.node_label);
  const line = bits.join(" · ");
  const message = one.message || "";
  if (!line) return message;
  if (message && !line.includes(message)) return `${line} · ${message}`;
  return line;
}
