# The shaping phase — an epic becomes its slices before anyone specs it

*2026-09-09. Design for one new phase in the v2 front half. Binding authority
for its plan; argues from, and does not restate, the front-half design
(`2026-09-05-front-half-design.md`), which it extends. Where this document
names a mechanism without defining it, the front-half design defines it.
Revision 3, after cross-vendor rounds 1 (Kimi) and 2 (Codex); the
dispositions are the last two sections.*

## §0 The claim, and why the front half falls short of it

The front half's claim is a vague issue in and a merged pull request out. Its
live runs met that twice and failed it once, and the failure was the shape of
the issue rather than the loop: muesli #12 produced a 15 KB spec and a plan
naming forty-five files across nine tasks, which the back half turned into a
5,297-line pull request nobody could review and nobody merged
(`docs/design/front-half-live-log.md`, E6). Muesli #11, one piece of work,
converged in five rounds and merged (E7). The loop could see the difference in
its own artefacts long before a person did, and did nothing with it.

An epic is the most common kind of vague issue. Today it costs a person the
judgement the front half exists to take off them — noticing in advance that an
issue is several pieces, and slicing it by hand. **After this design, that
judgement is a phase.** Before the spec, an author decides whether the issue is
one piece of work or several; if several, it writes a slice plan, the other
vendor reviews it with dispositions, and the accepted plan is filed as child
issues that the scheduled wave then works one by one, each through the whole
front half. The epic stays open as an umbrella until its children close.

**The claim, extended:** an issue of any size in; for a one-piece issue, a
merged pull request out as today; for an epic, its slices filed as issues that
each meet the claim, and the epic closed when they have.

## §1 Policy, labels, and what "coarse" means

`PHASES` (`kernel/policy.py:16`) gains `slices` as its first member; `gates`
defaults to `{slices, spec}` and `bircher:autonomous` clears it as it clears
the others. `bircher:gate-plan` is unchanged. `max_rounds` and `max_seats` bound
the shaping phase's rounds and seats as they bound the others; a run that
slices spends at most one reviewer seat per round in this phase and none on a
one-piece verdict.

Two labels join the vocabulary the pipeline sets, and preflight refuses a repo
that lacks them (`_preflight_labels`): `bircher:slice`, carried by every child
the phase files, and `bircher:sliced`, carried by a parent whose children are
filed. A child inherits its parent's *policy* labels — `bircher:grill`,
`bircher:gate-plan`, `bircher:autonomous` — so an autonomous epic yields
autonomous slices; it does not inherit `bircher:queued`, which it is given, nor
`bircher:running` or `bircher:sliced`, which are the runner's.

**The definition of coarse, which this document is the source of.** The brief
carries it and the reviewer grades against it; it lives here because no other
text in the repository states it:

> A slice is a coarse, coherent, independently reviewable capability: a few
> related files, on the order of one to four hundred lines, that deliver a
> working sub-feature a reviewer can see working end to end. It is not an
> atomic plan-step — not a migration, a single struct, one helper, one store
> method or one endpoint on its own. Every slice is a full pipeline cycle,
> an author, a reviewer, CI and a merge, and the cross-vendor review is
> weaker on a lone fragment than on a slice that does something. Aggregate by
> layer or capability, three to five slices to an epic; a plan that sliced an
> issue into its plan-steps has recreated the problem.

## §2 Kernel

### States

`create_run` lands a run in **`shaping`**, not `queued`. `queued` keeps its
meaning — the state from which a spec round starts — and every state after it
is unchanged. `_PHASE_OF_STATE` maps the three shaping states and `sliced` to
phase `slices`; `phase_of` therefore derives the phase for every new command
as it does for the old ones, and a review of the slices cannot be recorded
against the spec.

| From | Command | To | Notes |
|---|---|---|---|
| — (no run) | `create_run` | `shaping` | unchanged otherwise: snapshot, `policy_frozen`, base sha |
| `shaping` | `record_one_piece` | `queued` | the model ruling; the run proceeds as today |
| `shaping` | `submit_slices` | `slices_submitted` | the artefact, hashed, PUT to the store |
| `slices_submitted` | `record_review(accept)` | `slices_accepted` | the reviewer's verdict, bound as every verdict is |
| `slices_submitted` | `record_review(request_revision)` | `shaping` | the reviewer's; one revision destination, as every phase has |
| `slices_accepted` | `record_review(request_revision)` as `human` | `shaping` | **the human's correction at the gate.** `_review_destination`'s human clause (`authz.py:190`) today returns `queued` for `spec` and `specified` otherwise; it gains a `slices` case returning `shaping`, and a test that a correction at the slice gate does not land in `specified` |
| `slices_accepted` | `approve_artifact` | `sliced` | the human gate, when `slices ∈ gates` |
| `slices_accepted` | `advance_ungated` | `sliced` | the coordinator's transition when `slices ∉ gates`; refused when it is |
| `sliced` | `record_slice_filed`, `record_filing_complete`, `record_slice_closed` | `sliced` | no transition; the filing and closing facts (below) |
| `sliced` | `record_run_outcome(sliced)` | `ended` | recorded by the sweep when filing is complete and every slice of the plan is closed (§3) |
| `shaping`, `slices_submitted`, `slices_accepted` | `revise_bundle` | `shaping`, epoch + 1 | unchanged rule; a revised epic is shaped again |
| `sliced` | `revise_bundle` | **refused** | its children are the work now (ruling 7) |
| the three shaping states, and `sliced` | `park`, `record_human_direction`, `dismiss_human_item`, `cancel_run` | as today | the front-half vocabulary applies |

