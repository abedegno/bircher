# The front half: from an issue to an approved plan

**Status:** design, before code. It adds the phase bircher does not have —
turning intent into a specification — and changes which states the kernel
recognises, so a mistake here lets implementation start on an artefact nothing
reviewed, or lets the model manufacture the human's approval.

## Why

Bircher automates from a spec, not from an intent. The two live merges it has
made (muesli #711 → PR #751, #722 → PR #752) were of issues that were already
fully specified when it received them; both were the *output* of grooming done
by hand. `skills/muesli-loop/SKILL.md` §2 stops on ambiguity by design: "write a
short spec and STOP: surface it for human approval". Ten of the forty scorecard
rows are that stop.

The capability was demonstrated once, by hand, on 2026-08-23: muesli #711 went
from a vague issue through grilling, a spec under eight rounds of cross-vendor
review, a plan under ten, and subagent-driven implementation to PR #729. It was
never wired into the runner, and it was shelved on cost. Its two findings that
drive this design: **spec and plan review out-yielded code review per round**,
and **two vendors find disjoint defect sets**.

The kernel already holds the skeleton. `queued → specified → planned` exist;
M1-5 (2026-08-24) built `grill.py` (model questions and human answers as
immutable facts, the human path reachable only from the operator's side),
`bundle.py` (the frozen issue snapshot) and `enqueue.py` (the single
transaction). None of it has a production caller. Two rules block using it:
`record_review` is legal only from `{implementing, reviewing}`
(`v2/kernel/authz.py:39`), and `request_revision` always lands in `planned`
(`authz.py:78`). The runner walks through `specified` and `planned` by
submitting the queue prompt as both spec and plan (`batch/run-queue.sh:4083`).

The 2026-08-23 kernel design deferred this deliberately: Milestone 1's front
end is *supervised* — grill, spec, plan, export a frozen bundle a human
inspects and enqueues — and the *autonomous* front end is listed under "Not in
Milestone 1". This is that design.

## The claim

A run whose input is an issue body — vague or not — reaches `planned` with a
spec and a plan that are content-addressed artefacts, each accepted by a
cross-vendor reviewer, each approved by the human where the run's policy says
so, with every question, ruling, verdict and approval an immutable kernel fact.
From `planned` the existing implementation path runs unchanged.

**Done means:** one genuinely vague muesli issue merged with zero human touches
after enqueue, under the `(model, {})` policy. Each word of that is
observable, or it is not a claim:

- *genuinely vague*: the issue is named in the plan before the run, and its
  body names no file, no function and no acceptance test. That is a reading,
  recorded ahead of time so it cannot be chosen after the fact.
- *zero human touches*: the run's journal holds no `human_answer`, no fact
  of decision type `human_ruling`, no `parked` fact, no `effect_reconciled`
  fact and no `reconciliation_required` halt. Every human path — the
  session, the CLI fallback, a retry after a lost verdict, and the
  reconciliation of an uncertain effect, which `effects.reconcile` records
  with `actor="human"` because that is what it is — writes one of those, so
  a touch that left no fact is a defect in this design, not a clean run. The
  last two are the ones the first draft forgot: a run that halted on an
  uncertain session prompt, was reconciled in the morning and then merged
  satisfied every other assertion and still needed a person. And one path
  writes no fact at all: a person typing into a live author session in a
  run that never parked. The loop records what its listings see (§4), but
  the facts alone cannot prove the absence of a message after the last
  listing, so the proof also **lists every author session of the run** and
  requires every user-role item in them to be one of the coordinator's own
  prompts — a `prompt_item` by id, or in the crash window by hash. That is
  an observation of the sessions, not a reading of the journal.
- *merged*: the PR's merge is recorded under this run id, not a re-enqueue.
- and the journal holds at least one `model_ruling`: an author that found
  nothing to decide on a vague issue did not grill it. This is a proof
  assertion, not a kernel guard — a guard would only manufacture questions.

## Design principle

The agent is used only for what cannot be enforced mechanically. Turning an
issue into decisions, writing a spec, writing a plan, reviewing either, and
ruling on findings are judgment. Freezing the input, hashing artefacts,
refusing a plan that is the spec, dispatching a reviewer, counting rounds,
enforcing a bound, refusing implementation before approval, and telling the
human's message from the model's are mechanism. Everything in the second list
is code in the kernel or the coordinator, never an instruction in a prompt.

## Decisions taken

Four, with the human, 2026-09-04/05.

1. **The human's involvement is per-run policy, not a fixed shape.** Some
   features the human wants to be grilled on first; others they will state at
   a high level and let the agents work out. Two independent knobs cover both
   (§1).
2. **The coordinator drives the phases**, as a generalised repair loop
   dispatching short single-purpose sessions — not one long lead session
   composing skills. The mechanical steps stay code; the model authors and
   reviews.
3. **The operator is out of scope.** The front half runs as the first phase
   of today's `run_item`; the trigger stays queue files. Scheduling, Projects
   polling and label triggers are their own spec, calling the same entry
   point.
4. **The human approves and corrects in the omnigent session**, with a CLI
   as the operator's fallback. Not GitHub comments.

Rejected: predicates on today's three states (`specified` would mean three
things and `recover.py` would have to compute which); a generic `(phase, sub)`
kernel state (rewrites `authz.py` under 1,052 tests at once). The kernel gets
explicit states; the coordinator's loop is what is generic.

## §1 Policy — the knobs, frozen

```
grill:      human | model                     default: model
gates:      subset of {spec, plan}            default: {spec}
max_rounds: integer 1..5, per phase and epoch default: 3
max_seats:  integer 4..40, per run            default: 16
```

- `grill=human`: the spec author must ask the human at least once and must not
  submit with a question unanswered. `grill=model`: the author rules on its
  own questions; each ruling is recorded with its reasoning and its stated
  cost if wrong, as the SDD ledger already does for plans.
- `gates`: the phases whose accepted artefact additionally needs a human
  approval before the run advances.
- `max_rounds`: reviewer-driven revisions per phase before the run parks for
  the human. Human rulings are not counted. Counted per *epoch* (§2, bundle
  revision): a revised issue starts its phases afresh.
- `max_seats`: the run-wide ceiling on author and reviewer dispatches across
  the whole front half. **It never resets** — not on a bundle revision, not
  on a human ruling, not on a resume. `max_rounds` bounds convergence;
  `max_seats` bounds spend. Without it, a reviewer that keeps returning no
  verdict, or a human who keeps correcting, spends a session per pass with
  every counter unchanged. The default is two phases × (1 + 3 revisions) × 2
  seats. Only a human `retry` ruling (§4) raises it, by one round at a time.

**Derivation.** Project default from the omnigent Project `config`, overridden
per issue by labels: `bircher:grill` → `grill=human`; `bircher:autonomous` →
`gates={}`; `bircher:gate-plan` → adds `plan`. The derivation is a pure
function in `policy.py` and it runs **inside the kernel, inside the creation
transaction**: `create_run` receives the fetched issue and the Project config,
and the kernel snapshots the one and derives the policy from both. The
`policy_frozen` fact records the result and what it was derived from —
`{policy, labels, project_config_hash}`. A caller cannot hand the kernel a
policy that disagrees with the labels, because it does not hand the kernel a
policy at all. A second `policy_frozen` on a run is refused.

What that does and does not make observed, exactly: the *derivation* is the
kernel's and a test binds it. The *inputs* — the issue as fetched and the
Project config — are asserted by the runner adapter, which fetched them with
its own credential; the kernel canonicalises and hashes what it is given, as
`enqueue` does today. The provenance table (§9) says so. Making the inputs
observed means the kernel fetching the issue from its own credential domain,
which exists for C8; that is a follow-up, not a claim here.

**Every guard below reads the fact, never the caller.** The model cannot change
the policy: it cannot write labels (kernel-published, C8) and it cannot write
the journal (Landlock, `BIRCHER_KERNEL_DB` has no default).

The four combinations the human named: `(human, {})` — grill me, then the
agents own it; `(model, {})` — here is the requirement, work it out;
`(model, {spec})` — the default; `(human, {spec, plan})` — supervised.

## §2 Kernel

### States

Today's `specified` and `planned` keep their meaning — *has an approved spec*,
*has an approved plan* — so the back half, `recover.py` and their tests are
untouched. Four states are inserted before each:

```
queued → spec_submitted → spec_accepted → specified
       → plan_submitted → plan_accepted → planned
       → implementing → reviewing → merge_requested → merged   (unchanged)
```

`spec_accepted` and `plan_accepted` exist only as gates. When a phase is not
in `gates`, the accepting review lands directly in `specified` / `planned` and
the gate state is never visited. The kernel decides that from the
`policy_frozen` fact.

Two things ride alongside the state without being states:

- **Epoch.** The number of `bundle_revised` facts on the run, zero at
  creation. Every question, answer, artefact, verdict and round count belongs
  to the epoch it was recorded in, and every guard that counts or compares
  them reads only the current epoch. A revision of the issue is a new
  beginning for the phases; it is not a new run, and it is not a new budget
  (`max_seats` counts across epochs).
- **The `parked` fact.** `*_submitted` alone cannot tell "a review is in
  flight" from "the reviewer returned nothing" from "the bound is exhausted
  and the human is needed". Those are different situations with different
  next actions, and a run that crashes between them must not be re-reviewed
  by default and must not wait silently either. So a park is a kernel
  command (`park`, below) that records `parked {phase, epoch, reason,
  session_id, cursor_item_id, findings_hash, verdict, reviewer, generation}`
  — everything the next pass needs, in the journal. The state does not
  change; the fact is what the loop reads first. A `parked` fact is
  *current* while it is newer than the run's latest transition and than the
  run's latest human fact (`human_answer`, or a `human_ruling` verdict —
  `grant_round` is one); either consumes it. A refused `approve` writes
  nothing and leaves the park current, which is the truth: the run is still
  waiting.

