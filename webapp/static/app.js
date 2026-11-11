/* ComfyUI-Mie 短剧工作台 —— 前端逻辑。
 *
 * 纯浏览器脚本，没有构建步骤：Alpine 是 vendored 的一个文件。
 * appShell 是外壳（导航 + ComfyUI 状态），每个页面一个组件。
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
    if (!response.ok) throw new Error(data.error || `${response.status}`);
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
/* 浏览器暂存的小仓库：图片 Blob 住 IndexedDB（localStorage 装不下它）  */
/* ------------------------------------------------------------------ */

const INTAKE_DB = "mie-intake";
const INTAKE_STORE = "images";

function intakeDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(INTAKE_DB, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(INTAKE_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB 打不开"));
  });
}

async function idbPut(key, blob) {
  const db = await intakeDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(INTAKE_STORE, "readwrite");
    tx.objectStore(INTAKE_STORE).put(blob, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IndexedDB 写不进"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB 写入被中止"));
  });
}

async function idbGet(key) {
  const db = await intakeDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(INTAKE_STORE, "readonly");
    const request = tx.objectStore(INTAKE_STORE).get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB 读不了"));
  });
}

async function idbDel(key) {
  const db = await intakeDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(INTAKE_STORE, "readwrite");
    tx.objectStore(INTAKE_STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IndexedDB 删不了"));
  });
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(blob);
  });
}

/* ------------------------------------------------------------------ */
/* 外壳                                                                 */
/* ------------------------------------------------------------------ */

