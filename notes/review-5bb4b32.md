# 复核 5bb4b32 · 2026-11-10

结论：直接删除运行目录的 junction 场景已修复；但真实入口先收清单再删文件，仍会通过父目录 junction 删除另一作品的文件。发现 1 项 P1 集成缺陷。

## [P1] 产物清单删除没有检查路径中的父目录 junction
位置：webapp/services/comfy.py:429、435、439；清单来源 webapp/services/comfy.py:222；入口 webapp/services/runner.py:600。
_is_link(raw) 只判断最终文件自身。raw=output/h3_chains/take/final/film.mp4 时，film.mp4 是普通文件，而 take 是指向 other_run 的 junction；叶子检查返回 false。resolve 后落到 output/h3_chains/other_run/final/film.mp4，仍在 output 内，所以随后 unlink 删除了其他作品的文件。
_walk_output 的 os.walk 未剔除 junction；在实际 embedded Python 上会遍历该目录并收集 take/... 的文件路径。第二阶段只移除 take 链接本身，不能挽回第一阶段已删除的文件。

## 按真实入口的隔离复现
1. 在 tempfile 下创建 output/h3_chains/other_run/final/film.mp4。
2. 创建真实 Windows junction：output/h3_chains/take → other_run。
3. comfy.recent_runs() 的实际清单中确实包含 take（不是伪造标识）。
4. 调用 runner.delete_record("disk:take", delete_files=True)，保留实际的清单收集与删除函数。
结果：entry_was_in_real_disk_list=true、other_film_survives=false、link_removed=true；files_removed 包含 h3_chains/take/final/film.mp4。
执行前已验证 junction.resolve 等于临时 other_run，且删除目标仍在本次临时根目录内。没有访问或删除真实作品。

## 为什么现有新测试没发现
两条新增测试都调用 comfy.delete_run_files("take")，未传入 collect_artifacts 返回的 relpaths；因此只覆盖第二阶段的目录清理，没有覆盖真实入口先逐文件删除的第一阶段。

## 修复建议
- 扫描产物时剔除 junction/symlink/reparse point 目录，避免遍历它们和错误归属。
- 删除文件时独立检查从 output 到叶子文件的整条路径，任何父目录是链接都不得沿它删除目标；扫描结果可能是缓存，不能只依赖扫描阶段。
- 运行目录是链接时只移除该链接，并跳过其所有后代产物的逐文件删除。
- 回归必须走 runner.delete_record 或 Flask 删除接口，并使用实际 collect_artifacts，保留其他运行的 film.mp4。另测运行内部链接到 output 中其他运行的情形。

## 验证材料
- 独立完整入口脚本：notes/probe-5bb4b32-entry.py。
- collect→delete 脚本：notes/probe-5bb4b32-review.py。
- 完整入口临时根：C:\Users\ADMINI~1\AppData\Local\Temp/mie5bb-entry-EOSes1
- 未修改产品源码；未开始第二批功能。

本轮回归：143 passed in 87.28s；排除 test_e2e_webui.py 的 3 项真实 ComfyUI E2E，未声称复跑全部 146 项。日志 notes/5bb4b32-tests.log。
