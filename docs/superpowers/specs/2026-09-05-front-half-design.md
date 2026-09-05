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
(`v2/kernel/authz.py:37`), and `request_revision` always lands in `planned`
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
after enqueue, under the `(model, {})` policy.

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
grill:      human | model            default: model
gates:      subset of {spec, plan}   default: {spec}
max_rounds: integer 1..5, per phase  default: 3
```

- `grill=human`: the spec author must ask the human at least once and must not
  submit with a question unanswered. `grill=model`: the author rules on its
  own questions; each ruling is recorded with its reasoning and its stated
  cost if wrong, as the SDD ledger already does for plans.
- `gates`: the phases whose accepted artefact additionally needs a human
  approval before the run advances.
- `max_rounds`: reviewer-driven revisions per phase before the run parks for
  the human. Human rulings are not counted.

**Derivation.** Project default from the omnigent Project `config`, overridden
per issue by labels: `bircher:grill` → `grill=human`; `bircher:autonomous` →
`gates={}`; `bircher:gate-plan` → adds `plan`. Derived once, at run creation,
and recorded as a `policy_frozen` fact in the same transaction that snapshots
the issue. A second `policy_frozen` on a run is refused. The frozen bundle's
labels are part of its hash, so the derivation is reproducible from the
snapshot.

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
| `approve_artifact(hash)` | `human_ruling` | `spec_submitted`, `spec_accepted` | `specified` |
| | | `plan_submitted`, `plan_accepted` | `planned` |
| `revise_bundle(snapshot)` | — | every front-half state | `queued` |
| `record_model_question` / `record_human_answer` | — | every front-half state | no transition |
| `create_run(snapshot, policy)` | — | no run | `queued`, writing `policy_frozen` in the same transaction |
| `start_implementation` | — | `planned` | `implementing` (unchanged) |
| `record_run_outcome`, `cancel_run` | — | from-sets gain the four new states | unchanged |

One rule behind every revision destination: **`request_revision` returns the
run to the state the artefact was submitted from.** Implementation → `planned`,
plan → `specified`, spec → `queued`.

### Refusals

Each is a kernel check against facts the kernel holds. The coordinator may
observe the refusal; it may not pre-empt it.

| Refused | When |
|---|---|
| `submit_spec` | `grill=human` and there is no `human_answer` fact, or a `model_question` fact is newer than the last `human_answer` |
| `submit_spec`, `submit_plan` | the hash equals any artefact previously submitted on this run — a resubmission that did not change is not a revision |
| `submit_plan` | the hash equals the run's current spec hash — the runner's ceremony becomes a refusal |
| `record_review(request_revision, review_ruling)` | this phase already carries `max_rounds` reviewer-driven revisions. Human rulings are never bounded |
| `record_review(reject)` | from any front-half state. Bound exhaustion parks; it does not terminate |
| `record_review` from `*_accepted` | unless its decision type is `human_ruling` — only the human moves a gated run |
| `approve_artifact` | the hash differs from the run's current artefact of that phase. The kernel holds the hash; the caller can only supply the right answer |
| anything writing `policy_frozen` after creation | there is no such command. `create_run` writes it in the creation transaction and is idempotent on `run_id`, as `enqueue` is today |

`record_human_answer` and `approve_artifact` are operator-side entry points in
the sense `grill.py` already establishes: a model session cannot reach the
function, and there is no parameter it can pass to become the human.

### Grill facts

`grill.py` changes from one answer per question to one answer per human
message: `human_answer` carries `{question_ids: [...], answer: text}`,
referencing every question open when the message arrived. The `submit_spec`
guard counts facts, not text. `model_question` under `grill=model` is still
recorded — with the model's own ruling appended as `model_ruling
{question_id, ruling, reasoning, cost_if_wrong}` — so the spec's decision
ledger is in the journal, not only in the artefact.

### Bundle revision

`revise_bundle` is today a function; it becomes a command. On resumption the
coordinator re-snapshots the issue; if `is_relevant_change(old, new)` it
submits `revise_bundle`, which records `bundle_revised` with the diff and moves
the run to `queued`. The next author round is briefed with the prior artefact
and the diff as findings. Not charged against `max_rounds`: nobody's review
was wrong.

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
  state ← kernel.state(run)
  queued | specified          → out ← author_round(phase)
                                 questions               → record them; park(grill)
                                 artefact                → submit(phase, put_artifact(artefact))
  spec_submitted | plan_submitted
                              → verdict, findings ← review_round(phase, hash)
                                 None                    → park(no_verdict)
                                 PASS                    → record_review(accept)
                                 FAIL, revisions left    → record_review(request_revision, findings)
                                 FAIL, none left         → park(bound_exhausted, findings)
  spec_accepted | plan_accepted
                              → park(gate)
  planned                     → exit 0
```

where `phase` is `spec` for `queued` and `spec_*`, `plan` for `specified` and
`plan_*`. A run already at or beyond `planned` exits `0` at once.
`author_round` resumes the sidecar's session when the park reason was `grill`
and the human has answered (§4); in every other case it dispatches a fresh
session.

