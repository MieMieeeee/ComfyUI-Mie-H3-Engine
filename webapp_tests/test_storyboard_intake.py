"""The storyboard intake API: script mode through the real entry.

M0 of docs/plans/2026-11-11-storyboard-intake-plan.md. The web layer has
to hand the CONFIRMED source text to the generator's first positional
argument (the core swallows unknown keywords through ``**_unused``, so a
``source_script=`` keyword would vanish silently), pass every confirmed
header field explicitly, and keep the library untouched: a draft only
lands in the editor.

The connector is a stub that ECHOES what the generator actually sent:
it assembles a contract-valid asset out of the payload it received, so
a parameter that never made it through cannot come back looking right.
No real LLM is ever called.
"""

from __future__ import annotations

import json


# --------------------------------------------------------------------------- #
# the echoing stub
# --------------------------------------------------------------------------- #

class EchoScriptConnector:
    """Answers a script-mode call with the payload it was handed.

    The asset is rebuilt from ``asset_header`` and ``locked_events`` of
    the generator's own request, so the reply can only be valid if the
    adapter really put the confirmed content into the call. A reply that
    must be REJECTED can mutate one event (``corrupt``) - exactly the
    model-rewrote-the-words failure the contract has to catch.
    """

    timeout = 30

    def __init__(self, corrupt=None):
        self.payloads = []
        self.corrupt = corrupt  # (index, new text) or None

    def invoke(self, messages, **kwargs):
        user = next(m["content"] for m in messages if m["role"] == "user")
        payload = json.loads(user)
        self.payloads.append(payload)
        if payload.get("mode") != "script":
            return json.dumps({"version": 2}, ensure_ascii=False)
        header, events = payload["asset_header"], payload["locked_events"]
        if self.corrupt is not None:
            index, text = self.corrupt
            events = [dict(e) for e in events]
            events[index]["text"] = text
        return json.dumps(_echo_asset(header, events), ensure_ascii=False)


def _echo_asset(header, events, group=2):
    """A contract-valid asset built from the header and locked events."""
    groups = [events[i:i + group] for i in range(0, len(events), group)]
    shots = [{"id": "scene_%02d" % (n + 1), "duration_seconds": 6.0,
              "events": g} for n, g in enumerate(groups)]
    return dict(header, shots=shots, execution={
        "look": "clean modern look" if header.get("look") else "",
        "setting": "a small room" if header.get("setting") else "",
        "scene": "a cozy living room at dusk" if header.get("scene") else "",
        "visible_state": "Two people stand facing each other in the room.",
        "voices": {r["id"]: (r.get("voice") or "") for r in header["roles"]},
        "directions": {
            e["id"]: "Character %d stands up" % int(e["character"].split("_")[1])
            for e in events if e["type"] != "dialogue"},
    })


ROLES = [
    {"name": "小明", "presence": "on_screen", "position": "left"},
    {"name": "小红", "presence": "on_screen", "position": "right"},
]

#: ``role`` is the index into ROLES - the stable UI ids are the page's
#: business; what crosses the API is the position in the confirmed list.
EVENTS = [
    {"role": 0, "type": "dialogue", "text": "今天天气不错。", "language": "zh"},
    {"role": 1, "type": "action", "text": "她站起来看向窗外。", "language": "zh"},
    {"role": 0, "type": "dialogue", "text": "Hello there.", "language": "en"},
]

#: What the service layer has to serialize those events into: the
#: character id first (never a display name that could collide), the
#: type, the language tag only when it differs from the script language.
SERIALIZED = ("character_1 dialogue：今天天气不错。\n"
              "character_2 action：她站起来看向窗外。\n"
              "character_1 dialogue [en]：Hello there.")


def _post_script(client, monkeypatch, stub, **overrides):
    from webapp.services import autodraft
    monkeypatch.setattr(autodraft, "_connector", lambda: stub)
    body = {
        "mode": "script",
        "name": "草稿",
        "language": "zh",
        "roles": ROLES,
        "events": EVENTS,
        "shot_count": 0,
        "target_duration_seconds": 20,
        "audio_mode": "generated",
        "camera": {"framing": "medium_shot", "movement": "locked",
                   "perspective": "observer"},
        "look": "清新现代", "setting": "小房间", "scene": "傍晚的客厅",
        "constraints": "保持两人同框",
        "critic": "off",
    }
    body.update(overrides)
    return client.post("/api/scripts/auto", json=body)


# --------------------------------------------------------------------------- #
# the source text reaches the generator's first positional argument
# --------------------------------------------------------------------------- #

