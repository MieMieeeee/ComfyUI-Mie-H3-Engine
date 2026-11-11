import sys, json, tempfile, pathlib
from unittest.mock import patch
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp import config
from webapp.services import runner, comfy, history, runname
results = {}
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    with patch.multiple(config, SETTINGS_FILE=root / "settings.json", JOBS_FILE=root / "jobs.json", COMFYUI_ROOT=root, OUTPUT_SUBDIRS=["live_storyboards", "h3_chains"]), patch.object(config, "ensure_dirs", lambda: None), patch.object(runner, "_jobs", {}), patch.object(runner, "_reserved", set()):
        flat = root / "output" / "live_storyboards"
        flat.mkdir(parents=True)
        (flat / "take.mp4").write_bytes(b"probe")
        later = flat / "take_v2.mp4"
        later.write_bytes(b"probe")
        comfy.delete_run_files("take")
        results["delete_take_also_removed_take_v2"] = not later.exists()
        base = runname.base_run_name("story")
        runner._jobs.update({"v1": {"job_id": "v1", "script": "story", "attempt": 1, "run_name": base, "status": "success"}, "v2": {"job_id": "v2", "script": "story", "attempt": 2, "run_name": base + "_v2", "status": "success"}})
        old_next = runner.next_attempt("story")
        runner.delete_record("v2", delete_files=False)
        new_next = runner.next_attempt("story")
        predicted = runname.effective_run_name("", "story", new_next)
        results["remove_history"] = {"before_next": old_next, "after_next": new_next, "reused_name": predicted == base + "_v2", "new_name_is_dismissed": history.is_dismissed(predicted)}
        runner._jobs.clear()
        runner._jobs["active"] = {"job_id": "active", "run_name": "pending_run", "script": "story", "status": "queued"}
        with patch.object(comfy, "queue_ids", return_value=(set(), set())), patch.object(comfy, "history", return_value={"status": {"status_str": "error", "messages": [["execution_interrupted", {}]]}}), patch.object(comfy, "recent_runs", return_value=[]):
            listed = runner.all_records()[0]
        try:
            runner._refuse_a_duplicate("pending_run", "story")
            refusal = False
        except ValueError:
            refusal = True
        results["history_status"] = {"shown": listed["status"], "stored": runner._jobs["active"]["status"], "continuation_refused": refusal}
print(json.dumps(results))