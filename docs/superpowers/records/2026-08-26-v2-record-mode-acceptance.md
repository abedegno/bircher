# v2 record mode: acceptance run

Plan: `docs/superpowers/plans/2026-08-26-v2-record-mode.md`
Spec: `docs/superpowers/specs/2026-08-26-v2-supersedes-v1-record-mode-design.md`
Runner: `omnigent-runner-bircher`, checkout `/workspaces/bircher-v2`, branch `v2`.
Mode: `BIRCHER_EFFECT_MODE=kernel`, `BIRCHER_KERNEL_MODE=shadow` (default).

Two items were run. The first one **failed**, and that failure is the most
valuable thing in this document.

## Run 1 (zz01) — failed, and why the suite could not have caught it

Two sessions were created and both sat idle: `send_prompt FAILED (rc 5)`, the
item stayed queued, nothing was delivered.

Root cause: `_effect`'s kernel branch in `batch/lib/effect-adapter.sh` invoked
`python3 -m kernel.cli` with **no `PYTHONPATH`**, so every effect died with
`No module named kernel`. Prompt delivery goes through
`_effect session_control`, so the run could not start work.

`_kernel` is advisory — a failure changes nothing. **`_effect` in kernel mode
is not advisory; it is the execution path.** So this did not merely lose the
recording, it lost the effect.

The same defect had been found in `_kernel` during Task 3's preflight and
fixed in `kernel-client.sh`. The instance was fixed; the class was assumed
closed without checking the other file with the same shape.

**Why 494 tests missed it.** Both kernel-mode tests in `test_fault_injection.py`
supply no run id, so `${VAR:?}` aborts the function *before* `python3` is
invoked. They bind the `:?` guards, not the command line. Mutation proof:
corrupting the module name to `kernel.NOPE` left **all 494 passing**.

Fixed in `9140f19`, with the positive half added at both invocation sites
(unbounded and capped). Removing the `PYTHONPATH` prefix now reds both tests,
and so does the `kernel.NOPE` mutation.

A related claim was retracted (`5ad56e4`): `test_cli_command`'s docstring said
the adapter's invocation had been verified out-of-suite "with a stub
`python3`". A stub imports nothing, so that check was structurally incapable of
observing this defect while reading as coverage.

## The second defect: no terminal record

Run 1 also exposed a gap that was not a wiring bug but a **vocabulary** one.
`run_item` has six exits that write a scorecard row; five left the kernel with
no terminal fact. A run that escalated, timed out, went noop or merged stayed
in `implementing` forever — indistinguishable from one still in flight.

The kernel had no command that could say otherwise: `merged` (legal only from
`merge_requested`) and `cancelled` were the only terminal records, against a
scorecard vocabulary of seven. Criterion 1 was therefore not merely unmet, it
was **unsatisfiable for six of seven outcomes**.

`record_run_outcome` (`99bc4a8`) closes it, and the wiring (`e2772cf`) records
at the three exits that have a dispatched generation. The other three fire
before one exists and name themselves, with reasons, in
`test_lifecycle_wiring._NO_KERNEL_OUTCOME` — with a further test asserting each
exempt site really does sit above the dispatch, so an exemption cannot survive
its site drifting below one.

## Run 2 (zz02) — the acceptance run

Run id `zz02-terminal-record-1787795851`, implementer `codex`, 
outcome `escalated` (the file it was asked about is absent, which was the
expected answer), no PR, item moved to `processed/`.

## Run 3 (zz03) — after the dispatch reorder

The final review found that `_effect issue_or_label "running:..."` ran before
any generation existed, so kernel mode silently dropped the `bircher:running`
label. The fix moves the implementer dispatch above session creation, which
also gives the session-create failure exit a generation to record under.

That is a reordering of the live coordinator, so the smoke was re-run:
`zz03-terminal-record-2-1787799397`, implementer `codex`, `outcome=escalated`,
projected `ended`, kernel outcome `escalated`, scorecard `escalated`. No new
shadow rejections. The reorder did not break the live path.

## Run 4 (smoke) — the gh/git effect path, and what it found

Criteria 2 and 3 were untested for `gh`/`git` effects. To close that without
touching `abedegno/muesli`, a throwaway private repo
(`abedegno/bircher-smoke`) was seeded, a PR opened in it, and the coordinator
pointed at it with `BIRCHER_REPO`/`WORKDIR`.