### Phase artefacts and lineage

Today the kernel holds one *current artefact* per run, set only by
`record_implementation_output`, and `validate_review` binds every verdict to
it (`authz.py:188`, `commands.py:262`). A spec review has nothing to bind to
under that rule and is refused before the transition table is consulted. So:

- the store holds a current artefact **per phase** — `spec`, `plan`,
  `implementation` — and `submit_spec`/`submit_plan` set theirs, recording
  `artifact_submitted {phase, epoch, hash, author}` where `author` is the
  dispatched actor of the submitting generation, resolved by the kernel from
  its dispatch record exactly as `reviewer_identity` is;
- `record_review` carries `phase`, its binding is checked against *that*
  phase's current artefact, and the `review_verdict` fact carries `phase`;
- `dispatch.py` gains the `author` role. `submit_spec`/`submit_plan` are legal
  only under an `author` dispatch, as `record_implementation_output` is only
  under `implementer`.

Every consumer of `review_verdict` names the phase it reads. Today they all
assume implementation — `recover.py` takes `verdicts[-1]` as the latest code
review; `observe.revisions_used` counts every `request_revision` on the run;
`kernel/projection.py` collects every verdict into one list; the runner reads
them through `coordinator.cli revisions`, whose `used|left|confirmed` tuple
`_revision_is_recorded` (`run-queue.sh:2698`) checks before any repair is
dispatched — that tuple comes from `observe.revisions_used`, so it is the
same read. `kernel-client.sh`'s `_kernel_verdict` (`:557`) reads no facts: it
is the string mapping from coordinator to kernel vocabulary, and the first
draft listed it as a consumer by mistake (§10 has the corrected list). Left
alone, two spec revisions would cost the implementation two
of its repair rounds, and a recovery run before any code review would act on
the plan's acceptance as if it were the code's. Each consumer filters on
`phase` explicitly; a reader of all phases is a defect.

### Commands

| Command | Decision type | Legal from | Goes to |
|---|---|---|---|
| `submit_spec(hash)` | — | `queued` | `spec_submitted` |
| `submit_plan(hash)` | — | `specified` | `plan_submitted` |
| `record_review(accept)` | `review_ruling` | `spec_submitted` | `spec_accepted`, or `specified` if `spec ∉ gates` |
| | | `plan_submitted` | `plan_accepted`, or `planned` if `plan ∉ gates` |
| | | `implementing`, `reviewing` | `reviewing` (unchanged) |
| `record_review(request_revision)` | `review_ruling` | `spec_submitted` | `queued` |
| | | `plan_submitted` | `specified` |
| | | `implementing`, `reviewing` | `planned` (unchanged) |
| `record_review(request_revision)` | `human_ruling` | `spec_submitted`, `spec_accepted` | `queued` |
| | | `plan_submitted`, `plan_accepted` | `specified` |
| `record_review(reject)` | `review_ruling` | `implementing`, `reviewing` | `reviewing` (unchanged) |
| `approve_artifact(hash)` | `human_ruling` | `spec_accepted` | `specified` |
| | | `plan_accepted` | `planned` |
| `grant_round` | `human_ruling` | `queued`, `specified`, `spec_submitted`, `plan_submitted` | no transition; raises the current phase's revision allowance and `max_seats` by one round. Refused from `*_accepted`: nothing there is stalled |
| `record_human_direction(phase, text)` | `human_ruling` | `queued`, `specified` | no transition; the next author round's findings (§4) |
| `issue_review_brief(phase, brief_hash, artifact_hash, context_bundle_hash, policy_version, base_sha[, spec_hash])` | — | `spec_submitted`, `plan_submitted` | no transition; records `review_brief_issued` under the calling generation, which must be a `reviewer` dispatch with no brief yet (§2 below the split) |
| `record_author_empty(session)` | — | `queued`, `specified` | no transition; records `author_empty` — the cause of the one retry session after an author turn that produced nothing (§3, §7) |
| `park(reason, …)` | — | every front-half state | no transition; records `parked` |
| `revise_bundle(issue)` | — | every front-half state | `queued`; epoch + 1 |
| `record_model_question` / `record_human_answer` | — | every front-half state | no transition |
| `create_run(issue, project_config)` | — | no run | `queued`, snapshotting the issue and writing `policy_frozen` in the same transaction |
| `start_implementation` | — | `planned` | `implementing` (unchanged) |
| `record_run_outcome`, `cancel_run` | — | from-sets gain the four new states | unchanged |

One rule behind every revision destination: **`request_revision` returns the
run to the state the artefact was submitted from.** Implementation → `planned`,
plan → `specified`, spec → `queued`.

And one invariant the table must keep: **`specified` and `planned` are
reachable only through a reviewer's `accept` of the current hash.** The human
approves *after* the reviewer, never instead. An earlier draft let
`approve_artifact` run from `*_submitted` so the human could unblock a run
whose bound was exhausted — which let a human `approve` manufacture the
cross-vendor acceptance the claim promises. At exhaustion or a lost verdict
the human's choices are corrections (`request_revision`, a fresh author
round), one more review round (`grant_round`), or cancellation. Not approval.

### Refusals

Each is a kernel check against facts the kernel holds. The coordinator may
observe the refusal; it may not pre-empt it.

| Refused | When |
|---|---|
| `submit_spec` | `grill=human` and the current epoch has no `human_answer` fact, or a `model_question` in the current epoch is newer than its last `human_answer`. Scoped to the epoch: a question made obsolete by a revision of the issue must not block the revised spec forever, and an answer given about the old issue must not stand in for grilling the new one |
| `submit_spec`, `submit_plan` | not under an `author` dispatch |
| `submit_spec`, `submit_plan` | the hash equals an artefact previously submitted **for this phase in this epoch** — a resubmission that did not change is not a revision |
| `submit_plan` | the hash equals the run's current spec hash. Phase-scoped identity above is what makes this a refusal of its own: the earlier "any artefact on the run" rule refused the same input first, so no test could show this one working |
| `submit_plan` | the bytes contain no `### Task` heading. A plan with no tasks is a spec with a different hash. A shape check, not a quality check |
| `record_review` | its `phase` is not the phase of the state it is issued from, or its binding hash is not that phase's current artefact |
| `record_review` | the reviewing actor is the actor that submitted the artefact under review — the rotation rule as a kernel refusal, from the dispatch records, not from the coordinator's intention |
| `record_review(request_revision, review_ruling)` | this phase, this epoch, already carries `max_rounds` reviewer-driven revisions (plus any `grant_round`). Human rulings are never bounded |
| `record_review(reject)` | from any front-half state. Bound exhaustion parks; it does not terminate |
| `record_review` from `*_accepted` | unless its decision type is `human_ruling` — only the human moves a gated run |
| `record_review` with a `review_ruling` | its generation carries no `review_brief_issued`, or the ruling's `phase`, `artifact_hash`, `context_bundle_hash`, `policy_version` or `base_sha` differs from that fact's. These are the dispatch checks of the split above; a `human_ruling` has no brief and passes none of them |
| `issue_review_brief` | the calling generation is not a `reviewer` dispatch, or already carries a `review_brief_issued`; `phase` is not the phase of the current state; `artifact_hash` is not that phase's current artefact in the current epoch; `context_bundle_hash` is not the current epoch's bundle hash, `policy_version` not the version in `policy_frozen`, `base_sha` not the run's base; for a plan brief `spec_hash` is absent or not the run's current spec hash; or `brief_hash` names no object the store holds. The kernel holds every value on the right-hand side; the caller can only supply the right answer |
| `record_author_empty` | the named session is not the one a `sess-create` of the current generation's actor delivered, or this phase and epoch already carry an `author_empty` newer than the last transition — the second empty turn is `RC_FAILED`, not a third session |
| `approve_artifact` | the hash differs from the phase's current artefact in the current epoch. The kernel holds the hash; the caller can only supply the right answer. (From `*_submitted` it is refused by the transition table alone — legal only from `*_accepted` — and `authorize` checks that table before any guard, `authz.py:345`; a "no reviewer has accepted" guard would never be the first refusal, so there is none. The human is still told why, §4) |
| any `author` or `reviewer` dispatch | the run's front-half dispatches already number `max_seats` (plus grants). Refused at `dispatch`, before a session exists, so the budget bounds sessions and not merely commands |
| `create_run` | the `run_id` exists and the retry's inputs differ — a different issue snapshot hash, base sha, repository or Project config hash. `enqueue`'s replay recomputes its answer from the *retry's* arguments and reports `replayed` for whatever was passed, so a retry with different inputs is told it succeeded while the journal holds the first call's. Replay only an identical request; refuse the rest as `NotReplayable` |
| anything writing `policy_frozen` after creation | there is no such command. `create_run` writes it in the creation transaction |

