"""What would enforcement have refused? Evidence you cannot read is not
evidence."""
import pytest

from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.report import shadow_summary
from kernel.store import Store


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", "shadow")
    s = Store.open(":memory:", clock=Clock(start_us=1))
    for r in ("r1", "r2"):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s


def _bad_merge(s, run, key=None):
    """request_merge against a brand-new queued run: illegal, and shadow mode
    records the refusal instead of raising."""
    g = dispatch(s, run, actor="claude", role=Role.IMPLEMENTER).generation
    submit(s, Command(name="request_merge", run_id=run,
                      expected_version=s.run_version(run),
                      idempotency_key=key or f"k-{run}", generation=g,
                      payload={}))


def test_an_empty_store_summarises_to_nothing(store):
    assert shadow_summary(store) == []


def test_rejections_are_counted_by_command_name(store):
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    rows = shadow_summary(store)
    assert len(rows) == 1
    assert rows[0]["command_name"] == "request_merge"
    assert rows[0]["count"] == 2


def test_each_row_carries_a_reason(store):
    """A count with no reason tells you enforcement would break something and
    not what."""
    _bad_merge(store, "r1")
    assert "queued" in shadow_summary(store)[0]["example_reason"]


def test_rows_are_ordered_by_count_descending(store):
    """The first row is what to fix first."""
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    g = dispatch(store, "r1", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="submit_plan", run_id="r1",
                          expected_version=store.run_version("r1"),
                          idempotency_key="p", generation=g, payload={}))
    rows = shadow_summary(store)
    assert [r["count"] for r in rows] == sorted(
        [r["count"] for r in rows], reverse=True)


def test_the_summary_spans_runs(store):
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    assert shadow_summary(store)[0]["runs"] == 2


# --- what `count` counts ------------------------------------------------------

def test_a_retried_refusal_counts_once(store):
    """The coordinator RETRIES: an advisory call that is shadow-refused gets
    called again, and each attempt appends its own fact with the same
    causal_command_id. Counting facts would answer 'how many times did we
    notice' -- but the number that decides whether a command is safe to
    enforce is how many DISTINCT commands would have broken."""
    for _ in range(4):
        _bad_merge(store, "r1", key="same-key")
    row = shadow_summary(store)[0]
    assert row["count"] == 1, "four attempts at one command is one refusal"
    assert row["occurrences"] == 4, "the retries are still reported"


def test_two_runs_sharing_an_idempotency_key_are_two_commands(store):
    """Idempotency keys are unique WITHIN a run, not across runs. Deduping on
    the bare id would merge two unrelated commands into one and understate
    exactly the number this report exists to give."""
    _bad_merge(store, "r1", key="k3")
    _bad_merge(store, "r2", key="k3")
    row = shadow_summary(store)[0]
    assert row["count"] == 2, "same key, different runs -- two commands"
    assert row["runs"] == 2


def test_the_order_is_stable_when_counts_tie(store):
    """An unstable order makes two runs of the same report look like a change.

    The refusals are created in REVERSE alphabetical order on purpose. An
    earlier version of this test made them in alphabetical order, so insertion
    order and sorted order coincided and it passed with the tiebreak removed --
    it asserted the property it was named for only by accident of the fixture.
    """
    g = dispatch(store, "r1", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="submit_plan", run_id="r1",
                          expected_version=store.run_version("r1"),
                          idempotency_key="p", generation=g, payload={}))
    _bad_merge(store, "r2")

    names = [r["command_name"] for r in shadow_summary(store)]
    assert names == ["request_merge", "submit_plan"], names
    assert names == sorted(names), "tied counts must break ties by name"


def test_facts_without_a_causal_id_stay_distinct(store):
    """The `or f"#{fact.id}"` fallback, made reachable and asserted.

    No production call site passes a None causal id, so the fallback was dead
    code that no test could reach -- and dead code in a dedupe key is the kind
    that collapses two real refusals into one the day something does. Appending
    the facts directly is the only way to reach it.
    """
    from kernel.events import EventKind
    for i in range(2):
        store.append_fact(run_id="r1", kind=EventKind.SHADOW_REJECTED,
                          actor="kernel", causal_command_id=None,
                          payload={"command_name": "submit_plan",
                                   "reason": "no causal id"})
    row = shadow_summary(store)[0]
    assert row["count"] == 2, "two causal-id-less refusals collapsed into one"
    assert row["occurrences"] == 2


def test_runs_are_swept_oldest_first(store):
    """`all_run_ids` documents an ordering; nothing asserted it, so reversing
    the SQL ORDER BY changed nothing observable."""
    store.create_run(run_id="r3", base_repo="o/r", base_sha="a" * 40)
    assert store.all_run_ids() == ["r1", "r2", "r3"]
