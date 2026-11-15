# 交接说明：整改后的验证与补充测试 · 2026-11-15

> 给本地 agent 的任务书。基线提交 `1810737`（功能链止于 `fc03bd1`）。
> 交付记录：notes/remediation-final-2026-11-15.md；各批细节 notes/remediation-batch-{a..e}-*.md。

## 0. 环境事实（先核对，别假设）

| 项 | 值 |
| --- | --- |
| 仓库 | `F:\ComfyUI-Mie-H3-Engine`，分支 main |
| 测试命令 | `python -m pytest webapp_tests -q`（全量约 6 分钟，含浏览器套件；Playwright 已装） |
| 测试端口 | **8299**（fixture 固定）。**绝不可用 8199**——那是用户在用的长驻工作台 |
| 8199 | 用户实例，embedded python（`C:\PP\V9\...\python_embeded\python.exe webapp\__main__.py`），cwd=仓库根。健康检查 `/api/health` 应返回 `version` 与 `git rev-parse --short HEAD` 一致（当前 fc03bd1） |
| 真实资产库 | `C:\PP\...\custom_nodes\comfyui_mienodes\asset_library`（22 个剧本、真实角色/场景、真实 LLM keys）。**测试一律走 conftest 的隔离临时库，别绕过** |
| 版本机制 | 进程启动时取 git hash；静态资源带 `?v=`；页脚显示同值——三者不一致=旧进程（F01 复发） |

## 1. 必做验证（按序）

### V1 · 全量回归复跑
```
cd /f/ComfyUI-Mie-H3-Engine && python -m pytest webapp_tests -q
```
预期 **272 passed**。任何失败：先最小复现（`pytest <file>::<test> -q --tb=long`），再判断是顺序污染还是真回归，写入报告，不要直接改产品代码。

### V2 · 真实端到端（本计划的明确遗留）
前置：8199 已配好大模型平台；ComfyUI 在线（`/api/comfy_status`）。会写用户真实库/产生真实任务——这是预期行为，**不要删除任何产物**，全部记录路径。

在 8199（http://127.0.0.1:8199）按序走，每步截图+记录耗时与模型名：

1. **纯文字起草**（concept）：一句点子 → 生成 → 载入 → 保存。断言：库中出现该剧本、版本 1、execution 为英文。
2. **图文起草**（read）：放一张图 → 解读 → 编辑 observed/proposed → 生成 → 保存。记录平台视觉结论（supported/unsupported+图注）。若平台看不了图：验证 needs_caption → 补图注 → 按"根据你提供的图注"继续。
3. **保留原台词**（keep）：输入 2 角色台词（角色事件行格式）→ 整理（应显示"没有经过模型改写"）→ 生成。**逐事件断言**：打开保存后的剧本 JSON，`shots[].events[].text` 与输入逐字一致（仅首尾空白差异）、数量与顺序相同。
4. **一起设计**（guided）：图片理解 → 整理角色与事件 → 改一条事件、填一句话故事摘要 → 生成。断言：摘要出现在剧本 `constraints`（"故事主线：…"）、改动的事件逐字保留。
5. **参考资产**：起草图 → 裁剪区域 → 另存角色（含参考目标）→ 资产页读回目标文本与图片 → 同图另存场景。
6. **制作**：选剧本 → 接资产（试听样本）→ 选场景参考 → 计划预览含 scene → 提交渲染 → 历史页作品卡出现、成片可播。
7. **再做一版**：历史作品卡 → 用本次配置再做一版 → 新运行目录（与上次不同名）。

判据补充（契约细节见 notes/intake-m0-2026-11-11.md）：script 模式下 `source_script` 即确认稿序列化文本；`validate_v2` 强制 shots 事件与 source_script 逐字段全等——任何台词被改写都是失败。

### V3 · 关键负对照（隔离环境，做完恢复代码）
对三条保护各做一次"临时破坏→测试变红→恢复"：
1. `webapp/services/autodraft.py` storyboard_script：把第一个位置参数改空、传 `source_script=` 关键字 → `test_the_source_text_reaches_the_first_argument...` 必须红。
2. `webapp/static/app.js` intakeLoadCandidate：去掉 isDirty 检查直接载入 → `test_a_late_generation_never_clobbers_edits_made_while_waiting` 必须红。
3. `webapp/services/library.py` `_refuse_existing`：让 create 语义直接放行 → `test_create_semantics_refuses_a_same_named_character` 必须红。

### V4 · 补充测试（当前覆盖薄的地方，建议新增）
- **兼容**：无 `scene_asset` 字段的旧任务记录在 `grouped=1` 视图的显示/续跑/再做一版；旧版 localStorage 草稿（schema_version 缺失）被安全忽略。
- **恢复**：7 天过期草稿被清理；IndexedDB 图片丢失时文字恢复+明示图片缺失；`storyboard-intake:v1` 与 `mie-video-session:v1` 并存互不干扰。
- **压力**：往隔离库塞 ~200 条任务记录（复用 jobs.json fixture）后历史分组分页与轮询耗时（应明显快于无缓存——对比 `runner._jobs_grouped` 前 mono 计时即可）；200 条事件的事件稿预检耗时；多样本角色（3+）样本顺序与引擎加载序一致。
- **键盘走查**：纯键盘完成 起草→事件编辑（含排序/删除）→生成；资产页样本播放（space/进度条）；Esc 关灯箱。
- **浏览器再验证一遍窄屏 320px**（比 390 更狠一档）五页无横向溢出。

## 2. 禁区

- 不占 8199、不杀其进程（除非用户明确要求重启；重启前必须先查 `/api/render/jobs` 的 active）。
- 不写真实库/jobs.json/快照/snapshots（V2 的正常产物除外，且不删）。
- API key、图片 data URL、完整台词不进日志与提交信息。
- 只报告不修产品代码（除非用户另行授权）；发现问题时附最小复现。
- `notes/_old_riskfix.py` 是历史备份，勿提交勿执行。

## 3. 产出

写入 `notes/verify-<date>.md`：每项 V1–V4 的命令、结果、证据（截图存 `notes/verify-shots/`）、失败的最小复现；V2 附模型名、平台、各步耗时、产物路径。结论一句话：哪条链路可信、哪条有问题、问题的最小复现。
