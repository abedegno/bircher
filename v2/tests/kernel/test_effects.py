import pytest

from kernel.commands import Command, StaleVersion, submit
from kernel.effects import (
    EffectClass, UncertainEffect, is_halted, pending_reconciliation, perform, reconcile,
)
from kernel.ids import Clock
from kernel.ownership import OwnershipLost, acquire
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    for r in ("r", "other"):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s


class Recorder:
    """Fake executor. Records the journal state it OBSERVES, proving intent was
    persisted before the effect was attempted."""

    def __init__(self, store, fail=None):
        self.store, self.fail, self.calls = store, fail, []

    def __call__(self, effect_class, intent, idempotency_key):
        rows = self.store._conn.execute(
            "SELECT state FROM effects WHERE idempotency_key=?", (idempotency_key,)
        ).fetchall()
        self.calls.append(("executed", [r[0] for r in rows]))
        if self.fail:
            raise self.fail
        return "ext_123"


def test_the_eight_effect_classes_are_all_declared():
    """If this set shrinks, effects silently stop being journalled -- which is
    how PR creation was lost from an earlier revision of the journal."""
    assert sorted(EffectClass.ALL) == sorted([
        "ref_update", "pull_request", "status_check", "comment",
        "issue_or_label", "revert_or_recovery", "credential_lifecycle",
        "session_control",
    ])


def test_intent_is_persisted_before_the_effect_is_attempted(store):
    gen = acquire(store, "r", "a")
    rec = Recorder(store)
    perform(store, "r", gen, EffectClass.PULL_REQUEST, "k1", {"title": "x"}, rec)
    assert rec.calls == [("executed", ["intended"])]


def test_confirmed_effect_records_the_external_object_id(store):
    gen = acquire(store, "r", "a")
    perform(store, "r", gen, EffectClass.PULL_REQUEST, "k1", {}, Recorder(store))
    row = store._conn.execute(
        "SELECT state, external_object_id FROM effects WHERE idempotency_key='k1'"
    ).fetchone()
    assert row == ("confirmed", "ext_123")


def test_a_superseded_generation_cannot_perform_an_effect(store):
    stale = acquire(store, "r", "a")
    acquire(store, "r", "b")
    with pytest.raises(OwnershipLost):
        perform(store, "r", stale, EffectClass.REF_UPDATE, "k2", {}, Recorder(store))


def test_a_superseded_generation_leaves_no_journal_row(store):
    """Fencing the refusal without fencing the write would let a superseded
    attempt consume an idempotency key a live generation still needs."""
    stale = acquire(store, "r", "a")
    acquire(store, "r", "b")
    with pytest.raises(OwnershipLost):
        perform(store, "r", stale, EffectClass.REF_UPDATE, "k3", {}, Recorder(store))
    assert store._conn.execute(
        "SELECT COUNT(*) FROM effects WHERE idempotency_key='k3'"
    ).fetchone()[0] == 0


def test_an_uncertain_effect_blocks_retry_until_reconciled(store):
    """Two independent layers stop the retry, and both are asserted.

    The run-level halt catches it first (it gates every effect on a halted
    run). Underneath, the per-key uncertain check still refuses this specific
    key -- exercised by bypassing the halt, so removing either guard is
    visible here rather than masked by the other.
    """
    gen = acquire(store, "r", "a")
    with pytest.raises(UncertainEffect):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k4", {},
                Recorder(store, fail=TimeoutError("no response")))
    assert [e["idempotency_key"] for e in pending_reconciliation(store, "r")] == ["k4"]

    # Layer 1: the run-level halt.
    with pytest.raises(RuntimeError, match="reconcil"):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k4", {}, Recorder(store))

    # Layer 2: the per-key uncertain check, with the halt stood down.
    with pytest.raises(UncertainEffect, match="reconcil"):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k4", {}, Recorder(store),
                _bypass_halt=True)


def test_replaying_a_confirmed_key_does_not_re_execute(store):
    gen = acquire(store, "r", "a")
    rec = Recorder(store)
    perform(store, "r", gen, EffectClass.COMMENT, "k5", {}, rec)
    perform(store, "r", gen, EffectClass.COMMENT, "k5", {}, rec)
    assert len(rec.calls) == 1, "a confirmed effect was executed twice"


def _fail(store, run="r"):
    gen = acquire(store, run, "a")
    with pytest.raises(UncertainEffect):
        perform(store, run, gen, EffectClass.PULL_REQUEST, "k1", {},
                Recorder(store, fail=TimeoutError("no response")))
    return gen


def test_an_uncertain_effect_halts_the_run(store):
    _fail(store)
    assert is_halted(store, "r")


def test_the_halt_records_the_evidence_an_operator_needs(store):
    gen = _fail(store)
    ev = store.reconciliation_evidence("r")
    for key in ("run_id", "generation", "affected_resources",
                "last_confirmed_observations", "recommended_actions"):
        assert key in ev, f"evidence is missing {key}"
    assert ev["generation"] == gen


def test_unrelated_runs_continue(store):
    """Only the affected run halts. The wedge is per-run because an
    unconfirmed attempt holds that run's resources and nothing else."""
    _fail(store)
    assert not is_halted(store, "other")
    gen = acquire(store, "other", "b")
    assert submit(store, Command(
        name="submit_spec", run_id="other", expected_version=0,
        idempotency_key="ok", generation=gen, payload={},
    )).accepted


def test_resolution_is_an_audited_cas_command(store):
    _fail(store)
    v = store.run_version("r")
    with pytest.raises(StaleVersion):
        reconcile(store, "r", "k1", resolution="no_pr", expected_version=v + 5)
    reconcile(store, "r", "k1", resolution="no_pr", expected_version=v)
    assert not is_halted(store, "r")
    assert "effect_reconciled" in [f.kind for f in store.facts_for("r")]


def test_a_halted_run_refuses_further_commands(store):
    _fail(store)
    gen = acquire(store, "r", "a")
    with pytest.raises(RuntimeError, match="reconcil"):
        submit(store, Command(
            name="submit_spec", run_id="r", expected_version=1,
            idempotency_key="nope", generation=gen, payload={},
        ))
