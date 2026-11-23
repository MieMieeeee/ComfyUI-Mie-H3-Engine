"""Live render progress: the event stream, the state, and the page.

The progress of a render is not a guess - ComfyUI publishes it. These
tests drive the workbench's reader with a realistic ComfyUI event
sequence and check what the pages are told.

Nothing here touches a real ComfyUI: the websocket is a fake server on
a throwaway port, and the reader is injected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

import pytest

from webapp.services import comfy_ws, progress


# --------------------------------------------------------------------------- #
# a fake ComfyUI websocket
# --------------------------------------------------------------------------- #

#: The magic value the websocket protocol asks for in the handshake.
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _fake_ws_server(messages, close_after=True):
    """A websocket server that sends ``messages`` and hangs up.

    It speaks the real handshake - including the accept key, which the
    client checks - so the client is exercised for real.
    """
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def frame(payload: bytes) -> bytes:
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        return bytes(header) + payload

    def serve():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk
            key = ""
            for line in request.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1(
                (key + WS_GUID).encode("ascii")).digest()).decode("ascii")
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n")
            for message in messages:
                conn.sendall(frame(json.dumps(message).encode("utf-8")))
                time.sleep(0.01)
            if close_after:
                time.sleep(0.05)
                conn.close()
        except OSError:
            pass
        finally:
            try:
                server.close()
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def test_the_websocket_client_reads_what_comfyui_sends():
    """A real websocket handshake against a real socket: the client's
    own parser would not catch a broken handshake."""
    events = [
        {"type": "status", "data": {"status": {"queue_remaining": 0}}},
        {"type": "executing", "data": {"node": "13", "prompt_id": "p-1"}},
        {"type": "progress", "data": {"value": 3, "max": 20, "node": "13",
                                      "prompt_id": "p-1"}},
    ]
    port, _thread = _fake_ws_server(events)
    received = list(comfy_ws.messages(
        "ws://127.0.0.1:%d/ws?clientId=c1" % port))
    assert [event["type"] for event in received] == ["status", "executing",
                                                     "progress"]
    assert received[1]["data"]["node"] == "13"
    assert received[2]["data"]["value"] == 3


def test_the_websocket_client_gives_up_when_the_answer_is_not_a_websocket():
    """A server that answers with plain HTTP must not look like a stream."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _ = server.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
            conn.close()
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(comfy_ws.WebSocketUnavailable):
        list(comfy_ws.messages("ws://127.0.0.1:%d/ws" % port))


def test_a_connection_that_fails_is_not_reported_as_progress():
    """Nothing is listening: the reader has to hear about it."""
    with pytest.raises(comfy_ws.WebSocketUnavailable):
        list(comfy_ws.messages("ws://127.0.0.1:9/ws"))


# --------------------------------------------------------------------------- #
# the state the pages see
# --------------------------------------------------------------------------- #

def _hub_with(connect=None):
    return progress.ProgressHub(connect=connect)


def test_a_registered_prompt_is_queued_and_known():
    hub = _hub_with()
    hub.register("p-1", "cid-1", run_name="sb_run", script="剧本",
                 title="剧本 · 第 1 版", shot_total=12,
                 nodes={"13": "SamplerCustomAdvanced",
                        "25": "MiniMaxH3ChainSegmentSave|MieH3"})
    state = hub.snapshot("p-1")
    assert state["status"] == "queued"
    assert state["run_name"] == "sb_run"
    assert state["shot_total"] == 12
    assert hub.snapshot()["prompt_id"] == "p-1", "the newest prompt is the active one"


def test_the_events_of_a_known_prompt_move_the_state():
    hub = _hub_with()
    hub.register("p-1", "cid-1", shot_total=12,
                 nodes={"13": "SamplerCustomAdvanced",
                        "25": "MiniMaxH3ChainSegmentSave|MieH3"})
    hub.handle_event({"type": "executing",
                      "data": {"node": "13", "prompt_id": "p-1"}})
    state = hub.snapshot("p-1")
    assert state["status"] == "running"
    assert state["node"] == "13"
    assert state["node_label"] == "采样"

    hub.handle_event({"type": "progress",
                      "data": {"value": 8, "max": 20, "node": "13",
                               "prompt_id": "p-1"}})
    state = hub.snapshot("p-1")
    assert state["step"] == 8
    assert state["step_total"] == 20
    assert "8/20" in state["message"]


