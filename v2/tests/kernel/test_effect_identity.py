"""An effect the journal cannot attribute is an unattributable mutation.

Commands got their identity substrate in M1-3b; effects did not, and effects
are the half of the system that touches the world. Every effect fact recorded
`actor="kernel"`, which the spec permits only for facts the kernel originates
itself.
"""

import pytest

from kernel.authz import NotAuthorized
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, UncertainEffect, perform
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def _never_runs(*a):
    raise AssertionError("the executor ran for a refused effect")


def test_an_effect_fact_names_the_dispatched_actor():
    s = _store()
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    perform(s, "r", gen, EffectClass.COMMENT, "k", {}, lambda *a: "ok")
    facts = [f for f in s.facts_for("r") if f.kind.startswith("effect_")]
    assert facts, "no effect facts recorded at all"
    assert {f.actor for f in facts} == {"claude"}, (
        "the effect journal cannot say who requested the mutation"
    )


def test_an_uncertain_effect_also_names_who_asked():
    """The fact that matters most for an operator: an unconfirmed external
    mutation whose author is unknown is the worst row in the journal."""
    s = _store()
    gen = dispatch(s, "r", actor="codex", role=Role.REVIEWER).generation
    with pytest.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.PULL_REQUEST, "k", {},
                lambda *a: (_ for _ in ()).throw(TimeoutError("no response")))
    uncertain = [f for f in s.facts_for("r") if f.kind == "effect_uncertain"]
    assert [f.actor for f in uncertain] == ["codex"]


def test_an_undispatched_generation_cannot_perform_an_effect():
    """Fail closed, exactly as submit() does.

    The run MUST already hold a dispatch, so a lookup that fell back to the
    most recent one has something wrong to return.
    """
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    with pytest.raises(NotAuthorized, match="no dispatched actor"):
        perform(s, "r", self_fenced, EffectClass.COMMENT, "k", {}, _never_runs)


def test_a_refused_effect_does_not_execute():
    """Refusing after executing is not refusing. `_never_runs` is the witness
    in the test above; this one witnesses the filesystem-visible half."""
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    ran = []
    with pytest.raises(NotAuthorized):
        perform(s, "r", self_fenced, EffectClass.COMMENT, "k", {},
                lambda *a: ran.append(1))
    assert not ran


def test_a_refused_effect_consumes_no_idempotency_key():
    """An unattributable caller must not burn a key a live attempt needs."""
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    with pytest.raises(NotAuthorized):
        perform(s, "r", self_fenced, EffectClass.COMMENT, "shared", {}, _never_runs)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    assert perform(s, "r", gen, EffectClass.COMMENT, "shared", {},
                   lambda *a: "ok") == "ok"


def test_reconciliation_stays_a_human_fact():
    """An operator resolving a halt is a fact about a person, not about a
    dispatched attempt."""
    from kernel.effects import reconcile

    s = _store()
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.PULL_REQUEST, "k", {},
                lambda *a: (_ for _ in ()).throw(TimeoutError("no response")))
    reconcile(s, "r", "k", "closed by hand", s.run_version("r"))
    fact = [f for f in s.facts_for("r") if f.kind == "effect_reconciled"][0]
    assert fact.actor == "human"
