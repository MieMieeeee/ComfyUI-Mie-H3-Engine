import sys, tempfile, pathlib, json
from unittest.mock import patch
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp import config
from webapp.services import runner, comfy, history, runname
from webapp.flask_app import create_app
result = {}
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    comfy_root = root / "comfy"
    with patch.multiple(config, SETTINGS_FILE=root / "settings.json", JOBS_FILE=root / "jobs.json", COMFYUI_ROOT=comfy_root, OUTPUT_SUBDIRS=("live_storyboards", "h3_chains")), patch.object(config, "ensure_dirs", lambda: None), patch.object(runner, "_jobs", {}), patch.object(runner, "_reserved", set()):
        flat = comfy_root / "output" / "live_storyboards"
        flat.mkdir(parents=True)
        (comfy_root / "output" / "h3_chains").mkdir()
        (flat / "take_2026-11-10.mp4").write_bytes(b"probe")
        dotted = flat / "take.v2_2026-11-10.mp4"
        dotted.write_bytes(b"probe")
        relpaths = [a["relpath"] for a in comfy.collect_artifacts("take")]
        comfy.delete_run_files("take", relpaths)
        result["dot_named_other_run_deleted"] = not dotted.exists()
        base = runname.base_run_name("story")
        runner._jobs.update({"p1": {"job_id": "p1", "script": "story", "attempt": 1, "run_name": base + "_v1", "status": "success"}, "p2": {"job_id": "p2", "script": "story", "attempt": 2, "run_name": base + "_v2", "status": "success"}})
        before = runner.next_attempt("story")
        runner.delete_record("p2")
        result["legacy_counter_after_dismiss"] = {"before": before, "after": runner.next_attempt("story"), "persisted": history.last_attempt("story")}
        victim = comfy_root / "victim"
        victim.mkdir()
        (victim / "dummy.txt").write_text("temporary probe")
        traversal = chr(92).join(["..", "..", "victim"])
        computed = (comfy_root / "output" / "h3_chains" / traversal).resolve()
        assert computed == victim.resolve() and computed.is_relative_to(root.resolve())
        client = create_app().test_client()
        response = client.post("/api/render/jobs/disk:..%5C..%5Cvictim/delete", json={"files": True})
        result["delete_escape_probe"] = {"http": response.status_code, "outside_output_removed": not victim.exists()}
print(json.dumps(result))