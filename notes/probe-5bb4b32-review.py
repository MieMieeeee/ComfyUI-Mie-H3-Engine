import sys, pathlib, json
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp import config
from webapp.services import comfy
root = pathlib.Path(sys.argv[1]).resolve()
expected = root / "output" / "h3_chains" / "other_run"
link = root / "output" / "h3_chains" / "take"
assert link.resolve() == expected and expected.is_relative_to(root)
config.COMFYUI_ROOT = root
artifacts = comfy.collect_artifacts("take")
paths = [a["relpath"] for a in artifacts]
removed = comfy.delete_run_files("take", paths)
print(json.dumps({"collected": paths, "other_film_survives": (expected / "film.mp4").exists(), "link_removed": not __import__("os").path.lexists(link), "removed": removed}))