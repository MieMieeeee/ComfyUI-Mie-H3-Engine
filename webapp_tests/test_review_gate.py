"""The shot review gate, driven from the workbench.

The engine owns the review: its Review Gate node pauses a run after a
saved shot and its own routes carry the decisions. The workbench only
asks what is pending and hands the decisions back. These tests pin
down the parts that are the workbench's own:

* the opt-in (the unattended graph must not change),
* the filtering (this attempt, this workbench - not another run),
* the native payload (the engine's own field names),
* the refusal to apply one decision twice,
* and the assembly that joins what was saved without generating again.

Everything here is synthetic: a fake engine, a fake graph, no render.
"""

from __future__ import annotations

import json

import pytest

from webapp.services import comfy, review, runner, workflow

from test_library import SCRIPT


# --------------------------------------------------------------------------- #
# a fake engine
# --------------------------------------------------------------------------- #

def _pending_review(token="tok-1", run_name="run_a", prompt_id="job-1",
                    **extra):
    """One pending review, shaped like the engine's own payload."""
    payload = {
        "token": token,
        "run_name": run_name,
        "prompt_id": prompt_id,
        "_branch_id": "main",
        "node_id": "26",
        "clip_index": 2,
        "clip_count": 5,
        "shot_id": "scene_02",
        "scene_prompt": "a room at night",
        "basic_prompt": "",
        "prompt_prefix": "shared prompt",
        "seed": "4242",
        "raw_frames": 39,
        "duration_seconds": 39 / 24.0,
        "video": {"filename": "clip_0002.mp4",
                  "subfolder": "h3_chains/%s/reviews" % run_name,
                  "type": "output"},
        "has_audio": True,
        "warning": "",
        "candidate_count": 2,
        "candidate_generation_complete": True,
        "candidate_batch_active": False,
        "pending_decision": True,
        "review_each_candidate": False,
        "candidates": [
            {"number": 1, "revision": "a" * 32, "seed": "111",
             "created_at": "2026-11-23T00:00:00Z",
             "scene_prompt": "a room at night", "raw_frames": 39,
             "video": {"filename": "cand_1.mp4",
                       "subfolder": "h3_chains/%s/reviews" % run_name,
                       "type": "output"},
             "has_audio": True, "warning": ""},
            {"number": 2, "revision": "b" * 32, "seed": "222",
             "created_at": "2026-11-23T00:01:00Z",
             "scene_prompt": "a room at night", "raw_frames": 39,
             "video": {"filename": "cand_2.mp4",
                       "subfolder": "h3_chains/%s/reviews" % run_name,
                       "type": "output"},
             "has_audio": True, "warning": ""},
        ],
        "deadline": None,
        "server_now": 1000.0,
    }
    payload.update(extra)
    return payload


class FakeEngine:
    """The engine's own routes, recorded instead of called."""

    def __init__(self):
        self.reviews = []
        self.checkpoints = []
        self.posted = []
        self.submitted = []
        self.answers = {}
        self.submit_error = None

    def native(self, path, payload=None, method="GET", timeout=20):
        self.posted.append((method, path, payload))
        if method == "POST" and path.startswith(review.REVIEW_ROUTE):
            return self.answers.get("review_post", (200, {"ok": True}))
        if path.startswith(review.REVIEWS_ROUTE):
            return 200, {"reviews": self.reviews}
        if path.startswith(review.CHECKPOINTS_ROUTE):
            return self.answers.get(
                "checkpoints", (200, {"checkpoints": self.checkpoints}))
        return 404, {"error": "no such route"}

    def submit(self, graph, client_id=""):
        self.submitted.append(graph)
        if self.submit_error is not None:
            raise self.submit_error
        return {"prompt_id": "prompt-new", "number": 99}

    def posts_to(self, route):
        return [item for item in self.posted
                if item[0] == "POST" and item[1].startswith(route)]


@pytest.fixture()
def engine(monkeypatch):
    """A fake engine behind the workbench's own client."""
    fake = FakeEngine()
    monkeypatch.setattr(comfy, "native_request", fake.native)
    monkeypatch.setattr(comfy, "submit", fake.submit)
    return fake


@pytest.fixture(autouse=True)
def _clean_review_state():
    """The pending-review cache and the sent decisions are process state."""
    review.clear_cache()
    review._decided.clear()
    yield
    review.clear_cache()
    review._decided.clear()


