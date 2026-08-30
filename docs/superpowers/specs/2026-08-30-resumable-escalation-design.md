# An escalated run must be resumable

**Status:** design, written before the code. It changes the run state machine
and the merge-authorization path, so a mistake here lets a PR merge on a
history the gate never checked.

## The problem, verified by execution

muesli PR #736 is `mergeStateStatus=CLEAN` with every required check green.
Nothing on GitHub blocks it. It cannot be merged by any automated path, and
neither can #737.

`record_run_outcome` moves a run to `ended` from ANY state. Escalation uses it,
so a run that gave up and a run that succeeded are indistinguishable
afterwards. `ended` is terminal: `request_merge` is legal only from
`reviewing`, so no merge can ever be authorized on that run again.

Recovery cannot route around it. `_kernel_adopt_run` selects `runs[-1]` —
the newest run whose id starts with the item code — **regardless of state**,
and mints a fresh run only when there is NONE. Run ids are `<code>-<timestamp>`,
so a re-queued item matches its own dead run. Executed against the live
journal, both codes select their `ended` run:

    i723-conformance-suite-rejects-the-...   candidates=1  state=ended
    i714-the-notes-list-refetches-only-...   candidates=1  state=ended

**So an escalated item cannot be re-queued and retried.** That is not an edge
case: escalation is the normal outcome whenever anything goes wrong, and three
of three live muesli runs escalated today.

## Why the obvious fix is forbidden

"Skip the dead run and mint a fresh one" does not work, and must not be made
to work.

A minted run stays at `queued` and its lifecycle drive is refused — and
`kernel-client.sh` says that is CORRECT and deliberate, because *"a fresh run
would present an empty history to a merge gate whose whole job is to check
history"*. An earlier version seeded `submit_spec`/`submit_plan` with a
synthesized blob so the caller would not meet those refusals, and it
**fabricated the history the merge gate exists to check**: a PR that never came
from the queue reached `merge_requested` with every command accepted and its
spec and plan both the string `adopted: <code> in <repo>`.

The refusals are the gate working. Any design that resumes work by
manufacturing the missing stages is this defect with better manners.

## The design

**An escalation is not an ending, and the fix is to stop recording it as one.**

An escalated run has a REAL partial history — a spec, a plan, an
implementation, often CI and a review. Resumption is legitimate precisely
because that history exists and can be re-checked. Nothing is synthesized.

### A distinct state, and the stage it came from

`record_run_outcome` gains a distinction it does not make today:

| outcome | state | meaning |
|---|---|---|
| `merged`, `closed`, `skipped` | `ended` | terminal, as now |
| `escalated`, `failed`, `timeout` | `escalated` | work stopped, history intact |

`ended` keeps its exact current meaning and remains unreachable-from. Only the
escalation outcomes land in the new state, and the fact records
`escalated_from: <the state the run was in>` so resumption has somewhere to
return to rather than a guess.

### `resume_run`, an audited command under CAS

    resume_run --run-id R --expected-version N

- Legal ONLY from `escalated`. Never from `ended`, `cancelled` or a live state.
- Returns the run to its recorded `escalated_from` state — the stage it
  genuinely reached, not a stage chosen by the caller.
- Appends a `run_resumed` fact carrying the from/to states and the resume
  count.
- Re-fences: a resumed run takes a NEW generation, so a stale actor from the
  previous attempt cannot act, and every idempotency key that embeds the
  generation becomes a genuinely new attempt rather than a replay.
- Refused if the run has unresolved uncertain effects. A halt is reconciled
  first, exactly as today; resumption never clears one.
- Bounded by `BIRCHER_MAX_RESUMES` (default 3) per run, recorded in the fact.
  Without a bound a permanently-failing item resumes for ever, and the count
  belongs in the journal so the limit is auditable rather than ambient.

**No gate is skipped.** A run resumed to `implementing` still needs its review
and its merge authorization. A run resumed to `merge_requested` — #714's case
— already HAS a recorded, accepted review and a `merge_authorized` fact; those
are real, and the merge effect they authorize is the one that failed to
execute. Resumption re-runs the effect, not the approval.

### Adoption selects a live or resumable run, never a terminal one

`_kernel_adopt_run` currently takes `runs[-1]` regardless of state. It becomes:

1. the newest run in a LIVE state → adopt, as now;
2. else the newest run in `escalated` → adopt AND `resume_run` it;
3. else (only `ended`/`cancelled` runs, or none) → mint, with today's
   deliberate refusals intact.

Rule 3 is unchanged behaviour and keeps the fabrication scar closed: a genuinely
new run still presents an empty history and is still refused.

### What this does NOT do

**It does not resurrect the two PRs already stranded.** #736 and #737 recorded
`ended` before this exists, and no migration invents an `escalated_from` that
was never observed. They need a human, and that is the honest cost of shipping
the escalation state late.

**It does not make escalation cheap.** A resumed run is still an item a human
was asked about. The resume bound and the journalled count keep that visible.

## Acceptance

1. Every escalation outcome lands in `escalated`; every terminal outcome still
   lands in `ended`. Asserted per outcome, not by sampling one.
2. `resume_run` is refused from `ended`, from `cancelled`, from a live state,
   past the resume bound, and while an uncertain effect is unresolved. Each
   refusal is its own test.
3. A resumed run returns to its RECORDED stage — a test resumes runs escalated
   from three different stages and asserts each lands where it left.
4. A resumed run takes a new generation, and an actor holding the old one is
   refused.
5. Adoption picks live over escalated over minting, with a test per branch, and
   a test that a run in `ended` is never adopted.
6. **The fabrication guard still holds:** a minted run still cannot reach
   `merge_requested`, asserted directly, so this change cannot be read as
   permission to seed history.
7. A live muesli item that escalates is re-queued and completes without a
   human touching the kernel.
