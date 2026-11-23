"""The workbench's contract with the engine, checked against the engine.

The workbench drives two of the engine's own things:

* the Review Gate (its node in the graph, and its routes for the
  pending review and the decision), and
* the reassembly (two nodes: the Checkpoint Manager and Assemble).

Both contracts live in the engine, not here. These tests read the
engine's own source - its node definitions and the routes it registers
- and check that what the workbench builds and sends still fits.

Everything here is synthetic: no ComfyUI, no render, no files.
"""

from __future__ import annotations

import json

import pytest

from engine_source import (allowed_values, known_inputs, load_engine,
                            node_class, node_types)
from webapp.services import review, runner, workflow


@pytest.fixture(scope="module")
def engine():
    return load_engine()


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #

def test_the_engine_registers_the_routes_the_workbench_calls(engine):
    """The workbench does not answer a review itself: it asks the
    engine's own route. If the engine renamed one, the workbench would
    be asking a question nobody answers."""
    from engine_source import ROUTES

    registered = set(ROUTES)
    assert ("GET", review.REVIEWS_ROUTE) in registered
    assert ("POST", review.REVIEW_ROUTE) in registered
    assert ("GET", review.CHECKPOINTS_ROUTE) in registered
    assert ("POST", review.CANDIDATE_BATCH_ROUTE) in registered


def test_the_workbench_uses_the_engines_own_route_names():
    """The route names are part of the contract: they are the engine's,
    not the workbench's."""
    assert review.REVIEWS_ROUTE == "/minimax_h3_context_loop/reviews"
    assert review.REVIEW_ROUTE == "/minimax_h3_context_loop/review"
    assert review.CHECKPOINTS_ROUTE == "/minimax_h3_context_loop/checkpoints"


def test_the_decision_uses_a_name_the_engine_reads(engine):
    """The engine reads these field names out of the decision it is
    given. A different name would be a decision the engine ignores."""
    source = (engine_source_source()).read_text(encoding="utf-8")
    for field in ("token", "action", "candidate_revision", "scene_prompt",
                  "basic_prompt", "length"):
        assert '"%s"' % field in source, field


def engine_source_source():
    from engine_source import REPO_ROOT
    return REPO_ROOT / "chain_nodes.py"


# --------------------------------------------------------------------------- #
# the Review Gate node
# --------------------------------------------------------------------------- #

def test_the_review_gate_node_is_the_engines_own(engine):
    """The opt-in only flips a switch on the engine's node; the node
    itself has to be one the engine knows."""
    assert "MiniMaxH3ChainReview|MieH3" in engine.CHAIN_NODE_CLASS_MAPPINGS
    types = node_types(engine, "MiniMaxH3ChainReview|MieH3")
    known = known_inputs(types)
    assert "enabled" in known, "the engine's gate has an enabled switch"
    assert "candidate_count" in known


def test_the_opt_in_only_touches_switches_the_engine_has(engine):
    known = known_inputs(node_types(engine, "MiniMaxH3ChainReview|MieH3"))
    graph = workflow.build("测试剧本", {"version": 2, "roles": [], "shots": []},
                           {}, review={"enabled": True, "candidate_count": 3})
    node = next(node for node in graph.values()
                if node["class_type"] == "MiniMaxH3ChainReview|MieH3")
    for name in node["inputs"]:
        assert name in known, "%s is not an input of the engine's gate" % name
    assert node["inputs"]["enabled"] is True
    assert node["inputs"]["candidate_count"] == 3


def test_the_candidate_count_stays_inside_the_engines_range(engine):
    """The engine refuses a candidate count outside its own range. The
    workbench clamps to the same range instead of sending one the
    engine will reject."""
    spec = known_inputs(node_types(engine, "MiniMaxH3ChainReview|MieH3"))
    kind, options = spec["candidate_count"][0], spec["candidate_count"][1]
    assert kind == "INT"
    low, high = options.get("min"), options.get("max")
    for asked in (0, 1, 20, 999):
        graph = workflow.build("测试剧本",
                               {"version": 2, "roles": [], "shots": []}, {},
                               review={"enabled": True, "candidate_count": asked})
        node = next(node for node in graph.values()
                    if node["class_type"] == "MiniMaxH3ChainReview|MieH3")
        assert low <= node["inputs"]["candidate_count"] <= high


# --------------------------------------------------------------------------- #
# the two-node reassembly
# --------------------------------------------------------------------------- #

def _assembly_graph():
    return runner._assembly_graph(
        {"run_name": "run_a",
         "lineage": [{"scene": 1, "revision": "a" * 32},
                     {"scene": 2, "revision": "b" * 32}],
         "output_mode": "workflow_local",
         "output_scope": "project",
         "scope_start_scene": 1,
         "scope_end_scene": 2},
        "run_a")


def test_the_assembly_uses_nodes_the_engine_knows(engine):
    for node in _assembly_graph().values():
        assert node["class_type"] in engine.CHAIN_NODE_CLASS_MAPPINGS, \
            node["class_type"]


