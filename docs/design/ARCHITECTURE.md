# Bircher v2 — architecture, nomenclature and operation

**Status: WORK IN PROGRESS.** Bircher v2 is mid-migration. This document
describes the TARGET architecture and marks, at every point, where today's code
does not yet reach it. Every factual claim was read out of the code on
2026-08-30, not recalled.

Two conventions used throughout:

> **TARGET —** what the design is moving toward.

> **GAP —** where the code differs from that today, and what it costs.

Read §2 Nomenclature first. Most confusion in this project has come from three
different things sharing two names.

---

## 1. What Bircher is

An autonomous development agent. It takes an item from a backlog, gets a change
implemented and independently reviewed, and merges it — with a kernel that
authorises and journals every externally visible mutation, so that afterwards
you can prove what happened rather than believe a report.

Its subject is `abedegno/muesli`. Its own code lives in `abedegno/bircher`.

The design premise: **a model's report of what it did is not evidence.** Every
decision that matters is derived from the repository or refused.

---

## 2. Nomenclature

Three distinct things. Two are called "bircher"; two have been called
"coordinator". Precision here is not pedantry — the ambiguity has produced
several wrong conclusions.

| Term to use | What it is | Where |
|---|---|---|
| **the runner** | The bash orchestrator. Deterministic, no model. Drives the queue, creates sessions, derives outcomes, performs merges. | `batch/run-queue.sh` |
| **the coordinator** | A Python package inside the runner. Not an agent, no session, no model. Invoked as a subprocess to derive an outcome. | `v2/coordinator/` |
| **the kernel** | Authorises commands, journals facts, mediates effects. Enforces SAFETY policy; does not choose workflow policy (§7). | `v2/kernel/` |
| **the lead session** | An omnigent session running the `bircher` AGENT. A model. Delegates coding to sub-agents; writes no code itself. | `config.yaml`, `skills/muesli-loop/` |
| **sub-agents** | `codex` and `claude_code` sessions the lead session dispatches to implement or review. | `agents/codex/`, `agents/claude_code/` |

Terms to avoid:

- **"bircher"** unqualified — it names the repository, the runner, AND the lead
  session's agent (`config.yaml: name: bircher`). Say which.
- **"the coordinator"** for the lead session. The coordinator is Python code on
  the runner. The lead session is a model in a container.
- **"the reviewer"** unqualified — there are two, see §5.

---

## 2b. The target shape, in one table

Everything below is written against this. Nothing here is finished.

| concern | today | TARGET |
|---|---|---|
| orchestration | the runner, bash, ~7,700 lines | the coordinator, Python |
| queue loop | `main()` in bash | replaced, driving omnigent directly |
| derivation | coordinator as a one-shot subprocess | coordinator in-process, long-lived |
| review | twice — lead session AND coordinator | once, coordinator-owned |
| repair loop | lead session (3 rounds) AND the coordinator (2, default) | coordinator-owned only |
| lead session | implements, reviews, repairs, reports a marker | implements only |
| reporting | derived only — the marker is retired in code AND in the instructions | done |
| kernel | authorises and journals | unchanged — this part is done |

The kernel is the only component at its target. Everything else is in motion.

---

## 3. Components

### 3.1 The runner — `batch/run-queue.sh`

Bash, ~7,700 lines, runs on the NAS. Owns:

- the queue: GitHub issues labelled `bircher:queued` → `queue/*.md`
- session lifecycle: create the lead session, send the item, poll, cancel
- outcome derivation: delegates to the coordinator
- the merge path: pre-merge gate, cross-review status, merge, main-CI watch,
  revert-on-red
- the recovery paths: `--recover-pr`, the deferred-ready sweep

Key functions by size: `run_item` (522), `merge_ready_pr` (330),
`recover_pr_cmd` (269), `main` (207), `reconcile_deferred_ready` (145).

> **TARGET —** this file does not exist. Every responsibility above moves to
> the coordinator.
>
> **GAP —** all of it. The runner is 8,559 lines of bash (with `batch/lib/`)
> against a design document that calls 6,851 lines the problem. New mechanism
> added here is written to be moved; three pieces were added today alone (the
> pre-merge gate, the policy resolver, the tuple width check).

### 3.2 The coordinator — `v2/coordinator/`

