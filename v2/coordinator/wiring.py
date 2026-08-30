"""Real dependencies for `derive`.

Separated from `outcome.py` so the derivation itself never imports a subprocess:
its tests drive it with plain callables, and everything that touches the world
is assembled here.
"""

from __future__ import annotations

import os
import time

from coordinator import ci as ci_mod
from coordinator import discovery, review
from coordinator.ci import GhError, _gh
from coordinator.effects import perform_effect
from coordinator.observe import ci_history
from coordinator.outcome import Deps


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def live_deps(item: str, *, repo: str, reviewer: str, server: str,
              bundle_dir: str, poll_interval: int, ci_wait: int = 1500,
              rerun_wait: int = 900, log=None) -> Deps:
    """Wire `derive` to the real world.

    EVERYTHING IS PASSED IN. Nothing here reads `REPO`, `SERVER`, `BUNDLE_DIR`
    or `RECOVERY_REVIEWER` from the environment, because in `run-queue.sh` all
    four are PLAIN ASSIGNMENTS rather than exports -- a subprocess sees none of
    them.

    That cost three live runs to learn one instance at a time. The reviewer
    defaulted to the implementer's own vendor and quietly ended cross-vendor
    independence; the repo defaulted to empty, which only worked because the
    acceptance launcher happened to export `BIRCHER_REPO`; the bundle dir
    defaulted to `.`, so `agents/<reviewer>` resolved against whatever
    directory the runner was standing in.

    A required argument turns each of those into an argparse error instead.

    `poll_interval` joined them for the same reason and is passed the same way.
    It used to be read here as `MAIN_CI_POLL_INTERVAL`, which run-queue.sh
    ASSIGNS WITHOUT EXPORTING (line 86) -- so the operator's
    `BIRCHER_MAIN_CI_POLL_INTERVAL` override never crossed the boundary and
    every run silently polled at the 30s default. The paragraph above named
    four variables and was accurate about those four; the very next line then
    read a fifth from the environment. That is the whole failure mode in one
    docstring.

    `BIRCHER_CI_IGNORE_CHECKS` is different and IS read from the environment,
    correctly: run-queue.sh never assigns it -- every use is
    `${BIRCHER_CI_IGNORE_CHECKS:-...}` against the operator's own environment
    -- so both languages read the same exported value or both fall back to the
    same default. Reading it here keeps ONE list rather than two.
    """
    required_cache: dict = {}
    interval = poll_interval
    ignore = os.environ.get("BIRCHER_CI_IGNORE_CHECKS") or ci_mod.DEFAULT_IGNORED

    def required() -> str:
        return ci_mod.required_contexts(repo, cache=required_cache)

    def checks(pr):
        try:
            return _gh(["pr", "checks", str(pr), "--json", "name,bucket",
                        "-q", r'.[] | "\(.name)|\(.bucket)"'])
        except GhError:
            # EMPTY reads as `pending`, which keeps the caller waiting. A
            # failed lookup must not read as green.
            return ""

    def head_of(pr):
        # `--jq`, exactly as the bash called it: gh does the extraction and
        # returns a bare sha. Fetching the whole document and parsing it here
        # looked equivalent and was not -- `--self-test` fakes `gh api` at the
        # --jq boundary, so the port silently returned an empty sha and the
        # merge could not be pinned.
        try:
            return _gh(["api", f"repos/{repo}/pulls/{pr}", "--jq", ".head.sha"]).strip()
        except GhError:
            return ""

    def branch_of(pr):
        try:
            return _gh(["api", f"repos/{repo}/pulls/{pr}", "--jq", ".head.ref"]).strip()
        except GhError:
            return ""

    def pr_state(pr):
        try:
            import json
            d = json.loads(_gh(["pr", "view", str(pr), "--json", "state,mergedAt"]))
            return (d.get("state") or "", d.get("mergedAt") or "")
        except (GhError, ValueError, AttributeError):
            # Unknown state must NOT read as abandoned: discarding a PR we
            # could not read would lose the only candidate the item has.
            return ("", "")

    def do_review(pr, sha):
        return review.dispatch(
            str(pr), repo, sha, reviewer=reviewer, bundle_dir=bundle_dir,
            server=server,
            log_path=os.environ.get("BIRCHER_REVIEW_LOG") or f"/tmp/review-{item}.log")

    def close_sibling(loser, winner):
        perform_effect(
            "pull_request", f"close-pr:{loser}",
            ["gh", "pr", "close", str(loser), "--repo", repo, "--comment",
             f"Superseded by #{winner} (item {item} opened multiple PRs after a "
             f"CI-red retry; adopting the CI-green one)."])

    def ci_of(pr):
        return ci_mod.normalize(
            ci_mod.keep_blocking(checks(pr), required(), ignore))

    return Deps(
        checks=checks,
        head_of=head_of,
        review=do_review,
        effect=lambda cls, key, argv: perform_effect(cls, key, argv),
        log=log or (lambda m: print(f"[batch:derive] {m}", file=__import__("sys").stderr)),
        failure_kind=lambda pr: ci_mod.failure_kind(pr, ignore=ignore),
        rerun=lambda pr: ci_mod.rerun_and_wait(
            pr, required(), sleep=time.sleep,
            timeout=rerun_wait, interval=interval, ignore=ignore),
        history=lambda br: _history(repo, br),
        branch_of=branch_of,
        pr_state=pr_state,
        discover_by_code=lambda code: discovery.by_code(repo, code),
        discover_by_issue=lambda issue: discovery.by_issue(repo, issue),
        reconcile=lambda code, pr: discovery.reconcile(
            repo, code, pr, ci_of=ci_of, close=close_sibling),
        # `gh=_gh` is NOT optional here: `poll` is the one ci_mod entry point
        # whose `gh` has no default, and omitting it raised TypeError on the
        # exact branch this dep exists for -- a PENDING CI. Every unit test
        # injects its own `wait_ci`, so nothing exercised the wired one, and
        # the live acceptance happened to run against a PR whose CI had
        # already settled.
        wait_ci=lambda pr: ci_mod.poll(
            pr, required(), gh=_gh, sleep=time.sleep,
            timeout=ci_wait, interval=interval, ignore=ignore),
        required=required(),
        reviewer=reviewer,
        ignore=ignore,
    )


def _history(repo: str, branch: str):
    h = ci_history(repo, branch)
    return (h.ci_first, h.resubmissions)