def test_the_source_text_reaches_the_first_argument_and_the_header_is_full(
        client, monkeypatch):
    stub = EchoScriptConnector()
    response = _post_script(client, monkeypatch, stub)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()

    # One generation call, and it was a script call.
    assert len(stub.payloads) == 1
    payload = stub.payloads[0]
    assert payload["mode"] == "script"
    # THE regression this file exists for: the confirmed events travel as
    # the first positional argument (``concept``), which is where the core
    # reads them into asset_header.source_script and locked_events. A
    # source_script= keyword would be swallowed by **_unused and the call
    # would fail on an empty concept instead.
    assert payload["asset_header"]["source_script"] == SERIALIZED
    assert [e["id"] for e in payload["locked_events"]] == \
        ["event_001", "event_002", "event_003"]
    assert payload["asset_header"]["name"] == "草稿"
    assert payload["asset_header"]["language"] == "zh"
    assert payload["asset_header"]["camera"] == {
        "framing": "medium_shot", "movement": "locked", "perspective": "observer"}
    assert payload["asset_header"]["look"] == "清新现代"
    assert payload["asset_header"]["setting"] == "小房间"
    assert payload["asset_header"]["scene"] == "傍晚的客厅"
    assert payload["asset_header"]["constraints"] == "保持两人同框"
    assert payload["asset_header"]["target_duration_seconds"] == 20.0
    assert payload["asset_header"]["roles"][0]["name"] == "小明"

    # The draft comes back with the normalized events for the page to
    # cross-check against what the user confirmed.
    assert [e["id"] for e in data["events"]] == \
        ["event_001", "event_002", "event_003"]
    assert data["events"][2]["language"] == "en"
    assert data["json"]["name"] == "草稿"


def test_an_unknown_mode_is_a_field_error(client):
    response = client.post("/api/scripts/auto", json={
        "mode": "free", "concept": "随便"})
    assert response.status_code == 400
    assert "mode" in response.get_json()["error"]


# --------------------------------------------------------------------------- #
# round-trip: structured events -> source text -> the shared parser
# --------------------------------------------------------------------------- #

def test_a_confirmed_event_list_round_trips_through_the_parser(
        client, monkeypatch):
    stub = EchoScriptConnector()
    response = _post_script(client, monkeypatch, stub)
    assert response.status_code == 200, response.get_json()
    events = response.get_json()["events"]
    assert [(e["type"], e["character"], e["text"], e["language"])
            for e in events] == [
        ("dialogue", "character_1", "今天天气不错。", "zh"),
        ("action", "character_2", "她站起来看向窗外。", "zh"),
        ("dialogue", "character_1", "Hello there.", "en"),
    ]


def test_an_event_with_an_internal_newline_is_refused_before_the_call(
        client, monkeypatch):
    stub = EchoScriptConnector()
    events = [dict(e) for e in EVENTS]
    events[0] = dict(events[0], text="第一行\n第二行")
    response = _post_script(client, monkeypatch, stub, events=events)
    assert response.status_code == 400
    assert "换行" in response.get_json()["error"]
    assert not stub.payloads  # refused before any provider call


def test_a_role_name_with_a_separator_is_refused_before_the_call(
        client, monkeypatch):
    stub = EchoScriptConnector()
    roles = [dict(r) for r in ROLES]
    roles[0] = dict(roles[0], name="小明|小红")
    response = _post_script(client, monkeypatch, stub, roles=roles)
    assert response.status_code == 400
    assert "小明" in response.get_json()["error"]
    assert not stub.payloads


def test_shot_count_above_the_event_count_is_refused_before_the_call(
        client, monkeypatch):
    stub = EchoScriptConnector()
    response = _post_script(client, monkeypatch, stub, shot_count=5)
    assert response.status_code == 400
    assert "shot_count" in response.get_json()["error"]
    assert not stub.payloads


# --------------------------------------------------------------------------- #
# a model that rewrites the confirmed words is rejected
# --------------------------------------------------------------------------- #

def test_a_model_that_rewrites_a_line_is_rejected(client, monkeypatch):
    stub = EchoScriptConnector(corrupt=(0, "今天天气很好。"))
    response = _post_script(client, monkeypatch, stub)
    assert response.status_code == 400
    # The contract names the exact event that was rewritten.
    assert "source events differ" in response.get_json()["error"]
    assert "今天天气不错" in response.get_json()["error"]


# --------------------------------------------------------------------------- #
# a draft never writes to the library
# --------------------------------------------------------------------------- #

def test_a_script_draft_writes_nothing_to_the_library(
        client, monkeypatch):
    stub = EchoScriptConnector()
    response = _post_script(client, monkeypatch, stub)
    assert response.status_code == 200, response.get_json()
    assert client.get("/api/scripts").get_json()["names"] == []
