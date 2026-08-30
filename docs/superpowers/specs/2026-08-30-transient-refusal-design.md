# GitHub lags, so the merge path must wait and retry

**Status:** design, written before the code, and REWRITTEN after a
cross-vendor review rejected the first version. The change touches effect
classification in the kernel, where a mistake means treating a
possibly-merged pull request as not merged.

## The problem, from a live run

muesli #726 → PR #735, 2026-08-30. The first merge attempt failed and the run
halted, needing a human to reconcile it before it could merge at all. On a
system whose purpose is running unattended, that is disqualifying if it
recurs.

Two independent defects produced it.

### 1. The pre-merge gate watches a field that cannot see the blocker

`merge_ready_pr` polls `.mergeable`, which reports CONFLICT state only —
`MERGEABLE` / `CONFLICTING` / `UNKNOWN`. Branch protection lives in
`mergeStateStatus`: `CLEAN` / `BLOCKED` / `BEHIND` / `UNSTABLE` / `DIRTY`.

muesli requires `review-gate`, which bircher does not post. The chain is:
bircher posts `bircher/cross-review`, a workflow reacts to that status event
and posts `review-gate`. Measured on #735:

    13:29:11  bircher/cross-review = success   (bircher posts)
    13:29:18  review-gate          = success   (the workflow reacts, +7s)

In that window the PR was `mergeable=MERGEABLE` (true — no conflicts) and
`mergeStateStatus=BLOCKED`. The gate saw MERGEABLE, proceeded, and GitHub
refused.

This is ordinary GitHub eventual consistency. The code already waits out one
instance of it — mergeability is computed lazily, so a first query returns
`UNKNOWN` and only triggers computation while a second seconds later returns
`MERGEABLE` (reproduced on four open PRs while writing this). It simply does
not wait for the other.

### 2. A definitive refusal is recorded as an unknown outcome

`kernel/cli.py::_executor` raises on any non-zero exit, and `perform` turns
**any** executor exception into `effect_uncertain` plus a run halt. Its own
docstring already says so, and calls out the consequence.

So the halt is a known gap being reached, not a safety net working.
`merge_ready_pr` already retries for ~30s — enough to cover a 7-second lag —
but the first failure halted the run and every retry after it was refused by
the kernel rather than by GitHub. **The safety mechanism defeated the
recovery mechanism.**

## What the first version of this design got wrong

It proposed classifying the failure from an allowlist of stderr substrings
("base branch policy prohibits"). A cross-vendor review rejected that as
unsound, and the rejection is correct:

