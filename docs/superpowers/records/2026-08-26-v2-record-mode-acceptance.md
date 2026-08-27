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
posted and verified. The merge then failed closed, twice, including on the
end-of-run sweep; a manual `gh pr merge --match-head-commit` minutes later
succeeded, which is consistent with GitHub mergeability lag after the status
post. Not diagnosed further, and not claimed as more than that.

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

### Criterion 2 — every mutation is journalled: **NARROW, not verified**

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

### Criterion 3 — the shadow report: **zero rows, genuinely, on a narrow path**

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

The outstanding gate before enforcement can be argued for is a run that
reaches merge. Until then criteria 2 and 3 are untested where it counts, and
criterion 4 should be read as "the kernel is a hard dependency", not as the
safety guarantee the spec's wording implies.
