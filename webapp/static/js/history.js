/* 制作历史页：跑过什么、还在跑什么、能决定什么。
 *
 * 依赖的浏览器原生能力：fetch（网络）。
 */

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
    /* 拼片不是一次生成：它没有可续跑的东西。 */
    if (job.assembly || !job.can_resume) return false;
    return ["success", "error", "interrupted", "partial", "empty"].indexOf(
        job.status) >= 0;
  },

  /* 用本次配置再做一版（U04）：不是续跑（那是“从这次继续”），而是
   * 同样的角色/场景绑定与参数，做一个新的运行。写进制作页的会话，
   * 跳过去让作者核对后提交。 */
  remakeJob(job) {
    if (!job || !job.script) return;
    const params = job.params || {};
    const roles = (job.role_assets || {});
    try {
      window.localStorage.setItem("mie-video-session:v1", JSON.stringify({
        script: job.script,
        roles: Object.keys(roles).map((id) => ({ id, asset: roles[id] })),
        sceneAsset: job.scene_asset || "",
        width: params.width || 960,
        height: params.height || 544,
        steps: params.steps || 20,
        duration: params.duration || 18,
        // 这次用的那套模型和种子：再做一版就该是同一套。
        render: params.render || {},
        savedAt: Date.now(),
      }));
    } catch { /* 存不进就让制作页从头来 */ }
    window.location.href = "/video?script=" + encodeURIComponent(job.script);
  },
};

/* ------------------------------------------------------------------ */
/* 制作历史                                                          */
/* ------------------------------------------------------------------ */

