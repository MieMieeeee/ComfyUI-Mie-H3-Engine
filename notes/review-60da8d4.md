# 复核 60da8d4 · 2026-11-10

本轮仅复核提交，不实现第二批四项，不修改产品源码。

## 原三项修复
- 伪造 disk 标识必须来自实际清单；删除函数增加合法名称与 resolve 后的输出树包含校验。
- 平铺产物以完整 stem 或去日期后缀后的完整 stem 比较，点号名称不再按前缀归入其他运行。
- _load_jobs 后调用 _migrate_attempts；升级测试通过 importlib.reload(runner) 覆盖启动调用，移出旧最高版本后序号不回退。

## [P1] output 内部 junction 会使删除落到另一个作品
位置：webapp/services/comfy.py:438、442。
当前做法是把 output/sub/run_name resolve 成最终目标，只判断仍在整个 output 内，然后对目标执行 shutil.rmtree。若运行 take 是指向另一个运行 other_run 的 Windows junction，resolve 后得到 other_run；它仍在 output 内，检查通过，随后删除的是 other_run 的真实目录。
隔离实测：temp/output/h3_chains/take 是指向 temp/output/h3_chains/other_run 的 junction，other_run 内仅放一个 dummy 文件。调用 delete_run_files("take") 后，other_run_survives=false，返回声称删除 h3_chains/take。
执行前明确验证解析目标等于预设 other_run，且仍位于本次临时根目录内；未触碰任何真实作品目录。
同样的问题适用于文件链接：resolve 后 unlink 会删除目标文件，而不是链接自身。该文件变体为源码推断，未单独执行。
影响：输出树外越界已阻止，但输出树内跨作品删除仍未阻止；如果链接指向 output 本身，当前 _inside 也允许路径等于根目录。后一变体为源码推断，未执行。
建议：递归删除前识别并拒绝运行目录的 junction/symlink/reparse point，或只移除链接本身而保留目标；不要对 resolve 后目标直接递归删除。保留输出树边界检查，同时验证删除对象确实是该运行拥有的目录/文件。
回归：take junction→other_run，删除 take 后 other_run 的文件完整；另测链接指向 output 根与 output 外部。测试每种情形都应使用临时目录。

## 验证记录
- 非真实 ComfyUI E2E 的 webapp_tests：141 passed in 87.50s。
- 未运行 test_e2e_webui.py（3 项），因此本轮不声称复跑了用户报告的全部 144 项。
- 日志：notes/60da8d4-tests.log；独立 junction 复现：notes/probe-60da8d4-junction.py。
- 临时根：C:\Users\ADMINI~1\AppData\Local\Temp/mie60-review-4CdSLL
- 产品源码 git 状态保持未修改。
