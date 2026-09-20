"""The back half closes its own loop (spec §1-§3): refusals first."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.store import Store
from tests.kernel.front import Front

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    Front(s, "r", base_sha=BASE)
    return s


def _sub(s, name, key, actor=None, **payload):
    if name == "request_merge":
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    if name == "record_review":
        payload.setdefault("phase", "implementation")
    role = Role.REVIEWER if name == "record_review" else Role.IMPLEMENTER
    if actor is None:
        actor = "codex" if role == Role.REVIEWER else "claude"
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _to_implementing(s):
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    spec = put_artifact(s, b"# spec")
    _sub(s, "start_implementation", "k3", actor="claude")
    _sub(s, "record_implementation_output", "k3o", actor="claude", artifact_hash=spec)
    return s, spec


def _repair(s, key, **over):
    payload = {"cause": "ci_red", "head_sha": HEAD, "evidence": ["server (go)"]}
    payload.update(over)
    return _sub(s, "request_repair", key, actor="claude", **payload)


def test_request_repair_returns_an_implementing_run_to_planned():
    s, _ = _to_implementing(_store())
    assert _repair(s, "rr1").accepted
    assert s.run_state("r") == "planned"
    fact = [f for f in s.facts_for("r") if f.kind == EventKind.REPAIR_REQUESTED][-1]
    assert fact.payload["cause"] == "ci_red"
    assert fact.payload["head_sha"] == HEAD
    assert fact.payload["evidence"] == ["server (go)"]
    assert fact.payload["round"] == 1


def test_a_second_repair_counts_its_round():
    s, _ = _to_implementing(_store())
    _repair(s, "rr1")
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "rr2", cause="review_fail", evidence=["ab" * 20])
    facts = [f for f in s.facts_for("r") if f.kind == EventKind.REPAIR_REQUESTED]
    assert [f.payload["round"] for f in facts] == [1, 2]


def test_request_repair_is_refused_from_planned():
    s = _store()
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1")


def test_request_repair_is_refused_from_a_front_half_state():
    s = _store()
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1")


@pytest.mark.parametrize("bad", [
    {"cause": "gremlins"},
    {"head_sha": "abc"},
    {"evidence": "not a list"},
    {"evidence": [1, 2]},
])
def test_a_malformed_repair_request_is_refused(bad):
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1", **bad)


def test_request_repair_is_accepted_from_reviewing_and_returns_to_planned():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict="accept", artifact_hash=spec,
         base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD)
    assert s.run_state("r") == "reviewing"
    assert _repair(s, "rr1").accepted
    assert s.run_state("r") == "planned"


@pytest.mark.parametrize("verdict", ["accept", "request_revision", "reject"])
def test_no_back_half_verdict_transitions_the_run(verdict):
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict=verdict, artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD)
    assert s.run_state("r") == "reviewing"
    with pytest.raises(NotAuthorized):
        _sub(s, "start_implementation", "k9", actor="claude")


def _ci(s, key, status, head=HEAD, pr_state="open", jobs=None, pr="42"):
    return _sub(s, "record_ci_observation", key, actor="claude", status=status,
                head_git_sha=head, pr_state=pr_state, failing_jobs=list(jobs or []), pr=pr)


def test_the_verdict_stores_its_fingerprints():
    s, spec = _to_implementing(_store())
    fp = ["ab" * 20, "cd" * 20]
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec,
         base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1,
         head_sha=HEAD, fingerprints=fp)
    fact = [f for f in s.facts_for("r") if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["fingerprints"] == fp
    assert fact.schema_version == 3


def test_a_ci_observation_carries_the_prs_state_jobs_and_number():
    s, _ = _to_implementing(_store())
    assert _ci(s, "ci1", "failure", jobs=["server (go)", "client (node)"]).accepted
    with pytest.raises(NotAuthorized):
        _ci(s, "ci2", "failure", pr_state="draft")
    with pytest.raises(NotAuthorized):
        _ci(s, "ci3", "failure", pr="forty-two")


def test_fingerprints_must_be_a_list_of_strings_or_absent():
    s, spec = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _sub(s, "record_review", "v2", verdict="request_revision", artifact_hash=spec,
             base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1,
             head_sha=HEAD, fingerprints="not a list")
    with pytest.raises(NotAuthorized):
        _sub(s, "record_review", "v3", verdict="request_revision", artifact_hash=spec,
             base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1,
             head_sha=HEAD, fingerprints=[1, 2])


def test_failing_jobs_must_be_a_list_not_a_bare_string():
    """A bare string is iterable, so a shape check that only asked "is this
    iterable" would accept it and later split it into one job per
    character."""
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _sub(s, "record_ci_observation", "ci4", actor="claude", status="failure",
             head_git_sha=HEAD, failing_jobs="server (go)")


from kernel import back
from kernel.commands import HUMAN_GENERATION, execute_as_human


def _park(s, key, *, cause, evidence):
    return _sub(s, "park", key, actor="claude", reason="no_progress", cause=cause,
                evidence=list(evidence), session_id=None, cursor_item_id=None)


def _grant(s, key):
    return execute_as_human(s, Command(
        name="grant_round", run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=HUMAN_GENERATION, payload={"cursor_item_id": None}))


def test_back_reads_implementation_output_and_the_newest_observation():
    s = _store()
    assert not back.implementation_output_recorded(s, "r")
    s, _ = _to_implementing(s)
    assert back.implementation_output_recorded(s, "r")
    assert back.latest_ci(s, "r") is None
    _ci(s, "c1", "failure", jobs=["server (go)"])
    _ci(s, "c2", "success", pr_state="merged")
    assert back.latest_ci(s, "r")["status"] == "success"
    assert back.latest_pr_state(s, "r") == "merged"


def test_the_verdict_for_a_head_is_the_newest_on_that_head():
    s, spec = _to_implementing(_store())
    other = "f" * 40
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec,
         base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1, head_sha=other, fingerprints=["aa" * 20])
    assert back.verdict_for_head(s, "r", HEAD) is None
    assert back.verdict_for_head(s, "r", other).payload["fingerprints"] == ["aa" * 20]


def test_three_identical_repairs_in_a_row_is_no_progress():
    s, _ = _to_implementing(_store())
    ev = ["server (go)"]
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)
    _repair(s, "r1", evidence=ev)
    _sub(s, "start_implementation", "k4", actor="claude")
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)
    _repair(s, "r2", evidence=ev)
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ev)
    assert not back.would_be_third_identical(s, "r", "ci_red", ["client (node)"])
    assert not back.would_be_third_identical(s, "r", "review_fail", ev)


def test_evidence_order_does_not_matter():
    s, _ = _to_implementing(_store())
    _repair(s, "r1", evidence=["a", "b"])
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=["b", "a"])
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ["b", "a"])


def test_a_round_grant_resets_the_window():
    s, _ = _to_implementing(_store())
    ev = ["server (go)"]
    _repair(s, "r1", evidence=ev)
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=ev)
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ev)
    _park(s, "p1", cause="ci_red", evidence=ev)
    _grant(s, "g1")
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)


def test_no_progress_park_needs_the_journal_to_say_so():
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _park(s, "p0", cause="ci_red", evidence=["server (go)"])


def test_park_and_grant_are_legal_in_the_back_half():
    s, _ = _to_implementing(_store())
    ev = ["x"]
    _repair(s, "r1", evidence=ev); _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=ev); _sub(s, "start_implementation", "k5", actor="claude")
    assert _park(s, "p1", cause="ci_red", evidence=ev).accepted
    assert s.run_state("r") == "implementing"
    assert _grant(s, "g1").accepted


def test_no_progress_park_is_refused_from_the_front_half():
    s = _store()
    with pytest.raises(NotAuthorized):
        _park(s, "p1", cause="ci_red", evidence=["x"])


def test_a_run_with_an_open_pr_cannot_end():
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="open")
    with pytest.raises(NotAuthorized):
        _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed")


def test_an_unobserved_pr_counts_as_open():
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed")


def test_a_closed_pr_lets_the_run_end():
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="closed")
    assert _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed").accepted
    assert s.run_state("r") == "ended"


def test_a_run_without_an_implementation_still_ends():
    s = _store()
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    assert _sub(s, "record_run_outcome", "o1", actor="claude", outcome="timeout").accepted


def test_a_ci_observation_is_accepted_from_planned():
    """F2. A repair was requested and the run sits at `planned` with its
    session not yet started. A CI observation is a fact about the WORLD, not
    about the phase, and the ending rule reads the newest one -- so a run that
    cannot record here has `latest_pr_state` frozen at whatever it last saw
    and can never satisfy the rule."""
    s, _ = _to_implementing(_store())
    _repair(s, "rr1")
    assert s.run_state("r") == "planned"
    assert _ci(s, "c1", "failure").accepted
    assert back.latest_ci(s, "r")["status"] == "failure"


def test_a_run_at_planned_whose_pr_is_closed_can_end():
    """F2, the consequence: the ending rule depends on the observation being
    writable from every state the loop resumes from."""
    s, _ = _to_implementing(_store())
    _repair(s, "rr1")
    assert s.run_state("r") == "planned"
    _ci(s, "c1", "failure", pr_state="closed")
    assert _sub(s, "record_run_outcome", "o1", actor="claude", outcome="escalated").accepted
    assert s.run_state("r") == "ended"


def test_conflicted_actors_names_the_implementer():
    """F1. What the runner has to read before it seats a reviewer."""
    s, _ = _to_implementing(_store())
    assert back.conflicted_actors(s, "r") == {"claude"}


def test_conflicted_actors_delegates_to_authz(monkeypatch):
    """F1. ONE source of truth. A reimplementation in `back` would drift from
    the rule the kernel actually refuses on, and the drift would be invisible:
    the seating would look right and every verdict would be refused."""
    from kernel import authz
    monkeypatch.setattr(authz, "_conflicted_actors", lambda store, run_id: {"sentinel"})
    s, _ = _to_implementing(_store())
    assert back.conflicted_actors(s, "r") == {"sentinel"}


def test_a_cancelled_run_ends():
    # The generation is taken BEFORE the cancel: the runner records the
    # terminal fact under the generation it already holds, and `dispatch`
    # may refuse a seat on a cancelled run.
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="open")
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    assert submit(s, Command(name="cancel_run", run_id="r", expected_version=s.run_version("r"),
                             idempotency_key="x1", generation=gen, payload={})).accepted
    assert submit(s, Command(name="record_run_outcome", run_id="r", expected_version=s.run_version("r"),
                             idempotency_key="o1", generation=gen, payload={"outcome": "escalated"})).accepted
    assert s.run_state("r") == "ended"
