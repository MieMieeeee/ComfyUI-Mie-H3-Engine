# MieNodes 拆分实施审计 · 2026-11-18

> 本文保留首轮审计时的状态。后续已执行剩余编排迁移并发布/部署三个 0.2.0 wheel；当前收尾结果见 [完成记录](split-completion-2026-11-18.md)。

## 结论

拆分已实质落地，但原始 D11「生成器本体全族上移」尚未全部完成。11 月 17 日的文本域迁移已完成，不等于整个生成编排已迁出。

| 仓库 | 源码声明版本 / HEAD | 实施情况 |
| --- | --- | --- |
| ComfyUI-MieNodes | 1.5.2 / 2a93301 | LLM 客户端、资产库、剧本契约、组装器和提示词通过兼容入口调用共享包；ComfyUI 节点、tensor 适配与部分生成编排仍在本仓 |
| ComfyUI-Mie-H3-Engine | 0.7.3+mie.1 / 1be2f39 | 独立 fork、MieH3 节点命名、共享包调用、预览 provider 与工作台均已有代码 |
| ComfyUI-Mie-LLM | 0.1.5 / 5e22339 | client、budget、media、utils、platforms 已独立 |
| ComfyUI-Mie-Prompt | 0.1.10 / 1ac08ff | 21 个提示词族目录、模板解析、翻译语言表、宽高比助手与唯一 loader 已独立 |
| ComfyUI-Mie-Craft | 0.1.7 / 0dfdd4a | 资产/剧本读写、契约、critic 两半、生成器、零 LLM 组装、对白和 loop enhancer 文本域已独立；HEAD 晚于 0.1.7 发布提交 |

## 计划与实现逐项对照

- 总计划来源：本仓 PLAN.md；MieNodes docs/PROJECT_H3_ENGINE_AND_DRAMA_PLATFORM.md 是历史副本，存在旧名称、旧复选框。
- D5/D12 三个独立纯 pip 包：已实现，依赖方向为 LLM ← Prompt ← Craft。不存在需要另装的第四个 ComfyUI-Mie-Core 包。
- D13/D14：services/llm.py 已变为连接器节点壳，纯客户端在 mie_llm.client；该服务文件不再导入 numpy/cv2/torch。
- D9/D23/D26：资产库与剧本接口经 shim 指向 mie_craft；旧资产节点保留，写入口归共享库；asset_auto_describe 经 library shim 使用 Core。
- Phase 3 provider：web/mieh3_plan_preview.js 已覆盖 MieH3ScriptToPlan；PLAN 旧空勾选已补正。
- 11-17 批 1：loop user-input enhancer 文本域进入 Craft，符合依赖方向；translator 等保留节点壳。
- 11-17 批 2a/2b/2c：族文本已搬走，nodes/llm/prompts 仅有 __init__.py 与 loader.py 两个兼容入口。当前 Prompt 有 21 个族目录，旧记录的 19 不是当前目录总数。
- 批 3：mie_llm.media 已有 dataURL 多模态接口，无需重复造一层。
- D11 原始全量生成器目标：仍未全部完成。nodes/llm/minimax_h3_loop_prompt_generator.py 有 5513 行，H3LoopPromptEnhancer 包含 _auto_storyboard、_critique_shots、_generate_shot_prompt、_generate_all_shots_single_call 等编排；不宜把整个文件称为纯节点薄壳。
- 产品层已独立实现，实际为 webapp/flask_app.py 的 Flask，偏离原计划 FastAPI 选型；独立进程与复用 Core 的边界仍成立。未为追平旧文档重写技术栈。

## 本次已修复

