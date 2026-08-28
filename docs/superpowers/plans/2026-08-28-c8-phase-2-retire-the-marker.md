# C8 Phase 2 — Retire the Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `bircher-status:` marker — the coordinator's self-report of what happened — with observations run-queue makes for itself, so that a v2 implementer with no comment authority can complete an item.

**Architecture:** `recover_from_ground_truth` already derives the same tuple the marker carries, from CI checks and a reviewer it dispatches itself. Phase 2 makes that derived path the only path: run_item stops reading a marker, the derivation is factored into named observers, and `parse_marker` is deleted. The outcome vocabulary is unchanged.

**Tech Stack:** bash (`batch/run-queue.sh`, `batch/lib/*.sh`), python 3 tests under `v2/tests/execution/`, `gh` CLI, the v2 kernel.

**Spec:** `docs/superpowers/specs/2026-08-28-c8-the-kernel-publishes-design.md` — section "Phase 2 — retire the marker".

**Phase 1 record (read it):** `docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md`, section "C8 Phase 1 — the publish surface". Three of Phase 1's defects were invisible to a green unit suite because every unit test stubs `_effect`. Budget live runs into each task rather than bolting them on at the end.

## Global Constraints

- The outcome vocabulary is unchanged: `ready`, `escalated`, `noop`, `failed`, `timeout`, `skipped`. No new values, no renamed ones.
- **No `bircher-status:` string may be written anywhere** when Phase 2 is done — not by the coordinator, not by recovery, not by `--recover-pr`. Reading one is equally gone.
- `--self-test` stays green. **Every guard deleted with the marker has its replacement named in the commit that deletes it.** A deleted test is a coverage change, not a cleanup.
- `run_item`'s kernel lifecycle calls (`_kernel_record_output`, `_kernel_record_ci`, `_kernel_dispatch`, `_kernel_record_review`) keep firing in the same order with the same role changes. Phase 2 changes where their INPUTS come from, not the lifecycle.
- Mutation-prove every guard this plan introduces: commit first, one mutation at a time, prove it applied, restore with `git checkout`, confirm clean. A collection or syntax error is an INVALID mutation, never a survival.
- Scar citations drift when `run-queue.sh` grows. Run `python3 v2/tools/repoint-scar-citations.py` before each commit and read what it reports.

---

## The three decisions this plan makes

Stated up front because each changes behaviour, and a reviewer should be able to reject the decision rather than only the code.

### Decision 1: the review verdict becomes an observation

Today `review=codex:pass` is written into the marker **by the coordinator**, reporting what a reviewer told it. The kernel then records that string as the merge-authorising verdict. The value that decides whether a merge is authorized is model-reported, and nothing observes the review itself.

Phase 2 makes run-queue dispatch the reviewer and read its verdict directly — exactly what `recover_from_ground_truth` already does out-of-band. **This is the largest correctness gain in Phase 2 and the main reason it is worth its risk.**

The cost is real and must be stated: every item now pays for a review dispatch from run-queue rather than getting one free inside the coordinator session, and the coordinator's own in-session fix-loop review no longer feeds the verdict. Wall-clock per item rises by roughly one reviewer run.

### Decision 2: `rounds` changes meaning, so it changes name

`rounds` today is what the coordinator says its fix loop did. Nothing observes it. Derived, the closest honest quantity is **how many distinct commits the branch's CI ran on**, minus one — resubmissions observed from outside.

That is a different measurement, so the scorecard field is renamed `resubmissions` rather than silently redefined. A reader comparing a Phase 1 row to a Phase 2 row must not think they are the same number. `rounds` is emitted as `null` from the first Phase 2 run onward.

### Decision 3: the recovery comment survives, the marker inside it does not

`recover_from_ground_truth` posts a comment carrying the reviewer's findings, with a `bircher-status:` line at the bottom. The findings are genuinely useful to a human reading the PR; the marker line is a machine channel that criterion 1 forbids.

So the comment stays and the marker line goes. The comment becomes documentation, read by nobody in the pipeline.

---

## File Structure

- `batch/lib/observe.sh` — **new.** The observers: `observe_ci_history`, `observe_review`, `observe_outcome`. Extracted so they can be driven by tests without running `run_item`, which is the only reason Phase 1's execution tests could bind anything.
- `batch/run-queue.sh` — `run_item`'s marker branch deleted; `recover_from_ground_truth` reduced to a caller of the observers; `parse_marker`, `_marker_bodies_since` and their self-tests deleted; `json_row` field rename.
- `v2/tests/execution/test_observe_execution.py` — **new.** Drives the extracted functions with stubs.
- `v2/tests/execution/test_marker_is_gone.py` — **new.** The enumerating guard: no `bircher-status:` anywhere in the shipped tree.

---

### Task 1: observe the CI history

**Files:**
- Create: `batch/lib/observe.sh`
- Test: `v2/tests/execution/test_observe_execution.py` (create)

