import itertools

import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.commands import (HUMAN_GENERATION, Command, StaleVersion, execute_as_human,
                             submit)
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


_N = itertools.count(1)


def _human(store, run_id, name, payload, key=None):
    return execute_as_human(store, Command(
        name=name, run_id=run_id, expected_version=store.run_version(run_id),
        idempotency_key=key or f"h-{next(_N)}",
        generation=HUMAN_GENERATION, payload=payload))


def test_human_commands_do_not_pass_through_submit(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    g = f._dispatch(Role.OPERATOR, "runner")
    h = s.phase_artifact("r-1", "spec")
    for name, payload in (("approve_artifact", {"artifact_hash": h}), ("grant_round", {}),
                          ("record_human_answer", {"question_ids": [], "answer": "x", "cursor_item_id": None}),
                          ("record_human_direction", {"text": "x", "cursor_item_id": None})):
        with pytest.raises(NotAuthorized, match="execute_as_human"):
            f._cmd(g, name, payload)
    assert s.run_state("r-1") == "spec_accepted"


def test_dispatch_refuses_the_human_actor(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    with pytest.raises(ValueError, match="human"):
        dispatch(s, "r-1", actor="human", role=Role.OPERATOR)


def test_approve_moves_accepted_on_and_binds_the_hash(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:gate-plan",))
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    h = s.phase_artifact("r-1", "spec")
    with pytest.raises(NotAuthorized, match="current artefact"):
        _human(s, "r-1", "approve_artifact", {"artifact_hash": put_artifact(s, b"other")})
    _human(s, "r-1", "approve_artifact", {"artifact_hash": h})
    assert s.run_state("r-1") == "specified"
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert ruling.actor == "human"
    assert ruling.payload == {"ruling": "approve", "phase": "spec", "epoch": 0, "artifact_hash": h}
    accepted = [x for x in s.facts_of_kind("r-1", EventKind.COMMAND_ACCEPTED)
                if x.payload["command_name"] == "approve_artifact"]
    assert accepted[-1].actor == "human" and accepted[-1].payload["generation"] == HUMAN_GENERATION
    ph = f.author_round(PLAN_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "plan_accepted"
    _human(s, "r-1", "approve_artifact", {"artifact_hash": ph})
    assert s.run_state("r-1") == "planned"


def test_approve_is_illegal_from_submitted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        _human(s, "r-1", "approve_artifact", {"artifact_hash": s.phase_artifact("r-1", "spec")})


def test_human_request_revision_needs_only_four_fields(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    h = s.phase_artifact("r-1", "spec")
    with pytest.raises(NotAuthorized, match="request_revision"):
        _human(s, "r-1", "record_review", {"phase": "spec", "artifact_hash": h,
                                            "verdict": "accept", "findings": "fine"})
    _human(s, "r-1", "record_review", {"phase": "spec", "artifact_hash": h,
                                        "verdict": "request_revision", "findings": "missing the API"})
    assert s.run_state("r-1") == "queued"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    assert v.payload["ruling"] == "human_ruling" and v.payload["reviewer_identity"] == "human"
    assert v.payload["binding_hash"] is None
    assert v.payload["findings_hash"] == content_hash(b"missing the API")
    assert s.has_artifact(v.payload["findings_hash"])
    r = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert r.payload == {"ruling": "request_revision", "phase": "spec", "epoch": 0, "artifact_hash": h}
    # From *_submitted too, and to the plan's submitting state.
    f.author_round(SPEC_BYTES + b"\n## API\n"); f.review_round("accept")
    _human(s, "r-1", "approve_artifact", {"artifact_hash": s.phase_artifact("r-1", "spec")})
    ph = f.author_round(PLAN_BYTES)
    _human(s, "r-1", "record_review", {"phase": "plan", "artifact_hash": ph,
                                        "verdict": "request_revision", "findings": "no tests"})
    assert s.run_state("r-1") == "specified"


def test_human_review_is_front_half_only(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.to_implementing()
    with pytest.raises(NotAuthorized, match="front-half"):
        _human(s, "r-1", "record_review", {"phase": "implementation",
                                            "artifact_hash": s.current_artifact("r-1"),
                                            "verdict": "request_revision", "findings": "x"})


def test_grant_round_needs_a_current_park_and_consumes_it(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="park"):
        _human(s, "r-1", "grant_round", {})
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": "i-1",
                       "findings_hash": None, "verdict": "request_revision", "reviewer": "codex"})
    park = front.current_park(s, "r-1")
    _human(s, "r-1", "grant_round", {})
    assert front.grants(s, "r-1") == 1
    r = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert r.payload == {"ruling": "grant_round", "phase": "spec", "epoch": 0, "park_seq": park.seq}
    assert front.current_park(s, "r-1") is None
    with pytest.raises(NotAuthorized, match="park"):
        _human(s, "r-1", "grant_round", {})
    assert s.run_state("r-1") == "spec_submitted"


def test_grant_round_is_illegal_from_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_accepted'"):
        _human(s, "r-1", "grant_round", {})


def test_answer_and_direction_carry_the_epoch_and_phase(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    _human(s, "r-1", "record_human_answer", {"question_ids": ["q1", "q2"], "answer": "yes, both",
                                              "cursor_item_id": "i-7"})
    a = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    assert a.actor == "human"
    assert a.payload == {"epoch": 0, "question_ids": ["q1", "q2"], "answer": "yes, both",
                         "cursor_item_id": "i-7"}
    with pytest.raises(NotAuthorized, match="answer"):
        _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "", "cursor_item_id": None})
    _human(s, "r-1", "record_human_direction", {"text": "use sqlite", "cursor_item_id": "i-8"})
    d = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    assert d.payload == {"phase": "spec", "epoch": 0, "text": "use sqlite", "cursor_item_id": "i-8"}
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        _human(s, "r-1", "record_human_direction", {"text": "x", "cursor_item_id": None})


def test_execute_as_human_skips_the_fence_not_the_version_or_the_halt(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)                       # the current generation is an author's
    v = s.run_version("r-1")
    _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "a", "cursor_item_id": None}, key="k1")
    with pytest.raises(StaleVersion):
        execute_as_human(s, Command(name="record_human_answer", run_id="r-1", expected_version=v,
                                    idempotency_key="k2", generation=HUMAN_GENERATION,
                                    payload={"question_ids": [], "answer": "b", "cursor_item_id": None}))
    again = execute_as_human(s, Command(name="record_human_answer", run_id="r-1", expected_version=v,
                                        idempotency_key="k1", generation=HUMAN_GENERATION,
                                        payload={"question_ids": [], "answer": "a", "cursor_item_id": None}))
    assert again.replayed is True
    with pytest.raises(ValueError, match="HUMAN_GENERATION"):
        execute_as_human(s, Command(name="record_human_answer", run_id="r-1",
                                    expected_version=s.run_version("r-1"), idempotency_key="k3",
                                    generation=0, payload={"question_ids": [], "answer": "c", "cursor_item_id": None}))
    s.set_reconciliation("r-1", {"why": "test"})
    with pytest.raises(RuntimeError, match="halted"):
        _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "d", "cursor_item_id": None}, key="k4")
    requested = [x for x in s.facts_of_kind("r-1", EventKind.COMMAND_REQUESTED) if x.actor == "human"]
    assert len(requested) == 4          # k1, k2, the k1 replay, k4; the generation-0 call wrote nothing


