"""The derivation, driven for real.

Every dependency arrives in one injected object, so these exercise `derive`
rather than a rearrangement of it.
"""
import hashlib

import pytest

from coordinator.outcome import Deps, Derived, derive


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


def test_the_tuple_has_thirteen_fields_in_order():
    """Thirteen since the fingerprints, the PR's state and its failing jobs
    joined it. The width is asserted against `Derived.FIELDS` as well as the
    literal, because the shell's reader is checked against that constant and
    two numbers that can disagree will."""
    r = derive("i1", "i1", "7", "", deps=_deps())
    assert len(r.as_tuple()) == 13 == Derived.FIELDS
    assert r.outcome == "ready"
    assert r.as_tuple()[5:] == ("true", 0, "7", "", "", "", "open", "")
    assert len(r.as_line().split("|")) == Derived.FIELDS


def test_a_red_pr_never_reaches_the_reviewer():
    """CI is checked BEFORE the verdict. A PASS on a red PR must not merge, and
    dispatching a reviewer at all wastes a run on a foregone answer."""
    asked = []
    d = _deps(checks=lambda pr: "build|fail",
              review=lambda pr, sha: (asked.append(pr) or "PASS", ""))
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.outcome == "repair" and asked == []


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
    # The status posts BEFORE the comment: it rides the same pinned head the
    # verdict was captured against, and the comment documents the outcome
    # that head's verdict fed into.
    assert [c for c, _k, _a in d.posted] == ["status_check", "comment"]


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
    # The comment is the LAST post: the status precedes it.
    assert d.posted and "9" in d.posted[-1][2]


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
    assert "6" in d.posted[-1][2]


def test_the_head_rides_out_on_every_outcome_with_a_pr():
    """The closed loop (spec §1) repairs a red head and records CI against it,
    so the head is evidence on every outcome, not only the merge-authorising
    one. The merge gate is `outcome == ready`, not the presence of a sha."""
    head = "a" * 40
    repaired = derive("i1", "i1", "7", "", deps=_deps(review=lambda pr, sha: ("FAIL", "")))
    assert repaired.outcome == "repair" and repaired.sha == head
    esc = derive("i1", "i1", "7", "", deps=_deps(review=lambda pr, sha: (None, "")))
    assert esc.outcome == "escalated" and esc.sha == head
    red = derive("i1", "i1", "7", "", deps=_deps(checks=lambda pr: "build|fail"))
    assert red.ci == "red" and red.sha == head
    none = derive("i1", "i1", "", "", deps=_deps(pr_state=lambda pr: ("", "")))
    assert none.pr == "" and none.sha == ""


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
    # The empty resubmissions field is now followed by the settled PR, so the
    # assertion names the pair rather than the line's end -- otherwise adding
    # any field silently stops this from checking what it claims to.
    assert "|unknown||" in r.as_line(), r.as_line()


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

    # The comment is the LAST post: the status precedes it.
    key1 = d1.posted[-1][1]
    key2 = d2.posted[-1][1]
    # NOTE: this equality DOES NOT BIND the defect. `hash()` is stable WITHIN
    # a process, so both derives agree even with the bug present -- confirmed
    # by mutation: restoring `abs(hash(body))` left this assertion passing and
    # only the digest assertion below went red. It is kept as documentation of
    # the property, not as the guard.
    assert key1 == key2

    # THIS is the binding assertion: the key must equal a specific,
    # process-independent digest, which `hash()` cannot produce.
    body = d1.posted[-1][2][-1]
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


