# v2 supersedes v1 in record mode

**Goal:** make the v2 branch the runner that actually runs, with the kernel
recording every run's lifecycle, without changing what the coordinator does or
which agents it launches.

**Status of the thing being superseded:** v1 works. It is not broken and this
is not a rescue. The reason to supersede it is that v2's kernel currently has
*no callers outside its test suite* — 413 tests and eight review rounds over a
component nothing invokes. Recording is the cheapest way to make it real.

## The principle

**The kernel observes; the coordinator decides.**

Authorization and the argv contract are evaluated exactly as they would be
under enforcement, and a refusal is recorded as a fact rather than acted on.
After real traffic you can answer "what would enforcement have refused, and
why" from the journal, and switch commands to enforcing one at a time against
evidence.

This is the same discipline the rest of the programme runs on: observe before
asserting. Turning enforcement on because the tests pass would be a claim
outrunning its evidence.

## Components

### 1. A command CLI

`v2/kernel/cli.py` gains a `command` subcommand beside the existing effect one.
It is bash's only interface to the kernel's command layer, which today has no
caller outside tests.

```
python3 -m kernel.cli command \
  --db "$BIRCHER_KERNEL_DB" --run-id "$RUN_ID" --generation "$GEN" \
  --name record_ci_observation --payload-json '{"status":"success","head_git_sha":"..."}'
```

Exit codes mirror the effect CLI: `0` accepted, `87` refused, `88` fenced,
`2` usage. In shadow mode a refusal still exits `0` and prints the recorded
reason to stderr.

### 2. One mode switch

`BIRCHER_KERNEL_MODE`, defaulting to `shadow`, covering **both** command
authorization and the argv contract.

- `shadow` — evaluate; on `NotAuthorized` or `ContractViolation`, append a
  `shadow_rejected` fact carrying the command or effect, the exception type
  and its message. What happens next differs by kind, and the difference is
  the whole safety property:

  - **A refused COMMAND is not applied.** No `command_accepted`, no version
    bump, no state transition, no side effect, `Result.accepted` false. The
    coordinator decides what the run does, so the kernel never needs to
    pretend a refusal succeeded.
  - **A refused EFFECT still executes.** The contract violation is recorded
    and the command runs, because the coordinator is mid-run and stopping its
    external effects is exactly the interference record mode exists to avoid.
    The merge-target and empty-argv checks stay enforcing even here.

  **An earlier revision of this document said shadow should "proceed as if it
  had passed", without distinguishing the two.** That sentence produced a real
  defect: `submit()` wrote the `merge_authorized` fact and set the current
  artifact gated only on the command's NAME, so a shadowed `request_merge`
  naming someone else's pull request poisoned the record that the enforcing
  merge-target check reads — and a reviewer demonstrated shadow mode merging
  an attacker-named PR. Recorded here because the wording, not the code, was
  the cause, and a future implementer reading the old sentence would rebuild
  the same hole.
- `enforce` — present behaviour: the refusal is the outcome.

Defaulting to `shadow` is the opposite of `BIRCHER_EFFECT_MODE`, which defaults
to `deny`. That is deliberate and the difference is worth stating: the effect
adapter's default answers "may this mutation happen at all", where failing
closed is right. This switch answers "is the kernel's model of the run correct
yet", where failing closed would stop a working runner over a modelling bug.

One switch rather than two, because a run whose commands are shadowed and whose
effects are enforced is a state nobody reasoned about.

### 3. The recorder must never break the runner

**Every kernel call from bash is advisory.** A non-zero exit, a crash, a
missing database, a Python traceback — none of it may change the run's outcome.
Each call site is `|| _kernel_warn` and nothing reads a kernel exit code to
decide what to do next.

This is the load-bearing safety property of the whole design, and it is the one
to test hardest: a deliberately broken kernel must leave `--self-test` green
and a real run unaffected.

### 4. Lifecycle wiring

Inside `run_item`, at the points the run already has the information:

