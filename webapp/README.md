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
| `/assets` | 角色与场景资产：列表、预览、立绘与音色样本上传、描述与音色编辑、删除；「✨ AI 补全」按 auto 保存节点的语义起草空字段（有图看图写描述，角色补音色、场景补环境音，已填字段不动） |
| `/scripts` | 剧本资产：带元数据的列表（角色/分镜/时长，坏板标「损坏」并给原因）、时间轴+分镜卡预览、全字段结构化表单编辑（`source_script` 实时解析、事件分段成镜、execution 键自动增删）、JSON 源码标签、编辑时自动契约校验（契约错误与渲染风险分开报；渲染风险按严重度分级，红级标到具体事件行，弱代理降灰）；「✨ AI 起草」=「从图片或文字起草」：文字直接出稿，或放一张图先解读（可见/建议分开、图文冲突逐条裁决、平台看不了图可补人工图注），确认后的理解组织进概念生成（文字规则：允许改编）；起草过程在浏览器里暂存，刷新可续；素材图可「另存为参考资产」（角色/场景，独立于剧本保存，入库≠制作引用）；「✨ 修订」按改动意见出下一版（草稿先进编辑器，确认后才保存） |
| `/video` | 视频制作：选剧本 → 按角色接资产 → 出计划预览 → 提交渲染 → 进度与产物。片段名（run name）留空则按剧本名生成：引擎逐字接收这个名字（不合法就报错，不会偷偷改道），所以中文剧本名会转成 `sb_web_<摘要>`，页面上的「实际运行名」显示的就是真正提交的那个。任务列表有任务在跑时自己刷新，失败的显示真实报错而不是一直「running」；任务记录落盘，webapp 重启后仍在 |

## 实时进度（渲染中看得见走到哪一步）

ComfyUI 自己会报进度：哪个节点在跑、采样器走到第几步、这一镜做完没有。
工作台读的是它自己的 `/ws` 事件流，**只读一条**——所有页面共用一份状态，
再通过 `GET /api/render/progress`（Server-Sent Events）转发出去。浏览器标签页
再多，也只是多一条 SSE，不会多一条到 ComfyUI 的连接。

- 页面显示：当前镜头 X/N、采样步数 X/Y、正在跑的节点、最后一条状态。
  读不出来的字段就空着，不编数字。
- 事件按 **prompt_id** 归属：别的渲染的消息不会被算到这次头上，上一次
  渲染迟到的消息也不会串到下一次。
- 连不上 ComfyUI 时如实说「进度流断开」，任务状态仍由历史页的轮询确认。
- 任务是否成功仍然以 ComfyUI 的历史为准（事件流可能丢消息），
  停止渲染的按钮也还是原来那个。

## 模型 / 预设 / 种子（`/video`）

模型清单是向 ComfyUI 的 `/object_info` 问出来的，用的是工作流里那些节点
自己的输入枚举：**扩散模型、文本编码器、视频 VAE、音频 VAE、注意力后端**。
哪个节点是视频 VAE、哪个是音频 VAE，是从**图的连线**读出来的（接在采样器
`vae` 上的是视频 VAE，接在 `audio_vae` 上的是音频 VAE），不是写死的文件名，
所以换台机器不用改代码。

- 只发送 ComfyUI 真的有的值：不在清单里的选择会被当场拒绝（400），
  不会排进队列几分钟后才失败。
- 离线时清单为空，工作流自带的默认值原样保留。
- 预设：内置「快速 / 标准 / 高质」（只改采样步数），也可以把自己的
  组合存成命名预设，存在工作台自己的 settings.json 里。
- 基础种子用文本框传：引擎允许到 2^64-1，浏览器的一个数字装不下，
  写成字符串才不会被悄悄改掉；0 是合法的种子。
- 提交 → 历史 → 「用本次配置再做一版」都带着同一套设置；
  **续跑用原任务存下来的那套**，不会偷偷换成另一套模型。

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
它用 ComfyUI 的 embedded python 把网页端起在 8200，端口占用时不会重复起。

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

## 前端的文件怎么分的

**没有构建步骤。** 浏览器按 `base.html` 里列的顺序依次执行这些文件，
顺序就是依赖的顺序，Alpine 最后加载（它启动时组件必须已经定义好）。

| 文件 | 是什么 | 依赖的浏览器原生能力 |
|---|---|---|
| `static/js/api.js` | 网络层（`fetch` 包装）与实时进度（SSE） | `fetch`、`EventSource` |
| `static/js/shell.js` | 外壳（导航、ComfyUI 状态）、声音试听、剧本契约的固定词汇 | `fetch`、`Audio`、`localStorage` |
| `static/js/llm.js` | 大模型页的组件 | `fetch` |
| `static/js/assets.js` | 角色与场景资产页的组件 | `fetch`、`FileReader` |
| `static/js/scripts-intake.js` | 剧本模型与「从图片或文字起草」那一半 | `fetch`、`IndexedDB`、`FileReader`、`localStorage` |
| `static/js/scripts-editor.js` | 剧本编辑那一半（与接入那一半合成同一个 Alpine 组件） | `fetch`、`localStorage`、DOM |
| `static/js/video.js` | 视频制作页的组件 | `fetch`、`localStorage` |
| `static/js/history.js` | 制作历史页的组件 | `fetch` |

剧本页是两个半页（接入 + 编辑），但**只有一个 Alpine 组件**：页面只有
一个 `x-data`，`scriptsPage()` 把两半挂进同一个对象。挂的时候用
`Object.defineProperties` —— getter 必须原样挂过去，`Object.assign` 会把
getter 取成一个静态值，「现在该按哪个按钮」就再也不动了。

`templates/scripts.html` 也是拆开的：主模板 + `templates/scripts/` 下的四个
面板（列表 / 接入 / 编辑 / 三个视图），拆在面板的边界上，标记与文案一字未改。

**渲染风险的分类在服务端**（`webapp/services/warnings.py`）：契约给的是一句
诊断，页面上显示的是一句短话、一个严重程度和一条能照着做的改法 —— 这份
对照表只有一份真源，浏览器端不再自己认契约的标记。

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MIE_PLUGIN_ROOT` | 引擎旁边的 MieNodes 插件目录 | 资产库与 `mie_llm_keys.json` 的所在（默认自动找同级的 `comfyui_mienodes`） |
| `MIE_LLM_KEYS_FILE` | `<插件根>/mie_llm_keys.json` | 密钥文件 |
| `MIE_COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 地址 |
| `MIE_WEB_HOST` / `MIE_WEB_PORT` | `0.0.0.0` / `8200` | 本工作台监听 |
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
