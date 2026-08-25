import pytest

from kernel.commands import COMMAND_NAMES, Command, StaleVersion, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.ownership import OwnershipLost, acquire
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def _cmd(store, name="submit_spec", version=0, key="k1", generation=None, **payload):
    return Command(
        name=name, run_id="r", expected_version=version, idempotency_key=key,
        generation=(
            generation if generation is not None
            else dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
        ),
        payload=payload or {"spec_sha256": "a" * 64},
    )


def test_the_command_interface_is_closed_and_explicit():
    """A narrow interface, not a general one. Growth here is a design change
    to be argued, not absorbed.

    Two commands were added deliberately, each with its argument:

    record_merge_outcome -- merge_requested was a dead end, so a merge that
    came back uncertain wedged the run and the only escape recorded
    'cancelled' for a run that had in fact merged.

    record_implementation_output -- nothing recorded what an implementation
    PRODUCED, so the reviewer named the artifact it was reviewing and any blob
    the store happened to hold satisfied the check. Without it there is no
    such thing as "this run's current output", and every lineage check
    downstream compares a caller's choice against itself.
    """
    assert sorted(COMMAND_NAMES) == sorted([
        "submit_spec", "submit_plan", "record_review", "start_implementation",
        "record_implementation_output", "record_ci_observation",
        "request_merge", "record_merge_outcome", "cancel_run",
    ])


def test_command_at_the_current_version_is_accepted(store):
    assert submit(store, _cmd(store)).accepted


def test_command_derived_from_an_older_version_is_refused(store):
    """A command derived from version 12 cannot mutate version 15.

    Uses a legal SEQUENCE (submit_spec then submit_plan) because commands are
    now authorized against the run's state; re-issuing submit_spec would be
    refused for being illegal rather than for being stale, and the test would
    pass for the wrong reason.
    """
    submit(store, _cmd(store, key="k1"))
    with pytest.raises(StaleVersion):
        submit(store, _cmd(store, name="submit_plan", version=0, key="k2"))


def test_replaying_an_idempotency_key_returns_the_first_result(store):
    a = submit(store, _cmd(store, key="same"))
    b = submit(store, _cmd(store, key="same"))
    assert a.result == b.result and b.replayed is True


def test_replay_does_not_advance_the_version(store):
    submit(store, _cmd(store, key="same"))
    v = store.run_version("r")
    submit(store, _cmd(store, key="same"))
    assert store.run_version("r") == v, "a replayed command mutated state"


def test_command_from_a_superseded_generation_is_refused(store):
    """The stale generation IS dispatched, so this reaches the fence.

    An undispatched one would be refused a step earlier for having no actor,
    and the test would pass while proving nothing about fencing.
    """
    stale = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    dispatch(store, "r", actor="gpt", role=Role.IMPLEMENTER)
    with pytest.raises(OwnershipLost):
        submit(store, _cmd(store, generation=stale, key="k9"))


def test_a_rejected_command_records_a_fact(store):
    submit(store, _cmd(store, key="k1"))
    with pytest.raises(StaleVersion):
        submit(store, _cmd(store, name="submit_plan", version=0, key="k2"))
    assert "command_rejected" in [f.kind for f in store.facts_for("r")]


def test_unknown_command_name_is_refused(store):
    with pytest.raises(ValueError, match="unknown command"):
        submit(store, _cmd(store, name="merge_everything", key="k8"))
