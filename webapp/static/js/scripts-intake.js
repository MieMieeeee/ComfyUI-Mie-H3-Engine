/* 剧本页的「接入」这一半：剧本的模型，和从素材起草的那条路。
 *
 * 这里交出去两样东西：
 *
 * * 剧本模型的那几个共用函数（形状归一、源文本解析、分镜时长估算）——
 *   编辑器那一半也要用，所以放在这里：编辑器加载得比这一份晚。
 * * ``scriptsIntakePart``：接入流程自己的字段、方法与 getter。它和
 *   编辑器是同一个 Alpine 组件（页面只有一个 x-data），所以那边用
 *   Object.defineProperties 把它挂进同一个对象 —— getter 必须原样挂
 *   过去，Object.assign 会把 getter 取成一个静态值，「现在该按哪个
 *   按钮」就再也不动了。
 *
 * 依赖的浏览器原生能力：fetch（网络）、IndexedDB（图片暂存，
 * localStorage 装不下）、FileReader（读图）、localStorage（文字暂存）。
 */

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
/* 剧本资产                                                             */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/* 从图片或文字起草（intake）：素材 → 读图 → 确认理解 → 草稿          */
/* ------------------------------------------------------------------ */

const scriptsIntakePart = {
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
        storySummary: "",     /* 一句话故事摘要（U05）：核对“这版在讲什么” */
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
        modelInfo: null,      /* 当前平台/模型（U07），打开卡片时重读 */
        stashFailed: false,
      };
    }
    this.intakeOpen = true;
    this.intakeRefreshModelInfo();
    this.bringCardIntoView(".intake-card", ".intake-material textarea");
  },

  /* U08：窄屏上列表和说明占了首屏，表单在折叠线以下——打开就
   * 滚过去，焦点落在第一个输入上。 */
  bringCardIntoView(cardSelector, focusSelector) {
    const afterLayout = () => requestAnimationFrame(() => {
      const card = document.querySelector(cardSelector);
      if (focusSelector) {
        const input = document.querySelector(focusSelector);
        if (input) input.focus({ preventScroll: true });
      }
      requestAnimationFrame(() => {
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
    // Alpine must first reveal the card; focusing must finish before
    // scrolling, or Chromium can move the title above the viewport.
    if (this.$nextTick) this.$nextTick(afterLayout);
    else afterLayout();
  },

  /* U07：起草卡顶上说清“现在用的是谁”。没配置就给直达设置的入口；
   * 每次打开都重读——模型换过而理解/请求还揣着旧平台的假设，
   * 是解读失败的常见根因。 */
  async intakeRefreshModelInfo() {
    const s = this.intake;
    if (!s) return;
    const before = s.modelInfo ? s.modelInfo.key : "";
    try {
      const data = await api.get("/api/llm/platforms");
      const row = (data.platforms || []).find((x) => x.key === data.current);
      s.modelInfo = data.current
        ? { key: data.current, name: row ? row.name : data.current,
            model: row ? (row.model || "") : "",
            configured: !row || row.needs_key === false || row.has_key }
        : null;
    } catch {
      s.modelInfo = null;
    }
    // 平台换过：在途请求作废，已确认内容保留（用户重读即可）。
    if (before && s.modelInfo && s.modelInfo.key !== before) {
      s.requestSeq += 1;
      if (s.readBusy) { s.readBusy = false; s.step = "material"; }
      s.readError = "大模型平台已切换为 " + s.modelInfo.name
        + "：之前的理解结果按旧平台得出，建议重新解读一次。";
    }
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
    // A role label is a NAME and a short line: "小明：今天天气不错。".
    // Prose with a colon - "末班车上只剩两个人：一个抱着吉他箱的年轻人…"
    // - looks like one to a naive regex, and treating it as a script
    // would eat the whole paragraph as one line of dialogue (found by
    // the 2026-11-15 verification run).
    const labelled = lines.filter((t) => {
      const m = /^([^\s：:，,]{1,12})(\s*(动作|对白|入场|离场))?\s*(\[(zh|en)\])?\s*[：:](.*)$/.exec(t);
      if (!m) return false;
      // The groups: 1 name, 2 kind, 3 kind word, 4 [lang], 5 lang, 6 body.
      const body = (m[6] || "").trim();
      if (!body || body.length > 30) return false;
      // A body with clauses is prose, not a line of dialogue.
      if (body.length > 15 && /[，,、]/.test(body)) return false;
      return true;
    });
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
      exists: null,           /* 同名的现有资产（F06），等作者决定 */
      croppedBlob: null,      /* 多人物图裁出来的区域（F07） */
      croppedUrl: "",
      cropOpen: false,
    };
  },

  /* ---------------- 多人物图的区域裁剪（F07） ------------------------ */

  /* 打开裁剪：图铺满 canvas，拖一个矩形就是要保存的区域。 */
  async intakeOpenCrop() {
    const s = this.intake;
    const form = s && s.assetForm;
    if (!form || !s.imageBlob) return;
    form.cropOpen = true;
    await this.$nextTick;
    const canvas = document.querySelector(".intake-card .crop-canvas");
    if (!canvas) return;
    const bitmap = await createImageBitmap(s.imageBlob);
    const maxEdge = 360;   // 展示用尺寸；裁剪按原图坐标算
    const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas._bitmap = bitmap;
    canvas._scale = scale;
    canvas._rect = null;   // [x0, y0, x1, y1] 展示坐标
    this.intakeDrawCrop(canvas);

    const state = { dragging: false, x0: 0, y0: 0 };
    const position = (event) => {
      const box = canvas.getBoundingClientRect();
      return [event.clientX - box.left, event.clientY - box.top];
    };
    const down = (event) => {
      state.dragging = true;
      [state.x0, state.y0] = position(event);
      canvas._rect = [state.x0, state.y0, state.x0, state.y0];
      this.intakeDrawCrop(canvas);
    };
    const move = (event) => {
      if (!state.dragging) return;
      const [x, y] = position(event);
      canvas._rect = [state.x0, state.y0, x, y];
      this.intakeDrawCrop(canvas);
    };
    const up = () => { state.dragging = false; };
    if (canvas._wired !== true) {
      canvas._wired = true;
      canvas.addEventListener("pointerdown", down);
      canvas.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }
  },

  intakeDrawCrop(canvas) {
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (canvas._bitmap) context.drawImage(canvas._bitmap, 0, 0,
      canvas.width, canvas.height);
    if (canvas._rect) {
      const [x0, y0, x1, y1] = canvas._rect;
      context.save();
      context.fillStyle = "rgba(0,0,0,0.45)";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(canvas._bitmap,
        Math.min(x0, x1) / canvas._scale, Math.min(y0, y1) / canvas._scale,
        Math.abs(x1 - x0) / canvas._scale, Math.abs(y1 - y0) / canvas._scale,
        Math.min(x0, x1), Math.min(y0, y1),
        Math.abs(x1 - x0), Math.abs(y1 - y0));
      context.strokeStyle = "#7dd3fc";
      context.lineWidth = 2;
      context.strokeRect(Math.min(x0, x1), Math.min(y0, y1),
        Math.abs(x1 - x0), Math.abs(y1 - y0));
      context.restore();
    }
  },

  async intakeApplyCrop() {
    const s = this.intake;
    const form = s && s.assetForm;
    const canvas = document.querySelector(".intake-card .crop-canvas");
    if (!form || !canvas || !canvas._rect || !canvas._bitmap) return;
    const [x0, y0, x1, y1] = canvas._rect;
    const scale = canvas._scale;
    const left = Math.max(0, Math.round(Math.min(x0, x1) / scale));
    const top = Math.max(0, Math.round(Math.min(y0, y1) / scale));
    const width = Math.max(1, Math.round(Math.abs(x1 - x0) / scale));
    const height = Math.max(1, Math.round(Math.abs(y1 - y0) / scale));
    if (width < 8 || height < 8) {
      form.error = "选的区域太小了：拖一个大一点的框。";
      return;
    }
    const out = document.createElement("canvas");
    out.width = width;
    out.height = height;
    out.getContext("2d").drawImage(canvas._bitmap, left, top, width, height,
      0, 0, width, height);
    const blob = await new Promise((resolve) =>
      out.toBlob(resolve, "image/jpeg", 0.9));
    if (!blob) return;
    if (form.croppedUrl) URL.revokeObjectURL(form.croppedUrl);
    form.croppedBlob = blob;
    form.croppedUrl = URL.createObjectURL(blob);
    form.cropOpen = false;
    form.error = "";
  },

  intakeUndoCrop() {
    const s = this.intake;
    const form = s && s.assetForm;
    if (!form) return;
    if (form.croppedUrl) URL.revokeObjectURL(form.croppedUrl);
    form.croppedBlob = null;
    form.croppedUrl = "";
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

  async intakeSaveAsset(overwrite = false) {
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
    form.exists = null;
    try {
      // 参考目标组装进唯一的 description（F07）：后端读同名第一个
      // 字段，append 两次会把目标丢掉。
      let description = form.description.trim();
      if (form.target.trim()) {
        description += "\n（参考目标：" + form.target.trim() + "）";
      }
      const image = form.croppedBlob || s.imageBlob;
      const body = new FormData();
      body.append("name", form.name.trim());
      body.append("description", description);
      if (form.kind === "character") body.append("voice", form.voice);
      // “另存”按创建走（F06）：同名是 409 决策，不是静默覆盖。
      if (!overwrite) body.append("on_exists", "create");
      if (image) body.append("image", image,
        form.croppedBlob ? "intake-reference-cropped.jpg" : "intake-reference.jpg");
      const response = await fetch("/api/assets/" + form.kind, {
        method: "POST", body,
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 409) {
        // 同名：把现有资产摆出来，改名或明确覆盖，作者决定。
        form.exists = data.existing || { name: data.name || form.name };
        form.error = "";
        return;
      }
      if (!response.ok) {
        form.error = data.error || "保存失败了";
        return;
      }
      // 读回确认（F07）：保存的说“存进去了”，读回来的才算数。
      let readBack = "";
      try {
        const detail = await api.get(
          "/api/assets/" + form.kind + "/" + encodeURIComponent(form.name.trim()));
        readBack = detail.description || "";
      } catch { readBack = ""; }
      const targetSaved = !form.target.trim()
        || (readBack && readBack.indexOf(form.target.trim()) >= 0);
      form.saved = {
        kind: form.kind === "scene" ? "场景" : "角色",
        name: form.name.trim(),
        targetOk: targetSaved,
        target: form.target.trim(),
      };
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
    const visible = this.intakeVisibleEvents;
    const target = index + delta;
    if (!draft || !visible[index] || target < 0 || target >= visible.length) return;
    const fromIdx = draft.events.indexOf(visible[index]);
    const toIdx = draft.events.indexOf(visible[target]);
    const [row] = draft.events.splice(fromIdx, 1);
    draft.events.splice(toIdx, 0, row);
    this.intakeTouchEvents();
  },

  intakeDuplicateEvent(index) {
    const draft = this.intake && this.intake.eventDraft;
    const item = this.intakeVisibleEvents[index];
    if (!draft || !item) return;
    const realIdx = draft.events.indexOf(item);
    const copy = Object.assign({}, item, {
      ui_id: item.ui_id + "_c" + Date.now() % 1000,
      addition: false, included: true,
    });
    draft.events.splice(realIdx + 1, 0, copy);
    this.intakeTouchEvents();
  },

  intakeRemoveEvent(index) {
    const draft = this.intake && this.intake.eventDraft;
    const item = this.intakeVisibleEvents[index];
    if (!draft || !item) return;
    const realIdx = draft.events.indexOf(item);
    if (item.addition) item.removed = true;   // 补写删掉就是“不要”
    else draft.events.splice(realIdx, 1);
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
          scene: s.design.scene,
          constraints: (s.design.constraints || "").trim()
            + ((s.design.constraints || "").trim() && s.storySummary.trim() ? "\n" : "")
            + (s.storySummary.trim() ? "故事主线：" + s.storySummary.trim() : ""),
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
      // U08：错误带行号时把出错的那行带到眼前，而不是让作者滚着找。
      if (s.validateState.errors.length
          && s.validateState.errors.some((e) => e.event_index !== null)) {
        requestAnimationFrame(() => {
          const bad = document.querySelector(".intake-event .input-err");
          if (bad && bad.scrollIntoView) {
            bad.scrollIntoView({ behavior: "smooth", block: "center" });
          }
        });
      }
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
    // The scene statement goes in as a SCENE: the generator fills the
    // script's scene field from it, and the English mirror may then
    // mirror that scene. Without it the model writes an execution
    // scene the script never had, and the contract refuses the asset
    // three times in a row (2026-11-15 verification).
    if (s.design.scene && s.design.scene.trim()) {
      parts.push("场景：" + s.design.scene.trim());
    }
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
      // 一句话摘要进导演要求（U05）：锁定的事件不受影响，模型分镜
      // 归组和写执行描述时以它为准。UI 上说明这个关系。
      const constraints = (s.design.constraints || "").trim()
        + ((s.design.constraints || "").trim() && s.storySummary.trim()
           ? "\n" : "")
        + (s.storySummary.trim() ? "故事主线：" + s.storySummary.trim() : "");
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
        scene: s.design.scene, constraints: constraints,
        light: "follow_image", light_text: "",
      };
    } else {
      const summary = s.storySummary.trim();
      const concept = (summary ? "这一版的故事：" + summary + "\n\n" : "")
        + this.intakeConcept();
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
      storySummary: s.storySummary,
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
    s.storySummary = meta.storySummary || "";
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
};