@pytest.fixture()
def clean_jobs():
    """The job table is module-level; every test starts empty."""
    runner._jobs.clear()
    yield runner._jobs
    runner._jobs.clear()


def _record(job_id="job-1", run_name="run_a", status="running", **extra):
    record = {
        "job_id": job_id, "number": 1, "script": "测试剧本",
        "run_name": run_name, "title": "作品", "attempt": 1,
        "continued": False, "submitted_at": 1.0, "status": status,
        "error": "", "nodes": 34, "role_assets": {},
        "params": {}, "snapshot_dir": "", "scene_asset": "",
    }
    record.update(extra)
    return record


def _review_node(graph):
    return next(node for node in graph.values()
                if node["class_type"].endswith("ChainReview|MieH3"))


# --------------------------------------------------------------------------- #
# the unattended graph must not change
# --------------------------------------------------------------------------- #

def test_the_unattended_graph_keeps_the_review_gate_off(plugin_root):
    """Without the opt-in the run must behave exactly as before: the
    engine's Review Gate stays disabled and the render does not stop."""
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"})
    assert _review_node(graph)["inputs"]["enabled"] is False


def test_the_review_is_off_until_the_author_asks_for_it(plugin_root):
    assert review.review_settings()["enabled"] is False
    assert review.review_settings()["candidate_count"] == 1


def test_the_opt_in_turns_the_engines_own_gate_on(plugin_root):
    """The gate is the engine's node; the opt-in only flips its switch."""
    graph = workflow.build("测试剧本", SCRIPT, {"character_1": "甲"},
                           review={"enabled": True, "candidate_count": 3})
    node = _review_node(graph)
    assert node["inputs"]["enabled"] is True
    assert node["inputs"]["candidate_count"] == 3


def test_a_preset_without_a_review_node_is_refused(plugin_root):
    """Turning the review on and getting no review would be a lie."""
    with pytest.raises(ValueError):
        workflow.apply_review({"900": {"class_type": "MieH3LoadScriptAsset",
                                        "inputs": {}}},
                               {"enabled": True})


def test_the_opt_in_is_remembered(plugin_root):
    review.save_review_settings(True, 2)
    assert review.review_settings() == {"enabled": True, "candidate_count": 2}
    # A restart reads the same file - the setting is not in the browser.
    assert review.settings.get("review") == {"enabled": True,
                                             "candidate_count": 2}


def test_the_submitted_graph_carries_the_opt_in(client, engine, monkeypatch):
    """The setting on /video reaches the graph that is submitted."""
    from webapp.services import library

    library.save_storyboard(dict(SCRIPT))
    review.save_review_settings(True, 2)
    response = client.post("/api/render/submit", json={
        "script": "测试剧本",
        "role_assets": {"character_1": "甲"},
        "run_name": "opt_in_run",
    })
    assert response.status_code == 200, response.get_json()
    graph = engine.submitted[-1]
    assert _review_node(graph)["inputs"]["enabled"] is True
    assert _review_node(graph)["inputs"]["candidate_count"] == 2


def test_the_unattended_submission_keeps_the_gate_off(client, engine):
    from webapp.services import library

    library.save_storyboard(dict(SCRIPT))
    response = client.post("/api/render/submit", json={
        "script": "测试剧本",
        "role_assets": {"character_1": "甲"},
        "run_name": "plain_run",
    })
    assert response.status_code == 200, response.get_json()
    graph = engine.submitted[-1]
    assert _review_node(graph)["inputs"]["enabled"] is False


def test_the_review_setting_endpoint_says_and_saves(client):
    assert client.get("/api/render/review/settings").get_json()["enabled"] is False
    saved = client.post("/api/render/review/settings",
                        json={"enabled": True, "candidate_count": 3})
    assert saved.get_json() == {"enabled": True, "candidate_count": 3}
    assert client.get("/api/render/review/settings").get_json()["enabled"] is True


def test_the_page_may_send_its_own_opt_in_with_the_submission(client, engine):
    """A toggle that has not been saved yet must still reach the graph:
    the page sends the state it is showing."""
    from webapp.services import library

    library.save_storyboard(dict(SCRIPT))
    response = client.post("/api/render/submit", json={
        "script": "测试剧本",
        "role_assets": {"character_1": "甲"},
        "run_name": "override_run",
        "review": {"enabled": True, "candidate_count": 2},
    })
    assert response.status_code == 200, response.get_json()
    node = _review_node(engine.submitted[-1])
    assert node["inputs"]["enabled"] is True
    assert node["inputs"]["candidate_count"] == 2


