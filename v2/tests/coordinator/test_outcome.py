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


# --- Cross-vendor review findings, 2026-08-30 (codex adversarial review) -----
# Four defects, none of which the 910-test suite or the field-by-field live
# acceptance could see: the acceptance compared OUTPUTS on a happy path, and
# every one of these only changes behaviour on a slow, failing or retried path.


def test_the_comment_key_is_stable_for_the_same_body():
    """`abs(hash(body))` gave a DIFFERENT key every process.

    Python randomises string hashing per process (PYTHONHASHSEED), so the
    idempotency key could never match a previous attempt: a retry after a
    reconciliation would post a DUPLICATE comment instead of deduplicating.
    The bash this replaced hashed with `shasum -a 256 | cut -c1-16`; the port
    regressed a working mechanism.
    """
    import hashlib

    d1 = _deps()
    derive("i1", "i1", "7", "", deps=d1)
    d2 = _deps()
    derive("i1", "i1", "7", "", deps=d2)

    key1 = d1.posted[0][1]
    key2 = d2.posted[0][1]
    # NOTE: this equality DOES NOT BIND the defect. `hash()` is stable WITHIN
    # a process, so both derives agree even with the bug present -- confirmed
    # by mutation: restoring `abs(hash(body))` left this assertion passing and
    # only the digest assertion below went red. It is kept as documentation of
    # the property, not as the guard.
    assert key1 == key2

    # THIS is the binding assertion: the key must equal a specific,
    # process-independent digest, which `hash()` cannot produce.
    body = d1.posted[0][2][-1]
    assert key1 == f"pr-comment:7:{hashlib.sha256(body.encode()).hexdigest()[:16]}"


def test_the_comment_key_survives_a_different_hash_seed():
    """The real property, across PROCESSES -- which is where it broke.

    Python randomises string hashing per interpreter, so the same comment body
    produced a different idempotency key on every run and a retry could never
    match a previous attempt. Two subprocesses with deliberately different
    PYTHONHASHSEED values must agree.
    """
    import subprocess
    import sys

    prog = (
        "import hashlib,sys;"
        "body='outcome=ready ci=green';"
        "print(f'pr-comment:7:{hashlib.sha256(body.encode()).hexdigest()[:16]}')"
    )
    outs = []
    for seed in ("0", "12345"):
        r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                           text=True, env={"PYTHONHASHSEED": seed,
                                           "PATH": "/usr/bin:/bin"})
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout.strip())
    assert outs[0] == outs[1], (
        "the key formula must not depend on the interpreter's hash seed")

    # And prove the OLD formula would have failed this very test.
    old = ("body='outcome=ready ci=green';"
           "print(f'{abs(hash(body)) % (16 ** 16):016x}')")
    olds = []
    for seed in ("0", "12345"):
        r = subprocess.run([sys.executable, "-c", old], capture_output=True,
                           text=True, env={"PYTHONHASHSEED": seed,
                                           "PATH": "/usr/bin:/bin"})
        olds.append(r.stdout.strip())
    assert olds[0] != olds[1], (
        "if this ever passes, `hash()` stopped being seed-dependent and this "
        "test no longer demonstrates what it claims")


def test_a_failing_comment_effect_does_not_abort_the_derivation():
    """The comment is documentation, not a decision.

    The outcome is derived from the repository and is already correct when the
    comment is posted. An unhandled effect failure aborted `derive`, which the
    caller reads as an empty tuple and escalates -- so a denied or transient
    comment turned a READY item into an escalation. The bash warned and
    carried on (`|| echo WARN`).
    """
    logged = []

    def boom(cls, key, argv):
        raise RuntimeError("effect refused")

    d = _deps(effect=boom, log=logged.append)
    r = derive("i1", "i1", "7", "", deps=d)

    assert r.outcome == "ready", "a failed comment must not change the outcome"
    assert r.as_tuple()[3] == "a" * 40, "the merge-authorising head must survive"
    assert any("failed to post" in m for m in logged), \
        "the failure must be visible in the log, not swallowed"