**Interfaces:**
- Produces: `observe_ci_history <branch> -> "<ci_first>|<resubmissions>"` where `ci_first` is `true`/`false`/`unknown` and `resubmissions` is a non-negative integer or empty when unknown.

One API call, not one per commit: `actions/runs?branch=` returns every run on the branch with its head sha and conclusion.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/execution/test_observe_execution.py
"""The observers, EXECUTED with a stubbed `gh`.

Phase 1's three defects were all invisible to unit tests that stubbed the
effect seam. These extract the real shell functions and drive them.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OBSERVE = REPO_ROOT / "batch" / "lib" / "observe.sh"


def _drive(tmp_path, tag, runs_json, call="observe_ci_history feat-x"):
    """Run an observer for real against a stubbed `gh api`."""
    out = tmp_path / f"gh-{tag}.json"
    out.write_text(runs_json)
    script = f"""
set -uo pipefail
REPO=demo/demo
gh() {{ cat {out}; }}
. "{OBSERVE}"
{call}
"""
    f = tmp_path / f"obs-{tag}.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr


def test_one_green_run_is_first_time_green_with_no_resubmissions(tmp_path):
    out, err = _drive(tmp_path, "green",
                      "aaa|success|2026-08-01T10:00:00Z\n")
    assert out == "true|0", (out, err)


def test_the_EARLIEST_run_decides_ci_first_not_the_latest(tmp_path):
    """A branch that went red then green passed on the SECOND try. Reading the
    newest run would call every eventually-green branch first-time-green, which
    is the metric inverted."""
    out, err = _drive(tmp_path, "redgreen",
                      "bbb|success|2026-08-01T12:00:00Z\n"
                      "aaa|failure|2026-08-01T10:00:00Z\n")
    assert out == "false|1", (out, err)


def test_resubmissions_counts_DISTINCT_commits_not_runs(tmp_path):
    """Re-running CI on the same commit is not a resubmission. Counting runs
    would inflate every flaky branch into a fix loop that never happened."""
    out, err = _drive(tmp_path, "rerun",
                      "aaa|failure|2026-08-01T10:00:00Z\n"
                      "aaa|success|2026-08-01T10:30:00Z\n")
    assert out == "false|0", (out, err)


def test_runs_still_in_flight_are_not_read_as_a_verdict(tmp_path):
    """A null conclusion is 'not finished', not 'not success'."""
    out, err = _drive(tmp_path, "pending",
                      "aaa||2026-08-01T10:00:00Z\n")
    assert out == "unknown|", (out, err)


def test_no_runs_at_all_is_unknown_not_false(tmp_path):
    """No CI history is the absence of evidence. Reporting `false` would put a
    claim in the scorecard that nothing observed."""
    out, err = _drive(tmp_path, "none", "")
    assert out == "unknown|", (out, err)


def test_an_api_failure_is_unknown_not_a_silent_zero(tmp_path):
    """`gh` exiting non-zero must not read as 'no runs, so first-time green'."""
    script = f"""
