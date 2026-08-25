import pytest

from kernel.events import EventKind
from kernel.ids import Clock
from kernel.projection import project
from kernel.store import Store


@pytest.fixture
def store():
    return Store.open(":memory:", clock=Clock(start_us=1_000))


def _start(s, run="r"):
    s.append_fact(
        run_id=run, kind=EventKind.RUN_STARTED, actor="kernel",
        causal_command_id=None, payload={"base_sha": "aaa", "state": "queued"},
    )


def _to(s, state, run="r"):
    s.append_fact(
        run_id=run, kind=EventKind.TRANSITION, actor="kernel",
        causal_command_id="cmd_1", payload={"to": state},
    )


def test_projection_rebuilds_state_from_facts_alone(store):
    _start(store)
    _to(store, "implementing")
    assert project(store.facts_for("r")).state == "implementing"


def test_projection_is_order_dependent(store):
    """Replaying a prefix must give the earlier state, or ordering is not real
    and 'rebuildable' means only 'recomputes the last value'."""
    _start(store)
    for s in ("implementing", "reviewing", "merged"):
        _to(store, s)
    facts = store.facts_for("r")
    assert project(facts).state == "merged"
    assert project(facts[:-1]).state == "reviewing"
    assert project(facts[:-2]).state == "implementing"


def test_projection_skips_unknown_kinds_without_losing_known_ones(store):
    """Forward compatibility: a fact written by a newer mechanism version must
    not break replay of the facts this version understands."""
    _start(store)
    store.append_fact(
        run_id="r", kind=EventKind.OBSERVATION, actor="github",
        causal_command_id=None, payload={"anything": 1},
    )
    _to(store, "implementing")
    assert project(store.facts_for("r")).state == "implementing"


def test_artifacts_and_verdicts_accumulate(store):
    _start(store)
    store.append_fact(
        run_id="r", kind=EventKind.ARTIFACT_CREATED, actor="kernel",
        causal_command_id=None, payload={"artifact_hash": "h1"},
    )
    store.append_fact(
        run_id="r", kind=EventKind.REVIEW_VERDICT, actor="codex",
        causal_command_id=None, payload={"verdict": "accepted"},
    )
    st = project(store.facts_for("r"))
    assert st.artifacts == ["h1"]
    assert st.verdicts == [{"verdict": "accepted"}]


def test_empty_history_projects_to_no_run(store):
    assert project([]) is None


def test_facts_before_run_started_are_ignored(store):
    """A transition with no run cannot be applied to anything; silently
    inventing a RunState from it would fabricate a run that never started."""
    _to(store, "implementing")
    _start(store)
    assert project(store.facts_for("r")).state == "queued"
