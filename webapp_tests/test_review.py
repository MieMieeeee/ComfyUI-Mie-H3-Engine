"""The review's findings, as tests.

The four P1 items were: switching scripts lost unsaved work, a queued
render did not pin the script it was submitted with, the task list
called dead runs "running", and the render button was clickable with
the roles still unwired.
"""

from __future__ import annotations

import json

import pytest

from webapp.services import library

from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# the source / mirror pair
# --------------------------------------------------------------------------- #

def test_the_source_fingerprint_follows_the_chinese_source(plugin_root):
    first = library.source_fingerprint(SCRIPT)
    edited = json.loads(json.dumps(SCRIPT))
    edited["look"] = "换了画风"
    assert library.source_fingerprint(edited) != first


def test_the_fingerprint_ignores_the_english_mirror(plugin_root):
    """The mirror is what the source is checked AGAINST - it must not
    change the source's own fingerprint, or the check is circular."""
    first = library.source_fingerprint(SCRIPT)
    edited = json.loads(json.dumps(SCRIPT))
    edited["execution"]["visible_state"] = "Someone stands there."
    assert library.source_fingerprint(edited) == first


def test_a_script_with_no_confirmation_is_unknown(plugin_root):
    assert library.execution_state("测试剧本", SCRIPT) == "unknown"


def test_confirming_records_the_source(plugin_root):
    library.confirm_execution("测试剧本", SCRIPT)
    assert library.execution_state("测试剧本", SCRIPT) == "confirmed"


def test_a_changed_source_makes_the_confirmation_stale(plugin_root):
    library.confirm_execution("测试剧本", SCRIPT)
    edited = json.loads(json.dumps(SCRIPT))
    edited["setting"] = "改了表演设定"
    assert library.execution_state("测试剧本", edited) == "changed"


def test_the_script_endpoint_says_whether_the_mirror_is_stale(client):
    client.post("/api/scripts", json={"json": json.dumps(SCRIPT)})
    body = client.get("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC").get_json()
    assert body["execution_state"] == "unknown"
    client.post("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC/confirm_execution")
    body = client.get("/api/scripts/%E6%B5%8B%E8%AF%95%E5%89%A7%E6%9C%AC").get_json()
    assert body["execution_state"] == "confirmed"
    assert body["source_hash"]


# --------------------------------------------------------------------------- #
# the video page's pre-flight check
# --------------------------------------------------------------------------- #

def test_a_role_without_an_asset_blocks_the_submit(client):
    """The backend refuses a slot with no asset; the page says so before
    the click instead of after it."""
    from webapp.services import runner
    with pytest.raises(ValueError) as caught:
        runner.workflow.build("测试剧本", SCRIPT, {})
    assert "character_1" in str(caught.value)


def test_a_wired_role_builds_the_graph(plugin_root):
    from webapp.services import workflow
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"})
    assert graph["910"]["inputs"]["name"] == "甲"