**Stderr describes neither the command's transaction boundary nor the final
remote state.** `gh pr merge` can complete server-side before the client
fails — a case `run-queue.sh` already carries a scar for ("a failed ATTEMPT is
not a failed MERGE. The request can complete server-side before the client
dies"). So an allowlisted substring could accompany a merge that DID happen,
and the classifier would mark the journal `refused` and permit a replay
without ever establishing ground truth. GitHub can also reword the message at
any time, silently converting a retryable case back into a halt or, worse, the
reverse.

The lesson generalises: **do not infer a world state from a message about a
command. Observe the world.**

## Two phases, and only the first is being built now

A second review round rejected the combined design, and its findings pushed
me to a decomposition I should have reached first:

**Defect 1 alone explains the incident, and fixing it needs no kernel change
at all.** If the gate waits for `mergeStateStatus == CLEAN`, the merge is never
attempted while BLOCKED, GitHub never refuses it, no effect becomes uncertain,
and the run never halts. PR #735 would have merged unattended.

Defect 2 — teaching the kernel to distinguish a refused effect from an
uncertain one — is a robustness improvement for merges that fail *anyway*. It
touches the kernel's uncertainty boundary, which is the single most
safety-critical judgement in the system, and two review rounds have now found
real holes in it. **It is deferred**, and Phase 1 should make it rare enough
to design without time pressure.

Shipping Phase 1 alone is not a partial fix hiding a known bug: it removes the
cause. Phase 2 addresses what happens when something else goes wrong.

---

# Phase 1 — the gate waits for the state that actually blocks the merge

### The combined classifier

`mergeable` and `mergeStateStatus` are read from ONE response so they cannot
be mutually stale. Precedence, evaluated in order:

1. PR not `OPEN` → not a merge candidate; report the observed state.
2. Head != the expected reviewed head → **defer immediately**. The merge must
   stay pinned to what was reviewed; never proceed on a moved head.
3. `mergeable == CONFLICTING` → defer, whatever `mergeStateStatus` says.
4. Either field `UNKNOWN`/null → wait; both are computed lazily.
5. An enum value neither table knows → **fail closed**: defer, logging the
   unrecognised value by name. GitHub adding a state must never read as
   "proceed".
6. Otherwise dispatch on `mergeStateStatus` (below).
7. Any API error during the poll → treat as `UNKNOWN` and keep waiting inside
   the deadline. A failed lookup must never read as CLEAN.

### The state policies

| state | interpretation | policy |
|---|---|---|
| `CLEAN` | ready | proceed |
| `BLOCKED` | see the ordered classifier below | |
| `BEHIND` | strict mode; base moved | **defer. No mutation.** |
| `UNSTABLE` | a NON-required check is failing | proceed — branch protection does not require it and waiting cannot change it |
| `DIRTY` | real conflict | defer now |
| `UNKNOWN` | not computed yet | wait, backoff, bounded by the phase deadline |

**BEHIND defers rather than updating the branch.** An earlier draft had it
attempt one `update-branch`, and that was incoherent twice over. Precedence
rule 2 defers whenever the head is not the reviewed head — and `update-branch`
MOVES the head, so the path could never reach CLEAN unless an implementer
silently re-pointed the reviewed SHA, which would merge code no reviewer had
seen. It is also a routed mutation, contradicting Phase 1's claim to change
only WHEN a merge is attempted. Updating a BEHIND branch requires re-reviewing
the new head and issuing a fresh authorization; that is a lifecycle change and
belongs in its own piece of work, not smuggled into a timing fix.

**And it already exists.** `recover_pr_cmd` detects BEHIND before it reviews,
performs the `update-branch` effect, reviews the NEW head and only then calls
`merge_ready_pr` — the full lifecycle, in the one place that has a reviewer to
re-run. The gate deferring on BEHIND is therefore not a capability gap: it
keeps the mutation in the caller that can pay for it, rather than adding a
second, reviewer-less one inside a timing gate.

`UNKNOWN` has no separate poll count: the phase deadline already bounds it,
and a second bound would be a knob nobody sets correctly.

### The BLOCKED classifier, ordered and exhaustive

`BLOCKED` is not a promise that something will finish. It covers a check still
running, a check that failed, a check that has not registered yet, and
conditions that are not checks at all — a missing approval satisfies none of
the check-shaped rules while still blocking forever.

Evaluated in order; the first match wins:

| # | condition | policy |
|---|---|---|
| 1 | the required-context snapshot is unreadable or errored | **defer, fail closed** — and do NOT consume grace; a degraded API must not be read as a check being absent |
| 2 | the repository requires no contexts at all | defer — BLOCKED without required checks is a non-check policy blocker |
| 3 | any required context is present and PENDING | wait, backoff |
| 4 | any required context is ABSENT from the reported set | wait, within the registration grace below |
| 5 | every required context has reported, and any is not success | defer — durable; waiting cannot fix a failing check |
| 6 | every required context has reported success, yet still BLOCKED | defer — a non-check blocker (approval, or a policy this code cannot see) |

Row 6 is the case the obvious three-way split missed entirely: all green, still
blocked, forever.

Rows 1 and 4 must not be confused. An unreadable snapshot is not evidence of
absence, and treating it as such would spend the grace period on an API
outage and then defer for the wrong reason.

### The registration grace, and the race it exists for

The tempting rule — "any check pending → wait, else defer" — **fails on
exactly the case this design exists for.** During the seven-second reaction
window `review-gate` has not merely not finished: it has not REGISTERED. The
snapshot contains no such context, so that rule would defer to a human on the
transient condition it was written to tolerate.

So an absent required context is presumed to be arriving, bounded:

- Grace is keyed by **(head SHA, context name)** and starts when THAT context
  is first observed absent — not when the PR was first seen BLOCKED. A PR may
  sit BLOCKED for another reason first and only later expose a newly required
  or re-run context as absent; starting the clock at the first BLOCKED
  observation would defer immediately on a genuine registration race.
- It is measured on the same WALL clock as `_arm_deadline`. Monotonic would
  be better in isolation, but the deadline that bounds every wait here is
  wall-clock, so a monotonic grace inside a wall-clock phase would not make
  the pair safer — only the claim harder to check. If the clock cannot be read
  at all the gate keeps WAITING rather than deferring, because deferring would
  escalate on the transient condition this exists to tolerate; a poll cap
  bounds the loop in that case.
- It resets when the head changes: a new head is a new set of check runs.
- If the required SET changes, contexts new to it start their own grace;
  contexts that leave it are forgotten.
- Default `BIRCHER_CHECK_REGISTRATION_GRACE` = 120s, and it is bounded by
  `PREMERGE_DEADLINE_AT` regardless — grace can never outlive the phase.
- When grace expires, re-read the PR state and contexts as close together as
  the API allows, and defer only if the context is still absent.

### WHERE the gate runs, which the code decides

`merge_ready_pr` already waits for `mergeable`, and only THEN posts
`bircher/cross-review`. That ordering matters and was not obvious from the
design alone: `review-gate` reacts to the cross-review status, so a gate
waiting for `CLEAN` placed BEFORE the status post would wait for a state that
cannot arrive until we act. It would block forever on its own inaction.

So there are two waits, with different jobs:

1. The existing `mergeable` wait stays where it is — it catches `CONFLICTING`
   before the cost of posting a status.
2. The new `mergeStateStatus` gate runs AFTER `_post_cross_review_status` and
   immediately before the merge loop, which is the only point at which `CLEAN`
   is reachable.

### Backoff

Exponential, 2s doubling to a 30s ceiling, and every sleep capped by
`PREMERGE_DEADLINE_AT`. A deadline check followed by an uncapped sleep crosses
the deadline it just tested — this file has been bitten by that once (#71), so
the rule is uniform rather than a list of exceptions.

### What Phase 1 deliberately does NOT do

**No kernel change.** Effect classification is untouched: an unrecognised
merge failure still becomes `effect_uncertain` and still halts the run,
exactly as today. If a merge fails for a reason waiting cannot fix, a human is
still asked. That is the conservative behaviour and Phase 1 does not weaken it.

**`gh pr merge --auto`.** GitHub's error suggests it, and it would remove the
race by handing merge timing to GitHub. Rejected: bircher would not know when
the merge happened, and the post-merge safety net — watch main CI on the merge
commit, revert on red — depends on holding that commit at a known moment.

## Phase 1 acceptance

1. The classifier is driven over the full cross-product of `mergeable` ×
   `mergeStateStatus`, including null, an unknown future enum value, an API
   error, a non-OPEN PR and a moved head. Every fail-closed case asserts defer.
2. Every row of the BLOCKED classifier has a test, including row 6 (all
   required contexts green, still BLOCKED) and row 1 (unreadable snapshot
   defers WITHOUT consuming grace).
3. **The registration race has its own test:** BLOCKED with the required check
   absent, the check registering only afterwards, asserting the gate waited.
   Plus the late-absence case: BLOCKED for another reason first, the context
   appearing absent only later, asserting grace starts then and not earlier.
4. A durable BLOCKED (all contexts reported, one failing) defers immediately
   rather than burning the phase budget.
5. BEHIND defers and performs NO mutation — asserted by driving the gate with
   a fake gh that fails the test if `update-branch` is ever called.
6. No sleep crosses the phase deadline, and grace never outlives it.
7. Mutation: reverting the gate to poll `.mergeable` alone must fail a named
   test.
8. **A second live muesli item merges with no manual reconciliation.** That is
   the acceptance that matters; the first one needed a human.

---

# Phase 2 — refused vs uncertain (DEFERRED, not designed)

Recorded so the reasoning is not lost. Two review rounds rejected two attempts:

- **v1 classified from stderr text.** Rejected: stderr describes neither the
  command's transaction boundary nor the final remote state, and this file
  already carries the scar — a failed ATTEMPT is not a failed MERGE, because
  the request can complete server-side before the client dies.
- **v2 classified from one structured observation.** Rejected: an `OPEN`
  response is not authoritative either. It can be stale, or a concurrent actor
  can merge immediately after it, so the journal would assert non-occurrence
  without any linearizable relationship to the failed request.

The unresolved question is whether non-occurrence can be *established* at all
against an API that offers no transactional read tied to a failed request. The
most promising line — not yet designed — is to stop trying: lean on
`--match-head-commit`, which makes a merge structurally impossible to perform
twice at the same head, so a stale reading costs a wasted retry rather than a
double effect. Safety would come from the operation's idempotence rather than
the observation's authority, and the journal would record what was OBSERVED
and when, never the bare claim "did not happen".

Two further requirements any Phase 2 design must meet, both from review:

- The attempt rollover must be ONE atomic transaction — conditionally moving
  `(effect_id=old, state=refused)` to `(effect_id=new, state=intended)` and
  appending the intent fact together. A crash between two writes otherwise
  leaves a row retryable while an attempt is outstanding.
- Fault-injection tests at every persistence boundary, not only happy-path
  retry tests.
