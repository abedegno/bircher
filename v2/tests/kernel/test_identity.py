"""Identity is assigned by the kernel, never presented by the caller.

Round 5's provenance audit found five of the six links in the merge
authorization chain were caller assertions. Every comparison was correctly
implemented; the chain proved nothing, because `Command` had no actor and
both identities were payload fields -- so ONE caller named BOTH sides of its
own independence check.

The exploit is reproduced below as a test. It must not be expressible.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import ACTOR_FIELDS, Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, actor, role, **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _to_implementing(s, implementer="claude"):
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", implementer, Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", implementer, Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", implementer, Role.IMPLEMENTER)
    _sub(s, "record_implementation_output", "k3o", implementer,
         Role.IMPLEMENTER, artifact_hash=spec)
    return spec


# --- the payload cannot name an actor ----------------------------------------

@pytest.mark.parametrize(
    "field", ["actor", "implementer_identity", "reviewer_identity"]
)
def test_a_command_carrying_an_actor_field_is_refused(field):
    """If a caller can populate it, it is not identity.

    Parametrized over a LITERAL list, not over ACTOR_FIELDS. Driving the cases
    from the constant under test means removing a field deletes its case
    instead of failing it -- the test would adapt to the very mutation it
    exists to catch.
    """
    s = _store()
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(ValueError, match="assigned"):
        submit(s, Command(
            name="submit_spec", run_id="r", expected_version=0,
            idempotency_key=f"k-{field}", generation=gen,
            payload={field: "anyone", "spec_sha256": "a" * 64},
        ))


def test_the_refusal_covers_every_field_the_chain_ever_read():
    """A field dropped from this set is a field a caller can name again."""
    assert ACTOR_FIELDS == {"actor", "implementer_identity", "reviewer_identity"}


# --- identity comes from the dispatch record ---------------------------------

def test_the_accepted_fact_records_the_dispatched_actor():
    s = _store()
    _to_implementing(s, implementer="gpt")
    accepted = [f for f in s.facts_for("r") if f.kind == "command_accepted"]
    assert {f.actor for f in accepted} == {"gpt"}, (
        "the accepted facts attribute the work to the kernel, so the audit "
        "trail cannot say who did it"
    )


def test_a_rejection_records_who_was_refused():
    s = _store()
    with pytest.raises(NotAuthorized):
        _sub(s, "request_merge", "k", "claude", Role.IMPLEMENTER)
    rejected = [f for f in s.facts_for("r") if f.kind == "command_rejected"]
    assert [f.actor for f in rejected] == ["claude"]


def test_an_undispatched_generation_cannot_submit():
    """A worker that fences itself has no identity the kernel assigned."""
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    with pytest.raises(NotAuthorized, match="no dispatched actor"):
        submit(s, Command(
            name="submit_spec", run_id="r", expected_version=0,
            idempotency_key="k", generation=self_fenced,
            payload={"spec_sha256": "a" * 64},
        ))


def test_an_undispatched_attempt_is_recorded_as_undispatched_not_as_kernel():
    """Attributing an unnamed actor's attempt to the kernel would be a
    fabricated audit trail -- the kernel did not do this."""
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    with pytest.raises(NotAuthorized):
        submit(s, Command(
            name="submit_spec", run_id="r", expected_version=0,
            idempotency_key="k", generation=self_fenced, payload={},
        ))
    actors = {f.actor for f in s.facts_for("r") if f.kind == "command_rejected"}
    assert actors == {"undispatched"}
    assert "kernel" not in actors


# --- the round-5 exploit ------------------------------------------------------

def test_an_implementer_cannot_record_a_review():
    """THE EXPLOIT. A caller recorded as implementer submitted its own accept
    naming a different reviewer, reached merge_requested, and authorization
    succeeded. It cannot now name a reviewer at all, and its dispatched role
    is not one that reviews."""
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    with pytest.raises(NotAuthorized, match="reviewer role"):
        _sub(s, "record_review", "rv", "claude", Role.IMPLEMENTER,
             verdict="accept", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)


def test_a_reviewer_dispatched_as_the_implementer_is_refused_for_independence():
    """The role is right; the actor is the same one that implemented. Both
    sides of this comparison are now kernel-assigned."""
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    with pytest.raises(NotAuthorized, match="independence"):
        _sub(s, "record_review", "rv", "claude", Role.REVIEWER,
             verdict="accept", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)


def test_an_independent_reviewer_is_authorized():
    """The control. Without it, refusing everything would pass the two tests
    above and prove nothing."""
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    assert _sub(s, "record_review", "rv", "codex", Role.REVIEWER,
                verdict="accept", artifact_hash=spec, base_sha=BASE,
                context_bundle_hash=BUNDLE, policy_version=1).accepted
    assert s.run_state("r") == "reviewing"


def test_the_verdict_fact_names_the_dispatched_reviewer():
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    _sub(s, "record_review", "rv", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    verdict = [f for f in s.facts_for("r") if f.kind == "review_verdict"][0]
    assert verdict.payload["reviewer_identity"] == "codex"
    assert verdict.actor == "codex"


def test_the_revision_loop_tracks_the_current_implementer():
    """request_revision -> planned -> start_implementation puts a NEW
    implementer in place. Independence must follow it, or the new implementer
    reviews its own work."""
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    _sub(s, "record_review", "rv", "codex", Role.REVIEWER,
         verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)
    _sub(s, "start_implementation", "si2", "gpt", Role.IMPLEMENTER)
    with pytest.raises(NotAuthorized, match="independence"):
        _sub(s, "record_review", "rv2", "gpt", Role.REVIEWER, verdict="accept",
             artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
             policy_version=1)
    # ...and so is claude, who PRODUCED the artifact still under review.
    #
    # This assertion was inverted until round 6. It read "the original
    # implementer is free to review the revision" and asserted claude could
    # accept -- but gpt has not produced anything yet, so the artifact bound
    # here is still claude's own output. The test encoded the defect codex
    # found: independence was tracking who STARTED an implementation rather
    # than who produced the thing being reviewed.
    with pytest.raises(NotAuthorized, match="independence"):
        _sub(s, "record_review", "rv3", "claude", Role.REVIEWER,
             verdict="accept", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)
    # An actor with neither role may review it. The control.
    assert _sub(s, "record_review", "rv4", "codex", Role.REVIEWER,
                verdict="accept", artifact_hash=spec, base_sha=BASE,
                context_bundle_hash=BUNDLE, policy_version=1).accepted


def test_a_merge_request_cannot_name_the_reviewer_it_relies_on():
    """reviewer_identity left the binding tuple. A merge requester presenting
    the approved INPUTS is matched against whoever the kernel recorded as
    having approved them -- it does not get to choose."""
    s = _store()
    spec = _to_implementing(s, implementer="claude")
    _sub(s, "record_ci_observation", "ci", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "rv", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=spec, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    with pytest.raises(ValueError, match="assigned"):
        _sub(s, "request_merge", "rm", "claude", Role.IMPLEMENTER,
             head_git_sha=HEAD, artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, reviewer_identity="claude",
             policy_version=1)
    assert _sub(s, "request_merge", "rm2", "claude", Role.IMPLEMENTER,
                head_git_sha=HEAD, artifact_hash=spec, base_sha=BASE,
                context_bundle_hash=BUNDLE, policy_version=1).accepted
