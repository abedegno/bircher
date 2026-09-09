# The shaping phase — an epic becomes its slices before anyone specs it

*2026-09-09. Design for one new phase in the v2 front half. Binding authority
for its plan; argues from, and does not restate, the front-half design
(`2026-09-05-front-half-design.md`), which it extends. Where this document
names a mechanism without defining it, the front-half design defines it.*

## §0 The claim, and why the front half falls short of it

The front half's claim is a vague issue in and a merged pull request out. Its
live runs met that twice and failed it once, and the failure was the shape of
the issue rather than the loop: muesli #12 produced a 15 KB spec and a plan
naming forty-five files across nine tasks, which the back half turned into a
5,297-line pull request nobody could review and nobody merged. Muesli #11, one
piece of work, converged in five rounds and merged. The loop could see the
difference in its own artefacts long before a person did, and did nothing
with it.

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

## §1 Policy

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

## §2 Kernel

### States

`create_run` lands a run in **`shaping`**, not `queued`. `queued` keeps its
meaning — the state from which a spec round starts — and every state after it
is unchanged. `_PHASE_OF_STATE` maps the three shaping states to phase
`slices`; `phase_of` therefore derives the phase for every new command as it
does for the old ones, and a review of the slices cannot be recorded against
the spec.

| From | Command | To | Notes |
|---|---|---|---|
| — (no run) | `create_run` | `shaping` | unchanged otherwise: snapshot, `policy_frozen`, base sha |
| `shaping` | `record_one_piece` | `queued` | the model ruling; the run proceeds as today |
| `shaping` | `submit_slices` | `slices_submitted` | the artefact, hashed, PUT to the store |
| `slices_submitted` | `record_review(accept)` | `slices_accepted` | the reviewer's verdict, bound as every verdict is |
| `slices_submitted` | `record_review(request_revision)` | `shaping` | one revision destination, as every phase has |
| `slices_accepted` | `approve_artifact` | `sliced` | the human gate, when `slices ∈ gates` |
| `slices_accepted` | `advance_ungated` | `sliced` | the coordinator's transition when `slices ∉ gates`; refused when it is |
| `sliced` | `record_run_outcome(sliced)` | `ended` | recorded by the sweep when every child is closed (§3) |
| any of the three, and `sliced` | `revise_bundle` | `shaping`, epoch + 1 | unchanged rule; a revised epic is shaped again |
| any of the three, and `sliced` | `park`, `record_human_direction`, `dismiss_human_item`, `cancel_run` | as today | the front-half vocabulary applies |

`sliced` is a **front-half state that the loop does not work in**: `run_loop`
exits `OK` on it as it exits on `planned`, and `_LOOP_STATES` does not include
it. It is open — not `ended`, not `cancelled` — so `_kernel_find_run … open`
adopts it rather than minting a second run for a re-queued parent, and it
holds no `parked` fact, so the generator's journal sweep (§5) does not queue
it. It ends only when its children have.

The outcome set `_RUN_OUTCOMES` (`authz.py:973`) gains **`sliced`**.
`record_run_outcome(sliced)` is legal only from `sliced`, and only when the
kernel can see that every child the run filed is closed — by the
`slice_closed` facts the sweep records (§3), one per child, before it records
the outcome. An outcome recorded with a child still open is refused.

### The artefact: a slice plan

A slice plan is bytes the kernel holds under its hash, like a spec. Its grammar
is fixed so the kernel can refuse a malformed one from the bytes, as it refuses
a plan with no `### Task` heading today — a shape check, not a judgement:

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
not parsed.

`submit_slices(artifact_hash)` is refused when:

| Refused when | Why |
|---|---|
| fewer than two or more than five slices | one slice is a one-piece issue misfiled; six is the atomic-step slicing the grooming rule forbids (`~3–5 coarse, coherent, independently-reviewable capabilities`) |
| any slice lacks a title, a `Scope:`, a `Non-goals:` or a `Depends on:` | a slice without non-goals grows into its siblings; one without a scope is a title |
| a `Scope:` under three sentences or over six, by full stops | shorter is a title, longer is a spec — the slice's own spec phase decides the rest |
| a dependency names a slice not in the plan, names itself, or the dependency graph has a cycle | the children are filed in dependency order (§3); a cycle has no order |
| the frozen bundle's labels include `bircher:slice` | **a slice is not sliced.** Depth is one by construction, not by a counter the coordinator carries |
| the hash equals a plan previously submitted for this phase in this epoch | identical resubmission, as every phase |
| the calling generation is not an `author` dispatch, or its actor is not the vendor of the round's session | as `submit_spec` |
| a `human_direction` of the run is newer than the calling generation's dispatch | as `submit_spec` |

