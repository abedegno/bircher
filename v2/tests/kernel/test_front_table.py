import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import FRONT_HALF_STATES, NotAuthorized, phase_of
from kernel.commands import COMMAND_NAMES, Command, submit
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front, reach_planned


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_phase_of_every_state():
    assert FRONT_HALF_STATES == {"queued", "spec_submitted", "spec_accepted",
                                 "specified", "plan_submitted", "plan_accepted"}
    for s in ("queued", "spec_submitted", "spec_accepted"):
        assert phase_of(s) == "spec"
    for s in ("specified", "plan_submitted", "plan_accepted"):
        assert phase_of(s) == "plan"
    for s in ("implementing", "reviewing"):
        assert phase_of(s) == "implementation"
    assert phase_of("planned") is None and phase_of("merged") is None


def test_command_set_gains_turn_ended_and_park():
    assert {"record_turn_ended", "park"} <= COMMAND_NAMES


def test_submit_spec_lands_at_spec_submitted_and_records_the_artefact(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    assert s.run_state("r-1") == "spec_submitted"
    assert s.phase_artifact("r-1", "spec") == h
    sub = s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert sub.payload == {"phase": "spec", "epoch": 0, "hash": h, "author": "claude", "round": 1}
    assert sub.actor == "claude"


def test_accept_without_gate_reaches_specified_then_planned(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:autonomous",))
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "specified"
    h = f.author_round(PLAN_BYTES)
    assert s.run_state("r-1") == "plan_submitted"
    assert s.phase_artifact("r-1", "plan") == h
    f.review_round("accept")
    assert s.run_state("r-1") == "planned"
    verdicts = s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)
    assert [v.payload["phase"] for v in verdicts] == ["spec", "plan"]
    assert verdicts[-1].payload["artifact_hash"] == h
    assert verdicts[-1].payload["ruling"] == "review_ruling"
    assert verdicts[-1].payload["reviewer_identity"] == "codex"


def test_accept_under_the_gate_stops_at_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())          # default policy: gates == {spec}
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    # Task 8 adds the human's approve; here the gate is the end of the road.
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_accepted'"):
        f.author_round(PLAN_BYTES)


def test_request_revision_returns_to_the_submitting_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("request_revision", findings=b"needs a threat model")
    assert s.run_state("r-1") == "queued"
    f.author_round(SPEC_BYTES + b"\n## Threats\n")
    f.review_round("accept")
    f.author_round(PLAN_BYTES)
    f.review_round("request_revision")
    assert s.run_state("r-1") == "specified"


def test_reject_is_refused_in_the_front_half(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="reject"):
        f.review_round("reject")
    assert s.run_state("r-1") == "spec_submitted"


def test_review_phase_and_hash_must_match_the_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="phase"):
        f.review_round("accept", phase="plan")
    other = put_artifact(s, b"not the spec")
    with pytest.raises(NotAuthorized, match="current artefact"):
        f.review_round("accept", artifact_hash=other)
    assert s.run_state("r-1") == "spec_submitted"
    f.review_round("accept", artifact_hash=h)
    assert s.run_state("r-1") == "specified"


def test_review_binds_the_epochs_bundle_and_policy_version(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="context_bundle_hash"):
        f.review_round("accept", context_bundle_hash="f" * 64)
    with pytest.raises(NotAuthorized, match="policy_version"):
        f.review_round("accept", policy_version=front.policy_version(s, "r-1") + 1)
    with pytest.raises(NotAuthorized, match="findings"):
        f.review_round("accept", findings_hash="e" * 64)


