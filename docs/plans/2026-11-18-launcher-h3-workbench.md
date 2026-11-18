# 启动器接入 H3 短剧工作台

日期：2026-11-18

## 范围

- H3 Engine 侧统一默认端口为 **8200**。Flask 入口、ComfyUI 启动/状态/停止节点和 Windows 批处理保持一致。显式设置端口继续生效，旧工作流保存的端口值按需修改。
- 启动器由另一位 agent 在 `F:/ComfyUI-Mie-Package-Launcher` 实施。该工作区已有未提交的伴生服务框架和其他修改，接入时基于现状继续，并保留这些修改。
- 「H3 短剧工作台」标签页管理一个 Flask 进程。ComfyUI 继续加载 H3 Engine 的生成节点。网页可在 ComfyUI 离线时管理资产和剧本，提交渲染时才需要 ComfyUI。

## 启动器实现

1. 在 `core/companion/spec.py` 注册 `h3-engine`，显示名为「H3 短剧工作台」，端口默认 `8200`，覆盖键建议 `services.h3-engine.port`。以 `registry.resolve_pkg_root` 解析当前环境，检测 `ComfyUI/custom_nodes/ComfyUI-Mie-H3-Engine/webapp/__main__.py`，支持整合包内这个入口已经是 junction 的情况。检测不到入口时隐藏标签页。现有 WebUI 工作台仍使用 8199。
2. 给 `core/companion/manager.py` 补一个由 runtime 提供启动步骤的接口。OpenJutsu 保留现有 build、proxy、app 顺序，H3 只提供一个 app 步骤。启动命令为 `<当前环境 Python> -u <H3 插件根>/webapp/__main__.py`，工作目录为 H3 插件根。相对 Python 路径优先从所选整合包解析，支持配置中的绝对 Python 路径。用该解释器检查 `flask`、`soundfile`、`mie_llm`、`mie_prompt`、`mie_craft` 可否导入，并报告具体缺项。
3. 将环境路径和端口传给 H3 进程，避免共享 junction 时读错资产或把任务写到源码目录。

   | 变量 | 值 |
   | --- | --- |
   | `MIE_WEB_PORT` | 生效的 H3 端口，默认 8200 |
   | `MIE_WEB_HOST` | 启动器默认绑定 `127.0.0.1` |
   | `MIE_COMFYUI_ROOT` | 当前整合包的 `ComfyUI` 目录 |
   | `MIE_COMFYUI_URL` | 当前 ComfyUI 实际端口对应的环回地址 |
   | `MIE_PLUGIN_ROOT` | 当前环境的 MieNodes 插件根，兼容已有目录命名 |
   | `MIE_LLM_KEYS_FILE` | 当前 MieNodes 插件根下 `mie_llm_keys.json` 的绝对路径 |
   | `MIE_WEB_SETTINGS` | 整合包下的独立设置文件，如 `launcher/h3-engine/settings.json`。同目录存放任务记录与快照 |

   H3 Engine 已让显式 `MIE_PLUGIN_ROOT` 优先于相邻目录推断。找不到当前环境的 MieNodes 时给出提示。工作台支持用户主动保存后端地址，该值会覆盖 `MIE_COMFYUI_URL` 的默认值。不同环境应读取各自的设置文件。

4. 用 `GET /api/health` 确认 `status=ok`、`app=comfyui-mie-web`，并核对返回的 `plugin_root` 归属当前环境。PID 按服务 ID、环境、工作目录和脚本入口核对。端口被其他服务占用时报告冲突。CLI 启动后退出时，H3 进程应继续运行并能写日志，可直接重定向到服务日志文件。停止只回收匹配的 H3 进程，尤其防止多个环境指向同一 H3 junction 目标时误认 PID。
5. 在 `ui_qt/pages/advanced_services_page.py` 增加标签页，并通过 `ui_qt/qt_app.py` 接线。沿用现有标签页滚动区和主题刷新。首版提供状态、启动、停止、打开网页和日志，耗时操作放后台。工作台可独立启动。H3 Engine 本体升级走现有插件管理。
6. 复用 `core/cli/cmd_service.py` 和 `core/cli/parser.py`，接通 `service list`、`service start/status/stop/info h3-engine` 和 `logs h3-engine -n 100 --no-follow`。检查 `--env` 能一致传到启停和状态查询，尤其是 stop。目前 `info` 的依赖检查固定调用 OpenJutsu，需要改为调用选中的 runtime。

## 启动器验收