def test_a_finished_shot_moves_the_shot_counter():
    hub = _hub_with()
    hub.register("p-1", "cid-1", shot_total=3,
                 nodes={"25": "MiniMaxH3ChainSegmentSave|MieH3"})
    hub.handle_event({"type": "executing",
                      "data": {"node": "25", "prompt_id": "p-1"}})
    hub.handle_event({"type": "executed",
                      "data": {"node": "25", "prompt_id": "p-1"}})
    assert hub.snapshot("p-1")["shot"] == 2, \
        "one shot is done, so the second one is being made"


def test_a_finished_prompt_is_terminal():
    hub = _hub_with()
    hub.register("p-1", "cid-1", shot_total=2)
    hub.handle_event({"type": "executing",
                      "data": {"node": None, "prompt_id": "p-1"}})
    state = hub.snapshot("p-1")
    assert state["status"] == "success"
    assert state["shot"] == 2


def test_a_message_about_somebody_elses_prompt_is_ignored():
    """ComfyUI is shared. A message about another prompt is not ours."""
    hub = _hub_with()
    hub.register("p-1", "cid-1")
    hub.handle_event({"type": "executing",
                      "data": {"node": "13", "prompt_id": "somebody-else"}})
    assert hub.snapshot("p-1")["node"] == ""
    assert hub.snapshot("somebody-else") == {}


def test_a_late_message_of_the_previous_run_does_not_touch_the_new_one():
    """Two renders in a row: the first one's leftovers must not show up
    on the second one."""
    hub = _hub_with()
    hub.register("p-1", "cid-1", shot_total=4)
    hub.register("p-2", "cid-2", shot_total=4)
    hub.handle_event({"type": "executing",
                      "data": {"node": None, "prompt_id": "p-1"}})
    hub.handle_event({"type": "progress",
                      "data": {"value": 3, "max": 9, "node": "13",
                               "prompt_id": "p-1"}})
    new_state = hub.snapshot("p-2")
    assert new_state["node"] == ""
    assert new_state["step"] is None
    assert hub.snapshot("p-1")["status"] == "success", \
        "the finished render stays finished"


def test_an_unknown_field_is_left_out_rather_than_made_up():
    """What cannot be inferred must not be invented."""
    hub = _hub_with()
    hub.register("p-1", "cid-1")
    hub.handle_event({"type": "executing",
                      "data": {"node": "13", "prompt_id": "p-1"}})
    state = hub.snapshot("p-1")
    assert state["shot"] is None
    assert state["shot_total"] is None
    assert state["step"] is None


def test_the_history_verdict_wins_over_a_dropped_event():
    """The event stream can lose a message; the history cannot."""
    hub = _hub_with()
    hub.register("p-1", "cid-1")
    hub.set_status("p-1", "error", "渲染失败：显存不足")
    state = hub.snapshot("p-1")
    assert state["status"] == "error"
    assert "显存不足" in state["message"]


# --------------------------------------------------------------------------- #
# the reader: one subscription, reconnecting, cleaning up
# --------------------------------------------------------------------------- #

def test_one_subscription_serves_every_page():
    calls = []

    def connect(client_id, should_stop):
        calls.append(client_id)
        while not should_stop():
            time.sleep(0.01)
        return iter(())

    hub = _hub_with(connect=connect)
    first = hub.subscribe()
    second = hub.subscribe()
    hub.register("p-1", "cid-1")
    hub.publish()
    assert first.get(timeout=2)["active"] == "p-1"
    assert second.get(timeout=2)["active"] == "p-1"
    time.sleep(0.2)
    assert len(calls) == 1, "one subscription, not one per page"
    hub.forget("p-1")
    hub.stop()


