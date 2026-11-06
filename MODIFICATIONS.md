# MODIFICATIONS — 我们对上游的改动台账

> **上游**：`ethanfel/ComfyUI-MiniMaxH3-Context-Loop`（GPL-3.0）
> **上游作者**：`NikoDemon80`、`Ethanfel` 及贡献者 —— **署名与版权声明一律保留，不改动**
> **分叉基线**：`43afeff`（上游 `main`，2026-10-14）
> **本仓**：`MieMieeeee/ComfyUI-Mie-H3-Engine`
> **计划**：[`PLAN.md`](PLAN.md)

本文件按 GPL-3.0 §5(a)(b) 的要求逐条记录我们的改动。
**每一行都写清：改了什么、为什么、影响哪些文件。**

---

## 原则

0. **（2026-11-06 用户定调）本项目的核心是"尽可能吸纳上游已测过的代码"；
   但凡是已经发现的问题，就选最优解，而不是最小补丁。**
   这条原则的落法是：
   - **上游的测试是契约，不是障碍。** 上游有测试覆盖的地方，**先读那个测试**——
     它往往已经写清了上游想要的语义（例如 reference cache 的
     *"ambiguous semantic recipes must not be chosen by mtime"*），
     而实现却没做到。这时**让实现去满足上游的测试**，而不是改测试。
   - **上游的守卫若是对的，原样保留。** 上游已有的歧义守卫、完整性校验、
     journal 事务语义都经过实测，不因为"我们也要改这块"就重写。
   - **只在真正有缺陷的地方改**，且改动要**能被测试钉住**
     （每处改动配一条负对照：原实现必须报红）。
   - **发现上游的前提是错的，当场纠正并写进文档**（例如"临时名里的 32 位 hex
     是内容摘要"实为随机事务号；"reference cache 多候选必须拒绝"实为
     上游的合法格式迁移场景）。
1. **改动尽量是新增，不原地改。** 上游的 `chain_nodes.py` 单文件 31,538 行、
   占顶层生产代码 29.3%，原地改会让将来的任何上游对照变贵。
   能用新模块解决的，不动老文件。
2. **与上游不再有代码往来**（2026-11-06 裁定）。`upstream` remote **只作溯源参考，
   永不合并**；base commit 固定 `43afeff`。**不向上游回流任何改动。**
   ⇒ 我们的 commit 不需要为"将来要合并"而保持可重放形态；
   ⇒ 代价是**上游的修复拿不到**，ComfyUI 内核升级的兼容性由我们自己扛（见计划 R8）。
3. **每条改动对应计划里的一个决策或一个已定位的缺陷。**
   计划没有的改动，先写进计划。

---

## 台账