`record_human_answer`, `record_human_direction`, `approve_artifact`,
`grant_round` and the human form of
`record_review` are operator-side entry points in the sense `grill.py` already
establishes: a model session cannot reach the function, and there is no
parameter it can pass to become the human. `grill.py` appends its facts to
the store directly, which is enough for a fact with no transition; the ones
above that move the run (`approve_artifact`, the human `record_review`) and
the ones that change a bound (`grant_round`) must go through `execute`, so
the transition table, the version CAS and the halt gate apply to them. That
needs one thing `execute` does not have today: it reads the actor from the
generation's dispatch record (`commands.py:119`), and a human has no
dispatch. So `execute` gains a second entry, `execute_as_human(cmd)`, that
fixes the actor to `human` and skips the generation fence — its concurrency
control is `expected_version`, so two operators racing get one acceptance
and one `StaleVersion` — and is reachable only from the operator-side
functions. The recorded fact carries `actor="human"` and, for a verdict,
`reviewer_identity="human"`.

Which checks a human verdict passes is then a decision the code makes today
and this design must make explicitly. `validate_review` (`authz.py:150`)
binds the verdict to the current artefact and to observed `base_sha`,
requires the generation to have been dispatched as `reviewer`
(`authz.py:209`) and refuses a conflicted actor. Applied whole, it refuses
every human verdict — there is no reviewer dispatch to satisfy; exempted
whole, the most powerful verdict class in the system binds nothing. It
splits: the **binding** checks — the verdict word is legal, `phase` is the
phase of the current state, `artifact_hash` is that phase's current artefact
in the current epoch — apply to every verdict, human or model, and a human
correction that names a superseded hash is refused and told so in the session
(§4). The **dispatch** checks — role, independence, `base_sha`,
`context_bundle_hash`, `policy_version` — apply to `review_ruling` only:
they bind a model's attempt to what it was given, and a human correction
typed against the artefact on the screen has no such attempt to bind. A
human `record_review` therefore carries `{phase, artifact_hash, verdict:
request_revision, findings}` and nothing else.

"What it was given" has to be something the kernel holds, and today it is
not: `validate_review` parses `context_bundle_hash` and `policy_version`
(`authz.py:133-143`) and compares neither to anything (`:150-225` compares
only the artefact and `base_sha`), and the dispatch record is `{run_id,
generation, actor, role}` (`dispatch.py:61`, `schema.sql:82`) — so the
kernel knows what the current bundle *is* and nothing about what the
reviewer *saw*, and a coordinator that rendered a brief from the previous
epoch's snapshot while reporting the current hash is believed. So the brief
is journaled before the seat runs, by a command with a row in the table
above: the coordinator takes the `reviewer` dispatch, renders the review
brief **from the store, by hash** — the artefact bytes, the bundle, the spec
when reviewing a plan — PUTs the rendered brief, and calls
`issue_review_brief` under that generation through `execute`, which records
`review_brief_issued {phase, epoch, brief_hash, artifact_hash,
context_bundle_hash, policy_version, base_sha, spec_hash?}`. It is a command
and not a fact the coordinator appends the way `grill.py` appends its
questions (`store.append_fact`) because a fact appended directly passes no
check: the halt gate and the generation fence live in `submit`
(`commands.py:165`, `:171`) and the `facts` table carries no generation of
its own (`schema.sql:4-16`), so a brief written outside `execute` would be
recorded under nobody's authority, from any state, as often as the
coordinator liked. Through `execute` the kernel takes the generation from
the dispatch record and refuses the brief unless that generation is a
`reviewer` seat with no brief yet, unless the state is `*_submitted`, and
unless every field equals what the kernel holds — the phase's current
artefact, the current epoch's bundle hash, the version in `policy_frozen`,
the run's base, for a plan brief the current spec hash — and `brief_hash`
names an object in the store (the refusal rows below). The dispatch checks
on the ruling then read against that fact: a `review_ruling` under a
generation with no `review_brief_issued` is refused, and one whose binding
fields differ from the fact's is refused. The order is fixed: `dispatch`,
PUT, `issue_review_brief`, `omnigent run`, `record_review`; a crash after
the brief and before the seat leaves a fact under a generation that will
never rule, which costs the seat and nothing else, and a `revise_bundle`
while the seat runs puts the run at `queued` in a new epoch, from which
`record_review` is not legal at all (the table above) — the seat's verdict
is refused by the state table before any check reads the stale brief. What
remains asserted is that the session was fed those bytes (§9); what is now
observed is which bytes, so the §8 proof can open the stored brief and check
that the hashes embedded in it are the ones the fact names, and that the
verdict's `hash8` is the artefact's.

### Grill facts

`grill.py` changes from one answer per question to one answer per human
message: `human_answer` carries `{epoch, question_ids: [...], answer: text,
cursor_item_id}`, referencing every question open in the epoch when the
message arrived and the newest session item in the listing it was read from
(§4, the cursor). The
`submit_spec` guard counts facts, not text, in the current epoch only.
`model_question` under `grill=model` is still recorded — with the model's own
ruling appended as `model_ruling {question_id, ruling, reasoning,
cost_if_wrong}` — so the spec's decision ledger is in the journal, not only in
the artefact.

### Bundle revision

`revise_bundle` is today a function; it becomes a command. On resumption the
coordinator re-fetches the issue and hands it to the kernel; if
`is_relevant_change(old, new)` the kernel records `bundle_revised` with the
diff and moves the run to `queued`, epoch + 1. The next author round is
briefed with the prior artefact and the diff as findings. Not charged against
`max_rounds`: nobody's review was wrong. Charged against `max_seats`: the
seats it spends are real.

**What the snapshot must not see.** `bundle.py` today freezes every label and
every comment. Bircher writes both: the runner flips `bircher:queued` to
`bircher:running` the moment a run starts (`run-queue.sh:4053`), posts
`bircher: outcome=…` comments at the end, and under §3 the coordinator
publishes each approved artefact as a comment. As written, the first park would resume
into a "relevant change" made by bircher itself, and the run would reset to
`queued` on every pass, publishing another comment each time. So the snapshot
canon goes to version 2, excluding labels with the `bircher:` prefix and
comments the runner's `is_bircher_status` predicate drops. That predicate
(`run-queue.sh:2949`) matches four exact prefixes — `bircher: outcome=`,
`bircher-status:` and the two legacy sentences — and **not** the generic
`bircher: `, deliberately: it is a prefix match so that a human discussing a
marker still gets through (`:2933`). It therefore does not drop the
published-artefact comment this design adds, and the first draft's claim
that it "already" did was wrong; taken literally, the first park after an
acceptance would resume into bircher's own publication, reset the run to
`queued` and discard the artefact whose publication caused the reset. So the
predicate gains a fifth exact prefix, `bircher: published `, in both copies —
not `bircher: `, which would silence the human as well. The predicate moves
into `bundle.py` as the single definition; the bash copy stays for the
digest and both are tested against one fixture file of bircher-authored
comments, so neither can drift from the other unseen. The published artefact
comment starts with that exact line for the same reason.
The test that binds this: every mutation bircher makes to an issue — the label
flip, the outcome comment, the publication — applied to a fetched issue,
leaves `is_relevant_change` false.

No separate staleness refusal on `approve_artifact` or `record_review(accept)`
is specified: the transition to `queued` makes both illegal by state, and a
guard a state check always shadows cannot be bound by a test.

## §3 Coordinator — the phase loop

`coordinator.cli phases --run <id> --db <path> --server <url> --bundle-dir
<dir> --queue-dir <dir>`. Called by `run_item` after run creation and before
`_kernel_start_implementation`. Exit `0` with the run at `planned`;
`RC_PARKED`; `RC_FAILED`. Every iteration re-reads state and counts from the
journal; nothing survives in memory across iterations, and nothing survives a
crash that the journal does not already say.

```
loop:
  publish_owed(run)                              # §3 Artefacts: idempotent, every pass
  state ← kernel.state(run)
  if kernel.parked(run) is current              # §2: newer than the last transition and the last human fact
                              → human_pass(parked)                        (§4)
                                 nothing from the human   → exit RC_PARKED
                                 else                     → continue       # its facts moved the run
  queued | specified          → out ← author_round(phase)
                                 questions               → record them; park(grill)
                                 artefact                → submit(phase, put_artifact(artefact))
  spec_submitted | plan_submitted
                              → verdict, findings ← review_round(phase, hash)
                                 None                    → park(no_verdict)
                                 PASS                    → record_review(accept)
                                 FAIL, revisions left    → record_review(request_revision, findings)
                                 FAIL, none left         → park(bound_exhausted, findings, verdict, reviewer)
  spec_accepted | plan_accepted
                              → gate_prompt(phase)                        (§4)
                                 listing holds a human message → take it under the batch rules; no park
                                 else                    → park(gate)
  planned                     → exit 0
```

