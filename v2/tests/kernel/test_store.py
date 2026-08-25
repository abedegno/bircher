import sqlite3

import pytest

from kernel.events import SCHEMA_VERSIONS, EventKind
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "k.db", clock=Clock(start_us=1_000_000))


def _append(s, run="run_1", kind=None, **payload):
    return s.append_fact(
        run_id=run,
        kind=kind or EventKind.RUN_STARTED,
        actor="kernel",
        causal_command_id=None,
        payload=payload or {"base_sha": "a" * 40},
    )


def test_append_returns_a_stable_id_and_reads_back(store):
    fid = _append(store)
    facts = store.facts_for("run_1")
    assert [f.id for f in facts] == [fid]
    assert facts[0].payload == {"base_sha": "a" * 40}


@pytest.mark.parametrize("kind", sorted(SCHEMA_VERSIONS))
def test_every_fact_carries_schema_and_mechanism_version(store, kind):
    """Parametrized over EVERY declared kind. Exercising only RUN_STARTED left
    a mutation that set TRANSITION's schema version to 0 undetected -- the
    test named 'every fact' and checked one."""
    _append(store, kind=kind)
    f = [x for x in store.facts_for("run_1") if x.kind == kind][0]
    assert f.schema_version == SCHEMA_VERSIONS[kind]
    assert f.schema_version >= 1, f"{kind} declares schema version {f.schema_version}"
    assert f.mechanism_version >= 1


def test_facts_cannot_be_updated(store):
    """Enforced by the database. A rule living only in application code is one
    the next caller can forget; a trigger is not."""
    _append(store)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("UPDATE facts SET actor='tamper'")


def test_facts_cannot_be_deleted(store):
    _append(store)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM facts")


def test_ordering_is_by_sequence_not_timestamp():
    """Ordering must not depend on the clock, or replay order becomes
    nondeterministic when instants tie or move.

    A clock that RUNS BACKWARDS is what makes this bind. An earlier version
    used a frozen clock and two facts at the same microsecond -- but ties are
    resolved by insertion order anyway, so ordering by timestamp produced an
    identical result and the mutation survived. Sequence and timestamp order
    must actively disagree for the assertion to mean anything.
    """
    s = Store.open(":memory:", clock=Clock(start_us=9_000, step_us=-1_000))
    a = _append(s, run="r")
    b = _append(s, run="r")
    c = _append(s, run="r")
    facts = s.facts_for("r")
    assert [f.observed_at_us for f in facts] == [9_000, 8_000, 7_000], (
        "clock must be descending for this test to distinguish the two orderings"
    )
    assert [f.id for f in facts] == [a, b, c], (
        "facts came back in timestamp order, not insertion order"
    )


def test_unknown_event_kind_is_refused(store):
    """An event whose schema version is not declared cannot be stored: it
    would acquire meaning later, which the spec forbids."""
    with pytest.raises(KeyError):
        _append(store, kind="invented_kind")


def test_facts_are_scoped_per_run(store):
    _append(store, run="run_1")
    _append(store, run="run_2")
    assert len(store.facts_for("run_1")) == 1
    assert len(store.facts_for("run_2")) == 1