**Existing commands whose from-sets gain states.** The front-half design's
tables list each command's legal states explicitly, and the code enforces them
(`authz.py:99-154`); every one below is a stated change, so no implementer
discovers it as a refusal:

| Command | Gains | Why |
|---|---|---|
| `record_turn_ended` | `shaping`, `slices_submitted` | the shaping turn and the review turn end like any other |
| `record_author_empty` | `shaping` | the empty shaping turn and its one retry |
| `grant_round` | `slices_submitted` (with a current park), `shaping` (with a current park) | the failure table's "the person grants a round" |
| `issue_review_brief` | `slices_submitted` | the review round renders its brief through it |
| `record_review` (reviewer) | `slices_submitted` | the verdict |
| `record_review` (human) | `slices_accepted` | the correction, destination `shaping` |
| `approve_artifact` | `slices_accepted` | the gate |
| `record_model_question` | — | **not** extended: refused under a `slices` generation (§4) |

`sliced` is a **front-half state the loop works in and then exits from**, as
`planned` is today: `_LOOP_STATES` includes it, the loop's pass at `sliced`
performs the owed filing (§3) and exits `OK`, and every state past it belongs
to nothing — the run waits for its children. It is open — not `ended`, not
`cancelled` — so `_kernel_find_run … open` adopts it rather than minting a
second run for a re-queued parent. It ends only when its children have.

**Deciding, and changing one's mind, within an epoch.** A rejected slice plan
returns the run to `shaping`, and the next author — or a person, by `direct`
— may rule it one piece instead. That is a supported path, not a fault, so
the rule is stated: `record_one_piece` is legal from `shaping` whether or not
a slice plan was submitted earlier in the epoch, and refused from any state
where a slice plan is *accepted* — `slices_accepted` and `sliced` — where the
decision has been taken. The epoch's final decision is therefore whichever is
newer: a `shape` ruling newer than every `artifact_submitted` of phase
`slices` in that epoch, or a `slice_filed`. §6's fourth assertion reads it
that way.

The outcome set `_RUN_OUTCOMES` (`authz.py:249`, checked at `:971-973`) gains
**`sliced`**. `record_run_outcome(sliced)` is legal only from `sliced`, under
an `operator` dispatch, and only when the kernel can see a `filing_complete`
fact for the current epoch and, **for every slice of the accepted plan** — the
plan it holds under the phase's artefact hash and parses as below — one
`slice_closed` in that epoch. Iterating the filed children rather than the
plan was refused in review as a vacuous-truth hole: an epic that crashed
before filing anything would have closed as complete.

### The artefact: a slice plan

A slice plan is bytes the kernel holds under its hash, like a spec. Its grammar
is fixed so the kernel can refuse a malformed one from the bytes, as it refuses
a plan with no `### Task` heading today (`authz.py`, `_check_submit`) — a
shape check, not a judgement:

```
# <title of the slice plan>

<preamble: why this issue is several pieces — free text>

## Slice 1: <title>
Scope: <three to six sentences>
Non-goals: <one line>
Depends on: none

## Slice 2: <title>
Scope: ...
Non-goals: ...
Depends on: 1
```

Parsed as lines: a `## Slice N: <title>` heading opens slice N; within it,
exactly one line beginning `Scope:` (the paragraph runs to the next labelled
line), one beginning `Non-goals:`, one beginning `Depends on:` whose value is
`none` or a comma-separated list of slice numbers. Slice numbers are 1..N in
order with no gaps. Everything before the first heading is the preamble and is
not parsed. The parser is one function in the kernel, `slices.parse(bytes)`,
used by the refusals here, by the outcome guard above, by the filing step, by
the child renderer and by the proof, so there is one reading of a plan and not
five.

`submit_slices(artifact_hash)` is refused when:

| Refused when | Why |
|---|---|
| fewer than two or more than five slices | one slice is a one-piece issue misfiled; six is the atomic-step slicing §1's definition forbids |
| any slice lacks a title, a `Scope:`, a `Non-goals:` or a `Depends on:` | a slice without non-goals grows into its siblings; one without a scope is a title |
| a `Scope:` under three sentences or over six, by full stops | shorter is a title, longer is a spec — the slice's own spec phase decides the rest |
| a dependency names a slice not in the plan, names itself, or the dependency graph has a cycle | the children are filed in dependency order (§3); a cycle has no order |
| the run's `policy_frozen` fact records `bircher:slice` among the issue's labels | **a slice is not sliced.** The frozen *bundle* cannot carry it — the canon strips every `bircher:` label as state, not input (`bundle.py:90-92`) — but `policy_frozen` records the issue's labels unfiltered at creation (`policy.py:89-96`), and that is the observable the refusal reads. Depth is one by construction, not by a counter the coordinator carries |
| the hash equals a plan previously submitted for this phase in this epoch | identical resubmission, as every phase |
| the calling generation is not an `author` dispatch, or its actor is not the vendor of the round's session | as `submit_spec` |
| a `human_direction` of the run is newer than the calling generation's dispatch | as `submit_spec` |