1. Prompt 七个模块与 Craft 的 h3_loop_prompts 清除所有 _mienodes_internal/core.utils 宿主导入，统一从包内或 mie_llm 导入；删除重复的同内容 try/except。
2. 新增 Prompt/Craft 两组包边界测试：扫描所有 AST 导入，拒绝宿主及反向依赖，验证全包模块可独立导入；Craft CI 已接入 unittest。
3. MieNodes pyproject 与 requirements 同步下限：LLM >=0.1.5、Prompt >=0.1.10、Craft >=0.1.7，避免仍满足旧下限却缺少 h3_image/loop_enhancer。
4. Engine 两份依赖清单对齐为 LLM >=0.1.5、Prompt >=0.1.9、Craft >=0.1.7。该下限不宣称包含 Craft 尚未发布的 HEAD 写协议增强。
5. 删除 LLM pyproject 中误留的 mie_prompt package-data 声明；修正文档「三个包同版本发布」说法。
6. 两仓 WHERE_TO_CHANGE 修正普通安装与 editable 安装区别；计划补充当前实施状态，并明确原始 D11 未全量完成。

## 安装与部署的实际状态（只读元数据和文件检查）

| 环境 | LLM | Prompt | Craft |
| --- | --- | --- | --- |
| 系统 Python 3.12 | 0.1.5，普通安装 | 元数据 0.1.9，editable → F:/ComfyUI-Mie-Prompt，源码 __version__ 0.1.10 | 0.1.7，editable → F:/ComfyUI-Mie-Craft |
| ComfyUI embedded Python | 0.1.5，普通安装 | 0.1.10，editable → F:/ComfyUI-Mie-Prompt | 0.1.7，editable → F:/ComfyUI-Mie-Craft |

- custom_nodes/ComfyUI-Mie-H3-Engine 是指向 F:/ComfyUI-Mie-H3-Engine 的 junction。
- custom_nodes/comfyui_mienodes 是独立目录，不是开发仓 junction；所抽查 services/llm.py、h3_image_prompt_generator.py、prompts/loader.py 与开发仓一致，未宣称全目录完全同步。
- 普通安装的 LLM 不会因 F: 开发仓改动而自动更新。Prompt/Craft 新进程会读到 editable 源码；本次没有重载运行中进程。

## 发布与验收尚未闭环的部分

- Craft 0dfdd4a 的内容寻址写协议和 shot_list_errors 晚于 release 0.1.7（5de9e2b）；本地 dist/0.1.7 wheel 确实没有该函数，library.py 也不同。不能把 editable 可用等同于正式 wheel 已发布。
- 本次导入边界修复位于当前源码工作树，尚未发布新 Prompt/Craft wheel；现有 dist wheel 未包含修复。
- 系统 Prompt 的安装元数据滞后，待空闲维护窗口再重新安装；本次不修改 shared Python 环境。
- 局域网环境未访问、未验证，历史双机部署条款不据本次本机检查勾选。
- 原始 D11 剩余编排提取属于后续架构迁移，需要先明确中断、进度、缓存、媒体适配接口，再移动实现；本次未冒进搬动这一整段执行路径。
- 真实多镜头渲染、场景图送入采样器等端到端验收未执行；11-17 记录也明确留待真实渲染复验。

## 本次验证

- 修复前：Prompt/Craft 新增 AST 边界检查各失败一次，确认能抓到原缺陷；修复后各 2 项通过。
- Prompt 全部 tests：58 passed。
- MieNodes 六组受影响测试：262 passed，3 条既有 DeprecationWarning；覆盖 H3-Image、Krea2、SCAIL2、Flux3 Klein、storyboard prompts、H3 loop prompts。
- LLM / Prompt / Craft selftest：全部通过。
- 未重新运行历史全量 MieNodes 2055 项、Engine 295 项；这些数量仅来自迁移记录，不冒充本次结果。
- 回归过程使用独立 Python 进程、ComfyUI mocks 与网络连接拦截；Windows asyncio 内部 socketpair 除外。一次临时 runner 被 TEMP/click.py 遮蔽，误触发了该脚本对 8199 的只读页面访问并连接拒绝；已排除 TEMP 模块搜索路径后成功完成离线回归。未对 ComfyUI 发起 HTTP 请求或提交任务。
- 未执行 ComfyUI 启停、重启、reload、pip 安装、部署同步、队列/模型操作。未提交 Git、推送或发布。

现有用户未跟踪文件原样保留。本次修改仅在开发仓源码、依赖声明、测试与文档中。
