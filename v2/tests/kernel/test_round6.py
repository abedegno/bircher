"""Round 6 findings. Each test fails before its fix.

C3, C4 and C6 were found by codex (gpt-5.6-sol at xhigh) executing probes, not
by reading. C3 is a gap I created in M1-3b by adding
`record_implementation_output` without moving the independence check onto it.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, actor_for, dispatch
from conftest import valid_argv
from kernel.effects import (
    EffectClass, UncertainEffect, _perform_unhalted, is_halted, perform,
)
from kernel.ids import Clock
from kernel.ownership import current_generation
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store(run="r"):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id=run, base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, actor, role, run="r", **p):
    return submit(s, Command(
        name=name, run_id=run, expected_version=s.run_version(run),
        idempotency_key=key,
        generation=dispatch(s, run, actor=actor, role=role).generation, payload=p))


# --- C3: independence must name the producer of the artifact under review ----

def _to_output(s, starter="alice", producer="alice"):
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", starter, Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", starter, Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", starter, Role.IMPLEMENTER)
    out = put_artifact(s, b"output produced by " + producer.encode())
    _sub(s, "record_implementation_output", "k4", producer, Role.IMPLEMENTER,
         artifact_hash=out)
    return out


def test_the_producer_of_the_artifact_cannot_review_it():
    """THE CODEX FINDING. alice starts the implementation; bob produces the
    artifact. Independence read the STARTER, so bob reviewed his own output
    and the merge executed."""
    s = _store()
    out = _to_output(s, starter="alice", producer="bob")
    with pytest.raises(NotAuthorized, match="independence"):
        _sub(s, "record_review", "rv", "bob", Role.REVIEWER, verdict="accept",
             artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
             policy_version=1)


def test_the_starter_of_the_implementation_also_cannot_review():
    """Kept as well as the producer: an actor mid-implementation on this run
    has a conflict even before it has produced anything."""
    s = _store()
    out = _to_output(s, starter="alice", producer="bob")
    with pytest.raises(NotAuthorized, match="independence"):
        _sub(s, "record_review", "rv", "alice", Role.REVIEWER, verdict="accept",
             artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
             policy_version=1)


def test_a_third_party_may_still_review():
    """The control. Refusing everyone would pass both tests above."""
    s = _store()
    out = _to_output(s, starter="alice", producer="bob")
    assert _sub(s, "record_review", "rv", "carol", Role.REVIEWER,
                verdict="accept", artifact_hash=out, base_sha=BASE,
                context_bundle_hash=BUNDLE, policy_version=1).accepted


# --- C4: a retried uncertain effect must halt the run ------------------------

def test_retrying_an_interrupted_effect_halts_the_run():
    """THE CODEX FINDING. A hard crash leaves `intended`. The retry raised
    UncertainEffect but never halted, so the run went on performing external
    effects while holding an unconfirmed mutation."""
    s = _store()
    g = dispatch(s, "r", actor="worker", role=Role.IMPLEMENTER).generation
    s.journal_intent("eff_crash", "r", g, EffectClass.PULL_REQUEST, "crashed",
                     {"argv": ["gh", "pr", "create"]})
    with pytest.raises(UncertainEffect):
        perform(s, "r", g, EffectClass.PULL_REQUEST, "crashed", valid_argv(EffectClass.PULL_REQUEST),
                lambda *a: "must-not-run")
    assert is_halted(s, "r"), "the run stayed live holding an unconfirmed effect"


def test_a_later_effect_cannot_execute_after_that_retry():
    """The consequence, asserted directly: the halt is only meaningful if it
    stops the next effect."""
    s = _store()
    g = dispatch(s, "r", actor="worker", role=Role.IMPLEMENTER).generation
    s.journal_intent("eff_crash", "r", g, EffectClass.PULL_REQUEST, "crashed", valid_argv(EffectClass.PULL_REQUEST))
    with pytest.raises(UncertainEffect):
        perform(s, "r", g, EffectClass.PULL_REQUEST, "crashed", valid_argv(EffectClass.PULL_REQUEST), lambda *a: "x")
    with pytest.raises(RuntimeError, match="reconcil"):
        perform(s, "r", g, EffectClass.COMMENT, "later", valid_argv(EffectClass.COMMENT),
                lambda *a: "comment-executed")


def test_the_halt_records_evidence_for_the_retried_effect():
    """A halt an operator cannot act on is a stall."""
    s = _store()
    g = dispatch(s, "r", actor="worker", role=Role.IMPLEMENTER).generation
    s.journal_intent("eff_crash", "r", g, EffectClass.REF_UPDATE, "crashed", valid_argv(EffectClass.REF_UPDATE))
    with pytest.raises(UncertainEffect):
        perform(s, "r", g, EffectClass.REF_UPDATE, "crashed", valid_argv(EffectClass.REF_UPDATE), lambda *a: "x")
    ev = s.reconciliation_evidence("r")
    assert ev and ev.get("run_id") == "r"
    assert "ref_update" in str(ev.get("affected_resources"))


# --- C6: dispatch must be atomic ---------------------------------------------

def test_a_failure_inside_dispatch_leaves_no_fenced_generation():
    """THE CODEX FINDING. record_dispatch failing after acquire() fenced a
    generation with no actor: every command under it is refused, and the state
    is indistinguishable from a caller self-fencing."""
    s = _store()
    before = current_generation(s, "r")

    def crash(*a, **k):
        raise RuntimeError("disk failure after fence")

    s.record_dispatch = crash
    with pytest.raises(RuntimeError, match="disk failure"):
        dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    assert current_generation(s, "r") == before, (
        "the fence advanced without a dispatch behind it"
    )


def test_a_successful_dispatch_still_advances_the_fence():
    """The control."""
    s = _store()
    before = current_generation(s, "r")
    d = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    assert d.generation > before
    assert actor_for(s, "r", d.generation) == "claude"
