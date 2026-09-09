# The shaping phase — an epic becomes its slices before anyone specs it

*2026-09-09. Design for one new phase in the v2 front half. Binding authority
for its plan; argues from, and does not restate, the front-half design
(`2026-09-05-front-half-design.md`), which it extends. Where this document
names a mechanism without defining it, the front-half design defines it.
Revision 13, after cross-vendor rounds 1 (Kimi), 2 (Codex), 3 (Kimi), 4
(Codex), 5 (Kimi), 6 (Codex), 7 (Kimi), 8 (Codex), 9 (Kimi), 10 (Codex), 11
(Kimi) and 12 (Codex); the dispositions are the last twelve sections.*

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
autonomous slices; it does not inherit `bircher:queued`, which the filing step
gives it last, once its dependency links exist (§3), nor `bircher:running` or
`bircher:sliced`, which are the runner's.

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
| — (no run) | `create_run` | `shaping` | unchanged otherwise: snapshot, `policy_frozen`, base sha; **refused for an issue an earlier run may have filed children for** (below) |
| `shaping` | `record_one_piece` | `queued` | the model ruling; the run proceeds as today |
| `shaping` | `submit_slices` | `slices_submitted` | the artefact, hashed, PUT to the store |
| `slices_submitted` | `record_review(accept)` | `slices_accepted` | the reviewer's verdict, bound as every verdict is |
| `slices_submitted` | `record_review(request_revision)` | `shaping` | the reviewer's; one revision destination, as every phase has |
| `slices_accepted` | `record_review(request_revision)` as `human` | `shaping` | **the human's correction at the gate.** `_review_destination`'s human clause (`authz.py:190`) today returns `queued` for `spec` and `specified` otherwise; it gains a `slices` case returning `shaping`, and a test that a correction at the slice gate does not land in `specified` |
| `slices_accepted` | `approve_artifact` | `sliced` | the human gate, when `slices ∈ gates` |
| `slices_accepted` | `advance_ungated` | `sliced` | the coordinator's transition when `slices ∉ gates`; refused when it is |
| `sliced` | `record_slice_filed`, `record_filing_complete`, `record_slice_closed`, `record_slice_reopened` | `sliced` | no transition; the filing and closing facts (below) |
| `sliced` | `record_run_outcome(sliced)` | `ended` | recorded by the sweep when filing is complete and every slice of the plan is currently closed (§3); **no other outcome is accepted from `sliced`** (below) |
| the three shaping states, and every front-half state | `revise_bundle` | `shaping`, epoch + 1 | **a revised issue is shaped again.** The front-half rule's destination, `queued`, becomes `shaping` from every legal state — the epoch's first state — because a materially changed issue may have changed size (ruling 11); the front-half tests that assert `queued` after a revision change to `shaping` |
| `sliced` | `revise_bundle` | **refused** | its children are the work now (ruling 7) |
| the three shaping states | `park`, `record_human_answer`, `record_prompt_item`, `dismiss_human_item`, `cancel_run` | as today | by the membership table below, not by joining `FRONT_HALF_STATES` |
| `shaping` | `record_human_direction` | as today | the author-round state of this phase, as `queued` and `specified` are of theirs |
| `sliced` | `cancel_run`, `record_run_outcome` | as today | via `_ALL_ACTIVE`; nothing human is legal at `sliced` — no park exists there for `human_pass` to read under. A cancel after a create was attempted leaves the children as they are on GitHub and the issue un-mintable (below); §9 says what a person does with them |

**Existing commands whose from-sets gain states.** The front-half design's
tables list each command's legal states explicitly, and the code enforces them
(`_TRANSITIONS`, `authz.py:84-155`); every one below is a stated change, so no
implementer discovers it as a refusal:

| Command | Gains | Why |
|---|---|---|
| `record_turn_ended` | `shaping`, `slices_submitted` | the shaping turn and the review turn end like any other |
| `record_author_empty` | `shaping` | the empty shaping turn and its one retry |
| `grant_round` | `slices_submitted` (with a current park), `shaping` (with a current park) | the failure table's "the person grants a round" |
| `issue_review_brief` | `slices_submitted` | the review round renders its brief through it |
| `record_review` (reviewer) | `slices_submitted` | the verdict |
| `record_review` (human) | `slices_accepted` | the correction, destination `shaping` |
| `approve_artifact` | `slices_accepted` | the gate |
| `record_human_direction` | `shaping` | the direction during a live shaping turn (§4) |
| `revise_bundle` | the three shaping states; destination `shaping` from every state | above |
| `record_model_question`, `record_model_ruling` | — | **not** extended: refused from every shaping state (below) |

**Which state set each site reads.** The new states do not join
`FRONT_HALF_STATES`: that set is the from-set of `record_model_question` and
`record_model_ruling` (`authz.py:133-134`), and joining it would legalise
both from the shaping states — the second of them writing the very
`model_ruling` fact shape `record_one_piece` records, with none of its
guards. A new set **`SHAPING_STATES = {shaping, slices_submitted,
slices_accepted}`** is added beside it, and every site that reads
`FRONT_HALF_STATES` today is listed here with its decision, so membership is
stated per command and not inherited:

| Site | Decision |
|---|---|
| `park`, `record_human_answer`, `record_prompt_item`, `dismiss_human_item` (`_TRANSITIONS`) | `FRONT_HALF_STATES \| SHAPING_STATES`: the gate park, the carrier session's items, a refused token's dismissal |
| `record_human_direction` | `{queued, specified, shaping}`: the three author-round states |
| `record_model_question`, `record_model_ruling` | `FRONT_HALF_STATES` unchanged: refused from every shaping state. `record_model_ruling` also refuses `question_id: "shape"` from anywhere — the id is reserved to `record_one_piece`, so no path writes a shape ruling without that command's guards, and §6's fourth assertion reads only vetted decisions |
| `revise_bundle` | `FRONT_HALF_STATES \| SHAPING_STATES`, destination `shaping` |
| `_review_destination`'s human guard (`authz.py:183`) | admits `SHAPING_STATES` too; its `slices` case returns `shaping` |
| `_review_destination`'s reviewer clauses (`authz.py:212`, `:216`) | each gains a `slices` case: `request_revision` returns `shaping`; `accept` returns `slices_accepted` **whatever the gates** — the ungated path leaves through `advance_ungated`, not through the accept, because the transition that authorises filing must be its own fact (below). Without the case an accepted slice plan would land in `planned` |
| `approve_artifact`'s destination (`authz.py:928`, `"specified" if phase == "spec" else "planned"`) | gains a `slices` case returning `sliced`; the same else-branch trap as the two above |
| `front.FRONT_PHASES` (`front.py:56`) and `projection.FRONT_PHASES` (`projection.py:22`) — the two phase lists that say which verdicts are the front half's | **both gain `slices`.** `projection.is_front_verdict` feeds `observe.revisions_used` and `recover.decide`, which exist so the back half never spends its repair allowance on, or reads its own review from, a front-half verdict; a one-piece run that needed two slice-plan rounds carries `review_verdict {phase: slices}` facts into its back half, and without the entry each would spend a repair round. `front.FRONT_PHASES` gates the proof's verdict-binding block (`prove_front_half.py:185`), and without the entry the slice review — the only review a sliced parent has — would never be re-rendered against its brief, contradicting §6. One list would be better than two; this design does not merge them, and states both |
| the reviewer-binding check (`authz.py:738`) | `FRONT_HALF_STATES \| SHAPING_STATES`: the review turn at `slices_submitted` must have ended like any other |
| `_ALL_ACTIVE` | gains the three shaping states and `sliced`: `cancel_run` and `record_run_outcome` are legal from them, so the runner's `failed` on a non-zero exit in a shaping state and a person's cancel both have a state to act from. From `sliced`, `record_run_outcome` accepts only the outcome `sliced` (below) |
| `_PHASE_OF_STATE` | the three shaping states and `sliced` map to `slices` |
| `_LOOP_STATES` (`coordinator/phases.py`) | gains the three shaping states and `sliced` |
| `run_item`'s resume gate (§5) | gains the four states |

