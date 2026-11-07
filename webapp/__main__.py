"""``python webapp/__main__.py`` - start the web front end.

The ComfyUI node starts it this way: with ComfyUI's own interpreter.

The embedded python runs in ISOLATED MODE - a ``._pth`` file fixes
``sys.path``, so neither the current directory nor ``PYTHONPATH`` is on
it, and ``python -m webapp`` cannot find the package. So the package
directory is put back on the path here, by hand, before the imports that
need it.

Run it as a file, not as a module:

    <ComfyUI>/python_embeded/python.exe <plugin>/webapp/__main__.py
"""

from __future__ import annotations

import os
import sys

# The package's parent - the plugin root. Putting it back on sys.path is
# what makes "from webapp import ..." work under the embedded python.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)

from webapp import config  # noqa: E402
from webapp.flask_app import app  # noqa: E402


def main() -> int:
    """Run the web front end. Single process, no reloader: a second
    process would fight over the render queue."""
    app.run(host=config.WEB_HOST, port=config.WEB_PORT,
            debug=config.WEB_DEBUG, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
