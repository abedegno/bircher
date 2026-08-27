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

### Criterion 1 — the aggregate matches the scorecard: **HOLDS**

| | |
|---|---|
| projected state | `ended` |
| kernel outcome | `escalated` |
| scorecard outcome | `escalated` |

The three `zz01` runs remain `implementing` with no kernel outcome. That is
history, recorded before `record_run_outcome` existed — not a live divergence,
and deliberately not back-filled.

### Criterion 2 — every mutation is journalled: **VACUOUS, not verified**

The journal contains exactly two effects, both `session_control` (the prompt
send), for each of the two runs that got that far.

This run performed **no `gh` or `git` mutation at all** — it escalated without
opening a PR. So "every mutation is journalled" is satisfied only because
there were no mutations to journal. That is a pass that could not have failed,
and it is recorded here as not-yet-verified rather than as a green criterion.

**What would actually test it:** an item that opens a PR, posts a status and
merges — exercising `pull_request`, `status_check`, `comment` and `merge`.
That performs real, outward-facing effects on `abedegno/muesli` and is held
for explicit sign-off.

Also noted: `effect_confirmed` facts carry `effect_class=None`. The intent fact
carries the class and the confirmation does not, so the journal cannot be
filtered by class on confirmations alone. Cosmetic for now; it would matter to
any report that reconciles intents against confirmations.

### Criterion 3 — the shadow report: **zero rows**

```
[]
```

The plan says a zero-row report is to be suspicious of, not celebrated: it
means either the wiring is right or the kernel was never called, and Step 4's
journal distinguishes them. Here the kernel **was** called — the runs carry
`run_started`, `ownership_acquired`, `attempt_dispatched`, three
`command_accepted` with their transitions, a terminal `record_run_outcome`,
and the effect pair. So zero rows is genuine for the paths exercised.

But the paths exercised are narrow. No review, no CI observation, no merge
request, no merge. **Zero rows says nothing about whether the merge path is
safe to enforce**, because no command on that path was ever submitted. The
shadow report becomes decision-grade input only after a run that reaches merge.

## Verdict

Record mode works end to end for a run that starts, executes and ends without
merging: the kernel observes the lifecycle, journals the effect that matters,
and now records how the run finished, in agreement with the scorecard.

Criterion 1 holds. Criterion 3 is implemented and returns a true zero. 
Criterion 2 is untested by this run and is the outstanding gate before
enforcement can be argued for.
