"""The derivation, driven for real.

Every dependency arrives in one injected object, so these exercise `derive`
rather than a rearrangement of it.
"""
import pytest

from coordinator.outcome import Deps, derive


def _deps(**over):
    posted = []
    base = dict(checks=lambda pr: "build|pass",
                head_of=lambda pr: "a" * 40,
                review=lambda pr, sha: ("PASS", ""),
                effect=lambda c, k, a: posted.append((c, k, a)) or "ok",
                history=lambda br: ("true", 0),
                branch_of=lambda pr: "feat-x")
    base.update(over)
    d = Deps(**base)
    d.posted = posted
    return d


def test_the_tuple_has_seven_fields_in_order():
    r = derive("i1", "i1", "7", "", deps=_deps())
    assert len(r.as_tuple()) == 7
    assert r.outcome == "ready"
    assert r.as_tuple()[5:] == ("true", 0)
    assert len(r.as_line().split("|")) == 7


def test_a_red_pr_never_reaches_the_reviewer():
    """CI is checked BEFORE the verdict. A PASS on a red PR must not merge, and
    dispatching a reviewer at all wastes a run on a foregone answer."""
    asked = []
    d = _deps(checks=lambda pr: "build|fail",
              review=lambda pr, sha: (asked.append(pr) or "PASS", ""))
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.outcome == "failed" and asked == []


def test_the_reviewed_sha_is_captured_BEFORE_the_review_is_dispatched():
    """#66. The head travels INTO the reviewer and is never re-read afterwards;
    a push landing mid-review would otherwise be blessed as reviewed."""
    order = []
    d = _deps(head_of=lambda pr: (order.append("head"), "b" * 40)[1],
              review=lambda pr, sha: ((order.append(f"review:{sha}"), "PASS")[1], ""))
    r = derive("i1", "i1", "7", "", deps=d)
    assert order == ["head", "review:" + "b" * 40]
    assert r.sha == "b" * 40


def test_a_non_40_hex_head_yields_an_EMPTY_sha_field():
    """An unpinnable head must not authorise a merge."""
    for bad in ("nope", "", "abc", "A" * 40, "g" * 40, "a" * 39):
        r = derive("i1", "i1", "7", "", deps=_deps(head_of=lambda pr, b=bad: b))
        assert r.sha == "", bad


def test_a_PENDING_ci_is_waited_on_before_classifying():
    waited = []
    d = _deps(checks=lambda pr: "build|pending",
              wait_ci=lambda pr: waited.append(pr) or "green")
    r = derive("i1", "i1", "7", "", deps=d)
    assert waited == ["7"] and r.outcome == "ready"


def test_an_infra_red_is_re_run_at_most_the_configured_number_of_times():
    """The stub RAISES past the cap rather than letting the loop run.

    Removing the cap makes this loop forever -- `rerun` keeps returning red and
    `failure_kind` keeps saying infra -- so without a self-bound the mutation
    that proves the cap binds HANGS the suite instead of failing it. A test
    that can only be killed by a timeout reports nothing.
    """
    tries = []

    def rerun(pr):
        tries.append(pr)
        if len(tries) > 5:
            raise AssertionError(f"re-run loop is unbounded: {len(tries)} attempts")
        return "red"

    d = _deps(checks=lambda pr: "build|fail",
              failure_kind=lambda pr: "infra", rerun=rerun)
    derive("i1", "i1", "7", "", deps=d, rerun_max=2)
    assert len(tries) == 2, tries


def test_a_GENUINE_red_is_never_re_run():
    """Re-running a real test failure spends CI minutes to get the same answer."""
    tries = []
    d = _deps(checks=lambda pr: "build|fail",
              failure_kind=lambda pr: "genuine",
              rerun=lambda pr: tries.append(pr) or "red")
    derive("i1", "i1", "7", "", deps=d)
    assert tries == []


def test_the_comment_is_posted_through_the_effect_path():
    d = _deps()
    derive("i1", "i1", "7", "", deps=d)
    assert [c for c, _k, _a in d.posted] == ["comment"]