The scope-length refusal is the one judgement-shaped rule in the table, and it
is kept because it is observable and because both live failures of the plan
phase were documents that grew: a bound on the slice's own text is the cheapest
way to keep a slice plan from becoming five specs. The refusal names the
sentence count it found.

### `record_one_piece(reasoning, cost_if_wrong)`

A model ruling with a transition. Legal from `shaping` under an `author`
dispatch whose actor is the vendor of the round's session, after the turn is
observed ended and its stop satisfied (§3 *The turn's end is a fact* applies to
this command as to every output-recording command). Refused when:

| Refused when | Why |
|---|---|
| either string is empty or whitespace | a ruling without its reasoning is the "MUST state" placeholder; its cost-if-wrong is what a person reads when the spec phase later shows the issue was an epic after all |
| a `human_direction` of the run is newer than the calling generation's dispatch | **the same journal-order guard `_check_submit` gives `submit_slices` (`authz.py:582-598`)**; without it an interrupted author's ruling would advance the run to `queued` after a person had directed "slice it along these lines". Human commands do not supersede the author's generation by themselves; the guard is what does |
| a `shape` ruling already exists in this epoch | one decision per epoch by ruling; a second is an identical resubmission |
| the state is `slices_accepted` or `sliced` | the decision has been taken (above) |

Records `model_ruling {question_id: "shape", ruling: "one piece", reasoning,
cost_if_wrong}` — the existing fact and payload shape
(`commands.py:240-247`) — and moves the run to `queued`.

### `advance_ungated`

The coordinator's own transition from `slices_accepted` to `sliced` when the
policy holds no slice gate. Legal only under an `operator` dispatch of the
coordinator and only when `slices ∉ gates`; refused otherwise, so a coordinator
cannot skip a gate the policy set. Its fact is `artifact_advanced {phase:
slices, hash}`. The spec phase has no equivalent because its ungated path is
`record_review(accept)` landing directly on `specified`; the shaping phase
separates the two because filing children is an effect with an obligation
(§3), and the transition that authorises it must be its own fact.

### `record_slice_filed`, `record_filing_complete`, `record_slice_closed`

Every fact has an issuing command with legal states, a role and refusals, and
these three are no exception.

`record_slice_filed(slice, effect_key)` is legal from `sliced` under the
coordinator's `operator` dispatch. It names the slice number and the
idempotency key of the `issue_create` effect that filed it; the kernel reads
the created issue's number from that effect row's confirmed
`external_object_id` — **observed from the server's answer, never asserted by
the coordinator** — and refuses when the slice is not in the accepted plan,
when the effect is not confirmed or reconciled `delivered`, when its
obligation does not name this run, this epoch, this slice and this plan hash,
or when this slice already has a `slice_filed` in this epoch. Its fact:
`slice_filed {epoch, slice, issue, title, parent, plan_hash}`.

`record_filing_complete()` is legal from `sliced` under the coordinator's
`operator` dispatch, and refused unless the kernel can see, for the current
epoch, a `slice_filed` for every slice of the accepted plan **and** a
satisfied effect for every filing obligation the plan implies: one
`slice_dependency` per dependency edge, the `umbrella` comment and the
`umbrella_label` swap. It is the kernel's statement that nothing about filing
is still owed; the generator (§5) queues a `sliced` run until it exists, and
the sweep touches a run only once it does. Its fact:
`filing_complete {epoch, plan_hash, children: [numbers]}`. Refused a second
time in the same epoch.

`record_slice_closed(slice, closed_at, state)` is legal from `sliced` under the
sweep's `operator` dispatch, only after `filing_complete`. Refused for a slice
with no `slice_filed` in this epoch, or one already closed. Its fact:
`slice_closed {epoch, slice, issue, closed_at, state}` where `state` is
`merged` or `closed`, as the sweep observed it.

All three facts carry the epoch. Since `revise_bundle` is refused from
`sliced`, a run that has filed anything is in its final epoch, and the guards
above read "this epoch" without ambiguity.

### The effect class: `issue_create`

`EffectClass` gains **`ISSUE_CREATE`**. Creating an issue mints work, a
different blast radius from editing or labelling one, and it is the first
externally visible mutation of the pipeline that produces something another
run will consume. Its contract rule: argv is exactly `gh issue create --repo
<repo> --title <title> --body-file <path> --label <l>…`, the body file written
by the executor from the intent's `body.text` after the contract check — the
body never rides argv, for the reason the session events rule gives — and the
confirmed row's `external_object_id` is the created issue's URL, from which the
number is parsed as `from_gh` parses comment ids. The effect-site inventory
(`docs/design/effect-site-inventory.md`) gains the site; the routing
detector's table is **not** changed, because the call lives in the kernel
executor and not in `run-queue.sh`, and that table lists shell call sites only
and reds on an entry with none.

