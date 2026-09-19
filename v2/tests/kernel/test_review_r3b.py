"""Failing tests for round 3b's findings."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from conftest import valid_argv
from kernel.effects import EffectClass, pending_reconciliation, reconcile
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store
from tests.kernel.front import Front

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    # Through the driver, so the run has the frozen policy the front half's
    # guards read and the base_sha every review below binds.
    Front(s, "r", base_sha=BASE)
    return s


def _sub(s, name, key, actor=None, **payload):
    """Dispatch, then submit under the generation the kernel handed back."""
    if name == "request_merge":
        # A merge authorization must name its target (round 6, C2). Defaulted
        # here because these tests are about other properties; the binding
        # itself is asserted in test_effect_contract.py.
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    if name == "record_review":
        # Every review here is of an implementation output, and a review must
        # name the phase of the state it is recorded from.
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
    # `planned` is the driver's to reach: a submit lands at *_submitted and
    # only a reviewer's accept of the current hash moves the run on.
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    spec = put_artifact(s, b"# spec")
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
    ("submit_plan", {"artifact_hash": "b" * 64}),  # out of order
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


# --- gate integrity: the verdict records what it reviewed --------------------

#: The store `command()` below dispatches and reads against. Set by the
#: `store_and_run` fixture -- one store per test, the same module-global
#: pattern test_authorization.py uses for its FRONT driver, because `command`
#: (unlike `_sub` above) is called from inside a test body without a store to
#: hand it: the caller wants a `Command` back to extend, not a submitted one.
_ACTIVE_STORE = None


@pytest.fixture
def store_and_run():
    """A run at `reviewing`, implementation phase, with a passing CI
    observation -- the setup
    `test_request_merge_with_an_accepted_review_is_authorized`
    (test_authorization.py) builds before recording its review."""
    global _ACTIVE_STORE
    s, _ = _to_implementing(_store())
    _sub(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _ACTIVE_STORE = s
    return s, "r"


def command(name, run_id, payload, key=None, actor=None):
    """Build, but do not submit, a `Command` -- the same dispatch `_sub`
    performs above, returned instead of submitted so a test can extend the
    payload first."""
    role = Role.REVIEWER if name == "record_review" else Role.IMPLEMENTER
    if actor is None:
        actor = "codex" if role == Role.REVIEWER else "claude"
    return Command(
        name=name, run_id=run_id, expected_version=_ACTIVE_STORE.run_version(run_id),
        idempotency_key=key or f"{name}-cmd",
        generation=dispatch(_ACTIVE_STORE, run_id, actor=actor, role=role).generation,
        payload=payload,
    )


def _accept_payload(store, run_id):
    """The artifact/base/context binding an `accept` verdict must carry: the
    run's current output (what `record_implementation_output` recorded in
    `store_and_run`), the driver's base_sha, and its context bundle -- the
    same tuple `test_request_merge_with_an_accepted_review_is_authorized`
    binds."""
    return {
        "verdict": "accept",
        "phase": "implementation",
        "artifact_hash": store.current_artifact(run_id),
        "base_sha": BASE,
        "context_bundle_hash": BUNDLE,
        "policy_version": 1,
    }


def test_the_verdict_records_what_it_reviewed(store_and_run):
    store, run_id = store_and_run
    payload = _accept_payload(store, run_id)   # the helper's artifact/base/context binding
    payload.update({"head_sha": "e" * 40, "merge_base_sha": "2" * 40, "delta_digest": "d" * 64})
    submit(store, command("record_review", run_id, payload))
    fact = [f for f in store.facts_for(run_id) if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["head_sha"] == "e" * 40
    assert fact.payload["merge_base_sha"] == "2" * 40
    assert fact.payload["delta_digest"] == "d" * 64
    assert fact.schema_version == 2


def test_a_verdict_without_a_range_records_none_not_garbage(store_and_run):
    store, run_id = store_and_run
    submit(store, command("record_review", run_id, _accept_payload(store, run_id)))
    fact = [f for f in store.facts_for(run_id) if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["head_sha"] is None
    assert fact.payload["merge_base_sha"] is None
    assert fact.payload["delta_digest"] is None
