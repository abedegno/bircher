"""The runner's window onto the journal, driven against a REAL kernel.

Criterion 7 of the design: "the runner must observe an accepted REVIEW_VERDICT
carrying the submitted command's causal id before it dispatches any repair
work. Not the adapter's exit code, and not the absence of an error."

So these tests do not hand-build facts. They drive real commands through
`kernel.commands.submit` into a real on-disk store and then ask
`coordinator.cli revisions` what it sees -- because the thing being checked is
whether the kernel's record and the runner's reading of it agree, and two
hand-built fact lists agree with each other by construction.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _sub(s, name, key, actor, role, run="r", **payload):
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
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    art = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=art)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=art)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=art)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    return path, s, art


def _revisions(db_path, key="", mx=2, capsys=None):
    from coordinator.cli import main
    argv = ["revisions", "--db", db_path, "--run-id", "r", "--max", str(mx)]
    if key:
        argv += ["--confirm-command", key]
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_before_any_review_the_full_allowance_is_available(db, capsys):
    rc, out = _revisions(db[0], capsys=capsys)
    assert (rc, out) == (0, "0|2|no")


def test_a_recorded_revision_is_counted_and_confirmed(db, capsys):
    """The happy path, end to end through the real command: the fact the
    kernel writes is the fact the runner reads, keyed by the id the runner
    supplied."""
    path, s, art = db
    _sub(s, "record_review", "rev-1", "codex", Role.REVIEWER,
         verdict="request_revision", artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)

    rc, out = _revisions(path, key="rev-1", capsys=capsys)
    assert (rc, out) == (0, "1|1|yes")


def test_an_ACCEPTED_review_is_not_a_revision(db, capsys):
    """`transition_performed` records {to, via} and no verdict, so every review
    looks identical there. If the count came from transitions this would read
    as a revision and silently spend a round."""
    path, s, art = db
    _sub(s, "record_review", "acc-1", "codex", Role.REVIEWER,
         verdict="accept", artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)

    rc, out = _revisions(path, key="acc-1", capsys=capsys)
    assert (rc, out) == (0, "0|2|no"), (
        "an acceptance was counted as a revision, or confirmed as one")


def test_a_review_that_lost_the_CAS_leaves_nothing_to_confirm(db, capsys):
    """`commands.py` validates, THEN bumps the version under CAS, THEN appends
    REVIEW_VERDICT. A command submitted against a stale version is refused
    after validation and writes no fact -- so the revision did not happen,
    however the adapter exited. This is the window criterion 7 exists for."""
    from kernel.commands import StaleVersion
    path, s, art = db
    stale = s.run_version("r")
    # Something else advances the run first.
    _sub(s, "record_ci_observation", "k6", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha="f" * 40)
    with pytest.raises(StaleVersion):
        submit(s, Command(
            name="record_review", run_id="r", expected_version=stale,
            idempotency_key="rev-lost",
            generation=dispatch(s, "r", actor="codex",
                                role=Role.REVIEWER).generation,
            payload=dict(verdict="request_revision", artifact_hash=art,
                         base_sha=BASE, context_bundle_hash=BUNDLE,
                         policy_version=1)))

    rc, out = _revisions(path, key="rev-lost", capsys=capsys)
    assert (rc, out) == (0, "0|2|no"), (
        "a revision that lost the CAS was reported as recorded -- the runner "
        "would dispatch repair work the kernel never authorised")


def test_a_PREVIOUS_rounds_revision_does_not_confirm_this_one(db, capsys):
    """Round 1 recorded a revision; round 2's command was refused. Matching on
    the verdict alone -- 'is there a request_revision?' -- says yes, and the
    runner dispatches a repair for a round that never opened."""
    path, s, art = db
    _sub(s, "record_review", "rev-1", "codex", Role.REVIEWER,
         verdict="request_revision", artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)

    rc, out = _revisions(path, key="rev-2", capsys=capsys)
    assert (rc, out) == (0, "1|1|no"), (
        "round 1's revision confirmed round 2's missing one")


def test_the_allowance_is_spent_and_reaches_zero(db, capsys):
    """The bound is enforced from the journal, so a coordinator that dies and
    is re-driven gets no fresh allowance."""
    path, s, art = db
    for i in (1, 2):
        _sub(s, "record_review", f"rev-{i}", "codex", Role.REVIEWER,
             verdict="request_revision", artifact_hash=art, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)
        _sub(s, "start_implementation", f"si-{i}", "claude", Role.IMPLEMENTER)
        _sub(s, "record_implementation_output", f"io-{i}", "claude",
             Role.IMPLEMENTER, artifact_hash=art)
        _sub(s, "record_ci_observation", f"ci-{i}", "claude", Role.IMPLEMENTER,
             status="success", head_git_sha=HEAD)

    rc, out = _revisions(path, capsys=capsys)
    assert (rc, out) == (0, "2|0|no")


def test_max_zero_leaves_no_allowance_at_all(db, capsys):
    """BIRCHER_MAX_REVISIONS=0 must restore today's behaviour exactly, which
    is what makes the switch a real rollback rather than a path that usually
    agrees."""
    rc, out = _revisions(db[0], mx=0, capsys=capsys)
    assert (rc, out) == (0, "0|0|no")


def test_an_unreadable_journal_is_a_LOOKUP_FAILURE_not_zero_revisions(
        tmp_path, capsys):
    """Zero-used would hand the loop a full allowance every round and make the
    bound unenforceable -- the failure would present as a loop that never
    terminates, not as an error."""
    from coordinator.cli import RC_LOOKUP_FAILED
    bad = tmp_path / "not-a-db"
    bad.write_bytes(b"this is not sqlite" * 100)
    rc, out = _revisions(str(bad), capsys=capsys)
    assert rc == RC_LOOKUP_FAILED
    assert out == "", "a lookup failure printed a countable answer"


# --- criterion 4: no approval survives a round -------------------------------
#
# THE safety property of the whole loop. A repair produces a new commit, so
# everything the previous review bound is superseded. If a merge could still be
# authorised against what the FIRST reviewer read, the loop would merge code no
# reviewer ever saw -- strictly worse than the terminal `failed` it replaces.
#
# THREE INDEPENDENT GUARDS refuse this, not one, and the first version of these
# tests attributed every refusal to the review binding. That was wrong, and it
# passed: a `pytest.raises(NotAuthorized)` cannot tell which guard fired, so it
# read as evidence for a mechanism that was not the one doing the work. Each
# test below now pins the REASON, so a change that removes one guard cannot be
# masked by another still refusing for a different cause.
#
#   superseded artifact -> "not this run's current output"
#   stale head          -> "no successful CI observation for the head"
#   wrong state         -> "not legal from state"
#
# Each round uses a DISTINCT artifact, as production does: the recorded output
# text contains the head, so its hash necessarily differs per round. Reusing one
# artifact across rounds -- which the first version did -- disables the artifact
# guard entirely and leaves the test measuring only the other two.


def _round(s, n, *, artifact, head, verdict):
    """One repair round: revise, re-implement to *artifact*, re-observe at *head*."""
    _sub(s, "record_review", f"rv-{n}", "codex", Role.REVIEWER,
         verdict=verdict, artifact_hash=artifact, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)


def _reimplement(s, n, *, artifact, head):
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