Two more contract changes, stated so they are not discovered as refusals: the
`ISSUE_OR_LABEL` contract (`contract.py:230-238`) gains a rule admitting `gh
api -X POST /repos/<repo>/issues/<n>/dependencies/blocked_by -f issue_id=<id>`,
the write half of the endpoint the queue generator already reads
(`is_unblocked`, `issues-to-queue.sh:20-24`); and the new obligation kinds —
`slice_issue`, `slice_dependency`, `umbrella`, `umbrella_label`,
`umbrella_close`, `parent_close`, six in all — each get a delivered-form in
`_delivered_value` (`effects.py:389-433`), so typed reconciliation of an
uncertain one is possible; without that, §3's crash rows are promises the
kernel cannot keep.

The umbrella comment is a `comment` effect; the label swaps and the parent's
close are `issue_or_label`. None of those is new in kind.

### Facts

| Fact | Written by | Payload |
|---|---|---|
| `artifact_submitted` | `submit_slices` | as today, `phase: slices` |
| `review_verdict` | `record_review` | as today, `phase: slices` |
| `model_ruling` | `record_one_piece` | `question_id: "shape"`, `ruling`, `reasoning`, `cost_if_wrong` |
| `human_ruling` | `approve_artifact` | as today, `phase: slices` |
| `artifact_advanced` | `advance_ungated` | `phase: slices`, `hash` |
| `slice_filed` | `record_slice_filed` | `epoch`, `slice`, `issue`, `title`, `parent`, `plan_hash` |
| `filing_complete` | `record_filing_complete` | `epoch`, `plan_hash`, `children` |
| `slice_closed` | `record_slice_closed` | `epoch`, `slice`, `issue`, `closed_at`, `state` |
| `run_ended` | `record_run_outcome(sliced)` | as today, `outcome: sliced` |

## §3 Coordinator

### The shaping round

`run_loop` gains one branch: at `shaping` it runs `shape_round(ctx)`, the
author round's twin. Same seat and vendor choice (`choose_author_vendor`, phase
`slices`), same `run_turn` under the one rule, with **two watched paths**:
`bircher/shape.md` and `bircher/artifact.md`. At the turn's end:

- `shape.md` present and parsing as a ruling (below): `record_one_piece`, the
  round returns `one_piece`, the loop continues and the next pass starts the
  spec round from `queued`. An `artifact.md` beside it is ignored and moved
  aside, as a draft beside a questions file is under the grill: the ruling is
  read first.
- `artifact.md` present, no ruling: `submit_slices`, the round returns
  `submitted`, and the next pass runs the review round.
- neither: the empty turn, `record_author_empty` and one retry, as the author
  round.
- a `shape.md` that does not parse: the empty turn. A ruling with no
  reasoning is not a ruling.

**The ruling file's grammar**, labelled lines so nothing is split on
punctuation the reasoning may contain:

```
Ruling: one piece
Reasoning: <one or more lines>
Cost if wrong: <one or more lines>
```

The first non-blank line must be exactly `Ruling: one piece`; `Reasoning:`
and `Cost if wrong:` each open a block that runs to the next label or the end
of the file; both blocks must be non-empty after trimming; leading whitespace
on any line is ignored; any other non-blank line before the first label, or a
missing label, makes the file not a ruling. The parser is `slices.parse_ruling`,
beside `slices.parse`.

The brief (`author_brief`, phase `slices`) carries: the frozen snapshot, the
policy, §1's definition of coarse verbatim, the slice-plan grammar and the
ruling grammar, the instruction that a slice plan names no files, tasks or
acceptance tests, and the two ways to end the turn. On a revision it carries
the previous plan, the findings and the dispositions requirement, as every
phase does. The skill is `skills/shape-author/SKILL.md`, a sibling of
`spec-author`.

### The review round

`review_round` at `slices_submitted` is the review round at `spec_submitted`
with the phase name changed: template 2 brief, prior findings from the second
round, severities, the verdict rule, dispositions. One paragraph joins the
brief for this phase, telling the reviewer what it is grading against §1's
definition: whether each slice is a capability a reviewer could see working
end to end, whether any slice is a bare step of another, whether the
dependencies are real, and whether the union covers the issue with nothing
invented. Not the design of any slice, which is its own spec phase's work.

### The gate

At `slices_accepted` with `slices ∈ gates`, `stall(ctx, "gate", …)` as for the
spec: the park, the notice on the parent issue with the plan attached, the
prompt in the carrier session opening with the address to the model
(`human.NOT_FOR_THE_AGENT`, added to the front half on 2026-09-09 for finding
13 of the live log). `approve` moves it to `sliced`; corrections are the human's
`record_review(request_revision)`, destination `shaping`, and the next round's
findings; `cancel` cancels. `retry` is refused here as at the spec gate. With
`slices ∉ gates`, the same pass records `advance_ungated` and continues.

**How a pass reaches `sliced` on the gated path.** The person's `approve` is
read by a wave the schedule launched, whose queue generator queued the run
because it held a current park (§5 of the front-half design as extended on
2026-09-09). `human_pass` takes the approval, the run is `sliced`, and the
loop's next iteration — `sliced` being in `_LOOP_STATES` — performs the filing
below and exits. On the ungated path the same pass continues from
`advance_ungated` into the filing. In both cases a crash before filing is
complete leaves a `sliced` run without a `filing_complete` fact and no park;
§5 states how the next wave finds it.