**The plan changed once, for safety.** The original intent was a full run with
an implementer. The codex agent bundle hardcodes
`git -C /workspaces/muesli worktree add …` in its IMPLEMENT step, so an
implementer working a scratch-repo item could have branched off — and pushed
to — the public repo. `--recover-pr` never launches an implementer, so that
step never executes; it exercises the same effect classes with none of that
exposure.

**Result: the recovery path performs no kernel-journalled effects at all.**

```
runs: []                      # the kernel database is empty
PR comments posted:           # none
statuses on the head:  ci=success        # only the one posted by hand
```

`BIRCHER_RUN_ID` is assigned in exactly ONE place in `run-queue.sh` — inside
`run_item`. `--recover-pr` never calls `run_item`, yet it performs
`_effect ref_update` directly and reaches `_post_cross_review_status` and the
merge through `merge_ready_pr`. In `BIRCHER_EFFECT_MODE=kernel` every one of
those aborts on `${BIRCHER_RUN_ID:?}` and is swallowed by the redirects around
it. **The documented path for landing a human PR is silently inert in the mode
v2 is meant to run in.**

Mapping every `_effect` site in the file separates two failure modes:

| context | functions |
|---|---|
| reached from `run_item` — generation valid | `run_item`, `_send_prompt`, `_prune_session`, `_post_cross_review_status`, `merge_ready_pr`, `_issue_writeback`, `_ensure_issue_closed`, `recover_from_ground_truth`, `_reconcile_item_pr` |
| **no generation** — effects abort | `recover_pr_cmd` |
| **stale generation** — attributed to whichever item ran last, because nothing unsets the exported run id | `reconcile_deferred_ready`, `_reopen_reverted_issues`, `_pr` |

`reconcile_deferred_ready` is the end-of-run sweep, so this is the same gap
F12 identified from the other direction: its scorecard rows are unowned by any
kernel run *and* would be recorded under the previous item's generation.

**Not fixed here.** Giving these paths their own kernel run is a design
decision — whether `--recover-pr` mints a run, whether the sweep re-dispatches
per item — not a wiring tweak. `test_every_effect_site_is_classified` makes the
set enumerable so a new site cannot join it silently, and
`test_the_known_gaps_are_still_gaps_and_not_quietly_more` fails if the list
changes in either direction.

Two incidental findings from the same run, both minor: the OAuth token lacks
`workflow` scope so the scratch repo could not be given a CI workflow, and a
repo with no checks at all never settles the CI wait (it blocked until a status
was posted by hand). Neither affects muesli.

## Run 5 — a full item, end to end, on the throwaway repo

The work-repo directive (`30ca909`) makes an off-muesli end-to-end run safe:
`run_item` now tells the implementer which repo and checkout to use, overriding
the literal `/workspaces/muesli` in the agent bundles. Confirmed in practice —
the implementer worked entirely in `/workspaces/bircher-smoke` and muesli was
untouched.

Item `s02-changelog`, implementer `codex`, reviewer `claude_code`. The loop
ran the whole way: worktree, commit, **PR #2 opened**, cross-vendor review
**passed** (`claude_code:pass`, 1 round), `bircher/cross-review=success`
posted and verified. The merge then failed closed, twice, including on the end-of-run sweep.

**That diagnosis was wrong and is corrected below.** This record first
attributed it to GitHub mergeability lag, because a manual
`gh pr merge --match-head-commit` minutes later succeeded. Run 6 showed the
real cause: `merge_ready_pr` performs the merge through `_effect merge`, which
in kernel mode is authorized against the run reaching `merge_requested`. The
chain was broken at `record_review`, so the run never got there and the kernel
REFUSED the merge -- correctly. The manual merge succeeded because it bypassed
the kernel entirely, which is exactly what made it misleading evidence.

### Criterion 2, revisited: **holds for the coordinator's effects; structurally N/A for the implementer's**

Journalled: `effect_intended`/`effect_confirmed` for **`status_check`** — the
cross-review status post, a real `gh api` mutation.

Not journalled, and this is the point: **PR #2's creation and the
`bircher-status:` marker comment never passed through `_effect` at all.** They
were performed by the implementer, from its own credential domain, exactly as
v1 does. This is C8, already named in the plan's "not delivered" section — the
kernel cannot journal what it never mediates. So criterion 2 as written cannot
hold until the implementer's effects are routed, and no amount of testing the
coordinator will change that.

