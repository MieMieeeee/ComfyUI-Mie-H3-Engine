/* 剧本页的「编辑」这一半：表单、角色、分镜、校验、保存。
 *
 * 它和接入那一半是同一个 Alpine 组件：页面只有一个 x-data，
 * scriptsPage() 把两份合在一起才交给 Alpine。
 *
 * 依赖的浏览器原生能力：fetch（网络）、localStorage（记住本机的
 * 选择）、DOM（滚动到某一镜）。
 */

function scriptsPage() {
  const part = {
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
    /* 风险的分类由服务端给（级别、叫什么、怎么改）：页面不自己认
     * 契约的标记，两边就不会各认一套。 */
    warningDetails: [],
    warningLevels: ["err", "warn", "info"],
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
      this.errors = []; this.warnings = []; this.warningDetails = [];
      this.okMessage = ""; this.warnOpen = false;
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
      this.errors = []; this.warnings = []; this.warningDetails = [];
      this.okMessage = ""; this.warnOpen = false;
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
      this.errors = []; this.warnings = []; this.warningDetails = [];
      this.okMessage = "";
      this.warnOpen = false;
      this.draftNotes = [];
      this.draftForm.mode = "revise";
      if (this.model) {
        // 修订只允许改名字和改动意见：接线事实（配音/重叠/结构）由底稿继承。
        this.draftForm.name = this.model.name || "新剧本";
      }
      this.draftForm.concept = "";
      this.draftOpen = true;
      this.bringCardIntoView(".card:has(.auto-actions)", null);
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
        this.warningDetails = result.warning_details || [];
        if (Array.isArray(result.warning_levels) && result.warning_levels.length) {
          this.warningLevels = result.warning_levels;
        }
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
      if (!this.model) return false;
      if (this.tab === "json" && !this.trySyncJson()) return false;
      try {
        const result = await api.post("/api/scripts",
          { json: this.buildPayload(), note: this.changeNote || "手工修改" });
        this.selected = result.name;
        this.okMessage = `已保存：${result.name}（${result.status}）`;
        if (result.content_version) {
          this.okMessage += ` · 版本 ${result.content_version}`;
        }
        this.errors = []; this.warnings = []; this.warningDetails = [];
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
        return true;
      } catch (error) {
        this.errors = [error.message];
        this.okMessage = "";
        return false;
      }
    },

    /* U04：改了一半也要能直接去做——保存成功才走，失败留在当前稿。 */
    async saveAndMake() {
      if (!this.model) return;
      let landed = true;
      if (this.isDirty) landed = await this.save();
      if (!landed) {
        this.okMessage = "保存没成功：先处理上面的错误，再去制作。";
        return;
      }
      window.location.href = "/video?script="
        + encodeURIComponent(this.selected || "");
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

    /* 风险按服务端给的分类显示。
     *
     * 服务端没给分类时（老的服务端），页面仍然给每条风险一句能看懂
     * 的话和一个级别 —— 只是不再自己猜是哪一类：宁可笼统，也不
     * 装作知道。 */
    get parsedWarnings() {
      const details = this.warningDetails || [];
      if (details.length) return details;
      return (this.warnings || []).map((text) => ({
        text,
        label: "渲染风险",
        level: "warn",
        kind: "model",
        fix: "按诊断把这一处改到没有风险",
        shot: (text.match(/^(scene_\d+)\s/) || [])[1] || "",
      })).sort((a, b) => (a.shot < b.shot ? -1 : a.shot > b.shot ? 1 : 0));
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
      /* 级别的先后由服务端定（err → warn → info）：页面按它排，
       * 芯片上最重的那一级的颜色才对。 */
      const order = this.warningLevels || ["err", "warn", "info"];
      const rank = (level) => {
        const at = order.indexOf(level);
        return at < 0 ? order.length : at;
      };
      const groups = new Map();
      for (const warning of this.parsedWarnings) {
        const group = groups.get(warning.label)
          || { label: warning.label, level: warning.level, count: 0 };
        group.count += 1;
        if (rank(warning.level) < rank(group.level)) {
          group.level = warning.level;
        }
        groups.set(warning.label, group);
      }
      return [...groups.values()].sort((a, b) => rank(a.level) - rank(b.level));
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

  /* 接入那一半挂在同一个组件上。两边的字段名不重叠 —— 重叠会在这里
   * 直接报错，而不是悄悄地盖掉一个。 */
  Object.defineProperties(part,
    Object.getOwnPropertyDescriptors(scriptsIntakePart));
  return part;
}
