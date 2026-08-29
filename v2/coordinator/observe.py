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


def classify(pr: str | None, ci: str, verdict: str | None, *, reviewer: str) -> Outcome:
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
        return Outcome("failed", f"{reviewer}:fail", "green",
                       "out-of-band review FAIL")
    # NONE, empty, or anything unrecognised. A reviewer that crashed, timed out
    # or rambled has approved NOTHING; reading silence as approval is how a
    # merge gets authorised by an absence.
    return Outcome("escalated", f"{reviewer}:na", "green",
                   "review produced no verdict; needs human")
