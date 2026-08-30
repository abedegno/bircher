# GitHub lags, so the merge path must wait and retry

**Status:** design, written before the code. The change touches effect
classification in the kernel, where a mistake means treating a
possibly-merged pull request as not merged.

## The problem, from a live run

muesli #726 → PR #735, 2026-08-30. The first merge attempt failed and the run
halted, needing a human to reconcile it before it could be merged at all. On a
system whose purpose is running unattended, that is disqualifying if it
recurs.

Two independent defects produced it.

### 1. The pre-merge gate watches a field that cannot see the blocker

`merge_ready_pr` waits for mergeability by polling `.mergeable`, which reports
CONFLICT state only — `MERGEABLE` / `CONFLICTING` / `UNKNOWN`. Branch
protection lives in a different field, `mergeStateStatus`: `CLEAN` /
`BLOCKED` / `BEHIND` / `UNSTABLE` / `DIRTY`.

muesli requires `review-gate`, which bircher does not post directly. The chain
is: bircher posts `bircher/cross-review`, a workflow reacts to that status
event, and it posts `review-gate`. Measured on #735:

    13:29:11  bircher/cross-review = success   (bircher posts)
    13:29:18  review-gate          = success   (the workflow reacts, +7s)

Inside that window the PR was `mergeable=MERGEABLE` (true — no conflicts) and
`mergeStateStatus=BLOCKED`. The gate saw MERGEABLE, proceeded, and GitHub
refused: `base branch policy prohibits the merge`.

muesli also sets `strict: true`, so `BEHIND` — a PR needing an update against
a main that moved — is a second state the current field cannot see.

This is ordinary GitHub eventual consistency. The existing code already waits
out one instance of it (mergeability is computed lazily: a first query returns
`UNKNOWN` and only triggers the computation; a second, seconds later, returns
`MERGEABLE`. Reproduced on four open PRs while writing this). It simply does
not wait for the other.

### 2. A definitive refusal is recorded as an unknown outcome

`kernel/cli.py::_executor` raises on any non-zero exit, and `perform` turns
**any** executor exception into `effect_uncertain` plus a run halt. Its own
docstring already says so:

> Every executor failure — a clean non-zero exit as much as a crash
> mid-flight — becomes `effect_uncertain` and halts the run. The effect STATE
> does not distinguish "ran and failed" from "outcome unknown".

So the halt is not the safety net working; it is a known gap being reached.
`merge_ready_pr` already retries the merge for ~30s, which would have covered
the 7-second lag — but the first failure halted the run, and every retry after
it was refused by the kernel rather than by GitHub. **The safety mechanism
defeated the recovery mechanism.**

"Base branch policy prohibits the merge" is not an unknown outcome. GitHub
refused before acting: it is *known* that nothing happened.

## The design

### A. Gate on `mergeStateStatus`, with exponential backoff

Poll both fields. Classify:

| state | meaning | action |
|---|---|---|
| `CLEAN` | ready | proceed to merge |
| `BLOCKED` | a required check is not satisfied YET | wait, backoff |
| `BEHIND` | strict mode; base moved | wait, backoff |
| `UNKNOWN` | not computed yet | wait, backoff |
| `UNSTABLE` | non-required check failing | wait, backoff |
| `DIRTY` | real merge conflict | defer now, retry-eligible=0 |

Backoff is exponential (2s doubling to a 30s ceiling) rather than the current
fixed 5s, and every sleep stays capped by `PREMERGE_DEADLINE_AT` — a deadline
check followed by an uncapped sleep crosses the deadline it just tested, which
this file has already been bitten by once (#71).

`BLOCKED` is deliberately NOT terminal. It is the state a PR sits in while the
gate it is waiting for is still being computed, which is exactly the case that
failed.

### B. A definitive refusal fails the effect without halting the run

A new exception, raised by the executor and understood by `perform`:

```python
class EffectRefused(Exception):
    """The command was REFUSED before acting: it is known that the effect did
    not happen. Distinct from an uncertain outcome, which is unknown."""
```

`perform` handles it separately from every other exception:

- mark the effect `refused` (a new state, not `uncertain`)
- append an `EFFECT_REFUSED` fact carrying the same `detail`
- **do not** enter reconciliation; the run is not halted
- raise `RefusedEffect` so the caller learns it failed and can retry

A `refused` key must be retryable without reconciliation — that is the entire
point — so `perform`'s existing-key check treats `refused` like a key never
seen, while `uncertain` and `intended` keep refusing as they do now.

### Recognition, and which way it fails

The executor decides, from the effect class and the command's stderr, using a
**small allowlist keyed by effect class**, with every entry justified by an
observation:

```python
REFUSALS = {
    "merge": (
        # Observed on muesli PR #735, 2026-08-30. GitHub evaluates branch
        # protection BEFORE merging, so this message means no merge occurred.
        "is not mergeable",
        "base branch policy prohibits",
    ),
}
```

**Everything unrecognised stays uncertain and still halts.** The default does
not move. This narrows the halt only where GitHub has said plainly that
nothing happened, and a new failure shape gets today's conservative treatment
rather than silent retries.

Two further guards, because a message allowlist is brittle by nature:

1. `merge_ready_pr` already asks GitHub whether the PR merged anyway
   (`_pr_merge_state`) after a failed attempt. That observation stays, and it
   is the real check — the allowlist only decides whether to halt, never
   whether the merge happened.
2. The allowlist is matched case-insensitively against stderr only, never
   against an exit code alone, so an unrelated non-zero exit cannot inherit a
   refusal classification.

### What is deliberately NOT done

**`gh pr merge --auto`**, which GitHub's own error message suggests, would
remove the race by handing merge timing to GitHub. Rejected: bircher would no
longer know when the merge happened, and the whole post-merge safety net —
watch main CI on the merge commit, revert on red — depends on holding that
commit at a known moment. Trading the merge-watch for a retry loop is a bad
exchange.

**Auto-reconciling old halts.** Runs already halted stay halted. This changes
what halts in future, not what a halt means.

## Acceptance

1. A unit test drives the real `mergeStateStatus` classifier over all six
   states and asserts wait-vs-proceed-vs-defer.
2. A test proves a refused effect leaves the run UNHALTED and the key
   retryable, and that an unrecognised failure still halts. Both mutation-run.
3. The backoff is bounded: a test asserts no sleep crosses the phase deadline.
4. A second live muesli item merges WITHOUT manual reconciliation. That is the
   acceptance that matters; the first one needed a human.
