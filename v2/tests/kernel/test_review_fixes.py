"""Failing tests for the kernel review's findings. Written before the fixes."""

import pytest

from kernel import canon
from kernel.canon import CANON_VERSION, canonical_bytes, canonical_hash
from kernel.commands import Command, submit
from kernel.effects import (
    EffectClass, UncertainEffect, is_halted, perform, reconcile,
)
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


def _store(*runs):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    for r in runs or ("r",):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s


def _halt(s, run="r", key="k1"):
    gen = acquire(s, run, "a")

    def boom(*a):
        raise TimeoutError("no response")

    with pytest.raises(UncertainEffect):
        perform(s, run, gen, EffectClass.PULL_REQUEST, key, {}, boom)
    return gen


# --- 1. the halt must gate effects, not only commands -------------------------

def test_a_halted_run_refuses_further_effects():
    """The halt exists to stop duplicate external mutations. Gating only
    submit() leaves the effect path -- the thing that mutates -- wide open."""
    s = _store()
    gen = _halt(s)
    assert is_halted(s, "r")
    with pytest.raises(RuntimeError, match="reconcil"):
        perform(s, "r", gen, EffectClass.COMMENT, "k2", {}, lambda *a: "ext_9")


def test_reconcile_refuses_an_effect_that_is_not_uncertain():
    """mark_effect is a bare UPDATE by key: reconciling a confirmed or
    unknown key silently succeeds, bumps the version and clears the halt."""
    s = _store()
    _halt(s)
    with pytest.raises(ValueError, match="uncertain"):
        reconcile(s, "r", "no-such-key", resolution="x",
                  expected_version=s.run_version("r"))


def test_reconcile_does_not_unhalt_while_another_effect_is_uncertain():
    s = _store()
    gen = _halt(s, key="k1")

    def boom(*a):
        raise TimeoutError("also fails")

    # A second uncertain effect on the same run.
    with pytest.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.COMMENT, "k2", {}, boom, _bypass_halt=True)
    reconcile(s, "r", "k1", resolution="x", expected_version=s.run_version("r"))
    assert is_halted(s, "r"), "run unhalted while k2 is still uncertain"


# --- 2. idempotency-key scope is per-run, and mismatches are loud -------------

def test_the_same_key_in_two_runs_is_not_a_replay():
    """Global scope silently returns one run's result to another run's
    command -- a misattribution of authority, not a replay."""
    s = _store("runA", "runB")
    gA = acquire(s, "runA", "a")
    submit(s, Command(name="submit_spec", run_id="runA", expected_version=0,
                      idempotency_key="shared", generation=gA, payload={}))
    gB = acquire(s, "runB", "b")
    res = submit(s, Command(name="request_merge", run_id="runB", expected_version=0,
                            idempotency_key="shared", generation=gB, payload={}))
    assert not res.replayed, "runB's command was answered with runA's result"
    assert res.result["name"] == "request_merge"


def test_reusing_a_key_for_a_different_command_in_one_run_is_refused():
    s = _store()
    g = acquire(s, "r", "a")
    submit(s, Command(name="submit_spec", run_id="r", expected_version=0,
                      idempotency_key="k", generation=g, payload={}))
    with pytest.raises(ValueError, match="idempotency"):
        submit(s, Command(name="request_merge", run_id="r", expected_version=1,
                          idempotency_key="k", generation=g, payload={}))


# --- 5. canonical form ---------------------------------------------------------

def test_float_dict_keys_are_rejected():
    """json.dumps stringifies a float key, so a platform-dependent float
    rendering lands in the canonical bytes -- exactly what the guard exists
    to prevent. The check recursed values but never keys."""
    with pytest.raises(TypeError):
        canonical_bytes({1.5: "x"})


def test_canon_version_is_actually_recorded_in_the_hash(monkeypatch):
    """The old test asserted the constant existed. It passed whether or not
    anything recorded it -- and nothing did."""
    before = canonical_hash({"a": 1})
    monkeypatch.setattr(canon, "CANON_VERSION", CANON_VERSION + 1)
    assert canonical_hash({"a": 1}) != before, (
        "changing CANON_VERSION did not change the hash; the version is not recorded"
    )