| # | 阶段 | 改了什么 | 为什么 | 影响文件 | 状态 |
|---|---|---|---|---|---|
| 0.1 | Phase 0 | 仓库建立在上游 `43afeff` 历史上 | 计划 §9 Phase 0 指定的基线 | 全部 | ✅ 已做 |
| 0.2 | Phase 0 | `pyproject.toml`：`name` → `comfyui-mie-h3-engine`，`version` → `0.7.3+mie.1`，`DisplayName` → `Mie H3 Context Loop`，`PublisherId` → `mie`，URLs 指向本仓 | 让分叉身份可辨识；`version` 带 `+mie.1` 后缀便于区分上游版本 | `pyproject.toml` | ✅ 已做（**见 0.9：它触发了一条上游契约，本条与 0.9 必须一起读**） |
| 0.3 | Phase 0 | `pyproject.toml` 的 `authors` **追加** `MieMieeeee`，**未删除**上游两位作者 | GPL 要求保留作者署名 | `pyproject.toml` | ✅ 已做 |
| 0.4 | Phase 0 | `README.md` 顶部**前置**分叉横幅；上游正文逐字保留 | 让人一眼看出这是分叉、基线是哪个、改了什么 | `README.md` | ✅ 已做 |
| 0.5 | Phase 0 | `PLAN.md`（1,286 行）入库 | 计划的唯一真源 | `PLAN.md` | ✅ 已做 |
| 0.6 | Phase 0 | `.gitignore` 在上游条目后追加我们的 Python / 测试 / 临时产物条目 | 上游只忽略 4 条 | `.gitignore` | ✅ 已做 |
| 0.7 | Phase 0 | `LICENSE`、`THIRD_PARTY_NOTICES.md`、`docs/FEATURE_TRACEABILITY.md` **逐字未改** | GPL 要求；计划 §9 明确列出 | — | ✅ 未改 |
| 0.8 | Phase 0 | `git remote add upstream` | D6 的 merge 同步需要它 | `.git/config` | ✅ 已做 |
| **0.9** | **Phase 0** | **`tests/_web_cache_bust_unit_test.py` 的版本正则放宽为接受 `+local` 构建元数据** | **实测发现的真缺陷**：上游把 `pyproject.toml` 的 `version` 同时当作**浏览器 cache-bust token 的默认下限**，其正则 `^(\d+)\.(\d+)\.(\d+)$` 不接受 `+mie.1`，于是 `0.7.3+mie.1` 让这个测试**直接失败**（`AssertionError: Invalid web cache token '0.7.3+mie.1'`）。而 `0.7.3+mie.1` 在 PEP 440 / SemVer 里正是"上游版本 + 本地改动"的标准写法（`0.7.3 < 0.7.3+mie.1 < 0.7.4`）。**改法只放宽"能否解析"，不动排序语义**：正则接受可选后缀，`version_key()` 取前三个数字组。回归验证：把版本号换回 `0.7.3` 也同样通过 ⇒ 排序保证未变 | `tests/_web_cache_bust_unit_test.py` | ✅ 已做 |
| **1.1** | **Phase 1** | **P0-a：关键路径的 `checkpoint_verification` 默认由 `cached` 改为 `strict`**（widget 默认值、`export()` 签名默认、`or "cached"` 兜底三处），tooltip 重写为说明"cached 信任一个从未被强制的不变量"以及何时才该选 cached | 上游 tooltip 明说 cached *"trusts matching size and modification time"*——它把 `(size, mtime_ns)` 组合当成路径的内容身份。**本条是当天可做的生产修复**，不等 Q-F′，不动三处恢复逻辑 | `chain_nodes.py`（3 处） | ✅ 已做 |
| **1.0** | **Phase 1** | **新增 `tests/_mie_png_verification_strict_unit_test.py`** | 把 P0-a 的价值**钉成可执行的事实**而不是声明：① 证明 cached 模式**看不见**"字节变了但 size/mtime 未变"（原地覆写 + 保留 mtime，返回 `cached SHA-256`）；② 证明 strict 模式**能拦住**它（重算哈希 → `ValueError`）；③ 断言节点的 widget 默认就是 `strict`。**负对照已做**：把默认值改回 `cached` → 测试报 `AssertionError: cached` ⇒ 不是空测试 | `tests/_mie_png_verification_strict_unit_test.py` | ✅ 已做 |
| **1.2** | **Phase 1** | **P0-b ①：遗留 checkpoint 指针恢复兜底不再按 mtime 挑** —— 多份 `clip_NNNN.json.restore.<id>.tmp` 时**拒绝猜测**：报错并**保留全部文件**（不提升、不删除）；单份时行为完全不变 | 上游按 mtime 最大的那份提升为正式指针。**实测纠正**：临时名里那 32 位 hex 是 `uuid.uuid4().hex`（随机事务号，`chain_nodes.py:28650`），**不是内容摘要** ⇒ 盘上没有任何信号能指出哪一份才是对的。上游的 journal 路径本来就拒绝猜测（损坏即删临时文件）；遗留路径改成同样的拒绝语义，但**不删文件**（上游注释承诺"no media is deleted here"） | `chain_nodes.py` `_recover_checkpoint_pointer_transactions` | ✅ 已做 |
| **1.3** | **Phase 1** | **P0-b ②：封印 Chapter 清单改为按内容里的 `sealed_at` 排序** —— 选谁不再看文件的 mtime；`sealed_at` 是**写在文档内部**的时间戳且**被排除在清单摘要之外**（每次保存重写），所以 robocopy /COPYALL 会连它一起拷走，**不受文件系统 mtime 影响**。没有 `sealed_at` 的旧文档才回落到 mtime | 清单的文件名就是它的 `chapter_manifest_id`（内容摘要），选中后还会校验 `摘要 == id == 文件名`；但**选哪一份**此前是按 mtime ⇒ 一次保留 mtime 的还原/镜像就能让"当前封印"指到旧的快照上。**不改清单正文**（加 `_mie_seq` 之类会改变摘要和文件名，等于另造一份快照） | `chain_nodes.py` `_load_chapter_manifest` | ✅ 已做 |
| **1.4** | **Phase 1** | **P0-b ③：reference cache 描述符必须与自己的文件名一致** —— 缓存文件名带 `signature[:24]`，描述符内容里的 `signature` 必须与它相符，不符则**跳过该候选** | 缓存文件名本来就声明了"这份描述符属于哪个契约"。上游只校验字段存在，不校验内容与文件名一致 ⇒ 一次原地覆写（把 A 的内容换成 B 变体的内容）会让描述符自相矛盾而无人察觉。**上游那条已测的歧义守卫（不同 semantic-anchor 设置必须拒绝）原样保留**——它是对的，且上游自己的测试 `tests/_reference_cache_fingerprint_unit_test.py` 就在守它 | `chain_nodes.py` `_reference_cache_candidates` | ✅ 已做 |
| **2.1** | **Phase 2** | **节点类型名一律加 `\|MieH3` 后缀**，且在**模块级**施加（每个模块的 `*_NODE_CLASS_MAPPINGS` / `*_NODE_DISPLAY_NAME_MAPPINGS` 各自加后缀），不是只在 `__init__.py` 聚合处 | ComfyUI 对重名**静默覆盖**，胜者由**文件系统目录序**决定（实测：上游赢了，尽管我们的目录名排序在前）。**全仓只有一个名字空间**：模块字典里的键 = ComfyUI 注册的名字 = 工作流/提示词里出现的名字 | `fork_identity.py`（新增）+ 13 个模块 + `__init__.py` | ✅ 已做 |
| **2.2** | **Phase 2** | **`fork_identity.py`**：提供 `add_suffix` / `type_aliases` / `type_alias_set` / `display_name` | 改名之后，**读旧存档**的地方必须同时接受新旧两个名字：改名前的提示词/清单/工作流里是上游的名字，两包同装时执行的工作流里也是上游的名字 | `fork_identity.py`（新增） | ✅ 已做 |
| **2.3** | **Phase 2** | **7 处运行时自省 + 15 处文档/清单比对**改为接受新旧两个名字；**构造**新节点的 6 处（`graph.node(...)`）改用带后缀的名 | 比较类必须宽松（读的是可能很旧的文档），构造类必须用**我们的**名字（子图节点要解析到我们注册的类） | `chain_nodes.py`、`upscale_nodes.py`、`reference_cache_recovery.py`、`run_manager.py` | ✅ 已做 |
| **2.4** | **Phase 2** | **`tools/gen_rename_map.py`**：从各模块的 `NODE_CLASS_MAPPINGS` 用 AST 扫描生成 `tools/node_rename_map.json`（**86 条**），带 `--check` | 87 条手写必漏；漏一条就是一个静默冲突 | `tools/gen_rename_map.py`、`tools/node_rename_map.json`（新增） | ✅ 已做 |
| **2.5** | **Phase 2** | **`tools/apply_rename.py`**：把映射表应用到 `web/`、`tests/`、工作流 JSON 等 | 改名要覆盖前端 —— 上游前端用**字符串字面量**比对类型名 | `tools/apply_rename.py`（新增） | ✅ 已做 |
| **2.6** | **Phase 2** | **`tools/migrate_workflows.py`**：改写保存的工作流里的类型名 | 用户存量工作流要能迁移 | `tools/migrate_workflows.py`（新增） | ✅ 已做 |
| **2.7** | **Phase 2** | **上游的 Registry 发布工作流改成只手动触发** | 它在"push 到 main 且改动 `pyproject.toml`"时**自动**发布到 Comfy Registry；我们 Phase 0 正好改了那个文件 ⇒ 第一次 push 就会触发 | `.github/workflows/publish_action.yml` | ✅ 已做 |
| **4.1** | **Phase 4** | **新增 `mie_nodes.py`：三个薄壳节点** —— `MieH3LoadScriptAsset` / `MieH3ScriptToPlan` / `MieH3LoadAssetRoster` | 逻辑全在 `ComfyUI-Mie-Core`，这里只有 ComfyUI 的壳。`MieH3ScriptToPlan` 用的是**同一个零 LLM 组装器**，所以引擎出的 plan 与 MieNodes 出的 plan 逐字节相同；资产 roster 插槽类型也来自 Core ⇒ 两个包的 plan 可以互相接线 | `mie_nodes.py`（新增）、`__init__.py` | ✅ 已做 |
| **4.2** | **Phase 4** | **引擎包依赖 `ComfyUI-Mie-Core`**（`pip install -e`） | 计划 D12：Core 是纯 pip 包。**实测**：只装到系统 python 时，引擎包在本机 ComfyUI 里 IMPORT FAILED（日志一行、无异常栈）⇒ 必须装进 ComfyUI 自己的 python | `__init__.py`、`mie_nodes.py` | ✅ 已做 |
| **2.8** | **Phase 2** | **CI 增加"映射表是当前"的断言** | 映射表过期 = 有个节点类型注册用一种拼写、查找用另一种 | `.github/workflows/ci.yml` | ✅ 已做 |
| **1.6** | **Phase 1** | **P0-c：size 分层** —— 小于 `SMALL_FILE_BYTES`（64MB）的产物**一律直接哈希**，身份缓存只对大文件有意义 | 小文件哈希只要毫秒级，缓存省不下什么，却让"内容变了但 size/mtime 没变"漏检；分层后**暴露面缩到只剩大文件** | `checkpoint_identity.py`、`chain_nodes.py` | ✅ 已做 |
| **1.7** | **Phase 1** | **P0-e：文件系统识别** —— 新模块 `checkpoint_identity.py`，用 `GetVolumeInformationW` 识别卷类型，`GetDriveTypeW` 识别映射盘；**信号不可信就不用** | **FAT/exFAT 的 file index 来自目录项位置，原地覆写不变** —— 恰好防不住我们要防的场景；UNC/SMB 可能报 0。降级规则是**自校准**的：拿不到可靠信号就不放行 | `checkpoint_identity.py`（新增） | ✅ 已做 |
| **1.8** | **Phase 1** | **P0-f：write-once** —— 已发布的修订产物**只写一次**：同名不同内容 ⇒ 拒绝并报错；同名同内容 ⇒ 允许（幂等）。恢复路径**显式豁免** | 把"checkpoint 不可变"从未强制的不变量变成被强制的事实。发布路径按修订 uuid 命名 ⇒ write-once 是天然语义 | `checkpoint_identity.py`、`chain_nodes.py` | ✅ 已做 |
| **1.9** | **Phase 1** | **两个守卫都接入身份信号**：checkpoint 守卫与**导出 PNG/WAV 守卫**（此前未列入清单） | 第二处守的是**用户最终看到的画面文件**，上游自己的注释已说"mtime 不是内容身份"却仍在用 | `chain_nodes.py` | ✅ 已做 |
| **1.10** | **Phase 1** | **两处"最新者胜"改按内容时间戳** —— 活跃 checkpoint 指针改按 `segment.created_at`（`chain_nodes.py:10350`、`run_manager.py:447`） | 指针里的 `created_at` 是**内容里的**时间戳，robocopy /COPYALL 会连它一起拷走，**不受文件系统 mtime 影响** | `chain_nodes.py`、`run_manager.py` | ✅ 已做 |
| **1.11** | **Phase 1** | **新增 `tests/_mie_checkpoint_identity_unit_test.py`** | 钉住降级规则：网络共享 / FAT / exFAT / ino=0 / 被 pin 的目录 ⇒ 一律退回哈希；普通本地 NTFS ⇒ 保留信号。**该测试当场抓出一个真 bug**（`"exFAT"` 与 `"EXFAT"` 大小写不一致导致 exFAT 分支失效） | `tests/_mie_checkpoint_identity_unit_test.py`（新增） | ✅ 已做 |
| **1.5** | **Phase 1** | **新增 `tests/_mie_mtime_selection_unit_test.py`** | 把上面三条钉成可执行事实。**逐项负对照已做**：用上游原实现跑，三条**全部报红**（"an ambiguous legacy pointer must not be promoted" / "the snapshot with the newer sealed_at must win" / "a descriptor that does not match its file name must be rejected"）；用我们的实现跑，三条**全部通过** | `tests/_mie_mtime_selection_unit_test.py` | ✅ 已做 |
| **1.2** | **Phase 1** | **P0-b：三处"按 mtime 选源"改按各自规则选源** | 外部覆写（robocopy /COPYALL 保留 mtime）能把旧文件推成正式产物 | `chain_nodes.py` `:28453-28458` / `:22055` / `:4940` | ⬜ 未做（规则见 PLAN §8.6.1） |
| **1.3** | **Phase 1** | **P0-c：`<64MB` 一律直接哈希** | 把 P0 暴露面缩到只剩大文件 | `chain_nodes.py` `:24681` 附近 | ⬜ 未做 |
| **1.4** | **Phase 1** | **P0-e：`GetVolumeInformationW` 识别 FAT/exFAT ⇒ 无身份信号 ⇒ 强制 strict** | exFAT 的 file index 来自目录项位置、原地覆写不变，且**非 0**——只看 `ino == 0` 会误放行 | 新模块（优先，不原地改） | ⬜ 未做 |
| **1.5** | **Phase 1** | **P0-f：run 输出目录 write-once + 恢复路径显式豁免 + 恢复按内容选源** | 把"checkpoint 不可变"从未强制的不变量变成事实；但恢复路径必须豁免，否则与 `:28458` 撞车 | 新模块 | ⬜ 未做 |
| 1.6 | Phase 1 | Windows 无符号链接/硬链接权限降级（WinError 1314） | 本机开发者模式未开时上游会崩 | `chain_nodes.py` / `project_assets.py` | ⬜ 未做 |
| 1.7 | Phase 1 | Node 24 `vm.runInContext` ESM 兼容 | 10 个 `.mjs` 测试在本机红 | `tests/_*.mjs` | ⬜ 未做 |
| **2.1** | **Phase 2** | **节点类型名加 `\|MieH3` 后缀（87 个）** | ComfyUI `nodes.py:2305-2308` 对重名**静默覆盖**、无告警；同装两包会执行错实现 | `__init__.py` + 各模块 `NODE_CLASS_MAPPINGS` | ⬜ 未做 |
| **2.2** | **Phase 2** | **`web/` 里 47 个硬编码类型名、20+ 个文件一起改** | 上游前端用字符串比对类型名；只改 Python ⇒ 扩展挂不上 `\|MieH3` 节点，两包同装时还会挂到上游原名节点上 | `web/*.js`、`web/*.mjs` | ⬜ 未做 |
| 2.3 | Phase 2 | `tools/node_rename_map.json` **自动生成** + `tools/migrate_workflows.py` | 87 条手写必漏；迁移脚本要处理 subgraph 嵌套、幂等、目录批量 | `tools/` | ⬜ 未做 |
| **3.1** | **Phase 3** | **`POST /mieh3/plan_preview` 路由** | 前端在图执行前读不到 socket 的值 ⇒ 需要自己的通道；编译留在 Python 一处 | 新模块 | ⬜ 未做 |
| 3.2 | Phase 3 | `mieH3PlanProvider` 前端约定 | Scene Prompt Editor / Plan Studio 现在只读 widget，接外部 plan 时显示的是上游默认样例 | `web/` 新文件 | ⬜ 未做 |
| 3.3 | Phase 3 | `plan_json` 校验合并为一份（上游 `_normalize_plan` + 我们的 `validate_plan`） | 同一契约的两个半实现，各自漂移 | 新模块 | ⬜ 未做 |
| 4.1 | Phase 4 | 资产/剧本/plan 的 ComfyUI 外壳节点（薄壳，逻辑在 `ComfyUI-Mie-Core`） | 计划 D11 | 新模块 | ⬜ 未做 |