| point in `run_item` | kernel call |
|---|---|
| item accepted, prompt non-empty | `enqueue(...)` → `RUN_ID` |
| after `_create_session` succeeds | `dispatch(actor=$vendor, role=implementer)` → `GEN` |
| marker parsed | `put_artifact(marker body)`, then `record_implementation_output` |
| marker carries CI result | `record_ci_observation` |
| **before recording the review** | `dispatch(actor=$RECOVERY_REVIEWER, role=reviewer)` → `GEN` |
| marker carries review verdict | `record_review` |
| before `merge_ready_pr` | `dispatch(actor=$vendor, role=implementer)`, `request_merge` |
| after the merge effect returns | `record_merge_outcome` |

**Two corrections the self-review caught, both of which would have produced a
shadow rejection on every single run:**

*The reviewer needs its own dispatch.* `validate_review` refuses a review whose
attempt was not dispatched in the reviewer role (`v2/kernel/authz.py:170`). One
dispatch at session creation gives the implementer role only. Each role change
is a new dispatch, which also re-fences the generation — so `GEN` is re-read
after each one, and the merge request needs an implementer dispatch again.

*The output needs an artifact that exists.* `record_implementation_output`
refuses a hash the store does not hold (`v2/kernel/authz.py:321`), and in
record mode there is no patch — v1's implementer pushed a PR and reported a
marker. **The artifact is the marker body**: the bytes the coordinator actually
observed the implementer report. That is honest about what is being recorded
and it makes the review bind something real, but it is a stand-in — the
artifact is a *report about* the work, not the work. C8 is what replaces it
with the commit.

`RUN_ID` and `GEN` are exported so the effect adapter picks them up unchanged —
it already reads `BIRCHER_RUN_ID` and `BIRCHER_GENERATION`.

**`RUN_ID` is `<item-code>-<epoch-seconds>`.** Item codes recur across
attempts; a run identity that collides across attempts would merge two runs'
facts into one aggregate.

**The database lives at `$BUNDLE_DIR/.run/kernel.db`**, outside any worktree,
so no session can write it. `test_identity_precondition.py` already asserts
`BIRCHER_KERNEL_DB` has no default; this design gives it a value at the call
site rather than in the adapter.

## Identity in record mode, stated plainly

The implementer session cannot reach the kernel — no network, and the database
is outside its writable path. That is the M1-1 boundary working. It follows
that **the coordinator is the kernel's only client**: it dispatches a session
as `actor=<vendor>, role=implementer` and then submits commands under that
generation on the session's behalf.

So the kernel binds *which dispatched attempt a command belongs to*. It does
not establish that the session itself authored the content. The coordinator
vouches. This is weaker than "identity is assigned by the mechanism" sounds in
§4b, and it is a property of the architecture rather than of this design — no
arrangement short of giving sessions kernel access changes it.

## Explicitly out of scope

- **The credential boundary is not applied.** This keeps `claude_code` and
  `codex`, which have `sandbox: none` and `gate_pushes: false`; the implementer
  still holds a token and still opens its own PR. M1-1 is proven but not in
  force until the `v2_implementer` bundle is swapped in, which is a separate
  piece of work and is what makes C8 (kernel-created pull requests) necessary.
- **Enforcement.** Every authorization guard built in Milestone 1 stays
  shadowed until real traffic says what it would refuse.
- **C8, B-sealed, the front end, the per-class effect rules.**

## Cutover and rollback

Cutover is pointing the scheduler at the v2 checkout. Rollback is pointing it
back; the kernel database is additive and no v1 state is migrated or rewritten.
Run the two side by side first if the queue allows it.

## Acceptance

Superseded means, on the runner and against real queue items:

1. A completed run produces a kernel aggregate whose projected state matches
   the outcome the scorecard recorded.
2. Every externally visible mutation the run performed appears in the effect
   journal.
3. `shadow_rejected` facts are queryable, with a count and a reason per
   command name — the input to deciding what to enforce first.
4. A deliberately broken kernel changes nothing about the run's outcome.
5. `--self-test` stays green with the kernel wired in.

Criterion 4 is the one that decides whether this was safe to do.
