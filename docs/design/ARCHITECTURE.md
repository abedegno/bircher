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
| **the kernel** | Authorises commands, journals facts, mediates effects. Never decides policy. | `v2/kernel/` |
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
| repair loop | lead session only (3 rounds) | coordinator-owned |
| lead session | implements, reviews, repairs, reports a marker | implements only |
| reporting | `bircher-status:` marker + derived tuple | derived only |
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

Authorises and records. Never decides what *should* happen — only whether a
request is permitted and what actually occurred.

**Run states and the commands that move them:**

    queued --submit_spec--> specified --submit_plan--> planned
      --start_implementation--> implementing --record_review(accept)--> reviewing
      --request_merge--> merge_requested --record_run_outcome--> ended

Non-transitioning commands: `record_implementation_output`,
`record_ci_observation`, `record_merge_outcome`. `cancel_run` is legal from any
live state. `ended` is terminal and unreachable-from.

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

1. **Queue.** The runner reads `bircher:queued` issues, writes `queue/*.md`,
   labels the issue `bircher:running` (a routed `issue_or_label` effect).
2. **Run start.** `run_item` mints a run id `<item>-<epoch>` and records the
   work repo's HEAD as the run's `base_sha`.
3. **Dispatch.** The kernel issues a generation (a monotonic fence). The runner
   creates the lead session (`session_control` effect) and sends the item plus
   a vendor directive naming implementer and opposite reviewer.
4. **Implementation.** The lead session dispatches a coding sub-agent, opens a
   branch and a PR, dispatches its own reviewer, may run fix rounds, posts a
   summary, and stops.
5. **Settle detection.** The runner polls: session idle AND item count stable
   AND a PR open, held for N polls. Then it cancels the session.
6. **Derivation.** The runner invokes the coordinator, which selects the PR,
   waits out CI, dispatches an INDEPENDENT reviewer, and returns the tuple.
7. **Lifecycle recording.** The runner replays the derived facts into the
   kernel: output, CI observation, review verdict, then `request_merge`.
8. **Merge.** `merge_ready_pr` posts `bircher/cross-review`, waits for
   `mergeStateStatus == CLEAN`, merges pinned to the reviewed head, watches
   main CI, and reverts on a confirmed red.
9. **Close-out.** Issue comment, labels, scorecard row, `record_run_outcome`.

> **GAP — steps 4 and 6 both review.** Step 5 exists only because the
> orchestrator is a separate process from the session: it has to *detect* that
> the model stopped rather than being told. Steps 7's replay-into-the-kernel
> exists only because the derivation happened out-of-process.
>
> **TARGET —** steps 4–7 collapse. The coordinator dispatches the implementer,
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

    ~/homelab/omnigent.sh exec '<cmd>'        # via the Portainer API, no SSH
    OMNIGENT_RUNNER=omnigent-runner-bircher   # target the bircher runner

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

All three variables are required — `BIRCHER_KERNEL_DB` has a `:?` guard and
nothing defaults it. Defaults are already `BIRCHER_EFFECT_MODE=kernel` and
`BIRCHER_KERNEL_MODE=enforce`. `--source queue` drains `queue/*.md` instead.

**The merge gate on muesli:** branch protection requires `review-gate`, NOT
`bircher/cross-review`. They chain — bircher posts cross-review, a workflow
reacts to that status event and posts review-gate ~7s later. `strict: true` is
set, so a PR whose base has moved becomes `BEHIND` and is refused.

---

## 7. Where the boundaries are

- **The runner decides. The kernel permits.** Policy never moves into the
  kernel; the kernel only ever answers "is this allowed" and "what happened".
- **Every gh/git mutation carries `--repo`.** `gh` resolves an omitted repo
  from the working directory, which is the runner's own checkout. Enforced by
  `test_effect_argv_names_its_repo.py`.
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

1. **The review/repair split resolves itself.** The coordinator cannot repair
   today only because it is a one-shot subprocess. As the orchestrator it is
   present throughout, so it can re-dispatch a fix and re-review. The lead
   session's review, its fix loop and its `bircher-status:` marker are then all
   removable from `muesli-loop` — they exist because the session was the only
   thing that could review and report.
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
| 1 | a repairable finding dies when the two reviews disagree (§5) | **high** | migration | `run_item` in Python |
| 2 | duplicate cross-vendor review | medium | migration | same — deleting either one first loses repair or loses independence |
| 3 | `bircher-status:` marker still emitted | medium | `muesli-loop` edit | deciding what reports `rounds=<n>` |
| 4 | runner is 8,559 lines and still growing | medium | migration | ordering: `merge_ready_pr` → `run_item` → `main` |
| 5 | review base binding is tautological | moderate, config-dependent | own design | not urgent while muesli sets `strict: true` — see `base-binding-weakness.md` |
| 6 | why two reviews of the same commit disagreed is unexplained | moderate | investigation | recovering the lead session's child transcript |
| 7 | 12 stale citations in the effect-site inventory | low | hand-fixing | per-row judgement; some describe deleted code |
| 8 | citation binding test cannot land | low | gap 7 | — |
| 9 | `_derive_budget` warns every run | low | config or ceiling change | knobs promise 5,300s, a bounded call tops at 3,600s |
| 10 | kernel availability is unmonitored in `kernel` effect mode | low | operational | — |
| 11 | nothing schedules a wave | operational | a decision | gap 1 — scheduling unattended waves before repair works just multiplies escalations |
| 12 | v1 deployed, 237 commits behind | operational | cutover | gaps 1 and 11 |

**What is NOT a gap.** These are done and should not be reopened: the kernel's
state machine, effect classes, fact vocabulary and mode switches; the derived
outcome replacing the marker as the decision input; effect routing with its
enumerating guards; the pre-merge gate; the eight-field boundary's fail-closed
width check.
