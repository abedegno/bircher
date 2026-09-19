# The back half closes its own loop

**Status:** design, approved section by section in conversation on 2026-09-19; not yet implemented.

**Supersedes, in part:** `2026-08-31-repair-loop-design.md` ("The coordinator repairs, bounded"). The bound goes; the ownership split it settled stands.

## Why

Muesli issue #764 was implemented by a run whose session stopped twelve minutes before CI finished red. The batch layer derived a terminal outcome from the red CI, recorded the run as `ended`, and from that moment the pipeline was blind to the pull request. Every later step, six in all, was a person launching `--recover-pr` by hand: a repair, a cross-review that read one commit of thirty-eight and passed, a prompt fix, four review rounds each finding one real defect in a different subsystem, and the merge. The record is `docs/design/front-half-live-log.md`, from "Overnight: #764 to a red PR" onward.

Three properties of the current back half produced that week, each verified in the code:

1. **A run can end while its PR is open.** `record_run_outcome` is legal from every active state and is called unconditionally at the end of every `run_item` pass (`batch/run-queue.sh` ~5212). `ended` is the one state no command may leave (`v2/kernel/authz.py`, `_TRANSITIONS`). Waves resume only open runs (`batch/lib/kernel-client.sh` `_kernel_find_run open` excludes `ended`), so the next wave that meets the item mints a fresh run rather than look at the stranded PR.
2. **Red CI is an outcome, not a repair trigger.** `classify` in `v2/coordinator/observe.py` returns `failed` on `ci == "red"` before any verdict is consulted. The repair loop engages only on a review FAIL with rounds remaining, and only inside the pass that dispatched the implementer.
3. **The gate can attest to nothing.** `_post_cross_review_status` (`run-queue.sh` ~1173) posts only `success`; a FAIL posts nothing, so an older green stands on a head whose real review failed. The reviewer prompt pins a commit but never names a range, and one reviewer read that as "the head commit's diff". The prompt exists twice, in bash and Python, held identical by a byte-comparison test; the range fix on branch `fix/recovery-review-range` touched only the bash copy.

Two more gaps cost rounds rather than autonomy: nothing checks the accepted plan's named deliverables against the PR (the implementer disclosed two dropped files in the PR body and the fourth review found them), and neither the implementer's nor the reviewer's container can run the database-backed or Node test suites, so both ship or judge on CI logs.

The target this design serves, agreed with the user: **E11**, one real muesli issue from queued to merged PR with no human action beyond the spec and plan gates, every cross-review status on the PR attesting to a named range, and the round log written by the pipeline.

## What exists and is reused

- The kernel's transition table and its self-test that every command declares its states (`v2/kernel/authz.py`).
- Parks as facts orthogonal to state (`EventKind.PARKED`), with reasons in `PARK_REASONS`, the park notice on the issue, and the human replies `retry`, `correct`, `stop` read from the carrier session (front-half design §3).
- `record_implementation_output`, `record_ci_observation`, `record_review` with `_BACK_HALF_DESTINATIONS`, `request_merge`, `record_merge_outcome`, `cancel_run`, `resume_run`.
- `derive` and `classify` in `v2/coordinator` (ground truth from the repository), the review dispatch in `v2/coordinator/review.py`, `_repair_round` and `merge_ready_pr` in bash, and the effect lifecycle `effect_intended → effect_confirmed | effect_uncertain → effect_reconciled`.
- The journal sweep in `batch/issues-to-queue.sh` that re-queues parked, sliced and disputed runs each wave.
- `_pr_delta_digest`, the sha256 of the PR's three-dot compare, used today only to prove an `update-branch` changed nothing.

## Scope

In: the kernel legality changes below; a pure step function in the coordinator; the batch loop replaced by one-step passes; the no-progress park; status posting on every verdict with a named range; one reviewer prompt; orphan adoption; state-derived labels; a pipeline-owned round log; the plan-conformance check; test infrastructure for the agent containers.

Out: the front half and the shaping phase, which do not change; the implementer's own prompt; any change to the muesli `review-gate` workflow, which keeps requiring `success` on the head; the choice of reviewing vendor, which keeps rotating.

## 1. The state machine

No new states. A repair round is an implementation round: the back half is `planned → implementing → reviewing → merge_requested → merged`, and a repair re-enters it at `planned` through one door, described next.

**`request_repair`**, a new command, legal from `implementing` and `reviewing`, destination `planned`. Its payload names the cause and the evidence:

| cause | evidence | raised by |
|---|---|---|
| `ci_red` | the observed head and the set of failing job names | the step function, from a red CI observation on the current head |
| `review_fail` | the reviewed head and the verdict's blocking-finding fingerprints | the step function, from a recorded FAIL |
| `plan_conformance` | the observed head and the plan-named paths absent from the PR | the step function, once, after implementation output is first recorded |

It is refused from `planned` (there is nothing to repair yet) and from every front-half state. It is the only door back to `planned` in the back half: `record_review` no longer moves a back-half run on `request_revision` or `reject` (both now stay at `reviewing`, like `accept`), so a FAIL is a fact the step function answers with `request_repair(review_fail)`, and the red-CI short circuit in `classify` goes for the same reason: a red observation is ground truth the step function turns into a `ci_red` repair, never into an outcome. From `planned`, the repair implementer's dispatch is the existing `start_implementation`.

