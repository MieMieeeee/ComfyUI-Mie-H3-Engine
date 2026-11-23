"""The queue cache: a remembered failure is not an empty queue.

The task list asks the engine the same question every few seconds, and
an engine that cannot be reached must not be paid for on every poll -
so the answer is kept for a couple of seconds. What is kept has to be
HONEST: a failure is a failure, and two empty sets would say "nothing
is running" about an engine nobody was able to ask.

The cache belongs to ONE engine as well: a changed URL is a different
engine, and neither its queue nor its silence may be served out of the
old one's cache.
"""

from __future__ import annotations

import pytest

from webapp import config
from webapp.services import comfy

#: The queue of the engine the tests point at.
ENGINE_ONE = "http://engine-one:8188"
ENGINE_TWO = "http://engine-two:8188"


@pytest.fixture()
def engine(plugin_root, monkeypatch):
    """An engine that answers what the test tells it to.

    The answers are keyed by the full URL, so a test can say "this
    engine is down" and "that one answers" without touching the client.
    """
    answers: dict = {}
    calls: list = []

    def fake_json(url, payload=None, method=None, timeout=comfy.TIMEOUT):
        calls.append(url)
        if url not in answers:
            raise comfy.ComfyUnavailable("连不上 ComfyUI")
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(comfy, "_json", fake_json)
    monkeypatch.setattr(config, "COMFYUI_URL", ENGINE_ONE)
    comfy.forget_queue_cache()
    yield answers, calls
    comfy.forget_queue_cache()


def _queue(running=(), pending=()):
    return {"queue_running": [[0, pid, {}, {}, []] for pid in running],
            "queue_pending": [[1, pid, {}, {}, []] for pid in pending]}


def test_a_failed_queue_read_is_remembered_as_a_failure(engine):
    """The second question inside the cache window comes from the cache
    - and the cache says "could not be reached", not "nothing runs"."""
    answers, calls = engine
    answers[ENGINE_ONE + "/queue"] = comfy.ComfyUnavailable("连不上 ComfyUI")

    with pytest.raises(comfy.ComfyUnavailable):
        comfy.queue_ids()
    with pytest.raises(comfy.ComfyUnavailable):
        comfy.queue_ids()

    assert len(calls) == 1, "the failure must not be paid for twice"
    assert not comfy.queue_ok()


def test_a_reachable_empty_queue_is_not_a_failure(engine):
    """An engine that answered has an empty queue - that is a fact, and
    it is not the same as not knowing."""
    answers, calls = engine
    answers[ENGINE_ONE + "/queue"] = _queue()

    running, pending = comfy.queue_ids()

    assert running == set()
    assert pending == set()
    assert comfy.queue_ok()


def test_the_queue_cache_belongs_to_one_engine(engine, monkeypatch):
    """A different engine is a different engine: its answer must not
    come out of the old one's cache - not even its failure."""
    answers, calls = engine
    answers[ENGINE_ONE + "/queue"] = comfy.ComfyUnavailable("连不上 ComfyUI")
    with pytest.raises(comfy.ComfyUnavailable):
        comfy.queue_ids()

    monkeypatch.setattr(config, "COMFYUI_URL", ENGINE_TWO)
    answers[ENGINE_TWO + "/queue"] = _queue(running=["job-2"])

    running, pending = comfy.queue_ids()

    assert running == {"job-2"}
    assert pending == set()
    assert len(calls) == 2, "the new engine must be asked"


def test_forgetting_the_queue_cache_drops_the_failure(engine):
    """A stop request has to see the queue as it is NOW: the remembered
    failure may not stand in its way."""
    answers, calls = engine
    answers[ENGINE_ONE + "/queue"] = comfy.ComfyUnavailable("连不上 ComfyUI")
    with pytest.raises(comfy.ComfyUnavailable):
        comfy.queue_ids()

    comfy.forget_queue_cache()
    answers[ENGINE_ONE + "/queue"] = _queue(pending=["job-3"])

    running, pending = comfy.queue_ids()

    assert pending == {"job-3"}
    assert len(calls) == 2
    assert comfy.queue_ok()


def test_an_unreadable_answer_is_not_an_empty_queue(engine):
    """The engine answered, but not with a queue. Nothing is known about
    what is running - so nothing may be claimed."""
    answers, calls = engine
    answers[ENGINE_ONE + "/queue"] = None

    with pytest.raises(comfy.ComfyUnavailable):
        comfy.queue_ids()

    assert not comfy.queue_ok()
