"""Failing tests for round 3b's findings."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from conftest import valid_argv
from kernel.effects import EffectClass, pending_reconciliation, reconcile
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, actor=None, **payload):
    """Dispatch, then submit under the generation the kernel handed back."""
    if name == "request_merge":
        # A merge authorization must name its target (round 6, C2). Defaulted
        # here because these tests are about other properties; the binding
        # itself is asserted in test_effect_contract.py.
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
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
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", spec_sha256=spec)
    _sub(s, "submit_plan", "k2", plan_sha256=put_artifact(s, b"# plan"))
    _sub(s, "start_implementation", "k3", actor="claude")
    _sub(s, "record_implementation_output", "k3o", actor="claude",
         artifact_hash=spec)
    return s, spec


# --- 1. a persisted `intended` effect must be recoverable --------------------

def test_a_persisted_intended_effect_can_be_reconciled():
    """A real process death after journalling never runs the handler that
    converts the row to `uncertain`, so an `intended` row must itself be
    reconcilable -- otherwise the effect can never be retried at all."""
    s = _store()
    gen = acquire(s, "r", "a")
    s.journal_intent("eff", "r", gen, EffectClass.PULL_REQUEST, "k", valid_argv(EffectClass.PULL_REQUEST))
    assert [e["idempotency_key"] for e in pending_reconciliation(s, "r")] == ["k"]
    reconcile(s, "r", "k", resolution="no_pr_created",
              expected_version=s.run_version("r"))
    assert s.effect_state("k", run_id="r") == "reconciled"


# --- 2. merge must require CI evidence ---------------------------------------

def test_merge_without_ci_evidence_is_refused():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "rv", verdict="accept",
         artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
         actor="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="CI"):
        _sub(s, "request_merge", "rm", head_git_sha=HEAD, artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE,
             policy_version=1)


def test_merge_with_a_failing_ci_observation_is_refused():
    s, spec = _to_implementing(_store())
    _sub(s, "record_ci_observation", "ci", status="failure", head_git_sha=HEAD)
    _sub(s, "record_review", "rv", verdict="accept",
         artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
         actor="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="CI"):
        _sub(s, "request_merge", "rm", head_git_sha=HEAD, artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE,
             policy_version=1)


# --- 3. a review must bind an artifact the kernel actually has ----------------

def test_a_review_over_an_unrecorded_artifact_is_refused():
    """An independent reviewer could still approve hashes for objects that do
    not exist, and merge compared one caller-supplied tuple with another."""
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized, match="artifact"):
        _sub(s, "record_review", "rv", verdict="accept",
             artifact_hash="f" * 64, base_sha=BASE, context_bundle_hash=BUNDLE,
             actor="codex", policy_version=1)


# --- 4. a revision request must allow revised implementation -----------------

def test_request_revision_allows_reimplementation():
    """Every record_review transitioned to `reviewing`, and
    start_implementation is legal only from `planned` -- so asking for a
    revision left nowhere to do it."""
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "rv", verdict="request_revision",
         artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
         actor="codex", policy_version=1)
    assert _sub(s, "start_implementation", "impl2",
                actor="claude").accepted


# --- 5. every authorization failure records a rejection ----------------------

@pytest.mark.parametrize("name,payload", [
    ("request_merge", {"head_git_sha": HEAD}),                       # illegal from queued
    ("submit_plan", {"plan_sha256": "b" * 64}),  # out of order
])
def test_authorization_failures_record_a_rejection_fact(name, payload):
    """Only StaleVersion emitted COMMAND_REJECTED. Illegal transitions,
    malformed bindings and failed merge authorization left no immutable
    record of the refusal."""
    s = _store()
    with pytest.raises(NotAuthorized):
        _sub(s, name, "k", **payload)
    rejects = [f for f in s.facts_for("r") if f.kind == "command_rejected"]
    assert rejects, f"{name} was refused with no rejection fact"
    assert rejects[0].payload["command_name"] == name


def test_a_review_validation_failure_also_records_a_rejection():
    """The parametrized cases above all fail inside authorize(); removing the
    rejection recording from the validate_review path left them green."""
    s, spec = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        # An artifact the store does not hold: fails in validate_review, not
        # in authorize.
        _sub(s, "record_review", "rv", verdict="accept",
             artifact_hash="f" * 64, base_sha=BASE, context_bundle_hash=BUNDLE,
             actor="codex", policy_version=1)
    rejects = [f for f in s.facts_for("r") if f.kind == "command_rejected"]
    assert any(f.payload["command_name"] == "record_review" for f in rejects), (
        "a review validation failure left no rejection fact"
    )


# --- verdict domain ----------------------------------------------------------

def test_an_unknown_verdict_is_refused():
    """record_review accepted arbitrary strings while only literal 'accept'
    authorized merge -- so a typo silently became a non-approval."""
    s, spec = _to_implementing(_store())
    with pytest.raises(NotAuthorized, match="verdict"):
        _sub(s, "record_review", "rv", verdict="lgtm",
             artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
             actor="codex", policy_version=1)
