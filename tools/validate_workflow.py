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


def main() -> int:
    workflow = pathlib.Path(sys.argv[1]).resolve()
    comfy_root = pathlib.Path(
        os.environ.get("COMFYUI_ROOT")
        or r"C:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\ComfyUI"
    )
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