def test_a_custom_ignored_check_does_not_decide_the_first_read():
    """The gap the second cross-review round found.

    `_settle_ci` classified the FIRST read with `keep_blocking`'s default
    ignore list rather than the configured one. That read decides everything:
    a custom-ignored check that was already FAILING made the derivation return
    red immediately, so `wait_ci`, `failure_kind` and `rerun` -- all of which
    had been correctly threaded -- were never reached. The override was bound
    at three consumers and missed at the only one that gates them.

    Drives the whole of `derive`, deliberately. The earlier tests bound
    `poll`, `failure_kind` and `live_deps.wait_ci` individually and every one
    of them passed with this defect present.
    """
    checks = "build|pass\nflaky-lint|fail"

    # Default policy: the failing check is required and blocking -> red.
    plain = derive("i1", "i1", "7", "",
                   deps=_deps(checks=lambda pr: checks,
                              required="build\nflaky-lint"))
    assert plain.ci == "red"
    assert plain.outcome != "ready"

    # Operator policy ignores it -> the same repository state is green.
    ignored = derive("i1", "i1", "7", "",
                     deps=_deps(checks=lambda pr: checks,
                                required="build\nflaky-lint",
                                ignore="Dependabot|review-gate|flaky-lint"))
    assert ignored.ci == "green", (
        "BIRCHER_CI_IGNORE_CHECKS did not reach the first classification, "
        "which is the one that decides whether the others run")
    assert ignored.outcome == "ready"


def test_the_comment_is_addressed_to_the_repository_under_management():
    """Not just that `--repo` is present -- that it carries the RIGHT value.

    The flag's presence is a structural fact; the defect was about WHERE the
    effect landed. `gh` resolves an omitted `--repo` from the coordinator's own
    working directory, so s13's and s14's review comments went to
    abedegno/bircher #17 and #18 instead of abedegno/bircher-smoke -- and the
    kernel journalled effect_confirmed for both, because the command succeeded.
    """
    d = _deps(repo="owner/target")
    derive("i1", "i1", "7", "", deps=d)

    # The comment is the LAST post: the status precedes it, and names its
    # repository in the URL rather than a `--repo` flag.
    argv = d.posted[-1][2]
    assert "--repo" in argv, "the comment effect must name its repository"
    assert argv[argv.index("--repo") + 1] == "owner/target", (
        f"the comment was addressed to the wrong repository: {argv}")


def test_derive_returns_the_pr_it_settled_on_not_the_one_passed_in():
    """End to end through `derive`, which is where the loss happened.

    The unit tests for `_settle_pr` already proved it picks the right PR. None
    of them proved the answer LEFT the function -- and it did not.
    """
    d = _deps(pr_state=lambda pr: ("CLOSED", None),
              discover_by_code=lambda code: ["738"],
              reconcile=lambda code, pr: pr)
    r = derive("i723", "i723", "737", "", deps=d)
    assert r.pr == "738", (
        "derive discarded the abandoned #737 and discovered #738, but returned "
        f"{r.pr!r} to the caller that has to merge it")
    # `pr` is field 8 of 10 -- the range fields ride out after it.
    assert r.as_line().split("|")[7] == "738"


def test_derive_returns_a_reconciled_sibling():
    """The muesli #723 shape exactly: a CI-green sibling is adopted."""
    d = _deps(reconcile=lambda code, pr: "738")
    r = derive("i723", "i723", "737", "", deps=d)
    assert r.pr == "738"


def test_derive_returns_the_original_pr_when_nothing_changed():
    """The common case must round-trip, or the caller adopts a wrong value."""
    r = derive("i1", "i1", "7", "", deps=_deps())
    assert r.pr == "7"


def _green_pass_deps(effects, verdict="PASS", out="VERDICT: PASS"):
    return Deps(
        checks=lambda pr: "ci|pass",
        head_of=lambda pr: "e4ea3eb7" + "1" * 32,
        review=lambda pr, sha: (verdict, out),
        effect=lambda cls, key, argv: effects.append((cls, key, argv)) or "ok",
        base_of=lambda pr: "develop",
        merge_base=lambda base, head: "2742ae4f" + "0" * 32,
        delta_digest=lambda base, head: "d" * 64,
        round_number=3,
        reviewer="codex",
        repo="o/r",
    )


