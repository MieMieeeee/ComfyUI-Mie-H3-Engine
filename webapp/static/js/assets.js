/* 角色与场景资产页。
 *
 * 依赖的浏览器原生能力：fetch（网络）、FileReader（读上传的图片）、
 * localStorage（记住本机的选择）。
 */

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
    /* U01/U03：本地试听与预览的临时地址（撤销/保存后回收）。 */
    audioObjectUrl: "",
    imageObjectUrl: "",
    audioPreviewState: { key: "", playing: false, position: 0, duration: 0 },
    lightbox: "",

    async init() {
      await this.load();
      // 本页接管统一播放器；离开或切换时把本地文件引用一起释放。
      audioPreview.listen((state) => { this.audioPreviewState = state; });
      window.addEventListener("beforeunload", (event) => {
        this.releaseMedia();
        if (!this.isDirty) return;
        event.preventDefault();
        event.returnValue = "";
      });
      // 深链（F08 的补齐入口）：?kind=character&asset=名字 直达那份资产。
      const params = new URLSearchParams(window.location.search);
      const wantedKind = params.get("kind");
      if (wantedKind === "character" || wantedKind === "scene") {
        await this.setKind(wantedKind);
      }
      const wantedAsset = params.get("asset");
      if (wantedAsset && (this.items || []).some((x) => x.name === wantedAsset)) {
        await this.open(wantedAsset);
      }
    },

    /* 回收本地预览地址并停掉播放（同一个时间只有一个样本在响）。 */
    releaseMedia() {
      audioPreview.stop();
      if (this.audioObjectUrl) URL.revokeObjectURL(this.audioObjectUrl);
      if (this.imageObjectUrl) URL.revokeObjectURL(this.imageObjectUrl);
      this.audioObjectUrl = "";
      this.imageObjectUrl = "";
      this.lightbox = "";
    },

    playSample(key, src) {
      audioPreview.toggle(key, src);
    },

    replaySample() {
      audioPreview.replay();
    },

    seekSample(event) {
      audioPreview.seek(Number(event.target.value) || 0);
    },

    fmtSeconds(seconds) {
      const total = Math.max(0, Math.round(Number(seconds) || 0));
      return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
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
      this.releaseMedia();
    },

    /* 资产页也有同样的问题：改了一半切到别的资产，改动就没了。
       只选了新图/新音频（U03）也是改动：文件选择计入未保存。 */
    get isDirty() {
      if (!this.editing) return false;
      if (this.imageFile || this.audioFile) return true;
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
      this.releaseMedia();
      try {
        this.detail = await api.get(`/api/assets/${this.kind}/${encodeURIComponent(name)}`);
      } catch (error) {
        this.detail = {};
        this.message = `读取失败：${error.message}`;
      }
    },

    startNew() {
      if (!this.confirmDiscard("新建资产")) return;
      this.releaseMedia();
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
      this.releaseMedia();
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
      this.discardPicks();
      this.releaseMedia();
    },

    pickImage(event) {
      const file = event.target.files[0] || null;
      event.target.value = "";
      if (!file) return;
      if (this.imageObjectUrl) URL.revokeObjectURL(this.imageObjectUrl);
      this.imageFile = file;
      this.imageObjectUrl = URL.createObjectURL(file);
    },

    undoImagePick() {
      if (this.imageObjectUrl) URL.revokeObjectURL(this.imageObjectUrl);
      this.imageObjectUrl = "";
      this.imageFile = null;
    },

    pickAudio(event) {
      const file = event.target.files[0] || null;
      event.target.value = "";
      if (!file) return;
      audioPreview.stop();
      if (this.audioObjectUrl) URL.revokeObjectURL(this.audioObjectUrl);
      this.audioFile = file;
      this.audioObjectUrl = URL.createObjectURL(file);
    },

    /* 保存前反悔：文件选择撤掉（U01/U03），原媒体原样保留。 */
    undoAudioPick() {
      audioPreview.stop();
      if (this.audioObjectUrl) URL.revokeObjectURL(this.audioObjectUrl);
      this.audioObjectUrl = "";
      this.audioFile = null;
    },

    discardPicks() {
      this.undoImagePick();
      this.undoAudioPick();
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
        this.discardPicks();
        await this.load();
        await this.open(data.name);
        // After the reload, not before: open() clears the message, so a
        // confirmation set beforehand was wiped before anyone could read it.
        this.message = `已保存：${data.name}（${data.status}）`;
      } catch (error) {
        // 保存失败：表单和已选文件都保留（U03），改好再存。
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
