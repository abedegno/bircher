import json

import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def _stop_key(sid, g):
    return f"sess-stop:{sid}:{g}"


def test_output_refused_before_turn_ended(tmp_path):
    """§11: nothing a turn produced is recorded before its end is a fact."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    h = put_artifact(s, SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_model_question", {"question_id": "q1", "question": "?"})
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_author_empty", {"session": sid})
    f._end_turn(g, sid)
    f._cmd(g, "submit_spec", {"artifact_hash": h})
    assert s.run_state("r-1") == "spec_submitted"


def test_output_refused_while_stop_intended(tmp_path):
    """§11: the turn ended, but its stop is only intended -- still refused."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    key = _stop_key(sid, g)
    s.journal_intent(f"e-{key}", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
                      "body": {"event": "stop_session"}})
    h = put_artifact(s, SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="sess-stop"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    s.mark_effect(key, "uncertain", None, run_id="r-1")
    with pytest.raises(NotAuthorized, match="sess-stop"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    s.mark_effect(key, "confirmed", "", run_id="r-1")
    f._cmd(g, "submit_spec", {"artifact_hash": h})


def test_a_reconciled_delivered_stop_satisfies(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    key = _stop_key(sid, g)
    s.journal_intent(f"e-{key}", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
                      "body": {"event": "stop_session"}})
    s.mark_effect(key, "reconciled", "delivered", run_id="r-1")
    assert front.stop_satisfied_for(s, "r-1", te.id) is True
    f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})


def test_review_ruling_needs_the_reviewers_turn_ended_and_stopped(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    sid = f._session(g, f._newest_id())
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"),
               "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_review", payload)
    f._end_turn(g, sid)
    f._cmd(g, "record_review", payload)
    assert s.run_state("r-1") == "specified"