Python, twelve modules, invoked as `python3 -m coordinator.cli derive`. **A
one-shot subprocess, not a service.** This matters: it starts after the lead
session has settled and exits when the tuple is printed.

| module | responsibility |
|---|---|
| `outcome.py` | the derivation: what an item did, from the repository |
| `ci.py` | CI observation, classification, re-run |
| `discovery.py` | finding an item's PR when branch-code discovery fails |
| `pr_selection.py` | which PR belongs to this item |
| `review.py` | dispatching the independent reviewer, reading its verdict |
| `session.py` | talking to omnigent |
| `observe.py` | what the coordinator can see for itself |
| `effects.py`, `effect_mode.py` | performing an effect from Python, mode-aware |
| `wiring.py` | real dependencies for `derive` |
| `cli.py` | the command line, mirroring `kernel.cli` |

It returns an eight-field pipe-delimited tuple:

    outcome|review|note|sha|ci|ci_first|resubmissions|pr

> **TARGET —** the coordinator is the orchestrator. No tuple, no subprocess
> boundary, no pipe-delimited transport: the derivation's result is an object
> the caller already holds.
>
> **GAP —** it is a one-shot subprocess, and that single fact causes the
> review/repair split in §5. The transport is also fragile by nature: a
> pipe-delimited line parsed by `read` cannot express a field containing a pipe
> or a newline, and a short line silently corrupts the last value.
> `_derived_width_ok` fails closed on both, but that is a guard around a
> transport that should not survive the migration.

### 3.3 The kernel — `v2/kernel/`

Authorises and records. It does not choose WHAT the system should do next —
that is the orchestrator's — but it is not policy-free: it enforces the safety
rules that make an outcome trustworthy, refusing anything that would let a
claim stand in for evidence. See §7 for the split and the rules it owns.

**Run states and the commands that move them.** Two commands have
destinations that depend on their PAYLOAD, which is where the retry and
revision loops live:

    queued --submit_spec--> specified --submit_plan--> planned
      --start_implementation--> implementing
      --record_review--> (by verdict)
      --request_merge--> merge_requested
      --record_merge_outcome--> (by outcome)
      --record_run_outcome--> ended

`record_review` (`_VERDICTS`):

| verdict | lands in |
|---|---|
| `accept` | `reviewing` |
| `reject` | `reviewing` |
| `request_revision` | **`planned`** — the revision loop: back to planning |

`record_merge_outcome` (`_MERGE_OUTCOMES`), legal only from `merge_requested`:

| outcome | lands in |
|---|---|
| `merged` | **`merged`** |
| `failed` | **`reviewing`** — a failed merge is retryable after re-review |

So the successful path is
`implementing → reviewing → merge_requested → merged → ended`.

Genuinely non-transitioning: `record_implementation_output`,
`record_ci_observation`. `cancel_run` is legal from any live state.
`record_run_outcome` is legal from every state except `ended`. `ended` is
terminal and unreachable-from.

**Effect classes:** `merge`, `comment`, `status_check`, `pull_request`,
`issue_or_label`, `ref_update`, `session_control`.

**Fact kinds (the journal):** `run_started`, `command_requested`,
`command_accepted`, `command_rejected`, `artifact_created`, `review_verdict`,
`transition_performed`, `external_observation`, `human_ruling`,
`ownership_acquired`, `effect_intended`, `effect_confirmed`,
`effect_uncertain`, `effect_reconciled`, `attempt_dispatched`,
`merge_authorized`, `model_question`, `enqueue_proposed`, `run_enqueued`,
`revision_proposed`, `bundle_revised`, `shadow_rejected`.

**Two independent mode switches — do not conflate them:**

| variable | values | governs |
|---|---|---|
| `BIRCHER_KERNEL_MODE` | `shadow` \| `enforce` | whether the kernel REFUSES, or only records what it would have refused |
| `BIRCHER_EFFECT_MODE` | `deny` \| `legacy` \| `kernel` | how an effect is performed: refused / run unjournalled / journalled and contract-checked |

`legacy` exists to run WITHOUT the kernel — a bisecting tool for a suspected
kernel fault. That is why the switch cannot live inside the kernel.

> **TARGET —** unchanged. The kernel is the one component already at its
> target: the effect classes, the fact vocabulary, the state machine and the
> mode switches are all settled and in production use.
>
> **GAP —** two, both narrow. `validate_review`'s base check compares the run's
> recorded base against itself (§9). And in `kernel` effect mode the kernel is a
> HARD dependency: a broken kernel loses the effect, not merely the record.
> That is deliberate, but nothing monitors kernel availability.