---

## 本机实测记录（2026-11-06）

环境：本机 ComfyUI `C:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\ComfyUI`（0.44.0，8188），
本仓以 **junction** 挂在 `custom_nodes/ComfyUI-Mie-H3-Engine`（改 F: 即刻对 ComfyUI 可见）。

### 测试套件

| 阶段 | 结果 |
|---|---|
| 改动前基线（本仓，未改上游代码） | **189 passed / 58 failed** |
| P0-a + 缓存契约修复 + 回归测试 | **192 passed / 56 failed** |
| **P0-b（三处选源规则）落地后** | **193 passed / 56 failed** |
| 差异 | **零新增失败**；修好 3 个（cache-bust 契约 + 两个新增回归测试）；基线里多出的 2 个是环境/并行导致的偶发（单独跑都过） |

56 个失败与上游克隆在同一台机器上的失败数一致，**全部是既有失败**，
按计划 §8.6 的四类排查属于 Windows 环境类（symlink 权限、NTFS mtime 粒度）与 Node 24 harness 类。

> **注意**：`_selflift_upscale_preview_unit_test.py` 与 `_selflift_hunt_unit_test.py`
> 在并行负载下**偶发**失败（asyncio 计时），单独跑 3/3 通过。
> 判定"新增失败"时要先单独复跑，不要直接归因给改动。

