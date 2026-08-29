"""CI observation: what the checks say, classified.

Mechanism by the design's own list -- "Git, GitHub and CI adapters" and the
distinction between `code_failure` and `infrastructure_failure`. Shared by the
outcome derivation and the merge machinery, which is why it moves before either
of them: it is the dependency both are waiting on.

Pure. Every function here takes text and returns a verdict; the `gh` calls that
produce the text stay with their callers for now.
"""

from __future__ import annotations

import re

#: Checks that never block a merge. `review-gate` MUST stay excluded or the
#: derivation DEADLOCKS: it stays pending until a cross-vendor review is posted,
#: and the caller is the thing about to post one. Each waits for the other.
#: Seen on muesli PR #549, which hung with every other check green.
DEFAULT_IGNORED = "Dependabot|review-gate"


def drop_ignored(lines: str, ignore: str = DEFAULT_IGNORED) -> str:
    """Drop `name|bucket|...` rows whose NAME matches the ignore pattern."""
    pat = re.compile(rf"^({ignore})\|")
    return "\n".join(l for l in lines.splitlines() if not pat.match(l))


def normalize(buckets: str) -> str:
    """`green` | `red` | `pending` from gh's bucket column.

    EMPTY IS PENDING, not green. No checks reported yet is the absence of a
    verdict, and reading it as success would merge a PR whose CI had not
    started -- the single most expensive misreading available here.
    """
    if not buckets.strip():
        return "pending"
    seen = {l.strip() for l in buckets.splitlines()}
    if "fail" in seen or "cancel" in seen:
        return "red"
    if "pending" in seen:
        return "pending"
    return "green"


def keep_blocking(lines: str, required: str, ignore: str = DEFAULT_IGNORED) -> str:
    """Reduce `name|bucket|state` rows to the `bucket|state` of BLOCKING ones.

    With no required contexts declared, every non-ignored check blocks -- the
    conservative reading, since "nothing is required" more often means "the
    lookup failed" than "anything may fail".

    FALLS BACK to all non-ignored checks when the required filter matches
    nothing but checks exist. A required-context list that names only contexts
    this PR does not run would otherwise reduce to an empty set, which
    `normalize` reads as `pending` for ever.
    """
    filtered = drop_ignored(lines, ignore)
    rows = [l for l in filtered.splitlines() if l.strip()]

    def tail(row: str) -> str:
        """Columns 2-3, matching `cut -d'|' -f2,3` INCLUDING its odd case.

        `cut` passes a line containing NO delimiter through UNCHANGED. That is
        not a curiosity here: `gh pr checks` output reaches this function as
        bare bucket words in at least one real path, and a port that returned
        empty for them turned every check into `pending` -- which `_wait_ci`
        reads as "keep waiting", so the run hung instead of failing. Caught by
        `--self-test`, whose fake `gh` emits exactly that shape.
        """
        if "|" not in row:
            return row
        parts = row.split("|")
        return "|".join(parts[1:3])

    if not required.strip():
        return "\n".join(tail(r) for r in rows)

    names = {n.strip() for n in required.splitlines() if n.strip()}
    kept = [tail(r) for r in rows if r.split("|")[0] in names]
    if not kept and rows:
        return "\n".join(tail(r) for r in rows)
    return "\n".join(kept)


def classify_failure(failed_step_count) -> str:
    """A red run with NO failed step is infrastructure, not the code.

    B-5: a runner that was never acquired, or a cancelled job, fails with zero
    failed steps. Burying those as `failed` buried three green PRs during one
    GitHub incident.
    """
    try:
        return "genuine" if int(failed_step_count) > 0 else "infra"
    except (TypeError, ValueError):
        return "infra"


_RUN_ID = re.compile(r"/actions/runs/(\d+)(?:/.*)?$")


def run_ids_from_links(lines: str, ignore: str = DEFAULT_IGNORED) -> list[str]:
    """Workflow run ids from `name|link` rows, ignored checks dropped.

    Sorted and unique, matching `sort -u`: several checks belong to one run, and
    re-running the same id once per check would multiply the CI cost of a single
    infrastructure failure by the number of jobs in it.
    """
    ids = set()
    for row in drop_ignored(lines, ignore).splitlines():
        for field in row.split("|")[1:]:
            m = _RUN_ID.search(field.strip())
            if m:
                ids.add(m.group(1))
    return sorted(ids)


def poll(pr: str, required: str, *, gh, sleep, timeout: int = 900,
         interval: int = 30) -> str:
    """Watch until CI settles, or `pending` if it never does.

    `sleep` and `gh` are injected so a test can drive many iterations without
    waiting -- the bash this replaces could only be tested by watching it, so
    it was not.

    NOT YET CALLED FROM BASH, and deliberately so. `_coordinator` wraps every
    call in `_net_run` at `BIRCHER_KERNEL_TIMEOUT` (5s), which would kill a
    1500-second watch. Adding a long-call variant of that helper for one caller
    that is about to become Python would be scaffolding built to be deleted.
    `_poll_ci` keeps its loop and already delegates the per-iteration decisions
    here, so there is one implementation of the JUDGEMENT either way; this
    exists for when `observe_outcome` itself is Python and owns the loop.

    RETURNS PENDING ON TIMEOUT, deliberately. "I stopped looking" is not "the
    checks failed", and reporting red here would fail a PR whose CI was merely
    slow -- while reporting green would merge one nobody watched.
    """
    waited = 0
    while waited < timeout:
        buckets = gh(["pr", "checks", str(pr), "--json", "name,bucket",
                      "-q", r'.[] | "\(.name)|\(.bucket)"'])
        settled = normalize(keep_blocking(buckets, required))
        if settled != "pending":
            return settled
        sleep(interval)
        waited += interval
    return "pending"
