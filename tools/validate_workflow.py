"""Validate an API-format workflow with ComfyUI's own validator.

The /prompt endpoint validates and then QUEUES - a valid workflow would
start rendering, which is not what a wiring check wants. This runs
execution.validate_prompt directly, in ComfyUI's own interpreter, so the
workflow is checked against the real node registry and nothing runs.

Usage:
    python_embeded\\python.exe tools\\validate_workflow.py <workflow.api.json>
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys


def _find_comfy_root() -> pathlib.Path:
    """ComfyUI 的根目录.

    顺序: COMFYUI_ROOT 环境变量 → 从插件位置反推 (<ComfyUI>/custom_nodes/<插件>)
    → 旁边的 ComfyUI/Comfyui. **不钉死某台机器的路径** —— 换台机器就指向一个
    不存在的目录, 而工具会安静地报一个看不懂的错.
    """
    env = os.environ.get("COMFYUI_ROOT")
    if env:
        return pathlib.Path(env)
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    for cand in (plugin_root.parent.parent,      # <ComfyUI>/custom_nodes/<插件> → <ComfyUI>
                 plugin_root.parent / "ComfyUI",
                 plugin_root.parent / "Comfyui"):
        if (cand / "comfy").is_dir() or (cand / "main.py").is_file():
            return cand
    return plugin_root.parent.parent


def main() -> int:
    workflow = pathlib.Path(sys.argv[1]).resolve()
    comfy_root = _find_comfy_root()
    if not (comfy_root / "comfy").is_dir():
        print("找不到 ComfyUI 目录: %s" % comfy_root, file=sys.stderr)
        print("用 COMFYUI_ROOT 环境变量指一个。", file=sys.stderr)
        return 2
    os.chdir(comfy_root)
    sys.path.insert(0, str(comfy_root))
    sys.path.insert(0, str(comfy_root / "custom_nodes"))

    import nodes
    import execution

    # Importing nodes only defines the core nodes; the custom-node scan
    # is a separate step that main.py runs.
    asyncio.run(nodes.init_extra_nodes(init_api_nodes=False))

    prompt = json.loads(workflow.read_text(encoding="utf-8"))
    valid, error, outputs, node_errors = asyncio.run(
        execution.validate_prompt("validate-only", prompt, None))

    print("workflow:", workflow)
    print("nodes   :", len(prompt))
    print("valid   :", valid)
    if error:
        print("error   :", json.dumps(error, ensure_ascii=False, indent=2))
    if node_errors:
        print("node errors:")
        print(json.dumps(node_errors, ensure_ascii=False, indent=2))
    else:
        print("node errors: none")
    print("outputs :", len(outputs))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