On `RC_PARKED` the coordinator itself writes the sidecar
`<queue-dir>/<code>.parked` (§5) — atomically, and only after the kernel
state it names is durable — because it, not the runner, knows the session and
item ids.

**Idempotency keys** `phase:<run>:<phase>:<round>:<gen>:<kind>`, `round` read
from the journal as that phase's revisions used + 1. Every key carries the
generation, per the rule that a key naming "one per run" is a replay once a
loop exists.

### Author round

A fresh session, agent `v2_author`: `v2_implementer`'s Landlock and egress
bundle, no push allowance, a worktree at the run's base sha. It is briefed
**from files**, never from a pasted history:

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
and the spec (when reviewing a plan). It returns `VERDICT: PASS|FAIL` and
findings through the same `extract_verdict`; nonce `{hash8}-g{gen}`; worktree
cleared before creation. `None` is not a soft PASS.

### Rotation

Round *r*'s reviewer is the vendor that did not author the artefact under
review. The vendor that reviewed round *r* authors the revision in round
*r+1*; the other reviews it. A reviewer never grades its own prescriptions —
the defect that memory records was accepted two lines from the text under
review.

### Artefacts

Bytes go into the kernel artifact store; the hash is what is submitted. A
human-readable copy lands at `<bundle-dir>/<run>/<phase>-r<round>.md`. At
`specified` and `planned` the kernel publishes the approved artefact to the
issue as a comment through the C8 effect path — a record, not an approval
surface.

## §4 Human interaction

Two park reasons need the human: **grill** (`grill=human`, questions pending)
and **gate** (`*_accepted`, or `*_submitted` at bound exhaustion). In both the
last thing in the session is a turn stating what is needed. For a grill that
is the author's own questions. For a gate the coordinator prompts the author
session — or a fresh `v2_author` session if it is gone — with the artefact and
"Reply `approve`, or give corrections." The sidecar records the id of the last
item in the session at park time.

On the next pass the coordinator lists items after that id and takes, in
order, **every user-role item whose content hash is not a prompt the
coordinator sent**. It knows its own prompts because each one is recorded as
a `prompt_sent {session_id, sha256}` fact before it is sent — the discriminator
reads the journal, not the memory of the process that parked.

- **grill:** each such message becomes one `human_answer` fact referencing the
  question ids open at that moment. The same session is re-prompted:
  "Answered; continue." The author may ask again — a new round, a new park —
  or write the artefact.
- **gate:** if the first non-empty line, trimmed of whitespace and trailing
  punctuation, is `approve` (case-insensitive) →
  `approve_artifact(hash)`, the hash being the kernel's current artefact for
  the phase. Anything else → `record_review(human_ruling, request_revision,
  findings = the message)`; the loop resumes with an author round briefed on
  the human's words.
- **Fallback:** `kernel approve --run <id> --phase <spec|plan>` and `kernel
  revise --run <id> --phase <spec|plan> --findings <file>`. The same commands,
  from the operator's shell.

**The honesty claim, exactly.** The NAS runs `OMNIGENT_AUTH_ENABLED: "0"`, so
`created_by` is `None` on every item and cannot distinguish the human from the
runner. A user-role item is therefore human-authored iff nothing but the UI
and the coordinator can POST into that session. The coordinator excludes its
own by hash. The model is excluded by the `v2_author` bundle's default-deny
egress — `v2_implementer` allows only `GET api.github.com/repos/…/**`, and
the env-boundary tests pin the bundle. Any other process on the docker network
is a **deployment residual, listed as asserted** in the provenance table.
Enabling omnigent accounts mode would make `created_by` observed; it is a
deployment change outside this spec.

Notification that a run has parked is the operator's job. The morning summary
lists parked runs with their reason.

## §5 Parking and resumption

**Park** = the coordinator exits `RC_PARKED` with the run in a durable
front-half state and **no `record_run_outcome`**. The kernel's outcome set is
unchanged because the run has not ended. The coordinator has written
`<queue-dir>/<code>.parked` = `{run_id, state, session_id, last_item_id,
reason, findings_path}` (§3). `run_item` then keeps the queue file where it is
and records the scorecard row `outcome=parked` — runner vocabulary only.

**Resume** = the next pass over that queue item. `run_item` finds the sidecar,
exports `BIRCHER_RUN_ID` from it, re-fences through `_kernel_dispatch` for a
fresh generation — the deferred-merge path at `run-queue.sh:1946` is the
precedent — re-snapshots the issue, submits `revise_bundle` if the change is
relevant, and calls `phases`, which re-enters at §4.

The resumable-escalation design (2026-08-30) is not used: it is superseded, and
its one destination (`implementing`) is wrong for a run parked at a gate. What
it established still binds: resumption inherits history, never judgement.
Here the artefacts and the human's rulings are history; a review made stale by
the issue changing is handled by `revise_bundle`'s transition to `queued`, not
by trusting the old verdict.