### Filing the children

`file_owed(ctx)` runs in the loop beside `publish_owed`, and is the last thing
a pass at `sliced` does. It is **idempotent per obligation, not per slice**:
every step below checks its own fact or effect and performs only what is
missing, so a pass that resumes after any crash repairs exactly the remainder
and never re-performs or re-records anything. For each slice of the accepted
plan, in dependency order (a topological order; ties by slice number):

1. **The create.** Obligation `{kind: slice_issue, run, epoch, slice: n,
   parent, plan_hash}`, key `slice:<run>:<n>:<generation>`. If no satisfied
   `issue_create` carries this obligation, compose the child with
   `slices.render_child(slice, parent, hash8, sibling_numbers)` — title: the
   slice title; body: the marker line `Slice n of #<parent> (bircher shaping
   <hash8>)`, a blank line, the scope, `Non-goals: …`, and `Depends on: #<x>,
   #<y>` with the siblings' numbers already filed, or `Depends on: none`;
   labels: `bircher:queued`, `bircher:slice`, and the parent's inherited
   policy labels — and perform it. The renderer is the one function the proof
   re-renders with (§6).
2. **The fact.** If this slice has no `slice_filed` in this epoch,
   `record_slice_filed(n, key)`, which the kernel checks against the confirmed
   row. A satisfied create with its fact already recorded is skipped here and
   *not* skipped for step 3: a crash between a child's dependency links leaves
   its fact recorded and its links owed.
3. **The links.** For each dependency, obligation `{kind: slice_dependency,
   run, epoch, slice: n, blocker: m}`; performed if unsatisfied, as an
   `issue_or_label` effect posting the blocked-by link.

Then, each once, if unsatisfied: the umbrella comment, obligation `{kind:
umbrella, run, epoch, plan_hash}` — `bircher: sliced <hash8>` followed by one
line per child, `- #<number> Slice n: <title>` — and the label swap
`bircher:running` → `bircher:sliced` on the parent, obligation `{kind:
umbrella_label, run, epoch}`. Last, `record_filing_complete()`, which the
kernel accepts only when every obligation above is satisfied. An uncertain
effect anywhere halts the run (§3 *Sessions are effects*) for reconciliation,
which lists the parent's children by the marker line to say what exists. A
satisfied obligation is never re-performed — `perform` replays a confirmed key
— which is why nothing in this design depends on a replay to undo a person's
label edit (§5).

`bircher: sliced ` joins `BIRCHER_STATUS_PREFIXES` in both copies of the
predicate, so the umbrella never freezes into a bundle.

### The sweep

Each firing of the scheduled wave, before it generates the queue, runs
`sweep_sliced(ctx)` over every open run in state `sliced` **that carries a
`filing_complete` fact** (from the journal, by `open_run_ids` and the fact),
skipping a run that is halted or holds pending effects — a dispatch on such a
run is refused, and the halt is a person's to reconcile. For each such run,
under a fresh `operator` dispatch: for each slice in the accepted plan with no
`slice_closed`, read the issue; if closed, `record_slice_closed` with the
state the pull request shows. When every slice of the plan is closed, three
owed effects in order — the comment `bircher: sliced complete` naming each
child and its final state (obligation `{kind: umbrella_close, run, epoch}`),
the parent's close (obligation `{kind: parent_close, run, epoch}`; a parent a
person already closed is left as it is, the effect's precondition being the
open state the sweep observed), then `record_run_outcome(sliced)`. Under the
parent run's own generation, so the journal holds the story from epic to
closure. A child abandoned keeps its parent open; that is the honest state,
and the notice a person sees on the parent is the umbrella comment naming the
child that never closed. A parent a person closes by hand while children are
open stays a `sliced` run in the journal and is swept each wave; when its
children close, the completion comment is posted and the outcome recorded, the
close being skipped as above.

## §4 Human interaction

Nothing new in kind. The gate takes `approve`, corrections, or `cancel`. A
`direct` at `shaping` displaces a live shaping turn and starts the next round
with the direction as its findings — "this is one piece, do not slice it" or
"slice it along these lines" — which is how a person overrides the shaper
without editing the issue; the direction guard on both `submit_slices` and
`record_one_piece` (§2) is what makes the interrupted author's output
un-recordable, so the direction is not overtaken by the turn it interrupted.
The park notice names the phase `slices` and says what to type, as every
notice does. A `grill` park cannot occur in this phase: the shaper asks no
questions (§3), and `record_model_question` is refused under a `slices`
generation.

A revised *epic* — a person editing the issue after its children are filed —
is not re-sliced: `revise_bundle` is refused from `sliced`. What a person does
instead is stated in §9.

## §5 Runner

Five changes, two of them to `run_item`:

- `_preflight_labels` checks `bircher:slice` and `bircher:sliced` beside
  `running` and `escalated`.
- **`run_item`'s resume gate** (`run-queue.sh:4318-4328`) resumes a run only
  in an explicit list of front-half states and escalates any other as beyond
  the front half. The list gains `shaping`, `slices_submitted`,
  `slices_accepted` and `sliced`; without that, every resume of a parked
  shaping-phase run is escalated before the loop is called.