### Criterion 3, revisited: **three rows, and they are one causal chain**

```json
[ {"command_name": "record_merge_outcome", "count": 1,
   "example_reason": "not legal from state 'implementing'; legal from ['merge_requested']"},
  {"command_name": "record_review", "count": 1,
   "example_reason": "verdict 'claude_code:pass' is not one of ['accept', 'reject', 'request_revision']"},
  {"command_name": "request_merge", "count": 1,
   "example_reason": "not legal from state 'implementing'; legal from ['reviewing']"} ]
```

Read bottom-up, this is a single root cause. The coordinator's verdict
vocabulary is `<vendor>:pass` / `<vendor>:fail` / `na`; the kernel's is
`{accept, reject, request_revision}`. `record_review` is refused, so the run
never leaves `implementing`, so `request_merge` is refused, so
`record_merge_outcome` is refused. **In enforce mode this run would have been
stopped at review** — and the report says so before anyone tries it, which is
what the report is for.

**A naive fix would not work, and would look like one.** `_kernel_record_review`
sends only `{"verdict": …}`, while `validate_review` also requires
`artifact_hash`, `base_sha`, `context_bundle_hash` and `policy_version`.
Translating the verdict word alone moves the refusal from "not one of …" to
"malformed verdict binding: 'policy_version'" — same outcome, different
message. The real fix must also bind the review to the artifact the kernel
holds, which means `_kernel_record_output` surfacing the hash it PUT so
`_kernel_record_review` can name it.

This is the first run to produce decision-grade shadow output, and it says the
merge path is **not** safe to enforce yet.

## Run 6 — the full chain, and an empty shadow report

Two untranslated vocabularies, found one after the other by running the thing:

1. **verdict** — the marker says `<vendor>:pass`, the kernel accepts
   `{accept, reject, request_revision}`. Fixed in `db27ecc`, together with the
   verdict BINDING (`artifact_hash`, `base_sha`, `context_bundle_hash`,
   `policy_version`) that `validate_review` also requires — translating the
   word alone would have moved the refusal to `malformed verdict binding` and
   read like progress.
2. **CI status** — the marker says `ci=green`, the merge gate asks
   `status == "success"`. Fixed in `9891256`. CI had been recorded faithfully
   and counted for nothing.

Item `s04-notes`, run `s04-notes-…`, implementer codex, reviewer claude_code.
The complete journal:

```
submit_spec → specified          submit_plan → planned
start_implementation → implementing
effect_intended/confirmed  session_control
record_implementation_output     record_ci_observation
record_review → review_verdict → reviewing
request_merge → merge_authorized → merge_requested
effect_intended/confirmed  status_check
effect_intended/confirmed  merge          ← PR #4 MERGED, kernel-mediated
record_merge_outcome → merged
record_run_outcome → ended
```

```
SHADOW REPORT: []
```

### What this settles

**Criterion 2 — every mutation journalled: HOLDS for the coordinator's
effects, including a real merge.** `session_control`, `status_check` and
**`merge`** all carry intent and confirmation. The merge is the one that
matters: it is a genuine `gh` mutation on a real repository, authorized by the
kernel and journalled before it happened. Still outside: the implementer's own
PR creation and marker comment, which never pass through `_effect` (C8).

**Criterion 3 — the shadow report is empty, and now means something.** The
previous zero was on a path that never submitted a review, a CI observation, a
merge request or a merge. This one submitted all four and every one was
accepted. That is the difference between "nothing was refused" and "nothing was
asked".

**Criterion 1 — kernel `ended`/`merged`, scorecard `ready`.** The run reached a
terminal state in agreement with what the coordinator recorded.

### The merge failures were the kernel working

Runs 5 and the s03 run both had their merges refused twice. This record
originally called that GitHub mergeability lag. It was not: `_effect merge` in
kernel mode is authorized against `merge_requested`, the broken verdict chain
never got the run there, and the kernel refused the mutation. **The merge
failing was the boundary doing its job** — and the manual merge that "worked"
worked by going around it.

## Run 7 — the kernel ENFORCING

