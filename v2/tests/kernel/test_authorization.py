"""Command-specific authorization: legal transitions and merge authority.

Round 2 submitted `request_merge` against a brand-new queued run with an empty
payload and it was ACCEPTED, advancing the aggregate version. submit() checked
the command name, the halt, the generation and the version CAS -- and nothing
about whether the command was legal in the current state or whether anything
authorized a merge.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized, legal_states_for, next_state_for
from kernel.effects import EffectClass, perform
from kernel.commands import Command, submit
from kernel.effects import EffectClass, perform
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.projection import project
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64
SPEC = PLAN = None  # set per-store: a review may only bind artifacts the kernel holds


def _store():
    global SPEC, PLAN
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    SPEC = put_artifact(s, b"# spec")
    PLAN = put_artifact(s, b"# plan")
    return s


def _submit(s, name, key, actor=None, **payload):
    """Dispatch, then submit under the generation the kernel handed back.

    Tests no longer name an identity in a payload -- the kernel refuses that
    outright -- so the actor is chosen HERE, where the supervisor chooses it,
    and the command inherits it.
    """
    role = Role.REVIEWER if name == "record_review" else Role.IMPLEMENTER
    if actor is None:
        actor = "codex" if role == Role.REVIEWER else "claude"
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _advance_to_reviewing(s):
    _submit(s, "submit_spec", "k1", spec_sha256=SPEC)
    _submit(s, "submit_plan", "k2", plan_sha256=PLAN)
    _submit(s, "start_implementation", "k3", actor="claude")
    _submit(s, "record_implementation_output", "k3o", actor="claude",
            artifact_hash=SPEC)
    return s


# --- state-transition legality ------------------------------------------------

def test_request_merge_on_a_queued_run_is_refused():
    """The round-2 reproduction, verbatim."""
    s = _store()
    with pytest.raises(NotAuthorized, match="queued"):
        _submit(s, "request_merge", "k")


def test_the_full_lifecycle_advances_to_a_terminal_state():
    """The old version stopped at `implementing` and never exercised review,
    CI, merge request, merge outcome or terminal projection."""
    s = _advance_to_reviewing(_store())
    _submit(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _submit(s, "record_review", "rv", verdict="accept", artifact_hash=SPEC,
            base_sha=BASE, context_bundle_hash=BUNDLE, actor="codex",
            policy_version=1)
    _submit(s, "request_merge", "rm", head_git_sha=HEAD, artifact_hash=SPEC, base_sha=BASE,
            context_bundle_hash=BUNDLE, policy_version=1)
    assert s.run_state("r") == "merge_requested"
    # The merge effect must actually happen before its outcome is reported.
    perform(s, "r", acquire(s, "r", "impl"), EffectClass.MERGE, "m", {},
            lambda *a: "merged!")
    _submit(s, "record_merge_outcome", "mo", outcome="merged")
    assert s.run_state("r") == "merged"
    assert project(s.facts_for("r")).state == "merged", (
        "the projection disagrees with the aggregate at the terminal state"
    )


def test_a_command_out_of_order_is_refused():
    s = _store()
    with pytest.raises(NotAuthorized, match="submit_plan"):
        _submit(s, "submit_plan", "k", plan_sha256=PLAN)


@pytest.mark.parametrize("reach", ["queued", "specified", "planned",
                                   "implementing", "reviewing",
                                   "merge_requested"])
def test_cancel_run_is_legal_from_every_nonterminal_state(reach):
    """The old version tested only `queued`, so restricting cancellation to
    `queued` alone left it green -- it asserted the name of the property and
    exercised one case of it."""
    s = _store()
    # request_merge needs CI evidence, so it is supplied up front rather than
    # as a step -- record_ci_observation does not transition.
    steps = [
        ("specified", "submit_spec", {"spec_sha256": SPEC}),
        ("planned", "submit_plan", {"plan_sha256": PLAN}),
        ("implementing", "start_implementation", {}),
        (None, "record_implementation_output", {"artifact_hash": SPEC}),
        ("reviewing", "record_review", {"verdict": "accept",
                                        "artifact_hash": SPEC, "base_sha": BASE,
                                        "context_bundle_hash": BUNDLE,
                                        "policy_version": 1}),
        ("merge_requested", "request_merge", {"artifact_hash": SPEC,
                                              "base_sha": BASE,
                                              "context_bundle_hash": BUNDLE,
                                              "policy_version": 1,
                                              "head_git_sha": HEAD}),
    ]
    for i, (state, name, payload) in enumerate(steps):
        if s.run_state("r") == reach:
            break
        if name == "request_merge":
            # CI evidence is a precondition of the merge gate, not a state.
            _submit(s, "record_ci_observation", "cis", status="success",
                    head_git_sha=HEAD)
        _submit(s, name, f"s{i}", **payload)
    assert s.run_state("r") == reach
    assert _submit(s, "cancel_run", "cancel").accepted
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
        # A None next-state is legitimate where the destination depends on
        # what is being reported (a verdict, a merge outcome) or where the
        # command observes without transitioning.
        assert next_state_for(name) is not None or name in (
            "record_ci_observation", "record_merge_outcome", "record_review",
            # Records what the implementation produced; the run stays in
            # `implementing` until a review moves it.
            "record_implementation_output",
        )


# --- merge authorization ------------------------------------------------------

def test_request_merge_without_an_accepted_review_is_refused():
    s = _advance_to_reviewing(_store())
    _submit(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    # `reject`, not `request_revision`: a revision request now returns the run
    # to `planned`, so request_merge would be refused for being illegal in that
    # state and the test would pass without ever reaching the approval check.
    _submit(s, "record_review", "k4", verdict="reject",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            actor="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _submit(s, "request_merge", "k5", head_git_sha=HEAD, artifact_hash=SPEC, base_sha=BASE,
                context_bundle_hash=BUNDLE,
                policy_version=1)


def test_request_merge_with_an_accepted_review_is_authorized():
    s = _advance_to_reviewing(_store())
    _submit(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _submit(s, "record_review", "k4", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            actor="codex", policy_version=1)
    assert _submit(s, "request_merge", "k5", head_git_sha=HEAD, artifact_hash=SPEC, base_sha=BASE,
                   context_bundle_hash=BUNDLE,
                   policy_version=1).accepted
    assert s.run_state("r") == "merge_requested"


def test_a_verdict_bound_to_different_inputs_does_not_authorize_a_merge():
    """The whole point of the binding: yesterday's approval must not
    authorize today's object.

    The input varied here is context_bundle_hash, NOT artifact_hash. A wrong
    artifact is now refused a step earlier by the lineage guard, so varying it
    would exercise that guard and leave the verdict binding untested -- the
    test would still pass, for a reason other than the one it names.
    """
    s = _advance_to_reviewing(_store())
    _submit(s, "record_ci_observation", "ci", status="success", head_git_sha=HEAD)
    _submit(s, "record_review", "k4", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            actor="codex", policy_version=1)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _submit(s, "request_merge", "k5", head_git_sha=HEAD, artifact_hash=SPEC,
                base_sha=BASE, context_bundle_hash="f" * 64,
                policy_version=1)


def test_the_projection_matches_the_stored_aggregate():
    """The M1-2 plan's 'Done means' claims state is checked against the stored
    aggregate rather than trusted. Nothing compared them until now."""
    s = _advance_to_reviewing(_store())
    assert project(s.facts_for("r")).state == s.run_state("r")
    _submit(s, "record_review", "kx", verdict="accept",
            artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
            actor="codex", policy_version=1)
    assert project(s.facts_for("r")).state == s.run_state("r")