**An issue is sliced once.** `create_run` is refused when the journal holds
a run whose snapshot names the same repository and issue number and which
holds **any `issue_create` effect row not reconciled as not-delivered** —
`intended`, `confirmed`, `uncertain` or reconciled delivered, the row states
`effects.py` has — whatever that
run's state: ended `sliced`, cancelled, or halted. The observable is the
attempt, not the outcome, because a child may exist from the first attempt
on, and a cancelled run that had created two children would otherwise be
followed by a fresh run filing five. The refusal names the run and its
rows. This is the guard that binds when the parent's run is closed and
something — a stale queue file, a person's `bircher:queued` — asks the
runner to mint a second one: the open-run guard covers only an open run,
and the parent's `bircher:sliced` label is a label, not a fact the kernel
reads. A run cancelled before any create was attempted leaves the issue
mintable — nothing exists to duplicate. The runner's existing refused-mint
path handles the refusal (§5).

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
plan it holds under the phase's artefact hash and parses as below — a current
closure of `slice_closed` in that epoch (*current* is defined with the closing
facts below: a reopened child is open again, whatever was recorded before),
**and** a `children_observed_closed` fact recorded under the calling
generation — the firing that ends the run is the firing that read every child
and found it closed (below); closures from earlier firings are not enough,
because a read that failed this firing would leave a stale one standing —
**and** the completion effects: the `umbrella_close` obligation satisfied,
and the `parent_close` obligation satisfied or that same fact saying
`parent: closed`, a parent a person closed owing no close. A satisfied
`parent_close` beside a fact saying `parent: open` is the one remaining
shape — the pipeline closed the parent, a crash preceded the outcome, and
a person reopened it before the next firing — and it is accepted: the
obligation was delivered once, a satisfied obligation is never re-performed,
and a parent a person reopened after the pipeline closed it is the
person's, as one reopened after the run ended is (§7). Iterating the
filed children rather than the plan was refused in review as a
vacuous-truth hole: an epic that crashed before filing anything would have
closed as complete. **From `sliced`, no other outcome is accepted:** a run that
has entered `sliced` may have filed children, and `failed` or `merged` over
them would end the epic with its filing half done and nothing left to repair
it. It ends as `sliced` or a person cancels it; the runner's `failed` after a
non-zero exit is refused there, and §5 keeps the queue file instead.

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

### `record_slice_filed`, `record_filing_complete`, `record_slice_closed`, `record_slice_reopened`, `record_children_observed_closed`

Every fact has an issuing command with legal states, a role and refusals, and
these five are no exception.

`record_slice_filed(slice, effect_key)` is legal from `sliced` under the
coordinator's `operator` dispatch. It names the slice number and the
idempotency key of the `issue_create` effect that filed it; the kernel reads
the created issue's number and database id from that effect row's confirmed
delivered value — **observed from the server's answer, never asserted by
the coordinator** — and refuses when the slice is not in the accepted plan,
when the effect is not confirmed or reconciled `delivered`, when its
obligation does not name this run, this epoch, this slice and this plan hash,
or when this slice already has a `slice_filed` in this epoch. Its fact:
`slice_filed {epoch, slice, issue, issue_id, title, parent, plan_hash}`.

`record_filing_complete()` is legal from `sliced` under the coordinator's
`operator` dispatch, and refused unless the kernel can see, for the current
epoch, a `slice_filed` for every slice of the accepted plan **and** a
satisfied effect for every filing obligation the plan implies: one
`slice_dependency` per dependency edge, one `slice_queue` per slice, the
`umbrella` comment and the `umbrella_label` swap. It is the kernel's
statement that nothing about filing
is still owed; the generator (§5) queues a `sliced` run until it exists, and
the sweep touches a run only once it does. Its fact:
`filing_complete {epoch, plan_hash, children: [numbers]}`. Refused a second
time in the same epoch.

`record_slice_closed(slice, closed_at, state)` is legal from `sliced` under the
sweep's `operator` dispatch, only after `filing_complete`. Refused for a slice
with no `slice_filed` in this epoch, or one whose current closure is already
`slice_closed`. Its fact: `slice_closed {epoch, slice, issue, closed_at,
state}` where `state` is `merged` or `closed`, as the sweep observed it.

`record_slice_reopened(slice, observed_at)` is the same command's opposite:
legal from `sliced` under the sweep's `operator` dispatch after
`filing_complete`, refused unless the slice's current closure is
`slice_closed`. Its fact: `slice_reopened {epoch, slice, issue, observed_at}`.
**A slice's current closure** is its newest `slice_closed` or
`slice_reopened` in the epoch, or none; a closure is an observation with a
date, not a permanent property, and a person who reopens a child reopens the
epic. The outcome guard above reads current closures, so a parent never
closes over a child that was closed once and is open now.

`record_children_observed_closed(parent_state)` is the sweep's statement
that **this firing read every child of the plan and found every one closed,
and then read the parent**: legal from `sliced` under the sweep's `operator`
dispatch after `filing_complete`, refused unless every slice's current
closure is `slice_closed` — so it cannot contradict the closing facts — and
refused a second time under the same generation. Its fact:
`children_observed_closed {epoch, generation, slices: [numbers], parent:
open | closed, observed_at}`. The parent's state rides on it because the
close that follows is owed only when the parent was open: a parent a person
closed by hand has no close effect, and the outcome guard (above) accepts
the fact's `parent: closed` in place of a satisfied `parent_close` — the
observation is journalled under the generation that made it, and nothing
has to invent an effect row for a mutation nobody performed. **What the
kernel guarantees with it is
ordering, not truth:** no outcome is recorded unless the sweep stated, under
the same generation, that it read every child closed. Whether that statement
is true is the coordinator's — as every observation of GitHub in this design
is, `slice_closed` included; the kernel cannot tell a lie from a read, and
this design does not pretend it can. The freshness itself is the sweep's
abort rule (§3): a firing that could not read a child records nothing after
the failure and never this fact. Recorded at most once per firing and only
on a firing that saw everything closed, so a waiting parent does not gain a
closure fact per child per firing — each firing's dispatch is a journalled
fact in any case; what is avoided is N more beside it.

All five facts carry the epoch. Since `revise_bundle` is refused from
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
number is parsed as `from_gh` parses comment ids; the confirmation then reads
the issue back for its database id, which the dependency link needs (below).
The rule **refuses a
`--label` value of `bircher:queued`, `bircher:running` or `bircher:sliced`**:
those are the runner's and the filing step's to set later, and a created
issue that carried `queued` would be runnable before its links exist (§3) —
the contract is where that is cheapest to refuse, and a coordinator that
composes the label anyway is refused before anything reaches GitHub. The
effect-site inventory
(`docs/design/effect-site-inventory.md`) gains the site; the routing
detector's table is **not** changed, because the call lives in the kernel
executor and not in `run-queue.sh`, and that table lists shell call sites only
and reds on an entry with none.

Two more contract changes, stated so they are not discovered as refusals: the
`ISSUE_OR_LABEL` contract (`contract.py:230-238`) gains a rule admitting `gh
api -X POST /repos/<repo>/issues/<n>/dependencies/blocked_by -f issue_id=<id>`,
the write half of the endpoint the queue generator already reads
(`is_unblocked`, `issues-to-queue.sh:20-24`); and the new obligation kinds —
`slice_issue`, `slice_dependency`, `slice_queue`, `umbrella`,
`umbrella_label`, `umbrella_close`, `parent_close`, seven in all — each get a
delivered-form in
`_delivered_value` (`effects.py:389-433`), so typed reconciliation of an
uncertain one is possible; without that, §3's crash rows are promises the
kernel cannot keep.

The umbrella comment is a `comment` effect; the children's queue labels, the
parent's label swap and the parent's close are `issue_or_label`. None of
those is new in kind.