### 3.4 The lead session and sub-agents

`config.yaml` declares the `bircher` agent: *"muesli's autonomous tech lead...
delegating all coding to claude_code / codex sub-agents... Writes no code
itself."* It loads `skills/muesli-loop/SKILL.md`, which mandates: implement via
one vendor, review via the opposite, up to 3 fix rounds, then report.

`skills/cross-review/SKILL.md` is the review procedure, and encodes two scars:
muesli #705 (a green check can be masked by a swallowed exit code) and #666 (a
change acquiring a releasable resource needs its FAILURE paths tested).

`agents/v2_implementer/` is a restricted bundle (`gate_pushes: true`, cannot
push/comment/label) built for the C8 experiments. **It is not used by the wave
path.**

> **TARGET —** the lead session implements and stops. `muesli-loop` loses its
> review step, its fix loop and its `bircher-status:` reporting; those become
> the coordinator's.
>
> **GAP —** all three are still in `muesli-loop` today, and removing them is
> blocked on the coordinator being able to repair (§5) and on deciding what
> reports `rounds=<n>`, the one field the coordinator cannot observe.

---

## 4. One item, end to end

1. **Queue.** The runner reads `bircher:queued` issues and writes `queue/*.md`.
2. **Run start.** `run_item` mints a run id `<item>-<epoch>` and records the
   work repo's HEAD as the run's `base_sha`.
3. **Dispatch, THEN label.** The kernel issues a generation (a monotonic
   fence) *before* the session exists. Only then is `bircher:running` applied,
   because it is a routed effect and every routed effect needs a generation.
   **This order is load-bearing and was arrived at by a bug:** labelling
   earlier meant the effect was either silently dropped (`${BIRCHER_GENERATION:?}`
   aborts in kernel mode, and its `|| true` swallowed the failure) or — worse,
   on the second item of a run — attributed to the PREVIOUS item's stale
   exported generation.
4. **Session.** The runner creates the lead session (`session_control` effect)
   and sends the item plus a vendor directive naming implementer and opposite
   reviewer.
5. **Implementation.** The lead session dispatches a coding sub-agent, opens a
   branch and a PR, dispatches its own reviewer, may run fix rounds, posts a
   summary, and stops.
6. **Settle detection.** The runner polls: session idle AND item count stable
   AND a PR open, held for N polls. Then it cancels the session.
7. **Derivation.** The runner invokes the coordinator, which selects the PR,
   waits out CI, dispatches an INDEPENDENT reviewer, and returns the tuple.
8. **Lifecycle recording.** The runner replays the derived facts into the
   kernel: output, CI observation, review verdict, then `request_merge`.
9. **Merge.** `merge_ready_pr` posts `bircher/cross-review`, waits for
   `mergeStateStatus == CLEAN`, merges pinned to the reviewed head, watches
   main CI, and reverts on a confirmed red.
10. **Close-out.** Issue comment, labels, scorecard row, `record_run_outcome`.

> **GAP — steps 5 and 7 both review.** Step 6 exists only because the
> orchestrator is a separate process from the session: it has to *detect* that
> the model stopped rather than being told. Step 8's replay-into-the-kernel
> exists only because the derivation happened out-of-process.
>
> **TARGET —** steps 5–8 collapse. The coordinator dispatches the implementer,
> observes it directly, reviews once, repairs if needed, and records as it goes
> rather than replaying afterwards.

---

## 5. Two reviews, and why that matters

Steps 4 and 6 each perform a cross-vendor review of the same PR.

| | who dispatches | when | can it repair? |
|---|---|---|---|
| lead session's review | the model, per `muesli-loop` | during the session | **yes** — 3 bounded fix rounds |
| coordinator's review | Python, per `review.py` | after the session is cancelled | **no** — it can only classify |

`observe.py` turns a reviewer FAIL into outcome `failed`, which is terminal. So
**when the two disagree, the finding lands at the layer that cannot act on it**
and a repairable defect kills the run. Observed on muesli PR #739.

This is not redundancy to be deleted. It is the same capability in two places
with different powers, and resolving it is a question of ownership (§8).

---

## 6. Deployment

