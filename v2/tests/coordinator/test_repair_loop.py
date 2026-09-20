"""A reviewer FAIL is a repair round, not an ending.

Measured basis: 8 of 18 muesli item-runs ended `failed` on a reviewer FAIL,
every one a specific actionable finding with a named fix. Routing those back by
hand merged #740 after 1 round and #750 after 2; #722 produced a distinct
finding at every round and is still open. So the loop converges sometimes, and
convergence is the whole job now that nothing bounds the rounds.

Design: docs/superpowers/specs/2026-08-31-repair-loop-design.md
"""

import pathlib

import pytest

from coordinator.observe import classify, revisions_used


class _F:
    """A journal fact, shaped like the store's."""
    def __init__(self, kind, payload):
        self.kind, self.payload = kind, payload


# --- the count: the RIGHT fact, not a plausible one --------------------------

def test_revisions_are_counted_from_review_verdict_facts():
    """`transition_performed` records `{"to":..., "via":"record_review"}` and
    NOT the verdict, so every accepted review looks identical there. An
    implementation counting those would be indistinguishable from a correct one
    on a journal containing only revisions -- hence the MIX below."""
    facts = [
        _F("review_verdict", {"verdict": "accept"}),
        _F("review_verdict", {"verdict": "request_revision"}),
        _F("transition_performed", {"to": "planned", "via": "record_review"}),
        _F("review_verdict", {"verdict": "reject"}),
        _F("review_verdict", {"verdict": "request_revision"}),
        _F("transition_performed", {"to": "reviewing", "via": "record_review"}),
    ]
    assert revisions_used(facts) == 2, (
        "counted something other than request_revision verdicts")


def test_counting_review_transitions_would_give_the_wrong_answer():
    """States the trap explicitly: the journal above has THREE review
    transitions and TWO revisions. A test built only from revisions could not
    tell those apart."""
    facts = [
        _F("transition_performed", {"to": "reviewing", "via": "record_review"}),
        _F("transition_performed", {"to": "planned", "via": "record_review"}),
        _F("transition_performed", {"to": "reviewing", "via": "record_review"}),
    ]
    assert revisions_used(facts) == 0, (
        "review transitions carry no verdict and must never be counted")


def test_a_pre_crash_revision_still_counts():
    """The count comes from the journal precisely so a re-driven coordinator
    reads the same history: `_round_number` uses it for the status
    description's round number."""
    assert revisions_used([_F("review_verdict", {"verdict": "request_revision"})]) == 1


def test_an_empty_or_absent_journal_is_zero():
    assert revisions_used([]) == 0
    assert revisions_used(None) == 0


# --- the findings must reach the runner, and not via the tuple ---------------

def test_the_findings_never_enter_the_pipe_delimited_line():
    """The reviewer's output is multi-paragraph text containing pipes and
    newlines; the tuple is ONE pipe-delimited line whose width guard rejects
    both. Putting the findings in it would corrupt every field after them."""
    from coordinator.outcome import Derived
    d = Derived("repair", "cx:fail", "n", "a" * 40, "green", "true", 0, "7",
                findings="blocking:\n- one | two\n- three")
    assert len(d.as_line().split("|")) == Derived.FIELDS == 13
    assert "\n" not in d.as_line()
    assert "blocking" not in d.as_line()


def test_the_findings_ride_out_on_every_fail():
    """The repair round is briefed from them."""
    from coordinator.outcome import Deps, derive

    def _d(**over):
        base = dict(checks=lambda pr: "build|pass", head_of=lambda pr: "a" * 40,
                    review=lambda pr, sha: ("FAIL", "blocking: the thing"),
                    effect=lambda c, k, a: "ok", history=lambda br: ("true", 0),
                    branch_of=lambda pr: "feat-x")
        base.update(over)
        return Deps(**base)

    repairing = derive("i1", "i1", "7", "", deps=_d())
    assert repairing.outcome == "repair"
    assert "blocking: the thing" in repairing.findings

    passing = derive("i1", "i1", "7", "",
                     deps=_d(review=lambda pr, sha: ("PASS", "looks fine")))
    assert passing.outcome == "ready"
    assert passing.findings == ""


