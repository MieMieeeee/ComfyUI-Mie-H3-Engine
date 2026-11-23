#!/usr/bin/env python3
"""The plan contract is ONE rule, not two half-implementations.

The engine's chain plan and the authoring package's plan share the
shot-list rule (a JSON object with 1..128 shots), so the engine calls
the package's own helper instead of keeping a copy. The package's
SECTION rules stay with the package: a chain plan may carry a plain
prompt string per shot, and the whole document may be a bare list - the
workbench's own fallback plan looks exactly like that.

Three things are pinned here:

  1. the supported path IS the package's helper (no drift);
  2. the fallback says the same thing in the same words, so the message
     does not depend on which ``mie_craft`` version is installed;
  3. the engine still accepts the dialect the package refuses - the
     differences between the two plans are deliberate.
"""

import importlib.util
import json
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plan_contract  # noqa: E402


PACKAGE = "h3_mie_plan_contract_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test"
shared_nodes._prepare_native_guide_conditioning = lambda value: value
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


SHOT = {"id": "one", "prompt": "First scene.", "length": 73}

#: The documents the shared rule has to judge the same way on both sides.
CASES = [
    {"shots": [SHOT]},
    {"shots": []},
    {"shots": [{"id": "s%d" % i, "prompt": "p", "length": 73}
               for i in range(129)]},
    {"shots": "not a list"},
    "not an object at all",
]


def _normalize(plan_json: str):
    return chain._normalize_plan(
        plan_json, "plan-contract-test", 64, 64, 22,
        "video", "head", "disabled", "generated_audio",
        22, 3.0, 8, 7, 18, "model-stack", 0, "guide", None)


def _refused(plan_json: str) -> str:
    try:
        _normalize(plan_json)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("the plan was accepted: %s" % plan_json[:80])


def main() -> None:
    # 1. The supported path is the package's helper. Without it the
    #    engine would keep its own copy of the rule - the drift the
    #    consolidation is about.
    helper = plan_contract.craft_shot_list_errors()
    assert helper is not None, (
        "the installed mie_craft has no shot_list_errors: the shared rule "
        "cannot be delegated - install a newer comfyui-mie-craft")
    for case in CASES:
        assert plan_contract.shot_list_errors(case) == list(helper(case)), case

    # 2. The fallback (an installation whose mie_craft is too old) says
    #    the same thing in the same words.
    original = plan_contract.craft_shot_list_errors
    try:
        plan_contract.craft_shot_list_errors = lambda: None
        for case in CASES:
            fallback = plan_contract.shot_list_errors(case)
            supported = list(helper(case))
            assert fallback == supported, (fallback, supported)
    finally:
        plan_contract.craft_shot_list_errors = original

    # The shared sentence is the one both sides use.
    assert plan_contract.shot_list_errors({"shots": []}) == [
        "shots must be a list of 1..128 entries"]

    # 3. The engine's dialect: a bare list of shots, and a plain prompt
    #    string per shot. The package's section rules do NOT apply to a
    #    chain plan - moving them here would refuse every plan the
    #    workbench assembles.
    plan = _normalize(json.dumps({"shots": [SHOT]}))
    assert plan["shots"], plan
    shorthand = _normalize(json.dumps([SHOT]))
    assert shorthand["shots"], shorthand

    from mie_craft.h3_loop_prompts import validate_plan
    assert validate_plan({"shots": [SHOT]}), (
        "the package's section rules must NOT accept a chain plan - the "
        "two dialects are different on purpose")

    # 4. What the shared rule refuses, the engine refuses - with the
    #    shared sentence.
    assert "shots must be a list of 1..128 entries" in _refused(
        json.dumps({"shots": []}))
    assert "shots must be a list of 1..128 entries" in _refused(
        json.dumps({"shots": [{"id": "s%d" % i, "prompt": "p", "length": 73}
                              for i in range(129)]}))

    # 5. The negative control: the engine follows the SHARED rule. When
    #    the package's rule changes, the engine changes with it - the
    #    old copy of the rule did not, and refused on its own limits
    #    after the shared rule had already passed.
    import mie_craft.h3_loop_prompts as craft_prompts
    original = craft_prompts.shot_list_errors
    try:
        craft_prompts.shot_list_errors = lambda raw: []
        many = [{"id": "s%d" % i, "prompt": "p", "length": 73}
                for i in range(129)]
        assert len(_normalize(json.dumps({"shots": many}))["shots"]) == 129
    finally:
        craft_prompts.shot_list_errors = original

    print("plan contract: ok")


if __name__ == "__main__":
    main()