function historyPage() {
  return {
    works: [],             /* 一部作品一行（U06）；尝试在作品内 */
    attemptsTotal: 0,
    total: 0,
    page: 1,
    pages: 1,
    perPage: 10,
    filter: "all",
    searchQuery: "",
    expandedRuns: {},      /* 展开状态按 run_name 记，翻页回来还在 */
    activeCount: 0,        /* 全局在跑的任务数（不只看当前页） */
    interrupting: false,
    loading: false,
    message: "",
    statusText: "",
    playingJob: "",
    playingWork: "",
    audioPreviewState: { key: "", playing: false, position: 0, duration: 0 },
    submitting: false,
    reviewBusy: false,      /* 审片决定正在提交 */
    reviewMessage: "",
    pollTimer: null,
    progressLine: "",        /* 正在跑的那一次的实时进度 */
    progressOffline: false,

    async init() {
      audioPreview.listen((state) => { this.audioPreviewState = state; });
      renderProgress.listen((state, one) => {
        this.progressOffline = !state.connected;
        this.progressLine = progressText(one);
      });
      await this.load();
      this.startPolling();
    },

    progressText(one) { return progressText(one); },

    playSample(key, src) { audioPreview.toggle(key, src); },

    isExpanded(runName) { return !!this.expandedRuns[runName]; },

    toggleExpanded(runName) {
      this.expandedRuns[runName] = !this.expandedRuns[runName];
    },

    /* 作品的成片播放：点开才挂播放器。 */
    playWork(work) {
      if (this.playingWork === work.run_name) return;
      this.playingWork = work.run_name;
    },

    workManifest(work) {
      const delivered = (work.attempts || []).find(
        (a) => (a.manifest && (a.manifest.duration_seconds || a.manifest.clip_count)));
      return (delivered && delivered.manifest) || null;
    },

    async favoriteWork(work) {
      const carrier = (work.attempts || [])[0];
      if (!carrier) return;
      await this.toggleFavorite(carrier);
      work.favorite = carrier.favorite;   // 星星亮在作品卡上
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

    /* 停止一次渲染。
     *
     * 每条尝试都带着自己的 job_id：停的是这一条，不是「当前那个」。
     * 顶上的按钮不带 id，服务端只会在只有一个任务在跑时才动手——
     * 有好几个的时候它会让人选，不会替作者挑一个停掉。 */
    async interruptRender(job) {
      if (this.interrupting) return;
      this.interrupting = true;
      try {
        const body = (job && job.job_id) ? { job_id: job.job_id } : {};
        const result = await api.post("/api/render/interrupt", body);
        this.message = result.message || "已向 ComfyUI 发出停止请求…";
        await this.load();
      } catch (error) {
        // 409 是「没有停」：服务端说了为什么，原话说给作者听。
        this.message = error.status === 409
          ? error.message
          : "停止失败：" + error.message;
      } finally {
        this.interrupting = false;
      }
    },

    async load() {
      this.loading = true;
      try {
        // 筛选在服务端做：放在翻页之后会漏掉后面几页的任务，
        // 总数也会算错。
        const query = `/api/render/jobs?page=${this.page}`
          + `&per_page=${this.perPage}`
          + `&grouped=1`
          + (this.filter === "favorite" ? "&favorites=1" : "")
          + (["running", "done", "failed"].indexOf(this.filter) >= 0
            ? `&status=${this.filter}` : "")
          + (this.searchQuery.trim()
            ? `&q=${encodeURIComponent(this.searchQuery.trim())}` : "");
        const data = await api.get(query);
        this.works = data.groups || [];
        this.attemptsTotal = data.attempts || 0;
        this.total = data.total || 0;
        this.page = data.page || 1;
        this.pages = data.pages || 1;
        // 在跑的任务是全局的：当前这一页没有，不代表没有人在跑。
        this.activeCount = data.active || 0;
        // 有任务在跑才开着实时进度流；上一次跑完的那一次也带着，
        // 迟到的页面靠它说得出渲染是怎么结束的。
        renderProgress.sync(this.activeCount, data.last || null);
        // 有待审的分镜时，作品卡自己展开：等决定的事不该藏在折叠里。
        (this.works || []).forEach((work) => {
          if (this.workHasReview(work)) this.expandedRuns[work.run_name] = true;
        });
        this.statusText = this.total
          ? `共 ${this.total} 部作品` : "";
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
          // 续跑用原任务存下来的那套渲染设置（服务端也认这份）。
          settings: params.render || {},
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
    remakeJob(job) { return jobView.remakeJob(job); },

    /* ---------------- 逐镜审片 ---------------- */

    /* 这部作品里有没有在等决定的分镜。 */
    workHasReview(work) {
      return (work && (work.attempts || []).some((a) => a.review)) || false;
    },

    /* 把一个决定交给引擎自己的审片接口。工作台不自己决定任何事：
     * 它只是把这一次审片、这个候选和这个动作递过去。 */
    async decideReview(job, candidate, action) {
      if (this.reviewBusy) return;
      const pending = job.review || {};
      if (!pending.token) {
        this.reviewMessage = "这次审片没有令牌，先刷新看看。";
        return;
      }
      this.reviewBusy = true;
      this.reviewMessage = "正在提交决定…";
      try {
        const data = await api.post("/api/render/review/decision", {
          job_id: job.job_id,
          token: pending.token,
          action: action,
          candidate_revision: candidate ? candidate.revision : "",
        });
        this.reviewMessage = action === "approve"
          ? "已采纳候选 " + (data.candidate_number || "?") + "，这一镜接着往下走。"
          : "已要求重掷这一镜。";
        await this.load();
      } catch (error) {
        this.reviewMessage = "决定没有生效：" + error.message;
      } finally {
        this.reviewBusy = false;
      }
    },

    /* 拼片：把这次已经保存下来的分镜接成片子，不再生成任何一帧。 */
    canAssemble(job) {
      if (!job || !job.run_name || job.assembly) return false;
      return ["success", "partial", "error", "interrupted", "empty"]
        .indexOf(job.status) >= 0;
    },

    async assembleJob(job) {
      if (this.submitting) return;
      this.submitting = true;
      this.message = "正在用已保存的分镜拼片：" + job.run_name;
      try {
        const record = await api.post("/api/render/assemble", {
          job_id: job.job_id,
          mode: "assemble",
        });
        this.message = "已排队拼片（#" + record.number
          + "）：不重新生成，只把已保存的分镜接起来。";
        await this.load();
      } catch (error) {
        this.message = "拼片失败：" + error.message;
      } finally {
        this.submitting = false;
      }
    },
  };
}