def test_output_guard_reads_the_sessions_own_prompt(tmp_path):
    """Ruling 15: a newer prompt to ANOTHER session of the phase (a reply to
    the author while a reviewer seat runs) does not refuse the seat's ruling."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    a_gen = f._dispatch(Role.AUTHOR, "claude")
    a_sid = f._session(a_gen, f._newest_id()); f._end_turn(a_gen, a_sid)
    f._cmd(a_gen, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    g = f._dispatch(Role.REVIEWER, "codex")
    r_sid = f._session(g, f._newest_id()); f._end_turn(g, r_sid)
    f._prompt(g, a_sid, f._newest_id())                    # a newer prompt to the author session
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"ok")}
    f._cmd(g, "record_review", payload)
    assert s.run_state("r-1") == "specified"


def test_a_human_ruling_passes_the_turn_guard(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")       # a live author session, turn not ended
    f._session(g, f._newest_id())
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})
    assert s.run_state("r-1") == "queued"


def test_turn_ended_names_the_awaited_session_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    with pytest.raises(NotAuthorized, match="newest satisfied sess-prompt"):
        f._cmd(g, "record_turn_ended", {"session": "s-other", "ended": "file"})
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    with pytest.raises(NotAuthorized, match="one end per turn"):
        f._cmd(g, "record_turn_ended", {"session": sid, "ended": "cap"})
    # A second prompt to the same session is a second turn with its own end.
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    f._journal(g, _stop_key(sid, g), {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid,
                                                                         "cause": te.id},
                                       "body": {"event": "stop_session"}}, "")
    g2 = f._dispatch(Role.AUTHOR, "claude")
    f._prompt(g2, sid, f._newest_id())
    f._cmd(g2, "record_turn_ended", {"session": sid, "ended": "file"})
    assert len(s.facts_of_kind("r-1", EventKind.TURN_ENDED)) == 2


def test_author_empty_names_the_current_author_session_and_retries_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g1 = f._dispatch(Role.AUTHOR, "claude")
    s1 = f._session(g1, f._newest_id())
    f._end_turn(g1, s1, "file")
    with pytest.raises(NotAuthorized, match="newest"):
        f._cmd(g1, "record_author_empty", {"session": "s-not-it"})
    f._cmd(g1, "record_author_empty", {"session": s1})
    empty = s.newest_fact("r-1", EventKind.AUTHOR_EMPTY)
    assert empty.payload == {"session": s1, "phase": "spec", "epoch": 0, "generation": g1}
    # The retry: a fresh session whose cause is that fact.
    g2 = f._dispatch(Role.AUTHOR, "claude")
    s2 = f._session(g2, empty.id)
    f._end_turn(g2, s2, "file")
    with pytest.raises(NotAuthorized, match="retry"):
        f._cmd(g2, "record_author_empty", {"session": s2})
    # Naming the older session is refused: it is not the newest author session.
    with pytest.raises(NotAuthorized, match="newest"):
        f._cmd(g2, "record_author_empty", {"session": s1})


def test_author_empty_needs_the_author_role_and_a_front_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        f._cmd(g, "record_author_empty", {"session": "s-1"})


def test_direction_ordering_by_seq(tmp_path):
    """§11: a human_direction newer than the generation's attempt_dispatched
    (by journal seq) refuses the submit; one older does not."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.direct("use sqlite")                          # older than the dispatch below
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._end_turn(g, sid)
    h = put_artifact(s, SPEC_BYTES)
    f._cmd(g, "submit_spec", {"artifact_hash": h})
    assert s.run_state("r-1") == "spec_submitted"
    f.review_round("request_revision")
    g2 = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g2, f._newest_id())
    f._end_turn(g2, sid2)
    f.direct("no, postgres")                        # newer than g2's dispatch
    h2 = put_artifact(s, SPEC_BYTES + b"\nv2\n")
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g2, "submit_spec", {"artifact_hash": h2})
    assert s.run_state("r-1") == "queued"
    # The direction's own round submits fine.
    g3 = f._dispatch(Role.AUTHOR, "claude")
    sid3 = f._session(g3, f._newest_id())
    f._end_turn(g3, sid3)
    f._cmd(g3, "submit_spec", {"artifact_hash": h2})
    assert s.run_state("r-1") == "spec_submitted"


def test_direction_ordering_is_by_seq_not_by_time(tmp_path):
    """Every fact carries the same observed_at_us under a zero-step clock, so
    only the journal's seq can order the direction against the dispatch.
    (The facts table refuses UPDATE, so the tie is made by the clock.)"""
    from kernel.ids import Clock
    s = Store.open(tmp_path / "k.db", clock=Clock(start_us=1_000, step_us=0))
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._end_turn(g, sid)
    d_seq = front.dispatch_seq(s, "r-1", g)
    f.direct("later")
    direction = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    dispatched = [x for x in s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED) if x.seq == d_seq][0]
    assert direction.observed_at_us == dispatched.observed_at_us
    assert front.direction_after(s, "r-1", d_seq).seq == direction.seq
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})


def test_submit_refused_when_the_generation_has_no_attempt_dispatched_fact(tmp_path):
    """The direction clause's dispatch_seq lookup: a generation with a
    dispatches-table row but no attempt_dispatched fact -- unreachable via
    dispatch() itself, since Task 6 writes both in the fence's one
    transaction -- is refused with NotAuthorized naming what is missing, not
    a bare LookupError escaping submit()."""
    from kernel.ids import new_id
    from kernel.ownership import acquire

    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.direct("first")
    g = acquire(s, "r-1", "claude")
    s.record_dispatch(new_id("dsp"), "r-1", g, "claude", Role.AUTHOR)
    sid = f._session(g, f._newest_id())
    f._end_turn(g, sid)
    h = put_artifact(s, SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="attempt_dispatched"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    rejected = s.facts_of_kind("r-1", EventKind.COMMAND_REJECTED)
    assert rejected and rejected[-1].payload["reason"] == "NotAuthorized"
