"""The versions of a script.

Every save makes a new version and keeps the one it replaced. The
point of these tests is that the old version is still there, readable,
and that the engine is not disturbed by the history.
"""

from __future__ import annotations

import json

import pytest

from webapp.services import library, versions

from test_library import SCRIPT


NAME = "测试剧本"


def _script(text: str = "你好") -> dict:
    """The fixture script, saying something else."""
    script = json.loads(json.dumps(SCRIPT))
    script["source_script"] = "甲：" + text
    script["shots"][0]["events"][0]["text"] = text
    return script


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #

def test_saving_twice_keeps_the_first_version(plugin_root):
    """The copy that was replaced is still there, exactly as it was."""
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    listing = versions.list_versions(NAME)
    assert listing["current"] == 2, listing
    assert [item["number"] for item in listing["items"]] == [2, 1], listing

    old = versions.read_version(NAME, 1)
    assert old["source_script"] == "甲：你好", old["source_script"]
    assert versions.read_version(NAME, 2)["source_script"] == "甲：改过的台词"

    # 现在的当前版就是引擎读的那份。
    assert library.get_storyboard(NAME)["json"]["source_script"] == "甲：改过的台词"


def test_the_note_says_how_the_version_came_about(plugin_root):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")
    notes = {item["number"]: item["note"]
             for item in versions.list_versions(NAME)["items"]}
    assert notes[2] == "AI 修订", notes


def test_saving_the_same_content_makes_no_version(plugin_root):
    """Saving the same thing again is not a new version."""
    library.save_storyboard(_script("你好"), note="手工修改")
    result = library.save_storyboard(_script("你好"), note="又存了一次")
    assert result["status"] == "unchanged", result
    assert result["content_version"] == 1, result
    assert len(versions.list_versions(NAME)["items"]) == 1


def test_restoring_an_old_version_makes_a_new_one(plugin_root):
    """Restoring does not move a pointer back: the old content is
    saved as a new version, so the history only grows."""
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    result = versions.restore_version(NAME, 1)
    assert result["content_version"] == 3, result

    listing = versions.list_versions(NAME)
    assert listing["current"] == 3, listing
    assert [item["number"] for item in listing["items"]] == [3, 2, 1], listing
    assert library.get_storyboard(NAME)["json"]["source_script"] == "甲：你好"
    # 恢复出来的那一版，来历要写清楚。
    notes = {item["number"]: item["note"] for item in listing["items"]}
    assert notes[3] == "恢复自版本 1", notes


def test_an_archived_version_can_be_deleted(plugin_root):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    versions.delete_version(NAME, 1)
    listing = versions.list_versions(NAME)
    assert [item["number"] for item in listing["items"]] == [2], listing
    with pytest.raises(FileNotFoundError):
        versions.read_version(NAME, 1)


def test_the_current_version_cannot_be_deleted_that_way(plugin_root):
    """Deleting the current version would mean deleting the script."""
    library.save_storyboard(_script("你好"), note="手工修改")
    with pytest.raises(ValueError):
        versions.delete_version(NAME, 1)
    assert library.get_storyboard(NAME)["json"]["source_script"] == "甲：你好"


def test_a_script_that_was_never_edited_has_one_version(plugin_root):
    """An old script has no history: it is still one version - the one
    on disk."""
    library.save_storyboard(_script("你好"), note="手工修改")
    listing = versions.list_versions(NAME)
    assert listing["current"] == 1, listing
    assert len(listing["items"]) == 1, listing

    # 第一次保存（改了内容）之后，旧的那份成了版本 1，新的成了版本 2。
    library.save_storyboard(_script("改过的台词"), note="手工修改")
    listing = versions.list_versions(NAME)
    assert listing["current"] == 2, listing
    assert versions.read_version(NAME, 1)["source_script"] == "甲：你好"


def test_the_engine_is_not_disturbed_by_the_history(plugin_root):
    """The engine only ever reads storyboard.json. The versions live
    beside it, and it must not notice them - or trip over them."""
    from mie_craft.assets import script_asset

    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    listed = script_asset.list_storyboards(library.library_dir())
    assert NAME in listed["names"], listed
    assert listed["status"] == "ok", listed["status"]
    assert library.list_storyboards() == [NAME]
    assert library.get_storyboard(NAME)["json"]["source_script"] == "甲：改过的台词"


def test_a_board_the_contract_refuses_writes_no_version(plugin_root):
    """A rejected board must not leave a version behind."""
    broken = _script("你好")
    broken["shots"][0]["events"][0]["text"] = "别的"
    with pytest.raises(ValueError):
        library.save_storyboard(broken, note="手工修改")
    assert library.list_storyboards() == []
    assert not versions.versions_dir(NAME).exists()


def test_the_list_says_which_version_a_script_is_at(plugin_root):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")
    row = next(item for item in library.list_storyboards_meta()
               if item["name"] == NAME)
    assert row["content_version"] == 2, row


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #

def test_the_page_can_ask_for_the_versions(client):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    listing = client.get("/api/scripts/%s/versions" % NAME).get_json()
    assert listing["current"] == 2, listing
    assert [item["number"] for item in listing["items"]] == [2, 1], listing


def test_the_page_can_read_one_version(client):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    one = client.get("/api/scripts/%s/versions/1" % NAME).get_json()
    assert one["number"] == 1, one
    assert one["json"]["source_script"] == "甲：你好", one


def test_the_page_can_restore_and_delete_a_version(client):
    library.save_storyboard(_script("你好"), note="手工修改")
    library.save_storyboard(_script("改过的台词"), note="AI 修订")

    restored = client.post("/api/scripts/%s/versions/1/restore" % NAME).get_json()
    assert restored["content_version"] == 3, restored
    assert library.get_storyboard(NAME)["json"]["source_script"] == "甲：你好"

    deleted = client.delete("/api/scripts/%s/versions/2" % NAME).get_json()
    assert deleted["ok"] is True, deleted
    listing = client.get("/api/scripts/%s/versions" % NAME).get_json()
    assert [item["number"] for item in listing["items"]] == [3, 1], listing


def test_the_page_refuses_to_delete_the_current_version(client):
    library.save_storyboard(_script("你好"), note="手工修改")
    response = client.delete("/api/scripts/%s/versions/1" % NAME)
    assert response.status_code == 404, response.get_json()
    assert "等于删掉这份剧本" in response.get_json()["error"]


def test_saving_through_the_page_records_the_version(client):
    first = client.post("/api/scripts",
                        json={"json": json.dumps(_script("你好")),
                              "note": "手工修改"}).get_json()
    assert first["content_version"] == 1, first
    second = client.post("/api/scripts",
                         json={"json": json.dumps(_script("改过的台词")),
                               "note": "AI 修订"}).get_json()
    assert second["content_version"] == 2, second
    assert second["status"] == "overwritten", second