The PR's own state, `open`, `closed` or `merged`, is recorded as an external observation beside every CI observation, so the kernel can read it when it decides whether a run may end.

**`record_run_outcome`** is legal from `merged` and `cancelled` as today, and from an active state only when the run holds no `implementation_output` fact, or when its newest PR observation says the PR is closed without merging. Once a PR exists and is open, the only exits are a merge or a human `stop`. This is E11's property as a refusal.

**`park`** becomes legal from `planned`, `implementing` and `reviewing`. `PARK_REASONS` gains `no_progress` (§3). `no_verdict` and `budget_exhausted` apply in the back half with their existing meanings. **`grant_round`** and **`cancel_run`** widen to the same three states so the replies work: `retry` records a grant, `stop` cancels, `correct` is a person pushing to the branch and then replying `retry`.

The back-half revision bound (`BIRCHER_MAX_REVISIONS`, `_max_revisions`, `revisions_left`) is removed. Rounds are counted from the journal for the scorecard and the log, and nothing refuses a round on count.

## 2. The driver: one step per pass

The loop is the wave timer. Each pass performs, for each open back-half run, idempotent steps until none is ready, and returns.

**The step function** is pure Python in the coordinator, `next_step(journal, ground) -> Step`, beside `derive` and `classify`. `ground` is what `derive` already reconstructs from the repository: PR state and head, CI on that head as `pending | green | red` with failing job names, and the newest review verdict for that head as `PASS | FAIL | none` with its findings. It returns exactly one of:

| step | when |
|---|---|
| `wait` | CI pending on the head; or the round's implementer session is still alive, read from the session as today |
| `repair(cause, evidence)` | CI red, or a recorded FAIL, or a plan-conformance gap, and no repair is yet recorded for this head |
| `review` | CI green and no verdict recorded for this head |
| `merge` | a PASS recorded for this head |
| `park(reason)` | §3's conditions |
| `done` | the run is `merged` or `cancelled`; the runner records the run outcome and the scorecard row |

**The runner performs the step.** It calls the step function, does the one thing it names (dispatch a repair session, dispatch a review, request and perform the merge, park), records the resulting fact through the kernel, and re-derives. Dispatching stays in bash: the repair-loop design settled that session creation is bash-only and the coordinator judges, and that split stands. The `while :` loop in `run_item` (~4944 to 5123) and its per-pass allowance variables go; `observe_outcome`'s role becomes "derive ground truth", nothing more.

**Waves resume every open back-half run.** The journal sweep in `issues-to-queue.sh` also re-queues any open run in `planned`, `implementing` or `reviewing` that holds implementation output. A session death, a crash, or a vendor outage means the next wave resumes from the journal.

**A repair round** is a fresh implementer session on the PR's existing branch, briefed with the cause and its evidence and told to push to that branch, exactly as `_repair_round` does today; its turn ends as turns end.

## 3. Unbounded rounds, the no-progress park

**Progress** is judged from facts, each round against the round before it:

- a `ci_red` repair made progress if the next CI observation on a new head is green, or red with a different set of failing job names;
- a `review_fail` repair made progress if the next verdict for a new head is PASS, or FAIL with a different set of blocking-finding fingerprints;
- a `plan_conformance` repair made progress if the next conformance check names fewer paths.

A **finding fingerprint** is the sha1 of the finding's first file path, lower-cased, joined by one space to its first sentence with whitespace collapsed. The coordinator computes the fingerprints when it records a verdict and stores them on the `review_verdict` fact; the reviewer prompt already asks for findings as a list with a file reference.

**`no_progress`** is raised when two consecutive repair rounds each fail to make progress against their own predecessor: the same failing jobs three rounds running, the same fingerprints three rounds running, or the same missing paths three rounds running. The first repair round has no predecessor and always counts as progress. One repeat is tolerated because CI can be flaky and a reviewer occasionally re-raises a finding it should have closed; a second repeat is going in circles.

**`no_verdict`** parks at once when the reviewer produced no verdict for the head. **`budget_exhausted`** parks when the usage gate refuses a dispatch, with the reset time in the notice. The usage ceiling refuses dispatches regardless of anything above.

**A park from outside:** the issue receives the park notice the front half writes, naming the reason, the round, and the three replies; the PR receives the same as a comment; the label becomes `bircher:parked`; nothing on the PR is touched until a reply. `retry` resets the progress comparison window, so the next round is judged against itself.

## 4. Gate integrity

This section lands first, as its own PR, because nothing in §1 to §3 is safe on a gate that can lie.

**One reviewer prompt.** The Python prompt in `v2/coordinator/review.py` is the only one; `_recovery_review_prompt` in bash and the byte-identity test go. The prompt names the range in one sentence: the change under review is every file in `git diff origin/<base>...<head>`, where `<base>` is the PR's base branch, and a head commit touching one file does not narrow the review. Branch `fix/recovery-review-range` (bircher PR #101) is reworked to this shape and is not merged as it stands.