- **`run_item`'s post-loop branch.** After `phases` exits `0` the runner today
  dispatches an implementer, calls `start_implementation` — legal only from
  `planned` (`authz.py:86`) — and records `failed` when the state is not
  `implementing` (`run-queue.sh:4451-4465`). A `sliced` run exiting `0` would
  take that path and be scored as a failed implementation. The branch gains a
  case: state `sliced` after the loop records the scorecard row `sliced`,
  naming the children from `slice_filed`, keeps the queue file out of
  `processed` as a parked item is kept, and returns without dispatching
  anything or ending the run. The self-test pins both the resume list and
  this case.
- `issues-to-queue.sh`'s journal sweep queues, beside open runs with a current
  park, **open runs in `sliced` with no `filing_complete` fact in the current
  epoch** — the crash-anywhere-during-filing case — so the next wave's pass
  reaches `file_owed`, which repairs whatever is owed. A `sliced` run with
  `filing_complete` is not queued; it is the sweep's, not the loop's. Whether
  a slice *file* exists is never the test; the kernel's fact is.
- The wave calls `coordinator.cli sweep-sliced --db … --server … --repo …`
  once per firing, before queue generation.

`run_item`'s label effect, `queued` → `running`, fires on every pass that
resumes a run, generation-keyed; a `sliced` parent that a person re-labelled
`queued` is therefore adopted, re-labelled `running` beside its `sliced`
label, worked by a loop that has nothing owed and exits through the new
branch, and left carrying both labels until the sweep closes it. Nothing is
re-sliced.

## §6 Proof

`prove_front_half` gains four assertions, run over a parent and its children
together (`--children` reads the `slice_filed` facts and proves each child's
run as an ordinary run), all through `slices.parse` over the plan the kernel
holds:

1. **The children are the plan, body and all.** The accepted plan's slices and
   the `slice_filed` facts correspond one to one, in order, in the run's final
   epoch; and each child issue, fetched live, has the title and the body that
   `slices.render_child` produces for its slice from the parsed plan, the
   parent's number, the hash8 and the siblings' filed numbers — compared
   whole, not by marker and title alone, so a filing that gave every child the
   first slice's scope, or dropped the non-goals, fails here.
2. **The dependencies are the links.** Every `Depends on:` in the plan is a
   blocked-by link on the child, fetched live, and the child has no other.
3. **The umbrella names the children.** The parent's `bircher: sliced` comment
   for this plan hash lists exactly the numbers in `slice_filed`, and there is
   exactly one such comment.
4. **One decision per epoch.** In each epoch, either there is a `slice_filed`
   and no `shape` ruling newer than the accepted plan's `artifact_submitted`,
   or there is a `shape` ruling newer than every `artifact_submitted` of phase
   `slices` in that epoch and no `slice_filed`. A rejected slice plan followed
   by a one-piece ruling is the second case and passes; a ruling followed by
   a filing, or a filing beside a newer ruling, fails.

Under mode `zero`, no park at the slice gate; under mode `approval`, exactly
one `human_ruling {approve}` at it is admitted beside the spec gate's. Every
assertion is exercised by a planted defect in the tests, as §8 of the
front-half design requires of its own.

## §7 Failure table

| Situation | What happens |
|---|---|
| Shaper writes both `shape.md` and `artifact.md` | the ruling is read; the artefact is ignored and moved aside before any re-prompt |
| Shaper writes a six-slice plan | `submit_slices` refused; the refusal is the next round's findings, as the grill refusal is |
| Reviewer rejects three times | `bound_exhausted`, the park, the notice: the person grants a round (legal at `slices_submitted` with a park), corrects, or cancels — or directs "one piece" |
| A slice plan is rejected and the next author rules one piece | legal; the run proceeds to `queued`; the epoch's decision is the newer fact (§2), and the proof reads it so |
| A person directs during a live shaping turn | the turn is displaced; its ruling or plan is refused by the direction guard whichever it was; the direction is the next round's findings |
| A child is itself an epic | its shaper cannot slice it (`policy_frozen` carries `bircher:slice`); it proceeds as one piece and its spec phase does what it can; the reviewer's findings on an oversized spec are the signal a person reads |
| Crash after `sliced`, anywhere before `filing_complete` | obligations unsatisfied, no park, no `filing_complete`; the generator queues the run; the next pass's `file_owed` performs exactly what is missing — a create, a fact, a link, the umbrella or its label — and records completion |
| Crash between a create's confirmation and its `record_slice_filed` | the effect is confirmed; the next pass's step 1 finds it satisfied, step 2 records the fact, step 3 checks the links |
| Crash between a child's `slice_filed` and its dependency links | the fact exists; step 2 skips; step 3 performs the missing links |
| Crash between the last link and the umbrella, or between the umbrella and its label | the umbrella or the label is performed; then completion |
| Crash between the completion comment and the parent's close | the comment's obligation is satisfied and is not re-posted; the close's is not and is performed; the outcome follows |
| Two runs shape the same parent | impossible by the open-run guard: the parent's run is open in `sliced` |
| The parent is re-labelled `bircher:queued` by a person | adopted, re-labelled `running` by `run_item`, nothing owed, the loop exits through the `sliced` branch; the parent carries `running` and `sliced` until the sweep closes it. Nothing is re-sliced |
| The parent issue is edited after filing | `revise_bundle` refused from `sliced`; the edit has no effect on the run; §9 says what a person does instead |
| A child is closed without merging | `slice_closed {state: closed}`; the parent closes when all are; the completion comment names each child's final state, so a closed-not-merged child is visible |
| A person closes the parent by hand | the run stays `sliced` and is swept; when its children close, the completion comment is posted, the close is skipped, the outcome recorded |
| The sweep meets a halted parent run, or one without `filing_complete` | skipped, logged; a halt is reconciled by a person as every halt is, and an incomplete filing is the loop's |
| `bircher:sliced` or `bircher:slice` missing on the repo | preflight refuses to start, naming it |

