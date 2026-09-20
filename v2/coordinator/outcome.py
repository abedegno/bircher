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

from coordinator import attest
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
    #: The target repository, `owner/name`. EVERY effect argv must name it.
    #: `gh` resolves an omitted `--repo` from the CURRENT WORKING DIRECTORY's
    #: git remote -- which for the coordinator is the bircher checkout, not the
    #: repository under management. The comment below went to abedegno/bircher
    #: issues #17 and #18 instead of abedegno/bircher-smoke, and the kernel
    #: journalled `effect_confirmed` for both, because the command SUCCEEDED --
    #: against the wrong target. Second instance of this shape; `publish_cmd`
    #: was the first.
    repo: str = ""
    #: The PR's base branch, for the reviewer's range and the merge-base.
    base_of: callable = lambda pr: "main"
    #: (base, head) -> merge-base sha, or "" when unknown.
    merge_base: callable = lambda base, head: ""
    #: (base, head) -> the three-dot delta digest, or "" when unprovable.
    delta_digest: callable = lambda base, head: ""
    #: Which review round this is, from the journal (revisions used + 1).
    round_number: int = 1


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
    #: The reviewer's output, carried out on every FAIL (closed-loop spec §1)
    #: so the repair round can put the blocking findings in front of the next
    #: implementer. That routing is what merged #740 and #750 when done by
    #: hand.
    #:
    #: LAST in the field order deliberately: `pr` is passed positionally by
    #: `derive`, and inserting anything before it silently rebinds arguments.
    findings: str = ""
    #: What the review covered: the merge-base with the PR's base, and the
    #: digest of the PR's own delta at the reviewed head. Both ride out on
    #: every derivation that pinned a head, so the runner can record them on
    #: the verdict fact (spec §4).
    merge_base: str = ""
    delta_digest: str = ""
    #: The blocking findings' fingerprints (closed-loop spec §3), comma-joined
    #: hex, on a FAIL; empty otherwise. The runner records them on the verdict
    #: fact so the no-progress judgement can compare rounds.
    fingerprints: str = ""
    #: The PR's state as this derivation saw it -- open, closed or merged, or
    #: "" when there was no PR to ask about. For the PR the derivation settled
    #: on, or for the caller's own PR when the settle discarded it: a PR a
    #: person closed still has to reach the journal, or the run cannot end
    #: (spec §1).
    pr_state: str = ""
    #: The failing blocking checks' names, comma-joined and sorted, on a red;
    #: empty otherwise. A `ci_red` repair's evidence (spec §3).
    failing_jobs: str = ""

    def as_tuple(self):
        return (self.outcome, self.review, self.note, self.sha, self.ci,
                self.ci_first, self.resubmissions, self.pr, self.merge_base,
                self.delta_digest, self.fingerprints, self.pr_state,
                self.failing_jobs)

    #: Field count, so a consumer can assert it rather than assume it. The
    #: absorption hazard below is silent, and silence is what made the missing
    #: `pr` field survive a live run that looked like it worked.
    FIELDS = 13

    def as_line(self) -> str:
        """The THIRTEEN-field form the shell parses. A caller reading fewer
        absorbs the last into its neighbour, silently -- `read -r a b c` puts
        every remaining field into `c`, so a short reader does not error, it
        corrupts one value."""
        r = "" if self.resubmissions is None else self.resubmissions
        return (f"{self.outcome}|{self.review}|{self.note}|{self.sha}|"
                f"{self.ci}|{self.ci_first}|{r}|{self.pr}|{self.merge_base}|{self.delta_digest}|"
                f"{self.fingerprints}|{self.pr_state}|{self.failing_jobs}")


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


def _post_status(item, pr, head, verdict, reviewer_out, merge_base, d: Deps) -> None:
    """Every verdict posts `bircher/cross-review` on the reviewed head: success,
    failure, or error, with the range in the description (spec §4). A FAIL
    can no longer leave an older success standing.

    BEST EFFORT, as the PR comment is: the outcome is derived from the
    repository and is already correct; a transient GitHub failure here must
    not turn a derivation into an escalation. The runner's own idempotent
    post before the merge is the fallback for a missing success.
    """
    blocking = attest.blocking_count(reviewer_out) if verdict == "FAIL" else 0
    state = attest.state_for(verdict)
    desc = attest.describe(d.reviewer, d.round_number, verdict, blocking, merge_base, head)
    # CONTENT-ADDRESSED, exactly as the PR comment's key below is, and for the
    # same reason. `status:<head>:<round>` did not change when the VERDICT
    # changed: the round only advances on a recorded `review_verdict` fact, and
    # an `error` (no verdict) or a terminal FAIL records none -- so a later
    # derivation of the SAME head posting a different state replayed the same
    # key, `kernel/effects.py` returned the prior external id WITHOUT executing,
    # and the new state was never posted. A gate left showing a superseded
    # verdict is the exact failure this section exists to make impossible.
    stamp = hashlib.sha256(f"{state}\n{desc}".encode()).hexdigest()[:16]
    key = f"status:{head}:{stamp}"
    try:
        d.effect("status_check", key,
                 ["gh", "api", f"repos/{d.repo}/statuses/{head}", "-X", "POST",
                  "-f", f"state={state}", "-f", f"context={attest.CONTEXT}",
                  "-f", f"description={desc}"])
        # "recorded", not "posted": `d.effect` is idempotent, so a replayed key
        # returns the prior external id without a network call. Claiming a post
        # happened on that path is a log line that cannot be read as evidence.
        d.log(f"{item}: recorded status effect {attest.CONTEXT}={state} "
              f"on {head[:7]} ({desc})")
    except Exception as exc:                       # noqa: BLE001
        d.log(f"{item}: failed to post {attest.CONTEXT}={state} on PR #{pr} "
              f"({type(exc).__name__}: {exc}) -> continuing; the outcome is "
              f"derived from the repository and is unaffected")