set -uo pipefail
REPO=demo/demo
gh() {{ return 1; }}
. "{OBSERVE}"
observe_ci_history feat-x
"""
    f = tmp_path / "obs-fail.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert r.stdout.strip() == "unknown|", (r.stdout, r.stderr)
```

- [ ] **Step 2: Run and watch them fail** — `no such file or directory: batch/lib/observe.sh`.

Run: `cd v2 && uv run --offline --with pytest --with pyyaml pytest tests/execution/test_observe_execution.py -q`

- [ ] **Step 3: Implement**

Create `batch/lib/observe.sh`:

```bash
#!/usr/bin/env bash
# The observers: what run-queue can see for itself.
#
# Everything here answers a question the `bircher-status:` marker used to
# answer by assertion. The distinction is the whole point of Phase 2 -- a
# marker field is what the coordinator SAID happened, and an observation is
# what the repository SHOWS happened. Where the two disagreed, nothing could
# tell.
#
# Sourced by run-queue.sh. `gh` and `$REPO` resolve at call time, so the tests
# can substitute both.

# observe_ci_history <branch> -> "<ci_first>|<resubmissions>"
#
#   ci_first        true | false | unknown  -- did the FIRST finished CI run
#                                              on this branch succeed?
#   resubmissions   integer | empty         -- distinct commits CI ran on,
#                                              minus one.
#
# ONE call, not one per commit: `actions/runs?branch=` carries every run with
# its head sha and conclusion.
#
# `unknown` is a real answer and is never collapsed into `false`. No CI history
# is the absence of evidence, and a scorecard that records `false` there is
# making a claim nothing observed -- the exact shape this project keeps
# finding.
observe_ci_history() {  # <branch>
  local branch="$1" raw=""
  raw=$(gh api "repos/$REPO/actions/runs?branch=$branch&per_page=100" \
          --jq '.workflow_runs[] | "\(.head_sha)|\(.conclusion // "")|\(.created_at)"' \
          2>/dev/null) || { printf 'unknown|'; return 0; }

  printf '%s\n' "$raw" | awk -F'|' '
    $1 == "" { next }
    $2 == "" { next }               # still running: not a verdict
    { finished[NR] = $1 "|" $2 "|" $3; seen[$1] = 1; n++ }
    END {
      if (n == 0) { printf "unknown|"; exit }
      # earliest by created_at, which is field 3
      first_t = ""; first_c = ""
      for (i in finished) {
        split(finished[i], f, "|")
        if (first_t == "" || f[3] < first_t) { first_t = f[3]; first_c = f[2] }
      }
      d = 0; for (s in seen) d++
      printf "%s|%d", (first_c == "success" ? "true" : "false"), d - 1
    }'
}
```

- [ ] **Step 4: Run the tests** — expect all six PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/lib/observe.sh v2/tests/execution/test_observe_execution.py
git commit -m "feat(observe): derive the CI history run-queue can see for itself"
```

- [ ] **Step 6: Mutate**

| mutation | must red |
|---|---|
| take the LATEST run's conclusion instead of the earliest (`f[3] < first_t` → `f[3] > first_t`) | `test_the_EARLIEST_run_decides_ci_first_not_the_latest` |
| count runs instead of distinct shas (`d - 1` → `n - 1`) | `test_resubmissions_counts_DISTINCT_commits_not_runs` |
| treat a running job as a verdict (delete `$2 == "" { next }`) | `test_runs_still_in_flight_are_not_read_as_a_verdict` |
| `unknown` on API failure → `false\|0` | `test_an_api_failure_is_unknown_not_a_silent_zero` |
| `unknown` on empty history → `true\|0` | `test_no_runs_at_all_is_unknown_not_false` |

State each mutation and its result in the commit message.

---

### Task 2: observe the review

**Files:**
- Modify: `batch/lib/observe.sh`
- Modify: `batch/run-queue.sh` (extract from `recover_from_ground_truth`)
- Test: `v2/tests/execution/test_observe_execution.py` (append)

**Interfaces:**
- Consumes: `_recovery_review_prompt`, `_extract_verdict`, `$RECOVERY_REVIEWER`, `$BUNDLE_DIR`, `$SERVER` — all existing.
- Produces: `observe_review <pr> <reviewed_sha> -> "<verdict>|<log_path>"`, verdict one of `PASS`, `FAIL`, `NONE`.

This is Decision 1. The function already exists inside `recover_from_ground_truth` as inline code; this task lifts it out unchanged in behaviour so both callers share one implementation, and so it can be tested without a live reviewer.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_reviewer_that_says_PASS_is_read_as_PASS(tmp_path):
    log = tmp_path / "rlog"
    script = f"""
set -uo pipefail
REPO=demo/demo
RECOVERY_REVIEWER=codex
BUNDLE_DIR={tmp_path}
SERVER=http://x
BIRCHER_REVIEW_LOG={log}
_recovery_review_prompt() {{ printf 'review pr %s at %s' "$1" "$2"; }}
_extract_verdict() {{ printf '%s' "$(printf '%s' "$1" | grep -oE 'VERDICT: [A-Z]+' | sed 's/VERDICT: //')"; }}
omnigent() {{ echo "findings here"; echo "VERDICT: PASS"; }}
. "{OBSERVE}"
observe_review 42 deadbeef
"""
    f = tmp_path / "rev-pass.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert r.stdout.strip().startswith("PASS|"), (r.stdout, r.stderr)


def test_a_reviewer_that_produces_NO_verdict_is_NONE_not_PASS(tmp_path):
    """A reviewer that crashed, timed out, or rambled has not approved
    anything. Defaulting to PASS here would authorise a merge on silence."""
    log = tmp_path / "rlog2"
    script = f"""
set -uo pipefail
REPO=demo/demo
RECOVERY_REVIEWER=codex
BUNDLE_DIR={tmp_path}
SERVER=http://x
BIRCHER_REVIEW_LOG={log}
_recovery_review_prompt() {{ printf 'p'; }}
_extract_verdict() {{ printf ''; }}
omnigent() {{ echo "I could not complete the review."; }}
. "{OBSERVE}"
observe_review 42 deadbeef
"""
    f = tmp_path / "rev-none.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert r.stdout.strip().startswith("NONE|"), (r.stdout, r.stderr)


def test_a_reviewer_that_EXITS_NONZERO_is_NONE(tmp_path):
    log = tmp_path / "rlog3"
    script = f"""