The scope-length refusal is the one judgement-shaped rule in the table, and it
is kept because it is observable and because both live failures of the plan
phase were documents that grew: a bound on the slice's own text is the cheapest
way to keep a slice plan from becoming five specs.

### `record_one_piece(reasoning, cost_if_wrong)`

A model ruling with a transition. Legal from `shaping` under an `author`
dispatch whose actor is the vendor of the round's session, after the turn is
observed ended and its stop satisfied (§3 *The turn's end is a fact* applies to
this command as to every output-recording command). Refused when either string
is empty or whitespace: a ruling without its reasoning is the "MUST state"
placeholder, and its cost-if-wrong is what a person reads when the spec phase
later shows the issue was an epic after all. Records `model_ruling {question_id:
"shape", ruling: "one piece", reasoning, cost_if_wrong}` and moves the run to
`queued`. A run may carry at most one such ruling per epoch; a second is
refused as an identical resubmission is.

### `advance_ungated`

The coordinator's own transition from `slices_accepted` to `sliced` when the
policy holds no slice gate. Legal only under an `operator` dispatch of the
coordinator and only when `slices ∉ gates`; refused otherwise, so a coordinator
cannot skip a gate the policy set. Its fact is `artifact_advanced {phase:
slices, hash}`. The spec phase has no equivalent because its ungated path
is `record_review(accept)` landing directly on `specified`; the shaping phase
separates the two because filing children is an effect with an obligation
(§3), and the transition that authorises it must be its own fact.

### The effect class: `issue_create`

`EffectClass` gains **`ISSUE_CREATE`**. Creating an issue mints work, a
different blast radius from editing or labelling one, and it is the first
externally visible mutation of the pipeline that produces something another
run will consume. The routing detector (`test_routing.py`) classifies `gh
issue create` as mutating, and the effect inventory gains its site. Its
contract rule: argv is exactly `gh issue create --repo <repo> --title <title>
--body-file <path> --label <l>…`, the body file written by the executor from
the intent's `body.text` after the contract check — the body never rides argv,
for the reason the session events rule gives — and the confirmed row's
`external_object_id` is the created issue's URL, from which the number is
parsed as `from_gh` parses comment ids.

The dependency links are `issue_or_label` effects: `gh api -X POST
/repos/<repo>/issues/<child>/dependencies/blocked_by -f issue_id=<blocker id>`,
the write half of the endpoint the queue generator already reads
(`is_unblocked`, `issues-to-queue.sh`). The umbrella comment is a `comment`
effect; the label swaps are `issue_or_label`; the parent's close at the end is
`issue_or_label`. None of them is new in kind.

### Facts

| Fact | Written by | Payload |
|---|---|---|
| `artifact_submitted` | `submit_slices` | as today, `phase: slices` |
| `review_verdict` | `record_review` | as today, `phase: slices` |
| `model_ruling` | `record_one_piece` | `question_id: "shape"`, `ruling`, `reasoning`, `cost_if_wrong` |
| `human_ruling` | `approve_artifact` | as today, `phase: slices` |
| `artifact_advanced` | `advance_ungated` | `phase: slices`, `hash` |
| `slice_filed` | the coordinator, after each child's create is confirmed | `slice: n`, `issue: number`, `title`, `parent: number`, `plan_hash` |
| `slice_closed` | the sweep, when a child is observed closed | `slice: n`, `issue: number`, `closed_at` |
| `run_ended` | `record_run_outcome(sliced)` | as today, `outcome: sliced` |

`slice_filed` is what the proof and the sweep read; it is written from the
confirmed effect's `external_object_id`, so the issue number in it is observed
from the server's answer, never asserted by the coordinator (§9 of the
front-half design's provenance rule).

## §3 Coordinator

### The shaping round

`run_loop` gains one branch: at `shaping` it runs `shape_round(ctx)`, the
author round's twin. Same seat and vendor choice (`choose_author_vendor`, phase
`slices`), same `run_turn` under the one rule, with **two watched paths**:
`bircher/shape.md` and `bircher/artifact.md`. At the turn's end:

- `shape.md` present and parsing to a single `Ruling: one piece — <reasoning>
  — <cost if wrong>` line: `record_one_piece`, the round returns `one_piece`,
  the loop continues and the next pass starts the spec round from `queued`.
  An `artifact.md` beside it is ignored, as a draft beside a questions file is
  ignored under the grill: the ruling is read first.
- `artifact.md` present, no ruling: `submit_slices`, the round returns
  `submitted`, and the next pass runs the review round.
- neither: the empty turn, `record_author_empty` and one retry, as the author
  round.
- a `shape.md` that does not parse: the empty turn. A ruling with no
  reasoning is not a ruling.

