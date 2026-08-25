"""Failing tests for round 2's findings. Written before the fixes."""

import pytest

from kernel.commands import Command, submit
from kernel.effects import (
    EffectClass, UncertainEffect, _perform_unhalted, is_halted, perform, reconcile,
)
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


def _store(*runs):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    for r in runs or ("r",):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s


# --- 1. mark_effect must be scoped to the run --------------------------------

def test_confirming_one_runs_effect_does_not_touch_another_runs():
    """Reads and uniqueness were scoped per run; the UPDATE was not, so
    confirming run B's effect also confirmed run A's identically-keyed one."""
    s = _store("A", "B")
    gA, gB = dispatch(s, "A", actor="a", role=Role.IMPLEMENTER).generation, dispatch(s, "B", actor="b", role=Role.IMPLEMENTER).generation
    # A journals an intent that never completes.
    s.journal_intent("eff_a", "A", gA, EffectClass.COMMENT, "shared", {})
    perform(s, "B", gB, EffectClass.COMMENT, "shared", {}, lambda *a: "external-b")
    assert s.effect_state("shared", run_id="A") == "intended", (
        "run B's confirmation leaked into run A's effect"
    )
    assert s.effect_state("shared", run_id="B") == "confirmed"


# --- 2. a persisted `intended` effect must not be treated as a replay --------

def test_an_interrupted_effect_demands_reconciliation_rather_than_replaying():
    """A crash between journalling and confirmation leaves 'intended'. Treating
    that as a completed replay returns a null external id and neither executes
    nor demands reconciliation -- the run is silently wedged."""
    s = _store()
    gen = dispatch(s, "r", actor="a", role=Role.IMPLEMENTER).generation

    def interrupt(*a):
        raise KeyboardInterrupt("crash mid-effect")

    # The interrupt propagates unchanged -- swallowing a Ctrl-C would be worse
    # than the bug -- but the uncertainty is recorded before it does.
    with pytest.raises(KeyboardInterrupt):
        perform(s, "r", gen, EffectClass.PULL_REQUEST, "k", {}, interrupt)
    assert s.effect_state("k", run_id="r") == "uncertain", (
        "an interrupted effect was left unrecorded"
    )
    assert is_halted(s, "r"), "an interrupted effect did not halt the run"

    calls = []
    with pytest.raises((UncertainEffect, RuntimeError)):
        _perform_unhalted(s, "r", gen, EffectClass.PULL_REQUEST, "k", {},
                          lambda *a: calls.append(1) or "x")
    assert not calls, "the retry executed against an unresolved effect"


# --- 3. reconcile must be atomic ---------------------------------------------

def test_reconcile_is_atomic(monkeypatch):
    """CAS, effect update, halt clear and audit fact were four autocommitted
    operations: a crash could clear the safety halt and consume the version
    with no audit event."""
    s = _store()
    gen = dispatch(s, "r", actor="a", role=Role.IMPLEMENTER).generation
    with pytest.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.PULL_REQUEST, "k", {},
                lambda *a: (_ for _ in ()).throw(TimeoutError("no response")))
    v = s.run_version("r")

    real = s.append_fact
    monkeypatch.setattr(s, "append_fact", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("crash before the audit fact")))
    with pytest.raises(RuntimeError):
        reconcile(s, "r", "k", resolution="x", expected_version=v)
    monkeypatch.setattr(s, "append_fact", real)

    assert is_halted(s, "r"), "the halt was cleared without an audit fact"
    assert s.run_version("r") == v, "the version was consumed without an audit fact"
    assert s.effect_state("k", run_id="r") == "uncertain"


# --- 5. replay identity must cover the request, not just its name ------------

def test_reusing_a_key_for_the_same_command_with_a_different_payload_is_refused():
    """Replay compared only the stored name, so the same command with a
    DIFFERENT payload was answered with the first result."""
    s = _store()
    g = dispatch(s, "r", actor="a", role=Role.IMPLEMENTER).generation
    submit(s, Command(name="submit_spec", run_id="r", expected_version=0,
                      idempotency_key="k", generation=g, payload={"hash": "A"}))
    with pytest.raises(ValueError, match="idempotency"):
        submit(s, Command(name="submit_spec", run_id="r", expected_version=1,
                          idempotency_key="k", generation=g, payload={"hash": "B"}))


# --- 6. the halt bypass must not be reachable from the public API ------------

def test_perform_has_no_public_halt_bypass():
    """A comment saying production never passes _bypass_halt is not an
    enforcement. Any caller could continue mutating a halted run."""
    import inspect

    from kernel import effects

    assert "_bypass_halt" not in inspect.signature(effects.perform).parameters, (
        "perform() still exposes a halt bypass in its public signature"
    )
