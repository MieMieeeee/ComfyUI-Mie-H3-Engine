# 复查 bb75a23 · 2026-11-10

范围：c591e60..bb75a23。核对上一轮六项修复，并对删除范围、旧数据升级进行额外隔离复现。未修改产品源码，未删除真实文件，未提交真实渲染。

结果：138 项非真实引擎 E2E 测试通过；仍有 3 项 P1。上一轮的状态写回、按原快照续跑、服务端先筛选后分页、执行镜像绑定源版本，已见对应实现与回归覆盖。

## [P1] disk 任务名可让删除越过 output 目录
位置：webapp/services/runner.py:559；webapp/services/comfy.py:431。
delete_record 对任意 disk: 后缀直接当作 run_name，未确认它来自实际磁盘任务清单；delete_run_files 又直接拼 output/sub/name 后递归删除，没有校验解析后的路径仍在运行目录范围。
在 Windows Flask test_client 上，向 disk:..%5C..%5Cvictim 的 delete 入口发送 files=true，HTTP 200，删除了 COMFYUI_ROOT/victim，而该目录位于 output 之外。
复现只操作 tempfile 创建的目录；调用前检查解析目标等于临时 victim 且仍位于本次 tempfile 根内。没有触碰真实目录。
建议：disk 记录必须从可信清单解析，运行名拒绝分隔符/绝对路径/父目录；删除函数自身也必须对 resolve 后路径做严格包含校验，并拒绝通过链接逃逸。测试同时覆盖 Windows 反斜杠、绝对路径、父目录和伪造 disk 标识。

## [P1] 点号运行名仍会误删其他任务的平铺产物
位置：webapp/services/comfy.py:286。
_belongs_to_run 用 name.startswith(run_name + ".") 识别无日期文件。运行名允许点号，所以 take.v2_2026-11-10.mp4 也被归给 take。新删除逻辑虽然按清单删，但这份清单仍会包含其他任务。
隔离复现：平铺目录放 take_2026-11-10.mp4 与 take.v2_2026-11-10.mp4；collect_artifacts("take") 后按清单删除，后者也消失。
建议：先解析并移除明确的日期后缀与媒体扩展名，再精确比较运行名；无日期文件同样精确比较完整 stem。技术文件优先使用 manifest 记录的路径，避免再以模糊名称判断归属。

## [P1] 升级前已有任务未迁移版本计数，移出旧任务后仍会回退
位置：webapp/services/runner.py:44、101、558；webapp/services/history.py:claim_attempt。
新增独立计数器只在新提交时 claim。旧版本写下的 jobs.json 有 attempt，但 history.json 尚无 attempts；加载旧任务没有把已有最高版本迁入计数器。移出其中最高版本，内存与 jobs.json 都失去这条记录，next_attempt 仍会回退。
隔离复现模拟旧数据：任务表含 v1/v2、独立计数器为空；移出 v2 前 next=3，移出后 next=2，持久计数仍为0。已有 v2 的目录与 dismissed 标记会再次被复用。
建议：启动迁移时先把每份旧剧本最高 attempt 写入独立计数器；删除/截断旧记录之前也保住上界。加从旧 jobs.json 加载、移出最高版本、重启、再生成的完整升级回归。

## 验证
- webapp_tests 全部运行，排除会驱动真实 ComfyUI 的 test_e2e_webui.py：138 passed in 86.78s。
- 日志：notes/bb75a23-tests.log。
- 隔离复现脚本：notes/probe-bb75a23-review.py。
- 输出：dot_named_other_run_deleted=true；legacy before=3/after=2/persisted=0；delete_escape HTTP200/outside_output_removed=true。
- 工作区产品源码保持未修改；仅新增 notes 审查材料。