Shadow mode's whole job is to say when enforcement is safe. For this path it
said yes. This run tests that claim rather than trusting it:
`BIRCHER_EFFECT_MODE=kernel`, `BIRCHER_KERNEL_MODE=enforce`, item `s05-enforce`.

**Result: PR #5 merged, main CI green, every command accepted.**

```
submit_spec → submit_plan → start_implementation
effect_intended  session_control
record_implementation_output → record_ci_observation
record_review → review_verdict → reviewing
request_merge → merge_authorized → merge_requested
effect_intended  status_check
effect_intended  merge
record_merge_outcome → merged
record_run_outcome → ended
shadow_rejected facts: 0
```

### Why the empty report is NOT the evidence here

In enforce mode `shadow_or_raise` raises instead of recording, so
`shadow_rejected` facts cannot exist by construction. An empty shadow report
under enforcement is therefore worth nothing on its own — it is exactly what a
run that never reached the kernel would also produce, and reading it as a pass
would be the defect this programme keeps finding.

The evidence is the positive half: **every command reached
`command_accepted`**, every transition fired, `merge_authorized` was recorded,
and the merge effect was performed and confirmed.

### Proof the switch was actually on

"No refusals" looks identical whether enforcement is active or silently
defaulted, so the mode was proven separately against the deployed bundle — the
same contract-violating effect, run twice, changing only
`BIRCHER_KERNEL_MODE`:

| mode | rc | effect |
|---|---|---|
| `shadow` | 0 | **EXECUTED** — refused and tolerated |
| `enforce` | 87 | **BLOCKED** — refused and stopped |

That is the boundary doing the one thing it exists to do, on the machine that
ran the acceptance item.

### What this settles, and what it does not

v2 now runs a real item end to end with the kernel **enforcing**: it authorizes
the merge, mediates it, and journals it. That is a demonstrated boundary, not
an observed one.

It does not settle the paths this run did not touch. `--recover-pr` and the
end-of-run sweep still perform effects with no generation or a stale one, and
under enforcement those abort exactly as they did under shadow. C8 is unchanged:
the implementer's PR creation and marker comment never reach the kernel in
either mode.

## Run 9 — recovery through merge: adoption is necessary, not sufficient

The verification Run 8 could not complete, done properly: a fresh PR on the
throwaway repo, green CI, `--recover-pr` under `BIRCHER_EFFECT_MODE=kernel`.

**The generation gap is genuinely closed.** Where the same command previously
produced an EMPTY kernel database, it now journals two effects with intent and
confirmation:

```
run_started → ownership_acquired → attempt_dispatched(reviewer)
effect_intended/confirmed  comment        ← the recovery marker
effect_intended/confirmed  status_check   ← bircher/cross-review
```

Both were silently aborting before. The adoption fallback also worked as
designed: `s06` was never a queue item, so no run existed and one was minted
(`no existing run for 's06' -- minting s06-adopted-…`).

**And the merge still fails, correctly.** Run state is `queued`. The recovery
path never records the lifecycle — no submit_spec, no plan, no
record_implementation_output, no record_review, no request_merge — so the
kernel holds no authorization and refuses `_effect merge`. The shadow report is
empty because that refusal happens at the EFFECT layer rather than as a
rejected command.

So the ruling closed the gap it was aimed at and revealed the next one:
**`--recover-pr` cannot merge under kernel mode until it drives the same
lifecycle `run_item` does.** Giving its effects a valid generation was
necessary and is not enough. That is a bounded piece of work — the wiring
already exists in `run_item` and would be reused — and it is not done here.

Worth stating plainly: this means the documented human-recovery path can
review and comment under kernel mode, and cannot land a PR. Under v1 (legacy
effects) it still can.

## Run 8 — the generation gap, closed for recovery and the sweep

Ruling taken rather than deferred further: **(b), adopt the item's original
run**, with minting as the fallback. Recovery and the end-of-run sweep are
continuations of an item's lifecycle — the run already holds the spec, the plan
and the implementation output the recovery is deciding about — so a ledger that
splits them tells you less. Run ids are `<item>-<epoch>`, so the item's run is
discoverable by prefix. Minting is the fallback for a PR that never came from
the queue, deliberately not the default: a fresh run presents an empty history
to a merge gate whose whole job is checking history.

**Cost if wrong:** recovery attempts appear as extra generations on an existing
run rather than as separate runs. The facts stay readable and it is a rename to
undo.

