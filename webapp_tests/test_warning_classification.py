"""The render risks, classified by the server.

The contract hands out a sentence; the page shows a short label, how
serious it is and what to do about it. That translation lives on the
server now — the page shows what the server says, so the two cannot
drift apart.

These tests pin down the three things that must not change: the
severity order, the fallback for a diagnostic nobody recognises, and
the fact that the page no longer keeps its own copy of the table.
"""

from __future__ import annotations

import pathlib

import pytest

from webapp.services import library, warnings

WEBAPP = pathlib.Path(__file__).resolve().parents[1] / "webapp"

PUNCTUATION = ("scene_02 台词「？？？」不含可发音字符：H3 会把它渲染成提示音"
               "而非沉默 · punctuation-only dialogue renders as a notification chime")
FLOOR = ("scene_01 预算贴地：扣除续接重叠后 1.00 秒要装约 1.20 秒台词与动作，"
         "收尾节拍最可能被截 · shot budget at the floor")
ZERO_BUFFER = ("target_duration_seconds 恰好压在各片段最小可行预算上（零缓冲）"
               " · duration pinned at the feasibility floor")
CROWDS = ("画内角色 4 名：未接参考图时 H3 倾向把全员提前画进开场帧"
          " · text-only crowds leak into the opening frame")
UNKNOWN = "一条谁也不认识的诊断"


def test_a_defect_is_more_serious_than_a_budget_risk():
    assert warnings.classify(PUNCTUATION)["level"] == "err"
    assert warnings.classify(FLOOR)["level"] == "warn"
    assert warnings.classify(CROWDS)["level"] == "info"


def test_the_severity_order_is_the_one_the_page_shows():
    assert warnings.LEVELS == ("err", "warn", "info")
    assert warnings.LEVEL_ORDER["err"] < warnings.LEVEL_ORDER["warn"]
    assert warnings.LEVEL_ORDER["warn"] < warnings.LEVEL_ORDER["info"]


def test_the_risks_are_sorted_by_severity():
    items = warnings.classify_all([CROWDS, FLOOR, PUNCTUATION])
    assert [item["level"] for item in items] == ["err", "warn", "info"]


def test_every_risk_has_a_label_a_fix_and_a_kind():
    """Without a label the chip says nothing; without a kind the button
    does not know whether to change the duration or to ask the model."""
    for text in (PUNCTUATION, FLOOR, ZERO_BUFFER, CROWDS, UNKNOWN):
        item = warnings.classify(text)
        assert item["label"], text
        assert item["fix"], text
        assert item["kind"] in ("model", "duration"), text
        assert item["level"] in warnings.LEVELS, text


def test_an_unknown_diagnostic_still_gets_a_label():
    """A diagnostic nobody knows is still shown - as a risk, not as
    nothing. Guessing wrong is better than saying nothing."""
    item = warnings.classify(UNKNOWN)
    assert item["label"] == "渲染风险"
    assert item["level"] == "warn"
    assert item["kind"] == "model"
    assert item["fix"]


def test_the_shot_is_read_out_of_the_diagnostic():
    assert warnings.classify(FLOOR)["shot"] == "scene_01"
    assert warnings.classify(CROWDS)["shot"] == ""


def test_a_duration_risk_is_fixed_by_the_clock_not_by_the_model():
    """The button says what it will do: a duration can be computed, a
    staging problem has to be rewritten."""
    assert warnings.classify(FLOOR)["kind"] == "duration"
    assert warnings.classify(PUNCTUATION)["kind"] == "model"


def test_the_chips_are_grouped_and_ordered_by_severity():
    chips = warnings.chips(warnings.classify_all([CROWDS, FLOOR, PUNCTUATION]))
    assert [chip["label"] for chip in chips] == ["标点台词变提示音", "预算贴地",
                                                 "群像开场泄漏"]
    assert chips[0]["count"] == 1


def test_one_chip_counts_all_the_risks_of_its_kind():
    items = warnings.classify_all([FLOOR, FLOOR])
    chips = warnings.chips(items)
    assert chips[0]["count"] == 2


def test_the_validation_answer_carries_the_classification():
    """The page gets the classification with the validation, so it does
    not have to work it out again."""
    from test_library import SCRIPT

    result = library.validate_storyboard(dict(SCRIPT))
    assert "warning_details" in result
    assert "warning_chips" in result
    assert result["warning_levels"] == ["err", "warn", "info"]
    assert result["warning_source"] in ("mie_craft", "workbench")
    for item in result["warning_details"]:
        assert item["label"] and item["level"] and item["fix"]


def test_the_validation_answer_classifies_a_real_risk():
    """A line with nothing to say is a real defect: the contract says so
    and the page says so in the same words."""
    import json

    from test_library import SCRIPT

    script = json.loads(json.dumps(SCRIPT))
    script["source_script"] = "甲：？？？"
    script["shots"][0]["events"][0]["text"] = "？？？"
    result = library.validate_storyboard(script)
    assert result["ok"], result["errors"]
    labels = {item["label"] for item in result["warning_details"]}
    assert "标点台词变提示音" in labels, result["warning_details"]
    levels = {item["level"] for item in result["warning_details"]}
    assert "err" in levels, result["warning_details"]


def test_the_page_does_not_keep_its_own_table():
    """The table lives on the server now. A copy in the browser would be
    a second truth about what is serious."""
    for path in sorted((WEBAPP / "static" / "js").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "punctuation-only dialogue" not in text, path.name
        assert "WARNING_META" not in text, path.name
        assert "WARNING_LEVEL_ORDER" not in text, path.name


def test_the_table_is_read_from_the_shared_contract_when_it_is_there():
    """The contract may learn to classify the risks itself. When it
    does, the workbench uses its table instead of its own."""
    from mie_craft.script import contract

    original = getattr(contract, "WARNING_CLASSIFICATION", None)
    try:
        contract.WARNING_CLASSIFICATION = {
            "shot budget at the floor": ("契约自己说的", "err",
                                         "duration", "按契约说的改"),
        }
        assert warnings.source() == "mie_craft"
        item = warnings.classify(FLOOR)
        assert item["label"] == "契约自己说的"
        assert item["level"] == "err"
    finally:
        if original is None:
            del contract.WARNING_CLASSIFICATION
        else:
            contract.WARNING_CLASSIFICATION = original
    assert warnings.source() == "workbench"


def test_the_workbench_table_is_used_when_the_contract_has_none():
    assert warnings.source() == "workbench"
    assert warnings.classify(PUNCTUATION)["label"] == "标点台词变提示音"


@pytest.mark.parametrize("text", [PUNCTUATION, FLOOR, ZERO_BUFFER, CROWDS])
def test_every_contract_risk_has_a_classification(text):
    """Every diagnostic the contract can produce is classified - a risk
    the page cannot name is a risk the page cannot act on."""
    item = warnings.classify(text)
    assert item["label"] != "渲染风险", text
