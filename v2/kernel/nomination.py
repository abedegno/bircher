"""Verify what a run nominates for publication.

The implementer has no credential that can publish. It asks the kernel to,
naming a branch in a worktree the kernel can read. Everything the kernel goes
on to publish is what it OBSERVED at that branch, never what it was told.

`claimed_oid` exists so a caller can state what it thinks it built. It is never
a tiebreak and never a fallback: it may only AGREE with the observation. A
disagreement is a refusal, because the two readings mean the tree moved between
the claim and the observation, and the kernel does not publish a tree it cannot
name confidently.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class NotPublishable(Exception):
    """The nominated branch is not something the kernel will publish."""


def _git(worktree: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
    )


def verify_nomination(
    store,
    run_id: str,
    worktree: Path | str,
    branch: str,
    claimed_oid: str | None = None,
) -> str:
    """Return the oid the kernel will publish, or raise NotPublishable.

    The base is the one the KERNEL recorded when the run started — not the
    worktree's HEAD, which in an implementer's checkout is the tip of the work.
    """
    base = (store.run_base_sha(run_id) or "").strip()
    if not base:
        raise NotPublishable(
            f"run {run_id} has no base commit recorded; nothing to check provenance against"
        )

    tip = _git(worktree, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if tip.returncode != 0 or not tip.stdout.strip():
        raise NotPublishable(f"no such branch {branch!r} in {worktree}")
    observed = tip.stdout.strip()

    if observed == base:
        raise NotPublishable(
            f"branch {branch!r} is at the run's base {base[:12]}: no commit to publish"
        )

    descends = _git(worktree, "merge-base", "--is-ancestor", base, observed)
    if descends.returncode != 0:
        raise NotPublishable(
            f"{observed[:12]} does not descend from the run's base {base[:12]}"
        )

    merges = _git(worktree, "rev-list", "--merges", f"{base}..{observed}")
    if merges.returncode != 0:
        raise NotPublishable(
            f"cannot read the history of {observed[:12]}: {merges.stderr.strip()}"
        )
    if merges.stdout.strip():
        first = merges.stdout.split()[0]
        raise NotPublishable(
            f"merge commit {first[:12]} in {base[:12]}..{observed[:12]}: "
            "it imports history the kernel never observed"
        )

    if claimed_oid is not None and claimed_oid.strip() != observed:
        raise NotPublishable(
            f"claimed {claimed_oid.strip()[:12]} but observed {observed[:12]} "
            f"at {branch!r}; the observation decides and the claim disagrees"
        )

    return observed