where `phase` is `spec` for `queued` and `spec_*`, `plan` for `specified` and
`plan_*`. A run already at or beyond `planned` exits `0` at once. Any
`author`/`reviewer` dispatch the kernel refuses for `max_seats` parks
`budget_exhausted`. `author_round` resumes the parked session when the park
reason was `grill` and the human has answered (§4); in every other case it
dispatches a fresh session.

The `parked` branch comes first because the state alone is ambiguous. A run at
`spec_submitted` with a current `parked {reason: bound_exhausted}` needs the
human, not another review; the same state with no current `parked` fact means
the last pass died before recording anything about its review, and a review is
the right next act — bounded by `max_seats`. Bound exhaustion records the
final FAIL and its findings in the `parked` fact (the findings as an artefact,
by hash), since `record_review(request_revision)` is refused at that point and
a verdict that lives only in a file is a verdict the journal never saw.

`park` is a kernel command; the sidecar `<queue-dir>/<code>.parked` (§5) is a
projection of the `parked` fact that the coordinator writes after the command
is accepted, for the runner's convenience. Lose it and it is rebuilt from the
kernel; it is never the truth.

**Idempotency keys** `phase:<run>:<phase>:<round>:<gen>:<kind>`, `round` read
from the journal as that phase's revisions used in this epoch + 1. Every key
carries the generation, per the rule that a key naming "one per run" is a
replay once a loop exists.

**Sessions are effects.** The runner already routes session creation and
every prompt through the effect journal — `_create_session` under
`sess-create:<run>:<gen>`, `_send_prompt` under `sess-prompt:<session>:<hash16>`
(`run-queue.sh:1063`, `:1086`) — intent recorded before the POST, an
unconfirmed result halting the run for reconciliation. The coordinator's
author sessions go the same way, through `coordinator/effects.perform_effect`
with `SESSION_CONTROL`: `sess-create:<run>:<gen>` and
`sess-prompt:<session>:<gen>:<hash16>`. The generation is in the prompt key
because the runner's key is not enough here: "Answered; continue." is sent to
one session once per human turn, and under a key of session and hash the
second send is a replay that posts nothing — the author never wakes. A crash
after the effect is journaled and before the sidecar is written is then a
reconciliation the journal already names, not an orphan session that a resume
duplicates. The generation does a second job there: a reconciled key is
*spent* — `perform` raises `NotReplayable` on it (`effects.py:201`), because
the recorded outcome describes the attempt that was reconciled and not the
next one — so a prompt reconciled as "not delivered" can only be re-sent
under a key that has moved. Here it always has: reconciliation halts the run,
a halted run refuses every command but `cancel_run` (`commands.py:165`), the
coordinator exits on the halt, and the pass that resumes it fences a fresh
generation (§5) before it sends anything. The coordinator never retries an
effect inside the generation that journaled it; an implementer who adds such
a retry gets `NotReplayable` on the first reconciled prompt, and that is the
kernel being right. (The runner's own `sess-prompt:<session>:<hash16>` has
no generation and carries this defect latently — a reconciled prompt whose
text recurs is unsendable — masked today because the repair loop varies its
prompt text per round. Out of scope here; noted so it is not rediscovered.)

But "re-sent under a fresh key" is only right when the effect did *not*
land, and today's reconciliation cannot say. `effects.reconcile` records
`effect_reconciled` with a free-text `resolution` and nothing else
(`effects.py:333`, `:376`), and clears the effect's `external_object_id`
(`:367`) — so after a human has reconciled a `sess-create`, the journal
holds neither whether a session exists nor which one, and the resumed pass
would create a second; after a reconciled prompt it cannot tell a delivered
prompt from a lost one. Two changes:

- **Reconciliation is typed, per key.** `reconcile` and `reconcile_many`
  (`effects.py:342`, `:291`) take a result for each key beside the text:
  `delivered {external_object_id}` or `not_delivered`, journaled on that
  key's `effect_reconciled` fact and stored on its effect row in place of
  the cleared id. The batch shape is kept because it is right — one human
  look, one CAS, every pending key of the run resolved in one transaction
  (`_kernel_reconcile <run> <resolution> <version> <key>...`,
  `kernel-client.sh:230`; `run-queue.sh:2259` passes every pending key) —
  but a single result over the batch is not: a batch can hold a
  `sess-create` and a `sess-prompt`, each with its own answer. So
  `_kernel_reconcile` grows `--delivered <key>=<id>` and
  `--not-delivered <key>`, every pending key of the run named exactly once,
  and refuses a call that leaves a key unnamed or names one with neither
  result; the reconciling human is the one person who looked, and the
  journal records what they saw for each thing they looked at. For a
  `sess-create` reconciled as delivered the id is the session the
  coordinator adopts.
- **Obligations are derived from intents, keys name attempts.** Every
  effect the coordinator performs carries an intent — for a session,
  `{sess-create, run, epoch, agent, cause}`; for a prompt, `{sess-prompt,
  session, phase, epoch, cause, sha256}`; for a publication, `{publish,
  run, phase, hash}`. `cause` is the id of the fact the effect acts on, and
  it is what makes two obligations in one epoch distinct: for a session,
  the fact that called for it — `run_created` for the first author
  session, the `review_verdict {request_revision}` for each revision
  round's fresh session, the `human_ruling` or `human_direction` for the
  fresh round a correction starts, the accepting verdict or the `parked`
  fact for a session created only to carry a gate or stall prompt because
  the author's is gone, the `author_empty` fact for the one retry after an
  empty turn (§7); for a prompt, the fact it answers — the session's own
  `cause` for the first prompt to a fresh session, the `human_answer`
  behind "Answered; continue.", the accepting `review_verdict` behind a
  gate prompt, the **`parked` fact** behind a stall prompt (never its
  reason and round: two `no_verdict` parks in one round are two facts and
  two prompts, and a cause that named the reason would send the second
  never), the `review_verdict {request_revision}` behind a revision brief.
  Without `cause` on the session intent, `{sess-create, run, epoch,
  agent}` is the same obligation for round one and round two of a phase,
  and the second fresh session — the one the revision mandates — reads
  satisfied by the first and is never created. An obligation is
  **satisfied** when the journal holds an effect with that intent in state
  `confirmed` or reconciled `delivered`; **owed** otherwise; and every fresh
  attempt is a fresh key, the generation being the part that moves. The
  intent is stored where the kernel already puts it — `intent_json` on the
  `effects` row, written in the transaction that journals the effect
  (`store.py:308-317`, `effects.py:233-236`), so the obligation exists
  before the attempt — but as its own `obligation` object beside `argv`,
  and the satisfied-query matches on `obligation` alone: `argv` carries the
  session id, the body and the max-time, which legitimately differ between
  two attempts at one obligation, and a query over the whole intent would
  find no prior attempt equal to the current one. Before sending anything
  the coordinator asks the journal whether the obligation is satisfied; a
  satisfied one whose follow-up fact is missing — `prompt_item` after a
  confirmed prompt, `parked` after a confirmed gate prompt, the adopted
  session after a confirmed `sess-create` — is **completed** (list the
  session, find the item by hash, record) and never re-sent.
  `publish_owed` (§3 Artefacts) is one instance of this rule, not a special
  case, and "Answered; continue." is sent once per human turn because its
  cause is a different fact each time, not because its key carries a
  generation.
Reviewer sessions stay on `omnigent run` as `review.py` does
today: a reviewer has no push and no human in its session, so a lost reviewer
session costs one seat and never correctness — stated as a cost residual, and
the seat is still counted at `dispatch`.

### Author round

A fresh session, agent `v2_author_<vendor>`: `v2_implementer`'s Landlock and
credential-proxy shape, no push allowance, a worktree at the run's base sha.
There are **two** author bundles, not one, because the rotation below needs
an author of each vendor and `v2_implementer` is one vendor's bundle:
`harness: claude-sdk` with model egress to `api.anthropic.com` only
(`agents/v2_implementer/config.yaml:11`, `:67`). `v2_author_claude` is that
bundle; `v2_author_codex` is the same confinement with `harness: codex` and
only the codex harness's model hosts in `egress_rules`, established by probe
the way the Anthropic host was. `agents/codex` is not a substitute: it runs
`sandbox: none` and allows push and PR creation. The coordinator's dispatch
actor is the vendor, and the `sess-create` intent names the bundle, so §8
can assert the two agree; a dispatch labelled `codex` that launched the
Claude bundle would satisfy the independence refusal with a fabricated
identity, and that assertion is what catches it. The codex harness has not
yet been run under `linux_landlock` with the credential proxy; that is a
prerequisite of the first codex-authored round, listed beside the turn
timeout in §7. It is briefed **from files**, never from a pasted history:

