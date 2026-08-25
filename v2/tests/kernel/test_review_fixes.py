"""Failing tests for the kernel review's findings. Written before the fixes."""

import pytest

from kernel import canon
from kernel.canon import CANON_VERSION, canonical_bytes, canonical_hash
from kernel.commands import Command, submit
from kernel.effects import (
    EffectClass, UncertainEffect, _perform_unhalted, is_halted, perform, reconcile,
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
        _perform_unhalted(s, "r", gen, EffectClass.COMMENT, "k2", {}, boom)
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
    # submit_spec on runB: same key, same name, different run. Uses a command
    # legal from `queued` so the test exercises key scoping rather than
    # tripping the state check.
    res = submit(s, Command(name="submit_spec", run_id="runB", expected_version=0,
                            idempotency_key="shared", generation=gB,
                            payload={"spec_sha256": "b" * 64}))
    assert not res.replayed, "runB's command was answered with runA's result"
    assert res.result["name"] == "submit_spec"


def test_reusing_a_key_for_a_different_command_in_one_run_is_refused():
    s = _store()
    g = acquire(s, "r", "a")
    submit(s, Command(name="submit_spec", run_id="r", expected_version=0,
                      idempotency_key="k", generation=g, payload={}))
    with pytest.raises(ValueError, match="idempotency"):
        submit(s, Command(name="submit_plan", run_id="r", expected_version=1,
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


# --- 3. submit() must be atomic across CAS + fact + record --------------------

def test_submit_is_atomic_across_its_three_writes(monkeypatch):
    """Three independent commits under autocommit leave a crash window: the
    version advances, no command row exists, and the client's at-least-once
    retry then gets StaleVersion for a command that WAS accepted -- the
    idempotency mechanism failing in exactly the crash it exists to survive.
    """
    import kernel.commands as commands

    s = _store()
    g = acquire(s, "r", "a")
    v_before = s.run_version("r")

    real_record = s.record_command

    def boom(*a, **k):
        raise RuntimeError("crash after the CAS")

    monkeypatch.setattr(s, "record_command", boom)
    with pytest.raises(RuntimeError):
        submit(s, Command(name="submit_spec", run_id="r", expected_version=v_before,
                          idempotency_key="k", generation=g, payload={}))
    monkeypatch.setattr(s, "record_command", real_record)

    assert s.run_version("r") == v_before, (
        "the version advanced despite the command failing; a retry will now "
        "get StaleVersion for a command that was never recorded"
    )
    assert not [f for f in s.facts_for("r") if f.kind == "command_accepted"], (
        "a COMMAND_ACCEPTED fact survived a failed submit"
    )


def test_a_retry_after_a_crashed_submit_succeeds():
    """The point of the transaction: the retry must be able to succeed."""
    s = _store()
    g = acquire(s, "r", "a")
    v = s.run_version("r")
    real_record = s.record_command
    s.record_command = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        submit(s, Command(name="submit_spec", run_id="r", expected_version=v,
                          idempotency_key="k", generation=g, payload={}))
    s.record_command = real_record
    assert submit(s, Command(name="submit_spec", run_id="r", expected_version=v,
                             idempotency_key="k", generation=g, payload={})).accepted


# --- 5b. COMMAND_REQUESTED is part of the spec's event stream -----------------

def test_command_requested_is_recorded():
    """Spec section 2 lists 'command requested' alongside accepted/rejected.
    Recording only outcomes means the audit cannot distinguish no retry from
    forty retries."""
    s = _store()
    g = acquire(s, "r", "a")
    submit(s, Command(name="submit_spec", run_id="r", expected_version=0,
                      idempotency_key="k", generation=g, payload={}))
    kinds = [f.kind for f in s.facts_for("r")]
    assert "command_requested" in kinds
    assert kinds.index("command_requested") < kinds.index("command_accepted")


def test_a_replayed_command_still_records_the_request():
    """A replay mutates nothing, but a fact is an observation, not a mutation:
    without it the audit cannot see the retry happened at all."""
    s = _store()
    g = acquire(s, "r", "a")
    c = Command(name="submit_spec", run_id="r", expected_version=0,
                idempotency_key="k", generation=g, payload={})
    submit(s, c)
    before = len([f for f in s.facts_for("r") if f.kind == "command_requested"])
    submit(s, c)
    after = len([f for f in s.facts_for("r") if f.kind == "command_requested"])
    assert after == before + 1, "the replayed attempt left no trace"


# --- 9. a payload key must not shadow the command's own fields ---------------

def test_a_payload_key_cannot_shadow_the_command_name():
    """Splatting the payload alongside the command's own keys let a payload
    field silently overwrite the recorded command name."""
    s = _store()
    g = acquire(s, "r", "a")
    submit(s, Command(
        name="submit_spec", run_id="r", expected_version=0, idempotency_key="k",
        generation=g, payload={"command_name": "request_merge", "generation": 999},
    ))
    fact = [f for f in s.facts_for("r") if f.kind == "command_accepted"][0]
    assert fact.payload["command_name"] == "submit_spec", (
        f"payload shadowed the command name: {fact.payload}"
    )
    assert fact.payload["generation"] == g


# --- 7. 'accept' must be enforced, not asserted about a fixture --------------

def test_recommendation_is_constrained_to_a_closed_set():
    """The old test built a dict with recommendation='accept' and asserted it
    was not 'merge' -- a property of its own fixture. Nothing constrained
    recommendation at all, so a decision could carry 'merge' unchallenged."""
    from kernel.decisions import DecisionRejected, validate_decision

    d = {
        "decision_id": "d", "run_id": "r", "decision_type": "review_ruling",
        "based_on": {
            "state_version": 1, "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "base_git_sha": "c" * 40, "head_git_sha": "d" * 40,
            "review_bundle_sha256": "e" * 64,
        },
        "finding_rulings": [], "recommendation": "merge",
    }
    obs = {**d["based_on"], "reviewer_identity": "codex",
           "implementer_identity": "claude"}
    with pytest.raises(DecisionRejected, match="recommendation"):
        validate_decision(None, d, obs)