Bircher runs on the NAS, not on a workstation: `omnigent:8000` does not resolve
elsewhere and `/workspaces/*` does not exist elsewhere.

    OMNIGENT_RUNNER=omnigent-runner-bircher ~/homelab/omnigent.sh exec '<cmd>'

**The variable must be on the same command.** `omnigent.sh` defaults to
`RUNNER_NAME="${OMNIGENT_RUNNER:-omnigent-runner}"` — the GENERAL runner, which
runs other workloads. An earlier version of this document put the assignment on
its own line BELOW the command, which in shell cannot affect it: following that
would have launched an autonomous, merge-capable process against the wrong
container.

| path | what |
|---|---|
| `/workspaces/bircher` | v1 deployment, 237 commits behind, untouched |
| `/workspaces/bircher-v2` | v2 deployment, tracks `main` |
| `/workspaces/muesli` | the work repo |
| `/workspaces/bircher-v2/.run/` | kernel DBs, scorecards, run logs |

**Launch:**

    cd /workspaces/bircher-v2 && \
      BIRCHER_REPO=abedegno/muesli \
      WORKDIR=/workspaces/muesli \
      BIRCHER_KERNEL_DB=/workspaces/bircher-v2/.run/kernel-muesli.db \
      bash batch/launch.sh --source issues --log .run/<name>.log

**None of those three is required.** All are deployment overrides of shipped
defaults, and an earlier version of this document said otherwise:

| variable | shipped default | why override it |
|---|---|---|
| `BIRCHER_REPO` | `abedegno/muesli` | targeting a different repo (e.g. `bircher-smoke`) |
| `WORKDIR` | `/workspaces/muesli` | the matching work checkout |
| `BIRCHER_KERNEL_DB` | `$BUNDLE_DIR/.run/kernel.db` (set in `run_item`) | keeping a repo's journal separate |

`BIRCHER_KERNEL_DB` does carry a `:?` guard, but in `effect-adapter.sh` — which
runs long after `run_item` has already defaulted it, so it never fires in
practice. Passing it explicitly is a good habit for keeping muesli's journal
apart from smoke runs; it is not a requirement.

Mode defaults are already `BIRCHER_EFFECT_MODE=kernel` and
`BIRCHER_KERNEL_MODE=enforce`. `--source queue` drains `queue/*.md` instead.

**The merge gate on muesli:** branch protection requires `review-gate`, NOT
`bircher/cross-review`. They chain — bircher posts cross-review, a workflow
reacts to that status event and posts review-gate ~7s later. `strict: true` is
set, so a PR whose base has moved becomes `BEHIND` and is refused.

---

## 7. Where the boundaries are

- **Two kinds of policy, in two places.** The runner/coordinator owns WORKFLOW
  policy: which item, which vendor, when to wait, when to merge, when to give
  up. The kernel owns SAFETY policy and enforces it — `authz.py` raises
  `NotAuthorized` in 30 places, covering legal state transitions, the dispatch
  role a command may come from, reviewer independence from the implementer,
  the accepted verdict vocabulary, that a review binds an artifact the kernel
  actually holds, and that a `merged` outcome is backed by a confirmed merge
  effect rather than an actor's claim.

  An earlier version of this document said "the kernel never decides policy",
  which is wrong and would misdirect the migration: moving a decision into the
  coordinator can collide with a rule the kernel already enforces.
- **Routed `gh` effects in the coordinator name their repository explicitly.**
  `gh` resolves an omitted `--repo` from the working directory — the runner's
  own checkout — so an omission acts on the WRONG repository and still
  succeeds. That happened: review comments landed on `abedegno/bircher` instead
  of the smoke repo while the kernel journalled them confirmed.

  Scope of the guarantee, precisely: `test_effect_argv_names_its_repo.py`
  AST-enumerates literal `gh` argv lists inside effect calls in
  `v2/coordinator/*.py` only. `gh api` is exempt because the repository is in
  its URL path. **It does not cover the runner's own `gh` calls, and it cannot
  cover `git` at all** — git has no `--repo`; a routed `git push` is bound by
  the worktree it runs in and that worktree's `origin`, which is why
  `publish_cmd` uses a subshell `cd`. Treating this as a global invariant would
  recreate exactly the failure the test was written for.
- **An unreadable observation is never evidence.** A failed lookup is `UNKNOWN`,
  never `CLEAN`, never "absent", and never spends a grace period.