- the frozen issue snapshot;
- the policy (so the author knows whether it may ask);
- on a revision: the current artefact and the verbatim findings, via the same
  atomic findings file the repair loop uses;
- the phase's instructions: for the spec, grilling and the brainstorming
  design template; for the plan, writing-plans — composed from the upstream
  skills as the trial did, not forked.

Contract: write the artefact to `$BIRCHER_ARTIFACT_OUT` (a path inside its own
worktree, read by the coordinator from the host) and end the turn. Under
`grill=human` it may instead write `$BIRCHER_QUESTIONS_OUT` — each question
with the model's recommended answer — and end the turn with no artefact; the
coordinator records one `model_question` per question and parks (§4). Under
`grill=model` it writes its rulings to the same file, the coordinator records
them, and the turn continues to the artefact.

A grill conversation continues in the **same** session when the human answers:
the design tree lives there. A revision after a review verdict gets a
**fresh** session: a reader of findings, not a defender of its draft.

### Review round

`review.py` gains an artefact mode. The reviewer receives the bytes at the
hash — read from the store and checked against it, so a reviewer cannot be
handed something other than what will be approved — plus the issue snapshot
and the spec (when reviewing a plan), every one of them read from the store
by the hash the kernel currently holds. The rendered brief is PUT to the
store and `issue_review_brief` taken under the reviewer's generation before
the session runs (§2); a seat whose brief is refused does not run, and the
seat is still counted at `dispatch`. It returns `VERDICT: PASS|FAIL <hash8>` and findings through
`extract_verdict` extended to require the hash prefix in artefact mode — a
verdict that names another hash, or none, is `None`; nonce `{hash8}-g{gen}`
for the worktree path, as `review.py:153` does with the sha today; worktree
cleared before creation. `None` is not a soft PASS.

### Rotation

Round *r*'s reviewer is the vendor that did not author the artefact under
review. The vendor that reviewed round *r* authors the revision in round
*r+1*; the other reviews it. A reviewer never grades its own prescriptions —
the defect that memory records was accepted two lines from the text under
review.

The coordinator chooses the vendors; the kernel refuses the one pairing that
matters. `record_review` is refused when the reviewer's dispatch actor equals
the actor on the `artifact_submitted` fact it binds to (§2 Refusals) — the
kernel observes both from its own dispatch records, not from a claim in the
prompt. Every other rotation question — who authors after a park, who authors
the plan after the spec's last reviewer, what happens when a round returned no
verdict — is the coordinator's choice and only a cost, so the rule is stated
once: a phase's first artefact is authored by the vendor that did *not*
review the previous phase's last accepted artefact (for the spec, the
configured default author vendor), and after a park the next author is
whichever vendor the refusal above allows.

### Artefacts

Bytes go into the kernel artifact store; the hash is what is submitted. A
human-readable copy lands at `<bundle-dir>/<run>/<phase>-r<round>.md`.

At `specified` and `planned` the approved artefact is published to the issue
as a comment — a record, not an approval surface — through
`perform_effect(COMMENT)` under `publish:<run>:<phase>:<hash>:<gen>` with
intent `{publish, run, phase, hash}`. Nothing stores "publication pending":
the obligation is derived. `publish_owed(run)` is true when the run is at or
beyond `specified` (or `planned`) and the effect journal holds no effect
with that intent in state `confirmed` or reconciled `delivered` (§3 Sessions
are effects), and the loop calls it first on every pass, so a crash after
the accepting transition and before the comment lands is repaired by the
next pass without a fact that could itself be missed. The generation is in
the key and not in the intent for the reason given there: a publication
reconciled `not_delivered` is owed again under a key that has moved, and one
reconciled `delivered` is satisfied; under a generation-free key the first
would be `NotReplayable` on every pass forever. The comment's first line is
`bircher: published <phase> <hash8>`, which is what keeps it out of the
bundle (§2 Bundle revision).

## §4 Human interaction

Three kinds of park need the human: **grill** (`grill=human`, questions
pending), **gate** (`*_accepted`), and **stalled** — `bound_exhausted`,
`budget_exhausted`, `no_verdict`, `identical_resubmission`, all at
`*_submitted` or `queued`/`specified` with the loop unable to spend another
seat or round on its own. In each the last thing in the session is a turn
stating what is needed. For a grill that is the author's own questions. At a
gate the coordinator prompts the author session — or a fresh `v2_author_*`
session if it is gone — with the artefact and "Reply with the single word
`approve`, or give corrections." At a stall it prompts with the artefact,
the final findings if any, and "Reply with the single word `retry`, or give
corrections." — never `approve`, which the
kernel would refuse there. `retry` is one thing everywhere: `grant_round`,
which raises the phase's allowance and `max_seats` by one round and writes
the `human_ruling` that consumes the park. At `no_verdict` that grant is
slack the human chose to fund; the alternative — a retry that writes no fact
— would leave the park current and the loop asking again.

