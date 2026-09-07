import pytest

from kernel import front
from kernel.dispatch import PendingEffects, Role, SeatsExhausted, dispatch
from kernel.enqueue import create_run
from kernel.events import EventKind
from kernel.store import Store

ISSUE = {"number": 3, "title": "T", "body": "B", "labels": [], "comments": []}


def _run(tmp_path, cfg=None):
    s = Store.open(tmp_path / "k.db")
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
               issue=ISSUE, project_config=cfg or {})
    return s


def test_author_is_a_role():
    assert Role.AUTHOR == "author"
    assert Role.AUTHOR in Role.ALL
    assert Role.SEATS == frozenset({Role.AUTHOR, Role.REVIEWER})


def test_seat_bound_counts_author_and_reviewer_dispatches_only(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    dispatch(s, "r-1", actor="runner", role=Role.OPERATOR)
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert front.seats_used(s, "r-1") == 3
    assert front.seat_bound(s, "r-1") == 4
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    # Operator and implementer dispatches are not seats and are not bounded by them.
    dispatch(s, "r-1", actor="runner", role=Role.OPERATOR)
    dispatch(s, "r-1", actor="claude", role=Role.IMPLEMENTER)
    assert front.seats_used(s, "r-1") == 4


def test_a_grant_raises_the_bound_by_two(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    for _ in range(2):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_RULING, actor="human",
                  causal_command_id=None,
                  payload={"ruling": "grant_round", "phase": "spec"})
    assert front.grants(s, "r-1") == 1
    assert front.seat_bound(s, "r-1") == 6
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)


def test_a_non_grant_human_ruling_is_not_a_grant(tmp_path):
    s = _run(tmp_path)
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_RULING, actor="human",
                  causal_command_id=None, payload={"ruling": "request_revision"})
    assert front.grants(s, "r-1") == 0


def test_any_dispatch_over_an_intended_or_uncertain_effect_is_refused_and_halts(tmp_path):
    """spec §2 Refusals: refused, AND the run is halted with the keys as evidence."""
    from kernel.effects import is_halted
    s = _run(tmp_path)
    g = dispatch(s, "r-1", actor="runner", role=Role.OPERATOR).generation
    key = "sess-create:r-1:%d" % g
    s.journal_intent("e1", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-create", "run": "r-1",
                                                       "phase": "spec", "epoch": 0, "cause": "x"}})
    assert not is_halted(s, "r-1")
    for role in (Role.AUTHOR, Role.REVIEWER, Role.OPERATOR, Role.IMPLEMENTER):
        with pytest.raises(PendingEffects) as exc:
            dispatch(s, "r-1", actor="claude", role=role)
        assert exc.value.keys == [key]
    assert is_halted(s, "r-1")
    evidence = s.reconciliation_evidence("r-1")
    assert evidence["pending_keys"] == [key] and evidence["affected_resources"] == ["session_control"]
    s.clear_reconciliation("r-1")
    s.mark_effect(key, "uncertain", None, run_id="r-1")
    with pytest.raises(PendingEffects):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert is_halted(s, "r-1")
    s.clear_reconciliation("r-1")
    s.mark_effect(key, "confirmed", '{"id": "s1"}', run_id="r-1")
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert not is_halted(s, "r-1")


def test_refused_dispatch_leaves_no_generation_or_fact(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    for _ in range(2):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    before = len(s.dispatches_for("r-1")), len(s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED))
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    after = len(s.dispatches_for("r-1")), len(s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED))
    assert before == after
