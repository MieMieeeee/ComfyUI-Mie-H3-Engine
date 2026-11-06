"""Run the new architecture's asset-to-plan path against the old one.

Both paths are zero-LLM and both go through the same shared builder, so
for the same script asset and the same character assets they must produce
the SAME plan - byte for byte. That is the property the split was built
on, and this is the check that says it still holds.

It submits to the running ComfyUI and reads the plan back out of the
history, so it exercises the real nodes, not a re-implementation.

Usage:
    python tools/compare_plan_paths.py [script-name]
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8188"
SCRIPT = "Claude的千层套路001-系列版"
CHARACTERS = ["凌澈", "星惑"]


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit("HTTP %s %s\n%s" % (exc.code, exc.reason, body[:2000]))


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read())


def run(graph, timeout=240):
    body = {"prompt": graph, "client_id": str(uuid.uuid4())}
    result = post("/prompt", body)
    if result.get("error"):
        raise SystemExit("提交被拒: " + json.dumps(result["error"], ensure_ascii=False))
    pid = result["prompt_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = get("/history/" + pid)
        if pid in hist:
            entry = hist[pid]
            if entry.get("status", {}).get("status_str") == "error":
                raise SystemExit("执行出错: " + json.dumps(
                    entry.get("status", {}).get("messages", []), ensure_ascii=False)[:800])
            return entry
        time.sleep(1.0)
    raise SystemExit("超时")


def text_outputs(entry):
    """Every text output of every node, flattened.

    ComfyUI's history stores a node's text as a LIST OF CHARACTERS, one
    string per character - so the pieces have to be joined back before
    anything can be parsed.
    """
    out = []
    for nid, node_out in (entry.get("outputs") or {}).items():
        for t in node_out.get("text", []):
            out.append(t if isinstance(t, str) else str(t))
    return out


def joined_texts(entry):
    """Each node's text, joined back into one string."""
    out = []
    for nid, node_out in (entry.get("outputs") or {}).items():
        pieces = node_out.get("text", [])
        if not pieces:
            continue
        out.append("".join(p if isinstance(p, str) else str(p) for p in pieces))
    return out


def main() -> int:
    script = sys.argv[1] if len(sys.argv) > 1 else SCRIPT

    # ---- the new architecture: the engine reads the script and
    #      assembles the plan -------------------------------------------- #
    new_graph = {
        "85": {"class_type": "MieH3LoadScriptAsset",
               "inputs": {"library_dir": "", "name": script}},
        "79": {"class_type": "LoadAssetV2|Mie",
               "inputs": {"kind": "character", "library_dir": "",
                          "name": CHARACTERS[0]}},
        "80": {"class_type": "LoadAssetV2|Mie",
               "inputs": {"kind": "character", "library_dir": "",
                          "name": CHARACTERS[1]}},
        "86": {"class_type": "MieH3ScriptToPlan",
               "inputs": {"storyboard": ["85", 0],
                          "character_1": ["79", 5],
                          "character_2": ["80", 5]}},
        "57": {"class_type": "AssetToSlots|Mie",
               "inputs": {"assets": ["86", 3]}},
        "90": {"class_type": "MieH3LoadAssetRoster",
               "inputs": {"library_dir": "", "kind": "character"}},
        # A non-output node's STRING is not saved to the history, so the
        # plan has to go through an output node to be read back.
        "50": {"class_type": "ShowAndSaveAnything|Mie",
               "inputs": {"save_to_log": False, "log_file_name": "",
                          "displaytext": "plan", "anything": ["86", 0]}},
    }
    print("跑新架构路径（引擎侧：MieH3LoadScriptAsset -> MieH3ScriptToPlan）...")
    new_entry = run(new_graph)
    new_texts = joined_texts(new_entry)

    # ---- the old path: the MieNodes pack reads the script and
    #      generates the plan -------------------------------------------- #
    old_graph = {
        "85": {"class_type": "LoadStoryboardAsset|Mie",
               "inputs": {"library_dir": "", "name": script}},
        "79": {"class_type": "LoadAssetV2|Mie",
               "inputs": {"kind": "character", "library_dir": "",
                          "name": CHARACTERS[0]}},
        "80": {"class_type": "LoadAssetV2|Mie",
               "inputs": {"kind": "character", "library_dir": "",
                          "name": CHARACTERS[1]}},
        "86": {"class_type": "MiniMaxH3LoopPromptGeneratorV3|Mie",
               "inputs": {"storyboard": ["85", 0],
                          "character_1": ["79", 5],
                          "character_2": ["80", 5]}},
        "50": {"class_type": "ShowAndSaveAnything|Mie",
               "inputs": {"save_to_log": False, "log_file_name": "",
                          "displaytext": "plan", "anything": ["86", 0]}},
    }
    print("跑旧路径（MieNodes 侧：LoadStoryboardAsset -> V3 生成器）...")
    old_entry = run(old_graph)
    old_texts = joined_texts(old_entry)

    def plan_of(texts):
        for t in texts:
            s = t.strip()
            if s.startswith("{"):
                try:
                    parsed = json.loads(s)
                except ValueError:
                    continue
                if isinstance(parsed, dict) and "shots" in parsed:
                    return parsed
        return None

    new_plan = plan_of(new_texts)
    old_plan = plan_of(old_texts)

    print()
    print("新架构 产出 plan:", "有" if new_plan else "无")
    print("旧路径 产出 plan:", "有" if old_plan else "无")
    if not new_plan or not old_plan:
        print("（拿不到计划，先看上面的错误）")
        print("新架构文本:", new_texts[:2])
        print("旧路径文本:", old_texts[:2])
        return 1

    new_bytes = json.dumps(new_plan, ensure_ascii=False, sort_keys=True)
    old_bytes = json.dumps(old_plan, ensure_ascii=False, sort_keys=True)
    same = new_bytes == old_bytes
    print("镜头数      :", len(new_plan.get("shots", [])))
    print("字节级一致  :", "是" if same else "否")
    if not same:
        for i, (a, b) in enumerate(zip(new_bytes, old_bytes)):
            if a != b:
                print("第一个差异在第 %d 字符" % i)
                print("  新:", new_bytes[max(0, i - 80):i + 80])
                print("  旧:", old_bytes[max(0, i - 80):i + 80])
                break
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