# --- the findings file is EVIDENCE, so drive the real CLI ------------------
#
# These replace an earlier source-grep test. A grep for `fh.write` before
# `print` cannot see a stale file, a partial write, or a `repair` published
# without its brief -- exactly the three failures that matter here. Drive
# `main()` and look at the filesystem.

def _derive_argv(out, item="i1"):
    return ["derive", "--item", item, "--code", item, "--pr", "7",
            "--repo", "o/r", "--reviewer", "cx", "--findings-out", out]


def _fake_live_deps(verdict="FAIL", findings="blocking:\n- the thing | here"):
    """Stands in for coordinator.wiring.live_deps, which reaches the network."""
    from coordinator.outcome import Deps

    def _f(item, *, repo, reviewer=None, server=None, bundle_dir=None,
           poll_interval=None, ci_wait=None, rerun_wait=None, **kw):
        return Deps(checks=lambda pr: "build|pass",
                    head_of=lambda pr: "a" * 40,
                    review=lambda pr, sha: (verdict, findings),
                    effect=lambda c, k, a: "ok",
                    history=lambda br: ("true", 0),
                    branch_of=lambda pr: "feat-x")
    return _f


def _install(monkeypatch, deps):
    import sys
    import types
    mod = types.ModuleType("coordinator.wiring")
    mod.live_deps = deps
    monkeypatch.setitem(sys.modules, "coordinator.wiring", mod)


def test_a_repair_writes_the_findings_and_then_prints_the_tuple(
        tmp_path, capsys, monkeypatch):
    from coordinator.cli import RC_OK, main
    out = str(tmp_path / "findings.txt")
    _install(monkeypatch, _fake_live_deps())

    assert main(_derive_argv(out)) == RC_OK
    line = capsys.readouterr().out
    assert line.split("|")[0] == "repair"
    assert pathlib.Path(out).read_text() == "blocking:\n- the thing | here"
    assert "|" not in line.split("|")[2]  # the note, not the findings


def test_a_previous_rounds_findings_cannot_survive_into_this_one(
        tmp_path, capsys, monkeypatch):
    """THE failure this file exists for. Round 1 leaves findings on disk;
    round 2 passes. If the file survives, the runner reads round 1's brief
    beside round 2's verdict and every observable signal looks normal."""
    from coordinator.cli import main
    out = tmp_path / "findings.txt"
    out.write_text("ROUND ONE: the old finding")

    _install(monkeypatch, _fake_live_deps(verdict="PASS", findings="fine"))
    assert main(_derive_argv(str(out))) == 0
    assert capsys.readouterr().out.split("|")[0] == "ready"
    assert not out.exists(), (
        "a passing round left the previous round's findings on disk")


def test_a_derivation_killed_mid_flight_leaves_no_findings_behind(
        tmp_path, monkeypatch):
    """Derivation runs as long as CI does and its budget can kill it at any
    point. Clearing the path up front is what makes the file's existence
    evidence rather than an assumption -- so the clear must happen BEFORE
    derivation, not after it."""
    from coordinator.cli import main
    out = tmp_path / "findings.txt"
    out.write_text("ROUND ONE: the old finding")

    def _boom(*a, **kw):
        raise KeyboardInterrupt("budget expired")

    _install(monkeypatch, _boom)
    with pytest.raises(KeyboardInterrupt):
        main(_derive_argv(str(out)))
    assert not out.exists()


