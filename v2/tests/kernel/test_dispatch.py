import pytest

from kernel.dispatch import Role, actor_for, dispatch, role_for
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_dispatch_fences_the_attempt_and_binds_its_actor(store):
    d = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    assert actor_for(store, "r", d.generation) == "claude"
    assert role_for(store, "r", d.generation) == Role.IMPLEMENTER


def test_a_generation_acquired_outside_dispatch_has_no_actor(store):
    """A worker that fences itself gets no identity, and its commands are
    refused.

    The run MUST already hold a dispatch for another generation. Without one,
    a lookup that fell back to 'the most recent dispatch' would still return
    None -- for want of any row, not for want of the right one -- and this
    test would pass while the property it names was broken.
    """
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(store, "r", "claude")
    assert actor_for(store, "r", self_fenced) is None


def test_each_dispatch_gets_its_own_generation_and_actor(store):
    """Exact-generation lookup. Reading 'the most recent dispatch' is how one
    attempt inherits another attempt's identity."""
    d1 = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    d2 = dispatch(store, "r", actor="codex", role=Role.REVIEWER)
    assert d2.generation > d1.generation
    assert actor_for(store, "r", d1.generation) == "claude"
    assert actor_for(store, "r", d2.generation) == "codex"


def test_dispatch_supersedes_the_previous_attempt(store):
    """Dispatch is the fence: the older generation loses its write capability."""
    from kernel.ownership import current_generation

    d1 = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    dispatch(store, "r", actor="codex", role=Role.REVIEWER)
    assert current_generation(store, "r") != d1.generation


def test_dispatch_records_a_fact(store):
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    facts = [f for f in store.facts_for("r") if f.kind == "attempt_dispatched"]
    assert len(facts) == 1
    assert facts[0].payload["actor"] == "claude"
    assert facts[0].payload["role"] == Role.IMPLEMENTER


def test_dispatch_requires_a_known_role(store):
    with pytest.raises(ValueError, match="role"):
        dispatch(store, "r", actor="claude", role="boss")


@pytest.mark.parametrize("actor", ["", None, 7])
def test_dispatch_requires_a_named_actor(store, actor):
    with pytest.raises(ValueError, match="named actor"):
        dispatch(store, "r", actor=actor, role=Role.IMPLEMENTER)
