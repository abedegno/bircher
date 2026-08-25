import pytest

from kernel.dispatch import Role, actor_for, actor_in_role, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_dispatch_binds_an_actor_to_the_generation_it_acquired(store):
    gen = acquire(store, "r", "attempt_1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    assert actor_for(store, "r", gen) == "claude"


def test_a_generation_with_no_dispatch_has_no_actor(store):
    """An ungated caller must not inherit somebody else's identity.

    The run MUST already hold a dispatch for another generation. Without
    one, a lookup that fell back to 'the most recent dispatch' would still
    return None -- for want of any row, not for want of the right one --
    and this test would pass while the property it names was broken.
    """
    acquire(store, "r", "dispatched")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    ungated = acquire(store, "r", "ungated")
    assert actor_for(store, "r", ungated) is None


def test_each_generation_gets_its_own_actor(store):
    """Exact-generation lookup. A fallback to the most recent dispatch is how
    one attempt inherits another attempt's identity."""
    g1 = acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    g2 = acquire(store, "r", "a2")
    dispatch(store, "r", actor="codex", role=Role.REVIEWER)
    assert actor_for(store, "r", g1) == "claude"
    assert actor_for(store, "r", g2) == "codex"


def test_role_lookup_returns_the_most_recent_holder(store):
    """Independence must compare against the CURRENT implementer, which the
    revision loop can change."""
    acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    acquire(store, "r", "a2")
    dispatch(store, "r", actor="gpt", role=Role.IMPLEMENTER)
    assert actor_in_role(store, "r", Role.IMPLEMENTER) == "gpt"


def test_dispatch_records_a_fact(store):
    acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    facts = [f for f in store.facts_for("r") if f.kind == "attempt_dispatched"]
    assert len(facts) == 1
    assert facts[0].payload["actor"] == "claude"
    assert facts[0].payload["role"] == Role.IMPLEMENTER


def test_dispatch_requires_a_known_role(store):
    acquire(store, "r", "a1")
    with pytest.raises(ValueError, match="role"):
        dispatch(store, "r", actor="claude", role="boss")


def test_dispatch_requires_a_named_actor(store):
    acquire(store, "r", "a1")
    with pytest.raises(ValueError, match="named actor"):
        dispatch(store, "r", actor="", role=Role.IMPLEMENTER)


def test_two_dispatches_for_one_generation_are_refused(store):
    """One generation, one actor. Two would make 'who did this' ambiguous."""
    import sqlite3

    acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    with pytest.raises(sqlite3.IntegrityError):
        dispatch(store, "r", actor="codex", role=Role.REVIEWER)