**The cursor is the newest item the coordinator has listed — never one it
wrote afterwards.** Every prompt the coordinator sends is a
`SESSION_CONTROL` effect (§3); after the POST is accepted the coordinator
lists the session's items, finds the user-role item whose content hash
matches the prompt it just sent, and records `prompt_item {session_id,
item_id, sha256}`. That fact is the *exclusion list*, not the cursor. The
first draft made it the cursor, and that loses a message: the pass that reads
answer H1 records it, re-prompts the author, and records the re-prompt P2 —
and a second answer H2 the human sent between the list and the re-prompt now
sits *before* the cursor, unread forever, while the author resumes on H1
alone. So the cursor is `cursor_item_id`, carried on every fact the
coordinator records from a listing — `human_answer`, `human_ruling`,
`record_human_direction`, `parked` — and set to the newest item id *in that
listing*, whatever its role. The cursor is the `cursor_item_id` of the
run's latest fact that carries one; the session's first item if none does.
On the next pass the coordinator lists items after it and takes, in order,
**every user-role item that is not one of its own prompts** — excluded by
item id where a `prompt_item` fact exists, by content hash where the crash
window left an effect journaled but no `prompt_item` (the hash is in the
effect's intent). In the sequence above the next pass lists after H1, finds
H2 and P2, drops P2 by id and reads H2. A pass that lists nothing human
records nothing and does not move the cursor; re-listing the same handful of
its own prompts is the price of never leapfrogging a person. The
discriminator reads the journal, not the memory of the process that parked.

Two more rules make that a guarantee rather than a habit. **The cursor moves
only over items that were read.** A fact may carry `cursor_item_id = X`
only if every user-role item at or before X that is not a coordinator prompt
is recorded by a fact — in this pass or an earlier one. The listing that
confirms a gate prompt P can hold a message H the human sent while the
author was still working; set the cursor to P from that listing without
reading H and H is behind the cursor forever. So the confirm listing is
discriminated like any other: H is taken under the batch rules below, and
`parked` is **not** written — a human who has already spoken is not asked
to. **Every listing is discriminated, parked or not.** The coordinator lists
after each prompt it confirms and at the end of each author turn, and each
time it takes every unread user-role item after the cursor, whatever the
run's state. A person can type into a live author session in a run that
never parks — the sessions are attachable in the UI by design — and the
author then acts on words no fact records. Under the batch rules that
message is a `human_ruling` or a `human_direction` by state, exactly as a
parked message is; in a `(model, {})` run it makes the §8 proof fail, which
is the proof being honest. What the last listing cannot see — a message
typed after the coordinator's final list — is caught by the proof's own
listing (§8), not by the loop.

- **grill:** the batch of human messages becomes one `human_answer {epoch,
  question_ids, answer}` fact, `answer` the messages concatenated in order,
  `question_ids` the questions open at that moment. The same session is
  re-prompted "Answered; continue." (a new effect, new generation in the
  key). The author may ask again — a new round, a new park — or write the
  artefact.
- **gate / stall:** the whole message, trimmed of surrounding whitespace and
  nothing else, compared case-insensitively to the single token: `approve`
  → `approve_artifact(hash)`, the hash being the kernel's current artefact
  for the phase; `retry` → `grant_round`. No punctuation is stripped:
  `approve?` is a question and `approve if CI is green` is a condition, and
  a rule that normalised the first to an approval would advance a gated run
  on a person's doubt. Either is accepted only when it is the **only**
  message in the batch; a batch holding anything else is corrections,
  concatenated in order — including `approve?`, which the author then
  answers as a finding, visibly, rather than the run advancing silently.
  The prompt says so: "Reply with the single word `approve`, or give
  corrections." From `*_submitted` or `*_accepted` they
  become `record_review(human_ruling, request_revision, findings = the
  text)`; from `queued` or `specified` — a stall before any artefact was
  accepted for submission — they become `record_human_direction(phase,
  text)`, a `human_ruling` fact with no transition that the next author round
  is briefed on as findings. It is not a `human_answer`: an unsolicited
  direction must not satisfy the `grill=human` guard, which requires that the
  author *asked*. Either way the loop resumes with an author round briefed on
  the human's words. An `approve` the kernel refuses — at `*_submitted`, or
  against a hash that is no longer current — is answered in the session with
  the refusal text and the run stays parked; the human is told what the run
  is waiting for, not silently ignored.
- **Fallback:** `kernel approve --run <id> --phase <spec|plan>`, `kernel
  grant-round --run <id>`, `kernel revise --run <id> --phase <spec|plan>
  --findings <file>` and `kernel direct --run <id> --phase <spec|plan>
  --text <file>`. The same commands, from the operator's shell.

A message the human sends **between** the coordinator's list and its record
— or between its record and its re-prompt — is not lost: the cursor is the
listing, so the message is after it whatever the coordinator posted since,
and it is read on the next pass. A human
answer to a question the author has since superseded is still recorded
against the ids that were open when it was read; the author sees it as part
of the answer text and rules on it. A human message whose text is
byte-identical to one of the coordinator's own prompts is excluded with it —
a residual, stated: it costs one ignored message, never a wrong transition.

**The honesty claim, exactly.** The NAS runs `OMNIGENT_AUTH_ENABLED: "0"`, so
`created_by` is `None` on every item and cannot distinguish the human from the
runner. A user-role item is therefore human-authored iff nothing but the UI
and the coordinator can POST into that session. The coordinator excludes its
own by item id, and by hash only in the crash window. The model is excluded
by the `v2_author_*` bundles' default-deny egress — `v2_implementer`'s
`egress_rules` (`config.yaml:42-67`) are the muesli fetch endpoints on
`github.com`, `GET api.github.com/repos/abedegno/muesli/**` and the model
host, none of which is the omnigent server — and the env-boundary tests pin
both bundles. Any other process on the docker network
is a **deployment residual, listed as asserted** in the provenance table.
Enabling omnigent accounts mode would make `created_by` observed; it is a
deployment change outside this spec.

Notification that a run has parked is the operator's job. The morning summary
lists parked runs with their reason.

## §5 Parking and resumption

**Park** = the kernel accepts `park(reason, …)` (§2) and the coordinator
exits `RC_PARKED` with the run in a durable front-half state and **no
`record_run_outcome`**. The kernel's outcome set is unchanged because the run
has not ended. After the command is accepted the coordinator writes the
projection `<queue-dir>/<code>.parked` = `{run_id, state, reason}` — enough
for the runner to find the run without a kernel query on every pass, nothing
the kernel does not also hold. `run_item` then keeps the queue file where it
is and records the scorecard row `outcome=parked` — runner vocabulary only.

**Resume** = the next pass over that queue item. `run_item` asks the kernel
for the open run carrying this item code — `_kernel_find_run`
(`kernel-client.sh:149`) today returns the newest run with the code as prefix
regardless of state; it gains an `open` filter (not `ended`, not `cancelled`)
— which is the truth; the sidecar is a hint that is rebuilt from the answer
when missing and overwritten when it disagrees. **Open is not the same as
resumable.** `run_item` resumes a run only in a front-half state — `queued`
through `plan_accepted`, or `planned` with no `start_implementation` taken.
A run that is open beyond that — `implementing`, `reviewing`,
`merge_requested`, `merged`, any state the back half owns — is one the
front half has finished with: `phases` would exit `0` on it and §6 would
launch a second implementer for work that may already be merged, its
`start_implementation` refused and the refusal swallowed. Such a run is
**skipped**: logged with its state, the queue file left where it is, no run
minted (the leak guard below), until the back half's own recovery
(`--recover-pr`, the deferred sweep, `_kernel_reconcile`) or `cancel_run`
moves it. It exports `BIRCHER_RUN_ID`,
re-fences through `_kernel_dispatch` for a fresh generation **in the
`operator` role, actor `runner`** — `_kernel_dispatch` takes both
(`kernel-client.sh:323`), and the role decides whether the fence is a seat:
§1 counts `author` and `reviewer` dispatches, so a resumption fence, like the
first pass's fence, is free, and a pass that reads the park and exits having
done nothing spends nothing. Dispatched as `author` it would spend a seat per
pass and the run's own scheduler could exhaust the bound it is waiting out.
A run that is halted for reconciliation is not resumed: the runner sees
`reconciliation_required` before fencing, logs it and moves on, until the
human has reconciled (§7). The resumed pass re-snapshots the
issue, submits `revise_bundle` if the change is relevant, and calls `phases`,
which re-enters at the loop's `parked` branch (§3) and from there at §4. The
runner's flock singleton (`run-queue.sh:8543`) is what keeps two passes from
resuming one run; its warn-and-proceed fallback is disabled for resumption —
without the lock, `run_item` refuses to resume and logs why. The lock is
runner-held, so this is an **asserted** residual (§9): the kernel's
generation fence turns a second resumer's commands into refusals, but the
session prompts a second resumer sends before its first refused command are
real.

The precedent for a run id reused across runner passes is narrower than it
looks, and it does not do what the sweep believes: `run-queue.sh:1936-1946`
re-fences a *recorded* run id — one whose `record_run_outcome` has already
been taken — but the re-fence supplies a generation, not a state.
`merge_ready_pr` returns `0` on a deferral as on a merge (`:1602`), so
`:4585-4586` record `record_merge_outcome(merged)` for a PR that is still
open — legal, because `_kernel_request_merge` at `:4582` has just put the
run at `merge_requested` (`authz.py:44`) — and `:4617` ends the run; the
sweep's merge effect then reaches `revalidate_merge`, which requires
`merge_requested` and refuses `ended` (`effects.py:107`, `authz.py:469`).
That refusal is raised directly, not through `shadow_or_raise`
(`mode.py:49-67`), so shadow mode records nothing for it; and `_effect` in
kernel mode is the execution path, not an advisory one
(`effect-adapter.sh:82`; `cli.py:246` turns `NotAuthorized` into
`RC_REFUSED`), so in either mode the sweep can never merge, against a
journal that already says `merged` then `ended`. That is a live v1 defect — a false
`merged` fact on every transient deferral — filed as bircher#93 and out of
scope here. Resumption re-fences an open run whose
outcome has not been taken, and what it borrows from the sweep is only
`_kernel_dispatch` with the old id and a new generation; nothing about the
state the run is in, and nothing about what the sweep records afterwards.

The resumable-escalation design (2026-08-30) is not used: it is superseded, and
its one destination (`implementing`) is wrong for a run parked at a gate. What
it established still binds: resumption inherits history, never judgement.
Here the artefacts and the human's rulings are history; a review made stale by
the issue changing is handled by `revise_bundle`'s transition to `queued`, not
by trusting the old verdict.

**Leak guard.** A crash between the kernel transition and the sidecar write
leaves an open run with no sidecar — and, since resumption asks the kernel
first, that run is simply resumed on the next pass. `run_item` never mints
while the kernel holds an open (non-`ended`, non-`cancelled`) run whose id
carries this item code. A genuinely dead run — one nobody wants resumed — is
ended by the operator with `cancel_run` (`authz.py:64`), which is the only way
a second run for the same item code comes to exist.

## §6 Handoff

From `planned`, `run_item` continues into the implementation path, but not
with the generation it holds. Today the implementer dispatch
(`run-queue.sh:4050`) precedes the ceremonial submits and
`_kernel_start_implementation` (`:4086`) in one shell with one generation.
`phases` sits between them, and every `author`/`reviewer` dispatch it makes
supersedes the shell's generation — a subprocess cannot update the parent's
`BIRCHER_GENERATION`, and when `phases` exits the current generation belongs
to the last plan reviewer. `start_implementation` under the shell's stale
generation is `OwnershipLost` (`commands.py:171`) and under the reviewer's is
`NotAuthorized` (`authz.py:331`); and `_kernel` returns 0 on either
(`kernel-client.sh:88-98`, advisory by design), so the runner would create
and prompt an implementer session the kernel never authorised. The seam is
therefore reordered: after `create_run` the runner fences **`operator`,
actor `runner`** — the resume fence of §5, not a seat — for the
`bircher:running` label effect and the `phases` call; after `phases` exits
`0` it dispatches **`implementer`** afresh, exports that generation, calls
`start_implementation`, and reads the state back through `coordinator.cli`:
anything but `implementing` is `RC_FAILED` before a session exists. The
session is created after the state is observed, not before as today
(`:4056`); the existing "session create failed" path records `failed` under
the implementer generation as it does now. Three further changes at the seam:

- The implementer's brief is the spec and the plan, read from the store by the
  hashes the kernel holds, plus the issue snapshot — not the queue prompt.
  The two directives the runner prepends today (`run-queue.sh:4028-4035`) —
  the implementer-vendor directive and the work-repo directive that
  overrides the bundle's literal `/workspaces/muesli` — are **still
  prepended**: they are per-run deployment facts, not artefacts, and a brief
  without them sends the implementer to the repository written in the
  bundle.
- The two ceremonial submits at `run-queue.sh:4083-4084` are deleted, and the
  "deliberate reuse" comment in `kernel-client.sh`'s `submit_plan` wrapper
  with them. Under §2 they would be refused.
- **Every `run_item` run goes through the front half.** A fully-specified
  issue costs one short author round and one review seat per phase. There is
  no bypass inside `run_item`, so there is no second path to keep honest.
  `--recover-pr` is not a second path: it adopts a run for a PR that already
  exists, and the kernel already leaves that run at `queued` and refuses its
  lifecycle drive (`kernel-client.sh:304-308`). Recovery journals the review
  and the merge of work whose front half happened elsewhere or never; it does
  not claim `specified`/`planned` and this spec does not change it. A
  recovered run can never satisfy the done criterion, which requires the
  phase transitions under the same run id.

`skills/muesli-loop/SKILL.md` §2's STOP-on-ambiguity is retired: ambiguity is
the front half's job, and the implementer implements a plan. The `.escalated`
mechanism stays for what it was built for — an implementer that cannot proceed
for reasons no plan foresaw. It stays a sidecar: it is written by the
implementer and consumed by the same `run_item` pass that launched it, and
the runner wipes every `.escalated` at startup (`run-queue.sh:8566`). That
wipe is safe only because the front half never resumes a run that has left
`planned` (§5): a runner crash between the implementer's write and the read
loses the reason, and the run it belonged to is then skipped, not re-launched
without it. Making implementation escalation a kernel fact — the shape every
other cross-pass state takes in this design — is back-half work, noted so
the sidecar is not mistaken for something resumption can read.

Per-task review seats and the whole-branch mutation sweep from the trial stay
out on cost. The whole-branch cross-vendor review and the repair loop remain
the code gate. The mutation sweep is the first follow-up once this is live.

## §7 Errors

| Case | Handling |
|---|---|
| Reviewer produced no parseable verdict | park `no_verdict`. No round consumed, one seat consumed; the human decides whether to retry or rule |
| Reviewer's verdict line names a different `hash8` | as no verdict: the artefact-mode verdict must echo the hash it graded (§3 Review round); a mismatch parks `no_verdict` and is in the log. The review nonce today identifies the worktree, not the verdict |
| Review bound exhausted (`max_rounds` in this phase and epoch) | park `bound_exhausted` carrying the final findings hash, verdict and reviewer. `retry` from the human = `grant_round` |
| Seat budget exhausted (`max_seats`) | the kernel refuses the dispatch; park `budget_exhausted`. `retry` = `grant_round`, which raises both |
| Author produced neither artefact nor questions | the coordinator records `author_empty` (§2) and retries once with a fresh session whose `sess-create` cause is that fact — a distinct obligation, so the retry is not read as satisfied by the session that produced nothing (§3 Sessions are effects); one seat each. A second `author_empty` in the phase and epoch is refused by the kernel and the loop exits `RC_FAILED` |
| `submit_*` refused as identical to a prior artefact in this phase and epoch | treated as a FAIL with findings "identical to the prior artefact"; one re-author, then park `identical_resubmission` |
| `submit_plan` refused for shape (no `### Task` heading) | as identical: findings "plan has no tasks"; one re-author, then park `identical_resubmission` |
| Crash after a review verdict was obtained, before `record_review` / `park` | nothing in the journal says a review happened; the next pass reviews again. Bounded by `max_seats`, which counts the lost seat because dispatch was journaled |
| Crash after a `SESSION_CONTROL` effect was journaled, before its result | reconciliation as for every effect: the next pass sees the pending effect and halts the run for `_kernel_reconcile`, exactly as the runner does today. Halted, the run refuses everything but `cancel_run` — `park` included — so the runner skips it until the `effect_reconciled` fact exists (§5), then resumes under a fresh generation and reads the fact's typed result (the two rows below) to decide whether the effect is still owed — the fresh generation is what lets a `not_delivered` prompt be re-sent (§3). Reconciliation is a human touch: `effect_reconciled` and the halt both count against the claim (§1) |
| Crash after a prompt was confirmed, before `prompt_item` / `parked` was recorded | the obligation is satisfied and its follow-up is missing (§3 Sessions are effects): the next pass lists the session, finds the item by hash, records `prompt_item`, and discriminates the listing like any other (§4) — for a gate or stall prompt it records `parked` only if the listing holds no human message after the prompt; a message there is taken under the batch rules and no park is written. Nothing is re-sent; a second identical prompt would wake the author a second time |
| Effect reconciled `delivered` | the obligation is satisfied; for a `sess-create` the delivered id is the session the next pass adopts. Nothing is re-sent |
| Effect reconciled `not_delivered` | the obligation is owed again and the next pass performs it under the fresh generation's key. Results are per key: a `_kernel_reconcile` call that leaves a pending key of the run unnamed, or names one with neither result, is refused and reconciles nothing |
| Session creation fails | `RC_FAILED`; `run_item` records `failed` as today |
| Kernel refusal the loop did not expect | `RC_FAILED` with the refusal reason logged; never retried blind. The transient-refusal design's classes decide what is retryable |
| `create_run` replayed with differing inputs | `NotReplayable`; `run_item` treats it as `failed` and the log names both input hashes. Never a silent second policy |
| Author turn exceeds `HARNESS_TURN_TIMEOUT_S` | a prerequisite, not a design point: the bircher runner's 480 s (gap 15) must be raised before the live proof. Spec authoring on a vague issue is one long turn |
| Codex harness under `linux_landlock` + credential proxy | the same kind of prerequisite: `v2_author_codex` (§3 Author round) has no precedent in a confined bundle. Proven on `bircher-smoke` before the first codex-authored round on muesli; until then a `(model, {})` run whose rotation calls for a codex author is a run this design cannot yet make |
| Human message arrives while no pass is running | it waits in the session. Nothing is lost; the next pass reads it |
| Human `approve` at `*_submitted` or against a stale hash | kernel refusal, replied into the session; the run stays parked (§4) |

## §8 Testing and proof

**Kernel.** A real-kernel test for every row of the §2 command table and
every row of the refusal table. Each refusal test carries its disagreeing
case — the input that passes only if the guard is gone, and the test is run
with the guard deleted to prove it is the *first* reason the command is
refused:

- `create_run` twice with the same key and a differing issue body or Project
  config → `NotReplayable`; the same inputs → the same `policy_frozen`.
- `approve_artifact` with a hash one byte off. (From `spec_submitted` it is
  the transition table that refuses, so that case is a state-table test
  with no guard to delete.)
- the `max_rounds+1`th `review_ruling` refused in an epoch while a
  `human_ruling` passes; after `grant_round` the next `review_ruling` passes
  and the one after is refused; after `bundle_revised` the count is fresh.
- `author` dispatch refused at `max_seats`; `grant_round` then allows one
  round's worth and not two.
- a byte-identical resubmission in the same phase and epoch refused; the same
  bytes after `bundle_revised` accepted; the same bytes in the other phase
  refused for shape or for equalling the spec hash.
- `submit_plan` with no `### Task` heading refused; with one, accepted.
- `record_review` whose binding names the other phase's artefact; whose
  reviewer actor equals the `artifact_submitted` actor (both from dispatch
  records, the test passing the same claim in the prompt both ways).
- `record_review` from `spec_accepted` with a `review_ruling`.
- `submit_spec` from a `reviewer` dispatch.
- `grant_round` from `spec_accepted` refused; `record_human_direction` from
  `spec_submitted` refused, from `queued` accepted, and a `human_direction`
  alone does not satisfy the `grill=human` guard on `submit_spec`.
- `park` current-ness: a `parked` fact older than the latest transition, or
  than the latest human fact, is not current; a refused `approve` leaves it
  current.
- `issue_review_brief` from an `author` generation refused, from a
  `reviewer` generation at `spec_accepted` refused, a second under the same
  generation refused; with `context_bundle_hash` one byte off the current
  epoch's refused, and after `revise_bundle` the old epoch's hash refused
  and the new accepted; a plan brief without `spec_hash`, or with the prior
  spec's, refused; `brief_hash` of bytes never PUT refused. Then the
  ruling: a `review_ruling` under a generation with no brief refused; with
  a brief whose `artifact_hash` (and each other field in turn) differs
  refused; the same ruling with a matching brief accepted; a `human_ruling`
  with no brief accepted. Each with the guard deleted, as above.
- `record_author_empty` naming a session no `sess-create` of that actor
  delivered refused; a second in the phase and epoch refused; after a
  transition, accepted again.

Mutation discipline as before: commit before each mutation, a mutation that
did not apply is not a result.

**Bundle.** `is_bircher_status` in `bundle.py` and the bash copy in
`run-queue.sh:2949` are both run against one fixture file of comment bodies
with expected booleans; the runner self-test and the pytest both fail on a
divergence. The binding test: a fetched issue, with each of the label flip,
the `bircher: outcome=` comment and a `bircher: published` comment applied,
leaves `is_relevant_change` false; a one-character change to the body leaves
it true.

**Coordinator.** Loop tests against a real journal with fake sessions, the
`test_repair_loop` pattern: every branch of §3 including the `parked`-first
branch, every park reason, resumption from every parked state, the
relevant-change path through `revise_bundle`, `publish_owed` after a crash
between `accept` and the comment, and the leak guard. The discriminator test
includes the coordinator's own prompt in the item list (by id, and in the
crash window by hash only) and asserts it is **not** taken as human — the
case a working fallback would shadow. The batch tests: `approve` alone,
`Approve ` with surrounding whitespace (approval), `approve?` and
`approve.` alone (corrections, no `approve_artifact` call),
`approve` followed by a correction in one poll (corrections), two grill
answers in one poll (one `human_answer`). The cursor tests: a human message
already in the listing that confirms a gate prompt is recorded and `parked`
is not written; a message typed into a never-parked run is recorded at the
next listing by state; no fact ever carries a `cursor_item_id` past an
unrecorded human item (asserted over every fact the loop wrote, against the
fake session's full item list). Every `review_verdict` consumer
named in §2 gets a test with a spec-phase FAIL followed by an implementation
PASS, asserting the consumer reads the implementation one.

**Proof assertions** for the done criterion (§ The claim): the journal of the
merged run holds no `human_answer`, `human_ruling`, `parked` or
`effect_reconciled` fact and was never halted (`reconciliation_required`
absent from the journal); holds at least
one `model_ruling`; holds `artifact_submitted` for spec and plan with
different hashes; every `sess-create` intent for an `author` generation
names the bundle of that generation's actor (§3 Author round); every
`review_ruling` has a `review_brief_issued` under its generation, the
brief that fact names is in the store, the hashes embedded in its rendered
bytes are the fact's, and the ruling's `artifact_hash` and `hash8` are the
brief's (§2) — the one place the proof opens what a reviewer was given; no
two `sess-create` obligations of the run are equal, and every confirmed
`sess-create` names a session the loop went on to prompt (§3, the two ways
a session obligation goes wrong); the issue is
in the pre-registered list and its body names no file, function or
acceptance test. And, observed from the sessions rather than the journal:
every author session named by a `sess-create` effect of the run is listed,
and every user-role item in it is a `prompt_item` by id or matches a
confirmed prompt effect's `sha256` — the assertion that a person did not
type into the run without parking it.

**Runner.** Self-tests for the sidecar as projection (missing → rebuilt,
disagreeing → overwritten), the `parked` row, the resume path's generation
re-fence, resume refused without the flock, `_kernel_find_run` skipping an
`ended` run, that `run_item` no longer calls `_kernel_submit_spec` with
the prompt, and the §6 seam in order: with a fake `phases` that dispatches a
reviewer and exits `0`, `start_implementation` is taken under a generation
the runner dispatched as `implementer` *after* `phases` returned, the state
read back is `implementing`, and `_create_session` is called after that
read — asserted by the call log — while a `phases` that exits `0` with the
run still at `plan_submitted` has its `start_implementation` refused and
reaches `RC_FAILED` with no session created.
`_kernel_reconcile` refused with a pending key unnamed, and with a key
given both results.

**Live, in order.**
1. `abedegno/bircher-smoke` under `(human, {spec})`: the human is grilled in
   the session, answers, is asked to approve, approves; the run reaches
   `planned` and merges.
2. muesli under `(model, {spec})` on a genuinely vague issue: the human's only
   touch is the approval; merged.
3. muesli under `(model, {})` overnight: zero touches after enqueue; merged.
   This is the done criterion.

**Review.** Codex reviews this spec before the plan, the plan before code, and
continues through implementation. The reviewing vendor rotates each round.

## §9 Provenance rows

New authorization inputs and how the kernel comes by them:

| Input | Source | Observed / asserted |
|---|---|---|
| spec hash, plan hash | kernel store, on `put_artifact` | observed |
| policy derivation | computed in the kernel inside the `create_run` transaction, from the inputs below | observed |
| policy inputs: issue labels, Project config | fetched by the runner adapter and passed to `create_run`; hashed into `policy_frozen` | **asserted** until the kernel fetches under its own credential (C8 follow-up) |
| bundle snapshot | fetched by the runner adapter at creation and on resume, canon v2 applied in the kernel | content **asserted**, canon and hash observed |
| artefact author | the `dispatch` record active when `submit_*` ran | observed |
| reviewer ≠ author | two dispatch records | observed |
| reviewer verdict on an artefact | the reviewer session's output | asserted, permanently, as every verdict is |
| what the reviewer was given | the rendered brief, PUT to the store and named by `review_brief_issued` under the reviewer's generation (§2) | contents observed as bytes; that the session was fed them **asserted**, as the runner's prompts are |
| seat count | `dispatch` records for `author`/`reviewer` | observed |
| author session creation and prompts | `SESSION_CONTROL` effects | observed |
| reviewer session creation | `omnigent run` in `review.py`, unjournaled | **asserted**; costs a seat, never a transition |
| human approval / correction | a user-role session item not sent by the coordinator, under a default-deny bundle | observed for the model and the coordinator; **asserted** for any other process on the docker network |
| human answer to a grill question | as above | as above |
| exclusive resumption | the runner's flock | **asserted**; the generation fence bounds what a second resumer can do to prompts already sent |

## §10 Where it lands

- `v2/kernel/authz.py` — four states, the command and refusal tables,
  `grant_round`, `park`, `revise_bundle`, `issue_review_brief`,
  `record_author_empty`; `validate_review` split into
  binding and dispatch checks, the dispatch checks comparing against
  `review_brief_issued` and kernel-held values rather than parsing and
  discarding (`:133-143`). `v2/kernel/effects.py` — `reconcile` and
  `reconcile_many` take a per-key typed result; the `obligation` object
  beside `argv` in the stored intent and the satisfied-query over it.
  `v2/kernel/policy.py` (new) —
  derivation from labels and Project config, the `policy_frozen` fact, the
  epoch- and phase-scoped counters. `v2/kernel/grill.py` — many-question
  answers, `model_ruling`. `v2/kernel/enqueue.py` — `create_run(run_id,
  base_repo, base_sha, issue, project_config)`, no spec or plan bytes,
  `NotReplayable` on differing inputs. `v2/kernel/bundle.py` — canon v2,
  `is_bircher_status`, `revise_bundle` as a command. `v2/kernel/commands.py`
  — `submit_spec`/`submit_plan` record `artifact_submitted` with the phase
  and author; `record_review` takes and checks `phase`. `v2/kernel/store.py`,
  `schema.sql` — per-phase current artefact, `parked`, `prompt_item`, epoch.
  `v2/kernel/events.py` — the new fact types. `v2/kernel/dispatch.py` — the
  `author` role.
- Every `review_verdict` consumer filters on phase. Today there are three
  outside the tests: `v2/coordinator/recover.py:165-176` (`verdicts[-1]`),
  `v2/coordinator/observe.py` (`revisions_used`, which the runner reads as
  `coordinator.cli revisions` → `_revision_is_recorded`, `run-queue.sh:2698`,
  before dispatching any repair), and `v2/kernel/projection.py:46`
  (`RunState.verdicts`, which gains a per-phase view).
  `batch/lib/kernel-client.sh`'s `_kernel_verdict` (`:557`) is a vocabulary
  mapping and reads no facts; `outcome.py` takes its verdict from the live
  review call, not the journal. Both are unchanged.
- `v2/coordinator/phases.py` (new) — the loop, `publish_owed`. `author.py`
  (new) — author dispatch through `perform_effect(SESSION_CONTROL)` and the
  artefact/questions contract. `human.py` (new) — the session reader,
  `prompt_item` recording, the discriminator and the batch rules. `review.py`
  — artefact mode with the hash-echoing verdict. `cli.py` — `phases`,
  `approve`, `grant-round`, `revise`, `direct`, `parked`.
- `agents/v2_author_claude/`, `agents/v2_author_codex/` (new bundles, one per
  vendor — §3 Author round). `skills/spec-author/`, `skills/plan-author/`
  — composed from the upstream skills.
- `batch/run-queue.sh` — run creation through `create_run` with the fetched
  issue and Project config, the `phases` call, the sidecar as projection,
  `parked`, the leak guard via `_kernel_find_run`'s `open` filter, resume
  refused without the flock, the two ceremonial submits deleted,
  `is_bircher_status` tested against the shared fixture.
  `batch/lib/kernel-client.sh` — wrappers for the new commands.
- `docs/design/ARCHITECTURE.md` §4 flow and the gaps table;
  `docs/design/provenance-table.md` rows from §9.

## Out of scope

- The operator: what starts a run, resumes a parked one, and notifies the
  human. Its own spec; it calls `phases`.
- Moving the implementation repair loop into the same Python loop (gap 4).
- Omnigent accounts mode.
- Per-task review seats and the mutation sweep (first follow-up).
- Multiple projects. The policy derivation reads a Project config so that the
  operator spec can populate it; nothing here depends on more than one repo.
