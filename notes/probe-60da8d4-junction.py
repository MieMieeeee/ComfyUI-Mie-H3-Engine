import sys, pathlib, json
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp import config
from webapp.services import comfy
root = pathlib.Path(sys.argv[1]).resolve()
expected = root / "output" / "h3_chains" / "other_run"
link = root / "output" / "h3_chains" / "take"
target = link.resolve()
assert target == expected and target.is_relative_to(root)
config.COMFYUI_ROOT = root
removed = comfy.delete_run_files("take")
print(json.dumps({"other_run_survives": (expected / "keep.txt").exists(), "removed": removed}))