- 回归现有 WebUI 和 OpenJutsu 的标签页、CLI 和启动顺序。
- 切换有插件和无插件的环境，确认标签页显隐，以及 Python、设置、任务和资产路径随环境变化。
- 用临时资产、设置目录和测试端口跑一次真实 Flask 启停，验证健康检查、CLI 退出后服务仍可访问并持续写日志，以及启停幂等。模拟无关服务占用端口，确认启动报冲突、停止不影响占用者。
- 最后验证本机正常入口 `http://127.0.0.1:8200`。确认 ComfyUI 正常运行时，停止 H3 工作台不会中断渲染或更改队列。

## 代码入口

- 引擎侧 `webapp/__main__.py`、`webapp/config.py`、`webapp/flask_app.py`、`webui_node.py`。
- 启动器侧 `core/companion/spec.py`、`registry.py`、`manager.py`、`openjutsu_runtime.py`，`ui_qt/pages/advanced_services_page.py`、`ui_qt/qt_app.py`，`core/cli/cmd_service.py`、`parser.py`。

---

## 实施结果（2026-11-18，启动器侧已落地）

启动器侧按本计划实现，并按实施中发现的实际情况做了以下**偏离与补强**（都记在
启动器仓库的 `AGENTS.md` 与 `docs/companion-services-design.md` 里）：

1. **新增 runtime 分发表与四个可选钩子**：`core/companion/manager.py::_RUNTIME_MODULES`
   （runtime 字符串→模块）；runtime 可提供 `build_steps`（步骤列表，可零个一次性步骤）/
   `ready_probe`（语义就绪）/ `log_mode`（`"file"`=子进程直写日志）/ `health_info`（详细健康）。
   老 runtime 仍走 `build_step_cmd`/`build_proxy_cmd`/`build_app_cmd` 兼容路径（openjutsu 未受影响）。
2. **长驻服务必须文件直写日志**：原计划只说「CLI 退出后应继续运行并能写日志」。默认的
   管道 + launcher 内 drain 线程在 CLI 退出后会断管道，子进程写 stdout 抛 BrokenPipeError，
   Flask 每请求写日志 → 请求直接挂。故 H3 用 `log_mode()=="file"`（子进程持有自己的文件句柄）。
3. **身份门新增 `script_rel` + `registry.resolve_root_raw`**：原计划要求「按服务 ID、环境、
   工作目录和脚本入口核对」并「防止多环境指向同一 H3 junction 目标时误认 PID」。现有
   `IdentityRule` 只有 cmdline 子串 + `cwd_under_root`，表达不了「脚本入口」；且
   `resolve_root` 会 resolve 掉 junction（多环境塌成同一个路径）。现改为：启动命令按
   **未 resolve** 的 root 拼（命令行带各环境自己的路径文本），身份门新增 `script_rel`
   匹配 `<root>/<script_rel>`（raw 与 realpath 两种形态都认）。
4. **pidstore 跨环境共用 → 加环境闸门**：pidfile 路径不含 env，多环境共用。`_live_processes`
   现在比对 pidstore 的 `env_id` 与生效环境，不一致就不认也不杀；`service stop` 补上
   `--env` 透传（原来漏传，会拿激活环境的身份规则去动别的环境）。
5. **`service info` 的依赖检查按所选服务自己的 runtime 走**（原来固定调 openjutsu_runtime）。
6. **解释器解析**：相对 `python_path` 按**所选环境包根**解析（`h3_runtime.resolve_env_python`），
   不复用 `utils.paths.resolve_python_exec`（它优先 `stable_project_root()`，会拿到启动器
   自己那份没有 mie_* 的 python）。
7. **`MIE_PLUGIN_ROOT` 等变量是必填注入**：引擎侧 `webapp/config.py` 的兜底值指向别的机器的
   硬编码路径，且 `services/llm.py` 会把 LLM 密钥写回那份 → 找不到当前环境的 MieNodes
   资产库时判**依赖缺失硬失败**（退出码 8），不是「提示」。
8. **设置/任务/快照路径统一**：`<包根>/launcher/h3-engine/settings.json`（引擎侧
   `webui_node.settings_path()` 与启动器同口径），按环境分开；原来节点侧用的是
   `<插件根>/web_settings.json`，两边会看到不同的任务列表。
9. **端口 8200 与 8199 的冲突要写进迁移说明**：8199 是启动器自己的「WebUI 工作台」，
   H3 引擎旧默认也是 8199 → 老工作流里烤死的 8199 会让 ComfyUI 侧的节点去探另一个 app 的
   `/api/health` 并误判「已在跑」。

