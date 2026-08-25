"""Existence is not identity: a review binds what the implementation produced.

The old check asked only whether the store HELD the blob. Any artifact
satisfied it -- one from another run, or a superseded revision of this one --
so the merge chain compared a caller-chosen hash against itself the whole way
down. Nothing recorded what an implementation produced, so there was no such
thing as "this run's current output" to compare against.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store(*runs):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    for r in runs or ("r",):
        s.create_run(run_id=r, base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, actor, role, run="r", **payload):
    if name == "request_merge":
        # A merge authorization must name its target (round 6, C2). Defaulted
        # here because these tests are about other properties; the binding
        # itself is asserted in test_effect_contract.py.
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    return submit(s, Command(
        name=name, run_id=run, expected_version=s.run_version(run),
        idempotency_key=key,
        generation=dispatch(s, run, actor=actor, role=role).generation,
        payload=payload,
    ))


def _implementing(s, run="r", impl="claude"):
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", f"{run}1", impl, Role.IMPLEMENTER, run=run, spec_sha256=spec)
    _sub(s, "submit_plan", f"{run}2", impl, Role.IMPLEMENTER, run=run, plan_sha256=spec)
    _sub(s, "start_implementation", f"{run}3", impl, Role.IMPLEMENTER, run=run)
    return spec


def _review(s, key, artifact, verdict="accept", actor="codex", run="r"):
    return _sub(s, "record_review", key, actor, Role.REVIEWER, run=run,
                verdict=verdict, artifact_hash=artifact, base_sha=BASE,
                context_bundle_hash=BUNDLE, policy_version=1)


# --- recording the output -----------------------------------------------------

def test_only_an_implementer_may_record_an_implementation_output():
    s = _store()
    _implementing(s)
    out = put_artifact(s, b"diff v1")
    with pytest.raises(NotAuthorized, match="implementer role"):
        _sub(s, "record_implementation_output", "o", "codex", Role.REVIEWER,
             artifact_hash=out)


def test_an_output_must_be_an_artifact_the_kernel_holds():
    s = _store()
    _implementing(s)
    with pytest.raises(NotAuthorized, match="not an artifact"):
        _sub(s, "record_implementation_output", "o", "claude", Role.IMPLEMENTER,
             artifact_hash="f" * 64)


def test_recording_an_output_does_not_move_the_run():
    """It observes; a review moves the run."""
    s = _store()
    _implementing(s)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "o", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    assert s.run_state("r") == "implementing"
    assert s.current_artifact("r") == out


# --- what a review may bind ---------------------------------------------------

def test_a_review_before_any_output_is_recorded_is_refused():
    s = _store()
    spec = _implementing(s)
    with pytest.raises(NotAuthorized, match="no implementation output"):
        _review(s, "rv", spec)


def test_a_review_cannot_bind_an_artifact_this_run_did_not_produce():
    """THE DEFECT. The blob exists, so the old check passed."""
    s = _store()
    _implementing(s)
    out = put_artifact(s, b"diff v1")
    unrelated = put_artifact(s, b"something else entirely")
    _sub(s, "record_implementation_output", "o", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    assert s.has_artifact(unrelated), "the control: the store does hold it"
    with pytest.raises(NotAuthorized, match="current output"):
        _review(s, "rv", unrelated)


def test_a_review_cannot_bind_another_runs_output():
    """Cross-run: run B's implementation is a perfectly real artifact."""
    s = _store("r", "other")
    _implementing(s, run="r")
    _implementing(s, run="other")
    mine = put_artifact(s, b"diff mine")
    theirs = put_artifact(s, b"diff theirs")
    _sub(s, "record_implementation_output", "o1", "claude", Role.IMPLEMENTER,
         run="r", artifact_hash=mine)
    _sub(s, "record_implementation_output", "o2", "claude", Role.IMPLEMENTER,
         run="other", artifact_hash=theirs)
    with pytest.raises(NotAuthorized, match="current output"):
        _review(s, "rv", theirs, run="r")


def test_a_review_of_the_current_output_is_authorized():
    """The control. Refusing everything would pass the three tests above."""
    s = _store()
    _implementing(s)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "o", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    assert _review(s, "rv", out).accepted
    assert s.run_state("r") == "reviewing"


# --- a revision supersedes an approval ----------------------------------------

def test_a_revision_invalidates_an_acceptance_of_the_previous_output():
    """An accepted review over v1 must not authorize a merge of v2 -- nor of
    v1 once v2 is what the run is carrying."""
    s = _store()
    _implementing(s)
    v1 = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "o1", "claude", Role.IMPLEMENTER,
         artifact_hash=v1)
    _sub(s, "record_ci_observation", "ci", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _review(s, "rv", v1)                       # accepted, run is `reviewing`

    # A revision lands: the implementer records new output.
    _review(s, "rv2", v1, verdict="request_revision")
    _sub(s, "start_implementation", "si2", "claude", Role.IMPLEMENTER)
    v2 = put_artifact(s, b"diff v2")
    _sub(s, "record_implementation_output", "o2", "claude", Role.IMPLEMENTER,
         artifact_hash=v2)
    _review(s, "rv3", v2, verdict="reject")    # back to `reviewing`, unapproved

    with pytest.raises(NotAuthorized, match="current output"):
        _sub(s, "request_merge", "rm", "claude", Role.IMPLEMENTER,
             head_git_sha=HEAD, artifact_hash=v1, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)
    with pytest.raises(NotAuthorized, match="no accepted review"):
        _sub(s, "request_merge", "rm2", "claude", Role.IMPLEMENTER,
             head_git_sha=HEAD, artifact_hash=v2, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)