- **A model's claim is not an observation.** Outcomes are derived; the
  `bircher-status:` marker a lead session emits is read by nothing.

---

## 8. Current state versus target

**Target:** the runner is retired; the coordinator becomes the orchestrator,
driving items directly against omnigent, with the kernel unchanged beneath it.

| | today | target |
|---|---|---|
| orchestration | `batch/run-queue.sh` (bash) | `v2/coordinator/` (Python) |
| derivation | coordinator, one-shot subprocess | coordinator, in-process |
| review + repair | split across lead session and coordinator | coordinator owns both |
| lead session | implements, reviews, repairs, reports a marker | implements only |

Two consequences worth stating, because they change what to build:

1. **The review/repair split becomes SOLVABLE — it does not solve itself.**
   An earlier draft claimed longevity alone fixes it. That is wrong:
   being long-lived supplies *availability*, not repair authority or a
   protocol. The coordinator's review today is read-only and one-shot, and
   nothing anywhere defines how a FAIL becomes a fix task.

   A target repair protocol must specify, and none of this exists yet:

   - which session receives the fix task — the original implementer session
     (still alive? it is cancelled today) or a fresh one
   - how the kernel transition works: `record_review(request_revision)` already
     returns a run to `planned`, so the revision loop exists in the kernel and
     is unused by this path
   - how generations and roles advance across a repair round, and what fences
     the retry
   - how the reviewed artifact and head are invalidated and rebound after a fix
   - when CI and review repeat, and the bound on rounds
   - how a repair resumes idempotently if the coordinator itself dies mid-round
   - what terminal escalation looks like when the bound is reached

   Until that is designed, gap 1 is NOT "closed by migration" — the migration
   is a precondition, not the fix.
2. **New mechanism belongs in Python.** Anything added to `run-queue.sh` from
   here is written to be moved.

**Migration order:** `merge_ready_pr` → `run_item` (building the repair loop in
from the start, not bolted on) → `main` (likely replaced rather than ported) →
recovery and sweep (both may shrink substantially, since they exist to recover
work a one-shot orchestrator dropped).

---

## 9. Known gaps

Ordered by what blocks what. "Closed by migration" means the gap is a symptom
of the runner/coordinator split and should NOT be patched in place.

| # | gap | severity | closed by | blocked on |
|---|---|---|---|---|
| 1 | ~~a repairable finding dies when the two reviews disagree~~ | **CLOSED 2026-08-31** | the repair loop — muesli #722 failed review twice, was repaired twice and merged, with no human routing a finding | — |
| 2 | duplicate cross-vendor review | medium | gap 1 | deleting either one first loses repair or loses independence |
| 3 | ~~`bircher-status:` marker still emitted~~ | **CLOSED** | the skill no longer mandates it, and the enumerating guard now scans instruction files (`.md` under `skills/`, `agents/`) as code | — |
| 4 | runner is 8,559 lines and still growing | medium | migration | ordering: `merge_ready_pr` → `run_item` → `main` |
| 5 | review base binding is tautological | moderate, config-dependent | own design | not urgent while muesli sets `strict: true` — see `base-binding-weakness.md` |
| 6 | why two reviews of the same commit disagreed is unexplained | moderate | investigation | recovering the lead session's child transcript |
| 7 | ~~12 stale citations in the effect-site inventory~~ | **CLOSED** | `tools/repoint-citations.py` — repoints by the cited line's own TEXT at a named baseline, reports ambiguity rather than guessing | — |
| 8 | citation binding test cannot land | low | gap 7 | — |
| 9 | `_derive_budget` warns every run | low | config or ceiling change | knobs promise 5,300s, a bounded call tops at 3,600s |
| 10 | kernel availability is unmonitored in `kernel` effect mode | low | operational | — |
| 11 | nothing schedules a wave | operational | a decision | gap 1 — scheduling unattended waves before repair works just multiplies escalations |
| 13 | ~~the kernel's revision loop is never used by any path~~ | **CLOSED** | the repair loop uses it; the kernel needed no change | — |
| 15 | implementer sessions are reaped by omnigent's 480s per-turn IDLE watchdog mid-work | **operational, and probably the biggest lever on repair convergence** | raising `HARNESS_TURN_TIMEOUT_S` for `omnigent-runner-bircher` | a container restart, so not while a wave is running |
| 14 | an exhausted allowance records `request_revision`, so the kernel sits at `planned` while the runner escalates | low | its own design pass | recording `reject` changes what `BIRCHER_MAX_REVISIONS=0` records, and that must stay byte-identical |
| 12 | v1 deployed, 237 commits behind | operational | cutover | gaps 1 and 11 |