**Leak guard.** A crash between the kernel transition and the sidecar write
leaves an open run with no sidecar. `run_item` refuses to mint a new run while
the kernel holds an open (non-`ended`, non-`cancelled`) run whose id carries
this item code. A lost sidecar surfaces as a refusal naming the run, not as a
second run.

## §6 Handoff

From `planned`, `run_item` continues at `_kernel_start_implementation` exactly
as today. Three changes at the seam:

- The implementer's brief is the spec and the plan, read from the store by the
  hashes the kernel holds, plus the issue snapshot — not the queue prompt.
- The two ceremonial submits at `run-queue.sh:4083-4084` are deleted, and the
  "deliberate reuse" comment in `kernel-client.sh`'s `submit_plan` wrapper
  with them. Under §2 they would be refused.
- **Every run goes through the front half.** A fully-specified issue costs one
  short author round and one review seat per phase. There is no bypass, so
  there is no second path to keep honest.

`skills/muesli-loop/SKILL.md` §2's STOP-on-ambiguity is retired: ambiguity is
the front half's job, and the implementer implements a plan. The `.escalated`
mechanism stays for what it was built for — an implementer that cannot proceed
for reasons no plan foresaw.

Per-task review seats and the whole-branch mutation sweep from the trial stay
out on cost. The whole-branch cross-vendor review and the repair loop remain
the code gate. The mutation sweep is the first follow-up once this is live.

## §7 Errors

| Case | Handling |
|---|---|
| Reviewer produced no parseable verdict | park `no_verdict`. No round consumed; the human decides whether to retry or rule |
| Author produced neither artefact nor questions | one retry with a fresh session (an attempt nonce joins the key), then `RC_FAILED` |
| `submit_*` refused as identical to a prior artefact | treated as a FAIL with findings "identical to the prior artefact"; one re-author, then park `identical_resubmission` |
| Session creation fails | `RC_FAILED`; `run_item` records `failed` as today |
| Kernel refusal the loop did not expect | `RC_FAILED` with the refusal reason logged; never retried blind. The transient-refusal design's classes decide what is retryable |
| Author turn exceeds `HARNESS_TURN_TIMEOUT_S` | a prerequisite, not a design point: the bircher runner's 480 s (gap 15) must be raised before the live proof. Spec authoring on a vague issue is one long turn |
| Human message arrives while no pass is running | it waits in the session. Nothing is lost; the next pass reads it |

## §8 Testing and proof

**Kernel.** A real-kernel test for every row of the §2 command table and
every row of the refusal table. Each refusal test carries its disagreeing
case — the input that passes only if the guard is gone: a caller claim that
contradicts the `policy_frozen` fact; `approve_artifact` with a hash one byte
off; the `max_rounds+1`th `review_ruling` refused while a `human_ruling`
passes in the same state; a byte-identical resubmission; a plan whose hash is
the spec's; `record_review` from `spec_accepted` with a `review_ruling`.
Mutation discipline as before: commit before each mutation, a mutation that
did not apply is not a result.

**Coordinator.** Loop tests against a real journal with fake sessions, the
`test_repair_loop` pattern: every branch of §3, every park reason, resumption
from every parked state, the relevant-change path through `revise_bundle`, and
the leak guard. The discriminator test includes the coordinator's own prompt
in the item list and asserts it is **not** taken as human — the case a working
fallback would shadow.

**Runner.** Self-tests for the sidecar write order, the `parked` row, the
resume path's generation re-fence, and that `run_item` no longer calls
`_kernel_submit_spec` with the prompt.

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
| policy | `policy_frozen` fact, written once at creation | observed |
| bundle snapshot | kernel, from the provider at creation and on resume | observed |
| reviewer verdict on an artefact | the reviewer session's output | asserted, permanently, as every verdict is |
| human approval / correction | a user-role session item not sent by the coordinator, under a default-deny bundle | observed for the model and the coordinator; **asserted** for any other process on the docker network |
| human answer to a grill question | as above | as above |

## §10 Where it lands

- `v2/kernel/authz.py` — four states, the command and refusal tables.
  `v2/kernel/policy.py` (new) — derivation and the `policy_frozen` fact.
  `v2/kernel/grill.py` — many-question answers, `model_ruling`.
  `v2/kernel/enqueue.py` — `create_run(run_id, base_repo, base_sha, snapshot,
  policy)`, no spec or plan bytes. `v2/kernel/bundle.py` — `revise_bundle` as
  a command.
- `v2/coordinator/phases.py` (new) — the loop. `author.py` (new) — author
  dispatch and the artefact/questions contract. `human.py` (new) — the session
  reader and discriminator. `review.py` — artefact mode. `cli.py` — `phases`,
  `approve`, `revise`.
- `agents/v2_author/` (new bundle). `skills/spec-author/`, `skills/plan-author/`
  — composed from the upstream skills.
- `batch/run-queue.sh` — run creation through `create_run`, the `phases` call,
  the sidecar, `parked`, the leak guard, the two ceremonial submits deleted.
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
