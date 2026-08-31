"""What the coordinator can see for itself.

The first mechanism moved out of `batch/run-queue.sh` and into Python, where
the design says mechanism belongs: "Git, GitHub and CI adapters" and the
outcome vocabulary are mechanism, and mechanism wants determinism and tests.

These were written in bash first, tested by extracting shell functions from a
7,000-line script and driving them with stubs. That harness was the tell: a
component that needs a text-extraction rig to be testable is in the wrong
language.

`gh` is invoked rather than reimplemented — it already holds the auth and the
pagination conventions, and "compose, don't fork" applies to it as much as to
anything else. The call is injectable so tests need neither network nor
subprocess.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


class GhError(Exception):
    """`gh` failed. Distinct from "gh succeeded and returned nothing"."""


def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise GhError(r.stderr.strip()[:200])
    return r.stdout


@dataclass(frozen=True)
class CiHistory:
    """What the repository shows about how a branch got where it is.

    `ci_first` is a THREE-valued answer and `unknown` is a real one. No CI
    history is the absence of evidence, and reporting `false` there would put a
    claim in the scorecard that nothing observed -- the shape this project
    keeps finding.
    """

    ci_first: str = "unknown"          # "true" | "false" | "unknown"
    resubmissions: int | None = None   # distinct commits CI ran on, minus one


def ci_history(repo: str, branch: str, *, gh=_gh) -> CiHistory:
    """One API call, not one per commit.

    `actions/runs?branch=` carries every run on the branch with its head sha,
    conclusion and creation time. Verified against the live API 2026-08-28:
    muesli `main` gave 100 finished runs over 9 distinct shas, earliest
    `success` -- i.e. `true|8`.
    """
    try:
        raw = gh(["api", f"repos/{repo}/actions/runs?branch={branch}&per_page=100"])
    except GhError:
        return CiHistory()
    try:
        runs = json.loads(raw).get("workflow_runs") or []
    except (ValueError, AttributeError):
        return CiHistory()

    # A null conclusion means "still running", which is not a verdict. Counting
    # it as one would report an in-flight branch as having failed.
    finished = [r for r in runs if r.get("head_sha") and r.get("conclusion")]
    if not finished:
        return CiHistory()

    earliest = min(finished, key=lambda r: r.get("created_at") or "")
    distinct = {r["head_sha"] for r in finished}
    return CiHistory(
        ci_first="true" if earliest.get("conclusion") == "success" else "false",
        # Re-running CI on the SAME commit is not a resubmission. Counting runs
        # would inflate every flaky branch into a fix loop that never happened.
        resubmissions=len(distinct) - 1,
    )


@dataclass(frozen=True)
class Outcome:
    """The vocabulary is fixed and unchanged from v1: ready, escalated, noop,
    failed, timeout, skipped. Only `ready`, `failed`, `escalated` and `timeout`
    are reachable from here; the other two come from signal files."""

    outcome: str
    review: str
    ci: str
    note: str


def revisions_used(facts) -> int:
    """How many revisions this run has already had, from the JOURNAL.

    Counts `REVIEW_VERDICT` facts whose verdict is `request_revision`. NOT
    `transition_performed`, which records `{"to": ..., "via": "record_review"}`
    and no verdict at all -- every accepted review looks identical there, so
    counting those cannot tell a revision from an acceptance.

    `REVIEW_VERDICT` is written by the kernel AFTER validation and carries the
    verdict explicitly; it is the same fact `authz.py` reads when deciding
    whether a binding was approved.

    From the journal and not a variable, so a coordinator that dies and is
    re-driven gets no fresh allowance.

    NOTE WHAT THIS DOES NOT PROVE. `commands.py` validates a review, THEN bumps
    the version under CAS, THEN appends this fact -- so a review can validate
    and lose the CAS, leaving no fact. This counts what was ACCEPTED, which is
    the right basis for an allowance, but the caller must separately confirm
    its own revision was recorded before acting on it.
    """
    n = 0
    for f in facts or ():
        kind = getattr(f, "kind", None)
        kind = getattr(kind, "value", kind)
        if kind != "review_verdict":
            continue
        payload = getattr(f, "payload", None) or {}
        if payload.get("verdict") == "request_revision":
            n += 1
    return n


def classify(pr: str | None, ci: str, verdict: str | None, *, reviewer: str,
             revisions_left: int = 0) -> Outcome:
    """Ground truth to outcome. PURE -- no I/O, no globals.

    The reviewed sha is deliberately NOT an input: it is evidence attached to
    the result, not a classification input, and threading it through here once
    made this function impossible to self-test.
    """
    if not pr:
        return Outcome("timeout", "na", "na",
                       "no PR at timeout (reaped before implement delivered)")
    if ci == "red":
        return Outcome("failed", "na", "red",
                       "PR up, CI red, coordinator died before fix")
    if ci == "pending":
        return Outcome("escalated", "na", "pending", "CI still pending at timeout")

    if verdict == "PASS":
        return Outcome("ready", f"{reviewer}:pass", "green",
                       "out-of-band review PASS")
    if verdict == "FAIL":
        # A FAIL WITH ROUNDS LEFT IS A REVISION, NOT AN ENDING. Eight of
        # eighteen muesli item-runs stopped here, every one on a specific
        # actionable finding; routing them back by hand merged two of three.
        #
        # `revise` is the coordinator's vocabulary only -- the runner acts on
        # it and never records it, so the scorecard still ends `ready` or
        # `failed`. With `revisions_left <= 0` this is byte-identical to the
        # behaviour before the loop existed, which is what makes
        # BIRCHER_MAX_REVISIONS=0 a real rollback.
        if revisions_left > 0:
            return Outcome("revise", f"{reviewer}:fail", "green",
                           "out-of-band review FAIL; revising")
        return Outcome("failed", f"{reviewer}:fail", "green",
                       "out-of-band review FAIL")
    # NONE, empty, or anything unrecognised. A reviewer that crashed, timed out
    # or rambled has approved NOTHING; reading silence as approval is how a
    # merge gets authorised by an absence.
    return Outcome("escalated", f"{reviewer}:na", "green",
                   "review produced no verdict; needs human")