The brief (`author_brief`, phase `slices`) carries: the frozen snapshot, the
policy, the grooming definition of coarse in its own words, the slice-plan
grammar, the instruction that a slice plan names no files, tasks or acceptance
tests, and the two ways to end the turn. On a revision it carries the previous
plan, the findings and the dispositions requirement, as every phase does. The
skill is `skills/shape-author/SKILL.md`, a sibling of `spec-author`.

### The review round

`review_round` at `slices_submitted` is the review round at `spec_submitted`
with the phase name changed: template 2 brief, prior findings from the second
round, severities, the verdict rule, dispositions. One paragraph joins the
brief for this phase, telling the reviewer what it is grading: whether each
slice is a capability a reviewer could see working end to end, whether any
slice is a bare step of another, whether the dependencies are real, and
whether the union covers the issue with nothing invented. Not the design of
any slice, which is its own spec phase's work.

### The gate

At `slices_accepted` with `slices ∈ gates`, `stall(ctx, "gate", …)` as for the
spec: the park, the notice on the parent issue with the plan attached, the
prompt in the carrier session opening with the address to the model (§4 of
the front-half design as amended). `approve` moves it to `sliced`; corrections
are the next round's findings; `cancel` cancels. `retry` is refused here as at
the spec gate. With `slices ∉ gates`, the same pass records `advance_ungated`
and continues to filing.

### Filing the children

`file_owed(ctx)` runs in the loop beside `publish_owed`, and is the last thing
a pass at `sliced` does. For each slice of the accepted plan, in dependency
order (a topological order; ties by slice number), with obligation
`{kind: slice_issue, run, slice: n, parent, plan_hash}` and key
`slice:<run>:<n>:<generation>`:

1. If a satisfied `issue_create` with this obligation exists, take its issue
   number from the confirmed row and continue.
2. Otherwise compose the child — title: the slice title; body: the marker line
   `Slice n of #<parent> (bircher shaping <hash8>)`, a blank line, the scope,
   `Non-goals: …`, and `Depends on: #<x>, #<y>` with the siblings' numbers
   already filed, or `Depends on: none`; labels: `bircher:queued`,
   `bircher:slice`, and the parent's inherited policy labels — and perform the
   `issue_create`. Record `slice_filed` from the confirmed row.
3. For each dependency, an `issue_or_label` effect with obligation
   `{kind: slice_dependency, run, slice: n, blocker: m}` posting the
   blocked-by link.

Then, once, with obligation `{kind: umbrella, run, plan_hash}`: the comment
`bircher: sliced <hash8>` followed by one line per child, `- #<number> Slice n:
<title>`, and the label swap `bircher:running` → `bircher:sliced` on the
parent. Every step is owed: a crash anywhere leaves obligations unsatisfied
and the next pass performs what is missing, and an uncertain create halts the
run (§3 *Sessions are effects*) for reconciliation, which lists the parent's
children by the marker line to say what exists.

`bircher: sliced ` joins `BIRCHER_STATUS_PREFIXES` in both copies of the
predicate, so the umbrella never freezes into a bundle.

### The sweep

Each firing of the scheduled wave, before it generates the queue, runs
`sweep_sliced(ctx)` over every open run in state `sliced` (from the journal,
by `open_run_ids`): for each child in `slice_filed` with no `slice_closed`,
read the issue; if closed, record `slice_closed`. When every child has one,
post `bircher: sliced complete` on the parent (a `comment` effect, obligation
`{kind: umbrella_close, run}`), close the parent (`issue_or_label`), and
`record_run_outcome(sliced)`. Under the parent run's own operator generation,
so the journal holds the story from epic to closure. A child abandoned keeps
its parent open; that is the honest state, and the notice a person sees on
the parent is the umbrella comment naming the child that never closed.

## §4 Human interaction

Nothing new in kind. The gate takes `approve`, corrections, or `cancel`. A
`direct` at `shaping` displaces a live shaping turn and starts the next round
with the direction as its findings — "this is one piece, do not slice it" or
"slice it along these lines" — which is how a person overrides the shaper
without editing the issue. The park notice names the phase `slices` and says
what to type, as every notice does. A `grill` park cannot occur in this phase:
the shaper asks no questions (§3), and `record_model_question` is refused
under a `slices` generation.

## §5 Runner

Three changes, none to `run_item`'s flow:

- `_preflight_labels` checks `bircher:slice` and `bircher:sliced` beside
  `running` and `escalated`.
- `issues-to-queue.sh`'s journal sweep (which queues open runs with a current
  park) is unchanged; a `sliced` run holds no park and is not queued. Children
  are ordinary queued issues; a blocked child waits on `is_unblocked` until its
  blocker closes, which a merged pull request does through `Closes #n`.
- The wave calls `coordinator.cli sweep-sliced --db … --server … --repo …`
  once per firing, before queue generation, under the runner's own operator
  dispatch per parent run.