Live verification against the previous enforce run:

```
[batch:recover-pr] s05: kernel run=s05-enforce-1787837900 generation=4
```

and the adopted run grew from 40 to 42 facts, the new pair being
`ownership_acquired` + `attempt_dispatched(reviewer)`. Before this change the
same command produced an **empty** kernel database.

**What this verification does NOT cover.** The probe script failed to capture
the PR number, so the recovery ran against a nonexistent PR and was killed at
the CI wait rather than completing a review and merge. What is proven is the
part that was broken: adoption finds the run, fences a generation, and gives
the path's effects a valid context. A complete recovery through merge under
kernel mode is still unrun.

**Corrected.** No stale-generation site remains. `_reopen_reverted_issues` is
classified REACHED — its only caller is `merge_ready_pr`'s revert path, which
is itself reached from `run_item` — and `_pr` is not in the table at all: it
was a parser artefact, a one-line function whose span never closed, which
appeared to own an `_effect` occurrence inside a quoted self-test assertion.

This paragraph previously said both "remain STALE GENERATION". Only its first
clause was scoped as historical; the rest continued in the present tense and
was false, so a reader was left believing a checked claim. Announcing that a
correction matters is not the correction — the same shape as bolting a fix onto
code without removing what it replaced.

## Run 10 — a real item on abedegno/muesli, merged by the pipeline

The first run against the real target. Issue #728 (a p3 server-side defect
whose subject is this programme's own class: "hardcoding it to 0 keeps the
suite green"), `BIRCHER_EFFECT_MODE=kernel`, `BIRCHER_KERNEL_MODE=enforce`.

**Outcome: PR #730 MERGED**, sha `8dff63f`, main carries the change, issue
#728 closed. Verified against GitHub, not against the log — see below for why
that distinction earned its place.

### Verified live, on the real repo

- `bircher:running` applied to #728 — the CRITICAL defect from the first
  review, whose fix had never been exercised on an issue-backed item.
- The work-repo directive held: codex worked in `/workspaces/muesli`.
- `bircher/cross-review` → `review-gate` handshake fired as designed.
- The kernel authorized and mediated the merge under enforcement.

### Four defects, each exposed only by fixing the one before it

1. **`_out_hash` was branch-scoped.** Declared `local` inside the marker
   branch, read at the merge gate that every path reaches. Under `set -u` the
   NO-MARKER recovery path — which fires whenever an implementer session dies,
   and did, unprompted — crashed the coordinator after the PR was already open.
   My regression, introduced in the verdict-binding work, and invisible because
   the test harness drove only the marker path.
2. **The recovery branch recorded no lifecycle.** It reached the merge gate
   with no output, CI observation or verdict, so the kernel refused a merge it
   had no evidence for. Correct behaviour on an input the coordinator failed to
   supply. Both `run_item`'s branch and `--recover-pr` now CONTAIN that drive.
   For `run_item` it is exercised; for `--recover-pr` the evidence at the time
   was only that the calls appear in the source, and a later review showed the
   adopted-run path was still refused for binding the wrong base. "Wired" and
   "works" were not the same sentence and this recorded them as one.
3. **Reconciliation had no door.** `kernel.effects.reconcile` could always
   resolve an uncertain effect; nothing outside Python could ask. The merge
   came back uncertain — the coordinator races its own `review-gate`, posting
   `bircher/cross-review` and merging before the workflow that status triggers
   has run — and the run could not be advanced by any path the coordinator had.
   Added `kernel.cli pending` / `reconcile` and wired both recovery paths.
4. **A reconciled key was replayable, and answered `None`.** This one produced
   a WRONG ANSWER rather than a failure. Reconciliation leaves the external id
   None; the replay branch handled `uncertain` and `intended` and let
   `reconciled` fall through to `return existing["external_object_id"]`.
   `merge_ready_pr` retried under the same `merge:<pr>:<head>` key, got None,
   polled five times for a sha that could never arrive, and reported
   **"PR #730 MERGED (sha unknown)" while the PR was still open and main did
   not have the change.** Its fail-closed halt is the only reason it stopped
   there. Now `NotReplayable`, and the merge key carries the generation so a
   retry after reconciliation is a new attempt rather than a spent one.

### What this says about the evidence standard