def test_an_unwritable_findings_path_never_publishes_a_repair(
        tmp_path, capsys, monkeypatch):
    """A `repair` the caller cannot brief is worse than no answer: it
    dispatches a repair with an empty brief and no way to know."""
    from coordinator.cli import RC_FINDINGS_UNWRITABLE, main
    d = tmp_path / "ro"
    d.mkdir()
    out = str(d / "findings.txt")
    _install(monkeypatch, _fake_live_deps())
    d.chmod(0o500)
    try:
        rc = main(_derive_argv(out))
    finally:
        d.chmod(0o700)
    cap = capsys.readouterr()
    assert rc == RC_FINDINGS_UNWRITABLE
    assert cap.out == "", "the repair tuple must not be published"
    assert "could not write findings" in cap.err
    assert not list(d.glob("*.tmp")), "the temp file must not be left behind"


def test_the_findings_are_replaced_atomically_not_written_in_place(
        tmp_path, capsys, monkeypatch):
    """A crash mid-write to the real path leaves a truncated brief that reads
    as a complete one. The mutation this kills is `open(findings_out, "w")`
    directly: the target must never be opened for writing."""
    import builtins
    from coordinator.cli import main
    out = str(tmp_path / "findings.txt")
    opened = []
    real_open = builtins.open

    def _spy(f, mode="r", *a, **kw):
        if "w" in str(mode) or "a" in str(mode):
            opened.append(str(f))
        return real_open(f, mode, *a, **kw)

    _install(monkeypatch, _fake_live_deps())
    monkeypatch.setattr(builtins, "open", _spy)
    assert main(_derive_argv(out)) == 0
    monkeypatch.undo()

    assert opened, "nothing was written"
    assert out not in opened, (
        f"the target was opened for writing directly: {opened}")
    assert all(f.startswith(out) and f.endswith(".tmp") for f in opened)
    assert pathlib.Path(out).read_text().startswith("blocking:")


def test_no_findings_path_still_derives():
    """`--findings-out` is optional; every non-repair caller omits it."""
    from coordinator.outcome import Deps, derive
    r = derive("i1", "i1", "7", "", deps=Deps(
        checks=lambda pr: "build|pass", head_of=lambda pr: "a" * 40,
        review=lambda pr, sha: ("FAIL", "x"), effect=lambda c, k, a: "ok",
        history=lambda br: ("true", 0), branch_of=lambda pr: "feat-x"))
    assert r.outcome == "repair" and r.findings == "x"


# --- the head must ride out on a repair --------------------------------------
#
# Found by the FIRST LIVE RUN, not by any of the 1042 tests that were green when
# it launched. The reviewer FAILed, `revise` was derived correctly, and the head
# was withheld -- so the runner skipped the block that records the revision, the
# durability gate found nothing, and the item escalated. Everything downstream
# behaved correctly on a value that should never have been empty.

def _deps(verdict, **over):
    from coordinator.outcome import Deps
    base = dict(checks=lambda pr: "build|pass", head_of=lambda pr: "a" * 40,
                review=lambda pr, sha: (verdict, "blocking: the thing"),
                effect=lambda c, k, a: "ok", history=lambda br: ("true", 0),
                branch_of=lambda pr: "feat-x")
    base.update(over)
    return Deps(**base)


def test_a_repair_carries_the_reviewed_head():
    """Without it the runner records NO output, NO CI observation and NO
    review -- so there is no revision for the durability gate to confirm and
    the loop cannot start."""
    from coordinator.outcome import derive
    r = derive("i1", "i1", "7", "", deps=_deps("FAIL"))
    assert r.outcome == "repair"
    assert r.sha == "a" * 40, (
        "a repair with no head skips the runner's entire kernel lifecycle block")


def test_the_head_a_repair_carries_is_the_one_the_reviewer_READ():
    """Re-reading it after the verdict would bless a push that landed in
    between -- the #66 rule, which applies to a repair exactly as it does to
    an acceptance, because the review this head binds is what
    `validate_review` checks against."""
    from coordinator.outcome import derive
    seen = []

    def _review(pr, sha):
        seen.append(sha)
        return ("FAIL", "blocking: the thing")

    r = derive("i1", "i1", "7", "", deps=_deps("FAIL", review=_review))
    assert seen == [r.sha], (
        f"the head reported ({r.sha[:7]}) is not the one reviewed ({seen})")
