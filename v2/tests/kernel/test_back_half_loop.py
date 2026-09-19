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