def test_the_assembly_only_names_inputs_the_engine_has(engine):
    """An input the engine does not know is a graph the engine refuses
    to run - and the workbench would say "this engine cannot do it"."""
    for node in _assembly_graph().values():
        known = known_inputs(node_types(engine, node["class_type"]))
        for name in node["inputs"]:
            assert name in known, "%s has no input %s" % (
                node["class_type"], name)


def test_the_assembly_only_sends_values_the_engine_accepts(engine):
    for node in _assembly_graph().values():
        known = known_inputs(node_types(engine, node["class_type"]))
        for name, value in node["inputs"].items():
            spec = known[name]
            allowed = allowed_values(spec)
            if allowed:
                assert value in allowed, "%s.%s=%r" % (
                    node["class_type"], name, value)
            kind = spec[0] if isinstance(spec, tuple) else spec
            if kind == "INT":
                options = spec[1]
                low = options.get("min")
                high = options.get("max")
                if low is not None:
                    assert value >= low
                if high is not None:
                    assert value <= high


def test_the_assembly_connects_the_manifest_to_the_manager(engine):
    """The Checkpoint Manager's output is the manifest the Assemble node
    reads: one link, from the right socket."""
    graph = _assembly_graph()
    manager = graph[runner._ID_MANAGER]
    assemble = graph[runner._ID_ASSEMBLE]
    manager_class = node_class(engine, manager["class_type"])
    assert manager_class.RETURN_NAMES == ("selected_manifest",)
    assert assemble["inputs"]["manifest"] == [runner._ID_MANAGER, 0]


def test_the_selection_speaks_the_engines_own_language(engine):
    """The Checkpoint Manager reads these keys out of the selection it
    is given. A selection in another dialect is refused."""
    source = engine_source_source().read_text(encoding="utf-8")
    selection = json.loads(
        _assembly_graph()[runner._ID_MANAGER]["inputs"]["selection_json"])
    for key in selection:
        assert '"%s"' % key in source, key


def test_the_selection_says_what_the_engine_expects(engine):
    """The engine's own rules: a read-only selection, a project scope
    and a contiguous lineage from scene 1."""
    selection = json.loads(
        _assembly_graph()[runner._ID_MANAGER]["inputs"]["selection_json"])
    assert selection["output_mode"] == "workflow_local"
    assert selection["output_scope"] == "project"
    assert selection["scope_start_scene"] == 1
    assert selection["scope_end_scene"] == len(selection["lineage"])
    scenes = [item["scene"] for item in selection["lineage"]]
    assert scenes == list(range(1, len(scenes) + 1))


def test_the_assembly_never_generates_a_frame(engine):
    """The promise of the mode: nothing in the graph can sample. The
    engine's own node classes are the yardstick - neither of them takes
    a model, and neither of them samples."""
    graph = _assembly_graph()
    for node in graph.values():
        assert node["class_type"] in runner._ASSEMBLY_NODE_TYPES
        klass = node_class(engine, node["class_type"])
        # The engine's own entry points: one passes the manifest on, the
        # other joins what was saved. Neither one samples.
        assert klass.FUNCTION in ("passthrough", "assemble"), klass.FUNCTION
        known = known_inputs(node_types(engine, node["class_type"]))
        for name, spec in known.items():
            kind = spec[0] if isinstance(spec, tuple) else spec
            assert "MODEL" not in str(kind).upper(), name


def test_the_accepted_takes_are_never_deleted_by_the_assembly(engine):
    """The saved takes are the only copy of those shots: the assembly
    keeps them."""
    known = known_inputs(node_types(engine, "MiniMaxH3ChainAssemble|MieH3"))
    assert "delete_checkpoints_after_assembly" in known
    graph = _assembly_graph()
    assert graph[runner._ID_ASSEMBLE]["inputs"][
        "delete_checkpoints_after_assembly"] is False


def test_the_engine_knows_the_assembly_nodes_by_both_names(engine):
    """The workbench accepts both the fork name and the plain one - the
    engine registers one of them, and the other is the older spelling."""
    mapping = engine.CHAIN_NODE_CLASS_MAPPINGS
    for name in runner._ASSEMBLY_NODE_TYPES:
        if name.endswith("|MieH3"):
            assert name in mapping, name


# --------------------------------------------------------------------------- #
# the decision payload
# --------------------------------------------------------------------------- #

def test_the_decision_payload_uses_the_engines_own_field_names(engine):
    """The engine reads the decision out of these fields. The workbench
    must not rename them."""
    source = engine_source_source().read_text(encoding="utf-8")
    for field in ("token", "action", "candidate_revision"):
        assert 'body.get("%s")' % field in source, field


def test_the_actions_the_workbench_offers_are_the_engines_own(engine):
    """'approve' and 'reroll' are the engine's actions."""
    source = engine_source_source().read_text(encoding="utf-8")
    for action in review.ACTIONS:
        assert '"%s"' % action in source, action


def test_the_approve_decision_carries_the_candidate_the_engine_checks(engine):
    """The engine matches the candidate by its revision - the exact
    identity the candidate list publishes."""
    source = engine_source_source().read_text(encoding="utf-8")
    assert 'body.get("candidate_revision")' in source
    assert '"revision"' in source
