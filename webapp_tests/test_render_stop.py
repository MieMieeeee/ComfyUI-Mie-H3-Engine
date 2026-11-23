"""Stopping a render - and only the workbench's own render.

ComfyUI's ``/interrupt`` is GLOBAL when the body carries no prompt id:
the server reads ``json_data.get('prompt_id')`` and, when that is empty,
stops whatever happens to be running. An empty id is therefore not
"stop this one" - it is "stop everything", and on a shared ComfyUI that
takes down somebody else's render.

``/queue`` with ``{"delete": [id]}`` is the other half: it takes ONE
pending prompt out of the queue. A queued job needs that - an interrupt
does not touch the pending list, so the job would keep waiting while
the page says it was stopped.

The tests talk to a fake ComfyUI over HTTP, so the wire contract is
what is checked: which URL, which body, and what happened to the other
prompts.
"""

from __future__ import annotations

import json
import threading

import pytest

from webapp import config
from webapp.services import comfy, runner


# --------------------------------------------------------------------------- #
# the fake ComfyUI
# --------------------------------------------------------------------------- #

class FakeComfy:
    """A ComfyUI that answers /queue, /interrupt and /history.

    It behaves like the real server where it matters: a prompt-scoped
    interrupt stops only that prompt, and an interrupt with no prompt
    id stops EVERYTHING that is running.
    """

    def __init__(self):
        self.running = []
        self.pending = []
        self.history = {}
        self.requests = []
        self.interrupted = []
        self.global_interrupts = 0
        #: When set, the queue refuses to delete the pending prompt -
        #: the race where the prompt starts running instead.
        self.refuse_delete = False
        #: When set, the delete MOVES the prompt from pending to
        #: running: the prompt started while the delete was on its way.
        self.start_on_delete = False
        #: When set, the delete takes the prompt out of the queue AND
        #: writes its history: the prompt ran and finished while the
        #: delete was on its way.
        self.finish_on_delete = False
        self._server = None

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - keep the test quiet
                pass

            def _send(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/queue"):
                    fake.requests.append(("GET", self.path, None))
                    self._send({
                        "queue_running": [[0, pid, {}, {}, []]
                                          for pid in fake.running],
                        "queue_pending": [[1, pid, {}, {}, []]
                                          for pid in fake.pending],
                    })
                    return
                if self.path.startswith("/history/"):
                    prompt_id = self.path.split("/history/", 1)[1]
                    fake.requests.append(("GET", self.path, None))
                    self._send({prompt_id: fake.history.get(prompt_id, {})})
                    return
                self._send({})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    body = {}
                fake.requests.append(("POST", self.path, body))
                if self.path == "/interrupt":
                    prompt_id = str(body.get("prompt_id") or "")
                    if prompt_id:
                        if prompt_id in fake.running:
                            fake.running.remove(prompt_id)
                            fake.interrupted.append(prompt_id)
                    else:
                        fake.global_interrupts += 1
                        for other in list(fake.running):
                            fake.running.remove(other)
                            fake.interrupted.append(other)
                elif self.path == "/queue":
                    for prompt_id in body.get("delete") or []:
                        if fake.refuse_delete:
                            continue
                        if fake.start_on_delete:
                            # The prompt started running while the
                            # delete was on its way.
                            if prompt_id in fake.pending:
                                fake.pending.remove(prompt_id)
                            if prompt_id not in fake.running:
                                fake.running.append(prompt_id)
                            continue
                        if fake.finish_on_delete:
                            # The prompt ran and finished while the
                            # delete was on its way.
                            if prompt_id in fake.pending:
                                fake.pending.remove(prompt_id)
                            fake.history[prompt_id] = {
                                "status": {"status_str": "success",
                                           "completed": True,
                                           "messages": []},
                                "outputs": {},
                            }
                            continue
                        if prompt_id in fake.pending:
                            fake.pending.remove(prompt_id)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return "http://127.0.0.1:%d" % port

    def posts(self, path: str) -> list:
        return [body for method, url, body in self.requests
                if method == "POST" and url == path]

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


@pytest.fixture()
def server(plugin_root, monkeypatch):
    """A fake ComfyUI on a loopback port, pointed at by the app."""
    fake = FakeComfy().start()
    monkeypatch.setattr(config, "COMFYUI_URL", fake.url)
    comfy.forget_queue_cache()
    yield fake
    comfy.forget_queue_cache()
    fake.stop()


@pytest.fixture()
def clean_jobs(plugin_root):
    """The job table is module-level; every test starts empty."""
    runner._jobs.clear()
    yield
    runner._jobs.clear()


def _record(job_id="job-1", **extra):
    record = {
        "job_id": job_id, "number": 1, "script": "股神",
        "run_name": "sb_web_001e24bb", "role_assets": {},
        "submitted_at": 1.0, "status": "queued", "error": "", "nodes": 34,
    }
    record.update(extra)
    return record


# --------------------------------------------------------------------------- #
# the running job
# --------------------------------------------------------------------------- #

def test_a_running_job_is_stopped_by_its_own_prompt_id(server, clean_jobs):
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["job-1"]

    result = runner.stop("job-1")

    assert result["ok"] is True, result
    assert result["state"] == "running"
    assert server.interrupted == ["job-1"]
    # The prompt id is in the body: ComfyUI stops that one and no other.
    assert server.posts("/interrupt") == [{"prompt_id": "job-1"}]
    assert server.global_interrupts == 0


def test_a_queued_job_is_taken_out_of_the_queue(server, clean_jobs):
    runner._jobs["job-1"] = _record(status="queued")
    server.pending = ["job-1"]

    result = runner.stop("job-1")

    assert result["ok"] is True, result
    assert result["state"] == "queued"
    assert server.posts("/queue") == [{"delete": ["job-1"]}]
    # An interrupt would not have touched the pending list.
    assert server.posts("/interrupt") == []
    # And the record says what happened, in the runner's own words.
    assert runner._jobs["job-1"]["status"] == "interrupted"
    assert "排队" in runner._jobs["job-1"]["error"]


def test_a_queued_job_that_stays_queued_is_not_called_stopped(server, clean_jobs):
    """The queue can refuse the deletion (the prompt starts running
    instead). Saying "stopped" while it is still waiting is a lie."""
    runner._jobs["job-1"] = _record(status="queued")
    server.pending = ["job-1"]
    server.refuse_delete = True

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "queued"
    assert "还在队列里" in result["error"]


def test_a_queued_job_that_starts_running_is_stopped_by_its_own_id(
        server, clean_jobs):
    """Taking a prompt OUT of the queue is a request, not a fact: it can
    start running on the way. Then it is interrupted by its OWN prompt
    id - and it is not called "撤掉了" either."""
    runner._jobs["job-1"] = _record(status="queued")
    server.pending = ["job-1"]
    server.start_on_delete = True

    result = runner.stop("job-1")

    assert result["ok"] is True, result
    assert result["state"] == "running"
    assert server.interrupted == ["job-1"]
    assert server.global_interrupts == 0
    # It started rendering: it was NOT taken out of the queue.
    assert runner._jobs["job-1"]["status"] != "interrupted"


def test_a_removal_that_cannot_be_confirmed_is_not_a_removal(
        plugin_root, monkeypatch, clean_jobs):
    """ComfyUI went away right after the delete: nothing is known, so
    nothing may be claimed - and the record keeps its state."""
    calls = []

    def queue_ids(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return set(), {"job-1"}
        raise comfy.ComfyUnavailable("offline after the delete")

    monkeypatch.setattr(comfy, "queue_ids", queue_ids)
    monkeypatch.setattr(comfy, "delete_queued", lambda job: True)
    monkeypatch.setattr(comfy, "forget_queue_cache", lambda: None)
    runner._jobs["job-1"] = _record(status="queued")

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "unreachable"
    assert runner._jobs["job-1"]["status"] == "queued"


def test_a_just_finished_prompt_keeps_its_result(server, clean_jobs):
    """The prompt ran and finished while the stop was on its way. Its
    result must not be overwritten with "interrupted"."""
    runner._jobs["job-1"] = _record(status="queued")
    server.pending = ["job-1"]
    server.finish_on_delete = True

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "finished"
    assert runner._jobs["job-1"]["status"] == "queued"


def test_a_finished_record_is_not_overwritten(server, clean_jobs):
    """The task list already read the result. The stop does not get to
    write over it."""
    runner._jobs["job-1"] = _record(status="success")
    server.pending = ["job-1"]
    server.finish_on_delete = True

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "terminal"
    assert runner._jobs["job-1"]["status"] == "success"


# --------------------------------------------------------------------------- #
# the jobs that must not be touched
# --------------------------------------------------------------------------- #

def test_an_unrelated_prompt_is_never_touched(server, clean_jobs):
    """Somebody else's render on the same ComfyUI is not ours."""
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["somebody-elses-prompt"]

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert server.interrupted == []
    assert server.global_interrupts == 0


def test_stopping_our_job_leaves_the_other_render_alone(server, clean_jobs):
    """Two renders on the same ComfyUI - ours and somebody else's. Only
    ours is stopped: a global stop would take theirs down as well."""
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["job-1", "somebody-elses-prompt"]

    result = runner.stop("job-1")

    assert result["ok"] is True, result
    assert server.interrupted == ["job-1"]
    assert "somebody-elses-prompt" in server.running
    assert server.global_interrupts == 0


def test_an_unknown_job_id_never_reaches_comfyui(server, clean_jobs):
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["job-1", "not-ours"]

    result = runner.stop("not-ours")

    assert result["ok"] is False, result
    assert result["state"] == "unknown"
    assert server.requests == [], "nothing may be sent for an unknown id"


def test_a_finished_job_is_not_stopped(server, clean_jobs):
    runner._jobs["job-1"] = _record(status="success")
    server.running = []
    server.pending = []

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "terminal"
    assert server.interrupted == []


# --------------------------------------------------------------------------- #
# the top control: no job id
# --------------------------------------------------------------------------- #

def test_the_top_control_stops_the_only_job_in_flight(server, clean_jobs):
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["job-1"]

    result = runner.stop()

    assert result["ok"] is True, result
    assert server.interrupted == ["job-1"]


def test_the_top_control_asks_when_more_than_one_job_is_in_flight(server, clean_jobs):
    """Two renders running: the workbench does not pick one for the
    author - it asks."""
    runner._jobs["job-1"] = _record(status="running")
    runner._jobs["job-2"] = _record(job_id="job-2", status="running")
    server.running = ["job-1", "job-2"]

    result = runner.stop()

    assert result["ok"] is False, result
    assert result["state"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert server.interrupted == [], "nothing may be stopped on a guess"
    assert server.global_interrupts == 0


def test_the_top_control_says_when_there_is_nothing_to_stop(server, clean_jobs):
    result = runner.stop()
    assert result["ok"] is False, result
    assert result["state"] == "none"
    # The queue may be read - but nothing is sent to it.
    assert server.posts("/interrupt") == []
    assert server.posts("/queue") == []


# --------------------------------------------------------------------------- #
# transport failures
# --------------------------------------------------------------------------- #

def test_a_dead_comfyui_is_reported_not_swallowed(plugin_root, monkeypatch,
                                                   clean_jobs):
    monkeypatch.setattr(config, "COMFYUI_URL", "http://127.0.0.1:1")
    comfy.forget_queue_cache()
    runner._jobs["job-1"] = _record(status="running")

    result = runner.stop("job-1")

    assert result["ok"] is False, result
    assert result["state"] == "unreachable"
    assert "ComfyUI" in result["error"]


# --------------------------------------------------------------------------- #
# the endpoint
# --------------------------------------------------------------------------- #

def test_the_endpoint_stops_the_named_job(client, server, clean_jobs):
    runner._jobs["job-1"] = _record(status="running")
    server.running = ["job-1"]

    response = client.post("/api/render/interrupt", json={"job_id": "job-1"})

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["ok"] is True
    assert server.interrupted == ["job-1"]


def test_the_endpoint_refuses_an_ambiguous_request(client, server, clean_jobs):
    runner._jobs["job-1"] = _record(status="running")
    runner._jobs["job-2"] = _record(job_id="job-2", status="running")
    server.running = ["job-1", "job-2"]

    response = client.post("/api/render/interrupt", json={})

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["state"] == "ambiguous"
    assert server.interrupted == []


def test_the_endpoint_refuses_an_unknown_job(client, server, clean_jobs):
    response = client.post("/api/render/interrupt", json={"job_id": "nope"})
    assert response.status_code == 404, response.get_data(as_text=True)
    assert server.requests == []
