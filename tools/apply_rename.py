#!/usr/bin/env python3
"""Apply the node-type rename map to the files that carry type names.

The rule is deliberately narrow: a type name is rewritten only when it is
a WHOLE quoted string literal - ``"MiniMaxH3ChainPlan"``. That has two
consequences we need.

* Extension ids, node ids, repository URLs and comments are left alone.
  The upstream frontend carries ids like ``"MiniMaxH3ContexLoop.
  cancelRerollControl"`` and ``"MiniMaxH3.SelfLiftSeedHunt"``; a
  prefix-based scan would corrupt them.
* The rewrite is idempotent by construction. The suffixed name contains
  the old one, but it is not a whole quoted literal equal to the old one,
  so running the tool twice changes nothing.

A type name that appears inside a LONGER string (for example
``"comfy.MiniMaxH3ChainPlan"``) is reported rather than rewritten, because
that form has to be looked at by a human.

Usage:
    python tools/apply_rename.py            # report only
    python tools/apply_rename.py --apply    # rewrite
"""

import argparse
import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "tools" / "node_rename_map.json"

# Where type names live. The frontend is the important one: it compares
# nodeData.name against type-name literals, so a renamed node would not
# get its editor without this.
TARGETS = (
    "web",
    "tests",
    "workflows",
    "example_workflows",
    "docs/examples",
    # A second set of workflow recipes lives beside the build tooling.
    "tools/v06/recipes",
)

# Never rewritten: the map itself (its keys ARE the old names), and the
# documentation outside docs/examples - the plan's evidence index cites
# UPSTREAM type names, and those must stay as they are.
EXCLUDE = (
    "tools/node_rename_map.json",
    "docs/PLAN_H3_ENGINE_FORK_AND_DRAMA_PLATFORM.md",
    "docs/PLAN_ASSET_LIBRARY_GUIDE.md",
)

# Occurrences where the OLD name is the point. The code means "the upstream
# name" - a retired type that must not exist any more, or a historical type
# a modern workflow must not contain - not "this pack's current type".
# Rewriting those would break the very check they belong to.
#
# (file, type name)
EXCEPTIONS = (
    # A legacy-detection set: these are the historical names a modern
    # workflow must not contain.
    ("tests/_workflow_catalog_unit_test.py", "MiniMaxH3ChainPlan"),
    ("tests/_workflow_catalog_unit_test.py", "MiniMaxH3ChainRunManager"),
    # An AST lookup of the Python CLASS, which keeps its upstream name.
    ("tests/_ffmpeg_concat_path_unit_test.py", "MiniMaxH3ChainAssemble"),
)

TEXT_SUFFIXES = {".js", ".mjs", ".py", ".json", ".md", ".txt", ".html", ".yml"}


def load_map() -> dict[str, str]:
    if not MAP_PATH.is_file():
        print("node_rename_map.json is missing - run tools/gen_rename_map.py",
              file=sys.stderr)
        raise SystemExit(1)
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def iter_files():
    for target in TARGETS:
        root = ROOT / target
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if (path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
                    and ".git" not in path.parts):
                relative = path.relative_to(ROOT).as_posix()
                if relative not in EXCLUDE:
                    yield path


def quoted_pattern(name: str) -> re.Pattern:
    escaped = re.escape(name)
    return re.compile(r'(["\'`])' + escaped + r'\1')


def bare_key_pattern(name: str) -> re.Pattern:
    """A bare identifier used as an object key, e.g. ``MiniMaxH3ChainPlan:``.

    The frontend keeps tables keyed by node type, and those keys are not
    quoted literals - a quoted-literal-only rule would leave them on the
    old names while the type they are looked up with carries the new one.
    """
    return re.compile(r'^(\s*)' + re.escape(name) + r'(\s*):', re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the files (default: report only)")
    args = parser.parse_args()

    mapping = load_map()
    patterns = {name: (quoted_pattern(name), bare_key_pattern(name))
                for name in mapping}
    # A type name used inside a longer string - needs a human.
    embedded = re.compile(
        r'["\'`]' + re.escape("|".join(sorted(mapping, key=len, reverse=True)))
        + r'[^"\'`]+["\'`]')

    touched = 0
    replacements = 0
    embedded_reports = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        original = text
        relative = path.relative_to(ROOT).as_posix()
        for name, (quoted, bare) in patterns.items():
            if (relative, name) in EXCEPTIONS:
                continue
            text, count = quoted.subn(
                lambda match, target=mapping[name]:
                match.group(1) + target + match.group(1), text)
            replacements += count
            # A bare key cannot hold the suffix - "|" is not valid in an
            # identifier - so the key becomes a quoted one.
            text, count = bare.subn(
                lambda match, target=mapping[name]:
                match.group(1) + '"' + target + '"' + match.group(2) + ":",
                text)
            replacements += count
        if text != original:
            touched += 1
            if args.apply:
                path.write_text(text, encoding="utf-8")
        for match in embedded.finditer(original):
            line = original[:match.start()].count("\n") + 1
            embedded_reports.append("%s:%d  %s" % (
                path.relative_to(ROOT), line, match.group(0)[:100]))

    print("%s: %d file(s), %d replacement(s) across %d type names."
          % ("Rewrote" if args.apply else "Would rewrite",
             touched, replacements, len(mapping)))
    if embedded_reports:
        print("\nType names used inside a longer string (not rewritten - "
              "please look at these):")
        for report in embedded_reports:
            print("  " + report)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
