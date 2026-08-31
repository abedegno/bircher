"""What an item did, derived from the repository.

The 192 lines that decide every item's outcome. Ported from
`observe_outcome` in `batch/run-queue.sh` with its structure unchanged --
every difference is either covered by a test proving there is none, or written
down here as deliberate.

Everything it reaches the world through arrives in one `Deps`, so tests drive
the real function rather than a rearrangement of it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from coordinator.ci import DEFAULT_IGNORED as _DEFAULT_IGNORED
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
    review: callable                 # (pr, sha) -> (verdict, output)
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
    #: The operator's BIRCHER_CI_IGNORE_CHECKS, or the library default.
    #: ON Deps, not imported at the point of use: `_settle_ci` used to
    #: reconstruct the filtering with `keep_blocking`'s default, so the
    #: override reached `poll`, `failure_kind` and `rerun` but NOT the FIRST
    #: read -- and the first read is the one that decides whether the others
    #: are ever called. A custom-ignored check that was already failing made
    #: the derivation return red without ever consulting the policy that said
    #: to ignore it. Threading it here means there is one policy rather than
    #: one per call site.
    ignore: str = _DEFAULT_IGNORED
    #: How many revision rounds this run may still have, from the journal.
    #: 0 -- the default -- reproduces the behaviour before the repair loop, so
    #: BIRCHER_MAX_REVISIONS=0 is a real rollback rather than a code path that
    #: merely usually agrees.
    revisions_left: int = 0
    #: The target repository, `owner/name`. EVERY effect argv must name it.
    #: `gh` resolves an omitted `--repo` from the CURRENT WORKING DIRECTORY's
    #: git remote -- which for the coordinator is the bircher checkout, not the
    #: repository under management. The comment below went to abedegno/bircher
    #: issues #17 and #18 instead of abedegno/bircher-smoke, and the kernel
    #: journalled `effect_confirmed` for both, because the command SUCCEEDED --
    #: against the wrong target. Second instance of this shape; `publish_cmd`
    #: was the first.
    repo: str = ""


@dataclass(frozen=True)
class Derived:
    outcome: str
    review: str
    note: str
    sha: str
    ci: str
    ci_first: str
    resubmissions: object
    #: THE PR THIS DERIVATION ACTUALLY SETTLED ON, which is not always the one
    #: the caller passed in. `_settle_pr` discards an abandoned PR, discovers
    #: one by code or issue linkage, and adopts a CI-green sibling. All three
    #: were used internally -- the review, the status and the comment all went
    #: to the right PR -- and none of it reached the caller, which went on to
    #: authorize and merge the number it started with. muesli #723: the
    #: derivation reviewed #738 and the caller tried to merge closed #737.
    pr: str = ""
    #: The reviewer's output, carried out ONLY on a `revise` outcome so the
    #: runner can put the blocking findings in front of the next implementer.
    #: That routing is what merged #740 and #750 when done by hand.
    #:
    #: LAST in the field order deliberately: `pr` is passed positionally by
    #: `derive`, and inserting anything before it silently rebinds arguments.
    findings: str = ""

    def as_tuple(self):
        return (self.outcome, self.review, self.note, self.sha, self.ci,
                self.ci_first, self.resubmissions, self.pr)

    #: Field count, so a consumer can assert it rather than assume it. The
    #: absorption hazard below is silent, and silence is what made the missing
    #: `pr` field survive a live run that looked like it worked.
    FIELDS = 8

    def as_line(self) -> str:
        """The EIGHT-field form the shell parses. A caller reading seven
        absorbs the last into its neighbour, silently -- `read -r a b c` puts
        every remaining field into `c`, so a short reader does not error, it
        corrupts one value."""
        r = "" if self.resubmissions is None else self.resubmissions
        return (f"{self.outcome}|{self.review}|{self.note}|{self.sha}|"
                f"{self.ci}|{self.ci_first}|{r}|{self.pr}")


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

    ci = normalize(keep_blocking(d.checks(pr), d.required, d.ignore))
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
    """The whole derivation. Returns the eight fields."""
    d = deps
    pr = _settle_pr(item, code, pr, issue, d)

    ci_first, resubmissions = "unknown", None
    if pr:
        branch = d.branch_of(pr)
        if branch:
            ci_first, resubmissions = d.history(branch)

    ci, verdict, reviewed_sha, reviewer_out = "na", None, "", ""
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
            verdict, reviewer_out = d.review(pr, reviewed_sha)
            if verdict == "NONE":
                verdict = None

    o = classify(pr or None, ci, verdict, reviewer=d.reviewer,
                 revisions_left=d.revisions_left)

    if pr:
        head_field = f" head={reviewed_sha}" if o.outcome == "ready" and reviewed_sha else ""
        # THE REVIEWER'S FINDINGS STAY. Decision 3 of C8 Phase 2 kept them
        # deliberately: they are the most useful thing on the PR for a human,
        # and only the machine-readable `bircher-status:` prefix was retired.
        # The first port dropped them and always wrote the short form;
        # `--self-test` caught it.
        summary = (f"outcome={o.outcome} ci={o.ci}{head_field}\nnote: {o.note}")
        if reviewer_out:
            body = ("Cross-vendor review (outcome derived from the repository, "
                    f"not reported):\n\n{reviewer_out}\n\n{summary}")
        else:
            body = f"Outcome derived from the repository: {summary}"
        # SHA-256, NOT `hash()`. Python randomises string hashing per process
        # (PYTHONHASHSEED), so `abs(hash(body))` gave a DIFFERENT key on every
        # invocation: the idempotency key could never match a previous attempt
        # and a retry would post a duplicate comment rather than dedupe. The
        # bash this replaced already did the right thing --
        # `shasum -a 256 | cut -c1-16` -- and the port regressed it.
        key = f"pr-comment:{pr}:{hashlib.sha256(body.encode()).hexdigest()[:16]}"
        # BEST EFFORT, as the bash was (`|| echo WARN`). This comment is
        # documentation for a human, not part of the decision: the outcome is
        # derived from the repository and is already correct by the time we get
        # here. Letting a denied or transient comment failure propagate would
        # abort the derivation and turn a READY item into an escalation --
        # a non-essential courtesy blocking a merge.
        try:
            d.effect("comment", key,
                     ["gh", "pr", "comment", str(pr), "--repo", d.repo,
                      "--body", body])
        except Exception as exc:                       # noqa: BLE001
            d.log(f"{item}: failed to post the derived comment to PR #{pr} "
                  f"({type(exc).__name__}: {exc}) -> continuing; the outcome "
                  f"is derived from the repository and is unaffected")

    # The sha rides out only on a READY outcome: it is the merge-authorising
    # evidence, and a failed or escalated derivation must never carry one.
    sha_out = reviewed_sha if o.outcome == "ready" else ""
    # The findings ride out only on `revise`: on any other outcome the runner
    # has nothing to route them to, and a scorecard note is not the place for a
    # multi-paragraph review.
    return Derived(o.outcome, o.review, o.note, sha_out, o.ci,
                   ci_first, resubmissions, str(pr or ""),
                   findings=(reviewer_out if o.outcome == "revise" else ""))
