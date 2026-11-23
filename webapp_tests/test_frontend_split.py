"""The front end is split — and it still works.

Two things were split:

* ``webapp/static/app.js`` became one file per page/infrastructure under
  ``webapp/static/js/``. No build step: the browser loads them in the
  order the page lists them, and Alpine goes last.
* ``webapp/templates/scripts.html`` became a page plus a few Jinja
  includes, split at the panel boundaries.

These tests pin down what must not change: every page still mounts, the
scripts page still renders the same markup, and the split files are
really loaded (a file nobody loads is a file nobody reads).
"""

from __future__ import annotations

import pathlib
import re

import pytest

WEBAPP = pathlib.Path(__file__).resolve().parents[1] / "webapp"
STATIC = WEBAPP / "static"
JS = STATIC / "js"

#: The files the front end is split into, in load order.
SPLIT = ("api", "shell", "llm", "assets", "scripts-intake", "scripts-editor",
         "video", "history")

PAGES = ("/llm", "/assets", "/scripts", "/video", "/history")


def test_the_split_files_are_all_there():
    for name in SPLIT:
        assert (JS / ("%s.js" % name)).is_file(), name


def test_the_old_single_file_is_gone():
    """Two copies of the same code would be two truths about the page."""
    assert not (STATIC / "app.js").exists()


def test_every_page_loads_the_whole_bundle(client):
    """No build step: the page lists the files itself. Every page loads
    all of them, so a component a page does not use is still defined."""
    body = client.get("/").get_data(as_text=True)
    for name in SPLIT:
        assert "js/%s.js" % name in body, name


def test_alpine_is_loaded_last(client):
    """Alpine starts as soon as it is evaluated: the components it
    mounts have to be defined by then."""
    body = client.get("/").get_data(as_text=True)
    scripts = re.findall(r'<script defer src="([^"]+)"', body)
    assert scripts, "the page must load its scripts"
    assert scripts[-1].split("?")[0].endswith("alpine.min.js"), scripts
    for name in SPLIT:
        assert any(entry.split("?")[0].endswith("js/%s.js" % name)
                   for entry in scripts), name


def test_the_scripts_are_loaded_in_dependency_order(client):
    """api.js must come before the pages that call it, and the scripts
    page's two halves in the order they need each other."""
    body = client.get("/").get_data(as_text=True)
    scripts = [entry for entry in re.findall(r'<script defer src="([^"]+)"', body)]
    order = [entry for entry in scripts if "js/" in entry]
    names = [entry.rsplit("/", 1)[-1].rsplit(".", 1)[0] for entry in order]
    assert names.index("api") < names.index("shell")
    assert names.index("shell") < names.index("scripts-intake")
    assert names.index("scripts-intake") < names.index("scripts-editor")


@pytest.mark.parametrize("path", PAGES)
def test_every_page_mounts(client, path):
    """Each page has its own component; the file it lives in has to
    define it."""
    body = client.get(path).get_data(as_text=True)
    assert "x-data=" in body, path
    assert "<html" in body and "</html>" in body, path


def test_every_page_uses_the_component_of_its_own_file(client):
    assert 'x-data="appShell()"' in client.get("/").get_data(as_text=True)
    assert 'x-data="llmPage()"' in client.get("/llm").get_data(as_text=True)
    assert 'x-data="assetsPage()"' in client.get("/assets").get_data(as_text=True)
    assert 'x-data="scriptsPage()"' in client.get("/scripts").get_data(as_text=True)
    assert 'x-data="videoPage()"' in client.get("/video").get_data(as_text=True)
    assert 'x-data="historyPage()"' in client.get("/history").get_data(as_text=True)


def test_the_component_of_each_page_is_defined_in_its_own_file():
    """The page's component has to be in the file the page loads - not
    in another one that happens to be loaded too."""
    owners = {
        "appShell": "shell.js",
        "llmPage": "llm.js",
        "assetsPage": "assets.js",
        "scriptsPage": "scripts-editor.js",
        "videoPage": "video.js",
        "historyPage": "history.js",
    }
    for component, filename in owners.items():
        text = (JS / filename).read_text(encoding="utf-8")
        assert "function %s(" % component in text, component
        for other, other_file in owners.items():
            if other == component:
                continue
            assert "function %s(" % other not in text, \
                "%s belongs in %s, not in %s" % (other, other_file, filename)


def test_the_scripts_page_is_split_at_panel_boundaries():
    """The page is a page plus four panels. Every include is really
    included, and the page still says the same thing."""
    page = (WEBAPP / "templates" / "scripts.html").read_text(encoding="utf-8")
    for name in ("_list", "_intake", "_editor", "_panes"):
        assert 'scripts/%s.html' % name in page, name
        assert (WEBAPP / "templates" / "scripts" / ("%s.html" % name)).is_file()


def test_the_scripts_page_still_renders_everything(client):
    """The split must not lose a thing: the intake card, the editor, the
    three tabs and the JSON source are all still there."""
    body = client.get("/scripts").get_data(as_text=True)
    for needle in ("intake-card", "从图片或文字起草", "workbench-tabs",
                   "预览", "编辑", "JSON 源码", "class=\"json-pane\"",
                   "class=\"editor-pane\"", "class=\"preview-pane\""):
        assert needle in body, needle


def test_the_two_halves_of_the_scripts_page_are_one_component():
    """The intake half and the editor half are one Alpine component: the
    page has one x-data, and the halves are joined into it."""
    editor = (JS / "scripts-editor.js").read_text(encoding="utf-8")
    intake = (JS / "scripts-intake.js").read_text(encoding="utf-8")
    assert "const scriptsIntakePart = {" in intake
    assert "Object.getOwnPropertyDescriptors(scriptsIntakePart)" in editor
    # The getter has to be copied as a getter: Object.assign would turn
    # it into a value and the page would stop reacting.
    assert "Object.defineProperties" in editor
    assert "Object.assign(part" not in editor


def test_no_field_is_defined_twice_in_the_two_halves():
    """Both halves end up in the same object: a field in both would be
    defined twice, and the second one would silently win."""
    import re as _re
    editor = (JS / "scripts-editor.js").read_text(encoding="utf-8")
    intake = (JS / "scripts-intake.js").read_text(encoding="utf-8")

    def keys(text):
        found = set()
        for line in text.split("\n"):
            match = _re.match(r"^\s{2,4}([A-Za-z_$][\w$]*)\s*[:(]", line)
            if match:
                found.add(match.group(1))
        return found

    both = keys(editor) & keys(intake)
    assert not both, sorted(both)
