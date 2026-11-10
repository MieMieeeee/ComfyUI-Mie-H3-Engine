"""Run names: the app must hand the engine a name it accepts verbatim.

The engine's ``_strict_run_name`` refuses to rewrite a name it cannot
take as-is, so an illegal name is an error at submit time - not a
silent redirection. The obligation is the app's, and most script names
in a Chinese library have no ASCII in them at all.
"""

from __future__ import annotations

import re

from webapp.services import runname, workflow

from test_library import SCRIPT

#: The engine's rule, restated: 1-96 chars, letters/numbers/./_/-,
#: starting and ending with a letter or a number.
_ENGINE_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,94}[A-Za-z0-9]$|^[A-Za-z0-9]$")


def assert_acceptable(value: str) -> None:
    assert value, "the run name must not be empty"
    assert len(value) <= 96, value
    assert _ENGINE_RULE.match(value), value
    assert value == value.strip("._-"), value


def test_a_chinese_script_name_still_gets_a_legal_run_name():
    name = runname.effective_run_name("", "股神")
    assert_acceptable(name)


def test_two_chinese_scripts_never_share_one_run():
    """Without a digest every Chinese name would collapse to the same
    fallback and the second render would overwrite the first."""
    assert runname.effective_run_name("", "股神") != \
        runname.effective_run_name("", "宠物专车")


def test_the_same_script_always_gets_the_same_run():
    """Re-rendering replaces the run instead of scattering copies."""
    assert runname.effective_run_name("", "股神") == \
        runname.effective_run_name("", "股神")


def test_the_ascii_part_of_a_mixed_name_is_kept():
    # The engine's rule: every run of illegal characters becomes ONE
    # underscore, so "Claude的千层套路001-系列版" keeps "Claude" and "001".
    assert runname.effective_run_name("", "Claude的千层套路001-系列版") == \
        "sb_web_Claude_001"


def test_a_typed_name_wins():
    assert runname.effective_run_name("my_run-1", "股神") == "my_run-1"


def test_a_typed_name_that_is_all_illegal_falls_back():
    """A name the engine would refuse must not reach the engine."""
    assert_acceptable(runname.effective_run_name("我的运行", "股神"))
    assert runname.effective_run_name("我的运行", "股神") == \
        runname.effective_run_name("", "股神")


def test_a_long_name_is_cut_but_stays_legal():
    name = runname.effective_run_name("x" * 200, "股神")
    assert len(name) == 96
    assert_acceptable(name)


def test_the_graph_carries_a_name_the_engine_accepts(plugin_root):
    graph = workflow.build("股神", SCRIPT, {"character_1": "甲"})
    plan_node = graph["3"]
    assert_acceptable(plan_node["inputs"]["run_name"])


def test_the_file_name_follows_the_same_run(plugin_root):
    graph = workflow.build("股神", SCRIPT, {"character_1": "甲"})
    plan_node = graph["3"]
    assembler = next(n for n in graph.values()
                     if n["class_type"].endswith("ChainAssemble|MieH3"))
    assert assembler["inputs"]["filename"].startswith(
        plan_node["inputs"]["run_name"])