## §8 Tests

Kernel: every row of the state table and every refusal in §2, each named by a
test that reproduces it — the grammar cases (no scope, a two-sentence scope, a
seven-sentence scope, a missing non-goals line, a dependency on slice 9, a
self-dependency, a three-slice cycle, six slices, one slice), the
slice-of-a-slice read from `policy_frozen` and **not** from the bundle, the
empty ruling strings, the second ruling, the ruling after a direction, the
ruling from `slices_accepted`, the ruling after a rejected plan (legal), the
ungated advance under a gated policy, the human correction at the gate landing
in `shaping` and not `specified`, each extended from-set, `record_slice_filed`
against an unconfirmed effect, a wrong obligation and a duplicate,
`record_filing_complete` with a link unsatisfied and with the umbrella label
unsatisfied, `record_slice_closed` before completion and for an unfiled slice,
the outcome with a slice open and without `filing_complete`, `revise_bundle`
refused from `sliced`.

Coordinator, through the fake server: both endings of the shaping round and
the both-files case; the ruling grammar's edge cases; the review round with a
`slices` brief that carries the coarse paragraph; the gate park and its notice;
the gated path from approval into filing in one loop; `file_owed` with a crash
planted at **each** boundary named in §7 — after a create, after a fact, after
a link, after the umbrella — and the second pass performing exactly the
remainder, never a duplicate create and never a refused duplicate fact;
dependency order with a plan whose slice 1 depends on slice 2; the umbrella
comment's exact text; `render_child`'s exact text; `sweep_sliced` closing a
parent whose children are all closed, leaving one whose child is open,
skipping a halted run and one without `filing_complete`, and recording the
outcome only in the first case.

Runner self-test: the two labels in preflight; the resume gate's list; the
post-loop `sliced` branch recording `sliced` and dispatching nothing; the
generator queueing a `sliced` run without `filing_complete` and not one with;
a blocked child skipped until its blocker closes.

Proof: each of the four assertions reds on its planted defect, including a
child whose body carries the wrong slice's scope for assertion 1 and a
ruling-after-plan run for assertion 4's passing case.

Every new condition's mutation is executed, not argued; the review package for
each task states which line was mutated and which test went red.

## §9 Out of scope

Recursive slicing, refused by the kernel. Any estimate of slice size in lines
or files beyond §1's definition; coarse is the reviewer's judgement. Any change
to the back half. A spec for the epic itself; its children have specs. Shaping
issues that are not vague: the phase runs on every issue, and a clear
one-piece issue costs one turn and a ruling.

**Re-slicing a parent whose children stalled, or whose issue changed after
filing**, is a person's work, stated so nobody expects the loop to do it: close
the child issues that should not be worked (a closed child is a
`slice_closed {state: closed}`), let the parent close when the rest have, and
open a new issue for what remains. `cancel_run` on a child records an outcome
and closes nothing on GitHub; the issue is what the sweep reads.

## §10 Rulings taken in design

1. **Gated by default, off under autonomous.** Filing three to five issues is a
   wider blast radius than a comment and a wrong slicing costs several runs;
   the same shape as the spec gate keeps the policy legible. Costs, if wrong,
   a park on every epic under the default policy.
2. **Only slice plans are reviewed.** A one-piece verdict is a ruling with its
   reasoning, not an artefact; the spec reviewer sees the whole issue one
   phase later and catches a missed epic at the cost of a spec round. Costs,
   if wrong, that spec round.
3. **The parent stays open until its children close.** The backlog shows the
   epic as unfinished; nothing is lost to an abandoned child. Costs a sweep,
   which the scheduled wave already has the shape for.
4. **Depth one by the frozen policy's labels, not by counter.** `bircher:slice`
   as `policy_frozen` recorded it is what the kernel refuses on; a counter the
   coordinator carried would be a claim, and the bundle cannot carry the label
   at all. Costs nothing if wrong beyond a child that cannot be sliced further
   and must be worked as one piece.
5. **A scope-length bound in the kernel.** The one judgement-shaped refusal,
   kept because both live failures of the plan phase were documents that grew
   and this is the cheapest observable bound. Costs, if wrong, a refused plan
   whose author counted full stops differently from the parser; the refusal
   names the count.
