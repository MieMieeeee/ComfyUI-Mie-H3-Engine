# 工作台整改 批次 C 实施记录 · 2026-11-11

对应计划：docs/plans/2026-11-11-workbench-usability-remediation-plan.md（§5 批次 C）。
基线：101e82e（批次 B）。

## 交付内容（F06、F07、U02）

### C1/F06 同名资产保存前明确处理
- 服务端创建语义：`save_character/save_scene` 新增 `on_exists="create"` —— 同名
  **原子拒绝**（目录存在检查在写入前、同一调用内），抛 `AssetExistsError`
  （携带现有资产描述/图）；路由转 **409 + code=exists + existing 摘要**。
  缺省 `upsert` 不变（既有编辑流程兼容）。
- 前端（intake 另存表单）：按 create 提交；409 时暂停并摆出现有资产
  （图 + 描述）——「我改名字」回表单改名、「覆盖它」显式二次提交才覆盖；
  覆盖前原资产纹丝不动（测试断言）。
- 连续点击由既有 busy 保护；保存失败表单与已选文件保留（B2 行为）。

### C2/F07 参考目标保存与读回 + 区域裁剪
- 修复双 `append("description")` bug（服务端读首个值，目标被丢）——现在
  **组装唯一 description**：描述 + 换行 +「（参考目标：…）」。
- **读回确认**：保存成功后 GET 详情，回执明确"参考目标已随描述一起入库
  （读回核对过）：…"；读不到则警示去资产页检查。
- **区域裁剪**（多人物图）：canvas 拖拽选框（明暗对照预览选区）→ 按原图
  坐标裁出 JPEG → 表单预览"将保存这个区域"+撤销（用全图）；取消裁剪保留
  起草原图。测试断言保存图片的宽高比与选框一致、原图仍在起草卡。

### C3/U02 场景参考接入制作
- `workflow.build(+scene_asset)`：场景经 `LoadAssetV2|Mie`（kind=scene）接入
  计划节点的 **scene_1** 槽（引擎本就支持——MieH3ScriptToPlan 的 optional
  slot）；不传时图与旧请求完全一致。
- `runner.submit(+scene_asset)`：排队前接线；**续跑沿用原任务的场景选择**；
  任务记录落 `scene_asset`。`plan_preview(+scene_asset)` 同步支持，
  `/api/scripts/<name>/plan` 与 `/api/render/submit` 路由透传。
- 制作页新增「场景参考（可选）」区：场景资产下拉 + 缩略图 + 描述 + 环境音 +
  覆盖关系说明（"画面与场景描述以它为准"）；选择即刷新计划预览。
- 起草另存场景后的回执提供"在资产页查看"深链与制作页指引。

### 顺手修复（批次 C 测试抓到的回归）
- B1 引入的播放器状态字段 `preview` **覆盖了 videoPage 既有 `preview()` 方法**
  （计划预览被静默打断）——统一改名 `audioPreviewState`（app.js + 两模板）。
  这是"每批补能捕获旧行为的回归"的直接收益。

## 测试

- 服务层 +5（角色/场景同名 409 且原资产不动、显式覆盖可用、场景节点接线
  scene_1 与不传无痕、plan preview 透传）。
- 浏览器 +4（同名决策改名入库原资产不动、目标读回核对、裁剪区域即保存
  内容、**起草图 → 另存场景 → 制作选择 → 计划请求带 scene_asset 的闭环**）。
- 批次 B 相关 4 项随改名回归通过。

## 验收对应

| 验收旅程 | 证据 |
| --- | --- |
| 参考资产（重名不静默覆盖、目标与裁剪读回） | asks_before_touching / read_back / cropped_region |
| 场景使用（另存场景 → 制作选择 → 实际计划） | chosen_for_the_plan（plan 请求体断言） |