def test_the_video_page_offers_the_opt_in(client):
    body = client.get("/video").get_data(as_text=True)
    assert "reviewEnabled" in body
    assert "saveReviewSetting()" in body


def test_the_history_page_shows_the_pending_shot_and_its_buttons(client):
    body = client.get("/history").get_data(as_text=True)
    assert "j.review" in body
    assert "decideReview(j, c, 'approve')" in body
    assert "decideReview(j, null, 'reroll')" in body
    assert "assembleJob(j)" in body


# --------------------------------------------------------------------------- #
# which review belongs to which attempt
# --------------------------------------------------------------------------- #

def test_the_review_of_another_run_is_not_shown_here(engine):
    engine.reviews = [_pending_review(run_name="run_other", prompt_id="job-2")]
    record = _record(job_id="job-1", run_name="run_a")
    assert review.pending_for(record) == {}


def test_the_review_of_another_attempt_is_not_shown_here(engine):
    """Same run, other attempt: the prompt id says which one it is."""
    engine.reviews = [_pending_review(run_name="run_a", prompt_id="job-2")]
    record = _record(job_id="job-1", run_name="run_a")
    assert review.pending_for(record) == {}


def test_the_review_of_this_attempt_is_shown(engine):
    engine.reviews = [_pending_review(run_name="run_a", prompt_id="job-1")]
    record = _record(job_id="job-1", run_name="run_a")
    pending = review.pending_for(record)
    assert pending["token"] == "tok-1"
    summary = review.summarize(pending, record)
    assert summary["shot"]["index"] == 2
    assert summary["shot"]["id"] == "scene_02"
    assert [c["revision"] for c in summary["candidates"]] == ["a" * 32, "b" * 32]
    # The previews are the engine's own files, served by the workbench.
    assert summary["candidates"][0]["url"] == \
        "/media/h3_chains/run_a/reviews/cand_1.mp4"


def test_the_history_carries_the_pending_shot_and_its_candidates(
        client, engine, clean_jobs):
    """The page needs the shot, the clips and the previews - for the
    attempt that is actually waiting."""
    # 有待审分镜的这次运行，一定是**开了审片开关**的那一次提交
    # （开关关着的提交，引擎的图里就没有审片节点，也就不会有待审分镜）。
    # 记录带着这个开关，历史页才会去读引擎的待审列表。
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="running",
                                  review_opt_in={"enabled": True})
    engine.reviews = [_pending_review(run_name="run_a", prompt_id="job-1")]
    payload = client.get("/api/render/jobs?grouped=1").get_json()
    attempt = payload["groups"][0]["attempts"][0]
    assert attempt["review"]["shot"]["id"] == "scene_02"
    assert attempt["review"]["candidates"][1]["number"] == 2
    assert attempt["review"]["actionable"] is True


def test_the_history_of_another_run_carries_no_review(client, engine, clean_jobs):
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  review_opt_in={"enabled": True})
    clean_jobs["job-2"] = _record(job_id="job-2", run_name="run_b")
    engine.reviews = [_pending_review(run_name="run_a", prompt_id="job-1")]
    payload = client.get("/api/render/jobs?grouped=1").get_json()
    by_run = {work["run_name"]: work for work in payload["groups"]}
    assert by_run["run_a"]["attempts"][0].get("review")
    assert not by_run["run_b"]["attempts"][0].get("review")


def test_a_queue_that_cannot_be_read_does_not_hide_a_pending_review(
        client, engine, clean_jobs, monkeypatch):
    """The queue and the review routes are separate questions.

    A queue that could not be read says nothing about the review
    routes: the review that is waiting must still be shown, or the
    author cannot decide it from the workbench.
    """
    def offline_queue(*args, **kwargs):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    monkeypatch.setattr(comfy, "queue_ids", offline_queue)
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="running")
    engine.reviews = [_pending_review(run_name="run_a", prompt_id="job-1")]
    payload = client.get("/api/render/jobs?grouped=1").get_json()
    attempt = payload["groups"][0]["attempts"][0]
    assert attempt["review"]["shot"]["id"] == "scene_02"


