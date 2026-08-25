"""Command-specific authorization: legal transitions and merge authority.

Round 2 submitted `request_merge` against a brand-new queued run with an empty
payload and it was ACCEPTED, advancing the aggregate version. submit() checked
the command name, the halt, the generation and the version CAS -- and nothing
about whether the command was legal in the current state or whether anything
authorized a merge.
"""

import pytest

from kernel.authz import NotAuthorized, legal_states_for, next_state_for
from kernel.commands import Command, submit
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.projection import project
from kernel.store import Store

SPEC, PLAN, BASE, HEAD, BUNDLE = "a" * 64, "b" * 64, "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return s


def _submit(s, name, key, **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=acquire(s, "r", "a"), payload=payload,
    ))


def _advance_to_reviewing(s):
    _submit(s, "submit_spec", "k1", spec_sha256=SPEC)
    _submit(s, "submit_plan", "k2", plan_sha256=PLAN)
    _submit(s, "start_implementation", "k3", implementer_identity="claude")
    return s


# --- state-transition legality ------------------------------------------------

def test_request_merge_on_a_queued_run_is_refused():
    """The round-2 reproduction, verbatim."""
    s = _store()
    with pytest.raises(NotAuthorized, match="queued"):
        _submit(s, "request_merge", "k")


def test_the_happy_path_advances_state_and_is_projectable():
    s = _advance_to_reviewing(_store())
    assert project(s.facts_for("r")).state == "implementing"
    assert s.run_state("r") == "implementing", "runs.state was left stale"


def test_a_command_out_of_order_is_refused():
    s = _store()
    with pytest.raises(NotAuthorized, match="submit_plan"):
        _submit(s, "submit_plan", "k", plan_sha256=PLAN)


def test_cancel_run_is_legal_from_any_state():
    s = _store()
    assert _submit(s, "cancel_run", "k").accepted
    assert s.run_state("r") == "cancelled"


def test_every_command_declares_its_legal_states():
    """A command with no declared states would be legal everywhere or
    nowhere, and which one is an accident of the lookup's default."""
    from kernel.commands import COMMAND_NAMES

    for name in COMMAND_NAMES:
        assert legal_states_for(name), f"{name} declares no legal states"
        # A None next-state is legitimate for commands that observe without
        # transitioning, and for those whose destination depends on the
        # outcome they report.
        assert next_state_for(name) is not None or name in (
            "record_ci_observation", "record_merge_outcome",
        )


# --- merge authorization ------------------------------------------------------

def test_request_merge_without_an_accepted_review_is_refused():
    s = _advance_to_reviewing(_store())
    _submit(s, "record_review", "k4", verdict="request_revision",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            reviewer_identity="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _submit(s, "request_merge", "k5", artifact_hash=SPEC, base_sha=BASE,
                context_bundle_hash=BUNDLE, reviewer_identity="codex",
                policy_version=1)


def test_request_merge_with_an_accepted_review_is_authorized():
    s = _advance_to_reviewing(_store())
    _submit(s, "record_review", "k4", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            reviewer_identity="codex", policy_version=1)
    assert _submit(s, "request_merge", "k5", artifact_hash=SPEC, base_sha=BASE,
                   context_bundle_hash=BUNDLE, reviewer_identity="codex",
                   policy_version=1).accepted
    assert s.run_state("r") == "merge_requested"


def test_a_verdict_bound_to_different_inputs_does_not_authorize_a_merge():
    """The whole point of the binding: yesterday's approval must not
    authorize today's object."""
    s = _advance_to_reviewing(_store())
    _submit(s, "record_review", "k4", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            reviewer_identity="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _submit(s, "request_merge", "k5", artifact_hash="f" * 64, base_sha=BASE,
                context_bundle_hash=BUNDLE, reviewer_identity="codex",
                policy_version=1)


def test_the_projection_matches_the_stored_aggregate():
    """The M1-2 plan's 'Done means' claims state is checked against the stored
    aggregate rather than trusted. Nothing compared them until now."""
    s = _advance_to_reviewing(_store())
    assert project(s.facts_for("r")).state == s.run_state("r")
    _submit(s, "record_review", "kx", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            reviewer_identity="codex", policy_version=1)
    assert project(s.facts_for("r")).state == s.run_state("r")