### 引擎侧改动（已落地，未提交）

- `webui_node.py`：新增 `settings_path()`，与启动器同口径（MIE_WEB_SETTINGS）。
- 端口 8200 的统一（`webapp/config.py`、`webui_node.py`、`start_webui.bat`、README）已在工作区，
  但**尚未提交**；`PLAN.md` 里残留的 8199 已改成 8200。

### 验收结果

- 单测：`tests/unit/test_h3_runtime.py`（14）、`tests/unit/test_h3_service.py`（7）、
  `tests/unit/test_companion_framework.py`（新增 script_rel / resolve_root_raw 用例）、
  `tests/unit/test_advanced_services_page.py`（H3 tab 显隐）；全量 unit 套通过。
- e2e：`tests/e2e/test_h3_lifecycle_e2e.py`（3 例，真 Flask 进程 + 真端口 + 真健康检查）：
  起停 + 健康检查 + 幂等 / launcher 退出后服务仍可访问并持续写日志 / 端口冲突时启动报冲突
  且不误杀占用者。临时包根用 junction 搭真包的 ComfyUI 与 python_embeded，
  设置与任务落在 tmp 目录，不污染真包。

### 尚未满足的前提（需要在真机上做）

- 三个已配置环境（`E:/FF/ComfyUI_Mie_2026_V8.0`、`E:/HH/Package/ComfyUI_Mie_2026_V9.0`、
  `E:/HH/Package/ComfyUI_Mie_2026_V9.0_cu126`）**都没有装 H3 引擎**，也没有 mie_craft /
  mie_llm / mie_prompt（只有 `C:/PP/V9/V9-Large/comfyui_mie_2026_v9.0_large` 全套齐）。
  → 这几个环境下 H3 tab 会隐藏、`service start h3-engine` 退 7/8（未安装 / 依赖缺失）。
  要在真机上验证「有插件/无插件环境切换」，需要先给目标环境装引擎（或建 junction）
  并把 mie_* 装进该环境的 embedded python。
- `requires_comfyui=False`，所以工作台可在 ComfyUI 离线时用；停止 H3 不会影响 ComfyUI 的
  渲染队列（工作台只是提交任务给 ComfyUI，停止它不会改队列）。

---

## 第二轮：与 WebUI 工作台同构的下载/安装/更新（2026-11-18）

### 核实到的关键事实（推翻了我第一轮的说法）

**H3 引擎的代码从来就不依赖 MieNodes 资产包。**

| 事实 | 证据 |
|---|---|
| 引擎要的是 **pip 包**，不是 MieNodes 这个 custom node | `requirements.txt` 注释：*comfyui-mie-craft … It is the same package MieNodes uses; **the three authoring packages have independent versions*** |
| 引擎仓库里 `MIE_PLUGIN_ROOT` 只出现在网页那一层 | 全仓 grep：只有 `webapp/config.py` 与 `webapp/flask_app.py`，**节点侧一处都没有** |
| 资产库位置本来就是可配置的 | `mie_craft.assets.library.resolve_library_dir()`：显式参数 → **`$MIE_ASSET_LIBRARY`** → `<plugin_root>/asset_library`；`resolve_plugin_root()` 的 docstring 明写「自带资产的 pack 把 MIE_PLUGIN_ROOT 指向**自己**」 |
| 节点侧只认一个 `library_dir` 输入框（默认空） | `mie_nodes.py:41` |

所以「H3 找 MieNodes」是 `webapp/config.py::_find_asset_pack()` **主动去 sibling 扫 + 硬编码兜底**的结果，
是一个**可选耦合**，不是技术必需。**我第一轮把它当成硬依赖写进了启动器的依赖闸门 —— 那是错的，已改。**

### 定下来的归属（用户拍板）

**资产库（角色/场景/分镜）与 LLM 密钥归 H3 引擎插件目录**，但**更新时绝不能影响它们**。

- 位置：`<插件根>/asset_library/`、`<插件根>/mie_llm_keys.json`
- 老布局（资产还在同级 MieNodes 资产包里，实测 220MB）→ 继续用那个 sibling，**不遮住用户数据**；
  全新安装 → 引擎目录里建空库
- 引擎侧只设 `MIE_ASSET_LIBRARY`（不设 `MIE_PLUGIN_ROOT` —— 那是「谁拥有资产库」的全局开关，
  MieNodes 自己会指向它，引擎抢了会打断它）

### 「更新不影响用户资产」的两层保护