def test_the_reader_reconnects_when_the_stream_drops():
    attempts = []

    def connect(client_id, should_stop):
        attempts.append(client_id)
        if len(attempts) == 1:
            raise OSError("the stream dropped")
        while not should_stop():
            time.sleep(0.01)
        return iter(())

    hub = _hub_with(connect=connect)
    hub.register("p-1", "cid-1")
    deadline = time.time() + 5
    while len(attempts) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(attempts) >= 2, "the reader must reconnect"
    hub.forget("p-1")
    hub.stop()


def test_the_reader_stops_when_there_is_nothing_to_watch():
    def connect(client_id, should_stop):
        while not should_stop():
            time.sleep(0.01)
        return iter(())

    hub = _hub_with(connect=connect)
    hub.register("p-1", "cid-1")
    deadline = time.time() + 5
    while not hub.reader_running() and time.time() < deadline:
        time.sleep(0.05)
    assert hub.reader_running()
    hub.forget("p-1")
    deadline = time.time() + 5
    while hub.reader_running() and time.time() < deadline:
        time.sleep(0.05)
    assert not hub.reader_running(), "the reader must not hold on forever"
    hub.stop()


def test_a_new_prompt_takes_the_subscription_over():
    seen = []

    def connect(client_id, should_stop):
        seen.append(client_id)
        while not should_stop():
            time.sleep(0.01)
        return iter(())

    hub = _hub_with(connect=connect)
    hub.register("p-1", "cid-1")
    hub.register("p-2", "cid-2")
    deadline = time.time() + 5
    while "cid-2" not in seen and time.time() < deadline:
        time.sleep(0.05)
    assert "cid-2" in seen, "the new prompt must get its own subscription"
    hub.forget("p-2")
    hub.stop()


# --------------------------------------------------------------------------- #
# the endpoint the pages read
# --------------------------------------------------------------------------- #

def test_the_progress_endpoint_streams_server_sent_events(client, monkeypatch):
    from webapp.services import runner

    monkeypatch.setattr(progress, "hub", progress.ProgressHub(
        connect=lambda client_id, should_stop: iter(())))
    monkeypatch.setattr(runner, "progress", progress)
    response = client.get("/api/render/progress", buffered=False)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    chunk = next(iter(response.response)).decode("utf-8")
    assert chunk.startswith("data: ")
    body = json.loads(chunk[len("data: "):].strip())
    assert "prompts" in body and "active" in body
    response.response.close()


def test_the_progress_endpoint_answers_even_without_a_render(client):
    response = client.get("/api/render/progress", buffered=False)
    assert response.status_code == 200
    chunk = next(iter(response.response)).decode("utf-8")
    body = json.loads(chunk[len("data: "):].strip())
    assert isinstance(body["prompts"], list)
    assert "active" in body
    response.response.close()


def test_a_job_carries_its_live_progress(client, monkeypatch):
    """The task list shows the same progress the page does."""
    from webapp.services import runner

    monkeypatch.setattr(progress, "hub", progress.ProgressHub(
        connect=lambda client_id, should_stop: iter(())))
    monkeypatch.setattr(runner, "progress", progress)
    library = __import__("webapp.services.library", fromlist=["library"])
    from test_library import SCRIPT
    library.save_storyboard(dict(SCRIPT))
    name = client.get("/api/scripts").get_json()["names"][0]
    monkeypatch.setattr(runner.comfy, "submit",
                        lambda graph, client_id="": {"prompt_id": "p-77",
                                                     "number": 1})
    runner._jobs.clear()
    runner._reserved.clear()
    record = client.post("/api/render/submit", json={
        "script": name, "role_assets": {"character_1": "甲"}}).get_json()
    assert record["job_id"] == "p-77"
    progress.hub.handle_event({"type": "executing",
                               "data": {"node": "13", "prompt_id": "p-77"}})
    jobs = client.get("/api/render/jobs").get_json()
    live = [item for item in jobs["items"] if item["job_id"] == "p-77"]
    assert live, "the submitted job must be on the list"
    assert live[0]["progress"]["node"] == "13"
    runner._jobs.clear()
    runner._reserved.clear()
