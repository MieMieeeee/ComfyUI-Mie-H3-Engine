import sys
sys.path.insert(0, "F:/ComfyUI-Mie-H3-Engine")
import pytest
raise SystemExit(pytest.main(["webapp_tests", "--ignore=webapp_tests/test_e2e_webui.py", "-q", "--tb=short"]))