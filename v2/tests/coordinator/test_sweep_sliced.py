# v2/tests/coordinator/test_sweep_sliced.py
"""Task 9: the sweep (shaping spec §3 *The sweep*, §7's closure rows)."""
import json
import os

import pytest

from coordinator import sweep
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


class GH:
    """A dict-backed `gh_json`: `issues[n]` and `prs[n]` are what GitHub holds."""
    def __init__(self):
        self.issues = {12: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []},
                       40: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []},
                       41: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []}}
        self.prs = {}
        self.fail = set()
        self.reads = []

    def __call__(self, argv):
        n = int(argv[3])
        self.reads.append(n)
        if n in self.fail:
            raise sweep.ReadFailed(f"gh exited 1 for #{n}")
        if argv[1:3] == ["pr", "view"]:
            return {"state": self.prs.get(n, "OPEN")}
        fields = argv[argv.index("--json") + 1].split(",")
        return {k: self.issues[n][k] for k in fields}

    def merge(self, n, pr):
        self.issues[n] = {"state": "CLOSED", "closedAt": f"2026-09-10T0{n % 10}:00:00Z",
                          "closedByPullRequestsReferences": [{"number": pr}]}
        self.prs[pr] = "MERGED"

    def close(self, n, pr=None):
        self.issues[n] = {"state": "CLOSED", "closedAt": "2026-09-10T00:00:00Z",
                          "closedByPullRequestsReferences": [] if pr is None else [{"number": pr}]}
        if pr is not None:
            self.prs[pr] = "CLOSED"

    def reopen(self, n):
        self.issues[n] = {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(run_ids=("i12-epic-1",)):
        db = tmp_path / "k.db"
        s = Store.open(db)
        fronts = {}
        fake = FakeOmnigent()
        for i, rid in enumerate(run_ids):
            f = Front(s, rid, shape=False, issue=dict(ISSUE, number=12 + i)).to_sliced()
            g = f._dispatch(Role.OPERATOR, "coordinator")
            # Each run's children start at a distinct range (40, 140, 240, ...)
            # so two runs in the same firing never claim the same child number
            # -- `file_all`'s own default (40) is unchanged for the single-run
            # case, so every existing single-run test still sees 40 and 41.
            f.file_all(g, first_issue=40 + i * 100)
            fronts[rid] = f
            # Ruling 3: the completion close mutates `fake.issues[<parent>]
            # ["state"]` directly, and the fake raises KeyError for an issue it
            # has never seen -- the children are minted by `file_all` through
            # the JOURNAL, never through this fake, so nothing seeds a run's
            # parent unless this fixture does.
            fake.issues[12 + i] = {"state": "OPEN", "labels": [], "blocked_by": []}
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "PATH": os.environ["PATH"]}
        return s, fronts, fake, env
    return make


def _sweep(s, env, gh, **kw):
    return sweep.sweep_sliced(s, server="http://srv", repo="o/r", env=env, gh_json=gh, log=lambda m: None,
                              now=lambda: "2026-09-10T12:00:00Z", **kw)


def test_all_closed_ends_the_run_with_the_completion_comment_and_the_close(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.close(41, 91)
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert s.run_state("i12-epic-1") == "ended"
    closures = {f.payload["slice"]: f.payload for f in s.facts_of_kind("i12-epic-1", EventKind.SLICE_CLOSED)}
    assert closures[1]["state"] == "merged" and closures[2]["state"] == "closed"
    assert closures[1]["closed_at"] == "2026-09-10T00:00:00Z"
    obs = s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED)
    assert obs.payload["parent"] == "open" and obs.payload["slices"] == [1, 2]
    comment = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]][-1]
    assert comment[comment.index("--body") + 1] == (
        "bircher: sliced complete\n\n- #40 Slice 1: The store — merged\n- #41 Slice 2: The API — closed\n")
    assert [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]] == [["gh", "issue", "close", "12", "--repo", "o/r"]]
    # Ruling 1: the fake `GH` records pull-request reads too, so `gh.reads`
    # interleaves a child's read with its PR's -- [40, 90, 41, 91, 12]. The
    # parent is read last, in the same firing.
    assert gh.reads[0] == 40 and gh.reads[-1] == 12


def test_one_open_child_keeps_the_parent_open(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90)
    assert _sweep(s, env, gh) == []
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.current_closure(s, "i12-epic-1", 0, 1).payload["state"] == "merged"
    assert front.current_closure(s, "i12-epic-1", 0, 2) is None
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED) is None
    assert not [a for a in fake.gh if a[:3] in (["gh", "issue", "comment"], ["gh", "issue", "close"])]


