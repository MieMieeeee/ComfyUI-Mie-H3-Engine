# 界面与设计复查 · 2026-11-10 · cb2f34f

结论：主要界面改进已落地；仍有 4 项 P1、1 项 P2，集中在草稿保护和任务输入生命周期。此次不修改产品源码。

## 已确认的改进
- 制作页参数改为两列，桌面左栏横向溢出消失。
- 390px 下制作页和模型页 documentElement.scrollWidth 均为 375px（扣除滚动条后的布局宽度），没有整页横向溢出；制作页左栏 clientWidth 与 scrollWidth 均为 308px。
- 角色未绑定时显示缺项，开始渲染按钮禁用；前端提交锁已添加。
- 剧本页显示保存状态，并提供带 script 查询参数的“用此剧本制作”链接。
- 当前模型平台置顶、有搜索，模型页可见 input/select 均有可访问标签。
- 磁盘残留片段不再简单称作 running；排队与运行已分开。
- 中文/英文执行镜像加入了变更提示与高级区域。

## 仍需修复

### P1：打开剧本会先删除待恢复草稿
位置：webapp/static/app.js:602、744、746。
open 调 markSaved；markSaved 调 clearDraft；随后 readDraft(name) 已读不到原草稿。
独立 JS 上下文复现：预置草稿 → open → draftStillStored=false，recoveryOffered=false。
建议：打开时先读取草稿；设置已加载基线与清除草稿拆成不同操作，只有明确保存成功/丢弃才清草稿。

### P1：JSON 编辑未计入整体 dirty 状态
位置：webapp/static/app.js:591、659、804。
isDirty 仅比较 model 与 baseline。JSON 源码尚未成功解析时只改 rawText/jsonDirty，所以可能显示已保存，不拦截切换，saveDraft 也提前返回。
独立 JS 上下文复现：rawText 为未完成 JSON，onJsonInput 后 jsonDirty=true、isDirty=false、confirmDiscard=true。
建议：isDirty 包含 jsonDirty；JSON 不能解析时也保存原文，离开页面时保护它。

### P1：快照清理可能删除仍在队列中的输入
位置：webapp/services/snapshot.py:87；webapp/services/runner.py:138。
prune 只按目录 mtime 留 20 份，不查看任务状态。超过保留数量的新提交会删除较早仍在等待或执行的快照。
临时目录复现：用 keep=1 缩小阈值后，标记为 active_queued 的旧快照被删除。生产阈值为 20。
建议：活动任务引用的快照永久排除清理，终态后再回收；快照应使用独立提交 ID。

### P1：同名防重复提交不是原子操作
位置：webapp/services/runner.py:102、106、114。
重复检查在提交锁外；快照又写进同一个 run_name 目录。两个并发请求可以同时通过检查、覆盖同一个快照、先后进入队列。
隔离复现：屏障让两个请求同时通过检查，ComfyUI 调用全部 mock，同名请求成功数为 2，errors=[]。没有向真实 ComfyUI 提交。
建议：检查、预留名称、写独立快照、提交及记账形成一致的临界区，失败撤销预留；使用幂等请求键。

### P2：任务持久化漏掉新增的溯源信息
位置：webapp/services/runner.py:57、129、257。
_save_jobs 的白名单仍只有旧字段，未保存 role_assets、asset_hashes、script_hash、params。重启后资产变更检测缺少名称/哈希，不再提示输入变化。
临时文件复现：asset_hashes_persisted=false、role_assets_persisted=false。
建议：持久化这些字段和快照引用，增加真实保存/重载后的资产变化回归测试。

## 验证范围与限制
- pytest：test_review、test_snapshot、test_jobs、test_workflow、test_ui_browser，39 passed in 35.09s。日志：notes/recheck-tests.log。
- 对新增缺口运行独立 JS 状态复现与 Python 临时目录/mock 并发复现；Python 脚本：notes/recheck-boundaries.py。
- 模板检测器返回 []，不代表草稿与并发行为正确。
- 浏览器常规表单修改显示“未保存”；尝试切换触发交互阻塞，确认弹窗的完整取消/继续流程未完成浏览器验收，不能计为全部通过。
- 浏览器工具在该弹窗后多次无法重新控制原测试页；另开页面完成制作页与模型页的只读验证。测试浏览器中的临时画风没有保存到资产库。
- 未调用付费 LLM、未提交真实渲染、未重启或关闭用户的 8199 工作台。产品源码 git 状态保持干净。
