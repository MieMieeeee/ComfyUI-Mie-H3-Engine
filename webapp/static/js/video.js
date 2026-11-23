/* 视频制作页：选剧本、选模型、提交渲染。
 *
 * 依赖的浏览器原生能力：fetch（网络）、EventSource（在 api.js 里）、
 * localStorage（记住本机的选择）。
 */

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
    compiledPlan: null,
    planSha256: "",
    compileError: "",
    previewRequest: 0,
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
    audioPreviewState: { key: "", playing: false, position: 0, duration: 0 },
    sceneAsset: "",          /* U02：场景资产选择，接线进计划的 scene_1 */
    scenes: [],
    sceneDetails: {},
    progressLine: "",        /* 正在跑的那一次的实时进度 */
    /* 渲染设置：模型清单是从 ComfyUI 问出来的，不是写死的文件名 */
    modelSlots: {},
    modelSlotsLoaded: false,
    modelsOnline: false,     /* ComfyUI 的清单拿到了没有 */
    modelValues: { unet: "", clip: "", video_vae: "", audio_vae: "", attention: "" },
    baseSeed: "0",
    seedBounds: { min: "0", max: "18446744073709551615" },
    presets: [],
    presetChoice: "",
    presetName: "",
    presetDesc: "",
    presetMessage: "",
    modelsError: "",
    /* 逐镜审片：引擎自己的 Review Gate 的开关。默认关闭。 */
    reviewEnabled: false,
    reviewCandidateCount: 1,

    async init() {
      audioPreview.listen((state) => { this.audioPreviewState = state; });
      renderProgress.listen((state, one) => {
        this.progressLine = progressText(one);
      });
      const settings = Promise.all([this.loadModels(), this.loadReviewSetting()]);
      await this.loadScripts();
      // 从剧本页「用此剧本制作」过来时，URL 里带着剧本名。
      const wanted = new URLSearchParams(window.location.search).get("script");
      if (wanted && this.scripts.includes(wanted)) {
        this.script = wanted;
        await this.loadScript();
        this.applySession(wanted);   // 同一份剧本：接着上次的绑定
      } else if (!wanted) {
        // 没点名剧本：去补素材/配模型回来的人，接着上次的配置（U04）。
        await this.restoreSession();
      }
      await Promise.all([this.refreshCount(), settings]);
      if (this.script && this.modelsOnline) this.preview();
    },

    /* 审片开关是记下来的：上一次开了，这次回来还是开的。
       存的是服务端那份 —— 页面上的状态以服务端为准。 */
    async loadReviewSetting() {
      try {
        const data = await api.get("/api/render/review/settings");
        this.reviewEnabled = !!data.enabled;
        this.reviewCandidateCount = Number(data.candidate_count) || 1;
      } catch (error) {
        this.reviewEnabled = false;
        this.reviewCandidateCount = 1;
      }
    },

    async saveReviewSetting() {
      try {
        const data = await api.post("/api/render/review/settings", {
          enabled: this.reviewEnabled,
          candidate_count: this.reviewCandidateCount,
        });
        this.reviewEnabled = !!data.enabled;
        this.reviewCandidateCount = Number(data.candidate_count) || 1;
        this.message = this.reviewEnabled
          ? "已打开逐镜审片：下次生成每存好一镜就停下等你决定。"
          : "已关闭逐镜审片：下次生成无人值守跑完。";
      } catch (error) {
        this.message = "审片开关没有保存成功：" + error.message;
      }
    },

    /* ---------------- 制作会话：跨页面的那份配置（U04） ---------------- */

    get sessionKey() { return "mie-video-session:v1"; },

    readSession() {
      try {
        const raw = window.localStorage.getItem(this.sessionKey);
        const data = raw ? JSON.parse(raw) : null;
        return data && data.script ? data : null;
      } catch { return null; }
    },

    saveSession() {
      if (!this.script) return;
      try {
        window.localStorage.setItem(this.sessionKey, JSON.stringify({
          script: this.script,
          roles: this.roles.map((r) => ({ id: r.id, asset: r.asset })),
          sceneAsset: this.sceneAsset || "",
          width: this.width, height: this.height,
          steps: this.steps, duration: this.duration,
          render: this.renderSettings(true),
          savedAt: Date.now(),
        }));
      } catch { /* 存不进就不承诺恢复 */ }
    },

    /* 渲染设置：模型 + 种子。空的键不送出去，工作流自带的默认值
     * 才不会被一个空字符串顶掉。 */
    renderSettings(includeUnavailable = false) {
      const out = {};
      /* 清单拿不到的时候不送模型：那台机器上装的是哪些文件，我们
       * 并不知道，工作流自带的默认值才是对的。 */
      if (includeUnavailable || this.modelsOnline) {
        Object.keys(this.modelValues).forEach((key) => {
          const value = (this.modelValues[key] || "").trim();
          if (value) out[key] = value;
        });
      }
      const seed = String(this.baseSeed === null || this.baseSeed === undefined
        ? "" : this.baseSeed).trim();
      if (seed) out.base_seed = seed;
      return out;
    },

    async loadModels() {
      try {
        const data = await api.get("/api/render/models");
        this.modelSlots = data.slots || {};
        this.presets = data.presets || [];
        this.seedBounds = data.seed || this.seedBounds;
        this.modelsError = data.error || "";
        this.modelsOnline = !!data.online;
        this.modelSlotsLoaded = true;
        // 第一次进来：用工作流当前用的那套，不是猜的。
        Object.keys(this.modelSlots).forEach((key) => {
          if (!this.modelValues[key]) {
            this.modelValues[key] = this.modelSlots[key].current || "";
          }
        });
      } catch (error) {
        this.modelsError = "读取模型清单失败：" + error.message;
        this.modelsOnline = false;
        this.modelSlotsLoaded = true;
      }
    },

    onModelChange() {
      this.presetChoice = "";
      this.presetDesc = "";
      this.saveSession();
      this.preview();
    },

    applyPreset(presetId) {
      const preset = (this.presets || []).find((p) => p.id === presetId);
      this.presetDesc = preset ? (preset.desc || "") : "";
      if (!preset) return;
      const settings = preset.settings || {};
      if (typeof settings.steps === "number") this.steps = settings.steps;
      Object.keys(this.modelValues).forEach((key) => {
        if (settings[key]) this.modelValues[key] = settings[key];
      });
      if (settings.base_seed !== undefined && settings.base_seed !== null) {
        this.baseSeed = String(settings.base_seed);
      }
      this.presetName = preset.builtin ? "" : preset.name;
      this.saveSession();
      this.preview();
    },

    get presetIsBuiltin() {
      const preset = (this.presets || []).find((p) => p.id === this.presetChoice);
      return !preset || !!preset.builtin;
    },

    async savePreset() {
      const name = (this.presetName || "").trim();
      if (!name) {
        this.presetMessage = "先给这个预设起个名字。";
        return;
      }
      const settings = Object.assign({ steps: this.steps }, this.renderSettings());
      try {
        await api.post("/api/render/presets", { name, settings });
        this.presetMessage = `已保存预设「${name}」。`;
        await this.loadModels();
        this.presetChoice = name;
        this.presetName = name;
      } catch (error) {
        this.presetMessage = "保存预设失败：" + error.message;
      }
    },

    async deletePreset(name) {
      if (!name) return;
      if (!window.confirm(`删掉预设「${name}」？`)) return;
      try {
        await api.del("/api/render/presets/" + encodeURIComponent(name));
        this.presetMessage = `已删除预设「${name}」。`;
        this.presetChoice = "";
        this.presetName = "";
        await this.loadModels();
      } catch (error) {
        this.presetMessage = "删除预设失败：" + error.message;
      }
    },

    clearSession() {
      try { window.localStorage.removeItem(this.sessionKey); } catch { /* 同上 */ }
    },

    /* 恢复：剧本还在才谈恢复；绑定逐个核对，失效的明确说出来。 */
    async restoreSession() {
      const session = this.readSession();
      if (!session || !this.scripts.includes(session.script)) {
        if (session) this.clearSession();
        return;
      }
      this.script = session.script;
      await this.loadScript();
      this.applySession(session.script, session);
    },

    /* 把会话里的绑定套到当前剧本上：角色槽位还在的套用，不在的报失效。 */
    applySession(scriptName, session = null) {
      const data = session || this.readSession();
      if (!data || data.script !== scriptName) return;
      const stale = [];
      this.roles.forEach((role) => {
        const saved = (data.roles || []).find((r) => r.id === role.id);
        if (!saved || !saved.asset) return;
        if (this.characters.includes(saved.asset)) {
          role.asset = saved.asset;
          this.loadAssetDetail(saved.asset);
        } else {
          stale.push(`${role.name}（原绑定 ${saved.asset} 已不在库里）`);
        }
      });
      if (data.sceneAsset && this.scenes.includes(data.sceneAsset)) {
        this.sceneAsset = data.sceneAsset;
        this.loadSceneDetail(this.sceneAsset);
      }
      if (typeof data.width === "number") this.width = data.width;
      if (typeof data.height === "number") this.height = data.height;
      if (typeof data.steps === "number") this.steps = data.steps;
      if (typeof data.duration === "number") this.duration = data.duration;
      if (data.render && typeof data.render === "object") {
        Object.keys(this.modelValues).forEach((key) => {
          if (data.render[key]) this.modelValues[key] = data.render[key];
        });
        if (data.render.base_seed !== undefined) {
          this.baseSeed = String(data.render.base_seed);
        }
      }
      const minutes = Math.max(1, Math.round((Date.now() - (data.savedAt || Date.now())) / 60000));
      this.message = "已恢复上次的制作配置（约 " + minutes + " 分钟前）"
        + (stale.length ? "；需要重选：" + stale.join("、") : "。");
      this.saveSession();
      this.preview();
    },

    /* 场景资产（U02）：选中后计划的 scene_1 槽用它，剧本自己的 scene
     * 描述会被覆盖——引擎的接线规则，页面上明说。 */
    async loadScenes() {
      if (this.scenes.length) return;
      try {
        const assets = await api.get("/api/assets");
        this.scenes = (assets.scenes || [])
          .filter((x) => x && x.name).map((x) => x.name);
      } catch { this.scenes = []; }
    },

    async loadSceneDetail(name) {
      if (!name || this.sceneDetails[name]) return;
      try {
        this.sceneDetails[name] = await api.get(
          "/api/assets/scene/" + encodeURIComponent(name));
      } catch (error) {
        this.sceneDetails[name] = { name, image: "", description: "", ambience: "" };
      }
    },

    async onSceneChange() {
      audioPreview.stop();
      await this.loadSceneDetail(this.sceneAsset);
      await this.preview();
      this.saveSession();
    },

    playSample(key, src) {
      audioPreview.toggle(key, src);
    },

    fmtSeconds(seconds) {
      const total = Math.max(0, Math.round(Number(seconds) || 0));
      return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
    },

    async loadScripts() {
      try {
        const data = await api.get("/api/scripts");
        this.scripts = data.names || [];
        const assets = await api.get("/api/assets");
        this.characters = (assets.characters || [])
          .filter((x) => x && x.name).map((x) => x.name);
        this.scenes = (assets.scenes || [])
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
      audioPreview.stop();          // 换了人选，旧声音立刻停（U01）
      await this.loadAssetDetail(role.asset);
      await this.preview();
      this.saveSession();
    },

    /* F08：按实际配音模式检查每个角色的媒体。参考配音必须有可用
     * 样本（引擎要拿它生成声音）；出镜角色没图先提醒（有画面描述
     * 兜底），不硬拦。绑定一变，这里随 assetDetails 自动失效重算。 */
    roleReadiness(role) {
      const problems = [];
      const detail = (this.assetDetails[role.asset] || {});
      if (this.scriptJson && this.scriptJson.audio_mode === "reference"
          && !(detail.audios || []).length) {
        problems.push({ kind: "audio", text: "缺参考声音样本（参考配音要用它）",
                        fix: "/assets?kind=character&asset=" + encodeURIComponent(role.asset || "") });
      }
      if (role.presence === "on_screen" && detail.image === ""
          && detail.name !== undefined) {
        problems.push({ kind: "image", text: "没有立绘（将按剧本描述生成形象）",
                        fix: "/assets?kind=character&asset=" + encodeURIComponent(role.asset || "") });
      }
      return problems;
    },

    get blockingProblems() {
      if (!this.scriptJson || this.scriptJson.audio_mode !== "reference") return [];
      return this.roles.filter((r) => r.asset)
        .filter((r) => this.roleReadiness(r).some((p) => p.kind === "audio"))
        .map((r) => r.name);
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
      this.resetPlanPreview();
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
      /* F08：提交按钮看的是真实的制作条件——角色接满、参考音齐、
       * ComfyUI 在线、镜像没过期；每少一样，按钮自己说缺什么。 */
      if (!this.script || !this.roles.length || this.submitting) return false;
      if (this.missingRoles.length) return false;
      if (this.blockingProblems.length) return false;
      if (this.staleBlocked) return false;
      return this.comfyOnline === true;
    },

    get submitLabel() {
      if (this.submitting) return "提交中…";
      if (this.missingRoles.length)
        return "还差 " + this.missingRoles.length + " 个资产";
      if (this.blockingProblems.length)
        return "缺参考声音：" + this.blockingProblems.join("、");
      if (this.staleBlocked) return "先处理上面的镜像问题";
      if (this.comfyOnline === false) return "ComfyUI 未连接，不能渲染";
      return "🎬 开始渲染";
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

    resetPlanPreview() {
      this.previewRequest += 1;
      this.planPreview = "";
      this.planError = "";
      this.compiledPlan = null;
      this.planSha256 = "";
      this.compileError = "";
    },

    async preview() {
      this.resetPlanPreview();
      const request = this.previewRequest;
      if (!this.script || !this.roles.length) return;
      // The assembler refuses a slot with no asset, so there is nothing to
      // preview until every role has one. The page says which ones are
      // missing instead of asking and swallowing the error.
      if (this.missingRoles.length) {
        this.planError = "还没接资产的角色：" + this.missingRoles.join("、");
        return;
      }
      try {
        const data = await api.post(
          `/api/scripts/${encodeURIComponent(this.script)}/plan`,
          { role_assets: this.roleAssets(), scene_asset: this.sceneAsset,
            settings: this.renderSettings() });
        if (request !== this.previewRequest) return;
        this.compiledPlan = data.compiled_plan || null;
        this.planSha256 = data.plan_sha256 || "";
        this.compileError = data.compile_error || "";
        this.planPreview = (data.requirements || [])
          .map((r) => `${r.socket}: ${r.kind} ${r.name || "(未接线)"}`
            + `${r.required ? " [必填]" : ""}${r.note ? " — " + r.note : ""}`)
          .join("\n");
      } catch (error) {
        if (request !== this.previewRequest) return;
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
          scene_asset: this.sceneAsset,
          run_name: this.runName,
          width: this.width, height: this.height,
          steps: this.steps, duration: this.duration,
          settings: this.renderSettings(),
          // 审片开关跟着这次提交走：引擎的 Review Gate 只在这张图里
          // 被打开。
          review: {
            enabled: this.reviewEnabled,
            candidate_count: this.reviewCandidateCount,
          },
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
        renderProgress.sync(data.active || 0);
      } catch (error) {
        this.statusText = "";
      }
    },

  };
}
