import pytest

from kernel.artifacts import put_artifact
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
            # AUTHOR, because the default command here is submit_spec and a
            # submission now has to come from an author seat.
            else dispatch(store, "r", actor="claude", role=Role.AUTHOR).generation
        ),
        payload=payload or {"artifact_hash": put_artifact(store, b"spec")},
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
        # record_run_outcome -- the kernel had no way to say a run ENDED
        # without merging. `merged` (only from merge_requested) and
        # `cancelled` were the sole terminal records, so an escalated or
        # timed-out run read as one still in flight, and the spec's
        # "aggregate matches the scorecard" criterion was unsatisfiable for
        # six of the scorecard's seven terminal outcomes.
        "record_run_outcome",
        # The front half's two observations. record_turn_ended makes the end
        # of a model's turn a fact the kernel holds rather than a wait the
        # coordinator performs; park records why a pass stopped without a
        # transition, which v1 could express only as the absence of anything.
        "record_turn_ended", "park",
    ])


def test_command_at_the_current_version_is_accepted(store):
    assert submit(store, _cmd(store)).accepted


def test_command_derived_from_an_older_version_is_refused(store):
    """A command derived from version 12 cannot mutate version 15.

    The second command is `park`, which is legal from `spec_submitted` and
    passes every payload check, so the CAS is the first thing that can refuse
    it. A second `submit_spec` would be refused by the identical-hash guard
    and a `submit_plan` by the state check -- both NotAuthorized, both raised
    before the transaction the CAS lives in, so the test would pass for the
    wrong reason.
    """
    submit(store, _cmd(store, key="k1"))
    with pytest.raises(StaleVersion):
        submit(store, _cmd(store, name="park", version=0, key="k2", reason="gate"))


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
        submit(store, _cmd(store, name="park", version=0, key="k2", reason="gate"))
    assert "command_rejected" in [f.kind for f in store.facts_for("r")]


def test_unknown_command_name_is_refused(store):
    with pytest.raises(ValueError, match="unknown command"):
        submit(store, _cmd(store, name="merge_everything", key="k8"))
