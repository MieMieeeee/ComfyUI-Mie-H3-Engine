# 拆分收尾完成记录 · 2026-11-18

本次范围由用户明确为本机；局域网不在验收范围内。本文接续首轮审计，记录随后实际执行的修复、迁移、发布与验证。

## 代码归属已闭环

- mie_craft.loop_generator 拥有完整 H3LoopPromptEnhancer：提取对白、分镜、prefix、caption、单镜/整板调用、critic、计划组装。MieNodes 的同名文件由 5513 行缩减为约 826 行宿主适配与节点定义。
- LoopRuntime 按实例注入中断检查、缓存根、图像编码器和中断异常类型；默认宿主不导入 ComfyUI，并支持 dataURL 列表参考图。
- Prompt 拥有 11 个族 enhancer/相关纯实现、Ideogram CaptionVerifier/formatter 和十个通用生成方法；H3 storyboard 编排在 Craft。正式 wheel 中的 21 个族、99 个数据文件均已逐字节核验与源文件相同。
- 节点控件、类型名、插座、tensor 输入适配、宿主文件路径及自定义 preset 存取仍留 MieNodes；冻结资产节点的固定库路径行为保留。
- 原始 D11 的共享生成编排迁移已完成。之前 11-17 的文本域迁移结论没有被误当作整个迁移完成。
- 系统/embedded 正式安装后补修 services/llm.py：23 个构造调用在节点壳解析配置文件绝对路径，避免客户端相对 site-packages 查找密钥文件。新增两条回归，未读取或记录密钥内容。

## 发布与部署

三个包统一发布 0.2.0，均先上传 Test PyPI，再上传生产 PyPI。通过两个站点的 JSON 元数据下载 wheel 并检查 SHA256，下载内容与已经验证的候选 wheel 完全一致。

| 包 | 源码提交 | SHA256 |
| --- | --- | --- |
| comfyui-mie-llm | 502d621 | e8f8822011c72dcb816cb803c611bb73a12b6071dbc5fe71f0aff1eaa1badf21 |
| comfyui-mie-prompt | f71a472 | f9efca4b9527ce28a4fea11fa2f33d85acdae7f0e05ec63fac8caf88fcf22fec |
| comfyui-mie-craft | e3f8eda | 36b65c35d2c92c33bb0b95f3020240f27c5de2bc95876baa9b7e97eb5bbb515d |