### 解释器这件事（Phase 1 的一条真发现）

**套件用哪个 python 跑，结果差很多**：

| 解释器 | 结果 |
|---|---|
| 系统 python 3.12.10 + `PYTHONPATH=<repo>` | **192 / 56**（本文所有基线都是这个） |
| ComfyUI 自带 python 3.13.12（隔离模式） | **80 / 168** |

两个原因：

1. **ComfyUI 自带的 python 是隔离模式**（`python313._pth` 固定了 sys.path：
   不加脚本目录、**忽略 `PYTHONPATH`**）⇒ 上游测试互相 import 的 fixture
   （`source_audio_fixtures` 等）全部找不到；
2. 系统 python 缺 `comfy_aimdo`（它装在 ComfyUI 的 3.13 site-packages 里，
   主体是纯 Python + ctypes 加载 DLL）⇒ 30 处失败行提到它。

⇒ **"56 个失败"这个数字是环境产物，不是代码真相。** 想让套件真正全绿，
得先解决"用哪个解释器 + 怎么让 ComfyUI 核心模块可导入"，
这是 Phase 1 的**环境子任务**，与 P0 的代码修复是两条线。
本机已验证的可行组合：**ComfyUI 自带 python + 一个把 `tests/` 插进 `sys.path` 的引导脚本**
（`/tmp/run_test.py` 形态）⇒ `_reference_cache_fingerprint_unit_test.py` 在该组合下**通过**，
而它在系统 python 下因为 `comfy_aimdo` 失败。