def test_the_line_has_thirteen_fields_and_carries_the_range():
    effects = []
    r = derive("i", "c", "7", "", deps=_green_pass_deps(effects))
    assert r.FIELDS == 13
    fields = r.as_line().split("|")
    assert len(fields) == 13
    assert fields[8] == "2742ae4f" + "0" * 32
    assert fields[9] == "d" * 64


def test_the_line_has_thirteen_fields_with_fingerprints_state_and_jobs():
    effects = []
    out = "Blocking findings\n\n- a.go:1 One.\n- b.go:2 Two.\n\nVERDICT: FAIL\n"
    r = derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="FAIL", out=out))
    fields = r.as_line().split("|")
    assert len(fields) == 13 == Derived.FIELDS
    assert fields[10].count(",") == 1 and len(fields[10]) == 81
    assert fields[11] == "open" and fields[12] == ""


def test_a_pass_carries_no_fingerprints():
    assert derive("i", "c", "7", "", deps=_green_pass_deps([])).fingerprints == ""


def test_pr_state_is_lower_cased_and_merged_wins():
    d = _green_pass_deps([])
    d.pr_state = lambda pr: ("MERGED", "2026-09-19T00:00:00Z")
    assert derive("i", "c", "7", "", deps=d).pr_state == "merged"


def test_a_discarded_closed_pr_still_reports_its_state():
    # The settle drops a CLOSED-unmerged PR and finds nothing else; the runner
    # still has to hear "closed", or the run can never end (spec §1).
    r = derive("i1", "i1", "7", "", deps=_deps(pr_state=lambda pr: ("CLOSED", "null")))
    assert r.pr == "" and r.outcome == "timeout" and r.pr_state == "closed"


def test_red_ci_names_its_failing_jobs_sorted_and_ignoring_the_ignored():
    d = _deps(checks=lambda pr: "server (go)|fail\nclient (node)|pass\nlint|cancel")
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.ci == "red" and r.failing_jobs == "lint,server (go)"


def test_green_ci_names_no_failing_jobs():
    assert derive("i1", "i1", "7", "", deps=_deps()).failing_jobs == ""


def test_a_comma_in_a_job_name_does_not_split_into_two_entries():
    """GitHub matrix names carry a comma of their own
    (`build (ubuntu-latest, node 18)`), and the tuple's transport joins
    failing_jobs with a comma too -- so a raw comma in the name would read
    back as two jobs. Fixed at the source: the comma becomes a space."""
    d = _deps(checks=lambda pr: "build (ubuntu-latest, node 18)|fail\nlint|pass")
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.failing_jobs == "build (ubuntu-latest node 18)"


def _status_key(head, state, desc):
    """The key the derivation must compute: the head, then the CONTENT of the
    post. Written out here rather than imported so the test states the format
    independently of the code that produces it."""
    return f"status:{head}:" + hashlib.sha256(f"{state}\n{desc}".encode()).hexdigest()[:16]


def test_a_pass_posts_a_success_status_naming_the_range():
    effects = []
    derive("i", "c", "7", "", deps=_green_pass_deps(effects))
    posts = [e for e in effects if e[0] == "status_check"]
    assert len(posts) == 1
    cls, key, argv = posts[0]
    assert key == _status_key("e4ea3eb7" + "1" * 32, "success",
                              "codex round 3 PASS on 2742ae4..e4ea3eb")
    assert "repos/o/r/statuses/e4ea3eb7" + "1" * 32 in argv
    assert "state=success" in argv
    assert "context=bircher/cross-review" in argv
    assert "description=codex round 3 PASS on 2742ae4..e4ea3eb" in argv


