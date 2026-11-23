"""Shared fixtures.



The tests must not touch the real asset library or the real keys file, so

every test gets a temporary plugin root with an empty library in it.

"""



from __future__ import annotations



import pathlib

import sys



import pytest



# tests/webui/conftest.py -> tests/webui -> tests -> the plugin root.
# The plugin root is what has to be on sys.path: the web app is the
# ``webapp`` package that lives there.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:

    sys.path.insert(0, str(REPO_ROOT))



from webapp import config  # noqa: E402


#: The ComfyUI the tests talk to. NOT the one on 8188: that is the
#: person's own engine, and a test that submits there renders for real -
#: it burns the GPU, it fills the person's queue, and it leaves output
#: folders in their output tree. Nothing listens on this port.
DEAD_COMFYUI = "http://127.0.0.1:8187"





@pytest.fixture()

def plugin_root(tmp_path, monkeypatch):

    """A throwaway plugin root with an empty asset library."""

    root = tmp_path / "plugin"

    (root / "asset_library" / "characters").mkdir(parents=True)

    (root / "asset_library" / "scenes").mkdir(parents=True)

    (root / "asset_library" / "storyboards").mkdir(parents=True)

    monkeypatch.setattr(config, "PLUGIN_ROOT", root)

    monkeypatch.setattr(config, "LLM_KEYS_FILE", root / "mie_llm_keys.json")

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")

    monkeypatch.setattr(config, "JOBS_FILE", tmp_path / "jobs.json")

    # NOT the engine on 8188: that is the person's own, and a test that
    # submits there renders for real - it burns the GPU, it fills their
    # queue, and it leaves output folders in their output tree. Nothing
    # listens on this port, so a submission fails at once.
    monkeypatch.setattr(config, "COMFYUI_URL", DEAD_COMFYUI)

    monkeypatch.setenv("MIE_PLUGIN_ROOT", str(root))

    monkeypatch.setenv("MIE_COMFYUI_URL", DEAD_COMFYUI)

    return root





@pytest.fixture()

def client(plugin_root):

    """A Flask test client bound to the throwaway plugin root."""

    from webapp.flask_app import create_app

    return create_app().test_client()



@pytest.fixture(autouse=True)

def _isolate_the_job_log(tmp_path, monkeypatch):

    """No test may write into the user's real job log.

    The job table is module-level and the runner persists it on every
    status change, so a test that forgets the plugin_root fixture would
    quietly append its fake jobs to the real file.

    The snapshot folder goes with it: a submission pins its script into
    a snapshot, so a test that calls the runner leaves a folder behind
    in the user's real snapshot directory.
    """

    monkeypatch.setattr(config, "JOBS_FILE", tmp_path / "jobs.json")

    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")





#: A minimal PNG and a minimal WAV, made rather than hand-written: a

#: hex literal that is one byte off fails deep inside PIL or libsndfile,

#: which tells you nothing about the code under test.

def _png_bytes(width: int = 4, height: int = 4) -> bytes:

    import io

    from PIL import Image

    buf = io.BytesIO()

    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buf, format="PNG")

    return buf.getvalue()





def _wav_bytes(seconds: float = 0.25, rate: int = 16000) -> bytes:

    import io

    import wave

    frames = int(seconds * rate)

    buf = io.BytesIO()

    with wave.open(buf, "wb") as handle:

        handle.setnchannels(1)

        handle.setsampwidth(2)

        handle.setframerate(rate)

        handle.writeframes(bytes(2 * frames))

    return buf.getvalue()





PNG_1x1 = _png_bytes()

WAV_1S = _wav_bytes()