def test_the_review_opt_in_is_not_a_review_waiting_for_a_decision(
        client, engine, clean_jobs):
    """The opt-in says the author asked for the engine's Review Gate. It
    is not a review that is waiting, and the page must not show the
    review panel for it (the panel reads ``review.shot``)."""
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="running",
                                  review_opt_in={"enabled": True,
                                                 "candidate_count": 2})
    engine.reviews = []
    payload = client.get("/api/render/jobs?grouped=1").get_json()
    attempt = payload["groups"][0]["attempts"][0]
    assert not attempt.get("review")
    assert attempt.get("review_opt_in") == {"enabled": True,
                                            "candidate_count": 2}


def test_a_saved_record_keeps_the_opt_in_out_of_the_review_slot(
        client, engine, clean_jobs):
    """A record that was saved before the opt-in had its own key must not
    be read as a review that is waiting."""
    legacy = _record(job_id="job-1", run_name="run_a", status="running")
    legacy["review"] = {"enabled": True, "candidate_count": 2}
    migrated = runner._migrate_review_key(legacy)
    assert "review" not in migrated
    assert migrated["review_opt_in"] == {"enabled": True,
                                         "candidate_count": 2}


def test_the_history_answers_while_the_engine_is_unreachable(
        client, clean_jobs, monkeypatch):
    """An engine that cannot be reached must not cost the author the task
    list: the record is kept, and the answer says what it knows."""
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="running")

    def offline_history(prompt_id):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    def offline_queue(*args, **kwargs):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    def offline_native(path, payload=None, method="GET", timeout=20):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    monkeypatch.setattr(comfy, "history", offline_history)
    monkeypatch.setattr(comfy, "queue_ids", offline_queue)
    monkeypatch.setattr(comfy, "native_request", offline_native)
    response = client.get("/api/render/jobs")
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["job_id"] == "job-1"
    # Nothing is known about the run: the status it had is kept.
    assert payload["items"][0]["status"] == "running"


def test_terminal_history_without_review_stays_local(client, clean_jobs, monkeypatch):
    clean_jobs["job-1"] = _record(status="success")

    def unexpected_read(*args, **kwargs):
        pytest.fail("completed history without review should not query ComfyUI")

    monkeypatch.setattr(comfy, "queue_ids", unexpected_read)
    monkeypatch.setattr(comfy, "native_request", unexpected_read)
    response = client.get("/api/render/jobs?grouped=1")
    assert response.status_code == 200
    assert response.get_json()["total"] == 1


def test_a_slow_engine_does_not_hold_the_history_page(client, clean_jobs,
                                                      monkeypatch):
    """The history polls every few seconds. An engine that is slow to
    answer must not be paid for on every poll."""
    review.clear_cache()
    calls = []

    def slow_native(path, payload=None, method="GET", timeout=20):
        calls.append(path)
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    monkeypatch.setattr(comfy, "native_request", slow_native)
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="success", review_opt_in={"enabled": True})
    assert client.get("/api/render/jobs").status_code == 200
    assert calls, "the engine must be asked at least once"
    before = len(calls)
    assert client.get("/api/render/jobs").status_code == 200
    assert len(calls) == before, "a failed read must be remembered"


def test_an_unreachable_engine_does_not_hide_the_rest_of_the_list(
        client, clean_jobs, monkeypatch):
    """The engine being down must not cost the author the task list."""
    clean_jobs["job-1"] = _record(job_id="job-1", run_name="run_a",
                                  status="success", review_opt_in={"enabled": True})

    def offline(path, payload=None, method="GET", timeout=20):
        raise comfy.ComfyUnavailable("ComfyUI 没在运行")

    monkeypatch.setattr(comfy, "native_request", offline)
    payload = client.get("/api/render/jobs?grouped=1").get_json()
    assert payload["total"] == 1
    assert payload["groups"][0]["attempts"][0].get("review_error")


# --------------------------------------------------------------------------- #
# the decision: native payload, right review, once
# --------------------------------------------------------------------------- #

def test_accepting_a_candidate_posts_the_native_payload(engine):
    engine.reviews = [_pending_review()]
    record = _record()
    result = review.decide(record, "tok-1", "approve", "a" * 32)
    assert result["ok"] is True
    posts = engine.posts_to(review.REVIEW_ROUTE)
    assert len(posts) == 1
    _method, path, payload = posts[0]
    assert path == review.REVIEW_ROUTE
    assert payload["token"] == "tok-1"
    assert payload["action"] == "approve"
    assert payload["candidate_revision"] == "a" * 32


