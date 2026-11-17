#!/usr/bin/env python3
"""script_ref for /mieh3/plan_preview stays inside the asset library."""

import os
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plan_preview  # noqa: E402


def _library() -> pathlib.Path:
    folder = pathlib.Path(tempfile.mkdtemp(prefix="mie-preview-"))
    board = folder / "storyboards" / "Demo"
    board.mkdir(parents=True)
    (board / "storyboard.json").write_text("{}\n", encoding="utf-8")
    outside = folder.parent / (folder.name + "-outside.json")
    outside.write_text("nope\n", encoding="utf-8")
    return folder


def main() -> None:
    library = _library()
    try:
        expected = (library / "storyboards" / "Demo" / "storyboard.json").resolve()
        found = plan_preview.resolve_script_file(str(library), "Demo")
        assert found.resolve() == expected, found
        found_rel = plan_preview.resolve_script_file(
            str(library), "storyboards/Demo/storyboard.json")
        assert found_rel.resolve() == expected

        for bad in ("../" + library.name + "-outside.json",
                    "storyboards/Demo/../../" + library.name + "-outside.json",
                    str(library.parent / (library.name + "-outside.json")),
                    "C:/Windows/win.ini"):
            try:
                plan_preview.resolve_script_file(str(library), bad)
            except plan_preview.ScriptRefError:
                pass
            else:
                raise AssertionError("accepted %r" % bad)

        try:
            link = library / "storyboards" / "Linked"
            link.mkdir()
            target = library.parent / (library.name + "-outside.json")
            os.symlink(target, link / "storyboard.json")
        except OSError as exc:
            print("symlink case skipped:", exc)
        else:
            try:
                plan_preview.resolve_script_file(
                    str(library), "storyboards/Linked/storyboard.json")
            except plan_preview.ScriptRefError:
                pass
            else:
                raise AssertionError("symlink escape was accepted")

        try:
            plan_preview.build_preview(str(library), "Demo")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid storyboard compiled")
    finally:
        import shutil
        shutil.rmtree(library, ignore_errors=True)
        outside = library.parent / (library.name + "-outside.json")
        outside.unlink(missing_ok=True)
    print("plan preview confinement: ok")


if __name__ == "__main__":
    main()
