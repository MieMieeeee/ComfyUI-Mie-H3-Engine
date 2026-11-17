# 迁移与验证整改记录 · 2026-11-17

两条线：① MieNodes 的 llm/prompt 内容上移 Core（迁移计划）；② 本地 agent 的验证报告
（notes/verify-2026-11-15.md）暴露的缺陷修复。

## 1. 迁移：MieNodes 的 llm/prompt 内容上移 Core

计划：`C:\Users\administered\PycharmProjects\ComfyUI-MieNodes\docs\PLAN_LLM_PROMPT_MIGRATION_TO_CORE.md`

| 批次 | 内容 | 结果 |
| --- | --- | --- |
| 批 1 | H3 loop user-input enhancer 的文本域 → `mie_craft.loop_enhancer`（依赖 mie_craft 的模式解析，放 mie_prompt 会倒置依赖方向）；节点壳留本仓并显式传 ComfyUI 缓存根 | mie_craft 0.1.7 已发 PyPI |
| 批 2a | qwen_image_21 + ideogram4 两族 → `mie_prompt`（族文本 + 解析/字幕助手 + aspect_ratio 字符串助手）；族目录从本仓删除 | mie_prompt 0.1.7 已发 |
| 批 2b | bernini / flux3_klein / krea2 / ltx25 / scail2 五族 → `mie_prompt` | mie_prompt 0.1.8 已发 |
| 批 2c | 其余十族（flux2、flux_klein、hunyuan、kontext、ltx2、moss_image、prompt_generator、translator、frame_transition、zimage）+ **loader 合一**（本仓 loader 变 shim，只有一个 loader）+ moss 助手与翻译语言表 | mie_prompt 0.1.9 已发 |
| 批 3 | **无需拆分**：多模态接口 `build_multimodal_user_content` / `image_tensor_batch_to_data_urls` 本来就在 `mie_llm.media`（Core 层，比 mie_prompt 更下层，位置更对）；计划的前提不成立，收敛为核实 | — |

结果：`nodes/llm/` 除节点壳（`*_prompt_generator.py`、`custom_system_prompt.py`、
`prompt_generator.py`、`asset_auto_describe.py` 等）与 `__init__.py` 外全部为 shim；
`nodes/llm/prompts/` 只剩 loader shim。**19 个提示词族 + 纯文本逻辑 + 一个 loader
现在都在 Core 里。**

MieNodes 回归：基线 2055 passed / 10 skipped → 迁移后同数全绿。
engine 仓回归：见下。

实施中发现并修的坑（都写进了迁移计划）：
- shim 上 monkeypatch 无效（h3_prompts 迁移时的老教训）：三处测试 fixture 改 patch
  `mie_craft.loop_enhancer`。
- `_CACHE_ROOT_OVERRIDE` 的优先级低于调用方参数 → 陈旧的跨会话缓存会替测试作答；
  改为全局 override 最高优先。
- 加载器同名相对路径在两个包结构下相同，只需去掉 try/except 包装。
- 包数据声明只列了三族 → wheel 不含新族目录；已放宽。
- `aspect_ratio.py` 里混着 ComfyUI 节点类：只上移两个纯函数，节点类留本仓。

## 2. 验证发现的缺陷（notes/verify-2026-11-15.md）

本地 agent 的报告抓到我 5 个真缺陷，全部修复并有回归测试
（webapp_tests/test_verify_regressions.py，9 项）：

| 编号 | 缺陷 | 修法 |
| --- | --- | --- |
| 3.1【高】 | 制作页选的场景参考，**图没进模型**（计划里写着 `<Picture 3>` 是场景，采样器却只有两个角色）——引擎接了场景资产时会删掉文字场景，场景图不到采样器，这一镜连环境都没有 | workflow.py：场景是 picture 编号空间的最后一张，接 `ref_image_{出镜角色数}` |
| 3.2【中】 | 源剧本没写场景时，模型写 `execution.scene` 被契约连拒 3 次，白花 3 次调用 | 页面加「场景说明」字段（两条路径可见），concept 路径以「场景：…」拼进素材，模型据此填 scene，契约一次就过 |
| 3.3【中】 | 任务记录不落 `scene_asset` → 重启后"继续/再做一版"丢掉作者挑的场景 | `_save_jobs` 的 slim 字段表补上 |
| 3.4【中】 | 带冒号的散文被当成角色事件行，整段被吃成一句台词 | 服务端与页面同一判据：名字 ≤12 字、正文 ≤30 字、正文含逗号且较长即散文 |
| 3.5【低】 | 模型 source 引文没覆盖的标点被算成"未认领片段"，主按钮一直点不动 | `_mapped_pieces` 过滤纯标点的残留 |

另外两条：
- **历史页 200 条要 2 秒**：真凶是 `queue_ids()` 的一次连接超时（ComfyUI 离线时
  每次轮询都等）。改为短超时（1.5 秒）+ 2 秒缓存；另加 `comfy.group_artifacts_by_run`
  一次遍历分桶（原来每个 run 都遍历整棵输出树）。
- **提交失败不留痕**：ComfyUI 不可达时"渲染"点了没反应、列表里什么都没有。
  现在失败也落一条 status=error 的记录，带原因。

顺带修掉我自己在收紧启发式时引入的 bug：页面预选的正则组索引写错（m[5] 应为 m[6]），
导致预选永远不生效。

## 3. 测试

- engine 仓：295 项全绿（含 agent 的 15 项补充测试 + 我的 9 项回归）。
- MieNodes 仓：2055 passed / 10 skipped，与迁移前同数。
- 三包双端安装：mie_prompt 0.1.9 / mie_craft 0.1.7 / mie_llm 0.1.5。

## 4. 遗留

- ComfyUI 当时未运行，真实渲染的复验留给下一次（场景图现在确实到采样器了）。
- agent 的 `test_verify_gaps.py` 已随缺陷修复解除 xfail 标记（现在是真测试）。