function appShell() {
  return {
    currentTab: document.body.dataset.tab || "video",
    comfyStatusUrl: "",
    comfyOnline: false,
    comfyText: "检查中…",

    async init() {
      await this.refreshStatus();
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
/* 大模型                                                               */
/* ------------------------------------------------------------------ */

function llmPage() {
  return {
    platforms: [],
    current: "",
    keysFile: "",
    lastResult: "",
    filter: "",

    async init() {
      await this.load();
    },

    /* 当前平台排在最前面：它决定工作台用的是哪一家，
     * 不该让人在二十多行里找它。 */
    get orderedPlatforms() {
      const list = (this.platforms || []).slice();
      return list.sort((a, b) => {
        if (!!b.current !== !!a.current) return b.current ? 1 : -1;
        return 0;
      });
    },

    get visiblePlatforms() {
      const needle = (this.filter || "").trim().toLowerCase();
      if (!needle) return this.orderedPlatforms;
      return this.orderedPlatforms.filter((p) =>
        (p.name || "").toLowerCase().includes(needle)
        || (p.key || "").toLowerCase().includes(needle));
    },

    async load() {
      try {
        const data = await api.get("/api/llm/platforms");
        this.platforms = data.platforms || [];
        this.current = data.current || "";
        this.keysFile = data.keys_file || "";
      } catch (error) {
        this.lastResult = `读取失败：${error.message}`;
      }
    },

    async saveKey(platform) {
      const input = document.querySelector(`input[data-key="${platform.key}"]`);
      const token = input ? input.value : "";
      try {
        const result = await api.post("/api/llm/key", { key: platform.key, token });
        platform.has_key = result.has_key;
        platform.key_masked = result.key_masked;
        this.lastResult = `${platform.name} 的 Key 已保存`;
      } catch (error) {
        this.lastResult = `保存失败：${error.message}`;
      }
    },

    async saveModel(platform) {
      try {
        await api.post("/api/llm/model", { key: platform.key, model: platform.model });
        this.lastResult = `${platform.name} 的模型已保存`;
      } catch (error) {
        this.lastResult = `保存失败：${error.message}`;
      }
    },

    async saveHost(platform) {
      const input = document.querySelector(`input[data-host="${platform.key}"]`);
      try {
        await api.post("/api/llm/host", { key: platform.key, host: input ? input.value : "" });
        this.lastResult = `${platform.name} 的地址已保存`;
      } catch (error) {
        this.lastResult = `保存失败：${error.message}`;
      }
    },

    async makeCurrent(platform) {
      try {
        await api.post("/api/llm/current", { key: platform.key });
        this.platforms.forEach((p) => { p.current = p.key === platform.key; });
        this.current = platform.key;
        this.lastResult = `当前平台：${platform.name}`;
      } catch (error) {
        this.lastResult = `切换失败：${error.message}`;
      }
    },

    async test(platform) {
      platform.busy = true;
      this.lastResult = `${platform.name} 测试中…`;
      try {
        const result = await api.post("/api/llm/test", {
          key: platform.key, model: platform.model,
        });
        this.lastResult = result.ok
          ? `${platform.name} 通了：${result.answer || "(空回答)"}`
          : `${platform.name} 失败：${result.error}`;
      } catch (error) {
        this.lastResult = `${platform.name} 失败：${error.message}`;
      } finally {
        platform.busy = false;
      }
    },
  };
}

/* ------------------------------------------------------------------ */
/* 角色与场景资产                                                        */
/* ------------------------------------------------------------------ */

function assetsPage() {
  return {
    kind: "character",
    payload: { characters: [], scenes: [], storyboards: [] },
    libraryDir: "",
    selected: "",
    detail: {},
    editing: false,
    form: { name: "", description: "", voice: "", ambience: "" },
    imageFile: null,
    audioFile: null,
    message: "",
    autoHint: "",
    autoBusy: false,
    baselineForm: "",

    async init() {
      await this.load();
      window.addEventListener("beforeunload", (event) => {
        if (!this.isDirty) return;
        event.preventDefault();
        event.returnValue = "";
      });
    },

    /* 切角色/场景也是换一份表单：脏了就问。 */
    setKind(kind) {
      if (kind === this.kind) return;
      if (!this.confirmDiscard("切换类型")) return;
      this.kind = kind;
      this.selected = "";
      this.detail = {};
      this.editing = false;
      this.message = "";
    },

    /* 资产页也有同样的问题：改了一半切到别的资产，改动就没了。 */
    get isDirty() {
      if (!this.editing) return false;
      try { return JSON.stringify(this.form) !== this.baselineForm; }
      catch { return true; }
    },

    confirmDiscard(what) {
      if (!this.isDirty) return true;
      return window.confirm(`${what}会丢掉当前未保存的修改。继续吗？`);
    },

    get items() {
      const list = this.kind === "character" ? this.payload.characters : this.payload.scenes;
      const broken = (this.payload.broken || {})[this.kind] || [];
      // 库跳过读不出来的资产，所以它们本来不在列表里——也就没法点、
      // 没法删。放回列表，标着"损坏"，让人能处理它。
      return (list || []).filter((x) => x && x.name).concat(broken);
    },

    async load() {
      try {
        this.payload = await api.get("/api/assets");
        this.libraryDir = this.payload.library_dir || "";
      } catch (error) {
        this.message = `读取失败：${error.message}`;
      }
    },

    async open(name) {
      if (name !== this.selected && !this.confirmDiscard("切换资产")) return;
      this.selected = name;
      this.editing = false;
      this.message = "";
      try {
        this.detail = await api.get(`/api/assets/${this.kind}/${encodeURIComponent(name)}`);
      } catch (error) {
        this.detail = {};
        this.message = `读取失败：${error.message}`;
      }
    },

    startNew() {
      if (!this.confirmDiscard("新建资产")) return;
      this.selected = "";
      this.detail = {};
      this.editing = true;
      this.imageFile = null;
      this.audioFile = null;
      this.autoHint = "";
      this.form = { name: "", description: "", voice: "", ambience: "" };
      this.baselineForm = JSON.stringify(this.form);
      this.message = "";
    },

    startEdit() {
      if (!this.confirmDiscard("进入编辑")) return;
      this.editing = true;
      this.imageFile = null;
      this.audioFile = null;
      this.autoHint = "";
      this.form = {
        name: this.detail.name || "",
        description: this.detail.description || "",
        voice: this.detail.voice || "",
        ambience: this.detail.ambience || "",
      };
      this.baselineForm = JSON.stringify(this.form);
    },

    cancel() {
      if (!this.confirmDiscard("取消编辑")) return;
      this.editing = false;
      this.imageFile = null;
      this.audioFile = null;
    },

    pickImage(event) {
      this.imageFile = event.target.files[0] || null;
    },

    pickAudio(event) {
      this.audioFile = event.target.files[0] || null;
    },

    /* 把一张图读成 dataURL：优先刚选的文件，其次已有资产图。 */
    async _currentImage() {
      const file = this.imageFile;
      const read = (blob) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("图片读取失败"));
        reader.readAsDataURL(blob);
      });
      if (file) return read(file);
      if (this.detail && this.detail.image) {
        const response = await fetch(this.detail.image);
        if (response.ok) return read(await response.blob());
      }
      return null;
    },

    /* AI 补全空字段：对标 auto 保存节点——填了的字段原样保留。 */
    async autoFill() {
      if (this.autoBusy) return;
      this.autoBusy = true;
      this.message = "AI 补全中…（有图时看图写描述，可能要几十秒）";
      try {
        const result = await api.post("/api/assets/auto", {
          kind: this.kind,
          name: this.form.name || "",
          hint: this.autoHint,
          current: {
            description: this.form.description,
            voice: this.form.voice,
            ambience: this.form.ambience,
          },
          image: await this._currentImage(),
        });
        // 服务端只起草空字段；这里同样只在空时回填。
        if (!this.form.description && result.description) {
          this.form.description = result.description;
        }
        if (this.kind === "character" && !this.form.voice && result.voice) {
          this.form.voice = result.voice;
        }
        if (this.kind === "scene" && !this.form.ambience && result.ambience) {
          this.form.ambience = result.ambience;
        }
        this.message = (result.drafted || []).length
          ? `已补全：${result.drafted.join("、")}（检查后按保存入库）`
          : "没有空字段需要补全。";
      } catch (error) {
        this.message = `补全失败：${error.message}`;
      } finally {
        this.autoBusy = false;
      }
    },

    async save() {
      const body = new FormData();
      body.append("name", this.form.name || "");
      body.append("description", this.form.description || "");
      if (this.kind === "character") {
        body.append("voice", this.form.voice || "");
      } else {
        body.append("ambience", this.form.ambience || "");
      }
      if (this.imageFile) body.append("image", this.imageFile);
      if (this.audioFile) body.append("audio", this.audioFile);
      try {
        const response = await fetch(`/api/assets/${this.kind}`, {
          method: "POST", body,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || response.statusText);
        this.editing = false;
        await this.load();
        await this.open(data.name);
        // After the reload, not before: open() clears the message, so a
        // confirmation set beforehand was wiped before anyone could read it.
        this.message = `已保存：${data.name}（${data.status}）`;
      } catch (error) {
        this.message = `保存失败：${error.message}`;
      }
    },

    async remove() {
      if (!this.selected) return;
      if (!window.confirm(`删除 ${this.selected}？`)) return;
      try {
        await api.post(`/api/assets/${this.kind}/${encodeURIComponent(this.selected)}/delete`);
        this.selected = "";
        this.detail = {};
        this.message = "已删除";
        await this.load();
      } catch (error) {
        this.message = `删除失败：${error.message}`;
      }
    },
  };
}

/* ------------------------------------------------------------------ */
/* 剧本资产                                                             */
/* ------------------------------------------------------------------ */

/* v2 契约的固定取值，与 mie_craft.script.contract 的常量保持一致。 */
const SCRIPT_SPEECH_VERBS = ["说道", "问道", "喊道", "答道", "回答", "笑道", "怒道",
                             "轻声说", "低声说", "说", "问", "喊", "叫", "道", "答",
                             "嘀咕", "吐槽", "开口"];
const SCRIPT_EVENT_NAMES = { dialogue: "对白", action: "动作", enter: "入场", exit: "离场" };

/* 渲染风险的分级：契约给的是一串长句，这里按英文标记分类定级。
 * err = 内容里确定存在的缺陷（会真的翻车）；warn = 预算/形态风险；
 * info = 弱代理或只在特定条件下成立。 */
/* 每类风险配一句能照着做的改法：光把诊断丢给模型，它不知道该
 * 动哪里——诊断说的是"哪里不够"，改法说的是"动什么"。 */
const WARNING_META = [
  ["punctuation-only dialogue", "标点台词变提示音", "err", "model",
   "把这一句改写成有可发音内容的台词（至少留一个字），不能只留标点"],
  ["speech and action budget too tight", "慢速塞满会丢句", "warn", "duration",
   "把这一镜的时长加到装得下这些台词与动作，或者减掉一句台词；"
   + "不要让最短的那句被丢掉"],
  ["shot budget at the floor", "预算贴地", "warn", "duration",
   "把这一镜的时长留出余量（比现在多出至少半秒），别让它压在最低预算上"],
  ["duration pinned at the feasibility floor", "目标时长零缓冲", "warn",
   "duration",
   "把目标时长提高约 1 秒，让每一镜都有缓冲"],
  ["single-speaker continuation clip", "单人续接缺陷入形", "warn", "model",
   "把这一镜拆成两镜，或者让画面里可见的角色接上这句台词"],
  ["long same-speaker burst", "连珠炮口型漂移", "warn", "model",
   "把同一角色连着说的几句拆散到不同片段（每段不超过 4 句），"
   + "或者把收尾的反应拆成一个没有台词的第二镜"],
  ["shouting delivery", "喊叫语速偏慢", "info", "duration",
   "把目标时长加长一点，给喊叫的收尾留出 1 秒以上"],
  ["text-only crowds", "群像开场泄漏", "info", "model",
   "把没有台词的角色从这一镜里去掉，或者给他们各安排一个出场动作"],
];
const WARNING_LEVEL_ORDER = { err: 0, warn: 1, info: 2 };

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/* parse_script（mie_craft/script/contract.py）的浏览器侧镜像。
 * source_script 是事件的唯一编辑源，shots[].events 必须与它逐字一致，
 * 所以表单在这里实时解析出同一份事件列表，分镜只是把事件分段。 */
function parseSourceScript(script, roles, language) {
  const aliases = [];
  const owner = new Map();
  const roleList = roles || [];
  for (let index = 0; index < roleList.length; index++) {
    const role = roleList[index];
    for (const alias of [role.id, "角色" + (index + 1), "Character " + (index + 1), role.name || ""]) {
      const key = alias.toLowerCase();
      if (!key) continue; /* 空名字交给服务端报错 */
      if (owner.has(key) && owner.get(key) !== role.id) {
        return { events: [], error: `角色名/别名必须唯一：「${alias}」` };
      }
      if (!owner.has(key)) { owner.set(key, role.id); aliases.push(key); }
    }
  }
  if (!aliases.length) return { events: [], error: "先在角色区添加至少一个角色。" };
  const names = aliases.slice().sort((a, b) => b.length - a.length)
    .map(escapeRegExp).join("|");
  const kindWords = ["动作", "对白", "入场", "离场", "action", "dialogue", "enter", "exit",
                     ...SCRIPT_SPEECH_VERBS]
    .sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  const pattern = new RegExp("^\\s*(" + names + ")\\s*(" + kindWords + ")?"
    + "\\s*(?:\\[(zh|en)\\])?\\s*[：:]\\s*(.+)$", "i");
  const dialogueVerbs = new Set(SCRIPT_SPEECH_VERBS.map((verb) => verb.toLowerCase()));
  const chineseKind = { "动作": "action", "对白": "dialogue", "入场": "enter", "离场": "exit" };
  const events = [];
  const lines = String(script || "").split(/\r\n|\r|\n/);
  for (let number = 1; number <= lines.length; number++) {
    const line = lines[number - 1];
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const match = pattern.exec(line);
    if (!match) {
      return { events: [], error: `第 ${number} 行无法识别：「${line.trim().slice(0, 40)}」`
        + " — 每行以角色开头：台词 角色1：你好 ／ 动作 角色1动作：… ／ 入场 角色1入场：… ／ 离场 角色1离场：…" };
    }
    const [, alias, kind, lang, text] = match;
    let type = chineseKind[kind || "对白"];
    if (!type) {
      const key = (kind || "对白").toLowerCase();
      type = dialogueVerbs.has(key) || ["action", "dialogue", "enter", "exit"].includes(key)
        ? (["action", "dialogue", "enter", "exit"].includes(key) ? key : "dialogue")
        : "dialogue";
    }
    events.push({
      id: "event_" + String(events.length + 1).padStart(3, "0"),
      type,
      character: owner.get(alias.toLowerCase()),
      text: text.trim(),
      language: lang || language || "zh",
    });
  }
  if (!events.length) {
    return { events: [], error: "source_script 需要至少一行角色事件（1..200 行）。" };
  }
  if (events.length > 200) return { events: [], error: "source_script 最多 200 个事件。" };
  return { events, error: "" };
}

/* estimate_shot_seconds（contract.py）的镜像：给新分段一个接近
 * 服务端重算值的秒数；服务端校验后总会覆盖成权威值。 */
function estimateShotSeconds(events) {
  let seconds = 0;
  for (const event of events) {
    if (event.type === "dialogue") {
      seconds += (String(event.text).match(/[\u3400-\u9fff]/g) || []).length / 8.0;
      seconds += (String(event.text).match(/[A-Za-z]+(?:'[A-Za-z]+)?/g) || []).length / 3 + 0.2;
    } else {
      seconds += 0.5; /* 动作/入场/离场各一拍 */
    }
  }
  return Math.round(seconds * 1000) / 1000;
}

/* 表单需要的键一个不少，已有的值原样保留；missing 可给新键一个默认值。 */
function syncKeys(source, keys, missing) {
  const out = {};
  for (const key of keys) {
    out[key] = source && Object.prototype.hasOwnProperty.call(source, key)
      ? source[key]
      : (typeof missing === "function" ? missing(key) : "");
  }
  return out;
}

/* 把任意 JSON 还原成表单可以直绑的形状（缺的字段补默认值）。 */
function normalizeStoryboardModel(m) {
  m.language = m.language === "en" ? "en" : "zh";
  m.audio_mode = m.audio_mode === "reference" ? "reference" : "generated";
  m.camera = m.camera && typeof m.camera === "object"
    ? m.camera : { framing: "medium_close_up", movement: "locked", perspective: "observer" };
  m.look = m.look || ""; m.setting = m.setting || ""; m.scene = m.scene || "";
  m.light = m.light || "follow_image"; m.light_text = m.light_text || "";
  m.constraints = m.constraints || "";
  m.target_duration_seconds = Number(m.target_duration_seconds) || 2;
  m.continuation_overlap_frames = Number(m.continuation_overlap_frames ?? 22) || 0;
  m.roles = Array.isArray(m.roles) ? m.roles : [];
  m.roles.forEach((role, index) => {
    role.id = role.id || "character_" + (index + 1);
    role.name = role.name || "";
    role.presence = role.presence === "off_screen" ? "off_screen" : "on_screen";
    role.initially_visible = role.initially_visible ?? role.presence === "on_screen";
    role.position = role.position || (role.presence === "on_screen" ? "center" : "none");
    role.voice = role.voice || "";
  });
  m.character_designs = m.character_designs && typeof m.character_designs === "object"
    ? m.character_designs : {};
  const execution = m.execution = m.execution && typeof m.execution === "object" ? m.execution : {};
  execution.look = execution.look || ""; execution.setting = execution.setting || "";
  execution.visible_state = execution.visible_state || "";
  execution.scene = execution.scene || ""; execution.light = execution.light || "";
  execution.voices = execution.voices || {};
  execution.directions = execution.directions || {};
  execution.character_designs = execution.character_designs || {};
  m.source_script = m.source_script || "";
  m.shots = Array.isArray(m.shots) ? m.shots : [];
  return m;
}

function scriptsPage() {
  return {
    items: [],
    selected: "",
    tab: "preview",            /* preview | edit | json */
    model: null,               /* 正在编辑的剧本（表单直绑） */
    rawText: "",               /* JSON 源码标签页的文本 */
    jsonDirty: false,
    parseEvents: [],
    parseError: "",
    grouping: [],              /* 分镜 = 事件索引的分组 */
    shotDurations: [],         /* 与 grouping 平行；没动过分镜时沿用服务端值 */
    errors: [], warnings: [], okMessage: "",
    loadError: "",              /* 这份剧本读不出来时的原因（面板上直接说） */
    warnOpen: false,
    baseline: "",              /* 打开/保存时的模型快照（用来算未保存） */
    sourceSnapshot: "",        /* 镜像所对应的那一版中文源文本 */
    executionState: "unknown", /* confirmed | changed | edited | unknown */
    executionDraft: null,      /* 模型重写的英文镜像草稿 + 差异 */
    draftingExecution: false,
    executionApplied: "",      /* 应用镜像时的那一版源文本的指纹 */
    executionDraftSource: "",  /* 生成镜像草稿时的源文本指纹 */
    draftTimer: null,
    recoveredDraft: null,      /* 本地找到的草稿，等人决定恢复还是丢弃 */
    draftOpen: false,
    drafting: false,
    fixing: false,              /* 正在按风险改：可能是模型在写，等一会儿 */
    versionInfo: { current: 0, items: [] },  /* 这一份剧本的版本历史 */
    versionOpen: false,
    changeNote: "",             /* 这一版是怎么来的：AI 修订 / 手工修改… */
    draftNotes: [],
    draftForm: { mode: "concept", concept: "", name: "新剧本", shot_count: 2,
                 language: "zh", target_duration_seconds: 20,
                 audio_mode: "generated", character_settings: "", critic: true },
    cleaned: null,             /* 最近一次契约通过的权威 JSON（预览数据源） */
    validateTimer: null,
    validateSeq: 0,

    async init() {
      await this.load();
      // 制作页点「在剧本页打开」过来时，URL 里带着剧本名。
      const wanted = new URLSearchParams(window.location.search).get("script");
      if (wanted && (this.items || []).some((item) => item.name === wanted)) {
        await this.open(wanted);
      }
      // 上次没写完的起草会话还暂存着的话，问一句要不要继续。
      await this.intakeLoadStash();
      window.addEventListener("beforeunload", (event) => {
        if (!this.isDirty) return;
        event.preventDefault();
        event.returnValue = "";   // the browser shows its own wording
      });
    },

    /* ---------------------------------------------------------------- */
    /* 未保存保护                                                        */
    /* ---------------------------------------------------------------- */

    get isDirty() {
      // JSON 源码标签页里还没解析成功的内容也算未保存：它还没回到模型里，
      // 只比模型的话，这段字会被当成不存在，切走就没了。
      if (this.jsonDirty) return true;
      if (!this.model) return false;
      try { return JSON.stringify(this.model) !== this.baseline; }
      catch { return true; }
    },

    get savedLabel() {
      if (!this.model) return "";
      return this.isDirty ? "未保存" : "已保存";
    },

    /* 已确认事件的当前指纹（编辑器 parse 出来的事件序列化）。 */
    get confirmedEventsSignature() {
      if (!this.intakeConfirmed || !this.parseEvents.length) return "";
      return JSON.stringify(this.parseEvents.map((event) => [
        Number((event.character || "character_0").split("_")[1]) - 1,
        event.type, event.text, event.language]));
    },

    /* script 路径的草稿被载入后，作者又改了事件：提示确认稿已偏离。 */
    get confirmedEventsDrifted() {
      if (!this.intakeConfirmed || !this.parseEvents.length) return false;
      return this.confirmedEventsSignature !== this.intakeConfirmed.signature;
    },

    /* 只记基线，不动草稿：打开一份剧本时用它，之后还要去读草稿。
       注意：不动 sourceSnapshot —— 那是"镜像对上的是哪一版源文本"的
       记号，只有镜像本身被更新或确认时才该动它。 */
    setBaseline() {
      this.baseline = JSON.stringify(this.model || {});
    },

    /* 真的保存成功之后才用这个：基线更新，草稿也清掉。 */
    markSaved() {
      this.setBaseline();
      this.clearDraft();
      // 起草会话的草稿存进库了（转正）：会话和浏览器暂存一起收摊。
      if (this.intakeLoaded) {
        this.intakeLoaded = false;
        this.intake = null;
        this.intakeOpen = false;
        this.intakeResumed = true;
        this.intakeResume = null;
        this.intakeClearStash();
      }
      this.intakeConfirmed = null;
    },

    /* 中文源文本的指纹：英文镜像描述的就是它。 */
    sourceSignature() {
      const model = this.model;
      if (!model) return "";
      const execution = model.execution || {};
      const parts = {
        look: model.look || "", setting: model.setting || "",
        scene: model.scene || "", light: model.light || "",
        light_text: model.light_text || "",
        roles: (model.roles || []).map((role) => [
          role.id, role.name, role.presence, role.position, role.voice]),
        events: this.parseEvents.map((event) => [
          event.id, event.type, event.character, event.text, event.language]),
        designs: model.character_designs || {},
      };
      try { return JSON.stringify(parts); }
      catch { return ""; }
    },

    /* 镜像陈旧：源文本在这次会话里动过，或者它上次被确认时源文本还不是
       现在这样。保存不算确认 —— 保存的是新源文本，英文还是旧的。 */
    get executionStale() {
      if (!this.model) return false;
      if (this.executionState === "changed" || this.executionState === "edited") {
        return true;
      }
      const snapshot = this.sourceSnapshot;
      return !!snapshot && snapshot !== this.sourceSignature();
    },

    /* 这句话要具体：源文本改了、还是镜像被人手改过，处理方式不一样。 */
    get executionNote() {
      if (!this.model || !this.executionStale) return "";
      if (this.executionState === "changed") {
        return "中文设定改过了，英文镜像还是旧的：现在去生成会用旧描述。";
      }
      if (this.executionState === "edited") {
        return "英文镜像在确认之后被改过：这次确认不再作数。";
      }
      return "中文设定改过了，英文镜像还没更新：现在去生成会用旧描述。";
    },

    async confirmExecution() {
      if (!this.selected) return;
      try {
        await api.post(
          `/api/scripts/${encodeURIComponent(this.selected)}/confirm_execution`, {});
        this.executionState = "confirmed";
        this.sourceSnapshot = this.sourceSignature();
        this.okMessage = "已确认：英文镜像与当前中文设定一致。";
      } catch (error) {
        this.errors = [error.message];
      }
    },

    /* 让模型只重写英文镜像。改完先给差异，作者看过再决定要不要用。 */
    async draftExecution() {
      if (!this.model || this.draftingExecution) return;
      this.draftingExecution = true;
      this.executionDraft = null;
      this.executionDraftSource = "";
      this.errors = [];
      // 记下这份草稿是照着哪一版源文本写的：等模型的时候人可能
      // 还在改中文，那这份草稿就是照着旧版写的，不能用。
      const source = this.sourceSignature();
      try {
        const data = await api.post(
          `/api/scripts/${encodeURIComponent(this.selected || "")}/execution/draft`,
          { json: this.buildPayload() });
        this.executionDraft = data;
        this.executionDraftSource = source;
      } catch (error) {
        this.errors = [error.message];
      } finally {
        this.draftingExecution = false;
      }
    },

    /* 把草稿里的英文镜像放进编辑器：只动 execution，其余一个字不改。 */
    applyExecutionDraft() {
      if (!this.executionDraft || !this.model) return;
      if (this.executionDraftSource !== this.sourceSignature()) {
        // 等模型的时候中文改过了：这份镜像是照着旧版写的，用上去
        // 就是"改了画风却按旧描述生成"。
        this.executionDraft = null;
        this.executionDraftSource = "";
        this.okMessage = "中文设定在生成期间改过了，这份执行描述是照着旧版写的，"
          + "请重新生成一次。";
        return;
      }
      const incoming = this.executionDraft.execution || {};
      const current = this.model.execution || {};
      this.model.execution = Object.assign({}, current, incoming);
      this.executionDraft = null;
      this.executionDraftSource = "";
      // 记住这份镜像对应的是哪一版源文本：之后中文再动，保存就
      // 不能拿它去确认了。
      this.executionApplied = this.sourceSignature();
      this.executionState = "confirmed";
      this.sourceSnapshot = this.sourceSignature();
      this.okMessage = "英文镜像已换成新的：看一眼上面的差异，满意就按「💾 保存」。";
    },

    discardExecutionDraft() {
      this.executionDraft = null;
    },

    /* 换剧本、新建、离开页面之前：脏了就问一句。 */
    confirmDiscard(what) {
      if (!this.isDirty) return true;
      return window.confirm(`${what}会丢掉当前未保存的修改。继续吗？`);
    },

    /* 本地草稿：改了东西、还没保存就关了页面，草稿还在。 */
    draftKey(name) {
      return "mie-script-draft:" + (name || this.selected || "new");
    },

    saveDraft() {
      if (!this.model || !this.isDirty) return;
      try {
        window.localStorage.setItem(this.draftKey(), JSON.stringify({
          model: this.model,
          // JSON 标签页里的改动还没回到模型里，原文得一起存。
          raw: this.jsonDirty ? this.rawText : null,
        }));
      } catch (error) { /* 隐私模式/配额：没有草稿而已 */ }
    },

    clearDraft() {
      try { window.localStorage.removeItem(this.draftKey()); } catch (error) { /* 同上 */ }
    },

    readDraft(name) {
      try {
        const raw = window.localStorage.getItem(this.draftKey(name));
        return raw ? JSON.parse(raw) : null;
      } catch (error) { return null; }
    },

    restoreDraft() {
      if (!this.recoveredDraft) return;
      const draft = this.recoveredDraft.draft || this.recoveredDraft;
      this.recoveredDraft = null;
      let model = draft.model || draft;
      let raw = null;
      if (draft.raw) {
        raw = draft.raw;
        try { model = JSON.parse(draft.raw); } catch (error) { /* 原文坏了就用模型 */ }
      }
      this.model = normalizeStoryboardModel(model);
      this.rawText = raw || "";
      this.jsonDirty = !!raw;
      this.tab = raw ? "json" : "edit";
      this.syncDerived();
      this.queueValidate();
    },

    discardDraft() {
      if (!this.recoveredDraft) return;
      const name = this.recoveredDraft.name || this.selected;
      try { window.localStorage.removeItem(this.draftKey(name)); }
      catch (error) { /* 同上 */ }
      this.recoveredDraft = null;
    },

    async load() {
      try {
        const data = await api.get("/api/scripts");
        this.items = data.items || (data.names || []).map((name) => ({ name }));
      } catch (error) {
        this.okMessage = `读取失败：${error.message}`;
      }
    },

    metaLine(item) {
      if (item.error) return "";
      const parts = [`角色${item.roles}`, `分镜${item.shots}`];
      if (typeof item.duration_seconds === "number") {
        parts.push(`${item.duration_seconds.toFixed(1)}s`);
      }
      parts.push(item.audio_mode === "reference" ? "参考音" : "生成音");
      if (item.content_version) parts.push(`版本${item.content_version}`);
      return parts.join(" · ");
    },

    async open(name) {
      if (name !== this.selected && !this.confirmDiscard("切换剧本")) return;
      this.selected = name;
      this.errors = []; this.warnings = []; this.okMessage = ""; this.warnOpen = false;
      this.draftOpen = false; this.draftNotes = [];
      this.recoveredDraft = null;
      this.loadError = "";
      this.executionDraft = null;
      this.executionApplied = "";
      this.changeNote = "";
      await this.loadVersions(name);
      try {
        const data = await api.get(`/api/scripts/${encodeURIComponent(name)}`);
        this.model = normalizeStoryboardModel(data.json);
        this.cleaned = data.json;
        this.executionState = data.execution_state || "unknown";
        this.rawText = ""; this.jsonDirty = false;
        this.tab = "preview";
        // 先把派生状态同步好再记基线：同步会补 execution 的键，如果基线
        // 取在同步之前，一打开就会被算成"有未保存的改动"。
        this.syncDerived();
        this.adoptGroupingFromShots();
        // 这一次会话里，镜像是对着眼下这份源文本的；之后源文本动了，
        // 这个记号就会和新的对不上，"过期"因此会亮起来。
        this.sourceSnapshot = this.sourceSignature();
        // 草稿先读，再记基线：记基线不碰草稿，但顺序也不能反 ——
        // 上次没保存就关掉的改动，还在本地。
        const draft = this.readDraft(name);
        this.setBaseline();
        if (draft && JSON.stringify(draft) !== this.baseline) {
          // 记下它是哪一份剧本的：丢弃时要用同一个键去删。
          this.recoveredDraft = { name, draft };
        }
        this.queueValidate();
      } catch (error) {
        // 读不出来要说出来。这个面板在没有 model 的时候只显示一句
        // "从左侧选择一个剧本"，把错误写进 okMessage 等于没人看见。
        this.model = null;
        this.cleaned = null;
        this.loadError = `读取失败：${error.message}`;
      }
    },

    startNew() {
      if (!this.confirmDiscard("新建剧本")) return;
      this.selected = "";
      this.errors = []; this.warnings = []; this.okMessage = ""; this.warnOpen = false;
      this.draftOpen = false; this.draftNotes = [];
      this.recoveredDraft = null;
      this.loadError = "";
      this.cleaned = null;
      this.rawText = ""; this.jsonDirty = false;
      this.model = normalizeStoryboardModel({
        version: 2, name: "新剧本", language: "zh", audio_mode: "generated",
        roles: [{
          id: "character_1", name: "角色1", presence: "on_screen",
          initially_visible: true, position: "center", voice: "",
        }],
        camera: { framing: "medium_close_up", movement: "locked", perspective: "observer" },
        look: "", setting: "", scene: "", light: "follow_image", light_text: "",
        constraints: "",
        // 两秒：契约拒绝远超事件密度需要的目标时长（"too long for the
        // event density"），一行短台词的骨架刚好够。
        target_duration_seconds: 2, continuation_overlap_frames: 0,
        source_script: "角色1：你好",
        character_designs: {},
        execution: {
          look: "", setting: "", visible_state: "Character 1 stands at center.",
          voices: { character_1: "" }, directions: {}, character_designs: {},
        },
      });
      this.tab = "edit";
      this.syncDerived();
      // 只记基线，不清草稿：上次没保存就关掉的新剧本，也该被找回。
      this.setBaseline();
      // 新剧本还没有镜像可言，这一版就是它对照的那一版。
      this.sourceSnapshot = this.sourceSignature();
      this.executionState = "unknown";
      this.executionDraft = null;
      this.executionApplied = "";
      const draft = this.readDraft();
      if (draft && JSON.stringify(draft) !== this.baseline) {
        this.recoveredDraft = { name: this.selected || "new", draft };
      }
      this.queueValidate();
    },

    /* ---------------------------------------------------------------- */
    /* 标签页                                                             */
    /* ---------------------------------------------------------------- */

    setTab(tab) {
      if (this.tab === "json" && tab !== "json" && !this.trySyncJson()) return;
      if (tab === "json") { this.rawText = this.jsonString(); this.jsonDirty = false; }
      this.tab = tab;
    },

    jsonString() {
      try { return JSON.stringify(this.buildPayload(), null, 2); }
      catch { return JSON.stringify(this.model || {}, null, 2); }
    },

    onJsonInput() {
      this.jsonDirty = true;
      this.queueValidate();
      this.queueDraft();
    },

    /* JSON 标签页是唯一能直接改模型的地方：切走或校验前先解析回表单。 */
    trySyncJson() {
      if (!this.jsonDirty) return true;
      let parsed;
      try {
        parsed = JSON.parse(this.rawText);
      } catch (error) {
        this.errors = [`JSON 无法解析：${error.message}`];
        this.okMessage = "";
        return false;
      }
      this.model = normalizeStoryboardModel(parsed);
      this.jsonDirty = false;
      this.syncDerived();
      return true;
    },

    /* ---------------------------------------------------------------- */
    /* 派生状态：解析、分镜、execution 键                                  */
    /* ---------------------------------------------------------------- */

    afterModelChange() {
      this.syncDerived();
      this.queueValidate();
      this.queueDraft();
    },

    /* 草稿写本地：防抖，别在每次按键都写一次。 */
    queueDraft() {
      if (this.draftTimer) clearTimeout(this.draftTimer);
      this.draftTimer = setTimeout(() => {
        this.draftTimer = null;
        this.saveDraft();
      }, 800);
    },

    syncDerived() {
      const model = this.model;
      if (!model) {
        this.parseEvents = []; this.parseError = ""; this.grouping = [];
        return;
      }
      const result = parseSourceScript(model.source_script, model.roles, model.language);
      this.parseEvents = result.events;
      this.parseError = result.error;
      this.reconcileGrouping();
      this.syncExecutionKeys();
    },

    reconcileGrouping() {
      const count = this.parseEvents.length;
      if (!count) { this.grouping = []; return; }
      const flat = this.grouping.flat();
      if (flat.length === count && flat.every((value, index) => value === index)) return;
      // 保留仍然有效的小节起点，多出来的事件并进最后一节。
      const starts = [0];
      for (const group of this.grouping) {
        const start = group[0];
        if (start > 0 && start < count && !starts.includes(start)) starts.push(start);
      }
      starts.sort((a, b) => a - b);
      this.grouping = starts.map((start, index) => {
        const end = index + 1 < starts.length ? starts[index + 1] : count;
        return Array.from({ length: end - start }, (_, k) => start + k);
      });
      this.shotDurations = this.grouping.map((g) => this.estimateGroupSeconds(g));
    },

    /* 打开已有剧本时分镜照搬 shots 的结构，而不是重置成一整段。 */
    adoptGroupingFromShots() {
      const shots = (this.model && this.model.shots) || [];
      const indexById = new Map(this.parseEvents.map((event, i) => [event.id, i]));
      const groups = [];
      const durations = [];
      let covered = 0;
      for (const shot of shots) {
        const idxs = (shot.events || [])
          .map((event) => indexById.get(event.id))
          .filter((i) => i !== undefined);
        if (!idxs.length) continue;
        groups.push(idxs);
        durations.push(shot.duration_seconds);
        covered += idxs.length;
      }
      if (groups.length && covered === this.parseEvents.length) {
        this.grouping = groups;
        this.shotDurations = durations;
      } else {
        this.grouping = [this.parseEvents.map((_, i) => i)];
        this.shotDurations = [this.estimateGroupSeconds(this.grouping[0])];
      }
    },

    syncExecutionKeys() {
      const model = this.model;
      if (!model) return;
      const execution = model.execution;
      // 新键给契约能接受的英文骨架：direction 必须非空且以该角色的
      // 英文名开头，voices 镜像可以直接借用角色本身的英文音色短语。
      const voiceByKey = new Map(model.roles.map((role) => [role.id, role.voice || ""]));
      execution.voices = syncKeys(execution.voices, [...voiceByKey.keys()],
        (key) => voiceByKey.get(key) || "");
      const previousDirections = execution.directions || {};
      execution.directions = {};
      for (const event of this.parseEvents) {
        if (event.type === "dialogue") continue;
        execution.directions[event.id] =
          Object.prototype.hasOwnProperty.call(previousDirections, event.id)
            ? previousDirections[event.id]
            : this.suggestDirection(event);
      }
      const designIds = model.roles
        .filter((role) => role.presence === "on_screen").map((role) => role.id);
      model.character_designs = syncKeys(model.character_designs, designIds);
      execution.character_designs = syncKeys(execution.character_designs, designIds);
    },

    suggestDirection(event) {
      const move = { action: "acts", enter: "enters the frame", exit: "leaves the frame" };
      return `Character ${this.characterNumber(event.character)} ${move[event.type] || "acts"}.`;
    },

    /* ---------------------------------------------------------------- */
    /* 角色                                                              */
    /* ---------------------------------------------------------------- */

    get onScreenRoles() {
      return ((this.model && this.model.roles) || [])
        .filter((role) => role.presence === "on_screen");
    },

    addRole() {
      if (!this.model || this.model.roles.length >= 8) return;
      this.model.roles.push({
        id: "", name: "", presence: "on_screen",
        initially_visible: true, position: "center", voice: "",
      });
      this.renumberRoles();
      this.afterModelChange();
    },

    removeRole(index) {
      // voices / character_designs 的键是位置编号，删人后把值整体前移，
      // 否则后面角色的音色会错位到别人头上。
      const shift = (obj) => {
        const out = {};
        Object.keys(obj || {}).forEach((key, i) => {
          if (i === index) return;
          out["character_" + ((i < index ? i : i - 1) + 1)] = obj[key];
        });
        return out;
      };
      const execution = this.model.execution || {};
      execution.voices = shift(execution.voices);
      this.model.character_designs = shift(this.model.character_designs);
      execution.character_designs = shift(execution.character_designs);
      this.model.roles.splice(index, 1);
      this.renumberRoles();
      this.afterModelChange();
    },

    renumberRoles() {
      (this.model.roles || []).forEach((role, index) => {
        role.id = "character_" + (index + 1);
      });
    },

    onPresenceChange(role) {
      if (role.presence === "off_screen") {
        role.initially_visible = false;
        role.position = "none";
      }
      this.afterModelChange();
    },

    /* ---------------------------------------------------------------- */
    /* 分镜                                                              */
    /* ---------------------------------------------------------------- */

    get shotSummaries() {
      return this.grouping.map((idxs, index) => ({
        id: "scene_" + String(index + 1).padStart(2, "0"),
        seconds: this.shotDurations[index],
        events: idxs.map((k) => this.parseEvents[k]),
      }));
    },

    get directionEvents() {
      return this.parseEvents.filter((event) => event.type !== "dialogue");
    },

    splitBefore(eventIndex) {
      const position = this.grouping.findIndex((group) => group.includes(eventIndex));
      if (position < 0) return;
      const at = this.grouping[position].indexOf(eventIndex);
      if (at <= 0) return; /* 已是该镜第一个事件 */
      const group = this.grouping[position];
      const [before, after] = [group.slice(0, at), group.slice(at)];
      this.grouping.splice(position, 1, before, after);
      this.shotDurations.splice(position, 1,
        this.estimateGroupSeconds(before), this.estimateGroupSeconds(after));
      this.queueValidate();
    },

    mergeUp(groupIndex) {
      if (groupIndex <= 0 || groupIndex >= this.grouping.length) return;
      const merged = this.grouping[groupIndex - 1].concat(this.grouping[groupIndex]);
      this.grouping.splice(groupIndex - 1, 2, merged);
      this.shotDurations.splice(groupIndex - 1, 2, this.estimateGroupSeconds(merged));
      this.queueValidate();
    },

    estimateGroupSeconds(idxs) {
      return estimateShotSeconds(idxs.map((k) => this.parseEvents[k]));
    },

    /* allocate_frames 的 minimum 部分（contract.py）的镜像：按当前分镜
     * 和重叠帧，契约最少需要多少秒。低于它校验必报 too short。 */
    get minimumBoardSeconds() {
      if (!this.model) return 0;
      const overlap =
        Math.max(0, Math.round(Number(this.model.continuation_overlap_frames) || 0));
      return this.grouping.reduce((sum, group, index) => {
        const raw = Math.max(1.0, this.estimateGroupSeconds(group)) * 24
          + (index ? overlap : 0);
        return sum + (17 * Math.max(0, Math.ceil((raw - 5) / 17)) + 5) / 24;
      }, 0);
    },

    characterNumber(id) {
      const roles = (this.model && this.model.roles) || [];
      const index = roles.findIndex((role) => role.id === id);
      return index >= 0 ? index + 1 : 1;
    },

    /* ---------------------------------------------------------------- */
    /* payload 组装                                                       */
    /* ---------------------------------------------------------------- */

    buildPayload() {
      const model = this.model;
      if (!model) return {};
      const roles = model.roles.map((role, index) => ({
        id: "character_" + (index + 1),
        name: String(role.name || "").trim(),
        presence: role.presence === "off_screen" ? "off_screen" : "on_screen",
        initially_visible: role.presence === "off_screen" ? false : !!role.initially_visible,
        position: role.presence === "off_screen" ? "none" : (role.position || "none"),
        voice: String(role.voice || "").trim(),
      }));
      const payload = {
        version: 2,
        name: String(model.name || "").trim(),
        language: model.language,
        audio_mode: model.audio_mode,
        roles,
        camera: {
          framing: model.camera.framing,
          movement: model.camera.movement,
          perspective: model.camera.perspective,
        },
        look: String(model.look || ""),
        setting: String(model.setting || ""),
      };
      const scene = String(model.scene || "").trim();
      if (scene) payload.scene = scene;
      const light = model.light || "follow_image";
      if (light !== "follow_image") payload.light = light;
      if (light === "custom") payload.light_text = String(model.light_text || "").trim();
      payload.constraints = String(model.constraints || "");
      payload.target_duration_seconds = Number(model.target_duration_seconds) || 0;
      payload.continuation_overlap_frames =
        Math.max(0, Math.round(Number(model.continuation_overlap_frames) || 0));
      payload.source_script = String(model.source_script || "");
      payload.shots = this.buildShots();
      const designs = {};
      for (const [key, value] of Object.entries(model.character_designs || {})) {
        if (String(value || "").trim()) designs[key] = String(value).trim();
      }
      if (Object.keys(designs).length) payload.character_designs = designs;
      const execution = model.execution || {};
      const mirror = {
        look: String(execution.look || ""),
        setting: String(execution.setting || ""),
        visible_state: String(execution.visible_state || ""),
        voices: syncKeys(execution.voices, roles.map((role) => role.id)),
        directions: syncKeys(execution.directions,
          this.parseEvents.filter((event) => event.type !== "dialogue")
            .map((event) => event.id)),
      };
      if (scene) mirror.scene = String(execution.scene || "").trim();
      if (light === "custom") mirror.light = String(execution.light || "").trim();
      if (Object.keys(designs).length) {
        mirror.character_designs = syncKeys(execution.character_designs, Object.keys(designs));
      }
      payload.execution = mirror;
      return payload;
    },

    buildShots() {
      return this.grouping.map((idxs, index) => ({
        id: "scene_" + String(index + 1).padStart(2, "0"),
        // 单镜上限 30 秒；估算只为了让校验先过，服务端会重算成权威值。
        duration_seconds:
          Math.min(30, Math.max(0.1, Number(this.shotDurations[index]) || this.estimateGroupSeconds(idxs))),
        events: idxs.map((k) => ({ ...this.parseEvents[k] })),
      }));
    },

    /* ---------------------------------------------------------------- */
    /* AI 起草（concept 新建 / revise 修订）                               */
    /* ---------------------------------------------------------------- */

    startDraft(mode) {
      if (mode !== "revise") { this.openIntake(); return; }
      this.errors = []; this.warnings = []; this.okMessage = "";
      this.warnOpen = false;
      this.draftNotes = [];
      this.draftForm.mode = "revise";
      if (this.model) {
        // 修订只允许改名字和改动意见：接线事实（配音/重叠/结构）由底稿继承。
        this.draftForm.name = this.model.name || "新剧本";
      }
      this.draftForm.concept = "";
      this.draftOpen = true;
    },

    async runDraft() {
      if (this.drafting) return;
      if (!this.draftForm.concept.trim()) {
        this.errors = [this.draftForm.mode === "revise"
          ? "修改要求是空的：写这一版要改什么，例如「结尾改成她笑而不语，其余不动」。"
          : "点子是空的：写一个点子、一段故事或几句台词草稿都行。"];
        return;
      }
      this.drafting = true;
      this.errors = []; this.okMessage = "";
      try {
        const body = {
          mode: this.draftForm.mode,
          concept: this.draftForm.concept,
          name: this.draftForm.name || "新剧本",
          shot_count: Number(this.draftForm.shot_count) || 2,
          language: this.draftForm.language,
          target_duration_seconds: Number(this.draftForm.target_duration_seconds) || 20,
          audio_mode: this.draftForm.audio_mode,
          character_settings: this.draftForm.character_settings,
          critic: this.draftForm.critic ? "on" : "off",
        };
        // 底稿要用保存时那份：编辑器里的原始对象带着空的角色描述，
        // 契约不收空串，修订会直接被拒。
        if (this.draftForm.mode === "revise") body.base_asset = this.buildPayload();
        const result = await api.post("/api/scripts/auto", body);
        this.draftOpen = false;
        this.tab = "edit";
        this.applyDraftResult(result.json, result.notes,
                              this.draftForm.mode === "revise" ? "AI 修订" : "AI 起草");
      } catch (error) {
        this.errors = [error.message];
      } finally {
        this.drafting = false;
      }
    },

    /* ---------------------------------------------------------------- */
    /* 从图片或文字起草（intake）：素材 → 读图 → 确认理解 → 草稿          */
    /* ---------------------------------------------------------------- */

    /* 会话的三条铁律（计划 §5）：
       1. 内容一变 revision 就 +1，响应只认发起时的那版；
       2. 迟到的响应不许覆盖现在的工作（requestSeq 只认最新）；
       3. 草稿只在浏览器里暂存，正式资产要用户点保存。 */
    intakeOpen: false,
    intake: null,
    intakeResume: null,      /* 暂存里找到的上次会话，等人决定 */
    intakeResumed: false,    /* 已恢复/已放弃，别再问 */
    intakeLoaded: false,     /* 载入过 intake 草稿：保存转正后收摊 */
    intakeValidateSeq: 0,    /* 预检自己的序号（不抢占请求序号） */
    intakeConfirmed: null,   /* 载入时的已确认事件（指纹 + 条数） */

    openIntake() {
      this.errors = []; this.okMessage = "";
      if (!this.intake) {
        this.intake = {
          step: "material",   /* material|reading|understanding|events|generating|candidate */
          revision: 1,
          requestSeq: 0,
          flowMode: "direct",       /* 直接出一版 | 一起设计 */
          text: "", textPolicy: "adapt", policyChosen: false,
          policyHintLines: 0,
          image: null, imageDataUrl: "", imageBusy: false, imageError: "",
          caption: "", hasCaption: false, needsCaption: false,
          reading: null, readBusy: false, readError: "",
          eventDraft: null,   /* {mode, roles, events, unmapped, design} */
          previousDraft: null,  /* 重新整理前的上一份（含作者的改动，F05） */
          showDraftChoice: false,
          validateState: { ok: null, errors: [], checked: false },
          form: { name: "新剧本", shot_count: 0, language: "zh",
                  target_duration_seconds: 20, audio_mode: "generated",
                  critic: true },
          design: { scene: "", setting: "", look: "", constraints: "",
                    camera: { framing: "medium_shot", movement: "locked",
                              perspective: "observer" } },
          genBusy: false, genError: "", waited: 0, timer: null,
          candidate: null,      /* {json, notes, revision, forScript} */
          stashFailed: false,
        };
      }
      this.intakeOpen = true;
    },

    closeIntake() {
      // 会话留在内存里，暂存也留着：关上不等于放弃，随时可以再打开。
      if (this.intake && this.intake.genBusy) this.intakeCancelRunning();
      this.intakeOpen = false;
    },

    /* 每次素材、理解或参数变化都调用：修订号 +1，旧响应从此作废。 */
    intakeTouch() {
      if (!this.intake) return;
      this.intake.revision += 1;
      this.intake.candidate = null;   /* 旧候选描述的是旧内容 */
      this.intakeStash();
    },

    /* 主按钮只有一个：它是什么、点了做什么，都由当前步骤决定。 */
    get intakePrimary() {
      const s = this.intake;
      if (!s) return { label: "生成分镜草稿", run: () => {}, disabled: true };
      if (s.step === "reading" || s.step === "generating") {
        return { label: "取消等待", run: () => this.intakeCancelRunning(),
                 disabled: false };
      }
      if (s.step === "events") {
        // 保留台词路径：没分配完的原文片段、没确认的补写，都先处理。
        const pending = this.intakePendingPieces + this.intakeUnconfirmedAdditions;
        if (pending > 0) {
          return { label: "先处理上面的原文片段（剩 " + pending + " 条）",
                   run: () => {}, disabled: true };
        }
        const state = s.validateState;
        if (!state.checked) {
          return { label: "预检中…", run: () => {}, disabled: true };
        }
        if (!state.ok) {
          return { label: "先修正上面标出的问题", run: () => this.intakeValidate(),
                   disabled: false };
        }
        return { label: "按已确认内容生成", run: () => this.intakeGenerate(),
                 disabled: s.genBusy };
      }
      if (s.step === "understanding") {
        const blocked = this.intakeUnresolvedConflicts > 0;
        if (this.intakeNeedsEvents) {
          return { label: blocked ? "先处理上面的图文冲突" : "整理角色与事件",
                   run: () => this.intakeStructure(),
                   disabled: blocked || s.readBusy };
        }
        return { label: blocked ? "先处理上面的图文冲突" : "生成分镜草稿",
                 run: () => this.intakeGenerate(),
                 disabled: blocked || s.genBusy };
      }
      if (s.step === "candidate") {
        return { label: "载入编辑器", run: () => this.intakeLoadCandidate(),
                 disabled: false };
      }
      // material：保留台词 / 一起设计要先整理；有图先解读；纯文字+允许改编直接生成。
      const needsEvents = this.intakeNeedsEvents;
      if (s.needsCaption) {
        // 平台看不了图：图注写好就按图注解读；不写图注也可以只按文字走。
        return s.caption.trim()
          ? { label: "按图注解读", run: () => this.intakeRead(), disabled: false }
          : (needsEvents
              ? { label: "整理角色与事件", run: () => this.intakeStructure(),
                  disabled: !s.text.trim() }
              : { label: "生成分镜草稿", run: () => this.intakeGenerate(),
                  disabled: !s.text.trim() });
      }
      if (s.image) {
        return { label: "解读图片", run: () => this.intakeRead(), disabled: false };
      }
      return needsEvents
        ? { label: "整理角色与事件", run: () => this.intakeStructure(),
            disabled: !s.text.trim() }
        : { label: "生成分镜草稿", run: () => this.intakeGenerate(),
            disabled: !s.text.trim() };
    },

    /* 什么时候需要事件稿：保留原台词（keep）或一起设计（guided）。 */
    get intakeNeedsEvents() {
      const s = this.intake;
      return !!s && (s.textPolicy === "keep" || s.flowMode === "guided");
    },

    get intakeSecondary() {
      const s = this.intake;
      if (!s) return null;
      if (s.step === "material" && !s.image && s.text.trim()
          && !this.intakeNeedsEvents) {
        return { label: "先看看 AI 的理解", run: () => this.intakeRead() };
      }
      if (s.step === "understanding" || s.step === "events") {
        return { label: "返回修改素材", run: () => {
          // 事件稿留在会话里（F05）：素材可以改，回来时上一份还在，
          // 只有明确的“重新开始”才清空。
          s.step = "material";
          this.intakeTouch();
        } };
      }
      if (s.step === "candidate") {
        return { label: "再生成一版", run: () => this.intakeGenerate() };
      }
      return null;
    },

    get intakeUnresolvedConflicts() {
      const conflicts = this.intake && this.intake.reading
        ? this.intake.reading.conflicts || [] : [];
      return conflicts.filter((c) => !c.resolution).length;
    },

    get intakeStepLabel() {
      const s = this.intake;
      if (!s) return "";
      return {
        material: "第 1 步 · 素材",
        reading: "正在理解素材",
        understanding: "第 2 步 · 确认理解",
        events: "第 3 步 · 角色与事件",
        generating: "正在生成草稿",
        candidate: "草稿待载入",
      }[s.step] || s.step;
    },

    /* 未分配的原文片段：没处理（分配/标场景/解除）的才算“剩”。 */
    get intakePendingPieces() {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return 0;
      return (draft.unmapped || []).filter((p) => !p.handled).length;
    },

    /* 补写的台词还没表态（要么勾上确认，要么删掉）也算没处理完。 */
    get intakeUnconfirmedAdditions() {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return 0;
      return (draft.events || []).filter((e) => e.addition && !e.included
                                         && !e.removed).length;
    },

    get intakeValidateOk() {
      const state = this.intake && this.intake.validateState;
      return !!(state && state.checked && state.ok);
    },

    /* 生成前的一句确认摘要（U05）：把实际会生效的条件说成人话。 */
    get intakePrefaceSummary() {
      const s = this.intake;
      if (!s) return "";
      const shots = Number(s.form.shot_count) === 0
        ? "分镜自动" : "分镜 " + s.form.shot_count + " 镜";
      const policy = s.textPolicy === "keep"
        ? "台词逐字保留" : "允许改编情节和台词";
      const mode = this.intakeNeedsEvents ? "按已确认的角色和事件生成" : "按点子自由创作";
      return mode + "：" + policy + "，约 "
        + (Number(s.form.target_duration_seconds) || 20) + " 秒，" + shots
        + "（"
        + (this.intakeNeedsEvents ? "这些是锁定条件" : "时长和分镜是偏好，模型可调整")
        + "）";
    },

    intakeCaptionInput() {
      const s = this.intake;
      if (s && s.caption.trim()) s.hasCaption = true;
      this.intakeTouch();
    },

    /* 原文看着已经是角色事件行（每行都以「角色：」开头）且用户还没
       手选规则时，预选「保留原台词」并说明为什么（计划 §2）；
       用户选过一次之后，选择就是用户的。 */
    intakeTextPolicyHint() {
      const s = this.intake;
      if (!s) return;
      const lines = s.text.split("\n")
        .map((t) => t.trim())
        .filter((t) => t && !t.startsWith("#"));
      const labelled = lines.filter((t) =>
        /^[^\s：:，,]{1,40}(\s*(动作|对白|入场|离场))?\s*(\[(zh|en)\])?\s*[：:]/.test(t));
      const looksLabelled = lines.length > 0 && labelled.length === lines.length;
      if (!s.policyChosen) {
        s.textPolicy = looksLabelled ? "keep" : "adapt";
        s.policyHintLines = looksLabelled ? lines.length : 0;
      }
      this.intakeTouch();
    },

    /* ---------------- 图片：浏览器先归一化，服务端再验一次 ------------- */

    async intakePickImage(event) {
      const s = this.intake;
      const file = (event.target.files || [])[0];
      event.target.value = "";       // 同一张图重选也能触发 change
      if (!file || !s) return;
      s.imageError = "";
      s.imageBusy = true;
      try {
        const normalized = await this.normalizeImageFile(file);
        // 换图 = 换素材：旧解读全部作废（计划 §4.2）。
        s.imageBlob = normalized.blob;
        s.image = { name: file.name, width: normalized.width,
                    height: normalized.height, bytes: normalized.bytes,
                    key: "intake-image" };
        s.imageDataUrl = normalized.dataUrl;
        s.reading = null; s.needsCaption = false;
        // 图注跟旧图走；新图还没人描述过。
        s.caption = ""; s.hasCaption = false;
        this.intakeTouch();
      } catch (error) {
        s.imageError = error.message;
      } finally {
        s.imageBusy = false;
      }
    },

    async normalizeImageFile(file) {
      const okTypes = ["image/jpeg", "image/png", "image/webp"];
      if (!okTypes.includes(file.type)) {
        throw new Error("只接受 JPEG、PNG 或 WebP 图片；SVG 和动图请重新选择一张静态图");
      }
      if (file.size > 10 * 1024 * 1024) {
        throw new Error("这张原图有 " + (file.size / 1048576).toFixed(1)
          + " MB，超过 10 MB 上限：换一张小一点的");
      }
      let bitmap;
      try {
        // from-image：按 EXIF 把方向摆正，再谈尺寸。
        bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      } catch {
        throw new Error("这张图打不开（解码失败），请重新选择");
      }
      const maxEdge = 2048;
      const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width; canvas.height = height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
      bitmap.close();
      let blob = null;
      for (let quality = 0.9; quality >= 0.45; quality -= 0.15) {
        blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", quality));
        if (blob && blob.size <= 4 * 1024 * 1024) break;
      }
      if (!blob) throw new Error("图片压缩失败，请重试");
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("读取图片失败"));
        reader.readAsDataURL(blob);
      });
      return { blob, dataUrl, width, height, bytes: blob.size };
    },

    intakeRemoveImage() {
      const s = this.intake;
      if (!s) return;
      s.image = null; s.imageDataUrl = ""; s.imageError = "";
      s.reading = null; s.needsCaption = false;
      s.caption = ""; s.hasCaption = false;
      s.step = "material";
      s.assetForm = null;
      this.intakeTouch();
    },

    /* ---------------- 另存为参考资产（M3：显式、独立、可追溯） -------- */

    intakeOpenAssetForm() {
      const s = this.intake;
      if (!s || !s.image) return;
      s.assetForm = {
        kind: "character", name: "", target: "", description: "", voice: "",
        busy: false, error: "", saved: null, drafting: false,
      };
    },

    async intakeDraftAssetFields() {
      const s = this.intake;
      const form = s && s.assetForm;
      if (!form || form.drafting) return;
      if (!form.name.trim()) {
        form.error = "先写名字，AI 补全按名字和图来写描述。";
        return;
      }
      form.drafting = true;
      form.error = "";
      try {
        const result = await api.post("/api/assets/auto", {
          kind: form.kind, name: form.name.trim(),
          hint: form.target.trim(),
          current: { description: form.description, voice: form.voice },
          image: s.imageDataUrl,
        });
        if (result.description) form.description = result.description;
        if (result.voice) form.voice = result.voice;
      } catch (error) {
        form.error = error.message;
      } finally {
        form.drafting = false;
      }
    },

    async intakeSaveAsset() {
      const s = this.intake;
      const form = s && s.assetForm;
      if (!form || form.busy) return;      // 重复点击只有一次提交
      if (!form.name.trim()) {
        form.error = "名字是空的：资产要有名字才能入库。";
        return;
      }
      if (!form.description.trim()) {
        form.error = "描述是空的：写一句描述，或点「✨ AI 补全描述」。";
        return;
      }
      form.busy = true;
      form.error = "";
      try {
        const body = new FormData();
        body.append("name", form.name.trim());
        body.append("description", form.description);
        if (form.kind === "character") body.append("voice", form.voice);
        if (form.target.trim()) {
          // 多人物图要写清这图当谁的参考：跟描述一起入库可追溯。
          body.append("description",
            form.description + (form.description ? "\n" : "")
            + "（参考目标：" + form.target.trim() + "）");
        }
        if (s.imageBlob) body.append("image", s.imageBlob, "intake-reference.jpg");
        const response = await fetch("/api/assets/" + form.kind, {
          method: "POST", body,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          form.error = data.error || "保存失败了";
          return;
        }
        form.saved = { kind: form.kind === "scene" ? "场景" : "角色",
                       name: form.name.trim() };
      } catch (error) {
        form.error = error.message;
      } finally {
        form.busy = false;
      }
    },

    /* ---------------- 读素材：文字、图片或图文 ------------------------- */

    async intakeRead() {
      const s = this.intake;
      if (!s || s.readBusy) return;
      if (!s.text.trim() && !s.image) {
        s.readError = "至少给一项素材：写一段文字，或放一张图。";
        return;
      }
      s.readError = "";
      s.step = "reading";
      s.readBusy = true;
      const seq = ++s.requestSeq;
      const revision = s.revision;
      try {
        const body = {
          request_id: "req-" + seq + "-" + Date.now(),
          revision, action: "read",
          text: s.text, manual_caption: s.caption,
        };
        if (s.image) body.image = s.imageDataUrl;
        const { status, data } = await this.intakePost("/api/scripts/intake", body);
        if (seq !== s.requestSeq) return;      // 迟到了：现在的工作优先
        if (status !== 200) {
          s.step = "material";                  // 失败回到发起步骤，素材全在
          s.readError = data.error || "读素材失败了";
          return;
        }
        if (data.code === "needs_caption") {
          s.needsCaption = true;
          s.reading = null;
          s.step = "material";
          return;
        }
        const reading = data.reading || {};
        s.reading = {
          vision: reading.vision || "", basis: reading.basis || "",
          observed: (reading.observed || []).map((o) => ({ id: o.id, text: o.text })),
          proposed: (reading.proposed || []).map((p) => ({ id: p.id, text: p.text })),
          conflicts: (reading.conflicts || []).map((c, n) => ({
            id: c.id || "cfl_" + (n + 1), field: c.field || "",
            image: c.image || "", text: c.text || "",
            resolution: "", override: "",
          })),
        };
        s.needsCaption = false;
        s.step = "understanding";
        this.intakeStash();
      } catch (error) {
        if (seq !== s.requestSeq) return;
        s.step = "material";
        s.readError = error.message;
      } finally {
        s.readBusy = false;
      }
    },

    intakeAddItem(group) {
      const s = this.intake;
      if (!s || !s.reading) return;
      const items = s.reading[group];
      const prefix = group === "observed" ? "obs_" : "pro_";
      items.push({ id: prefix + (items.length + 1) + "_" + Date.now() % 1000,
                   text: "" });
    },

    intakeRemoveItem(group, id) {
      const s = this.intake;
      if (!s || !s.reading) return;
      s.reading[group] = s.reading[group].filter((item) => item.id !== id);
      this.intakeTouch();
    },

    intakeResolveConflict(conflict, how) {
      conflict.resolution = how;
      if (how !== "manual") conflict.override = "";
      this.intakeTouch();
    },

    /* ---------------- 事件稿：整理、编辑、预检 ------------------------- */

    /* 整理角色与事件。已经像事件行的文字在服务端走共享解析器直接
       解析（不经过模型）；普通故事文字由模型给建议，补写单独标出。
       有图片理解时把确认过的信息一并带上（F03）。 */
    async intakeStructure() {
      const s = this.intake;
      if (!s || s.readBusy) return;
      if (!s.text.trim() && !this.intakeReadingSummary().trim()) {
        s.readError = "素材是空的：写一段文字，或放一张图并解读后再整理。";
        return;
      }
      s.readError = "";
      s.step = "reading";
      s.readBusy = true;
      const seq = ++s.requestSeq;
      // 重新整理时，上一份（可能带着作者的改动）留作可换回的版本（F05）。
      s.previousDraft = s.eventDraft
        ? JSON.parse(JSON.stringify(s.eventDraft)) : null;
      try {
        const body = {
          request_id: "req-" + seq + "-" + Date.now(),
          revision: s.revision, action: "events",
          text: s.text,
          reading: this.intakeConfirmedReading(),
        };
        const { status, data } = await this.intakePost(
          "/api/scripts/intake", body);
        if (seq !== s.requestSeq) return;
        if (status !== 200 || !data.events) {
          s.step = s.reading ? "understanding" : "material";
          s.readError = data.error || "整理失败了";
          s.previousDraft = null;
          return;
        }
        this.intakeAdoptEvents(data.events);
        s.showDraftChoice = !!s.previousDraft;
        s.step = "events";
        s.validateState = { ok: null, errors: [], checked: false };
        this.intakeStash();
        this.intakeValidate();   // 进页面先预检一遍，别等用户点
      } catch (error) {
        if (seq !== s.requestSeq) return;
        s.step = s.reading ? "understanding" : "material";
        s.readError = error.message;
      } finally {
        s.readBusy = false;
      }
    },

    /* 明确的重新开始：只有用户点了才清掉事件稿（F05）。 */
    intakeRestartEvents() {
      const s = this.intake;
      if (!s) return;
      s.eventDraft = null;
      s.previousDraft = null;
      s.showDraftChoice = false;
      s.validateState = { ok: null, errors: [], checked: false };
      this.intakeTouch();
    },

    /* 换回上一份（含作者改动的那份）；预检随之重跑。 */
    intakeUsePreviousDraft() {
      const s = this.intake;
      if (!s || !s.previousDraft) return;
      s.eventDraft = s.previousDraft;
      s.previousDraft = null;
      s.showDraftChoice = false;
      s.validateState = { ok: null, errors: [], checked: false };
      this.intakeTouch();
      this.intakeValidate();
    },

    /* 就用新整理的这份，旧的不要了。 */
    intakeKeepNewDraft() {
      const s = this.intake;
      if (!s) return;
      s.previousDraft = null;
      s.showDraftChoice = false;
      this.intakeTouch();
    },

    /* 已确认的图片理解，结构化传给事件整理（F03）：不要拼进原文
       （原文是台词保护的对象），作为独立的上下文字段。 */
    intakeConfirmedReading() {
      const s = this.intake;
      const reading = s && s.reading;
      if (!reading) return null;
      return {
        basis: reading.basis || "",
        caption: s.caption || "",
        observed: (reading.observed || []).map((o) => o.text).filter(Boolean),
        proposed: (reading.proposed || []).map((p) => p.text).filter(Boolean),
        conflicts: (reading.conflicts || [])
          .filter((c) => c.resolution)
          .map((c) => ({
            field: c.field, image: c.image, text: c.text,
            resolution: c.resolution, override: c.override || "",
          })),
      };
    },

    /* 给“素材是否够整理”看的摘要文本（纯图也可整理，F03）。 */
    intakeReadingSummary() {
      const confirmed = this.intakeConfirmedReading();
      if (!confirmed) return "";
      const parts = confirmed.observed.concat(confirmed.proposed);
      return parts.join(" ");
    },

    /* 服务端的事件稿落进会话：角色保 ui_id，事件保角色序号。 */
    intakeAdoptEvents(incoming) {
      const s = this.intake;
      const roles = (incoming.roles || []).map((role, index) => ({
        ui_id: role.ui_id || "r" + (index + 1),
        name: role.name || "",
        presence: role.presence === "off_screen" ? "off_screen" : "on_screen",
        initially_visible: role.initially_visible !== false,
        position: role.position || (role.presence === "off_screen" ? "none" : "center"),
        voice: role.voice || "",
      }));
      const indexOf = (uiId) => Math.max(0, roles.findIndex((r) => r.ui_id === uiId));
      const events = (incoming.events || []).map((event, index) => ({
        ui_id: event.ui_id || "e" + (index + 1),
        role: indexOf(event.role_ui_id),
        type: event.type || "dialogue",
        text: event.text || "",
        language: event.language === "en" ? "en" : "zh",
        addition: !!event.addition,
        included: !event.addition,     /* 补写默认不勾：等作者确认 */
        removed: false,
      }));
      const unmapped = (incoming.unmapped || []).map((piece, index) => ({
        ui_id: "u" + (index + 1), text: piece.text || "",
        handled: false, handledHow: "", handledTo: -1,
      }));
      s.eventDraft = { mode: incoming.mode || "proposed",
                       roles, events, unmapped };
    },

    get intakeVisibleEvents() {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return [];
      return draft.events.filter((e) => !e.removed);
    },

    intakeTouchEvents() {
      // 事件稿一变，预检结果就过期了：标过期，并安排一次自动重查。
      const s = this.intake;
      if (!s) return;
      s.validateState = { ok: null, errors: [], checked: false };
      this.intakeTouch();
      if (this.intakeValidateTimer) clearTimeout(this.intakeValidateTimer);
      this.intakeValidateTimer = setTimeout(() => {
        this.intakeValidateTimer = null;
        if (this.intake && this.intake.step === "events") this.intakeValidate();
      }, 700);
    },

    intakeSetEvent(event, field, value) {
      event[field] = value;
      if (field === "type" || field === "role" || field === "language") {
        this.intakeTouchEvents();
      }
    },

    intakeMoveEvent(index, delta) {
      const draft = this.intake && this.intake.eventDraft;
      const list = draft ? draft.events : [];
      const target = index + delta;
      if (target < 0 || target >= list.length) return;
      const [row] = list.splice(index, 1);
      list.splice(target, 0, row);
      this.intakeTouchEvents();
    },

    intakeDuplicateEvent(index) {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return;
      const copy = Object.assign({}, draft.events[index], {
        ui_id: draft.events[index].ui_id + "_c" + Date.now() % 1000,
        addition: false, included: true,
      });
      draft.events.splice(index + 1, 0, copy);
      this.intakeTouchEvents();
    },

    intakeRemoveEvent(index) {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return;
      const event = draft.events[index];
      if (event.addition) event.removed = true;   // 补写删掉就是“不要”
      else draft.events.splice(index, 1);
      this.intakeTouchEvents();
    },

    intakeAddEvent() {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft || !draft.roles.length) return;
      draft.events.push({
        ui_id: "enew_" + Date.now() % 10000,
        role: 0, type: "dialogue", text: "", language: "zh",
        addition: false, included: true, removed: false,
      });
      this.intakeTouchEvents();
    },

    intakeToggleAddition(event) {
      event.included = !event.included;
      this.intakeTouchEvents();
    },

    intakeAddRole() {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return;
      draft.roles.push({
        ui_id: "rnew_" + Date.now() % 10000,
        name: "", presence: "on_screen", initially_visible: true,
        position: "center", voice: "",
      });
      this.intakeTouchEvents();
    },

    intakeRemoveRole(index) {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft) return;
      if (draft.events.some((e) => !e.removed && e.role === index)) {
        this.intake.validateState = {
          ok: false, checked: true,
          errors: [{ event_index: null,
                     message: "这个角色还有事件在引用：先处理那些事件（改角色或删掉），再删角色" }],
        };
        return;
      }
      draft.roles.splice(index, 1);
      draft.events.forEach((e) => {
        if (e.role > index) e.role -= 1;
      });
      this.intakeTouchEvents();
    },

    /* 未分配片段的三条出路（§4.1）：并进事件 / 记为场景说明 / 解除保留。 */
    intakeAssignPiece(piece) {
      const draft = this.intake && this.intake.eventDraft;
      if (!draft || !draft.roles.length) return;
      draft.events.push({
        ui_id: "ep_" + Date.now() % 10000,
        role: 0, type: "dialogue", text: piece.text,
        language: "zh", addition: false, included: true, removed: false,
      });
      piece.handled = true; piece.handledHow = "event";
      this.intakeTouchEvents();
    },

    intakePieceToScene(piece) {
      const s = this.intake;
      if (!s) return;
      s.design.scene = (s.design.scene
        ? s.design.scene + "\n" + piece.text : piece.text);
      piece.handled = true; piece.handledHow = "scene";
      this.intakeTouchEvents();
    },

    intakeDropPiece(piece) {
      piece.handled = true; piece.handledHow = "dropped";
      this.intakeTouchEvents();
    },

    async intakeValidate() {
      const s = this.intake;
      const draft = s && s.eventDraft;
      if (!s || !draft) return;
      const roles = draft.roles.map((r) => ({
        name: r.name, presence: r.presence,
        initially_visible: r.initially_visible, position: r.position,
        voice: r.voice,
      }));
      const events = this.intakeVisibleEvents
        .filter((e) => e.included)
        .map((e) => ({ role: e.role, type: e.type, text: e.text,
                       language: e.language }));
      // 预检用自己的序号：生成流程内部也会预检（§5 生成前重验），
      // 抢占请求序号会让在途生成作废。
      const seq = ++this.intakeValidateSeq;
      try {
        const { status, data } = await this.intakePost(
          "/api/scripts/intake/validate", {
            request_id: "reqv-" + seq + "-" + Date.now(),
            revision: s.revision,
            roles, events,
            name: s.form.name, language: s.form.language,
            shot_count: Number(s.form.shot_count) || 0,
            target_duration_seconds: Number(s.form.target_duration_seconds) || 20,
            audio_mode: s.form.audio_mode,
            camera: s.design.camera,
            look: s.design.look, setting: s.design.setting,
            scene: s.design.scene, constraints: s.design.constraints,
            light: "follow_image", light_text: "",
          });
        if (seq !== this.intakeValidateSeq) return;
        if (status !== 200) {
          s.validateState = { ok: false, checked: true,
                              errors: [{ event_index: null,
                                         message: data.error || "预检没有通过" }] };
          return;
        }
        s.validateState = {
          ok: !!data.validation.ok, checked: true,
          errors: data.validation.errors || [],
        };
      } catch (error) {
        if (seq !== this.intakeValidateSeq) return;
        s.validateState = { ok: false, checked: true,
                            errors: [{ event_index: null,
                                       message: error.message }] };
      }
    },

    /* 错误定位：第 index 条事件（可见列表里的）对应哪一行。 */
    intakeEventError(index) {
      const state = this.intake && this.intake.validateState;
      if (!state || !state.checked) return "";
      const rows = state.errors.filter((e) => e.event_index === index);
      return rows.map((e) => e.message).join("；");
    },

    get intakeHeaderErrors() {
      const state = this.intake && this.intake.validateState;
      if (!state || !state.checked) return [];
      return state.errors.filter((e) => e.event_index === null
                                 || e.event_index === undefined);
    },

    intakeCancelRunning() {
      const s = this.intake;
      if (!s) return;
      // 序号一加，在途响应回来也不认账。服务端的调用可能已经花了钱，
      // 但结果不许再碰当前页面（计划 §5）。
      s.requestSeq += 1;
      if (s.step === "reading") { s.step = "material"; s.readBusy = false; }
      if (s.step === "generating") {
        s.step = s.genFrom || (s.reading ? "understanding" : "material");
        s.genBusy = false;
        if (s.timer) { clearInterval(s.timer); s.timer = null; }
      }
    },

    /* ---------------- 生成：把确认过的理解组织成 concept --------------- */

    intakeConcept() {
      const s = this.intake;
      const parts = [];
      if (s.text.trim()) parts.push(s.text.trim());
      const reading = s.reading;
      const lines = [];
      if (reading) {
        if (reading.basis === "caption" && s.caption.trim()) {
          lines.push("（图片内容由作者提供图注，人工来源）");
          lines.push(s.caption.trim());
        } else {
          const observed = reading.observed
            .map((o) => o.text.trim()).filter(Boolean);
          const proposed = reading.proposed
            .map((p) => p.text.trim()).filter(Boolean);
          if (observed.length || proposed.length) lines.push("（配图信息，帮助理解故事）");
          observed.forEach((t) => lines.push("- " + t));
          proposed.forEach((t) => lines.push("- 剧情建议：" + t));
        }
      }
      (reading ? reading.conflicts : []).forEach((c) => {
        if (c.resolution === "text") {
          lines.push("- 图文冲突（以作者文字为准）：图中" + c.image
            + "；原文" + c.text);
        } else if (c.resolution === "image") {
          lines.push("- 图文冲突（以图片为准）：" + c.image);
        } else if (c.resolution === "manual" && c.override.trim()) {
          lines.push("- 图文冲突（作者裁定）：" + c.override.trim());
        }
      });
      if (lines.length) parts.push(lines.join("\n"));
      return parts.join("\n\n") || s.text.trim();
    },

    async intakeGenerate() {
      const s = this.intake;
      if (!s || s.genBusy) return;
      // 保留台词 / 一起设计走 script 模式：结构化确认稿逐事件锁定；
      // 允许改编的直接路径还是 concept（把素材组织成一段概念）。
      const scriptMode = s.step === "events" || this.intakeNeedsEvents;
      let body = null;
      let backStep = s.reading ? "understanding" : "material";
      if (scriptMode) {
        const draft = s.eventDraft;
        if (!draft || !this.intakeVisibleEvents.length) {
          s.genError = "事件稿是空的：先整理角色与事件。";
          return;
        }
        if (this.intakePendingPieces > 0
            || this.intakeUnconfirmedAdditions > 0) {
          s.genError = "还有没处理的原文片段或补写台词。";
          return;
        }
        // 生成前在服务端再验一次（计划 §5：前端标记不能替代校验）。
        await this.intakeValidate();
        if (!this.intakeValidateOk) {
          backStep = "events";
          s.step = "events";
          s.genError = "";
          return;
        }
        body = {
          mode: "script",
          roles: draft.roles.map((r) => ({
            name: r.name, presence: r.presence,
            initially_visible: r.initially_visible, position: r.position,
            voice: r.voice,
          })),
          events: this.intakeVisibleEvents
            .filter((e) => e.included)
            .map((e) => ({ role: e.role, type: e.type, text: e.text,
                           language: e.language })),
          name: s.form.name || "新剧本",
          shot_count: Number(s.form.shot_count) || 0,
          language: s.form.language,
          target_duration_seconds: Number(s.form.target_duration_seconds) || 20,
          audio_mode: s.form.audio_mode,
          critic: s.form.critic ? "on" : "off",
          camera: s.design.camera,
          look: s.design.look, setting: s.design.setting,
          scene: s.design.scene, constraints: s.design.constraints,
          light: "follow_image", light_text: "",
        };
      } else {
        const concept = this.intakeConcept();
        if (!concept.trim()) {
          s.genError = "素材是空的：写一段文字，或放一张图并解读。";
          return;
        }
        body = {
          mode: "concept", concept,
          name: s.form.name || "新剧本",
          shot_count: Number(s.form.shot_count) || 0,
          language: s.form.language,
          target_duration_seconds: Number(s.form.target_duration_seconds) || 20,
          audio_mode: s.form.audio_mode,
          critic: s.form.critic ? "on" : "off",
        };
      }
      s.genError = "";
      s.genFrom = scriptMode ? "events"
        : (s.reading ? "understanding" : "material");
      s.step = "generating";
      s.genBusy = true;
      s.waited = 0;
      if (s.timer) clearInterval(s.timer);
      s.timer = setInterval(() => { s.waited += 1; }, 1000);
      const seq = ++s.requestSeq;
      const revision = s.revision;
      const forScript = this.selected;
      try {
        const { status, data } = await this.intakePost("/api/scripts/auto", body);
        if (seq !== s.requestSeq) return;
        if (status !== 200) {
          s.step = scriptMode ? "events" : backStep;
          s.genError = data.error || "生成失败了";
          return;
        }
        s.candidate = { json: data.json, notes: data.notes || [],
                        revision, forScript };
        s.step = "candidate";
        this.intakeStash();
        // 编辑器干净、内容也没再动过：直接载入，少点一次。
        if (!this.isDirty && revision === s.revision) this.intakeLoadCandidate();
      } catch (error) {
        if (seq !== s.requestSeq) return;
        s.step = scriptMode ? "events" : backStep;
        s.genError = error.message;
      } finally {
        s.genBusy = false;
        if (s.timer) { clearInterval(s.timer); s.timer = null; }
      }
    },

    intakeLoadCandidate() {
      const s = this.intake;
      if (!s || !s.candidate) return;
      // 编辑器里有别人的东西（或生成期间切走了剧本）：要用户点头才覆盖。
      const sameScript = s.candidate.forScript === this.selected;
      if ((this.isDirty || !sameScript) && !this.confirmDiscard("载入新草稿")) {
        return;
      }
      this.applyDraftResult(s.candidate.json, s.candidate.notes, "从图片或文字起草");
      this.intakeLoaded = true;
      // script 路径的草稿带着确认稿：编辑器要能说出“你改过确认的事件”。
      const draft = s.eventDraft;
      if (draft && this.intakeVisibleEvents.length) {
        this.intakeConfirmed = {
          count: this.intakeVisibleEvents.filter((e) => e.included).length,
          signature: JSON.stringify(
            this.intakeVisibleEvents.filter((e) => e.included)
              .map((e) => [e.role, e.type, e.text, e.language])),
        };
      } else {
        this.intakeConfirmed = null;
      }
      s.candidate = null;
      s.step = s.reading ? "understanding" : "material";
      this.intakeOpen = false;
    },

    applyDraftResult(json, notes, changeNote) {
      this.model = normalizeStoryboardModel(json);
      this.cleaned = json;
      this.draftNotes = notes || [];
      this.jsonDirty = false;
      this.rawText = "";
      this.selected = ""; /* 还没入库：保存后才有名字 */
      this.changeNote = changeNote;
      this.syncDerived();
      this.queueValidate();
      this.okMessage = "草稿已生成：检查、修改后按「💾 保存」入库。";
    },

    /* intake 响应要带 code，不能走 api.post（它只留 error 文本）。 */
    async intakePost(url, body) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await response.json().catch(() => ({}));
      return { status: response.status, data };
    },

    /* ---------------- 浏览器暂存：文字在 localStorage，图在 IndexedDB -- */

    get intakeStashKey() { return "storyboard-intake:v1"; },

    async intakeStash() {
      const s = this.intake;
      if (!s) return;
      const meta = {
        schema_version: 1, updated_at: Date.now(),
        step: s.step === "reading" || s.step === "generating"
          ? (s.eventDraft ? "events"
             : (s.reading ? "understanding" : "material")) : s.step,
        flowMode: s.flowMode, textPolicy: s.textPolicy,
        text: s.text, caption: s.caption,
        hasCaption: s.hasCaption,
        reading: s.reading ? {
          vision: s.reading.vision, basis: s.reading.basis,
          observed: s.reading.observed, proposed: s.reading.proposed,
          conflicts: s.reading.conflicts,
        } : null,
        eventDraft: s.eventDraft,
        previousDraft: s.previousDraft,
        showDraftChoice: s.showDraftChoice,
        design: s.design,
        form: s.form,
        candidate: null,   /* 候选不暂存：它要么载入，要么重来 */
      };
      try {
        // 先图片后元数据：引用了 Blob 的记录发布时，Blob 一定已经在。
        if (s.image) await idbPut("intake-image", s.imageBlob || null);
        window.localStorage.setItem(this.intakeStashKey, JSON.stringify(meta));
        s.stashFailed = false;
      } catch {
        // 配额满了、隐私模式、IDB 坏了：页面照常工作，只是不承诺恢复。
        s.stashFailed = true;
      }
    },

    async intakeLoadStash() {
      let meta = null;
      try {
        const raw = window.localStorage.getItem(this.intakeStashKey);
        meta = raw ? JSON.parse(raw) : null;
      } catch { meta = null; }
      if (!meta || meta.schema_version !== 1) return;
      // 七天没碰过的草稿清掉（计划 §5 的产品默认值）。
      if (Date.now() - (meta.updated_at || 0) > 7 * 24 * 3600 * 1000) {
        await this.intakeClearStash();
        return;
      }
      this.intakeResume = meta;
    },

    async intakeResumeFromStash() {
      const meta = this.intakeResume;
      this.intakeResume = null;
      this.intakeResumed = true;
      if (!meta) return;
      this.openIntake();
      const s = this.intake;
      s.text = meta.text || "";
      s.flowMode = meta.flowMode === "guided" ? "guided" : "direct";
      s.textPolicy = meta.textPolicy === "keep" ? "keep" : "adapt";
      s.caption = meta.caption || "";
      s.hasCaption = !!meta.hasCaption;
      s.reading = meta.reading || null;
      if (meta.eventDraft && Array.isArray(meta.eventDraft.events)) {
        s.eventDraft = meta.eventDraft;
      }
      if (meta.previousDraft && Array.isArray(meta.previousDraft.events)) {
        s.previousDraft = meta.previousDraft;
        s.showDraftChoice = !!meta.showDraftChoice;
      }
      if (meta.design) s.design = Object.assign(s.design, meta.design);
      s.form = Object.assign(s.form, meta.form || {});
      s.step = s.eventDraft ? "events"
        : (meta.reading ? "understanding" : "material");
      // 恢复完成就自己预检（F04）：主按钮从“预检中…”走到可生成或
      // 明确错误，不需要用户去找“重新预检”。
      if (s.step === "events") this.intakeValidate();
      if (meta.reading && Array.isArray(meta.reading.conflicts)) {
        s.reading.conflicts = meta.reading.conflicts.map((c) => ({
          id: c.id, field: c.field || "", image: c.image || "",
          text: c.text || "", resolution: c.resolution || "",
          override: c.override || "",
        }));
      }
      // 图片单独住：丢失了就明说，文字部分照常回来。
      try {
        const blob = await idbGet("intake-image");
        if (blob instanceof Blob) {
          s.imageBlob = blob;
          s.imageDataUrl = await blobToDataUrl(blob);
          s.image = { name: "暂存的图片", width: 0, height: 0,
                      bytes: blob.size, key: "intake-image" };
        } else if (s.reading) {
          s.readError = "暂存的图片没能恢复（图没了），文字和理解还在；需要的话重新放一张。";
        }
      } catch {
        if (s.reading) s.readError = "暂存的图片没能恢复（图没了），文字和理解还在。";
      }
    },

    async intakeDropStash() {
      this.intakeResume = null;
      this.intakeResumed = true;
      await this.intakeClearStash();
    },

    async intakeClearStash() {
      try { window.localStorage.removeItem(this.intakeStashKey); } catch { /* 同上 */ }
      try { await idbDel("intake-image"); } catch { /* 同上 */ }
    },

    /* ---------------------------------------------------------------- */
    /* 版本                                                                */
    /* ---------------------------------------------------------------- */


    /* 每一版都是一份完整的剧本：引擎读的是当前版，旧版原样留着。
       旧版不是"另一个剧本"——它是同一份剧本的第几版。 */
    async loadVersions(name) {
      this.versionInfo = { current: 0, items: [] };
      if (!name) return;
      try {
        const data = await api.get(
          `/api/scripts/${encodeURIComponent(name)}/versions`);
        this.versionInfo = { current: data.current || 0, items: data.items || [] };
      } catch (error) {
        this.versionInfo = { current: 0, items: [] };
      }
    },

    /* 打开旧版看看：读进编辑器，不落库。基线不动，所以徽章是"未保存"——
       你正在看的是旧版，不是库里那份。 */
    async openVersion(number) {
      if (!this.selected || this.drafting) return;
      if (!this.confirmDiscard("打开旧版本")) return;
      try {
        const data = await api.get(
          `/api/scripts/${encodeURIComponent(this.selected)}/versions/${number}`);
        this.model = normalizeStoryboardModel(data.json);
        this.cleaned = data.json;
        this.rawText = ""; this.jsonDirty = false;
        this.tab = "edit";
        this.syncDerived();
        this.adoptGroupingFromShots();
        // 镜像是按剧本名记的，不是按版本记的：这一版的镜像对不对得上，
        // 服务端不知道，所以说"还没确认过"，不装作知道。
        this.executionState = "unknown";
        this.executionDraft = null;
        this.executionApplied = "";
        this.sourceSnapshot = this.sourceSignature();
        this.changeNote = `打开版本 ${number}`;
        this.queueValidate();
        this.okMessage = `打开的是版本 ${number}（现在的当前版是版本 `
          + `${this.versionInfo.current}）。改完按「💾 保存」会存成新的一版。`;
      } catch (error) {
        this.errors = [error.message];
      }
    },

    /* 把旧版恢复成当前版：不是把指针挪回去，而是把旧版的内容存成
       新的一版——历史只增不减，回退本身也在历史里。 */
    async restoreVersion(number) {
      if (!this.selected || this.drafting) return;
      // 恢复会重新打开这份剧本：编辑器里没保存的改动会没，所以要说。
      const dirty = this.isDirty
        ? "\n\n你在编辑器里还有没保存的改动，恢复之后那些改动就没了。"
        : "";
      if (!window.confirm(`把版本 ${number} 的内容恢复成当前版？\n\n`
        + `现在这一版不会被删掉：它会变成新的一版（版本 `
        + `${(this.versionInfo.current || 0) + 1}），历史只增不减。` + dirty)) return;
      try {
        const result = await api.post(
          `/api/scripts/${encodeURIComponent(this.selected)}/versions/${number}/restore`);
        const from = number;
        const to = result.content_version;
        await this.open(this.selected);
        this.okMessage = `已把版本 ${from} 的内容存成版本 ${to}，现在是当前版。`;
      } catch (error) {
        this.errors = [error.message];
      }
    },

    async deleteVersion(number) {
      if (!this.selected || this.drafting) return;
      if (!window.confirm(`删掉版本 ${number}？\n\n删了就没有了，救不回来。`)) return;
      try {
        await api.del(
          `/api/scripts/${encodeURIComponent(this.selected)}/versions/${number}`);
        await this.loadVersions(this.selected);
        this.okMessage = `版本 ${number} 删掉了。`;
      } catch (error) {
        this.errors = [error.message];
      }
    },

    /* ---------------------------------------------------------------- */
    /* 校验 / 保存 / 删除                                                  */
    /* ---------------------------------------------------------------- */

    queueValidate() {
      if (this.validateTimer) clearTimeout(this.validateTimer);
      this.validateTimer = setTimeout(() => {
        this.validateTimer = null;
        this.validateNow();
      }, 700);
    },

    async validateNow(explicit = false) {
      if (this.validateTimer) { clearTimeout(this.validateTimer); this.validateTimer = null; }
      if (!this.model) return;
      if (this.tab === "json" && !this.trySyncJson()) return;
      const ticket = ++this.validateSeq;
      try {
        const result = await api.post("/api/scripts/validate", { json: this.buildPayload() });
        if (ticket !== this.validateSeq) return;
        this.errors = result.errors || [];
        this.warnings = result.warnings || [];
        if (result.ok) {
          this.cleaned = result.json;
          if (explicit) this.okMessage = "契约通过";
        } else {
          this.okMessage = "";
        }
      } catch (error) {
        if (ticket !== this.validateSeq) return;
        this.errors = [error.message];
      }
    },

    async save() {
      if (!this.model) return;
      if (this.tab === "json" && !this.trySyncJson()) return;
      try {
        const result = await api.post("/api/scripts",
          { json: this.buildPayload(), note: this.changeNote || "手工修改" });
        this.selected = result.name;
        this.okMessage = `已保存：${result.name}（${result.status}）`;
        if (result.content_version) {
          this.okMessage += ` · 版本 ${result.content_version}`;
        }
        this.errors = []; this.warnings = [];
        this.changeNote = "";
        this.markSaved();
        // 存的是新源文本；镜像是否已经跟上，只有用户知道。
        if (this.executionState === "unknown") this.executionState = "changed";
        // 保存不等于确认镜像：源文本动了而英文没动，"过期"必须留着。
        // 只有这次会话里刚用"更新生成描述"换过镜像、而且换过之后
        // 中文一个字没动，才算已经跟上。
        if (this.executionApplied
            && this.executionApplied === this.sourceSignature()) {
          this.executionApplied = "";
          await this.confirmExecution();
        } else if (this.executionApplied) {
          this.executionApplied = "";
        }
        await this.load();
        await this.loadVersions(this.selected);
        this.queueValidate();
      } catch (error) {
        this.errors = [error.message];
        this.okMessage = "";
      }
    },

    async remove() {
      if (!this.selected) return;
      // 读不出来的剧本删掉就没有备份了：整个文件夹都会被拿走，
      // 里面那份文件也没了。所以这一次的确认要说清楚。
      // 版本历史也在那个文件夹里：删剧本连同历史一起没。
      const old = (this.versionInfo.items || []).length;
      const history = old > 1 ? `\n\n它的 ${old} 个版本也一起删掉，救不回来。` : "";
      const ask = this.loadError
        ? `删除 ${this.selected}？\n\n这份剧本读不出来，删除会把整个文件夹一起删掉，`
          + "里面的文件也不会留下备份。" + history
        : `删除 ${this.selected}？` + history;
      if (!window.confirm(ask)) return;
      try {
        await api.post(`/api/scripts/${encodeURIComponent(this.selected)}/delete`);
        this.selected = ""; this.model = null; this.cleaned = null;
        this.rawText = ""; this.parseEvents = []; this.grouping = [];
        this.baseline = "";
        this.loadError = "";
        this.versionInfo = { current: 0, items: [] };
        this.versionOpen = false;
        this.okMessage = "已删除";
        await this.load();
      } catch (error) {
        this.okMessage = `删除失败：${error.message}`;
      }
    },

    /* ---------------------------------------------------------------- */
    /* 渲染风险                                                           */
    /* ---------------------------------------------------------------- */

    get parsedWarnings() {
      return (this.warnings || []).map((text) => {
        const at = text.lastIndexOf("·");
        const tag = at >= 0 ? text.slice(at + 1).trim() : "";
        const meta = WARNING_META.find(([needle]) => tag.includes(needle))
          || ["", "渲染风险", "warn", "model", "按诊断把这一处改到没有风险"];
        return {
          text,
          label: meta[1],
          level: meta[2],
          kind: meta[3] || "model",
          fix: meta[4] || "",
          shot: (text.match(/^(scene_\d+)\s/) || [])[1] || "",
        };
      }).sort((a, b) => WARNING_LEVEL_ORDER[a.level] - WARNING_LEVEL_ORDER[b.level]
        || (a.shot < b.shot ? -1 : a.shot > b.shot ? 1 : 0));
    },

    /* 按这条风险的建议改。

       风险的诊断里已经写着该怎么改（"建议提高约 1 秒"），所以这条
       诊断可以直接当成修改要求交给模型：它重写一版草稿，作者看过
       再决定要不要存。诊断指出来的事，作者不必自己翻译一遍。 */
    async fixWarning(warning) {
      if (this.fixing || this.drafting || !warning) return;
      await this.applyRiskFix([warning.text]);
    },

    /* 一份剧本常常有好几条风险：一次全改，别让人一条条点。 */
    async fixAllWarnings() {
      if (this.fixing || this.drafting) return;
      const warnings = this.parsedWarnings.map((w) => w.text);
      if (!warnings.length) return;
      await this.applyRiskFix(warnings);
    },

    /* 一次改完：服务端先按契约算时长，再把要判断的合成一次请求交给
       模型，最后按模型写出来的台词重算时长。

       顺序在服务端是有道理的：模型看到的是已经装得下的那一版，改
       完再算一次时长，才知道它写出来的台词够不够时间。在页面这边
       分两步做，第二步会把第一步的时长改覆盖掉——所以一次做完。 */
    async applyRiskFix(warnings) {
      if (this.fixing) return;
      this.fixing = true;
      this.errors = []; this.okMessage = "";
      const sent = this.editSnapshot();
      try {
        const result = await api.post("/api/scripts/risk/fix",
          { json: this.buildPayload(), warnings: warnings });
        // 等模型的这几十秒里，作者可能又改了：改好的那一版是按改之前
        // 的那一版算的，直接盖上去会把刚改的东西弄没了。先问一句。
        const now = this.editSnapshot();
        if (JSON.stringify(now) !== JSON.stringify(sent)) {
          const ask = "你在等它改的时候又动了这一版（"
            + this.editSummary(sent, now).join("、")
            + "）。\n\n改好的那一版是按你改之前的那一版算的。要盖上去吗？\n"
            + "盖上去，你刚才改的就没了；不盖的话这次改动作废，"
            + "再点一次就是按你现在的样子改。";
          if (!window.confirm(ask)) {
            this.okMessage = "这一次的改动作废了（你在等的时候改了这一版）。"
              + "再点一次就是按你现在的样子改。";
            return;
          }
        }
        this.adoptFixedBoard(result.json);
        this.changeNote = "按风险改";
        const notes = (result.notes || []).filter(Boolean);
        let message = notes.length ? notes.join(" ")
          : "已经按这些风险改好了";
        message += "（还没存盘：看过再按「💾 保存」）";
        const left = (result.remaining || []).length;
        if (left) message += " 还有 " + left + " 条没消掉，页面上写着原因。";
        this.okMessage = message;
      } catch (error) {
        this.errors = [error.message];
      } finally {
        this.fixing = false;
      }
    },

    /* 编辑的版本记号：等模型的时候作者又动了哪儿，一比就知道。 */
    editSnapshot() {
      const model = this.model || {};
      return {
        name: model.name || "",
        look: model.look || "",
        setting: model.setting || "",
        constraints: model.constraints || "",
        source_script: model.source_script || "",
        target_duration_seconds: model.target_duration_seconds,
        camera: model.camera || {},
        roles: model.roles || [],
        execution: model.execution || {},
        shots: this.buildShots(),
      };
    },

    /* 说清楚改了哪儿，才好让人决定要不要盖上去。 */
    editSummary(before, after) {
      const names = [];
      if (before.name !== after.name) names.push("名字");
      if (before.look !== after.look) names.push("画风");
      if (before.setting !== after.setting) names.push("场景");
      if (before.constraints !== after.constraints) names.push("限制");
      if (before.source_script !== after.source_script) names.push("台词");
      if (before.target_duration_seconds !== after.target_duration_seconds) {
        names.push("目标时长");
      }
      if (JSON.stringify(before.camera) !== JSON.stringify(after.camera)) {
        names.push("镜头");
      }
      if (JSON.stringify(before.roles) !== JSON.stringify(after.roles)) {
        names.push("角色");
      }
      if (JSON.stringify(before.execution) !== JSON.stringify(after.execution)) {
        names.push("英文镜像");
      }
      if (JSON.stringify(before.shots) !== JSON.stringify(after.shots)) {
        names.push("分镜");
      }
      return names.length ? names : ["内容"];
    },

    /* 改好的稿子整个换进来：分镜结构和分镜时长都按服务端算好的来，
       不然模型拆开的那一镜会在这里又被合回去。 */
    adoptFixedBoard(board) {
      if (!board) return;
      this.model = normalizeStoryboardModel(board);
      this.cleaned = board;
      this.rawText = "";
      this.jsonDirty = false;
      this.syncDerived();
      this.adoptGroupingFromShots();
      this.queueValidate();
    },

    get warningChips() {
      const groups = new Map();
      for (const warning of this.parsedWarnings) {
        const group = groups.get(warning.label)
          || { label: warning.label, level: warning.level, count: 0 };
        group.count += 1;
        if (WARNING_LEVEL_ORDER[warning.level] < WARNING_LEVEL_ORDER[group.level]) {
          group.level = warning.level;
        }
        groups.set(warning.label, group);
      }
      return [...groups.values()]
        .sort((a, b) => WARNING_LEVEL_ORDER[a.level] - WARNING_LEVEL_ORDER[b.level]);
    },

    warningsForShot(shotId) {
      return this.parsedWarnings.filter((warning) => warning.shot === shotId);
    },

    /* 与服务端同一机械判据（contract.storyboard_warnings）：不含可发音
     * 字符的对白，H3 会渲染成消息提示音而不是沉默。 */
    riskyEvent(event) {
      return event.type === "dialogue"
        && !/[\p{L}\p{N}]/u.test(String(event.text));
    },

    /* ---------------------------------------------------------------- */
    /* 预览                                                              */
    /* ---------------------------------------------------------------- */

    get board() {
      if (this.cleaned) return this.cleaned;
      try { return this.model ? this.buildPayload() : null; } catch { return null; }
    },

    get timeline() {
      const shots = (this.board && this.board.shots) || [];
      const total = shots.reduce((sum, shot) => sum + (shot.duration_seconds || 0), 0) || 1;
      return shots.map((shot) => ({
        id: shot.id,
        seconds: shot.duration_seconds || 0,
        width: Math.max(6, Math.round(((shot.duration_seconds || 0) / total) * 100)),
      }));
    },

    get deliveredSeconds() {
      const board = this.board;
      if (!board || !board.shots || !board.shots.length) return 0;
      const overlap = (board.continuation_overlap_frames ?? 22) / 24;
      return board.shots.reduce((sum, shot) => sum + (shot.duration_seconds || 0), 0)
        - overlap * (board.shots.length - 1);
    },

    roleName(id) {
      const role = ((this.board && this.board.roles) || []).find((r) => r.id === id);
      return role ? role.name : id;
    },

    eventTypeName(type) {
      return SCRIPT_EVENT_NAMES[type] || type;
    },

    formatSeconds(value) {
      return (Math.round((Number(value) || 0) * 100) / 100).toFixed(2) + "s";
    },

    scrollToShot(id) {
      const element = document.getElementById("shot-" + id);
      if (element) element.scrollIntoView({ behavior: "smooth", block: "start" });
    },
  };
}

