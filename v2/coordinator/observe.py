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

from kernel.projection import is_front_verdict


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
    """`ready`, `repair`, `waiting`, `escalated` and `timeout` are reachable
    from `classify` (closed loop's words, spec §2); `noop` and `skipped` come
    from signal files, not from here. `repair` and `waiting` reach the
    scorecard only when a pass ends on them -- the runner acts on
    `next_step`, not on this outcome."""

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

    A spec- or plan-phase verdict is skipped (`is_front_verdict`): the
    allowance belongs to the implementation, and a spec-phase FAIL must not
    spend it.

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
        if is_front_verdict(payload):
            continue
        if payload.get("verdict") == "request_revision":
            n += 1
    return n


def classify(pr: str | None, ci: str, verdict: str | None, *, reviewer: str) -> Outcome:
    """Ground truth to outcome. PURE -- no I/O, no globals.

    `repair` and `waiting` are the closed loop's words (spec §2): the runner
    acts on `next_step`, and these reach the scorecard only when a pass ends
    on them. The reviewed sha is deliberately NOT an input: it is evidence
    attached to the result, not a classification input.
    """
    if not pr:
        return Outcome("timeout", "na", "na",
                       "no PR at timeout (reaped before implement delivered)")
    if ci == "red":
        return Outcome("repair", "na", "red", "PR up, CI red; a repair round is owed")
    if ci == "pending":
        return Outcome("waiting", "na", "pending", "CI still pending; the next wave resumes")
    if verdict == "PASS":
        return Outcome("ready", f"{reviewer}:pass", "green", "out-of-band review PASS")
    if verdict == "FAIL":
        return Outcome("repair", f"{reviewer}:fail", "green", "out-of-band review FAIL; a repair round is owed")
    # NONE, empty, or anything unrecognised. A reviewer that crashed, timed out
    # or rambled has approved NOTHING; reading silence as approval is how a
    # merge gets authorised by an absence.
    return Outcome("escalated", f"{reviewer}:na", "green",
                   "review produced no verdict; needs human")