### 节点名冲突（计划 §3.1 的实证）

两个包**同时启用**时的实测结果：

```
启动日志：ComfyUI-MiniMaxH3-Context-Loop  导入成功
          ComfyUI-Mie-H3-Engine            导入成功
          重复注册/覆盖告警：0 条
/object_info 里 MiniMaxH3* 类型数 = 148（上游单独时也是 148；若叠加会是 296）
```

⇒ **确认为静默覆盖，不是叠加** —— 与计划 §3.1 一致。

**但比计划写的更脆弱**：日志里的 "Import times" 一节是
`for n in sorted(node_import_times)`，**按耗时排序**，不是导入顺序；
真正的导入顺序是 `os.listdir(custom_nodes)` 的**文件系统目录序**。
实测中 `ComfyUI-Mie-H3-Engine` 先于 `ComfyUI-MiniMaxH3-Context-Loop` 被导入，
于是**上游赢了**——即使我们的包名字看起来更"新"。

⇒ **两个包同时启用时谁生效，取决于一个完全偶然的文件系统顺序因素，且没有任何提示。**
这比"后加载者胜"更糟：**改名（Phase 2）不是可选项，是必须项**。

### 当前本机状态

上游包已用启动器 `plugins disable` 隔离（目录重命名为 `*.disabled`），
本机只加载本仓。恢复上游：`ComfyUI启动器-CLI.cmd plugins enable ComfyUI-MiniMaxH3-Context-Loop`。

