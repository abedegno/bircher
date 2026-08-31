# The coordinator repairs, bounded

**Status:** design, before code. It changes when a merge can be authorised, so
a mistake lets a PR merge on a review that no longer describes it.

## Why

Measured, not assumed. Over eighteen muesli item-runs, **8 ended `failed` on a
reviewer FAIL**. Every one was a specific, actionable finding with a named fix;
none was a flake. `observe.py` turns a FAIL into outcome `failed`, which is
terminal, so each one stopped there.

Routing those findings back by hand and re-queueing produced:

- **#740** merged after 1 round
- **#750** merged after 2 rounds
- **#722** three distinct findings in three reviews, still open

So a bounded loop converges sometimes — 1 of 2 items inside the bound in the
controlled test — and every implementer fixed precisely what was routed. The
failure mode is not ignored findings; it is that the reviewer keeps finding
more. That makes a repair loop worth building and insufficient alone, which is
why the bound and its terminal escalation matter as much as the loop.

## What already exists, and must be used rather than rebuilt

**The kernel has the revision loop.** `_VERDICTS` maps
`request_revision -> planned`, and `authz.py` already defends the consequence:

> "LAST, not first. Returning the first let the revision loop —
> `request_revision -> planned -> start_implementation` — put a new implementer
> in place whose own review then passed the check."

So the reviewer-independence check already anticipates a NEW implementer
arriving mid-run, and takes the last `start_implementation` actor. The path is
designed, defended and unused.

**`muesli-loop` has a working fix loop**, bounded at 3, inside the lead session.
It is not the thing being replaced here — see Scope.

## Scope

This designs the loop the COORDINATOR runs when its own out-of-band review
fails. It does not remove the lead session's internal fix loop, which operates
on a different signal (its own review, and its own CI failures) and is measured
to work — during #750's round 2 it spent ~90 minutes fixing a red
`server (go)` before the runner ever saw the session settle.

Two loops will therefore run until the migration removes the lead session's
review. That duplication is already recorded as architecture gap 2 and is not
resolved here.

## The protocol

### 1. A FAIL with rounds remaining becomes a revision, not an outcome

`classify` gains one input, `rounds_remaining`, and one branch:

    verdict == "FAIL" and rounds_remaining > 0  -> Outcome("revise", ...)
    verdict == "FAIL" and rounds_remaining == 0 -> Outcome("failed", ...)   # as today

`revise` is a new outcome in the coordinator's vocabulary only. It never
reaches the scorecard: the run ends as `ready` or `failed`, and the scorecard's
`rounds` field — currently always null — reports how many revisions happened.
That field exists and has never had an observation behind it.

### 2. Which session receives the fix

**A fresh one.** The original implementer session is cancelled before the
derivation runs; reviving it is not available and would not be desirable —
resuming a cancelled session inherits whatever state it stopped in.

The coordinator dispatches a new implementer with `_kernel_dispatch(vendor,
implementer)`, which mints a new generation. The prompt carries the item, the
PR, and the reviewer's blocking findings verbatim — the same shape that worked
by hand for #740 and #750.

**Vendor rotation stays.** The repair is implemented by the vendor whose turn
it is and reviewed by the opposite one, as now. In the hand-run test the
repairs were implemented by a different vendor than the original, and both
still fixed exactly what was routed.

### 3. Kernel transitions, exactly

    reviewing --record_review(request_revision)--> planned
             --start_implementation--------------> implementing
             --record_implementation_output------> (new artifact)
             --record_ci_observation-------------> (new head)
             --record_review(accept)-------------> reviewing
             --request_merge---------------------> merge_requested

Nothing new is needed in the kernel. `record_review` must be called with
`request_revision`, NOT `reject`: `reject` leaves the run in `reviewing`, which
is a dead end for this purpose.

### 4. What is invalidated, and what rebinds

**Everything the old review bound.** A revision produces a new commit, so:

- the artifact hash changes — `record_implementation_output` records the new one
- the head changes — `record_ci_observation` records the new one
- the new `record_review` binds THOSE, and `validate_review` already refuses a
  binding that does not match the run's current output

The reviewed-head that feeds `--match-head-commit` is re-captured per round, as
it is today. **No approval survives a round**, which is the property that makes
this safe: the merge is authorised against what the last reviewer saw.

### 5. The bound

`BIRCHER_MAX_REVISIONS`, default **2**, range 0–5. Zero disables the loop
entirely and restores today's behaviour exactly, which is what makes this
shippable behind a switch.

Counted from the JOURNAL — the number of `transition_performed` facts for
`record_review` with `request_revision` in this run — not from a variable. A
coordinator that dies and is re-driven must not get a fresh allowance.

Default 2 and not 3 because the evidence supports it: #740 converged in 1,
#750 in 2, #722 had produced a new finding at every round and would have
exhausted any bound. A third round costs a full implement-plus-CI-plus-review
cycle (~25 minutes) for a case not yet observed to succeed.

### 6. Crash resumption

The loop holds no state of its own. On re-entry the coordinator reads the run's
state and the journalled revision count:

| run state on re-entry | meaning | action |
|---|---|---|
| `planned` | a revision was requested, the implementer never dispatched | dispatch it |
| `implementing` | an implementer is or was in flight | settle-detect as normal |
| `reviewing` | a verdict was recorded | proceed to merge or revise |
| `merge_requested` | authorised | resume at the merge |

This is the same re-entry logic `recover_pr_cmd` already performs; it is not
new machinery.

### 7. Terminal escalation

When the bound is reached the run escalates exactly as today — `failed`, the
issue labelled `bircher:escalated`, the PR left open — with one addition: the
note names the round count and the LAST finding, so a human sees what the
final reviewer objected to rather than having to open three review logs.

## What this deliberately does not do

**It does not touch `revalidate_merge`'s base check**, which compares the run's
recorded base against itself (`base-binding-weakness.md`). A revision does not
make that worse: the base is the run's, unchanged across rounds, and muesli's
`strict: true` still refuses a stale branch.

**It does not remove the lead session's fix loop** — see Scope.

**It does not run in the runner.** This is coordinator code. The bash keeps
calling `derive` and receives `ready` or `failed`, unchanged, which is what
lets this ship before the migration.

## Acceptance

1. Every branch of `classify` under the new input, including
   `rounds_remaining == 0` reproducing today's behaviour exactly.
2. A FAIL with rounds remaining records `request_revision` and NOT `reject` —
   asserted on the command, since the two differ only in destination.
3. The revision count is read from the journal, proven by a test that
   re-enters with a synthetic journal and gets the right allowance.
4. A round produces a NEW artifact and head, and the merge is pinned to the
   LAST reviewed head — asserted by driving two rounds with different heads and
   checking what `--match-head-commit` receives.
5. `BIRCHER_MAX_REVISIONS=0` is byte-identical to today, so the switch is a
   real rollback.
6. Re-entry at each of the four states resumes correctly.
7. Mutation: making a FAIL record `reject` instead of `request_revision` must
   fail a named test; the run would sit in `reviewing` and never revise.
8. **A live muesli item that fails review is repaired and merged with no human
   routing the finding.** That is what this is for, and #722 is the standing
   counter-example: if it still exhausts the bound, that is the correct outcome
   and not a failure of the loop.