def test_driver_approves_at_a_gate(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:gate-plan",))
    f.to_planned()
    assert s.run_state("r-1") == "planned"
    assert [r.payload["ruling"] for r in s.facts_of_kind("r-1", EventKind.HUMAN_RULING)] == ["approve", "approve"]


def test_execute_as_human_runs_only_the_humans_commands(tmp_path):
    """Everything but the human's five is a dispatched actor's command, and
    execute_as_human must refuse it before _submit ever sees it -- otherwise
    a caller can run ANY command as `human` with no dispatch behind it."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    before = len(s.facts_for("r-1"))
    refused = (
        ("park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                  "findings_hash": None, "verdict": None, "reviewer": None}),
        ("record_turn_ended", {"session": "s-1", "ended": "file"}),
        ("submit_spec", {"artifact_hash": s.phase_artifact("r-1", "spec")}),
        ("start_implementation", {}),
    )
    for name, payload in refused:
        with pytest.raises(ValueError, match="does not go through execute_as_human"):
            _human(s, "r-1", name, payload)
        assert len(s.facts_for("r-1")) == before, f"{name} wrote a fact through execute_as_human"

    _human(s, "r-1", "cancel_run", {})
    assert s.run_state("r-1") == "cancelled"
    cancelled = [x for x in s.facts_of_kind("r-1", EventKind.COMMAND_ACCEPTED)
                 if x.payload["command_name"] == "cancel_run"]
    assert cancelled[-1].actor == "human"


def test_human_record_review_needs_non_empty_findings(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    h = s.phase_artifact("r-1", "spec")
    state_before = s.run_state("r-1")
    verdicts_before = len(s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT))
    rulings_before = len(s.facts_of_kind("r-1", EventKind.HUMAN_RULING))
    for extra in ({}, {"findings": ""}, {"findings": 123}):
        payload = {"phase": "spec", "artifact_hash": h, "verdict": "request_revision"}
        payload.update(extra)
        with pytest.raises(NotAuthorized, match="non-empty findings"):
            _human(s, "r-1", "record_review", payload)
    assert s.run_state("r-1") == state_before
    assert len(s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)) == verdicts_before
    assert len(s.facts_of_kind("r-1", EventKind.HUMAN_RULING)) == rulings_before