def test_a_fail_posts_failure_with_its_blocking_count():
    effects = []
    out = "Blocking findings\n\n- one\n- two\n\nNon-blocking findings\n\n- None.\n\nVERDICT: FAIL\n"
    derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="FAIL", out=out))
    argv = [e for e in effects if e[0] == "status_check"][0][2]
    assert "state=failure" in argv
    assert "description=codex round 3 FAIL (2 blocking) on 2742ae4..e4ea3eb" in argv


def test_no_verdict_posts_error():
    effects = []
    derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="NONE", out="rambling"))
    argv = [e for e in effects if e[0] == "status_check"][0][2]
    assert "state=error" in argv
    assert "no verdict on 2742ae4..e4ea3eb" in " ".join(argv)


def test_two_verdicts_on_one_head_do_not_share_an_effect_key():
    """THE REPLAY. The round only advances on a recorded `review_verdict`
    fact, and an `error` (no verdict) or a terminal FAIL records none -- so
    `status:<head>:<round>` was the SAME key for a FAIL and for the PASS that
    superseded it, and `kernel/effects.py` returns the prior external id on a
    replayed key WITHOUT executing. The later verdict was never posted and the
    gate kept showing the earlier one.
    """
    first, second = [], []
    out = "Blocking findings\n\n- one\n\nVERDICT: FAIL\n"
    derive("i", "c", "7", "", deps=_green_pass_deps(first, verdict="FAIL", out=out))
    derive("i", "c", "7", "", deps=_green_pass_deps(second))          # a PASS, same head
    fail_key = [e for e in first if e[0] == "status_check"][0][1]
    pass_key = [e for e in second if e[0] == "status_check"][0][1]
    assert fail_key != pass_key, (
        "a FAIL and a later PASS on the same head share an effect key, so the "
        "PASS replays the FAIL's journalled effect and never posts")
    # ...and both still name the head they are about.
    head = "e4ea3eb7" + "1" * 32
    assert fail_key.startswith(f"status:{head}:") and pass_key.startswith(f"status:{head}:")


def test_an_unparseable_merge_base_is_recorded_as_unknown():
    """`gh` prints `null` for a merge-base it cannot compute, and that text was
    taken raw into the tuple, the description and (through the runner) into
    hand-built JSON. It gets the head's own 40-hex validation."""
    effects = []
    d = _green_pass_deps(effects)
    d.merge_base = lambda base, head: "null\n"
    r = derive("i", "c", "7", "", deps=d)
    assert r.merge_base == ""
    assert r.as_line().split("|")[8] == ""
    argv = [e for e in effects if e[0] == "status_check"][0][2]
    assert "description=codex round 3 PASS on ?..e4ea3eb" in argv


def test_a_failed_status_post_does_not_change_the_outcome():
    def effect(cls, key, argv):
        if cls == "status_check":
            raise RuntimeError("github is down")
        return "ok"
    d = _green_pass_deps([])
    d.effect = effect
    r = derive("i", "c", "7", "", deps=d)
    assert r.outcome == "ready"


def test_no_status_is_posted_without_a_pinned_head():
    effects = []
    d = _green_pass_deps(effects)
    d.head_of = lambda pr: "not-a-sha"
    derive("i", "c", "7", "", deps=d)
    assert not [e for e in effects if e[0] == "status_check"]


def test_red_ci_posts_nothing_and_carries_no_range():
    effects = []
    d = _green_pass_deps(effects)
    d.checks = lambda pr: "ci|fail"
    r = derive("i", "c", "7", "", deps=d)
    assert not [e for e in effects if e[0] == "status_check"]
    assert r.merge_base == "" and r.delta_digest == ""


def test_the_round_is_revisions_used_plus_one(tmp_path):
    from coordinator.cli import _round_number
    from kernel.store import Store
    db = tmp_path / "k.db"
    Store.open(str(db))  # an empty journal
    assert _round_number(str(db), "run-1") == 1


def test_no_journal_means_round_one():
    from coordinator.cli import _round_number
    assert _round_number("", "") == 1
    assert _round_number("/nonexistent/path.db", "run-1") == 1
