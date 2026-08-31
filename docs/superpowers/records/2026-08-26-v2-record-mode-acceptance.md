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

---

# C8 Phase 1 — the publish surface

Run 2026-08-28 against `abedegno/bircher-smoke`, from `/workspaces/bircher-v2`
at `c0fb5a9`, under `BIRCHER_EFFECT_MODE=kernel BIRCHER_KERNEL_MODE=enforce`.
Nothing in this section touched `abedegno/muesli`; verified against its ref
list after the misconfiguration described below.

## The five criteria

**1. A session produces a commit and no PR — HELD, and it is worse than a
refusal.** A real session under the bundle (the sandbox wrapper is visible in
the process table, and the bundle's system prompt with it) wrote and committed
`e00d445` in `/tmp/c8impl-repo`. Asked to push, it issued exactly one tool call
— `git push origin c8impl-branch` — and that call **never returned**. It ran
about ten minutes before being cancelled, and `c8impl-branch` never appeared on
the remote.

That is the criterion met, but not in the way the bundle's own prompt
describes. The prompt tells the implementer "do not treat a failure to reach
GitHub as a problem to work around — it is the boundary working." There is no
failure to observe. The egress policy blackholes the connection, so the push
HANGS. An implementer that obediently does not work around it will sit there
until its session timeout, having produced one useless tool call. **The
boundary holds and the feedback is wrong.** Recorded for Phase 2: the denial
should be legible, either as a fast refusal or by removing the instruction that
implies one will arrive.

**2. The kernel publishes that commit — HELD.** `--publish c8impl
/tmp/c8impl-repo c8impl-branch e00d445…` produced
`https://github.com/abedegno/bircher-smoke/pull/10`, with the claimed oid
supplied and agreeing. The journal carries `effect_intended` and
`effect_confirmed` for both `ref_update` and `pull_request`, the latter's
`external_object_id` being the PR URL. A second, independent run without a
claim (`c8v6`) produced PR #9 on the same path.

**3. A commit on a stale base is refused — HELD.** Recorded base
`3e4fe5fe4804`, branch built on `f7b989846786`:

    refused: 4adf8109d96b does not descend from the run's base 3e4fe5fe4804

Both values named, `RC_REFUSED`, and `git ls-remote` shows no `c8stale` ref.

**4. A disagreeing claim is refused — HELD.**

    refused: claimed 000000000000 but observed 147208e0d2b0 at 'c8v6-branch';
    the observation decides and the claim disagrees

Both values named; nothing pushed.

**5. The shadow report after a clean run — UNTESTED, not passed.** It reads
`[]` for both clean runs. The criterion says an empty report is worth nothing
on its own and that the positive evidence is every command reaching
`command_accepted`. **There are no commands on this path at all.** `publish_cmd`
issues a dispatch and two effects and nothing else, so the evidence the
criterion asks for cannot exist here, and its emptiness is not a result. The
criterion was written for a path that runs commands; it does not bind this one.

## Three defects found by running, none visible by reading

Each survived a green unit suite, because every unit test stubs `_effect`.

**The push ran in the coordinator's cwd.** `git push origin` resolved `origin`
to the *bircher* repo rather than the one the work is on, and the nominated
object was not present there at all. The kernel journalled `effect_uncertain`
and halted the run. Fixed by running both effects in the nominated worktree;
`git -C` is not available because the argv contract stops a signature at the
first flag, which is correct and stays.

**A halt recorded its exception's class and nothing else.** `error:
"RuntimeError"` was the whole record. Diagnosing the above took three
round-trips re-running the effect by hand against the live world — the work a
journal exists to make unnecessary. The message is now recorded as `detail`.
The fix paid for itself immediately: the next failure named itself.

**The push and the PR could name different repositories.** `git push origin`
resolves through the worktree's remote; `gh pr create --repo "$REPO"` names one
explicitly, and nothing made them agree. `run-queue.sh` re-derives `REPO` from
`BIRCHER_REPO`, so a harness exporting `REPO` alone pushed to the smoke repo
and asked GitHub to open the PR on muesli. GitHub answered "Head ref must be a
branch" — an error about the head ref, for a fault in the repository.

**I misdiagnosed that one and shipped the wrong fix.** I read it as a
propagation delay between push and PR visibility, measured a delay with an
experiment that never reproduced the failing conditions, found the ~1s I
expected to find, and committed a thirty-poll wait for it. The measurement
confirmed a hypothesis it could not have refuted. Reverted in `c0fb5a9`; the
repository agreement is now checked first, before the kernel is consulted and
before any effect, because publishing to one repository while announcing it on
another has no recovery path worth building.

## Mutation results

Twelve mutations over the guards this phase introduces, each proved applied and
restored clean. Eleven died against the test named for them. One survived:
`_kernel_run_base` returning forty zeros instead of empty on a falsy base left
all 614 tests green — the existing unknown-run test exercises the `except`
path, and nothing reached the `or ""` beside it. Killed by a test that reaches
a run existing with no recorded base.

## Unchanged, as promised

`run_item` (521 lines) and `parse_marker` (29 lines) are byte-identical to
their state at the plan commit `02299a7`, checked by extracting both functions
at both revisions and diffing. `run-queue.sh` took 56 insertions and zero
deletions, at two points outside both functions.

**The marker is still how `run_item` learns outcomes.** `--publish` is reachable
only as its own subcommand; nothing in the normal item path calls it yet. Phase
2 retires the marker.

---

## CORRECTION to C8 Phase 1, criterion 1 (2026-08-29)

**The claim recorded above is withdrawn.** I wrote that the implementer's push
"HANGS" because "the egress policy blackholes the connection". That mechanism
is disproved, and criterion 1 is **not demonstrated**.

### What the evidence actually shows

The session that ran `git push origin c8impl-branch` and never returned was
running under sandbox launcher `/tmp/omnigent-sandbox-ork3z59r.py`, created
21:12 — matching the 21:13 start of that session's `claude` process. Decoding
its embedded config:

    backend: linux_landlock | allow_network: True
    egress_relay_port: None | egress_socket_path: None

**There was no egress relay in that session at all.** The bundle declared four
egress rules; the sandbox that ran carried none of them.

I then built a launcher from that exact config with `/usr/bin/git` as the
target instead of `/usr/local/bin/claude`, and pushed:

    [omnigent-sandbox] landlock active abi=6 write_roots=8 read_roots=unrestricted
    To https://github.com/abedegno/bircher-smoke.git
       e00d445..81a71a0  c8impl-branch -> c8impl-branch
    elapsed=2s

Two seconds, rc=0. Repeated against a *different* session's config that DOES
carry a relay (`egress_relay_port: 57927`): also 2 seconds, also rc=0.

The launcher sets no proxy environment variables, and Landlock restricts TCP
by PORT, not by destination host — so a direct connection to github.com:443 is
not something the allow-list is positioned to stop. The bundle's own comment
says as much about TCP-only enforcement; I read it as a caveat and should have
read it as the mechanism's limit.

### What remains true, and what does not

TRUE: the session produced a local commit, and nothing it did reached the
remote. `c8impl-branch` appeared on GitHub only when I pushed it myself.

NOT ESTABLISHED: that it *could not* have pushed. The tool call hung for
reasons I have not identified, and the same command outside the harness
completes in two seconds. A session that does not push is not the same as a
session that cannot, and criterion 1 is about *cannot*.

**OPEN, and it is the load-bearing question for C8:** does the v2_implementer
boundary actually deny a push? On this evidence I cannot say it does. The
whole design rests on "the credential never enters this session, and the
network policy denies the paths those operations use" — the second half of
that sentence is now in question, and the first half deserves its own test.

### Why this happened

I asked "did the push land?", got "no", and wrote down a mechanism. The
observation was real; the explanation was invented to fit it and never
checked. It is the same error as the propagation-delay misdiagnosis recorded
above — twice in one session, both times a mechanism asserted from an outcome.
The check that settled it took four minutes and could have been run first.

**Neither the Phase 1 nor the Phase 2 work depends on this being resolved** —
the kernel publishes correctly either way, and that is separately proved. What
depends on it is the claim that publication through the kernel is the *only*
route available to an implementer.

---

# C8 Phase 2 — retire the marker (in progress)

Branch `c8/phase-2`, four of six tasks complete. Phase 1 landed on `main`
first (fast-forward, 170 commits, verified green on the merged result).

## What is done and proved

**Tasks 1-4: the marker is gone from the code.** `parse_marker` (30 lines),
`_marker_bodies_since` (20) and their self-tests (44) are deleted;
`run_item` derives every outcome field from the repository via
`observe_outcome`; `test_marker_is_gone.py` fails if any shipped file writes or
reads a `bircher-status:` line again. 640 tests pass, `--self-test` green.

Twelve mutations across the three tasks, each proved applied and restored
clean; all twelve killed. The one the plan left open — "delete the empty-tuple
guard; if no existing test reds, the guard is unbound and the task is not
done" — reds
`test_an_empty_recovery_tuple_is_treated_as_a_failure[run_item]`.

**`observe_ci_history` verified against live GitHub**, which no unit test can
do since they all stub `gh`:

    main -> true|8
    raw API: finished_runs=100 distinct_shas=9
    earliest finished run: 2026-08-17T23:07:29Z success
    a branch that never existed -> unknown|

Nine distinct shas gives eight resubmissions; the earliest finished run
succeeded, so `ci_first=true`. A branch with no history reports `unknown`, not
`false|0` — the distinction the mutation table exists to protect.

**`observe_outcome` emits seven fields with live CI history** against a real
PR (`escalated|codex:na|...|||true|2`, the `true|2` read from the API).

## What is NOT done

**Task 6 (a full item end to end, no marker anywhere) has not run.** It needs a
live coordinator session, and session dispatch on this runner failed four of
five attempts tonight: `omnigent run` returns while the harness is still
starting, and the session goes idle without executing its queued message. That
is an orchestration problem, not a Phase 2 result, but it means criterion 1 of
Phase 2 is untested and the field-mapping table (criterion 2) is written from
the code rather than from an observed run.

**Task 5 (make the denied push legible) is suspended, not skipped.** Its whole
premise — that a denied push hangs — rests on the criterion-1 claim withdrawn
above. If the boundary does not deny the push at all, there is no stall to
bound and the task should be deleted rather than done. Resolving the boundary
question comes first.

## The scorecard mapping, from the code

Written from the source, NOT from an observed run — see Task 6 above.

| field | observation |
|---|---|
| `outcome` | `classify_recovery(pr, ci, verdict)` |
| `review` | `observe_review` — a reviewer run-queue dispatched itself |
| `ci_pass_first_try` | earliest finished workflow run's conclusion |
| `resubmissions` | distinct head shas CI ran on, minus one |
| `rounds` | **null** — no observation exists; renamed, not redefined |
| `pr`, `wall_seconds`, `bound`, `implementer` | unchanged, already observed |
| `cost` | **null** — unchanged, never populated |

### SECOND CORRECTION, same night: the boundary DOES enforce

The correction above was right to withdraw the original mechanism and wrong in
what it put in its place. Both errors have the same root, and it is worth
naming twice rather than hiding the second one.

**What settled it.** A real session, asked to run `curl https://example.com`
and print its proxy environment, reported:

    example=000
    HTTP_PROXY=http://omnigent:<token>@127.0.0.1:40463   (and https_/http_/HTTPS_)

`example.com` appears nowhere in the bundle's allow-list, and the request
FAILED. The session's traffic is routed through an authenticated local relay
that the parent process (`os_env`) injects into the environment — not through
Landlock, which matches TCP by port and cannot see a host or a path.

**Why my test said otherwise.** I built a launcher from a captured session
config and ran `git push` and `curl` under it directly. That process had no
parent to set `HTTP_PROXY`, so nothing routed through the relay and everything
went straight out. `run_launcher`'s own docstring says the wrap "inherits
HTTP_PROXY / CA env vars set by the parent" — I read the file that says so and
still concluded from my own unfaithful reproduction.

**Where criterion 1 now stands: PARTIALLY DEMONSTRATED.**

- DEMONSTRATED: a real session under this bundle cannot reach a host outside
  its allow-list. The egress boundary is enforced, by the relay.
- NOT OBSERVED: a `git push` returning a denial. Session dispatch failed on
  six of eight attempts tonight (`omnigent run` returns while the harness is
  still starting; the session goes idle without executing its queued message),
  and the two that worked were spent on the questions above.
- MOST LIKELY, and still an inference: the hung push went to the relay and was
  refused there, which is consistent with a call that never returns. Recorded
  as a hypothesis, not a finding, because that is the distinction this whole
  section exists to enforce.

**The lesson, since it is now the third instance.** Twice tonight I explained
an outcome with a mechanism I had not tested, and once I disproved a real
mechanism with a test that did not reproduce the conditions. The common error
is not carelessness about evidence — it is treating a reproduction as faithful
without checking what the real path supplies that mine does not. The question
that would have caught all three: **what does the production path do that my
test does not?**

**The cross-vendor seat caught it first, with no tooling at all.** A codex
reviewer was dispatched to attack the withdrawal while I kept testing. Every
container command it tried failed (`exit 7` — its sandbox has no route to the
NAS), so it ran nothing and read nothing. It still returned:

> **Verdict: REFUTED — insufficiently demonstrated.** The direct-`git`
> launcher proves network-level push capability, but not that a real
> Claude-driven v2 implementer can push; it bypasses the unresolved layers.

and declined to answer the two questions it had no evidence for rather than
manufacturing a mechanism for either. Its nominated decisive experiment — have
the real harness invoke `git push` under the session's own launcher — is
exactly the one still outstanding.

Worth recording precisely because it had NO advantage over me except distance:
same facts, no execution, and it went straight to "is your reproduction the
thing that runs?" while I was busy running more of it.

### THIRD AND FINAL: criterion 1 is DEMONSTRATED, and the push does not hang

A real session under the v2_implementer bundle, asked to attempt the push as an
authorised boundary verification, ran:

    timeout 45 git push origin c8impl-branch 2>&1; echo "EXIT=$?"

and reported:

    error: RPC failed; HTTP 403 curl 22 The requested URL returned error: 403
    send-pack: unexpected disconnect while reading sideband packet
    fatal: the remote end hung up unexpectedly
    Everything up-to-date
    EXIT=1

**EXIT=1, not 124.** Not a timeout. An HTTP 403 from the egress relay, refusing
the `git-receive-pack` path that the bundle's allow-list deliberately omits.
The session's commits never reached the remote — local `78db634`, remote
`a76029f`.

**Criterion 1 holds.** A v2_implementer produces a commit and cannot publish
it, and the refusal is immediate, legible and attributable to the allow-list.

**And the push does NOT hang — that claim is withdrawn for the last time.**
Twice a session wedged after being asked to push, which I read as the push
hanging. The harness watchdog said what I did not: "likely a wedged LLM **or**
tool call". It was the model. Bounded, the push answers in under a second.

### Why it took three attempts, and the one thing that fixed it

Every earlier attempt let the command run UNBOUNDED, so a wedge anywhere in the
stack swallowed the result and I inferred a mechanism from the silence. The
answer arrived the moment the command was bounded — `timeout 45` — because a
bound converts "no result" into a result. **When an observation keeps failing
to arrive, the problem is usually that nothing forces it to.**

That is a different failure from the earlier two. Those were reproductions that
omitted what production supplies. This one was an observation with no deadline,
which cannot distinguish "slow", "hung" and "never going to answer".

### Task 5 does not exist

"Make the denied push legible" assumed a stall to bound. There is none: the
denial is a fast, explicit HTTP 403. The task is deleted rather than done.

### Session dispatch: `omnigent run` was the problem, not the runner

Six of eight `omnigent run` invocations went idle holding an unread message.
Driven through the REST sequence run-queue.sh actually uses — upload bundle →
holder session → agent id → create session → post event — every attempt worked
first time. I had been testing through a mechanism the pipeline never uses,
and reading its flakiness as the runner's.

---

## C8 Phase 2 — ACCEPTED (2026-08-29)

Item `s07-phase2-derivation` ran end to end on `abedegno/bircher-smoke`,
launched through `batch/launch.sh` so the run also exercised the new
effect-mode default. Implementer codex, reviewer claude_code, PR #11 merged,
main CI green.

### The three criteria

**1. A full item runs with no `bircher-status:` comment anywhere, and merges —
HELD.** `gh pr view 11 --json comments` contains zero occurrences. The only
comment on the PR is the derived prose, which opens "Cross-vendor review
(outcome derived from the repository, not reported)". `PHASE2.md` is on main.

The log shows the new path taking over where the marker used to be read:

    cap reached, session ... still alive -> cancelling
    deriving the outcome from the repository
    PR #11 CI green, no marker -> claude_code recovery review at da059eb
    posted+verified bircher/cross-review=success on da059eb
    PR #11 MERGED; watching main CI -> green
    -> outcome=ready pr=11 review=claude_code:pass rounds=? bound=ok

**2. Every scorecard field is traceable to an observation — HELD, and now from
an OBSERVED run rather than from the source.** The row:

    {"item": "s07-phase2-derivation", "pr": 11, "outcome": "ready",
     "implementer": "codex", "review": "claude_code:pass",
     "ci_pass_first_try": true, "rounds": null, "resubmissions": 0,
     "wall_seconds": 1536, "cost": null, "bound": "ok",
     "note": "out-of-band review PASS"}

`rounds: null` and `resubmissions: 0` are Decision 2 working: the field with no
observation behind it reports nothing, and the one that replaced it reports
what CI history showed. The note carries no `RECOVERED:` prefix.

**3. `--self-test` green and every removed guard's replacement named — HELD.**
691 tests pass, 1 skipped (a detached-launch test that needs `setsid`, verified
separately on the Linux runner). The replacement table is in the commit that
deleted the marker vocabulary.

### Criterion 5 from Phase 1 is now satisfied

Phase 1 recorded it as UNTESTED because `--publish` issues no commands, so the
positive evidence it asks for could not exist. This run issues them:

    command_requested x9   command_accepted x9
    effect_intended x6     effect_confirmed x6
    merge_authorized x1    review_verdict x1
    transition_performed x7   state=ended

Nine commands, nine accepted, no refusals. The shadow report reads `[]`, and
here that means something — under enforce, with commands actually flowing, an
empty report is the absence of refusals rather than the absence of traffic.

The six journalled effects, with their external ids:

    session_control  {"id":"47bf2a29...","agent_id":"77db59f...   (session create)
    session_control  {"queued":true,"item_id":"a3c7416c...        (prompt)
    session_control  {"queued":false}                             (stop)
    comment          .../pull/11#issuecomment-...
    status_check     .../repos/abedegno/bircher-smoke/...
    merge            ok

**The first and third were unrouted and INVISIBLE to the detector until earlier
the same day.** They are in this journal because that was fixed hours before
this run; a Phase 2 acceptance taken yesterday would have recorded a complete
journal that silently omitted session creation and termination.

### The cost of removing the marker, measured

    s05-enforce (marker era)     wall_seconds =  136
    s07 (derived)                wall_seconds = 1536

**Eleven times longer, and almost all of it is waiting.** The implementer
opened its PR within about four minutes; the run then sat until the 25-minute
`ITEM_TIMEOUT` cap, because the coordinator can no longer say "I am done" and
`idle` is not death. The derivation itself, once it started, took under a
minute.

This was predicted when the poll loop's marker check was removed, and it is
recorded here as a measurement rather than an estimate. It is the single
largest regression in Phase 2 and it is structural, not a bug: the fix is a
completion signal that is an OBSERVATION rather than a model-authored comment
— the session's own terminal state, or the PR's — and that is Phase 3 work, not
a tuning problem.

### The wall-clock regression, addressed and measured

    s05-enforce   marker era              136s
    s07           derived, cap-bound     1536s   (11x)
    s08           derived, settle-bound   318s   (2.3x)

**4.8x faster than s07**, on the same repo with the same shape of item. The log
line that replaced the wait:

    s08-settle-probe: PR #12 open and session quiet for 4 polls
                      -> deriving now rather than waiting for the cap

The remaining 318s is roughly three minutes of implementer work plus the four
quiet polls the check requires (180s at the 45s poll interval). The threshold
is `BIRCHER_SETTLE_POLLS`, so the residual is tunable against the risk below,
not structural.

**What the signal is, and what it is not.** The marker was the coordinator
REPORTING convergence. The replacement OBSERVES it: the session is idle and
has stopped producing items, held for four consecutive polls, with a PR
already open. Neither half suffices alone -- `idle` is not done (a coordinator
awaiting a sub-agent is idle, which is why `died()` refuses to read it as
death), and a stable item count is not done either (a session mid-tool-call
produces no items while it works).

**The residual risk, stated rather than mitigated away.** A coordinator whose
sub-agent takes longer than four quiet polls looks settled. The guard against
deriving too early is the PR requirement plus the fact that the derivation
re-reads CI and dispatches its own review -- it does not trust a snapshot taken
at break time. But an item whose coordinator would have pushed a fix after 200
seconds of silence would now be derived on the earlier head. Raising
`BIRCHER_SETTLE_POLLS` trades wall-clock for that margin.

A failed lookup RESETS the streak rather than extending it: "I cannot see the
session" is not "nothing is happening", and treating an unreadable session as
quiet would settle during a server outage -- exactly when a coordinator is
least likely to have finished.

**Also fixed:** the teardown logged "cap reached, session still alive" even
when the loop ended early, so every fast item's log contained a false
statement about why it stopped. It now says which.

---

## The derivation in Python — acceptance (2026-08-30)

Item `s13-python-derivation` on `abedegno/bircher-smoke`, PR #17 merged, main CI
green. `observe_outcome` is now `coordinator.cli derive`; run-queue.sh lost 253
lines.

### The comparison that matters

Every scorecard field, against `s08` — the last item the BASH derived:

    same  bound              ok            | ok
    same  ci_pass_first_try  True          | True
    same  implementer        codex         | codex
    same  note               out-of-band review PASS | out-of-band review PASS
    same  outcome            ready         | ready
    same  resubmissions      0             | 0
    same  review             claude_code:pass | claude_code:pass
    same  rounds             None          | None
    *     wall_seconds       318           | 408

Nine of ten identical. The tenth is implementer and CI timing, not derivation.

The kernel journal is the same shape as the bash-derived run: 9 commands
requested, 9 accepted, 6 effects intended, 6 confirmed, one merge authorized,
`state=ended`.

### Criterion 1 does NOT hold as worded, and the reason is worth keeping

There IS a `bircher-status:` comment on PR #17. It was written by the
IMPLEMENTER's own session:

    bircher-status: outcome=ready ci=green ci_first=true review=claude_code:pass
    rounds=1 head=c5027ca... note="s13: DERIVED.md added, contract exact"

Nothing read it. The outcome came from the repository, and the journal shows
the derivation never looked at a comment. But the criterion says "no
`bircher-status:` comment anywhere", and there is one.

**The bundle prompt already forbids it** — `agents/codex/config.yaml` says "do
NOT post a status marker or classify the PR outcome" — and the model did it
anyway. What differed from `s07` and `s08`, which had ZERO occurrences, is the
ITEM text: those said "do not write a `bircher-status` line anywhere" and this
one only said "do not post a status comment of any kind."

So the honest statement is: **the marker is no longer READ, and a model can
still WRITE one.** Prompt wording is the only thing discouraging it, and prompt
wording is not a mechanism. Retiring a channel removes the reader; it cannot
remove the habit.

### Five live runs, five defects, none of them findable by the suite

The port was green on 900+ tests before the first live run. Every one of these
came from an actual wave:

  1. `gh api` REJECTS `--repo`, which the generic runner appended to every
     call. Every api call failed, `head_of` returned an empty sha, no merge
     could be pinned, and a green PR escalated. The self-test's fake `gh`
     ignores unknown arguments, so it passed -- a stub more permissive than the
     real tool hides exactly this.
  2. `RECOVERY_REVIEWER` is a plain shell assignment, not an export. The
     subprocess never saw it and defaulted to the implementer's OWN vendor.
     Codex would have reviewed codex's work; cross-vendor independence ended
     silently, with nothing failing.
  3. `REPO`, `SERVER` and `BUNDLE_DIR` are unexported too. The run only worked
     because the acceptance launcher happened to export `BIRCHER_REPO`.
  4. The reviewer's streams were captured SEPARATELY and concatenated, so
     omnigent's stderr progress lines landed after the verdict. `extract_verdict`
     reads the last non-blank line -- by design, because a verdict mid-report is
     not a verdict -- so a genuine `VERDICT: PASS` was read as no verdict and
     every review escalated. The bash used `2>&1`: one interleaved stream.
  5. The reviewer's FINDINGS were dropped from the posted comment (caught by
     `--self-test` rather than the live run, but the same class).

Four of the five are the same shape: **the port assumed an environment it did
not have.** Unexported globals, a flag the real tool rejects, two streams where
the shell had one. None is a logic error, and no test that injects its
dependencies can see any of them.

**That is the argument for the plan having ended in a live run**, and against
ever treating a green suite as sufficient for a port of an I/O-bound path.

---

## v2 merges a real muesli item — 2026-08-30

**The binding goal was "v2 working to supersede v1". v2 has now taken a real
issue on `abedegno/muesli` to a merged pull request with main CI green, every
externally visible mutation kernel-authorised and journalled.**

muesli #726 (`parseChatError` cannot read a status from a real bridge error)
→ PR #735, merged as `eb49d394`, issue closed, labels cleared. Implementer
codex, reviewer claude_code, effect mode `kernel`, kernel mode `enforce`.

### The defect the smoke run found first, which would have hit muesli

Before touching muesli the smoke item was re-run with the day's nine fixes
deployed. It passed — merged, CI green. Then the effects were checked for WHERE
they landed rather than whether they happened:

**The derivation's review comments were being posted to the wrong repository.**
`gh pr comment` carried no `--repo`, so `gh` resolved the target from the
coordinator's own working directory — the bircher checkout. The comments for
smoke runs s13 and s14 landed on `abedegno/bircher` issues #17 and #18.

The kernel journalled `effect_intended` AND `effect_confirmed` for both, and
was right to: the command succeeded. **The journal recorded a true fact about a
command and a false impression about the world.** 940 tests, five cross-review
rounds and two green live runs all missed it, because every one of them checked
that the effect was PERFORMED, never where it LANDED.

Second instance of the shape — `publish_cmd` ran `git push` in the
coordinator's cwd where `origin` resolved to bircher. That instance was fixed
and the class was not closed. It is closed now: an enumeration requires every
`gh` effect argv to name its repo (`gh api` exempted, the repo is in its URL),
plus a test binding the VALUE rather than the flag's presence.

### What the live run proved, including by failing

The first merge attempt FAILED, and the failure is the most valuable part.

muesli requires `review-gate`, not `bircher/cross-review`. They chain: bircher
posts cross-review, a workflow reacts to that status event and posts the
required check. Measured timings:

    13:29:11  bircher/cross-review = success   (bircher posts)
    13:29:18  review-gate          = success   (the workflow reacts, +7s)

The merge was attempted inside that seven-second window and GitHub refused:
`base branch policy prohibits the merge`. The kernel recorded
`effect_uncertain`, **halted the run pending reconciliation**, and then
REJECTED `record_merge_outcome` and `record_run_outcome` with
`reason: halted`. The sweep's retry could not proceed either.

Nothing was forced, nothing was guessed, and no outcome was recorded for a run
whose effect state was unresolved. That is criterion 2 doing exactly its job
against a real-world race, and it is the first time the halt path has fired on
a live repository.

Resolution was by observation, not assumption: PR #735 was read back as `OPEN`
with `mergedAt=null` and no merge commit, so the merge definitively had not
happened. `kernel.cli reconcile` under expected-version CAS (7 → 8) recorded
that resolution, and `--recover-pr` re-drove the merge at **generation 5** — a
new generation, so a genuinely new attempt rather than a replay of a spent key.
`merge:735:c9f51fb…:g5` is `effect_confirmed` in the journal.

**The gap this exposes, not yet fixed:** a merge refused for a DEFINITIVE
reason ("base branch policy prohibits") is not an uncertain outcome — it is a
clean refusal, and it is known that the merge did not happen. Treating it as
uncertain halts the run and blocks the automatic retry that would have
succeeded seconds later. Distinguishing a definitive refusal from a genuinely
uncertain one (timeout, transport death) would let this class self-heal. That
is a design change and is deliberately not made here.

### Criterion 1 on a real item: still PARTIAL, and now measured on muesli

The implementer wrote a `bircher-status:` marker into its own PR comment.
Nothing read it — the outcome was derived from the repository — but the
criterion says no marker anywhere. On the smoke items the marker was absent
only because the hand-written item text forbade it by name; a muesli item's
text comes from the ISSUE, which does not. **Prompt wording is the only thing
discouraging it, and prompt wording is not a mechanism.**

### What is now true

- v1 remains deployed at `/workspaces/bircher` (218 commits behind); v2 runs
  from `/workspaces/bircher-v2` on current main. Both exist side by side.
- The cutover is NOT done: nothing schedules a wave, and v1 is untouched.

---

## Wave 01 — four items unattended, 2026-08-31

The first multi-item wave, run to answer one question: **is the repair gap the
binding constraint, or is something else?** Four real muesli issues, queued and
left alone.

| item | pri | PR | outcome |
|---|---|---|---|
| #722 python streaming transcriber window cap | p1 | 741 | **escalated** — review FAIL |
| #721 streaming API drops frames under backpressure | p2 | 742 | **MERGED** |
| #727 post-publication note writes | p2 | 743 | **escalated** — review FAIL |
| #725 live stream endpoint gates by note status | p3 | 744 | **MERGED** |

**No mechanism failure observed BY THESE CHECKS** — which is a narrower claim
than the one first written here, and the difference matters. The checks were:
a grep of the wave log for `halt|uncertain|malformed|BLOCKED|no tuple`, and a
per-run journal count. Journals:

    i722  31 facts  8 intended / 8 confirmed  merge_authorized=0
    i721  52 facts  9 intended / 9 confirmed  merge_authorized=1
    i727  29 facts  7 intended / 7 confirmed  merge_authorized=0
    i725  50 facts  8 intended / 8 confirmed  merge_authorized=1

What those checks CANNOT see: an action the mechanism never journalled at all;
a wrong action or payload that succeeded; a confirmation that disagrees with
external state; stale review inputs; a failure expressed under a fact kind the
grep does not name. Balanced intended/confirmed proves only that everything
journalled as intended was later confirmed — it is silent about omissions.
Establishing the broad claim needs each run reconciled against external state
(PR identity, head SHA, labels, verdict, merge state) and fault injection for
omissions and wrong-but-successful payloads. That has not been done.

Two merges, two escalations, both issues correctly labelled and both PRs left
open for a human.

### What the escalations were

Both were specific and actionable, with a named fix — not flakes.

**#741** — the diff bounded the decode window to the trailing 5s but left `t0`
computed from the ORIGINAL utterance start, so any utterance over 5s would emit
a caption whose displayed span was far wider than the text it contained
("0:00–0:12" showing only the words from 0:07). The reviewer verified against
`git show <sha>^` that the diff introduced it, noted the new test asserts only
on byte counts and never on `t0`/`t1` so the suite could not see it, and named
the fix.

**#743** — not summarised here, because I did not read it in the same detail.
Recording it as "the same shape" was an assertion about a document I had
skimmed.

### What this establishes, stated narrowly

**A review finding was the immediate stopping condition in 2 of 4 runs.** That
is the observation. It is NOT a demonstration that repair is the binding
constraint on autonomy, and the first version of this section claimed that.

There was no control and no repair-enabled arm. The two failures are
correlated — both transcription-adjacent, both reviewed by the same vendor —
and the two merges may simply have been easier items. The experiment cannot
separate a missing repair loop from task difficulty, implementation quality,
reviewer behaviour or issue-specific risk.

Nor is "these would plainly resolve in a fix round" supported. No repair was
attempted. #741's implementer already missed that timestamp invariant once,
with the issue text in front of it; a repair could introduce a different defect
or draw a new finding. Both escalations are **candidate repairs**, not
demonstrated ones.

### The experiment that would actually discriminate

Cheapest first: run the two failed PRs through a bounded repair round with a
fresh cross-vendor review, recording rounds, regressions and final disposition.
Convergence within the bound is what would support calling repair the
constraint; failure to converge would point at implementation quality instead.

Then, to separate the confounders: matched or randomised items through
current and repair-enabled pipelines, merge/escalation criteria fixed in
advance, reviewer vendor varied, comparing unattended completion rate AND
new-defect rate.

---

## The repair experiment — 2026-08-31

Cross-review of wave 01 said its conclusion was unsupported and named the
cheapest discriminating test: **run the two escalated items through a repair
round and see whether they converge.** This is that test. It took three
attempts, and the first two were void for a reason worth more than the result.

### Attempts 1 and 2 were void, and that is the finding

Both items' findings were written onto their issues, the PRs closed, the issues
re-queued. Both came back `review=codex:fail`. Neither had been reviewed:

    fatal: '/tmp/review-745' already exists
    Per your instruction, I stopped without reviewing anything.
    VERDICT: FAIL

Two defects, and the second matters more than the first.

**Both reviewers wrote the same worktree path.** `skills/cross-review/SKILL.md`
for the lead session's reviewer, `review.py::_PROMPT` for the coordinator's,
each `/tmp/review-<PR>`. The first created it; the second died on it. Nothing
cleaned them up either — the runner held **30** stale worktrees, back to smoke
PRs #11–#16.

A sha-derived nonce does not fix this, which was the first attempt at a fix:
the two reviewers review the SAME commit, so they compute the same nonce. The
coordinator's path now carries an explicit `-oob` suffix naming which reviewer
it is.

**The prompt offered only PASS and FAIL.** A reviewer that could not review had
to claim one. It said FAIL — and the run recorded `review=codex:fail` and
escalated the item as though a reviewer had judged the code. There is now a
third verdict: `BLOCKED` means "I formed no opinion", `extract_verdict` maps it
to None, and None already routes to escalated rather than to a rejection.

**An infrastructure failure inside a reviewer was indistinguishable from a code
rejection.** Two consecutive experimental results were fabricated by it, and I
drew a conclusion from the first before checking.

### Attempt 3, with both fixed

Worktree paths confirmed unique (`/tmp/review-747-866a6187-oob`,
`/tmp/review-748-c1c02737-oob`), reviews genuine — gates run and reconciled,
`pytest -q: 13 passed`, `ruff check: All checks passed`.

**Both repairs converged on exactly what was routed.**

`#722` → PR #747. The routed finding was that `t0` stayed anchored to the
utterance start after the decode window was bounded. The fix is
`window_start_frame = max(self._segment_start_frame, end_frame - partial_frames)`
— the suggested anchor — plus a new test,
`test_partial_timestamps_bound_the_decoded_trailing_window`, docstringed
"Regression test for the #741 review finding" and asserting on `t0` rather than
on byte counts, which was the specific gap named.

`#727` → PR #748. The routed finding was that the guarded write preceded the
storage delete, so a failed delete stranded a note as permanently
non-retranscribable. The fix introduces `SetRetentionStateDiscardedIfCurrent`,
which "spans the storage delete itself under a notes-row lock", with six new
tests.

**And both drew a NEW, narrower finding in the same area.**

- #747: partial timestamps are still wrong when an utterance contains
  VAD-classified pauses shorter than `silence_threshold_ms` — a residual case
  of the same class.
- #748: `DeleteNoteSummariesIfCurrent` does not make its generation check and
  deletion atomic — the same class, a different function.

### What this supports — CORRECTED after review

An earlier version of this section called the experiment "that test" and said
the repairs "converged". Both were wrong, and the correction matters.

**The pre-registered test was convergence within a BOUNDED LOOP, to a final
disposition. This ran ONE round, and both items are still blocked.** Moving the
endpoint from "merged or bound exhausted" to "the first requested change
appeared" is exactly the kind of substitution this record exists to catch.

What is actually supported: **routing a finding back caused a targeted patch
change.** Both implementers produced the specific fix named, including the
specific missing test named, and the diffs show it. That is real and it is
narrower than "repair works".

What is NOT supported:

- **That the repairs WORK.** I verified the code EXISTS — the anchor
  expression, the lock-spanning function, the new test names. I did not verify
  either fix under the scenario it addresses: no failure injection for the
  storage delete, no concurrency test, no run of the timestamp case. `13
  passed` and a clean `ruff` do not speak to either.
- **That nothing regressed.** Unspecified generic gate results cannot support
  that claim.
- **That a bounded loop terminates in a merge.** Untested. Both items sit
  blocked after round one.

### The new findings: class unestablished

I described both as "the same class, narrower". For #747 that is defensible —
sub-threshold VAD pauses are a residual of the same timestamp defect. For #748
it is not: `DeleteNoteSummariesIfCurrent` is a DIFFERENT function, and the
finding may be an independent defect rather than a refinement.

"The previous review did not raise it" does not show it was newly exposed. It
is equally consistent with reviewer sampling — that a review of any
sufficiently large diff surfaces something. **That reading would mean a bounded
loop terminates at its bound rather than at a merge**, which is the outcome
that matters and which this experiment does not measure.

Settling it needs a baseline: repeated blinded reviews of an UNCHANGED diff, to
estimate how much a reviewer finds by churn alone, compared against bounded
repair runs with fixed stopping criteria.

### Contamination audit of earlier results

The false-FAIL mode — an infrastructure failure reported as a code rejection —
could have affected any earlier escalation, and the first version of this
section quarantined only attempts 1 and 2.

**The primary evidence is gone: my own `rm -rf /tmp/review-*` cleanup deleted
the earlier review logs, which match that glob.** So this audit is indirect.

- **The FAILs that conclusions were drawn from are backed by quoted
  substantive findings** — #739, #741 and #743 are each recorded here with
  file:line citations, hand-traced code, and `git show <sha>^` comparisons. A
  collision produces a ~600-byte log saying "already exists" and nothing else.
  Those reviews were real.
- **A PASS cannot be a collision artefact.** The failure mode is: the reviewer
  cannot check out, stops, and — offered only two verdicts — says FAIL. It
  never yields PASS. So every merge recorded here is structurally immune to
  this defect.
- **Attempts 1 and 2 are void** and are marked as such.

That leaves no known contaminated conclusion, but the argument rests on quoted
excerpts rather than on the logs, because I destroyed them.
