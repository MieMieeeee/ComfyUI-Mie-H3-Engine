# 工作台整改 最终交付记录 · 2026-11-15

对应计划：docs/plans/2026-11-11-workbench-usability-remediation-plan.md（F01–F08、U01–U08）。
五批全部完成，实际工作台已运行验收版本。

## 提交与测试

| 批次 | 提交 | 内容 | 全量测试 |
| --- | --- | --- | --- |
| A 起草可靠性 | df272aa | F01 版本对齐、F02 角色下拉、F03 图片入引导、F04 恢复预检、F05 返回保留、U05 参数 | 245 passed |
| B 试听与准备 | 101e82e | U01 三处试听、U03 媒体保护、F08 制作预检 | 252 passed |
| C 参考资产 | eb0e38b | F06 同名决策、F07 目标读回+裁剪、U02 场景接计划 | 261 passed |
| D 编辑与制作 | 602e01a | U04 保存并制作+会话恢复+再做一版、U05 摘要、U07 模型状态 | 272 passed（浏览器 51） |
| E 历史与窄屏 | fc03bd1 | U06 作品分组、U08 窄屏直达 | **272 passed**（基线 236） |

各批实施细节见 notes/remediation-batch-{a..e}-2026-11-11.md。

## 实际部署验收（8199）

- 重启前核对在途任务（两轮均 active=0）；重启后健康检查 `version=fc03bd1`，
  页面静态资源版本戳全量匹配，启动自检通过。
- 真址复查（零模型调用、不写真实库）：
  - 起草卡打开即显示当前模型状态；双角色台词确定性解析，下拉显示与提交一致，
    事件稿进入预检流程；库 22 个剧本未动。
  - 资产页：真实角色（Claude娘，多样本）详情显示声音样本列表。
  - 制作页：就绪标签与场景参考选择在位。
  - 历史页：**共 21 部作品 · 12 次尝试**（按作品组织，真实数据）。

## §8 验收旅程对应

| 旅程 | 状态 | 证据 |
| --- | --- | --- |
| 实际实例升级 | ✅ | 健康版本=页面=资源=fc03bd1 |
| 双角色台词 | ✅ | 浏览器 own_role（显示/值/请求三方）+ 真址复查 |
| 纯图片引导 | ✅ | 服务端 picture_only 整理（A3） |
| 返回和刷新 | ✅ | keeps_the_edited + auto_prechecks |
| 音频选择 | ✅ | play_and_say / previews_locally / readiness_blocks |
| 媒体改动 | ✅ | counts_as_unsaved |
| 制作准备 | ✅ | readiness_blocks + 服务端排队前拒绝 |
| 参考资产 | ✅ | asks_before_touching / read_back / cropped_region |
| 场景使用 | ✅ | chosen_for_the_plan（计划请求带 scene_asset） |
| 编辑到制作 | ✅ | save_and_make |
| 跨页面继续 | ✅ | comes_back_after |
| 历史再制作 | ✅ | remake_with + works 视图 |
| 参数与模型 | ✅ | shot_count_shown / story_summary_travels / names_the_model |
| 窄屏与键盘 | ✅ | comes_to_you + stay_within（五页 390px） |

## 遗留与说明

- §8 末两条（合成媒体隔离验证、真实已配置模型的端到端）中，合成媒体部分已由
  浏览器/服务层测试覆盖（临时库+合成 PNG/WAV）；**真实模型 + ComfyUI 的
  端到端验收**（真实读图质量、参考音与最终配音一致性、场景引用后的真实渲染）
  需在用户环境实际跑一次渲染，本轮未产生渲染任务（未主动消耗配额）。
- 模型合成试听按计划列为后续扩展；多人物裁剪为矩形区域（非轮廓级）。
