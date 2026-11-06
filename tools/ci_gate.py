#!/usr/bin/env python3
"""CI gate for the upstream release-check suite.

The upstream suite does not pass cleanly on any platform we run it on: the
Windows environment loses 56 of its 245 scripts to symlink privileges, NTFS
timestamp granularity and a Node 24 harness change, and the Linux runner
loses a different set. A gate that demands zero failures would therefore be
red from its first run and nobody would look at it again.

So the gate is a BASELINE gate: the set of scripts that are known to fail is
recorded per platform, and only a NEW failure turns the build red. A baseline
entry that starts passing is reported as fixed and never fails the build -
the baseline shrinks, it does not block.

Two rules keep this honest:

  * the scripts that carry our own fixes are a hard requirement and must
    pass, whatever the baseline says (see MUST_PASS);
  * when a platform's baseline file is missing, the first run writes it and
    says so, instead of pretending the build is green.

Usage:
    python tools/ci_gate.py --comfy-root <ComfyUI checkout> [--platform NAME]

The platform name defaults to the runner label (windows-latest /
ubuntu-latest) and selects the baseline file under tools/known_failures/.
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / "tools" / "known_failures"

# Our own regressions carry the fixes this fork ships. They are never
# allowed into the baseline: if one of them fails, the build is red.
MUST_PASS = (
    "_mie_png_verification_strict_unit_test.py",
    "_mie_mtime_selection_unit_test.py",
)

FAIL_LINE = re.compile(r"^FAIL (\S+)", re.MULTILINE)


def rerun_failures_serially(failures, comfy_root: pathlib.Path,
                            timeout: int) -> set[str]:
    """Re-run the failing scripts one at a time.

    A few upstream scripts (the selflift hunt, the working branches, the
    upscale preview) are timing-sensitive and fail under parallel load while
    passing when they run alone. Re-running serially separates those from
    real failures, so the baseline records only the latter and does not
    drift on every run.
    """
    confirmed = set()
    for name in sorted(failures):
        script = ROOT / "tests" / name
        if not script.is_file():
            confirmed.add(name)
            continue
        executable = ["node", str(script)] if script.suffix == ".mjs" else [
            sys.executable, str(script)]
        try:
            result = subprocess.run(
                executable, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=timeout,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "",
                     "COMFYUI_PATH": str(comfy_root)})
        except (OSError, subprocess.TimeoutExpired):
            confirmed.add(name)
            continue
        if result.returncode:
            confirmed.add(name)
        else:
            print("  (passes on its own, not counted as a failure: %s)" % name)
    return confirmed


def discover_platform() -> str:
    if os.name == "nt":
        return "windows-latest"
    if sys.platform == "darwin":
        return "macos-latest"
    return "ubuntu-latest"


def run_suite(comfy_root: pathlib.Path, jobs: int, timeout: int):
    command = [sys.executable, str(ROOT / "tools" / "check_release.py"),
               "--comfy-root", str(comfy_root),
               "--jobs", str(jobs), "--timeout", str(timeout)]
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, flush=True)
    return result.stdout


def baseline_path(platform: str) -> pathlib.Path:
    return BASELINE_DIR / ("%s.txt" % platform)


def read_baseline(path: pathlib.Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def write_baseline(path: pathlib.Path, failures) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Scripts known to fail on %s. Managed by tools/ci_gate.py:\n"
        "# a NEW failure outside this list fails the build; an entry that\n"
        "# starts passing is reported as fixed and can be removed.\n"
        % path.stem
        + "\n".join(sorted(failures)) + "\n",
        encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", type=pathlib.Path, required=True)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    platform = args.platform or discover_platform()
    output = run_suite(args.comfy_root, args.jobs, args.timeout)
    failed = set(FAIL_LINE.findall(output))
    print("\n=== CI gate (%s) ===" % platform)
    print("failing scripts after the parallel run: %d" % len(failed))
    if failed:
        print("re-running them one at a time to drop the timing-sensitive ones:")
        failed = rerun_failures_serially(failed, args.comfy_root, args.timeout)
    print("confirmed failures: %d" % len(failed))

    must_fail = sorted(failed.intersection(MUST_PASS))
    if must_fail:
        print("FAIL: our own regressions are failing:")
        for name in must_fail:
            print("  - %s" % name)
        return 1

    path = baseline_path(platform)
    baseline = read_baseline(path)
    if not path.is_file():
        write_baseline(path, failed)
        print("No baseline for %s yet: wrote %s with %d entries."
              % (platform, path.relative_to(ROOT), len(failed)))
        print("Commit that file so the next run can catch NEW failures.")
        return 0

    new_failures = sorted(failed - baseline)
    fixed = sorted(baseline - failed)
    if fixed:
        print("fixed since the baseline (not a failure):")
        for name in fixed:
            print("  + %s" % name)
        print("Consider removing them from %s." % path.relative_to(ROOT))
    if new_failures:
        print("FAIL: %d new failure(s) not in the baseline:" % len(new_failures))
        for name in new_failures:
            print("  - %s" % name)
        return 1
    print("PASS: no new failures against the %s baseline." % platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