set -uo pipefail
REPO=demo/demo
RECOVERY_REVIEWER=codex
BUNDLE_DIR={tmp_path}
SERVER=http://x
BIRCHER_REVIEW_LOG={log}
_recovery_review_prompt() {{ printf 'p'; }}
_extract_verdict() {{ printf 'PASS'; }}
omnigent() {{ echo "VERDICT: PASS"; return 1; }}
. "{OBSERVE}"
observe_review 42 deadbeef
"""
    f = tmp_path / "rev-rc1.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert r.stdout.strip().startswith("NONE|"), (
        "a reviewer that died must not have its stdout mined for a verdict: "
        f"{r.stdout}")


def test_the_review_is_dispatched_against_the_SHA_it_was_given(tmp_path):
    """The prompt must carry the sha run-queue observed, not one the reviewer
    re-derives. #66: a reviewer asked to find its own head can bless a
    concurrent push."""
    seen = tmp_path / "seen"
    script = f"""
set -uo pipefail
REPO=demo/demo
RECOVERY_REVIEWER=codex
BUNDLE_DIR={tmp_path}
SERVER=http://x
BIRCHER_REVIEW_LOG={tmp_path}/rlog4
_recovery_review_prompt() {{ printf 'review pr %s at %s' "$1" "$2"; }}
_extract_verdict() {{ printf 'PASS'; }}
omnigent() {{ printf '%s\\n' "$*" > {seen}; echo "VERDICT: PASS"; }}
. "{OBSERVE}"
observe_review 42 cafebabe0000
"""
    f = tmp_path / "rev-sha.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert "cafebabe0000" in seen.read_text(), seen.read_text()
```

- [ ] **Step 2: Run and watch them fail** — `observe_review: command not found`.

- [ ] **Step 3: Implement**

Append to `batch/lib/observe.sh`:

```bash
# observe_review <pr> <reviewed_sha> -> "<verdict>|<log_path>"
#   verdict: PASS | FAIL | NONE
#
# DECISION 1 OF PHASE 2. The verdict that authorises a merge is now read from a
# reviewer run-queue dispatched, not from a string the coordinator wrote about
# a reviewer it dispatched. Both spellings produce `codex:pass`; only one of
# them observed anything.
#
# NONE is not a soft PASS. A reviewer that crashed, timed out, or produced no
# parseable verdict has approved nothing, and `classify_*` must route that to
# `escalated`. Reading silence as approval is how a merge gets authorised by an
# absence.
observe_review() {  # <pr> <reviewed_sha>
  local pr="$1" sha="$2" prompt out rc log
  log="${BIRCHER_REVIEW_LOG:-/tmp/review-$pr.log}"
  prompt=$(_recovery_review_prompt "$pr" "$sha")
  # The sha travels IN the prompt: the reviewer is told what to read, never
  # asked to work it out. See #66.
  ( cd "$BUNDLE_DIR" && omnigent run "agents/$RECOVERY_REVIEWER" \
      --server "$SERVER" -p "$prompt" ) >"$log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    # A dead reviewer's stdout is not evidence. Mining it for "VERDICT: PASS"
    # would let a crash that happened to echo the prompt authorise a merge.
    printf 'NONE|%s' "$log"
    return 0
  fi
  out=$(cat "$log" 2>/dev/null)
  local v; v=$(_extract_verdict "$out")
  case "$v" in
    PASS|FAIL) printf '%s|%s' "$v" "$log" ;;
    *)         printf 'NONE|%s' "$log" ;;
  esac
}
```

Then in `batch/run-queue.sh`, replace the inline review block inside
`recover_from_ground_truth` (the `prompt=`/`rlog=`/`omnigent run`/`verdict=`
lines) with:

```bash
      local _rv; _rv=$(observe_review "$pr" "$reviewed_sha")
      verdict="${_rv%%|*}"
      reviewer_out=$(cat "${_rv#*|}" 2>/dev/null)
      [ "$verdict" = NONE ] && verdict=""
```

`verdict=""` preserves `classify_recovery`'s existing `*)` arm, which already
routes an absent verdict to `escalated`. **Do not change
`classify_recovery` in this task** — its mapping is self-tested and a change
here would be an unrelated behaviour edit riding in a refactor.

Source the new file beside the others, after `effect-adapter.sh`:

```bash
# The observers. Sourced after the effect adapter because `observe_review`
# dispatches a session and must be able to reach `_effect`'s helpers.
# shellcheck source=lib/observe.sh
. "$BUNDLE_DIR/batch/lib/observe.sh"
```

- [ ] **Step 4: Run the tests and the self-test**

Run: the full pytest suite, then `bash batch/run-queue.sh --self-test`.
Expected: green, and **unchanged** — this task is a lift, not a behaviour change.

- [ ] **Step 5: Commit**

```bash
git add batch/lib/observe.sh batch/run-queue.sh v2/tests/execution/test_observe_execution.py
git commit -m "refactor(observe): lift the review dispatch out of recovery"
```

- [ ] **Step 6: Mutate**

| mutation | must red |
|---|---|
| drop the `rc -ne 0` arm so a dead reviewer's stdout is parsed | `test_a_reviewer_that_EXITS_NONZERO_is_NONE` |
| `*) printf 'NONE'` → `*) printf 'PASS'` | `test_a_reviewer_that_produces_NO_verdict_is_NONE_not_PASS` |
| pass `""` instead of `$sha` to `_recovery_review_prompt` | `test_the_review_is_dispatched_against_the_SHA_it_was_given` |

---

### Task 3: one derived outcome, and run_item stops reading a marker

**Files:**
- Modify: `batch/lib/observe.sh`
- Modify: `batch/run-queue.sh` (`run_item`)
- Test: `v2/tests/execution/test_observe_execution.py` (append)

**Interfaces:**
- Consumes: `observe_ci_history` (Task 1), `observe_review` (Task 2), and the existing `classify_recovery`, `_normalize_ci`, `_wait_ci`, `_keep_blocking_checks`, `_required_contexts`.
- Produces: `observe_outcome <item> <code> <pr> <issue> -> "<outcome>|<review>|<note>|<sha>|<ci>|<ci_first>|<resubmissions>"` — the existing five fields plus the two from Task 1.

**This is the task that changes the working path.** Everything before it is additive.

- [ ] **Step 1: Write the failing test**

```python
def test_run_item_no_longer_reads_a_marker(tmp_path):
    """The structural half. `run_item` must not mention the marker at all --
    not parse_marker, not _marker_bodies_since, not the string itself."""
    src = (REPO_ROOT / "batch" / "run-queue.sh").read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("run_item() {"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = "\n".join(src[start:end + 1])
    for banned in ("parse_marker", "_marker_bodies_since", "bircher-status:"):
        assert banned not in body, f"run_item still reads the marker: {banned}"


def test_the_derived_tuple_carries_all_seven_fields(tmp_path):
    """A caller reading five fields where seven are emitted silently absorbs
    the last two into `ci`. Both callers must be updated together."""
    script = f"""
