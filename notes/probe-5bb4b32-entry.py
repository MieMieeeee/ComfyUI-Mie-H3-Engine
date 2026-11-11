import sys, pathlib, json, os
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp import config
root = pathlib.Path(sys.argv[1]).resolve()
config.COMFYUI_ROOT = root
config.SETTINGS_FILE = root / "settings.json"
config.JOBS_FILE = root / "jobs.json"
config.ensure_dirs = lambda: None
from webapp.services import comfy, runner
expected = root / "output" / "h3_chains" / "other_run"
link = root / "output" / "h3_chains" / "take"
assert link.resolve() == expected and expected.is_relative_to(root)
listed = any(r["run_name"] == "take" for r in comfy.recent_runs())
result = runner.delete_record("disk:take", delete_files=True)
print(json.dumps({"entry_was_in_real_disk_list": listed, "other_film_survives": (expected / "final" / "film.mp4").exists(), "link_removed": not os.path.lexists(link), "files_removed": result["files_removed"]}))