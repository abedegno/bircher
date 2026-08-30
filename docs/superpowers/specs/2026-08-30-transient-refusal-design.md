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

## The design

### A. The refusal is decided by OBSERVATION, not by the error text

No allowlist. Nothing parses stderr for classification; it is kept only as
`detail` for humans.

When the merge command exits non-zero, the executor makes ONE structured
observation of the pull request — its state, its merge commit, and the head
that was merged — and classifies from that alone:

| observed | meaning | effect state | run |
|---|---|---|---|
| open, head == expected | the merge did not happen | `refused` | not halted, retryable |
| merged, commit at expected head | it DID happen, client lost the reply | `confirmed`, carrying the merge commit | not halted |
| merged, commit at a DIFFERENT head | landed unreviewed | `confirmed` + the existing unreviewed alarm | halts downstream as today |
| closed, not merged | someone closed it | `refused` | not halted; the caller sees a closed PR |
| observation failed, timed out, or ambiguous | unknown | `uncertain` | **halted, exactly as today** |

**A successful observation is a PRECONDITION for recording `refused`.** If the
observation cannot be made, the outcome is unknown and the run halts. The
default does not move: unknown still means halt.

This is also strictly better than the status quo for the second row — today a
merge that succeeded server-side while the client died is recorded as
uncertain and halts, when the observation could have confirmed it.

The observation uses the REST/GraphQL PR representation, not CLI wording, and
compares the merge commit's parentage against the expected head rather than
trusting a boolean.

### B. Retrying a refused effect: an explicit attempt model

The store keeps one row per `(run_id, idempotency_key)`. Facts are append-only.
So:

- A `refused` row is **updated in place** to a new attempt: `perform`
  journals a NEW intent with a NEW `effect_id`, and the row's current
  `effect_id` moves to it.
- History is not lost, because it lives in the facts, not the row:
  `effect_intended(eff_1)` → `effect_refused(eff_1)` →
  `effect_intended(eff_2)` → `effect_confirmed(eff_2)`. Every fact carries its
  `effect_id`, so the attempts are unambiguous.
- `uncertain` and `intended` keep refusing re-execution exactly as today. Only
  `refused` — a state reachable ONLY through a successful observation proving
  non-occurrence — is retryable.
- **Concurrency:** the retry runs under the same generation as the attempt it
  replaces, and generation fencing already rejects a write from a superseded
  owner (`OwnershipLost`). A retry under a NEW generation gets a different key
  and is a different effect. Two callers inside one generation is the
  pre-existing single-owner assumption, unchanged here.
- A retry budget bounds this: at most `BIRCHER_MERGE_REFUSED_RETRIES`
  (default 5) refused→retry cycles per key, after which the effect is left
  `refused` and the item defers to a human. Without it a permanently-refusing
  condition spins to the phase deadline every time.

### C. Bounded wait states, each with a transition policy

Polling `mergeStateStatus` is not enough on its own: `BLOCKED` is not a
promise that something will finish. Each state gets a bounded policy.

| state | interpretation | policy |
|---|---|---|
| `CLEAN` | ready | proceed |
| `BLOCKED`, with a required check PENDING | the gate is still computing | wait, exponential backoff, bounded by the phase deadline |
| `BLOCKED`, with no required check pending | durable (needs approval, or a required check is failing/absent) | defer NOW to a human; waiting cannot fix it |
| `BEHIND` | strict mode; base moved | attempt ONE update-branch (an existing routed effect), then re-poll; if still BEHIND, defer |
| `UNSTABLE` | a non-required check is failing | proceed — branch protection does not require it, and waiting cannot change it |
| `DIRTY` | real conflict | defer now |
| `UNKNOWN` | not computed yet | wait, backoff; after N polls with no answer, defer |

The `BLOCKED` split is the important one: it separates "a gate is being
computed" from "a gate says no", which is exactly the difference between the
#735 race and a PR that will never merge on its own. It is decided by reading
the required contexts and their states — machinery `_required_contexts_snapshot`
and `_commit_ci_lines` already provide.

Backoff is exponential (2s doubling to a 30s ceiling), and every sleep stays
capped by `PREMERGE_DEADLINE_AT`: a deadline check followed by an uncapped
sleep crosses the deadline it just tested, which this file has been bitten by
once already (#71).

### D. The combined classifier, fully specified

`mergeable` and `mergeStateStatus` are read from ONE response so they cannot
be mutually stale. Precedence:

1. PR not `OPEN` → not a merge candidate; report the observed state.
2. Head != the expected reviewed head → **defer immediately**; the merge must
   stay pinned to what was reviewed. Never proceed on a moved head.
3. `mergeable == CONFLICTING` → defer, regardless of `mergeStateStatus`.
4. Either field `UNKNOWN`/null → wait (both are lazily computed).
5. An enum value neither field's table knows → **fail closed**: defer, and log
   the unrecognised value by name. GitHub adding a state must not read as
   "proceed".
6. Otherwise dispatch on `mergeStateStatus` per the table above.
7. Any API error during the poll → treat as `UNKNOWN` and keep waiting inside
   the deadline; a failed lookup must never read as CLEAN.

### What is deliberately NOT done

**`gh pr merge --auto`**, which GitHub's error suggests, would remove the race
by handing merge timing to GitHub. Rejected: bircher would not know when the
merge happened, and the post-merge safety net — watch main CI on the merge
commit, revert on red — depends on holding that commit at a known moment.

**Auto-reconciling existing halts.** Runs already halted stay halted. This
changes what halts in future, not what a halt means.

## Acceptance

1. The combined classifier is driven over the full cross-product of
   `mergeable` × `mergeStateStatus`, including null, an unknown future enum
   value, an API error, a non-OPEN PR and a moved head. Fail-closed cases
   assert defer.
2. A refused effect leaves the run UNHALTED and the key retryable; an
   observation that FAILS still halts. Both mutation-run.
3. A merge that succeeded server-side while the client failed is `confirmed`
   with its merge commit, not `uncertain`.
4. The refused-retry budget is enforced, and the attempt facts carry distinct
   `effect_id`s so history survives.
5. No sleep crosses the phase deadline.
6. **A second live muesli item merges with no manual reconciliation.** That is
   the acceptance that matters; the first one needed a human.