def _failing_names(checks: str, ignore: str) -> list[str]:
    """The failing blocking checks' names from gh's `name|bucket` rows, with
    the ignore pattern applied as `keep_blocking` applies it.

    Each name has its own commas replaced with a space and its whitespace
    collapsed. A GitHub matrix name carries a comma of its own (`build
    (ubuntu-latest, node 18)`), and `as_line` joins this field's names with a
    comma too -- so a name with a raw comma in it would read back as two
    names. The name is never an identifier the loop looks up again: it is
    evidence for a set comparison (`test_red_ci_names_its_failing_jobs_
    sorted_and_ignoring_the_ignored`) and for a repair brief's text, and a
    lossy-but-unambiguous name serves both exactly as well as the original --
    so normalising it here keeps the comma-joined transport a single shape
    rather than adding a second escaping convention just for names that
    happen to contain a comma.
    """
    from coordinator.ci import drop_ignored
    out = set()
    for line in drop_ignored(checks or "", ignore).splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[1].strip() in ("fail", "cancel"):
            name = " ".join(parts[0].replace(",", " ").split())
            out.add(name)
    return sorted(out)


def _pr_state_word(d: Deps, pr: str) -> str:
    """open | closed | merged | "" for *pr*."""
    if not pr:
        return ""
    state, merged_at = d.pr_state(pr)
    if merged_at and merged_at != "null":
        return "merged"
    return {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}.get((state or "").upper(), "")


def derive(item: str, code: str, pr: str, issue: str, *, deps: Deps,
           rerun_max: int = 4) -> Derived:
    """The whole derivation. Returns the thirteen fields."""
    d = deps
    known = pr
    pr = _settle_pr(item, code, pr, issue, d)

    ci_first, resubmissions = "unknown", None
    if pr:
        branch = d.branch_of(pr)
        if branch:
            ci_first, resubmissions = d.history(branch)

    ci, verdict, head_sha, reviewer_out = "na", None, "", ""
    merge_base, digest = "", ""
    failing = []
    if pr:
        ci = _settle_ci(item, pr, d, rerun_max)
        # THE HEAD CI RAN ON, captured once CI has settled and never re-read
        # (#66), on every colour: a red head is what a ci_red repair names
        # (closed-loop spec §1) and what the CI observation is recorded
        # against.
        head = (d.head_of(pr) or "").strip()
        if _FULL_SHA.match(head):
            head_sha = head
        else:
            d.log(f"{item}: could not capture a full 40-hex head for PR #{pr} "
                  f"(got {head or '<empty>'!r}) -> cannot pin a merge")
        if ci == "red":
            failing = _failing_names(d.checks(pr), d.ignore)

        if ci == "green":
            d.log(f"{item}: PR #{pr} CI green -> {d.reviewer} review at {head_sha[:7]}")
            verdict, reviewer_out = d.review(pr, head_sha)
            if verdict == "NONE":
                verdict = None
            if head_sha:
                # WHAT WAS REVIEWED, captured against the same pinned head the
                # reviewer was told to check out (spec §4): the merge-base with
                # the PR's base and the digest of the PR's own delta.
                base = d.base_of(pr) or "main"
                # VALIDATED LIKE THE HEAD IS. `merge_base` is whatever the
                # lookup printed -- `null`, an error line, anything -- and it
                # is spliced into the pipe-delimited tuple, into the status
                # description and, through the runner, into hand-built JSON.
                # Anything that is not a 40-hex sha is "unknown" (`""`), which
                # the description already renders as `?`.
                raw_base = (d.merge_base(base, head_sha) or "").strip()
                merge_base = raw_base if _FULL_SHA.match(raw_base) else ""
                if raw_base and not merge_base:
                    d.log(f"{item}: merge-base for PR #{pr} is not a 40-hex sha "
                          f"({raw_base!r}) -> recording it as unknown")
                digest = d.delta_digest(base, head_sha) or ""
                _post_status(item, pr, head_sha, verdict, reviewer_out,
                             merge_base, d)

    o = classify(pr or None, ci, verdict, reviewer=d.reviewer)

    if pr:
        head_field = f" head={head_sha}" if o.outcome == "ready" and head_sha else ""
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

    # The head rides out whenever there is one (closed-loop spec §1). The
    # merge gate is the runner's `outcome == ready`; a sha on a failed or
    # escalated derivation authorises nothing and is the evidence a repair
    # round and a CI observation need.
    sha_out = head_sha
    # THE VERDICT'S FINGERPRINTS (closed-loop spec §3): one per blocking
    # finding, so a later round can tell "the same defect, unmoved" from "a
    # different one". Only on a FAIL -- a PASS or an escalation names no
    # findings to fingerprint.
    fps = ",".join(attest.fingerprints(reviewer_out)) if verdict == "FAIL" else ""
    # THE PR'S STATE, for the PR this derivation actually settled on, or for
    # the caller's own PR when the settle discarded it (spec §1): a PR a
    # person closed still has to reach the journal, or the run cannot end.
    pr_state = _pr_state_word(d, pr or known)
    # The findings ride out on every FAIL: the repair round is briefed from
    # them.
    return Derived(o.outcome, o.review, o.note, sha_out, o.ci,
                   ci_first, resubmissions, str(pr or ""),
                   findings=(reviewer_out if verdict == "FAIL" else ""),
                   merge_base=merge_base if head_sha else "",
                   delta_digest=digest if head_sha else "",
                   fingerprints=fps, pr_state=pr_state,
                   failing_jobs=",".join(failing))
