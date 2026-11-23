"""Write JSON the way the workbench writes its own state: atomically.

jobs.json, history.json and execution_sync.json are the workbench's
memory - the task log, the dismissed runs, the version counter. A plain
write that dies halfway leaves half a file, and half a JSON file parses
as nothing: the next start would quietly begin from an empty table.
So the content is written to a temporary name and moved into place in
one step. A write that fails anywhere leaves the previous content
untouched and no litter behind.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading


settings_lock = threading.Lock()


def write_json(path, value) -> None:
    """Write ``value`` as JSON to ``path``, all at once or not at all."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
