# 依赖改动范围 · 2026-11-18

本任务执行的安装仅涉及 comfyui-mie-llm、comfyui-mie-prompt、comfyui-mie-craft 0.2.0。

- 系统 Python 与 embedded Python 均使用 pip install --force-reinstall --no-deps，输入为已从生产 PyPI 下载并核对 SHA256 的三个 wheel。
- 测试候选环境使用 pip install --no-deps --no-index --target 指定隔离目录，同样只装三个包。
- 未安装、升级或卸载 torch、numpy、nvvfx/nvidia-vfx、xformers、comfy-kitchen、build、twine、pytest 或任何其他依赖。
- 构建调用已有 build，使用 --no-isolation；上传调用已有 twine，不执行依赖安装。
- install-system.log 与 install-embedded.log 的 Successfully installed/uninstalled 只列出上述三个包。
- 本次核对的 ComfyUI 启动日志无 Successfully installed、Collecting 或 Installing collected packages 记录；Please pip install xformers 属提示文字，不是实际安装。
- 启动日志中的 nvvfx/nvidia-vfx、Effect/QualityLevel、xformers 等问题不在本任务中修复；不据新一次启动暴露错误推断依赖被本任务升级。
- 一次通过 node_repl 启动器创建的进程缺少用户名环境变量，触发 Torch/getpass 错误；随后在子进程环境中补齐 USERNAME/USER/LOGNAME 恢复启动，没有改动任何第三方包。

依据：notes/split-audit-2026-11-18/install-system.log、install-embedded.log、testpypi-upload.log、pypi-upload.log。
