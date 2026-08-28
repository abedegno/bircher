"""What the kernel will publish is OBSERVED, not reported."""
import subprocess

import pytest

from kernel.ids import Clock
from kernel.nomination import NotPublishable, verify_nomination
from kernel.store import Store

ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _git(wt, *args):
    r = subprocess.run(["git", "-C", str(wt), *args], capture_output=True,
                       text=True, env=ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    (wt / "f").write_text("base")
    _git(wt, "add", "f")
    _git(wt, "commit", "-qm", "base")
    return wt


def _store(base):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=base)
    return s


def test_the_branch_tip_is_what_is_published(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    assert verify_nomination(_store(base), "r", repo, "work") == tip


def test_a_commit_not_descended_from_the_RECORDED_base_is_refused(repo):
    """The base the KERNEL recorded, not the checkout's HEAD. A recovery on the
    predecessor branch bound `git rev-parse HEAD` at recovery time against a
    base recorded hours earlier, and every review was refused."""
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")

    # A parentless commit: a real oid, reachable from nothing on this branch.
    unrelated = _git(repo, "commit-tree", "-m", "unrelated",
                     _git(repo, "rev-parse", "HEAD^{tree}"))
    with pytest.raises(NotPublishable, match="does not descend"):
        verify_nomination(_store(unrelated), "r", repo, "work")


def test_a_merge_commit_in_the_lineage_is_refused(repo):
    """A merge imports history the kernel never saw."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "side")
    (repo / "s").write_text("side")
    _git(repo, "add", "s"); _git(repo, "commit", "-qm", "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "m").write_text("main")
    _git(repo, "add", "m"); _git(repo, "commit", "-qm", "main2")
    _git(repo, "checkout", "-qb", "work")
    _git(repo, "merge", "--no-ff", "-m", "merge", "side")

    with pytest.raises(NotPublishable, match="merge commit"):
        verify_nomination(_store(base), "r", repo, "work")


def test_a_claimed_oid_that_differs_from_the_tip_is_REFUSED(repo):
    """Never a tiebreak and never a fallback: the observation decides, and a
    claim can only disagree with it. Both values appear in the refusal."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(NotPublishable) as e:
        verify_nomination(_store(base), "r", repo, "work", claimed_oid="b" * 40)
    assert tip[:12] in str(e.value) and ("b" * 40)[:12] in str(e.value)


def test_a_matching_claim_is_accepted(repo):
    """The claim is allowed to be right; it is simply not allowed to decide."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    assert verify_nomination(_store(base), "r", repo, "work", claimed_oid=tip) == tip


def test_a_missing_branch_is_refused(repo):
    base = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(NotPublishable, match="no such branch"):
        verify_nomination(_store(base), "r", repo, "nope")


def test_a_branch_identical_to_base_publishes_nothing(repo):
    """No commit is a noop, not a publication of the base."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    with pytest.raises(NotPublishable, match="no commit"):
        verify_nomination(_store(base), "r", repo, "work")


def test_a_run_with_no_recorded_base_is_refused(repo):
    """Provenance needs something to check against. A run the kernel never
    started has nothing, and inventing one is how the predecessor branch
    fabricated the history its merge gate exists to check."""
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")

    with pytest.raises(NotPublishable, match="no base"):
        verify_nomination(_store(""), "r", repo, "work")


def test_the_cli_prints_the_oid_it_will_publish(repo, capsys):
    from kernel.cli import main

    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    db = repo.parent / "k.db"
    s = Store.open(db)
    s.create_run(run_id="r", base_repo="o/r", base_sha=base)

    rc = main(["verify-nomination", "--db", str(db), "--run-id", "r",
               "--worktree", str(repo), "--branch", "work"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == tip


def test_the_cli_refuses_with_a_nonzero_code_and_says_why(repo, capsys):
    """The coordinator branches on the code and reports the reason; a refusal
    that exits 0 is a publication."""
    from kernel.cli import main

    db = repo.parent / "k2.db"
    s = Store.open(db)
    s.create_run(run_id="r", base_repo="o/r", base_sha="0" * 40)
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")

    rc = main(["verify-nomination", "--db", str(db), "--run-id", "r",
               "--worktree", str(repo), "--branch", "work"])
    assert rc != 0
    assert "does not descend" in capsys.readouterr().err