/* ------------------------------------------------------------------ */
/* 视频制作                                                             */
/* ------------------------------------------------------------------ */

function videoPage() {
  return {
    scripts: [],
    characters: [],
    script: "",
    roles: [],
    runName: "",
    width: 960,
    height: 544,
    steps: 20,
    duration: 18,
    planPreview: "",
    planError: "",
    message: "",
    statusText: "",
    effectiveRunName: "",
    nextTitle: "",           /* 这一次生成的作品名（第几版） */
    submitting: false,
    assetDetails: {},
    scriptJson: null,           /* 选中的剧本：摊开给作者核对 */
    executionState: "unknown",  /* 剧本的英文镜像跟不跟得上中文 */
    executionReason: "",
    staleOverride: false,       /* 明知镜像过期，仍要用旧版生成 */

    async init() {
      await this.loadScripts();
      // 从剧本页「用此剧本制作」过来时，URL 里带着剧本名。
      const wanted = new URLSearchParams(window.location.search).get("script");
      if (wanted && this.scripts.includes(wanted)) {
        this.script = wanted;
        await this.loadScript();
      }
      await this.refreshCount();
    },

    async loadScripts() {
      try {
        const data = await api.get("/api/scripts");
        this.scripts = data.names || [];
        const assets = await api.get("/api/assets");
        this.characters = (assets.characters || [])
          .filter((x) => x && x.name).map((x) => x.name);
      } catch (error) {
        this.message = `读取失败：${error.message}`;
      }
    },

    /* 选中一个角色资产后，取它的立绘和描述：选错人等于换了张脸，
     * 光看名字看不出来。 */
    async loadAssetDetail(name) {
      if (!name || this.assetDetails[name]) return;
      try {
        const data = await api.get(
          `/api/assets/character/${encodeURIComponent(name)}`);
        this.assetDetails[name] = data;
      } catch (error) {
        this.assetDetails[name] = { name, image: "", description: "" };
      }
    },

    async onRoleAssetChange(role) {
      await this.loadAssetDetail(role.asset);
      await this.preview();
    },

    /* 名字像的角色资产，摆到前面当建议。 */
    suggestionsFor(role) {
      const wanted = (role.name || "").trim().toLowerCase();
      if (!wanted) return [];
      return this.characters.filter((name) => {
        const lower = name.toLowerCase();
        return lower.includes(wanted) || wanted.includes(lower);
      }).slice(0, 5);
    },

    async loadScript() {
      this.roles = [];
      this.planPreview = "";
      this.message = "";
      this.executionState = "unknown";
      this.executionReason = "";
      this.staleOverride = false;
      this.scriptJson = null;
      if (!this.script) { this.effectiveRunName = ""; return; }
      try {
        const data = await api.get(`/api/scripts/${encodeURIComponent(this.script)}`);
        this.scriptJson = data.json;
        this.roles = (data.json.roles || []).map((r) => ({
          id: r.id, name: r.name, presence: r.presence, asset: "",
        }));
        // 英文镜像跟不跟得上中文：生成实际读的是英文，源文本改了而
        // 镜像没改，就是拿旧描述去生成。这里必须说出来。
        this.executionState = data.execution_state || "unknown";
        this.executionReason = data.execution_reason || "";
        await this.preview();
        await this.refreshRunName();
      } catch (error) {
        this.message = `读取失败：${error.message}`;
      }
    },

    /* 镜像过期时不能闷头生成。要么去更新，要么明说"我知道，仍用旧版"。 */
    get executionStale() {
      return this.executionState === "changed" || this.executionState === "edited";
    },

    get staleBlocked() {
      return this.executionStale && !this.staleOverride;
    },

    /* 引擎逐字接收 run_name，所以这里显示真正会提交的那个名字。 */
    async refreshRunName() {
      try {
        const data = await api.post("/api/render/run_name", {
          script: this.script, run_name: this.runName,
        });
        this.effectiveRunName = data.run_name || "";
        this.nextTitle = data.title || "";
      } catch (error) {
        this.effectiveRunName = "";
        this.nextTitle = "";
      }
    },

    roleAssets() {
      const out = {};
      this.roles.forEach((r) => { if (r.asset) out[r.id] = r.asset; });
      return out;
    },

    /* 预检：每个角色都要接上资产，否则后端会拒绝，页面也该先说清楚。 */
    get missingRoles() {
      return this.roles.filter((r) => !r.asset).map((r) => r.name);
    },

    get canSubmit() {
      return !!(this.script && this.roles.length && !this.missingRoles.length
        && !this.submitting && !this.staleBlocked);
    },

    /* 选中的剧本摊开：不然作者没法确认自己选对了没有。 */
    get scriptPreview() {
      const json = this.scriptJson;
      if (!json) return null;
      const shots = (json.shots || []).map((shot, index) => ({
        index: index + 1,
        id: shot.id,
        seconds: Number(shot.duration_seconds) || 0,
        events: (shot.events || []).map((event, eventIndex) => ({
          // 同一角色说同一句话是常事，所以 key 不能是台词：重复的 key
          // 会让 Alpine 整个列表渲染不出来，页面上一条都不剩。
          key: event.id || (shot.id || "shot") + "-" + eventIndex,
          type: event.type,
          who: event.character,
          text: event.text || "",
        })),
      }));
      const total = shots.reduce((sum, shot) => sum + shot.seconds, 0);
      return {
        source: json.source_script || "",
        shots: shots,
        roles: (json.roles || []).map((r) => ({
          id: r.id, name: r.name, presence: r.presence,
        })),
        // 事件里记的是角色编号，作者要看的是名字。
        names: (json.roles || []).reduce((out, r) => {
          out[r.id] = r.name || r.id;
          return out;
        }, {}),
        target: json.target_duration_seconds,
        total: total,
        audio: json.audio_mode === "reference" ? "参考音" : "生成音",
        look: json.look || "",
        setting: json.setting || "",
      };
    },

    eventTypeName(type) {
      return SCRIPT_EVENT_NAMES[type] || type;
    },

    /* 角色编号换成角色名：作者不认 character_1。 */
    roleName(id) {
      const names = (this.scriptPreview && this.scriptPreview.names) || {};
      return names[id] || id || "";
    },

    async preview() {
      this.planError = "";
      if (!this.script || !this.roles.length) return;
      // The assembler refuses a slot with no asset, so there is nothing to
      // preview until every role has one. The page says which ones are
      // missing instead of asking and swallowing the error.
      if (this.missingRoles.length) {
        this.planPreview = "";
        this.planError = "还没接资产的角色：" + this.missingRoles.join("、");
        return;
      }
      try {
        const data = await api.post(
          `/api/scripts/${encodeURIComponent(this.script)}/plan`,
          { role_assets: this.roleAssets() });
        this.planPreview = (data.requirements || [])
          .map((r) => `${r.socket}: ${r.kind} ${r.name || "(未接线)"}`
            + `${r.required ? " [必填]" : ""}${r.note ? " — " + r.note : ""}`)
          .join("\n");
      } catch (error) {
        this.planPreview = "";
        this.planError = `计划预览失败：${error.message}`;
      }
    },

    async submit() {
      if (this.submitting) return;
      if (!this.canSubmit) {
        this.message = this.missingRoles.length
          ? "还有角色没接资产：" + this.missingRoles.join("、")
          : (this.staleBlocked
            ? "英文镜像跟中文设定对不上：先去剧本页「更新生成描述」，"
              + "或勾选下面的「仍用旧版生成」。"
            : "先选一个剧本。");
        return;
      }
      this.submitting = true;
      this.message = "提交中…";
      try {
        const record = await api.post("/api/render/submit", {
          script: this.script,
          role_assets: this.roleAssets(),
          run_name: this.runName,
          width: this.width, height: this.height,
          steps: this.steps, duration: this.duration,
        });
        this.message = `已排队（#${record.number}），渲染要几分钟`;
        await this.refreshCount();
      } catch (error) {
        this.message = `提交失败：${error.message}`;
      } finally {
        this.submitting = false;
      }
    },

    /* 这一页只管提交。任务本身在制作历史页。 */
    async refreshCount() {
      try {
        const data = await api.get("/api/render/jobs?per_page=1");
        const total = data.total || 0;
        this.statusText = total ? `历史里共 ${total} 条` : "";
      } catch (error) {
        this.statusText = "";
      }
    },

  };
}

