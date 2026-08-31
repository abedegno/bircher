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

## Who owns the loop — decided, because the first draft did not say

The first draft had the coordinator dispatch and settle a fresh implementer,
while also claiming nothing changed in the runner. Those contradict, and the
code settles it: **`v2/coordinator/session.py` is READ-ONLY** — `state`,
`died`, `last_assistant_text`, `settle`, `item_count`. There is no session
create and no prompt; `_create_session` and `_send_prompt` are bash. `Deps`
exposes no dispatch capability either. **The coordinator cannot start an
implementer today.**

So the split is:

| concern | owner | why |
|---|---|---|
| whether to revise, and the bound | **coordinator** (Python) | it is a judgement about a verdict and a journal count — the kind of decision this migration exists to move |
| dispatching, prompting, settling, re-deriving | **runner** (bash) | it already does all four, once; the loop wraps an existing sequence |

This is not the migration target — the target is the coordinator orchestrating
both. It is the honest split given what exists, and the plumbing moves with the
rest of `run_item` when it migrates. Building session dispatch in Python now
would mean two implementations of it until then.

**The tuple contract changes, explicitly.** `outcome` gains one value,
`revise`, which the runner acts on and never records. The scorecard still ends
`ready` or `failed`; `rounds` — a field that has always been null — finally
reports something observed.

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

**The RUNNER dispatches it** — a previous draft said the coordinator did, in
this section, while the ownership section above said the opposite. The
coordinator only judges.

The coordinator returns `revise` plus the data the runner needs, in the tuple:
the reviewer's blocking findings verbatim, and the round number. The runner
then records the revision, calls `_kernel_dispatch(vendor, implementer)` to
mint a generation, creates and prompts a session with the item, the PR and
those findings — the same shape that worked by hand for #740 and #750 — settles
it, and calls `derive` again.

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

Counted from the JOURNAL, and from the RIGHT fact. `transition_performed`
records `{"to": ..., "via": "record_review"}` and **not the verdict**, so every
accepted review looks identical there — counting those cannot tell a revision
from an acceptance.

The count is the number of `REVIEW_VERDICT` facts for this run whose
`payload["verdict"] == "request_revision"`. That fact is written by the kernel
after validation, carries the verdict explicitly, and is the same fact
`authz.py` itself reads when deciding whether a binding was approved.

From the journal and not a variable, so a coordinator that dies and is
re-driven gets no fresh allowance.

**But the count proves only what was ACCEPTED, and that is not the same as what
was requested.** `commands.py` validates a review, then bumps the version under
CAS, then appends `REVIEW_VERDICT` — in that order. A review can validate and
then lose the CAS as stale, producing no fact at all. The shell kernel adapter
is advisory besides, so a caller can see success after a refusal.

Therefore: **the runner must observe an accepted `REVIEW_VERDICT` carrying the
submitted command's causal id before it dispatches any repair work.** Not the
adapter's exit code, and not the absence of an error. If the fact is not there,
the revision did not happen and the run must re-derive rather than proceed on
the assumption that it did.

Default 2 and not 3 because the evidence supports it: #740 converged in 1,
#750 in 2, #722 had produced a new finding at every round and would have
exhausted any bound. A third round costs a full implement-plus-CI-plus-review
cycle (~25 minutes) for a case not yet observed to succeed.

### 6. Crash resumption

The loop holds no state of its own, but **the state name is not enough to
recover from** and the first draft assumed it was.

`reviewing` is reached from `record_review(accept)`, from
`record_review(reject)`, AND from `record_merge_outcome(failed)`. "Proceed to
merge or revise" cannot be derived from it. Worse, a crash after the external
review returned FAIL but BEFORE `request_revision` was recorded leaves no
journal evidence that a revision is owed, while an older accepted binding may
still be the latest verdict — so a naive resume would merge on it.

Recovery therefore reads HISTORY, not the state name:

| evidence | action |
|---|---|
| latest `REVIEW_VERDICT` is `request_revision`, no later `start_implementation` | dispatch the implementer |
| latest `REVIEW_VERDICT` is `request_revision`, a later `start_implementation` exists | settle-detect the in-flight implementer |
| latest `REVIEW_VERDICT` is `accept` AND binds the run's CURRENT output | proceed to merge |
| latest `REVIEW_VERDICT` is `accept` but binds a SUPERSEDED output | re-review; the approval is stale, which `validate_review` already refuses |
| latest `REVIEW_VERDICT` is `reject` | terminal, as today |
| a `record_review` command was REJECTED (stale CAS) and no matching `REVIEW_VERDICT` exists | the revision was NOT recorded; re-derive. Do not treat an older `accept` as current |
| run is `reviewing` with a current accept, no `MERGE_AUTHORIZED` | authorise, then merge |
| `MERGE_AUTHORIZED` present, NO merge effect journalled at all | authorised and never attempted; perform it. Do NOT re-issue `request_merge` — illegal from `merge_requested` |
| `merge` effect `intended` or `uncertain` | **the merge may ALREADY have happened at GitHub.** Halt and reconcile against the observed PR state; never re-execute. This is the halt path muesli #726 took on the first live run |
| confirmed `merge` effect present, no `record_merge_outcome` | **the merge HAPPENED.** Record the outcome from the observed merge commit; never re-execute the effect |
| `record_merge_outcome(merged)` recorded | done; close out |
| `record_merge_outcome(failed)` recorded | the merge failed, not the review; retry the merge, do NOT consume a revision |
| no `REVIEW_VERDICT` at all | derive from scratch |

**The external review result is not durable until its kernel command is
recorded.** A crash in that window loses the verdict and the item re-derives,
which costs a review and is correct — the alternative is acting on a verdict
the journal cannot evidence.

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

**It does not move session dispatch into Python.** The runner keeps dispatching
and settling; only the judgement moves. See "Who owns the loop".

## Acceptance

1. Every branch of `classify` under the new input, including
   `rounds_remaining == 0` reproducing today's behaviour exactly.
2. A FAIL with rounds remaining records `request_revision` and NOT `reject` —
   asserted on the command, since the two differ only in destination.
3. The revision count comes from `REVIEW_VERDICT` facts with
   `verdict == "request_revision"`, proven against a synthetic journal
   containing a MIX of accept, reject and request_revision verdicts plus a
   pre-crash revision — a test that only counted review transitions would pass
   a naive implementation, which is why the mix is specified.
4. A round produces a NEW artifact and head, and the merge is pinned to the
   LAST reviewed head — asserted by driving two rounds with different heads and
   checking what `--match-head-commit` receives.
5. `BIRCHER_MAX_REVISIONS=0` is byte-identical to today, so the switch is a
   real rollback.
6. Re-entry at every row of the recovery table, driven from journal history
   rather than a state name. The rows that must each have their own test,
   because each was missing from a draft and each does something different:
   crash after the external FAIL but before `request_revision`; a
   `record_review` that validated and then lost the CAS; an accept binding a
   superseded output; `MERGE_AUTHORIZED` with no confirmed effect; a confirmed
   merge effect with no recorded outcome (**must not re-execute**); an
   INTENDED or UNCERTAIN merge effect (**must halt and reconcile, never
   re-execute** — GitHub may already have merged it); and a failed merge that
   must not consume a revision.
7. The runner refuses to dispatch repair work unless an accepted
   `REVIEW_VERDICT` for the submitted command exists — asserted by driving a
   revision whose kernel command was refused and requiring no dispatch.
8. Mutation: making a FAIL record `reject` instead of `request_revision` must
   fail a named test; the run would sit in `reviewing` and never revise.
9. **A live muesli item that fails review is repaired and merged with no human
   routing the finding.** That is what this is for, and #722 is the standing
   counter-example: if it still exhausts the bound, that is the correct outcome
   and not a failure of the loop.
