"""No approval survives a repair round, driven against a REAL kernel.

THE safety property of the whole loop. A repair produces a new commit, so
everything the previous review bound is superseded. If a merge could still be
authorised against what the FIRST reviewer read, the loop would merge code no
reviewer ever saw -- strictly worse than the terminal `failed` it replaces.

THREE INDEPENDENT GUARDS refuse this, not one, and the first version of these
tests attributed every refusal to the review binding. That was wrong, and it
passed: a `pytest.raises(NotAuthorized)` cannot tell which guard fired, so it
read as evidence for a mechanism that was not the one doing the work. Each
test below now pins the REASON, so a change that removes one guard cannot be
masked by another still refusing for a different cause.

  superseded artifact -> "not this run's current output"
  stale head          -> "no successful CI observation for the head"
  wrong state          -> "not legal from state"

Each round uses a DISTINCT artifact, as production does: the recorded output
text contains the head, so its hash necessarily differs per round. Reusing one
artifact across rounds -- which the first version did -- disables the artifact
guard entirely and leaves the test measuring only the other two.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.store import Store
from tests.kernel.front import Front

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _sub(s, name, key, actor, role, run="r", **payload):
    if name == "record_review":
        # Every review here is of an implementation output, and a review must
        # name the phase of the state it is recorded from.
        payload.setdefault("phase", "implementation")
    return submit(s, Command(
        name=name, run_id=run, expected_version=s.run_version(run),
        idempotency_key=key,
        generation=dispatch(s, run, actor=actor, role=role).generation,
        payload=payload))


@pytest.fixture()
def db(tmp_path):
    """A run driven as far as `reviewing`, on disk, ready to be reviewed."""
    path = str(tmp_path / "k.db")
    s = Store.open(path, clock=Clock(start_us=1))
    # `planned` is the driver's to reach: a submit lands at *_submitted and
    # only a reviewer's accept of the current hash moves the run on.
    Front(s, "r", base_sha=BASE).to_planned()
    art = put_artifact(s, b"# spec")
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=art)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    return path, s, art


def _round(s, n, *, artifact, head, verdict):
    """One repair round: review, re-implement to *artifact*, re-observe at *head*."""
    _sub(s, "record_review", f"rv-{n}", "codex", Role.REVIEWER,
         verdict=verdict, artifact_hash=artifact, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)


def _reimplement(s, n, *, artifact, head):
    # request_repair -- not the verdict -- reopens `planned` (closed-loop
    # spec §1): a repair round always crosses it on the way back in.
    _sub(s, "request_repair", f"rr-{n}", "claude", Role.IMPLEMENTER,
         cause="review_fail", head_sha=head, evidence=["codex review"])
    _sub(s, "start_implementation", f"si-{n}", "claude", Role.IMPLEMENTER)
    _sub(s, "record_implementation_output", f"io-{n}", "claude",
         Role.IMPLEMENTER, artifact_hash=artifact)
    _sub(s, "record_ci_observation", f"ci-{n}", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=head)


@pytest.fixture()
def two_rounds(db):
    """A run that failed review once, was repaired, and passed. Round 2's
    artifact and head both differ from round 1's."""
    from kernel.artifacts import put_artifact
    path, s, art1 = db
    art2 = put_artifact(s, b"derived: outcome=ready head=bbbb note=repaired")
    head2 = "b" * 40
    _round(s, 1, artifact=art1, head=HEAD, verdict="request_revision")
    _reimplement(s, 1, artifact=art2, head=head2)
    _round(s, 2, artifact=art2, head=head2, verdict="accept")
    assert s.run_state("r") == "reviewing"
    return s, art1, art2, head2


def _merge(s, key, *, artifact, head):
    return _sub(s, "request_merge", key, "claude", Role.IMPLEMENTER,
                pr=42, repo="o/r", head_git_sha=head, artifact_hash=artifact,
                base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1)


def test_the_repaired_run_can_merge_what_the_LAST_reviewer_read(two_rounds):
    """Asserted FIRST and deliberately: a loop that refused everything would
    satisfy every other test in this section while merging nothing at all."""
    s, _art1, art2, head2 = two_rounds
    assert _merge(s, "m-ok", artifact=art2, head=head2).accepted
    assert s.run_state("r") == "merge_requested"


def test_round_ONES_artifact_cannot_merge_after_a_repair(two_rounds):
    """The approval of a superseded object authorizes nothing. This is the
    review-binding guard, and it is the one that would let repaired work merge
    on the pre-repair reviewer's word."""
    from kernel.authz import NotAuthorized
    s, art1, _art2, head2 = two_rounds
    with pytest.raises(NotAuthorized, match="current output"):
        _merge(s, "m-stale-art", artifact=art1, head=head2)


def test_round_ONES_head_cannot_merge_after_a_repair(two_rounds):
    """The CI guard, which is a different one: `_ci_is_green` compares the
    LATEST observation's head against the head being merged, so the repair's
    own CI observation is what supersedes the old head -- not the review."""
    from kernel.authz import NotAuthorized
    s, _art1, art2, _head2 = two_rounds
    with pytest.raises(NotAuthorized, match="CI observation"):
        _merge(s, "m-stale-head", artifact=art2, head=HEAD)


def test_a_merge_cannot_be_requested_MID_repair(db):
    """The state guard, the third one. Between `request_revision` and the next
    accept the run is in `planned`/`implementing`, where request_merge is not
    legal at all -- so a crash mid-loop cannot leave a merge reachable."""
    from kernel.artifacts import put_artifact
    from kernel.authz import NotAuthorized
    path, s, art1 = db
    art2 = put_artifact(s, b"derived: outcome=ready head=bbbb note=repaired")
    _round(s, 1, artifact=art1, head=HEAD, verdict="request_revision")
    _reimplement(s, 1, artifact=art2, head="b" * 40)
    with pytest.raises(NotAuthorized, match="not legal from state"):
        _merge(s, "m-midway", artifact=art2, head="b" * 40)


def test_the_revision_itself_does_not_authorise_a_merge(db):
    """A `request_revision` is a REVIEW_VERDICT like any other. If the gate
    looked for the existence of a verdict rather than an `accept`, every failed
    review would authorise its own merge."""
    from kernel.authz import NotAuthorized
    path, s, art1 = db
    _round(s, 1, artifact=art1, head=HEAD, verdict="request_revision")
    with pytest.raises(NotAuthorized):
        _merge(s, "m-rev", artifact=art1, head=HEAD)