/* ------------------------------------------------------------------ */
/* 任务卡片：制作页与历史页共用的那几件事                             */
/* ------------------------------------------------------------------ */

const jobView = {
  /* 任务状态：说人话。磁盘上只剩片段的运行不是"正在跑"。 */
  statusLabel(status) {
    return {
      success: "完成", error: "失败", running: "运行中", queued: "排队中",
      partial: "部分产物", interrupted: "已中断", empty: "无产物",
      unknown: "未知",
    }[status] || status;
  },

  statusClass(status) {
    if (status === "success") return "ok";
    if (status === "error" || status === "interrupted") return "err";
    if (status === "running" || status === "queued") return "warn";
    return "";
  },

  /* 成片的时长：manifest 里写着，没有就不知道，不编。 */
  durationText(manifest) {
    const seconds = manifest && manifest.duration_seconds;
    if (typeof seconds !== "number" || !isFinite(seconds)) return "";
    const clips = manifest.clip_count;
    const shots = clips ? ` · ${clips} 个分镜` : "";
    return `时长 ${seconds.toFixed(1)} 秒${shots}`;
  },

  timeText(stamp) {
    if (!stamp) return "";
    const date = new Date(stamp * 1000);
    if (isNaN(date.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
      + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  },

  /* 接着上次那个任务继续：同样的角色绑定、参数，进同一个目录。 */
  canContinue(job) {
    if (!job || !job.script || !job.run_name) return false;
    return ["success", "error", "interrupted", "partial", "empty"].indexOf(
      job.status) >= 0;
  },
};

/* ------------------------------------------------------------------ */
/* 制作历史                                                          */
/* ------------------------------------------------------------------ */

function historyPage() {
  return {
    items: [],
    total: 0,
    page: 1,
    pages: 1,
    perPage: 20,
    filter: "all",
    activeCount: 0,        /* 全局在跑的任务数（不只看当前页） */
    loading: false,
    message: "",
    statusText: "",
    playingJob: "",
    submitting: false,
    pollTimer: null,

    async init() {
      await this.load();
      this.startPolling();
    },

    /* 有任务在跑就自己刷新：一次失败的提交原来会永远停在 "running"。 */
    startPolling() {
      if (this.pollTimer) return;
      this.pollTimer = setInterval(() => {
        if (this.hasActiveJob) this.load();
      }, 3000);
    },

    get hasActiveJob() {
      return (this.activeCount || 0) > 0;
    },

    async load() {
      this.loading = true;
      try {
        // 筛选在服务端做：放在翻页之后会漏掉后面几页的任务，
        // 总数也会算错。
        const query = `/api/render/jobs?page=${this.page}`
          + `&per_page=${this.perPage}`
          + (this.filter === "favorite" ? "&favorites=1" : "")
          + (["running", "done", "failed"].indexOf(this.filter) >= 0
            ? `&status=${this.filter}` : "");
        const data = await api.get(query);
        this.items = data.items || [];
        this.total = data.total || 0;
        this.page = data.page || 1;
        this.pages = data.pages || 1;
        // 在跑的任务是全局的：当前这一页没有，不代表没有人在跑。
        this.activeCount = data.active || 0;
        this.statusText = this.total ? `共 ${this.total} 条` : "";
      } catch (error) {
        this.message = `读取失败：${error.message}`;
      } finally {
        this.loading = false;
      }
    },

    setFilter(filter) {
      this.filter = filter;
      this.page = 1;
      this.load();
    },

    goTo(page) {
      const target = Math.max(1, Math.min(page, this.pages || 1));
      if (target === this.page) return;
      this.page = target;
      this.load();
    },

    /* 播放器是点开才挂的：一次挂二十几个，每个都会去拉一次 mp4 的
       元数据，页面会卡住。 */
    playJob(job) {
      this.playingJob = job.job_id;
    },

    async toggleFavorite(job) {
      try {
        const data = await api.post("/api/render/favorites",
          { run_name: job.run_name });
        job.favorite = !!data.favorite;
        this.message = data.favorite
          ? `已收藏：${job.title || job.run_name}`
          : `已取消收藏：${job.title || job.run_name}`;
        if (this.filter === "favorite") this.load();
      } catch (error) {
        this.message = `收藏失败：${error.message}`;
      }
    },

    /* 移出列表：硬盘上的成片不动。 */
    async removeRecord(job) {
      if (!window.confirm(
        `把 ${job.title || job.run_name} 从历史里移出？\n\n`
        + "硬盘上的成片不会删，只是不再出现在这个列表里。")) return;
      try {
        await api.post(`/api/render/jobs/${encodeURIComponent(job.job_id)}/delete`,
          { files: false });
        this.message = `已移出：${job.title || job.run_name}（文件还在硬盘上）`;
        await this.load();
      } catch (error) {
        this.message = `移出失败：${error.message}`;
      }
    },

    /* 连文件一起删：这一步无法恢复，确认框要说清楚。 */
    async removeFiles(job) {
      if (!window.confirm(
        `删掉 ${job.title || job.run_name} 的成片和全部文件？\n\n`
        + "这一下是真的删掉硬盘上的文件，没有备份，也找不回来。")) return;
      try {
        await api.post(`/api/render/jobs/${encodeURIComponent(job.job_id)}/delete`,
          { files: true });
        this.message = `已删除：${job.title || job.run_name}`;
        await this.load();
      } catch (error) {
        this.message = `删除失败：${error.message}`;
      }
    },

    async continueJob(job) {
      if (this.submitting) return;
      const params = job.params || {};
      this.submitting = true;
      this.message = `正在接着 ${job.title || job.run_name} 继续…`;
      try {
        // continue_from 让服务端用原任务固定的那份剧本：续跑是接着
        // 那个任务走，不是拿资产库里现在这份重新来过。
        const record = await api.post("/api/render/submit", {
          script: job.script,
          role_assets: job.role_assets || {},
          run_name: job.run_name,
          width: params.width || 960,
          height: params.height || 544,
          steps: params.steps || 20,
          duration: params.duration || 18,
          continue_run: true,
          continue_from: job.job_id || "",
        });
        this.message = `已排队（#${record.number}）：接着 ${job.run_name} 继续`;
        await this.load();
      } catch (error) {
        this.message = `继续失败：${error.message}`;
      } finally {
        this.submitting = false;
      }
    },

    statusLabel(status) { return jobView.statusLabel(status); },
    statusClass(status) { return jobView.statusClass(status); },
    durationText(manifest) { return jobView.durationText(manifest); },
    timeText(stamp) { return jobView.timeText(stamp); },
    canContinue(job) { return jobView.canContinue(job); },
  };
}