I repeated the coordinator's "MERGED" line in a status report before checking
GitHub. It was false. That is the same defect class as the code being fixed — a
claim outrunning its evidence — committed in the report about the fix, and it
is why the merge above is stated against `gh pr view` rather than against a log
line that said the same thing an hour earlier and was wrong.

### Criterion 1 — the aggregate matches the scorecard: **HOLDS, on a narrow path**

| | |
|---|---|
| projected state | `ended` |
| kernel outcome | `escalated` |
| scorecard outcome | `escalated` |

The three `zz01` runs remain `implementing` with no kernel outcome. That is
history, recorded before `record_run_outcome` existed — not a live divergence,
and deliberately not back-filled.

**The caveat this criterion needs, and did not have.** It was checked on one
escalated run with no PR and no merge — a path where the known failure modes
cannot arise. Two of them are real:

1. `_kernel` is advisory and always returns 0, so a failed or refused terminal
   command leaves no fact while the scorecard row is written regardless. The
   run then stays non-terminal and the two disagree. The source comments at
   the three recording sites used to claim they "agree by construction"; they
   now say this.
2. `reconcile_deferred_ready` appends further terminal scorecard rows for the
   same item **after** `run_item` has returned, and can record `escalated`
   where `run_item` already recorded `ready`. Because a second
   `record_run_outcome` is refused by design, the kernel's terminal fact then
   disagrees with the scorecard's last word and can never be corrected. Those
   sweep rows are outside the scope of the class-closure tests, which read
   `run_item` only. **This is an open gap, not a solved one.**

### Criterion 2 (as of Run 3) — every mutation is journalled: **NARROW, not verified**

> Superseded by Runs 6, 7 and 10, which journalled `status_check` and a real
> kernel-mediated `merge`. See "Criterion 2, revisited" above and the Verdict
> below. Kept because the reasoning that produced it is the reasoning the
> later runs had to defeat.

The journal contains exactly two effects per run that got that far, both
`session_control` (the prompt send).

An earlier version of this record called this "vacuous" on the grounds that the
run performed no mutation at all. That was wrong in the pessimistic direction:
`session_control` **is** an externally visible mutation, it was performed, and
it was journalled — the criterion had something to check and it passed. What is
true is that the coverage is narrow: no `gh` or `git` effect ran, because the
item escalated without opening a PR.

**What would actually test it:** an item that opens a PR, posts a status and
merges — exercising `pull_request`, `status_check`, `comment` and `merge`.
That performs real, outward-facing effects on `abedegno/muesli` and is held
for explicit sign-off.

Also noted: `effect_confirmed` facts carry `effect_class=None`. The intent fact
carries the class and the confirmation does not, so the journal cannot be
filtered by class on confirmations alone.

### Criterion 3 (as of Run 3) — the shadow report: **zero rows, genuinely, on a narrow path**

> Superseded. Run 6 produced three decision-grade rows that drove two real
> fixes. See "Criterion 3, revisited" above.

```
[]
```

The plan says a zero-row report is to be suspicious of, not celebrated: it
means either the wiring is right or the kernel was never called, and Step 4's
journal distinguishes them. Here the kernel **was** called — the runs carry
`run_started`, `ownership_acquired`, `attempt_dispatched`, three
`command_accepted` with their transitions, a terminal `record_run_outcome`,
and the effect pair. So zero rows is genuine.

But no review, CI observation, merge request or merge command was ever
submitted. **Zero rows says nothing about whether the merge path is safe to
enforce.** The report becomes decision-grade only after a run that reaches
merge.

### Criterion 4 — a deliberately broken kernel changes nothing: **DOES NOT HOLD**

The spec calls this "the one that decides whether this was safe to do", and
an earlier version of this record omitted it entirely. It does not hold in the
configuration the acceptance run used.

Probe — `BIRCHER_EFFECT_MODE=kernel`, `BIRCHER_KERNEL_DB` pointed at an
unopenable path, one routed `ref_update` effect:

```
rc = 1
sqlite3.OperationalError: unable to open database file
the mutation happened: NO
```

The criterion holds for `_kernel`, which is advisory by construction: every
command call warns and returns 0, and nothing branches on it. It does **not**
hold for `_effect` in kernel mode, which is not advisory — it is the execution
path. A broken kernel there does not merely lose the record, it loses the
merge, the status post, the PR comment and prompt delivery. Run 1 of this very
document is the live demonstration.

