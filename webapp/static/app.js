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
};

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
      return (list || []).filter((x) => x && x.name);
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
const WARNING_META = [
  ["punctuation-only dialogue", "标点台词变提示音", "err"],
  ["speech and action budget too tight", "慢速塞满会丢句", "warn"],
  ["shot budget at the floor", "预算贴地", "warn"],
  ["duration pinned at the feasibility floor", "目标时长零缓冲", "warn"],
  ["single-speaker continuation clip", "单人续接缺陷入形", "warn"],
  ["long same-speaker burst", "连珠炮口型漂移", "warn"],
  ["shouting delivery", "喊叫语速偏慢", "info"],
  ["text-only crowds", "群像开场泄漏", "info"],
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
    sourceSnapshot: "",        /* 打开/保存时的中文源文本（镜像陈旧的判据） */
    executionState: "unknown", /* confirmed | changed | unknown */
    draftTimer: null,
    recoveredDraft: null,      /* 本地找到的草稿，等人决定恢复还是丢弃 */
    draftOpen: false,
    drafting: false,
    draftNotes: [],
    draftForm: { mode: "concept", concept: "", name: "新剧本", shot_count: 2,
                 language: "zh", target_duration_seconds: 20,
                 audio_mode: "generated", character_settings: "", critic: true },
    cleaned: null,             /* 最近一次契约通过的权威 JSON（预览数据源） */
    validateTimer: null,
    validateSeq: 0,

    async init() {
      await this.load();
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

    /* 只记基线，不动草稿：打开一份剧本时用它，之后还要去读草稿。 */
    setBaseline() {
      this.baseline = JSON.stringify(this.model || {});
      this.sourceSnapshot = this.sourceSignature();
    },

    /* 真的保存成功之后才用这个：基线更新，草稿也清掉。 */
    markSaved() {
      this.setBaseline();
      this.clearDraft();
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

    /* 镜像陈旧：源文本在这次会话里动过，或者它上次被确认时源文本还不是现在这样。 */
    get executionStale() {
      if (!this.model) return false;
      if (this.executionState === "changed") return true;
      const snapshot = this.sourceSnapshot;
      return !!snapshot && snapshot !== this.sourceSignature();
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
      return parts.join(" · ");
    },

    async open(name) {
      if (name !== this.selected && !this.confirmDiscard("切换剧本")) return;
      this.selected = name;
      this.errors = []; this.warnings = []; this.okMessage = ""; this.warnOpen = false;
      this.draftOpen = false; this.draftNotes = [];
      this.recoveredDraft = null;
      this.loadError = "";
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
        // 草稿先读，再记基线：记基线不碰草稿，但顺序也不能反 -—
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
      this.errors = []; this.warnings = []; this.okMessage = "";
      this.warnOpen = false;
      this.draftNotes = [];
      this.draftForm.mode = mode === "revise" ? "revise" : "concept";
      if (this.draftForm.mode === "revise" && this.model) {
        // 修订只允许改名字和改动意见：接线事实（配音/重叠/结构）由底稿继承。
        this.draftForm.name = this.model.name || "新剧本";
      } else {
        this.draftForm.mode = "concept";
        this.draftForm.name = "新剧本";
        this.draftForm.shot_count = 2;
        this.draftForm.language = "zh";
        this.draftForm.target_duration_seconds = 20;
        this.draftForm.audio_mode = "generated";
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
        if (this.draftForm.mode === "revise") body.base_asset = this.model;
        const result = await api.post("/api/scripts/auto", body);
        this.model = normalizeStoryboardModel(result.json);
        this.cleaned = result.json;
        this.draftNotes = result.notes || [];
        this.jsonDirty = false;
        this.rawText = "";
        this.selected = ""; /* 还没入库：保存后才有名字 */
        this.draftOpen = false;
        this.tab = "edit";
        this.okMessage = "草稿已生成：检查、修改后按「💾 保存」入库。";
        this.syncDerived();
        this.queueValidate();
      } catch (error) {
        this.errors = [error.message];
      } finally {
        this.drafting = false;
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
        const result = await api.post("/api/scripts", { json: this.buildPayload() });
        this.selected = result.name;
        this.okMessage = `已保存：${result.name}（${result.status}）`;
        this.errors = []; this.warnings = [];
        this.markSaved();
        // 存的是新源文本；镜像是否已经跟上，只有用户知道。
        if (this.executionState === "unknown") this.executionState = "changed";
        await this.load();
        this.queueValidate();
      } catch (error) {
        this.errors = [error.message];
        this.okMessage = "";
      }
    },

    async remove() {
      if (!this.selected) return;
      if (!window.confirm(`删除 ${this.selected}？`)) return;
      try {
        await api.post(`/api/scripts/${encodeURIComponent(this.selected)}/delete`);
        this.selected = ""; this.model = null; this.cleaned = null;
        this.rawText = ""; this.parseEvents = []; this.grouping = [];
        this.baseline = "";
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
          || ["", "渲染风险", "warn"];
        return {
          text,
          label: meta[1],
          level: meta[2],
          shot: (text.match(/^(scene_\d+)\s/) || [])[1] || "",
        };
      }).sort((a, b) => WARNING_LEVEL_ORDER[a.level] - WARNING_LEVEL_ORDER[b.level]
        || (a.shot < b.shot ? -1 : a.shot > b.shot ? 1 : 0));
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
    jobs: [],
    planPreview: "",
    planError: "",
    message: "",
    statusText: "",
    effectiveRunName: "",
    pollTimer: null,
    submitting: false,
    assetDetails: {},

    async init() {
      await this.loadScripts();
      // 从剧本页「用此剧本制作」过来时，URL 里带着剧本名。
      const wanted = new URLSearchParams(window.location.search).get("script");
      if (wanted && this.scripts.includes(wanted)) {
        this.script = wanted;
        await this.loadScript();
      }
      await this.refreshJobs();
      this.startPolling();
    },

    /* 有任务在跑就自己刷新：一次失败的提交原来会永远停在 "running"，
     * 因为页面从提交那一刻起就不再看 ComfyUI 了。 */
    startPolling() {
      if (this.pollTimer) return;
      this.pollTimer = setInterval(() => {
        if (this.hasActiveJob) this.refreshJobs();
      }, 3000);
    },

    get hasActiveJob() {
      return (this.jobs || []).some(
        (job) => job.status === "running" || job.status === "queued");
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
      if (!this.script) { this.effectiveRunName = ""; return; }
      try {
        const data = await api.get(`/api/scripts/${encodeURIComponent(this.script)}`);
        this.roles = (data.json.roles || []).map((r) => ({
          id: r.id, name: r.name, presence: r.presence, asset: "",
        }));
        await this.preview();
        await this.refreshRunName();
      } catch (error) {
        this.message = `读取失败：${error.message}`;
      }
    },

    /* 引擎逐字接收 run_name，所以这里显示真正会提交的那个名字。 */
    async refreshRunName() {
      try {
        const data = await api.post("/api/render/run_name", {
          script: this.script, run_name: this.runName,
        });
        this.effectiveRunName = data.run_name || "";
      } catch (error) {
        this.effectiveRunName = "";
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
        && !this.submitting);
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
          : "先选一个剧本。";
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
        await this.refreshJobs();
      } catch (error) {
        this.message = `提交失败：${error.message}`;
      } finally {
        this.submitting = false;
      }
    },

    async refreshJobs() {
      try {
        const data = await api.get("/api/render/jobs");
        this.jobs = data.jobs || [];
        this.statusText = this.jobs.length
          ? `${this.jobs.length} 个任务`
          : "";
      } catch (error) {
        this.statusText = "";
      }
    },

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
  };
}
