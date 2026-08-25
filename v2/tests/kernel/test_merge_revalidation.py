"""Reaching a state records that authorization happened; it is not it.

Round 5 drove a run to `merge_requested` legitimately, deleted the reviewed
artifact, and the merge effect still executed -- because the effect path
checked the state NAME and nothing else. Between the transition and the
effect the world moves, and the authorized binding has to travel with the
decision.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized, latest_merge_authorization
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _sub(s, name, key, actor, role, **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _authorized_merge():
    """A run legitimately at merge_requested. Returns (store, artifact)."""
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    _sub(s, "request_merge", "k7", "claude", Role.IMPLEMENTER, head_git_sha=HEAD,
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    assert s.run_state("r") == "merge_requested"
    return s, out


def _merge(s, key="m"):
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    return perform(s, "r", gen, EffectClass.MERGE, key, {}, lambda *a: "merged!")


# --- the control --------------------------------------------------------------

def test_an_authorized_merge_executes():
    """Without this, refusing everything would pass every test below."""
    s, _ = _authorized_merge()
    assert _merge(s) == "merged!"


def test_the_kernel_records_what_it_authorized():
    s, out = _authorized_merge()
    authorized = latest_merge_authorization(s, "r")
    assert authorized["artifact_hash"] == out
    assert authorized["head_git_sha"] == HEAD


# --- the world moves between the decision and the effect ----------------------

def test_deleting_the_reviewed_artifact_stops_the_merge():
    """THE ROUND-5 EXPLOIT, verbatim."""
    s, out = _authorized_merge()
    s.delete_blob(out)
    assert not s.has_artifact(out), "the mutation must actually apply"
    with pytest.raises(NotAuthorized, match="no longer held"):
        _merge(s)


def test_a_revision_landing_after_authorization_stops_the_merge():
    s, out = _authorized_merge()
    v2 = put_artifact(s, b"diff v2")
    s.set_current_artifact("r", v2)
    with pytest.raises(NotAuthorized, match="current output moved"):
        _merge(s)


def test_a_withdrawn_approval_stops_the_merge():
    """The verdict is superseded after merge_requested was reached."""
    s, out = _authorized_merge()
    from kernel.artifacts import VerdictBinding, binding_hash
    from kernel.events import EventKind

    wanted = binding_hash(VerdictBinding(
        artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
        policy_version=1,
    ))
    s.append_fact(run_id="r", kind=EventKind.REVIEW_VERDICT, actor="codex",
                  causal_command_id=None,
                  payload={"verdict": "reject", "binding_hash": wanted,
                           "reviewer_identity": "codex"})
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _merge(s)


def test_ci_superseded_by_a_failure_stops_the_merge():
    s, _ = _authorized_merge()
    from kernel.events import EventKind

    s.append_fact(run_id="r", kind=EventKind.COMMAND_ACCEPTED, actor="claude",
                  causal_command_id=None,
                  payload={"command_name": "record_ci_observation",
                           "payload": {"status": "failure",
                                       "head_git_sha": HEAD}})
    with pytest.raises(NotAuthorized, match="no longer green"):
        _merge(s)


def test_the_reviewer_becoming_the_implementer_stops_the_merge():
    """Independence can lapse after the approval: a later implementation
    attempt by the reviewer makes the standing approval a self-review."""
    s, out = _authorized_merge()
    from kernel.events import EventKind

    s.append_fact(run_id="r", kind=EventKind.COMMAND_ACCEPTED, actor="codex",
                  causal_command_id=None,
                  payload={"command_name": "start_implementation", "payload": {}})
    with pytest.raises(NotAuthorized, match="both reviewer and implementer"):
        _merge(s)


def test_a_merge_requested_state_with_no_authorization_behind_it_is_refused():
    """Fail closed. The state is reached WITHOUT request_merge -- the shape a
    restore, a migration or a direct write leaves behind. The old gate read
    the state name, so this merged."""
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    s.set_run_state("r", "merge_requested")
    assert s.run_state("r") == "merge_requested", "the setup must actually apply"
    with pytest.raises(NotAuthorized, match="nothing to check"):
        _merge(s)
