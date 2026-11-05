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

1. **改动尽量是新增，不原地改。** 上游的 `chain_nodes.py` 单文件 31,538 行、
   占顶层生产代码 29.3%，原地改会让将来 `git merge upstream/main` 变贵。
   能用新模块解决的，不动老文件。
2. **同步用 merge，不用 rebase**（计划 D6）。上游是单人高频项目（7 周 954 commit），
   我们的 commit 一旦被 rebase 重写，将来回流上游会变成另写一遍。
   `upstream` 是永久的第二 remote。
3. **每条改动对应计划里的一个决策或一个已定位的缺陷。**
   计划没有的改动，先写进计划。

---

## 台账

| # | 阶段 | 改了什么 | 为什么 | 影响文件 | 状态 |
|---|---|---|---|---|---|
| 0.1 | Phase 0 | 仓库建立在上游 `43afeff` 历史上 | 计划 §9 Phase 0 指定的基线 | 全部 | ✅ 已做 |
| 0.2 | Phase 0 | `pyproject.toml`：`name` → `comfyui-mie-h3-engine`，`version` → `0.7.3+mie.1`，`DisplayName` → `Mie H3 Context Loop`，`PublisherId` → `mie`，URLs 指向本仓 | 让分叉身份可辨识；`version` 带 `+mie.1` 后缀便于区分上游版本 | `pyproject.toml` | ✅ 已做 |
| 0.3 | Phase 0 | `pyproject.toml` 的 `authors` **追加** `MieMieeeee`，**未删除**上游两位作者 | GPL 要求保留作者署名 | `pyproject.toml` | ✅ 已做 |
| 0.4 | Phase 0 | `README.md` 顶部**前置**分叉横幅；上游正文逐字保留 | 让人一眼看出这是分叉、基线是哪个、改了什么 | `README.md` | ✅ 已做 |
| 0.5 | Phase 0 | `PLAN.md`（1,286 行）入库 | 计划的唯一真源 | `PLAN.md` | ✅ 已做 |
| 0.6 | Phase 0 | `.gitignore` 在上游条目后追加我们的 Python / 测试 / 临时产物条目 | 上游只忽略 4 条 | `.gitignore` | ✅ 已做 |
| 0.7 | Phase 0 | `LICENSE`、`THIRD_PARTY_NOTICES.md`、`docs/FEATURE_TRACEABILITY.md` **逐字未改** | GPL 要求；计划 §9 明确列出 | — | ✅ 未改 |
| 0.8 | Phase 0 | `git remote add upstream` | D6 的 merge 同步需要它 | `.git/config` | ✅ 已做 |
| **1.1** | **Phase 1** | **P0-a：关键路径的 `checkpoint_verification` 切 `strict`** | 上游 tooltip 明说 cached 模式 *"trusts matching size and modification time"*——信任一个从未被强制的不变量；strict 永不走缓存。**当天能做的生产修复** | `chain_nodes.py`（widget 默认值 / 调用点） | ⬜ 未做 |
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