The spec's supporting sentence, "each call site is `|| _kernel_warn` and
nothing reads a kernel exit code", is true of the `_kernel` sites and not of
the `_effect` sites, which carry no such guard.

**This is a property of the design, not a defect to fix here:** routing effects
through the kernel is the point, and a kernel that cannot journal must not let
the mutation proceed unrecorded. But it means **kernel effect mode is a hard
dependency**, and the safety argument for deploying it is not the one the
criterion states. Recorded as failed rather than reworded.

### Criterion 5 — the self-test stays green: **HOLDS**

`bash batch/run-queue.sh --self-test` exits 0, ending `self-test OK`.

## Verdict

Record mode works end to end for a run that starts, executes and ends without
merging: the kernel observes the lifecycle, journals the effect that matters,
and records how the run finished, in agreement with the scorecard.

- **Criterion 1** holds on this run, with two known divergence paths untested.
- **Criterion 2** is narrow, not vacuous, and not yet verified for `gh`/`git`.
- **Criterion 3** is implemented and returns a true zero, on a narrow path.
- **Criterion 4** does not hold in kernel effect mode, by design.
- **Criterion 5** holds.

**That verdict was written after Run 3 and left standing through Run 10.** It
names "a run that reaches merge" as the outstanding gate — which Runs 6, 7 and
10 each passed, the last of them on `abedegno/muesli` under enforcement. A
verdict section that does not move as its own document does is worse than none:
a reader who stops at the end gets the state of the work eight runs ago.

### Verdict as of Run 10

- **Criterion 1** holds; two divergence paths (advisory-call failure, the
  sweep's later rows) remain untested.
- **Criterion 2** holds for the coordinator's effects including a real
  kernel-mediated merge; the implementer's own PR and marker still never reach
  the kernel (C8), so it cannot hold fully until that changes.
- **Criterion 3** is implemented and returned decision-grade rows that drove
  two real fixes (Run 6, shadow mode). No shadow-report evidence exists for
  Run 10: it ran under ENFORCE, where `shadow_rejected` facts cannot exist, and
  this record's own rule four sections above says an empty report there is
  worth nothing. An earlier draft of this bullet claimed "a true zero on the
  path that merged" — a claim with no figure behind it, added by a pass whose
  subject is claims outrunning evidence.
- **Criterion 4** does not hold in kernel effect mode, by design: the kernel is
  a HARD dependency there, not an advisory observer.
- **Criterion 5** holds.

The outstanding gate is C8. Five review rounds closed everything else.

### On "fixes introduce defects" — what the evidence does and does not support

An earlier version of this paragraph said this branch's repair work has "a
demonstrated failure rate". It does not, and the claim was a sampling artefact
reported as a finding — in a document whose subject is claims outrunning
evidence.

**Supported, and causal rather than co-located.** Three defects did not exist
before the commit that closed the previous round's finding:

| introduced by the fix for | defect |
|---|---|
| round 2's verdict-skip | `review=accept` from a model-authored field minted a real kernel verdict |
| round 3's verdict fix | a local CAS increment absorbing a foreign writer's version |
| round 4's JSON fix | CI sanitisation normalising `suc"cess` into `success`, authorizing a merge |

**Not supported: any rate, or that repair work is worse than fresh work.**
From round 3 onward the reviewers were given ONLY the fix diff — 6, 5 and 2
commits against round 2's 28. "The new findings are in the fixes" is close to
tautological when the fixes are the entire corpus under review. No round
compared repair work against fresh work, so no rate exists to cite.

**What would test it:** hand a seat a diff containing both fresh feature work
and repair work, unlabelled, and compare finding density. That has not been
done.

**Separately supported, and independent of scope:** eleven specific mutations
survived a full green suite and were then killed — `kernel.NOPE`,
`runs[-1]`→`runs[0]`, a hardcoded adopt role, a deleted `_kernel_reconcile`
call, a reverted base binding, `&& false`, a deleted `_rp_drive` guard,
`&&`→`||`, `and`→`or`, `continue`→`break`, and hoisting a reconcile above its
guard. The shape they share is concrete and worth carrying forward: this
author binds PRODUCERS rather than consumers, and asserts TEXT or ABSENCES
rather than effects.
