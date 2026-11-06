#!/usr/bin/env python3
"""Rewrite node type names in saved ComfyUI workflows.

This fork registers every node type under a ``|MieH3`` suffix so it can be
installed beside the upstream pack. A workflow saved before the rename
still says ``"MiniMaxH3ChainPlan"``, and ComfyUI resolves that name against
whatever pack registered it - so a workflow written for the upstream pack
would silently run the upstream nodes against our pack, or show red missing
nodes if the upstream pack is not installed.

This script rewrites those names. It handles both shapes ComfyUI writes:

* the UI format, where a node carries ``"type"``;
* the API format, where a node carries ``"class_type"``;

and it recurses through the whole document, so nodes inside subgraphs
(``definitions.subgraphs[].nodes[]``) and inside any other nested
structure are covered too.

Only the type name is touched. ``widgets_values`` - the positional widget
data - is left exactly as it is, because the rename does not change a
node's input order.

The rewrite is idempotent: the suffixed name is not a key in the map, so
running the tool twice changes nothing the second time.

Usage:
    python tools/migrate_workflows.py <path> [<path> ...]
    python tools/migrate_workflows.py --apply <path> [<path> ...]

Paths may be files or directories; directories are walked for .json files.
"""

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "tools" / "node_rename_map.json"

TYPE_KEYS = ("type", "class_type")


def load_map() -> dict[str, str]:
    if not MAP_PATH.is_file():
        print("node_rename_map.json is missing - run tools/gen_rename_map.py",
              file=sys.stderr)
        raise SystemExit(1)
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def rewrite(value, mapping):
    """Return (rewritten value, number of replacements)."""
    count = 0
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in TYPE_KEYS and isinstance(item, str) and item in mapping:
                result[key] = mapping[item]
                count += 1
                continue
            new_item, item_count = rewrite(item, mapping)
            result[key] = new_item
            count += item_count
        return result, count
    if isinstance(value, list):
        result = []
        for item in value:
            new_item, item_count = rewrite(item, mapping)
            result.append(new_item)
            count += item_count
        return result, count
    return value, count


def detect_indent(text: str) -> int:
    """Guess the indentation of a JSON document, so the rewrite keeps it.

    A JSON file may start with a long first line and continue on
    continuation lines that are indented far deeper than the document's own
    indentation, so the base indent is the SMALLEST indent among the lines
    that open a key.
    """
    candidates = []
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if not stripped or stripped == line:
            continue
        if stripped.startswith('"') or stripped.startswith("{"):
            candidates.append(len(line) - len(stripped))
    return min(candidates) if candidates else 2


def collect(paths):
    for target in paths:
        root = pathlib.Path(target)
        if root.is_file():
            yield root
        elif root.is_dir():
            for path in sorted(root.rglob("*.json")):
                if ".git" not in path.parts:
                    yield path
        else:
            print("No such path: %s" % target, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--apply", action="store_true",
                        help="write the files (default: report only)")
    args = parser.parse_args()

    mapping = load_map()
    total_files = 0
    total_names = 0
    for path in collect(args.paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            print("Skipped (not JSON): %s" % path, file=sys.stderr)
            continue
        rewritten, count = rewrite(document, mapping)
        if not count:
            continue
        total_files += 1
        total_names += count
        print("%s: %d node type name(s)" % (path, count))
        if args.apply:
            indent = detect_indent(text)
            path.write_text(
                json.dumps(rewritten, ensure_ascii=False, indent=indent) + "\n",
                encoding="utf-8")
    print("\n%s %d file(s), %d node type name(s)."
          % ("Rewrote" if args.apply else "Would rewrite",
             total_files, total_names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