def test_rerolling_posts_the_native_payload_of_the_same_shot(engine):
    """A reroll is the SAME shot: its prompt, its length, a new seed."""
    engine.reviews = [_pending_review()]
    record = _record()
    review.decide(record, "tok-1", "reroll")
    _method, _path, payload = engine.posts_to(review.REVIEW_ROUTE)[0]
    assert payload["action"] == "reroll"
    assert payload["scene_prompt"] == "a room at night"
    assert payload["length"] == 39
    assert "candidate_revision" not in payload


def test_a_candidate_of_another_batch_is_refused(engine):
    engine.reviews = [_pending_review()]
    record = _record()
    with pytest.raises(review.ReviewError):
        review.decide(record, "tok-1", "approve", "c" * 32)
    assert engine.posts_to(review.REVIEW_ROUTE) == []


def test_a_review_of_another_run_is_not_decided_here(engine):
    """The token exists, but it belongs to another run: it is not this
    workbench's to decide, and nothing is posted."""
    engine.reviews = [_pending_review(run_name="run_other", prompt_id="job-2")]
    record = _record(job_id="job-1", run_name="run_a")
    with pytest.raises(review.ReviewError):
        review.decide(record, "tok-1", "approve", "a" * 32)
    assert engine.posts_to(review.REVIEW_ROUTE) == []


def test_a_stale_review_is_refused(engine):
    engine.reviews = []
    record = _record()
    with pytest.raises(review.ReviewError) as caught:
        review.decide(record, "tok-1", "approve", "a" * 32)
    assert caught.value.code == "stale"
    assert engine.posts_to(review.REVIEW_ROUTE) == []


def test_the_same_review_is_not_decided_twice(engine):
    engine.reviews = [_pending_review()]
    record = _record()
    review.decide(record, "tok-1", "approve", "a" * 32)
    with pytest.raises(review.ReviewError) as caught:
        review.decide(record, "tok-1", "approve", "a" * 32)
    assert caught.value.code == "decided"
    assert len(engine.posts_to(review.REVIEW_ROUTE)) == 1


def test_the_engines_own_refusal_is_passed_on(engine):
    """The engine says "this review already has a decision": the
    workbench says so too, and does not pretend it worked."""
    engine.answers["review_post"] = (
        409, {"error": "This H3 review already has a decision."})
    engine.reviews = [_pending_review()]
    record = _record()
    with pytest.raises(review.ReviewError) as caught:
        review.decide(record, "tok-1", "approve", "a" * 32)
    assert caught.value.code == "decided"
    assert "already has a decision" in str(caught.value)


def test_a_durable_review_cannot_be_decided(engine):
    """After a restart the review is read-only inventory."""
    engine.reviews = [_pending_review(durable=True, actionable=False)]
    record = _record()
    with pytest.raises(review.ReviewError) as caught:
        review.decide(record, "tok-1", "approve", "a" * 32)
    assert caught.value.code == "stale"
    assert engine.posts_to(review.REVIEW_ROUTE) == []


def test_the_decision_route_refuses_a_job_of_another_workbench(client, engine):
    response = client.post("/api/render/review/decision", json={
        "job_id": "not-mine", "token": "tok-1", "action": "approve"})
    assert response.status_code == 404
    assert engine.posts_to(review.REVIEW_ROUTE) == []


def test_the_decision_route_carries_a_decision_to_the_engine(
        client, engine, clean_jobs):
    clean_jobs["job-1"] = _record()
    engine.reviews = [_pending_review()]
    response = client.post("/api/render/review/decision", json={
        "job_id": "job-1", "token": "tok-1", "action": "approve",
        "candidate_revision": "b" * 32})
    assert response.status_code == 200, response.get_json()
    _method, _path, payload = engine.posts_to(review.REVIEW_ROUTE)[0]
    assert payload["candidate_revision"] == "b" * 32


def test_the_decision_route_reports_a_stale_review(client, engine, clean_jobs):
    clean_jobs["job-1"] = _record()
    engine.reviews = []
    response = client.post("/api/render/review/decision", json={
        "job_id": "job-1", "token": "tok-1", "action": "approve",
        "candidate_revision": "a" * 32})
    assert response.status_code == 409
    assert engine.posts_to(review.REVIEW_ROUTE) == []