**What `perform` refuses, per obligation kind.** Today `perform` checks
the dispatched identity, the generation, the contract and the halt state
for every effect, and checks the run's *state* only for `MERGE`
(`effects.py`, `perform`); a command's transition guards do not reach the
effects a coordinator performs between commands. So `record_slice_filed`
being legal only from `sliced` binds the *fact*, not the create: a
coordinator that performed an `issue_create` from `slices_accepted` would
mint a child the kernel never authorised, and a refused fact afterwards
undoes nothing on GitHub. Every obligation kind this design adds therefore
carries a precondition the kernel checks **when the effect is journalled**,
before anything reaches the executor:

Every filing obligation (§3) carries `run`, `epoch` and `plan_hash`; the
per-slice kinds carry `slice`, and `slice_dependency` carries `blocker`
too. **The common conditions**, checked for every kind in the table: the
run is in `sliced`; the dispatch is the coordinator's `operator`
generation; the obligation's `run` is this run and its `epoch` the current
epoch; where the kind carries `plan_hash`, it equals **the accepted hash** —
the hash the epoch's `human_ruling {approve, phase: slices}` or
`artifact_advanced {phase: slices}` named, the plan a reviewer accepted and
the gate passed, not any plan submitted; where the kind carries `slice`,
that slice is in the accepted plan.

| Obligation kind | Refused unless, beyond the common conditions |
|---|---|
| `slice_issue` | nothing further |
| `slice_dependency` | the edge `slice ← blocker` is in the plan, and both slices have a `slice_filed` in this epoch |
| `slice_queue` | every `slice_issue` and `slice_dependency` obligation the plan implies is satisfied — the property §3's third pass depends on, a queued child has every link, refused here rather than discovered at `filing_complete` after the generator has taken the child up |
| `umbrella`, `umbrella_label` | a `slice_filed` for every slice of the plan |
| `umbrella_close`, `parent_close` (no `plan_hash`, no `slice`) | `filing_complete` in this epoch, and a `children_observed_closed` under the calling generation |

**Bound to the effect, not only to the metadata.** The obligation says what
the effect is for; the argv says what it does; `perform` refuses the pair
when they disagree, as it binds a merge to its actual target today and
nothing else. The kernel admits effects with no obligation by design — the runner's own
merges, labels and comments are journalled as `{argv}` alone — so a
precondition that fires only when an effect announces its kind polices only
the effects that announce themselves. **Every argv shape this design adds
to the contracts is therefore admitted only with its kind:** an
`ISSUE_CREATE` must carry `slice_issue`; the blocked-by `POST` must carry
`slice_dependency`; an `issue_or_label` argv that adds `bircher:queued` must
carry `slice_queue`, and one that adds `bircher:sliced` must carry
`umbrella_label`; a comment whose body opens `bircher: sliced ` must carry
`umbrella` or `umbrella_close`; and `gh issue close` on the issue of a run in
`sliced` must carry `parent_close`. An effect of any of those shapes with no
obligation, or with an obligation of another kind, is refused — which is
what makes "no create outside `sliced`" and "a queued child has its links"
true of every create and every queue label, not only of the ones that say
what they are. The runner's own label swap, `queued` → `running`, adds
neither label and stays as it is. Each kind is admitted on exactly one
effect class and operation, against the targets the kernel already holds:

| Kind | Class and operation | The argv must name |
|---|---|---|
| `slice_issue` | `issue_create` | `--repo` the run's repository; `--title` the slice's title; a body file whose bytes equal `slices.render_child` over the accepted plan, the run's issue number, `hash8` and the siblings' numbers from this epoch's `slice_filed` facts — the kernel renders and compares, and **refuses the create while any blocker of the slice has no `slice_filed` in this epoch**: `render_child` is strict and raises on a missing sibling number rather than rendering `Depends on: none`, so an out-of-order create cannot produce a body the binding admits; labels exactly `bircher:slice` plus the inherited policy labels |
| `slice_dependency` | `issue_or_label`, the blocked-by `POST` | the path's issue number is the child the epoch's `slice_filed` names for `slice`; `issue_id` is the database id that fact names for `blocker` |
| `slice_queue` | `issue_or_label`, `gh issue edit` | the `slice_filed` issue for `slice`; `--add-label bircher:queued` and nothing else |
| `umbrella` | `comment` | the run's issue; a body equal to the umbrella text rendered from the plan and the `slice_filed` numbers |
| `umbrella_label` | `issue_or_label`, `gh issue edit` | the run's issue; `--add-label bircher:sliced --remove-label bircher:running` and nothing else |
| `umbrella_close` | `comment` | the run's issue; a body equal to the completion text rendered from the plan and the current closures |
| `parent_close` | `issue_or_label`, `gh issue close` | the run's issue |

The database id the dependency link needs is not in the create's URL, so
the create's confirmation reads the created issue back (`gh api
/repos/<repo>/issues/<n>`, `.id`) and the confirmed row's delivered value
carries both number and id; `record_slice_filed` records both. The kernel
parses the plan and holds every obligation's row and every fact these rows
name, so each precondition and each binding is a read of what it already
holds.
The coordinator's pass ordering is the behaviour; these refusals are the
guard (ruling 18), and the first of them — no create outside `sliced`,
against the accepted hash — is the one that makes "the transition that
authorises filing must be its own fact" true of the effect and not only of
the fact that follows it (ruling 19).

### Facts

| Fact | Written by | Payload |
|---|---|---|
| `artifact_submitted` | `submit_slices` | as today, `phase: slices` |
| `review_verdict` | `record_review` | as today, `phase: slices` |
| `model_ruling` | `record_one_piece` | `question_id: "shape"`, `ruling`, `reasoning`, `cost_if_wrong` |
| `human_ruling` | `approve_artifact` | as today, `phase: slices` |
| `artifact_advanced` | `advance_ungated` | `phase: slices`, `hash` |
| `slice_filed` | `record_slice_filed` | `epoch`, `slice`, `issue`, `issue_id`, `title`, `parent`, `plan_hash` |
| `filing_complete` | `record_filing_complete` | `epoch`, `plan_hash`, `children` |
| `slice_closed` | `record_slice_closed` | `epoch`, `slice`, `issue`, `closed_at`, `state` |
| `slice_reopened` | `record_slice_reopened` | `epoch`, `slice`, `issue`, `observed_at` |
| `children_observed_closed` | `record_children_observed_closed` | `epoch`, `generation`, `slices`, `parent` (`open` or `closed`), `observed_at` |
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
(`human.NOT_FOR_THE_AGENT` in `v2/coordinator/human.py`, added for finding 13
of the live log; in the code, not in the front-half document). `approve`
moves it to `sliced`; corrections are the human's
`record_review(request_revision)`, destination `shaping`, and the next round's
findings; `cancel` cancels. `retry` is refused here as at the spec gate. With
`slices ∉ gates`, the same pass records `advance_ungated` and continues.

**How a pass reaches `sliced` on the gated path.** The person's `approve` is
read by a wave the schedule launched, whose queue generator queued the run
because it held a current park (the journal sweep in `issues-to-queue.sh`,
live-log finding 12; the front-half design does not describe it — the code
and `docs/design/front-half-live-log.md` do). `human_pass` takes the
approval, the run is `sliced`, and the
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
and never re-performs or re-records anything. It runs in **four passes over
the accepted plan, each complete before the next begins**, because the third
pass is what makes a child visible to the queue, and a child must not be
visible before its links exist:

1. **The creates and their facts**, for each slice in dependency order (a
   topological order; ties by slice number). Obligation `{kind: slice_issue,
   run, epoch, slice: n, parent, plan_hash}`, key
   `slice:<run>:<n>:<generation>`. If no satisfied `issue_create` carries this
   obligation, compose the child with `slices.render_child(slice, parent,
   hash8, sibling_numbers)` — `hash8` being the first eight hex characters
   of the accepted plan's hash, wherever it appears in this document —
   title: the slice title; body: the marker line
   `Slice n of #<parent> (bircher shaping <hash8>)`, a blank line, the scope,
   `Non-goals: …`, and `Depends on: #<x>, #<y>` with the siblings' numbers
   already filed, or `Depends on: none`; labels: `bircher:slice` and the
   parent's inherited policy labels, **never `bircher:queued`** — and perform
   it. The renderer is the one function the proof re-renders with (§6). Then,
   if this slice has no `slice_filed` in this epoch, `record_slice_filed(n,
   key)` with the key of the satisfied create just found — the key that row
   carries, minted by whichever generation performed it, never the current
   generation's — which the kernel checks against the confirmed row. A
   satisfied create with its fact already recorded is skipped here and *not*
   skipped in the passes below.