### 顺带撞到的两个本机坑（已在计划 §11 记录，此处补细节）

1. **启动器的 `stop` 会谎报**：`{"stopped": false, "pid": null}` 而 8188 仍在服务
   —— pidfile 没记到那个进程。必须按 PID 结束（`netstat -ano | grep :8188`）。
   启动器自己拉起的进程则能正常 stop/restart（返回真实 pid）。
2. **ComfyUI 的 "Import times" 一节是按耗时排序的**，不能当作导入顺序读。

---

## P0-d 排查报告：36 处 mtime 参与点（2026-11-06）

用 AST 精确扫描（排除文档串里的假阳性），生产代码里 **36 处** `st_mtime_ns` /
`getmtime` 参与点，分布在 **13 个文件**。按计划的四类逐处定类：

| 类 | 含义 | 处数 | 判定 |
|---|---|---|---|
| **① 缓存跳过** | 拿 mtime 当"内容没变"的凭据，于是跳过哈希 | **2** | **两处已修**（P0-c + P0-e） |
| **② 身份等值比较** | 拿 (dev, ino, size, mtime) 元组当内容身份 | **13** | 全部**良性**（见下） |
| **③ 最新者胜** | 拿 mtime 挑"最新的那个"当正式产物 | **6** | **五处已修**，一处良性 |
| **④ 年龄清理 + 报告** | 记录时间戳、算年龄、列清单 | **15** | 全部**良性** |