# --------------------------------------------------------------------------- #
# assembling what was already accepted
# --------------------------------------------------------------------------- #

def _two_checkpoints():
    return [{"scene": 1, "revision": "a" * 32, "ready": True},
            {"scene": 2, "revision": "b" * 32, "ready": True}]


def test_the_assembly_graph_joins_the_saved_takes_only(engine, clean_jobs):
    """No sampler, no loop, no review: nothing here can generate a
    frame. The saved takes go in, one film comes out."""
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = _two_checkpoints()
    record = runner.assemble("job-1")
    assert len(engine.submitted) == 1
    graph = engine.submitted[0]
    assert set(graph) == {"900", "901"}
    for node in graph.values():
        assert node["class_type"] in runner._ASSEMBLY_NODE_TYPES
    assert graph["901"]["inputs"]["manifest"] == ["900", 0]
    selection = json.loads(graph["900"]["inputs"]["selection_json"])
    assert selection["run_name"] == "run_a"
    assert selection["lineage"] == [{"scene": 1, "revision": "a" * 32},
                                    {"scene": 2, "revision": "b" * 32}]
    assert record["assembly"] is True
    assert record["run_name"] == "run_a"


def test_the_assembly_never_deletes_the_accepted_takes(engine, clean_jobs):
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = _two_checkpoints()
    runner.assemble("job-1")
    graph = engine.submitted[0]
    assert graph["901"]["inputs"]["delete_checkpoints_after_assembly"] is False


def test_the_assembly_reads_only_what_is_saved(engine, clean_jobs):
    """A scene that was never saved ends the chain: the engine joins a
    contiguous prefix, and joining past a hole would lose a shot."""
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = [{"scene": 1, "revision": "a" * 32, "ready": True},
                          {"scene": 3, "revision": "c" * 32, "ready": True}]
    runner.assemble("job-1")
    selection = json.loads(
        engine.submitted[0]["900"]["inputs"]["selection_json"])
    assert selection["lineage"] == [{"scene": 1, "revision": "a" * 32}]


def test_another_mode_is_refused_before_anything_is_submitted(engine, clean_jobs):
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = _two_checkpoints()
    with pytest.raises(ValueError):
        runner.assemble("job-1", mode="render")
    assert engine.submitted == []


def test_a_graph_that_would_generate_is_refused():
    graph = {
        "900": {"class_type": "MiniMaxH3ChainCheckpointManager|MieH3",
                "inputs": {}},
        "901": {"class_type": "MiniMaxH3ChainAssemble|MieH3",
                "inputs": {"manifest": ["900", 0]}},
        "902": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
    }
    with pytest.raises(ValueError):
        runner._check_assembly_mode(graph)


def test_an_engine_without_the_recovery_nodes_is_reported(engine, clean_jobs):
    """An older engine that does not know the recovery nodes: the
    limitation is said out loud, and nothing on disk is touched."""
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = _two_checkpoints()
    engine.submit_error = comfy.ComfyUnavailable(
        "ComfyUI 缺少工作流节点: MiniMaxH3ChainCheckpointManager|MieH3")
    with pytest.raises(ValueError) as caught:
        runner.assemble("job-1")
    assert "拼片" in str(caught.value)
    assert "原样保留" in str(caught.value)


def test_an_engine_without_the_checkpoint_route_is_reported(engine, clean_jobs,
                                                            monkeypatch):
    clean_jobs["job-1"] = _record(status="success")
    engine.answers["checkpoints"] = (404, {"error": "not found"})
    with pytest.raises(review.ReviewError) as caught:
        runner.assemble("job-1")
    assert "检查点" in str(caught.value)
    assert engine.submitted == []


def test_a_run_without_saved_takes_cannot_be_assembled(engine, clean_jobs):
    clean_jobs["job-1"] = _record(status="success")
    engine.checkpoints = []
    with pytest.raises(ValueError):
        runner.assemble("job-1")
    assert engine.submitted == []


def test_the_assemble_route_refuses_a_job_of_another_workbench(client, engine):
    response = client.post("/api/render/assemble", json={"job_id": "not-mine"})
    assert response.status_code == 400
    assert engine.submitted == []
