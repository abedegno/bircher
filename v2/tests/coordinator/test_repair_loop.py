"""A reviewer FAIL with rounds remaining is a revision, not an ending.

Measured basis: 8 of 18 muesli item-runs ended `failed` on a reviewer FAIL,
every one a specific actionable finding with a named fix. Routing those back by
hand merged #740 after 1 round and #750 after 2; #722 produced a distinct
finding at every round and is still open. So the loop converges sometimes, and
the bound matters as much as the loop.

Design: docs/superpowers/specs/2026-08-31-repair-loop-design.md
"""
import pytest

from coordinator.observe import Outcome, classify, revisions_used


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
    """The allowance comes from the journal precisely so a re-driven
    coordinator does not get a fresh one."""
    assert revisions_used([_F("review_verdict", {"verdict": "request_revision"})]) == 1


def test_an_empty_or_absent_journal_is_zero():
    assert revisions_used([]) == 0
    assert revisions_used(None) == 0


# --- the classification ------------------------------------------------------

@pytest.mark.parametrize("left,expected", [(0, "failed"), (1, "revise"), (2, "revise")])
def test_a_fail_revises_only_while_rounds_remain(left, expected):
    assert classify("7", "green", "FAIL", reviewer="codex",
                    revisions_left=left).outcome == expected


def test_the_default_reproduces_the_behaviour_before_the_loop():
    """BIRCHER_MAX_REVISIONS=0 must be a real rollback, so the default
    argument alone has to give today's answer exactly."""
    before = Outcome("failed", "codex:fail", "green", "out-of-band review FAIL")
    got = classify("7", "green", "FAIL", reviewer="codex")
    assert (got.outcome, got.review, got.ci, got.note) == (
        before.outcome, before.review, before.ci, before.note)


def test_only_a_FAIL_revises():
    """A PASS merges and a missing verdict escalates, whatever the allowance.
    Reading silence as a repairable failure would spend rounds on a reviewer
    that never ran -- which has happened here for other reasons."""
    assert classify("7", "green", "PASS", reviewer="codex", revisions_left=2).outcome == "ready"
    assert classify("7", "green", None, reviewer="codex", revisions_left=2).outcome == "escalated"
    assert classify("7", "red", "FAIL", reviewer="codex", revisions_left=2).outcome == "failed"
    assert classify(None, "green", "FAIL", reviewer="codex", revisions_left=2).outcome == "timeout"


def test_a_revision_keeps_the_reviewer_verdict_as_evidence():
    """The runner needs to know WHO failed it and why, to route the finding."""
    o = classify("7", "green", "FAIL", reviewer="claude_code", revisions_left=1)
    assert o.review == "claude_code:fail"
    assert "revising" in o.note


# --- the findings must reach the runner, and not via the tuple ---------------

def test_the_findings_never_enter_the_pipe_delimited_line():
    """The reviewer's output is multi-paragraph text containing pipes and
    newlines; the tuple is ONE pipe-delimited line whose width guard rejects
    both. Putting the findings in it would corrupt every field after them."""
    from coordinator.outcome import Derived
    d = Derived("revise", "cx:fail", "n", "a" * 40, "green", "true", 0, "7",
                findings="blocking:\n- one | two\n- three")
    assert len(d.as_line().split("|")) == Derived.FIELDS == 8
    assert "\n" not in d.as_line()
    assert "blocking" not in d.as_line()


def test_the_findings_ride_out_only_on_a_revise():
    """On any other outcome the runner has nothing to route them to, and a
    scorecard note is not a place for a multi-paragraph review."""
    from coordinator.outcome import Deps, derive

    def _d(**over):
        base = dict(checks=lambda pr: "build|pass", head_of=lambda pr: "a" * 40,
                    review=lambda pr, sha: ("FAIL", "blocking: the thing"),
                    effect=lambda c, k, a: "ok", history=lambda br: ("true", 0),
                    branch_of=lambda pr: "feat-x")
        base.update(over)
        return Deps(**base)

    revising = derive("i1", "i1", "7", "", deps=_d(revisions_left=2))
    assert revising.outcome == "revise"
    assert "blocking: the thing" in revising.findings

    terminal = derive("i1", "i1", "7", "", deps=_d(revisions_left=0))
    assert terminal.outcome == "failed"
    assert terminal.findings == "", (
        "findings must not ride out when there is no round to spend them on")

    passing = derive("i1", "i1", "7", "",
                     deps=_d(review=lambda pr, sha: ("PASS", "looks fine"),
                             revisions_left=2))
    assert passing.outcome == "ready"
    assert passing.findings == ""


def test_the_findings_file_is_written_before_the_tuple_is_printed():
    """A caller reads the tuple, sees `revise`, then reads the file. Printing
    first would let it act on `revise` while the findings were unwritten."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "coordinator" / "cli.py").read_text()
    i = src.index("findings_out")
    write = src.index("fh.write(r.findings)", i)
    printed = src.index("print(r.as_line()", i)
    assert write < printed, "the findings file must be written before the tuple"
    assert re.search(r"could not write findings", src), (
        "a failed write must be reported, or the runner dispatches a repair "
        "with an empty brief and no way to know")