### ① 缓存跳过 —— 两处，都已修

| 位置 | 作用 | 修法 |
|---|---|---|
| `chain_nodes.py:24807` | **checkpoint 守卫**：拿 (sha256, size, mtime) 比对，命中就跳过哈希 | P0-c（<64MB 一律直接哈希）+ P0-e（身份信号不可信时不用缓存；可信时把 dev/ino 一起比对） |
| `chain_nodes.py:24923` | **导出 PNG/WAV 守卫**：同样的形状 | 同上 |

第二处此前**没人注意过**：它守的是**导出的画面文件**——那正是用户最终看到的东西，
而上游自己的注释已经写着 *"Timestamps are a cache hint, not content identity"*，
却在 cached 分支里照样拿它当等值依据。

### ② 身份等值比较 —— 十三处，全部良性，但有一处必须写清楚

| 位置 | 用途 | 为什么良性 |
|---|---|---|
| `storage_inventory.py:212-213`、`:371-372` | 扫描期的 before/after 竞态检测 | **上游自己写着** *"This is not an atomic snapshot and does not detect same-stat byte edits"* ⇒ 它是**竞态检测**，不是内容身份。**这条注释恰恰加强了我们的论据**：连上游都承认这个元组抓不到"stat 不变但字节变了" |
| `storage_inventory.py:380` | 目录那一半的 mtime-only 比较 | 同上，是目录的竞态检测 |
| `png_video_export.py:131`、`processing_checkpoint_delete.py:271`、`chapter_snapshot_retirement.py:80`、`assembly_checkpoint_cleanup.py:29`、`working_branch_cleanup.py:69` | 删除/清理**记账**用的身份元组 | 只用来记录"删的是哪个文件"，不判定内容 |
| `chain_layout_conversion.py:52`、`:61` | **搬迁**校验：移动后 size/mtime 必须一致 | 搬迁不改变内容，mtime 不变是**应该**的 |
| `chain_nodes.py:8720`、`:8515`、`:8529`、`storage_inventory.py:149`、`:186` | 清单/报告里记录的时间戳 | 纯记录 |
| `chain_nodes.py:24935` | 导出增量身份：size + mtime **旁边就有 sha256** | 内容判据在哈希上 |

### ③ 最新者胜 —— 六处，五处已修

| 位置 | 挑什么 | 修法 |
|---|---|---|
| `chain_nodes.py:4972` | reference cache 候选 | P0-b：**描述符的签名必须与文件名一致**，不符即跳过 |
| `chain_nodes.py:22128` | 封印 Chapter 清单 | P0-b：改按**内容里的 `sealed_at`** 排序 |
| `chain_nodes.py:10350` | 活跃 checkpoint 指针 | 本轮：改按**内容里的 `segment.created_at`** 排序 |
| `run_manager.py:447` | 活跃 checkpoint 指针（第二处） | 本轮：同上 |
| `chain_nodes.py:28453`（遗留恢复） | 恢复用的临时指针 | P0-b：**多份 ⇒ 拒绝猜测**并保留全部文件 |
| `chain_nodes.py:27987` | 审阅预览 `*.review.mp4` | **良性**：文件名前缀已含 `video_hash`，过滤后剩下的都是同一份内容，mtime 只是挑个代表 |

### ④ 年龄清理 + 报告 —— 十五处，全部良性

`checkpoint_manager.py:307/1063/1147`（时间戳的记录与格式化）、`handoff_state.py:335`
（按年龄判 stale，这是它本来的用途）、`png_export_transaction.py:90`、
`chain_nodes.py:13034/13040/13306`（`IS_CHANGED` 的输入指纹——**用途正是"输入变没变"**，
不是内容完整性）、以及各类清单记录。

