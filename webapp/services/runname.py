"""Run names the engine will accept verbatim.

The engine's ``_strict_run_name`` refuses to redirect a name it cannot
take as-is: a run's identity is the folder on disk and the file name,
so quietly rewriting one would move the artifacts somewhere the user
does not look. That strictness is right, and the obligation is ours:
the web app is the author of the run name it submits, so it has to
hand the engine a clean one.

The rule (chain_nodes._strict_run_name): 1-96 characters, letters,
numbers, '.', '_' or '-', starting and ending with a letter or number.
Most script names in a Chinese library have no ASCII at all, so the
generated fallback carries a short digest of the name - two different
scripts never share one run, and the same script always gets the same
run (re-rendering replaces it instead of scattering copies).
"""

from __future__ import annotations

import hashlib
import re

#: The same shape the engine's _safe_name produces.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_run_name(value: str) -> str:
    """One clean run name, or "" when nothing usable is left."""
    text = _UNSAFE.sub("_", str(value or "").strip())
    # Strip at both ends AND after the cut: truncation can leave a
    # trailing '.', '_' or '-', which the engine would refuse.
    text = text.strip("._-")[:96].strip("._-")
    return text


def effective_run_name(requested: str, script_name: str) -> str:
    """The run name the web app will actually submit.

    A typed name wins (cleaned); otherwise the name is derived from the
    script, and a script whose name is entirely non-ASCII gets a digest
    so its run is still unique and still reproducible.
    """
    typed = safe_run_name(requested)
    if typed:
        return typed
    from_script = safe_run_name(script_name)
    if from_script:
        return ("sb_web_" + from_script)[:96].strip("._-") or "sb_web"
    digest = hashlib.sha1(str(script_name or "").encode("utf-8")).hexdigest()[:8]
    return "sb_web_" + digest
