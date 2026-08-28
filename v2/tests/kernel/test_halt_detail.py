"""A halt must say what failed, not only that something did."""
import pytest

from kernel.dispatch import dispatch
from kernel.effects import UncertainEffect, perform
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.store import Store


def _run(tmp_path):
    store = Store.open(tmp_path / "k.db", clock=Clock(start_us=1))
    store.create_run(run_id="r", base_repo="o/r", base_sha="ab" * 20)
    gen = dispatch(store, "r", actor="codex", role="implementer").generation
    return store, gen


def test_a_halted_effect_records_WHAT_the_failure_said(tmp_path):
    """Diagnosing one halted publish cost three round-trips against the live
    world, because `error` held the exception's class name and nothing else."""
    store, gen = _run(tmp_path)

    def boom(effect_class, intent, key):
        raise RuntimeError("ref_update failed rc=128: src refspec does not match any")

    with pytest.raises(UncertainEffect):
        perform(store, "r", gen, "ref_update", "k1", {"argv": ["git", "push", "origin", "cafe:refs/heads/b"]}, boom)

    fact = next(f for f in store.facts_for("r")
                if f.kind == EventKind.EFFECT_UNCERTAIN)
    assert fact.payload["error"] == "RuntimeError"
    assert "src refspec does not match any" in fact.payload["detail"]


def test_the_detail_is_capped_so_a_pathological_stderr_cannot_bloat_the_journal(tmp_path):
    store, gen = _run(tmp_path)

    def boom(effect_class, intent, key):
        raise RuntimeError("x" * 5000)

    with pytest.raises(UncertainEffect):
        perform(store, "r", gen, "ref_update", "k2", {"argv": ["git", "push", "origin", "cafe:refs/heads/b"]}, boom)

    fact = next(f for f in store.facts_for("r")
                if f.kind == EventKind.EFFECT_UNCERTAIN)
    assert len(fact.payload["detail"]) == 500