**Every verdict posts a status** on the reviewed head, context `bircher/cross-review`: `success` for PASS, `failure` for FAIL with the count of blocking findings, `error` when no verdict was produced. The description is `<vendor> round <n> <PASS|FAIL|no verdict> on <merge-base7>..<head7>`. A FAIL can no longer leave an older `success` standing; the muesli `review-gate` check keeps requiring `success` on the head and does not change.

**The verdict fact records the range.** `review_verdict` carries the merge-base sha, the head sha, and the three-dot delta digest. A re-stamp after a content-identical `update-branch` stays provable by digest; anything else gets a fresh review.

**Posting is an authorised effect** through the intended, confirmed, uncertain, reconciled lifecycle every other GitHub effect uses. An uncertain post halts for reconciliation rather than being retried blindly.

## 5. Plan conformance, orphans, labels, the round log

**Plan conformance** runs once, when implementation output is first recorded and before any review is dispatched. The coordinator extracts from the accepted plan every path on a `**Files:**` line under `create` and `modify`, reads the PR's changed files, and raises `request_repair(plan_conformance)` naming every `create` path absent from the PR and every `modify` path unchanged in it. No vendor is spent. An implementer that believes a named path should not exist lists it under a `## Plan deviations` heading in the PR body with one line of reason; a listed path is exempt and the exemption is written to the round log, so the check cannot be bypassed quietly.

**Orphan adoption.** Each wave lists open PRs whose head branch carries an item code and which have no open run. For each, it starts a run directly at `implementing` (`run_started` already carries its opening state) with `base_sha` set to the merge-base, records `implementation_output` (PR number and head), and the step loop takes it from there. A legacy run left `ended` with an open PR is adopted this way exactly once; the old journal is not edited.

**`--recover-pr` is retired.** It remains for one release as an alias that adopts one PR and performs one step, then goes.

**Labels follow the journal.** Each pass sets the issue's label from the run's state: `bircher:running` while the run is open and stepping, `bircher:parked` on a park, `bircher:escalated` only after `stop`, none while nothing is open. The stale `bircher:running` on #764 came from a writeback that ran once per pass; a state-derived label cannot go stale.

**The round log.** The pipeline owns one comment per PR, marked `<!-- bircher:round-log -->`, and appends one line per step: the round, the action, the range, and the verdict or the failing jobs. The by-hand E10 log is what that comment should have contained.

## 6. Testing, proof, and order of work

**Kernel, from the refusal side,** against a real journal: `record_run_outcome` on a run with implementation output and an open PR is refused; `request_repair` from `planned` is refused; `park` with `no_progress` from `queued` is refused; a back-half `grant_round` and `cancel_run` are accepted; the every-command-declares-its-states self-test covers the additions.

**The step function by replay.** #764's journal at the moment its session died with red CI: expected `repair(ci_red)`, not an ending. #769's six verdicts: expected five repairs and a merge, no park, because every round changed the fingerprints. Two identical FAIL fingerprint sets: expected `park(no_progress)`. Each replay is a fixture file checked in with the test.

**Fault injection where the last design found its bugs:** session death between a recorded verdict and its repair dispatch; a status post that returns uncertain; a wave that finds the same run already stepping. Each against a live database, as the lifecycle tests are today.

**E11.** First on bircher-smoke, with an issue whose implementation is arranged to fail CI once and a review once, so both repair causes fire before the merge; a second smoke issue is arranged to repeat a finding until it parks, and is resumed with `retry`. Then one real muesli issue, hands off after its plan gate. E11 passes only if no park occurred on the muesli issue; a park there is a person in the loop, and the proof fails. The proof is the pipeline's own round comment, checked line by line against the journal.

**Order of PRs:**

1. Gate integrity (§4), with #101 folded in.
2. The kernel changes and the step function (§1 to §3), with the batch loop replaced behind them.
3. Adoption, labels and the round log (§5, without conformance).
4. Plan conformance.
5. Postgres and the Node toolchain in the review and repair containers.

Each PR goes through the pipeline's own cross-review; the codex plugin reviews what the pipeline cannot review about itself. The implementation plan is written per PR, in this order, each plan owning the acceptance lines below that its PR satisfies.

## Acceptance

- A run whose PR is open cannot be recorded `ended`; the kernel refuses it and the test proves the refusal.
- A red CI observation on an open PR produces a `ci_red` repair, replayed from #764's journal.
- Six alternating verdicts produce five repairs and a merge with no park, replayed from #769's journal.
- Two identical FAIL fingerprint sets produce `park(no_progress)`; a `retry` reply resumes.
- Every recorded verdict has a `bircher/cross-review` status on its head whose state matches the verdict and whose description names the range.
- Exactly one reviewer prompt exists in the repository and it names the range.
- An open PR on an item-code branch with no open run is adopted within one wave.
- A PR missing a plan-named `create` path without a listed deviation receives a `plan_conformance` repair before any review.
- E11 passes on bircher-smoke and then on one muesli issue, with the round log matching the journal.