set -uo pipefail
REPO=demo/demo
classify_recovery() {{ printf 'ready|codex:pass|green|derived'; }}
observe_ci_history() {{ printf 'true|2'; }}
observe_review() {{ printf 'PASS|/dev/null'; }}
_pr_is_abandoned() {{ return 1; }}
_reconcile_item_pr() {{ printf ''; }}
_normalize_ci() {{ printf 'green'; }}
_keep_blocking_checks() {{ printf ''; }}
_required_contexts() {{ printf ''; }}
_discover_pr_by_issue() {{ printf ''; }}
gh() {{ case "$*" in *head.sha*) printf '%040d' 7 ;; *) printf '' ;; esac; }}
. "{OBSERVE}"
observe_outcome item-1 code1 42 ""
"""
    f = tmp_path / "outcome.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    fields = r.stdout.strip().split("|")
    assert len(fields) == 7, (fields, r.stderr)
    assert fields[0] == "ready" and fields[5] == "true" and fields[6] == "2"
```

- [ ] **Step 2: Run and watch them fail** — the first because `run_item` still greps for the marker, the second because `observe_outcome` does not exist.

- [ ] **Step 3: Implement `observe_outcome`**

Move the body of `recover_from_ground_truth` into `observe_outcome` in
`batch/lib/observe.sh`, with three changes and no others:

1. call `observe_ci_history "$branch"` and append its two fields to the output;
2. drop the `bircher-status:` line from the comment it posts (Decision 3), keeping the reviewer's findings as prose;
3. emit seven fields instead of five.

Leave `recover_from_ground_truth` in `run-queue.sh` as a thin wrapper that
calls `observe_outcome` and prints the first five fields, so `--recover-pr`
keeps working unchanged while Task 4 removes the marker vocabulary from it.

The comment body becomes:

```bash
    if [ -n "$reviewer_out" ]; then
      body="Cross-vendor review (outcome derived from the repository, not reported):

$reviewer_out

outcome=$r_outcome ci=$r_ci review=$r_review${_head_field}
note: $r_note"
    else
      body="Outcome derived from the repository: outcome=$r_outcome ci=$r_ci${_head_field}
note: $r_note"
    fi
```

**Note what changed and what did not.** The prose still states the outcome, so a
human reading the PR learns the same facts. What is gone is the parseable
`bircher-status:` prefix — nothing reads this text, and Task 5's guard proves
it.

- [ ] **Step 4: Rewrite `run_item`'s decision block**

Replace the whole `if [ -n "$marker" ] … else … fi` block (currently around
`run-queue.sh:3858`) with the unconditional derivation:

```bash
  local outcome ci_first review rounds note resubmissions observed_head _obs_ci
  if [ "${_blind:-0}" = 1 ]; then
    # Unchanged from the marker era, and still correct: the cancel was never
    # confirmed, so the coordinator may still be running. Deriving an outcome
    # means READING and WRITING the PR, which races a live session.
    outcome="escalated"; review="na"; ci_first="unknown"; resubmissions=""; observed_head=""
    note="server unreachable at cap; could not confirm the session stopped, so outcome derivation was skipped to avoid racing a live coordinator - needs a human"
    echo "[batch] $item: blind at teardown -> escalating without derivation" >&2
  else
    echo "[batch] $item: deriving outcome from the repository" >&2
    local obs
    obs=$(observe_outcome "$item" "$code" "$pr" "$_iss")
    # An EMPTY tuple is a CRASH, not a verdict -- the same reasoning that
    # guarded the recovery path, and it applies to EVERY item now that this is
    # the only path. `obs=$(...)` swallows a mid-function death into an empty
    # string, which parses as outcome="" and reports "NOT ready": a crash
    # wearing a verdict's clothes.
    if [ -z "${obs//[[:space:]]/}" ]; then
      echo "[batch] $item: derivation produced NO tuple -> it failed; escalating rather than reading it as a verdict" >&2
      obs="escalated|na|outcome derivation failed (no tuple); needs a human||na|unknown|"
    fi
    IFS='|' read -r outcome review note observed_head _obs_ci ci_first resubmissions <<EOF
$obs
EOF
    : "${outcome:=timeout}" "${ci_first:=unknown}"

    # The kernel lifecycle, unchanged in order and roles. Its INPUTS are now
    # observations; the sequence is the same one the marker branch drove.
    if [ -n "${observed_head:-}" ]; then
      local _body="derived: outcome=$outcome review=$review head=$observed_head note=$note"
      _out_hash=$(_kernel_record_output "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_body")
      _kernel_record_ci "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "${_obs_ci:-na}" "$observed_head"
      BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer)
      export BIRCHER_GENERATION
      _kernel_record_review "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$review" \
        "$_out_hash" "$_base_sha" "$_spec_hash"
      BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
      export BIRCHER_GENERATION
    fi
  fi
  rounds=""   # Decision 2: no longer reported; `resubmissions` replaces it.
```

`marker_head` is renamed `observed_head` throughout: the value is the same
40-hex sha, but it no longer arrives in a marker and the old name would be the
last thing in `run_item` still describing a channel that does not exist. Grep
for `marker_head` after this step and expect no hits inside `run_item`.

Then delete from the poll loop the two marker checks at `run-queue.sh:3720`
and `:3727`, and the final marker re-check at `:3846`. The loop's remaining
exits — a `.noop` file, a `.escalated` file, a dead session, and the cap —
are unchanged.

**The poll loop now has no completion signal from the coordinator at all.** It
runs to one of those four exits. Say so in a comment where the marker check
used to sit, because a reader will otherwise look for the fast path and
conclude it was lost by accident:

```bash
    # NO MARKER, BY DESIGN (Phase 2). A v2 implementer has no comment
    # authority, so there is nothing it could post here. The loop ends on a
    # noop/escalated signal, a dead session, or the cap -- and the outcome is
    # then DERIVED. The cost is real: an item that finishes early no longer
    # says so, and waits for its session to die or its cap to expire.
```

- [ ] **Step 5: Update the scorecard row**

In `json_row`, rename the field (Decision 2):

```python
 "rounds": None,
 "resubmissions": int(a[5]) if a[5].isdigit() else None,
```

and update both call sites to pass `$resubmissions` where they passed
`$rounds`.

- [ ] **Step 6: Run everything**

Run: the full pytest suite, then `bash batch/run-queue.sh --self-test`.
Expected: pytest green. **The self-test will FAIL** on the `parse_marker` and
`_marker_bodies_since` blocks — that is Task 4's work, and it is correct for it
to fail here. Do not delete those blocks in this task.

- [ ] **Step 7: Commit**

```bash
git add batch/lib/observe.sh batch/run-queue.sh v2/tests/execution/test_observe_execution.py
git commit -m "feat(run_item): derive the outcome instead of reading a marker"
```

- [ ] **Step 8: Mutate**

| mutation | must red |
|---|---|
| emit six fields from `observe_outcome` instead of seven | `test_the_derived_tuple_carries_all_seven_fields` |
| restore the `if _contains "$body" 'bircher-status:'` check in the poll loop | `test_run_item_no_longer_reads_a_marker` |
| delete the empty-tuple guard so `obs=""` parses as a verdict | write the test if none reds: an empty derivation must give `escalated`, never `""` |

The third row is deliberately open. **If no existing test reds, that guard is
unbound and the task is not done** — write the test that binds it before
committing.

---

### Task 4: delete the marker vocabulary and name every replacement

**Files:**
- Modify: `batch/run-queue.sh` (delete `parse_marker`, `_marker_bodies_since`, their self-tests, and the marker line in `_post_cross_review_status` if present)
- Create: `v2/tests/execution/test_marker_is_gone.py`

**Interfaces:**
- Removes: `parse_marker`, `_marker_bodies_since`.

- [ ] **Step 1: Write the enumerating guard**

```python
# v2/tests/execution/test_marker_is_gone.py
"""The marker is retired, and this is what keeps it retired.