def test_the_comment_carries_no_bircher_status_line():
    d = _deps()
    derive("i1", "i1", "7", "", deps=d)
    assert "bircher-status" not in " ".join(str(a) for _c, _k, a in d.posted)


def test_no_comment_is_posted_when_there_is_no_pr():
    d = _deps(discover_by_code=lambda code: [])
    derive("i1", "i1", "", "", deps=d)
    assert d.posted == []


def test_an_abandoned_tracked_pr_is_dropped_and_rediscovered():
    d = _deps(pr_state=lambda pr: ("CLOSED", "") if pr == "7" else ("OPEN", ""),
              discover_by_code=lambda code: ["9"])
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.outcome == "ready"
    assert d.posted and "9" in d.posted[0][2]


def test_no_pr_anywhere_is_a_timeout():
    d = _deps(discover_by_code=lambda code: [], discover_by_issue=lambda i: [])
    r = derive("i1", "i1", "", "", deps=d)
    assert (r.outcome, r.ci) == ("timeout", "na")


def test_exactly_one_issue_match_adopts_and_two_do_not():
    """Two or more are left for a human: this path has no live escalation
    channel, and choosing would be a guess about which PR the item produced."""
    one = _deps(discover_by_code=lambda c: [], discover_by_issue=lambda i: ["9"])
    assert derive("i1", "i1", "", "711", deps=one).outcome == "ready"

    two = _deps(discover_by_code=lambda c: [], discover_by_issue=lambda i: ["9", "10"])
    assert derive("i1", "i1", "", "711", deps=two).outcome == "timeout"


def test_a_reconciled_sibling_replaces_the_tracked_pr():
    d = _deps(reconcile=lambda code, pr: "6")
    derive("i1", "i1", "5", "", deps=d)
    assert "6" in d.posted[0][2]


def test_the_sha_rides_out_only_on_a_ready_outcome():
    """It is the merge-authorising evidence; a failed derivation must not carry
    one."""
    d = _deps(review=lambda pr, sha: ("FAIL", ""))
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.outcome == "failed" and r.sha == ""


def test_a_reviewer_with_no_verdict_escalates():
    d = _deps(review=lambda pr, sha: (None, ""))
    assert derive("i1", "i1", "7", "", deps=d).outcome == "escalated"


def test_the_ci_history_travels_into_the_last_two_fields():
    d = _deps(history=lambda br: ("false", 3))
    r = derive("i1", "i1", "7", "", deps=d)
    assert (r.ci_first, r.resubmissions) == ("false", 3)


def test_an_unknown_history_leaves_the_resubmission_field_empty():
    """`unknown|` must not become `unknown|0`: zero is a claim."""
    d = _deps(branch_of=lambda pr: "")
    r = derive("i1", "i1", "7", "", deps=d)
    assert (r.ci_first, r.resubmissions) == ("unknown", None)
    assert r.as_line().endswith("|unknown|")


def test_the_reviewers_findings_are_kept_in_the_comment():
    """Decision 3 of C8 Phase 2: the findings are the most useful thing on the
    PR for a human, and only the machine-readable prefix was retired. The first
    port dropped them and `--self-test` caught it."""
    d = _deps(review=lambda pr, sha: ("PASS", "finding: the retry is unbounded"))
    derive("i1", "i1", "7", "", deps=d)
    body = " ".join(str(a) for _c, _k, a in d.posted)
    assert "finding: the retry is unbounded" in body
    assert "Cross-vendor review" in body


def test_with_no_findings_the_short_form_is_used():
    d = _deps(review=lambda pr, sha: ("PASS", ""))
    derive("i1", "i1", "7", "", deps=d)
    body = " ".join(str(a) for _c, _k, a in d.posted)
    assert "Outcome derived from the repository" in body


def test_the_reviewer_vendor_is_carried_into_the_verdict():
    """Cross-vendor independence. The reviewer must be the OPPOSITE vendor to
    the implementer, and a live run found it silently defaulting to the same
    one -- `RECOVERY_REVIEWER` is a shell assignment, not an export, so the
    subprocess never saw it."""
    d = _deps()
    d.reviewer = "claude_code"
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.review == "claude_code:pass"