def test_skips_a_halted_run_and_one_without_filing_complete(world, tmp_path):
    s, fronts, fake, env = world()
    s.set_reconciliation("i12-epic-1", {"why": "test"})
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91)
    assert _sweep(s, env, gh) == [] and gh.reads == []
    s2 = Store.open(tmp_path / "k2.db")
    Front(s2, "i13-epic-1", shape=False, issue=dict(ISSUE, number=13)).to_sliced()   # sliced, nothing filed
    assert _sweep(s2, dict(env, BIRCHER_KERNEL_DB=str(tmp_path / "k2.db")), gh) == [] and gh.reads == []


def test_three_firings_with_a_reopening(world):
    s, fronts, fake, env = world()
    gh = GH()
    gh.merge(40, 90)
    _sweep(s, env, gh)                                             # A closed, recorded
    gh.reopen(40); gh.merge(41, 91)
    assert _sweep(s, env, gh) == []                                # A reopened, B closed, no outcome
    assert front.current_closure(s, "i12-epic-1", 0, 1).kind == EventKind.SLICE_REOPENED
    assert front.current_closure(s, "i12-epic-1", 0, 2).kind == EventKind.SLICE_CLOSED
    gh.merge(40, 92)
    assert _sweep(s, env, gh) == ["i12-epic-1"]                    # A closed again: the outcome
    assert s.run_state("i12-epic-1") == "ended"


def test_a_failed_read_ends_the_firing_before_completion(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91); gh.fail.add(41)
    assert _sweep(s, env, gh) == []
    assert front.current_closure(s, "i12-epic-1", 0, 1).payload["state"] == "merged"     # recorded before the failure
    assert front.current_closure(s, "i12-epic-1", 0, 2) is None
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED) is None
    assert not [a for a in fake.gh if a[:3] in (["gh", "issue", "comment"], ["gh", "issue", "close"])]
    gh.fail.clear()
    assert _sweep(s, env, gh) == ["i12-epic-1"]


def test_a_hand_closed_parent_ends_without_a_close(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91); gh.close(12)
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED).payload["parent"] == "closed"
    assert [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]]
    assert not [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]]


def test_a_parent_reopened_after_the_pipeline_closed_it_is_the_persons(world):
    s, fronts, fake, env = world()
    f = fronts["i12-epic-1"]
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f.satisfy(g, {"kind": "parent_close", "run": f.run_id, "epoch": 0}, value="closed")   # closed once, before a crash
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91)                  # and the parent is open again
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert not [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]]                # no second close


def test_the_cli_runs_the_sweep_as_a_subprocess(world, tmp_path, monkeypatch):
    import subprocess, sys
    # `world()` monkeypatches `kernel.cli.subprocess.run` -- which, because
    # `kernel.cli` binds the name `subprocess` to the very module object
    # `sys.modules["subprocess"]`, replaces `subprocess.run` for the WHOLE
    # process, this test included. The real function is captured here, before
    # `world()` runs, so launching the CLI below goes through an actual
    # subprocess rather than the fake write-side meant for `gh` argv.
    real_run = subprocess.run
    s, fronts, fake, env = world()
    marker = tmp_path / "gh-called"
    gh_stub = tmp_path / "gh"
    # Ruling 2: under `BIRCHER_EFFECT_MODE=legacy` nothing is journalled, so a
    # completing run's outcome would be refused and the CLI would raise. This
    # stub never lets a child close, so the sweep never attempts completion --
    # the read path and the CLI wiring are what this test proves. It writes a
    # marker on every call so the test can tell the sweep actually read
    # through it rather than short-circuiting.
    gh_stub.write_text('#!/usr/bin/env bash\ntouch "$GH_MARKER"\n'
                       'echo \'{"state":"OPEN","closedAt":null,"closedByPullRequestsReferences":[]}\'\n')
    gh_stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    r = real_run([sys.executable, "-m", "coordinator.cli", "sweep-sliced", "--db", str(tmp_path / "k.db"),
                  "--server", "http://srv", "--repo", "o/r"],
                 cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))), capture_output=True, text=True,
                 env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", BIRCHER_EFFECT_MODE="legacy",
                          GH_MARKER=str(marker)))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""
    assert marker.exists()