The scorecard gains an outcome row `sliced` naming the children filed.

## §6 Proof

`prove_front_half` gains four assertions, run over a parent and its children
together (`--children` reads the `slice_filed` facts and proves each child's
run as an ordinary run):

1. **The children are the plan.** The accepted plan's bytes parse to the same
   slices the `slice_filed` facts name, one per slice, in order; each child
   issue's body, fetched live, opens with the marker naming this run's plan
   hash8, and its title equals its slice's.
2. **The dependencies are the links.** Every `Depends on:` in the plan is a
   blocked-by link on the child, fetched live, and the child has no other.
3. **The umbrella names the children.** The parent's `bircher: sliced` comment
   lists exactly the numbers in `slice_filed`.
4. **A ruling is not a plan.** A run with a `shape` `model_ruling` has no
   `slices` artefact and no `slice_filed`; a run with `slice_filed` has no
   `shape` ruling in the same epoch.

Under mode `zero`, no park at the slice gate; under mode `approval`, exactly
one `human_ruling {approve}` at it is admitted beside the spec gate's. Every
assertion is exercised by a planted defect in the tests, as §8 of the
front-half design requires of its own.

## §7 Failure table

| Situation | What happens |
|---|---|
| Shaper writes both `shape.md` and `artifact.md` | the ruling is read; the artefact is ignored and moved aside before any re-prompt |
| Shaper writes a six-slice plan | `submit_slices` refused; the refusal is the next round's findings, as the grill refusal is |
| Reviewer rejects three times | `bound_exhausted`, the park, the notice: the person grants a round, corrects, or cancels — or directs "one piece" |
| A child is itself an epic | its shaper cannot slice it (`bircher:slice` refuses); it proceeds as one piece and its spec phase does what it can; the reviewer's findings on an oversized spec are the signal a person reads |
| Crash between a child's create and its `slice_filed` | the effect is `intended` or `uncertain`; the run halts; reconciliation lists children by marker; the next pass files the rest |
| Two runs shape the same parent | impossible by the open-run guard: the parent's run is open in `sliced` |
| The parent is re-labelled `bircher:queued` by a person | `_kernel_find_run … open` adopts the `sliced` run; the loop exits OK on it; the label is swapped back to `sliced` by the umbrella step's replay. Nothing is re-sliced |
| A child is closed without merging | its `slice_closed` is recorded like any other close; the parent closes when all are; the umbrella-close comment names each child's final state so a closed-not-merged child is visible |
| `bircher:sliced` missing on the repo | preflight refuses to start, naming it |

## §8 Tests

Kernel: every row of the state table and every refusal in §2, each named by a
test that reproduces it — the grammar cases (no scope, a two-sentence scope, a
seven-sentence scope, a missing non-goals line, a dependency on slice 9, a
self-dependency, a three-slice cycle, six slices, one slice), the
slice-of-a-slice, the empty ruling strings, the second ruling, the ungated
advance under a gated policy, the outcome with a child still open.

Coordinator, through the fake server: both endings of the shaping round and
the both-files case; the review round with a `slices` brief that carries the
coarse paragraph; the gate park and its notice; `file_owed` with a crash
planted after the first create — the second pass files slices two and three
and not a second slice one; dependency order with a plan whose slice 1 depends
on slice 2; the umbrella comment's exact text; `sweep_sliced` closing a parent
whose children are all closed and leaving one whose child is open, and
recording the outcome only in the first case.

Runner self-test: the two labels in preflight; a blocked child skipped by the
generator until its blocker closes.

Proof: each of the four assertions reds on its planted defect.

Every new condition's mutation is executed, not argued; the review package for
each task states which line was mutated and which test went red.

## §9 Out of scope

Recursive slicing, refused by the kernel. Any estimate of slice size in lines
or files; coarse is the reviewer's judgement. Re-slicing a parent whose
children stalled; a person cancels the children and directs the parent. Any
change to the back half. A spec for the epic itself; its children have specs.
Shaping issues that are not vague: the phase runs on every issue, and a clear
one-piece issue costs one turn and a ruling.

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
4. **Depth one by label, not by counter.** `bircher:slice` on the child's
   frozen bundle is what the kernel refuses on; a counter the coordinator
   carried would be a claim. Costs nothing if wrong beyond a child that cannot
   be sliced further and must be worked as one piece.
5. **A scope-length bound in the kernel.** The one judgement-shaped refusal,
   kept because both live failures of the plan phase were documents that grew
   and this is the cheapest observable bound. Costs, if wrong, a refused plan
   whose author counted full stops differently from the parser; the refusal
   names the count.
6. **A new effect class rather than `issue_or_label`.** Creation mints work
   that another run consumes; keeping it distinct keeps the routing inventory
   honest about what the pipeline can do to a repository. Costs one class.