An enumerating test, not N per-site tests: it fails when someone adds site
N+1. The predecessor of this shape caught two new `_effect` call sites that
would otherwise have joined silently.
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Files that may still SAY "bircher-status" -- records and specs describe the
#: history. Code and tests may not.
_PROSE = {"docs", "README.md"}


def _shipped_files():
    for p in sorted(REPO_ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO_ROOT)
        if rel.parts[0] in {".git", ".superpowers"} or rel.parts[0] in _PROSE:
            continue
        if p.suffix in {".sh", ".py", ".yaml", ".yml"}:
            yield rel, p


def test_no_shipped_file_writes_or_reads_the_marker():
    offenders = [str(rel) for rel, p in _shipped_files()
                 if "bircher-status" in p.read_text(errors="ignore")]
    assert not offenders, (
        "the marker is retired; these still mention it: " + ", ".join(offenders))


def test_parse_marker_is_gone():
    src = (REPO_ROOT / "batch" / "run-queue.sh").read_text()
    assert "parse_marker" not in src
    assert "_marker_bodies_since" not in src
```

- [ ] **Step 2: Run and watch it fail** — listing every file that still mentions the marker.

- [ ] **Step 3: Delete, and name each replacement**

Delete `parse_marker` (`run-queue.sh:141`), `_marker_bodies_since`
(`run-queue.sh:185`), and their self-test blocks (`:4080-4095` and
`:4105-4120`). Also delete the `marker_line=` construction inside
`observe_outcome` if Task 3 left one.

Write this table into the commit message. **A row without a named replacement
means the guard is being dropped, not replaced, and that needs saying out
loud:**

| deleted guard | what it protected | replacement |
|---|---|---|
| `parse_marker` pre-#24 no-`head=` case | a marker without a reviewed head yields empty, caller fails closed | `observe_outcome` captures the head itself via `gh api .head.sha`; `test_the_review_is_dispatched_against_the_SHA_it_was_given` |
| `parse_marker` literal-`\n` case (EXP02) | a marker mid-line still parses | **none needed** — nothing parses comment text any more |
| `parse_marker` malformed `head=` → empty | a bad sha never reaches `merge_ready_pr` | the 40-hex `case` guard already in `observe_outcome`, which Task 3 moved unchanged |
| `_marker_bodies_since` freshness (#47) | a stale marker from an earlier run is not adopted | **none needed** — there is no marker to adopt; freshness came from reading comments |
| self-test `parse_marker head= OK (#24)` | the above, executed | `test_observe_execution.py` drives `observe_review` and `observe_outcome` |

- [ ] **Step 4: Run everything** — full pytest suite, `--self-test`, both green.

- [ ] **Step 5: Commit** with the table above in the body.

---

### Task 5: make the denied push legible

**Files:** determined by Step 1 — see below.

Independent of Tasks 1-4 and reorderable. Recorded in the Phase 1 acceptance
section: the implementer's `git push` does not fail, it **hangs** — measured at
over ten minutes before cancellation. The bundle's prompt tells the implementer
that a failure is the boundary working, and no failure ever arrives.

> **STATUS 2026-08-29: premise confirmed, mechanism corrected.** The boundary
> DOES enforce — a real session cannot reach a host outside its allow-list
> (`curl https://example.com` → `000`) — but it enforces via an authenticated
> local relay injected as `HTTP_PROXY` by the parent process, NOT via Landlock,
> which matches TCP by port and cannot see a host or a path. So a denied push
> is refused by the relay, and the candidate table below is wrong about where
> to look. Before implementing, OBSERVE THE DENIAL'S ACTUAL SHAPE: a relay
> refusal may already return an error, in which case there is no stall to bound
> and this task should be deleted rather than done. See the record's second
> correction.

**This task begins as a spike, because the obvious fix does not exist.**
`run_item` never creates the implementer's worktree — `run-queue.sh:3579` is
PROMPT TEXT instructing the session to create its own. There is no
run-queue-side worktree to configure, so any plan step of the form
`git -C "$wt" config …` is unimplementable. Establish the mechanism by
measurement before writing the fix.

- [ ] **Step 1: Spike — find a mechanism that actually bounds the stall**

On `bircher-smoke`, in a clone under the v2_implementer bundle, time
`git push origin <branch>` under each candidate. Record elapsed seconds for
each; a candidate that does not bound the stall is eliminated, not adjusted.

| candidate | where it lives | unknown to settle |
|---|---|---|
| `GIT_HTTP_LOW_SPEED_LIMIT` / `GIT_HTTP_LOW_SPEED_TIME` env vars | the bundle's `os_env`, if omnigent supports an env block | **no existing bundle sets env** — support is unverified. Check the omnigent bundle schema before assuming it. |
| `git config --global http.lowSpeedLimit/Time` in the runner image | homelab, not bircher | crosses a repo boundary; needs the operator's agreement |
| the instruction added to the worktree-creation prompt at `run-queue.sh:3579` | bircher | model-dependent — but the worktree creation it joins is already model-dependent, so it adds no new class of failure |

`lowSpeed` rather than a connect timeout, for all three: the connection
SUCCEEDS and then stalls, so there is no connect failure to catch. No bytes
move, so the low-speed bound is the one that can fire. **If the measurement
shows the stall happens before any HTTP exchange begins, lowSpeed will not fire
either and all three candidates are wrong** — say so and stop rather than
picking the least-bad one.

- [ ] **Step 2: Report the spike's result and pick**

Write the measured seconds per candidate into the task's report. The winner is
the one that bounded the stall, not the one that was tidiest.

- [ ] **Step 3: Implement the winner**, with a comment stating the measured
      bound and the date it was measured.

- [ ] **Step 4: Re-measure after implementing**

Run a fresh session under the bundle and time its push. **If the push still
hangs past the bound, the mechanism does not work and the task is not done.**
Say so and stop rather than shipping a comment claiming a bound it does not
deliver. The Phase 1 record already contains one fix built on a hypothesis its
own measurement could not have refuted; do not add a second.

- [ ] **Step 5: Commit**, stating the measured seconds before and after.

**No unit test is prescribed for this task**, deliberately. A test asserting
`"http.lowSpeedLimit" in source` asserts TEXT, not behaviour — it passes when
the setting is present and inert, which is the exact failure mode in question.
The measurement in Step 4 is the evidence. If the winning mechanism turns out
to have a behavioural seam worth binding, write the test then.

---

### Task 6: a full item, end to end, with no marker anywhere

**Files:**
- Modify: `docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md` (append a C8 Phase 2 section)

Operational, on `abedegno/bircher-smoke`. **Must not run against
`abedegno/muesli`.**

- [ ] **Step 1: Deploy** the branch to `/workspaces/bircher-v2` and confirm the
      deployed sha.

- [ ] **Step 2: Queue one trivial item** and run the queue end to end under
      `BIRCHER_EFFECT_MODE=kernel BIRCHER_KERNEL_MODE=enforce`, with
      `BIRCHER_REPO=abedegno/bircher-smoke` — **`BIRCHER_REPO`, not `REPO`**;
      `run-queue.sh` re-derives `REPO` from it, and exporting `REPO` alone is
      what made Phase 1 push to one repository and open the PR on another.

- [ ] **Step 3: Assert criterion 1** — the item merges, and
      `gh pr view <pr> --json comments` contains no `bircher-status:` anywhere.

- [ ] **Step 4: Write the field mapping (criterion 2)**

Every scorecard field, and the observation behind it. A field with no
observation is recorded as `null` with the reason, not filled in:

| field | observation |
|---|---|
| `outcome` | `classify_recovery(pr, ci, verdict)` |
| `review` | `observe_review` — a reviewer run-queue dispatched |
| `ci_pass_first_try` | earliest finished workflow run's conclusion |
| `resubmissions` | distinct head shas CI ran on, minus one |
| `rounds` | **null** — no observation exists; see Decision 2 |
| `pr`, `wall_seconds`, `bound`, `implementer` | already observed, unchanged |
| `cost` | **null** — unchanged, never populated |

- [ ] **Step 5: Assert criterion 3** — `--self-test` green, and the Task 4
      replacement table reproduced in the record.

- [ ] **Step 6: Read the journal and the shadow report**, and state the
      contents whatever they are. Unlike Phase 1, this path DOES issue kernel
      commands, so `command_accepted` facts are the positive evidence that
      criterion 5 could not have in Phase 1.

- [ ] **Step 7: Commit the record.**

---

## Done means

`run_item` derives every outcome field from the repository; `parse_marker` and
`_marker_bodies_since` no longer exist; no shipped file writes or reads
`bircher-status:`, enforced by a test that fails when someone adds one back. A
full item runs end to end on the throwaway repo and merges with no marker
anywhere. Every scorecard field is traceable to an observation or recorded as
`null` with its reason. `--self-test` is green and every guard removed with the
marker has its replacement named — or is explicitly recorded as dropped.

**The risk this plan does not remove:** Phase 2 rewrites the only pipeline that
currently works. Sequencing contains it — a failure rolls back to a Phase 1
that works — but does not eliminate it. Three of Phase 1's defects were
invisible to a green unit suite and appeared only under a live run, and Phase 2
touches far more of the working path than Phase 1 did. Task 6 is not a
formality.

**Also not delivered:** the effect-mode deployment default is still `deny` and
still an operational decision nobody has made. Criterion 4 of the record-mode
spec stands as failed by design, and C8 deepens it: with the kernel publishing,
a broken kernel means no PR at all.
