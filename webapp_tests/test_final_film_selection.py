"""Finished films must not depend on filesystem traversal order."""
from webapp import config
from webapp.services import comfy, runner

def _group(tmp_path, monkeypatch, with_final):
    monkeypatch.setattr(config, "COMFYUI_ROOT", tmp_path)
    root = tmp_path / "output" / "h3_chains" / "sb_selection"
    (root / "segments").mkdir(parents=True)
    (root / "segments" / "clip.mp4").write_bytes(b"segment")
    if with_final:
        (root / "final").mkdir()
        (root / "final" / "film.mp4").write_bytes(b"finished")
    entries = comfy.scan_output(max_age=0)
    entries.sort(key=lambda entry: entry["role"] != "segment")
    monkeypatch.setattr(comfy, "scan_output", lambda: entries)
    record = {"job_id": "selection", "run_name": "sb_selection", "status": "success", "submitted_at": 1, "script": "Selection"}
    return runner._jobs_grouped([record], 1, 10, 0, "")["groups"][0]

def test_finished_film_wins_even_when_segment_is_scanned_first(plugin_root, tmp_path, monkeypatch):
    group = _group(tmp_path, monkeypatch, True)
    assert group["film"].endswith("/final/film.mp4")

def test_a_segment_is_not_presented_as_a_finished_film(plugin_root, tmp_path, monkeypatch):
    group = _group(tmp_path, monkeypatch, False)
    assert group["film"] == ""