- 三仓源码及 v0.2.0 标签已推送 origin/main，各自 GitHub CI 已成功。MieNodes 消费者提交 8812fa8 已推送，CI 已成功。引擎消费者与跨平台补修最终提交 ef60ee3 已推送；最终 [CI 44224962673](https://github.com/MieMieeeee/ComfyUI-Mie-H3-Engine/actions/runs/44224962673) 的 Ubuntu/Windows 两个 job 均 success。MieNodes 后续仅文档提交 7c69953 修正了旧总计划的真源声明。
- 系统 Python 与 ComfyUI embedded Python 都安装生产下载的三个 wheel，版本均为 0.2.0，均为普通安装，已经移除 editable。
- 每次安装使用 --no-deps，构建使用已有 build --no-isolation；除了三个子仓库，没有安装、升级或卸载任何依赖。详见 dependency-change-audit-2026-11-18.md。
- 队列检查为空后，定向备份部署 17 个节点/依赖文件，另部署连接器路径修复；没有同步用户未跟踪的 nodes/web。回滚副本和部署 manifest 留在本机 notes/split-release-0.2.0，不进入 Git。
- 在空闲窗口受控重启，最终本机 8188 健康；引擎 86 个后缀节点与 MieNodes 关键节点已注册。MieH3ScriptToPlan 本来就是不带后缀的注册名。
- 首次 node_repl 子进程启动缺少 USERNAME，Torch/getpass 失败；补齐仅子进程环境后已恢复。没有修改 torch/nvvfx 等依赖；用户指出的其他插件依赖报错不在本任务修复。

## 验证

| 检查 | 结果 |
| --- | --- |
| 独立 wheel 下 MieNodes 全量 | 2076 passed，10 skipped |
| 正式双端安装后及配置路径补修的 MieNodes 全量 | 2078 passed，10 skipped（3 条既有 DeprecationWarning） |
| Prompt 全量与包边界 | 59 passed |
| Craft 运行时隔离、dataURL 与包边界 | 5 passed |
| LLM/Prompt/Craft selftest | 全部通过；系统与 embedded 均在阻断 ComfyUI/Torch/NumPy/OpenCV/Pillow 下独立导入全部 48 个共享模块 |
| 最终 wheel 工作台服务测试 | 初轮 224 passed；跨平台问题补修及新增两条成片回归后 226 passed |
| 工作台完整浏览器/服务回归 | 初轮本机 295 passed、1 skipped；最终双平台 CI 均 297 passed、1 skipped；该真实后端项另已补验通过 |

## 本机真实执行

1. 三镜头渲染 prompt_id：2a3a1a12-e07f-470b-a7d0-403b8af1016b。状态 success/completed，耗时 49.54 秒。
   - 私有剧本资产经 MieH3LoadScriptAsset → MieH3ScriptToPlan → ChainPlanModern → 真实采样 → ChainAssemble。
   - 角色与场景引用现有资产，只读；生成器的三镜包含中文对白。采样器实际接入角色图、场景图与角色声音。
   - 三个 scene 的 checkpoint、三段 segment、最终 MP4/生成 WAV 均落盘。
   - 技术冒烟参数：320×320、每镜 2 步；不把该低步数成片当作品质验收。
   - ffprobe：H.264、320×320、24fps、107 帧，AAC 双声道 32kHz，总时长约 4.459 秒。
   - 成片：C:/PP/V9/V9-Large/ComfyUI_Mie_2026_V9.0_Large/ComfyUI/output/split_audit_20261118/split_audit_20261118_2026-11-18.mp4。
   - 首次验收图误以为冻结 LoadAssetV2 接受自定义库路径，在读资产前失败；调整验收绑定为只读现有资产后成功，没有修改冻结契约或用户资产。
2. 真实 LLM 编排 prompt_id：00c64d1e-ce63-4c1a-9594-c9b426745140。MiniMax-M3，single_call，success/completed，约 199.15 秒；真实经过 dialogue_extract、storyboard、prefix、single 四阶段生成三镜头计划。
3. 多模态节点 prompt_id：5adf9e99-f658-4f39-bcc5-e540ccf71a32。H3ImagePromptGenerator|Mie 的 edit 模式将现有角色图送入 MiniMax-M3，success/completed，约 260.71 秒；返回 1456 字符有效增强正文，包含 <Picture 1> 引用。4096/8192 token 预算出现 finish_reason=length 后自动升级至 16384，重试成功。
4. 真实 ComfyUI 后端的工作台 Start/Stop 节点补验：webapp_tests/test_e2e_webui.py::test_the_nodes_work_through_comfyui，1 passed；只启停专用测试端口 8298。完整回归中原先因隔离后端而跳过的这一项已在真实后端补齐。

完整 history、输入图、ffprobe 与日志均保存在本机 notes/split-release-0.2.0/live-acceptance；不向 Git 推送个人参考媒体或运行环境副本。

## CI 补充修复

远端 Linux 首轮发现 4 个链接边界测试仅支持 Windows cmd，以及成片选择依赖遍历顺序、起草卡片滚动早于 DOM/聚焦的两项浏览器问题。已保留真实断言并修复：POSIX 使用目录 symlink；成片只选择 deliverable/final 角色，拒绝 segment；Alpine nextTick 后先聚焦再滚动。增加两条成片选择回归。本机 226 服务、37 边界/选择、2 浏览器针对检查通过，Modern Plan 契约检查通过；另修正目录符号链接的摘除方式：先识别 symlink 并 unlink，Windows junction 保持 rmdir。最终双平台均通过全部工作台测试及既有基线闸门。

引擎上游大套件仍采用仓库原有失败基线，未声称全部零失败：本次 Ubuntu 为 186 脚本通过、65 个既有失败，Windows 为 164 通过、87 个既有失败；两个闸门均报告 PASS: no new failures。日志保存在本机 notes/split-audit-2026-11-18/final-ci-ubuntu.log 与 final-ci-windows.log。

## 验收边界

- 本次不验收局域网：用户已明确选择“本次只验收本机”。
- 本次不修复 nvvfx/nvidia-vfx、xformers 等其他插件报错，且没有变更这些库。
- 原计划 FastAPI 的选型在实现中已演变为 Flask；实际边界仍是独立进程与共享 Core，未为追平旧文档重写框架。
