#!/usr/bin/env python3
"""Generate the node-type rename map for this fork.

ComfyUI overwrites a duplicate node type name silently - there is no
warning and no error, and the winner is decided by the filesystem's
directory order, not by anything a user could reason about. Two packs that
register the same type name therefore make the wrong implementation run,
quietly. This fork renames every type it registers so that it can sit
beside the upstream pack.

The map is generated, never hand-written: 87 entries typed by hand will
miss one, and a missed one is a silent collision. It is read from the
NODE_CLASS_MAPPINGS literals of every module with an AST pass, so no
ComfyUI runtime is needed to produce it.

Usage:
    python tools/gen_rename_map.py            # write the map
    python tools/gen_rename_map.py --check    # verify the map is current
"""

import argparse
import ast
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "tools" / "node_rename_map.json"
SUFFIX = "|MieH3"


def collect_type_names() -> dict[str, str]:
    """Return {type name: module that registers it}."""
    found: dict[str, str] = {}
    for path in sorted(ROOT.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets
                       if isinstance(t, ast.Name)]
            if not any(name.endswith("NODE_CLASS_MAPPINGS") for name in targets):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.setdefault(key.value, path.name)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed map matches the code")
    args = parser.parse_args()

    names = collect_type_names()
    if not names:
        print("No node types found - is this the right directory?", file=sys.stderr)
        return 1
    mapping = {name: name + SUFFIX for name in sorted(names)}

    if args.check:
        if not MAP_PATH.is_file():
            print("node_rename_map.json is missing.", file=sys.stderr)
            return 1
        current = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        if current != mapping:
            missing = sorted(set(mapping) - set(current))
            extra = sorted(set(current) - set(mapping))
            changed = sorted(k for k in set(current) & set(mapping)
                             if current[k] != mapping[k])
            print("The rename map is out of date:", file=sys.stderr)
            for label, items in (("missing", missing), ("extra", extra),
                                 ("changed", changed)):
                for name in items:
                    print("  %s: %s" % (label, name), file=sys.stderr)
            return 1
        print("The rename map matches the registered node types (%d)." % len(mapping))
        return 0

    MAP_PATH.write_text(json.dumps(mapping, indent=2, ensure_ascii=False,
                                   sort_keys=True) + "\n", encoding="utf-8")
    print("Wrote %s with %d entries." % (MAP_PATH.relative_to(ROOT), len(mapping)))
    modules = sorted(set(names.values()))
    print("Registered by %d modules: %s" % (len(modules), ", ".join(modules)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
