import sys, tempfile, pathlib, json, threading
from unittest.mock import patch
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
from webapp.services import runner, snapshot
from webapp import config
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    gate = threading.Barrier(2)
    calls = []
    def pinned(*args):
        gate.wait(timeout=5)
        return str(root / "pin")
    def queued(*args, **kwargs):
        calls.append(1)
        return {"prompt_id": "probe-" + str(len(calls)), "number": len(calls)}
    results = []
    errors = []
    def send():
        try:
            results.append(runner.submit("probe", {"character_1": "asset"}, run_name="same_run"))
        except Exception as exc:
            errors.append(str(exc))
    with patch.object(runner, "_jobs", {}), patch.object(config, "JOBS_FILE", root / "jobs.json"), patch.object(config, "ensure_dirs", lambda: None), patch.object(runner.library, "get_storyboard", return_value={"json": {}}), patch.object(runner.workflow, "build", return_value={}), patch.object(snapshot, "write_script_snapshot", side_effect=pinned), patch.object(snapshot, "script_fingerprint", return_value="script-hash"), patch.object(snapshot, "asset_fingerprint", return_value="asset-hash"), patch.object(snapshot, "prune", lambda: None), patch.object(runner.comfy, "submit", side_effect=queued):
        threads = [threading.Thread(target=send) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        saved = json.loads((root / "jobs.json").read_text(encoding="utf-8"))["jobs"]
        print(json.dumps({"concurrent_same_name_accepted": len(results), "errors": errors, "persisted_fields": sorted(saved[0]), "asset_hashes_persisted": "asset_hashes" in saved[0], "role_assets_persisted": "role_assets" in saved[0]}))
    with patch.object(config, "SNAPSHOT_DIR", root / "snapshots"):
        import os
        active = config.SNAPSHOT_DIR / "active_queued"
        newer = config.SNAPSHOT_DIR / "newer"
        active.mkdir(parents=True)
        newer.mkdir()
        os.utime(active, (100, 100))
        os.utime(newer, (200, 200))
        snapshot.prune(keep=1)
        print(json.dumps({"older_active_snapshot_survives_prune": active.exists()}))