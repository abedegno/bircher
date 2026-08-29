"""What an item did, derived from the repository.

The 192 lines that decide every item's outcome. Ported from
`observe_outcome` in `batch/run-queue.sh` with its structure unchanged --
every difference is either covered by a test proving there is none, or written
down here as deliberate.

Everything it reaches the world through arrives in one `Deps`, so tests drive
the real function rather than a rearrangement of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from coordinator.observe import classify

#: A head we can pin a merge to. Anything else is "cannot pin", which the
#: caller reads as "cannot auto-merge" -- better than merging an unverifiable
#: commit.
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Deps:
    """Everything `derive` reaches the world through."""

    checks: callable                 # pr -> "name|bucket" text
    head_of: callable                # pr -> sha
    review: callable                 # (pr, sha) -> "PASS" | "FAIL" | None
    effect: callable                 # (cls, key, argv) -> str
    log: callable = lambda msg: None
    failure_kind: callable = lambda pr: "genuine"
    rerun: callable = lambda pr: "red"
    history: callable = lambda branch: ("unknown", None)
    branch_of: callable = lambda pr: ""
    pr_state: callable = lambda pr: ("OPEN", "")
    discover_by_code: callable = lambda code: []
    discover_by_issue: callable = lambda issue: []
    reconcile: callable = lambda code, pr: pr
    wait_ci: callable = lambda pr: "pending"
    required: str = ""
    reviewer: str = "codex"


@dataclass(frozen=True)
class Derived:
    outcome: str
    review: str
    note: str
    sha: str
    ci: str
    ci_first: str
    resubmissions: object

    def as_tuple(self):
        return (self.outcome, self.review, self.note, self.sha, self.ci,
                self.ci_first, self.resubmissions)

    def as_line(self) -> str:
        """The seven-field form the shell parses. A caller reading six absorbs
        the last into its neighbour, silently."""
        r = "" if self.resubmissions is None else self.resubmissions
        return f"{self.outcome}|{self.review}|{self.note}|{self.sha}|{self.ci}|{self.ci_first}|{r}"


def _settle_pr(item, code, pr, issue, d: Deps) -> str:
    """Which PR this item actually produced."""
    if pr:
        state, merged = d.pr_state(pr)
        from coordinator.pr_selection import is_abandoned
        if state and is_abandoned(state, merged):
            d.log(f"{item}: tracked PR #{pr} is CLOSED and unmerged -> discarding")
            pr = ""

    if not pr and code:
        found = d.discover_by_code(code)
        if found:
            pr = found[0]
            d.log(f"{item}: found open PR #{pr} by code -> adopting")

    if not pr and issue:
        # EXACTLY ONE auto-adopts. Two or more are left for a human: this path
        # has no live escalation channel, and choosing would be a guess about
        # which PR the item produced.
        found = d.discover_by_issue(issue)
        if len(found) == 1:
            pr = found[0]
            d.log(f"{item}: found #{pr} via issue #{issue} linkage -> adopting")
        elif len(found) > 1:
            d.log(f"{item}: multiple PRs link issue #{issue} ({found}) -- leaving for a human")

    if pr:
        chosen = d.reconcile(code, pr)
        if chosen and chosen != pr:
            d.log(f"{item}: adopted CI-green sibling PR #{chosen} (was #{pr})")
            pr = chosen
    return pr


def _settle_ci(item, pr, d: Deps, rerun_max: int) -> str:
    """Wait out a pending CI, and re-run an INFRASTRUCTURE red."""
    from coordinator.ci import keep_blocking, normalize

    ci = normalize(keep_blocking(d.checks(pr), d.required))
    if ci == "pending":
        d.log(f"{item}: PR #{pr} CI still running -> waiting for CI to settle")
        ci = d.wait_ci(pr)

    tries = 0
    while ci == "red" and tries < rerun_max and d.failure_kind(pr) == "infra":
        tries += 1
        d.log(f"{item}: PR #{pr} CI red but INFRA (no failed step) "
              f"-> re-running CI (attempt {tries}/{rerun_max})")
        ci = d.rerun(pr)
    return ci


def derive(item: str, code: str, pr: str, issue: str, *, deps: Deps,
           rerun_max: int = 4) -> Derived:
    """The whole derivation. Returns the seven fields."""
    d = deps
    pr = _settle_pr(item, code, pr, issue, d)

    ci_first, resubmissions = "unknown", None
    if pr:
        branch = d.branch_of(pr)
        if branch:
            ci_first, resubmissions = d.history(branch)

    ci, verdict, reviewed_sha = "na", None, ""
    if pr:
        ci = _settle_ci(item, pr, d, rerun_max)

        if ci == "green":
            # CAPTURED BEFORE THE REVIEW, never re-read after (#66). A push
            # landing between the verdict and a later read would be blessed as
            # reviewed, defeating the --match-head-commit guard it feeds.
            head = (d.head_of(pr) or "").strip()
            if _FULL_SHA.match(head):
                reviewed_sha = head
            else:
                d.log(f"{item}: could not capture a full 40-hex head for PR #{pr} "
                      f"(got {head or '<empty>'!r}) -> cannot pin a merge")
            d.log(f"{item}: PR #{pr} CI green -> {d.reviewer} review at {reviewed_sha[:7]}")
            verdict = d.review(pr, reviewed_sha)
            if verdict == "NONE":
                verdict = None

    o = classify(pr or None, ci, verdict, reviewer=d.reviewer)

    if pr:
        head_field = f" head={reviewed_sha}" if o.outcome == "ready" and reviewed_sha else ""
        body = (f"Outcome derived from the repository: outcome={o.outcome} "
                f"ci={o.ci}{head_field}\nnote: {o.note}")
        key = f"pr-comment:{pr}:{abs(hash(body)) % (16 ** 16):016x}"
        d.effect("comment", key,
                 ["gh", "pr", "comment", str(pr), "--body", body])

    # The sha rides out only on a READY outcome: it is the merge-authorising
    # evidence, and a failed or escalated derivation must never carry one.
    sha_out = reviewed_sha if o.outcome == "ready" else ""
    return Derived(o.outcome, o.review, o.note, sha_out, o.ci,
                   ci_first, resubmissions)