1. **引擎仓库 `.gitignore` 排除 `asset_library/` 与 `mie_llm_keys.json`** —— 未跟踪 + 被忽略的文件
   不受 `git reset --hard` 影响（已实测；MieNodes 仓库当年就是这么干的）
2. **启动器更新前体检**（`installer.protect_user_data`）：`git ls-files` 发现数据曾被跟踪 →
   **先挪到仓库外暂存**，更新完再搬回

> ⚠️ 只「从索引摘除」**不够**：`reset --hard` 会把索引重置成 `origin/HEAD` 的树，远端历史里若含那些路径
> （用户曾 `git add -f` 提交过），工作树里的文件照样被还原 —— **这条是被测试抓出来的**。

### 安装链（与 WebUI 工作台同构）

| action | 做什么 |
|---|---|
| `service install h3-engine` | `git clone --depth 1` → `<包根>/ComfyUI/custom_nodes/ComfyUI-Mie-H3-Engine`（目录已有内容则跳过，**不删用户数据**）；走用户配置的 git 代理 |
| `service setup h3-engine` | 引擎 `requirements.txt` 逐包容错装进该环境的 embedded python；镜像上没有的新版本自动回退官方 PyPI |
| `service update h3-engine` | `git fetch` + `git reset --hard origin/HEAD`（与 webui 一致） |

通用实现放在启动器 `core/companion/installer.py`（从 `core/webui_installer.py` 泛化）。

### 镜像滞后（实测踩到）

华为云/阿里云镜像只有 `comfyui-mie-craft` 0.1.x，PyPI 已有 0.2.0 → `No matching distribution found`。
`installer.install_requirements` 因此加了回退：镜像上「没有」的包改用**官方 PyPI 显式地址**重装。
⚠️ 不能传 `None`（那是「用 pip 自己的配置」，而 `python_embeded/pip.ini` 里钉着镜像）。

### 引擎侧本轮改动

- `.gitignore`：排除 `asset_library/` 与 `mie_llm_keys.json`
- `asset_root.py`（新）：**唯一的归属规则** —— 自己的 `asset_library` → sibling（老布局）→ 自己。
  节点侧与网页侧都调它，两边不可能解析出两个库
- `__init__.py`：`os.environ.setdefault("MIE_ASSET_LIBRARY", asset_root.asset_library_dir())`
  —— 节点侧与网页侧看到同一份库，又不干扰 MieNodes
- `webapp/config.py`：`_find_asset_pack()` 改为「自己的目录 → sibling（老布局）→ 自己」，
  **删掉那个跨机器的硬编码兜底**
- `webui_node.py`：`settings_path()`（第一轮）

### 一处返工：节点侧与网页侧解析成了两个库

上面那版 `__init__.py` 把 `MIE_ASSET_LIBRARY` **无条件**指向 `<引擎根>/asset_library`。
老布局下引擎目录里根本没有这个目录，于是：

| 进程 | 解析到的库 | 里面的角色 |
| --- | --- | --- |
| ComfyUI 节点侧 | `F:\ComfyUI-Mie-H3-Engine\asset_library`（新建的空库） | **0** |
| 工作台网页侧（启动器起） | `custom_nodes\comfyui_mienodes\asset_library` | 358 |

页面列出 358 个角色，渲染时节点说「找不到」—— 正是「老布局不受影响」这句话要防的事。
实测（本机，真实安装）：`resolve_library_dir("")` 在两个进程里给出两个不同目录。

改法：归属规则收进 `asset_root.py` 一处，节点侧与网页侧调同一个函数；
`MIE_ASSET_LIBRARY` 的值改成**解析出来的那个库**，不再是「引擎根下那个还不存在的目录」。

### 验收结果（本轮）

- 全量 unit + e2e 通过
- 真机跑通整条链（V8 环境）：`install`（clone 走 gh-proxy）→ `setup`（6 个依赖，含镜像回退）→
  `start`（2.3 秒就绪）→ `/api/health` 回 `plugin_root = 引擎自己的目录` → CLI 退出后服务仍可访问、
  日志持续写入（文件直写）→ `stop` 干净收尾
- 新增 `tests/unit/test_companion_installer.py`（8 例，含**「更新后用户数据原样还在」**这条核心保证）
- 新增 `webapp_tests/test_asset_root.py`（6 例）：归属三态 + 「网页侧与节点侧必须同一个库」
- 本机实测（真实安装，插件按 ComfyUI 的方式 import）：97 个节点注册成功，
  `MIE_ASSET_LIBRARY` = `custom_nodes\comfyui_mienodes\asset_library`，节点侧可见 358 个角色 —— 与工作台一致

