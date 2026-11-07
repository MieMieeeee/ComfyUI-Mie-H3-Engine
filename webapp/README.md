# 短剧工作台（网页端）

短剧流水线的网页端，**随 H3 engine 插件一起发布**（`webapp/` 目录）。
纯 Python，没有 nodejs，没有构建步骤：Flask + Jinja + 一份 vendored 的 Alpine。

它跑在 **ComfyUI 自己的 embedded python** 上 —— 由引擎的
`MieH3StartWebUI` 节点启动。所以网页端与插件共用同一个解释器、同一套依赖、
**同一份资产库与 API Key**。

主题复刻自 `F:\ComfyUIWebToRunWorkflow`（`tokens.css` 的设计令牌 +
`themes.json` 的主题预设 + 同一套外壳），但页面与数据是本项目自己的。

---

## 四个页面

| 页面 | 做什么 |
|---|---|
| `/llm` | 大语言模型设置：23 个平台（SiliconFlow / Kimi / MiniMax / MiMo / 豆包 / 千帆 / 讯飞 / OpenAI / Grok / OpenRouter / Claude / Gemini / 百炼 / 智谱 / DeepSeek / Ollama …），每个平台的接口、模型、API Key、连通性测试 |
| `/assets` | 角色与场景资产：列表、预览、立绘与音色样本上传、描述与音色编辑、删除 |
| `/scripts` | 剧本资产：列表、预览、编辑 v2 JSON、契约校验（错误与渲染风险分开报）、删除 |
| `/video` | 视频制作：选剧本 → 按角色接资产 → 出计划预览 → 提交渲染 → 进度与产物 |

## 数据真源

页面上的每一个数字都来自那三个 PyPI 包，不是另写一份：

- **资产库与剧本契约**：`mie_craft`（`mie_craft.assets.library` /
  `mie_craft.assets.script_asset` / `mie_craft.script.contract`）——
  和 ComfyUI 节点用的是同一份实现，两边不会对「一个资产是什么」产生分歧。
- **平台与连接器**：`mie_llm`（`mie_llm.platforms` / `mie_llm.client`）。
- **API Key**：写进 ComfyUI 插件的 `mie_llm_keys.json` —— **插件的节点
  立刻就能用**，两边共用一份密钥。

## 渲染走的是新架构

`/video` 提交的工作流是引擎侧的那条路：

```
MieH3LoadScriptAsset  →  MieH3ScriptToPlan  →  MiniMaxH3ChainPlanModern|MieH3  →  …|MieH3
```

剧本由引擎读、计划由引擎组装（零 LLM，与 MieNodes 共用同一个 builder），
整条链路跑 `|MieH3` 节点 —— 引擎自己的实现，不是上游的。

**角色槽位按剧本的 `roles` 顺序接线。** 剧本说这个角色是画外音还是出镜，
决定了他给不给参考图；接错人等于换了张脸。

## 跑起来

**从 ComfyUI 里**：加一个 `MieH3StartWebUI` 节点（分类 MieNodes/web），运行它。
它用 ComfyUI 的 embedded python 把网页端起在 8199，端口占用时不会重复起。

**不用 ComfyUI**：插件根目录的 `start_webui.bat`（GBK 编码，cmd 读的就是
系统 ANSI 码页）。

```powershell
<ComfyUI>\python_embeded\python.exe webapp\__main__.py
```

⚠️ 用**文件**跑，不要 `python -m webapp`：embedded python 是**隔离模式**
（`._pth` 固定了 sys.path），`-m` 找不到这个包。`__main__.py` 自己会把包
的父目录放回 sys.path。

**ComfyUI 由启动器管，本脚本不管它**（见 `F:\Mie-Skills\AGENTS.md`）：

```powershell
cmd /c "C:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\ComfyUI启动器-CLI.cmd" start --timeout 600
```

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MIE_PLUGIN_ROOT` | 引擎旁边的 MieNodes 插件目录 | 资产库与 `mie_llm_keys.json` 的所在（默认自动找同级的 `comfyui_mienodes`） |
| `MIE_LLM_KEYS_FILE` | `<插件根>/mie_llm_keys.json` | 密钥文件 |
| `MIE_COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 地址 |
| `MIE_WEB_HOST` / `MIE_WEB_PORT` | `0.0.0.0` / `8199` | 本工作台监听 |
| `MIE_WEB_WORKFLOW` | `webapp/data/mie_h3_storyboard_movie.api.json` | 渲染用的工作流模板 |
| `MIE_COMFYUI_ROOT` | 本机 ComfyUI 根 | 产物目录（`/media` 从这里读） |

## 几个刻意的选择

- **单进程，不开 `--reload`。** 第二个进程会跟渲染队列抢活。任务表在内存里，
  所以工作台重启后任务列表是空的 —— 但渲染产物在磁盘上，不受影响。
- **上传的图片转成 PNG、音频转成 WAV。** 资产库只收这两种字节，而且音频
  一律存成 `NN.wav` —— 不转的话，磁盘上的文件名会撒谎。
- **密钥只显示头尾。** 页面不回显全文。
- **「测试连接」是真实调用，会烧额度。** 额度按天刷新，北京时间早晨 5:00
  之后可重试 —— 额度用完时该等，不该反复重试。
- **契约校验和渲染风险分开报。** 错误 = 契约不收；警告 = 收，但某处贴地
  （比如目标时长正好压在最小可行预算上，实际语速稍慢就会截掉收尾节拍）。

## 测试

```powershell
python -m pytest webapp_tests -q
```

41 个用例，不碰真实资产库：每个用例拿一个临时插件根。

## Licence

GPL-3.0。主题文件（`tokens.css` / `themes.json` / `alpine.min.js`）
来自 `F:\ComfyUIWebToRunWorkflow`，同作者、同许可。
