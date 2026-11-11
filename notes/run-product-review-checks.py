import sys
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
import pytest
raise SystemExit(pytest.main(["webapp_tests/test_boundaries.py", "webapp_tests/test_review.py", "webapp_tests/test_snapshot.py", "webapp_tests/test_jobs.py", "webapp_tests/test_workflow.py", "webapp_tests/test_ui_browser.py", "-q", "--tb=short"]))