> 一处**残留**要写明：`IS_CHANGED` 用的 (path, size, mtime) 指纹**抓不到同 stat 的改写**
> ⇒ 源媒体被原地改写时，节点不会重跑。后果是"用旧的条件"，不是"渲染出错的文件"，
> 且它本来就是 ComfyUI 的缓存失效机制 ⇒ 不在 P0 范围内。

### 结论

**真正会让错内容进成片的，只有 ① 和 ③ 两类，共 8 处 —— 现在全部处理完毕。**
② 和 ④ 的 28 处要么是竞态检测，要么是记账/报告/年龄计算，**没有一处被当成内容完整性判据**。

排查过程中还发现**两处此前未列入清单的"最新者胜"**（`run_manager.py:447` 与
`chain_nodes.py:24923`）——前者是同一类问题的第二个副本，后者是**第二个缓存守卫**。
这说明 36 处这个数字本身是会变的：**每次上游改动都要重扫**，
所以 `tools/` 里留了扫描方法的说明，CI 的基线闸门会在新增失败时报警。

---

## CI 与发布（2026-11-06）

本仓未来 **public**，CI 可以上云。但**闸门不能是"零失败"**——上游套件在任何平台上
都不干净（Windows 丢 56 个脚本，Linux 丢另一批），要求零失败的闸门从第一次跑起就是红的。

⇒ 落地是**基线闸门**：

| 文件 | 作用 |
|---|---|
| `tools/ci_gate.py` | 跑套件 → 取失败集 → **串行复跑**剔除时序敏感的 flaky → 与 `tools/known_failures/<platform>.txt` 比对 → **只有新失败才红** |
| `tools/known_failures/windows-latest.txt` | 本机实测的 56 个既有失败（193/56） |
| `.github/workflows/ci.yml` | `ubuntu-latest` + `windows-latest` 双平台 |

**我们的两条回归测试是硬门槛**（`_mie_png_verification_strict_unit_test.py`、
`_mie_mtime_selection_unit_test.py`）——无论基线怎么说都必须通过。

**明确不在 CI 里的**：Windows 卷类型（FAT/exFAT）、映射盘、符号链接权限、
"原地覆写且保留 size/mtime"的探针、`MIE_LIVE_E2E=1` 真机渲染矩阵
——它们必须在**本机与局域网的真实盘**上跑。

### 修掉的一个隐患：上游带来的 Registry 发布工作流

上游的 `.github/workflows/publish_action.yml` 在 **push 到 main 且改动 `pyproject.toml`** 时
**自动**触发 Comfy Registry 发布。我们 Phase 0 正好改了 `pyproject.toml`
⇒ **第一次 push 就会触发它**。已改成**只保留手动触发**，并在文件顶部写明
何时、怎样才该真的发布（Q-E 仍暂缓）。

### 公开前的卫生扫描（已做，2026-11-06）

仓库内**无**密钥/令牌、**无**内网地址或 UNC 路径、**无**个人路径、**无**未跟踪残留；
大于 2MB 的文件只有上游自带的示例工作流素材（本就公开）。

---

## 与上游的接口约定（我们依赖、但不拥有的部分）

| 上游的东西 | 我们怎么用 | 我们的义务 |
|---|---|---|
| `plan_json` / `plan_json_input` socket | 我们的生成器产出这个字符串 | 保持 schema 不变（`shots` + `prompt_prefix`；`length` 满足 `5 ≤ n ≤ 3592 且 n % 17 == 5`；`seed` 用十进制字符串） |
| `H3_CHAIN_PLAN` / `H3_PROJECT_ASSETS` / `H3_CHAIN_POLICY` socket 类型 | 直接消费 | 不改它们的定义 |
| `/prompt` / `/history` HTTP 接口 | 产品层驱动渲染用 | 只做只读消费 |
| `output/h3_chains/<run>/` 布局 | 产物取证 | 不改布局 |

---

## 待办提醒

- **Q-F′ 未裁定**：CI 与"不再推送"冲突。GitHub 托管 runner 是虚拟盘，
  Windows 卷类型 / 映射盘 / 符号链接权限 / 原地覆写**测不到**——
  这几组探针必须在本机和局域网真实盘上跑。见 `PLAN.md` §14。
- **上游仍有 7 个 `sceneweaver-*` 分支未合入 `main`**，
  其中 `sceneweaver-asset-library` 与我们的资产层方向重叠。
  同步前先看它（计划 §1.1）。
