/* 大模型页：平台、Key 和模型。
 *
 * 依赖的浏览器原生能力：fetch（网络）。
 */

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

    async saveKey(platform, rawValue) {
      const token = typeof rawValue === "string" ? rawValue
        : document.querySelector(`input[data-key="${platform.key}"]`)?.value ?? "";
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

    async saveHost(platform, rawValue) {
      const host = typeof rawValue === "string" ? rawValue
        : document.querySelector(`input[data-host="${platform.key}"]`)?.value ?? "";
      try {
        const result = await api.post("/api/llm/host", { key: platform.key, host });
        platform.endpoint = result.host || host;
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
