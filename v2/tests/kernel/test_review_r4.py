"""Failing tests for round 4's findings."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.effects import EffectClass, UncertainEffect, perform
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, owner="impl", **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=acquire(s, "r", owner), payload=payload,
    ))


def _to_reviewing(s, implementer="claude"):
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "a1", spec_sha256=spec)
    _sub(s, "submit_plan", "a2", plan_sha256=put_artifact(s, b"# plan"))
    _sub(s, "start_implementation", "a3", implementer_identity=implementer)
    return spec


def _review(s, key, verdict, artifact, reviewer="codex", head=HEAD):
    _sub(s, "record_review", key, owner=reviewer, verdict=verdict,
         artifact_hash=artifact, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity=reviewer, policy_version=1, head_git_sha=head)


# --- 2. independence must use the CURRENT implementer ------------------------

def test_a_second_implementer_cannot_review_its_own_revision():
    """_implementer_of returned the FIRST start_implementation fact, so the
    revision loop added last round let a later implementer approve itself."""
    s = _store()
    spec = _to_reviewing(s, implementer="claude")
    _review(s, "rv1", "request_revision", spec)
    _sub(s, "start_implementation", "a4", implementer_identity="codex")
    with pytest.raises(NotAuthorized, match="independen"):
        _review(s, "rv2", "accept", spec, reviewer="codex")


# --- 3. the latest verdict wins ----------------------------------------------

def test_a_later_rejection_invalidates_an_earlier_acceptance():
    """Merge authorization scanned for ANY historical accept with a matching
    binding, ignoring a later reject for the same binding."""
    s = _store()
    spec = _to_reviewing(s)
    _sub(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _review(s, "rv1", "accept", spec)
    _review(s, "rv2", "reject", spec)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _sub(s, "request_merge", "rm", head_git_sha=HEAD, artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, reviewer_identity="codex",
             policy_version=1)


# --- 4. CI must be bound to the reviewed head --------------------------------

def test_ci_success_on_a_different_head_does_not_authorize_merge():
    """_ci_is_green read `status` and discarded head_git_sha, so green CI on
    an unrelated or older head authorized the merge."""
    s = _store()
    spec = _to_reviewing(s)
    _sub(s, "record_ci_observation", "ci", status="success", head_git_sha="9" * 40)
    _review(s, "rv", "accept", spec)
    with pytest.raises(NotAuthorized, match="CI"):
        _sub(s, "request_merge", "rm", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, reviewer_identity="codex",
             policy_version=1, head_git_sha=HEAD)


# --- 6. a halted run's refusal is also recorded ------------------------------

def test_a_halt_refusal_records_a_rejection_fact():
    """The 'every authorization failure is recorded' claim excluded the halt,
    which is refused before the try/except."""
    s = _store()
    gen = acquire(s, "r", "a")
    with pytest.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.PULL_REQUEST, "eff", {},
                lambda *a: (_ for _ in ()).throw(TimeoutError("no response")))
    with pytest.raises(RuntimeError, match="reconcil"):
        _sub(s, "submit_spec", "k", spec_sha256=put_artifact(s, b"x"))
    rejects = [f for f in s.facts_for("r") if f.kind == "command_rejected"]
    assert any(f.payload.get("reason") == "halted" for f in rejects), (
        f"halt refusal left no rejection fact: {[f.payload for f in rejects]}"
    )


# --- 1. the effect path must be gated, not only the command path -------------

def test_a_merge_effect_cannot_execute_without_kernel_authorization():
    """perform() accepted any effect class from any run state after checking
    only the ownership generation, so a current owner could execute a merge
    without an accepted verdict, green CI, or reaching merge_requested at all.
    The authorization protected a state transition, not the authority-bearing
    external effect."""
    s = _store()
    _to_reviewing(s)
    gen = acquire(s, "r", "impl")
    with pytest.raises(NotAuthorized, match="merge"):
        perform(s, "r", gen, EffectClass.MERGE, "m", {}, lambda *a: "merged!")


def test_a_merge_effect_executes_once_the_kernel_has_authorized_it():
    s = _store()
    spec = _to_reviewing(s)
    _sub(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _review(s, "rv", "accept", spec)
    _sub(s, "request_merge", "rm", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, reviewer_identity="codex",
         policy_version=1, head_git_sha=HEAD)
    gen = acquire(s, "r", "impl")
    assert perform(s, "r", gen, EffectClass.MERGE, "m", {},
                   lambda *a: "merged!") == "merged!"


# --- 5. a merge outcome must reference a confirmed effect --------------------

def test_record_merge_outcome_requires_a_confirmed_merge_effect():
    """Any current owner could assert outcome='merged' and produce the
    terminal state without a merge ever having happened."""
    s = _store()
    spec = _to_reviewing(s)
    _sub(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _review(s, "rv", "accept", spec)
    _sub(s, "request_merge", "rm", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, reviewer_identity="codex",
         policy_version=1, head_git_sha=HEAD)
    with pytest.raises(NotAuthorized, match="confirmed"):
        _sub(s, "record_merge_outcome", "mo", outcome="merged")
