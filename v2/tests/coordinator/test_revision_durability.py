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