2. **The links**, for each slice and each of its dependencies: obligation
   `{kind: slice_dependency, run, epoch, plan_hash, slice: n, blocker: m}`;
   performed if unsatisfied, as an `issue_or_label` effect posting the
   blocked-by link.
3. **The queue labels**, for each slice: obligation `{kind: slice_queue, run,
   epoch, plan_hash, slice: n}`, an `issue_or_label` effect adding
   `bircher:queued` to the child; performed if unsatisfied. This pass starts only when every
   obligation of passes 1 and 2 is satisfied, so **a child that carries
   `bircher:queued` has every link the plan gives it**, and a child the
   generator would otherwise take up — created, linked or not — is invisible
   to it until then, since the generator lists `bircher:queued` issues and
   nothing else. A crash before this pass leaves children nobody runs; a
   crash during it leaves some children queued, each with its links, and the
   rest owed. A halted parent's unqueued children stay unqueued until the
   halt is reconciled and a pass completes the filing.
4. **The umbrella**, each once, if unsatisfied: the comment, obligation
   `{kind: umbrella, run, epoch, plan_hash}` — `bircher: sliced <hash8>`
   followed by one line per child, `- #<number> Slice n: <title>` — and the
   label swap `bircher:running` → `bircher:sliced` on the parent, obligation
   `{kind: umbrella_label, run, epoch, plan_hash}`.

Last, if this epoch has no `filing_complete` fact,
`record_filing_complete()`, which the kernel accepts only when every
obligation above is satisfied; a pass over a run that has nothing owed
performs nothing and records nothing, and issues no command the kernel would
refuse. An uncertain
effect anywhere halts the run (§3 *Sessions are effects*) for reconciliation,
which lists the parent's children by the marker line to say what exists. A
satisfied obligation is never re-performed — `perform` replays a confirmed key
— which is why nothing in this design depends on a replay to undo a person's
label edit (§5).

`bircher: sliced ` joins `BIRCHER_STATUS_PREFIXES` in both copies of the
predicate, so neither the umbrella nor the completion comment (which shares
the prefix) ever freezes into a bundle. The pin
`test_prefixes_are_exactly_six_and_not_the_generic_one`
(`v2/tests/kernel/test_bundle_v2.py:32`) becomes seven — both copies hold
six today (`bundle.py:41-52`, `run-queue.sh:3090-3095`); the "Five EXACT
prefixes" comment at `bundle.py:38` is stale and goes with the change — and
the fixture and bash-agreement tests that enumerate the prefixes change
with it, stated here so the reds are expected, as §2's note on the
`revise_bundle` tests states its own.

### The sweep

