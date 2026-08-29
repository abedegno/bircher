"""The observers, tested natively.

Contrast with what these replaced: the bash versions were tested by locating a
function in a 7,000-line script by name, slicing to its closing brace, and
driving the fragment with stubbed shell builtins. Every one of those tests is
gone, and nothing here needs a subprocess.
"""
import json

import pytest

from coordinator.observe import CiHistory, GhError, ci_history, classify


def _gh_returning(payload):
    def gh(args):
        assert args[0] == "api", args
        assert "actions/runs" in args[1], args
        return json.dumps(payload)
    return gh


def _run(sha, conclusion, created_at):
    return {"head_sha": sha, "conclusion": conclusion, "created_at": created_at}


# --- ci_history --------------------------------------------------------------

def test_one_green_run_is_first_time_green_with_no_resubmissions():
    h = ci_history("o/r", "feat-x", gh=_gh_returning(
        {"workflow_runs": [_run("aaa", "success", "2026-08-01T10:00:00Z")]}))
    assert h == CiHistory("true", 0)


def test_the_EARLIEST_run_decides_ci_first_not_the_latest():
    """A branch that went red then green passed on the SECOND try. Reading the
    newest run would call every eventually-green branch first-time-green --
    the metric inverted, and it would read as a success."""
    h = ci_history("o/r", "feat-x", gh=_gh_returning({"workflow_runs": [
        _run("bbb", "success", "2026-08-01T12:00:00Z"),
        _run("aaa", "failure", "2026-08-01T10:00:00Z"),
    ]}))
    assert h == CiHistory("false", 1)


def test_resubmissions_counts_DISTINCT_commits_not_runs():
    """Re-running CI on one commit is not a resubmission. Observed live on
    muesli: three runs on a single sha."""
    h = ci_history("o/r", "feat-x", gh=_gh_returning({"workflow_runs": [
        _run("aaa", "failure", "2026-08-01T10:00:00Z"),
        _run("aaa", "success", "2026-08-01T10:30:00Z"),
    ]}))
    assert h == CiHistory("false", 0)


def test_a_run_still_in_flight_is_not_read_as_a_verdict():
    """A null conclusion is 'not finished', not 'not success'."""
    h = ci_history("o/r", "feat-x", gh=_gh_returning(
        {"workflow_runs": [_run("aaa", None, "2026-08-01T10:00:00Z")]}))
    assert h == CiHistory("unknown", None)


def test_no_runs_at_all_is_unknown_not_false():
    h = ci_history("o/r", "feat-x", gh=_gh_returning({"workflow_runs": []}))
    assert h == CiHistory("unknown", None)


def test_a_gh_failure_is_unknown_not_a_silent_zero():
    """`gh` exiting non-zero must not read as 'no runs, so first-time green'."""
    def boom(args):
        raise GhError("HTTP 403")
    assert ci_history("o/r", "feat-x", gh=boom) == CiHistory("unknown", None)


def test_unparseable_output_is_unknown():
    assert ci_history("o/r", "x", gh=lambda a: "not json") == CiHistory("unknown", None)


def test_the_branch_is_actually_passed_to_the_api():
    """A query that ignored its branch would report the repo's whole history
    for every item, and every number in the scorecard would be the same."""
    seen = {}

    def gh(args):
        seen["url"] = args[1]
        return json.dumps({"workflow_runs": []})

    ci_history("acme/widgets", "feat-42", gh=gh)
    assert "repos/acme/widgets/actions/runs" in seen["url"]
    assert "branch=feat-42" in seen["url"]


# --- classify ----------------------------------------------------------------

@pytest.mark.parametrize("verdict,expected", [
    ("PASS", ("ready", "codex:pass")),
    ("FAIL", ("failed", "codex:fail")),
    ("NONE", ("escalated", "codex:na")),
    (None, ("escalated", "codex:na")),
    ("", ("escalated", "codex:na")),
    ("pass", ("escalated", "codex:na")),      # case matters; no fuzzy matching
])
def test_a_green_pr_is_classified_by_its_verdict(verdict, expected):
    o = classify("42", "green", verdict, reviewer="codex")
    assert (o.outcome, o.review) == expected


def test_silence_is_never_approval():
    """The single most important row above, stated on its own: a reviewer that
    produced no verdict has approved nothing."""
    assert classify("42", "green", None, reviewer="codex").outcome != "ready"


def test_no_pr_is_a_timeout_regardless_of_everything_else():
    o = classify(None, "green", "PASS", reviewer="codex")
    assert (o.outcome, o.ci) == ("timeout", "na")


def test_red_ci_fails_without_consulting_the_verdict():
    """CI is checked BEFORE the verdict: a PASS on a red PR must not merge."""
    o = classify("42", "red", "PASS", reviewer="codex")
    assert (o.outcome, o.ci) == ("failed", "red")


def test_pending_ci_escalates_rather_than_guessing():
    o = classify("42", "pending", "PASS", reviewer="codex")
    assert o.outcome == "escalated"


def test_the_reviewer_name_travels_into_the_verdict_string():
    assert classify("42", "green", "PASS", reviewer="claude_code").review == "claude_code:pass"