def test_submit_requires_the_author_role(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    h = put_artifact(s, SPEC_BYTES)
    g = dispatch(s, "r-1", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized, match="author"):
        submit(s, Command(name="submit_spec", run_id="r-1", expected_version=s.run_version("r-1"),
                          idempotency_key="k", generation=g, payload={"artifact_hash": h}))


def test_identical_resubmission_in_the_phase_and_epoch_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("request_revision")
    with pytest.raises(NotAuthorized, match="identical"):
        f.author_round(SPEC_BYTES)
    rejected = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rejected.payload["command_name"] == "submit_spec"


def test_submit_plan_refuses_the_spec_hash_and_a_plan_without_tasks(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    with pytest.raises(NotAuthorized, match="spec"):
        f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="### Task"):
        f.author_round(b"# Plan\n\nno tasks here\n")
    assert s.run_state("r-1") == "specified"


def test_reviewer_may_not_be_the_submitting_actor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", author="claude", reviewer="claude")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="independence"):
        f.review_round("accept")


def test_turn_ended_names_a_session_and_one_of_four_ends(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    with pytest.raises(NotAuthorized, match="ended"):
        f._cmd(g, "record_turn_ended", {"session": sid, "ended": "timeout"})
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "cap"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    assert te.payload == {"session": sid, "ended": "cap", "phase": "spec", "epoch": 0,
                          "generation": g, "prompt_key": prompt["key"]}


def test_park_records_the_kernels_view_and_currency(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    with pytest.raises(NotAuthorized, match="reason"):
        f._cmd(g, "park", {"reason": "tired", "session_id": None, "cursor_item_id": None,
                           "findings_hash": None, "verdict": None, "reviewer": None})
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": "s-9", "cursor_item_id": "i-3",
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    p = front.current_park(s, "r-1")
    assert p is not None and p.payload["phase"] == "spec" and p.payload["generation"] == g
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_ANSWER, actor="human",
                  causal_command_id=None, payload={"epoch": 0, "question_ids": [], "answer": "go on",
                                                   "cursor_item_id": "i-4"})
    assert front.current_park(s, "r-1") is None
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert front.current_park(s, "r-1") is not None
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_DIRECTION, actor="human",   # ruling 2
                  causal_command_id=None, payload={"phase": "spec", "epoch": 0, "text": "no",
                                                   "cursor_item_id": "i-5"})
    assert front.current_park(s, "r-1") is None
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert front.current_park(s, "r-1") is not None
    f.review_round("accept")                      # a transition consumes it
    assert front.current_park(s, "r-1") is None


def test_reach_planned_helper_and_cancel_from_new_states(tmp_path):
    s = _store(tmp_path)
    f = reach_planned(s, "r-1")
    assert s.run_state("r-1") == "planned"
    s2 = Store.open(tmp_path / "k2.db")
    f2 = Front(s2, "r-2")
    f2.author_round(SPEC_BYTES)
    g = f2._dispatch(Role.OPERATOR, "runner")
    f2._cmd(g, "cancel_run", {})
    assert s2.run_state("r-2") == "cancelled"


def test_a_front_half_accept_does_not_authorize_a_merge(tmp_path):
    """A spec accept binds the same four fields a merge presents.

    Front-half verdicts are `review_verdict` facts carrying a real
    binding_hash, so `_merge_is_authorized` can see them. Treated as merge
    approvals they are one: below, the implementation output IS the spec
    artefact, and the requester -- who chooses the bundle and the policy
    version, both declared residuals -- presents exactly the tuple the SPEC
    reviewer accepted. The back-half reject binds a different bundle, so it
    does not supersede that accept. Without the phase filter the run reaches
    `merge_requested` with no implementation review having accepted anything.
    """
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.to_specified()
    spec = s.phase_artifact("r-1", "spec")
    f.author_round(PLAN_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "planned"

    def sub(name, key, actor, role, **payload):
        return submit(s, Command(
            name=name, run_id="r-1", expected_version=s.run_version("r-1"),
            idempotency_key=key,
            generation=dispatch(s, "r-1", actor=actor, role=role).generation,
            payload=payload))

    sub("start_implementation", "si", "mallory", Role.IMPLEMENTER)
    sub("record_implementation_output", "io", "mallory", Role.IMPLEMENTER,
        artifact_hash=spec)
    sub("record_ci_observation", "ci", "mallory", Role.IMPLEMENTER,
        status="success", head_git_sha="d" * 40)
    sub("record_review", "rv", "carol", Role.REVIEWER, phase="implementation",
        verdict="reject", artifact_hash=spec, base_sha="0" * 40,
        context_bundle_hash="f" * 64, policy_version=front.policy_version(s, "r-1"))
    assert s.run_state("r-1") == "reviewing"

    with pytest.raises(NotAuthorized, match="no accepted review"):
        sub("request_merge", "rm", "mallory", Role.IMPLEMENTER, pr=1, repo="o/r",
            head_git_sha="d" * 40, artifact_hash=spec, base_sha="0" * 40,
            context_bundle_hash=front.bundle_hash(s, "r-1"),
            policy_version=front.policy_version(s, "r-1"))
    assert s.run_state("r-1") == "reviewing"


def test_epoch_and_bundle_hash_queries(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    assert front.epoch(s, "r-1") == 0
    enq = s.newest_fact("r-1", EventKind.RUN_ENQUEUED)
    assert front.bundle_hash(s, "r-1") == enq.payload["bundle_hash"]
    s.append_fact(run_id="r-1", kind=EventKind.BUNDLE_REVISED, actor="human",
                  causal_command_id=None, payload={"bundle_hash": "b" * 64, "reason": "x"})
    assert front.epoch(s, "r-1") == 1
    assert front.bundle_hash(s, "r-1") == "b" * 64
