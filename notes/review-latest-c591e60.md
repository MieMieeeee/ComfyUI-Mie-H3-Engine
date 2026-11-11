# 最新改动审查 · c591e60 · 2026-11-10

范围：bf76d31..c591e60，包含 75e6834、db6ad70、c591e60。审查执行描述、新版本/续跑、产物索引、历史分页/收藏/删除；只读查看本地历史页面。未修改产品源码，未提交真实渲染或付费请求。

结论：发现 5 项 P1、1 项 P2。现有非真实引擎 E2E 测试 131 项通过，边界复现仍显示以下问题。

## 1. [P1] 删除一个版本会删除前缀相同的其他版本文件
位置：webapp/services/comfy.py:413。
delete_run_files 对 live_storyboards 平铺文件用 startswith(name) 匹配；删除 take 会命中 take_v2.mp4。目录匹配虽然精确，平铺匹配仍会越界到其他版本。
临时目录复现：创建 take.mp4 与 take_v2.mp4，执行 delete_run_files("take") 后两者均消失。没有触碰真实目录。
建议：使用本次任务记录/manifest 的精确文件清单；若需要兼容命名规则，匹配完整 run 标识，不能按任意前缀删除。测试应覆盖 take、take_v2、take_v20 同时存在。

## 2. [P1] 移出历史会回退版本编号并让新任务隐藏
位置：webapp/services/runner.py:109、439。
next_attempt 从仍在 _jobs 里的记录取最大 attempt；delete_record 把记录真正移除，同时按 run_name 写 dismissed。删掉最高版本记录但保留文件后，新提交会复用被删版本的目录，且仍被 dismissed 隐藏。
临时状态复现：v1/v2 存在时 next=3；移出 v2 后 next=2；预测新 run_name 与旧 v2 相同，history.is_dismissed(new_name)=true。
建议：版本分配使用独立、持久化且单调递增的序号；列表移除只改变可见性，不删除任务账本；生成前校验输出目录已存在情况。重启/保留数量截断也不能回退编号。

## 3. [P1] 历史页更新状态却不更新内部任务表，导致续跑被拒绝
位置：webapp/services/runner.py:372。
all_records 复制 record 后调用 _apply_status，但没有 _remember。页面可以显示已结束，_jobs 与持久化文件却仍是 queued；_refuse_a_duplicate 继续认为名称正在使用。终态缓存也因此无法正常生效。
隔离复现：初始 queued、mock ComfyUI execution_interrupted 后，列表状态 interrupted，内部状态 queued；同名检查拒绝继续。
建议：刷新状态后统一提交给任务表并持久化；测试从“本系统提交 queued → 历史轮询变终态 → 从这次继续”的完整路径，而非预设终态记录。

## 4. [P1] 应用执行描述后再改中文，保存会自动确认过期镜像
位置：webapp/static/app.js:711、1327。
executionApplied 是本次会话内的布尔值，不绑定生成描述所对应的源版本。应用镜像后继续改中文，保存仍直接调用 confirmExecution，把新源与旧英文当作已确认的一对。
独立 VM 复现：应用 source A 的英文镜像 → 把中文改为 source B → 保存；staleBeforeSave=true、staleAfterSave=false、oldEnglishStillPresent=true、confirmedBySave=true。没有写入真实剧本。
建议：保存镜像草稿对应的 sourceSignature 与 execution hash，自动确认前同时核对；中文或英文后续变化均使已应用标记失效。异步 AI 返回期间修改源文本也要检查版本。

## 5. [P1] 从历史续跑读取当前剧本，而不是原任务的快照
位置：webapp/services/runner.py:127、150；webapp/static/app.js:1844。
continueJob 只传旧 run_name/角色/参数；submit 无论是否 continue_run 都按 script_name 读取当前资产库，再创建新快照。用户改过剧本后点旧任务继续，会把新剧本带进旧运行目录，与原任务固定输入的语义不一致；可能被引擎的一致性检查拒绝，或在允许范围内改变续跑内容。
证据等级：源码确认，未运行真实引擎续跑以判断最终行为。
建议：续跑以原 job_id 为入口，读取原 snapshot_dir 与固定资产/参数；需要修改剧本时显式创建新版本/分支；原快照已回收时提示无法原样续跑。保留原 attempt，不能统一写成第 1 版。

## 6. [P2] 状态过滤发生在分页之后，导致漏项与错误总数
位置：webapp/static/app.js:1751、1757。
接口只收到 page/per_page/favorites，running/done/failed 在前端对当前页过滤。运行任务在后续页时，“进行中”第一页可能为空，total/pages 却显示全部记录；hasActiveJob 也只看过滤后的当前页。
独立 VM 复现：服务端第一页只有 success，total=2/pages=2，运行任务在第二页；filter=running 后请求无状态参数，visible=0、total=2、pages=2。
建议：把状态过滤放到服务端，在计算 total/pages 和切片之前执行；返回全局活动任务数来决定轮询。测试目标任务只出现在未过滤第 2 页的情形。

## 已看到的改进
- 制作历史独立成页，成片/分镜/技术文件层级比原来清楚。
- 播放器按需挂载、共享扫描缓存、移除重复 init，方向合理。
- 镜像差异预览、双指纹确认、用户显式允许旧描述，比之前完整。

## 验证记录
- 执行 webapp_tests，排除会驱动真实 ComfyUI 的 test_e2e_webui.py：131 passed in 79.42s。
- 日志：notes/latest-review-tests.log；Python 隔离复现：notes/probe-latest-review.py。
- 前端边界在独立 VM 中复现，使用 fake API，不改真实资产。
- 本地浏览器只查看制作历史，未点击收藏/删除/续跑等真实写入操作。
