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

**An escalation is not an ending, and a resumed run re-earns everything that
can go stale.**

An escalated run has a REAL partial history — a spec, a plan, an
implementation, a pull request. Resumption is legitimate because that history
exists. What it must NOT inherit is any judgement about the current world.

### Resumption returns the run to `implementing`, and no further

A first draft returned the run to whatever stage it escalated from, so a run
that gave up at `merge_requested` would resume holding its `merge_authorized`
fact and re-run the merge effect. **That is unsafe, and the reason is not
obvious.**

`revalidate_merge` re-derives every input from kernel state, which sounds like
it would catch a stale approval. It does not catch this one. Its base check
compares `binding.base_sha` against `store.run_base_sha(run_id)` — **the run's
own recorded base against itself** — so it is tautological on exactly this
path, a fact `kernel-client.sh` already states in a comment about adoption.
And `--match-head-commit` pins the PR HEAD, not the base: main moving
underneath an unchanged head is invisible to both. A run escalated at
`merge_requested` on Monday could merge on Friday against a main it was never
tested with.

So resumption lands at `implementing`. The spec and the plan survive — they are
history, not judgement. The review, the CI observation and the merge
authorization do not: they are claims about a world that has moved, and the
run re-earns them against the world as it is now.

This also removes the need to record which stage a run escalated from. There
is one destination, so there is nothing to record, nothing written mid-failure
to be trusted later, and no crash window between recording the outcome and
recording the stage. The first draft needed `escalated_from` and a rule making
it kernel-derived inside the CAS transaction; the simpler destination deletes
the requirement instead of satisfying it.

### The outcome vocabulary, from the code rather than from memory

`_RUN_OUTCOMES` in `v2/kernel/authz.py` is
`{merged, ready, escalated, noop, skipped, failed, timeout}`. A first draft of
this table invented `closed` and omitted `ready` and `noop` — the whole set is
classified here, and a test generated FROM `_RUN_OUTCOMES` fails the build if
an outcome is ever added without a decision:

| outcome | state | why |
|---|---|---|
| `merged` | `ended` | terminal, and already checked against a confirmed merge effect |
| `noop` | `ended` | there was nothing to do |
| `skipped` | `ended` | deliberately not done |
| `escalated` | `escalated` | a judgement was handed to a human; the work survives |
| `failed` | `escalated` | the mechanism broke; the work survives |
| `timeout` | `escalated` | a budget expired; the work survives |
| `ready` | `escalated` | **the case that stranded PR #736** — the work is complete and the merge did not happen, which is precisely a resumable state, not an ending |

`ended` keeps its exact current meaning and stays unreachable-from.

### `resume_run`, an audited command under CAS

    resume_run --run-id R --expected-version N

- Legal ONLY from `escalated`; never from `ended`, `cancelled`, or a live
  state.
- Moves the run to `implementing` and appends a `run_resumed` fact carrying the
  resume count.
- Re-fences: a new generation, so a stale actor cannot act and every
  idempotency key embedding the generation becomes a genuinely new attempt.
- **Refused while any effect is uncertain.** A halt is reconciled first,
  exactly as today; resumption never clears one.
- Bounded by `BIRCHER_MAX_RESUMES` (default 3) per run, counted from the
  journal so the limit is auditable rather than ambient.

### Why resumption may be automatic here, having not been safe before

Adoption gains one branch: the newest LIVE run is adopted as now; failing that
the newest `escalated` run is adopted and resumed; failing that a fresh run is
minted, with today's deliberate refusals intact.

Automatic resumption was rejected in review while resumption could replay an
authorization — and rightly, because a retry would then re-enable a mutation
nobody had re-approved. Landing at `implementing` removes that: a resumed run
holds no authorization and can reach a merge only by earning a fresh review
against the current head and base. The bound stops an item looping, and every
resume is journalled.

The residual risk is honest and worth stating: an item that escalated for a
reason a human should have looked at will be retried up to three times before
it stops. It will not merge anything unearned while doing so, and each attempt
is visible in the journal.

### What this does NOT do

**It does not resurrect the two PRs already stranded.** #736 and #737 recorded
`ended` before this exists, and no migration invents a state that was never
observed. They need a human, which is the honest cost of shipping the
escalation state late.

**It does not touch `revalidate_merge`'s tautological base check.** That is a
real weakness, now written down, and it belongs in its own piece of work: the
fix is to bind an approval to an OBSERVED base rather than the run's recorded
one, which changes what a review means.

## Acceptance

1. Every outcome in `_RUN_OUTCOMES` is classified, with the test generated
   from that frozenset so a new outcome fails the build rather than defaulting.
2. `resume_run` is refused from `ended`, from `cancelled`, from every live
   state, past the resume bound, and while an uncertain effect is unresolved.
   Each refusal is its own test.
3. A run escalated from `merge_requested` resumes to `implementing` and its
   `merge_authorized` no longer authorizes anything — asserted by driving the
   merge effect and requiring a refusal.
4. A resumed run takes a new generation, and an actor holding the old one is
   refused.
5. Adoption picks live over escalated over minting, a test per branch, plus a
   test that a run in `ended` is never adopted.
6. **The fabrication guard still holds:** a minted run still cannot reach
   `merge_requested`, asserted directly, so this cannot be read as permission
   to seed history.
7. A live muesli item that escalates is re-queued and completes, with the
   journal showing a resume and a FRESH review rather than a replayed one.
