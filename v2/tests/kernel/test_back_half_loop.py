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
