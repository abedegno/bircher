"""The pre-merge gate must wait for the state that actually blocks the merge.

muesli PR #735, 2026-08-30: the gate polled `.mergeable`, which reports
CONFLICT state only. Branch protection lives in `mergeStateStatus`. bircher
posts `bircher/cross-review` at 13:29:11; a workflow reacts and posts the
required `review-gate` at 13:29:18. In that seven-second window the PR was
`mergeable=MERGEABLE` (true) and `mergeStateStatus=BLOCKED`. The gate saw
MERGEABLE, merged, GitHub refused, the effect became uncertain, the run HALTED
-- and the halt then blocked the 30s retry loop meant to absorb exactly this.

These drive the REAL shell functions, extracted by name from run-queue.sh.
The classifiers are pure so they can be driven exhaustively without a network.
"""
import itertools
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _sh(fns: list[str], call: str) -> str:
    src = RUN_QUEUE.read_text()
    body = []
    for name in fns:
        m = re.search(rf"^{name}\(\) \{{.*?^\}}", src, re.S | re.M)
        assert m, f"{name} not found in run-queue.sh"
        body.append(m.group(0))
    r = subprocess.run(["bash", "-c", "\n".join(body) + f"\n{call}\n"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def classify(state, mergeable, mss, head="abc", expected="abc") -> str:
    return _sh(["_classify_merge_state"],
               f'_classify_merge_state "{state}" "{mergeable}" "{mss}" "{head}" "{expected}"')


def _q(s: str) -> str:
    """A bash single-quoted literal that PRESERVES newlines.

    `repr()` does not: it renders a newline as a literal backslash-n, which
    bash then passes as two characters. That silently collapsed a two-context
    required set into one and made a test pass for the wrong reason.
    """
    return "'" + s.replace("'", "'\\''") + "'"


def blocked(required, rows) -> str:
    return _sh(["_classify_blocked"],
               f"_classify_blocked {_q(required)} {_q(rows)}")


# --- the incident itself ------------------------------------------------------

def test_the_case_that_halted_muesli_735_now_waits():
    """`mergeable=MERGEABLE` with `mergeStateStatus=BLOCKED` must NOT proceed.

    This exact pair is what the old gate saw and merged on.
    """
    assert classify("OPEN", "MERGEABLE", "BLOCKED") == "blocked"
    assert classify("OPEN", "MERGEABLE", "BLOCKED") != "proceed"


def test_a_required_check_that_has_not_registered_is_not_a_durable_block():
    """The registration race. During the reaction window the required context
    is not pending -- it is ABSENT -- so a naive 'anything pending? else defer'
    rule would escalate to a human on the transient condition."""
    assert blocked("review-gate", "other-check|success") == "absent"


# --- the ordered BLOCKED classifier, every row --------------------------------

@pytest.mark.parametrize("required,rows,expected", [
    ("?",           "",                      "defer"),   # 1 unreadable -> fail closed
    ("",            "",                      "defer"),   # 2 no required checks
    ("review-gate", "review-gate|pending",   "wait"),    # 3 running
    ("review-gate", "other|success",         "absent"),  # 4 not registered yet
    ("review-gate", "review-gate|failure",   "defer"),   # 5 reported, failed
    ("review-gate", "review-gate|success",   "defer"),   # 6 all green, still BLOCKED
])
def test_every_row_of_the_blocked_classifier(required, rows, expected):
    assert blocked(required, rows) == expected


def test_pending_wins_over_absent_when_both_are_true():
    """Order matters: something is demonstrably running, so wait on that rather
    than starting a registration grace for a sibling."""
    assert blocked("a\nb", "a|pending") == "wait"


def test_an_unreadable_snapshot_is_not_evidence_of_absence():
    """Row 1 vs row 4. Spending the registration grace on an API outage would
    defer for the wrong reason, minutes later."""
    assert blocked("?", "") == "defer"
    assert blocked("?", "") != "absent"


# --- the precedence list ------------------------------------------------------

def test_a_moved_head_is_never_merged():
    """The reviewed head is the authorisation. A PR whose head moved must not
    merge whatever else is green."""
    assert classify("OPEN", "MERGEABLE", "CLEAN", head="moved", expected="abc") == "defer"


def test_conflicting_defers_whatever_the_merge_state_says():
    assert classify("OPEN", "CONFLICTING", "CLEAN") == "defer"


def test_a_non_open_pr_is_not_a_merge_candidate():
    for state in ("MERGED", "CLOSED"):
        assert classify(state, "MERGEABLE", "CLEAN") == "defer"


def test_an_unknown_future_enum_fails_closed():
    """GitHub adding a state must never read as 'go'."""
    assert classify("OPEN", "MERGEABLE", "SOMETHING_NEW") == "defer"


def test_lazily_computed_fields_are_waited_out_not_deferred():
    """Mergeability is computed on demand: a first read returns UNKNOWN and only
    TRIGGERS the computation. Reproduced on four open PRs, 2026-08-30."""
    assert classify("OPEN", "UNKNOWN", "UNKNOWN") == "wait"
    assert classify("OPEN", "", "") == "wait"
    assert classify("OPEN", "MERGEABLE", "UNKNOWN") == "wait"


def test_behind_defers_and_does_not_try_to_update():
    """BEHIND used to attempt an update-branch. That was incoherent: the update
    MOVES the head, which the moved-head rule then refuses forever -- and it is
    a mutation, in a change that only decides WHEN to merge."""
    assert classify("OPEN", "MERGEABLE", "BEHIND") == "defer"


def test_unstable_proceeds():
    """UNSTABLE is a NON-required check failing. Branch protection ignores it,
    so waiting cannot change anything and deferring would strand the PR."""
    assert classify("OPEN", "MERGEABLE", "UNSTABLE") == "proceed"


def test_clean_is_the_only_state_that_proceeds_on_a_required_check():
    """The whole cross-product: nothing but CLEAN and UNSTABLE may proceed."""
    states = ["CLEAN", "BLOCKED", "BEHIND", "DIRTY", "UNSTABLE", "UNKNOWN", "WEIRD", ""]
    mergeables = ["MERGEABLE", "CONFLICTING", "UNKNOWN", ""]
    for mss, mergeable in itertools.product(states, mergeables):
        got = classify("OPEN", mergeable, mss)
        if got == "proceed":
            assert mergeable == "MERGEABLE" and mss in ("CLEAN", "UNSTABLE"), (
                f"proceeded on mergeable={mergeable!r} mergeStateStatus={mss!r}")


def test_the_gate_never_mutates():
    """It decides WHEN to merge and nothing else. A gate that could merge, post
    or update would need the whole effect-routing argument made again."""
    src = RUN_QUEUE.read_text()
    m = re.search(r"^_await_mergeable_state\(\) \{.*?^\}", src, re.S | re.M)
    assert m, "_await_mergeable_state not found"
    body = m.group(0)
    for forbidden in ("gh pr merge", "gh pr comment", "update-branch",
                      "git push", "_effect "):
        assert forbidden not in body, (
            f"the pre-merge gate must not mutate, but calls {forbidden!r}")