Each firing of the scheduled wave, before it generates the queue, runs
`sweep_sliced(ctx)` over every open run in state `sliced` **that carries a
`filing_complete` fact** (from the journal, by `open_run_ids` and the fact),
skipping a run that is halted or holds pending effects — a dispatch on such a
run is refused, and the halt is a person's to reconcile. For each such run,
under a fresh `operator` dispatch: for **every** slice in the accepted plan —
not only those without a closure, because a closure is an observation that
can go stale — read the issue (`gh issue view --json
state,closedAt,closedByPullRequestsReferences`). Observed closed with a
current closure that is not `slice_closed`: `record_slice_closed` with
`closed_at` from the issue's `closedAt` and state `merged` when that list
names a pull request whose own `state` is `MERGED` (`gh pr view --json
state`), and `closed` otherwise — a child closed by hand, or by a pull
request that was itself closed unmerged. Observed open with a current closure
of `slice_closed`: `record_slice_reopened`. Otherwise nothing. All observed
from the server; the child run's journal is not consulted, because a person
may close a child the loop never worked. **A read that fails — the issue
view or the pull request view — ends this run's firing there:** the facts
recorded before it stand, nothing after it is read or recorded, no
completion is attempted, and the firing logs which child it could not read;
the next firing starts over. A firing in which every read succeeded and
every child was observed closed then reads the parent (`gh issue view
--json state` on it; a failed read ends the firing as a child's would) and
records `children_observed_closed` with the parent's state, and only then,
in order: the owed comment `bircher: sliced complete` naming each child and
its final state (obligation `{kind: umbrella_close, run, epoch}`); the
parent's close (obligation `{kind: parent_close, run, epoch}`) **only if the
fact says `parent: open` and the obligation is unsatisfied** — a parent a
person already closed owes no close, and the outcome guard reads the fact
instead of an effect row nothing performed; a parent the pipeline closed and
a person reopened is not closed again, and is the person's (§2); and then
the command `record_run_outcome(sliced)`. A person
who closes the parent between the observation and the close makes the close
a no-op the executor reports delivered: `parent_close`'s delivered-form is
the issue's `closed` state, observed, so an uncertain close reconciles by
reading it. Under the
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
questions (§3), and `record_model_question` is refused from every shaping
state — the states stay out of its from-set (§2, *which state set each site
reads*).

A revised *epic* — a person editing the issue after its children are filed —
is not re-sliced: `revise_bundle` is refused from `sliced`. What a person does
instead is stated in §9.

## §5 Runner

Seven changes — three of them to `run_item`, two to the queue generator —
and one changed note on a path that already exists:

- `_preflight_labels` checks `bircher:slice` and `bircher:sliced` beside
  `running` and `escalated`.
- **`run_item`'s resume gate** (`run-queue.sh:4318-4328`) resumes a run only
  in an explicit list of front-half states and escalates any other as beyond
  the front half. The list gains `shaping`, `slices_submitted`,
  `slices_accepted` and `sliced`; without that, every resume of a parked
  shaping-phase run is escalated before the loop is called.
- **`run_item`'s post-loop branch.** After `phases` exits `0` the runner today
  dispatches an implementer, calls `start_implementation` — legal only from
  `planned` (`authz.py:89`) — and records `failed` when the state is not
  `implementing` (`run-queue.sh:4451-4465`). A `sliced` run exiting `0` would
  take that path and be scored as a failed implementation. The branch gains a
  case: state `sliced` after the loop records the scorecard row `sliced`,
  naming the children from `slice_filed`, **moves the queue file to
  `processed`** — the loop is finished with this item; what remains is the
  sweep's, and the sweep reads the journal, never the queue — and returns
  without dispatching anything or ending the run. A parked item's file is
  kept because a later pass resumes from it; nothing resumes a completed
  filing from a file. The crash case never reaches this branch: a halt takes
  the existing escalation path, a non-zero exit takes the case below, and
  both leave the file where it is, so the next pass adopts the open run from
  the file (`queue` source) or from the journal (`issues` source). The
  self-test pins both the resume list and this case.
- **`run_item`'s non-zero-exit branch.** When `phases` exits non-zero and
  the run holds no pending effect, the runner today records `failed` and
  moves the file to `processed` unconditionally (`run-queue.sh:4440-4445`).
  A coordinator that dies between two *confirmed* filing effects — the
  process killed, the host's turn timeout — exits that way with nothing
  pending, and the file would be consumed with the filing half done; under
  the `queue` source nothing regenerates it. The branch reads the state back
  first: at `sliced` it records no outcome (the kernel would refuse `failed`
  there in any case, §2), writes a scorecard row `escalated` whose note says
  the filing was interrupted and the next pass repairs it, and returns with
  the file where it is. In a shaping state the existing `failed` path
  stands — nothing has been filed.
- **The refused mint.** When a queue file names an issue whose run has ended
  — the file a person wrote by hand, or a re-labelled parent the generator
  rendered — `_kernel_find_run … open` finds nothing and `run_item` mints a
  run, which `create_run` refuses (§2, *an issue is sliced once*). The
  existing refused-mint path records a scorecard row and moves the file to
  `processed` (`run-queue.sh:4362-4371`); the row's note carries the
  refusal's text, so it reads "sliced by run i12-…" and not a bare "refused".
  Today the call's output is discarded (`_kernel_run_start … >/dev/null`)
  and the note is a constant; the change is to capture the refusal and put
  it in the note — small, but code, not only a string.
- `issues-to-queue.sh`'s journal sweep queues, beside open runs with a current
  park, **open runs in `sliced` with no `filing_complete` fact in the current
  epoch** — the crash-anywhere-during-filing case — so the next wave's pass
  reaches `file_owed`, which repairs whatever is owed. A `sliced` run with
  `filing_complete` is not queued; it is the sweep's, not the loop's. Whether
  a queue file for the parent exists is never the test; the kernel's fact
  is.
- **`is_unblocked` fails closed.** Today a `gh` error reading an issue's
  blockers substitutes zero (`issues-to-queue.sh:21-22`, `|| echo 0`) and the
  issue is queued as unblocked. That was harmless while nothing depended on
  the links; this design makes them the sequencing of an epic's children,
  and a child started because its blocker could not be read is the round-4
  defect by another door. On a read error the generator skips the issue this
  wave and says so; the next wave reads again.
- The wave calls `coordinator.cli sweep-sliced --db … --server … --repo …`
  once per firing, before queue generation.

`run_item`'s label effect, `queued` → `running`, fires on every pass that
resumes a run, generation-keyed; a `sliced` parent that a person re-labelled
`queued` is therefore adopted, re-labelled `running` beside its `sliced`
label, worked by a loop that has nothing owed and exits through the new
branch, and left carrying both labels until the sweep closes it. Nothing is
re-sliced. The same re-label after the run has ended is the refused mint
above, once per wave while the label stays — the label is the person's, and
the runner does not remove a label it never put there; the scorecard row is
what tells them.

## §6 Proof

`prove_front_half` gains four assertions, run over a parent and its children
together (`--children` reads the `slice_filed` facts and proves each child's
run as an ordinary run), all through `slices.parse` over the plan the kernel
holds:

1. **The children are the plan, body and all.** The accepted plan's slices and
   the `slice_filed` facts correspond one to one, matched by slice number —
   not by journal order, which is dependency order and may run 2, 1 — in the
   run's final epoch; and each child issue, fetched live, has the title and
   the body that
   `slices.render_child` produces for its slice from the parsed plan, the
   parent's number, the hash8 and the siblings' filed numbers — compared
   whole, not by marker and title alone, so a filing that gave every child the
   first slice's scope, or dropped the non-goals, fails here.
2. **The dependencies are the links.** Every `Depends on:` in the plan is a
   blocked-by link on the child, fetched live, and the child has no other.
3. **The umbrella names the children.** The parent's `bircher: sliced` comment
   for this plan hash lists exactly the numbers in `slice_filed`, and there is
   exactly one such comment.
4. **One decision per epoch, and every filing in the last.** No `slice_filed`
   lies outside the run's final epoch — a superseded epoch never filed, since
   `revise_bundle` is refused from `sliced`. In every epoch that holds a
   `shape` ruling, the ruling is newer than every `artifact_submitted` of
   phase `slices` in that epoch and the epoch holds no `slice_filed`. In the
   final epoch exactly one of the two exists, a `slice_filed` or a `shape`
   ruling. An epoch superseded before either — a person edited the issue
   while a plan waited for review or approval — holds neither and passes; a
   rejected slice plan followed by a one-piece ruling passes; a ruling
   followed by a filing, or a filing beside a newer ruling, fails.

Under mode `zero`, no park at the slice gate; under mode `approval`, exactly
one `human_ruling {approve}` at it is admitted beside the spec gate's. Every
assertion is exercised by a planted defect in the tests, as §8 of the
front-half design requires of its own.

**What the existing assertions do over a sliced parent.** `assert_journal`
today requires at least one `model_ruling` and a submission phase set of
exactly `{spec, plan}` with different hashes (`prove_front_half.py`,
`assert_journal`); read unchanged, it fails every sliced parent and every
one-piece run that submitted a rejected slice plan first. Two amendments:
the model-ruling check **excludes `question_id: "shape"`** — it means a
ruling on a question the author raised, and a shape ruling every one-piece
run carries would satisfy it vacuously; and the phase-set check is per kind
of run **and reads the final epoch only** — `assert_journal` today collects
submissions across the whole run, and a run that submitted a spec in epoch
1, had its issue materially revised, and filed an accepted slice plan in
epoch 2 is a valid history whose whole-run set is `{spec, slices}`. In the
final epoch a sliced parent submits exactly `{slices}` and the hash is the
accepted plan's; a one-piece run submits `{spec, plan}` with different
hashes and may also hold `slices` (the rejected plan before its ruling).
Earlier epochs hold whatever they reached before they were superseded, and
this check does not read them; the binding and session checks still read
every epoch. The parent is exempt from the model-ruling check, its decision being
`slice_filed`, and from nothing else: the human-fact checks by mode, the
reconciled-or-halted check and every session check (`assert_sessions`:
snapshot names the dispatch's vendor, one obligation per create, every
session prompted or stopped) apply to it as to any run, over its shaping and
review seats. The children are proved as ordinary runs.

## §7 Failure table

| Situation | What happens |
|---|---|
| Shaper writes both `shape.md` and `artifact.md` | the ruling is read; the artefact is ignored and moved aside before any re-prompt |
| Shaper writes a six-slice plan | `submit_slices` refused; the refusal is the next round's findings, as the grill refusal is |
| Reviewer rejects three times | `bound_exhausted`, the park, the notice: the person grants a round (legal at `slices_submitted` with a park), corrects, or cancels — or directs "one piece" |
| A slice plan is rejected and the next author rules one piece | legal; the run proceeds to `queued`; the epoch's decision is the newer fact (§2), and the proof reads it so |
| A person directs during a live shaping turn | the turn is displaced; its ruling or plan is refused by the direction guard whichever it was; the direction is the next round's findings |
| A child is itself an epic | its shaper cannot slice it (`policy_frozen` carries `bircher:slice`); it proceeds as one piece and its spec phase does what it can; the reviewer's findings on an oversized spec are the signal a person reads |
| Crash after `sliced`, anywhere before `filing_complete` | obligations unsatisfied, no park, no `filing_complete`; the generator queues the run; the next pass's `file_owed` performs exactly what is missing — a create, a fact, a link, a queue label, the umbrella or its label — and records completion |
| Crash between a create's confirmation and its `record_slice_filed` | the effect is confirmed; the next pass's first pass finds it satisfied and records the fact; the later passes check the links and the label |
| Crash after the creates, before or during the links | the children exist without `bircher:queued`; the generator does not list them; no child runs before its links; the next pass performs the missing links, then the queue labels |
| Crash during the queue labels | the labelled children have every link; the unlabelled ones are invisible; the next pass labels the rest |
| The coordinator dies between two confirmed filing effects and `phases` exits non-zero with nothing pending | `run_item`'s non-zero branch reads `sliced`, records no outcome, keeps the file, writes an `escalated` row; the next pass repairs. `record_run_outcome(failed)` would be refused from `sliced` in any case |
| The parent run halts mid-filing (an uncertain effect) | in the first or second pass: the children created so far are unqueued and stay so until a person reconciles the halt and the next pass completes the filing. In the third pass: the children already labelled have their links and may be worked; the one whose label went uncertain is reconciled like any label effect; the rest stay unqueued. In the fourth: every child is queued and linked; only the umbrella is owed |
| A person cancels a parent after a create was attempted | the run is `cancelled`; the children created stand as issues, queued or not by the pass the cancel interrupted; the issue cannot be minted again (§2, *an issue is sliced once*); the person closes the children that should not be worked and opens a new issue for what remains (§9). A cancel before any create was attempted leaves the issue mintable |
| A coordinator performs a queue label before a create or a link is satisfied (a bug in the pass order) | the kernel refuses the `slice_queue` effect at journal time; nothing reaches GitHub; the refusal halts the pass and names the unsatisfied obligation |
| A coordinator composes a child with `bircher:queued` in its labels | the `ISSUE_CREATE` contract refuses the argv; nothing reaches GitHub |
| Crash between the last queue label and the umbrella, or between the umbrella and its label | the umbrella or the label is performed; then completion |
| A closed child is reopened while a sibling is still open | the next sweep observes it open and records `slice_reopened`; its current closure is open; the parent does not close until it is observed closed again |
| A read fails mid-sweep — a child reopened last week, its read failing this firing while its siblings read closed | the firing ends at the failed read: no `children_observed_closed`, no completion effects, no outcome; the facts recorded before the failure stand; the next firing reads every child again. The kernel holds the order — no outcome without the sweep's same-generation statement — and the statement's truth is the sweep's (§2) |
| The generator cannot read a child's blockers | the child is skipped this wave, not queued as unblocked; the next wave reads again |
| A closed child is reopened after the parent closed | the parent's run has ended; the sweep does not read it; the reopened child is a person's, as an issue reopened under any closed epic is |
| Crash between the completion comment and the parent's close | the comment's obligation is satisfied and is not re-posted; the close's is not and is performed; the outcome follows |
| Crash between the parent's close and the outcome, and a person reopens the parent before the next firing | the next completing firing observes `parent: open` beside a satisfied `parent_close`; no second close — a satisfied obligation is never re-performed, and the reopening is the person's; the outcome is recorded and the parent stays open as they left it, exactly as a parent reopened after the run ended |
| An effect's argv disagrees with its obligation — a link targeting the wrong child, a create whose body is not the slice's, a create with no obligation | `perform` refuses the pair at journal time (§2, *bound to the effect*); nothing reaches GitHub |
| Two runs shape the same parent | while the parent's run is open, impossible by the open-run guard; once any run for the issue has attempted a create — ended, cancelled or halted — impossible by `create_run`'s refusal (§2, *an issue is sliced once*). Neither reads a label |
| A stale queue file for a sliced parent is drained after its run ended (the `queue` source drains `queue/*.md` whatever the manifest says) | `_kernel_find_run … open` finds nothing; the mint is refused; the scorecard row names the run that sliced the issue; the file is moved to `processed`. Nothing is re-sliced |
| The parent is re-labelled `bircher:queued` by a person | while the run is open: adopted, re-labelled `running` by `run_item`'s own label effect as on every resume, nothing owed, no filing command issued, the loop exits through the `sliced` branch; the parent carries `running` and `sliced` until the sweep closes it. After the run has ended: the refused mint, once per wave until the person removes the label. Nothing is re-sliced |
| The parent issue is edited while in a spec or plan state | `revise_bundle` lands in `shaping`, epoch + 1: the changed issue is shaped again, at the cost of one shaping turn if it is still one piece |
| The parent issue is edited after filing | `revise_bundle` refused from `sliced`; the edit has no effect on the run; §9 says what a person does instead |
| A child is closed without merging | `slice_closed {state: closed}`; the parent closes when all are; the completion comment names each child's final state, so a closed-not-merged child is visible |
| A person closes the parent by hand | the run stays `sliced` and is swept; when its children close, the completing firing reads the parent, records `children_observed_closed {parent: closed}`, posts the completion comment, performs no close, and records the outcome, which the kernel accepts on the fact's `parent: closed` in place of a `parent_close` effect (§2) |
| A coordinator performs a child create before the gate — from `slices_accepted`, or against a plan hash that is not the accepted one | `perform` refuses the effect at journal time (§2, *what `perform` refuses*); nothing reaches GitHub; the pass halts on the refusal |
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
in `shaping` and not `specified`, the reviewer's accept landing in
`slices_accepted` under an ungated policy and not `planned`, each extended
from-set, **every row of the membership table** — in particular
`record_model_question` and `record_model_ruling` refused from each of the
three shaping states, and `record_model_ruling` refusing `question_id:
"shape"` from `queued` — `create_run` refused for an issue whose earlier run
holds an `issue_create` row (one ended `sliced`, one cancelled after a
confirmed create, one halted on an uncertain create) and admitted for one
whose earlier run was cancelled before any create and for one that ended
any other way,
`revise_bundle` landing in `shaping` from `spec_submitted`, `record_slice_filed`
against an unconfirmed effect, a wrong obligation and a duplicate,
`record_filing_complete` with a link unsatisfied, with a queue label
unsatisfied and with the umbrella label unsatisfied, `record_slice_closed`
before completion, for an unfiled slice and for a currently closed one,
`record_slice_reopened` for a slice never closed and for one already
reopened, `record_slice_closed` accepted again after a `slice_reopened`, the
outcome with a slice open, with a slice reopened after closing, and without
`filing_complete`, the outcome refused with every slice closed but no
`children_observed_closed` under the calling generation (one under an
earlier generation planted), `record_children_observed_closed` refused with
a slice open and refused twice under one generation,
`record_run_outcome(failed)` and `(merged)` refused from `sliced`, a
`slice_queue` effect refused while a create or a link of the plan is
unsatisfied and accepted once all are, the `ISSUE_CREATE` contract refusing
each of the three runner labels, `revise_bundle` refused from `sliced`,
`is_front_verdict` true for a `slices` verdict and `revisions_used` not
spending a repair round on one, `approve_artifact` from `slices_accepted`
landing in `sliced` and not `planned`; and **every row of the `perform`
table**: an `issue_create` with a `slice_issue` obligation refused from
`slices_accepted`, refused from `sliced` under an author dispatch, refused
with the hash of a submitted-but-rejected plan, refused for a slice number
not in the plan, and accepted from `sliced` under the operator with the
accepted hash; a `slice_issue` whose `epoch` is not the current one
refused; a `slice_dependency` for an edge not in the plan refused, and one
whose blocker has no `slice_filed` refused; an `umbrella` and an
`umbrella_label` each refused with a slice unfiled; a `parent_close` without
a same-generation observation fact refused; the outcome accepted with
`parent: closed` and no `parent_close` row, refused with `parent: open`
and no `parent_close` row, and accepted with `parent: open` beside a
satisfied `parent_close` (the reopened-after-close case) with no second
close performed; **and every row of the binding table**: an `issue_create`
with no obligation refused, one with a `slice_dependency` obligation
refused, one whose title is not the slice's refused, one whose body is not
`render_child`'s bytes refused, one whose labels include `bircher:queued`
refused by the contract; a `slice_dependency` whose path issue is a
sibling's refused and one whose `issue_id` is not the blocker's refused; a
`slice_queue` on the wrong child or adding a second label refused; an
`umbrella` on a child refused; an `umbrella_label` removing nothing
refused; a `parent_close` on a child refused; and each accepted when the
argv matches; **and the mandate**: with no obligation at all, each of a
blocked-by `POST`, `--add-label bircher:queued`, `--add-label
bircher:sliced`, a comment opening `bircher: sliced ` and `gh issue close`
on a `sliced` run's issue refused, each with an obligation of another kind
refused, and the runner's `queued` → `running` swap admitted with none as
today; a create for a slice whose blocker is unfiled refused, and
`render_child` raising on the missing number rather than rendering `none`.

Coordinator, through the fake server: both endings of the shaping round and
the both-files case; the ruling grammar's edge cases; the review round with a
`slices` brief that carries the coarse paragraph; the gate park and its notice;
the gated path from approval into filing in one loop; `file_owed` with a crash
planted at **each** boundary named in §7 — after a create, after a fact, after
a link, after a queue label, after the umbrella — and the second pass
performing exactly the remainder, never a duplicate create and never a
refused duplicate fact; **no child carries `bircher:queued` while any link of
the plan is unsatisfied**, asserted over the fake server's label history at
every planted crash; dependency order with a plan whose slice 1 depends on
slice 2; a second pass over a complete filing issuing no command at all (the
fake kernel counts them); the umbrella comment's exact text; `render_child`'s
exact text and its labels; `sweep_sliced` closing a parent whose children are
all closed, leaving one whose child is open, skipping a halted run and one
without `filing_complete`, recording the outcome only in the first case,
recording `merged` for a child whose closing pull request is `MERGED` and
`closed` for one closed by hand and for one whose pull request was closed
unmerged, **across three firings**: child A closed (recorded), A reopened
and B closed (a `slice_reopened` and a `slice_closed`, no outcome), A closed
again (the outcome); and **a failed read mid-sweep**: the fake server erring
on one child's view while the others read closed, the firing recording the
closures it reached, no observation fact, no completion effect, no outcome,
and the next firing completing once the read succeeds.

Runner self-test: the two labels in preflight; the resume gate's list; the
post-loop `sliced` branch recording `sliced`, dispatching nothing and moving
the file to `processed`; the non-zero-exit branch at `sliced` recording no
outcome and keeping the file, and at `shaping` recording `failed` as today;
the refused mint's scorecard note carrying the refusal's text; the generator
queueing a `sliced` run without `filing_complete` and not one with; the
generator not listing a child created without `bircher:queued`; a blocked
child skipped until its blocker closes; a child whose blockers cannot be read
(the `gh` stub failing) skipped rather than queued.

Proof: each of the four assertions reds on its planted defect, including a
child whose body carries the wrong slice's scope for assertion 1, and for
assertion 4 both passing cases — a ruling after a rejected plan, and an epoch
superseded by `revise_bundle` while a plan waited — beside its red. The
amended existing checks: a sliced parent with no model ruling and a
`{slices}` submission passes `assert_journal`; a one-piece run whose only
model ruling is `shape` fails the ruling check as a run with none does
today; a one-piece run with `{slices, spec, plan}` submissions passes the
phase-set check and one with `{spec}` alone fails it; a parent that
submitted a spec in epoch 1 and was sliced in epoch 2 passes it, and one
whose final epoch holds `{slices, spec}` fails it; and the verdict-binding
block re-rendering a sliced parent's review brief, red when the planted
brief names the wrong hash.

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
11. **Every revision lands in `shaping`.** The front half reset a revised run
    to `queued`; with a phase before the spec, the epoch's first state is
    `shaping`, and a materially changed issue may have changed size. Costs a
    one-piece issue one shaping turn per revision, and the front-half tests
    that assert `queued` their expected state.
12. **An issue is sliced once, by the journal.** `create_run` refuses an
    issue whose earlier run attempted a create — the `issue_create` row,
    whatever the run's state — reading the journal and not the parent's
    `bircher:sliced` label, because a person can remove a label and cannot
    remove a fact, and because a cancelled run's children are as real as an
    ended run's. Costs a scan of the issue's earlier runs at minting, and a
    person who wants an epic re-sliced the steps §9 gives.
13. **The shaping states stay out of `FRONT_HALF_STATES`.** Membership is
    stated per site; the model's question and ruling channels are refused
    from the phase, and the `shape` question id is reserved to
    `record_one_piece`. Costs one more state set and a table nobody can
    inherit from by accident.
14. **The queue label is the last thing a child gets.** Visibility to the
    generator is `bircher:queued` and nothing else, so a child is labelled
    only after every create and every link of the plan is satisfied. Costs
    one `issue_or_label` effect per child and a fourth pass in `file_owed`;
    the alternative, teaching the generator to read the parent's journal,
    would not have bound a hand-written queue file.
15. **A run that entered `sliced` ends only as `sliced`.** Children may exist
    from the first create on, and a `failed` over them would leave a filing
    nobody repairs. Costs the runner a state read on the non-zero path and a
    person `cancel_run` as the only other exit.
16. **A closure is an observation, not a property.** The sweep reads every
    child every firing and records reopenings; the outcome guard reads
    current closures. Costs one command, one fact, and N reads per firing
    instead of the unclosed remainder.
17. **The firing that ends the run is the firing that read every child.**
    A failed read ends the firing, and the outcome is refused without a
    `children_observed_closed` under its own generation. The kernel binds the
    order; the reads are the sweep's, as every GitHub observation is, and
    the design says so rather than claiming a guard it cannot have. Costs one
    command and one fact, recorded only on the completing firing — not a
    closure per child per firing, which would grow the journal by N beside
    the dispatch fact each firing already writes.
18. **The kernel refuses the out-of-order queue label; the contract refuses
    the label at creation.** The pass order in `file_owed` is behaviour; the
    property it protects — a queued child has its links — is one the kernel
    can see from the plan and the obligation rows, so it refuses the effect
    that would break it. Costs one precondition on one obligation kind and
    one valued-flag rule; the alternative left the round-4 defect guarded by
    a test of coordinator ordering alone.
19. **The effects are bound at `perform`, not only the facts that follow
    them.** A command's from-set guards the fact; the create is an effect
    performed between commands, and `perform` checks state only for a merge
    today. Each obligation kind this design adds has a precondition the
    kernel checks when the effect is journalled — state, dispatch, epoch,
    the accepted hash, the plan's contents. Costs one table in `perform`;
    without it a coordinator could mint a child before the gate and no
    refusal afterwards would take it back.
20. **The parent's state rides on the observation fact.** A hand-closed
    parent has no close effect to satisfy, and inventing an effect row for
    a mutation nobody performed would be a lie in the journal; the sweep
    reads the parent in the completing firing and the outcome guard accepts
    `parent: closed` in place of a `parent_close` row. Costs one field.
21. **The obligation is bound to the argv.** Metadata that says "slice 2's
    link" on an effect that links slice 3 satisfies nothing; `perform`
    compares the argv with what the kernel holds — the filed numbers, the
    rendered child, the parent — and refuses the pair, and every create must
    carry a `slice_issue` obligation. Costs one binding table and a read-back
    of the created issue for its database id.
22. **A parent a person reopens after the pipeline closed it is the
    person's.** Whether the run has ended or a crash merely preceded the
    outcome, closing it again would fight a deliberate act; the satisfied
    obligation stands, the outcome is recorded, the parent stays as they
    left it. Costs a person the parent they reopened.

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

## Dispositions — round 3 (Kimi, 2026-09-09)

1. accepted — the post-loop `sliced` branch moves the queue file to
   `processed` (the loop is finished with the item; the sweep reads the
   journal), and `create_run` refuses an issue whose earlier run ended
   `sliced`, so a stale file or a re-labelled parent drained after the run
   ended is a refused mint on the runner's existing path, not a second epic
   run (§2 *an issue is sliced once*, §5, two failure-table rows, ruling 12).
   Revision 3's "keeps the queue file out of `processed`" was wrong and is
   withdrawn; round 2's disposition 1 reads "the queue file kept" and is
   superseded here.
2. accepted — a `SHAPING_STATES` set beside `FRONT_HALF_STATES`, and a table
   naming every site that reads the old set with its decision; the model's
   question and ruling channels stay refused from the shaping states,
   `record_model_ruling` refuses the reserved `shape` id from anywhere, and
   `record_human_answer`, `record_prompt_item`, `record_human_direction`,
   `_ALL_ACTIVE` and the reviewer-binding check are each stated (§2, ruling
   13, tests in §8).
3. accepted — the completion step is conditional like the others: "if this
   epoch has no `filing_complete` fact"; a pass with nothing owed issues no
   command, and §8 has the fake kernel count them.
4. accepted — `_review_destination`'s reviewer clauses are named in the
   membership table with their `slices` cases, and the test that an ungated
   accept lands in `slices_accepted` and not `planned` is in §8.
5. accepted — both citations now point at the code and the live log
   (`issues-to-queue.sh`'s journal sweep, finding 12; `human.NOT_FOR_THE_AGENT`,
   finding 13) and say the front-half document does not describe them.
6. accepted — the sweep reads `closedByPullRequestsReferences` and the named
   pull request's `state`; `merged` only for `MERGED`, `closed` otherwise;
   the child run's journal is not consulted, and §8 tests the three cases.
7. accepted — `authz.py:89` and `:84-155`; step 2 passes the key the
   satisfied create carries, whichever generation minted it.

## Dispositions — round 4 (Codex, 2026-09-09)

1. accepted — children are created without `bircher:queued`; `file_owed` runs
   in four passes and the third, the `slice_queue` label per child, starts
   only when every create and every link is satisfied, so a queued child has
   its links and an unqueued one is invisible to the generator; `slice_queue`
   is the seventh obligation kind and `filing_complete` requires it (§1, §2,
   §3, three failure-table rows, the label-history assertion in §8, ruling
   14).
2. accepted — `run_item`'s non-zero-exit branch reads the state back and at
   `sliced` records no outcome and keeps the file; the kernel refuses every
   outcome but `sliced` from `sliced` so the runner's `failed` cannot end a
   half-filed epic whatever the runner does (§2, §5, a failure-table row,
   ruling 15). Round 3's disposition 1 said "a non-zero exit takes the
   existing escalation path"; that was wrong and is superseded here.
3. accepted — `record_slice_reopened` and the definition of a slice's current
   closure; the sweep reads every child every firing; the outcome guard and
   the completion effects read current closures; a three-firing test (§2,
   §3, two failure-table rows, ruling 16).
4. accepted — assertion 4 restated: no filing outside the final epoch, the
   ordering rule wherever a ruling exists, exactly one decision in the final
   epoch, and an epoch superseded before either passes; both passing cases
   are tests (§6, §8).

## Dispositions — round 5 (Kimi, 2026-09-09)

1. accepted, both halves — a failed read ends the run's firing before any
   completion (§3, a failure-table row, a coordinator test), and the kernel
   refuses the outcome without a `children_observed_closed` fact under the
   calling generation (§2, ruling 17, kernel tests). Round 6, finding 3,
   corrected what the second half guarantees: the order, not the reads.
2. accepted, both halves — the `ISSUE_CREATE` contract refuses the three
   runner labels as `--label` values, and a `slice_queue` effect is refused
   at journal time while any create or link obligation of the plan is
   unsatisfied (§2, two failure-table rows, ruling 18, kernel tests).
3. accepted — assertion 1 matches by slice number and says journal order is
   dependency order.
4. accepted — `closedAt` joins the fetch and is the source of `closed_at`.
5. accepted — `is_unblocked` fails closed: a read error skips the issue this
   wave (§5, the seventh runner change, a failure-table row, a self-test).
6. accepted — "a queue file for the parent".

## Dispositions — round 6 (Codex, 2026-09-09)

1. accepted — `create_run`'s refusal reads the attempt, not the outcome: any
   `issue_create` row not reconciled not-delivered in an earlier run of the
   issue, whatever that run's state; a cancel before any create leaves the
   issue mintable; the cancel-after-create row and the "two runs" row say so
   (§2, §7, ruling 12 rewritten, kernel tests).
2. accepted — §6 states what the existing `assert_journal` does over a
   sliced parent and a one-piece run: the model-ruling check excludes the
   `shape` ruling, the phase-set check is per kind of run and admits a
   rejected slice plan, and every other check applies to the parent as to
   any run; §8 tests the four cases. Round 12, finding 1, scoped the
   phase-set check to the final epoch.
3. accepted — the kernel guarantees the order (no outcome without a
   same-generation statement), not the reads; §2, §7 and ruling 17 now say
   so, and round 5's disposition 1 is annotated rather than left
   over-claiming.
4. accepted — the halt row is qualified by pass.

## Dispositions — round 7 (Kimi, 2026-09-09)

1. accepted — both `FRONT_PHASES` lists gain `slices`, stated in the
   membership table with what each consumer does without it; §8 tests
   `is_front_verdict`, `revisions_used` and the proof's re-render of a
   sliced parent's brief.
2. accepted — `approve_artifact`'s destination site is named in the
   membership table and tested.
3. accepted — two effects, then the command.
4. accepted — "no filing command issued"; the pass's own label effect is
   named.
5. accepted — the parent is read in the completing firing after the
   children; the close is performed only if open, and a hand-closed parent
   satisfies the obligation by that observation.
6. accepted — the rationale now says what is avoided: a closure per child
   per firing beside the dispatch fact each firing writes anyway.

## Dispositions — round 8 (Codex, 2026-09-09)

1. accepted — a table of what `perform` refuses per obligation kind (§2,
   *what `perform` refuses*): a `slice_issue` create is refused unless the
   run is in `sliced`, under the coordinator's operator dispatch, in the
   current epoch, against the hash the gate or `advance_ungated` named, for
   a slice in that plan; the other kinds likewise; a failure-table row and
   the tests §8 lists, refusal before approval first among them (ruling
   19).
2. accepted — the parent's state rides on `children_observed_closed`
   (`parent: open | closed`), read in the completing firing after the
   children; the close is performed only when open, and the outcome guard
   accepts `parent: closed` in place of a `parent_close` row, so a
   hand-closed parent with no close attempt ends cleanly without an
   invented effect row (§2, §3, §7, ruling 20, tests). Round 7's
   disposition 5 said the obligation was "recorded satisfied by that
   observation" without saying how; this is how.

## Dispositions — round 9 (Kimi, 2026-09-09)

1. accepted — every filing obligation now carries `plan_hash` (§3's
   payloads for `slice_dependency`, `slice_queue` and `umbrella_label`
   extended), the table's common conditions are stated once and scoped to
   the fields a kind carries, and the completion pair is marked as carrying
   neither `plan_hash` nor `slice`.
2. accepted — `hash8` is defined at its first use in §3.
3. accepted — the three missing planted refusals are in §8.
4. accepted — `intended`, `confirmed`, `uncertain`, reconciled.
5. accepted — the refused mint's note is a captured refusal, and §5 says
   the capture is code.
6. accepted — the prefix pin and its companions are named in §3 as
   expected reds.

## Dispositions — round 10 (Codex, 2026-09-09)

1. accepted — a binding table beside the precondition table (§2, *bound to
   the effect*): every `ISSUE_CREATE` must carry a `slice_issue`
   obligation, each kind is admitted on one class and operation, and the
   argv must name the targets the kernel holds — the filed numbers and
   database ids, the rendered child body, the parent; the create's
   confirmation reads the database id back and `slice_filed` records it;
   a failure-table row, ruling 21, and a planted refusal per binding in
   §8. Round 8's disposition 1 claimed the state binding for every create;
   the obligation requirement is what makes that true.
2. accepted — the reopened-after-close case is decided: the satisfied
   close stands, no second close is performed, the outcome is recorded and
   the parent is the person's (§2 outcome guard, §3, a failure-table row,
   ruling 22, a test).

## Dispositions — round 11 (Kimi, 2026-09-09)

1. accepted — the mandate covers every argv shape this design adds: the
   blocked-by `POST`, `--add-label bircher:queued` and `bircher:sliced`, a
   `bircher: sliced ` comment, and `gh issue close` on a `sliced` run's
   issue each require their kind; the runner's own swap is unaffected (§2,
   tests in §8).
2. accepted — `test_prefixes_are_exactly_six_and_not_the_generic_one` at
   `:32`, seven after the change, and the stale "Five" comment named.
3. accepted — the create is refused while a blocker is unfiled, and
   `render_child` is strict.

## Dispositions — round 12 (Codex, 2026-09-09)

1. accepted — the phase-set check reads the final epoch only; earlier
   epochs hold whatever they reached before being superseded, and the
   binding and session checks still read every epoch (§6, two proof tests
   in §8, round 6's disposition 2 annotated).