6. **A new effect class rather than `issue_or_label`.** Creation mints work
   that another run consumes; keeping it distinct keeps the effect inventory
   honest about what the pipeline can do to a repository. Costs one class.
7. **`revise_bundle` refused from `sliced`.** Once children exist they are the
   work; re-slicing over them files duplicates and leaves orphans nothing
   closes. Costs a person the re-slicing of a changed epic, by the steps §9
   gives.
8. **The outcome guard iterates the plan, not the filed set.** The only reading
   under which "every child closed" cannot be vacuously true. Costs the kernel
   one parse of bytes it already holds.
9. **Filing completion is a kernel fact, not a coordinator inference.**
   `filing_complete` is what the generator, the sweep and the outcome read;
   the kernel accepts it only when every obligation the plan implies is
   satisfied. A fact the coordinator inferred from "every slice has a
   `slice_filed`" was shown to strand links and the umbrella. Costs one
   command and one fact.
10. **Changing one's mind within an epoch is legal until a plan is accepted.**
    A rejected slice plan followed by a one-piece ruling is a supported path,
    because a person may direct it; the proof reads the epoch's newer
    decision. Costs the proof one ordering comparison.

## Dispositions — round 1 (Kimi, 2026-09-09)

1. accepted — the depth refusal reads `bircher:slice` from `policy_frozen`'s
   unfiltered labels, not from the bundle, which the canon strips; §2 table,
   §7, ruling 4 rewritten, and a test that the bundle path does not fire.
2. accepted — `sliced` is in `_LOOP_STATES`; the loop's pass at `sliced` runs
   `file_owed` then exits, as `planned` runs `publish_owed` then exits; the
   gated path's approval continues into filing in the same pass, and the
   crash case is queued by the generator (§5) from the journal.
3. accepted — `record_run_outcome(sliced)` and the sweep iterate the accepted
   plan's slices via `slices.parse`, requiring a `slice_closed` per slice;
   ruling 8 records why.
4. accepted — `run_item`'s resume gate gains the four states; §5 names it and
   the self-test pins the list.
5. accepted — `revise_bundle` is refused from `sliced` (ruling 7); the
   filing and closing facts carry the epoch; §9 states the human path for a
   changed epic, including that `cancel_run` closes no issue.
6. accepted — the failure-table row now states what actually happens (adopted,
   re-labelled `running` by `run_item`, nothing owed, both labels until the
   sweep closes it) and §3 says why no replay undoes a label edit.
7. accepted — a table of every existing command whose from-set gains states,
   and the human correction's destination at the slice gate is `shaping`,
   with the `_review_destination` clause named and a test that it does not
   land in `specified`.
8. accepted — `record_slice_filed(slice, effect_key)` and
   `record_slice_closed(slice, closed_at, state)` are commands with legal
   states, roles and refusals; the issue number is read from the confirmed
   effect row, not the payload.
9. accepted — the definition of coarse is in §1 verbatim; this document is
   its source, and the brief carries it from here.
10. accepted — the routing table is untouched (no shell site); the effect
    inventory gains the site; the sentence now says which.
11. accepted — the `ISSUE_OR_LABEL` contract rule for the blocked-by POST and
    the delivered-forms for the obligation kinds are stated in §2 (six kinds
    as of revision 3; see round 2, finding 7).
12. accepted — the parent's close has its own obligation, a halted parent is
    skipped, and a hand-closed parent is in the failure table.
13. accepted — citation corrected to `authz.py:249`, checked at `:971-973`.
14. accepted — the amendment is named: `human.NOT_FOR_THE_AGENT`, finding 13
    of the live log.
15. accepted — the ruling file is three labelled lines with a stated parse
    rule; `slices.parse_ruling` beside `slices.parse`.

## Dispositions — round 2 (Codex, 2026-09-09)

1. accepted — `run_item` gains a post-loop `sliced` branch (§5, now the fifth
   runner change): scorecard row `sliced`, the queue file kept, no implementer
   dispatched, the run not ended; the self-test pins it.
2. accepted — filing completion is a kernel fact, `filing_complete`, accepted
   only when every obligation the plan implies is satisfied, links, umbrella
   and label included; the generator queues a `sliced` run until it exists
   and the sweep touches a run only once it does (§2, §3, §5, ruling 9).
3. accepted — `file_owed` is idempotent per obligation: step 2 records the
   fact only when absent and step 3 checks the links regardless, so a
   satisfied create with its fact recorded is neither re-recorded nor
   skipped past its owed links; §7 gains a row per crash boundary and §8 a
   planted crash at each.
4. accepted — a rejected slice plan followed by a one-piece ruling is a
   supported path, stated in §2: `record_one_piece` is legal from `shaping`
   regardless of an earlier submission and refused once a plan is accepted;
   assertion 4 reads the epoch's newer decision (ruling 10).
5. accepted — `record_one_piece` carries the same journal-order direction
   guard as `submit_slices`, stated in its refusal table and §4.
6. accepted — assertion 1 compares each child's title and body whole against
   `slices.render_child` over the parsed plan, the one renderer the filing
   step uses; a wrong-scope child is a planted defect in §8.
7. accepted — `umbrella_label` is the sixth obligation kind with a
   delivered-form; the list in §2 and disposition 11 above say six.
