"""The one next action for a run with a pull request (closed-loop spec §2).

Pure with respect to the world: `ground` is what the CLI read from GitHub,
`store` is the journal, and the answer is one Step. The runner performs it
and records the resulting fact; the next call starts again from the journal.
"""

from __future__ import annotations

from dataclasses import dataclass

from kernel import back


@dataclass(frozen=True)
class Ground:
    pr_state: str            # open | closed | merged | "" (unknown)
    head: str                # the PR's head, 40 hex, or ""
    ci: str                  # green | red | pending | na
    failing_jobs: tuple      # names of the failing jobs when red
    verdict: str | None      # PASS | FAIL | None, for THIS head, from the journal
    fingerprints: tuple      # that verdict's fingerprints


@dataclass(frozen=True)
class Step:
    kind: str                # wait | review | repair | merge | park | done
    cause: str = ""          # ci_red | review_fail, for repair and park
    evidence: tuple = ()
    reason: str = ""         # park reason
    redispatch: bool = False # repair: already requested for this head; dispatch the session, request nothing


_TERMINAL = frozenset({"merged", "cancelled", "ended"})


def next_step(store, run_id: str, g: Ground) -> Step:
    state = store.run_state(run_id)
    if state in _TERMINAL:
        return Step("done")
    if g.pr_state in ("closed", "merged"):
        # The PR left the loop by another hand; the runner records the outcome.
        return Step("done")
    # No head, or a PR state the derivation could not read: nothing to act
    # on. (The round's session is the runner's concern: it cancels it before
    # deriving, as it always has.)
    if not g.head or g.pr_state != "open":
        return Step("wait")
    if state == "merge_requested":
        return Step("merge")
    if g.ci in ("pending", "na"):
        return Step("wait")

    if g.ci == "red":
        cause, evidence = "ci_red", tuple(sorted(g.failing_jobs))
    elif g.verdict is None:
        return Step("review")
    elif g.verdict == "PASS":
        return Step("merge")
    else:
        cause, evidence = "review_fail", tuple(g.fingerprints)

    # A repair already requested and not yet worked: the run sits at
    # `planned`; dispatch the session, request nothing new.
    #
    # THE STATE ALONE, not the head. In the back half `planned` is only ever
    # reached through `request_repair`, so a repair round is already owed
    # whatever the head now is -- and the head moves the moment anyone pushes
    # to the branch while the run waits here. Also matching `repair_for_head`
    # stranded such a run: no repair named the new head, so this returned a
    # plain `repair`, `request_repair` is refused from `planned`, and the
    # runner logged "the kernel did not open a repair round" and waited --
    # every wave, forever, spending a derivation and a cross-review each time.
    if state == "planned":
        return Step("repair", cause, evidence, redispatch=True)
    if back.would_be_third_identical(store, run_id, cause, evidence):
        return Step("park", cause, evidence, reason="no_progress")
    return Step("repair", cause, evidence)