def test_a_raising_run_does_not_stop_its_sibling(world):
    """Fix round 1, finding 1: `store.all_run_ids()` is stable oldest-first,
    so a run that raises something other than `ReadFailed` must not abort the
    firing for every run listed after it. A is the OLDER run (created first,
    so listed first) and is the one that raises -- the exact case the
    unisolated loop got wrong."""
    s, fronts, fake, env = world(run_ids=("i12-epic-1", "i13-epic-1"))
    logs = []

    def gh(argv):
        if argv[1:3] == ["issue", "view"]:
            n = int(argv[3])
            if n == 40:                                    # A's first child: not a read failure
                raise RuntimeError("boom")
            if n in (140, 141):                             # B's children: closed by hand
                return {"state": "CLOSED", "closedAt": "2026-09-10T00:00:00Z",
                        "closedByPullRequestsReferences": []}
            if n == 13:                                     # B's parent: still open
                return {"state": "OPEN"}
            raise AssertionError(f"unexpected issue view for #{n}: A's firing should have ended at #40")
        raise AssertionError(f"unexpected argv {argv}")

    result = sweep.sweep_sliced(s, server="http://srv", repo="o/r", env=env, gh_json=gh,
                                log=logs.append, now=lambda: "2026-09-10T12:00:00Z")
    assert result == ["i13-epic-1"]
    assert s.run_state("i13-epic-1") == "ended"
    assert s.run_state("i12-epic-1") == "sliced"
    assert s.facts_of_kind("i12-epic-1", EventKind.SLICE_CLOSED) == []
    assert any("i12-epic-1" in line and "RuntimeError" in line for line in logs), logs


def test_two_healthy_runs_end_in_one_firing(world):
    """Fix round 1, finding 2(b): both runs of one firing end, each under its
    own generation -- one umbrella comment and one parent close per run."""
    s, fronts, fake, env = world(run_ids=("i12-epic-1", "i13-epic-1"))

    def gh(argv):
        if argv[1:3] == ["issue", "view"]:
            n = int(argv[3])
            if n in (12, 13):                               # the parents: still open, so each owes a close
                return {"state": "OPEN"}
            return {"state": "CLOSED", "closedAt": "2026-09-10T00:00:00Z", "closedByPullRequestsReferences": []}
        raise AssertionError(f"unexpected argv {argv}")

    result = sweep.sweep_sliced(s, server="http://srv", repo="o/r", env=env, gh_json=gh,
                                log=lambda m: None, now=lambda: "2026-09-10T12:00:00Z")
    assert result == ["i12-epic-1", "i13-epic-1"]
    assert s.run_state("i12-epic-1") == "ended"
    assert s.run_state("i13-epic-1") == "ended"
    comments = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]]
    assert {c[3] for c in comments} == {"12", "13"}
    closes = [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]]
    assert sorted(closes) == [["gh", "issue", "close", "12", "--repo", "o/r"],
                             ["gh", "issue", "close", "13", "--repo", "o/r"]]
    # Each run's completion effects are satisfied under ITS OWN obligation --
    # `check_filing_effect` refuses an obligation naming the wrong run, so a
    # satisfied row keyed to this run and this epoch is itself the proof.
    for rid, epoch_n in (("i12-epic-1", 0), ("i13-epic-1", 0)):
        assert front.satisfied_obligation(s, rid, {"run": rid, "epoch": epoch_n, "kind": "umbrella_close"}) is not None
        assert front.satisfied_obligation(s, rid, {"run": rid, "epoch": epoch_n, "kind": "parent_close"}) is not None


# --- the default `gh_json` is bounded (fix round 1, minor d) -----------------

def test_the_default_gh_read_is_bounded_and_a_timeout_is_a_ReadFailed(monkeypatch):
    """The sweep runs INSIDE the wave, before the queue is generated, so a `gh`
    that never returns stalls every item and not one run. Two claims, because
    the bound alone is not the behaviour: the read is bounded, AND the timeout
    arrives as the sweep's own `ReadFailed` -- `TimeoutExpired` is not a
    `CalledProcessError`, so without the conversion it would surface through
    the per-run guard as "an unexpected exception", a different log line and a
    different meaning from "a read did not answer"."""
    import subprocess

    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(sweep.subprocess, "run", fake_run)
    with pytest.raises(sweep.ReadFailed) as exc:
        sweep.gh_json(["gh", "issue", "view", "12", "--repo", "o/r"])
    assert seen.get("timeout") == sweep.GH_READ_TIMEOUT
    assert sweep.GH_READ_TIMEOUT > 0
    assert "no answer" in str(exc.value)


def test_the_default_gh_read_still_parses_a_normal_answer(monkeypatch):
    """The guard above must not have swallowed the ordinary path."""
    import subprocess

    monkeypatch.setattr(
        sweep.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '{"state": "OPEN"}', ""))
    assert sweep.gh_json(["gh", "issue", "view", "12"]) == {"state": "OPEN"}