### The repair loop, as of 2026-08-31

Built and merged; `BIRCHER_MAX_REVISIONS` (default 2, range 0–5, 0 disables).
A reviewer FAIL with rounds remaining is a `revise`, not an ending: the runner
records `request_revision`, confirms the kernel journalled it, dispatches a
repair session briefed on the reviewer's verbatim findings, and derives again.

Judgement is the coordinator's; dispatch, settle and re-derive stay the
runner's until `run_item` migrates. That split is honest rather than ideal —
`v2/coordinator/session.py` is read-only and cannot start a session — and it
moves with the rest of `run_item`.

WHAT IS PROVEN, and by what:

| claim | evidence |
|---|---|
| a FAIL with rounds left revises; with none it fails exactly as before | `tests/coordinator/test_repair_loop.py`, and the shell→Python path in `--self-test` |
| the revision is not acted on until the kernel journals it | `test_revision_durability.py`, driven through real commands including a lost CAS |
| no approval survives a round | `test_revision_durability.py` — one test per guard (artifact, CI head, state), each killed by exactly one mutation |
| re-entry is decided from history, not the state name | `test_recovery_table.py`, every row against a real journal |
| recovery will not merge what may already be merged | `_recovery_forbids_merge`, wired into `--recover-pr` |

WHAT THE LIVE RUNS SHOWED, in order. Four runs on 2026-08-31, each finding
exactly one defect that 1045 passing tests did not:

| run | how far it got | what it found |
|---|---|---|
| 1 | derived `revise`, refused to repair | a `revise` carried no head, so the runner skipped the whole kernel lifecycle block and no revision was journalled |
| 2 | **repair round 1 dispatched**, revision journalled, allowance 2→1 | `sess-create:<run_id>` made the repair's create a REPLAY — the prompt went to the session the runner had just cancelled |
| 3 | **PR #751 MERGED**, main CI green | nothing; but the review PASSED first time, so the loop never engaged |
| 4 | **#722 MERGED after 2 repair rounds** | nothing — this is criterion 9 |

All three are one shape: an identifier that named the run, the conversation or
the commit, in a system that now does each of those more than once per item.
Each was found only by running it, and each was caught by a guard behaving
correctly — the durability gate refused an unjournalled revision, BLOCKED
refused to fabricate a verdict for an unread diff. The guards turned three
silent corruptions into three diagnosable escalations. They did not prevent
the defects, and no test found them.

CRITERION 9 IS MET. muesli #722 -- the item this design named as its standing
counter-example, escalated after three distinct findings in three reviews --
failed review twice more, was repaired twice, and merged. PR #752, merge commit
`9ec9e869`, main CI green, issue closed. No human routed a finding.

The journal is the evidence, not the log:

    seq 31  review_verdict    request_revision   causal id revise:i722-...
    seq 56  review_verdict    request_revision   causal id revise:i722-...
    seq 81  review_verdict    accept
    seq 87  merge_authorized
    seq 92  effect_confirmed  merge:752:6f615ac...

Three reviews of three DIFFERENT commits -- `48eaf46`, `7304400`, `6f615ac` --
by three distinct repair sessions, each briefed on the previous reviewer's
verbatim findings. The scorecard reports `rounds: 2`, a field that had been null
since Phase 2 and now carries an observation.

It converged on the LAST round it had. That is worth stating plainly rather than
reading as comfortable: a bound of 2 was chosen from #740 converging in 1 and
#750 in 2, and #722 used both. One more finding and it would have escalated
correctly. The bound is doing real work, and this run is evidence for the loop,
not evidence that 2 is the right number.

STILL OPEN in the loop itself: `run_item` does not consult `recover` (only
`--recover-pr` does), so a crash mid-loop re-derives rather than resuming; and
gap 2 stands, because the lead session's own fix loop is untouched.

**What is NOT a gap.** These are done and should not be reopened: the kernel's
state machine, effect classes, fact vocabulary and mode switches; the derived
outcome replacing the marker as the decision input; effect routing with its
enumerating guards; the pre-merge gate; the eight-field boundary's fail-closed
width check.
