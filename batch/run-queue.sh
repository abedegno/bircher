#!/usr/bin/env bash
# Bircher batch runner: work queue/*.md one at a time through a
# fresh Bircher session each, derive completion from the repository
# marker, append a scorecard row, then move on. Sequential by design (M4).
set -uo pipefail

# _derive_bundle_dir <script-path> -> the bundle root (the checkout containing batch/,
# skills/, agents/, config.yaml): the parent of the script's dir. Works flattened
# (/workspaces/bircher/batch/run-queue.sh -> /workspaces/bircher) and nested
# (.../agents/bircher/batch/run-queue.sh -> .../agents/bircher). PURE-ish (needs the dir to exist).
_derive_bundle_dir() { ( cd "$(dirname "$1")/.." && pwd ); }

REPO="${BIRCHER_REPO:-abedegno/muesli}"
WORKDIR="${WORKDIR:-/workspaces/muesli}"                        # the WORK repo (target app)
BUNDLE_DIR="${BIRCHER_BUNDLE_DIR:-$(_derive_bundle_dir "${BASH_SOURCE[0]}")}"  # the bircher checkout
# The effect adapter: the single seam every externally visible mutation passes
# through. Sourced before anything can call `_effect`; the functions it calls
# (`_net_run`) resolve at call time, so the order of definition does not bind.
# shellcheck source=lib/effect-adapter.sh
. "$BUNDLE_DIR/batch/lib/effect-adapter.sh"
# The observers: what run-queue can see for itself, replacing what the
# coordinator used to assert in its `bircher-status:` marker.
# shellcheck source=lib/observe.sh
. "$BUNDLE_DIR/batch/lib/observe.sh"
# The kernel client: the coordinator's advisory interface to the v2 kernel.
# Sourced after the effect adapter and before anything can call `_kernel` /
# `_kernel_dispatch`. Every call it makes is advisory -- see the file's own
# header -- so sourcing it here changes nothing about what a run does.
# shellcheck source=lib/kernel-client.sh
. "$BUNDLE_DIR/batch/lib/kernel-client.sh"

QUEUE="${QUEUE:-$BUNDLE_DIR/queue}"
PROCESSED="$QUEUE/processed"
SCORECARD="${SCORECARD:-$BUNDLE_DIR/.run/scorecard.jsonl}"
DEFERRED_READY_FILE="${DEFERRED_READY_FILE:-$BUNDLE_DIR/.run/deferred-ready.tsv}"
# No-op signal dir: the coordinator drops <code>.noop here when an item is
# already satisfied (no product change needed) so the runner records a `noop`
# and advances instantly instead of polling out the full ITEM_TIMEOUT (gap #3).
NOOP_DIR="${BIRCHER_NOOP_DIR:-/workspaces/.bircher-noop}"
SERVER="${OMNIGENT_SERVER:-http://omnigent:8000}"
# Safety cap (NOT the primary done-signal): completion is detected from the PR
# marker or a dead server session (see run_item). 90 min lets a legitimately
# long coordinator (multi-round in-run fix-loop) finish; the omnigent 0.4
# reaper no longer caps sessions at ~30 min.
ITEM_TIMEOUT="${ITEM_TIMEOUT:-5400}"
POLL="${POLL_INTERVAL:-45}"
# Layer-2 recovery: vendor for the out-of-band review when a coordinator dies
# before posting its marker. Default codex = opposite the standing claude_code
# implementer (cross-vendor). Override for a codex-implemented item.
RECOVERY_REVIEWER="${BIRCHER_RECOVERY_REVIEWER:-codex}"
# B-1 in-run merge: when an item completes outcome=ready (CI green + independent
# cross-vendor pass - the same gate the human applied mechanically), merge its
# PR BEFORE launching the next item, so every later item builds on merged
# siblings and the merge-order conflict class (run #15: 3 reviewed-ready PRs
# lost to serial admin-nav conflicts) disappears. Opt out with =0.
INRUN_MERGE="${BIRCHER_INRUN_MERGE:-1}"
# How long to watch MAIN's CI on each merge commit before halting conservatively.
#
# #62: this used to be ONE constant for both the initial watch and every re-run poll,
# at 900s. That is below what main CI actually takes here: muesli's merge commit
# a80a55b2 (PR #696) ran CI for 2867s, because the same merge spawned 18 Dependabot
# update jobs and starved the queue. The watcher timed out mid-run with the state still
# `pending`, the re-run produced no verdict, and `_main_ci_verdict pending unknown`
# halted the wave with main perfectly healthy. Every other main commit in that window
# took 265-374s, so a flat 900s looks generous right up until it isn't.
#
# #62 was filed against a different cause -- "a path-filtered change registers no CI" --
# which the evidence does not support: that commit carries 43 check-runs with all six
# main-applicable required contexts green. The halt was the timeout, not a missing
# classification.
#
# Split rather than raised, because these two budgets are consumed differently:
# _rerun_main_ci_until_green spends MAIN_CI_TIMEOUT up to three times, so raising the
# shared constant to cover a slow first watch would have turned a documented ~71-minute
# worst case into ~4 hours, leaving a genuinely red main unreverted for that long.
MAIN_CI_SETTLE_TIMEOUT="${BIRCHER_MAIN_CI_SETTLE_TIMEOUT:-3600}"   # the INITIAL watch
MAIN_CI_TIMEOUT="${BIRCHER_MAIN_CI_TIMEOUT:-900}"                  # each RE-RUN poll
# One wall-clock bound over the whole watch-plus-re-runs sequence, so no combination of
# the two budgets above can exceed it. Stored as an absolute epoch instant rather than a
# per-loop elapsed counter: each loop lives in its own function and would otherwise
# restart its own count, which is how the two budgets multiplied in the first place.
MAIN_CI_ABSOLUTE_DEADLINE="${BIRCHER_MAIN_CI_ABSOLUTE_DEADLINE:-7200}"
# How often the CI poll loops look. Injectable so the self-test does not pay 30s per
# watched merge: splitting the budgets left MAIN_CI_TIMEOUT controlling only the RERUN
# loop, so a test setting it low no longer bounds the initial watch at all.
MAIN_CI_POLL_INTERVAL="${BIRCHER_MAIN_CI_POLL_INTERVAL:-30}"
# B-3 vendor allocation: which vendor implements each item.
#   auto (default) = usage-aware selection that balances the two subscriptions'
#   WEEKLY windows (pick the lower used_percent) and rides out 5h-window
#   exhaustion by waiting for the sooner reset; claude_code | codex = pinned.
# A per-item queue-file tag `bircher-implementer: <vendor>` overrides everything.
# auto is the DEFAULT: the codex-as-implementer quality pilot passed (2026-07-08,
# EXP01 PR #246 -- ci_first, rounds=1, zero blocking findings). The cross-vendor
# reviewer is always the opposite vendor and CI gates every PR, so a weaker
# implementation is caught regardless of vendor. Pin with
# BIRCHER_IMPLEMENTER=claude_code to force a single-vendor run.
IMPLEMENTER="${BIRCHER_IMPLEMENTER:-auto}"
# 5h-window utilization (%) above which a vendor is excluded from selection.
FIVEH_MAX="${BIRCHER_5H_MAX:-92}"
# Claude usage: read live from Claude Code's OWN statusLine, harvested by a
# short PTY probe (claude-usage-probe.py). It runs the genuine `claude` binary
# interactively with a one-shot --settings statusLine override + one trivial
# turn, then reads the authoritative account-wide rate_limits.five_hour/seven_day
# {used_percentage, resets_at} Claude feeds its own statusLine hook -- the exact
# data `/usage` shows, reflecting ALL consumption sources. This is ToS-clean (it
# is Claude Code running + reporting its own usage; NO OAuth-token reuse and NO
# scope-gated endpoint) and works with the runner's inference-only setup-token
# (rate_limits come from the inference response, not the user:profile endpoint).
# Verified live on macOS + the NAS runner 2026-07-08. Probing costs one tiny
# claude turn, so we cache the tuple briefly and reuse. codex usage is read from
# the newest ~/.codex rollout.
CLAUDE_USAGE_CACHE="${BIRCHER_CLAUDE_USAGE_CACHE:-/tmp/claude-usage.tuple}"
CLAUDE_USAGE_TTL="${BIRCHER_CLAUDE_USAGE_TTL:-150}"
CLAUDE_USAGE_PROBE_TIMEOUT="${BIRCHER_CLAUDE_USAGE_PROBE_TIMEOUT:-55}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
CLAUDE_USAGE_PROBE="${BIRCHER_CLAUDE_USAGE_PROBE:-$SELF_DIR/claude-usage-probe.py}"
CODEX_SESSIONS_DIR="${BIRCHER_CODEX_SESSIONS_DIR:-/root/.codex/sessions}"
export PATH="/root/bin:$PATH"

# -- Dedicated bircher runner (Phase B2) ----------------------------------------
# Isolation mechanism: this script runs INSIDE the omnigent-runner-bircher
# container.  When 'omnigent run' is exec'd here it auto-binds the session to
# the co-located runner -- verified live: session launched in the bircher
# container bound to runner_token_1c7bae19.../host_83b59621..., not no2's.
#
# OMNIGENT_RUNNER is exported so any transitive omnigent.sh calls made by child
# processes also target this container (belt-and-suspenders).  No runner_id PATCH
# is needed or possible: the real runner id is a per-connection runner_token_<hex>
# that is not known in advance and changes each connection.
export OMNIGENT_RUNNER="${OMNIGENT_RUNNER:-omnigent-runner-bircher}"
# -------------------------------------------------------------------------------

# _branch_code_filter <code> -> a jq filter selecting PRs whose head branch carries
# the item code on a token boundary. Single source of truth for a regex that was
# copy-pasted at three call sites (poll-loop discovery, observe_outcome,
# _reconcile_item_pr) — issue #22.
#
# The boundary anchors matter: a bare substring test makes `i23` match branch
# `i230-...`, so an item would adopt its neighbour's PR. Keep them.
_branch_code_filter() {
  printf '[.[] | select(.headRefName | ascii_downcase | test("(^|[^a-z0-9])%s([^a-z0-9]|$)"))]' "$1"
}



# _extract_verdict <text> -> "PASS" | "FAIL" | "" (empty = NO USABLE VERDICT).
#
# #65: this used to take the LAST MATCHING TOKEN anywhere in the prose, so a
# closing remark like "had the microphone not been stopped I would have returned
# VERDICT: FAIL" silently flipped the recorded outcome -- and this single string
# decides whether a PR auto-merges.
#
# The fix anchors on the CONTRACT the reviewer is given: the verdict is the FINAL
# LINE. So parse only the last non-empty line and require it to be exactly a
# verdict. That is strictly better than counting tokens (the first attempt here):
# a reviewer quoting "VERDICT: FAIL" while explaining its findings is no longer
# punished with a malformed review, while a trailing restatement still fails
# closed because the last line is then not a bare verdict.
#
# Anything else yields empty, which every caller already treats as "no verdict"
# and escalates. Fail closed, like the rest of this file.
_extract_verdict() {
  # `PASS`, `FAIL`, or EMPTY. Reads the LAST non-blank line and requires a BARE
  # verdict: one mentioned mid-report is not a verdict. Markdown decoration and
  # a single trailing `.`/`!` are tolerated; `...` is prose. The trimming loop,
  # its bound, and the warning on a non-verdict final line are in
  # v2/coordinator/review.py and cli.py.
  _coordinator verdict --text "$1" || printf ''
}

# _normalize_ci <newline-separated gh check buckets> -> green|red|pending
# `gh pr checks --json bucket` emits one bucket per check:
#   pass | fail | pending | skipping | cancel
# Precedence: any fail/cancel -> red; else any pending -> pending; else green.
# No checks at all (empty) -> pending: CI has not registered yet, do not review.
_normalize_ci() {
  # `green` | `red` | `pending`. The rule lives in v2/coordinator/ci.py.
  # EMPTY IS PENDING, not green: no checks reported yet is the absence of a
  # verdict, and reading it as success merges a PR whose CI never started.
  local out=""
  # PIPED, not passed: a CI list has no upper bound and an oversized argv
  # element fails `execve` with "Argument list too long" -- which this
  # function would then read as `pending`, and `_wait_ci` loops on pending.
  out=$(printf '%s' "$1" | _coordinator ci-normalize --buckets -) || out=""
  # A failed call must not read as green. `pending` keeps the caller waiting,
  # which is the survivable answer -- but note `_wait_ci` loops on pending, so
  # a PERMANENTLY failing call hangs rather than errors. That trade is why the
  # package must stay reachable, and why `_coordinator` no longer hides its
  # stderr.
  [ -n "$out" ] || out=pending
  printf '%s' "$out"
}

# classify_recovery <pr> <ci_state> <verdict> -> "outcome|review|ci|note"
# Pure mapping from ground truth to a scorecard row. Maps ONLY onto the existing
# outcome vocabulary; the note carries the detail. Reads the global
# RECOVERY_REVIEWER for the review-vendor label.
classify_recovery() {
  # Ground truth to outcome. The mapping lives in v2/coordinator/observe.py,
  # where it is pure and directly testable; this is the shell's call into it.
  #
  # Empty output would parse as outcome="" and read as "NOT ready" -- a crash
  # wearing a verdict's clothes, which this project has shipped three times. So
  # a failed call escalates loudly instead.
  local out=""
  out=$(_coordinator classify --pr "$1" --ci "$2" --verdict "$3" \
          --reviewer "$RECOVERY_REVIEWER") || out=""
  if [ -z "$out" ]; then
    out="escalated|na|$2|outcome classification failed (no output); needs a human"
  fi
  printf '%s' "$out"
}

# _checkrun_state <lines of "status|conclusion"> -> green|red|pending
# Classifies GitHub check-runs on a commit (gh api .../check-runs). PENDING when no
# check-runs have registered yet (empty input - never treat silence as green).
#
# GREEN IS AN ALLOWLIST, and deliberately so. This was two denylists -- red for a
# list of failing conclusions, pending for `queued|in_progress`, and GREEN for
# everything else -- so every value on neither list read as a pass. GitHub's own
# OpenAPI description gives the check-run `status` enum as
#   queued, in_progress, completed, waiting, requested, pending
# and the last three were all landing on the green default. `waiting` and `requested`
# are what a deployment-gated or Actions-requested check run reports, so a required
# check sitting behind an approval gate read as though it had passed. A bare
# `completed|` with no conclusion read green too.
#
# So: green requires POSITIVE evidence of a pass -- completed, with one of the three
# conclusions GitHub defines as non-failing. Anything not yet completed is pending,
# including status values GitHub has not invented yet. Anything completed without a
# passing conclusion is red, likewise including future values: a spurious red costs a
# revert, which this pipeline can undo (_reopen_reverted_issues), while a spurious
# green ships broken code, which is the entire subject of #67.
_checkrun_state() {
  local lines="$1" line st cc saw_pending=0
  [ -z "${lines//[[:space:]]/}" ] && { echo pending; return; }
  while IFS= read -r line; do
    [ -n "${line//[[:space:]]/}" ] || continue
    st="${line%%|*}"; cc="${line#*|}"
    case "$st" in
      completed)
        case "$cc" in
          success|neutral|skipped) ;;   # the only non-failing conclusions GitHub defines
          *) echo red; return ;;        # failure/cancelled/timed_out/action_required/
        esac ;;                         # stale/"" and anything added later
      *) saw_pending=1 ;;               # queued/in_progress/waiting/requested/pending/...
    esac
  done <<EOF
$lines
EOF
  [ "$saw_pending" = 1 ] && { echo pending; return; }
  echo green
}

# _classify_ci_failure <failed_step_count> -> infra|genuine   (PURE, self-tested)
# A red CI run whose failed/cancelled jobs produced ZERO failed STEPS never
# actually ran the tests -- transient GitHub infra ("job not acquired by Runner",
# startup_failure, or a fail-fast cancellation with no real failure). A genuine
# test failure always leaves at least one failed step. (B-5, 2026-07-09: PIN01
# #264 showed all jobs red at 15m01s = runner-acquisition timeout; a plain re-run
# went green.) Unknown/empty count -> genuine, so we never loop re-runs blindly.
_classify_ci_failure() {
  [ "${1:-0}" -gt 0 ] 2>/dev/null && echo genuine || echo infra
}

# _reopen_reverted_issues <pr> -> reopens every issue the merged PR closed
#
# A revert restores the code but not the tracker. Without this, `Closes #N` leaves
# #N closed while the defect is live again -- and anything `blocked_by` #N silently
# becomes runnable. On 2026-08-12 that chain came within one wave of shipping a
# broken app: a transient outage reverted a fix, its issue stayed closed, and the
# dependent issue it was gating unblocked itself.
#
# A FAILED lookup must never read as "nothing to reopen": that is the exact silence
# this exists to remove, so the two are reported differently.
_reopen_reverted_issues() {
	local pr="$1" nums n rc
	[ -n "$pr" ] || return 0
	nums=$(_net_run "$BIRCHER_NET_TIMEOUT" gh pr view "$pr" --repo "$REPO" --json closingIssuesReferences \
		-q '.closingIssuesReferences[]?.number' 2>/dev/null); rc=$?
	if [ "$rc" -ne 0 ]; then
		echo "[batch:merge] WARN revert: could NOT look up the issues PR #$pr closed (gh rc=$rc) - any are still marked fixed; check by hand" >&2
		return 0
	fi
	if [ -z "${nums//[[:space:]]/}" ]; then
		echo "[batch:merge] revert: PR #$pr closed no issues (nothing to reopen)" >&2
		return 0
	fi
	while IFS= read -r n; do
		[ -n "$n" ] || continue
		if _effect issue_or_label "reopen:$n" "$BIRCHER_NET_TIMEOUT" gh issue reopen "$n" --repo "$REPO" \
			--comment "Reopening: the fix for this was merged in #$pr and then automatically reverted because main CI went red. The defect is live again." >/dev/null 2>&1; then
			echo "[batch:merge] revert: reopened issue #$n (its fix was reverted)" >&2
		else
			echo "[batch:merge] WARN revert: could NOT reopen issue #$n - it still reads as fixed; reopen by hand" >&2
		fi
	done <<EOF
$nums
EOF
}

# _run_ids_from_check_links <lines> -> unique workflow-run IDs, one per line. (PURE)
# Input lines are "name|link" as emitted by `gh pr checks --json name,link`.
#
# #41 (2026-08-08): this REPLACED `gh run list --branch <ref> --limit 1`, which took
# the branch's most recent run across ALL workflows. Once muesli gained a second
# workflow (`Review Gate`, added 2026-08-07) the two were created in the same second
# and the ordering between them is not guaranteed. When `Review Gate` came back:
#   - _ci_failure_kind counted failed steps in a workflow that had SUCCEEDED -> zero
#     -> a genuine red was reported as `infra`;
#   - _rerun_and_wait_ci re-ran that same wrong workflow, so the failing one never
#     re-ran and the recovery could not succeed.
# Seen on muesli PR #560, then again live on #568.
#
# Check LINKS are the right key: they name the run that actually produced each check,
# so no workflow-name list is needed (and cannot drift). Commit STATUSES — `review-gate`
# itself among them — carry an empty link and drop out here for free.
#
# Non-CI check runs are removed by _drop_non_ci_checkruns, the same filter and the same
# BIRCHER_CI_IGNORE_CHECKS override used everywhere else, so there is one list, not two.
_run_ids_from_check_links() {
  _drop_non_ci_checkruns "$1" \
    | sed -n 's#.*/actions/runs/\([0-9][0-9]*\)\(/.*\)\{0,1\}$#\1#p' \
    | sort -u
}

# _ci_run_ids <pr> -> the workflow-run IDs backing this PR's CI check runs.
# NOTE on the exit code: `gh pr checks` exits 1 when checks are FAILING, but only in
# its human-readable mode. With --json it exits 0 and still emits every row (verified
# 2026-08-08 against muesli PR #542, 5 failing checks: human mode 1, --json 0, 17 rows).
# So `|| return 1` below catches real lookup failures — auth, network, unknown PR — and
# never a red PR. That distinction matters: returning 1 here means `genuine`, so getting
# it wrong would disable infra recovery entirely.
_ci_run_ids() {
  local pr="$1" lines
  lines=$(gh pr checks "$pr" --repo "$REPO" --json name,link \
            -q '.[] | "\(.name)|\(.link)"' 2>/dev/null) || return 1
  _run_ids_from_check_links "$lines"
}

# _arm_ci_deadline -> sets MAIN_CI_DEADLINE_AT to an absolute epoch instant.
#
# #62: the initial watch and each re-run poll have their own budgets, and each lives in
# its own function with its own elapsed counter -- so three re-runs multiplied a 900s
# budget into a ~71-minute worst case, and raising it for a slow first watch would have
# made that ~4 hours. An absolute instant is the only thing all three loops can agree
# on. Exported, because _rerun_main_ci runs inside a command substitution.
#
# Every input is validated before the arithmetic. An oversized override would otherwise
# overflow to a NEGATIVE epoch, which `_past_ci_deadline` then rejects for its minus
# sign -- silently disabling the very bound the operator was trying to set. And an
# unreadable clock would arm the deadline near epoch 0, expiring instantly and halting a
# healthy merge before its watch began. Both are configuration-reachable, since the span
# is a documented environment override.
_arm_deadline() {
  local __var="$1" span="$2" now
  now=$(date +%s 2>/dev/null)
  case "$now" in
    ''|*[!0-9]*)
      # ASSIGN empty, never `unset`: unset on a dynamically scoped local HIDES it and
      # re-exposes an outer variable of the same name, so a stale expired deadline in
      # the environment would spring back and cap every later call at one second (#62).
      printf -v "$__var" '%s' ''
      echo "[batch] WARN: cannot read the clock -> $__var NOT armed (the per-loop budgets still bound every wait)" >&2
      return 1 ;;
  esac
  printf -v "$__var" '%s' "$(( now + span ))"
}

_arm_ci_deadline() {
  local span
  span=$(_clamp_int "${MAIN_CI_ABSOLUTE_DEADLINE:-}" 7200 60 9999999)
  [ "$span" = "${MAIN_CI_ABSOLUTE_DEADLINE:-}" ] \
    || echo "[batch] WARN: unusable BIRCHER_MAIN_CI_ABSOLUTE_DEADLINE '${MAIN_CI_ABSOLUTE_DEADLINE}' -> using ${span}s" >&2
  _arm_deadline MAIN_CI_DEADLINE_AT "$span"
}

# _deadline_passed <epoch> -> rc 0 once that instant has passed.
#
# #71: generalised from _past_ci_deadline so the pre-merge phase can have its own wall
# clock without a third copy of this arithmetic -- the numeric-validation lesson from
# #62, where the same defect was found independently in three hand-rolled copies.
#
# An unusable value reads as "not past" rather than erroring: per-loop budgets still
# bound every caller, so failing open here cannot run forever, whereas failing closed on
# a garbled value would abandon healthy work. The digits check must come first and be
# SILENT: bash's `[ x -ge y ]` ERRORS rather than returning false on an oversized operand
# (#61b), and a test asserting only "non-zero" cannot tell the guard from the error.
# _now_s -> seconds since the epoch, or empty if the clock is unreadable.
# Wall clock, matching `_arm_deadline`. A monotonic source would be better in
# isolation, but the deadline that BOUNDS every wait here is wall-clock, so a
# monotonic grace inside a wall-clock phase would not make the pair safer -- it
# would only make the claim harder to check.
_now_s() {
  local n; n=$(date +%s 2>/dev/null)
  case "$n" in ''|*[!0-9]*) return 1 ;; esac
  printf '%s' "$n"
}

_deadline_passed() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "${#1}" -le 12 ] || return 1
  [ "$(date +%s)" -ge "$1" ]
}

# _cap_to <default> <epoch> -> seconds a single call may take: min(default, remaining).
#
# #71: floored at 1, never 0 -- `timeout 0` means NO LIMIT, so a zero cap would silently
# remove the bound at exactly the moment the budget ran out. Unarmed or unusable epoch
# -> the default, so a caller with no deadline still gets a per-call ceiling.
_cap_to() {
  local def="$1" at="${2:-}" now rem
  def=$(_clamp_int "$def" 120 1 3600)
  case "$at" in ''|*[!0-9]*) printf '%s' "$def"; return ;; esac
  [ "${#at}" -le 12 ] || { printf '%s' "$def"; return; }
  now=$(date +%s 2>/dev/null)
  case "$now" in ''|*[!0-9]*) printf '%s' "$def"; return ;; esac
  rem=$(( at - now ))
  [ "$rem" -lt 1 ] && rem=1
  [ "$rem" -lt "$def" ] && def="$rem"
  printf '%s' "$def"
}

# _past_ci_deadline -> rc 0 once the shared main-CI wall clock has run out.
#
# Unarmed, it never fires, so any caller that has not armed it keeps exactly its own
# budget. An unusable value reads as "not past" rather than erroring: the per-loop
# budgets still bound every caller, so failing open here cannot run forever, whereas
# failing closed on a garbled value would abandon a healthy watch. (bash's
# `[ x -ge y ]` ERRORS rather than returning false on an oversized operand -- #61b -- so
# the digits check has to come first and be silent, not be left to the comparison. A
# test that only asserts "nonzero" cannot tell the guard from the error.)
_past_ci_deadline() { _deadline_passed "${MAIN_CI_DEADLINE_AT:-}"; }

# _net_run <seconds> <cmd...> -> run a NETWORK command under a fixed wall-clock cap.
#
# #62: distinct from `_ci_gh` on purpose. `_ci_gh` shrinks its cap to whatever is left
# of the main-CI deadline, which is right for polling -- once the deadline passes there
# is no point continuing to poll. It is exactly WRONG for the confirmed-red recovery
# path, which runs AFTER that deadline has typically expired and must still be able to
# fetch, revert and push. Capping recovery at the CI remainder would give it one second
# and guarantee it failed.
#
# `git fetch`, `git worktree add` against a remote-tracking ref, and `git push` all
# perform network and credential operations. An earlier revision justified leaving them
# unbounded as "not network-facing", which was simply false: a hang there means the
# revert never completes, the function never returns 2, and the queue never reaches its
# halt handling -- main stays red with no operator-facing final state recorded.
# _clamp_int <value> <default> <min> <max> -> a usable decimal integer.
#
# #62: this is the THIRD numeric knob on this branch, and review found the same defect
# in each of the first two before this existed -- so it is now written once. Every
# element matters and each corresponds to a real finding:
#   * digits first, because `$((10#abc))` is a FATAL arithmetic error that aborts the
#     shell rather than returning non-zero;
#   * a length cap before the arithmetic, because bash WRAPS an oversized operand to a
#     positive value instead of erroring, so overflow looks like a valid large number;
#   * base 10 forced, because `$(( ))` reads a leading zero as OCTAL while `[ -ge ]`
#     reads it as decimal, and the two then disagree about what the string meant;
#   * range checked, because a syntactically fine value can still be operationally
#     absurd -- a 1-second deadline or a 0-second poll interval.
# Anything failing any of those falls back to the default rather than being honoured.
# _contains <haystack> <literal-needle> -> rc 0 if present. PIPE-FREE, deliberately.
#
# `producer | grep -q needle` is FLAKY under `set -o pipefail` (line 5): grep exits the
# moment it matches, and if the producer still has buffered output it takes SIGPIPE and
# returns non-zero, which pipefail then propagates as a failed pipeline -- despite the
# match. Whether it fires depends on buffer sizes and scheduling, so it presents as a
# rare, unreproducible failure. That is exactly what a `declare -f | grep -q` assertion
# in this suite did: failed once on the Linux runner, then passed four runs.
#
# `case` does literal substring matching in-process, with no second process to signal.
_contains() { case "$1" in *"$2"*) return 0 ;; esac; return 1; }

_clamp_int() {
  local v="$1" def="$2" lo="$3" hi="$4"
  case "$v" in ''|*[!0-9]*) printf '%s' "$def"; return ;; esac
  [ "${#v}" -le 9 ] || { printf '%s' "$def"; return; }
  v=$((10#$v))
  { [ "$v" -ge "$lo" ] && [ "$v" -le "$hi" ]; } || { printf '%s' "$def"; return; }
  printf '%s' "$v"
}

# _timeout_bin -> the path to a GNU-compatible timeout(1), or rc 1 if there is none.
#
# #62: both wrappers used to fall through to running the command UNBOUNDED when
# `timeout` was missing -- which silently discarded the entire guarantee this issue
# exists to provide, on exactly the machines least likely to notice. Now they refuse.
#
# Refusing is the conservative outcome on both paths that use it: `_ci_gh`'s callers
# already treat a non-zero return as a failed lookup (keep polling -> deadline -> halt),
# and `_net_run`'s recovery callers already treat it as "revert setup FAILED - main is
# red; fix by hand". Neither silently proceeds.
#
# `gtimeout` is checked too: that is the name GNU coreutils installs on macOS, so a
# developer box with coreutils is fully supported rather than falling into the refusal.
_timeout_bin() {
  if [ -z "${_TIMEOUT_BIN_LOADED:-}" ]; then
    _TIMEOUT_BIN_LOADED=1
    _TIMEOUT_BIN_CACHE=$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || printf '')
  fi
  [ -n "$_TIMEOUT_BIN_CACHE" ] || return 1
  printf '%s' "$_TIMEOUT_BIN_CACHE"
}

_net_run() {
  local cap="$1"; shift
  # #71: floor of 1, not 5. A COMPUTED remainder of 1-4 seconds is a correct answer,
  # and the old clamp replaced it with the 300s default -- so a call could run five
  # minutes past the very deadline the remainder was protecting. Operator-configured
  # defaults are validated separately, at load, where a 1-second value IS an error.
  cap=$(_clamp_int "$cap" 300 1 3600)
  # -k's grace is CLAMPED: it is added to every capped call, so an operator setting it
  # to an hour would make the phase bound arbitrarily weak -- the honest statement of
  # the bound is "deadline plus at most one clamped kill grace".
  # -k: plain `timeout` sends only SIGTERM and then KEEPS WAITING if the child ignores
  # it, so it is not a bound at all against exactly the hung transport this exists to
  # stop. A `git push` stuck in credential negotiation that does not die on TERM would
  # leave recovery blocked forever and main red.
  local tb; tb=$(_timeout_bin) || {
    echo "[batch] WARN: no usable timeout(1) -> refusing to run '$1' UNBOUNDED" >&2
    return 1
  }
  "$tb" -k "$(_clamp_int "${BIRCHER_KILL_GRACE:-10}" 10 1 60)" "$cap" "$@"
}
# Validated once, here, rather than at each use: these are OPERATOR-configured, so a
# 1-second value is a mistake and must fall back -- unlike a computed deadline remainder,
# where 1 second is meaningful. Conflating the two is what let a small remainder expand
# into a 300s call (#71).
BIRCHER_NET_TIMEOUT=$(_clamp_int "${BIRCHER_NET_TIMEOUT:-300}" 300 30 3600)
# #71: the pre-merge phase gets ONE wall clock covering the mergeability poll, the
# cross-review status post+verify, and the merge/reconciliation retries. Separate
# budgets per phase would reintroduce exactly the multiplication #62 removed.
BIRCHER_PREMERGE_BUDGET=$(_clamp_int "${BIRCHER_PREMERGE_BUDGET:-600}" 600 60 7200)
BIRCHER_PREMERGE_TIMEOUT=$(_clamp_int "${BIRCHER_PREMERGE_TIMEOUT:-60}" 60 10 3600)

# _ci_call_cap -> seconds any single main-CI `gh` call may take: the smaller of
# BIRCHER_CI_CALL_TIMEOUT (120s) and whatever is left on the shared deadline.
# The CONFIGURED ceiling is clamped here, at the point of use, with a floor of 5 -- an
# operator setting 3 has made a mistake. `_cap_to`'s own floor of 1 is a different thing
# entirely: it exists so a COMPUTED remainder of 1-4s passes through intact, which is
# what a per-call clamp of 5 used to destroy by expanding it back to the default (#71).
# Clamping at use rather than at load also survives a runtime override.
_ci_call_cap() {
  _cap_to "$(_clamp_int "${BIRCHER_CI_CALL_TIMEOUT:-120}" 120 5 3600)" "${MAIN_CI_DEADLINE_AT:-}"
}

# _ci_gh <args...> -> `gh`, bounded by a wall clock.
#
# #62: the absolute deadline is checked BETWEEN operations, so it could not bound one.
# A `gh` call that HANGS rather than failing was never interrupted: the initial watch or
# a re-run poll would block indefinitely, `_main_ci_verdict` would never be reached, and
# a genuinely broken main would stay unreverted with no halt. An earlier revision waved
# this away as unportable, which was wrong -- this script already depends on `timeout`
# unguarded for preflight and dispatch (:2256, :2265, :2303).
#
# A timed-out call exits non-zero, which every caller here already treats as a failed
# lookup -> keep polling -> eventually the deadline. Failing closed, on the existing
# path. The `command -v` fallback keeps the self-test runnable on stock macOS, where
# coreutils' timeout is absent; the runner is Linux and always takes the bounded path.
_ci_gh() {
  local cap; cap=$(_ci_call_cap)
  local tb; tb=$(_timeout_bin) || {
    echo "[batch] WARN: no usable timeout(1) -> refusing to run 'gh $1' UNBOUNDED" >&2
    return 1
  }
  "$tb" -k "$(_clamp_int "${BIRCHER_KILL_GRACE:-10}" 10 1 60)" "$cap" gh "$@"   # -k: see _net_run
}

# _ci_fetch_records <api-path> <list-field> -> every record in <list-field>, across
# ALL pages, as one compact JSON array on stdout. rc 1 unless the WHOLE response is
# well-formed: on any doubt it fails closed rather than emitting a partial set.
#
# This replaced `gh api <path> -q '<filter>'`, which had two ways to hand back an
# incomplete record set with rc 0:
#
#   PAGINATION. GitHub returns 30 records per page and `gh api` fetches only the
#   first without --paginate. muesli's own main commit already carries 28 check-runs
#   (measured 2026-08-16), so one more Dependabot batch puts a required failure on
#   page 2 where nothing will ever see it.
#
#   EMPTY BODY. `gh api` can exit 0 having written nothing, and jq exits 0 with no
#   output on zero inputs. The empty result then reads as "no records": the required
#   set matches nothing, _keep_blocking_checks falls back to every check, and
#   _checkrun_state calls it green. Silence must never be green. (`{}`, `null` and
#   error objects like {"message":"Not Found"} did already fail closed under -q,
#   because `.field[]` errors on null and `gh api -q` propagates jq's status - both
#   verified. The empty body was the one that got through.)
#
# --slurp makes gh emit ONE JSON array of page objects, so the entire response can be
# shape-checked before anything is read out of it. The outer `jq -s` additionally
# asserts that exactly one document arrived: plain `jq -e` reports only the status of
# the LAST of several concatenated documents, so a degraded first page followed by a
# well-formed one would otherwise pass.
_ci_fetch_records() {
  local path="$1" field="$2" raw
  raw=$(_ci_gh api --paginate --slurp "$path" 2>/dev/null) || return 1
  printf '%s' "$raw" | jq -e -s --arg f "$field" '
      length == 1
      and (.[0] | type == "array" and length > 0
           and all(.[]; type == "object" and (.[$f] | type == "array")))' >/dev/null 2>&1 || return 1
  printf '%s' "$raw" | jq -c -s --arg f "$field" '[ .[0][] | .[$f][] ]' 2>/dev/null || return 1
}

# _commit_ci_lines <sha> [required] -> "name|status|conclusion" for BOTH check-runs
# and commit STATUSES on a commit; rc 1 if either lookup is anything less than a
# complete, well-formed response.
#
# #67: the post-merge watcher and the rerun poll read only /check-runs, so a
# REQUIRED context that happens to be a commit status was invisible to them --
# `_required_contexts` lists it (GitHub's `contexts` covers both kinds), so
# `_keep_blocking_checks` kept looking for a name that could never appear. The PR
# path never had this gap because `gh pr checks` returns both.
#
# Statuses are normalised into check-run shape. The mapping that matters:
#   success -> completed|success
#   failure -> completed|failure
#   error   -> completed|failure   <-- NOT passed through: _checkrun_state matches
#                                      only check-run conclusions, so a bare
#                                      `error` would read as GREEN. GitHub counts
#                                      error and failure alike as a failed status.
#   pending -> in_progress|
#
# Uses `.statuses[]` rather than the combined `.state`: the combined verdict is
# `pending` whenever a required context has not reported, which on a merge commit
# is the normal case (verified: muesli merge commits carry 0 statuses, combined
# `pending`, while PR heads carry review-gate + bircher/cross-review).
#
# Deduped newest-first per context. The API appears to return one entry per
# context, but that is not documented as guaranteed and a stale duplicate could
# flip a verdict, so it is not assumed.
#
# STRUCTURE. Both kinds are normalised into ONE materialised record array, and every
# step downstream reads that array. This is deliberate: four separate false-greens in
# this function were all variants of two streams disagreeing about the same record --
# a stale duplicate winning dedup, control data sharing a namespace with record data,
# an unvalidated body reading as "none", and one half being validated while the other
# was not. With a single validated array there is no second stream to disagree with.
_commit_ci_lines() {
  local sha="$1" required="${2:-}" runs_json sts_json recs bad_pipe nl_count _b
  runs_json=$(_ci_fetch_records "repos/$REPO/commits/$sha/check-runs" check_runs) || {
    echo "[batch] WARN: unusable check-runs response for $sha -> failing closed" >&2
    return 1
  }
  sts_json=$(_ci_fetch_records "repos/$REPO/commits/$sha/status" statuses) || {
    echo "[batch] WARN: unusable status response for $sha -> failing closed" >&2
    return 1
  }
  # Validate the fields deduplication SORTS ON, before it sorts on them. The shape
  # guard only established that `.statuses` is an array; a record with a missing,
  # null or non-string `updated_at` would still be ordered -- by jq's cross-type
  # ordering, where null sorts below every string -- so a stale success could beat a
  # current failure and the function would return rc 0 having chosen wrongly.
  # `updated_at` is always present on a real status (verified against a live PR head).
  printf '%s' "$sts_json" | jq -e '
      all(.[]; (.context|type) == "string" and (.state|type) == "string"
               and (.updated_at|type) == "string")' >/dev/null 2>&1 || {
    echo "[batch] WARN: a commit status on $sha lacks a usable context/state/updated_at -> failing closed" >&2
    return 1
  }
  recs=$(jq -c -n --argjson runs "$runs_json" --argjson sts "$sts_json" '
      [ $runs[] | {name, status, conclusion: (.conclusion // ""),
                   app: ((.app.id // "") | tostring)} ]
    + [ $sts | to_entries | map(.value + {_i: .key})
        | group_by(.context) | map(sort_by([.updated_at, (0 - ._i)]) | last)[]
        | {name: .context,
           status: (if .state == "pending" then "in_progress" else "completed" end),
           conclusion: (if .state == "pending" then ""
                        elif .state == "success" then "success"
                        else "failure" end),
           # #73: commit statuses carry NO producing app -- verified against the live
           # API, where a real status object is {context, state, creator:null} with no
           # `app` key at all. Empty is therefore not "unknown", it is "unbindable", and
           # the matcher treats such a record as eligible for ANY requirement. That
           # mirrors branch protection, which can only pin a `checks[]` entry.
           app: ""} ]' 2>/dev/null) || return 1
  # A record whose name or status is not a string would emit `null|...` and classify
  # as an unrecognised - therefore green - line. Never guess at a malformed record.
  printf '%s' "$recs" | jq -e '
      all(.[]; (.name|type) == "string" and (.status|type) == "string"
               and (.conclusion|type) == "string" and (.app|type) == "string")' >/dev/null 2>&1 || {
    echo "[batch] WARN: a CI record on $sha has a non-string name/status -> failing closed" >&2
    return 1
  }
  # A newline in a name breaks the line protocol AND the line-oriented `grep -Fxq`
  # used to test membership of the required set -- so it cannot even be established
  # whether such a check is required. Always fail closed.
  nl_count=$(printf '%s' "$recs" | jq -r '[.[] | select(.name | test("\n"))] | length') || return 1
  if [ "${nl_count:-0}" != 0 ]; then
    echo "[batch] WARN: a CI check name on $sha contains a newline and cannot be matched -> failing closed" >&2
    return 1
  fi
  # A name carrying the field delimiter cannot be represented in this protocol:
  # `a|b` pending would become `a|b|in_progress|`, _keep_blocking_checks would read
  # the name as `a`, the required match would fail, it would fall back, and
  # _checkrun_state would see `b|in_progress|` -- unrecognised, so a required
  # PENDING check would read GREEN.
  #
  # Refuse only when such a check is REQUIRED. A non-required one is filtered
  # downstream anyway, so halting every verdict over it would turn a cosmetic
  # naming choice in someone else's repo into an outage. This applies to check-runs
  # and statuses alike; an earlier cut refused unconditionally on the check-run side
  # and conditionally on the status side, for no reason other than how it grew.
  bad_pipe=$(printf '%s' "$recs" | jq -r '.[] | select(.name | test("[|]")) | .name') || return 1
  if [ -n "$bad_pipe" ]; then
    while IFS= read -r _b; do
      [ -n "$_b" ] || continue
      if [ -n "$required" ] && printf '%s\n' "$required" | grep -Fxq "$_b"; then
        echo "[batch] WARN: REQUIRED check '$_b' contains a delimiter and cannot be classified -> failing closed for $sha" >&2
        return 1
      fi
      echo "[batch] WARN: dropping non-required check '$_b' (contains a delimiter)" >&2
    done <<EOF
$bad_pipe
EOF
  fi
  printf '%s' "$recs" | jq -r '
      .[] | select(.name | test("[|]") | not)
          | "\(.name)|\(.status)|\(.conclusion)|\(.app)"' 2>/dev/null
}

# _poll_ci <pr> <timeout_s> -> green|red|pending
# Poll `gh pr checks` until CI settles (not pending) or the timeout elapses.
_poll_ci() {
  local pr="$1" timeout="${2:-900}" w=0 buckets ci req
  # Fetched ONCE, outside the loop. Every call site invokes this inside $( ), so the
  # cache in _required_contexts lives in a subshell and dies with it -- polling would
  # re-request branch protection every 30s for the whole timeout.
  req=$(_required_contexts)
  while [ "$w" -lt "$timeout" ]; do
    # Fetch NAME too, so non-CI checks are filtered before deciding.
    # `review-gate` MUST be excluded or this DEADLOCKS: it stays pending until a
    # cross-vendor review is posted, and the caller of this function is the thing
    # about to perform that review. Each waits for the other. Seen 2026-08-07 on
    # muesli PR #549, which hung with every other check green. (Normal waves do
    # not hit it — the coordinator reviews without waiting on review-gate.)
    #
    # Not CI's business regardless: this answers "is the code green?", while
    # "has it been reviewed" is a separate question that branch protection still
    # enforces at merge time.
    buckets=$(gh pr checks "$pr" --repo "$REPO" --json name,bucket \
                -q '.[] | "\(.name)|\(.bucket)"' 2>/dev/null)
        buckets=$(_keep_blocking_checks "$buckets" "$req")
    ci=$(_normalize_ci "$buckets")
    [ "$ci" != pending ] && { echo "$ci"; return; }
    _iv=$(_clamp_int "$MAIN_CI_POLL_INTERVAL" 30 1 300)
    sleep "$_iv"; w=$((w + _iv))
  done
  echo pending
}

# _send_retry_decision <fails_so_far> <max> -> retry|give-up   (PURE, self-tested)
# Bounds the rc-5 prompt-delivery retry. Split out because the rc-5 path lives in
# run_item, which has no test harness -- but the BUDGET is the part that can loop
# forever, so it should not be the untested part. A non-numeric or absent max
# falls back to 2 rather than to "unbounded": the failure this bounds is an
# infinite session-creating loop.
_send_retry_decision() {
  local fails="${1:-0}" max="${2:-2}"
  case "$max"   in ''|*[!0-9]*) max=2 ;; esac
  case "$fails" in ''|*[!0-9]*) fails=0 ;; esac
  # Digits-only is NOT enough. A value too large for the shell's integer test
  # makes `[ ... -ge ... ]` ERROR rather than answer, and an erroring test is
  # false -- so the guard fell through to `retry` and the budget became
  # unbounded again, which is the precise failure it exists to prevent
  # (codex review; reproduced on bash 3.2 with a 30-digit value). Treat an
  # absurd magnitude as misconfiguration rather than as a very large budget.
  [ "${#max}"   -gt 4 ] && max=2
  [ "${#fails}" -gt 4 ] && fails=9999
  [ "$max" -lt 1 ] && max=1
  # Fail CLOSED: give up unless we can positively establish there is budget
  # left. Any comparison that cannot be evaluated must bound, never unbound.
  [ "$fails" -lt "$max" ] 2>/dev/null || { echo give-up; return; }
  echo retry
}

# _is_limit_message <text> -> yes|no. Matches the provider usage-limit
# signature a coordinator emits as its FIRST message when the window is
# exhausted (run #11: "You've hit your session limit / resets 6pm ...").
_is_limit_message() {
  # grep -c reads to EOF, so the producer cannot be SIGPIPEd mid-write (see _contains).
  [ "$(printf '%s' "$1" | grep -ciE "hit your (session|usage|weekly) limit|usage limit (reached|hit)")" != 0 ] \
    && echo yes || echo no
}

# _pick_implementer <c5> <c5reset> <c7> <x5> <x5reset> <x7> <now>
#   -> claude_code | codex | wait:<epoch>
# PURE usage-aware vendor selection ("-" = signal unavailable):
#   1. Exclude a vendor whose 5h window used_percent >= FIVEH_MAX.
#   2. Both excluded -> wait:<soonest 5h reset>.
#   3. Among eligible, pick the LOWER WEEKLY used_percent (balances the two
#      subscriptions against their own allocations - percentages normalize
#      unequal quotas/burn rates). Missing signal = eligible with weekly 0
#      (never block on absent data). Tie -> claude_code.
_pick_implementer() {
  local c5="$1" c5r="$2" c7="$3" x5="$4" x5r="$5" x7="$6" now="$7"
  local c_ok=1 x_ok=1
  [ "$c5" != "-" ] && [ "${c5%.*}" -ge "$FIVEH_MAX" ] 2>/dev/null && c_ok=0
  [ "$x5" != "-" ] && [ "${x5%.*}" -ge "$FIVEH_MAX" ] 2>/dev/null && x_ok=0
  if [ "$c_ok" = 0 ] && [ "$x_ok" = 0 ]; then
    local w="${c5r:--}"
    [ "$x5r" != "-" ] && { [ "$w" = "-" ] || [ "$x5r" -lt "$w" ] 2>/dev/null; } && w="$x5r"
    [ "$w" = "-" ] && w=$((now + 900))
    echo "wait:$w"; return
  fi
  [ "$c_ok" = 0 ] && { echo codex; return; }
  [ "$x_ok" = 0 ] && { echo claude_code; return; }
  local cw="${c7%.*}" xw="${x7%.*}"
  [ "$c7" = "-" ] && cw=0; [ "$x7" = "-" ] && xw=0
  if [ "${xw:-0}" -lt "${cw:-0}" ] 2>/dev/null; then echo codex; else echo claude_code; fi
}

# _claude_usage -> "5h_pct|5h_reset|7d_pct|7d_reset" (percent 0-100, resets as
# epochs), or non-zero when the probe fails (no claude / PTY drive failed / no
# rate_limits captured) -- callers degrade to the default vendor. A fresh cached
# tuple (< CLAUDE_USAGE_TTL) is served first so back-to-back gate evaluations do
# not each pay a probe turn. The probe (claude-usage-probe.py) runs the genuine
# `claude` binary and reads its own statusLine -- see the config note above.
_claude_usage() {
  local now age tup
  now=$(date +%s)
  if [ -f "$CLAUDE_USAGE_CACHE" ]; then
    age=$(( now - $(stat -c %Y "$CLAUDE_USAGE_CACHE" 2>/dev/null || stat -f %m "$CLAUDE_USAGE_CACHE" 2>/dev/null || echo 0) ))
    if [ "$age" -ge 0 ] && [ "$age" -lt "$CLAUDE_USAGE_TTL" ]; then
      tup=$(cat "$CLAUDE_USAGE_CACHE" 2>/dev/null)
      [ -n "$tup" ] && { printf '%s\n' "$tup"; return 0; }
    fi
  fi
  [ -f "$CLAUDE_USAGE_PROBE" ] || return 1
  tup=$(python3 "$CLAUDE_USAGE_PROBE" "$CLAUDE_USAGE_PROBE_TIMEOUT" 2>/dev/null) || return 1
  [ -n "$tup" ] || return 1
  mkdir -p "$(dirname "$CLAUDE_USAGE_CACHE")" 2>/dev/null
  printf '%s\n' "$tup" > "$CLAUDE_USAGE_CACHE"
  printf '%s\n' "$tup"
}

# _parse_codex_rate_limits: read ONE codex rollout JSONL line on stdin, emit
# "5h_pct|5h_reset|7d_pct|7d_reset" (percent 0-100, resets as epochs; "-" for an
# absent window). The whole line is parsed as JSON and searched for a rate_limits
# object; each present window (primary/secondary) is classified by window_minutes
# -- <=360 -> 5h bucket, >=1440 -> weekly bucket -- because the labels are
# plan-dependent (codex-cli 0.144.x "prolite" exposes only a weekly primary with
# secondary:null; legacy plans had a 5h primary + a weekly secondary). Prints
# nothing on any parse failure so callers degrade to the default vendor.
_parse_codex_rate_limits() {
  python3 -c '
import json,sys
line=sys.stdin.read().strip()
def find_rl(o):
    if isinstance(o,dict):
        rl=o.get("rate_limits")
        if isinstance(rl,dict): return rl
        for v in o.values():
            r=find_rl(v)
            if r: return r
    elif isinstance(o,list):
        for v in o:
            r=find_rl(v)
            if r: return r
    return None
try:
    rl=find_rl(json.loads(line))
except Exception:
    sys.exit(0)
if not isinstance(rl,dict): sys.exit(0)
five=("-","-"); week=("-","-")
for w in (rl.get("primary"), rl.get("secondary")):
    if not isinstance(w,dict): continue
    up=w.get("used_percent")
    if up is None: continue
    rs=w.get("resets_at"); rs="-" if rs is None else rs
    wm=w.get("window_minutes")
    if isinstance(wm,(int,float)) and wm<=360: five=(up,rs)
    elif isinstance(wm,(int,float)) and wm>=1440: week=(up,rs)
print("%s|%s|%s|%s" % (five[0],five[1],week[0],week[1]))
'
}

# _codex_usage -> "5h_pct|5h_reset|7d_pct|7d_reset" from the LAST rate_limits
# snapshot in the NEWEST codex rollout file (every `codex exec` - incl. the
# preflight probe - writes one). Non-zero when no rollout / no snapshot so
# callers degrade to the default vendor.
_codex_usage() {
  local f line
  f=$(ls -t "$CODEX_SESSIONS_DIR"/*/*/*/rollout-*.jsonl 2>/dev/null | head -1)
  [ -n "$f" ] || return 1
  line=$(grep '"rate_limits"' "$f" 2>/dev/null | tail -1)
  [ -n "$line" ] || return 1
  printf '%s' "$line" | _parse_codex_rate_limits
}

# _session_died <status> <err_code> -> "died" | "alive"
# `unknown` (from a failed lookup) maps to alive DELIBERATELY: one failed poll
# must never trigger recovery against a session that may still be running. A
# RUN of them is a different question, handled by the poll loop (#61).
# The server session is DEAD (recovery may run) only when it has a non-empty
# last_task_error_code OR a terminal-dead status. It is ALIVE for running AND
# idle: a coordinator ends its turn and goes idle between turns while a
# sub-agent runs, then is woken on delivery -- idle is NOT terminal. Empty /
# unknown status -> alive (never recover against a session we can't confirm
# dead).
_session_died() {
  # PURE, and it stays in bash for that reason. `coordinator.session.died` holds
  # the same rule and is what the Python coordinator will use, but routing THIS
  # caller through a subprocess was a mistake: it spawns a interpreter per poll
  # for a four-line predicate, and when the spawn cannot run the loop never sees
  # death and polls to the cap. The argument-wiring harness found it by running
  # run_item, not by reading it.
  #
  # IDLE IS NOT DEATH -- a coordinator awaiting a sub-agent is idle, and reading
  # that as death starts recovery against a session still working the PR.
  local status="$1" errcode="$2"
  [ -n "${errcode//[[:space:]]/}" ] && { echo died; return; }
  case "$status" in
    failed|error|cancelled) echo died ;;
    *)                      echo alive ;;
  esac
}

# _session_state <conv_id> -> "<status>|<err_code>"; "unknown|" on any failure.
#
# #61: this used to return "|" on failure, which _session_died read as `alive`.
# A transient 404/5xx/timeout was therefore indistinguishable from a healthy
# coordinator, so a DEAD one could be waited on to the full cap -- and, worse,
# a sustained lookup failure against a LIVE coordinator kept bircher blind while
# it ran. `unknown` keeps the same conservative single-poll behaviour (never
# recover against a session we cannot confirm dead) while letting the caller
# SEE the difference and act on a run of them.
# Reads the server session so run_item can detect death (vs the ambiguous
# local client process). $SERVER is the omnigent server (e.g. http://omnigent:8000).
_session_state() {
  # Reads omnigent. The parsing lives in v2/coordinator/session.py; this is the
  # shell's call into it. An unreachable or unparseable server is `unknown|`,
  # never a guess -- run_item counts consecutive unknowns and keeps waiting
  # rather than recovering while blind.
  local out=""
  out=$(_coordinator session-state --server "$SERVER" --id "$1") || out=""
  [ -n "$out" ] || out="unknown|"
  printf '%s' "$out"
}

# _last_assistant_text <conv_id> [n] -> concatenated text of the newest n (default
# 3) assistant messages, one blob on stdout. rc 0 = we got an answer (possibly
# empty); rc 1 = the LOOKUP FAILED and the empty output means nothing.
# Used by the within-item fast limit check (B-2): a young session whose first
# assistant turn is a provider "hit your limit" message means the implementer
# vendor's window is exhausted -- fail fast and re-gate rather than idle the cap.
#
# #60: this called GET /v1/conversations/{id}/items, which omnigent v0.9.0
# REMOVED. The 404 was swallowed by `curl -sf ... || return 0`, so the check
# silently answered "no limit message" for a whole release and every exhausted
# window idled the full ITEM_TIMEOUT instead of re-gating in seconds. The
# replacement is GET /v1/sessions/{id}/items, same order/limit shape.
#
# The rc split exists because that is what made the regression invisible: an
# empty string meant BOTH "no assistant text yet" and "we could not ask".
_last_assistant_text() {
  # rc 1 means the lookup FAILED, which the caller must not confuse with "no
  # assistant text" -- that distinction is what the provider-limit check runs
  # on. v2/coordinator/session.py raises rather than returning empty, and the
  # CLI turns that into a non-zero exit.
  local out=""
  if ! out=$(_coordinator last-assistant-text --server "$SERVER" \
               --id "$1" --n "${2:-3}"); then
    echo "[batch] WARN: session-items lookup failed for $1 (limit check cannot run this poll)" >&2
    return 1
  fi
  printf '%s' "$out"
}

# --- REST launch helpers (omnigent server API; see omnigent/server/API.md) ----

# _http_json <method> <path> [json_body] -> response body on stdout; curl rc.
_http_json() {
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sf --max-time 30 -X "$method" "$SERVER$path" \
      -H 'content-type: application/json' -d "$body" 2>/dev/null
  else
    curl -sf --max-time 30 -X "$method" "$SERVER$path" 2>/dev/null
  fi
}

# _json_get <key> -- read stdin JSON, print d[key] or "" (never errors).
# The last python-in-bash left here. Kept only because `_create_session` still
# lives in this file; it moves with it when the mutating session calls do.
_json_get() {
  python3 -c 'import json,sys
try: print(json.load(sys.stdin).get(sys.argv[1]) or "")
except Exception: print("")' "$1" 2>/dev/null
}

# _upload_bundle <agent_dir> <title> -> holder session_id ("" on failure).
# Multipart POST /v1/sessions {metadata, bundle} -> 201 {session_id}.
_upload_bundle() {
  local dir="$1" title="$2" tgz resp code
  tgz=$(mktemp "/tmp/bircher-bundle-XXXXXX.tgz") || return 1
  # Exclude harness scratch. omnigent's codex harness leaves temp CODEX_HOMEs
  # under .codex-tmp/ that contain SYMLINKS (auth.json -> /root/.codex/auth.json),
  # and the server rejects any tarball containing a link — so one failed codex
  # boot silently breaks every later upload until someone clears the directory.
  # Never add tar --dereference here: it would inline the real credential into
  # the uploaded bundle.
  tar czf "$tgz" -C "$dir" --exclude='./.codex-tmp' . 2>/dev/null || { rm -f "$tgz"; return 1; }
  # Capture the status and body: a bare `curl -sf ... 2>/dev/null` reports every
  # failure as an unexplained "bundle upload failed", which costs a diagnosis
  # round on exactly the errors that carry the reason in the response body.
  resp=$(curl -s --max-time 60 -w '\n%{http_code}' -X POST "$SERVER/v1/sessions" \
    -F "metadata={\"title\":\"$title\"}" \
    -F "bundle=@$tgz;type=application/gzip" 2>&1)
  rm -f "$tgz"
  # Only treat a trailing bare 3-digit line as the status code. Anything else
  # means no status was appended (e.g. a stubbed curl), so fall through to the
  # body unchanged rather than eating the first line of the response.
  code=$(printf '%s' "$resp" | tail -n1)
  # Pure parameter expansion rather than `printf | grep -q`: the pipeline form is
  # SIGPIPE-flaky under pipefail (see _contains), and "exactly three digits" needs no
  # subprocess at all.
  if [ "${#code}" = 3 ] && [ -z "${code//[0-9]/}" ]; then
    resp=$(printf '%s' "$resp" | sed '$d')
    case "$code" in
      200|201) : ;;
      *) echo "[batch] bundle upload failed (HTTP $code): ${resp:-no response body}" >&2; return 1 ;;
    esac
  fi
  printf '%s' "$resp" | _json_get session_id
}

# _get_agent_id <session_id> -> agent_id ("") via SessionResponse.
_get_agent_id() { _http_json GET "/v1/sessions/$1" | _json_get agent_id; }

# _create_session <agent_id> <host_id> <workspace> -> conv_id ("") = SessionResponse.id
_create_session() {
  # ROUTED since 2026-08-29. It was an unrouted mutation from the initial public
  # release, and INVISIBLE to the routing detector with it: the call went
  # through `_http_json`, whose curl takes `-X "$method"`, and every detector
  # pattern required a literal verb. Creating a model session is exactly the
  # kind of externally visible act the journal exists to record.
  #
  # Keyed on the RUN, not the workspace: a replay must return the session this
  # run already created rather than start a second one, and `perform` returns
  # the recorded external id without re-executing.
  local body
  body=$(python3 -c 'import json,sys; print(json.dumps({"agent_id":sys.argv[1],"host_id":sys.argv[2],"workspace":sys.argv[3]}))' "$1" "$2" "$3" 2>/dev/null) || return 1
  # CAPTURED, then parsed -- not piped straight out of `_effect`. A pipeline
  # masks the effect's exit status behind `_json_get`'s, so a refused or failed
  # create would look like a successful call that happened to return no id.
  local resp
  resp=$(_effect session_control "sess-create:${BIRCHER_RUN_ID:-$3}" 30 \
    curl -sf --max-time 30 -X POST "$SERVER/v1/sessions" \
    -H 'content-type: application/json' -d "$body") || return 1
  printf '%s' "$resp" | _json_get id
}

# _send_prompt <conv_id> <prompt> -> rc 0 on success. POST events (message).
# Uses a 120s timeout (not _http_json's 30s): per API.md, a message POSTed
# before the host-launched runner settles WAITS for the launch - on a cold
# start that exceeded 30s and logged a false "send_prompt failed" (run #13
# EMB02) even though the server delivered the message.
_send_prompt() {
  local body
  body=$(python3 -c 'import json,sys; print(json.dumps({"type":"message","data":{"role":"user","content":[{"type":"input_text","text":sys.argv[1]}]}}))' "$2" 2>/dev/null) || return 1
  _effect session_control "sess-prompt:$1" 120 \
    curl -sf --max-time 120 -X POST "$SERVER/v1/sessions/$1/events" \
    -H 'content-type: application/json' -d "$body" >/dev/null 2>&1
}

# _stop_session <conv_id> -> POST stop_session (hard-terminate incl. host runner).
_stop_session() {
  # ROUTED since 2026-08-29 -- see _create_session for how both hid. Stopping a
  # session is the act that makes an unconfirmed attempt confirmable, and the
  # design turns on that distinction: an unconfirmed stop leaves the attempt
  # non-terminal and halts the run. A stop that is never journalled cannot be
  # cited as evidence that it happened.
  _effect session_control "sess-stop:$1" 30 \
    curl -sf --max-time 30 -X POST "$SERVER/v1/sessions/$1/events" \
    -H 'content-type: application/json' -d '{"type":"stop_session"}' >/dev/null 2>&1 \
    || echo "[batch] WARN: stop_session for $1 failed" >&2
}

# _prune_session <session_id> -> DELETE a session. MANUAL-CLEANUP-ONLY for
# holders: upstream #1388 - the holder OWNS the run's session-scoped agent, so
# deleting it cascade-deletes EVERY session of the run (coordinators, children,
# their items) - i.e. the run's entire UI-visible history and forensic record.
# Run #11b's history was lost exactly this way (pruned at run end). Never call
# this on a holder whose run's history you still want; run-queue itself only
# prunes a DUD holder (failed agent_id lookup, no run ever started).
_prune_session() {
  _effect session_control "sess-stop:$1" 15 \
    curl -sf --max-time 15 -X DELETE "$SERVER/v1/sessions/$1" >/dev/null 2>&1 \
    || echo "[batch] WARN: prune of session $1 failed" >&2
}

# _post_cross_review_status <item> <pr> -> post + VERIFY a `bircher/cross-review`
# = success commit status on the PR's head commit, so a repo whose branch protection
# REQUIRES that check (in lieu of an approving review) can self-merge. Only called
# from merge_ready_pr, which the caller reaches ONLY on an outcome=ready item
# (cross-vendor review PASS) - so the status is only ever posted on a genuine PASS.
# Posted as the runner's own identity: setting a commit status is NOT a self-approval,
# so (unlike `gh pr review --approve`) it needs no second GitHub account. On a repo
# WITHOUT that required check the status is harmless. Retries transient GitHub API
# failures (5xx / secondary rate limit) and reads the status back to confirm it landed;
# a single swallowed hiccup here previously stranded a reviewed, CI-green PR until a
# human merged it. Contract: rc 0 = status confirmed present on the head sha;
# rc 1 = gave up after retries (caller escalates + records for the end-of-run sweep,
# not a silent defer).
# _pr_merge_state <pr> <expected_sha> -> merged | merged-unpinned | open | unknown
#
# #71: `gh pr merge` can COMPLETE SERVER-SIDE and then have its client die -- a timeout,
# a dropped response. Treating that as "merge failed" left a PR that IS merged with no
# main-CI watch and the item recorded as deferred: the same false-success class #62
# closed two of. So a failed attempt asks GitHub what actually happened before
# concluding anything.
#
# Evidence is `state == MERGED` plus a non-null headRefOid, deliberately NOT
# `mergeCommit.oid`: that field is eventually consistent and is exactly what forced a
# retry loop into #62's merge-sha lookup. Requiring it here would reintroduce the trap
# one function earlier.
#
# `merged-unpinned` -- MERGED, but not at the head we reviewed -- is its own answer
# rather than an error, because the code IS on main and must still be watched. What it
# must never do is report success; that is the caller's job.
_pr_merge_state() {
  local pr="$1" expected="${2:-}" out st head
  out=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
        gh pr view "$pr" --repo "$REPO" --json state,headRefOid \
        -q '"\(.state)|\(.headRefOid)"' 2>/dev/null) || { echo unknown; return; }
  st="${out%%|*}"; head="${out#*|}"
  case "$st" in
    MERGED) ;;
    OPEN)   echo open; return ;;
    *)      echo unknown; return ;;
  esac
  case "$head" in ''|null) echo unknown; return ;; esac
  if [ -n "$expected" ] && [ "$head" != "$expected" ]; then
    echo merged-unpinned
  else
    echo merged
  fi
}

_post_cross_review_status() {
  local item="$1" pr="$2" sha="${3:-}" attempt err
  # Head sha: the caller may PIN it (the sweep pins the reviewed head so the status
  # is never posted on an unreviewed push); otherwise fetch the current head, with a
  # few retries (gh pr view can transiently fail too).
  if [ -z "$sha" ]; then
    for attempt in 1 2 3; do
      sha=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
            gh pr view "$pr" --repo "$REPO" --json headRefOid -q '.headRefOid' 2>/dev/null)
      [ -n "$sha" ] && break
      [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep $((attempt * 2))
    done
  fi
  [ -n "$sha" ] || { echo "[batch:merge] WARN $item: no head sha for PR #$pr -> cross-review status skipped" >&2; return 1; }
  # Post, then read the status back to confirm it landed. Retry both with
  # exponential backoff; log the REAL gh error (no more 2>/dev/null) so a
  # non-transient cause is diagnosable next time.
  for attempt in 1 2 3 4 5; do
    # #71: BEFORE the POST, not only after the pair. Checked only at the bottom, a
    # deadline expiring during the 32s backoff still let attempt 5 start both calls --
    # ~53s of overrun (the sleep plus two expired-but-still-issued calls), where the
    # bound promises at most one in-flight call.
    _deadline_passed "${PREMERGE_DEADLINE_AT:-}" && break
    err=$(_effect status_check "status:$sha" \
            "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
            gh api "repos/$REPO/statuses/$sha" -X POST -f state=success \
            -f context=bircher/cross-review \
            -f description="cross-vendor review PASS (Bircher)" 2>&1 >/dev/null)
    # ...and before the verification too. Skipping it after a successful POST costs a
    # confirmation, so the caller merges best-effort and branch protection decides --
    # which is the existing conservative path, not a new one.
    _deadline_passed "${PREMERGE_DEADLINE_AT:-}" && break
    # grep -c (not -q) so the producer is never SIGPIPEd mid-write, and the COUNT is
    # compared rather than the pipeline's exit status -- the failure mode that can turn
    # a landed status into an unconfirmed one, and red CI into green in _normalize_ci.
    if [ "$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
         gh api "repos/$REPO/commits/$sha/status" \
         -q '.statuses[] | select(.context=="bircher/cross-review") | .state' 2>/dev/null \
         | grep -cx 'success')" != 0 ]; then
      echo "[batch:merge] $item: posted+verified bircher/cross-review=success on ${sha:0:7} (attempt $attempt)" >&2
      return 0
    fi
    echo "[batch:merge] WARN $item: cross-review status not confirmed on ${sha:0:7} (attempt $attempt/5)${err:+: $err}" >&2
    # #71: the phase deadline outranks the attempt counter, and a fixed backoff sleep
    # must not overrun it either -- five attempts x (POST + verify) could otherwise
    # spend ten minutes inside a "60-second" per-call cap.
    _deadline_passed "${PREMERGE_DEADLINE_AT:-}" && break
    # A FIXED sleep can outlast the deadline on its own. Capped to what remains, so
    # the phase cannot be overrun by waiting rather than by working.
    [ "$attempt" -lt 5 ] && { [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] \
      || sleep "$(_cap_to $((attempt * attempt * 2)) "${PREMERGE_DEADLINE_AT:-}")"; }
  done
  echo "[batch:merge] ERROR $item: could NOT post bircher/cross-review on PR #$pr after 5 attempts -> ESCALATE (ready, needs human merge)" >&2
  return 1
}

# _sha256 -> hex digest of stdin. sha256sum on Linux (the runner), shasum on macOS
# (where the self-tests also run).
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum
  else shasum -a 256
  fi | cut -d' ' -f1
}

# _pr_delta_digest <base_ref> <head_ref> -> a stable digest of the PR's OWN delta,
# or rc 1 when that delta cannot be established.
#
# GitHub's compare is three-dot, so `base...head` is the change the PR contributes
# and never the base branch's own commits. That property is what makes the digest
# comparable across an update-branch: that operation MERGES the base into the head
# (it does not rebase), so the new head CONTAINS the new base, the merge-base IS
# the new base, and the compare still yields only the PR's work.
#
# rc 1 is NOT "assume equal" - the caller turns it into an escalation. The
# unprovable cases are real: GitHub caps the changed-file list at 300, omits `patch`
# for binaries, pure renames and anything over its size limit, and truncates a large
# tree. Digesting a partial answer would produce a confident wrong one, which here
# means merging code no reviewer read.
_pr_delta_digest() {
  local base="$1" ref="$2" cmp tree count
  cmp=$(gh api "repos/$REPO/compare/${base}...${ref}" 2>/dev/null) || return 1
  [ -n "$cmp" ] || return 1
  count=$(printf '%s' "$cmp" | jq -r '.files | length' 2>/dev/null) || return 1
  case "$count" in ''|*[!0-9]*) return 1 ;; esac
  # 0 files = nothing to compare (degenerate, and it would make two unrelated empty
  # answers look equal); >= 300 = GitHub's documented cap for the changed-file list,
  # so at exactly 300 it may be truncated.
  { [ "$count" -ge 1 ] && [ "$count" -lt 300 ]; } || return 1
  # A file whose patch GitHub withheld leaves a hole in the comparison.
  printf '%s' "$cmp" | jq -e 'any(.files[]; has("patch") | not)' >/dev/null 2>&1 && return 1
  # The compare payload carries NO mode and NO type - verified against the API, where
  # files[] is filename/status/sha/patch plus counts and URLs. That gap is load-bearing
  # here: git stores a symlink's TARGET as its blob content, so a symlink pointing at
  # "x" and a regular file containing "x" share a blob sha AND project to an identical
  # patch. Digesting the compare alone would let a base that changed a path's TYPE
  # merge as though the PR's delta were untouched. The tree carries mode and type; a
  # TRUNCATED tree cannot answer for every path, so it fails closed like the rest.
  tree=$(gh api "repos/$REPO/git/trees/${ref}?recursive=1" 2>/dev/null) || return 1
  [ -n "$tree" ] || return 1
  # Require an explicit false. `== true` would PROCEED on an absent or null
  # `truncated`, i.e. treat an answer we did not get as a reassuring one.
  printf '%s' "$tree" | jq -e '.truncated == false' >/dev/null 2>&1 || return 1
  # Note this escalates whenever the base touched a file the PR also touches: the
  # resulting blob sha (and often the patch context) moves. That is CORRECT rather
  # than merely cautious - the merged file then combines both changes, and the
  # reviewer never saw that combination. The feature is for the common case where
  # the base moved elsewhere in the tree.
  # Canonical form: sorted by filename, sorted keys, and EVERY field that identifies
  # the change - path, rename origin, status, resulting blob, patch, and the tree
  # entry's mode|type. `--slurpfile` rather than `--argjson` keeps a large compare
  # payload off the command line, where a big PR would hit ARG_MAX.
  printf '%s' "$tree" \
    | jq -cS --slurpfile c <(printf '%s' "$cmp") '
        (.tree | map({key: .path, value: (.mode + "|" + .type)}) | from_entries) as $m
        | [ $c[0].files
            | sort_by(.filename)[]
            | { filename,
                previous_filename: (.previous_filename // null),
                status,
                sha,
                patch,
                entry: ($m[.filename] // "ABSENT") } ]' 2>/dev/null \
    | _sha256
}

# _restamp_if_delta_unchanged <item> <pr> <reviewed_sha>
#   -> rc 0 and RESTAMPED_HEAD=<new head> when the updated head provably carries the
#      SAME change the reviewer passed; rc 1 (caller escalates) otherwise.
#
# This is the ONE place bircher posts bircher/cross-review on a sha no reviewer saw,
# so the bar is a proof rather than a heuristic. update-branch merges the BASE into
# the head and touches nothing else:
# if the PR's own three-dot delta is byte-identical before and after, the reviewed
# CONTENT is unchanged and the PASS still covers exactly what will merge (issue #51).
#
# It deliberately does NOT hold when the update touched the PR's own diff - a
# conflict resolution or a fixup commit - and that is precisely what comparing the
# digests detects. Every other outcome (API failure, truncated compare, a head that
# never moved) returns rc 1, leaving the pre-#51 behaviour: escalate to a human.
RESTAMPED_HEAD=""
_restamp_if_delta_unchanged() {
  local item="$1" pr="$2" reviewed="$3" base new attempt old_digest new_digest
  RESTAMPED_HEAD=""
  if [ "${BIRCHER_CONTENT_EQUALITY:-1}" = 0 ]; then
    echo "[batch:sweep] $item: content-equality re-stamp disabled (BIRCHER_CONTENT_EQUALITY=0) -> escalate" >&2
    return 1
  fi
  base=$(gh pr view "$pr" --repo "$REPO" --json baseRefName -q '.baseRefName' 2>/dev/null)
  [ -n "$base" ] || { echo "[batch:sweep] $item: PR #$pr base branch unknown -> cannot prove delta" >&2; return 1; }
  # update-branch is ASYNCHRONOUS: wait for the head to actually move off the
  # reviewed sha. A head that never moves means nothing was updated, so there is
  # nothing to re-stamp and the earlier BEHIND reading is unexplained.
  for attempt in 1 2 3 4 5 6; do
    new=$(gh pr view "$pr" --repo "$REPO" --json headRefOid -q '.headRefOid' 2>/dev/null)
    [ -n "$new" ] && [ "$new" != "$reviewed" ] && break
    new=""
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep $((attempt * 2))
  done
  [ -n "$new" ] || { echo "[batch:sweep] $item: PR #$pr head never moved after update-branch -> escalate" >&2; return 1; }
  old_digest=$(_pr_delta_digest "$base" "$reviewed") || old_digest=""
  new_digest=$(_pr_delta_digest "$base" "$new")      || new_digest=""
  if [ -z "$old_digest" ] || [ -z "$new_digest" ]; then
    echo "[batch:sweep] $item: PR #$pr delta not provable (compare failed/truncated/patch withheld) -> escalate" >&2
    return 1
  fi
  if [ "$old_digest" != "$new_digest" ]; then
    echo "[batch:sweep] $item: PR #$pr delta CHANGED across the update (${reviewed:0:7} -> ${new:0:7}) -> escalate for re-review" >&2
    return 1
  fi
  echo "[batch:sweep] $item: PR #$pr delta PROVEN identical across the update (${reviewed:0:7} -> ${new:0:7}) -> re-stamping the review" >&2
  _post_cross_review_status "$item" "$pr" "$new" || return 1
  RESTAMPED_HEAD="$new"
  return 0
}

# _classify_blocked <required_names> <reported_rows> -> wait | defer | absent
#
# ORDERED and EXHAUSTIVE. BLOCKED covers a check still running, a check that
# failed, a check that has not REGISTERED yet, and conditions that are not
# checks at all -- a missing approval matches none of the check-shaped rules
# while still blocking forever, which is the case an obvious three-way split
# misses entirely.
#
# <required_names> newline-separated context names; empty means the snapshot
# said "no required contexts". The literal token `?` means the snapshot could
# not be read, which is NOT evidence of absence and must not spend the
# registration grace on an API outage.
# <reported_rows> `name|state` lines for the head.
_classify_blocked() {
  local req="$1" rows="$2" name row_state saw_absent=0 saw_pending=0 saw_bad=0
  [ "$req" = "?" ] && { printf 'defer'; return; }          # 1: unreadable required set -> fail closed
  # 1b: the ROWS could not be read. Unknown, not absent -- so wait, and do NOT
  # spend the registration grace on an API outage.
  [ "$rows" = "?" ] && { printf 'wait'; return; }
  [ -z "$req" ]    && { printf 'defer'; return; }          # 2: no required checks -> non-check blocker
  # ROWS ARE `name|status|conclusion`, three fields, and the pair must be read
  # the way `_checkrun_state` reads it. An earlier cut took field 2 alone --
  # which for a FINISHED check is `completed`, not `success` -- so every
  # reported context looked like a failure and a transient block was called
  # durable. Live evidence: muesli PR #736 was deferred to a human and was
  # CLEAN and mergeable seconds later.
  local st cc
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    row_state=$(printf '%s\n' "$rows" | awk -F'|' -v n="$name" '$1==n {print $2 "|" $3; exit}')
    if [ -z "$row_state" ]; then saw_absent=1; continue; fi
    st="${row_state%%|*}"; cc="${row_state#*|}"
    case "$st" in
      completed)
        case "$cc" in
          success|neutral|skipped) ;;             # GitHub's non-failing conclusions
          *) saw_bad=1 ;;
        esac ;;
      # queued/in_progress/waiting/requested/... A commit STATUS is already
      # NORMALISED into this shape by `_commit_ci_lines` -- pending becomes
      # `in_progress|""` and success becomes `completed|success` -- so both
      # kinds of required context are read by one rule.
      *) saw_pending=1 ;;
    esac
  done <<EOF
$req
EOF
  [ "$saw_pending" = 1 ] && { printf 'wait'; return; }     # 3: something is running
  [ "$saw_absent" = 1 ]  && { printf 'absent'; return; }   # 4: not registered YET -> grace
  [ "$saw_bad" = 1 ]     && { printf 'defer'; return; }    # 5: reported, not success -> durable
  # 6: every required context is GREEN and the PR is still BLOCKED.
  #
  # An earlier cut called this durable -- "an approval must be missing" -- and
  # it deferred muesli PR #736 to a human when the PR was CLEAN and mergeable
  # seconds later. `mergeStateStatus` is EVENTUALLY CONSISTENT and lags the
  # check states it is derived from, so all-green-but-BLOCKED is overwhelmingly
  # GitHub not having recomputed yet. Transient, bounded by the same grace that
  # bounds an unregistered check; only a block that OUTLIVES the grace with
  # everything green is durable.
  printf 'settling'
}

# _classify_merge_state <state> <mergeable> <mergeStateStatus> <head> <expected>
#   -> proceed | wait | defer
#
# Precedence, in order. FAILS CLOSED: an enum value neither table knows defers
# rather than proceeding, because GitHub adding a state must never read as
# "go". BLOCKED is resolved by _classify_blocked, whose `absent` answer the
# caller converts to wait-or-defer using the registration grace.
_classify_merge_state() {
  local st="$1" mergeable="$2" mss="$3" head="$4" expected="$5"
  [ "$st" != OPEN ] && { printf 'defer'; return; }                    # 1
  # 2: never proceed on a head the reviewer did not see.
  [ -n "$expected" ] && [ -n "$head" ] && [ "$head" != "$expected" ] \
    && { printf 'defer'; return; }
  [ "$mergeable" = CONFLICTING ] && { printf 'defer'; return; }       # 3
  { [ -z "$mergeable" ] || [ "$mergeable" = UNKNOWN ] \
    || [ -z "$mss" ] || [ "$mss" = UNKNOWN ]; } && { printf 'wait'; return; }  # 4
  case "$mss" in                                                      # 6
    CLEAN)    printf 'proceed' ;;
    UNSTABLE) printf 'proceed' ;;   # a NON-required check; protection ignores it
    BLOCKED)  printf 'blocked' ;;   # the caller resolves this one
    BEHIND)   printf 'defer' ;;     # no mutation here: update-branch moves the
                                    # head, which rule 2 then refuses forever
    DIRTY)    printf 'defer' ;;
    *)        printf 'defer' ;;     # 5: unknown future enum -> fail closed
  esac
}

# _await_mergeable_state <item> <pr> <expected_sha> -> rc 0 proceed | rc 1 defer
#
# THE FIX FOR THE muesli #735 HALT. The old gate polled `.mergeable`, which
# reports CONFLICT state only. Branch protection lives in `mergeStateStatus`,
# so a PR whose required check had not yet posted read as MERGEABLE, the merge
# was attempted, GitHub refused it, the effect became uncertain and the run
# HALTED -- needing a human before it could merge at all. Measured on #735:
# bircher posts bircher/cross-review at 13:29:11, the workflow reacts and posts
# review-gate at 13:29:18, and the merge landed in that seven-second window.
#
# Runs AFTER the cross-review status is posted, deliberately: review-gate reacts
# to that status, so a gate placed before it would wait for a state that cannot
# arrive until we act.
#
# Sets MERGE_GATE_NOTE on deferral. Never merges, never mutates.
MERGE_GATE_NOTE=""
_await_mergeable_state() {
  local item="$1" pr="$2" expected="$3"
  MERGE_GATE_NOTE=""
  local sleep_s=2 absent_since="" absent_key="" j st mergeable mss head verdict
  # A BACKSTOP, not the budget. If the clock is unreadable `_arm_deadline`
  # leaves the deadline empty and `_deadline_passed` never fires, so without
  # this the loop would poll for ever on the one failure that disables its
  # bound. The phase deadline remains the real limit whenever it is armed.
  local polls=0 max_polls=200
  local grace; grace=$(_clamp_int "${BIRCHER_CHECK_REGISTRATION_GRACE:-120}" 120 0 3600)
  while ! _deadline_passed "$PREMERGE_DEADLINE_AT" && [ "$polls" -lt "$max_polls" ]; do
    polls=$((polls + 1))
    # ONE response: two calls could disagree with each other.
    j=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "$PREMERGE_DEADLINE_AT")" \
        gh pr view "$pr" --repo "$REPO" \
        --json state,mergeable,mergeStateStatus,headRefOid 2>/dev/null) || j=""
    # `_json_get <key>` reads the document from STDIN, not from $1.
    st=$(printf '%s' "$j" | _json_get state)
    mergeable=$(printf '%s' "$j" | _json_get mergeable)
    mss=$(printf '%s' "$j" | _json_get mergeStateStatus)
    head=$(printf '%s' "$j" | _json_get headRefOid)
    # A failed lookup is UNKNOWN, never CLEAN.
    [ -z "$j" ] && { st=OPEN; mergeable=UNKNOWN; mss=UNKNOWN; head="$expected"; }
    verdict=$(_classify_merge_state "$st" "$mergeable" "$mss" "$head" "$expected")

    if [ "$verdict" = blocked ]; then
      local req rows snap
      snap=$(_required_contexts_snapshot) || snap=""
      if [ "${snap%%$'\n'*}" = known ]; then req=$(_req_names "${snap#*$'\n'}"); else req="?"; fi
      # `?` and not "": a FAILED lookup is not an empty result. Discarding the
      # failure made every required context look ABSENT, started the
      # registration grace on an API outage, and stranded the PR with "a
      # required check never registered". The required-SET snapshot already
      # preserved its uncertainty as `?`; the rows did not, and uncertainty
      # must survive both reads or neither.
      rows=$(_commit_ci_lines "$head" "$req") || rows="?"
      verdict=$(_classify_blocked "$req" "$rows")
      if [ "$verdict" = settling ]; then
        # Every required context is green and the PR is still BLOCKED. Bounded
        # by the SAME grace as an unregistered check, and keyed the same way,
        # because it is the same phenomenon: GitHub has not caught up yet.
        local skey="settle:$head" snow; snow=$(_now_s) || snow=""
        [ "$skey" = "$absent_key" ] || { absent_key="$skey"; absent_since="$snow"; }
        if [ -z "$snow" ] || [ -z "$absent_since" ] \
           || [ "$(( snow - absent_since ))" -lt "$grace" ]; then
          verdict=wait
        else
          MERGE_GATE_NOTE="merge deferred: blocked with every required check green for ${grace}s (an approval or a policy this cannot see)"
          echo "[batch:merge] $item: PR #$pr BLOCKED for ${grace}s with every required check green -> left open for the human" >&2
          return 1
        fi
      elif [ "$verdict" = absent ]; then
        # Grace is keyed by (head, the required set) and starts when a context
        # is FIRST observed absent -- not at the first BLOCKED observation. A PR
        # can sit BLOCKED for another reason first and only later expose a
        # newly required or re-run context as absent; starting the clock earlier
        # would defer on a genuine registration race.
        local key="$head:$req" now; now=$(_now_s) || now=""
        [ "$key" = "$absent_key" ] || { absent_key="$key"; absent_since="$now"; }
        # An unreadable clock cannot time the grace. Keep WAITING rather than
        # deferring: deferring would escalate to a human on the transient
        # condition this exists to tolerate, and `max_polls` still bounds it.
        if [ -z "$now" ] || [ -z "$absent_since" ] \
           || [ "$(( now - absent_since ))" -lt "$grace" ]; then
          verdict=wait
        else
          MERGE_GATE_NOTE="merge deferred: a required check never registered within ${grace}s"
          echo "[batch:merge] $item: PR #$pr BLOCKED and a required check never registered (${grace}s) -> left open for the human" >&2
          return 1
        fi
      elif [ "$verdict" = defer ]; then
        MERGE_GATE_NOTE="merge deferred: blocked by branch protection (not a pending check)"
        echo "[batch:merge] $item: PR #$pr BLOCKED durably (no required check pending) -> left open for the human" >&2
        return 1
      fi
    fi

    case "$verdict" in
      proceed) return 0 ;;
      defer)
        if [ -n "$expected" ] && [ -n "$head" ] && [ "$head" != "$expected" ]; then
          # SPECIFIC, because it is the most dangerous refusal to misread: the
          # PR moved off the head a reviewer signed off on.
          MERGE_GATE_NOTE="merge refused: PR head ${head:0:7} is not the reviewed head ${expected:0:7}"
        else
          MERGE_GATE_NOTE="merge deferred: mergeStateStatus=${mss:-unknown} mergeable=${mergeable:-unknown}"
        fi
        echo "[batch:merge] $item: PR #$pr not ready to merge (state=${mss:-unknown}) -> left open for the human" >&2
        return 1 ;;
    esac
    # Exponential, and CAPPED: a deadline check followed by an uncapped sleep
    # crosses the deadline it just tested (#71).
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep "$(_cap_to "$sleep_s" "$PREMERGE_DEADLINE_AT")"
    sleep_s=$(( sleep_s * 2 )); [ "$sleep_s" -gt 30 ] && sleep_s=30
  done
  MERGE_GATE_NOTE="merge deferred: the pre-merge budget expired before the PR became mergeable"
  echo "[batch:merge] $item: PR #$pr still not mergeable when the pre-merge budget expired -> left open for the human" >&2
  return 1
}

# merge_ready_pr <item> <pr> -> rc 0 (merged or deferred; MERGE_NOTE set on
# deferral) | rc 2 (HALT the run: main went red and the merge was reverted, or
# main CI never resolved). B-1 in-run merge: merging each ready PR before the
# next item launches means every later implementer branches from a main that
# already contains its siblings - the merge-order conflict class disappears.
# Safety: watch MAIN's CI on the merge commit; on red, revert (throwaway
# worktree; never touches the shared working tree) and halt; on timeout, halt
# without reverting (conservative).
MERGE_NOTE=""
MERGE_RETRY_ELIGIBLE=""
merge_ready_pr() {
  local item="$1" pr="$2" expected_sha="${3:-}"
  MERGE_NOTE=""
  MERGE_RETRY_ELIGIBLE=0
  # #71: evidence that what LANDED was not what was reviewed. Two variables, not one:
  # MERGE_NOTE is reassigned by every post-merge outcome (sha lookup failure, revert
  # success, revert failure, unresolved CI), so a note set at detection would be lost on
  # exactly the paths that matter most -- an unreviewed merge followed by a red main.
  # This one is written ONCE, at detection, and read by the callers that report.
  MERGE_UNREVIEWED=0
  MERGE_UNREVIEWED_NOTE=""
  # ONE wall clock for the whole pre-merge phase, armed before the first network call.
  # Per-call caps do not bound a LOOP: five status attempts x (POST + verify) at 60s
  # each is ten minutes inside a "60-second" ceiling. Local, so it cannot leak into the
  # next item the way the CI deadline did (#62).
  local PREMERGE_DEADLINE_AT=""
  _arm_deadline PREMERGE_DEADLINE_AT "$BIRCHER_PREMERGE_BUDGET"
  # #66: an automatic merge MUST be pinned. This used to treat an absent sha as
  # authorisation for an unpinned merge, so every caller had to remember a subtle
  # precondition -- and _merge_gate's comments record that exact mistake already
  # happening once. Refuse instead. A caller that legitimately needs an unpinned
  # merge should not be reaching for this function.
  if [ -z "$expected_sha" ]; then
    MERGE_NOTE="merge refused: no reviewed head to pin to"
    echo "[batch:merge] $item: refusing to merge PR #$pr UNPINNED (no reviewed head) -> left for the human" >&2
    return 0
  fi
  # Wait out GitHub's mergeability recompute, then merge. A transient gh failure
  # yields an EMPTY $m (not UNKNOWN); treat empty like UNKNOWN so a hiccup keeps
  # polling and, if it persists, is flagged retry-eligible for the sweep instead
  # of stranding a ready PR.
  local m=UNKNOWN t=0
  while { [ "$m" = "UNKNOWN" ] || [ -z "$m" ]; } && [ "$t" -lt 60 ] \
        && ! _deadline_passed "$PREMERGE_DEADLINE_AT"; do
    m=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "$PREMERGE_DEADLINE_AT")" \
        gh pr view "$pr" --repo "$REPO" --json mergeable -q '.mergeable' 2>/dev/null)
    { [ "$m" = "UNKNOWN" ] || [ -z "$m" ]; } && {
      _deadline_passed "$PREMERGE_DEADLINE_AT" && break
      # Capped: checking the deadline and THEN sleeping a fixed 5s crosses it by four
      # when one second remains. The check does not bound the sleep that follows it.
      [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep "$(_cap_to 5 "$PREMERGE_DEADLINE_AT")"; t=$((t + 5)); }
  done
  if [ "$m" != "MERGEABLE" ]; then
    MERGE_NOTE="merge deferred: mergeable=${m:-unknown}"
    { [ "$m" = "UNKNOWN" ] || [ -z "$m" ]; } && MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: PR #$pr not mergeable (${m:-unknown}) -> left open for the human" >&2
    return 0
  fi
  # #10: satisfy a required-check branch protection (bircher/cross-review) so a
  # protected repo self-merges without an approving review. No-op on repos that
  # don't require the check.
  local _status_confirmed=1
  if ! _post_cross_review_status "$item" "$pr" "$expected_sha"; then
    _status_confirmed=0
    # Best-effort: attempt the merge anyway. On a repo that REQUIRES the check the
    # merge below is BLOCKED and defers (retry-eligible); on a repo that does NOT
    # require it the merge still succeeds. Let branch protection decide rather than
    # pre-empting an otherwise-mergeable PR.
    MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: PR #$pr cross-review status unconfirmed -> attempting merge anyway (branch protection decides)" >&2
  fi
  # WAIT FOR THE STATE THAT ACTUALLY BLOCKS THE MERGE, now that the status this
  # repo's gate reacts to has been posted. Before this the loop below was the
  # only thing standing between a BLOCKED PR and a refused merge -- and on
  # muesli #735 the refusal made the effect uncertain, halted the run, and the
  # halt then blocked the very retries meant to absorb the lag.
  # SKIPPED when the status could not be confirmed, deliberately. The gate waits
  # for a CLEAN that OUR OWN status post produces; if that post failed, CLEAN
  # can never arrive and waiting for it would strand the PR for the whole phase.
  # The pre-existing best-effort behaviour is also explicit that an unconfirmed
  # status should still attempt the merge and "let branch protection decide
  # rather than pre-empting an otherwise-mergeable PR" -- on a repo that does
  # not require the check, the merge simply succeeds.
  if [ "$_status_confirmed" = 1 ] && ! _await_mergeable_state "$item" "$pr" "$expected_sha"; then
    MERGE_NOTE="$MERGE_GATE_NOTE"
    MERGE_RETRY_ELIGIBLE=1
    return 0
  fi
  # Merge, retrying briefly: the status just posted needs a moment to propagate to
  # the protected-branch merge gate (a single early attempt can still see BLOCKED).
  # When the caller PINNED the reviewed head (the sweep), merge ATOMICALLY against
  # it: --match-head-commit makes gh refuse the merge if a push moved the head after
  # review, so a race can never land unreviewed code.
  local merged=0 mt=0
  while [ "$mt" -lt 30 ] && ! _deadline_passed "$PREMERGE_DEADLINE_AT"; do
    # The key carries the GENERATION, so a retry after a reconciliation is a
    # new attempt rather than a replay of a spent one. `merge:<pr>:<head>`
    # alone was stable across reconciliations: the kernel had resolved that
    # attempt, its key was consumed, and the retry came back with the resolved
    # attempt's null external id instead of executing -- which this function
    # then read as "merged, sha unknown" for a PR that was still open.
    #
    # The generation is the kernel's own notion of a distinct attempt: it is
    # re-fenced at every dispatch, so retries WITHIN one attempt still collapse
    # to a single effect (which is what idempotency is for) while a genuinely
    # new attempt gets a genuinely new key. Empty outside a kernel run, which
    # leaves the legacy shape untouched.
    _effect merge "merge:$pr:$expected_sha${BIRCHER_GENERATION:+:g$BIRCHER_GENERATION}" \
      "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "$PREMERGE_DEADLINE_AT")" \
      gh pr merge "$pr" --repo "$REPO" --squash --delete-branch \
      --match-head-commit "$expected_sha" >/dev/null 2>&1 && { merged=1; break; }
    # #71: a failed ATTEMPT is not a failed MERGE. The request can complete server-side
    # before the client dies, so ask GitHub before concluding -- otherwise a merged PR
    # is recorded as deferred and its merge commit is never watched.
    # #71: the reconciliation probe is itself a network call. Reached with the budget
    # already spent by the merge attempt, `_cap_to` floors it to 1s and it can still
    # burn another kill grace -- so the phase overran by two graces, not one.
    _deadline_passed "$PREMERGE_DEADLINE_AT" && break
    case "$(_pr_merge_state "$pr" "$expected_sha")" in
      merged)
        echo "[batch:merge] $item: PR #$pr was already MERGED server-side (the client call failed) -> continuing" >&2
        merged=1; break ;;
      merged-unpinned)
        # MERGED, but not at the head we reviewed. The code is on main so it must still
        # be watched -- but it can never be reported as a success. Flag and evidence are
        # set HERE, at detection, because every later exit path reassigns MERGE_NOTE.
        MERGE_UNREVIEWED=1
        MERGE_UNREVIEWED_NOTE="merged head was NOT the reviewed head ${expected_sha:0:7} -- review did not cover what landed"
        echo "[batch:merge] !!!! $item: PR #$pr merged at a head bircher never reviewed (expected ${expected_sha:0:7}) !!!!" >&2
        merged=1; break ;;
    esac
    _deadline_passed "$PREMERGE_DEADLINE_AT" && break
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep "$(_cap_to 5 "$PREMERGE_DEADLINE_AT")"
    mt=$((mt + 5))
  done
  if [ "$merged" != 1 ]; then
    MERGE_NOTE="merge deferred: gh pr merge failed"
    MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: merge of PR #$pr FAILED -> left open for the human" >&2
    return 0
  fi
  # #62: LOCAL, not global. `_arm_ci_deadline` assigns to this name; declaring it local
  # here means bash's dynamic scoping still shows it to every helper called from this
  # function -- including `_rerun_main_ci` inside a command substitution -- while it
  # vanishes on EVERY return path with no cleanup to forget. Exported and un-cleared, an
  # expired deadline from one item shrank the next item's `_required_contexts` lookup to
  # a one-second cap, which failed, made the required set unknown, and let
  # `_keep_blocking_checks` fall back to every check.
  local MAIN_CI_DEADLINE_AT=""
  # Arm BEFORE the first post-merge GitHub lookup, not after. Armed later, a hang in either the merge-sha lookup or the branch-protection
  # fetch below would run unbounded and the deadline would never even be set -- the
  # watch would not start, so nothing downstream could time it out.
  _arm_ci_deadline
  # RETRY before concluding the oid is absent. GitHub's PR representation is eventually
  # consistent -- this file already waits out the mergeability recompute above for the
  # same reason -- so a single empty answer immediately after a merge is not evidence of
  # persistent absence, and halting on first sight would strand healthy merges. A
  # transport FAILURE is different and breaks out at once. Bounded by both a try count
  # and the absolute deadline.
  local sha sha_rc _sha_try=0 _sha_max
  _sha_max=$(_clamp_int "${BIRCHER_MERGE_SHA_TRIES:-5}" 5 1 20)
  while :; do
    sha=$(_ci_gh pr view "$pr" --repo "$REPO" --json mergeCommit -q '.mergeCommit.oid' 2>/dev/null); sha_rc=$?
    { [ "$sha_rc" -ne 0 ] || [ -n "$sha" ]; } && break
    [ "$_sha_try" -ge "$_sha_max" ] && break
    _past_ci_deadline && break
    _sha_try=$((_sha_try + 1))
    echo "[batch:merge] $item: no merge sha for PR #$pr yet (try $_sha_try) -> retrying" >&2
    # Capped against the CI deadline for the same reason as the pre-merge sleeps: a
    # fixed sleep after a deadline check crosses the deadline it just tested. The
    # overshoot here is small against a 7200s budget, but the rule is uniform so the
    # assertion can be "no bare numeric sleep" rather than a list of exceptions.
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep "$(_cap_to 3 "${MAIN_CI_DEADLINE_AT:-}")"
  done
  # A FAILED lookup is not "no merge sha". The PR is already merged at this point, so an
  # unwatched main is the one outcome that must never be reported as success -- and
  # returning 0 here does exactly that, letting the queue move on while a merge that may
  # have broken main goes unexamined. The refusal path added for a missing timeout(1)
  # reaches this line, which is how it was found: `_ci_gh` returning 1 fell straight into
  # the empty-sha branch below and skipped the watch.
  if [ "$sha_rc" -ne 0 ]; then
    echo "[batch:merge] !!!! $item: merge-sha lookup FAILED after merging PR #$pr -> main is UNWATCHED -> HALTING !!!!" >&2
    MERGE_NOTE="merged; merge-sha lookup FAILED - main is UNWATCHED, check by hand"
    return 2
  fi
  echo "[batch:merge] $item: PR #$pr MERGED (${sha:-sha unknown}); watching main CI" >&2
  # An EMPTY sha with rc 0 halts too. I argued for leaving this at rc 0 on the grounds
  # that GitHub returning no merge commit is a DIFFERENT failure from a lookup that
  # broke; review refuted it, correctly. The safety invariant is about the OUTCOME, not
  # the cause: after a confirmed merge, not having the identifier needed to watch main
  # is unresolved post-merge state either way. Returning 0 told the caller the merge
  # succeeded, `run_item` propagated it, the loop launched the next item, and the sweep
  # recorded `0:merged` -- stacking further merges onto a main nobody had checked.
  [ -z "$sha" ] && {
    echo "[batch:merge] !!!! $item: no merge sha for PR #$pr -> main is UNWATCHED -> HALTING !!!!" >&2
    MERGE_NOTE="merged; no merge sha - main is UNWATCHED, check by hand"
    return 2
  }
  # Watch main's CI on the merge commit (the #157 green-per-PR-red-on-main net).
  local waited=0 state=pending lines mreq _iv _snap _miss _exp _cls
  # #70: ONE snapshot, tri-state. `known`/`empty` are authoritative answers and are
  # cached for the phase; `unknown` is a cached DEPENDENCY FAILURE, not a verdict, and is
  # refreshed every poll below -- backoff by poll count was tried and rejected because at
  # a 30s interval its sixth attempt lands at 930s, past the rerun phase's entire budget,
  # so a dependency that recovered early would never be re-observed.
  # Declared local so `_rerun_main_ci` inherits them through the command substitution
  # (dynamic scoping, the same route MAIN_CI_DEADLINE_AT already takes) rather than
  # re-deriving a second, possibly different, required set mid-recovery.
  local MAIN_SNAP_STATE MAIN_SNAP_NAMES
  _snap=$(_required_contexts_snapshot)
  MAIN_SNAP_STATE=${_snap%%$'\n'*}
  MAIN_SNAP_NAMES=${_snap#*$'\n'}; [ "$MAIN_SNAP_STATE" = known ] || MAIN_SNAP_NAMES=""
  # #73: MAIN_SNAP_NAMES is TYPED (bound<TAB>ctx<TAB>app / unbound<TAB>ctx). Everything
  # that only cares about names takes field 2; the completeness gate keeps the typing,
  # which is the whole point -- flattening it here would put the trust boundary straight
  # back in the bin.
  mreq=$(_req_names "$MAIN_SNAP_NAMES")   # once, not on every 30s poll
  while [ "$waited" -lt "$MAIN_CI_SETTLE_TIMEOUT" ] && ! _past_ci_deadline; do
    _iv=$(_clamp_int "$MAIN_CI_POLL_INTERVAL" 30 1 300)
    sleep "$_iv"; waited=$((waited + _iv))
    # #67: check-runs AND commit statuses -- a required status was invisible here.
    # rc 1 (either fetch failed) leaves `lines` empty, which _checkrun_state reads
    # as pending, so a failed lookup keeps polling rather than reading green.
    # A failed protection lookup is recoverable: retry it EVERY poll (the 30s interval
    # already rate-limits it to the same cadence as the CI fetch beside it) until it
    # answers, so a transient outage does not cost the whole phase.
    # Gated on the list being SET. The snapshot only matters for the completeness gate,
    # and `mreq` was fetched once per phase before this change -- so retrying it on an
    # opted-OUT repo would add an API call per poll during a protection outage, which is
    # a behaviour change on repos whose whole guarantee is that nothing changed.
    if [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ] && [ "$MAIN_SNAP_STATE" = unknown ]; then
      _snap=$(_required_contexts_snapshot)
      MAIN_SNAP_STATE=${_snap%%$'\n'*}
      MAIN_SNAP_NAMES=${_snap#*$'\n'}; [ "$MAIN_SNAP_STATE" = known ] || MAIN_SNAP_NAMES=""
      mreq=$(_req_names "$MAIN_SNAP_NAMES")
    fi
    lines=$(_commit_ci_lines "$sha" "$mreq") || lines=""
    # #73: drop rows from an app the requirement did not name BEFORE classifying, or a
    # stray wrong-app failure turns main red and can trigger an auto-revert that branch
    # protection itself would never have asked for. Gated on opt-in, so a repo that has
    # not declared its expected contexts keeps exactly its previous behaviour.
    # Classification sees only ELIGIBLE rows; completeness keeps the originals, because
    # a row dropped for ineligibility must not look the same as a check that never
    # reported. Absence is precisely what the gate is for.
    _cls="$lines"
    [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ] \
      && _cls=$(_drop_wrong_producer "$lines" "$MAIN_SNAP_NAMES")
    state=$(_checkrun_state "$(_keep_blocking_checks "$_cls" "$mreq")")
    # #70: green needs the EXPECTED set complete, not merely the registered subset green.
    # A required context gated behind `needs:` registers after faster ones finish -- an
    # 11-second margin measured on muesli, and structurally unbounded elsewhere -- so
    # `_keep_blocking_checks` sees only the fast ones, all green, and the watcher breaks
    # out before the slow one can report. Red is untouched and still breaks at once: a
    # terminal red is authoritative whether or not the rest has registered.
    if [ "$state" = green ]; then
      _exp=$(_expected_set "$MAIN_SNAP_STATE" "$MAIN_SNAP_NAMES")
      if [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ] && [ "$MAIN_SNAP_STATE" = unknown ]; then
        echo "[batch:merge] $item: main CI green but branch protection is unreadable -> not accepting green yet" >&2
        state=pending
      elif [ -n "$_exp" ] || [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ]; then
        # A required context whose only rows were FILTERED OUT has no eligible report at
        # all -- and if it is not in the declared subset, nothing else will notice.
        # After BOTH filters: a required context can be erased by the ignore list just
        # as easily as by producer matching, and either way it stops being looked at.
        _miss=$(_emptied_by_filter "$lines" "$(_drop_ignored "$_cls")" "$mreq")
        [ -z "$_miss" ] && _miss=$(_expected_incomplete "$lines" "$_exp" "$MAIN_SNAP_NAMES")
        [ -n "$_miss" ] && {
          echo "[batch:merge] $item: main CI green so far, but expected context '$_miss' has not finished -> waiting" >&2
          state=pending
        }
      fi
    fi
    [ "$state" != "pending" ] && break
  done
  local decision
  if [ "$state" = green ] || [ "${BIRCHER_MAIN_CI_RERUN:-1}" = 0 ]; then
    decision=$(_main_ci_verdict "$state" "")
  else
    echo "[batch:merge] $item: main CI $state on $sha -> re-running before deciding (flake check)" >&2
    local second; second=$(_rerun_main_ci_until_green "$sha")
    decision=$(_main_ci_verdict "$state" "$second")
    echo "[batch:merge] $item: re-run main CI -> $second (verdict: $decision)" >&2
  fi
  case "$decision" in
    continue)
      # #71: green proves the build is healthy, not that review covered what landed.
      # Every other arm already returns 2; this is the only one that could have reported
      # an unreviewed merge as a success.
      if [ "${MERGE_UNREVIEWED:-0}" = 1 ]; then
        echo "[batch:merge] !!!! $item: main CI green on $sha, but $MERGE_UNREVIEWED_NOTE -> HALTING !!!!" >&2
        MERGE_NOTE="merged then HALTED: $MERGE_UNREVIEWED_NOTE"
        return 2
      fi
      echo "[batch:merge] $item: main CI green on $sha" >&2
      return 0 ;;
    revert-halt)
      echo "[batch:merge] !!!! $item: MAIN CI RED on merge $sha (confirmed) -> reverting + HALTING the run !!!!" >&2
      # Guard: never run a bare `git revert` (empty sha -> a usage error that leaves
      # main red, exactly the 2026-07-10 failure). Fix by hand if we have no sha.
      if [ -z "$sha" ]; then
        echo "[batch:merge] WARN $item: no merge sha to revert - main is red; fix by hand" >&2
        MERGE_NOTE="merged; main CI red but NO sha to revert (fix by hand)"
        return 2
      fi
      local rw="/tmp/revert-$pr" pc rargs reverted=0
      if ( cd "$WORKDIR" && _net_run "$BIRCHER_NET_TIMEOUT" git fetch origin -q \
            && _net_run "$BIRCHER_NET_TIMEOUT" git worktree add --detach "$rw" origin/main -q ); then
        # parents = (fields in `rev-list --parents` line) - 1; a merge commit needs -m 1.
        pc=$(git -C "$rw" rev-list --parents -n1 "$sha" 2>/dev/null | wc -w | tr -d ' ')
        pc=$(( pc > 0 ? pc - 1 : 1 ))
        rargs=$(_revert_git_args "$sha" "$pc")
        # shellcheck disable=SC2086
        if [ -n "$rargs" ] && ( cd "$rw" && git revert $rargs \
              && _effect ref_update "revert-push:$pr" "$BIRCHER_NET_TIMEOUT" git push origin HEAD:main -q ); then
          echo "[batch:merge] $item: revert pushed to main (parents=$pc)" >&2; reverted=1
        else
          echo "[batch:merge] WARN $item: automatic revert FAILED (sha=$sha parents=$pc) - main is red; fix by hand" >&2
        fi
      else
        echo "[batch:merge] WARN $item: revert setup (fetch/worktree) FAILED - main is red; fix by hand" >&2
      fi
      git -C "$WORKDIR" worktree remove --force "$rw" 2>/dev/null
      # MERGE_NOTE must reflect what ACTUALLY happened (it lands in the scorecard).
      if [ "$reverted" = 1 ]; then
        _reopen_reverted_issues "$pr"
        MERGE_NOTE="merged then REVERTED: main CI red (confirmed on re-run)"
      else
        MERGE_NOTE="merged; automatic revert FAILED - main RED, fix by hand"
      fi
      return 2 ;;
    halt)
      echo "[batch:merge] !!!! $item: main CI unresolved on $sha (confirmed) -> HALTING (no revert) !!!!" >&2
      MERGE_NOTE="merged; main CI unresolved after re-run"
      return 2 ;;
    *)
      echo "[batch:merge] !!!! $item: unexpected merge-gate verdict '$decision' on $sha -> HALTING (fail-closed) !!!!" >&2
      MERGE_NOTE="merged; unexpected CI verdict '$decision' -> halted (fail-closed)"
      return 2 ;;
  esac
}

# _record_deferred_ready <item> <pr> <merge_rc> [issue] [reviewed_sha]: append
# (item,pr,issue,reviewed_sha,run_id) to DEFERRED_READY_FILE iff the PR deferred on a
# transient/retry-eligible class, so the end-of-run sweep can re-drive it by its
# EXACT pr number (no re-discovery -> no GOTCHA-1 mapping blind spot), close its
# issue on merge, and refuse to merge a head that changed since review. The caller
# passes the head the PASS covered, captured BEFORE merge_ready_pr's retry window --
# a push landing during that window must NOT be recorded as the reviewed head.
# No-op for a clean merge or a human-hand-off deferral (CONFLICTING/DIRTY/reverted).
_record_deferred_ready() {
  local item="$1" pr="$2" mrc="$3" issue="${4:-}" sha="${5:-}"
  [ "$mrc" = 0 ] && [ -n "$MERGE_NOTE" ] && [ "${MERGE_RETRY_ELIGIBLE:-0}" = 1 ] || return 0
  mkdir -p "$(dirname "$DEFERRED_READY_FILE")"
  # The RUN ID is recorded, as a fifth field. Without it the sweep could only
  # adopt by item code and took the newest run for that code -- which is not
  # necessarily the run that opened this PR. A re-queued item creates a newer
  # run, and the sweep would then attribute the older run's PR to it and ask
  # the kernel to revalidate the merge against the wrong attempt's
  # authorization. Adopting by code was a guess that looked like a lookup.
  printf '%s\t%s\t%s\t%s\t%s\n' "$item" "$pr" "$issue" "$sha" "${BIRCHER_RUN_ID:-}" >> "$DEFERRED_READY_FILE"
}

# reconcile_deferred_ready -> end-of-run self-heal: re-drive every ready PR that a
# TRANSIENT failure left open (recorded in DEFERRED_READY_FILE). By end-of-run the
# startup gh burst is long gone, so re-posting bircher/cross-review + merging
# overwhelmingly succeeds. Reuses merge_ready_pr UNCHANGED, so its post+verify gate
# and main-CI-watch/revert safety still apply. Anything still unmergeable becomes a
# loud escalation scorecard row for the (now rare) human hand-off. NEVER --admin.
reconcile_deferred_ready() {
  echo "[batch:sweep] reconciling ready-but-open PRs deferred by transient failures" >&2
  local line rest item pr issue sha st cur mss mrc deferred_run
  # awk dedup PRESERVES the original append order (queue/manifest order); `sort -u`
  # would reorder lexicographically (i10 before i2) and reintroduce the merge-order
  # conflicts the sequential runner avoids.
  awk '!seen[$0]++' "$DEFERRED_READY_FILE" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    # Split on TAB by hand: `IFS=$'\t' read` collapses consecutive tabs (tab is
    # IFS-whitespace), which would drop an EMPTY issue field and misalign sha.
    item=${line%%$'\t'*}; rest=${line#*$'\t'}
    pr=${rest%%$'\t'*};   rest=${rest#*$'\t'}
    issue=${rest%%$'\t'*}; rest=${rest#*$'\t'}
    # A row written before the run id was recorded has no fifth field, so
    # `sha` keeps the whole tail and `deferred_run` is empty -- which falls
    # back to adopting by code, exactly as before. An old queue must not
    # become unreadable because a field was added.
    case "$rest" in
      *$'\t'*) sha=${rest%%$'\t'*}; deferred_run=${rest#*$'\t'} ;;
      *)        sha="$rest"; deferred_run="" ;;
    esac
    [ -n "$pr" ] || continue
    # Adopt THIS item's run before touching it. The sweep runs after run_item
    # has returned, and nothing unsets the exported BIRCHER_RUN_ID/GENERATION --
    # so without this every sweep effect is recorded against whichever item
    # happened to run last. That is worse than not recording: it attributes one
    # item's mutations to another's ledger. Re-adopting per iteration also
    # re-fences the generation, which is what makes each sweep attempt its own
    # attempt rather than a continuation of a finished one.
    # Prefer the run that actually opened this PR over the newest run sharing
    # its item code.
    if [ -n "$deferred_run" ]; then
      BIRCHER_RUN_ID="$deferred_run"; export BIRCHER_RUN_ID
      BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer)
      export BIRCHER_GENERATION
      # A recorded id the kernel does not know yields NO generation, and the
      # first `_effect` then aborts on `${BIRCHER_GENERATION:?}` -- which under
      # `set -u` exits the shell and silently kills the whole sweep loop, at
      # rc 0, with the remaining PRs never looked at. Fall back to adoption
      # rather than carrying an empty generation into an effect.
      if [ -z "$BIRCHER_GENERATION" ]; then
        # DO NOT fall back to adoption here. The recorded id exists precisely so
        # this sweep stops guessing by item code, and adoption takes the NEWEST
        # run for that code -- so a phantom id would hand this PR's status and
        # merge attempts to a newer run created by a requeue, which is the
        # attribution defect the fifth field was added to remove. Skipping
        # leaves the PR for a human with its ledger honest.
        echo "[batch:sweep] $item: recorded run '$deferred_run' yielded no generation -> skipping (refusing to guess a different run)" >&2
        # AND SAY SO WHERE HUMANS LOOK. Every other fail-closed sweep path
        # writes an escalation row; this one left the handoff in transient
        # stderr, and the next wave truncates the deferred file -- so the PR it
        # "leaves for a human" was never mentioned to one.
        mkdir -p "$(dirname "$SCORECARD")" 2>/dev/null
        json_row "$item" "$pr" escalated false sweep 0 0 \
          "sweep: recorded run '$deferred_run' is unknown to the kernel; refusing to attribute this PR to a different run - needs a human" ok \
          >> "$SCORECARD"
        continue
      fi
    else
      _kernel_adopt_run "$item" "$REPO" "${sha:-0000000000000000000000000000000000000000}" \
        "$RECOVERY_REVIEWER" >/dev/null
    fi
    st=$(gh pr view "$pr" --repo "$REPO" --json state -q '.state' 2>/dev/null)
    case "$st" in
      MERGED|CLOSED)
        echo "[batch:sweep] $item: PR #$pr already $st -> skip" >&2
        continue ;;
    esac
    # The cross-review PASS covers a SPECIFIC head sha. Auto-merge ONLY when we can
    # PROVE the current head is still that reviewed head. Fail CLOSED: a missing
    # recorded sha, a failed head lookup, or a changed head -> escalate, never
    # re-stamp bircher/cross-review on unreviewed code.
    cur=$(gh pr view "$pr" --repo "$REPO" --json headRefOid -q '.headRefOid' 2>/dev/null)
    if [ -z "$sha" ] || [ -z "$cur" ]; then
      echo "[batch:sweep] $item: PR #$pr cannot verify reviewed head (recorded='${sha}' current='${cur}') -> escalate for human" >&2
      json_row "$item" "$pr" ready false sweep 0 0 "sweep: unverifiable reviewed head (recorded='${sha:-?}' current='${cur:-?}'); human merge" ok >> "$SCORECARD"
      continue
    fi
    if [ "$cur" != "$sha" ]; then
      echo "[batch:sweep] $item: PR #$pr head changed since review ($sha -> $cur) -> escalate for re-review" >&2
      json_row "$item" "$pr" ready false sweep 0 0 "sweep: head changed since review ($sha -> $cur); needs re-review (human)" ok >> "$SCORECARD"
      continue
    fi
    # Head PROVEN == reviewed head. But merging a BEHIND PR needs update-branch, which
    # REWRITES the head -> the PASS no longer covers the merged sha literally. Re-stamp
    # ONLY when that update provably left the PR's own delta untouched (#51); every
    # other case escalates exactly as it did before that check existed.
    mss=$(gh pr view "$pr" --repo "$REPO" --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
    if [ "$mss" = "BEHIND" ]; then
      echo "[batch:sweep] $item: PR #$pr BEHIND main -> update-branch" >&2
      # expected_head_sha makes GitHub REFUSE the update if the head moved since we
      # verified it, so a concurrent push cannot be silently folded into the update.
      _effect ref_update "update-branch:$pr:$sha" - gh api "repos/$REPO/pulls/$pr/update-branch" -X PUT -f expected_head_sha="$sha" >/dev/null 2>&1 \
        || echo "[batch:sweep] WARN $item: update-branch call failed (head moved, already updating, or up to date)" >&2
      if _restamp_if_delta_unchanged "$item" "$pr" "$sha"; then
        # Pin every downstream step (the status, --match-head-commit) to the head we
        # just PROVED carries the reviewed change, never to whatever is current.
        sha="$RESTAMPED_HEAD"
        mss=$(gh pr view "$pr" --repo "$REPO" --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
      else
        json_row "$item" "$pr" ready false sweep 0 0 "sweep: PR was BEHIND; update-branched, needs re-review before merge (human)" ok >> "$SCORECARD"
        continue
      fi
    fi
    # Allow-list: only ATTEMPT the merge from states we can vouch are safe. CLEAN /
    # HAS_HOOKS are healthy; BLOCKED is the NORMAL deferred state (missing our
    # bircher/cross-review status, which merge_ready_pr posts -- other required checks
    # still gate the real merge). Anything else -- UNSTABLE (a check went red since the
    # PASS), DIRTY, DRAFT, UNKNOWN, empty -- is unverifiable/unsafe -> fail closed.
    case "$mss" in
      CLEAN|HAS_HOOKS|BLOCKED) : ;;
      *)
        echo "[batch:sweep] $item: PR #$pr mergeStateStatus '${mss:-unknown}' not safe to auto-merge -> escalate for human" >&2
        json_row "$item" "$pr" ready false sweep 0 0 "sweep: mergeStateStatus '${mss:-unknown}' not safe to auto-merge; human merge" ok >> "$SCORECARD"
        continue ;;
    esac
    # Up to date AND head-verified: merging lands exactly the reviewed code.
    echo "[batch:sweep] $item: retrying merge of verified ready PR #$pr" >&2
    merge_ready_pr "$item" "$pr" "$sha"; mrc=$?
    case "$mrc:$MERGE_NOTE" in
      0:|0:merged*)
        echo "[batch:sweep] $item: PR #$pr merged by sweep" >&2
        json_row "$item" "$pr" ready true sweep 0 0 "merged by end-of-run reconciliation sweep" ok >> "$SCORECARD"
        # #3 safety-net: close an issue-backed PR's issue if GitHub's `Closes #N`
        # auto-close missed (run_item's close ran while this PR was still open -> no-op).
        _ensure_issue_closed "$issue" "$pr" ;;
      2:*)
        # rc 2 = merge_ready_pr merged then main CI went red (reverted or halted).
        # STOP the sweep: do NOT merge further PRs onto a possibly-red main -- the
        # same halt safety the main item loop honors on an rc-2 merge.
        echo "[batch:sweep] !!!! $item: PR #$pr sweep merge triggered a main-CI HALT (rc=2; $MERGE_NOTE) -> stopping sweep !!!!" >&2
        # #71: an unreviewed merge is not a `ready` row. The sweep does not close issues
        # on this path, but the scorecard is what a human reads afterwards.
        if [ "${MERGE_UNREVIEWED:-0}" = 1 ]; then
          json_row "$item" "$pr" escalated false sweep 0 0 "sweep merge halted (rc=2): ${MERGE_NOTE:-unknown}; $MERGE_UNREVIEWED_NOTE" ok >> "$SCORECARD"
        else
          json_row "$item" "$pr" ready false sweep 0 0 "sweep merge halted the run (rc=2): ${MERGE_NOTE:-unknown}" ok >> "$SCORECARD"
        fi
        break ;;
      *)
        echo "[batch:sweep] $item: PR #$pr STILL not merged (rc=$mrc; $MERGE_NOTE) -> escalate for human" >&2
        json_row "$item" "$pr" ready false sweep 0 0 "sweep could not merge (rc=$mrc): ${MERGE_NOTE:-unknown}" ok >> "$SCORECARD" ;;
    esac
  done
}

# recover_pr_cmd <code> <pr> [reviewer_vendor] -> STANDALONE recovery of ONE
# orphaned PR: bring a BEHIND branch up to date, run the genuine cross-vendor
# recovery review, and (on PASS + green CI) merge it -- no coordinator session,
# no re-implementation. First-class entry point for the failure the 2026-07-14
# overnight run exposed: a GitHub-infra CI flake outlasted recovery's reruns and
# buried 3 CI-green PRs as `escalated`, orphaning a 4th. A plain re-queue does
# NOT fix that class: the coordinator's opening step would no-op ("a sibling PR
# already did it" -> leaves the PR unmerged) or re-implement it (duplicate PR);
# this adopts and lands the EXISTING PR. The runner posts
# bircher/cross-review=success ONLY after a real review PASS, and merges WITHOUT
# --admin (the branch is up to date by then) -- so no self-approval and no
# branch-protection bypass. rc mirrors merge_ready_pr (0 = merged or left open
# with a marker; 2 = merged but main-CI HALT). Reviewer defaults to claude_code
# (the overnight implementer was codex; the reviewer must be the opposite vendor).
# publish_cmd <code> <worktree> <branch> [claimed_oid] -- publish an
# implementer's nominated commit from the KERNEL's credential domain.
#
# The implementer cannot do any of this: its egress denies git-receive-pack and
# its API rules are GET-only. This is the other half of the sentence in its own
# prompt, and until it existed that prompt described a capability nothing
# provided.
publish_cmd() {
  local code="${1:?usage: --publish <code> <worktree> <branch> [oid]}"
  local wt="${2:?usage: --publish <code> <worktree> <branch> [oid]}"
  local branch="${3:?usage: --publish <code> <worktree> <branch> [oid]}"
  local claimed="${4:-}"

  # THE PUSH AND THE PR MUST NAME THE SAME REPOSITORY.
  #
  # `git push origin` resolves through the WORKTREE's remote; `gh pr create
  # --repo "$REPO"` names one explicitly. Nothing made them agree, and they
  # silently did not: run-queue.sh re-derives REPO from BIRCHER_REPO, so a
  # caller that exported REPO alone pushed to one repo and asked GitHub to open
  # the PR on another. It surfaced as "Head ref must be a branch" -- an error
  # about the head ref, for a fault in the repository -- and cost an entire
  # misdiagnosis: a propagation delay that does not exist, complete with a
  # bounded poll to wait for it. The only reason it was not worse is that the
  # branch did not exist on the other repository.
  #
  # Checked BEFORE any effect, so a mismatch costs nothing and is refused
  # rather than journalled.
  local origin_url origin_slug
  origin_url=$(git -C "$wt" remote get-url origin 2>/dev/null) || origin_url=""
  origin_slug=$(printf '%s' "$origin_url" | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://[^/]+/)##; s#\.git$##')
  if [ "$origin_slug" != "$REPO" ]; then
    echo "[batch:publish] $code: the worktree pushes to '${origin_slug:-<no origin>}' but the PR would open on '$REPO' -- refusing to publish to one repository and announce it on another" >&2
    return 1
  fi

  # ASK BEFORE ADOPTING. `_kernel_adopt_run` mints when it finds nothing, so
  # routing this question through it would answer "was this dispatched?" by
  # dispatching it. Refusing work the kernel never started is the correct
  # answer, and it must not have a side effect.
  local run_id; run_id=$(_kernel_find_run "$code")
  if [ -z "$run_id" ]; then
    echo "[batch:publish] $code: no run behind this code -- the kernel did not dispatch this work and cannot vouch for where it came from" >&2
    return 1
  fi

  # The base the kernel RECORDED. `git rev-parse HEAD` in the implementer's
  # worktree is the TIP of the work, not the base it started from; checking
  # provenance against it would make every nomination trivially valid.
  local recorded; recorded=$(_kernel_run_base "$run_id")
  if [ -z "$recorded" ]; then
    echo "[batch:publish] $code: run $run_id has no recorded base -- nothing to check provenance against" >&2
    return 1
  fi

  _kernel_adopt_run "$code" "$REPO" "$recorded" codex implementer >/dev/null

  local oid; oid=$(_kernel_verify_nomination "${BIRCHER_RUN_ID:-}" "$wt" "$branch" "$claimed")
  if [ -z "$oid" ]; then
    echo "[batch:publish] $code: the kernel refused the nomination -> publishing nothing" >&2
    return 1
  fi
  echo "[batch:publish] $code: kernel will publish $oid on '$branch'" >&2

  # BOTH effects run IN THE NOMINATED WORKTREE, not the coordinator's cwd.
  #
  # Found live, not by reading: run from the coordinator's checkout, `git push
  # origin` resolved `origin` to the BIRCHER repo -- a different remote from
  # the one the work is on -- and the oid was not present there at all. The
  # kernel journalled the failure as `effect_uncertain` and halted the run.
  #
  # `git -C "$wt" push` is NOT the fix: the argv contract stops a signature at
  # the first flag, so `git -C <dir> push` reads as `git` and is refused. That
  # refusal is correct and must stay -- `-C` is precisely the redirect the
  # contract exists to deny, and widening it to make this call work would sell
  # the boundary for a convenience.
  ( cd "$wt" && _effect ref_update "publish:$code:$oid" "$BIRCHER_NET_TIMEOUT" \
      git push origin "$oid:refs/heads/$branch" ) || {
      echo "[batch:publish] $code: push refused or failed" >&2; return 1; }

  ( cd "$wt" && _effect pull_request "publish-pr:$code:$oid" "$BIRCHER_NET_TIMEOUT" \
      gh pr create --repo "$REPO" --head "$branch" --base main \
        --title "$code" --body "Published by the Bircher kernel from $oid." ) \
    || { echo "[batch:publish] $code: PR creation refused or failed" >&2; return 1; }
}

recover_pr_cmd() {
  local code="${1:?usage: --recover-pr <code> <pr> [reviewer_vendor]}"
  local pr="${2:?usage: --recover-pr <code> <pr> [reviewer_vendor]}"
  RECOVERY_REVIEWER="${3:-claude_code}"
  local item="recover-$code"
  echo "[batch:recover-pr] $code: adopting PR #$pr (reviewer=$RECOVERY_REVIEWER)" >&2
  # Adopt the item's kernel run BEFORE the first effect below. Without this
  # BIRCHER_RUN_ID/BIRCHER_GENERATION are unset here -- they are assigned only
  # inside run_item -- so in kernel mode every effect on this path aborts on
  # `${VAR:?}` and is swallowed by its own redirect. A live run in that state
  # left an empty kernel database, posted no cross-review status and no comment,
  # and still reported success.
  local _rec_base; _rec_base=$(git -C "$WORKDIR" rev-parse HEAD 2>/dev/null)
  : "${_rec_base:=0000000000000000000000000000000000000000}"
  # Adopt as the IMPLEMENTER: the first kernel command this path issues is
  # record_implementation_output, which refuses any other role. The reviewer
  # dispatch happens below, at the role change, before the verdict.
  local _rec_impl; _rec_impl=$([ "$RECOVERY_REVIEWER" = codex ] && printf claude_code || printf codex)
  _kernel_adopt_run "$code" "$REPO" "$_rec_base" "$_rec_impl" implementer >/dev/null
  echo "[batch:recover-pr] $code: kernel run=${BIRCHER_RUN_ID:-<none>} generation=${BIRCHER_GENERATION:-<none>}" >&2

  # RESOLVE A HALT FIRST. An uncertain effect halts its run and `perform`
  # refuses everything after it, so a halted run adopted here would have every
  # subsequent effect declined -- correctly, and with no way forward. This is
  # not hypothetical: a live merge on muesli came back uncertain because the
  # coordinator raced its own review-gate, and the run could not be advanced by
  # any path the coordinator had.
  #
  # The resolution is an OBSERVATION this code makes and the kernel cannot: it
  # asks GitHub whether the PR actually merged. That answer is recorded with
  # the version it was derived from, so a run that moved meanwhile refuses a
  # conclusion drawn about a different state.
  local _pend; _pend=$(_kernel_pending "${BIRCHER_RUN_ID:-}")
  if printf '%s' "$_pend" | grep -q '"halted": *true'; then
    local _merged_at _resolution _pver _pkeys
    _merged_at=$(_net_run "$BIRCHER_NET_TIMEOUT" gh pr view "$pr" --repo "$REPO" \
                   --json mergedAt -q '.mergedAt' 2>/dev/null)
    if [ -n "$_merged_at" ] && [ "$_merged_at" != "null" ]; then
      _resolution="observed: PR #$pr IS merged (mergedAt=$_merged_at)"
    else
      _resolution="observed: PR #$pr is NOT merged; the attempt did not land"
    fi
    # ONLY the effects this observation actually speaks to. The first version
    # applied one PR-merge observation to EVERY pending key regardless of
    # class, so an uncertain comment, status_check or ref_update was "resolved"
    # by evidence that said nothing about it -- an observation about one PR
    # presented as an observation about each effect, which is the exact shape
    # this design exists to refuse. Anything else stays unresolved and the run
    # stays halted, which is the truthful state: nobody has looked at it.
    # The key must name THIS pr, not merely be a merge. Filtering by class
    # alone closed half the defect: an adopted run can hold an uncertain merge
    # for a DIFFERENT PR -- adoption still picks the newest run for an item
    # code without linking it to the PR on the command line -- and this
    # observation is about `$pr` and nothing else. Merge keys are
    # `merge:<pr>:<head>[:g<n>]`, so the prefix is the check.
    _pkeys=$(K_PR="$pr" printf '%s' "$_pend" | K_PR="$pr" "${BIRCHER_PY:-python3}" -c 'import json,os,sys
want = "merge:" + os.environ.get("K_PR","") + ":"
for e in json.load(sys.stdin)["pending"]:
    if e.get("effect_class") == "merge" and e.get("idempotency_key","").startswith(want):
        print(e["idempotency_key"])' 2>/dev/null)
    local _unspoken
    _unspoken=$(printf '%s' "$_pend" | K_PR="$pr" "${BIRCHER_PY:-python3}" -c 'import json,sys
import os
want = "merge:" + os.environ.get("K_PR","") + ":"
for e in json.load(sys.stdin)["pending"]:
    k = e.get("idempotency_key","")
    if e.get("effect_class") != "merge" or not k.startswith(want):
        print(e["effect_class"], k)' 2>/dev/null)
    local _pver
    _pver=$(printf '%s' "$_pend" | "${BIRCHER_PY:-python3}" -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null)
    echo "[batch:recover-pr] $code: run is HALTED -> $_resolution" >&2
    # ONE KEY PER INVOCATION. Not a loop with a local increment, and not a
    # loop with a re-read: both absorb an unrelated writer's version change and
    # apply an observation derived from state that has since moved.
    #
    # The local increment recreated the defect it replaced. `_kernel_reconcile`
    # is advisory and returns 0 whatever happened, so after key 1's CAS
    # correctly FAILS at version V -- because a foreign writer moved the run to
    # V+1 -- the loop still advanced its expectation to V+1, and key 2 then
    # reconciled successfully against that foreign version. The increment is
    # only valid after a KNOWN-successful reconciliation, and this interface
    # cannot establish one.
    #
    # So: resolve one, then stop and report. Each invocation derives its
    # expected version from its own fresh observation, which is the only
    # version it can honestly claim to have observed. Multiple uncertain merges
    # on one PR are rare; when they happen the remainder are named and the halt
    # stands, which is the fail-closed direction.
    # ALL of them, in one call. The kernel resolves them under a single CAS in
    # a single transaction, so the "one key per invocation" restriction -- which
    # was correct but left a run with several uncertain merges halted forever,
    # with nothing owning the follow-up -- is no longer needed.
    local _karr=() _k
    while IFS= read -r _k; do
      [ -n "$_k" ] && _karr+=("$_k")
    done <<EOF
$_pkeys
EOF
    if [ "${#_karr[@]}" -gt 0 ]; then
      echo "[batch:recover-pr] $code: attempting reconcile of ${#_karr[@]} key(s) at version ${_pver:-?}" >&2
      _kernel_reconcile "$BIRCHER_RUN_ID" "$_resolution" "${_pver:-0}" "${_karr[@]}"
    fi
    # What is ACTUALLY left, read back rather than inferred from the calls
    # above having returned 0 -- which they always do.
    local _still
    _still=$(_kernel_pending "$BIRCHER_RUN_ID" | "${BIRCHER_PY:-python3}" -c 'import json,sys
d = json.load(sys.stdin)
print("halted" if d.get("halted") else "clear", len(d.get("pending") or []))' 2>/dev/null)
    echo "[batch:recover-pr] $code: after reconciliation the kernel reports: ${_still:-<no answer>}" >&2
    # CONSULTED, not just printed. A run still halted here will have every
    # subsequent effect refused, so driving the lifecycle into it produces a
    # run of guaranteed refusals and a merge that cannot happen. Stop and say
    # so. An unreadable answer is not treated as "clear".
    case "$_still" in
      clear\ *) : ;;
      *) echo "[batch:recover-pr] $code: still halted after reconciliation -> not driving further; needs a human" >&2
         _rp_drive=0 ;;
    esac

    if [ -n "${_unspoken//[[:space:]]/}" ]; then
      echo "[batch:recover-pr] $code: NOT reconciled -- this observation says nothing about them, so they need a human:" >&2
      printf '%s\n' "$_unspoken" | while IFS= read -r _u; do
        [ -n "$_u" ] && echo "[batch:recover-pr] $code:   $_u" >&2
      done
    fi
  fi
  # Strict branch protection blocks a BEHIND branch from merging. Normal runs
  # dodge this by creating PRs sequentially off fresh main; a stale orphan must
  # be brought up to date first. update-branch re-triggers the required checks on
  # the new head, which observe_outcome's CI-wait then settles before
  # the review -- so the subsequent (non-admin) merge sees a green, up-to-date PR.
  local mss
  mss=$(gh pr view "$pr" --repo "$REPO" --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
  if [ "$mss" = "BEHIND" ]; then
    echo "[batch:recover-pr] $code: PR #$pr is BEHIND main -> update-branch" >&2
    _effect ref_update "update-branch:$pr" - gh api "repos/$REPO/pulls/$pr/update-branch" -X PUT >/dev/null 2>&1 \
      || echo "[batch:recover-pr] WARN $code: update-branch call failed (already updating or up to date)" >&2
  fi
  # Operator identity for the (rare) revert-worktree path inside merge_ready_pr.
  _install_work_git_config "$WORKDIR" >/dev/null 2>&1 || true
  local rec r_outcome r_review r_note r_sha r_ci r_settled_pr
  # An EMPTY tuple is a CRASH, not a verdict. observe_outcome has a
  # single exit and always emits five fields, so no output means it died before
  # reaching that line -- and `rec=$(...)` swallows the death into an empty
  # string. Parsed straight, that reads as outcome="" and the caller reports
  # "NOT ready", which is a benign-looking sentence for "the recovery
  # crashed". Seen once on the smoke repo (s01, review -> outcome= review=
  # note= head=) and not reproducible since; the specific crash is unknown and
  # the misreading is the part worth making impossible.
  rec=$(observe_outcome "$item" "$code" "$pr")
  if [ -z "${rec//[[:space:]]/}" ]; then
    echo "[batch:recover-pr] $code: recovery produced NO tuple -> it failed; PR left untouched for a human" >&2
    return 1
  fi
  if ! _derived_width_ok "$rec"; then
    echo "[batch:recover-pr] $code: recovery returned a malformed tuple -> PR left untouched for a human" >&2
    return 1
  fi
  # THE EIGHTH FIELD IS USED HERE TOO. An earlier cut named it and threw it
  # away, which left recovery able to reproduce the exact defect the field was
  # added to fix: `observe_outcome` runs the same `_settle_pr`, so recovery can
  # review and comment a sibling PR and then authorize and merge the stale one
  # it was invoked with.
  IFS='|' read -r r_outcome r_review r_note r_sha r_ci _ _ r_settled_pr <<EOF
$rec
EOF
  if [ -n "${r_settled_pr:-}" ] && [ "$r_settled_pr" != "$pr" ]; then
    echo "[batch:recover-pr] $code: derivation settled on PR #$r_settled_pr (was #$pr) -> adopting" >&2
    pr="$r_settled_pr"
  fi
  echo "[batch:recover-pr] $code: review -> outcome=$r_outcome review=$r_review note=$r_note head=${r_sha:0:7}" >&2

  # Only drive the lifecycle from a state that can still accept it. A run
  # adopted at `merge_requested` has already recorded its output, CI and
  # verdict, and re-driving them earns four refusals that are individually
  # correct and collectively noise -- they sit in the shadow report next to
  # refusals that mean something, which is how a report stops being read.
  # WHAT THE HISTORY SAYS, before what the state name says. The gate below is
  # a state-name check, and the recovery table exists because that is not
  # enough -- most of all for the merge rows, where the state name cannot tell
  # "authorised and never attempted" from "already merged".
  # NO CONTEXT HASH IS PASSED, deliberately, and the consequence is stated
  # because it is not obvious: `decide` skips the staleness comparison when it
  # has no binding, so the `re_review` row -- "the latest acceptance binds a
  # superseded output" -- cannot fire from here. The context bundle hash is
  # minted inside the drive branch below, after the PR the derivation settles
  # on is known, so it does not exist yet at this point.
  #
  # That is a loss of LEGIBILITY, not of safety. A stale approval is refused by
  # the kernel at `request_merge` -- "merge binds an artifact that is not this
  # run's current output" -- which is proven by
  # `test_round_ONES_artifact_cannot_merge_after_a_repair`. Feeding the binding
  # here would move that refusal earlier and give it a better message; it would
  # not change what merges. Wiring it means minting the context artifact before
  # the derivation, which changes what the binding IS, so it is a design change
  # rather than an omission to patch.
  local _rp_act
  _rp_act=$(_recovery_action "${BIRCHER_RUN_ID:-}" "${BIRCHER_RUN_BASE:-$_rec_base}" "")
  [ -n "$_rp_act" ] && echo "[batch:recover-pr] $code: journal says ${_rp_act%%|*} -- ${_rp_act#*|}" >&2
  local _rp_state="" _rp_raw
  _rp_raw=$(_kernel_pending "${BIRCHER_RUN_ID:-}")
  [ -n "${_rp_raw//[[:space:]]/}" ] && _rp_state=$(printf '%s' "$_rp_raw" \
    | "${BIRCHER_PY:-python3}" -c 'import json,sys; print(json.load(sys.stdin).get("state") or "")' 2>/dev/null)
  # A FLAG, not $r_sha. Blanking r_sha would skip the drive and also unpin the
  # merge below, which reads the same variable to pass --match-head-commit --
  # trading four harmless refusals for an unpinned merge.
  local _rp_drive=1
  # THREE CAUSES, THREE MESSAGES. This used to collapse "the run is past these
  # stages", "there is no such run" and "the kernel is unreachable" into one
  # branch that printed the first -- so an unreachable kernel was reported as a
  # run that had progressed: a false claim about the source, in code added to
  # fix a different instance of exactly that. Only the first is a reason to
  # skip quietly.
  if [ -z "${_rp_raw//[[:space:]]/}" ]; then
    # `pending` returns nothing for BOTH an unreachable kernel and a run the
    # kernel does not know -- `run_state` raises on a missing run, so the CLI
    # emits no JSON either way and the two cannot be told apart from here. An
    # earlier version claimed to distinguish them, reported this case as
    # "unreachable", and left the drive ENABLED so recovery proceeded blind
    # against a run that might not exist. Both causes are now reported as one
    # unknown, honestly, and both stop the drive: driving a lifecycle at a run
    # whose state is unknown is how the four-refusal noise started.
    echo "[batch:recover-pr] $code: WARN cannot read run state (kernel unreachable, or it knows no run '${BIRCHER_RUN_ID:-}') -- not driving the lifecycle" >&2
    _rp_drive=0
  else
    case "$_rp_state" in
      queued|specified|planned|implementing|reviewing) : ;;
      *)  echo "[batch:recover-pr] $code: run is at '$_rp_state' -- past the lifecycle stages; not re-driving them" >&2
          _rp_drive=0 ;;
    esac
  fi

  # Drive the kernel lifecycle, exactly as run_item's recovery branch does.
  # Adopting a run gave this path a valid generation; it did not give the
  # kernel any EVIDENCE, so the merge gate had no recorded output, CI
  # observation or verdict to authorize against and refused the merge -- a live
  # probe left the run at `queued` with only a comment and a status_check
  # journalled. Adoption was necessary and not sufficient.
  #
  # The context blob names the target rather than reusing the output hash: the
  # binding's four fields are compared as a tuple, and two of them being the
  # same value by accident makes a mismatch harder to read, not easier.
  if [ -n "$r_sha" ] && [ "$_rp_drive" = 1 ]; then
    local _rp_out _rp_ctx
    _rp_out=$(_kernel_record_output "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" \
      "recovered: outcome=$r_outcome review=$r_review head=$r_sha note=$r_note")
    _rp_ctx=$(_kernel_put_artifact "recover-pr context: repo=$REPO pr=$pr code=$code")
    _kernel_record_ci "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "${r_ci:-na}" "$r_sha"
    BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer)
    export BIRCHER_GENERATION
    _kernel_record_review "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$r_review" \
      "$_rp_out" "${BIRCHER_RUN_BASE:-$_rec_base}" "$_rp_ctx"
    _kernel_request_merge "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$pr" "$REPO" "$r_sha" \
      "$_rp_out" "${BIRCHER_RUN_BASE:-$_rec_base}" "$_rp_ctx"
  fi

  if [ "$r_outcome" = "ready" ] && _recovery_forbids_merge "$_rp_act"; then
    # The derivation says the PR is ready, and the JOURNAL says a merge has
    # already happened or may have. The derivation cannot know that: it reads
    # the repository, and a PR whose merge is uncertain still reads as open.
    echo "[batch:recover-pr] $code: derivation says ready, but the journal says ${_rp_act%%|*} -> NOT merging (${_rp_act#*|})" >&2
    return 0
  fi
  if [ "$r_outcome" = "ready" ]; then
    # #66: this was the one production caller that merged UNPINNED. A recovery
    # PASS with no captured head cannot be merged automatically -- the same
    # fail-closed rule the marker path already applies.
    if [ -z "$r_sha" ]; then
      echo "[batch:recover-pr] $code: ready but no reviewed head captured -> NOT merging; left for a human" >&2
      return 0
    fi
    merge_ready_pr "$item" "$pr" "$r_sha"; local mrc=$?
    echo "[batch:recover-pr] $code: merge_ready_pr rc=$mrc${MERGE_NOTE:+ note=\"$MERGE_NOTE\"}${MERGE_UNREVIEWED_NOTE:+ UNREVIEWED=\"$MERGE_UNREVIEWED_NOTE\"}" >&2

    # WRITE BACK TO THE ISSUE. This path had none, so a recovered item left its
    # issue carrying `bircher:running` after the PR had merged -- the label
    # means "being worked" and was saying so about finished work. It needed
    # clearing by hand after tonight's muesli merge, which is the sort of
    # residue that turns a label into noise nobody trusts.
    #
    # The issue comes from the PR's own closing references rather than a queue
    # item, because this path has no item: --recover-pr adopts a PR and may
    # never have seen the prompt that created it.
    if [ "$mrc" = 0 ]; then
      local _rp_iss
      _rp_iss=$(_net_run "$BIRCHER_NET_TIMEOUT" gh pr view "$pr" --repo "$REPO" \
                  --json closingIssuesReferences \
                  -q '.closingIssuesReferences[]?.number' 2>/dev/null | head -1)
      if [ -n "$_rp_iss" ]; then
        _issue_writeback "$_rp_iss" "ready" "$pr" "$r_review" "" ""
        echo "[batch:recover-pr] $code: wrote back to issue #$_rp_iss" >&2
      fi
    fi
    return $mrc
  fi
  echo "[batch:recover-pr] $code: NOT ready (outcome=$r_outcome) -> PR left open with marker for human" >&2
  return 0
}

# _recovery_review_prompt <pr> -> the read-only reviewer sub-agent input.
# Mirrors the cross-review skill's reviewer template: fetch the PR branch,
# read whole files, run gates each with an inline PATH export, end with an
# exact VERDICT line (findings above it).
_recovery_review_prompt() {
  local pr="$1" sha="${2:-}"
  # #66: the worktree is created at the EXACT captured commit, not at FETCH_HEAD.
  # `pull/N/head` is a MOVING ref: a push between capture and the reviewer's fetch
  # would have it read one commit while the merge pinned another, and a later
  # force-push back to the captured sha would then merge code the reviewer never
  # read. Checking out the sha removes the ambiguity mechanically rather than by
  # asking the reviewer to verify it. If the sha is no longer reachable from the
  # ref, the checkout fails and so does the review -- which is the correct
  # outcome, not a regression.
  local _co="FETCH_HEAD"; [ -n "$sha" ] && _co="$sha"
  # A UNIQUE worktree per REVIEWER. The `-oob` suffix does the work: both
  # reviewers review the same commit, so a sha-derived nonce alone would still
  # collide. Both wrote `/tmp/review-<PR>`;
  # on muesli #745 the second died on "already exists" and, with only PASS
  # and FAIL on offer, reported FAIL for a PR it had not read.
  local _nonce="${_co:0:8}"; [ -n "$_nonce" ] || _nonce=head
  cat <<EOF
Review PR #$pr in $REPO as an INDEPENDENT, READ-ONLY reviewer. Do NOT edit, commit, or open/update any PR.
First: export PATH=/root/bin:\$PATH; git fetch origin pull/$pr/head; git worktree add --detach /tmp/review-$pr-$_nonce-oob $_co; cd /tmp/review-$pr-$_nonce-oob.
You are reviewing EXACTLY commit $_co. If that checkout fails, STOP and report it -- do not review a different commit.
READ the changed files AND enough surrounding code to verify correctness -- do NOT judge from the diff alone.
Run the gates you can, EACH as ONE command prefixed with 'export PATH=/root/bin:\$PATH &&' (e.g. 'export PATH=/root/bin:\$PATH && go build ./...', '... && go vet ./...', client '... && npm run typecheck' / '... && npx vitest run', plugin '... && pytest'); DB-backed 'go test' needs a DB the runner lacks, so for THOSE you must not simply accept a green check.
A green check is a CLAIM, not evidence: for any gate you could not run yourself, open the run log (\`gh pr checks $pr\` to find the run, then \`gh run view <run-id> --log\`) and RECONCILE it with the check's conclusion -- a step can execute, report failing tests, and STILL be reported green if its exit code was swallowed (\`|| true\`, continue-on-error, a wrapper that always exits 0). Quote the log line showing test counts or the failure, and NAME every gate you delegated rather than ran. If you cannot reach the log, say so and treat that gate as UNVERIFIED -- do not report it as passing. (muesli #705 shipped a CI gate that reported success while tests failed; it passed review because the reviewer was told to trust the check.)
If the change acquires a resource that must be released -- a capture device, stream, handle, lock or subscription -- verify its FAILURE paths are tested, not just the happy path; a missing release-on-error test is a blocking finding. (muesli #666 left a microphone recording when a capture start failed.) Keep that scope narrow: do not treat every state change as in scope.
Report blocking / non-blocking / suggestion findings, then a FINAL LINE that is EXACTLY 'VERDICT: PASS', 'VERDICT: FAIL', or 'VERDICT: BLOCKED'.
Use BLOCKED, and ONLY BLOCKED, when you could not review at all -- the checkout failed, the tooling was unavailable, the commit was unreachable. BLOCKED means "I formed no opinion"; FAIL means "I reviewed this and it must not merge". They are routed differently and confusing them is expensive: a reviewer that could not check out its worktree once emitted FAIL, and the run recorded a code rejection for a PR nobody had read.
Put findings BEFORE the verdict so the verdict is the last line even if output is long.
EOF
}

# _reconcile_item_pr <code> <tracked_pr> -> the open PR number to act on.
# A coordinator that opens a fresh branch/PR for a CI-red retry (run #20: item
# i141 left #178 red + #179 green, both open) leaves run-queue tracking only the
# FIRST PR it discovered, so recovery buried a green fix as failed. Re-scan every
# open PR whose head branch carries the item code; if a DIFFERENT one is CI-green
# adopt it and close the non-adopted siblings as superseded (so they do not
# orphan). Prints the tracked pr unchanged when there is nothing better to pick.
_reconcile_item_pr() {
  local code="$1" tracked="$2" matches count m green=""
  local chosen="$tracked"
  [ -n "$code" ] || { echo "$tracked"; return; }
  matches=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
    -q "$(_branch_code_filter "$code") | .[].number" 2>/dev/null)
  count=$(printf '%s\n' "$matches" | grep -c .)
  [ "${count:-0}" -le 1 ] && { echo "$tracked"; return; }
  for m in $matches; do
    if [ "$(_normalize_ci "$(_keep_blocking_checks "$(gh pr checks "$m" --repo "$REPO" --json name,bucket -q '.[] | "\(.name)|\(.bucket)"' 2>/dev/null)" "$(_required_contexts)")")" = green ]; then
      green="$m"; break
    fi
  done
  # Only reshuffle when a CI-green sibling exists; if all are red leave them for
  # the normal failed/re-queue path (never close a PR we did not supersede).
  [ -z "$green" ] && { echo "$tracked"; return; }
  chosen="$green"
  for m in $matches; do
    [ "$m" = "$chosen" ] && continue
    _effect pull_request "close-pr:$m" - gh pr close "$m" --repo "$REPO" \
      --comment "Superseded by #$chosen (Bircher recovery: item $code opened multiple PRs after a CI-red retry; adopting the CI-green one)." >/dev/null 2>&1 || true
  done
  echo "$chosen"
}

# observe_outcome <item> <code> <pr> [issue]
# Called when a coordinator ended (idle-reaper ~30 min) before posting its
# marker. Derives a truthful outcome from the PR and, for a CI-green PR, an
# out-of-band cross-vendor review. Posts a self-describing
# marker to the PR and prints "outcome|review|note" for the scorecard row.
# `issue` (optional) enables the issue-linkage PR fallback when both signal and
# branch-code discovery miss (run #24 a06-vs-i230); standalone --recover-pr omits it.
# _derive_budget -> seconds the Python derivation may take.
#
# DERIVED, not a constant. A fixed 1800 was SHORTER THAN THE WORK IT BOUNDED:
# the derivation may wait BIRCHER_CI_WAIT (1500) for CI, then re-run up to
# BIRCHER_CI_RERUN_MAX (4) times, each waiting BIRCHER_CI_RERUN_WAIT (900) plus
# a settle -- 5180s of legitimate work inside a 1800s cap. A healthy infra
# recovery was killed mid-rerun and reported as a crashed derivation, and the
# three knobs above accepted values that could never be spent.
#
# The bash this replaced had NO whole-derivation cap at all, so a budget that
# cannot cut a legitimate run short is also the faithful behaviour.
# _ci_policy -> "<wait> <rerun_max> <rerun_wait>", validated ONCE.
#
# RERUN_MAX DEFAULTS TO 2, NOT 4, BECAUSE A DEFAULT MUST BE DELIVERABLE.
# With 4 the advertised budget is 1500 + 4*920 + 120 = 5300s, and a single
# bounded call tops out at 3600s (`_net_run` clamps, and `_clamp_int` returns
# its 300s DEFAULT above the ceiling rather than truncating). So the third and
# fourth reruns could never complete: the enclosing process was killed and the
# item escalated, while the knob said four were available.
#
# 2 gives 1500 + 2*920 + 120 = 3460s, which fits. An operator may still set 4
# or more -- the range is 0-20 and the warning below fires when the choice
# cannot fit, which is the honest treatment of an EXPLICIT decision as opposed
# to a shipped default nobody chose.
#
# Not evidence that 3-4 reruns are useless: 26 scorecard rows contain exactly
# one rerun, which is too few to conclude anything about a rare failure mode.
# The argument is only that a default must be one the runner can honour.
#
# One resolution point, because two of them disagree. `_derive_budget` clamped
# these while the Python CLI re-parsed the RAW environment with a bare `int()`:
# `BIRCHER_CI_RERUN_MAX=abc` gave bash a budget computed from 4 and gave Python
# a ValueError, which `observe_outcome` reads as an empty tuple -- so a single
# malformed operator setting escalated EVERY item while the shell believed it
# had defaulted safely. A value above 20 diverged the other way: Python would
# attempt more reruns than the budget bounding it allowed.
#
# The clamped values are passed to the coordinator explicitly, so there is no
# second interpretation to drift.
_ci_policy() {
  printf '%s %s %s' \
    "$(_clamp_int "${BIRCHER_CI_WAIT:-1500}" 1500 1 7200)" \
    "$(_clamp_int "${BIRCHER_CI_RERUN_MAX:-2}" 2 0 20)" \
    "$(_clamp_int "${BIRCHER_CI_RERUN_WAIT:-900}" 900 1 7200)"
}

_derive_budget() {
  local w r rw floor
  # The largest cap `_net_run` will honour. Above it `_clamp_int` returns its
  # 300s DEFAULT rather than truncating, so this is a cliff, not a ceiling.
  local ceiling=3600
  read -r w r rw <<<"$(_ci_policy)"
  # +20s settle per rerun (coordinator.ci.rerun_and_wait), +120s slack for
  # process start, the review dispatch and the effect calls.
  floor=$(( w + r * (rw + 20) + 120 ))
  # CLAMPED TO WHAT `_net_run` WILL ACTUALLY HONOUR. Its cap goes through
  # `_clamp_int "$cap" 300 1 3600`, and `_clamp_int` returns its DEFAULT for an
  # out-of-range value -- so handing it 5300 silently produced a 300-SECOND
  # bound, six times shorter than the 1800s this replaced. A live muesli run
  # escalated on it while the log reported the 5300s that was asked for rather
  # than the 300s that applied.
  #
  # Say so when the configured budgets cannot fit: a bound that cannot cover
  # the work is a real limitation, and silently shrinking it is how this broke.
  if [ "$floor" -gt "$ceiling" ]; then
    echo "[batch:derive] WARN the CI wait/rerun settings can spend ${floor}s but a single bounded call tops out at ${ceiling}s -> using ${ceiling}s; lower BIRCHER_CI_WAIT or BIRCHER_CI_RERUN_MAX to fit" >&2
    floor=$ceiling
  fi
  if [ -n "${BIRCHER_DERIVE_TIMEOUT:-}" ]; then
    # CLAMPED, not returned raw. Returning it raw recreated the very collapse
    # this helper exists to prevent: BIRCHER_DERIVE_TIMEOUT=5300 reached
    # `_net_run`, exceeded its 3600 ceiling, and `_clamp_int` handed back its
    # 300s DEFAULT. The same defect, one branch over from where it was fixed.
    local want; want=$(_clamp_int "$BIRCHER_DERIVE_TIMEOUT" "$floor" 1 "$ceiling")
    [ "$want" != "$BIRCHER_DERIVE_TIMEOUT" ] \
      && echo "[batch:derive] WARN BIRCHER_DERIVE_TIMEOUT=$BIRCHER_DERIVE_TIMEOUT is not usable (1-${ceiling}s) -> using ${want}s" >&2
    [ "$want" -lt "$floor" ] 2>/dev/null \
      && echo "[batch:derive] WARN the derive timeout ${want}s is below the ${floor}s the CI wait/rerun settings can legitimately spend -> a healthy recovery may be cut short" >&2
    printf '%s' "$want"; return
  fi
  printf '%s' "$floor"
}

# _derived_width_ok <tuple> -> rc 0 if it is exactly one line of eight fields.
#
# FAIL CLOSED. The old reader could not tell a seven-field tuple from an
# eight-field one: `read -r a..h` simply leaves `h` empty, so a short result --
# version skew against an older coordinator, a truncated write, a partial
# failure -- silently restored the very behaviour the eighth field was added to
# remove, and the caller went on to authorize and merge a stale PR.
#
# Also rejects embedded newlines: `read` consumes only the FIRST line, so a
# multi-line result would be parsed as its first line and the rest discarded
# without a word.
_derived_width_ok() {
  local line="$1" n
  # `$'\n'` and NOT `"$(printf '\n')"`: command substitution strips trailing
  # newlines, so the latter is the EMPTY STRING and the pattern `*""*` matches
  # every input -- the check rejected everything, including valid tuples.
  case $line in *$'\n'*) return 1 ;; esac
  n=$(printf '%s' "$line" | awk -F'|' '{print NF}')
  [ "$n" = 8 ]
}

# _max_revisions -> how many repair rounds this run may spend, 0-5.
#
# `_clamp_int` returns its DEFAULT for anything outside the range, NOT a
# truncation to the nearest bound -- a distinction that already cost this file
# a silent 300s budget where 5300s was intended. Here it means
# BIRCHER_MAX_REVISIONS=9 gets 2, not 5. That is the safe direction (an
# operator asking for more rounds than the range allows gets the default rather
# than the maximum) and it is stated because the alternative reading is the
# obvious one.
#
# 0 disables the loop and restores the pre-loop behaviour exactly, which is
# what makes this shippable behind a switch rather than as a rewrite.
_max_revisions() { _clamp_int "${BIRCHER_MAX_REVISIONS:-2}" 2 0 5; }

# _repair_prompt <item> <pr> <branch> <findings> -> the brief for a repair round.
#
# Shaped after the hand-run repairs that worked: #740 converged in one round and
# #750 in two, both by handing the implementer the reviewer's blocking findings
# verbatim and the PR to push to.
#
# PUSH TO THE EXISTING BRANCH, stated twice and first. A repair that opens a
# second PR leaves the reviewed one behind: the merge gate is pinned to a head
# on THIS PR, so the fix would land nowhere the run can authorise, and the
# derivation would then discover two open PRs for one code and escalate on the
# ambiguity. Neither failure names the cause.
_repair_prompt() {  # <item> <pr> <branch> <findings>
  printf '%s' "A review of pull request #$2 in ${REPO} found blocking problems. Fix them.

Work on the EXISTING branch \`$3\` and push to the EXISTING pull request #$2.
Do NOT open a new pull request, do NOT create a new branch, and do NOT close #$2.

    git -C ${WORKDIR} worktree add /tmp/wt-repair-$2 $3

The reviewer's blocking findings, verbatim:

$4

Fix every blocking finding above. Run the tests. Push to \`$3\`. Do not change
anything the findings do not ask for -- this is a repair round on a reviewed
branch, not a second implementation.

The original task, for context:

$1"
}

# _revision_is_recorded <revisions-tuple> -> rc 0 only if the kernel journalled
# the revision we submitted. The tuple is `used|left|confirmed` from
# `coordinator.cli revisions --confirm-command <key>`.
#
# A FUNCTION, and not `[ "${state##*|}" = yes ]` at the call site, for one
# reason: the call site is inside run_item and nothing can drive it. The branch
# it guards is the one criterion 7 of the design exists for -- "the runner must
# observe an accepted REVIEW_VERDICT carrying the submitted command's causal id
# before it dispatches any repair work" -- and a guard on the most consequential
# branch in the loop, with no test able to reach it, is the shape that let a
# whole coordinator arm ship with zero executing coverage.
#
# EMPTY IS NO. An unreadable journal, a crashed lookup and a bounded call that
# timed out all arrive here as "", and every one of them means the same thing:
# we did not observe the fact. Reading absence as permission is how a repair
# gets dispatched against a run the kernel never revised.
_revision_is_recorded() {  # <used|left|confirmed>
  case "${1:-}" in *"|yes") return 0 ;; esac
  return 1
}

# _recovery_action <run_id> <base_sha> <context_hash> -> `do|why`, or empty.
#
# Asks the JOURNAL what to do with an interrupted run. The existing gate below
# asks the STATE NAME, and the state name cannot answer it: `reviewing` is
# reached from an accept, from a reject AND from a failed merge.
_recovery_action() {  # <run_id> <base_sha> <context_hash>
  [ -n "${1:-}" ] || return 0
  _coordinator recover --db "${BIRCHER_KERNEL_DB:-}" --run-id "$1" \
    --base-sha "${2:-}" --context-hash "${3:-}" 2>/dev/null || true
}

# _recovery_forbids_merge <action> -> rc 0 if this recovery action means the
# caller must NOT merge.
#
# THE WORST OUTCOME THIS PROGRAMME CAN PRODUCE is merging something twice, or
# merging something a reviewer never saw. Three of the recovery table's rows say
# "do not merge here" and each says it for a different reason:
#
#   done                 the outcome is already recorded; there is nothing left
#   record_merge_outcome the merge HAPPENED; record it, never re-execute it
#   halt_and_reconcile   the merge MAY have happened; only an observation of the
#                        forge can settle it
#
# UNKNOWN IS NOT PERMISSION. An empty action -- an unreachable kernel, a failed
# lookup, a bounded call that timed out -- returns rc 1 here, meaning "does not
# forbid", because refusing every merge whenever the kernel is unreachable would
# stop the pipeline dead on a transient. That is a deliberate trade and it is
# only safe because the merge path has its own gates behind this one: the kernel
# authorization, the reviewed-head pin, and `gh --match-head-commit`. This check
# is an early, cheap refusal, NOT the last line of defence, and treating it as
# the last line is how a fail-open default gets written.
_recovery_forbids_merge() {  # <action>
  case "${1%%|*}" in
    done|record_merge_outcome|halt_and_reconcile) return 0 ;;
  esac
  return 1
}

# _findings_path <code> -> where this round's findings go, or EMPTY when the
# repair loop is disabled.
#
# EMPTY IS THE POINT. `observe_outcome` omits `--findings-out` entirely for an
# empty value, and the CLI's unlink-then-replace only runs when that flag is
# present -- so BIRCHER_MAX_REVISIONS=0 performs no file operation at all, which
# is what "restores the previous behaviour exactly" has to mean.
#
# It did not, in the first cut: the path was passed unconditionally, so a
# disabled loop still made derivation depend on being able to unlink a file in
# NOOP_DIR. An unwritable or misowned directory there turned a healthy item into
# a nonzero exit, an empty tuple and an escalation -- a live item failing for
# repair-loop storage it was configured never to use. Found by cross-review.
_findings_path() {  # <code>
  [ "$(_max_revisions)" = 0 ] && return 0
  printf '%s' "${NOOP_DIR}/${1}.findings"
}

# _pr_branch <pr> -> the PR's head branch, or empty.
#
# READ, not derived from the code. The branch is not always `<code>-<slug>`:
# an implementer that named its branch after the skill's EXAMPLE code once
# stalled a whole wave (CAL06 #277, branch `a06-...`), which is why PR
# discovery stopped guessing from the code and why a repair must not either.
_pr_branch() {  # <pr>
  [ -n "${1:-}" ] || return 0
  gh pr view "$1" --repo "$REPO" --json headRefName -q .headRefName 2>/dev/null || true
}

# _repair_round <item> <code> <pr> <branch> <findings> <round> <vendor> -> rc 0 if a
# repair session ran to a stop, rc 1 if one could not be started.
#
# The RUNNER's half of the repair loop. The coordinator judges that a revision
# is owed and hands over the findings; this dispatches, prompts, waits and
# stops. It deliberately does NOT touch the queue file, the scorecard or the
# issue: those belong to the run, and the run is not over -- another derivation
# follows this.
#
# Its settle loop is a SIMPLER one than run_item's, and the differences are
# deliberate rather than an omission:
#   - no PR discovery. The PR exists; that is the whole premise.
#   - no provider-limit re-gate. That path returns rc 3 to re-queue the item on
#     the other vendor, which cannot happen mid-run with a reviewed PR already
#     open. A limit here ends the round quiet-handed and the next derivation
#     finds the branch unchanged and spends another round or escalates.
# Both are stated because a reader comparing the two loops will otherwise read
# the shorter one as the older one.
# `vendor` is PASSED, not inherited. Every other name this function reads --
# AGENT_ID, WORKDIR, SERVER, REPO, POLL, ITEM_TIMEOUT, BIRCHER_RUN_ID -- is a
# script-level global, but `vendor` is a `local` of run_item, so reading it here
# would work only by dynamic scope: correct in production, silently empty when
# the function is extracted and driven on its own, which is how --self-test
# exercises it. An empty actor dispatches a generation attributed to nobody.
_repair_round() {  # <item> <code> <pr> <branch> <findings> <round> <vendor>
  local item="$1" code="$2" pr="$3" branch="$4" findings="$5" round="$6"
  local vendor="${7:?_repair_round needs the implementing vendor}"
  local cap; cap=$(_clamp_int "${BIRCHER_REPAIR_TIMEOUT:-$ITEM_TIMEOUT}" "$ITEM_TIMEOUT" 60 86400)
  # A NEW generation, in the implementer role. The run is at `planned` after
  # the revision was recorded, and `start_implementation` moves it to
  # `implementing` -- the transition authz.py already anticipates, taking the
  # LAST start_implementation actor so this round's reviewer independence is
  # checked against this round's implementer.
  BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
  export BIRCHER_GENERATION
  # start_implementation
  _kernel_start_implementation "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION"

  local host_id conv_id
  host_id=$(_local_host_id 2>/dev/null) || host_id=""
  conv_id=$(_create_session "$AGENT_ID" "$host_id" "$WORKDIR")
  if [ -z "$conv_id" ]; then
    echo "[batch:repair] $item round $round: session create FAILED -> no repair this round" >&2
    return 1
  fi
  echo "[batch:repair] $item round $round: session $conv_id repairing PR #$pr on $branch (cap ${cap}s)" >&2
  if ! _send_prompt "$conv_id" "$(_repair_prompt "$item" "$pr" "$branch" "$findings")"; then
    echo "[batch:repair] $item round $round: send_prompt FAILED -> stopping session" >&2
    _stop_session "$conv_id"
    return 1
  fi

  local start elapsed=0 _sc="" _sp=0 _sr
  start=$(date +%s)
  while [ "$elapsed" -lt "$cap" ]; do
    sleep "$POLL"; elapsed=$(( $(date +%s) - start ))
    _sr=$(_coordinator session-settle --server "${SERVER:-}" --id "$conv_id" \
            --prev-count "$_sc" --stable-polls "$_sp" \
            --needed "${BIRCHER_SETTLE_POLLS:-4}") || _sr=""
    if [ -n "$_sr" ]; then
      _sc="${_sr%%|*}"; _sr="${_sr#*|}"; _sp="${_sr%%|*}"
      if [ "${_sr#*|}" = yes ]; then
        echo "[batch:repair] $item round $round: session quiet for $_sp polls -> re-deriving" >&2
        break
      fi
    fi
    local _ss; _ss=$(_session_state "$conv_id")
    if [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = died ]; then
      echo "[batch:repair] $item round $round: session died (state=$_ss) -> re-deriving" >&2
      break
    fi
  done
  [ "$elapsed" -ge "$cap" ] && \
    echo "[batch:repair] $item round $round: hit its ${cap}s cap -> re-deriving whatever landed" >&2
  # ALWAYS stop it. A quiet session is idle, not finished, and a live repair
  # session would race the review the next derivation is about to dispatch --
  # the same race run_item's teardown exists to close.
  _stop_session "$conv_id"
  return 0
}

observe_outcome() {  # <item> <code> <pr> [issue] [revisions_left] [findings_out]
  # THE DERIVATION, in Python since 2026-08-29. What was 192 lines here is now
  # v2/coordinator/outcome.py with eighteen tests driving it directly, plus its
  # dependencies -- discovery, reconciliation, CI classification, the review
  # dispatch -- each ported with a differential test against the bash it
  # replaced.
  #
  # NOT via `_coordinator`: that wraps every call in `_net_run` at
  # BIRCHER_KERNEL_TIMEOUT (5s), and this legitimately runs as long as CI does.
  # Bounded by its own budget instead, which defaults to half an hour.
  #
  # Emits the same SEVEN fields it always did:
  #   outcome|review|note|head|ci|ci_first|resubmissions
  #
  # The last two arguments are the repair loop's, and both default to the
  # pre-loop behaviour: no allowance, and nowhere to write findings. A
  # `--revisions-left` of 0 makes `revise` unreachable, so every caller that
  # does not pass them gets exactly what it got before.
  local out="" _budget _rc=0 _pw _pr _prw
  local _rl="${5:-0}" _fo="${6:-}"
  _budget=$(_derive_budget)
  # The SAME validated numbers the budget was computed from.
  read -r _pw _pr _prw <<<"$(_ci_policy)"
  out=$( PYTHONPATH="$(_kernel_pythonpath)" \
         _net_run "$_budget" \
         "${BIRCHER_PY:-python3}" -m coordinator.cli derive \
           --item "$1" --code "${2:-}" --pr "${3:-}" --issue "${4:-}" \
           --reviewer "$RECOVERY_REVIEWER" --repo "$REPO" \
           --server "$SERVER" --bundle-dir "$BUNDLE_DIR" \
           --poll-interval "$MAIN_CI_POLL_INTERVAL" \
           --ci-wait "$_pw" --rerun-max "$_pr" --rerun-wait "$_prw" \
           --revisions-left "$_rl" ${_fo:+--findings-out "$_fo"}
  ) || { _rc=$?; out=""; }
  # A TIMEOUT AND A CRASH BOTH YIELD AN EMPTY TUPLE, and the caller correctly
  # escalates either way -- but a human reading the log cannot tell them apart,
  # and they call for opposite responses (raise the budget vs fix the code).
  # `timeout` reports 124.
  [ "$_rc" = 124 ] && echo "[batch:derive] $1: derivation exceeded its ${_budget}s budget -> escalating (this is a BUDGET failure, not a crash)" >&2
  # An EMPTY tuple is a CRASH, not a verdict -- the caller checks for it and
  # escalates rather than reading outcome="" as "NOT ready".
  printf '%s' "$out"
}

# _is_blank <text> -> rc 0 if text is empty or whitespace-only.
# RC-1 guard helper: a missing/empty queue file must never launch a task-less
# session (an empty -p prompt produced coordinators that idled the full
# ITEM_TIMEOUT). Kept tiny and pure so --self-test can exercise it.
_is_blank() { [ -z "${1//[[:space:]]/}" ]; }

# _render_issue_item <number> <title> <body> -> the queue-file text for a GitHub
# issue. First line is the task heading (code i<number>); an `Issue: #<number>`
# header lets run-queue write back + the coordinator emit `Closes #<number>`.
# _format_issue_comments <comments_json> [max_comments] [max_chars] -> rendered
# discussion block, or empty. PURE: takes the JSON, fetches nothing.
#
# #46: the queue item was built from title+body only, so a comment was invisible
# to the implementer. Commenting on an escalated issue and re-queueing it is the
# obvious way to hand a diagnosis back, and it delivered NOTHING -- the run
# looked healthy and re-derived its previous conclusion (muesli #621).
#
# Bircher's own status comments are dropped: feeding `bircher: outcome=...` back
# to the next run is noise at best and self-reinforcing at worst. Matched with
# startswith, not a substring, so a human discussing a marker still gets through.
_format_issue_comments() {
  local json="$1"
  printf '%s' "$json" | MAXC="${2:-20}" MAXCH="${3:-16000}" python3 -c '
import json, os, sys

raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
try:
    comments = json.loads(raw)
except ValueError:
    sys.exit(0)
if not isinstance(comments, list):
    sys.exit(0)

def is_bircher_status(body):
    # Filters the comments BIRCHER ITSELF wrote out of the digest handed to a
    # session, so it does not read its own machinery as human discussion.
    #
    # The two legacy prefixes stay AFTER Phase 2 retired the marker, and that is
    # deliberate: real PRs carry thousands of them, and a filter that stopped
    # recognising them would start feeding a session the status lines written
    # by its predecessor. Retiring a channel means never writing one again; it
    # does not mean forgetting how to read the archive.
    head = body.lstrip()
    return (head.startswith("bircher: outcome=")
            or head.startswith("bircher-status:")
            or head.startswith("Outcome derived from the repository")
            or head.startswith("Cross-vendor review (outcome derived"))

kept = [c for c in comments if not is_bircher_status(c.get("body") or "")]
maxc, maxch = int(os.environ["MAXC"]), int(os.environ["MAXCH"])

omitted = max(0, len(kept) - maxc)
kept = kept[-maxc:]
if not kept:
    sys.exit(0)

parts = []
for c in kept:
    who = (c.get("author") or {}).get("login") or "unknown"
    parts.append("### %s (%s)\n\n%s" % (who, c.get("createdAt") or "", (c.get("body") or "").strip()))
text = "\n\n".join(parts)

notes = []
if omitted:
    notes.append("%d older comment(s) omitted" % omitted)
if len(text) > maxch:
    # Keep the NEWEST characters: the latest comment is the one most likely to
    # be the correction this run needs.
    text = text[-maxch:]
    notes.append("truncated to the last %d characters" % maxch)
if notes:
    text = "> NOTE: %s.\n\n%s" % ("; ".join(notes), text)
print(text)
'
}

_render_issue_item() {
  local n="$1" title="$2" body="$3" comments="${4:-}"
  printf '# i%s: %s\n\nIssue: #%s\n\n%s\n' "$n" "$title" "$n" "$body"
  # `if` rather than `[ -n ... ] &&` so the function still returns 0 when there
  # are no comments -- the caller runs under `set -e`.
  if [ -n "$comments" ]; then
    printf '\n## Discussion (oldest first)\n\nComments on the issue. A later comment may correct an earlier one, and may correct the issue body above -- prefer the most recent statement where they disagree.\n\n%s\n' "$comments"
  fi
}

# _required_contexts -> the contexts branch protection actually requires, one per
# line; EMPTY when unknown (no protection, no permission, request failed).
#
# #43: the alternative is _drop_non_ci_checkruns' hardcoded denylist, which has to be
# edited in THIS repo every time the TARGET repo adds a check. It had already grown
# twice (Dependabot, then review-gate) and muesli's `coverage report (informational)`
# would have been the third -- discovered only after it silently stalled a merge and
# left main one poll away from an auto-revert. An allowlist the target repo publishes
# cannot drift.
_REQUIRED_CONTEXTS_CACHE=""
_REQUIRED_CONTEXTS_LOADED=""
# _required_contexts_snapshot -> line 1: known | empty | unknown ; line 2+: the contexts.
#
# #70: `_required_contexts` returns the SAME empty string for "this branch has no
# required checks", "the token cannot see protection", and "the request failed". That
# ambiguity is harmless while nothing depends on it -- and became load-bearing the
# moment an expected-context list had to be intersected with the required set, because
# an empty intersection would silently disable the completeness check during exactly the
# degraded access that makes a partially registered set most likely.
#
# The state is FRAMED, not sentinelled: always line 1, always one of three fixed words,
# names strictly on line 2+. Position carries the meaning, so a context literally named
# `known` cannot be mistaken for the state -- the distinction #67's __MALFORMED_CONTEXT__
# failure was actually about.
#
# BOTH representations, and TYPED rather than flattened (#73). GitHub's
# `status-check-policy` carries `contexts` (bare names -- ANY producer satisfies them)
# and `checks` (`{context, app_id}` -- a SPECIFIC producer does). #70 unioned them into
# a name list, which closed one gap and erased a trust boundary: a same-named check from
# the wrong app could satisfy an app-bound requirement.
#
# This is not hypothetical on the repo it was built for. Every one of muesli's seven
# required checks is declared app-bound to app_id 15368, and `contexts` merely mirrors
# them -- so the name-only matcher was ignoring the binding on every merge.
#
# Each line is therefore `bound<TAB>context<TAB>app_id` or `unbound<TAB>context`.
# Callers that only want names take field 2.
#
# 404 is an ANSWER, not a failure: an unprotected branch returns it, and treating that as
# `unknown` would hold every merge on a repo that simply has no branch protection. Any
# other error is `unknown`. NOTE a token lacking permission to READ protection also gets
# 404, not 403 -- observed directly. That lands in `empty`, which for #70 fails SAFE: the
# declared list is then used unintersected, so the completeness gate still applies.
_required_contexts_snapshot() {
  # ONE call. A first cut ran gh twice -- once for stderr, once for stdout -- which both
  # doubles the cost and lets the two observations disagree. stderr goes to a temp file
  # so the context list on stdout stays clean; folding them with 2>&1 would let any gh
  # warning become a bogus context name.
  local out err rc tmp
  tmp=$(mktemp 2>/dev/null) || { printf 'unknown\n'; return; }
  out=$(_ci_gh api "repos/$REPO/branches/${MAIN_BRANCH:-main}/protection" \
        2>"$tmp"); rc=$?
  err=$(cat "$tmp" 2>/dev/null); rm -f "$tmp"
  if [ "$rc" -ne 0 ]; then
    # ONLY the message GitHub returns for a genuinely unprotected branch counts as an
    # authoritative empty set. A blanket "HTTP 404" was too permissive: a token that
    # cannot READ protection also gets 404 (observed directly), and classifying that as
    # `empty` would use the declared list UNINTERSECTED -- letting a stale, misspelled or
    # PR-only entry gate on a context branch protection never required, and turning a
    # permission regression into a multi-hour halt after a healthy merge. Indistinguishable
    # 404s are `unknown`, which holds pending and says why.
    if _contains "$err" 'Branch not protected'; then
      printf 'empty\n'
    else
      _contains "$err" '404' && echo "[batch] WARN: branch protection returned 404 but not 'Branch not protected' -> treating as UNREADABLE, not unprotected (token permissions?)" >&2
      printf 'unknown\n'
    fi
    return
  fi
  # VALIDATE BEFORE SERIALISING. The typed protocol uses TAB as its field separator, so
  # a context containing a TAB would be read as extra fields: `build<TAB>linux` becomes
  # name `build`, the declared list can no longer intersect it, the expected set comes
  # back EMPTY -- and an empty set means no gate. Silently disabling the check on an
  # opted-in repo is the worst outcome available, and it is exactly #67's lesson about a
  # name that cannot be represented in a delimiter protocol.
  #
  # Every line here IS a requirement, so there is no "drop the non-required one" option
  # as #67 had: an unrepresentable context means the requirements cannot be described at
  # all, and the honest answer is `unknown` -- which holds pending and says why.
  # VALIDATE THE WHOLE SCHEMA before serialising, and fail closed on anything unexpected.
  # Every partial validation so far has leaked: a TAB truncated a name, and an EMPTY
  # context serialised to `unbound<TAB>` which `_req_names` then dropped -- leaving a
  # `known` snapshot with no gate at all. A malformed protection response must never
  # become "nothing to wait for".
  printf '%s' "$out" | jq -e '
      (.required_status_checks // null) as $r
      | if $r == null then true
        else ($r | type) == "object"
             and (($r.contexts // []) | type) == "array"
             and (($r.checks   // []) | type) == "array"
             and (($r.contexts // []) | all((type == "string") and (length > 0) and (test("[\t\n]") | not)))
             and (($r.checks   // []) | all((type == "object")
                    and ((.context | type) == "string") and ((.context | length) > 0)
                    and ((.context | test("[\t\n]")) | not)
                    and ((.app_id == null) or (((.app_id | type) == "number")
                          and ((.app_id == -1) or (.app_id > 0))))))
        end' >/dev/null 2>&1 || {
    echo "[batch] WARN: branch protection did not match the expected schema (unrepresentable or malformed context, or a bad app_id) -> treating it as UNREADABLE" >&2
    printf 'unknown\n'; return
  }
  # app_id -1 is GitHub's documented WILDCARD -- "Pass -1 to explicitly allow any app to
  # set the status" -- and an omitted app_id is likewise permissive. Both are recorded as
  # UNBOUND, or the matcher would demand a producer literally called "-1" and hold every
  # merge on a perfectly valid configuration.
  out=$(printf '%s' "$out" | jq -r '
      [ (.required_status_checks.checks[]?
         | if (.app_id != null) and (.app_id > 0)
           then "bound\t\(.context)\t\(.app_id)"
           else "unbound\t\(.context)" end),
        (.required_status_checks.contexts[]? | "unbound\t\(.)") ] | unique[]' 2>/dev/null) \
    || { printf 'unknown\n'; return; }
  case "$out" in *[![:space:]]*) printf 'known\n%s\n' "$out" ;; *) printf 'empty\n' ;; esac
}

# _req_names <typed-requirements> -> just the context names, deduplicated.
# Everything that predates #73 keys on names; only the completeness gate needs the type.
_req_names() {
  printf '%s\n' "$1" | awk -F'\t' 'NF>1 && $2 != "" { print $2 }' | awk '!seen[$0]++'
}

# _req_app <typed-requirements> <context> -> the required app id, or "" when the
# requirement is unbound. A context declared BOTH ways (GitHub mirrors `checks` into
# `contexts`, so muesli declares all seven twice) resolves to the BOUND form: it is the
# stricter of the two, and taking the looser one would silently discard the binding.
_req_app() {
  printf '%s\n' "$1" | awk -F'\t' -v c="$2" '$2 == c && $1 == "bound" { print $3; found=1 }
                                               END { if (!found) print "" }' | head -1
}

# _expected_set <snap_state> <snap_names> -> the contexts whose presence gates GREEN.
#
# Empty when the operator has not opted in, so every repo behaves exactly as before.
# Otherwise the declared list, restricted:
#   * to what branch protection REQUIRES (so the list can never grant blocking authority
#     branch protection did not) -- unless protection legitimately lists nothing, in
#     which case there is nothing to intersect with and the declaration stands;
#   * minus anything on the ignore list, because waiting for a context
#     `_keep_blocking_checks` is documented to ignore would bypass load-bearing ignore
#     behaviour -- that filter exists because Dependabot's check-runs once turned a
#     healthy main red and triggered an auto-revert.
_expected_set() {
  local st="$1" names="$2" want="${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" line out=""
  # #73: the snapshot is TYPED now. Intersect on bare names -- the binding is applied
  # later, by the matcher, which is the only place that can weigh a producer against the
  # rows that actually reported.
  case "$names" in *"$(printf '\t')"*) names=$(_req_names "$names") ;; esac
  [ -n "${want//[[:space:]]/}" ] || return 0
  [ "$st" = known ] || [ "$st" = empty ] || return 0
  while IFS= read -r line; do
    # Strip ONE trailing CR. `read -r` preserves it, so a list pasted from a CRLF source
    # yields `unit\r`, the exact intersection rejects every entry, `_expected_set` returns
    # empty -- and an empty set means NO GATE. The feature would silently become a no-op
    # on an opted-in repo, which is the worst failure direction available to it: not a
    # wrong verdict, but the quiet absence of the check that was supposed to prevent one.
    # Only \r, and only one: any other whitespace is significant, because these are
    # literal check names and GitHub allows leading and trailing spaces in them.
    line=${line%$'\r'}
    case "$line" in ''|*[![:space:]]*) ;; *) continue ;; esac
    [ -n "$line" ] || continue
    printf '%s\n' "$line" \
      | grep -qE "^(${BIRCHER_CI_IGNORE_CHECKS:-Dependabot|review-gate})$" && continue
    if [ "$st" = known ]; then
      _contains "$(printf '\n%s\n' "$names")" "$(printf '\n%s\n' "$line")" || continue
    fi
    out="${out}${line}
"
  done <<EOF
$want
EOF
  printf '%s' "$out"
}

# _drop_wrong_producer <lines> <typed-requirements> -> the same lines, minus check-run
# rows from an app that a BOUND requirement did not name.
#
# #73: producer matching has to happen BEFORE the verdict, not only inside the
# completeness check. `_expected_incomplete` runs only once `_checkrun_state` has already
# said green -- and `_checkrun_state` sees every row with a matching NAME, so a stray
# wrong-app failure made main red before the producer-aware rule got a turn. Branch
# protection would have accepted the required app's success; bircher would have reverted.
# The unit test missed it exactly because it exercised the matcher in isolation.
#
# A row is dropped only when its context has a bound requirement AND the row names a
# different app. Unbound contexts, unknown contexts, and app-less rows are untouched
# here -- eligibility of app-less rows for a bound requirement is the matcher's business,
# and dropping them at this stage would make an absent required check look like a green
# one.
_drop_wrong_producer() {
  local lines="$1" typed="$2" row name app need
  [ -n "${typed//[[:space:]]/}" ] || { printf '%s' "$lines"; return; }
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    name=${row%%|*}
    app=${row##*|}
    need=$(_req_app "$typed" "$name")
    # EXACTLY the matcher's rule -- including excluding app-less rows from a bound
    # requirement. Leaving them in was the same composition defect one case over: a
    # same-named failing commit STATUS survived here, `_checkrun_state` went red, and the
    # matcher that would have ignored it never ran. Two components disagreeing about
    # eligibility means whichever runs first decides, which is not a design.
    [ -n "$need" ] && [ "$app" != "$need" ] && continue
    printf '%s\n' "$row"
  done <<EOF
$lines
EOF
}

# _emptied_by_filter <raw> <filtered> <required-names> -> the first required context that
# HAD rows before producer filtering and has none after, or "" if none.
#
# #73: filtering removes ineligible rows, which is right for classification -- but it can
# remove the ONLY rows for a required context, and if that context is not in the
# operator's expected subset nothing downstream ever looks for it. Classification then
# sees a smaller, all-green set and the merge is accepted while branch protection is
# still waiting. Exact shape: protection requires A and B; the declared list names only
# A; A is green from the required app and B reported ONLY from the wrong one.
#
# Deliberately narrow: it fires only where filtering actually removed something, so it
# can never demand a context that legitimately never reports on a merge commit -- which
# is the whole reason #70 had to be declared rather than inferred.
_emptied_by_filter() {
  local raw="$1" filt="$2" names="$3" c before after
  [ -n "${names//[[:space:]]/}" ] || return 0
  while IFS= read -r c; do
    [ -n "${c//[[:space:]]/}" ] || continue
    before=$(printf '%s\n' "$raw"  | awk -F'|' -v n="$c" '$1 == n' | grep -c .)
    [ "$before" = 0 ] && continue
    after=$(printf '%s\n' "$filt" | awk -F'|' -v n="$c" '$1 == n' | grep -c .)
    [ "$after" = 0 ] && { printf '%s' "$c"; return; }
  done <<EOF
$names
EOF
}

# _expected_incomplete <lines> <expected> [typed-requirements] -> the first expected
# context not yet satisfied, or "" when all are.
#
# `lines` are unfiltered "name|status|conclusion|app" rows, because _keep_blocking_checks
# strips the name this needs.
#
# #73 -- PRODUCER MATCHING. A requirement declared as `checks[]` is pinned to an app_id;
# one declared as `contexts[]` is satisfied by any producer. Matching on name alone let a
# same-named check from the WRONG app satisfy a pinned requirement, which is not
# theoretical: every one of muesli's required checks is app-bound.
#
# For a BOUND requirement the eligible rows are:
#   * check-runs whose app id equals the required one, and
#   * rows with NO producer identity -- commit statuses, which the API gives no app at
#     all, so they cannot be pinned and must not be excluded by a pin.
# Rows from a different app are IGNORED: not evidence for, and not evidence against.
#
# Eligible rows are then aggregated conservatively, worst-first:
#   any red        -> unsatisfied (report it, so the caller keeps polling and the red
#                     surfaces through the normal verdict path)
#   any pending    -> unsatisfied
#   none at all    -> unsatisfied  (absence is never satisfaction -- the whole point)
#   otherwise      -> satisfied
# So required-app green + stray-app red PASSES, required-app red + stray-app green FAILS,
# and only-stray-app green HOLDS. That is what branch protection itself would do.
_expected_incomplete() {
  local lines="$1" expected="$2" typed="${3:-}" want row need st app
  while IFS= read -r want; do
    [ -n "${want//[[:space:]]/}" ] || continue
    need=$(_req_app "$typed" "$want")
    local seen=0 red=0 pend=0 good=0
    while IFS= read -r row; do
      [ "${row%%|*}" = "$want" ] || continue
      st=${row#*|}; app=${st##*|}; st=${st%|*}      # st -> status|conclusion, app -> id
      # A BOUND requirement names the app that must provide the check, so ONLY that
      # app's check-runs are evidence. An app-less row -- a commit status -- is not
      # evidence that app N produced anything, and anyone able to post a status could
      # otherwise satisfy this gate while branch protection itself stayed pending. An
      # earlier cut accepted app-less rows here on the reasoning that protection cannot
      # pin what it cannot identify; that had it backwards, and the self-test codified
      # the mistake. Wildcard (-1) and omitted app_id are recorded UNBOUND upstream, so
      # `need` is only ever a real app id.
      [ -n "$need" ] && [ "$app" != "$need" ] && continue
      seen=1
      case "$st" in
        completed\|success|completed\|neutral|completed\|skipped) good=1 ;;
        completed\|*)                                              red=1 ;;
        *)                                                          pend=1 ;;
      esac
    done <<ROWS
$lines
ROWS
    { [ "$red" = 1 ] || [ "$pend" = 1 ] || [ "$seen" = 0 ] || [ "$good" = 0 ]; } \
      && { printf '%s' "$want"; return; }
  done <<EOF
$expected
EOF
}

_required_contexts() {
  if [ -z "$_REQUIRED_CONTEXTS_LOADED" ]; then
    _REQUIRED_CONTEXTS_LOADED=1
    # #62: bounded like every other CI lookup. This one is easy to overlook because it
    # is not "CI" -- but it runs inside the post-merge watch, and a hang here outlives
    # the absolute deadline just as surely as a hung check-runs fetch would. With the
    # deadline unarmed (the PR path) the cap is simply the 120s default.
    _REQUIRED_CONTEXTS_CACHE=$(_ci_gh api \
      "repos/$REPO/branches/${MAIN_BRANCH:-main}/protection" \
      --jq '[.required_status_checks.contexts[]?, .required_status_checks.checks[]?.context] | unique[]' 2>/dev/null) || _REQUIRED_CONTEXTS_CACHE=""
  fi
  printf '%s' "$_REQUIRED_CONTEXTS_CACHE"
}

# _keep_blocking_checks <lines> <required> -> lines that actually gate a merge, name
# stripped. (PURE -- <required> is passed in, never fetched here.)
#
# Two filters, and BOTH are load-bearing:
#
#   1. The ignore list still applies FIRST. `review-gate` is itself a REQUIRED context,
#      so an allowlist alone would re-admit it -- and admitting it DEADLOCKS: review-gate
#      stays pending until a cross-vendor review is posted, and the caller of this
#      function is the thing about to post it. Each waits for the other (muesli PR #549,
#      2026-08-07). Filtering required-minus-ignored keeps that guard intact.
#   2. Then, when <required> is known, keep only those contexts. A failing NON-required
#      check is not a merge blocker, and treating it as one is what made the wave path
#      (which asks GitHub `mergeable`, required-only) and the recovery path (which asked
#      this code, any-check) disagree about the same PR.
#
# Unknown <required> falls back to ignore-list-only -- the previous behaviour, which
# errs toward calling things red. Failing closed matters here: inverting it would make
# every genuinely red PR look green.
# _drop_ignored <lines> -> the same lines minus ignore-listed checks, names INTACT.
# Extracted (#73) so the removal guard and the classifier apply the identical list: the
# guard was comparing against a producer-filtered set only, so a required context removed
# by the IGNORE list vanished silently and the merge went green while protection waited.
_drop_ignored() {
  printf '%s\n' "$1" | grep -vE "^(${BIRCHER_CI_IGNORE_CHECKS:-Dependabot|review-gate})\|"
}

_keep_blocking_checks() {
  # Reduce `name|bucket|state` rows to the `bucket|state` of BLOCKING ones.
  # `review-gate` is dropped or the derivation deadlocks against itself; a
  # required list matching nothing falls back to all checks rather than
  # reducing to an empty set that reads as pending for ever; and a row with NO
  # delimiter passes through whole, because `cut` does and real `gh pr checks`
  # output arrives that way. All three rules, and their tests, are in
  # v2/coordinator/ci.py.
  # PIPED for the same reason as `_normalize_ci`: the check list is unbounded
  # and an oversized argv element fails execve rather than returning an answer.
  printf '%s' "$1" | _coordinator ci-keep-blocking --lines - --required "${2:-}" \
    || printf '%s' "$1"
}

# _drop_non_ci_checkruns <lines> -> the same lines minus non-CI ones, name stripped.
# Input lines are "name|status|conclusion"; output is "status|conclusion" for
# _checkrun_state.
#
# GitHub reports MORE than CI under a commit's check-runs. Dependabot's update jobs
# land there too — same app (github-actions), all named "Dependabot" — so the moment
# .github/dependabot.yml merged, the next main-CI watch saw 28 extra check-runs, 2 of
# them failed, and declared main red. The run then tried to auto-revert a healthy main
# and halted with items still queued (2026-08-07, i520). Real CI was green throughout.
#
# Filter by name because the producing app does not distinguish them. Override with
# BIRCHER_CI_IGNORE_CHECKS (an ERE matched against the check name).
_drop_non_ci_checkruns() {
  printf '%s\n' "$1" \
    | grep -vE "^(${BIRCHER_CI_IGNORE_CHECKS:-Dependabot|review-gate})\|" \
    | cut -d'|' -f2,3
}

# _rerun_main_ci_until_green <sha> [budget] [delay_s] -> green|red|pending|unknown
#
# Re-runs main's CI on the SAME commit until it goes green or the budget is spent.
# A green with no code change is causal evidence the earlier red was transient --
# unlike reading the logs, which proves only that some words co-occurred. Three
# rounds of review killed the log-classifying designs for exactly that reason.
#
# 2026-08-12 is the case to beat: three reviewed, green merges were reverted
# because a provider outage lasted MINUTES, and the single immediate re-run rode
# straight into it.
#
# BUDGET COUNTS RE-RUNS, not loop iterations. An earlier version counted
# iterations while a single iteration could dispatch TWO re-runs (a partial plus
# its confirmation), so the real worst case was five re-runs against a documented
# three -- roughly 87 minutes of a broken main going unreverted while the comment
# promised an hour. The bound now means what it says.
#
# A green from a `--failed` re-run is not evidence on its own: the jobs that
# passed before are never re-run, so only a FULL run's green is accepted. The last
# re-run the budget can afford is therefore always full, and a partial green with
# nothing left to confirm it reports `unknown` (no verdict -> halt, never revert).
#
# Worst case with the defaults: 3 x (MAIN_CI_TIMEOUT 900s + 20s startup) + 2 x 300s
# = 56 minutes, plus the initial watch. That initial watch is now
# MAIN_CI_SETTLE_TIMEOUT (3600s, #62) rather than 900s, so the sum of the budgets is
# ~116 minutes; MAIN_CI_ABSOLUTE_DEADLINE (7200s = 120m) bounds the sequence, and
# `_past_ci_deadline` is checked in this loop, before each re-run dispatch, before the
# partial-green confirmation, and in both poll loops.
#
# Be precise about what that bound is worth: it is checked BETWEEN operations, never
# inside one. A `gh` call that hangs rather than failing is not interrupted, so the
# deadline bounds how much WORK is started, not how long the process can block. Giving
# it teeth against a hung subprocess needs a per-call timeout, which `timeout(1)` does
# not portably provide (absent on macOS by default). Treat 7200s as the ceiling on
# scheduled work, not a hard kill.
#
# The practical figure that matters: a genuinely broken main can now stay unreverted for
# up to ~120 minutes rather than ~71. That is the price of tolerating the 2867s CI run
# that halted a healthy wave; keep it in mind before raising anything here.
_rerun_main_ci_until_green() {
	local sha="$1" budget="${2:-${BIRCHER_MAIN_CI_RERUNS:-3}}" delay="${3:-${BIRCHER_MAIN_CI_RERUN_DELAY:-300}}"
	local st=unknown mode
	# A non-numeric or non-positive setting must not silently mean "never retried".
	case "$budget" in ''|*[!0-9]*) budget=3 ;; esac
	[ "$budget" -ge 1 ] 2>/dev/null || budget=1
	while [ "$budget" -gt 0 ]; do
		# #62: the shared wall clock outranks the re-run budget. Checked at the TOP so an
		# in-flight poll always finishes -- cutting one mid-flight would discard a verdict
		# we have already paid for, and the point of the bound is to stop the budgets
		# multiplying, not to shave seconds.
		if _past_ci_deadline; then
			echo "[batch:merge] main CI re-runs abandoned: the ${MAIN_CI_ABSOLUTE_DEADLINE}s absolute deadline passed" >&2
			break
		fi
		# The last re-run we can afford must be full, so a green can be accepted.
		mode=""; [ "$budget" -eq 1 ] && mode=full
		st=$(_rerun_main_ci "$sha" "$mode"); budget=$((budget - 1))
		if [ "$st" = green ]; then
			if [ "$mode" = full ]; then
				echo "[batch:merge] main CI went GREEN on a full re-run with no code change -> the red was transient" >&2
				echo green; return
			fi
			# No budget check needed: the last affordable re-run is always full, so a
			# green from a PARTIAL run always has at least one re-run left to confirm it.
			echo "[batch:merge] main CI green on a partial re-run -> confirming with a full re-run" >&2
			# This dispatch does NOT pass back through the loop guard above, so it needs
			# its own check or it can start a fresh full re-run after the deadline.
			if _past_ci_deadline; then
				echo "[batch:merge] confirming full re-run skipped: the ${MAIN_CI_ABSOLUTE_DEADLINE}s absolute deadline passed -> partial green is not evidence" >&2
				st=unknown; break
			fi
			st=$(_rerun_main_ci "$sha" full); budget=$((budget - 1))
			if [ "$st" = green ]; then
				echo "[batch:merge] full re-run confirmed GREEN -> the red was transient" >&2
				echo green; return
			fi
			echo "[batch:merge] full re-run did NOT confirm ($st) -> the partial green was not evidence" >&2
		fi
		[ "$budget" -gt 0 ] && sleep "$delay"
	done
	[ "$st" = red ] || echo "[batch:merge] main CI re-runs produced NO verdict ($st) -> will halt without reverting" >&2
	echo "$st"
}

# _main_ci_verdict <first-state> <second-state> -> continue|revert-halt|halt
# first/second are _checkrun_state outputs (green|red|pending). A non-green FIRST
# is re-checked once (SECOND); only a still-bad SECOND acts. Pending==unresolved.
_main_ci_verdict() {
  case "$1" in
    green) echo continue ;;
    red)
      # An empty second state means no re-run was ATTEMPTED (the operator disabled
      # it), so the first red stands. `pending`/`unknown` mean one was attempted and
      # produced NO VERDICT -- a timeout, a rate limit, a run already in flight.
      # Reverting on that is reverting on ignorance, which is how a provider outage
      # destroyed reviewed work on 2026-08-12. Halt instead: the wave still stops,
      # but nothing good is thrown away.
      case "${2:-}" in
        "")      echo "revert-halt" ;;
        green)   echo continue ;;
        red)     echo "revert-halt" ;;
        *)       echo halt ;;
      esac ;;
    *)     [ "${2:-}" = green ] && echo continue || echo halt ;;
  esac
}

# _revert_git_args <sha> <parent_count> -> the arg string for `git revert`, or "" when
# unrevertable (empty sha -> caller must NOT run a bare `git revert`; the 2026-07-10 run
# did exactly that and left main red). A MERGE commit (parents>1) needs `-m 1` (mainline);
# a normal/squash commit (1 parent) does not. PURE + self-tested (#359).
_revert_git_args() {
  local sha="$1" parents="${2:-1}"
  [ -n "$sha" ] || { echo ""; return; }
  # NO -q. `git revert` has no --quiet: passing it exits 129 with a usage dump and
  # reverts nothing, so EVERY auto-revert failed, for every commit shape, for as long
  # as this function existed. The parent handling below was always right and never got
  # the chance to matter. Found 2026-08-07 (muesli i520): the run logged git's usage
  # text alongside "automatic revert FAILED (parents=1)", and the parent count was a
  # red herring. The old self-test asserted the exact string INCLUDING -q, so it passed
  # throughout — see the test below, which now runs git rather than matching a string.
  if [ "${parents:-1}" -gt 1 ] 2>/dev/null; then
    echo "--no-edit -m 1 $sha"
  else
    echo "--no-edit $sha"
  fi
}

# _rerun_main_ci <sha> -> green|red|pending. Re-runs the failed jobs of main's CI
# run for the merge commit ONCE, then re-polls the commit's check-runs. Used to
# distinguish a flaky red/hung main from a genuine one before reverting/halting.
_rerun_main_ci() {
  local sha="$1" full="${2:-}" rid w=0 lines st _iv _rr_exp _rr_cls
  # #62: check BEFORE dispatching. This function costs a `gh run list`, a `gh run
  # rerun` and a 20s startup sleep before it reaches its poll loop, so a check only at
  # the loop would let all of that run past an expired deadline.
  if _past_ci_deadline; then
    echo "[batch:merge] re-run not dispatched: the ${MAIN_CI_ABSOLUTE_DEADLINE}s absolute deadline passed" >&2
    echo unknown; return
  fi
  rid=$(_ci_gh run list --repo "$REPO" --branch main --limit 10 --json databaseId,headSha \
        -q ".[] | select(.headSha==\"$sha\") | .databaseId" 2>/dev/null | head -1)
  # `unknown`, NOT red: no verdict was obtained. Reporting red here would let a
  # rate limit or "this workflow is already running" masquerade as confirmed
  # regression evidence and revert a good commit (2026-08-12 hit both).
  [ -n "$rid" ] || { echo unknown; return; }
  if [ "$full" = full ]; then
    _ci_gh run rerun "$rid" --repo "$REPO" >/dev/null 2>&1 || { echo unknown; return; }
  else
    _ci_gh run rerun "$rid" --repo "$REPO" --failed >/dev/null 2>&1 \
      || _ci_gh run rerun "$rid" --repo "$REPO" >/dev/null 2>&1 || { echo unknown; return; }
  fi
  sleep 20
  # Once, not per poll: inside the loop's command substitution the cache in
  # _required_contexts dies with the subshell, so it re-requested branch
  # protection every 30s -- the exact hazard the comment on _poll_ci warns about.
  # #70: INHERIT the watcher's snapshot rather than re-deriving one. A second lookup is a
  # second failure window, and it can observe a protection edit made mid-watch -- judging
  # the rerun against a different required set than the one that produced the red. Only
  # an inherited `unknown` (or a direct call, e.g. from the self-test) refreshes.
  local _rr_req _rr_snap _rr_state="${MAIN_SNAP_STATE:-unknown}" _rr_names="${MAIN_SNAP_NAMES:-}"
  if [ "$_rr_state" = unknown ]; then
    _rr_snap=$(_required_contexts_snapshot)
    _rr_state=${_rr_snap%%$'\n'*}
    _rr_names=${_rr_snap#*$'\n'}; [ "$_rr_state" = known ] || _rr_names=""
  fi
  _rr_req=$(_req_names "$_rr_names")
  while [ "$w" -lt "$MAIN_CI_TIMEOUT" ] && ! _past_ci_deadline; do
    # #67: same helper AND same required set as the watcher. This used to poll
    # unnamed check-runs with no _keep_blocking_checks, so a non-required failure
    # could contradict the watch that triggered it -- and re-create the #43 stall
    # that filtering exists to prevent. Selecting a workflow to re-run and
    # deciding whether main is green are separate concerns.
    if [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ] && [ "$_rr_state" = unknown ]; then
      _rr_snap=$(_required_contexts_snapshot)
      _rr_state=${_rr_snap%%$'\n'*}
      _rr_names=${_rr_snap#*$'\n'}; [ "$_rr_state" = known ] || _rr_names=""
      _rr_req=$(_req_names "$_rr_names")
    fi
    lines=$(_commit_ci_lines "$sha" "$_rr_req") || lines=""
    _rr_cls="$lines"
    [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ] \
      && _rr_cls=$(_drop_wrong_producer "$lines" "$_rr_names")
    st=$(_checkrun_state "$(_keep_blocking_checks "$_rr_cls" "$_rr_req")")
    if [ "$st" = green ] && [ -n "${BIRCHER_MAIN_EXPECTED_CONTEXTS:-}" ]; then
      if [ "$_rr_state" = unknown ]; then st=pending
      else
        _rr_exp=$(_expected_set "$_rr_state" "$_rr_names")
        if [ -n "$(_emptied_by_filter "$lines" "$(_drop_ignored "$_rr_cls")" "$_rr_req")" ]; then st=pending
        elif [ -n "$_rr_exp" ] && [ -n "$(_expected_incomplete "$lines" "$_rr_exp" "$_rr_names")" ]; then st=pending
        fi
      fi
    fi
    [ "$st" != pending ] && { echo "$st"; return; }
    _iv=$(_clamp_int "$MAIN_CI_POLL_INTERVAL" 30 1 300)
    sleep "$_iv"; w=$((w + _iv))
  done
  echo pending
}

# _manifest_items <manifest-file> <queue-dir> -> prints "<queue-dir>/<basename>" for
# each non-empty manifest line, IN ORDER (the shim wrote them in priority order).
_manifest_items() {
  local mf="$1" qdir="$2" b
  [ -f "$mf" ] || return 0
  while IFS= read -r b; do [ -n "$b" ] && printf '%s\n' "$qdir/$b"; done < "$mf"
}

# _pr_signal <code> -> the PR number the coordinator recorded for this item in
# $NOOP_DIR/<code>.pr (digits only), or "" if none (B-6). Deterministic PR<->item
# mapping that does not depend on the implementer's branch name.
_pr_signal() {
  [ -f "$NOOP_DIR/$1.pr" ] || return 0
  head -c 20 "$NOOP_DIR/$1.pr" 2>/dev/null | tr -cd '0-9'
}

# _pr_is_abandoned <state> <mergedAt> -> 0 (true) when a PR can NEVER satisfy its
# item: CLOSED without ever being merged. Pure; the caller owns the gh query.
#
# 2026-08-04 (i506): the item's acceptance criteria required PROVING a new CI gate
# job actually fails, so the implementer opened a deliberately-broken scratch PR
# (#510, branch `i506-scratch-break-leg`), showed it red, closed it, and then
# opened the real PR (#511, `i506-plugins-gate`). #510 was open for 3.5 minutes and
# its branch carries the item code, so branch-code discovery matched it legitimately
# -- and nothing ever re-evaluated that choice once it closed unmerged. The item
# tracked a closed PR to the cap and reported `outcome=failed pr=510` while the real
# PR sat green and mergeable. Any item whose criteria demand a demonstrated failure
# can reproduce this, so the tracked PR must be re-checked, not trusted once.
_pr_is_abandoned() {
  # A PR CLOSED without merging can never satisfy its item. `gh` reports an
  # unmerged PR's mergedAt as empty OR the string "null" depending on the
  # query, and both mean the same thing -- reading "null" as a timestamp would
  # mark every closed-unmerged PR merged. Rule and tests in
  # v2/coordinator/pr_selection.py.
  _coordinator pr-abandoned --state "$1" --merged "${2:-}"
}

# _read_note <file> -> the signal file's text, flattened to one line for JSONL,
# capped, and EXPLICITLY MARKED when the cap bites.
#
# Replaces a bare `head -c 300`, which had two faults. (a) It cut mid-word with no
# marker, so a scorecard note read as a complete sentence that simply stopped --
# 2026-08-04 logged two rows at exactly 300 chars ending "...switching vend" and
# "...waive vendor directive", and the lost text was the operator's only record of
# WHY an item escalated. (b) `head -c` counts BYTES, so it can split a multi-byte
# UTF-8 character and emit invalid UTF-8 into the JSONL. Bash ${#s}/${s:0:n} are
# character-based, so the cut always lands on a character boundary.
#
# The cap stays (scorecard rows are one JSONL line each; unbounded notes bloat the
# file) but is generous, and a truncated note now says so and points at the source.
# _merge_gate <had_marker:0|1> <marker_head> -> "pin|<sha>" | "skip"
#
# Decides how (or whether) an outcome=ready item may be merged in-run. Pure, so the
# cases can be asserted directly — they are easy to conflate and the failure mode of
# conflating them is silent.
#
#   head present -> pin  : merge atomically against the reviewed commit.
#   no head      -> skip : the commit that was reviewed is unverifiable. Leave the
#                          PR for a human.
#
# The `skip` case is the point of issue #24. It previously passed an empty sha to
# merge_ready_pr, which silently took its UNPINNED branch and merged anyway while the
# log claimed the PR had been left for a human.
#
# #66 REMOVED a third case. `no marker -> unpinned` existed because the ground-truth
# recovery path has no marker by definition and "never carried a reviewed sha", so
# failing closed would have broken every recovery. That premise no longer holds:
# recovery now captures the head itself at dispatch time and returns it, so a missing
# head means the same thing on every path — we cannot say what was reviewed — and gets
# the same answer. `had_marker` is retained in the signature for its callers but no
# longer changes the decision.
_merge_gate() {
  local head="${2:-}"
  if [ -n "$head" ]; then printf 'pin|%s' "$head"; return 0; fi
  printf 'skip'
}

_read_note() {
  local f="$1" cap="${BIRCHER_NOTE_MAX:-1200}" raw
  [ -f "$f" ] || return 0
  raw=$(tr '\n' ' ' < "$f" 2>/dev/null)
  if [ "${#raw}" -gt "$cap" ]; then
    printf '%s... [truncated %d chars]' "${raw:0:$cap}" "$(( ${#raw} - cap ))"
  else
    printf '%s' "$raw"
  fi
}

# _select_pr_candidate <signal_pr> <matching_prs_string> -> one of
# use-signal|<pr>, use-the-one-match|<pr>, no-match|, ambiguous/escalate|<prs>.
# Pure selection only: the caller owns any gh query and the .escalated write.
_select_pr_candidate() {
  # An explicit signal wins; otherwise EXACTLY ONE match is required and two or
  # more escalate rather than picking -- choosing would be a guess about which
  # PR an item produced, and a wrong guess merges someone else's work under
  # this item's name. Rule and tests in v2/coordinator/pr_selection.py.
  local out=""
  out=$(_coordinator pr-select --signal "$1" --matches "$2") || out=""
  # A failed call must not read as "no PR". Escalating is the answer that
  # stops rather than the one that quietly proceeds.
  [ -n "$out" ] || out="ambiguous/escalate|selection unavailable"
  printf '%s\n' "$out"
}

# _item_issue <prompt-text> -> the issue number from an `Issue: #<n>` header, or empty.
_item_issue() {
  printf '%s\n' "$1" | grep -iE '^Issue:[[:space:]]*#[0-9]+' | head -1 | grep -oE '[0-9]+' | head -1
}

# _discover_pr_by_issue <issue_num> -> open-PR number(s), one per line ("" if none).
# LAST-RESORT PR->item mapping: fires ONLY when neither the coordinator's explicit
# <code>.pr signal nor a branch-name code match found the PR. Every issue-driven
# item is REQUIRED (muesli-loop step 3) to put `Closes #N` (or Fixes/Resolves #N)
# in its PR body, so this recovers a PR whose branch AND signal were named after
# the WRONG code -- run #24 (2026-07-14): item i230's implementer branched
# `a06-release-assets-v2` and wrote `a06.pr` after the "A6" epic tag in the title,
# so `_pr_signal i230` + the i230 branch match both missed the (green, ready) PR
# and the run stalled ~45min. Matching by the issue linkage is code-name-agnostic.
_discover_pr_by_issue() {
  local issue="$1"
  [ -n "$issue" ] || return 0
  gh pr list --repo "$REPO" --state open --search "$issue in:body" \
    --json number,body 2>/dev/null | python3 -c '
import json, re, sys
try: prs = json.load(sys.stdin)
except Exception: sys.exit(0)
issue = sys.argv[1]
pat = re.compile(r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*:?\s*#" + re.escape(issue) + r"\b")
for pr in prs:
    if pat.search(pr.get("body") or ""):
        print(pr["number"])
' "$issue"
}

# _writeback_plan <outcome> -> "add_label|remove_label|verb" for the issue write-back.
# ready/noop close via the PR's `Closes #N`; we only clear bircher:running.
# escalated/failed/timeout keep the issue OPEN and flag it.
_writeback_plan() {
  case "$1" in
    ready)              echo "|bircher:running|done" ;;
    noop|skipped)       echo "|bircher:running|noop" ;;
    escalated)          echo "bircher:escalated|bircher:running|escalated" ;;
    failed|timeout)     echo "bircher:escalated|bircher:running|failed" ;;
    *)                  echo "|bircher:running|$1" ;;
  esac
}

# _issue_writeback <issue> <outcome> <pr> <review> <rounds>: comment the scorecard
# line on the issue and set/clear status labels. No-op if issue empty or writeback off.
_issue_writeback() {
  # THE FIFTH ARGUMENT IS RESUBMISSIONS, and it was labelled `rounds=` in the
  # issue comment -- the exact conflation Phase 2 emptied the scorecard field to
  # avoid, still live on the channel a human actually reads. Repair rounds are
  # now their own seventh argument and their own label.
  local issue="$1" outcome="$2" pr="$3" review="$4" resubmissions="$5" ci_first="$6"
  local rounds="${7:-}"
  [ -n "$issue" ] || return 0
  [ "${BIRCHER_ISSUE_WRITEBACK:-1}" = "1" ] || return 0
  local plan add rm; plan=$(_writeback_plan "$outcome"); IFS='|' read -r add rm _ <<EOF
$plan
EOF
  # #6: build the comment from only the fields that have a value, so a noop/
  # escalated write-back reads "bircher: outcome=noop" instead of a malformed
  # "... rounds=? pr=" with bare/empty tails.
  local body="bircher: outcome=$outcome"
  [ -n "$ci_first" ] && body="$body ci_first=$ci_first"
  [ -n "$review" ]   && body="$body review=$review"
  [ -n "$resubmissions" ] && body="$body resubmissions=$resubmissions"
  [ -n "$rounds" ]   && body="$body rounds=$rounds"
  [ -n "$pr" ]       && body="$body pr=#$pr"
  _effect comment "issue-outcome:$issue:$(printf '%s' "$body" | shasum -a 256 | cut -c1-16)" - gh issue comment "$issue" --repo "$REPO" --body "$body" >/dev/null 2>&1 || true
  [ -n "$rm" ]  && _effect issue_or_label "unlabel:$issue:$rm" - gh issue edit "$issue" --repo "$REPO" --remove-label "$rm"  >/dev/null 2>&1 || true
  [ -n "$add" ] && _effect issue_or_label "label:$issue:$add" - gh issue edit "$issue" --repo "$REPO" --add-label "$add"    >/dev/null 2>&1 || true
}

# _ensure_issue_closed <issue> <pr>: safety-net for the `Closes #N` auto-close
# (bircher #3). After a CONFIRMED PR merge, GitHub normally closes the linked
# issue via `Closes #N` in the PR body -- but occasionally it does not fire
# (observed on muesli #33/#35). Wait a grace period for GitHub's own close, then
# close the issue ourselves if it is still open. Idempotent, and gated on the PR
# actually being MERGED so it never closes a deferred/failed item.
_ensure_issue_closed() {
  local issue="$1" pr="$2"
  [ -n "$issue" ] && [ -n "$pr" ] || return 0
  [ "${BIRCHER_ISSUE_WRITEBACK:-1}" = "1" ] || return 0
  [ "$(gh pr view "$pr" --repo "$REPO" --json state -q '.state' 2>/dev/null)" = "MERGED" ] || return 0
  sleep "${BIRCHER_AUTOCLOSE_GRACE_S:-5}"
  [ "$(gh issue view "$issue" --repo "$REPO" --json state -q '.state' 2>/dev/null)" = "OPEN" ] || return 0
  _effect issue_or_label "close-issue:$issue" - gh issue close "$issue" --repo "$REPO" \
    --comment "Safety-net close: PR #$pr merged but GitHub did not auto-close this issue via \`Closes #$issue\`; the work is on main (bircher #3)." >/dev/null 2>&1 || true
  echo "[batch] safety-net: closed issue #$issue after PR #$pr merged (auto-close missed)" >&2
}

# preflight_auth -> rc 0 if BOTH providers respond to a trivial call; rc 1 else.
# The 2026-06-22 run wasted ~30h after codex's /root/.codex/auth.json went
# 7-days stale mid-run (ops runner-resilience findings) -> every codex
# reviewer hit "Timed out waiting for Codex app-server socket". Fail fast HERE,
# before launching the queue, instead of letting every item time out.
# Skip with SKIP_PREFLIGHT=1; tune the per-probe timeout with PREFLIGHT_TIMEOUT.
preflight_auth() {
  # #5: honor SKIP_PREFLIGHT only for an ATTENDED (interactive TTY) invocation.
  # An unattended/detached run (no controlling TTY -- the overnight launch runs
  # with stdin </dev/null and stdout to a log) MUST probe: a stale codex/claude
  # auth would otherwise silently waste the whole batch (the 2026-06-22 ~30h loss).
  if [ -n "${SKIP_PREFLIGHT:-}" ]; then
    if [ -t 0 ] || [ -t 1 ]; then
      echo "[batch] preflight: skipped (SKIP_PREFLIGHT set, attended TTY)"; return 0
    fi
    echo "[batch] preflight: SKIP_PREFLIGHT IGNORED on an unattended run (no TTY) -> probing anyway" >&2
  fi
  local t="${PREFLIGHT_TIMEOUT:-60}" ok=1
  echo "[batch] preflight: probing claude + codex auth (timeout ${t}s each)..."
  # Claude (claude-sdk coordinator brain + worker): trivial headless call.
  if timeout "$t" claude -p "Reply with the single word READY." >/tmp/preflight-claude.txt 2>&1 \
     && grep -qi 'ready' /tmp/preflight-claude.txt; then
    echo "[batch] preflight: claude OK"
  else
    echo "[batch] preflight: !!! CLAUDE auth/health FAILED (tail of /tmp/preflight-claude.txt):" >&2
    tail -n 3 /tmp/preflight-claude.txt >&2 2>/dev/null; ok=0
  fi
  # Codex (codex worker): file-based ChatGPT OAuth, expires ~7d, silently.
  # --skip-git-repo-check: codex refuses to run outside a trusted git dir.
  if timeout "$t" codex exec --skip-git-repo-check "Reply with the single word READY." >/tmp/preflight-codex.txt 2>&1 \
     && grep -qi 'ready' /tmp/preflight-codex.txt; then
    echo "[batch] preflight: codex OK"
  else
    echo "[batch] preflight: !!! CODEX auth/health FAILED -- likely stale /root/.codex/auth.json; run 'codex login' on the runner (tail of /tmp/preflight-codex.txt):" >&2
    tail -n 3 /tmp/preflight-codex.txt >&2 2>/dev/null; ok=0
  fi
  [ "$ok" = 1 ] || { echo "[batch] preflight FAILED -> refusing to start the queue; fix auth then re-run" >&2; return 1; }
  echo "[batch] preflight OK -> both providers healthy"
}

# The model codex workers must be dispatched with. Kept in step with the
# DEPLOYMENT PIN directive in config.yaml -- change both together.
BIRCHER_CODEX_MODEL="${BIRCHER_CODEX_MODEL:-gpt-5.6-sol}"

# preflight_dispatch: prove a worker can actually LAUNCH THROUGH THE HARNESS.
#
# preflight_auth probes the CLIs directly (`claude -p`, `codex exec`), which is a
# DIFFERENT question from whether omnigent can dispatch a worker. On 2026-08-04 it
# reported "codex OK" -- correctly, the CLI was healthy and self-reported
# `model: gpt-5.6-sol` -- and then all 4 items died at dispatch with
# `400 ... The 'gpt-5.6' model is not supported when using Codex with a ChatGPT
# account`: omnigent resolved the bare family name `gpt-5.6`, and its curated
# catalog spells the variants with hyphens (`gpt-5-6-sol`) where the real IDs use
# dots. Both spellings 400. A CLI-only probe cannot see any of that.
# Upstream: omnigent-ai/omnigent#4063.
preflight_dispatch() {
  [ -n "${SERVER:-}" ] || { echo "[batch] preflight: no SERVER set -> skipping dispatch probe" >&2; return 0; }
  local t="${PREFLIGHT_DISPATCH_TIMEOUT:-180}" ok=1
  local harness spec out attempt
  for spec in "codex|--model $BIRCHER_CODEX_MODEL" "claude|"; do
    harness="${spec%%|*}"
    local extra="${spec#*|}"
    for attempt in 1 2; do
      # A cold server can lose the first dispatch to warmup (documented: 2x
      # `400 Bad Request` on /token then a clean connect ~70s in), so one retry
      # before calling it a failure -- but never more: a real breakage must not
      # be retried into a long stall.
      out=$(timeout "$t" omnigent run --harness "$harness" $extra \
        -p "reply READY and stop" --server "$SERVER" 2>&1)
      case "$out" in
        *READY*) break ;;
      esac
      [ "$attempt" = 1 ] && echo "[batch] preflight: $harness dispatch attempt 1 did not return READY (cold server?) -> retrying" >&2
    done
    case "$out" in
      *READY*) echo "[batch] preflight: $harness dispatch OK${extra:+ ($extra)}" ;;
      *) ok=0
         echo "[batch] preflight: !!! $harness DISPATCH FAILED${extra:+ ($extra)} -- the CLI may be fine while the harness cannot launch a worker:" >&2
         printf '%s\n' "$out" | tail -n 4 >&2 ;;
    esac
  done
  [ "$ok" = 1 ] || { echo "[batch] preflight FAILED at dispatch -> refusing to start the queue (every item would die at launch)" >&2; return 1; }
}

# json_row item pr outcome ci_first review rounds wall note bound [implementer]
# #4: implementer is optional (last arg) so pre-launch call sites can omit it;
# recording it makes the cross-vendor pairing (implementer vs review) auditable.
json_row() {
  python3 - "$@" <<'PY'
import json,sys,datetime
a=sys.argv[1:]
item,pr,outcome,ci_first,review,resubmissions,wall,note,bound=a[:9]
implementer=a[9] if len(a)>9 else ""
# The 11th field, and it is REPAIR ROUNDS -- not the 6th, which has been
# `resubmissions` under a parameter misleadingly named `rounds` since Phase 2.
repair_rounds=a[10] if len(a)>10 else ""
print(json.dumps({
 "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
 "item": item, "pr": int(pr) if pr.isdigit() else None, "outcome": outcome,
 "implementer": implementer or None, "review": review or None,
 "ci_pass_first_try": ci_first=="true",
 # `rounds` was the coordinator's own count of its fix loop, asserted in a
 # marker and observed by nothing, so Phase 2 emitted it null rather than
 # quietly redefining it. It now carries REPAIR ROUNDS -- how many times this
 # runner dispatched a repair -- which is observed, because the runner
 # performed each one. Still null when the loop is disabled or never engaged,
 # so a reader comparing an old row to a new one is not misled: a number here
 # always means "a repair happened", never "the loop ran and found nothing".
 "rounds": int(repair_rounds) if repair_rounds.isdigit() else None,
 "resubmissions": int(resubmissions) if resubmissions.isdigit() else None,
 "wall_seconds": int(wall) if wall.isdigit() else None, "cost": None,
 "bound": bound or None, "note": note}))
PY
}

# _local_host_id
# Read the bircher runner's stable host_id from /root/.omnigent/config.yaml.
# This script runs inside the omnigent-runner-bircher container, so that file
# holds exactly the bircher runner's host_id (e.g. host_83b59621...).
_local_host_id() {
  local cfg="/root/.omnigent/config.yaml"
  [ -f "$cfg" ] || return 1
  # host_id: is indented under the top-level "host:" key, so do NOT anchor with ^.
  grep -m1 'host_id:' "$cfg" | awk '{print $2}'
}

# _host_ids_match <session_host_id> <local_host_id> -> yes|no   (PURE, self-tested)
#
# The two sides report the same host in different shapes: the runner's
# config.yaml stores the PREFIXED form (host_<uuid>) while omnigent v0.6.0's
# GET /v1/sessions/<id> returns the BARE <uuid>. A literal comparison therefore
# never matched on v0.6.0 and every item was scored bound=failed -- an alarm
# that always fires detects nothing (#26). Compare on the normalized form so a
# genuine mismatch is still caught whichever shape either side sends.
#
# An empty/unknown session host is NOT a match: an unverifiable binding must
# stay a failure, not be waved through.
_host_ids_match() {
  local a="${1#host_}" b="${2#host_}"
  if [ -n "$a" ] && [ "$a" = "$b" ]; then echo "yes"; else echo "no"; fi
}

run_item() {
  local f="$1"; local item; item=$(basename "$f" .md)
  local code; code=$(printf '%s' "$item" | cut -d- -f1 | tr 'A-Z' 'a-z')  # item code, e.g. a06

  # RC-1 guard: NEVER launch a session with an empty prompt. The 2026-06-22 run
  # lost ~30h because a vanished queue file (a second instance moved it) made
  # `cat` return nothing, so `omnigent run -p ""` spawned a task-less coordinator
  # that idled the full ITEM_TIMEOUT. A missing file is recorded and skipped (no
  # `mv` -- there is nothing to move); a present-but-blank file is moved aside.
  if [ ! -f "$f" ]; then
    echo "[batch] SKIP $item: queue file vanished before processing (concurrent instance?); not launching" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "skipped" "false" "" "" 0 "queue file missing at read time; not launched" "n/a" >> "$SCORECARD"
    return 0
  fi
  local prompt; prompt=$(cat "$f")
  if _is_blank "$prompt"; then
    echo "[batch] SKIP $item: queue file is empty/blank; not launching" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "skipped" "false" "" "" 0 "empty/blank prompt; not launched" "n/a" >> "$SCORECARD"
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/" 2>/dev/null || true
    return 0
  fi
  echo "[batch] === $item ==="
  # The kernel's record of this run. Item codes recur across attempts, so the
  # epoch makes each attempt its own aggregate.
  BIRCHER_RUN_ID="${item}-$(date +%s)"; export BIRCHER_RUN_ID
  BIRCHER_KERNEL_DB="${BIRCHER_KERNEL_DB:-$BUNDLE_DIR/.run/kernel.db}"
  export BIRCHER_KERNEL_DB
  mkdir -p "$(dirname "$BIRCHER_KERNEL_DB")" 2>/dev/null || true
  # enqueue: creates this run's row in the kernel. See
  # batch/lib/kernel-client.sh's _kernel_run_start for why this is not
  # `_kernel command --name enqueue` (that command name does not exist).
  local _base_sha; _base_sha=$(git -C "$WORKDIR" rev-parse HEAD 2>/dev/null)
  # Defaulted ONCE, here, rather than at the call below: the verdict binding
  # must present the base the kernel actually recorded, and `validate_review`
  # compares them. Defaulting at the call site left the binding free to send
  # the empty string while the run held forty zeros -- a mismatch the reviewer
  # could not have caused and could not have fixed.
  : "${_base_sha:=0000000000000000000000000000000000000000}"
  _kernel_run_start "$BIRCHER_RUN_ID" "$REPO" "$_base_sha"
  local _iss; _iss=$(_item_issue "$prompt")
  # B-3 vendor dispatch: resolve THIS item's implementer and flip the reviewer to
  # the opposite vendor (cross-vendor integrity is invariant). A per-item queue tag
  # `bircher-implementer: <vendor>` wins over the runner-level PICKED_VENDOR (set by
  # the usage-aware gate / BIRCHER_IMPLEMENTER). muesli-loop step 3 honors the
  # directive line; the reviewer agent is selected via RECOVERY_REVIEWER below.
  local vendor tag
  tag=$(printf '%s\n' "$prompt" | grep -iE '^[[:space:]]*bircher-implementer:' | head -1 \
        | sed -E 's/.*:[[:space:]]*//' | tr -d '[:space:]' | tr 'A-Z' 'a-z')
  case "$tag" in
    claude_code|claude) vendor=claude_code ;;
    codex)              vendor=codex ;;
    *)                  vendor="${PICKED_VENDOR:-$IMPLEMENTER}" ;;
  esac
  [ "$vendor" = auto ] && vendor=claude_code   # never dispatch the literal 'auto'
  if [ "$vendor" = codex ]; then RECOVERY_REVIEWER=claude_code; else RECOVERY_REVIEWER=codex; fi
  # WORK REPO DIRECTIVE. The agent bundles spell their worktree setup with a
  # LITERAL /workspaces/muesli (agents/codex/config.yaml,
  # agents/claude_code/config.yaml), so an implementer working any other target
  # would branch from -- and push to -- muesli regardless of WORKDIR. That made
  # a throwaway-repo end-to-end run unsafe: the only reason tonight's smoke
  # dropped to --recover-pr, which launches no implementer.
  #
  # Sent as a directive rather than fixed in the bundles because the bundle is
  # uploaded as static text and WORKDIR is only known here, per run. It is
  # stated unconditionally, not only when WORKDIR differs from the default: a
  # directive that appears only in the unusual case is one nobody has read when
  # the unusual case arrives.
  prompt="IMPLEMENTER VENDOR DIRECTIVE: dispatch the implement sub-agent to ${vendor}; the cross-vendor reviewer MUST be the opposite vendor (${RECOVERY_REVIEWER}). Do not set any model or model_override.

WORK REPO DIRECTIVE: the repository for this task is ${REPO}, checked out at ${WORKDIR}. This OVERRIDES any path written in your agent bundle. Wherever the bundle says /workspaces/muesli, read ${WORKDIR}. Set your isolated worktree up with:
    git -C ${WORKDIR} fetch origin main
    git -C ${WORKDIR} worktree add -b <code>-<slug> /tmp/wt-<code> origin/main
and open the pull request against ${REPO}. Do not fetch, branch from, push to, or open a pull request against any other repository.

${prompt}"
  echo "[batch] $item: implementer=$vendor reviewer=$RECOVERY_REVIEWER" >&2
  # REST launch: create the run session bound to the bircher host (deterministic
  # conv_id from the create response - no discovery heuristic), then send the
  # prompt. (omnigent/server/API.md: Create From Existing Agent + Post Event.)
  # The implementer attempt is dispatched HERE, as soon as the vendor is known
  # and BEFORE the session exists. It used to sit after session creation, which
  # left every effect between run start and that point with no generation --
  # and in kernel mode `${BIRCHER_GENERATION:?}` aborts the call, so the
  # `bircher:running` label below was SILENTLY dropped (its `|| true` swallowed
  # the failure) while legacy mode applied it. On the second item of a run it
  # was worse than dropped: BIRCHER_GENERATION is exported, so the stale value
  # from the previous item would have attributed this item's label effect to
  # another run. Dispatching here also gives the session-create failure exit a
  # generation to record a terminal outcome under.
  BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
  export BIRCHER_GENERATION
  # Now that a generation exists, the label is a routed effect like any other.
  [ -n "$_iss" ] && _effect issue_or_label "running:$_iss" - gh issue edit "$_iss" --repo "$REPO" --add-label bircher:running --remove-label bircher:queued >/dev/null 2>&1 || true
  local host_id conv_id bound_outcome="ok"
  host_id=$(_local_host_id 2>/dev/null) || host_id=""
  conv_id=$(_create_session "$AGENT_ID" "$host_id" "$WORKDIR")
  if [ -z "$conv_id" ]; then
    echo "[batch] $item: REST session create FAILED; recording failed" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    # The run was started at _kernel_run_start above, so without this the
    # kernel would sit at `queued` while the scorecard says `failed` -- a
    # criterion-1 divergence previously waved through as an exemption on the
    # grounds that no generation existed. One now does, because the dispatch
    # moved above session creation.
    _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "failed"
    json_row "$item" "" "failed" "false" "" "" 0 "REST session create failed" "failed" >> "$SCORECARD"
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi
  echo "[batch] $item: session $conv_id (agent $AGENT_ID)"
  # The queue item's prompt, PUT once as the spec artifact and reused as a
  # stand-in plan artifact (v1 has no separate plan document -- see
  # kernel-client.sh's submit_plan wrapper for why that reuse is deliberate).
  local _spec_hash; _spec_hash=$(_kernel_put_artifact "$prompt")
  # Declared at RUN scope, not inside the marker branch that assigns it.
  # It was a `local` in that branch and is read at the merge gate below, which
  # every path reaches -- so the no-marker recovery path died on `set -u` with
  # "_out_hash: unbound variable", after the implementer session had already
  # opened its PR. Empty is the correct value there: nothing recorded an
  # implementation output, so there is no artifact to bind, and the client
  # refuses an incomplete binding rather than sending one.
  local _out_hash=""
  _kernel_submit_spec "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_spec_hash"
  _kernel_submit_plan "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_spec_hash"
  # start_implementation
  _kernel_start_implementation "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION"
  # Binding check: we set host_id in create; confirm the session bound to THIS runner.
  local sess_host; sess_host=$(_http_json GET "/v1/sessions/$conv_id" | _json_get host_id)
  if [ -n "$host_id" ] && [ "$(_host_ids_match "$sess_host" "$host_id")" != "yes" ]; then
    bound_outcome="failed"
    echo "[batch] !!!! BINDING MISMATCH for $item: session host_id='${sess_host:-<empty>}' != local='$host_id'" >&2
  fi
  # #61: a failed delivery used to warn and carry on. The session exists but has
  # NO prompt, so the coordinator idles, ground-truth recovery finds nothing, and
  # the queue file is moved to processed at the end -- consuming an item that was
  # never worked. Stop the session and leave the queue file QUEUED instead.
  #
  # NOTE the budget is per RUN, not persistent: a permanently undeliverable item
  # keeps its queue file and gets a fresh budget next run. That is deliberate --
  # losing the item is worse -- but it means a genuinely dead endpoint churns
  # session create/stop once per run until someone looks. No dead-letter state
  # exists yet (codex review).
  if ! _send_prompt "$conv_id" "$prompt"; then
    # rc 5, NOT rc 3. rc 3 means "usage limit -> re-gate and retry", and its
    # caller loops on it unboundedly (the quota budget only bounds consecutive
    # PREFLIGHT failures, and is re-declared each iteration). A deterministic
    # delivery failure -- server down, bad payload -- would therefore spin
    # forever creating sessions (codex review). rc 5 carries its own small,
    # bounded retry budget in the caller.
    echo "[batch] $item: send_prompt FAILED -> stopping session; item stays queued (rc 5)" >&2
    _stop_session "$conv_id"
    return 5
  fi

  local start; start=$(date +%s); local pr="" elapsed=0 polls=0 _unknown_polls=0
  # Settle-detection state: the last observed item count and how many
  # consecutive quiet polls we have seen. The judgement lives in
  # coordinator.session.settle; the loop only carries these two values.
  local _settle_count="" _settle_polls=0
  while [ "$elapsed" -lt "$ITEM_TIMEOUT" ]; do
    sleep "$POLL"; elapsed=$(( $(date +%s) - start )); polls=$((polls + 1))
    # B-2 within-item fast limit check: only in the early window (first 2 polls).
    # If the coordinator's opening turn is a provider "hit your limit" message the
    # vendor window is exhausted -- stop the session, DO NOT consume the queue
    # file, and return rc 3 so main re-gates and retries the SAME item on the
    # other vendor (or after the reset). Never fires later (a mid-run limit
    # mention in normal output would false-positive).
    if [ "$polls" -le 2 ] && [ -n "$conv_id" ]; then
      if [ "$(_is_limit_message "$(_last_assistant_text "$conv_id" 3)")" = yes ]; then
        echo "[batch] $item: provider limit message in opening turn -> stop + re-gate (rc 3)" >&2
        _stop_session "$conv_id"
        return 3
      fi
    fi
    if [ -z "$pr" ]; then
      # B-6: prefer the coordinator's EXPLICIT PR signal -- the coordinator knows
      # THIS item's code (from the prompt) and writes the PR number to
      # <code>.pr, so mapping is deterministic and immune to an implementer that
      # names its branch after the skill's EXAMPLE code (CAL06 #277 branch
      # 'a06-...' vs code 'cal06' broke the branch match below and stalled a
      # coupled wave). Prefer the signal outright; otherwise inspect the
      # branch-prefix matches and either take the single match, keep polling, or
      # escalate on ambiguity.
      local pr_signal pr_matches pr_decision
      pr_signal=$(_pr_signal "$code")
      if [ -n "$pr_signal" ]; then
        pr="$pr_signal"
        rm -f "$NOOP_DIR/$code.pr" 2>/dev/null  # consume-once: a stale signal must not misattribute to a later same-coded item
      else
        # Match THIS item's PR by its code in the head branch - STRICT match only.
        # The old "newest new PR" fallback misattributed twice (2026-06-28 H03
        # latched #64; 2026-07-05 SUM02 credited to SUM03), so it was removed.
        pr_matches=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
          -q "$(_branch_code_filter "$code") | .[].number" 2>/dev/null)
        # Branch match empty too -> last-resort issue-linkage fallback (run #24
        # a06-vs-i230: branch+signal named after the epic tag, not the item code).
        if [ -z "$pr_matches" ] && [ -n "$_iss" ]; then
          pr_matches=$(_discover_pr_by_issue "$_iss")
          [ -n "$pr_matches" ] && echo "[batch] $item: no signal/branch code match; mapped PR via issue #$_iss linkage (Closes #$_iss)" >&2
        fi
        pr_decision=$(_select_pr_candidate "" "$pr_matches")
        case "$pr_decision" in
          use-the-one-match\|*) pr=${pr_decision#use-the-one-match|} ;;
          no-match\|*) ;;
          ambiguous/escalate\|*)
            mkdir -p "$NOOP_DIR"
            printf '%s\n' "multiple open PRs match branch prefix $code: ${pr_decision#ambiguous/escalate|} and no .pr signal to disambiguate" \
              > "$NOOP_DIR/$code.escalated"
            break
            ;;
        esac
      fi
    fi
    # NO COMPLETION SIGNAL FROM THE COORDINATOR, and none is wanted: a v2
    # implementer holds no comment authority, so there is nothing it could post
    # here even if something read it. What replaced the marker is an
    # OBSERVATION -- the session has gone quiet.
    #
    # Removing the marker without this cost item s07 twenty-three minutes of
    # pure waiting (measured 136s -> 1536s): the work finished in about four
    # and the run then sat until its cap, because nothing said "done".
    #
    # Requires a PR to already exist. Quiet with NO PR is a session that failed
    # to deliver, and that belongs to the cap -- ending early there would
    # convert a silent failure into a fast one without learning anything.
    if [ -n "$pr" ] && [ -n "$conv_id" ]; then
      local _sr
      # `${SERVER:-}`: an unset server makes the call fail immediately, which
      # `|| _sr=""` turns into "no settle" and the loop falls back to the cap.
      # That is the right degradation -- waiting too long is survivable, and
      # `set -u` killing run_item mid-item is not.
      _sr=$(_coordinator session-settle --server "${SERVER:-}" --id "$conv_id" \
              --prev-count "$_settle_count" --stable-polls "$_settle_polls" \
              --needed "${BIRCHER_SETTLE_POLLS:-4}") || _sr=""
      if [ -n "$_sr" ]; then
        _settle_count="${_sr%%|*}"; _sr="${_sr#*|}"
        _settle_polls="${_sr%%|*}"
        if [ "${_sr#*|}" = yes ]; then
          echo "[batch] $item: PR #$pr open and session quiet for $_settle_polls polls -> deriving now rather than waiting for the cap" >&2
          break
        fi
      fi
    fi
    # No-op signal: the coordinator decided the item is already satisfied (gap #3)
    # and dropped a marker here instead of forcing a (garbage) PR.
    [ -f "$NOOP_DIR/$code.noop" ] && break
    # Escalation-without-PR signal: the coordinator escalated (confidence gate /
    # unmet dependency) and there is no PR to carry a marker (run #13 SRC01b
    # burned its whole cap invisibly). Stop waiting and record it honestly.
    [ -f "$NOOP_DIR/$code.escalated" ] && break
    # Session-aware completion: stop waiting if the SERVER session has DIED.
    # idle is NOT death (coordinator awaiting a sub-agent wake), so only an
    # error/failed/cancelled session ends the wait here; a healthy long run
    # continues to the cap.
    if [ -n "$conv_id" ]; then
      local _ss; _ss=$(_session_state "$conv_id")
      if [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = died ]; then
        echo "[batch] $item: session $conv_id died (state=$_ss) -> stop waiting" >&2
        break
      fi
      # #61: a run of failed lookups means we are flying blind. Breaking out early
      # to recover (the first cut of this fix) is worse: the same unreachable
      # server that caused the unknowns also makes `_stop_session` fail, so death
      # is never CONFIRMED and recovery races a coordinator that may still be
      # working the PR. So keep the old control flow -- but note that waiting to
      # the cap only NARROWS that window, it does not close it (an earlier
      # comment here claimed it "cannot corrupt anything", which was wrong).
      # The teardown below closes it, by refusing to recover while blind.
      if [ "${_ss%%|*}" = unknown ]; then
        _unknown_polls=$((_unknown_polls + 1))
        if [ "$_unknown_polls" = "${BIRCHER_UNKNOWN_POLL_LIMIT:-5}" ]; then
          echo "[batch] WARN $item: session state UNKNOWN for $_unknown_polls consecutive polls (server unreachable?) -- still waiting to the cap; death cannot be confirmed so recovery must not start early" >&2
        fi
      else
        _unknown_polls=0
      fi
    fi
  done
  # If we exited the loop without a noop signal and the server session is still
  # ALIVE -- because the cap fired, or because the session went quiet and we
  # ended early -- cancel it via the API so it actually stops. Killing the local
  # client alone does NOT stop the server-side session, and a live coordinator
  # would otherwise race the review the derivation is about to dispatch.
  #
  # This matters MORE since settle detection: a quiet session is idle, not
  # finished, so the run now routinely reaches here with a live session that
  # could still wake up.
  local _blind=0
  if [ ! -f "$NOOP_DIR/$code.noop" ] && [ ! -f "$NOOP_DIR/$code.escalated" ] && [ -n "$conv_id" ]; then
    local _ss; _ss=$(_session_state "$conv_id")
    if [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = alive ]; then
      # Says WHY, because "cap reached" was printed even when the loop ended
      # early on a quiet session -- a false statement in the log of every fast
      # item, and the sort that gets believed later.
      local _why="cap reached"
      [ "${_settle_polls:-0}" -ge "${BIRCHER_SETTLE_POLLS:-4}" ] && _why="session settled"
      echo "[batch] $item: $_why, session $conv_id still alive -> cancelling" >&2
      _stop_session "$conv_id"
      local _w=0
      while [ "$_w" -lt 30 ]; do
        sleep 3; _w=$((_w + 3))
        _ss=$(_session_state "$conv_id")
        [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = died ] && break
      done
      # #61: if the state is STILL unknown, the stop was never confirmed and the
      # coordinator may be alive and working. Ground-truth recovery reads AND
      # WRITES the PR, so running it here can race a live session. Refuse, and
      # escalate to a human instead -- an escalation costs attention, a race
      # costs a corrupted PR. `alive` with a real status (running/idle) is a
      # different case: we reached the server, it answered, the stop was
      # delivered, and the existing behaviour is unchanged.
      if [ "${_ss%%|*}" = unknown ]; then
        _blind=1
        echo "[batch] WARN $item: session $conv_id state still UNKNOWN after cancel -- cannot confirm it stopped; SKIPPING ground-truth recovery to avoid racing a live coordinator" >&2
      fi
    fi
  fi
  # Teardown: the session is server-side (no local client to kill). If it is
  # still alive here (e.g. we broke early), stop it so it can't run on untracked.
  if [ -n "$conv_id" ]; then
    local _ss; _ss=$(_session_state "$conv_id")
    [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = alive ] && _stop_session "$conv_id"
  fi

  # No-op exit: the coordinator signalled "already satisfied; no change needed" ->
  # record a noop (not a false timeout) and advance without forcing a PR (gap #3).
  if [ -f "$NOOP_DIR/$code.noop" ]; then
    local nnote; nnote=$(_read_note "$NOOP_DIR/$code.noop")
    rm -f "$NOOP_DIR/$code.noop"
    mkdir -p "$(dirname "$SCORECARD")"
    # The run is over: close its ledger before the scorecard row. The kernel's
    # terminal fact is written first, but the two are NOT guaranteed to agree:
    # `_kernel` is advisory and always returns 0, so a failed or refused command
    # leaves no terminal fact while the scorecard row below is written anyway.
    # An earlier version of this comment claimed they "agree by construction",
    # which is the unearned-claim shape this change exists to remove.
    _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "noop"
    json_row "$item" "" "noop" "" "" "" "$elapsed" "${nnote:-already satisfied; no product change needed}" "$bound_outcome" "$vendor" >> "$SCORECARD"
    echo "[batch] $item -> outcome=noop (no change needed)"
    _issue_writeback "$(_item_issue "$prompt")" "noop" "" "" "" ""
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi

  # Escalated-without-PR exit: the coordinator hit the confidence gate or an
  # unmet dependency and there is no PR to carry a marker. Record an honest
  # `escalated` row (not a false timeout) and advance (run #13 SRC01b).
  if [ -f "$NOOP_DIR/$code.escalated" ]; then
    local enote; enote=$(_read_note "$NOOP_DIR/$code.escalated")
    rm -f "$NOOP_DIR/$code.escalated"
    mkdir -p "$(dirname "$SCORECARD")"
    # The run is over: close its ledger before the scorecard row. The kernel's
    # terminal fact is written first, but the two are NOT guaranteed to agree:
    # `_kernel` is advisory and always returns 0, so a failed or refused command
    # leaves no terminal fact while the scorecard row below is written anyway.
    # An earlier version of this comment claimed they "agree by construction",
    # which is the unearned-claim shape this change exists to remove.
    _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "escalated"
    json_row "$item" "${pr:-}" "escalated" "false" "" "" "$elapsed" "${enote:-coordinator escalated without a PR}" "$bound_outcome" "$vendor" >> "$SCORECARD"
    echo "[batch] $item -> outcome=escalated (no PR; reason: ${enote:-n/a})"
    _issue_writeback "$(_item_issue "$prompt")" "escalated" "${pr:-}" "" "" ""
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi

  local outcome ci_first review rounds note resubmissions observed_head _obs_ci
  # AT RUN_ITEM SCOPE, indent 2, not inside the branch that assigns them.
  #
  # `_rev_key` feeds a kernel binding and `_rev_round` is read below at the
  # scorecard, which every path reaches -- including the `_blind` path that
  # never enters the derivation branch. Declared inside that branch they would
  # be unbound there, and `set -u` kills run_item AFTER the implementer has
  # already opened its PR. That is not hypothetical: it is what `_out_hash`
  # did, and `test_binding_variables_are_declared_at_run_item_scope` exists
  # because of it -- it caught this one too, by indent, after I had already
  # moved them once for the same test and satisfied only half of what it says.
  local _rev_key=""
  local _rev_round=0
  if [ "${_blind:-0}" = 1 ]; then
    # Unchanged from the marker era, and still correct: the cancel was never
    # confirmed, so the coordinator may still be running. Deriving an outcome
    # means READING and WRITING the PR, which races a live session.
    outcome="escalated"; review="na"; ci_first="unknown"; resubmissions=""
    observed_head=""
    note="server unreachable at cap; could not confirm the session stopped, so outcome derivation was skipped to avoid racing a live coordinator - needs a human"
    echo "[batch] $item: blind at teardown -> escalating without derivation" >&2
  else
    # THE REPAIR LOOP. Derive; if the coordinator says the reviewer found
    # blocking problems and rounds remain, dispatch a repair and derive again.
    #
    # Measured basis: 8 of 18 muesli item-runs ended `failed` on a reviewer
    # FAIL, every one a specific actionable finding. Routing them back by hand
    # merged #740 in one round and #750 in two.
    #
    # The allowance is re-read FROM THE JOURNAL every round, never carried in a
    # variable, so a coordinator that dies and is re-driven gets no fresh
    # rounds. `_max_revisions` of 0 makes `revise` unreachable and this loop
    # runs exactly once -- the pre-loop behaviour, byte for byte.
    # ONE `local` PER NAME, deliberately. `test_binding_variables_are_declared
    # _at_run_item_scope` matches `local <var>`, so a name riding second on a
    # shared `local` line reads to it as undeclared -- and it caught exactly
    # that here. The check exists because `_out_hash` was once declared inside
    # the branch that assigned it and killed run_item on `set -u` AFTER the
    # implementer had opened its PR, so satisfying it by splitting the line is
    # the honest fix and loosening its regex would not be.
    local obs
    local _rev_left=0
    local _rev_state=""
    local _findings=""
    local _last_finding=""
    local _ffile; _ffile=$(_findings_path "$code")
    [ -n "$_ffile" ] && mkdir -p "$NOOP_DIR"
    while :; do
    # RESET EVERY ROUND, at the top, before anything can be read.
    #
    # `_rev_key` is assigned inside the `observed_head` branch below, so a round
    # that skips that branch would otherwise still be holding the PREVIOUS
    # round's key -- and the durability check would confirm this round's
    # revision using last round's fact, dispatching a repair for a round the
    # kernel never opened. Same class as a stale findings file, and invisible
    # for the same reason: every signal looks normal.
    _rev_key=""
    _rev_left=0
    if [ "$(_max_revisions)" != 0 ] && [ -n "${BIRCHER_RUN_ID:-}" ]; then
      # `used|left|confirmed`. A LOOKUP FAILURE leaves _rev_left at 0, which
      # ends the loop and escalates -- deliberately, because the alternative
      # reading of an unreadable journal is "no revisions used yet", and that
      # hands the loop a full allowance every round and never terminates.
      _rev_state=$(_coordinator revisions --db "${BIRCHER_KERNEL_DB:-}" \
                     --run-id "$BIRCHER_RUN_ID" --max "$(_max_revisions)") || _rev_state=""
      if [ -n "$_rev_state" ]; then
        _rev_left="${_rev_state#*|}"; _rev_left="${_rev_left%%|*}"
        _rev_left=$(_clamp_int "$_rev_left" 0 0 5)
      else
        echo "[batch] $item: could not read the revision allowance from the journal -> no repair rounds this derivation" >&2
      fi
    fi
    echo "[batch] $item: deriving the outcome from the repository (repair rounds left: $_rev_left)" >&2
    obs=$(observe_outcome "$item" "$code" "$pr" "$_iss" "$_rev_left" "$_ffile")
    # An EMPTY tuple is a CRASH, not a verdict -- and since Phase 2 this is the
    # ONLY path, so the guard that used to protect recovery alone now protects
    # every item. `obs=$(...)` swallows a mid-function death into an empty
    # string, which parses as outcome="" and reports "NOT ready": a crash
    # wearing a verdict's clothes.
    if [ -z "${obs//[[:space:]]/}" ]; then
      echo "[batch] $item: derivation produced NO tuple -> it failed; escalating rather than reading it as a verdict" >&2
      obs="escalated|na|outcome derivation failed (no tuple); needs a human||na|unknown||"
    fi
    # EIGHT fields. The last is the PR the DERIVATION settled on, which is not
    # always the one passed in: it discards an abandoned PR, discovers one by
    # code or issue linkage, and adopts a CI-green sibling. Reading seven
    # absorbed the eighth into `resubmissions` silently -- `read` does not
    # error on a short variable list, it concatenates the remainder into the
    # last name.
    # WIDTH CHECKED BEFORE PARSING. A short tuple leaves `_settled_pr` empty,
    # which used to mean "keep the PR I started with" -- indistinguishable from
    # a derivation that legitimately settled on nothing.
    if ! _derived_width_ok "$obs"; then
      echo "[batch] $item: derivation returned a malformed tuple (not eight fields on one line) -> escalating rather than guessing which field is which" >&2
      obs="escalated|na|derivation returned a malformed tuple; needs a human||na|unknown||"
    fi
    IFS='|' read -r outcome review note observed_head _obs_ci ci_first resubmissions _settled_pr <<EOF
$obs
EOF
    : "${outcome:=timeout}" "${ci_first:=unknown}"
    # ADOPT IT BEFORE THE MERGE AUTHORIZATION, not after. `$pr` feeds the
    # kernel merge command, the merge itself and the scorecard line, so a
    # stale value authorizes one PR and reports another. On muesli #723 the
    #
    # (The kernel command is not named literally here on purpose:
    # `test_the_merge_request_redispatches_as_implementer` slices this
    # function between the FIRST occurrence of two identifiers, so naming one
    # in a comment above the real call gives it an empty slice to search.)
    # derivation reviewed #738, posted its cross-review there, and the caller
    # then tried to merge #737 -- which the implementer had already closed.
    if [ -n "${_settled_pr:-}" ] && [ "${_settled_pr}" != "${pr:-}" ]; then
      echo "[batch] $item: derivation settled on PR #$_settled_pr (was ${pr:-none}) -> adopting" >&2
      pr="$_settled_pr"
    fi

    # The kernel lifecycle, unchanged in order and in roles. Its INPUTS are now
    # observations; the sequence is the one the marker branch used to drive.
    #
    # GUARDED ON A HEAD, and that guard does the work: the blind path above
    # never reaches here, so a bare "na" verdict cannot arrive at
    # _kernel_record_review from this function.
    if [ -n "${observed_head:-}" ]; then
      local _body="derived: outcome=$outcome review=$review head=$observed_head note=$note"
      _out_hash=$(_kernel_record_output "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_body")
      _kernel_record_ci "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "${_obs_ci:-na}" "$observed_head"
      BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer)
      export BIRCHER_GENERATION
      # The key is passed ONLY on a revise. Every other path keeps
      # `kernel.cli`'s own default key, so the accept and reject branches are
      # unchanged -- which is what makes BIRCHER_MAX_REVISIONS=0 a real
      # rollback rather than a path that merely usually agrees.
      _rev_key=""
      [ "$outcome" = revise ] && \
        _rev_key="revise:${BIRCHER_RUN_ID}:${_rev_round}:${BIRCHER_GENERATION}"
      _kernel_record_review "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$review" \
        "$_out_hash" "$_base_sha" "$_spec_hash" "$_rev_key"
      BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
      export BIRCHER_GENERATION
    fi

    # Not a revision -> the derivation is final and the loop ends.
    [ "$outcome" != revise ] && break

    # A REVISION IS OWED. Before dispatching any repair work, confirm the
    # kernel actually RECORDED it, by its causal id.
    #
    # Not the adapter's exit code and not the absence of an error: `_kernel` is
    # advisory and always returns 0, and `commands.py` validates a review, bumps
    # the version under CAS, then appends REVIEW_VERDICT -- in that order -- so a
    # review can validate and then lose the CAS, producing no fact at all. Either
    # way the run is still in `reviewing`, `start_implementation` would be
    # refused, and the repair session would work against a run that never
    # authorised it.
    _rev_state=$(_coordinator revisions --db "${BIRCHER_KERNEL_DB:-}" \
                   --run-id "$BIRCHER_RUN_ID" --max "$(_max_revisions)" \
                   --confirm-command "$_rev_key") || _rev_state=""
    if ! _revision_is_recorded "$_rev_state"; then
      echo "[batch] $item: the revision was NOT recorded in the journal (${_rev_state:-lookup failed}) -> escalating instead of repairing" >&2
      outcome="escalated"
      note="${note:+$note; }reviewer found blocking problems but the kernel did not record the revision (causal id $_rev_key); no repair was dispatched"
      break
    fi

    # The findings the repair is briefed on. The file exists BECAUSE this
    # derivation wrote it -- the coordinator unlinks the path before deriving
    # and replaces it atomically after -- so an empty or missing one here means
    # something went wrong in this round, never that a previous round is
    # speaking.
    _findings=""
    [ -s "$_ffile" ] && _findings=$(cat "$_ffile")
    if _is_blank "$_findings"; then
      echo "[batch] $item: a revision is owed but no findings were written -> escalating rather than dispatching a repair with an empty brief" >&2
      outcome="escalated"
      note="${note:+$note; }reviewer found blocking problems but wrote no findings to brief a repair with"
      break
    fi
    _last_finding=$(printf '%s' "$_findings" | head -c 400)
    _rev_round=$((_rev_round + 1))
    echo "[batch] $item: review FAILED with $_rev_left round(s) left -> repair round $_rev_round" >&2
    local _rbranch; _rbranch=$(_pr_branch "$pr")
    if [ -z "${_rbranch//[[:space:]]/}" ]; then
      # Without the branch the prompt cannot name what to push to, and an
      # implementer left to infer it opens a second PR -- which strands the
      # reviewed one and makes the next derivation escalate on ambiguity
      # instead of on this.
      echo "[batch] $item: could not read PR #$pr's head branch -> escalating rather than briefing a repair that cannot push" >&2
      outcome="escalated"
      note="${note:+$note; }could not read PR #$pr's head branch to brief a repair round"
      break
    fi
    if ! _repair_round "$item" "$code" "$pr" "$_rbranch" "$_findings" "$_rev_round" "$vendor"; then
      outcome="escalated"
      note="${note:+$note; }repair round $_rev_round could not be started"
      break
    fi
    done
    [ -n "$_ffile" ] && { rm -f "$_ffile" 2>/dev/null || true; }
    # TERMINAL ESCALATION names what the last reviewer objected to, so a human
    # sees the finding instead of having to open N review logs to find it.
    if [ "$_rev_round" != 0 ]; then
      note="${note:+$note; }after $_rev_round repair round(s)"
      [ "$outcome" != ready ] && [ -n "$_last_finding" ] && \
        note="$note; last finding: $_last_finding"
    fi
  fi
  # `rounds` REPORTS SOMETHING AGAIN, and it is a different measurement from the
  # one that used to bear the name. It was the coordinator's count of its own
  # internal fix loop and nothing observed it, so it was emptied. It is now the
  # number of REPAIR ROUNDS this runner dispatched -- observed, because the
  # runner performed each one, and cross-checkable against the run's
  # request_revision facts. `resubmissions` keeps its own name and meaning
  # (distinct commits CI ran on, minus one), which is still a third thing.
  #
  # Empty, not 0, when the loop is disabled: a 0 would claim the loop ran and
  # found nothing to repair, which is not what BIRCHER_MAX_REVISIONS=0 means.
  rounds=""
  [ "$_rev_round" != 0 ] && rounds="$_rev_round"

  # B-1 in-run merge: merge a ready PR now so the NEXT item builds on it
  # (eliminates the merge-order conflict class). Deferral appends to the note;
  # a red/unresolved main after merge HALTS the run (rc 2 propagates to main).
  local merge_rc=0
  if [ "$INRUN_MERGE" != "0" ] && [ "$outcome" = "ready" ] && [ -n "${pr:-}" ]; then
    # The reviewed head comes from the REVIEWER (marker `head=`), never from a
    # re-fetch here (issue #24). A `gh pr view` after the PASS would record whatever
    # the head is NOW — so a push landing between the reviewer's verdict and this
    # line would be blessed as "reviewed", defeating the --match-head-commit guard
    # it feeds. Only the reviewer knows which commit it actually read.
    #
    # Fail closed when a marker exists but carries no head=. NOTE: passing an empty
    # sha to merge_ready_pr used to NOT fail closed — it took an unpinned branch and
    # merged anyway — so the skip happens HERE, before the call. Since #66 the callee
    # also refuses an empty sha, so this is now belt and braces rather than the only
    # guard.
    # `_merge_gate` reads only its SECOND argument; the first was a
    # had-a-marker flag it never consulted, and there is no marker now.
    local _gate; _gate=$(_merge_gate "" "${observed_head:-}")
    local reviewed_sha=""
    case "$_gate" in
      pin\|*)   reviewed_sha="${_gate#pin|}" ;;
        skip)
        echo "[batch] $item: no reviewed head available -> reviewed commit unverifiable; NOT merging PR #$pr (left for a human)" >&2
        MERGE_NOTE="merge skipped: no reviewed head (reviewed commit unverifiable)"
        note="${note:+$note; }$MERGE_NOTE"
        merge_rc=0
        ;;
    esac
    if [ "$_gate" != "skip" ]; then
      # request_merge, record_merge_outcome
      _kernel_request_merge "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$pr" "$REPO" "$observed_head" \
        "$_out_hash" "$_base_sha" "$_spec_hash"
      merge_ready_pr "$item" "$pr" "$reviewed_sha"; merge_rc=$?
      local _k_outcome; [ "$merge_rc" = 0 ] && _k_outcome=merged || _k_outcome=failed
      _kernel_record_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_k_outcome"
      [ -n "$MERGE_NOTE" ] && note="${note:+$note; }$MERGE_NOTE"
      # #71: what LANDED was not what was reviewed. rc 2 already halts the run, but the
      # scorecard row is written from $outcome -- captured from the item's MARKER, not
      # from merge_rc -- and _ensure_issue_closed fires on outcome=ready. Left alone, an
      # unreviewed merge would halt AND still close its issue as done; worse, on the
      # red path it would close the very issue _reopen_reverted_issues had just
      # reopened. Downgrading the outcome stops both, because the close is gated on it.
      if [ "${MERGE_UNREVIEWED:-0}" = 1 ]; then
        note="${note:+$note; }$MERGE_UNREVIEWED_NOTE"
        outcome=escalated
      fi
      _record_deferred_ready "$item" "$pr" "$merge_rc" "$_iss" "$reviewed_sha"
    fi
  fi

  mkdir -p "$(dirname "$SCORECARD")"
  # The run is over: close its ledger before the scorecard row. The kernel's
  # terminal fact is written first, but the two are NOT guaranteed to agree:
  # `_kernel` is advisory and always returns 0, so a failed or refused command
  # leaves no terminal fact while the scorecard row below is written anyway.
  # An earlier version of this comment claimed they "agree by construction",
  # which is the unearned-claim shape this change exists to remove.
  #
  # This site could once diverge on VALUE: $outcome came from a model-authored
  # marker parsed with no schema validation, so a word outside the kernel's
  # vocabulary was refused -- correctly and visibly -- while the scorecard
  # recorded it regardless. Since Phase 2 the value comes from
  # `classify_recovery`, which emits only the fixed vocabulary, so that
  # particular divergence has no route left. The two records can still
  # disagree for other reasons, which is why this note stays.
  _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$outcome"
  json_row "$item" "${pr:-}" "$outcome" "$ci_first" "${review:-}" "${resubmissions:-}" "$elapsed" "$note" "$bound_outcome" "$vendor" "${rounds:-}" >> "$SCORECARD"
  _issue_writeback "$(_item_issue "$prompt")" "$outcome" "${pr:-}" "${review:-}" "${resubmissions:-}" "${ci_first:-}" "${rounds:-}"
  # #3: guarantee the issue closes when its PR actually merged (backstops a
  # missed GitHub `Closes #N` auto-close). No-op unless outcome=ready + PR merged.
  [ "$outcome" = "ready" ] && _ensure_issue_closed "$(_item_issue "$prompt")" "${pr:-}"
  echo "[batch] $item -> outcome=$outcome pr=${pr:-none} review=${review:-na} rounds=${rounds:-?} bound=$bound_outcome"
  mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
  return "$merge_rc"
}

self_test() {
  # v1 orchestration is what this exercises, so effects execute directly. The
  # adapter's default is `deny` -- correct for a real run, where an unset
  # variable must not silently restore v1 authority -- and it would turn every
  # behavioural assertion below into a refusal. Set here rather than left to the
  # caller so `--self-test` means the same thing however it is invoked.
  export BIRCHER_EFFECT_MODE="${BIRCHER_EFFECT_MODE:-legacy}"
  # #62: the wrappers now REFUSE to run a network command when no timeout(1) exists,
  # which is right in production and would otherwise make this whole suite unrunnable on
  # a box without GNU coreutils (stock macOS). Give it a passthrough so every existing
  # test still exercises the wrapper rather than skipping it. Blocks that assert on the
  # wrapper's ARGUMENTS prepend their own recording shim, which wins over this one.
  if ! command -v timeout >/dev/null 2>&1 && ! command -v gtimeout >/dev/null 2>&1; then
    _ST_TO_DIR=$(mktemp -d)
    printf '%s\n' '#!/usr/bin/env bash' \
      'if [ "$1" = "-k" ]; then shift 2; fi' \
      'shift' \
      'exec "$@"' > "$_ST_TO_DIR/timeout"
    chmod +x "$_ST_TO_DIR/timeout"
    PATH="$_ST_TO_DIR:$PATH"; export PATH
    echo "[self-test] no timeout(1) on this box -> using a passthrough shim (the runner has GNU coreutils)" >&2
  fi
  # Capture the SHIPPED defaults before overriding them for speed -- the #62 assertions
  # below check the defaults, not whatever the suite is running with.
  _DEF_SETTLE="$MAIN_CI_SETTLE_TIMEOUT"; _DEF_RERUN="$MAIN_CI_TIMEOUT"; _DEF_ABS="$MAIN_CI_ABSOLUTE_DEADLINE"
  # Splitting the budgets (#62) left MAIN_CI_TIMEOUT bounding only the RERUN loop, so the
  # per-test `MAIN_CI_TIMEOUT=31` no longer bounds the initial watch -- every watched
  # merge silently started costing a 30s poll, and an unparseable green would have cost
  # an hour. Bound the settle loop and shrink the interval for the whole suite.
  MAIN_CI_SETTLE_TIMEOUT=5; MAIN_CI_POLL_INTERVAL=1
  # CALL-SITE STRUCTURE FIRST. These are instant, and they must run before any test that
  # could hang: stripping the merge-sha clamp makes a later behavioural test hot-loop
  # for two hours, so placed after it these assertions would never be reached.
  # STRUCTURAL, because the behavioural version of this one cannot fail fast. Strip the
  # clamp from the merge-sha retry and `[ n -ge abc ]` errors every iteration, so the
  # break never fires and the loop hot-polls the API until the 7200s deadline: the test
  # "catches" it by hanging for two hours. A hang is not a regression test -- it is slow,
  # it says nothing about what broke, and it burns API quota to say it. This fails in
  # milliseconds and names the call site.
  _cs=$(declare -f merge_ready_pr)
  _contains "$_cs" '_sha_max=$(_clamp_int' \
    || { echo "FAIL #62: the merge-sha retry count must go through _clamp_int (a raw value hot-loops to the deadline)"; exit 1; }
  for _fn in merge_ready_pr _rerun_main_ci _poll_ci; do
    _cs=$(declare -f "$_fn")
    _contains "$_cs" 'MAIN_CI_POLL_INTERVAL' || continue
    printf '%s\n' "$_cs" | grep 'MAIN_CI_POLL_INTERVAL' | grep -qv '_clamp_int' \
      && { echo "FAIL #62: $_fn uses MAIN_CI_POLL_INTERVAL without _clamp_int"; exit 1; }
  done
  unset _cs _fn
  # --- #22: one boundary-anchored branch-code filter, not three copies --------
  local bf; bf=$(_branch_code_filter i23)
  case "$bf" in *'(^|[^a-z0-9])i23([^a-z0-9]|$)'*) : ;; *) echo "FAIL branch filter: '$bf'"; exit 1 ;; esac
  # The anchors are the whole point: i23 must NOT match branch i230-foo.
  local bfout
  bfout=$(printf '%s' '[{"headRefName":"i230-foo"},{"headRefName":"i23-bar"}]' \
    | jq -r "$(_branch_code_filter i23) | .[].headRefName" 2>/dev/null)
  [ "$bfout" = "i23-bar" ] || { echo "FAIL branch filter: prefix collision, got '$bfout'"; exit 1; }
  echo "_branch_code_filter OK (#22)"
  # --- #24 + #66: the merge gate must fail closed without a reviewed head -------
  # Regression guard. An earlier cut passed an empty sha to merge_ready_pr believing
  # that failed closed; merge_ready_pr's else-branch merged UNPINNED while the log
  # said the PR was left for a human. The skip is decided before the call, and since
  # #66 the callee refuses an empty sha too.
  [ "$(_merge_gate 1 a502a88e20f959c908d00871ee7f25572512dd6d)" = "pin|a502a88e20f959c908d00871ee7f25572512dd6d" ] \
    || { echo "FAIL merge_gate: head must pin"; exit 1; }
  [ "$(_merge_gate 1 '')" = "skip" ] \
    || { echo "FAIL merge_gate: no head must skip, never merge unpinned"; exit 1; }
  # #66 removed the `unpinned` outcome. It existed because ground-truth recovery had
  # no marker and "never carried a reviewed sha" -- recovery now captures the head
  # itself, so a missing head means the same thing on every path and gets the same
  # answer. A recovery WITH a head pins; without one it skips.
  [ "$(_merge_gate 0 '')" = "skip" ] \
    || { echo "FAIL merge_gate: recovery without a head must skip, not merge unpinned"; exit 1; }
  [ "$(_merge_gate 0 a502a88e20f959c908d00871ee7f25572512dd6d)" = "pin|a502a88e20f959c908d00871ee7f25572512dd6d" ] \
    || { echo "FAIL merge_gate: recovery WITH a head must pin"; exit 1; }
  case "$(_merge_gate 0 '')$(_merge_gate 1 '')" in
    *unpinned*) echo "FAIL merge_gate: the unpinned outcome must no longer exist"; exit 1 ;;
  esac
  echo "_merge_gate OK (#24 fail-closed)"
  # --- non-CI check-runs must not turn main red -------------------------------
  # Regression guard for 2026-08-07 (i520): merging .github/dependabot.yml added 28
  # check-runs named "Dependabot" to the merge commit, 2 failed, and the run declared
  # a green main red — then tried to revert it.
  cr=$(printf '%s\n' 'Dependabot|completed|failure' 'server (go)|completed|success' 'Dependabot|completed|success')
  [ "$(_drop_non_ci_checkruns "$cr")" = "completed|success" ] \
    || { echo "FAIL drop_non_ci: dependabot runs must be filtered, got '$(_drop_non_ci_checkruns "$cr")'"; exit 1; }
  [ "$(_checkrun_state "$(_drop_non_ci_checkruns "$cr")")" = "green" ] \
    || { echo "FAIL drop_non_ci: a green CI beside a failed Dependabot run must read green"; exit 1; }
  # A REAL CI failure must still read red — the filter must not swallow those.
  cr2=$(printf '%s\n' 'Dependabot|completed|success' 'server (go)|completed|failure')
  [ "$(_checkrun_state "$(_drop_non_ci_checkruns "$cr2")")" = "red" ] \
    || { echo "FAIL drop_non_ci: a real CI failure must still be red"; exit 1; }
  # Names containing the ignored word must NOT be dropped (anchored match).
  cr3='Dependabot Config Check|completed|failure'
  [ -n "$(_drop_non_ci_checkruns "$cr3")" ] \
    || { echo "FAIL drop_non_ci: only an exact name match may be ignored"; exit 1; }
  # --- #41: CI runs come from check LINKS, never from branch recency ----------
  # 2026-08-08: `gh run list --branch <ref> --limit 1` returned whichever workflow
  # sorted first. muesli runs CI and `Review Gate` in the SAME SECOND, so a genuine
  # red was classified `infra` (steps counted in the workflow that succeeded) and the
  # wrong run was re-run. Seen on PR #560, then live on #568.
  local rl ids
  rl=$(printf '%s\n' \
    'server (go)|https://github.com/o/r/actions/runs/111/job/9' \
    'client (node)|https://github.com/o/r/actions/runs/111/job/8' \
    'review-gate|')
  ids=$(_run_ids_from_check_links "$rl")
  [ "$ids" = "111" ] \
    || { echo "FAIL #41: expected only CI run 111, got '$ids'"; exit 1; }
  # The exact #560 shape: a SUCCEEDING non-CI workflow must never be the run we
  # inspect. If 222 leaks through, _ci_failure_kind counts its zero failed steps
  # and reports a real failure as infra — the whole defect.
  rl=$(printf '%s\n' \
    'server (go)|https://github.com/o/r/actions/runs/111/job/9' \
    'Dependabot|https://github.com/o/r/actions/runs/222/job/7')
  ids=$(_run_ids_from_check_links "$rl")
  [ "$ids" = "111" ] \
    || { echo "FAIL #41: non-CI workflow run must be filtered, got '$ids'"; exit 1; }
  # Several CI workflows on one PR: keep them all, deduped and stable. Assuming a
  # single run is what made the old code wrong.
  rl=$(printf '%s\n' \
    'server (go)|https://github.com/o/r/actions/runs/300/job/1' \
    'client (node)|https://github.com/o/r/actions/runs/100/job/2' \
    'lighthouse|https://github.com/o/r/actions/runs/300/job/3')
  ids=$(_run_ids_from_check_links "$rl" | tr '\n' ',')
  [ "$ids" = "100,300," ] \
    || { echo "FAIL #41: expected deduped 100,300, got '$ids'"; exit 1; }
  # Commit statuses carry no link; they must not yield a bogus id.
  [ -z "$(_run_ids_from_check_links 'review-gate|')" ] \
    || { echo "FAIL #41: a linkless status must yield no run id"; exit 1; }
  [ -z "$(_run_ids_from_check_links 'some-status|not-a-url')" ] \
    || { echo "FAIL #41: an unparseable link must yield no run id"; exit 1; }
  echo "_run_ids_from_check_links OK (#41)"
  # _poll_ci passes "name|bucket", not "name|status|conclusion". Same filter,
  # different shape — assert it rather than assuming it generalises.
  #
  # THE DEADLOCK GUARD: a pending `review-gate` must NOT make CI read pending.
  # review-gate stays pending until a cross-vendor review is posted, and the code
  # calling _poll_ci is the thing about to post it — so counting it made the
  # recovery path wait forever (muesli PR #549, with every other check green).
  local cr4 cr5 cr6
  cr4=$(printf '%s\n' 'server (go)|pass' 'review-gate|pending' 'client (node)|pass')
  [ "$(_normalize_ci "$(_drop_non_ci_checkruns "$cr4")")" = "green" ] \
    || { echo "FAIL poll_ci filter: a pending review-gate must not block CI (deadlock)"; exit 1; }
  # ...but a genuinely pending CI check must still read pending.
  cr5=$(printf '%s\n' 'server (go)|pending' 'review-gate|pending')
  [ "$(_normalize_ci "$(_drop_non_ci_checkruns "$cr5")")" = "pending" ] \
    || { echo "FAIL poll_ci filter: real pending CI must still be pending"; exit 1; }
  # ...and a real CI failure must still read red.
  cr6=$(printf '%s\n' 'server (go)|fail' 'review-gate|pending')
  [ "$(_normalize_ci "$(_drop_non_ci_checkruns "$cr6")")" = "red" ] \
    || { echo "FAIL poll_ci filter: real CI failure must still be red"; exit 1; }
  echo "_drop_non_ci_checkruns OK (i520 false-red + #549 deadlock)"
  # --- #43: only checks that actually gate a merge may turn CI red --------------
  # 2026-08-08: muesli`s `coverage report (informational)` is NOT a required context.
  # The wave path asks GitHub `mergeable` (required-only) and merged; the recovery path
  # asked this code (any-check) and refused the identical state. Worse, the main-CI
  # watcher shares this filter, so a non-required red on main was one poll away from
  # auto-reverting a healthy commit -- exactly i520, with a different check.
  local req blk
  req=$(printf '%s\n' 'server (go)' 'client (node)' 'review-gate')
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|pass' 'coverage report (informational)|fail')" "$req")
  [ "$(_normalize_ci "$blk")" = green ] \
    || { echo "FAIL #43: a failing NON-required check must not be red, got '$(_normalize_ci "$blk")'"; exit 1; }
  # ...and a required one still must be.
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|fail' 'coverage report (informational)|pass')" "$req")
  [ "$(_normalize_ci "$blk")" = red ] \
    || { echo "FAIL #43: a failing REQUIRED check must still be red"; exit 1; }
  # THE DEADLOCK GUARD. review-gate IS a required context, so an allowlist alone would
  # re-admit it -- and it stays pending until the review this very caller is about to
  # post. required-MINUS-ignored is what keeps #549 fixed; assert it explicitly.
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|pass' 'review-gate|pending')" "$req")
  [ "$(_normalize_ci "$blk")" = green ] \
    || { echo "FAIL #43: review-gate must stay filtered even though it is required (#549)"; exit 1; }
  # Unknown required (no protection / no permission / request failed) -> fall back to
  # the old ignore-list-only behaviour, which reads a stray failure as RED. Fail closed:
  # inverting this would make every genuinely red PR look green.
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|pass' 'coverage report (informational)|fail')" "")
  [ "$(_normalize_ci "$blk")" = red ] \
    || { echo "FAIL #43: unknown required-set must fail CLOSED (red), got '$(_normalize_ci "$blk")'"; exit 1; }
  # Allowlist matching is exact, not substring -- "server (go) extra" is a different
  # check. A real required context is present too, so this exercises exact matching
  # WITHOUT tripping the no-match fallback below (which would legitimately return the
  # unfiltered set and mask what this is asserting).
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|pass' 'server (go) extra|fail')" "$req")
  [ "$(_normalize_ci "$blk")" = green ] \
    || { echo "FAIL #43: 'server (go) extra' must not match 'server (go)' (got '$(_normalize_ci "$blk")')"; exit 1; }
  # A required-set matching NO check must not read as "no checks" -- that is pending
  # forever, or a hidden red. Fall back to ignore-list-only instead.
  blk=$(_keep_blocking_checks "$(printf '%s\n' 'server (go)|fail')" "$(printf '%s\n' 'totally-different-context')")
  [ "$(_normalize_ci "$blk")" = red ] \
    || { echo "FAIL #43: a required-set matching nothing must fall back, not vanish (got '$(_normalize_ci "$blk")')"; exit 1; }
  echo "_keep_blocking_checks OK (#43)"
  local row; row=$(json_row demo 7 ready true codex:pass 0 800 "ok" ok codex)
  printf '%s' "$row" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["item"]=="demo" and d["pr"]==7 and d["ci_pass_first_try"] is True and d["cost"] is None and d["bound"]=="ok" and d["implementer"]=="codex", d; print("json_row OK (incl. #4 implementer)")'
  local row2; row2=$(json_row demo2 "" timeout false "" "" 900 "" failed)
  printf '%s' "$row2" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["bound"]=="failed" and d["pr"] is None, d; print("json_row bound=failed OK")'
  # RC-1: _is_blank gates the empty-prompt guard.
  _is_blank ""        || { echo "FAIL _is_blank: empty"; exit 1; }
  _is_blank $'  \n\t' || { echo "FAIL _is_blank: whitespace-only"; exit 1; }
  _is_blank "x"       && { echo "FAIL _is_blank: non-blank treated as blank"; exit 1; }
  _is_blank $' # A01 ' && { echo "FAIL _is_blank: real prompt treated as blank"; exit 1; }
  echo "_is_blank OK"
  # RC-1: a skipped item produces a valid scorecard row.
  local row3; row3=$(json_row demo3 "" skipped false "" "" 0 "empty/blank prompt; not launched" "n/a")
  printf '%s' "$row3" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["outcome"]=="skipped" and d["pr"] is None and d["wall_seconds"]==0 and d["bound"]=="n/a" and d["implementer"] is None, d; print("json_row skipped OK (implementer omitted -> null)")'
  # --- Layer-2 recovery: pure decision helpers -------------------------------
  RECOVERY_REVIEWER=codex   # make the asserts deterministic regardless of env
  # #65: the old contract was "last matching token anywhere wins", and this test
  # asserted it -- so a closing restatement silently flipped an auto-merge. Now
  # anchored on the contract the reviewer is actually given: the verdict is the
  # FINAL LINE, and must be exactly that.
  [ "$(_extract_verdict $'findings here\nVERDICT: PASS' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: single PASS"; exit 1; }
  [ "$(_extract_verdict $'findings here\nVERDICT: FAIL' 2>/dev/null)" = "FAIL" ] \
    || { echo "FAIL _extract_verdict: single FAIL"; exit 1; }
  # Trailing whitespace/blank lines after the verdict are normal agent output.
  [ "$(_extract_verdict $'findings\nVERDICT: PASS   \n\n' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: trailing blank lines"; exit 1; }
  # Decoration real agents emit must NOT escalate (kimi, on its own suggestion:
  # byte-exact anchoring is brittle against markdown).
  [ "$(_extract_verdict $'findings\n**VERDICT: PASS**' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: markdown-bold verdict"; exit 1; }
  [ "$(_extract_verdict $'findings\nVERDICT: FAIL.' 2>/dev/null)" = "FAIL" ] \
    || { echo "FAIL _extract_verdict: trailing full stop"; exit 1; }
  [ "$(_extract_verdict $'findings\n`VERDICT: PASS`' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: code-ticked verdict"; exit 1; }
  # Decoration and punctuation COMBINED, in both orders. The first cut stripped
  # all decoration then punctuation, so `\`VERDICT: FAIL\`.` failed while
  # `\`VERDICT: FAIL.\`` passed -- no test combined them, so it went unnoticed.
  [ "$(_extract_verdict $'findings\n`VERDICT: FAIL`.' 2>/dev/null)" = "FAIL" ] \
    || { echo "FAIL _extract_verdict: tick-then-period"; exit 1; }
  [ "$(_extract_verdict $'findings\n`VERDICT: FAIL.`' 2>/dev/null)" = "FAIL" ] \
    || { echo "FAIL _extract_verdict: period-inside-ticks"; exit 1; }
  [ "$(_extract_verdict $'findings\n**VERDICT: PASS.**' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: period inside bold"; exit 1; }
  [ "$(_extract_verdict $'findings\n****VERDICT: PASS****' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: four-char decoration"; exit 1; }
  # The accepted grammar is NARROW: ornament plus AT MOST ONE terminal mark.
  # Unlimited punctuation is not ornament, it is non-contractual output, and it
  # must not authorise a merge.
  [ "$(_extract_verdict $'findings\nVERDICT: PASS!!!' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: multi-punctuation must not authorise"; exit 1; }
  [ "$(_extract_verdict $'findings\nVERDICT: PASS...' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: ellipsis must not authorise"; exit 1; }
  [ "$(_extract_verdict $'findings\n****' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: decoration-only line"; exit 1; }
  [ "$(_extract_verdict $'findings\nNOT A VERDICT: PASS' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: prefixed line must not match"; exit 1; }
  # ...but decoration must not smuggle a verdict past a trailing restatement.
  [ "$(_extract_verdict $'VERDICT: PASS\n**but I would have said VERDICT: FAIL**' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: decorated restatement must still fail closed"; exit 1; }
  # A restatement AFTER the verdict must fail closed -- the exact flip that
  # motivated this change.
  [ "$(_extract_verdict $'VERDICT: PASS\nbut had X failed I would have said VERDICT: FAIL' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: trailing restatement must yield no verdict"; exit 1; }
  # Quoting the token WHILE explaining findings is legitimate and must not be
  # punished, so long as the final line is the real verdict.
  [ "$(_extract_verdict $'I considered VERDICT: FAIL here but the guard holds\nVERDICT: PASS' 2>/dev/null)" = "PASS" ] \
    || { echo "FAIL _extract_verdict: mid-findings mention must not block a valid final verdict"; exit 1; }
  [ "$(_extract_verdict 'no verdict token here' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: empty when absent"; exit 1; }
  [ "$(_extract_verdict '' 2>/dev/null)" = "" ] \
    || { echo "FAIL _extract_verdict: empty input"; exit 1; }
  # Empty input must be SILENT; a non-verdict final line should warn.
  [ -z "$(_extract_verdict '' 2>&1 >/dev/null)" ] \
    || { echo "FAIL _extract_verdict: empty input must not warn"; exit 1; }
  [ -n "$(_extract_verdict $'VERDICT: PASS\ntrailing prose' 2>&1 >/dev/null)" ] \
    || { echo "FAIL _extract_verdict: non-verdict final line must warn"; exit 1; }
  echo "_extract_verdict OK (#65 last-line anchored, fails closed)"
  [ "$(_normalize_ci $'pass\npass')"    = "green"   ] || { echo "FAIL _normalize_ci green"; exit 1; }
  [ "$(_normalize_ci $'pass\nfail')"    = "red"     ] || { echo "FAIL _normalize_ci red"; exit 1; }
  [ "$(_normalize_ci $'pass\npending')" = "pending" ] || { echo "FAIL _normalize_ci pending"; exit 1; }
  [ "$(_normalize_ci '')"               = "pending" ] || { echo "FAIL _normalize_ci empty->pending"; exit 1; }
  # SIGPIPE REGRESSION. This was `printf | grep -qE '^(fail|cancel)$'` under
  # `set -o pipefail`. grep exits the instant it matches, so if the producing printf
  # still has output buffered it takes SIGPIPE and the pipeline reports FAILURE despite
  # the match -- and the pending check dies the same way. Red CI was then reported
  # GREEN, on the merge-safety path.
  #
  # The input must EXCEED THE PIPE BUFFER (64KB on Linux) for the producer to still be
  # writing when grep exits; a few thousand short lines fit entirely inside it and never
  # reproduce the fault. Hence ~140KB of padding, built with awk because bash string
  # concatenation in a loop is quadratic and made this test the slowest thing in the
  # suite before I noticed.
  _pad=$(awk 'BEGIN{for(i=0;i<3000;i++) printf "pass-%039d\n", i}')
  [ "${#_pad}" -gt 65536 ] \
    || { echo "FAIL _normalize_ci: the SIGPIPE fixture (${#_pad} bytes) is under the pipe buffer and proves nothing"; exit 1; }
  [ "$(_normalize_ci "$(printf 'fail\n%s' "$_pad")")" = "red" ] \
    || { echo "FAIL _normalize_ci: a large list starting with 'fail' must be RED, not green (SIGPIPE regression)"; exit 1; }
  [ "$(_normalize_ci "$(printf 'pending\n%s' "$_pad")")" = "pending" ] \
    || { echo "FAIL _normalize_ci: a large list starting with 'pending' must be PENDING, not green"; exit 1; }
  [ "$(_normalize_ci "$(printf 'pending\nfail\n%s' "$_pad")")" = "red" ] \
    || { echo "FAIL _normalize_ci: red must outrank pending in a large list"; exit 1; }
  unset _pad
  [ "$(_classify_ci_failure 0)" = infra ]   || { echo "FAIL _classify_ci_failure 0->infra"; exit 1; }
  [ "$(_classify_ci_failure 3)" = genuine ] || { echo "FAIL _classify_ci_failure 3->genuine"; exit 1; }
  [ "$(_classify_ci_failure '')" = infra ]  || { echo "FAIL _classify_ci_failure empty->infra"; exit 1; }

  # --- #50: a revert must reopen what its merge closed --------------------------
  # The silence this removes: a failed LOOKUP previously read the same as "this PR
  # closed nothing", so a revert could leave a live defect marked fixed while the
  # log said all was well. 2026-08-12: that also silently unblocked a dependent
  # issue whose fix alone would have shipped a broken app.
  # A PATH SHIM, not a shell function. #62 routed these calls through `_net_run`, which
  # uses `timeout` -- and `timeout` execs a BINARY, so it cannot see a shell function.
  # On macOS `timeout` is absent and the wrapper falls through, so a function shim kept
  # passing locally while the same test failed on the Linux runner, where the real `gh`
  # was invoked instead of the stub. Modelling production (gh IS a binary) makes the
  # test exercise the wrapper rather than bypass it.
  local _g50; _g50=$(mktemp -d)
  cat >"$_g50/gh" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"--json closingIssuesReferences"*)
    [ "${_GH_LOOKUP_FAILS:-0}" = 1 ] && exit 4
    printf '%s\n' ${_GH_ISSUES:-} ;;
  *"issue reopen"*) [ "${_GH_REOPEN_FAILS:-0}" = 1 ] && exit 5; exit 0 ;;
  *) exit 0 ;;
esac
SH
  chmod +x "$_g50/gh"
  local _out
  export _GH_ISSUES _GH_LOOKUP_FAILS _GH_REOPEN_FAILS
  _GH_ISSUES="41 42" _out=$(PATH="$_g50:$PATH" _reopen_reverted_issues 99 2>&1)
  printf '%s' "$_out" | grep -q "reopened issue #41" \
    || { echo "FAIL #50: did not reopen the first closed issue"; exit 1; }
  printf '%s' "$_out" | grep -q "reopened issue #42" \
    || { echo "FAIL #50: did not reopen the second closed issue"; exit 1; }

  _GH_ISSUES="" _out=$(PATH="$_g50:$PATH" _reopen_reverted_issues 99 2>&1)
  printf '%s' "$_out" | grep -q "closed no issues" \
    || { echo "FAIL #50: a PR that closed nothing should say so"; exit 1; }
  printf '%s' "$_out" | grep -qi "WARN" \
    && { echo "FAIL #50: closing nothing is not a warning"; exit 1; }

  # The distinction that matters: a lookup FAILURE must not read as "nothing to do".
  _GH_LOOKUP_FAILS=1 _out=$(PATH="$_g50:$PATH" _reopen_reverted_issues 99 2>&1)
  printf '%s' "$_out" | grep -qi "WARN.*could NOT look up" \
    || { echo "FAIL #50: a failed lookup must warn, not report silence"; exit 1; }
  printf '%s' "$_out" | grep -q "closed no issues" \
    && { echo "FAIL #50: a failed lookup must NOT claim the PR closed nothing"; exit 1; }

  # NB: these are assignments, not command prefixes, so they persist between
  # cases -- reset the previous one explicitly or it leaks into this test.
  _GH_LOOKUP_FAILS=0 _GH_ISSUES="41" _GH_REOPEN_FAILS=1 _out=$(PATH="$_g50:$PATH" _reopen_reverted_issues 99 2>&1)
  printf '%s' "$_out" | grep -qi "WARN.*could NOT reopen issue #41" \
    || { echo "FAIL #50: a failed reopen must warn"; exit 1; }
  rm -rf "$_g50"
  unset _GH_ISSUES _GH_LOOKUP_FAILS _GH_REOPEN_FAILS _g50
  echo "_reopen_reverted_issues OK (#50)"

  # _rerun_main_ci itself must report `unknown`, not `red`, when it never obtained a
  # verdict -- the stub above replaces the whole function, so this exercises its own
  # dispatch path. `red` here is what let a rate limit read as regression evidence.
  # PATH shim, not a shell function -- see the note in the #50 block: `_ci_gh` wraps
  # these in `timeout`, which execs a binary and cannot see a function.
  local _g52; _g52=$(mktemp -d)
  cat >"$_g52/gh" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"run list"*)  [ "${_GH_NO_RUN:-0}" = 1 ] && exit 0; echo 12345 ;;
  *"run rerun"*) exit "${_GH_RERUN_RC:-0}" ;;
  *) exit 0 ;;
esac
SH
  chmod +x "$_g52/gh"
  # Run in subshells with REPO/MAIN_CI_TIMEOUT scoped in: the function reads both,
  # and the suite does not set them globally.
  _out=$( PATH="$_g52:$PATH"; REPO=demo/demo MAIN_CI_TIMEOUT=1 _GH_NO_RUN=1
          export PATH _GH_NO_RUN; _rerun_main_ci deadbeef 2>/dev/null )
  [ "$_out" = unknown ] \
    || { echo "FAIL #52: no run found must be unknown, not '$_out'"; exit 1; }
  _out=$( PATH="$_g52:$PATH"; REPO=demo/demo MAIN_CI_TIMEOUT=1 _GH_NO_RUN=0 _GH_RERUN_RC=1
          export PATH _GH_NO_RUN _GH_RERUN_RC; _rerun_main_ci deadbeef 2>/dev/null )
  [ "$_out" = unknown ] \
    || { echo "FAIL #52: a failed rerun dispatch must be unknown, not '$_out'"; exit 1; }
  # #62: the RE-RUN poll loop must validate the interval too. It is a SECOND loop, and
  # consolidating the validation missed it precisely because the hostile-value
  # integration test drives the initial watch to green and never enters this one. With
  # an unvalidated `abc`, `sleep` fails every iteration while the arithmetic resolves
  # the unset name to 0, so `w` never advances and the loop polls to the deadline.
  # Here the run EXISTS and stays pending, so the loop is actually entered.
  local _t0 _t1
  _t0=$(date +%s)
  _out=$( PATH="$_g52:$PATH"; REPO=demo/demo MAIN_CI_TIMEOUT=3 MAIN_CI_POLL_INTERVAL=abc
          export PATH; _rerun_main_ci deadbeef 2>/dev/null )
  _t1=$(date +%s)
  [ "$_out" = pending ] \
    || { echo "FAIL #62: the re-run poll must exhaust its budget and report pending, got '$_out'"; exit 1; }
  [ "$(( _t1 - _t0 ))" -le 120 ] \
    || { echo "FAIL #62: a non-numeric MAIN_CI_POLL_INTERVAL stalled the re-run loop ($(( _t1 - _t0 ))s)"; exit 1; }
  unset _t0 _t1
  rm -rf "$_g52"; unset _g52

  # --- #52: retries prove transience causally; no verdict must never revert -----
  # State lives in a FILE: the helper calls the stub inside $( ), so a variable
  # mutated there dies with the subshell and every call would replay the first
  # answer (which is how the first version of this test fooled itself).
  local _sq="${TMPDIR:-/tmp}/bircher-st-seq-$$"
  # SAVE the real function before shadowing it. `unset -f` below used to delete the
  # script's own definition, not just this stub, so every test after this point ran
  # against a _rerun_main_ci that did not exist -- calls returned empty and any
  # assertion on them was vacuous. Nothing noticed because nothing called it again
  # until #62 added a test that did.
  local _real_rerun; _real_rerun=$(declare -f _rerun_main_ci)
  _rerun_main_ci() {
    local first rest
    first=$(head -1 "$_sq"); rest=$(tail -n +2 "$_sq")
    printf '%s\n' "$rest" > "$_sq"
    printf '%s\n' "${2:-partial}" >> "$_sq.modes"
    echo "$(( $(cat "$_sq.n" 2>/dev/null || echo 0) + 1 ))" > "$_sq.n"
    printf '%s' "$first"
  }
  _seq() { printf '%s\n' "$@" > "$_sq"; : > "$_sq.n"; : > "$_sq.modes"; }
  _ncalls() { cat "$_sq.n" 2>/dev/null || echo 0; }

  # budget 3: partial(red), partial(green) + full confirm(green) = 3 re-runs.
  _seq red green green
  [ "$(_rerun_main_ci_until_green deadbeef 3 0 2>/dev/null)" = green ] \
    || { echo "FAIL #52: a confirmed green after a red is transient"; exit 1; }
  [ "$(_ncalls)" = 3 ] \
    || { echo "FAIL #52: budget must count re-runs (got $(_ncalls))"; exit 1; }

  _seq red red red
  [ "$(_rerun_main_ci_until_green deadbeef 3 0 2>/dev/null)" = red ] \
    || { echo "FAIL #52: red through the whole budget stays red"; exit 1; }
  [ "$(_ncalls)" = 3 ] \
    || { echo "FAIL #52: must not exceed its budget (got $(_ncalls))"; exit 1; }

  # No verdict must propagate as unknown, NOT red -- red would revert.
  _seq unknown unknown unknown
  [ "$(_rerun_main_ci_until_green deadbeef 3 0 2>/dev/null)" = unknown ] \
    || { echo "FAIL #52: undispatched re-runs must report unknown, not red"; exit 1; }

  # A partial green must be confirmed by a FULL run before it counts.
  _seq green green
  [ "$(_rerun_main_ci_until_green deadbeef 3 0 2>/dev/null)" = green ] \
    || { echo "FAIL #52: a confirmed partial green is accepted"; exit 1; }
  [ "$(_ncalls)" = 2 ] \
    || { echo "FAIL #52: a partial green costs a confirming run (got $(_ncalls))"; exit 1; }
  [ "$(tail -1 "$_sq.modes")" = full ] \
    || { echo "FAIL #52: the confirming run must be full"; exit 1; }

  # Unconfirmed, and the budget runs out: must NOT be reported green, and the
  # exact outcome is pinned rather than merely "not green".
  _seq green red red
  [ "$(_rerun_main_ci_until_green deadbeef 3 0 2>/dev/null)" = red ] \
    || { echo "FAIL #52: an unconfirmed partial green must report the full run's verdict"; exit 1; }
  [ "$(_ncalls)" = 3 ] \
    || { echo "FAIL #52: unconfirmed path must respect the budget (got $(_ncalls))"; exit 1; }

  # A partial green with NO budget left to confirm it is not evidence either.
  _seq green
  [ "$(_rerun_main_ci_until_green deadbeef 1 0 2>/dev/null)" = green ] \
    || { echo "FAIL #52: with budget 1 the single run is full, so its green counts"; exit 1; }
  [ "$(head -1 "$_sq.modes")" = full ] \
    || { echo "FAIL #52: a budget of 1 must spend it on a FULL run"; exit 1; }

  # A bad setting must not silently mean "never retried".
  _seq red green green
  [ "$(_rerun_main_ci_until_green deadbeef notanumber 0 2>/dev/null)" = green ] \
    || { echo "FAIL #52: a non-numeric budget must fall back to a sane default"; exit 1; }

  rm -f "$_sq" "$_sq.n" "$_sq.modes"; unset -f _seq _ncalls
  # RESTORE, do not unset: see the note where _real_rerun is captured.
  unset -f _rerun_main_ci; eval "$_real_rerun"
  declare -F _rerun_main_ci >/dev/null \
    || { echo "FAIL: the real _rerun_main_ci was not restored after stubbing"; exit 1; }
  echo "_rerun_main_ci_until_green OK (#52)"

  local _std="${TMPDIR:-/tmp}/bircher-st-pr-$$"; mkdir -p "$_std"; NOOP_DIR="$_std"
  printf '279\n' > "$_std/cal08.pr"
  [ "$(_pr_signal cal08)" = "279" ] || { echo "FAIL _pr_signal read"; exit 1; }
  [ -z "$(_pr_signal absent)" ]     || { echo "FAIL _pr_signal absent->empty"; exit 1; }
  [ "$(_select_pr_candidate '' '297 298')" = "ambiguous/escalate|297 298" ] \
    || { echo "FAIL _select_pr_candidate ambiguous"; exit 1; }
  rm -rf "$_std"
  # ONE case, not six. The mapping moved to v2/coordinator/observe.py and is
  # covered there by nineteen native tests -- including four the shell version
  # never had (a lowercase verdict, an empty verdict, the reviewer name
  # travelling into the string, and CI being checked before the verdict).
  #
  # What is kept is what those tests CANNOT see: that this shell can actually
  # reach the Python and parse what comes back. Delete this and a broken call
  # path is green in both suites.
  #
  # The `RECOVERED:` prefix is gone from the notes deliberately. It described a
  # recovery path; since Phase 2 this is the ONLY path, and prefixing every
  # scorecard note with a word that means "something went wrong earlier" would
  # be false on every ordinary run.
  [ "$(classify_recovery 7 green PASS)" = "ready|codex:pass|green|out-of-band review PASS" ] \
    || { echo "FAIL classify: shell cannot reach the coordinator package: '$(classify_recovery 7 green PASS)'"; exit 1; }
  echo "classify_recovery -> coordinator.observe OK"
  # --- Layer-2 recovery: wrapper end-to-end with fake gh/omnigent on PATH -----
  local shimdir; shimdir=$(mktemp -d)
  cat >"$shimdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: `pr checks ... --json bucket` -> two passing checks;
#          `pr comment ... --body X` -> write X to $GH_COMMENT_OUT
sub="$2"
# #66: recovery captures the reviewed head itself via `gh api repos/../pulls/N`.
# FAKE_HEAD_SHA drives it so the tests can exercise present / absent / malformed.
if [ "$1" = "api" ]; then
  case "$*" in *"/pulls/"*) printf '%s' "${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}"; exit 0 ;; esac
  exit 0
fi
if [ "$sub" = "checks" ]; then printf 'pass\npass\n'; exit 0; fi
if [ "$sub" = "comment" ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--body" ]; then printf '%s' "$2" > "$GH_COMMENT_OUT"; break; fi
    shift
  done
  # real `gh pr comment` prints the created comment URL to stdout:
  echo "https://github.com/demo/demo/pull/7#issuecomment-123456"
  exit 0
fi
exit 0
SH
  cat >"$shimdir/omnigent" <<'SH'
#!/usr/bin/env bash
# fake omnigent: emit findings ending in the required verdict line
printf 'Reviewed build + tests, all good.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$shimdir/gh" "$shimdir/omnigent"
  local rec_out
  rec_out=$(PATH="$shimdir:$PATH" WORKDIR="$shimdir" REPO=demo/demo SERVER=http://x \
            GH_COMMENT_OUT="$shimdir/comment.txt" RECOVERY_REVIEWER=codex \
            observe_outcome demo demo 7)
  # #66: 4th field is the orchestrator-captured reviewed head, so the recovery
  # merge can be pinned exactly as the marker path is.
  # 5th field is the CI value the derivation OBSERVED. It is returned rather
  # than inferred from `outcome=ready` so the caller records an observation
  # instead of a deduction, and pinned here because both callers read the full
  # tuple -- a short read would silently absorb it into the sha.
  #
  # 6th and 7th are the CI history (Phase 2). This shim's `gh` answers nothing
  # for the branch lookup, so they are `unknown` and empty -- which is the
  # correct answer to "no history was visible", and is pinned here so a change
  # that starts inventing `false|0` on a failed lookup is caught.
  [ "$rec_out" = "ready|codex:pass|out-of-band review PASS|a502a88e20f959c908d00871ee7f25572512dd6d|green|unknown||7" ] \
    || { echo "FAIL derive happy-path tuple: '$rec_out'"; exit 1; }
  grep -q 'head=a502a88e20f959c908d00871ee7f25572512dd6d' "$shimdir/comment.txt" \
    || { echo "FAIL derive: comment must carry head= on a ready outcome"; cat "$shimdir/comment.txt"; exit 1; }
  grep -q '^outcome=ready ci=green ' "$shimdir/comment.txt" \
    || { echo "FAIL derive: outcome line not posted to PR"; cat "$shimdir/comment.txt"; exit 1; }
  # PHASE 2: the machine marker is gone. Its replacement is the prose above --
  # and this assertion is what keeps a future edit from quietly restoring the
  # channel by restoring its prefix.
  grep -q 'bircher-status:' "$shimdir/comment.txt" \
    && { echo "FAIL derive: the retired marker was posted"; exit 1; }
  grep -q 'VERDICT: PASS' "$shimdir/comment.txt" \
    || { echo "FAIL recover: reviewer findings not included in comment"; exit 1; }
  local rec_nopr
  rec_nopr=$(PATH="$shimdir:$PATH" WORKDIR="$shimdir" REPO=demo/demo SERVER=http://x \
             observe_outcome demo demo "")
  # 5th field is "na" here: no PR means no CI was ever observed, and "na" is
  # not a value _kernel_ci_status maps to success, so it cannot be mistaken for
  # green by the merge gate.
  [ "$rec_nopr" = "timeout|na|no PR at timeout (reaped before implement delivered)||na|unknown||" ] \
    || { echo "FAIL derive no-pr tuple: '$rec_nopr'"; exit 1; }
  rm -rf "$shimdir"
  echo "observe_outcome OK"

  # --- The repair loop: shell -> Python -> back, over the real call path ------
  #
  # tests/coordinator/ already drives `classify`, `revisions_used` and the
  # findings transport directly, and none of that can see what these check:
  # that THIS shell passes the two new arguments in a form the CLI accepts, and
  # that the file the CLI writes is the file this shell can read. Both sides
  # were green while disagreeing about a vocabulary before -- twice -- and
  # neither time was it visible from either side alone.
  [ "$(BIRCHER_MAX_REVISIONS= _max_revisions)" = 2 ] \
    || { echo "FAIL _max_revisions: default is not 2"; exit 1; }
  [ "$(BIRCHER_MAX_REVISIONS=0 _max_revisions)" = 0 ] \
    || { echo "FAIL _max_revisions: 0 must disable the loop"; exit 1; }
  [ "$(BIRCHER_MAX_REVISIONS=5 _max_revisions)" = 5 ] \
    || { echo "FAIL _max_revisions: 5 is in range"; exit 1; }
  # OUT OF RANGE RETURNS THE DEFAULT, not the nearest bound. `_clamp_int` has
  # always done this and the misreading of it silently turned a 5300s budget
  # into 300s once already. Pinned so the behaviour is a decision, not a
  # surprise a future reader has to rediscover.
  [ "$(BIRCHER_MAX_REVISIONS=9 _max_revisions)" = 2 ] \
    || { echo "FAIL _max_revisions: out of range must give the DEFAULT (2), not the max"; exit 1; }
  [ "$(BIRCHER_MAX_REVISIONS=abc _max_revisions)" = 2 ] \
    || { echo "FAIL _max_revisions: a non-numeric value must give the default"; exit 1; }
  echo "_max_revisions OK"

  local rdir; rdir=$(mktemp -d)
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "api" ]; then
  case "$*" in *"/pulls/"*) printf '%s' "a502a88e20f959c908d00871ee7f25572512dd6d"; exit 0 ;; esac
  exit 0
fi
case "$2" in
  checks)  printf 'pass\npass\n'; exit 0 ;;
  comment) exit 0 ;;
  view)    printf 'i900-repair-me\n'; exit 0 ;;
esac
exit 0
SH
  cat >"$rdir/omnigent" <<'SH'
#!/usr/bin/env bash
# A reviewer that BLOCKS, with findings shaped like the real ones: multiple
# paragraphs, a pipe, and newlines -- all three of which the eight-field tuple
# cannot carry, which is the whole reason the findings travel by file.
printf 'Blocking:\n- the retry loop reads `a | b` and drops b\n- no test covers the empty case\n\nVERDICT: FAIL\n'
exit 0
SH
  chmod +x "$rdir/gh" "$rdir/omnigent"

  # WITH rounds left: the outcome is `revise` and the findings are on disk.
  local rev_out
  rev_out=$(PATH="$rdir:$PATH" WORKDIR="$rdir" REPO=demo/demo SERVER=http://x \
            RECOVERY_REVIEWER=codex \
            observe_outcome demo demo 7 "" 2 "$rdir/findings.txt")
  case "$rev_out" in
    revise\|codex:fail\|*) ;;
    *) echo "FAIL repair: a FAIL with rounds left must derive 'revise', got '$rev_out'"; exit 1 ;;
  esac
  _derived_width_ok "$rev_out" \
    || { echo "FAIL repair: the revise tuple is not eight fields on one line: '$rev_out'"; exit 1; }
  [ -s "$rdir/findings.txt" ] \
    || { echo "FAIL repair: no findings were written for a revise"; exit 1; }
  grep -q 'drops b' "$rdir/findings.txt" \
    || { echo "FAIL repair: the reviewer's findings did not survive the file transport"; cat "$rdir/findings.txt"; exit 1; }
  # The findings contain a pipe and newlines. If any of that had leaked into the
  # tuple the width check above would already have failed -- this pins WHY.
  case "$rev_out" in
    *"drops b"*) echo "FAIL repair: findings leaked into the tuple"; exit 1 ;;
  esac

  # WITHOUT rounds left: byte-identical to the pre-loop behaviour, and NOTHING
  # is written. This is the assertion that makes BIRCHER_MAX_REVISIONS=0 a real
  # rollback rather than a path that merely usually agrees.
  rm -f "$rdir/findings.txt"
  local norev_out
  norev_out=$(PATH="$rdir:$PATH" WORKDIR="$rdir" REPO=demo/demo SERVER=http://x \
              RECOVERY_REVIEWER=codex \
              observe_outcome demo demo 7 "" 0 "$rdir/findings.txt")
  case "$norev_out" in
    failed\|codex:fail\|*) ;;
    *) echo "FAIL repair: a FAIL with NO rounds left must stay 'failed', got '$norev_out'"; exit 1 ;;
  esac
  [ ! -e "$rdir/findings.txt" ] \
    || { echo "FAIL repair: findings were written for a terminal failure"; exit 1; }
  # And the DEFAULT -- no arguments at all -- must be the terminal one, so every
  # existing caller of observe_outcome is unaffected by the loop's existence.
  local dflt_out
  dflt_out=$(PATH="$rdir:$PATH" WORKDIR="$rdir" REPO=demo/demo SERVER=http://x \
             RECOVERY_REVIEWER=codex observe_outcome demo demo 7)
  [ "$dflt_out" = "$norev_out" ] \
    || { echo "FAIL repair: observe_outcome's default is not the pre-loop behaviour: '$dflt_out' vs '$norev_out'"; exit 1; }
  echo "observe_outcome repair arguments OK"

  # `_pr_branch` READS the branch. Deriving it from the code instead stalled a
  # whole wave once (CAL06 #277), which is why this is a lookup.
  [ "$(PATH="$rdir:$PATH" REPO=demo/demo _pr_branch 7)" = "i900-repair-me" ] \
    || { echo "FAIL _pr_branch: did not read headRefName"; exit 1; }
  [ -z "$(PATH="$rdir:$PATH" REPO=demo/demo _pr_branch "")" ] \
    || { echo "FAIL _pr_branch: an empty PR must yield an empty branch"; exit 1; }

  # The repair brief. Every clause here was load-bearing in the hand-run
  # repairs, and the prohibitions are asserted individually because a prompt
  # that merely MENTIONS the branch while permitting a new PR strands the
  # reviewed one.
  local rp; rp=$(REPO=demo/demo WORKDIR=/w _repair_prompt "do the thing" 7 "i900-repair-me" "FINDING ONE | and two")
  case "$rp" in
    *"i900-repair-me"*) ;;
    *) echo "FAIL _repair_prompt: does not name the branch to push to"; exit 1 ;;
  esac
  case "$rp" in
    *"FINDING ONE | and two"*) ;;
    *) echo "FAIL _repair_prompt: does not carry the findings verbatim"; exit 1 ;;
  esac
  case "$rp" in
    *"Do NOT open a new pull request"*) ;;
    *) echo "FAIL _repair_prompt: does not forbid opening a second PR"; exit 1 ;;
  esac
  case "$rp" in
    *"do the thing"*) ;;
    *) echo "FAIL _repair_prompt: drops the original task"; exit 1 ;;
  esac
  echo "_repair_prompt OK"

  # `_repair_round` REFUSES to run without a vendor. It reads every other name
  # from script globals but `vendor` is a caller local, so an extracted or
  # re-ordered call site would dispatch a generation attributed to nobody --
  # silently, because `_kernel_dispatch` is advisory.
  # ASSERTED ON THE REASON, not on the exit code. The first version of this
  # checked only that the call failed -- and it fails anyway in the self-test
  # environment, where no session server exists, so removing the guard entirely
  # left the assertion green. A mutation that swaps one failure for another
  # proves nothing; the stderr text is what tells the two apart.
  local _rr_err
  _rr_err=$( ( _repair_round item code 7 br findings 1 ) 2>&1 >/dev/null )
  case "$_rr_err" in
    *"needs the implementing vendor"*) ;;
    *) echo "FAIL _repair_round: a missing vendor did not refuse by name (got: ${_rr_err:-<nothing>})"; exit 1 ;;
  esac
  # The durability gate. Criterion 7: an accepted REVIEW_VERDICT carrying the
  # submitted command's causal id, or no repair work is dispatched.
  _revision_is_recorded "1|1|yes" \
    || { echo "FAIL _revision_is_recorded: a confirmed revision was rejected"; exit 1; }
  ! _revision_is_recorded "1|1|no" \
    || { echo "FAIL _revision_is_recorded: an unconfirmed revision was accepted"; exit 1; }
  # EVERY failure shape arrives as an empty string, and every one must be "no".
  ! _revision_is_recorded "" \
    || { echo "FAIL _revision_is_recorded: a failed lookup read as confirmation"; exit 1; }
  ! _revision_is_recorded \
    || { echo "FAIL _revision_is_recorded: a missing argument read as confirmation"; exit 1; }
  # A count is not a confirmation. `1|1|` says a revision was used at some point
  # and says nothing about OURS -- which is precisely the previous round's
  # revision confirming this round's missing one.
  ! _revision_is_recorded "1|1|" \
    || { echo "FAIL _revision_is_recorded: a truncated tuple read as confirmation"; exit 1; }
  # And it must match the FIELD, not the substring: a run whose note or counts
  # merely contain the word must not pass.
  ! _revision_is_recorded "yes|1|no" \
    || { echo "FAIL _revision_is_recorded: matched 'yes' outside the confirmed field"; exit 1; }
  echo "_revision_is_recorded OK"

  # THE ROLLBACK, asserted as a property of the call and not of the loop.
  # BIRCHER_MAX_REVISIONS=0 must leave derivation with no findings-file
  # operation whatever, so a NOOP_DIR that cannot be written to -- unwritable,
  # misowned, holding a protected stale file -- cannot fail an item that was
  # configured never to repair.
  [ -z "$(BIRCHER_MAX_REVISIONS=0 NOOP_DIR=/nonexistent/nope _findings_path c1)" ] \
    || { echo "FAIL _findings_path: a disabled loop still names a findings file"; exit 1; }
  [ "$(BIRCHER_MAX_REVISIONS=2 NOOP_DIR=/tmp/nd _findings_path c1)" = "/tmp/nd/c1.findings" ] \
    || { echo "FAIL _findings_path: an enabled loop must name one"; exit 1; }
  # And an empty path must make observe_outcome behave as it did before the
  # loop existed -- proven against a path it could not possibly write to.
  local ro_out
  ro_out=$(PATH="$rdir:$PATH" WORKDIR="$rdir" REPO=demo/demo SERVER=http://x \
           RECOVERY_REVIEWER=codex \
           observe_outcome demo demo 7 "" 0 "")
  [ "$ro_out" = "$dflt_out" ] \
    || { echo "FAIL rollback: an empty findings path did not reproduce the pre-loop tuple: '$ro_out' vs '$dflt_out'"; exit 1; }
  # THE HAZARD ITSELF, reproduced. A path that merely does not exist is fine --
  # the CLI tolerates ENOENT on the pre-derivation unlink, because "nothing to
  # clear" is the normal case. The failure needs a stale file that CANNOT be
  # removed, which is what an unwritable or misowned NOOP_DIR produces.
  #
  # An earlier version of this test used /nonexistent/... and passed while
  # asserting the opposite of what happened -- the derivation succeeded, ENOENT
  # having been swallowed exactly as designed. A reproduction that cannot
  # produce the failure it names proves nothing about the fix.
  mkdir -p "$rdir/ro"
  : > "$rdir/ro/f.txt"
  chmod 500 "$rdir/ro"
  local rofail_out
  rofail_out=$(PATH="$rdir:$PATH" WORKDIR="$rdir" REPO=demo/demo SERVER=http://x \
               RECOVERY_REVIEWER=codex \
               observe_outcome demo demo 7 "" 0 "$rdir/ro/f.txt")
  # This is the pre-fix behaviour, pinned so the hazard stays visible: passing
  # an unusable path fails the derivation even with NO revisions allowed.
  [ -z "$rofail_out" ] \
    || { chmod 700 "$rdir/ro"; echo "FAIL rollback: an unremovable stale findings file did not fail the derivation, so this test no longer reproduces the hazard: '$rofail_out'"; exit 1; }
  # And the fix: with the loop disabled, run_item never passes the path at all,
  # so the same unusable directory cannot reach the derivation.
  [ -z "$(BIRCHER_MAX_REVISIONS=0 NOOP_DIR="$rdir/ro" _findings_path demo)" ] \
    || { chmod 700 "$rdir/ro"; echo "FAIL rollback: a disabled loop still names a path in an unwritable directory"; exit 1; }
  chmod 700 "$rdir/ro"
  echo "_findings_path rollback OK"

  # The three recovery rows that must never reach a merge, and the reasons they
  # are three rather than one.
  _recovery_forbids_merge "done|the merge outcome is recorded" \
    || { echo "FAIL _recovery_forbids_merge: a recorded merge was not forbidden"; exit 1; }
  _recovery_forbids_merge "record_merge_outcome|the merge HAPPENED" \
    || { echo "FAIL _recovery_forbids_merge: a confirmed-but-unrecorded merge was not forbidden"; exit 1; }
  _recovery_forbids_merge "halt_and_reconcile|a merge effect is uncertain" \
    || { echo "FAIL _recovery_forbids_merge: an uncertain merge was not forbidden"; exit 1; }
  # And the rows that must still be allowed through, or recovery merges nothing
  # ever again -- an assertion that only forbade would pass every test above.
  ! _recovery_forbids_merge "merge|an acceptance binds the current output" \
    || { echo "FAIL _recovery_forbids_merge: a clean merge was forbidden"; exit 1; }
  ! _recovery_forbids_merge "perform_merge|authorised and never attempted" \
    || { echo "FAIL _recovery_forbids_merge: an authorised merge was forbidden"; exit 1; }
  ! _recovery_forbids_merge "retry_merge|the merge failed, not the review" \
    || { echo "FAIL _recovery_forbids_merge: a failed merge could not be retried"; exit 1; }
  # UNKNOWN IS NOT PERMISSION, but it is also not a refusal: see the function's
  # own comment for why, and for what stands behind it.
  ! _recovery_forbids_merge "" \
    || { echo "FAIL _recovery_forbids_merge: an unreadable journal stopped every merge"; exit 1; }
  # Matched on the ACTION, not the whole line: a `why` mentioning the word
  # must not decide anything.
  ! _recovery_forbids_merge "merge|do not confuse this with halt_and_reconcile" \
    || { echo "FAIL _recovery_forbids_merge: matched the reason text, not the action"; exit 1; }
  echo "_recovery_forbids_merge OK"

  # `rounds` REACHES THE SCORECARD. It was computed, documented as "reports
  # something again", and read by nothing: `json_row` hardcoded `"rounds": None`
  # and mapped its sixth argument to `resubmissions`. The comment claimed a
  # behaviour the code did not have, which is the same defect as an unbinding
  # test in a different medium.
  local _jr
  _jr=$(json_row it 7 ready true cx:pass 3 12 note bound claude 2)
  printf '%s' "$_jr" | grep -q '"rounds": 2' \
    || { echo "FAIL json_row: repair rounds did not reach the scorecard: $_jr"; exit 1; }
  printf '%s' "$_jr" | grep -q '"resubmissions": 3' \
    || { echo "FAIL json_row: resubmissions moved or was overwritten: $_jr"; exit 1; }
  # Absent -> null, NOT 0. A 0 would claim the loop ran and found nothing to
  # repair, which is not what a disabled loop means.
  _jr=$(json_row it 7 ready true cx:pass 3 12 note bound claude)
  printf '%s' "$_jr" | grep -q '"rounds": null' \
    || { echo "FAIL json_row: an absent round count is not null: $_jr"; exit 1; }
  echo "json_row rounds OK"

  # --- _repair_round actually EXECUTES ----------------------------------------
  #
  # Until this, nothing in either suite had ever run it. Every other repair-loop
  # test drives the pieces around it -- the classification, the findings
  # transport, the durability gate -- and the function that performs the repair
  # was defended only by reading it. "Does ANY test drive the happy path to
  # completion" had the answer NO, which is the shape that let a whole
  # coordinator arm ship with a corrupted module name and 494 green tests.
  #
  # Driven by redefining its collaborators in a SUBSHELL, so the overrides
  # cannot leak into the tests after it.
  local rrdir; rrdir=$(mktemp -d)
  (
    _kernel_dispatch() { echo 7; }
    _kernel_start_implementation() { echo "start_implementation $1 $2" >> "$rrdir/kernel"; }
    _local_host_id() { echo host-1; }
    _create_session() { echo "created $*" >> "$rrdir/calls"; echo conv-42; }
    _send_prompt() { printf '%s' "$2" > "$rrdir/prompt"; echo "prompted $1" >> "$rrdir/calls"; }
    _stop_session() { echo "stopped $1" >> "$rrdir/calls"; }
    _session_state() { echo "failed|runner_error"; }   # dies -> the loop ends
    _coordinator() { return 1; }                        # no settle answer
    POLL=0 ITEM_TIMEOUT=5 AGENT_ID=ag WORKDIR=/w SERVER=http://x REPO=demo/demo \
      BIRCHER_RUN_ID=r1 BIRCHER_KERNEL_DB=/dev/null \
      _repair_round "the task" c1 7 i711-branch "BLOCKING: the retry drops b" 1 claude_code \
      > "$rrdir/out" 2>&1
    echo "$?" > "$rrdir/rc"
  )
  [ "$(cat "$rrdir/rc")" = 0 ] \
    || { echo "FAIL _repair_round: rc=$(cat "$rrdir/rc")"; cat "$rrdir/out"; exit 1; }
  # It MINTED A GENERATION AND RECORDED start_implementation. Without that the
  # run is still at `planned` and every later command in the round is refused.
  grep -q 'start_implementation r1 7' "$rrdir/kernel" \
    || { echo "FAIL _repair_round: did not record start_implementation at the new generation"; cat "$rrdir/kernel" 2>/dev/null; exit 1; }
  # It CREATED, PROMPTED and STOPPED, in that order. Stopping matters most: a
  # quiet session is idle, not finished, and a live repair session races the
  # review the next derivation is about to dispatch.
  [ "$(tr '\n' ' ' < "$rrdir/calls" | sed 's/created [^p]*/created /')" = "created prompted conv-42 stopped conv-42 " ] \
    || { echo "FAIL _repair_round: wrong call sequence: $(cat "$rrdir/calls")"; exit 1; }
  # The BRIEF reached the session -- verbatim, with the branch and the PR.
  grep -q 'BLOCKING: the retry drops b' "$rrdir/prompt" \
    || { echo "FAIL _repair_round: the findings did not reach the prompt"; cat "$rrdir/prompt"; exit 1; }
  grep -q 'i711-branch' "$rrdir/prompt" \
    || { echo "FAIL _repair_round: the branch did not reach the prompt"; exit 1; }
  grep -q 'Do NOT open a new pull request' "$rrdir/prompt" \
    || { echo "FAIL _repair_round: the prohibition did not reach the prompt"; exit 1; }
  # A session that cannot be created is rc 1 and NO prompt -- the caller
  # escalates rather than looping on a round that never started.
  : > "$rrdir/calls"
  (
    _kernel_dispatch() { echo 7; }
    _kernel_start_implementation() { :; }
    _local_host_id() { echo host-1; }
    _create_session() { echo ""; }
    _send_prompt() { echo "prompted" >> "$rrdir/calls"; }
    _stop_session() { :; }
    POLL=0 ITEM_TIMEOUT=5 AGENT_ID=ag WORKDIR=/w SERVER=http://x REPO=demo/demo \
      BIRCHER_RUN_ID=r1 BIRCHER_KERNEL_DB=/dev/null \
      _repair_round t c1 7 br "f" 1 claude_code >/dev/null 2>&1
    echo "$?" > "$rrdir/rc2"
  )
  [ "$(cat "$rrdir/rc2")" = 1 ] \
    || { echo "FAIL _repair_round: a failed session create must return rc 1, got $(cat "$rrdir/rc2")"; exit 1; }
  [ ! -s "$rrdir/calls" ] \
    || { echo "FAIL _repair_round: prompted a session that was never created"; exit 1; }
  rm -rf "$rrdir"
  echo "_repair_round executes OK"
  rm -rf "$rdir"
  echo "repair loop OK"
  # --- Fix 1b: recovery re-discovers the PR when it was recorded "no PR" -------
  local ddir; ddir=$(mktemp -d)
  cat >"$ddir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: an open PR #300 for the item; CI green; record the comment.
# #66: also answer the reviewed-head lookup so a ready outcome can be pinned.
#
# `list` returns JSON, not a bare number: the derivation asks for
# `--json number,headRefName` and does the boundary-anchored code match in
# Python (which also ESCAPES the code, so `i.3` is not a pattern). The old shim
# answered gh's `-q` form and is why this failed first.
if [ "$1" = "api" ]; then
  case "$*" in
    *".head.ref"*) printf 'i300-work' ;;
    *"/pulls/"*)   printf '%s' "${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}" ;;
  esac
  exit 0
fi
case "$2" in
  list)    printf '%s' '[{"number":300,"headRefName":"i300-work"}]' ;;
  checks)  printf 'pass\npass\n' ;;
  comment) echo "https://github.com/demo/demo/pull/300#issuecomment-1" ;;
esac
exit 0
SH
  cat >"$ddir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review of the adopted PR.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$ddir/gh" "$ddir/omnigent"
  local rec_disc
  rec_disc=$(PATH="$ddir:$PATH" WORKDIR="$ddir" REPO=demo/demo SERVER=http://x \
             RECOVERY_REVIEWER=codex observe_outcome i300 i300 "")
  # The eighth field is 300: this case passes an EMPTY pr and the derivation
  # DISCOVERS it, so the field carrying it back is exactly what this asserts.
  # Before the field existed the discovery was used internally and the caller
  # never learned of it.
  [ "$rec_disc" = "ready|codex:pass|out-of-band review PASS|a502a88e20f959c908d00871ee7f25572512dd6d|green|unknown||300" ] \
    || { echo "FAIL recover discovery-adopt (1b): '$rec_disc'"; rm -rf "$ddir"; exit 1; }
  rm -rf "$ddir"
  echo "recover discovery-adopt (1b) OK"
  # --- issue-linkage fallback: branch AND signal used the WRONG code (run #24
  #     a06-vs-i230), but the PR body carries Closes #N -> map/adopt by issue ----
  local idir; idir=$(mktemp -d)
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
# branch-code discovery + reconcile -> NO match (empty); the --search issue query
# returns PR #305 whose body carries "Closes #307"; CI green.
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":305,"body":"Impl. Closes #307 done"}]\n'; exit 0; }
  exit 0
fi
[ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$2" = "comment" ] && { echo "https://x/pull/305#c1"; exit 0; }
exit 0
SH
  cat >"$idir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$idir/gh" "$idir/omnigent"
  [ "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue 307)" = "305" ] \
    || { echo "FAIL _discover_pr_by_issue: expected 305"; rm -rf "$idir"; exit 1; }
  [ -z "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue '')" ] \
    || { echo "FAIL _discover_pr_by_issue: empty issue must return nothing"; rm -rf "$idir"; exit 1; }
  # a body that mentions #307 but does NOT close it must NOT match (search returns it; regex rejects)
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
# #66: answer the reviewed-head lookup so a ready recovery can be pinned.
if [ "$1" = "api" ]; then
  case "$*" in *"/pulls/"*) printf '%s' "${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}" ;; esac
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":999,"body":"see also #307 for context"}]\n'; exit 0; }
fi
exit 0
SH
  chmod +x "$idir/gh"
  [ -z "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue 307)" ] \
    || { echo "FAIL _discover_pr_by_issue: bare #307 mention must not match (needs a closing keyword)"; rm -rf "$idir"; exit 1; }
  # end-to-end: recover with a wrong code (no branch match) but the issue param adopts #305
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
# #66: answer the reviewed-head lookup so a ready recovery can be pinned.
if [ "$1" = "api" ]; then
  case "$*" in *"/pulls/"*) printf '%s' "${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}" ;; esac
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":305,"body":"Impl. Closes #307 done"}]\n'; exit 0; }
  exit 0
fi
[ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$2" = "comment" ] && { echo "https://x/pull/305#c1"; exit 0; }
exit 0
SH
  chmod +x "$idir/gh"
  local rec_iss
  rec_iss=$(PATH="$idir:$PATH" WORKDIR="$idir" REPO=demo/demo SERVER=http://x \
            RECOVERY_REVIEWER=codex observe_outcome iwrong iwrong "" 307)
  [ "$rec_iss" = "ready|codex:pass|out-of-band review PASS|a502a88e20f959c908d00871ee7f25572512dd6d|green|unknown||305" ] \
    || { echo "FAIL recover issue-linkage adopt: '$rec_iss'"; rm -rf "$idir"; exit 1; }
  rm -rf "$idir"
  echo "issue-linkage fallback (_discover_pr_by_issue + recover) OK"
  # --- Fix C: _reconcile_item_pr adopts a CI-green sibling + closes the loser --
  local rdir; rdir=$(mktemp -d)
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: two open PRs for the item; 179 green, 178 red; record closes.
case "$2" in
  list)   printf '178\n179\n' ;;
  checks) case "$3" in 179) printf 'pass\npass\n' ;; *) printf 'fail\npass\n' ;; esac ;;
  close)  echo "$3" >> "$GH_CLOSED" ;;
esac
exit 0
SH
  chmod +x "$rdir/gh"
  : > "$rdir/closed.txt"
  local rchosen
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i141 178)
  [ "$rchosen" = 179 ]                       || { echo "FAIL reconcile chose '$rchosen' not 179"; rm -rf "$rdir"; exit 1; }
  grep -qx 178 "$rdir/closed.txt"            || { echo "FAIL reconcile did not close loser 178"; rm -rf "$rdir"; exit 1; }
  grep -qx 179 "$rdir/closed.txt" 2>/dev/null && { echo "FAIL reconcile closed winner 179"; rm -rf "$rdir"; exit 1; }
  # single match -> unchanged, closes nothing
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
case "$2" in list) printf '200\n' ;; checks) printf 'pass\npass\n' ;; close) echo "$3" >> "$GH_CLOSED" ;; esac
exit 0
SH
  : > "$rdir/closed.txt"
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i200 200)
  [ "$rchosen" = 200 ]        || { echo "FAIL reconcile single-match '$rchosen'"; rm -rf "$rdir"; exit 1; }
  [ ! -s "$rdir/closed.txt" ] || { echo "FAIL reconcile single-match closed something"; rm -rf "$rdir"; exit 1; }
  # two matches, both red -> keep tracked, close nothing
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
case "$2" in list) printf '301\n302\n' ;; checks) printf 'fail\npass\n' ;; close) echo "$3" >> "$GH_CLOSED" ;; esac
exit 0
SH
  : > "$rdir/closed.txt"
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i301 301)
  [ "$rchosen" = 301 ]        || { echo "FAIL reconcile all-red '$rchosen'"; rm -rf "$rdir"; exit 1; }
  [ ! -s "$rdir/closed.txt" ] || { echo "FAIL reconcile all-red closed something"; rm -rf "$rdir"; exit 1; }
  rm -rf "$rdir"
  echo "_reconcile_item_pr OK"
  # --- RC2: _session_died (idle is NOT death) --------------------------------
  [ "$(_session_died running '')"   = "alive" ] || { echo "FAIL _session_died running";  exit 1; }
  [ "$(_session_died idle '')"      = "alive" ] || { echo "FAIL _session_died idle";     exit 1; }
  [ "$(_session_died '' '')"        = "alive" ] || { echo "FAIL _session_died empty";    exit 1; }
  [ "$(_session_died failed '')"    = "died"  ] || { echo "FAIL _session_died failed";   exit 1; }
  [ "$(_session_died error '')"     = "died"  ] || { echo "FAIL _session_died error";    exit 1; }
  [ "$(_session_died cancelled '')" = "died"  ] || { echo "FAIL _session_died cancelled";exit 1; }
  [ "$(_session_died idle 'ReadError')" = "died" ] || { echo "FAIL _session_died errcode"; exit 1; }
  # #61: a failed lookup ("unknown") must still read as alive for a SINGLE poll --
  # never recover against a session we cannot confirm dead. The run-of-unknowns
  # escape hatch lives in the poll loop, not here.
  [ "$(_session_died unknown '')"   = "alive" ] || { echo "FAIL _session_died unknown"; exit 1; }
  echo "_session_died OK"
  # --- RC2: _session_state parse, via fake curl -------
  local ssdir; ssdir=$(mktemp -d)
  cat >"$ssdir/curl" <<'SH'
#!/usr/bin/env bash
# fake curl: print a session JSON built from $FAKE_STATUS / $FAKE_ERR.
printf '{"status":"%s","labels":{"omnigent.last_task_error_code":"%s"}}' "${FAKE_STATUS:-running}" "${FAKE_ERR:-}"
SH
  chmod +x "$ssdir/curl"
  local ss
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x FAKE_STATUS=running _session_state conv_t)
  [ "$ss" = "running|" ] || { echo "FAIL _session_state running: '$ss'"; exit 1; }
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x FAKE_STATUS=failed FAKE_ERR=ReadError _session_state conv_t)
  [ "$ss" = "failed|ReadError" ] || { echo "FAIL _session_state failed: '$ss'"; exit 1; }
  # #61: a FAILED lookup must be distinguishable from a healthy session. Before
  # this it returned "|", which _session_died read as alive -- indistinguishable
  # from a real answer, which is why the v0.9.0 endpoint removal went unnoticed.
  cat >"$ssdir/curl" <<'SH'
#!/usr/bin/env bash
exit 22          # curl -f on an HTTP error
SH
  chmod +x "$ssdir/curl"
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x _session_state conv_t)
  [ "$ss" = "unknown|" ] || { echo "FAIL _session_state http-error: expected 'unknown|', got '$ss'"; exit 1; }
  cat >"$ssdir/curl" <<'SH'
#!/usr/bin/env bash
printf 'not json at all'
SH
  chmod +x "$ssdir/curl"
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x _session_state conv_t)
  [ "$ss" = "unknown|" ] || { echo "FAIL _session_state malformed: expected 'unknown|', got '$ss'"; exit 1; }
  rm -rf "$ssdir"
  echo "session helpers OK (incl. #61 unknown-vs-alive)"
  # --- #60: _last_assistant_text hits the v0.9.0 endpoint and reports failure --
  local ldir; ldir=$(mktemp -d)
  cat >"$ldir/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$URL_LOG"
printf '{"data":[{"role":"assistant","content":[{"text":"You have hit your usage limit"}]}]}'
SH
  chmod +x "$ldir/curl"
  local ltxt
  ltxt=$(PATH="$ldir:$PATH" SERVER=http://x URL_LOG="$ldir/urls" _last_assistant_text conv_t 3)
  grep -q '/v1/sessions/conv_t/items' "$ldir/urls" \
    || { echo "FAIL _last_assistant_text: wrong endpoint"; cat "$ldir/urls"; rm -rf "$ldir"; exit 1; }
  grep -q '/v1/conversations/' "$ldir/urls" \
    && { echo "FAIL _last_assistant_text: still calling the REMOVED conversations endpoint"; rm -rf "$ldir"; exit 1; }
  [ "$(_is_limit_message "$ltxt")" = yes ] \
    || { echo "FAIL _last_assistant_text: limit message not detected: '$ltxt'"; rm -rf "$ldir"; exit 1; }
  # A failed lookup must be rc 1, NOT rc 0 with empty output (the #60 defect).
  cat >"$ldir/curl" <<'SH'
#!/usr/bin/env bash
exit 22
SH
  chmod +x "$ldir/curl"
  if PATH="$ldir:$PATH" SERVER=http://x _last_assistant_text conv_t 3 >/dev/null 2>&1; then
    echo "FAIL _last_assistant_text: failed lookup returned rc 0 (indistinguishable from 'no text')"; rm -rf "$ldir"; exit 1
  fi
  # A 200 carrying junk is ALSO a failed lookup -- the first cut of this fix only
  # handled curl rc, so a malformed body still returned rc 0 + empty (codex review).
  cat >"$ldir/curl" <<'SH'
#!/usr/bin/env bash
printf 'this is not json'
SH
  chmod +x "$ldir/curl"
  if PATH="$ldir:$PATH" SERVER=http://x _last_assistant_text conv_t 3 >/dev/null 2>&1; then
    echo "FAIL _last_assistant_text: malformed 200 body returned rc 0"; rm -rf "$ldir"; exit 1
  fi
  # ...as is a 200 whose shape is not what the parser expects.
  cat >"$ldir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"items":[{"role":"assistant"}]}'
SH
  chmod +x "$ldir/curl"
  if PATH="$ldir:$PATH" SERVER=http://x _last_assistant_text conv_t 3 >/dev/null 2>&1; then
    echo "FAIL _last_assistant_text: unexpected schema (no .data) returned rc 0"; rm -rf "$ldir"; exit 1
  fi
  # But a well-formed response with NO assistant text is a legitimate empty: rc 0.
  cat >"$ldir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"data":[{"role":"user","content":[{"text":"hello"}]}]}'
SH
  chmod +x "$ldir/curl"
  PATH="$ldir:$PATH" SERVER=http://x _last_assistant_text conv_t 3 >/dev/null 2>&1 \
    || { echo "FAIL _last_assistant_text: legitimate empty must be rc 0, not a failure"; rm -rf "$ldir"; exit 1; }
  rm -rf "$ldir"
  echo "_last_assistant_text OK (#60 endpoint + loud failure)"
  # --- #61b: the rc-5 delivery-retry budget must actually bound -------------
  [ "$(_send_retry_decision 0 2)" = retry   ] || { echo "FAIL send-retry 0/2";  exit 1; }
  [ "$(_send_retry_decision 1 2)" = retry   ] || { echo "FAIL send-retry 1/2";  exit 1; }
  [ "$(_send_retry_decision 2 2)" = give-up ] || { echo "FAIL send-retry 2/2";  exit 1; }
  [ "$(_send_retry_decision 9 2)" = give-up ] || { echo "FAIL send-retry 9/2";  exit 1; }
  # Garbage or absent config must NOT mean unbounded -- that is the loop this
  # exists to prevent.
  [ "$(_send_retry_decision 2 '')"    = give-up ] || { echo "FAIL send-retry empty max";  exit 1; }
  [ "$(_send_retry_decision 2 abc)"   = give-up ] || { echo "FAIL send-retry junk max";   exit 1; }
  [ "$(_send_retry_decision 1 0)"     = give-up ] || { echo "FAIL send-retry zero max";   exit 1; }
  # All-digit but too large for the shell's integer test: the comparison ERRORS,
  # and an erroring test used to fall through to "retry" = unbounded.
  [ "$(_send_retry_decision 5 999999999999999999999999999999)" = give-up ] \
    || { echo "FAIL send-retry oversized max (unbounded retry)"; exit 1; }
  [ "$(_send_retry_decision 99999999999999999999 2)" = give-up ] \
    || { echo "FAIL send-retry oversized fails"; exit 1; }
  echo "_send_retry_decision OK (#61b bounded)"
  # --- REST launch helpers, via fake curl on PATH -----------------------------
  local rdir; rdir=$(mktemp -d)
  cat >"$rdir/curl" <<'SH'
#!/usr/bin/env bash
# fake curl: record the invocation to $CURL_LOG; emit canned JSON by endpoint.
printf '%s\n' "$*" >> "${CURL_LOG:-/dev/null}"
last=""; for a in "$@"; do last="$a"; done
if printf '%s\n' "$@" | grep -q '/events'; then exit 0; fi          # message/stop -> 2xx, no body
if printf '%s\n' "$@" | grep -q -- '-F'; then echo '{"session_id":"conv_holder1"}'; exit 0; fi  # multipart upload
if printf '%s\n' "$@" | grep -q '/v1/sessions/conv_run1'; then echo '{"id":"conv_run1","agent_id":"ag_x","host_id":"host_local","status":"running"}'; exit 0; fi
if printf '%s\n' "$@" | grep -q '/v1/sessions/conv_holder1'; then echo '{"id":"conv_holder1","agent_id":"ag_x"}'; exit 0; fi
if printf '%s\n' "$@" | grep -q 'POST'; then echo '{"id":"conv_run1"}'; exit 0; fi  # JSON create
echo '{}'
SH
  chmod +x "$rdir/curl"
  local got
  got=$(PATH="$rdir:$PATH" SERVER=http://x _upload_bundle "$rdir" "t")
  [ "$got" = "conv_holder1" ] || { echo "FAIL _upload_bundle: '$got'"; exit 1; }
  got=$(PATH="$rdir:$PATH" SERVER=http://x _get_agent_id conv_holder1)
  [ "$got" = "ag_x" ] || { echo "FAIL _get_agent_id: '$got'"; exit 1; }
  got=$(PATH="$rdir:$PATH" SERVER=http://x _create_session ag_x host_local /workspaces/muesli)
  [ "$got" = "conv_run1" ] || { echo "FAIL _create_session: '$got'"; exit 1; }
  PATH="$rdir:$PATH" SERVER=http://x CURL_LOG="$rdir/log" _send_prompt conv_run1 $'a "quoted" prompt\nline2'
  grep -q '/v1/sessions/conv_run1/events' "$rdir/log" || { echo "FAIL _send_prompt endpoint"; exit 1; }
  PATH="$rdir:$PATH" SERVER=http://x CURL_LOG="$rdir/log2" _stop_session conv_run1
  grep -q 'stop_session' "$rdir/log2" || { echo "FAIL _stop_session payload"; exit 1; }
  rm -rf "$rdir"
  echo "REST helpers OK"
  # --- B-1: _checkrun_state (main-CI classification) --------------------------
  [ "$(_checkrun_state $'completed|success\ncompleted|success')" = "green" ]   || { echo "FAIL _checkrun_state green"; exit 1; }
  [ "$(_checkrun_state $'completed|success\ncompleted|failure')" = "red" ]     || { echo "FAIL _checkrun_state red"; exit 1; }
  [ "$(_checkrun_state $'completed|success\nin_progress|')" = "pending" ]      || { echo "FAIL _checkrun_state pending"; exit 1; }
  [ "$(_checkrun_state '')" = "pending" ]                                      || { echo "FAIL _checkrun_state empty"; exit 1; }
  [ "$(_checkrun_state 'completed|action_required')" = "red" ]                 || { echo "FAIL _checkrun_state action_required"; exit 1; }
  [ "$(_checkrun_state $'completed|skipped\ncompleted|neutral')" = "green" ]   || { echo "FAIL _checkrun_state skipped/neutral"; exit 1; }
  # GREEN IS AN ALLOWLIST. These are the values that used to fall through two
  # denylists onto the green default. `waiting`, `requested` and `pending` are all in
  # GitHub's documented check-run `status` enum -- `waiting`/`requested` are exactly
  # what a check run behind a deployment approval gate reports -- so a required check
  # that had not started read as though it had passed.
  for _st in 'waiting|' 'requested|' 'pending|' 'queued|' 'in_progress|' 'some_future_status|'; do
    [ "$(_checkrun_state "$_st")" = "pending" ] \
      || { echo "FAIL _checkrun_state: '$_st' must be pending, not green"; exit 1; }
    [ "$(_checkrun_state "$(printf 'completed|success\n%s' "$_st")")" = "pending" ] \
      || { echo "FAIL _checkrun_state: '$_st' must hold the whole set pending"; exit 1; }
  done
  # A completed run with no conclusion, or one GitHub adds later, is not evidence of
  # a pass. Red rather than pending: a spurious revert is recoverable, a false green
  # ships broken code.
  for _cc in 'completed|' 'completed|startup_failure' 'completed|stale' 'completed|some_future_conclusion'; do
    [ "$(_checkrun_state "$_cc")" = "red" ] \
      || { echo "FAIL _checkrun_state: '$_cc' must be red, not green"; exit 1; }
  done
  # Red still outranks pending when both are present, in EITHER order -- red
  # short-circuits on the first red line while pending is only decided after the
  # whole set, so ordering must not change the verdict.
  [ "$(_checkrun_state $'in_progress|\ncompleted|failure')" = "red" ] \
    || { echo "FAIL _checkrun_state: red must outrank pending"; exit 1; }
  [ "$(_checkrun_state $'completed|failure\nin_progress|')" = "red" ] \
    || { echo "FAIL _checkrun_state: red must outrank pending in either order"; exit 1; }
  # Malformed lines must never read green. A one-field line has no conclusion to
  # match; extra fields, trailing whitespace and a stray CR all make the conclusion
  # unrecognised. Each of these reached this function as green before the allowlist.
  [ "$(_checkrun_state 'completed')" = "red" ]              || { echo "FAIL _checkrun_state: one-field line"; exit 1; }
  [ "$(_checkrun_state 'completed|success|extra')" = "red" ] || { echo "FAIL _checkrun_state: three-field line"; exit 1; }
  [ "$(_checkrun_state 'completed|success ')" = "red" ]      || { echo "FAIL _checkrun_state: trailing whitespace"; exit 1; }
  [ "$(_checkrun_state "$(printf 'completed|success\r')")" = "red" ] || { echo "FAIL _checkrun_state: trailing CR"; exit 1; }
  [ "$(_checkrun_state ' completed|success')" = "pending" ]  || { echo "FAIL _checkrun_state: leading whitespace"; exit 1; }
  [ "$(_checkrun_state '   ')" = "pending" ]                 || { echo "FAIL _checkrun_state: whitespace-only"; exit 1; }
  # The final line must be read whether or not the input ends in a newline -- the
  # here-doc supplies the terminator, but that is worth pinning.
  [ "$(_checkrun_state 'completed|failure')" = "red" ] \
    || { echo "FAIL _checkrun_state: unterminated single line dropped"; exit 1; }
  [ "$(_checkrun_state $'completed|success\ncompleted|failure')" = "red" ] \
    || { echo "FAIL _checkrun_state: unterminated final line dropped"; exit 1; }
  [ "$(_checkrun_state $'completed|success\n')" = "green" ] \
    || { echo "FAIL _checkrun_state: a trailing newline must not read as a record"; exit 1; }
  unset _st _cc
  echo "_checkrun_state OK (green is an allowlist)"
  # --- #67: commit statuses must reach the CI verdict ------------------------
  # A required context can be a commit STATUS, not a check-run. The post-merge
  # watcher read only /check-runs, so such a context was invisible to it.
  #
  # The fake gh runs the REAL jq filter against fixture JSON, so these exercise
  # the actual normalisation. A first cut had the shim return pre-normalised
  # lines, which meant the `error -> failure` mapping was never executed and two
  # assertions were accidentally identical.
  local cdir; cdir=$(mktemp -d)
  cat >"$cdir/gh" <<'SH'
#!/usr/bin/env bash
# args: api [--paginate --slurp] <path> [-q <filter>]
#
# Three fixture shapes per endpoint, deliberately distinct:
#   FAKE_*_JSON   one page object; wrapped in [...] when --slurp is present, because
#                 that is what real `gh api --slurp` emits.
#   FAKE_*_PAGES  a complete page ARRAY -- served in full ONLY when --paginate is
#                 present, and truncated to page 1 otherwise, because that is what
#                 real `gh api` does. Without that the pagination test would pass
#                 even with --paginate deleted, i.e. assert nothing.
#   FAKE_*_BODY   emitted VERBATIM, so a test can express a degraded response that
#                 the wrapping would otherwise repair. Without a BODY var on the
#                 check-runs side, its empty-body false-green was inexpressible,
#                 which is exactly why it survived three reviews.
#
# jq's exit status is propagated: an earlier version ended with a blanket `exit 0`,
# which swallowed it, so the malformed-response tests could never have failed.
# Defaults are assigned plainly rather than inside a ${VAR:-...} expansion, whose
# escaping produced invalid JSON.
filter=""; nx=0; slurp=0; paginate=0
for a in "$@"; do
  [ "$nx" = 1 ] && { filter="$a"; nx=0; }
  [ "$a" = "-q" ] && nx=1
  [ "$a" = "--slurp" ] && slurp=1
  [ "$a" = "--paginate" ] && paginate=1
done
_pages() { # <pages-array> -> all of it with --paginate, else just page 1
  if [ "$paginate" = 1 ]; then printf '%s' "$1"; else printf '%s' "$1" | jq -c '[.[0]]'; fi
}
runs_json="${FAKE_RUNS_JSON-}";     [ -n "$runs_json" ]   || runs_json='{"check_runs":[]}'
status_json="${FAKE_STATUS_JSON-}"; [ -n "$status_json" ] || status_json='{"statuses":[]}'
if   [ -n "${FAKE_RUNS_BODY+set}" ];  then runs_out="$FAKE_RUNS_BODY"
elif [ -n "${FAKE_RUNS_PAGES-}" ];    then runs_out="$(_pages "$FAKE_RUNS_PAGES")"
elif [ "$slurp" = 1 ];                then runs_out="[$runs_json]"
else                                       runs_out="$runs_json"; fi
if   [ -n "${FAKE_STATUS_BODY+set}" ]; then status_out="$FAKE_STATUS_BODY"
elif [ -n "${FAKE_STATUS_PAGES-}" ];   then status_out="$(_pages "$FAKE_STATUS_PAGES")"
elif [ "$slurp" = 1 ];                 then status_out="[$status_json]"
else                                        status_out="$status_json"; fi
case "$*" in
  *"/check-runs"*) [ "${FAKE_RUNS_RC:-0}" = 0 ] || exit 1
                   if [ -z "$filter" ]; then printf '%s' "$runs_out"; exit 0; fi
                   printf '%s' "$runs_out"   | jq -r "$filter"; exit $? ;;
  *"/status"*)     [ "${FAKE_STATUS_RC:-0}" = 0 ] || exit 1
                   # No -q: the caller wants raw JSON (it applies jq itself).
                   if [ -z "$filter" ]; then printf '%s' "$status_out"; exit 0; fi
                   printf '%s' "$status_out" | jq -r "$filter"; exit $? ;;
esac
exit 0
SH
  chmod +x "$cdir/gh"
  _st_json() { printf '{"statuses":[{"context":"ext-ci","state":"%s","updated_at":"2026-01-01T00:00:00Z"}]}' "$1"; }
  _cl() { PATH="$cdir:$PATH" REPO=demo/demo _commit_ci_lines abc123; }
  # Each status state maps to the right check-run shape -- via the real jq.
  [ "$(FAKE_STATUS_JSON="$(_st_json success)" _cl)" = "ext-ci|completed|success|" ] \
    || { echo "FAIL _commit_ci_lines: success mapping"; rm -rf "$cdir"; exit 1; }
  [ "$(FAKE_STATUS_JSON="$(_st_json failure)" _cl)" = "ext-ci|completed|failure|" ] \
    || { echo "FAIL _commit_ci_lines: failure mapping"; rm -rf "$cdir"; exit 1; }
  # THE TRAP: `error` passed through as a conclusion would read GREEN, because
  # _checkrun_state matches only check-run failure conclusions.
  [ "$(FAKE_STATUS_JSON="$(_st_json error)" _cl)" = "ext-ci|completed|failure|" ] \
    || { echo "FAIL _commit_ci_lines: status 'error' must map to failure, not pass through"; rm -rf "$cdir"; exit 1; }
  [ "$(_checkrun_state "$(FAKE_STATUS_JSON="$(_st_json error)" _cl | sed 's/^[^|]*|//')")" = red ] \
    || { echo "FAIL _commit_ci_lines: status 'error' must make the verdict red"; rm -rf "$cdir"; exit 1; }
  [ "$(FAKE_STATUS_JSON="$(_st_json pending)" _cl)" = "ext-ci|in_progress||" ] \
    || { echo "FAIL _commit_ci_lines: pending mapping"; rm -rf "$cdir"; exit 1; }
  # Newest-per-context only: a stale duplicate must not flip the verdict.
  [ "$(FAKE_STATUS_JSON='{"statuses":[{"context":"ext-ci","state":"failure","updated_at":"2026-01-01T00:00:00Z"},{"context":"ext-ci","state":"success","updated_at":"2026-01-02T00:00:00Z"}]}' _cl)" \
    = "ext-ci|completed|success|" ] \
    || { echo "FAIL _commit_ci_lines: must take the NEWEST status per context"; rm -rf "$cdir"; exit 1; }
  # Statuses merge with check-runs.
  [ "$(FAKE_RUNS_JSON='{"check_runs":[{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' \
       FAKE_STATUS_JSON="$(_st_json success)" _cl | sort | tr '\n' ' ')" \
    = "ext-ci|completed|success| server|completed|success|15368 " ] \
    || { echo "FAIL _commit_ci_lines: statuses not merged with check-runs"; rm -rf "$cdir"; exit 1; }
  # FAIL CLOSED: either fetch failing must not yield a partial (false-green) list.
  FAKE_STATUS_RC=1 _cl >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: status fetch failure must be rc 1, not a green subset"; rm -rf "$cdir"; exit 1; }
  FAKE_RUNS_RC=1 _cl >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: check-run fetch failure must be rc 1"; rm -rf "$cdir"; exit 1; }
  # Zero statuses is NORMAL -- merge commits legitimately carry none (verified
  # against muesli: merge commits have 0, PR heads carry review-gate).
  [ "$(FAKE_RUNS_JSON='{"check_runs":[{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' _cl)" \
    = "server|completed|success|15368" ] \
    || { echo "FAIL _commit_ci_lines: zero statuses must be normal, not an error"; rm -rf "$cdir"; exit 1; }
  # EQUAL timestamps: GitHub returns newest-first, and max_by picks the LAST equal
  # maximum -- so `[failure, success]` at the same second selected the stale
  # success and read GREEN. Tie-break is now by input order.
  [ "$(FAKE_STATUS_JSON='{"statuses":[{"context":"ext-ci","state":"failure","updated_at":"2026-01-01T00:00:00Z"},{"context":"ext-ci","state":"success","updated_at":"2026-01-01T00:00:00Z"}]}' _cl)" \
    = "ext-ci|completed|failure|" ] \
    || { echo "FAIL _commit_ci_lines: equal timestamps must take the FIRST (newest) entry"; rm -rf "$cdir"; exit 1; }
  # A context carrying the field delimiter cannot be represented; emitting it
  # anyway turned a required PENDING status into GREEN. Must fail closed.
  # A REQUIRED unrepresentable context is "cannot classify" -> fail closed.
  FAKE_STATUS_JSON='{"statuses":[{"context":"a|b","state":"pending","updated_at":"2026-01-01T00:00:00Z"}]}' \
    PATH="$cdir:$PATH" REPO=demo/demo _commit_ci_lines abc123 'a|b' >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: a REQUIRED pipe-bearing context must fail closed"; rm -rf "$cdir"; exit 1; }
  # A NON-required one is dropped, not fatal -- halting every verdict over a
  # cosmetic name in someone else's repo would be an outage, and it would be
  # filtered downstream anyway.
  [ "$(FAKE_RUNS_JSON='{"check_runs":[{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' \
       FAKE_STATUS_JSON='{"statuses":[{"context":"a|b","state":"pending","updated_at":"2026-01-01T00:00:00Z"}]}' \
       PATH="$cdir:$PATH" REPO=demo/demo _commit_ci_lines abc123 'server' 2>/dev/null)" \
    = "server|completed|success|15368" ] \
    || { echo "FAIL _commit_ci_lines: a NON-required pipe-bearing context must be dropped, not fatal"; rm -rf "$cdir"; exit 1; }
  # An absent .statuses key is a malformed response, not "no statuses".
  FAKE_STATUS_JSON='{}' _cl >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: absent .statuses must fail closed"; rm -rf "$cdir"; exit 1; }
  # A state GitHub may add later must not read as success.
  [ "$(FAKE_STATUS_JSON="$(_st_json some_new_state)" _cl)" = "ext-ci|completed|failure|" ] \
    || { echo "FAIL _commit_ci_lines: an unknown state must not read as success"; rm -rf "$cdir"; exit 1; }
  # The exact false-green the sentinel design allowed: a REAL context whose name
  # merely begins with the old sentinel string was deleted by the cleanup while
  # not being recognised as malformed, so a required FAILING status vanished and
  # the verdict read green. Control records no longer share a namespace with data.
  [ "$(FAKE_STATUS_JSON='{"statuses":[{"context":"__MALFORMED_CONTEXT__prod","state":"failure","updated_at":"2026-01-01T00:00:00Z"}]}' _cl)" \
    = "__MALFORMED_CONTEXT__prod|completed|failure|" ] \
    || { echo "FAIL _commit_ci_lines: a context named like the old sentinel must survive as a normal record"; rm -rf "$cdir"; exit 1; }
  [ "$(_checkrun_state "$(FAKE_STATUS_JSON='{"statuses":[{"context":"__MALFORMED_CONTEXT__prod","state":"failure","updated_at":"2026-01-01T00:00:00Z"}]}' _cl | sed 's/^[^|]*|//')")" = red ] \
    || { echo "FAIL _commit_ci_lines: sentinel-named failing context must still read red"; rm -rf "$cdir"; exit 1; }
  # A DEGRADED response must fail closed, not read as "no records". `gh api` can
  # return rc 0 with an empty body, and jq exits 0 with no output on zero inputs --
  # so without a shape check a required context would match nothing, fall back to
  # all checks, and read GREEN. The list also covers responses that are valid JSON
  # but not the page ARRAY --slurp promises, and TWO CONCATENATED documents, which
  # a bare `jq -e` accepts because it reports only the last one's status.
  for _body in "" "null" "{}" '[]' '{"statuses":[]}' '[{"statuses":null}]' \
               '[{"message":"Not Found"}]' '[{"statuses":[]}][{"statuses":[]}]' \
               '[{"message":"x"}][{"statuses":[]}]'; do
    FAKE_RUNS_JSON='{"check_runs":[{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' \
      FAKE_STATUS_BODY="$_body" _cl >/dev/null 2>&1 \
      && { echo "FAIL _commit_ci_lines: degraded status body '${_body:-<empty>}' must fail closed"; rm -rf "$cdir"; exit 1; }
  done
  # THE FOURTH FALSE-GREEN: the check-runs half was a bare `gh api -q` with no shape
  # check while the status half was validated, so an empty check-runs body dropped
  # every check-run and a lone green status carried the verdict. The test shim could
  # not even express it (no BODY var on that side), which is how it survived three
  # adversarial passes. Both halves now go through _ci_fetch_records.
  for _body in "" "null" "{}" '[]' '{"check_runs":[]}' '[{"check_runs":null}]' \
               '[{"message":"Not Found"}]' '[{"check_runs":[]}][{"check_runs":[]}]'; do
    FAKE_RUNS_BODY="$_body" FAKE_STATUS_JSON="$(_st_json success)" _cl >/dev/null 2>&1 \
      && { echo "FAIL _commit_ci_lines: degraded check-runs body '${_body:-<empty>}' must fail closed"; rm -rf "$cdir"; exit 1; }
  done
  # ...but a well-formed EMPTY list on either side is legitimate: merge commits carry
  # no statuses, and a commit with no CI at all carries no check-runs (-> pending).
  [ "$(FAKE_RUNS_JSON='{"check_runs":[{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' \
       FAKE_STATUS_BODY='[{"statuses":[]}]' _cl)" = "server|completed|success|15368" ] \
    || { echo "FAIL _commit_ci_lines: an empty statuses ARRAY must be accepted"; rm -rf "$cdir"; exit 1; }
  [ "$(FAKE_RUNS_BODY='[{"check_runs":[]}]' FAKE_STATUS_JSON="$(_st_json success)" _cl)" \
    = "ext-ci|completed|success|" ] \
    || { echo "FAIL _commit_ci_lines: an empty check_runs ARRAY must be accepted"; rm -rf "$cdir"; exit 1; }
  # PAGINATION. GitHub serves 30 records per page; muesli's main commit already
  # carries 28 check-runs. Without --paginate a required FAILURE on page 2 is simply
  # not fetched, page 1 is all green, and the verdict reads green.
  [ "$(FAKE_RUNS_PAGES='[{"check_runs":[{"name":"p1","status":"completed","conclusion":"success","app":{"id":15368}}]},{"check_runs":[{"name":"p2","status":"completed","conclusion":"failure","app":{"id":15368}}]}]' \
       _cl | sort | tr '\n' ' ')" = "p1|completed|success|15368 p2|completed|failure|15368 " ] \
    || { echo "FAIL _commit_ci_lines: records beyond page 1 must be fetched"; rm -rf "$cdir"; exit 1; }
  [ "$(_checkrun_state "$(FAKE_RUNS_PAGES='[{"check_runs":[{"name":"p1","status":"completed","conclusion":"success","app":{"id":15368}}]},{"check_runs":[{"name":"p2","status":"completed","conclusion":"failure","app":{"id":15368}}]}]' \
       _cl | sed 's/^[^|]*|//')")" = red ] \
    || { echo "FAIL _commit_ci_lines: a page-2 failure must make the verdict red"; rm -rf "$cdir"; exit 1; }
  # A record with a non-string name/status would emit `null|...`, which classifies as
  # an unrecognised -- therefore GREEN -- line. Never guess at a malformed record.
  for _body in '[{"check_runs":[{"status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
               '[{"check_runs":[{"name":null,"status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
               '[{"check_runs":[{"name":{"x":1},"status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
               '[{"check_runs":[{"name":"server","status":null,"conclusion":"success"}]}]'; do
    FAKE_RUNS_BODY="$_body" _cl >/dev/null 2>&1 \
      && { echo "FAIL _commit_ci_lines: non-string record field must fail closed ($_body)"; rm -rf "$cdir"; exit 1; }
  done
  # Deduplication SORTS on `updated_at`, so that field must be validated before it is
  # sorted on: jq orders null below every string, so a stale success with a null
  # timestamp could beat a current failure and still return rc 0.
  for _body in '[{"statuses":[{"context":"ext-ci","state":"success"}]}]' \
               '[{"statuses":[{"context":"ext-ci","state":"success","updated_at":null}]}]' \
               '[{"statuses":[{"context":"ext-ci","state":"success","updated_at":123}]}]' \
               '[{"statuses":[{"context":null,"state":"success","updated_at":"2026-01-01T00:00:00Z"}]}]' \
               '[{"statuses":[{"context":"ext-ci","state":null,"updated_at":"2026-01-01T00:00:00Z"}]}]'; do
    FAKE_STATUS_BODY="$_body" _cl >/dev/null 2>&1 \
      && { echo "FAIL _commit_ci_lines: unsortable/malformed status record must fail closed ($_body)"; rm -rf "$cdir"; exit 1; }
  done
  # A NEWLINE in a name breaks both the line protocol and the line-oriented required
  # match, so it cannot even be established whether the check is required -> always
  # fail closed, required set or not.
  FAKE_STATUS_JSON='{"statuses":[{"context":"a\nb","state":"pending","updated_at":"2026-01-01T00:00:00Z"}]}' \
    _cl >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: a newline-bearing name must fail closed"; rm -rf "$cdir"; exit 1; }
  # The delimiter rule now applies to CHECK-RUNS on the same required/non-required
  # terms as statuses; it used to refuse unconditionally on that side, which would
  # halt every wave over one awkwardly-named job in a repo bircher does not control.
  FAKE_RUNS_JSON='{"check_runs":[{"name":"a|b","status":"queued","conclusion":null}]}' \
    PATH="$cdir:$PATH" REPO=demo/demo _commit_ci_lines abc123 'a|b' >/dev/null 2>&1 \
    && { echo "FAIL _commit_ci_lines: a REQUIRED pipe-bearing check-run must fail closed"; rm -rf "$cdir"; exit 1; }
  [ "$(FAKE_RUNS_JSON='{"check_runs":[{"name":"a|b","status":"queued","conclusion":null},{"name":"server","status":"completed","conclusion":"success","app":{"id":15368}}]}' \
       PATH="$cdir:$PATH" REPO=demo/demo _commit_ci_lines abc123 'server' 2>/dev/null)" \
    = "server|completed|success|15368" ] \
    || { echo "FAIL _commit_ci_lines: a NON-required pipe-bearing check-run must be dropped, not fatal"; rm -rf "$cdir"; exit 1; }
  rm -rf "$cdir"; unset -f _cl _st_json
  echo "_commit_ci_lines OK (#67 statuses reach the verdict, fails closed)"
  # --- B-2v2/B-3v2: limit-message matcher + usage-aware vendor pick -----------
  [ "$(_is_limit_message "You've hit your session limit - resets 6pm")" = "yes" ] || { echo "FAIL limitmsg session"; exit 1; }
  [ "$(_is_limit_message "weekly limit exceeded... hit your weekly limit")" = "yes" ] || { echo "FAIL limitmsg weekly"; exit 1; }
  [ "$(_is_limit_message "Implemented the rate limiter as specified")" = "no" ] || { echo "FAIL limitmsg falsepos"; exit 1; }
  #                      c5  c5r  c7  x5  x5r  x7  now
  FIVEH_MAX=92
  [ "$(_pick_implementer 10 100 50  10 200 30 1000)" = "codex" ]       || { echo "FAIL pick lower-weekly codex"; exit 1; }
  [ "$(_pick_implementer 10 100 20  10 200 60 1000)" = "claude_code" ] || { echo "FAIL pick lower-weekly claude"; exit 1; }
  [ "$(_pick_implementer 95 100 20  10 200 60 1000)" = "codex" ]       || { echo "FAIL pick claude-5h-excluded"; exit 1; }
  [ "$(_pick_implementer 10 100 80  97 200 5  1000)" = "claude_code" ] || { echo "FAIL pick codex-5h-excluded"; exit 1; }
  [ "$(_pick_implementer 95 1500 20 97 1200 5 1000)" = "wait:1200" ]   || { echo "FAIL pick both-excluded wait"; exit 1; }
  [ "$(_pick_implementer -  -   -   -  -   -  1000)" = "claude_code" ] || { echo "FAIL pick no-signal default"; exit 1; }
  [ "$(_pick_implementer 50 100 40  -  -   -  1000)" = "codex" ]       || { echo "FAIL pick missing-codex-eligible"; exit 1; }
  echo "_pick_implementer OK"
  # --- #26: host_id shapes differ across omnigent versions (config.yaml stores
  #     host_<uuid>, v0.6.0's session API returns the bare <uuid>). Same host must
  #     match either way; a different host, or an unknown one, must still fail.
  [ "$(_host_ids_match 'abc123' 'host_abc123')" = "yes" ] || { echo "FAIL hostmatch bare-vs-prefixed"; exit 1; }
  [ "$(_host_ids_match 'host_abc123' 'abc123')" = "yes" ] || { echo "FAIL hostmatch prefixed-vs-bare"; exit 1; }
  [ "$(_host_ids_match 'host_abc123' 'host_abc123')" = "yes" ] || { echo "FAIL hostmatch both-prefixed"; exit 1; }
  [ "$(_host_ids_match 'abc123' 'abc123')" = "yes" ] || { echo "FAIL hostmatch both-bare"; exit 1; }
  [ "$(_host_ids_match 'host_abc123' 'host_def456')" = "no" ] || { echo "FAIL hostmatch different-host"; exit 1; }
  [ "$(_host_ids_match 'abc123' 'host_def456')" = "no" ] || { echo "FAIL hostmatch different-host-mixed"; exit 1; }
  [ "$(_host_ids_match '' 'host_abc123')" = "no" ] || { echo "FAIL hostmatch empty-session-host"; exit 1; }
  [ "$(_host_ids_match 'host_' 'host_abc123')" = "no" ] || { echo "FAIL hostmatch prefix-only"; exit 1; }
  echo "_host_ids_match OK"
  # --- _parse_codex_rate_limits: current "prolite" weekly-only schema, legacy
  #     dual-window schema, and garbage all parse correctly (regression for the
  #     codex-cli 0.144.x schema drift that silently pinned every item to codex) -
  local pl lg
  pl='{"type":"token_count","rate_limits":{"limit_id":"codex","limit_name":null,"primary":{"used_percent":6.0,"window_minutes":10080,"resets_at":1784488480},"secondary":null,"credits":null,"plan_type":"prolite"}}'
  [ "$(printf '%s' "$pl" | _parse_codex_rate_limits)" = "-|-|6.0|1784488480" ] || { echo "FAIL codex prolite (weekly-only) parse"; exit 1; }
  lg='{"rate_limits":{"primary":{"used_percent":30,"window_minutes":300,"resets_at":111},"secondary":{"used_percent":55,"window_minutes":10080,"resets_at":222}}}'
  [ "$(printf '%s' "$lg" | _parse_codex_rate_limits)" = "30|111|55|222" ] || { echo "FAIL codex legacy dual-window parse"; exit 1; }
  [ -z "$(printf '%s' 'not json at all' | _parse_codex_rate_limits)" ]    || { echo "FAIL codex garbage -> empty"; exit 1; }
  echo "_codex_usage parse OK"
  # --- commit-msg hook: strips AI-attribution trailers, keeps body + human co-authors
  local hook="$BUNDLE_DIR/githooks/commit-msg" hmf
  if [ -x "$hook" ]; then
    hmf=$(mktemp)
    printf 'feat: real change\n\nExplains the change.\nCo-authored-by: Real Human <human@team.org>\nCo-authored-by: Codex <codex@example.com>\n' > "$hmf"
    "$hook" "$hmf"
    grep -q 'Explains the change.' "$hmf" || { echo "FAIL hook dropped the body"; rm -f "$hmf"; exit 1; }
    grep -q 'Real Human' "$hmf"           || { echo "FAIL hook dropped a human co-author"; rm -f "$hmf"; exit 1; }
    grep -qi 'codex' "$hmf"               && { echo "FAIL hook kept the codex trailer"; rm -f "$hmf"; exit 1; }
    rm -f "$hmf"; echo "commit-msg hook OK"
  else
    echo "WARN: commit-msg hook not executable at $hook" >&2
  fi
  # --- _install_work_git_config forces the operator identity over codex's Codex
  #     author (the real source of the squash Co-authored-by trailer) ------------
  local gdir; gdir=$(mktemp -d)
  ( cd "$gdir" && git init -q && git config user.name Codex && git config user.email codex@example.com )
  _install_work_git_config "$gdir"
  [ "$(git -C "$gdir" config user.name)"  = "Abedegno" ]               || { echo "FAIL identity name not overridden"; rm -rf "$gdir"; exit 1; }
  [ "$(git -C "$gdir" config user.email)" = "jon@jonwilliams.org.uk" ] || { echo "FAIL identity email not overridden"; rm -rf "$gdir"; exit 1; }
  BIRCHER_GIT_AUTHOR_NAME=Custom BIRCHER_GIT_AUTHOR_EMAIL=c@x.io _install_work_git_config "$gdir"
  [ "$(git -C "$gdir" config user.name)"  = "Custom" ]                 || { echo "FAIL identity env override"; rm -rf "$gdir"; exit 1; }
  rm -rf "$gdir"; echo "_install_work_git_config OK"
  # --- Layer-1: _post_cross_review_status retry + verify -----------------------
  local pdir; pdir=$(mktemp -d)
  cat >"$pdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for _post_cross_review_status. CNT counts statuses POSTs; the posted
# context only "lands" (becomes visible to the read-back) from POST attempt >= $LAND_AT.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  echo "headsha1234567"; exit 0
fi
if [ "$1" = "api" ]; then
  if printf '%s\n' "$@" | grep -q '/statuses/'; then
    n=$(cat "$CNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$CNT"
    [ "$n" -ge "${LAND_AT:-1}" ] && printf 'success\n' >> "$STORE"
    exit 0
  fi
  if printf '%s\n' "$@" | grep -q '/status'; then if printf '%s\n' "$@" | grep -q -- '-q'; then :; else printf '[{"statuses":[]}]'; exit 0; fi; cat "$STORE" 2>/dev/null; exit 0; fi
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the
  # catch-all's bare "name|status|conclusion" lines will not do here.
  # The default is assigned to a variable, not written inline: `${VAR:-...}` ends at the
  # first `}` and this JSON is full of them, while escaping them inside a QUOTED heredoc
  # emits literal backslashes. Both produce invalid JSON, a permanently pending watch,
  # and a stall in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  # #70: branch protection. FAKE_PROT_RC=1 models an unreadable protection endpoint.
  printf '%s\n' "$@" | grep -q '/protection' && {
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    # RAW protection JSON since #73 -- the snapshot validates the object before
    # serialising its own typed form, so pre-flattened names no longer parse.
    if [ -n "${FAKE_PROT_CONTEXTS:-}" ]; then
      printf '%s\n' "$FAKE_PROT_CONTEXTS" \
        | jq -R . | jq -s '{required_status_checks: {contexts: ., checks: [.[] | {context: ., app_id: 15368}]}}'
    else
      printf '%s' '{"required_status_checks":{"contexts":[],"checks":[]}}'
    fi
    exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$pdir/gh"
  # retry-then-success: lands only on POST attempt 2 -> rc 0, verified on attempt 2
  ( PATH="$pdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      CNT="$pdir/cnt" STORE="$pdir/store" LAND_AT=2 \
      _post_cross_review_status demo 7 2>"$pdir/err"; rc=$?; [ $rc -eq 0 ] ) \
    && grep -q 'posted+verified .* (attempt 2)' "$pdir/err" \
    || { echo "FAIL _post retry-then-success"; cat "$pdir/err"; rm -rf "$pdir"; exit 1; }
  # never-confirms (covers both a hard POST failure and a 2xx that never persists,
  # since _post trusts the read-back, not the POST rc): rc 1 + ESCALATE line
  : > "$pdir/cnt"; : > "$pdir/store"
  ( PATH="$pdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      CNT="$pdir/cnt" STORE="$pdir/store" LAND_AT=999 \
      _post_cross_review_status demo 7 2>"$pdir/err"; rc=$?; [ $rc -eq 1 ] ) \
    && grep -q 'ESCALATE (ready, needs human merge)' "$pdir/err" \
    || { echo "FAIL _post never-confirms -> rc1+escalate"; cat "$pdir/err"; rm -rf "$pdir"; exit 1; }
  rm -rf "$pdir"; echo "_post_cross_review_status OK (retry+verify)"
  # --- B-1: merge_ready_pr via fake gh (merged + deferred paths) ---------------
  local mdir; mdir=$(mktemp -d)
  cat >"$mdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for merge_ready_pr: $FAKE_MERGEABLE controls mergeability; FAKE_GH_LOG records status posts.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  # The pre-merge GATE asks for all four fields in one response, because two
  # calls could disagree. FAKE_MERGE_STATE defaults to CLEAN so every test
  # written before the gate existed still reaches the merge it is exercising.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    # FAKE_GATE_* and NOT FAKE_PR_STATE: those model two different MOMENTS.
    # FAKE_PR_STATE is what GitHub says AFTER a failed merge attempt (the #71
    # reconciliation probe); the gate looks BEFORE the merge. Reusing one
    # variable for both made the gate see MERGED and defer, so the
    # unreviewed-merge test never reached the merge it exists to exercise.
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  if printf '%s\n' "$@" | grep -q 'state,headRefOid'; then
    # #71 reconciliation probe. FAKE_PR_STATE / FAKE_PR_HEAD model what GitHub says the
    # PR actually looks like after a merge attempt whose client call failed.
    printf '%s|%s\n' "${FAKE_PR_STATE:-OPEN}" "${FAKE_PR_HEAD:-headsha1234567}"; exit 0
  fi
  if printf '%s\n' "$@" | grep -q 'headRefOid'; then echo "headsha1234567"
  elif printf '%s\n' "$@" | grep -q 'mergeCommit'; then
    # FAKE_SHA_RC=1 fails ONLY this lookup. The test used to force the timeout binary
    # missing, which since #71 makes the PRE-merge calls refuse too -- so it deferred
    # long before reaching the merge-sha lookup it meant to exercise.
    [ "${FAKE_SHA_RC:-0}" = 0 ] || exit 1
    # FAKE_NO_SHA models GitHub answering the lookup SUCCESSFULLY with no oid --
    # distinct from the transport failing, and the case that used to return rc 0.
    [ "${FAKE_NO_SHA:-0}" = 1 ] && { echo ""; exit 0; }
    # FAKE_SHA_EMPTY_TIMES models EVENTUAL CONSISTENCY: empty for the first N calls,
    # then the real oid. Counted in a FILE because each call is a separate process.
    if [ -n "${FAKE_SHA_EMPTY_TIMES:-}" ] && [ -n "${FAKE_SHA_COUNT_FILE:-}" ]; then
      _n=$(cat "$FAKE_SHA_COUNT_FILE" 2>/dev/null || echo 0); _n=$((_n + 1))
      echo "$_n" > "$FAKE_SHA_COUNT_FILE"
      [ "$_n" -le "$FAKE_SHA_EMPTY_TIMES" ] && { echo ""; exit 0; }
    fi
    echo "deadbeefsha"
  else echo "${FAKE_MERGEABLE:-MERGEABLE}"; fi
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then exit "${FAKE_MERGE_RC:-0}"; fi
if [ "$1" = "api" ]; then
  # a statuses POST -> record it (log + make it visible to the read-back);
  # a commits/<sha>/status GET -> return the recorded contexts (verify);
  # a check-runs GET -> report main CI green.
  if printf '%s\n' "$@" | grep -q '/statuses/'; then
    echo "$@" >> "${FAKE_GH_LOG:-/dev/null}"
    printf 'success\n' >> "${FAKE_STATUS_STORE:-/dev/null}"
    exit 0
  fi
  # FAKE_STATUS_JSON lets a test put real commit STATUSES on the sha. It was hardcoded
  # empty, so a test that set it exercised nothing and passed without ever creating the
  # condition it named.
  if printf '%s\n' "$@" | grep -q '/status'; then
    if printf '%s\n' "$@" | grep -q -- '-q'; then cat "${FAKE_STATUS_STORE:-/dev/null}" 2>/dev/null; exit 0; fi
    SJ="${FAKE_STATUS_JSON:-}"; [ -n "$SJ" ] || SJ='{"statuses":[]}'
    printf '[%s]' "$SJ"; exit 0
  fi
  # Branch protection. By default the demo repo has none -> a successfully EMPTY
  # required set. FAKE_PROT_CONTEXTS declares one; FAKE_PROT_RC=1 makes the endpoint
  # unreadable, which is a different thing entirely (#70's tri-state).
  if printf '%s\n' "$@" | grep -q '/protection'; then
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    if [ -n "${FAKE_PROT_CONTEXTS:-}" ]; then
      printf '%s\n' "$FAKE_PROT_CONTEXTS" \
        | jq -R . | jq -s '{required_status_checks: {contexts: ., checks: [.[] | {context: ., app_id: 15368}]}}'
    else
      printf '%s' '{"required_status_checks":{"contexts":[],"checks":[]}}'
    fi
    exit 0
  fi
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the catch-all's
  # bare "name|status|conclusion" lines will not do here. The default is assigned to a
  # variable rather than written inline: `${VAR:-...}` ends at the first `}`, which this
  # JSON is full of, and escaping them inside a QUOTED heredoc emits literal backslashes
  # -- which produced invalid JSON, a permanently pending watch, and a 16-minute stall
  # in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$mdir/gh"
  # happy path: mergeable -> merged -> main CI green -> rc 0, empty MERGE_NOTE
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s1" merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] && [ -z "$MERGE_NOTE" ] ) || { echo "FAIL merge_ready_pr happy path"; exit 1; }
  # ELAPSED-TIME GUARD. The happy path now ENTERS the main-CI watch (the shims used to
  # return an empty merge sha to skip it, which is the unsafe shortcut #62 removed), so
  # this asserts the watch is actually bounded by MAIN_CI_POLL_INTERVAL and exits on the
  # first green observation. Without it, hardcoding the interval back to 30s is a silent
  # 3-minute regression that no correctness assertion can see -- verified: that mutation
  # left the suite green and only the clock changed.
  _t0=$(date +%s)
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s7" \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  _t1=$(date +%s)
  [ "$(( _t1 - _t0 ))" -le 15 ] \
    || { echo "FAIL #62: the main-CI watch took $(( _t1 - _t0 ))s -- MAIN_CI_POLL_INTERVAL is not being honoured"; exit 1; }
  unset _t0 _t1
  # #70 END-TO-END, through the real watcher. `e2e-desktop` is required and expected but
  # has NOT registered; the two that have are green. Before this, _keep_blocking_checks
  # saw only those two, _checkrun_state said green, and the watcher broke out. Now the
  # watcher must keep polling and time out rather than declaring main green.
  _t0=$(date +%s)
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/e1" \
    FAKE_PROT_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"server (go)","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #70: main declared GREEN while the expected context 'e2e-desktop' had never registered"; exit 1; }
  _t1=$(date +%s)
  [ "$(( _t1 - _t0 ))" -le 30 ] \
    || { echo "FAIL #70: the completeness wait was not bounded by the settle budget ($(( _t1 - _t0 ))s)"; exit 1; }
  # ...and once it DOES register and finish, the same setup goes green. Without this the
  # assertion above would hold for a gate that never passes anything.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/e2" \
    FAKE_PROT_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"server (go)","status":"completed","conclusion":"success","app":{"id":15368}},{"name":"e2e-desktop","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #70: a COMPLETE expected set must go green"; exit 1; }
  # RED still breaks immediately -- a terminal red is authoritative whether or not the
  # rest has registered, so a regression is never left unreverted awaiting a straggler.
  _t0=$(date +%s)
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=60 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/e3" \
    FAKE_PROT_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"server (go)","status":"completed","conclusion":"failure","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  _t1=$(date +%s)
  [ "$(( _t1 - _t0 ))" -le 20 ] \
    || { echo "FAIL #70: a RED main was held waiting for an unregistered context ($(( _t1 - _t0 ))s)"; exit 1; }
  # AN UNREADABLE required set must never yield green while the list is set.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/e4" FAKE_PROT_RC=1 \
    BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'server (go)')" \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"server (go)","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #70: green accepted while branch protection was unreadable"; exit 1; }
  # OPT-IN: with the list UNSET the identical incomplete setup goes green, exactly as
  # today. This is the assertion that protects every repo nobody has opted in.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/e5" \
    FAKE_PROT_CONTEXTS="$(printf 'server (go)\ne2e-desktop')" \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"server (go)","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #70: with the list UNSET, behaviour must be unchanged (green)"; exit 1; }
  unset _t0 _t1
  # #73 END-TO-END: a stray wrong-app FAILURE must not veto the required app's success.
  # The unit table asserted this of _expected_incomplete, and missed the real defect
  # entirely, because that function only runs once _checkrun_state has already returned
  # green -- and _checkrun_state saw BOTH rows (it filters by name) and said RED first.
  # Branch protection would have accepted app 15368's success; bircher would have
  # reverted a healthy main. Only an integration test can see that.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p1" \
    FAKE_PROT_CONTEXTS='e2e' BIRCHER_MAIN_EXPECTED_CONTEXTS='e2e' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"e2e","status":"completed","conclusion":"success","app":{"id":15368}},{"name":"e2e","status":"completed","conclusion":"failure","app":{"id":999}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #73: a stray wrong-app RED vetoed the required app's green -- protection would have accepted it"; exit 1; }
  # ...and the converse still holds: the REQUIRED app's red is authoritative however many
  # greens another producer posts.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p2" \
    FAKE_PROT_CONTEXTS='e2e' BIRCHER_MAIN_EXPECTED_CONTEXTS='e2e' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"e2e","status":"completed","conclusion":"failure","app":{"id":15368}},{"name":"e2e","status":"completed","conclusion":"success","app":{"id":999}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #73: the required app's RED was masked by a stray producer's green"; exit 1; }
  # ...and only the WRONG app reporting is not satisfaction: it must hold, not go green.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p3" \
    FAKE_PROT_CONTEXTS='e2e' BIRCHER_MAIN_EXPECTED_CONTEXTS='e2e' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"e2e","status":"completed","conclusion":"success","app":{"id":999}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #73: only the WRONG app reported and the watcher accepted green"; exit 1; }
  # ...and the same composition, one case over: a same-named failing commit STATUS. It
  # carries no app, so it is not evidence about app 15368 either way -- but it survived
  # the wrong-app filter, `_checkrun_state` went red, and the matcher that would have
  # ignored it never ran. Two components disagreeing about eligibility means whichever
  # runs first decides.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p4" \
    FAKE_PROT_CONTEXTS='e2e' BIRCHER_MAIN_EXPECTED_CONTEXTS='e2e' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"e2e","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    FAKE_STATUS_JSON='{"statuses":[{"context":"e2e","state":"failure","updated_at":"2026-01-01T00:00:00Z"}]}' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #73: an app-less RED status vetoed the required app's green -- protection would have accepted it"; exit 1; }
  # A REQUIRED CONTEXT OUTSIDE THE DECLARED SUBSET, whose only rows were filtered out.
  # Protection requires A and B; the operator declared only A; A is green from the
  # required app and B reported ONLY from the wrong one. Filtering removes B, so
  # classification sees a smaller all-green set, and completeness only looks at A --
  # green, while branch protection is still waiting for B. Neither component is wrong on
  # its own; the composition is.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p5" \
    FAKE_PROT_CONTEXTS="$(printf 'A\nB')" BIRCHER_MAIN_EXPECTED_CONTEXTS='A' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"A","status":"completed","conclusion":"success","app":{"id":15368}},{"name":"B","status":"completed","conclusion":"failure","app":{"id":999}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #73: a required context whose only rows were FILTERED OUT was accepted as green"; exit 1; }
  # ...but a context that legitimately never reports on a merge commit must NOT be held
  # by this guard, or it would demand review-gate on every merge -- the exact reason #70
  # had to be declared rather than inferred.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p6" \
    FAKE_PROT_CONTEXTS="$(printf 'A\nreview-gate')" BIRCHER_MAIN_EXPECTED_CONTEXTS='A' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"A","status":"completed","conclusion":"success","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #73: a required context that never reported at all must not be held by the filter guard"; exit 1; }
  # AN IGNORE-LISTED REQUIRED CONTEXT that actually reported. Protection requires A and
  # Dependabot; the declared subset names only A; Dependabot is on the ignore list and is
  # still RUNNING. Producer filtering keeps it, `_keep_blocking_checks` removes it,
  # classification goes green over A alone, and the removal guard -- comparing against
  # the producer-filtered set, where it still existed -- saw nothing. Protection is still
  # pending on it.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p7" \
    FAKE_PROT_CONTEXTS="$(printf 'A\nDependabot')" BIRCHER_MAIN_EXPECTED_CONTEXTS='A' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"A","status":"completed","conclusion":"success","app":{"id":15368}},{"name":"Dependabot","status":"in_progress","conclusion":null,"app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #73: an ignore-listed REQUIRED context that reported was erased into green"; exit 1; }
  # ...and the ignore list still does its job for a NON-required check: Dependabot's own
  # failing runs must not turn a healthy main red, which is why that list exists.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=2 MAIN_CI_SETTLE_TIMEOUT=2 \
    MAIN_CI_POLL_INTERVAL=1 FAKE_STATUS_STORE="$mdir/p8" \
    FAKE_PROT_CONTEXTS='A' BIRCHER_MAIN_EXPECTED_CONTEXTS='A' \
    FAKE_CHECKRUNS='[{"check_runs":[{"name":"A","status":"completed","conclusion":"success","app":{"id":15368}},{"name":"Dependabot","status":"completed","conclusion":"failure","app":{"id":15368}}]}]' \
    BIRCHER_MAIN_CI_RERUN=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 ) \
    || { echo "FAIL #73: a NON-required ignore-listed failure must not block the merge"; exit 1; }
  # #62 END-TO-END: a REFUSED merge-sha lookup (no usable timeout) must HALT, not skip
  # the watch and report success. The PR is already merged by this point, so reporting
  # rc 0 lets the queue move on with main unexamined -- the one outcome that must never
  # read as success. Before the fix, `_ci_gh`'s rc 1 fell into the empty-sha branch and
  # returned 0.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s4" \
    FAKE_SHA_RC=1 \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 2 ] && printf '%s' "$MERGE_NOTE" | grep -q 'UNWATCHED' ) \
    || { echo "FAIL #62: a refused merge-sha lookup must halt (rc 2) with an UNWATCHED note, not skip the watch"; exit 1; }
  # ...and the SUCCESSFUL-but-empty answer halts identically. I argued these were
  # different failures and should differ; review refuted it. The invariant is about the
  # outcome -- after a confirmed merge, no identifier means main goes unwatched -- not
  # about which layer failed to supply it. Returning 0 here had `run_item` propagate
  # success and the sweep record `0:merged`, stacking merges onto an unchecked main.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s5" \
    FAKE_NO_SHA=1 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 2 ] && printf '%s' "$MERGE_NOTE" | grep -q 'UNWATCHED' ) \
    || { echo "FAIL #62: an EMPTY merge sha (rc 0) must halt too, not report the merge successful"; exit 1; }
  # --- #71: a failed merge ATTEMPT is not a failed MERGE -----------------------
  # `gh pr merge` can complete server-side and then have its client die. Treating that
  # as failure left a PR that IS merged recorded as deferred, with its merge commit
  # never watched -- the same false-success class #62 closed two of.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/r1" \
    BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=1 FAKE_PR_STATE=MERGED FAKE_PR_HEAD=headsha1234567 \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?; [ "$rc" -eq 0 ] && [ "${MERGE_UNREVIEWED:-0}" = 0 ] ) \
    || { echo "FAIL #71: a client-side merge failure with the PR actually MERGED must reconcile and continue"; exit 1; }
  # Genuinely not merged -> still defers, as before.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/r2" \
    BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=1 FAKE_PR_STATE=OPEN \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?; [ "$rc" -eq 0 ] && printf '%s' "$MERGE_NOTE" | grep -q 'merge deferred' ) \
    || { echo "FAIL #71: a genuinely unmerged PR must still defer"; exit 1; }
  # MERGED AT A HEAD WE NEVER REVIEWED. The code is on main so it must still be watched,
  # but it can never be reported as success -- green CI proves the build is healthy, not
  # that review covered what landed. This is the arm that returns 0 without the guard.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/r3" \
    BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=1 FAKE_PR_STATE=MERGED FAKE_PR_HEAD=someoneelsehead \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 2 ] && [ "${MERGE_UNREVIEWED:-0}" = 1 ] \
      && printf '%s' "$MERGE_UNREVIEWED_NOTE" | grep -q 'NOT the reviewed head' ) \
    || { echo "FAIL #71: an unreviewed merge must halt (rc 2) with durable evidence, even on GREEN main CI"; exit 1; }
  # The EVIDENCE must survive every later outcome. MERGE_NOTE is reassigned by the
  # sha-lookup-failure, revert and unresolved arms, so a note set at detection would be
  # lost on exactly the paths that matter most.
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/r4" \
    BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=1 FAKE_PR_STATE=MERGED FAKE_PR_HEAD=someoneelsehead \
    FAKE_SHA_RC=1 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 2 ] && [ "${MERGE_UNREVIEWED:-0}" = 1 ] \
      && printf '%s' "$MERGE_UNREVIEWED_NOTE" | grep -q 'NOT the reviewed head' \
      && printf '%s' "$MERGE_NOTE" | grep -q 'UNWATCHED' ) \
    || { echo "FAIL #71: the unreviewed evidence must survive a later MERGE_NOTE reassignment"; exit 1; }
  # THE EVIDENCE MUST NOT LEAK TO THE NEXT ITEM. Both globals are reset on entry; without
  # that, one unreviewed merge would mark every subsequent item in the run as unreviewed
  # too -- halting a healthy wave and escalating items nothing was wrong with. Two calls
  # in the SAME shell, because a subshell per call would hide the leak entirely.
  ( PATH="$mdir:$PATH"; REPO=demo/demo; MAIN_CI_TIMEOUT=31; export PATH
    BIRCHER_STATUS_BACKOFF=0
    FAKE_STATUS_STORE="$mdir/l1" FAKE_MERGE_RC=1 FAKE_PR_STATE=MERGED FAKE_PR_HEAD=someoneelsehead \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "${MERGE_UNREVIEWED:-0}" = 1 ] || { echo "setup: first merge should have flagged"; exit 1; }
    FAKE_STATUS_STORE="$mdir/l2" merge_ready_pr demo 8 headsha1234567 >/dev/null 2>&1
    [ "${MERGE_UNREVIEWED:-0}" = 0 ] && [ -z "${MERGE_UNREVIEWED_NOTE:-}" ] ) \
    || { echo "FAIL #71: the unreviewed flag/evidence leaked into the next item"; exit 1; }
  # ...but NOT on first sight. GitHub's PR representation is eventually consistent, so a
  # transient empty oid must be retried, not treated as persistent absence -- halting
  # immediately would strand healthy merges. Two empties then the real oid must proceed.
  : > "$mdir/shacount"
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s6" \
    BIRCHER_STATUS_BACKOFF=0 FAKE_SHA_EMPTY_TIMES=2 FAKE_SHA_COUNT_FILE="$mdir/shacount" \
    merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 0 ] && ! printf '%s' "$MERGE_NOTE" | grep -q 'UNWATCHED' ) \
    || { echo "FAIL #62: a transient empty merge sha must be RETRIED, not halted on first sight"; exit 1; }
  [ "$(cat "$mdir/shacount" 2>/dev/null)" -ge 3 ] \
    || { echo "FAIL #62: the retry never happened (lookup called $(cat "$mdir/shacount" 2>/dev/null) times, expected >=3)"; exit 1; }
  # A non-numeric retry count is covered STRUCTURALLY, not behaviourally -- see the
  # assertion in the _clamp_int block. Driven behaviourally it cannot fail fast:
  # `[ n -ge abc ]` errors every iteration so the break never fires, and the test
  # "catches" the regression by hot-polling the API for two hours. Bounding the count
  # is the property worth asserting, and the structural check asserts it in
  # milliseconds. What IS driven behaviourally here is the retry itself, above.
  # A hostile POLL INTERVAL must not break the watch: unvalidated, `sleep abc` fails and
  # `waited + abc` is an arithmetic error, so the loop never advances and spins to the
  # settle budget. Validated, it falls back to the PRODUCTION default -- which is 30s,
  # so this case legitimately costs one real poll. The assertion is that the watch
  # terminates correctly, not that it is fast; an earlier version bounded it at 15s and
  # failed on the correct behaviour.
  _t0=$(date +%s)
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s9" \
    MAIN_CI_POLL_INTERVAL=abc merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -eq 0 ] ) \
    || { echo "FAIL #62: a non-numeric MAIN_CI_POLL_INTERVAL broke the watch loop"; exit 1; }
  _t1=$(date +%s)
  [ "$(( _t1 - _t0 ))" -le 60 ] \
    || { echo "FAIL #62: a non-numeric MAIN_CI_POLL_INTERVAL did not fall back cleanly ($(( _t1 - _t0 ))s)"; exit 1; }
  unset _t0 _t1
  # deferred path: CONFLICTING -> rc 0 with a deferral note
  ( PATH="$mdir:$PATH" REPO=demo/demo FAKE_MERGEABLE=CONFLICTING FAKE_STATUS_STORE="$mdir/s2" merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] && [ "$MERGE_NOTE" = "merge deferred: mergeable=CONFLICTING" ] && [ "${MERGE_RETRY_ELIGIBLE:-0}" != 1 ] ) \
    || { echo "FAIL merge_ready_pr deferred path"; exit 1; }
  # #10 cross-review status: a ready item posts bircher/cross-review=success before merging
  local slog="$mdir/status.log"; : >"$slog"
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_GH_LOG="$slog" FAKE_STATUS_STORE="$mdir/s3" merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  grep -q 'repos/demo/demo/statuses/headsha' "$slog" \
    && grep -q 'state=success' "$slog" \
    && grep -q 'context=bircher/cross-review' "$slog" \
    || { echo "FAIL merge_ready_pr: cross-review status not posted"; exit 1; }
  rm -rf "$mdir"
  echo "merge_ready_pr OK (incl. #10 cross-review status)"
  # Task 3 (codex P2-1): status-post unconfirmed -> BEST-EFFORT merge (let branch
  # protection decide), not a pre-emptive defer.
  local sdir; sdir=$(mktemp -d)
  cat >"$sdir/gh" <<'SH'
#!/usr/bin/env bash
# cross-review status NEVER verifies (read-back empty); `pr merge` exits $FAKE_MERGE_RC.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  printf '%s\n' "$@" | grep -q 'headRefOid'  && { echo "headsha1234567"; exit 0; }
  # A real sha: an empty one now HALTS (rc 2, main unwatched). These tests set
  # MAIN_CI_TIMEOUT low, so entering the watch costs a second or two.
  printf '%s\n' "$@" | grep -q 'mergeCommit' && { echo "mergesha7654321"; exit 0; }
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "merge" ] && { echo "merge $3" >> "${PMLOG:-/dev/null}"; exit "${FAKE_MERGE_RC:-0}"; }
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q '/statuses/' && exit 0   # POST "ok" but never persists
  printf '%s\n' "$@" | grep -q '/status'    && { if printf '%s\n' "$@" | grep -q -- '-q'; then :; else printf '[{"statuses":[]}]'; exit 0; fi; exit 0; }   # read-back empty -> _post fails
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the
  # catch-all's bare "name|status|conclusion" lines will not do here.
  # The default is assigned to a variable, not written inline: `${VAR:-...}` ends at the
  # first `}` and this JSON is full of them, while escaping them inside a QUOTED heredoc
  # emits literal backslashes. Both produce invalid JSON, a permanently pending watch,
  # and a stall in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  # #70: branch protection. FAKE_PROT_RC=1 models an unreadable protection endpoint.
  printf '%s\n' "$@" | grep -q '/protection' && {
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    [ -n "${FAKE_PROT_CONTEXTS:-}" ] && { printf '%s\n' "$FAKE_PROT_CONTEXTS"; exit 0; }
    exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$sdir/gh"; : > "$sdir/pmlog"
  # (a) protected repo: merge BLOCKED while status unconfirmed -> merge ATTEMPTED, deferred, retry-eligible
  ( PATH="$sdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=1 PMLOG="$sdir/pmlog" \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ $rc -eq 0 ] && [ "${MERGE_RETRY_ELIGIBLE:-x}" = 1 ] && [ "$MERGE_NOTE" = "merge deferred: gh pr merge failed" ] ) \
    || { echo "FAIL merge_ready_pr: status-unconfirmed+blocked not deferred/eligible"; rm -rf "$sdir"; exit 1; }
  grep -qx 'merge 7' "$sdir/pmlog" || { echo "FAIL merge_ready_pr: merge not attempted despite unconfirmed status"; rm -rf "$sdir"; exit 1; }
  # (b) unprotected repo: merge SUCCEEDS while status unconfirmed -> merged (best-effort)
  : > "$sdir/pmlog"
  ( PATH="$sdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 FAKE_MERGE_RC=0 PMLOG="$sdir/pmlog" \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ $rc -eq 0 ] && case "$MERGE_NOTE" in ""|merged*) true;; *) false;; esac ) \
    || { echo "FAIL merge_ready_pr: status-unconfirmed best-effort merge did not succeed"; rm -rf "$sdir"; exit 1; }
  grep -qx 'merge 7' "$sdir/pmlog" || { echo "FAIL merge_ready_pr: best-effort merge not attempted"; rm -rf "$sdir"; exit 1; }
  rm -rf "$sdir"; echo "merge_ready_pr status-unconfirmed OK (best-effort merge; blocked->defer, open->merge)"
  # Task 3b (codex P2-B): a transient EMPTY mergeable lookup is retry-eligible (not stranded)
  local edir; edir=$(mktemp -d)
  cat >"$edir/gh" <<'SH'
#!/usr/bin/env bash
# every mergeable lookup returns EMPTY (simulated transient gh failure).
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  printf '%s\n' "$@" | grep -q 'headRefOid' && { echo "headsha1234567"; exit 0; }
  echo ""; exit 0
fi
exit 0
SH
  chmod +x "$edir/gh"
  ( PATH="$edir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    rc=$?
    [ $rc -eq 0 ] && [ "${MERGE_RETRY_ELIGIBLE:-x}" = 1 ] && case "$MERGE_NOTE" in "merge deferred: mergeable="*) true;; *) false;; esac ) \
    || { echo "FAIL merge_ready_pr: empty mergeable not retry-eligible"; rm -rf "$edir"; exit 1; }
  rm -rf "$edir"; echo "merge_ready_pr empty-mergeable OK (retry-eligible)"
  # codex round 7: a PINNED reviewed sha that no longer matches the head -> merge REFUSED (atomic)
  local mhdir; mhdir=$(mktemp -d)
  cat >"$mhdir/gh" <<'SH'
#!/usr/bin/env bash
# current head is headsha1234567; a --match-head-commit != that -> merge refused.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  printf '%s\n' "$@" | grep -q 'headRefOid'  && { echo headsha1234567; exit 0; }
  # A real sha: an empty one now HALTS (rc 2, main unwatched). These tests set
  # MAIN_CI_TIMEOUT low, so entering the watch costs a second or two.
  printf '%s\n' "$@" | grep -q 'mergeCommit' && { echo "mergesha7654321"; exit 0; }
  echo MERGEABLE; exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
  nx=""; mh=""; for a in "$@"; do [ "$nx" = 1 ] && { mh="$a"; nx=""; }; [ "$a" = "--match-head-commit" ] && nx=1; done
  [ -n "$mh" ] && [ "$mh" != headsha1234567 ] && exit 1
  echo "merge $3" >> "${PMLOG:-/dev/null}"; exit 0
fi
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q '/statuses/' && { printf 'success\n' >> "${STORE:-/dev/null}"; exit 0; }
  printf '%s\n' "$@" | grep -q '/status'    && { if printf '%s\n' "$@" | grep -q -- '-q'; then :; else printf '[{"statuses":[]}]'; exit 0; fi; cat "${STORE:-/dev/null}" 2>/dev/null; exit 0; }
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the
  # catch-all's bare "name|status|conclusion" lines will not do here.
  # The default is assigned to a variable, not written inline: `${VAR:-...}` ends at the
  # first `}` and this JSON is full of them, while escaping them inside a QUOTED heredoc
  # emits literal backslashes. Both produce invalid JSON, a permanently pending watch,
  # and a stall in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  # #70: branch protection. FAKE_PROT_RC=1 models an unreadable protection endpoint.
  printf '%s\n' "$@" | grep -q '/protection' && {
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    [ -n "${FAKE_PROT_CONTEXTS:-}" ] && { printf '%s\n' "$FAKE_PROT_CONTEXTS"; exit 0; }
    exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$mhdir/gh"; : > "$mhdir/pmlog"; : > "$mhdir/store"
  ( PATH="$mhdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 PMLOG="$mhdir/pmlog" STORE="$mhdir/store" \
      merge_ready_pr demo 7 OTHERSHA000 >/dev/null 2>&1
    # The NOTE changed when the pre-merge gate landed, and it had to: the gate
    # refuses a moved head WITHOUT calling gh, so "gh pr merge failed" would be
    # a false statement about what happened. The property this test exists for
    # -- a stale pinned head is never merged -- is asserted below and now holds
    # more strongly, because no merge is attempted at all.
    rc=$?; [ $rc -eq 0 ] && printf '%s' "$MERGE_NOTE" | grep -qE 'merge (deferred|refused)' ) \
    || { echo "FAIL merge_ready_pr: stale pinned head not refused (note=$MERGE_NOTE)"; rm -rf "$mhdir"; exit 1; }
  [ ! -s "$mhdir/pmlog" ] || { echo "FAIL merge_ready_pr: merged despite stale pinned head"; cat "$mhdir/pmlog"; rm -rf "$mhdir"; exit 1; }
  rm -rf "$mhdir"; echo "merge_ready_pr pinned-head-mismatch OK (atomic refuse)"
  # #66: an automatic merge with NO reviewed head must be refused outright. This
  # used to take an unpinned branch and merge anyway, so every caller had to
  # remember the precondition; --recover-pr was the one that did not.
  local updir; updir=$(mktemp -d)
  cat >"$updir/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$UNPIN_LOG"
[ "$2" = "view" ] && { echo MERGEABLE; exit 0; }
exit 0
SH
  chmod +x "$updir/gh"
  ( PATH="$updir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 UNPIN_LOG="$updir/log" \
      merge_ready_pr demo 7 >/dev/null 2>&1
    case "$MERGE_NOTE" in *"no reviewed head"*) : ;; *) exit 1 ;; esac ) \
    || { echo "FAIL merge_ready_pr: empty sha must be refused with an explanatory note"; rm -rf "$updir"; exit 1; }
  grep -q "pr merge" "$updir/log" 2>/dev/null \
    && { echo "FAIL merge_ready_pr: attempted a merge with no reviewed head"; cat "$updir/log"; rm -rf "$updir"; exit 1; }
  grep -q "statuses/" "$updir/log" 2>/dev/null \
    && { echo "FAIL merge_ready_pr: posted cross-review status with no reviewed head"; cat "$updir/log"; rm -rf "$updir"; exit 1; }
  rm -rf "$updir"; echo "merge_ready_pr unpinned-refused OK (#66)"
  # --- Task 4: _record_deferred_ready + reconcile_deferred_ready ----------------
  local rdir; rdir=$(mktemp -d)
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="ready but cross-review status post failed -> human merge" MERGE_RETRY_ELIGIBLE=1 \
    _record_deferred_ready itemA 11 0 77 headsha1234567
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="" MERGE_RETRY_ELIGIBLE=0 \
    _record_deferred_ready itemB 12 0
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="merge deferred: mergeable=CONFLICTING" MERGE_RETRY_ELIGIBLE=0 \
    _record_deferred_ready itemC 13 0
  # Five fields now: the fifth is BIRCHER_RUN_ID, empty here because the
  # self-test drives this outside a kernel run. The sweep falls back to
  # adopting by item code when it is empty, which is what a queue written
  # before this field existed also produces.
  [ "$(cat "$rdir/deferred.tsv")" = "$(printf 'itemA\t11\t77\theadsha1234567\t')" ] \
    || { echo "FAIL _record_deferred_ready: wrong contents"; cat "$rdir/deferred.tsv"; rm -rf "$rdir"; exit 1; }
  echo "_record_deferred_ready OK"
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
# stateful fake gh for the sweep: merge flips pr state OPEN->MERGED (MERGEDDIR/<pr>);
# MSSDIR/<pr> seeds mergeStateStatus (default CLEAN); STATEDIR/<pr> seeds state
# (default OPEN); HEADDIR/<pr> seeds headRefOid (default headsha1234567; an EMPTY
# file models a failed head lookup); STORE models the status post->read-back.
# For the #51 content-equality path: NEWHEADDIR/<pr> is the sha update-branch moves
# the head TO (and it flips mss to BLOCKED, as GitHub does); CMPDIR/<ref> holds the
# compare JSON for that ref (absent = a compare GitHub could not answer).
_pr(){ for a in "$@"; do case "$a" in [0-9]*) printf '%s' "$a"; return;; esac; done; }
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The gate asks for all four fields in ONE response. This fake is STATEFUL,
  # so it must answer from its OWN dirs -- handing back defaults would give the
  # gate a PR state contradicting the one the test seeded, and PR #8 (MERGED)
  # would sail through a gate that is supposed to refuse it.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    _p=$(_pr "$@")
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "$(cat "$STATEDIR/$_p" 2>/dev/null || echo OPEN)" \
      "${FAKE_MERGEABLE:-MERGEABLE}" \
      "$(if grep -q success "$STORE" 2>/dev/null; then echo CLEAN
          else cat "$MSSDIR/$_p" 2>/dev/null || echo CLEAN; fi)" \
      "$(cat "$HEADDIR/$_p" 2>/dev/null || echo headsha1234567)"
    exit 0
  fi
  p=$(_pr "$@")
  printf '%s\n' "$@" | grep -q 'mergeStateStatus' && { cat "$MSSDIR/$p" 2>/dev/null || echo CLEAN; exit 0; }   # contains 'state' -> match FIRST
  printf '%s\n' "$@" | grep -q 'baseRefName' && { echo main; exit 0; }
  printf '%s\n' "$@" | grep -q 'headRefOid'  && { cat "$HEADDIR/$p" 2>/dev/null || echo headsha1234567; exit 0; }
  if printf '%s\n' "$@" | grep -q 'state'; then
    { [ -n "$MERGEDDIR" ] && [ -f "$MERGEDDIR/$p" ]; } && { echo MERGED; exit 0; }
    cat "$STATEDIR/$p" 2>/dev/null || echo OPEN; exit 0
  fi
  # A real sha: an empty one now HALTS (rc 2, main unwatched). These tests set
  # MAIN_CI_TIMEOUT low, so entering the watch costs a second or two.
  printf '%s\n' "$@" | grep -q 'mergeCommit' && { echo "mergesha7654321"; exit 0; }
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
  nx=""; mh=""; for a in "$@"; do [ "$nx" = 1 ] && { mh="$a"; nx=""; }; [ "$a" = "--match-head-commit" ] && nx=1; done
  if [ -n "$mh" ]; then cur=$(cat "$HEADDIR/$3" 2>/dev/null || echo headsha1234567); [ "$mh" = "$cur" ] || exit 1; echo "matchhead $3" >> "$PMLOG"; fi
  echo "merge $3" >> "$PMLOG"; [ -n "$MERGEDDIR" ] && touch "$MERGEDDIR/$3"; exit 0
fi
[ "$1" = "issue" ] && [ "$2" = "view" ]  && { echo "${FAKE_ISSUE_STATE:-OPEN}"; exit 0; }
[ "$1" = "issue" ] && [ "$2" = "close" ] && { echo "close $3" >> "$PMLOG"; exit 0; }
if [ "$1" = "api" ]; then
  if printf '%s\n' "$@" | grep -q 'update-branch'; then
    up=$(printf '%s' "$*" | sed -n 's#.*/pulls/\([0-9][0-9]*\)/update-branch.*#\1#p')
    # GitHub REFUSES the update unless expected_head_sha matches the current head.
    # Model that, so omitting or mis-sending the field fails a test instead of
    # passing silently.
    ehs=$(printf '%s\n' "$@" | sed -n 's/^expected_head_sha=//p')
    cur=$(cat "$HEADDIR/$up" 2>/dev/null || echo headsha1234567)
    [ -n "$ehs" ]        || { printf 'update-branch-NOSHA %s\n' "$*" >> "$PMLOG"; exit 1; }
    [ "$ehs" = "$cur" ]  || { printf 'update-branch-REFUSED %s\n' "$*" >> "$PMLOG"; exit 1; }
    printf 'update-branch %s\n' "$*" >> "$PMLOG"
    # GitHub merges the base into the head and the PR stops being BEHIND.
    [ -n "$up" ] && [ -f "$NEWHEADDIR/$up" ] && { cp "$NEWHEADDIR/$up" "$HEADDIR/$up"; echo BLOCKED > "$MSSDIR/$up"; }
    exit 0
  fi
  # compare/<base>...<ref> -> the PR's own delta for <ref>; absent file = unanswerable
  cref=$(printf '%s' "$*" | sed -n 's#.*/compare/[^ ]*\.\.\.\([A-Za-z0-9._-]*\).*#\1#p')
  if [ -n "$cref" ]; then cat "$CMPDIR/$cref" 2>/dev/null || exit 1; exit 0; fi
  tref=$(printf '%s' "$*" | sed -n 's#.*/git/trees/\([A-Za-z0-9._-]*\).*#\1#p')
  if [ -n "$tref" ]; then cat "$TREEDIR/$tref" 2>/dev/null || exit 1; exit 0; fi
  # Posting the required status CLEARS the block, exactly as GitHub does: the
  # sweep's BLOCKED is "missing our status", so a fixture where it never clears
  # models a repository that could never merge at all. Without this the gate
  # correctly waits for a CLEAN the fake would never produce.
  printf '%s\n' "$@" | grep -q '/statuses/' && { printf 'success\n' >> "$STORE"; exit 0; }
  printf '%s\n' "$@" | grep -q '/status'    && { if printf '%s\n' "$@" | grep -q -- '-q'; then :; else printf '[{"statuses":[]}]'; exit 0; fi; cat "$STORE" 2>/dev/null; exit 0; }
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the
  # catch-all's bare "name|status|conclusion" lines will not do here.
  # The default is assigned to a variable, not written inline: `${VAR:-...}` ends at the
  # first `}` and this JSON is full of them, while escaping them inside a QUOTED heredoc
  # emits literal backslashes. Both produce invalid JSON, a permanently pending watch,
  # and a stall in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  # #70: branch protection. FAKE_PROT_RC=1 models an unreadable protection endpoint.
  printf '%s\n' "$@" | grep -q '/protection' && {
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    [ -n "${FAKE_PROT_CONTEXTS:-}" ] && { printf '%s\n' "$FAKE_PROT_CONTEXTS"; exit 0; }
    exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$rdir/gh"
  mkdir -p "$rdir/states" "$rdir/mss" "$rdir/head" "$rdir/merged" "$rdir/newhead" "$rdir/cmp" "$rdir/tree"
  echo OPEN > "$rdir/states/7"; echo MERGED > "$rdir/states/8"; echo BLOCKED > "$rdir/mss/7"
  # 4b: head-verified PR #7 (mss=BLOCKED = the NORMAL deferred state, missing our status)
  # merges (pinned) + its issue is closed; MERGED PR #8 skipped
  printf 'sweepA\t7\t77\theadsha1234567\nsweepB\t8\t\theadsha1234567\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_ISSUE_STATE=OPEN \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -qx 'merge 7' "$rdir/pmlog"            || { echo "FAIL sweep: verified PR #7 not merged"; rm -rf "$rdir"; exit 1; }
  grep -qx 'matchhead 7' "$rdir/pmlog"        || { echo "FAIL sweep: merge not pinned to reviewed head (--match-head-commit)"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q  'merge 8' "$rdir/pmlog"            && { echo "FAIL sweep: non-OPEN PR #8 was merged"; rm -rf "$rdir"; exit 1; }
  grep -q 'reconciliation sweep' "$rdir/scorecard.jsonl" || { echo "FAIL sweep: no merged scorecard row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  grep -qx 'close 77' "$rdir/pmlog"           || { echo "FAIL sweep: issue #77 not closed after sweep merge"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready merge+issue-close OK"
  # 4c: head-verified, mergeable=CONFLICTING -> escalate (NOT merged), sweep continues
  echo OPEN > "$rdir/states/9"
  printf 'sweepC\t9\t\theadsha1234567\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 FAKE_MERGEABLE=CONFLICTING \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 9' "$rdir/pmlog"                         && { echo "FAIL sweep-escalate: CONFLICTING PR #9 was merged"; rm -rf "$rdir"; exit 1; }
  grep -q 'sweep could not merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-escalate: no escalation scorecard row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready escalate OK"
  # --- #51 content-equality re-stamp on a BEHIND PR --------------------------------
  # A BEHIND PR must be update-branched. Whether it may then MERGE turns entirely on
  # whether the update left the PR's own delta untouched; the cases below are
  # the whole contract. `store` is asserted because the failure that matters is not
  # "did not merge" but "re-stamped bircher/cross-review on unreviewed code".
  _sweep_env() {
    PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      NEWHEADDIR="$rdir/newhead" CMPDIR="$rdir/cmp" TREEDIR="$rdir/tree" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1
  }
  # Fixtures mirror the REAL payload shapes: compare files[] carries
  # filename/status/sha/patch and NO mode or type; the tree carries mode/type and a
  # `truncated` flag. Both are needed because the digest spans both.
  cat > "$rdir/cmp/reviewedsha" <<'J'
{"files":[{"filename":"a.txt","status":"modified","sha":"blob0000000000000000000000000000000000b1","patch":"@@ -1 +1 @@\n-old\n+new"}]}
J
  cat > "$rdir/tree/reviewedsha" <<'J'
{"truncated":false,"tree":[{"path":"a.txt","mode":"100644","type":"blob","sha":"blob0000000000000000000000000000000000b1"}]}
J
  # 4d: the update CHANGED the PR's own delta (conflict resolution / fixup) ->
  # escalate, do NOT merge and do NOT re-stamp. This is the case that keeps the
  # pre-#51 guarantee intact.
  echo OPEN > "$rdir/states/10"; echo BEHIND > "$rdir/mss/10"
  echo reviewedsha > "$rdir/head/10"; echo rebased10 > "$rdir/newhead/10"
  cat > "$rdir/cmp/rebased10" <<'J'
{"files":[{"filename":"a.txt","status":"modified","sha":"blob0000000000000000000000000000000000b2","patch":"@@ -1 +1 @@\n-old\n+SOMETHING ELSE"}]}
J
  cat > "$rdir/tree/rebased10" <<'J'
{"truncated":false,"tree":[{"path":"a.txt","mode":"100644","type":"blob","sha":"blob0000000000000000000000000000000000b2"}]}
J
  printf 'sweepD\t10\t\treviewedsha\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( _sweep_env )
  grep -q 'pulls/10/update-branch' "$rdir/pmlog" || { echo "FAIL sweep-behind-changed: PR #10 not update-branched"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'merge 10' "$rdir/pmlog"               && { echo "FAIL sweep-behind-changed: PR #10 merged though its delta changed"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  [ -s "$rdir/store" ]                           && { echo "FAIL sweep-behind-changed: cross-review re-stamped on a CHANGED delta"; rm -rf "$rdir"; exit 1; }
  grep -q 'needs re-review before merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-behind-changed: no escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "sweep BEHIND + delta changed -> escalate OK (#51)"
  # 4d2: the update left the delta byte-identical -> re-stamp on the NEW head and
  # merge, pinned to that new head (never to the stale reviewed sha).
  echo OPEN > "$rdir/states/20"; echo BEHIND > "$rdir/mss/20"
  echo reviewedsha > "$rdir/head/20"; echo rebased20 > "$rdir/newhead/20"
  cp "$rdir/cmp/reviewedsha" "$rdir/cmp/rebased20"
  cp "$rdir/tree/reviewedsha" "$rdir/tree/rebased20"
  printf 'sweepE\t20\t\treviewedsha\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( _sweep_env )
  grep -q 'pulls/20/update-branch' "$rdir/pmlog" || { echo "FAIL sweep-behind-same: PR #20 not update-branched"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -qx 'merge 20' "$rdir/pmlog"              || { echo "FAIL sweep-behind-same: identical delta was NOT merged"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -qx 'matchhead 20' "$rdir/pmlog"          || { echo "FAIL sweep-behind-same: merge not pinned to the re-stamped head"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'expected_head_sha=reviewedsha' "$rdir/pmlog" || { echo "FAIL sweep-behind-same: update-branch sent without expected_head_sha"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  [ -s "$rdir/store" ]                           || { echo "FAIL sweep-behind-same: cross-review never posted on the new head"; rm -rf "$rdir"; exit 1; }
  echo "sweep BEHIND + delta identical -> re-stamp + merge OK (#51)"
  # 4d3: GitHub withheld a file's patch (binary / too large) -> the delta cannot be
  # PROVEN equal, so fail closed even though nothing looks wrong.
  echo OPEN > "$rdir/states/21"; echo BEHIND > "$rdir/mss/21"
  echo reviewedsha > "$rdir/head/21"; echo rebased21 > "$rdir/newhead/21"
  cat > "$rdir/cmp/rebased21" <<'J'
{"files":[{"filename":"a.txt","status":"modified","sha":"blob0000000000000000000000000000000000b1","patch":"@@ -1 +1 @@\n-old\n+new"},{"filename":"logo.png","status":"modified","sha":"blob0000000000000000000000000000000000c9"}]}
J
  cat > "$rdir/tree/rebased21" <<'J'
{"truncated":false,"tree":[{"path":"a.txt","mode":"100644","type":"blob","sha":"blob0000000000000000000000000000000000b1"},{"path":"logo.png","mode":"100644","type":"blob","sha":"blob0000000000000000000000000000000000c9"}]}
J
  printf 'sweepG\t21\t\treviewedsha\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( _sweep_env )
  grep -q 'merge 21' "$rdir/pmlog" && { echo "FAIL sweep-behind-withheld: merged on an unprovable delta"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  [ -s "$rdir/store" ]             && { echo "FAIL sweep-behind-withheld: re-stamped on an unprovable delta"; rm -rf "$rdir"; exit 1; }
  grep -q 'needs re-review before merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-behind-withheld: no escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "sweep BEHIND + patch withheld -> fail closed OK (#51)"
  # 4d4: the compare payload is byte-IDENTICAL to the reviewed one - same filename,
  # same status, same blob sha, same patch - but the base turned that path from a
  # regular file into a SYMLINK. Git stores a symlink's target as its blob content,
  # so the blob sha genuinely collides; only mode/type distinguishes them. Digesting
  # the compare alone would merge this as "unchanged". (codex review, 2026-08-14)
  echo OPEN > "$rdir/states/22"; echo BEHIND > "$rdir/mss/22"
  echo reviewedsha > "$rdir/head/22"; echo rebased22 > "$rdir/newhead/22"
  cp "$rdir/cmp/reviewedsha" "$rdir/cmp/rebased22"
  cat > "$rdir/tree/rebased22" <<'J'
{"truncated":false,"tree":[{"path":"a.txt","mode":"120000","type":"blob","sha":"blob0000000000000000000000000000000000b1"}]}
J
  printf 'sweepM\t22\t\treviewedsha\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( _sweep_env )
  grep -q 'merge 22' "$rdir/pmlog" && { echo "FAIL sweep-behind-mode: merged a file whose TYPE changed under an identical patch"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  [ -s "$rdir/store" ]             && { echo "FAIL sweep-behind-mode: re-stamped across a type change"; rm -rf "$rdir"; exit 1; }
  grep -q 'needs re-review before merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-behind-mode: no escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "sweep BEHIND + file TYPE changed -> fail closed OK (#51, codex)"
  # 4f (codex round 4): a PR whose head changed since review is escalated, NOT merged
  echo OPEN > "$rdir/states/12"
  printf 'sweepF\t12\t\tOLDSHA999\n' > "$rdir/deferred.tsv"   # recorded sha != current head (headsha1234567)
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 12' "$rdir/pmlog"                      && { echo "FAIL sweep-headchanged: PR #12 merged on an unreviewed head"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'head changed since review' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-headchanged: no head-changed escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready head-changed OK"
  # 4g (codex round 5): FAIL CLOSED when the reviewed head sha is unknown -> escalate, NOT merged
  echo OPEN > "$rdir/states/13"
  printf 'sweepG\t13\t\t\n' > "$rdir/deferred.tsv"   # recorded sha EMPTY -> cannot prove reviewed head
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 13' "$rdir/pmlog"                            && { echo "FAIL sweep-failclosed: PR #13 merged with unknown reviewed head"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'unverifiable reviewed head' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-failclosed: no fail-closed escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready fail-closed OK"
  # 4h (codex round 8): unverifiable mergeStateStatus -> fail closed (escalate, NOT merged)
  echo OPEN > "$rdir/states/14"; echo UNKNOWN > "$rdir/mss/14"
  printf 'sweepH\t14\t\theadsha1234567\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 14' "$rdir/pmlog"                                && { echo "FAIL sweep-mss-unknown: PR #14 merged on unverifiable mergeStateStatus"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'mergeStateStatus.*not safe to auto-merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-mss-unknown: no mss-unsafe escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready mss-unverifiable OK"
  # 4i (codex round 9): UNSTABLE (a check went red since the PASS) -> fail closed (escalate, NOT merged)
  echo OPEN > "$rdir/states/15"; echo UNSTABLE > "$rdir/mss/15"
  printf 'sweepI\t15\t\theadsha1234567\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" HEADDIR="$rdir/head" MERGEDDIR="$rdir/merged" \
      PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 15' "$rdir/pmlog"                                && { echo "FAIL sweep-mss-unstable: PR #15 merged on UNSTABLE mergeStateStatus"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -q 'mergeStateStatus.*not safe to auto-merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-mss-unstable: no mss-unsafe escalation row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready mss-unstable OK"
  rm -rf "$rdir"; echo "reconcile_deferred_ready OK"
  # --- --recover-pr: standalone adopt+review+merge of one orphaned PR ----------
  local prdir; prdir=$(mktemp -d)
  cat >"$prdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for recover_pr_cmd end-to-end (recovery review + merge_ready_pr).
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "$(if grep -q update-branch "${PR_LOG:-/dev/null}" 2>/dev/null; then echo CLEAN
          else echo "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}"; fi)" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}}"
    exit 0
  fi
  printf '%s\n' "$@" | grep -q 'mergeStateStatus' && { echo "${FAKE_MSS:-CLEAN}"; exit 0; }
  printf '%s\n' "$@" | grep -q 'headRefOid'       && { echo "headsha1234567"; exit 0; }
  # A real sha: an empty one now HALTS (rc 2, main unwatched), so this shim would fail
  # the recovery test for the wrong reason. The watch it enters is bounded by
  # MAIN_CI_TIMEOUT, which these tests set to a couple of seconds.
  printf '%s\n' "$@" | grep -q 'mergeCommit'      && { echo "mergesha7654321"; exit 0; }
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$1" = "pr" ] && [ "$2" = "comment" ] && { echo "https://x/pull/9#c1"; exit 0; }
[ "$1" = "pr" ] && [ "$2" = "list" ]    && { exit 0; }
[ "$1" = "pr" ] && [ "$2" = "merge" ]   && { echo "merge $3" >> "${PR_LOG:-/dev/null}"; exit 0; }
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q 'update-branch' && { echo "update-branch" >> "${PR_LOG:-/dev/null}"; exit 0; }
  # #66: recovery captures the reviewed head itself before dispatching the review.
  case "$*" in *"/pulls/"*) printf '%s' "${FAKE_HEAD_SHA-a502a88e20f959c908d00871ee7f25572512dd6d}"; exit 0 ;; esac
  if printf '%s\n' "$@" | grep -q '/statuses/'; then echo "status $*" >> "${PR_LOG:-/dev/null}"; printf 'success\n' >> "${STORE:-/dev/null}"; exit 0; fi
  printf '%s\n' "$@" | grep -q '/status' && { if printf '%s\n' "$@" | grep -q -- '-q'; then :; else printf '[{"statuses":[]}]'; exit 0; fi; cat "${STORE:-/dev/null}" 2>/dev/null; exit 0; }
  # _commit_ci_lines fetches this with --slurp and parses it as JSON, so the
  # catch-all's bare "name|status|conclusion" lines will not do here.
  # The default is assigned to a variable, not written inline: `${VAR:-...}` ends at the
  # first `}` and this JSON is full of them, while escaping them inside a QUOTED heredoc
  # emits literal backslashes. Both produce invalid JSON, a permanently pending watch,
  # and a stall in the rerun path's 300s delays.
  CR="${FAKE_CHECKRUNS:-}"
  [ -n "$CR" ] || CR='[{"check_runs":[{"name":"ci","status":"completed","conclusion":"success","app":{"id":15368}}]}]'
  printf '%s\n' "$@" | grep -q '/check-runs' && { printf '%s' "$CR"; exit 0; }
  # #70: branch protection. FAKE_PROT_RC=1 models an unreadable protection endpoint.
  printf '%s\n' "$@" | grep -q '/protection' && {
    [ "${FAKE_PROT_RC:-0}" = 0 ] || { echo "HTTP 500 something broke" >&2; exit 1; }
    [ -n "${FAKE_PROT_CONTEXTS:-}" ] && { printf '%s\n' "$FAKE_PROT_CONTEXTS"; exit 0; }
    exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  cat >"$prdir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review of the adopted PR.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$prdir/gh" "$prdir/omnigent"
  # up-to-date green PR: review PASS -> cross-review status + NON-admin merge; no update-branch
  ( PATH="$prdir:$PATH" REPO=demo/demo SERVER=http://x WORKDIR="$prdir" \
      MAIN_CI_TIMEOUT=31 PR_LOG="$prdir/log" STORE="$prdir/store" FAKE_MSS=CLEAN \
      recover_pr_cmd rdemo 9 codex >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] ) || { echo "FAIL recover_pr_cmd happy rc"; rm -rf "$prdir"; exit 1; }
  grep -q 'context=bircher/cross-review' "$prdir/log" || { echo "FAIL recover_pr_cmd: cross-review status not posted"; rm -rf "$prdir"; exit 1; }
  grep -qx 'merge 9' "$prdir/log" || { echo "FAIL recover_pr_cmd: PR not merged"; rm -rf "$prdir"; exit 1; }
  grep -q 'update-branch' "$prdir/log" && { echo "FAIL recover_pr_cmd: update-branch run for an up-to-date PR"; rm -rf "$prdir"; exit 1; }
  # #66: a recovery whose head cannot be captured as a full 40-hex sha must NOT
  # merge -- this was the one production path that merged unpinned. Abbreviated
  # and malformed values fail closed too, so the looser 7-40 hex marker contract
  # is not inherited by accident.
  for _bad in "" "a502a88" "not-a-sha" "a502a88e20f959c908d00871ee7f25572512dd6dEXTRA"; do
    : > "$prdir/log"; : > "$prdir/store"
    ( PATH="$prdir:$PATH" REPO=demo/demo SERVER=http://x BIRCHER_STATUS_BACKOFF=0 \
        MAIN_CI_TIMEOUT=31 PR_LOG="$prdir/log" STORE="$prdir/store" FAKE_MSS=CLEAN \
        FAKE_HEAD_SHA="$_bad" recover_pr_cmd rdemo 9 codex >/dev/null 2>&1 )
    grep -q 'merge 9' "$prdir/log" \
      && { echo "FAIL recover_pr_cmd: merged with an unusable head ('$_bad')"; cat "$prdir/log"; rm -rf "$prdir"; exit 1; }
    grep -q 'context=bircher/cross-review' "$prdir/log" \
      && { echo "FAIL recover_pr_cmd: stamped cross-review with an unusable head ('$_bad')"; rm -rf "$prdir"; exit 1; }
  done
  echo "recover_pr_cmd unusable-head refused OK (#66)"
  # #66: the prompt must pin the checkout to the CAPTURED sha. The first cut
  # passed the sha in and the function ignored it, still fetching the moving
  # `pull/N/head` -- so the reviewer could read a different commit than the one
  # the merge pinned. Assert the sha reaches the prompt text.
  local _pp
  _pp=$(REPO=demo/demo _recovery_review_prompt 9 a502a88e20f959c908d00871ee7f25572512dd6d)
  case "$_pp" in
    # The path now carries a per-review nonce so two reviewers cannot collide
    # on it (muesli #745). The PROPERTY is unchanged and is what this asserts:
    # the CAPTURED sha, not the moving pull/N/head, is what gets checked out.
    *"worktree add --detach /tmp/review-9-a502a88e-oob a502a88e20f959c908d00871ee7f25572512dd6d"*) : ;;
    *) echo "FAIL _recovery_review_prompt: checkout not pinned to the captured sha"; exit 1 ;;
  esac
  case "$_pp" in
    *"reviewing EXACTLY commit a502a88e20f959c908d00871ee7f25572512dd6d"*) : ;;
    *) echo "FAIL _recovery_review_prompt: prompt does not name the reviewed commit"; exit 1 ;;
  esac
  # With no sha it must still work (pre-#66 behaviour), rather than emitting an
  # empty checkout target.
  _pp=$(REPO=demo/demo _recovery_review_prompt 9)
  case "$_pp" in
    # With no sha the nonce falls back to `head`, and the checkout target is
    # still FETCH_HEAD -- which is what this asserts.
    *"worktree add --detach /tmp/review-9-FETCH_HE-oob FETCH_HEAD"*) : ;;
    *) echo "FAIL _recovery_review_prompt: no-sha fallback broken"; exit 1 ;;
  esac
  echo "_recovery_review_prompt pins the reviewed commit OK (#66)"
  # BEHIND PR: update-branch FIRST, then review + merge
  : > "$prdir/log"; : > "$prdir/store"
  ( PATH="$prdir:$PATH" REPO=demo/demo SERVER=http://x WORKDIR="$prdir" \
      MAIN_CI_TIMEOUT=31 PR_LOG="$prdir/log" STORE="$prdir/store" FAKE_MSS=BEHIND \
      recover_pr_cmd rdemo 9 codex >/dev/null 2>&1 )
  grep -q 'update-branch' "$prdir/log" || { echo "FAIL recover_pr_cmd: BEHIND did not update-branch"; rm -rf "$prdir"; exit 1; }
  grep -qx 'merge 9' "$prdir/log" || { echo "FAIL recover_pr_cmd: BEHIND path did not merge"; rm -rf "$prdir"; exit 1; }
  rm -rf "$prdir"
  echo "recover_pr_cmd (--recover-pr) OK"
  # --- _render_issue_item: pure queue-file renderer ---------------------------
  r=$(_render_issue_item 301 "People / attendees" $'## Summary\nDo the thing.\n## Verify\nno db tests')
  printf '%s\n' "$r" | grep -q '^Issue: #301$'            || { echo "FAIL render: Issue header"; exit 1; }
  printf '%s\n' "$r" | grep -q '^## Summary$'             || { echo "FAIL render: body copied"; exit 1; }
  printf '%s\n' "$r" | head -1 | grep -q '^# i301: People / attendees$' || { echo "FAIL render: title heading"; exit 1; }
  printf '%s\n' "$r" | grep -q '## Discussion' && { echo "FAIL render: discussion heading with no comments"; exit 1; }
  echo "_render_issue_item OK"
  # --- #46: issue comments reach the implementer ------------------------------
  # The defect this replaces was silent: guidance posted as a comment simply
  # never appeared in the queue item, and the run looked entirely healthy.
  local cj='[
    {"author":{"login":"abedegno"},"createdAt":"2026-08-10T08:00:00Z","body":"bircher: outcome=escalated ci_first=false review=claude_code:pass rounds=1 pr=#625"},
    {"author":{"login":"abedegno"},"createdAt":"2026-08-10T09:00:00Z","body":"Root cause is CORS, not CSP. Move the fetch to the request fixture."}
  ]'
  local cb; cb=$(_format_issue_comments "$cj")
  printf '%s\n' "$cb" | grep -q 'Root cause is CORS'  || { echo "FAIL #46: human comment dropped"; exit 1; }
  printf '%s\n' "$cb" | grep -q 'outcome=escalated'   && { echo "FAIL #46: bircher status comment fed back in"; exit 1; }
  printf '%s\n' "$cb" | grep -q '^### abedegno (2026-08-10T09:00:00Z)$' || { echo "FAIL #46: attribution heading"; exit 1; }
  # A comment that merely QUOTES a marker is a human talking, not a status post.
  local cq; cq=$(_format_issue_comments '[{"author":{"login":"jon"},"createdAt":"t","body":"the bircher: outcome=ready line was stale"}]')
  printf '%s\n' "$cq" | grep -q 'was stale' || { echo "FAIL #46: startswith must not swallow quoted markers"; exit 1; }
  # Only status comments -> no block at all, and no bare Discussion heading.
  [ -z "$(_format_issue_comments '[{"author":{"login":"a"},"createdAt":"t","body":"bircher: outcome=ready x"}]')" ] \
    || { echo "FAIL #46: status-only should render nothing"; exit 1; }
  [ -z "$(_format_issue_comments '')" ]      || { echo "FAIL #46: empty input"; exit 1; }
  [ -z "$(_format_issue_comments 'not json')" ] || { echo "FAIL #46: malformed json must not abort the run"; exit 1; }
  # Bounding is announced, never silent.
  local many; many=$(python3 -c 'import json;print(json.dumps([{"author":{"login":"a"},"createdAt":"t","body":"c%d"%i} for i in range(30)]))')
  local mb; mb=$(_format_issue_comments "$many" 5)
  printf '%s\n' "$mb" | grep -q '25 older comment(s) omitted' || { echo "FAIL #46: truncation must be stated"; exit 1; }
  printf '%s\n' "$mb" | grep -q 'c29' || { echo "FAIL #46: newest comment must survive"; exit 1; }
  local tb; tb=$(_format_issue_comments "$many" 30 200)
  printf '%s\n' "$tb" | grep -q 'truncated to the last 200 characters' || { echo "FAIL #46: char cap must be stated"; exit 1; }
  # Rendered into the item under a heading the implementer can find.
  local rc; rc=$(_render_issue_item 621 "t" "body" "$cb")
  printf '%s\n' "$rc" | grep -q '^## Discussion (oldest first)$' || { echo "FAIL #46: discussion heading"; exit 1; }
  printf '%s\n' "$rc" | grep -q 'Root cause is CORS' || { echo "FAIL #46: comments not rendered into the item"; exit 1; }
  echo "_format_issue_comments OK (#46)"
  # --- Task 4: _item_issue + _writeback_plan pure helpers ----------------------
  [ "$(_item_issue $'# i301: x\n\nIssue: #301\n\nbody')" = "301" ] || { echo "FAIL _item_issue read"; exit 1; }
  [ -z "$(_item_issue 'no issue header here')" ]                   || { echo "FAIL _item_issue absent"; exit 1; }
  [ "$(_writeback_plan ready)"     = "|bircher:running|done" ]     || { echo "FAIL wbplan ready"; exit 1; }
  [ "$(_writeback_plan escalated)" = "bircher:escalated|bircher:running|escalated" ] || { echo "FAIL wbplan esc"; exit 1; }
  [ "$(_writeback_plan failed)"    = "bircher:escalated|bircher:running|failed" ]    || { echo "FAIL wbplan failed"; exit 1; }
  echo "_item_issue + _writeback_plan OK"
  # --- #6 + #3: write-back comment shape + safety-net issue close --------------
  local wbdir; wbdir=$(mktemp -d)
  cat >"$wbdir/gh" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "issue" ] && [ "$2" = "comment" ]; then
  while [ $# -gt 0 ]; do [ "$1" = "--body" ] && { echo "$2" >> "${WB_LOG:-/dev/null}"; break; }; shift; done; exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # The pre-merge gate asks for all four fields in ONE response. Every fake a
  # merge path drives must answer it, or the gate reads a failed lookup
  # (correctly: never CLEAN) and polls until its budget expires.
  #
  # Matched on the EXACT field list, not on 'mergeStateStatus' alone: the sweep
  # already queries that field by itself, and a looser match shadowed it --
  # handing its bare-value comparison a whole JSON document, which it then
  # reported as a state unsafe to auto-merge.
  #
  # The defaults CHAIN into each fake's own variables (FAKE_MSS, FAKE_HEAD_SHA)
  # so a fixture stays self-consistent. Returning a constant head here made the
  # recovery fixture contradict itself -- it captures its reviewed head from
  # FAKE_HEAD_SHA -- and the gate correctly refused a PR whose head did not
  # match the head that was reviewed. The old code never noticed because it
  # never compared the two.
  if printf '%s\n' "$@" | grep -q 'state,mergeable,mergeStateStatus,headRefOid'; then
    printf '{"state":"%s","mergeable":"%s","mergeStateStatus":"%s","headRefOid":"%s"}\n' \
      "${FAKE_GATE_STATE:-OPEN}" "${FAKE_MERGEABLE:-MERGEABLE}" \
      "${FAKE_MERGE_STATE:-${FAKE_MSS:-CLEAN}}" \
      "${FAKE_GATE_HEAD:-${FAKE_HEAD_SHA:-headsha1234567}}"
    exit 0
  fi
  echo "${FAKE_PR_STATE:-MERGED}"; exit 0
fi
if [ "$1" = "issue" ] && [ "$2" = "view" ]; then echo "${FAKE_ISSUE_STATE:-OPEN}"; exit 0; fi
if [ "$1" = "issue" ] && [ "$2" = "close" ]; then echo "CLOSED $3" >> "${WB_CLOSE_LOG:-/dev/null}"; exit 0; fi
exit 0
SH
  chmod +x "$wbdir/gh"
  local wbc="$wbdir/comment.log"; : >"$wbc"
  ( PATH="$wbdir:$PATH" REPO=demo/demo WB_LOG="$wbc" _issue_writeback 42 noop "" "" "" "" )
  grep -qx 'bircher: outcome=noop' "$wbc" || { echo "FAIL #6 noop comment: [$(cat "$wbc")]"; exit 1; }
  : >"$wbc"
  ( PATH="$wbdir:$PATH" REPO=demo/demo WB_LOG="$wbc" _issue_writeback 42 ready 7 codex:pass 1 true )
  { grep -q 'outcome=ready' "$wbc" && grep -q 'review=codex:pass' "$wbc" && grep -q 'pr=#7' "$wbc"; } \
    || { echo "FAIL #6 ready comment: [$(cat "$wbc")]"; exit 1; }
  echo "_issue_writeback comment (#6) OK"
  local wbcl="$wbdir/close.log"; : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=MERGED FAKE_ISSUE_STATE=OPEN WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  grep -q 'CLOSED 42' "$wbcl" || { echo "FAIL #3: merged+open issue not closed"; exit 1; }
  : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=OPEN FAKE_ISSUE_STATE=OPEN WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  [ -s "$wbcl" ] && { echo "FAIL #3: closed issue for an unmerged PR"; exit 1; }
  : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=MERGED FAKE_ISSUE_STATE=CLOSED WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  [ -s "$wbcl" ] && { echo "FAIL #3: redundant close on already-closed issue"; exit 1; }
  rm -rf "$wbdir"
  echo "_ensure_issue_closed (#3) OK"
  # --- Task 2 (#346): _main_ci_verdict pure re-run/decision helper ------------
  [ "$(_main_ci_verdict green "")"     = continue ]    || { echo "FAIL verdict green"; exit 1; }
  [ "$(_main_ci_verdict red green)"    = continue ]    || { echo "FAIL verdict red,green"; exit 1; }
  [ "$(_main_ci_verdict red red)"      = revert-halt ] || { echo "FAIL verdict red,red"; exit 1; }
  # CHANGED deliberately (#52). This used to assert revert-halt, i.e. revert when a
  # re-run never settled. That is reverting on ignorance: on 2026-08-12 a provider
  # outage produced exactly this shape and destroyed reviewed work. An empty second
  # state still reverts -- that means no re-run was ATTEMPTED, so the first red stands.
  [ "$(_main_ci_verdict red pending)"  = halt ]        || { echo "FAIL verdict red,pending must NOT revert (no verdict)"; exit 1; }
  [ "$(_main_ci_verdict red unknown)"  = halt ]        || { echo "FAIL verdict red,unknown must NOT revert (no verdict)"; exit 1; }
  [ "$(_main_ci_verdict red "")"       = revert-halt ] || { echo "FAIL verdict red,'' (re-run disabled) still reverts"; exit 1; }
  [ "$(_main_ci_verdict pending green)" = continue ]   || { echo "FAIL verdict pending,green"; exit 1; }
  [ "$(_main_ci_verdict pending red)"  = halt ]        || { echo "FAIL verdict pending,red"; exit 1; }
  [ "$(_main_ci_verdict pending pending)" = halt ]     || { echo "FAIL verdict pending,pending"; exit 1; }
  echo "_main_ci_verdict OK"
  # --- #62: the shared main-CI wall clock --------------------------------------
  # The bug this bounds: muesli's a80a55b2 ran CI for 2867s against a 900s watcher,
  # so the watch timed out `pending`, the re-run gave no verdict, and the wave halted
  # with main healthy. The settle budget now covers that; the absolute deadline stops
  # the settle budget and three re-run budgets from multiplying into hours.
  [ "$_DEF_SETTLE" -gt 2867 ] \
    || { echo "FAIL #62: the settle budget must cover the observed 2867s main-CI run (default=$_DEF_SETTLE)"; exit 1; }
  [ "$_DEF_RERUN" = 900 ] \
    || { echo "FAIL #62: the RE-RUN budget must stay 900s (raising it multiplies by the re-run count)"; exit 1; }
  [ "$_DEF_ABS" -ge "$_DEF_SETTLE" ] \
    || { echo "FAIL #62: the absolute deadline must not be shorter than the settle budget"; exit 1; }
  # The initial watch must NOT be bounded by the re-run budget -- that conflation is what
  # made a per-test MAIN_CI_TIMEOUT silently stop bounding it.
  [ "$_DEF_SETTLE" != "$_DEF_RERUN" ] \
    || { echo "FAIL #62: the settle and re-run budgets must be distinct"; exit 1; }
  ( unset MAIN_CI_DEADLINE_AT; _past_ci_deadline ) \
    && { echo "FAIL #62: an UNARMED deadline must never fire"; exit 1; }
  ( MAIN_CI_DEADLINE_AT=$(( $(date +%s) + 300 )); _past_ci_deadline ) \
    && { echo "FAIL #62: a future deadline must not fire"; exit 1; }
  ( MAIN_CI_DEADLINE_AT=$(( $(date +%s) - 1 )); _past_ci_deadline ) \
    || { echo "FAIL #62: a passed deadline MUST fire"; exit 1; }
  # An unusable value reads as "not past": the per-loop budgets still bound every
  # caller, so failing open cannot run forever, while failing closed on a garbled
  # value would abandon a healthy watch.
  #
  # ASSERT ON STDERR TOO. bash ERRORS rather than returning false on an oversized
  # `-ge` operand (#61b), and an error is also non-zero -- so an rc-only assertion
  # passes whether the guard rejected the value deliberately or bash blew up on it.
  # That is the same unfalsifiable-assertion trap as the #67 test shims: the earlier
  # version of this loop was caught by its `-5` case alone and would have missed the
  # guard's removal entirely for every other value.
  for _bad in "" "abc" "12a" "99999999999999999999999999" "-5" " 123" "1e9"; do
    _err=$( { MAIN_CI_DEADLINE_AT="$_bad"; _past_ci_deadline; } 2>&1 ); _rc=$?
    [ "$_rc" -ne 0 ] \
      || { echo "FAIL #62: unusable deadline '$_bad' must read as NOT past"; exit 1; }
    [ -z "$_err" ] \
      || { echo "FAIL #62: '$_bad' was REJECTED BY BASH, not by the guard: $_err"; exit 1; }
  done
  # An all-digit but absurd value is the one that proves the magnitude clamp rather
  # than the digits check: it passes `*[!0-9]*` and only the length test stops it.
  _err=$( { MAIN_CI_DEADLINE_AT=99999999999999999999; _past_ci_deadline; } 2>&1 ); _rc=$?
  { [ "$_rc" -ne 0 ] && [ -z "$_err" ]; } \
    || { echo "FAIL #62: an oversized ALL-DIGIT deadline must be clamped, not compared (rc=$_rc err=$_err)"; exit 1; }
  # Arming validates its inputs. The assertion has to bound the armed value from ABOVE
  # as well as below: bash's overflow on `now + 99999999999999999999` wraps to a
  # POSITIVE 19-digit number, so "later than now" is satisfied while the deadline sits
  # ~246 billion years out and `_past_ci_deadline`'s length clamp then rejects it as
  # unusable -- silently disabling the bound the operator was trying to set. An
  # assertion that only checked `> now` passed straight through that.
  _sane_arm() { # <label>; asserts the armed instant is in (now, now + max span]
    local now armed; now=$(date +%s)
    armed="${MAIN_CI_DEADLINE_AT:-}"
    case "$armed" in ''|*[!0-9]*) echo "FAIL #62: $1 left the deadline unusable ('$armed')"; exit 1 ;; esac
    [ "${#armed}" -le 12 ] \
      || { echo "FAIL #62: $1 armed an out-of-range deadline ($armed) -- _past_ci_deadline would reject it and fail OPEN"; exit 1; }
    [ "$armed" -gt "$now" ] \
      || { echo "FAIL #62: $1 armed a deadline in the past ($armed <= $now)"; exit 1; }
    [ "$armed" -le "$((now + 9999999))" ] \
      || { echo "FAIL #62: $1 armed a deadline beyond the maximum span ($armed)"; exit 1; }
    # Also bound from BELOW by the minimum span: a 1-second deadline would expire
    # before the first poll and halt healthy merges, so an absurdly small override
    # must fall back rather than be honoured.
    [ "$armed" -ge "$((now + 60))" ] \
      || { echo "FAIL #62: $1 armed a deadline under the minimum span ($armed vs $now)"; exit 1; }
  }
  ( MAIN_CI_ABSOLUTE_DEADLINE=99999999999999999999 _arm_ci_deadline 2>/dev/null
    _sane_arm "an oversized span" )                        || exit 1
  ( MAIN_CI_ABSOLUTE_DEADLINE=abc _arm_ci_deadline 2>/dev/null
    _sane_arm "a non-numeric span" ) \
    || { echo "FAIL #62: a non-numeric span must fall back, not abort (\$((10#abc)) is FATAL, not a non-zero return)"; exit 1; }
  ( MAIN_CI_ABSOLUTE_DEADLINE=1 _arm_ci_deadline 2>/dev/null
    _sane_arm "an absurdly small span" )                   || exit 1
  # An 8-digit span (~3.2 years) is the case that isolates the LENGTH clamp: it is
  # small enough that `-ge 60` succeeds normally, so only the length test rejects it.
  # The 20-digit case above is caught either way, because bash ERRORS on an oversized
  # `-ge` operand -- but zsh truncates and compares instead (#61b), so a clamp that
  # only works by relying on that error is not a clamp.
  ( MAIN_CI_ABSOLUTE_DEADLINE=99999999 _arm_ci_deadline 2>/dev/null
    _sane_arm "an 8-digit span" )                          || exit 1
  # LEADING ZEROS ARE OCTAL to `$(( ))` but decimal to `[ -ge ]`, so the two disagreed
  # about what the string meant: 0000060 armed 48s (under the very minimum the check
  # exists to enforce), 00007200 armed 3712s, and 0000080 was an arithmetic ERROR that
  # left the deadline unusable. All three verified against the shipped code first.
  ( MAIN_CI_ABSOLUTE_DEADLINE=0000060  _arm_ci_deadline 2>/dev/null
    _sane_arm "a leading-zero minimum" )                   || exit 1
  ( MAIN_CI_ABSOLUTE_DEADLINE=0000080  _arm_ci_deadline 2>/dev/null
    _sane_arm "a leading-zero 8" )                         || exit 1
  # In range is not enough -- it must mean what it says in DECIMAL. 00007200 is 7200
  # seconds, not 3712, and _sane_arm's window is wide enough to accept both.
  ( MAIN_CI_ABSOLUTE_DEADLINE=00007200 _arm_ci_deadline 2>/dev/null
    _sane_arm "a leading-zero default"
    _n=$(date +%s); [ "$(( MAIN_CI_DEADLINE_AT - _n ))" -ge 7000 ] ) \
    || { echo "FAIL #62: a leading-zero span must be read as DECIMAL (00007200 = 7200s, not 3712)"; exit 1; }
  ( MAIN_CI_ABSOLUTE_DEADLINE=7200 _arm_ci_deadline 2>/dev/null
    _sane_arm "the default span"
    _past_ci_deadline ) \
    && { echo "FAIL #62: a freshly armed deadline must not already be past"; exit 1; }
  # A DEAD CLOCK must leave the deadline unarmed, never arm it near epoch 0 -- which
  # would expire instantly and halt a healthy merge before its watch began.
  local _ddir; _ddir=$(mktemp -d)
  printf '#!/usr/bin/env bash\nexit 1\n' > "$_ddir/date"; chmod +x "$_ddir/date"
  ( PATH="$_ddir:$PATH" MAIN_CI_DEADLINE_AT=  _arm_ci_deadline 2>/dev/null
    [ -z "${MAIN_CI_DEADLINE_AT:-}" ] ) \
    || { echo "FAIL #62: a dead clock must leave the deadline UNARMED, not armed near epoch 0"; rm -rf "$_ddir"; exit 1; }
  rm -rf "$_ddir"; unset -f _sane_arm
  # An EXPIRED deadline must stop _rerun_main_ci BEFORE it dispatches. That path costs
  # a `gh run list`, a `gh run rerun` and a 20s startup sleep before it reaches its own
  # poll loop, so a check only at the loop would let all of it run past the deadline.
  # Asserting on the gh log, not just the return value: `unknown` is also what a failed
  # lookup returns, so a rc-only assertion could not tell the guard from a broken shim.
  local _rdir; _rdir=$(mktemp -d)
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$GH_CALLS"\nexit 1\n' > "$_rdir/gh"
  chmod +x "$_rdir/gh"; : > "$_rdir/calls"
  _rr=$( PATH="$_rdir:$PATH" REPO=demo/demo GH_CALLS="$_rdir/calls" \
         MAIN_CI_DEADLINE_AT=$(( $(date +%s) - 1 )) _rerun_main_ci deadsha 2>/dev/null )
  [ "$_rr" = unknown ] \
    || { echo "FAIL #62: a re-run past the deadline must report unknown (got '$_rr')"; rm -rf "$_rdir"; exit 1; }
  [ ! -s "$_rdir/calls" ] \
    || { echo "FAIL #62: a re-run past the deadline DISPATCHED anyway: $(cat "$_rdir/calls")"; rm -rf "$_rdir"; exit 1; }
  # ...and with the deadline in the future it must still reach gh, or the assertion
  # above would pass for a function that never does anything.
  : > "$_rdir/calls"
  ( PATH="$_rdir:$PATH" REPO=demo/demo GH_CALLS="$_rdir/calls" \
    MAIN_CI_DEADLINE_AT=$(( $(date +%s) + 600 )) _rerun_main_ci livesha >/dev/null 2>&1 )
  [ -s "$_rdir/calls" ] \
    || { echo "FAIL #62: with time remaining, the re-run must still reach gh"; rm -rf "$_rdir"; exit 1; }
  rm -rf "$_rdir"
  # --- #62: main-CI gh calls are bounded by a wall clock ------------------------
  # A `gh` call that HANGS rather than failing was never interrupted, so the initial
  # watch or a re-run poll could block indefinitely and a broken main would stay
  # unreverted with no halt ever reached.
  #
  # Tested through a FAKE `timeout` that records its arguments, rather than by sleeping
  # under the real one: that makes the wiring assertion exact and keeps the suite
  # runnable on stock macOS, where coreutils' timeout is absent. The runner is Linux
  # and always takes the bounded path.
  local _tdir; _tdir=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'printf "%s\n" "$*" >> "$TO_ARGS"' \
    'if [ "$1" = "-k" ]; then shift 2; fi' \
    'printf "%s\n" "$1" >> "$TO_LOG"' \
    'shift' \
    'exec "$@"' > "$_tdir/timeout"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$_tdir/gh"
  chmod +x "$_tdir/timeout" "$_tdir/gh"; : > "$_tdir/caps"; : > "$_tdir/args"
  : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" TO_ARGS="$_tdir/args" MAIN_CI_DEADLINE_AT= _ci_gh api x >/dev/null 2>&1 )
  # A TERM-only timeout is not a bound: a child that ignores SIGTERM leaves `timeout`
  # waiting forever, which is exactly the hung transport this wraps against.
  grep -q -- '-k' "$_tdir/args" \
    || { echo "FAIL #62: _ci_gh must pass -k (TERM alone does not bound a child that ignores it)"; rm -rf "$_tdir"; exit 1; }
  [ "$(cat "$_tdir/caps")" = 120 ] \
    || { echo "FAIL #62: an unarmed deadline must cap a gh call at the 120s default (got '$(cat "$_tdir/caps")')"; rm -rf "$_tdir"; exit 1; }
  # With less than the default left on the deadline, the REMAINDER is the cap -- a
  # single call must never be allowed to outlive the bound it is supposed to respect.
  : > "$_tdir/caps"; : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" MAIN_CI_DEADLINE_AT=$(( $(date +%s) + 30 )) _ci_gh api x >/dev/null 2>&1 )
  _cap=$(cat "$_tdir/caps")
  { [ "$_cap" -ge 25 ] && [ "$_cap" -le 30 ]; } \
    || { echo "FAIL #62: the cap must shrink to the deadline remainder (got '$_cap', expected ~30)"; rm -rf "$_tdir"; exit 1; }
  # Already past the deadline: still a positive cap, never 0 or negative, which
  # `timeout` would read as "no limit at all".
  : > "$_tdir/caps"; : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" MAIN_CI_DEADLINE_AT=$(( $(date +%s) - 500 )) _ci_gh api x >/dev/null 2>&1 )
  [ "$(cat "$_tdir/caps")" -ge 1 ] \
    || { echo "FAIL #62: a passed deadline must still yield a POSITIVE cap, not 0 (which timeout reads as unlimited)"; rm -rf "$_tdir"; exit 1; }
  # An unusable BIRCHER_CI_CALL_TIMEOUT must fall back, not disable the cap. Leading
  # zeros here are the same octal trap as the deadline span.
  for _bad in "" "abc" "0" "3" "0000060"; do
    : > "$_tdir/caps"; : > "$_tdir/args"
    ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" BIRCHER_CI_CALL_TIMEOUT="$_bad" \
      MAIN_CI_DEADLINE_AT= _ci_gh api x >/dev/null 2>&1 )
    _cap=$(cat "$_tdir/caps")
    { [ -n "$_cap" ] && [ "$_cap" -ge 5 ]; } 2>/dev/null \
      || { echo "FAIL #62: unusable BIRCHER_CI_CALL_TIMEOUT '$_bad' must fall back to a sane cap (got '$_cap')"; rm -rf "$_tdir"; exit 1; }
    # CANONICAL, not merely in range: the cap is handed to `timeout` verbatim, so
    # "0000060" must have become "60". Asserting only ">= 5" cannot see the difference
    # and left the base-10 forcing unfalsifiable.
    case "$_cap" in 0?*) echo "FAIL #62: cap '$_cap' from '$_bad' is not canonical decimal"; rm -rf "$_tdir"; exit 1 ;; esac
  done
  # ...and a leading-zero cap must mean its DECIMAL value.
  : > "$_tdir/caps"; : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" BIRCHER_CI_CALL_TIMEOUT=0000060 \
    MAIN_CI_DEADLINE_AT= _ci_gh api x >/dev/null 2>&1 )
  [ "$(cat "$_tdir/caps")" = 60 ] \
    || { echo "FAIL #62: BIRCHER_CI_CALL_TIMEOUT=0000060 must yield a cap of 60 (got '$(cat "$_tdir/caps")')"; rm -rf "$_tdir"; exit 1; }
  # The branch-protection lookup is easy to overlook because it is not "CI" -- but it
  # runs inside the post-merge watch, so a hang there outlives the absolute deadline
  # exactly as a hung check-runs fetch would.
  : > "$_tdir/caps"; : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" REPO=demo/demo \
    _REQUIRED_CONTEXTS_LOADED= _REQUIRED_CONTEXTS_CACHE= MAIN_CI_DEADLINE_AT= \
    _required_contexts >/dev/null 2>&1 )
  [ -s "$_tdir/caps" ] \
    || { echo "FAIL #62: _required_contexts must go through the bounded wrapper"; rm -rf "$_tdir"; exit 1; }
  # ORDER MATTERS, and it is not observable from behaviour alone: the deadline has to be
  # armed BEFORE the first post-merge GitHub lookup. Armed after, a hang in the
  # merge-sha or branch-protection call would run unbounded AND leave the deadline
  # unset, so nothing downstream could ever time it out. Asserted structurally because
  # the alternative is driving a full merge with a blocking shim.
  _body=$(declare -f merge_ready_pr); _body_rc=$?
  # No `| head -1`: head closes the pipe early and SIGPIPEs grep, the same pipefail
  # hazard as `grep -q`. Take the first line with parameter expansion instead.
  _arm_ln=$(printf '%s\n' "$_body" | grep -n '_arm_ci_deadline' | cut -d: -f1); _arm_ln=${_arm_ln%%$'\n'*}
  _view_ln=$(printf '%s\n' "$_body" | grep -n 'pr view .*mergeCommit' | cut -d: -f1); _view_ln=${_view_ln%%$'\n'*}
  { [ -n "$_arm_ln" ] && [ -n "$_view_ln" ] && [ "$_arm_ln" -lt "$_view_ln" ]; } \
    || { echo "FAIL #62: _arm_ci_deadline must precede the merge-sha lookup (arm=$_arm_ln view=$_view_ln)"; rm -rf "$_tdir"; exit 1; }
  _contains "$_body" 'sha=$(_ci_gh pr view' \
    || { echo "FAIL #62: the merge-sha lookup must be bounded"; rm -rf "$_tdir"; exit 1; }
  # NO LEAK ACROSS ITEMS. The deadline used to be an exported global that nothing
  # cleared, so an EXPIRED one from a previous merge shrank the next item's
  # `_required_contexts` lookup to a one-second cap -- which failed, made the required
  # set unknown, and let `_keep_blocking_checks` fall back to every check. It is now a
  # local in merge_ready_pr, which bash's dynamic scoping still shows to every helper
  # called from there (including inside a command substitution) while it vanishes on
  # every return path with no cleanup to forget.
  # Diagnostics on failure: this assertion failed ONCE on the Linux runner and then
  # passed four consecutive runs, with the source and `declare -f` rendering verified
  # identical on both platforms. Unexplained, so a recurrence must say what it actually
  # saw rather than only that it did not match.
  _contains "$_body" 'local MAIN_CI_DEADLINE_AT' \
    || { echo "FAIL #62: MAIN_CI_DEADLINE_AT must be LOCAL to merge_ready_pr, or it leaks into the next item"
         echo "  diag: _body is ${#_body} chars; declare -f rc was ${_body_rc:-?}; bash ${BASH_VERSION}"
         printf '  diag: first 3 lines of _body: %s\n' "$(printf '%s\n' "$_body" | head -3 | tr '\n' '~')"
         rm -rf "$_tdir"; exit 1; }
  _contains "$(declare -f _arm_ci_deadline)" 'export MAIN_CI_DEADLINE_AT' \
    && { echo "FAIL #62: _arm_ci_deadline must not EXPORT the deadline (that is the leak)"; rm -rf "$_tdir"; exit 1; }
  # Prove the scoping actually works both ways: visible to a helper called through a
  # command substitution, and gone once the arming frame returns.
  _scope_probe() { local MAIN_CI_DEADLINE_AT=""; _arm_ci_deadline 2>/dev/null; echo "$( _ci_call_cap )"; }
  _inner_cap=$(_scope_probe)
  { [ -n "$_inner_cap" ] && [ "$_inner_cap" -ge 5 ]; } 2>/dev/null \
    || { echo "FAIL #62: the armed deadline must reach helpers called via \$( ) (got '$_inner_cap')"; rm -rf "$_tdir"; exit 1; }
  [ -z "${MAIN_CI_DEADLINE_AT:-}" ] \
    || { echo "FAIL #62: the deadline survived the frame that armed it -> it will shorten the next item"; rm -rf "$_tdir"; exit 1; }
  # DEAD CLOCK WITH A STALE OUTER VALUE. `unset` on a dynamically scoped local HIDES
  # it and re-exposes an outer variable of the same name -- so an expired deadline left
  # in the environment would spring back, cap every later call at one second, and halt
  # a successfully merged PR as unresolved. The scope test above starts with no outer
  # value and so cannot see this; this one deliberately plants one.
  _dead_probe() { local MAIN_CI_DEADLINE_AT=""; _arm_ci_deadline 2>/dev/null; printf '%s' "${MAIN_CI_DEADLINE_AT:-UNARMED}"; }
  local _ddir2; _ddir2=$(mktemp -d)
  printf '#!/usr/bin/env bash\nexit 1\n' > "$_ddir2/date"; chmod +x "$_ddir2/date"
  _res=$( MAIN_CI_DEADLINE_AT=$(( $(date +%s) - 9999 )); export MAIN_CI_DEADLINE_AT
          PATH="$_ddir2:$PATH" _dead_probe )
  [ "$_res" = UNARMED ] \
    || { echo "FAIL #62: a dead clock re-exposed a stale outer deadline ('$_res') instead of leaving this merge unarmed"; rm -rf "$_ddir2"; exit 1; }
  rm -rf "$_ddir2"; unset -f _dead_probe
  unset -f _scope_probe
  # RECOVERY IS BOUNDED SEPARATELY. It runs after the CI deadline has typically expired
  # and must still fetch, revert and push -- capping it at the CI remainder would give
  # it one second and guarantee failure. `git fetch`, `git worktree add` against a
  # remote-tracking ref and `git push` are all network operations; calling them "not
  # network-facing" was simply wrong.
  _contains "$_body" '_net_run "$BIRCHER_NET_TIMEOUT" git fetch origin' \
    || { echo "FAIL #62: the recovery git fetch must be bounded"; rm -rf "$_tdir"; exit 1; }
  # v2: the push is ROUTED through the effect adapter, which takes the cap as its
  # third argument and hands it to _net_run. The bound is unchanged; the shape is
  # not, so this asserts the PROPERTY (routed, with a real cap) rather than the v1
  # spelling. A cap of `-` means unbounded and must not satisfy it.
  _contains "$_body" '_effect ref_update "revert-push:$pr" "$BIRCHER_NET_TIMEOUT" git push origin' \
    || { echo "FAIL #62: the recovery git push must be routed AND bounded"; rm -rf "$_tdir"; exit 1; }
  # EVERY gh invocation in there, not just one: an assertion that merely finds
  # `_net_run` somewhere passes while a second call sits unbounded next to it.
  # `grep -v 'echo '` because `declare -f` re-emits diagnostic strings on their own
  # lines, and one of them contains the literal "gh rc=$rc" -- a message about gh, not
  # a call to it. Matching that was a false positive that failed the whole suite.
  # v2: a call is bounded if it goes through _net_run directly, OR through
  # _effect with a cap that is not `-`. The cap is _effect's third argument, so
  # the pattern reads class, key, then a cap whose first character is not a bare
  # dash -- an unbounded `-` cap deliberately still counts as unbounded here.
  _unbounded=$(declare -f _reopen_reverted_issues | grep -E '(^|[^_[:alnum:]])gh ' \
                 | grep -v '_net_run' \
                 | grep -vE '_effect +[a-z_]+ +[^ ]+ +[^-]' \
                 | grep -v 'echo ' || true)
  [ -z "${_unbounded//[[:space:]]/}" ] \
    || { echo "FAIL #62: unbounded gh call in _reopen_reverted_issues: $_unbounded"; rm -rf "$_tdir"; exit 1; }
  # _net_run's cap is INDEPENDENT of the CI deadline: an expired one must not shrink it.
  : > "$_tdir/caps"; : > "$_tdir/args"
  ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" MAIN_CI_DEADLINE_AT=$(( $(date +%s) - 9999 )) \
    _net_run "$BIRCHER_NET_TIMEOUT" gh x >/dev/null 2>&1 )
  [ "$(cat "$_tdir/caps")" = "$BIRCHER_NET_TIMEOUT" ] \
    || { echo "FAIL #62: _net_run must ignore an expired CI deadline (got '$(cat "$_tdir/caps")')"; rm -rf "$_tdir"; exit 1; }
  grep -q -- '-k' "$_tdir/args" \
    || { echo "FAIL #62: _net_run must pass -k -- a git push that ignores SIGTERM would block recovery forever"; rm -rf "$_tdir"; exit 1; }
  for _bad in "" "abc" "0" "0000060"; do
    : > "$_tdir/caps"; : > "$_tdir/args"
    ( PATH="$_tdir:$PATH" TO_LOG="$_tdir/caps" TO_ARGS="$_tdir/args" _net_run "$_bad" gh x >/dev/null 2>&1 )
    _cap=$(cat "$_tdir/caps")
    { [ -n "$_cap" ] && [ "$_cap" -ge 5 ]; } 2>/dev/null \
      || { echo "FAIL #62: _net_run cap '$_bad' must fall back to a sane value (got '$_cap')"; rm -rf "$_tdir"; exit 1; }
    case "$_cap" in 0?*) echo "FAIL #62: _net_run cap '$_cap' is not canonical decimal"; rm -rf "$_tdir"; exit 1 ;; esac
  done
  # NO timeout(1) -> REFUSE, never run unbounded. Falling through was the whole
  # guarantee silently discarded on exactly the machines least likely to notice.
  #
  # Resolution and execution are tested SEPARATELY on purpose. `_timeout_bin` uses only
  # the `command -v` builtin, so it can be probed with a PATH containing nothing else;
  # the wrappers need a real PATH to exec through, so their refusal is driven by forcing
  # the cache instead. An earlier cut conflated the two and stripped PATH so hard that
  # `#!/usr/bin/env bash` could not find bash.
  local _edir; _edir=$(mktemp -d); : > "$_edir/ran"
  printf '#!/usr/bin/env bash\necho ran >> "$RAN_LOG"\nexit 0\n' > "$_edir/gh"; chmod +x "$_edir/gh"
  [ -z "$( PATH="$_edir"; _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= _timeout_bin )" ] \
    || { echo "FAIL #62: _timeout_bin must find nothing when neither timeout nor gtimeout exists"; rm -rf "$_edir"; exit 1; }
  ( PATH="$_edir"; _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= _timeout_bin >/dev/null ) \
    && { echo "FAIL #62: _timeout_bin must return non-zero when there is no timeout(1)"; rm -rf "$_edir"; exit 1; }
  # gtimeout is GNU coreutils' name on macOS: a dev box with coreutils must resolve,
  # not be refused.
  printf '#!/usr/bin/env bash\nexit 0\n' > "$_edir/gtimeout"; chmod +x "$_edir/gtimeout"
  [ "$( PATH="$_edir"; _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= _timeout_bin )" = "$_edir/gtimeout" ] \
    || { echo "FAIL #62: _timeout_bin must accept gtimeout (GNU coreutils on macOS)"; rm -rf "$_edir"; exit 1; }
  # The wrappers refuse, and do NOT execute, when resolution comes back empty.
  ( RAN_LOG="$_edir/ran"; export RAN_LOG
    _TIMEOUT_BIN_LOADED=1 _TIMEOUT_BIN_CACHE= _ci_gh api x >/dev/null 2>&1 ) \
    && { echo "FAIL #62: _ci_gh must FAIL when no timeout(1) exists, not run unbounded"; rm -rf "$_edir"; exit 1; }
  ( RAN_LOG="$_edir/ran"; export RAN_LOG
    _TIMEOUT_BIN_LOADED=1 _TIMEOUT_BIN_CACHE= _net_run 60 "$_edir/gh" >/dev/null 2>&1 ) \
    && { echo "FAIL #62: _net_run must FAIL when no timeout(1) exists, not run unbounded"; rm -rf "$_edir"; exit 1; }
  [ ! -s "$_edir/ran" ] \
    || { echo "FAIL #62: a wrapper RAN the command with no timeout(1) available"; rm -rf "$_edir"; exit 1; }
  rm -rf "$_edir"
  # BEHAVIOURAL:  # BEHAVIOURAL: a child that IGNORES SIGTERM must still be killed, within cap+grace.
  # This is the one thing the argument-recording shims cannot prove, and it needs a
  # REAL timeout -- so it runs only where one exists (always, on the Linux runner).
  # Gate on a REAL implementation, not merely on `command -v timeout` succeeding: the
  # suite installs a passthrough shim under that name when the box has none, and the
  # passthrough by definition does not kill anything. (It ran for the child's full 60s
  # and failed this assertion, which is the test proving it is behavioural rather than
  # decorative.) `_ST_TO_DIR` is set only when the passthrough was needed.
  if [ -z "${_ST_TO_DIR:-}" ]; then
    local _kdir; _kdir=$(mktemp -d)
    printf '#!/usr/bin/env bash\ntrap "" TERM\nsleep 60\n' > "$_kdir/stubborn"; chmod +x "$_kdir/stubborn"
    local _t0 _t1 _el
    _t0=$(date +%s)
    ( _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= BIRCHER_KILL_GRACE=1 \
      _net_run 5 "$_kdir/stubborn" >/dev/null 2>&1 )
    _t1=$(date +%s); _el=$(( _t1 - _t0 ))
    [ "$_el" -le 20 ] \
      || { echo "FAIL #62: a TERM-ignoring child was not force-killed (took ${_el}s, cap 5 + grace 1)"; rm -rf "$_kdir"; exit 1; }
    rm -rf "$_kdir"; unset _t0 _t1 _el _kdir
  else
    echo "[self-test] SKIP: no real timeout(1) -> kill-escalation not exercised here (it is on the runner)" >&2
  fi
  unset _edir
  rm -rf "$_tdir"
  unset _bad _err _rc _ddir _rdir _rr _tdir _cap _n _body _body_rc _arm_ln _view_ln _inner_cap _unbounded _res _ddir2
  # _clamp_int is the single validated path for every numeric knob on this branch --
  # the same defect was found independently in the first two before it existed.
  [ "$(_clamp_int 42 7 1 100)"     = 42 ]   || { echo "FAIL clamp: a valid value must pass through"; exit 1; }
  [ "$(_clamp_int abc 7 1 100)"    = 7 ]    || { echo "FAIL clamp: non-digits must fall back"; exit 1; }
  [ "$(_clamp_int '' 7 1 100)"     = 7 ]    || { echo "FAIL clamp: empty must fall back"; exit 1; }
  [ "$(_clamp_int ' 4' 7 1 100)"   = 7 ]    || { echo "FAIL clamp: whitespace must fall back"; exit 1; }
  [ "$(_clamp_int -5 7 1 100)"     = 7 ]    || { echo "FAIL clamp: negative must fall back"; exit 1; }
  [ "$(_clamp_int 0 7 1 100)"      = 7 ]    || { echo "FAIL clamp: below min must fall back"; exit 1; }
  [ "$(_clamp_int 101 7 1 100)"    = 7 ]    || { echo "FAIL clamp: above max must fall back"; exit 1; }
  # OCTAL: 0000060 is 48 to $(( )) and 60 to [ -ge ]. Must come out as decimal 60.
  [ "$(_clamp_int 0000060 7 1 100)" = 60 ]  || { echo "FAIL clamp: leading zeros must read as DECIMAL"; exit 1; }
  # OVERFLOW: bash WRAPS rather than erroring, so an oversized value looks valid. The
  # length cap is not redundant with the range check, and this is the value that proves
  # it: 18446744073709551666 is 2^64 + 50, so `$((10#...))` yields exactly 50 -- inside
  # [1,100], accepted by the range check, and a completely fabricated number. A merely
  # "very large" test value wraps to something out of range and is caught either way,
  # which is why the first version of this assertion could not tell the guards apart.
  [ "$(_clamp_int 18446744073709551666 7 1 100)" = 7 ] \
    || { echo "FAIL clamp: an oversized value that WRAPS INTO RANGE must fall back, not be honoured"; exit 1; }
  [ "$(_clamp_int 99999999999999999999 7 1 100)" = 7 ] || { echo "FAIL clamp: oversized must fall back"; exit 1; }
  # FATAL-ABORT guard: $((10#abc)) kills the shell, so the digit check must precede it.
  # An rc-only assertion cannot tell the guard from the abort -- require clean stderr.
  _cerr=$( _clamp_int abc 7 1 100 2>&1 >/dev/null )
  [ -z "$_cerr" ] || { echo "FAIL clamp: rejected by BASH, not by the guard: $_cerr"; exit 1; }
  unset _cerr
  # A hostile poll interval must not defeat the loops: 0 would spin without advancing
  # the counter, and a huge one would sleep past the absolute deadline.
  [ "$(_clamp_int 0 30 1 300)"     = 30 ]   || { echo "FAIL #62: a 0 poll interval must fall back (it would hot-loop)"; exit 1; }
  [ "$(_clamp_int 86400 30 1 300)" = 30 ]   || { echo "FAIL #62: a huge poll interval must fall back (it would sleep past the deadline)"; exit 1; }
  [ "$(_clamp_int abc 5 1 20)"     = 5 ]    || { echo "FAIL #62: a non-numeric retry count must fall back, not disable the bound"; exit 1; }
  [ "$(_clamp_int 100000 5 1 20)"  = 5 ]    || { echo "FAIL #62: an oversized retry count must clamp"; exit 1; }
  # (The call sites are asserted structurally at the top of the suite -- unit-testing
  # the helper proves nothing about whether the loops route through it.)
  # --- #71: generalised deadline helpers + the pre-merge phase bound -----------
  [ "$(_cap_to 120 '')" = 120 ]            || { echo "FAIL #71: an unarmed deadline must yield the default cap"; exit 1; }
  [ "$(_cap_to 120 abc)" = 120 ]           || { echo "FAIL #71: an unusable epoch must yield the default cap"; exit 1; }
  [ "$(_cap_to 120 "$(( $(date +%s) + 30 ))")" -le 31 ] \
    || { echo "FAIL #71: the cap must shrink to the remaining budget"; exit 1; }
  # THE DEFECT THIS EXISTS FOR: a remainder of 1-4s used to be treated as garbage and
  # replaced with the 300s default, so a call could run five minutes past the very
  # deadline the remainder was protecting.
  for _r in 1 2 3 4; do
    _c=$(_cap_to 120 "$(( $(date +%s) + _r ))")
    { [ "$_c" -ge 1 ] && [ "$_c" -le "$_r" ]; } \
      || { echo "FAIL #71: a ${_r}s remainder must pass through as ${_r}s, not expand (got '$_c')"; exit 1; }
  done
  # NEVER zero: `timeout 0` means NO LIMIT, so a zero cap would remove the bound at
  # exactly the moment the budget ran out.
  [ "$(_cap_to 120 "$(( $(date +%s) - 500 ))")" -ge 1 ] \
    || { echo "FAIL #71: an expired deadline must still yield a POSITIVE cap"; exit 1; }
  # ...and _net_run must not clamp a small computed cap back up.
  local _n71; _n71=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' \
    'printf "%s\n" "$1" >> "$TO_LOG"' 'shift' 'exec "$@"' > "$_n71/timeout"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$_n71/gh"; chmod +x "$_n71/timeout" "$_n71/gh"
  for _r in 1 3 5; do
    : > "$_n71/caps"
    ( PATH="$_n71:$PATH" TO_LOG="$_n71/caps" _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= \
      _net_run "$_r" gh x >/dev/null 2>&1 )
    [ "$(cat "$_n71/caps")" = "$_r" ] \
      || { echo "FAIL #71: _net_run expanded a computed cap of ${_r}s to '$(cat "$_n71/caps")'"; rm -rf "$_n71"; exit 1; }
  done
  rm -rf "$_n71"; unset _n71 _r _c
  # The generalised arming must reach a caller's local, and leave it empty (never
  # `unset`, which re-exposes an outer value) when the clock cannot be read.
  _arm_probe() { local D=""; _arm_deadline D 600 2>/dev/null; printf '%s' "${D:-UNARMED}"; }
  _ap=$(_arm_probe); { [ "$_ap" != UNARMED ] && [ "$_ap" -gt "$(date +%s)" ]; } \
    || { echo "FAIL #71: _arm_deadline must set the caller's variable to a future instant"; exit 1; }
  local _dd; _dd=$(mktemp -d); printf '#!/usr/bin/env bash\nexit 1\n' > "$_dd/date"; chmod +x "$_dd/date"
  _ap=$( D=$(( $(date +%s) - 9999 )); export D; PATH="$_dd:$PATH" _arm_probe )
  [ "$_ap" = UNARMED ] \
    || { echo "FAIL #71: a dead clock must leave the deadline unarmed, not expose a stale outer value (got '$_ap')"; rm -rf "$_dd"; exit 1; }
  rm -rf "$_dd"; unset -f _arm_probe; unset _ap _dd
  # EVERY pre-merge loop must consult the PHASE deadline, not only its own sleep
  # counter. A per-call cap does not bound a loop: five status attempts x (POST +
  # verify) at 60s each is ten minutes inside a "60-second" ceiling. Asserted
  # structurally because the behavioural version is timing-dependent -- with backoff
  # disabled the sleep counter wins instantly, and with it enabled the test would race
  # the clock and flake.
  _mb=$(declare -f merge_ready_pr)
  [ "$(printf '%s\n' "$_mb" | grep -c '_deadline_passed "\$PREMERGE_DEADLINE_AT"')" -ge 5 ] \
    || { echo "FAIL #71: merge_ready_pr's pre-merge loops must all consult the phase deadline"; exit 1; }
  # NOTE: the count above is a cheap smoke check, NOT the property. Two real defects
  # once satisfied it -- a bare `sleep 5` immediately after a check, and an unguarded
  # reconciliation probe -- and a shape-matching replacement was no better: it only
  # recognised the spellings I happened to write, and would have missed
  # `delay=5; sleep "$delay"` or `command sleep 5`. The property is enforced
  # BEHAVIOURALLY below instead.
  _contains "$_mb" '_arm_deadline PREMERGE_DEADLINE_AT' \
    || { echo "FAIL #71: the pre-merge deadline must be armed"; exit 1; }
  # Armed BEFORE the first network call, or the phase it bounds has already started.
  _al=$(printf '%s\n' "$_mb" | grep -n '_arm_deadline PREMERGE_DEADLINE_AT' | cut -d: -f1); _al=${_al%%$'\n'*}
  _fl=$(printf '%s\n' "$_mb" | grep -n 'gh pr view' | cut -d: -f1); _fl=${_fl%%$'\n'*}
  { [ -n "$_al" ] && [ -n "$_fl" ] && [ "$_al" -lt "$_fl" ]; } \
    || { echo "FAIL #71: the pre-merge deadline must be armed before the first network call (arm=$_al call=$_fl)"; exit 1; }
  # And no pre-merge gh call may bypass the bounded wrapper. Anchored to COMMAND
  # position -- a looser pattern matched "gh pr merge failed" inside a MERGE_NOTE string
  # and "gh rc=$rc" inside a diagnostic, i.e. prose ABOUT gh rather than calls to it.
  _ub=$(printf '%s\n' "$_mb" | grep -E '(^[[:space:]]*|[;&|(]|\$\()[[:space:]]*gh (pr|api) ' | grep -v '_net_run\|_ci_gh')
  [ -z "${_ub//[[:space:]]/}" ] \
    || { echo "FAIL #71: unbounded gh call in merge_ready_pr: $_ub"; exit 1; }
  _pc=$(declare -f _post_cross_review_status)
  _ub=$(printf '%s\n' "$_pc" | grep -E '(^[[:space:]]*|[;&|(]|\$\()[[:space:]]*gh (pr|api) ' | grep -v '_net_run\|_ci_gh')
  [ -z "${_ub//[[:space:]]/}" ] \
    || { echo "FAIL #71: unbounded gh call in _post_cross_review_status: $_ub"; exit 1; }
  _contains "$_pc" '_deadline_passed "${PREMERGE_DEADLINE_AT:-}"' \
    || { echo "FAIL #71: the status post+verify retry loop must consult the phase deadline"; exit 1; }
  unset _mb _pc _ub _al _fl
  # NO NETWORK CALL AFTER EXPIRY. The deadline used to be checked only after each
  # POST+verify pair, so one expiring during the 32s backoff still let the next attempt
  # start BOTH calls -- ~53s of overrun where the bound promises at most one in-flight
  # call. Driven behaviourally: an already-expired deadline must produce zero calls.
  local _sd; _sd=$(mktemp -d); : > "$_sd/calls"
  printf '#!/usr/bin/env bash\necho "$*" >> "$GH_CALLS"\nexit 1\n' > "$_sd/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_sd/timeout"
  chmod +x "$_sd/gh" "$_sd/timeout"
  ( PATH="$_sd:$PATH"; export PATH; GH_CALLS="$_sd/calls"; export GH_CALLS
    REPO=demo/demo; PREMERGE_DEADLINE_AT=$(( $(date +%s) - 5 ))
    _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= \
    _post_cross_review_status demo 7 headsha1234567 >/dev/null 2>&1 )
  [ ! -s "$_sd/calls" ] \
    || { echo "FAIL #71: status post made $(wc -l < "$_sd/calls") network calls after the deadline expired"; rm -rf "$_sd"; exit 1; }
  # ...and with time left it must still work, or the assertion above would pass for a
  # function that never does anything.
  : > "$_sd/calls"
  ( PATH="$_sd:$PATH"; export PATH; GH_CALLS="$_sd/calls"; export GH_CALLS
    REPO=demo/demo; PREMERGE_DEADLINE_AT=$(( $(date +%s) + 600 )); BIRCHER_STATUS_BACKOFF=0
    _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= \
    _post_cross_review_status demo 7 headsha1234567 >/dev/null 2>&1 )
  [ -s "$_sd/calls" ] \
    || { echo "FAIL #71: with budget remaining, the status post must still reach gh"; rm -rf "$_sd"; exit 1; }
  # THE CHECK BEFORE THE VERIFICATION needs the deadline to expire DURING the POST --
  # an already-expired one breaks at the first check and never reaches it. So the fake
  # POST outlives the remaining budget, and exactly ONE call must be made.
  local _sd2; _sd2=$(mktemp -d); : > "$_sd2/calls"
  printf '%s\n' '#!/usr/bin/env bash' 'echo "$*" >> "$GH_CALLS"' \
    'case "$*" in *statuses*) sleep 2 ;; esac' 'exit 1' > "$_sd2/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_sd2/timeout"
  chmod +x "$_sd2/gh" "$_sd2/timeout"
  ( PATH="$_sd2:$PATH"; export PATH; GH_CALLS="$_sd2/calls"; export GH_CALLS
    REPO=demo/demo; PREMERGE_DEADLINE_AT=$(( $(date +%s) + 1 ))
    _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= \
    _post_cross_review_status demo 7 headsha1234567 >/dev/null 2>&1 )
  [ "$(wc -l < "$_sd2/calls" | tr -d ' ')" = 1 ] \
    || { echo "FAIL #71: a deadline expiring during the POST must stop before the verification (got $(wc -l < "$_sd2/calls") calls)"; rm -rf "$_sd2"; exit 1; }
  rm -rf "$_sd2"; unset _sd2
  # THE BACKOFF must be capped to what remains. Unbounded, attempt 2's 8s sleep starts
  # while ~1s of budget is left and overruns by seven -- the phase exceeded by WAITING
  # rather than by working, which the per-call caps cannot see.
  local _sd3 _t0 _t1; _sd3=$(mktemp -d)
  printf '#!/usr/bin/env bash\nexit 1\n' > "$_sd3/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_sd3/timeout"
  chmod +x "$_sd3/gh" "$_sd3/timeout"
  _t0=$(date +%s)
  ( PATH="$_sd3:$PATH"; export PATH; REPO=demo/demo
    PREMERGE_DEADLINE_AT=$(( $(date +%s) + 3 )); BIRCHER_STATUS_BACKOFF=1
    _TIMEOUT_BIN_LOADED= _TIMEOUT_BIN_CACHE= \
    _post_cross_review_status demo 7 headsha1234567 >/dev/null 2>&1 )
  _t1=$(date +%s)
  [ "$(( _t1 - _t0 ))" -le 6 ] \
    || { echo "FAIL #71: the status backoff overran the phase deadline ($(( _t1 - _t0 ))s for a 3s budget)"; rm -rf "$_sd3"; exit 1; }
  rm -rf "$_sd3"; unset _sd3 _t0 _t1
  # ONCE THE PHASE DEADLINE IS SPENT, merge_ready_pr must neither sleep nor call out.
  # Behavioural, and spelling-independent: a shimmed clock jumps forward after arming,
  # and `sleep` and `gh` are instrumented, so ANY wait or call after expiry is caught
  # however it is written. This replaces a grep for `sleep <digit>`, which only ever
  # tested the spellings I had used.
  # An explicit budget, and a required non-empty log. Both were implicit before: the
  # test assumed BIRCHER_PREMERGE_BUDGET was 600 without setting it, and
  # BIRCHER_PREMERGE_BUDGET is a supported override clamped as low as 60. At 60 the
  # first guard would see the phase already expired, neither run would sleep at all,
  # and an empty log passed -- so an uncapped sleep in either loop could sail through a
  # test whose whole purpose was to catch exactly that.
  local _bud=600 _off=598
  # BIRCHER_PREMERGE_BUDGET is consumed at load into the clamped constant, so a subshell
  # override of the env var alone would not reach merge_ready_pr -- set both.
  local _bd; _bd=$(mktemp -d); : > "$_bd/sleeps"; : > "$_bd/ghcalls"; : > "$_bd/n"
  printf '%s\n' '#!/usr/bin/env bash' \
    'n=$(cat "$DATE_N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$DATE_N"' \
    'r=$(/bin/date +%s)' \
    'if [ "$n" -le 1 ]; then echo "$r"; else echo "$((r + ${DATE_OFFSET:-99999}))"; fi' \
    > "$_bd/date"
  printf '#!/usr/bin/env bash\necho "$*" >> "$SLEEP_LOG"\nexit 0\n' > "$_bd/sleep"
  # The shim must drive the flow THROUGH the mergeability poll and into the merge retry
  # loop, or that loop's sleep is never exercised -- which is exactly why the first
  # version of this test scored a real uncapped sleep there as caught when it was not.
  printf '%s\n' '#!/usr/bin/env bash' 'echo "$*" >> "$GH_CALLS"' \
    'case "$*" in' \
    '  *mergeable*)          echo "${FAKE_MERGEABLE_OUT:-MERGEABLE}"; exit 0 ;;' \
    '  *state,headRefOid*)   echo "OPEN|headsha1234567"; exit 0 ;;' \
    'esac' \
    'exit 1' > "$_bd/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_bd/timeout"
  chmod +x "$_bd/date" "$_bd/sleep" "$_bd/gh" "$_bd/timeout"
  ( PATH="$_bd:$PATH"; export PATH
    DATE_N="$_bd/n"; DATE_OFFSET=99999; SLEEP_LOG="$_bd/sleeps"; GH_CALLS="$_bd/ghcalls"
    export DATE_N DATE_OFFSET SLEEP_LOG GH_CALLS
    REPO=demo/demo BIRCHER_STATUS_BACKOFF=1 \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  [ ! -s "$_bd/sleeps" ] \
    || { echo "FAIL #71: merge_ready_pr slept $(wc -l < "$_bd/sleeps") time(s) after the phase deadline expired"; rm -rf "$_bd"; exit 1; }
  # THE DURATION, not just the timing. The instrumented sleep returns instantly, so a
  # fake clock alone cannot show an overrun -- an uncapped `sleep 5` issued with 2s left
  # looks identical to a capped one. Asserting the ARGUMENT catches it however it is
  # spelled: `sleep 5`, `_d=5; sleep "$_d"`, `command sleep "$((5))"` all log 5.
  # DATE_OFFSET=598 against a 600s budget leaves ~2s remaining for the whole run.
  : > "$_bd/sleeps"; : > "$_bd/ghcalls"; : > "$_bd/n3"
  ( PATH="$_bd:$PATH"; export PATH
    DATE_N="$_bd/n3"; DATE_OFFSET="$_off"; SLEEP_LOG="$_bd/sleeps"; GH_CALLS="$_bd/ghcalls"
    BIRCHER_PREMERGE_BUDGET="$_bud"; export DATE_N DATE_OFFSET SLEEP_LOG GH_CALLS BIRCHER_PREMERGE_BUDGET
    REPO=demo/demo BIRCHER_STATUS_BACKOFF=1 \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  _check_sleeps() { # <log> <label> [required-gh-substring]
    local _slp
    # A shared sleep log cannot tell WHICH phase slept: `_post_cross_review_status` runs
    # before the merge loop and logs its own backoff, so the run labelled "the merge
    # retry loop" could pass on status sleeps alone without ever dispatching a merge.
    # Each scenario now proves it reached its own phase.
    if [ -n "${3:-}" ]; then
      _contains "$(cat "$_bd/ghcalls" 2>/dev/null)" "$3" \
        || { echo "FAIL #71: $2 never reached its phase (no '"'"'$3'"'"' in the gh log)"; rm -rf "$_bd"; exit 1; }
    fi
    [ -s "$1" ] \
      || { echo "FAIL #71: $2 logged NO sleep -- the test never reached the path it asserts on"; rm -rf "$_bd"; exit 1; }
    while IFS= read -r _slp; do
      [ -n "$_slp" ] || continue
      case "$_slp" in ''|*[!0-9]*) echo "FAIL #71: non-numeric sleep argument '$_slp' ($2)"; rm -rf "$_bd"; exit 1 ;; esac
      [ "$_slp" -le 2 ] \
        || { echo "FAIL #71: $2 slept ${_slp}s with only ~2s of phase budget left -- not capped to the remainder"; rm -rf "$_bd"; exit 1; }
    done < "$1"
  }
  _check_sleeps "$_bd/sleeps" "the merge retry loop" "pr merge"
  # ...and again with mergeability never resolving, which is the ONLY way to reach the
  # mergeability poll's own sleep. One shim cannot exercise both loops: answering
  # MERGEABLE skips that poll entirely, and not answering it never reaches the merge.
  : > "$_bd/sleeps"; : > "$_bd/ghcalls"; : > "$_bd/n4"
  ( PATH="$_bd:$PATH"; export PATH
    DATE_N="$_bd/n4"; DATE_OFFSET="$_off"; SLEEP_LOG="$_bd/sleeps"; GH_CALLS="$_bd/ghcalls"
    FAKE_MERGEABLE_OUT=UNKNOWN; BIRCHER_PREMERGE_BUDGET="$_bud"
    export DATE_N DATE_OFFSET SLEEP_LOG GH_CALLS FAKE_MERGEABLE_OUT BIRCHER_PREMERGE_BUDGET
    REPO=demo/demo BIRCHER_STATUS_BACKOFF=1 \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  _check_sleeps "$_bd/sleeps" "the mergeability poll" "--json mergeable"
  unset -f _check_sleeps
  # ...and the control: with the clock running normally it MUST do work, or the
  # assertion above would hold for a function that does nothing at all.
  : > "$_bd/ghcalls"
  ( PATH="$_bd:$PATH"; export PATH
    DATE_N="$_bd/n2"; DATE_OFFSET=0; SLEEP_LOG="$_bd/sleeps2"; GH_CALLS="$_bd/ghcalls"
    export DATE_N DATE_OFFSET SLEEP_LOG GH_CALLS
    REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  [ -s "$_bd/ghcalls" ] \
    || { echo "FAIL #71: with budget remaining, merge_ready_pr must still call gh"; rm -rf "$_bd"; exit 1; }
  rm -rf "$_bd"; unset _bd
  rm -rf "$_sd"; unset _sd
  # The kill grace is added to EVERY capped call, so an unvalidated one weakens the
  # whole bound. The honest statement is "deadline plus at most one clamped grace".
  [ "$(_clamp_int "${BIRCHER_KILL_GRACE:-10}" 10 1 60)" = 10 ] \
    || { echo "FAIL #71: the default kill grace must be 10"; exit 1; }
  [ "$(_clamp_int 99999 10 1 60)" = 10 ] \
    || { echo "FAIL #71: an oversized kill grace must clamp"; exit 1; }
  _kg=$(declare -f _net_run _ci_gh | grep -c '_clamp_int "${BIRCHER_KILL_GRACE:-10}" 10 1 60')
  [ "$_kg" = 2 ] \
    || { echo "FAIL #71: both timeout wrappers must clamp the kill grace (found $_kg)"; exit 1; }
  unset _kg
  # LEADING ZEROS END TO END. `_clamp_int` canonicalises (it reassigns v=$((10#$v))
  # before printing), so `$(( now + span ))` inside _arm_deadline cannot re-read the
  # value as octal -- the #62 defect cannot come back through the generalised helper.
  # Review flagged this as a regression; it was not, but the class is real enough that
  # the invariant is now pinned rather than argued.
  for _lz in 00007200 0000600 0000060; do
    _c=$(_clamp_int "$_lz" 600 60 9999999)
    case "$_c" in 0?*) echo "FAIL #71: _clamp_int returned non-canonical '$_c' for '$_lz'"; exit 1 ;; esac
    _armed=""; _arm_deadline _armed "$_c"
    _span=$(( _armed - $(date +%s) ))
    [ "$_span" -ge $(( _c - 2 )) ] && [ "$_span" -le $(( _c + 2 )) ] \
      || { echo "FAIL #71: BUDGET='$_lz' armed ${_span}s, expected ~${_c}s (octal re-interpretation?)"; exit 1; }
  done
  unset _lz _c _armed _span
  # --- #70: an expected context that has not registered must not read green ------
  # The defect: a required context gated behind `needs:` registers AFTER faster ones
  # finish. `_keep_blocking_checks` then sees only the fast ones, all green, and the
  # watcher breaks out before the slow one reports. Measured on muesli the margin is 11
  # seconds; it is structurally unbounded on a repo with deeper job staging.
  # Four fields since #73: name|status|conclusion|app. These fixtures are app-less, which
  # is the unbindable case -- eligible for any requirement, bound or not.
  _ec_lines='server (go)|completed|success|
client (node)|completed|success|
plugins (python) gate|in_progress||'
  [ -z "$(_expected_incomplete "$_ec_lines" 'server (go)')" ] \
    || { echo "FAIL #70: a present, terminal context must not read as incomplete"; exit 1; }
  [ "$(_expected_incomplete "$_ec_lines" 'plugins (python) gate')" = 'plugins (python) gate' ] \
    || { echo "FAIL #70: a REGISTERED but unfinished context must read as incomplete"; exit 1; }
  [ "$(_expected_incomplete "$_ec_lines" 'e2e-desktop')" = 'e2e-desktop' ] \
    || { echo "FAIL #70: an ABSENT context must read as incomplete -- this is the bug"; exit 1; }
  [ -z "$(_expected_incomplete "$_ec_lines" '')" ] \
    || { echo "FAIL #70: an empty expected set must never block"; exit 1; }
  # Names contain spaces and parentheses; matching must be exact and literal.
  [ "$(_expected_incomplete "$_ec_lines" 'server')" = 'server' ] \
    || { echo "FAIL #70: matching must be EXACT -- 'server' is not 'server (go)'"; exit 1; }
  # OPT-IN. Unset means every repo behaves exactly as it does today.
  [ -z "$(_expected_set known 'a')" ] \
    || { echo "FAIL #70: an unset list must produce an empty expected set"; exit 1; }
  [ -z "$(BIRCHER_MAIN_EXPECTED_CONTEXTS='   ' _expected_set known 'a')" ] \
    || { echo "FAIL #70: a whitespace-only list must produce an empty expected set"; exit 1; }
  # The list can never grant blocking authority branch protection did not.
  [ "$(BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'a\nb')" _expected_set known 'a' | tr -d '\n')" = a ] \
    || { echo "FAIL #70: a listed context that is NOT required must be dropped"; exit 1; }
  # ...but a successfully-unprotected branch has nothing to intersect with, so the
  # declaration stands on its own.
  [ "$(BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'a\nb')" _expected_set empty '' | tr -d '\n')" = ab ] \
    || { echo "FAIL #70: with no protection the declared list must stand"; exit 1; }
  # UNKNOWN is a failed lookup, not "nothing expected". It must never yield a set that
  # silently disables the check -- the caller holds the verdict pending instead.
  [ -z "$(BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'a\nb')" _expected_set unknown '')" ] \
    || { echo "FAIL #70: an unreadable required set must not produce an expected set"; exit 1; }
  # Ignore-listed contexts are subtracted: waiting for one would bypass the filter that
  # exists because Dependabot's check-runs once turned a healthy main red.
  [ "$(BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'a\nDependabot')" _expected_set known "$(printf 'a\nDependabot')" | tr -d '\n')" = a ] \
    || { echo "FAIL #70: an ignore-listed context must be subtracted from the expected set"; exit 1; }
  # CRLF. `read -r` preserves the carriage return, so a list pasted from a CRLF source
  # becomes `unit\r` / `e2e\r`, the exact intersection rejects every entry, and the
  # expected set comes back EMPTY -- which means no gate at all. Silently disabling a
  # safety feature is worse than breaking it loudly.
  [ "$(BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'unit\r\ne2e\r\n')" \
       _expected_set known "$(printf 'unit\ne2e')" | tr -d '\n')" = unite2e ] \
    || { echo "FAIL #70: a CRLF expected-context list must still produce the expected set, not silently empty it"; exit 1; }
  # ...and only ONE trailing CR is stripped: other whitespace is significant, because
  # GitHub permits leading and trailing spaces in check names.
  [ -z "$(BIRCHER_MAIN_EXPECTED_CONTEXTS='unit ' _expected_set known 'unit' | tr -d '\n')" ] \
    || { echo "FAIL #70: a trailing SPACE must remain significant -- 'unit ' is not 'unit'"; exit 1; }
  unset _ec_lines
  # OPT-OUT MUST COST NOTHING. With the list unset, an unreadable protection endpoint
  # must not add a lookup per poll: `mreq` was fetched once per phase before #70, and a
  # repo whose entire guarantee is "nothing changed" should not start paying for a
  # feature it has not enabled. Counted, because the alternative is asserting it in prose.
  local _pd; _pd=$(mktemp -d); : > "$_pd/prot"
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in *"/protection"*) echo x >> "$PROT_LOG"; echo "HTTP 500 broke" >&2; exit 1 ;; esac' \
    'case "$*" in *mergeable*) echo MERGEABLE; exit 0 ;; *"/check-runs"*) printf "%s" "[{\"check_runs\":[{\"name\":\"ci\",\"status\":\"completed\",\"conclusion\":\"success\"}]}]"; exit 0 ;; esac' \
    'case "$*" in *mergeCommit*) echo mergesha7654321; exit 0 ;; *headRefOid*) echo headsha1234567; exit 0 ;; esac' \
    'case "$*" in *"/status"*) printf "%s" "[{\"statuses\":[]}]"; exit 0 ;; esac' \
    'exit 0' > "$_pd/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_pd/timeout"
  chmod +x "$_pd/gh" "$_pd/timeout"
  ( PATH="$_pd:$PATH"; export PATH; PROT_LOG="$_pd/prot"; export PROT_LOG
    REPO=demo/demo MAIN_CI_SETTLE_TIMEOUT=4 MAIN_CI_POLL_INTERVAL=1 BIRCHER_MAIN_CI_RERUN=0 \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  _pn=$(wc -l < "$_pd/prot" | tr -d ' ')
  [ "$_pn" -le 1 ] \
    || { echo "FAIL #70: with the list UNSET, a failing protection lookup was retried $_pn times (expected 1 per phase)"; rm -rf "$_pd"; exit 1; }
  # ...and with the list SET it MUST retry, or a transient outage costs the whole phase.
  : > "$_pd/prot"
  ( PATH="$_pd:$PATH"; export PATH; PROT_LOG="$_pd/prot"; export PROT_LOG
    REPO=demo/demo MAIN_CI_SETTLE_TIMEOUT=4 MAIN_CI_POLL_INTERVAL=1 BIRCHER_MAIN_CI_RERUN=0 \
      BIRCHER_MAIN_EXPECTED_CONTEXTS='server (go)' \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1 )
  [ "$(wc -l < "$_pd/prot" | tr -d ' ')" -ge 2 ] \
    || { echo "FAIL #70: with the list SET, an unreadable protection endpoint must be retried each poll"; rm -rf "$_pd"; exit 1; }
  rm -rf "$_pd"; unset _pd _pn
  # APP-BOUND REQUIRED CHECKS. GitHub's status-check-policy carries `contexts` (names)
  # AND `checks` ({context, app_id}). Reading only `contexts` drops any check configured
  # through the app-bound form: the operator's declared context then fails the
  # intersection, is silently removed from the expected set, and #70's gate does not
  # apply to it -- this issue's own defect, reintroduced one layer down. The fixture puts
  # the delayed context ONLY in `checks`.
  local _ad; _ad=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in' \
    '  *"/protection"*)' \
    '    # RAW json. Since #73 the snapshot fetches the whole protection object and' \
    '    # validates it before serialising, so a shim that pre-applied --jq would be' \
    '    # answering a question the production code no longer asks.' \
    '    printf "%s" "$PROT_JSON"; exit 0 ;;' \
    '  *mergeable*) echo MERGEABLE; exit 0 ;;' \
    '  *mergeCommit*) echo mergesha7654321; exit 0 ;;' \
    '  *headRefOid*) echo headsha1234567; exit 0 ;;' \
    '  *"/status"*) printf "%s" "[{\"statuses\":[]}]"; exit 0 ;;' \
    '  *"/check-runs"*) printf "%s" "[{\"check_runs\":[{\"name\":\"unit\",\"status\":\"completed\",\"conclusion\":\"success\"}]}]"; exit 0 ;;' \
    'esac' 'exit 0' > "$_ad/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_ad/timeout"
  chmod +x "$_ad/gh" "$_ad/timeout"
  ( PATH="$_ad:$PATH"; export PATH
    PROT_JSON='{"required_status_checks":{"contexts":["unit"],"checks":[{"context":"unit"},{"context":"e2e"}]}}'
    export PROT_JSON
    REPO=demo/demo MAIN_CI_SETTLE_TIMEOUT=3 MAIN_CI_POLL_INTERVAL=1 BIRCHER_MAIN_CI_RERUN=0 \
      BIRCHER_MAIN_EXPECTED_CONTEXTS="$(printf 'unit\ne2e')" \
      merge_ready_pr demo 7 headsha1234567 >/dev/null 2>&1
    [ "$?" -ne 0 ] ) \
    || { echo "FAIL #70: an app-bound required check (present only in .checks) was dropped, so the gate did not apply"; rm -rf "$_ad"; exit 1; }
  # A PERMISSION 404 is not evidence that the branch is unprotected. Both return 404;
  # only the message distinguishes them. Misread, a stale or PR-only entry in the
  # declared list would gate on a context protection never required -- a permission
  # regression becoming a multi-hour halt after a perfectly healthy merge.
  local _p4; _p4=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in *"/protection"*) echo "$PROT_ERR" >&2; exit 1 ;; esac' 'exit 0' > "$_p4/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_p4/timeout"
  chmod +x "$_p4/gh" "$_p4/timeout"
  [ "$( PATH="$_p4:$PATH" PROT_ERR='gh: Branch not protected (HTTP 404)' REPO=demo/demo \
        _required_contexts_snapshot )" = empty ] \
    || { echo "FAIL #70: 'Branch not protected' must classify as an authoritative EMPTY set"; rm -rf "$_p4"; exit 1; }
  [ "$( PATH="$_p4:$PATH" PROT_ERR='gh: Not Found (HTTP 404)' REPO=demo/demo \
        _required_contexts_snapshot 2>/dev/null )" = unknown ] \
    || { echo "FAIL #70: a permission 404 must be UNREADABLE, not 'unprotected' -- it would use the declared list unintersected"; rm -rf "$_p4"; exit 1; }
  [ "$( PATH="$_p4:$PATH" PROT_ERR='gh: Bad gateway (HTTP 502)' REPO=demo/demo \
        _required_contexts_snapshot 2>/dev/null )" = unknown ] \
    || { echo "FAIL #70: a non-404 error must be unknown"; rm -rf "$_p4"; exit 1; }
  # The SECOND union call site. `_required_contexts` feeds the PR path (poll, sibling
  # reconciliation, recovery) and got the same fix -- but nothing exercised it, so
  # reverting only that line to `.contexts` was test-invisible even though the commit
  # deliberately changed it. Driven through a jq-honouring fixture, as real gh api behaves.
  local _rc2; _rc2=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in *"/protection"*)' \
    '  JQ=""; nx=0; for a in "$@"; do [ "$nx" = 1 ] && { JQ="$a"; nx=0; }; [ "$a" = "--jq" ] && nx=1; done' \
    '  printf "%s" "$PROT_JSON" | jq -r "${JQ:-.}"; exit 0 ;; esac' 'exit 0' > "$_rc2/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_rc2/timeout"
  chmod +x "$_rc2/gh" "$_rc2/timeout"
  _rcout=$( PATH="$_rc2:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":["unit"],"checks":[{"context":"unit"},{"context":"e2e"}]}}' \
    _REQUIRED_CONTEXTS_LOADED= _REQUIRED_CONTEXTS_CACHE= _required_contexts )
  _contains "$_rcout" 'e2e' \
    || { echo "FAIL #70: _required_contexts dropped an app-bound required check (got '$_rcout')"; rm -rf "$_rc2"; exit 1; }
  _contains "$_rcout" 'unit' \
    || { echo "FAIL #70: _required_contexts dropped a legacy context (got '$_rcout')"; rm -rf "$_rc2"; exit 1; }
  # Deduplicated: `unit` appears in BOTH representations and must be listed once.
  [ "$(printf '%s\n' "$_rcout" | grep -c '^unit$')" = 1 ] \
    || { echo "FAIL #70: a context in both .contexts and .checks must be deduplicated"; rm -rf "$_rc2"; exit 1; }
  rm -rf "$_rc2"; unset _rc2 _rcout
  rm -rf "$_p4"; unset _p4
  rm -rf "$_ad"; unset _ad
  # --- #73: producer matching, table-driven -----------------------------------
  # A `checks[]` requirement is pinned to an app_id; a `contexts[]` one is not. Matching
  # on name alone let a same-named check from the WRONG app satisfy a pinned requirement.
  # Not hypothetical: every one of muesli's seven required checks is app-bound.
  #
  # Columns: typed-requirement | rows | expected-result ("" = satisfied, else the name).
  _pm() { _expected_incomplete "$2" "$3" "$1"; }
  _B=$(printf 'bound\te2e\t15368'); _U=$(printf 'unbound\te2e')
  # the defect itself: only the WRONG producer reported, and it is green
  [ "$(_pm "$_B" 'e2e|completed|success|999' e2e)" = e2e ] \
    || { echo "FAIL #73: a wrong-app green must NOT satisfy an app-bound requirement"; exit 1; }
  [ -z "$(_pm "$_B" 'e2e|completed|success|15368' e2e)" ] \
    || { echo "FAIL #73: the required app's green must satisfy it"; exit 1; }
  # a stray producer must not be able to VETO the required one either
  [ -z "$(_pm "$_B" "$(printf 'e2e|completed|success|15368\ne2e|completed|failure|999')" e2e)" ] \
    || { echo "FAIL #73: a stray wrong-app RED must not block a satisfied requirement"; exit 1; }
  # ...but the required app's own red must hold, whatever else is green
  [ "$(_pm "$_B" "$(printf 'e2e|completed|failure|15368\ne2e|completed|success|999')" e2e)" = e2e ] \
    || { echo "FAIL #73: the required app's RED must not be masked by a stray green"; exit 1; }
  # order must not matter -- the aggregation is worst-first, not first-wins
  [ "$(_pm "$_B" "$(printf 'e2e|completed|success|999\ne2e|completed|failure|15368')" e2e)" = e2e ] \
    || { echo "FAIL #73: reversing row order changed the verdict"; exit 1; }
  # A commit STATUS carries no app, so it is NOT evidence that the named app produced
  # anything -- and anyone able to post a status could otherwise satisfy this gate while
  # branch protection itself stayed pending. An earlier cut had this backwards and the
  # test codified the mistake, which is why it is spelled out here.
  [ "$(_pm "$_B" 'e2e|completed|success|' e2e)" = e2e ] \
    || { echo "FAIL #73: an app-less row must NOT satisfy an app-bound requirement"; exit 1; }
  # ...but for an UNBOUND requirement a status is perfectly good evidence.
  [ -z "$(_pm "$_U" 'e2e|completed|success|' e2e)" ] \
    || { echo "FAIL #73: an app-less row must satisfy an UNBOUND requirement"; exit 1; }
  # pending from the required app holds; pending from a stray does not satisfy
  [ "$(_pm "$_B" 'e2e|in_progress||15368' e2e)" = e2e ] \
    || { echo "FAIL #73: the required app still running must hold"; exit 1; }
  [ "$(_pm "$_B" 'e2e|in_progress||999' e2e)" = e2e ] \
    || { echo "FAIL #73: a stray app running is not satisfaction"; exit 1; }
  # UNBOUND requirements keep the old behaviour: any producer satisfies
  [ -z "$(_pm "$_U" 'e2e|completed|success|999' e2e)" ] \
    || { echo "FAIL #73: an UNBOUND requirement must accept any producer"; exit 1; }
  # a context declared BOTH ways resolves to the bound form -- GitHub mirrors checks[]
  # into contexts[], so muesli declares all seven twice, and taking the looser one would
  # discard every binding on the repo this was built for
  [ "$(_pm "$(printf 'bound\te2e\t15368\nunbound\te2e')" 'e2e|completed|success|999' e2e)" = e2e ] \
    || { echo "FAIL #73: a doubly-declared context must resolve to the BOUND requirement"; exit 1; }
  # ...IN EITHER ORDER. The snapshot's `unique[]` sorts, so "bound" happens to precede
  # "unbound" today and a first-wins lookup would pass this by luck of the alphabet. That
  # is not a property worth depending on: it makes the guard untestable and would break
  # silently if the jq ever stopped sorting. Reversed, so the preference is real.
  [ "$(_pm "$(printf 'unbound\te2e\nbound\te2e\t15368')" 'e2e|completed|success|999' e2e)" = e2e ] \
    || { echo "FAIL #73: BOUND must win regardless of declaration order, not by sort luck"; exit 1; }
  # absence is still never satisfaction
  [ "$(_pm "$_B" 'other|completed|success|15368' e2e)" = e2e ] \
    || { echo "FAIL #73: an absent expected context must remain unsatisfied"; exit 1; }
  # app_id -1 is GitHub's documented WILDCARD ("Pass -1 to explicitly allow any app to
  # set the status"), and an omitted app_id is likewise permissive. Treated as a literal
  # binding, the matcher would demand a producer called "-1" and hold every merge on a
  # valid configuration -- so both are normalised to UNBOUND at the snapshot.
  _wild=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in *"/protection"*) printf "%s" "$PROT_JSON"; exit 0 ;; esac' 'exit 0' > "$_wild/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_wild/timeout"
  chmod +x "$_wild/gh" "$_wild/timeout"
  _w=$( PATH="$_wild:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":[],"checks":[{"context":"e2e","app_id":-1}]}}' \
    _required_contexts_snapshot 2>/dev/null | tail -1 )
  [ "$_w" = "$(printf 'unbound\te2e')" ] \
    || { echo "FAIL #73: app_id -1 is the ANY-APP wildcard and must record as unbound (got '$_w')"; rm -rf "$_wild"; exit 1; }
  _w=$( PATH="$_wild:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":[],"checks":[{"context":"e2e","app_id":null}]}}' \
    _required_contexts_snapshot 2>/dev/null | tail -1 )
  [ "$_w" = "$(printf 'unbound\te2e')" ] \
    || { echo "FAIL #73: an omitted app_id is permissive and must record as unbound (got '$_w')"; rm -rf "$_wild"; exit 1; }
  # SCHEMA violations are UNREADABLE, never a gate-less `known`. An empty context used to
  # serialise to `unbound<TAB>`, which _req_names then dropped -- a known snapshot with
  # nothing in it, i.e. no gate.
  for _bad in '{"required_status_checks":{"contexts":[""],"checks":[]}}' \
              '{"required_status_checks":{"contexts":[123],"checks":[]}}' \
              '{"required_status_checks":{"contexts":"nope","checks":[]}}' \
              '{"required_status_checks":{"checks":[{"context":"e2e","app_id":0}]}}' \
              '{"required_status_checks":{"checks":[{"context":"e2e","app_id":"15368"}]}}' \
              '{"required_status_checks":{"checks":[{"app_id":15368}]}}'; do
    _w=$( PATH="$_wild:$PATH" REPO=demo/demo PROT_JSON="$_bad" _required_contexts_snapshot 2>/dev/null )
    [ "$_w" = unknown ] \
      || { echo "FAIL #73: malformed protection must be UNREADABLE, not a gate-less known: $_bad -> '$_w'"; rm -rf "$_wild"; exit 1; }
  done
  # ...and a branch with no required checks at all is still a legitimate EMPTY.
  _w=$( PATH="$_wild:$PATH" REPO=demo/demo PROT_JSON='{"required_status_checks":{"contexts":[],"checks":[]}}' _required_contexts_snapshot 2>/dev/null )
  [ "$_w" = empty ] || { echo "FAIL #73: no required checks must be EMPTY, not unknown (got '$_w')"; rm -rf "$_wild"; exit 1; }
  _w=$( PATH="$_wild:$PATH" REPO=demo/demo PROT_JSON='{"url":"x"}' _required_contexts_snapshot 2>/dev/null )
  [ "$_w" = empty ] || { echo "FAIL #73: absent required_status_checks must be EMPTY (got '$_w')"; rm -rf "$_wild"; exit 1; }
  rm -rf "$_wild"; unset _wild _w _bad
  unset -f _pm; unset _B _U
  # AN UNREPRESENTABLE CONTEXT MUST NOT SILENTLY DISABLE THE GATE. The typed protocol
  # separates fields with TAB, so a context containing one would be read as extra fields:
  # `build<TAB>linux` becomes name `build`, the declared list can no longer intersect it,
  # the expected set comes back EMPTY -- and empty means no gate at all. That is #67's
  # lesson about names that cannot be represented in a delimiter protocol, in a new
  # delimiter. Every line here IS a requirement, so there is no "drop the non-required
  # one" escape: the honest answer is `unknown`, which holds pending and says why.
  local _tb; _tb=$(mktemp -d)
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in *"/protection"*) printf "%s" "$PROT_JSON"; exit 0 ;; esac' 'exit 0' > "$_tb/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "$1" = "-k" ]; then shift 2; fi' 'shift' 'exec "$@"' > "$_tb/timeout"
  chmod +x "$_tb/gh" "$_tb/timeout"
  _tbout=$( PATH="$_tb:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":["ok"],"checks":[{"context":"build\tlinux","app_id":15368}]}}' \
    _required_contexts_snapshot 2>/dev/null )
  [ "$_tbout" = unknown ] \
    || { echo "FAIL #73: a TAB-bearing required context must make protection UNREADABLE, not silently truncate (got '$_tbout')"; rm -rf "$_tb"; exit 1; }
  # ...and a newline, for the same reason.
  _tbout=$( PATH="$_tb:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":["a\nb"],"checks":[]}}' \
    _required_contexts_snapshot 2>/dev/null )
  [ "$_tbout" = unknown ] \
    || { echo "FAIL #73: a newline-bearing required context must make protection UNREADABLE (got '$_tbout')"; rm -rf "$_tb"; exit 1; }
  # ...but an ordinary name with SPACES and PARENTHESES -- which every muesli context has
  # -- must still parse, or the guard would reject the real world.
  _tbout=$( PATH="$_tb:$PATH" REPO=demo/demo \
    PROT_JSON='{"required_status_checks":{"contexts":["plugins (python) gate"],"checks":[{"context":"plugins (python) gate","app_id":15368}]}}' \
    _required_contexts_snapshot 2>/dev/null | head -1 )
  [ "$_tbout" = known ] \
    || { echo "FAIL #73: a normal context with spaces and parens must parse (got '$_tbout')"; rm -rf "$_tb"; exit 1; }
  rm -rf "$_tb"; unset _tb _tbout
  echo "_expected_incomplete producer matching OK (#73)"
  echo "_expected_set/_expected_incomplete OK (#70 completeness gate)"
  echo "_cap_to/_arm_deadline OK (#71 pre-merge phase bound)"
  echo "_clamp_int OK (#62 one validated path for every numeric knob)"
  echo "_past_ci_deadline OK (#62 shared wall clock)"
  # --- #359: _revert_git_args guards empty sha + adds -m 1 for merge commits -----
  [ "$(_revert_git_args '' 1)" = "" ]                     || { echo "FAIL revert empty-sha (must be blank -> no bare git revert)"; exit 1; }
  [ "$(_revert_git_args abc123 1)" = "--no-edit abc123" ] || { echo "FAIL revert single-parent"; exit 1; }
  [ "$(_revert_git_args abc123 2)" = "--no-edit -m 1 abc123" ] || { echo "FAIL revert merge-parent (needs -m 1)"; exit 1; }
  [ "$(_revert_git_args abc123 '')" = "--no-edit abc123" ] || { echo "FAIL revert default-parent"; exit 1; }
  # The string assertions above are not enough on their own: they passed for months
  # while the args contained `-q`, which `git revert` rejects with exit 129 and a usage
  # dump. So ACTUALLY RUN git with the produced args, on both commit shapes, in a
  # throwaway repo. This is the test that would have caught it.
  local rvd; rvd=$(mktemp -d)
  ( set -e
    cd "$rvd"; git init -q .
    git -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
    echo one > f; git add f; git -c user.email=t@t -c user.name=t commit -q -m one
    # single-parent (the squash-merge shape the runner actually produces)
    sha1=$(git rev-parse HEAD)
    # shellcheck disable=SC2046
    git -c user.email=t@t -c user.name=t revert $(_revert_git_args "$sha1" 1) >/dev/null 2>&1
    # a real merge commit (parents=2)
    git checkout -q -b side HEAD~1; echo two > g; git add g
    git -c user.email=t@t -c user.name=t commit -q -m two
    git checkout -q -; git -c user.email=t@t -c user.name=t merge --no-ff -q side -m merge
    sham=$(git rev-parse HEAD); pc=$(( $(git rev-list --parents -n1 "$sham" | wc -w) - 1 ))
    [ "$pc" = 2 ] || exit 9
    # shellcheck disable=SC2046
    git -c user.email=t@t -c user.name=t revert $(_revert_git_args "$sham" "$pc") >/dev/null 2>&1
  ) || { echo "FAIL revert args rejected by git (this is the -q class of bug)"; rm -rf "$rvd"; exit 1; }
  rm -rf "$rvd"
  echo "_revert_git_args OK (incl. git actually accepting them)"
  # --- Task 3 (#347): _manifest_items preserves priority-manifest line order --
  local mdir2; mdir2=$(mktemp -d)
  printf '%s\n' "i2-b.md" "i10-a.md" "i1-c.md" > "$mdir2/.manifest"
  local out; out=$(_manifest_items "$mdir2/.manifest" "$mdir2")
  [ "$(printf '%s\n' "$out" | sed -n '1p')" = "$mdir2/i2-b.md" ]  || { echo "FAIL manifest order 1"; exit 1; }
  [ "$(printf '%s\n' "$out" | sed -n '2p')" = "$mdir2/i10-a.md" ] || { echo "FAIL manifest order 2"; exit 1; }
  [ "$(printf '%s\n' "$out" | sed -n '3p')" = "$mdir2/i1-c.md" ]  || { echo "FAIL manifest order 3 (must preserve file order, NOT sort)"; exit 1; }
  rm -rf "$mdir2"
  echo "_manifest_items OK"
  # --- decoupling: BUNDLE_DIR derivation (from script location) + path defaults ---
  local bdt; bdt=$(mktemp -d); mkdir -p "$bdt/batch"; : > "$bdt/batch/run-queue.sh"
  [ "$(_derive_bundle_dir "$bdt/batch/run-queue.sh")" = "$bdt" ] || { echo "FAIL bundle-dir derive"; exit 1; }
  # QUEUE/SCORECARD are already-bound globals (set at top of file), so a subshell
  # inherits them; `unset` them here so the ${VAR:-default} expansions actually
  # exercise the DEFAULT. Check the subshell exit status so a failure aborts
  # self_test (a bare `( ... )` would swallow the inner `exit 1`).
  ( unset QUEUE SCORECARD; BUNDLE_DIR=/tmp/xbundle
    [ "${QUEUE:-$BUNDLE_DIR/queue}" = "/tmp/xbundle/queue" ] || exit 1
    [ "${SCORECARD:-$BUNDLE_DIR/.run/scorecard.jsonl}" = "/tmp/xbundle/.run/scorecard.jsonl" ] || exit 1
    QUEUE=/tmp/override
    [ "${QUEUE:-$BUNDLE_DIR/queue}" = "/tmp/override" ] || exit 1
  ) || { echo "FAIL bundle-dir path defaults/override"; exit 1; }
  rm -rf "$bdt"
  echo "_bundle_dir OK"

  # --- _pr_is_abandoned: only a CLOSED-and-never-merged PR is abandoned --------
  # The i506 case: a scratch PR opened to PROVE a CI gate fails, then closed,
  # while the real PR is opened separately. Tracking the scratch one to the cap
  # reported outcome=failed against a closed PR while the real one sat green.
  _pr_is_abandoned "CLOSED" ""      || { echo "FAIL abandoned: closed+unmerged must be abandoned"; exit 1; }
  _pr_is_abandoned "CLOSED" "null"  || { echo "FAIL abandoned: closed + literal null mergedAt must be abandoned"; exit 1; }
  _pr_is_abandoned "CLOSED" "2026-08-04T20:42:03Z" && { echo "FAIL abandoned: a MERGED pr must never be abandoned"; exit 1; }
  _pr_is_abandoned "OPEN"   ""      && { echo "FAIL abandoned: an OPEN pr must never be abandoned"; exit 1; }
  _pr_is_abandoned "MERGED" "2026-08-04T20:42:03Z" && { echo "FAIL abandoned: MERGED state must never be abandoned"; exit 1; }
  # Unknown/empty state (gh failed) must NOT be treated as abandoned: discarding a
  # real PR on a transient gh error would be worse than tracking it one cycle more.
  _pr_is_abandoned "" ""            && { echo "FAIL abandoned: empty state must not be abandoned"; exit 1; }
  echo "_pr_is_abandoned OK"

  # --- _read_note: flatten, cap visibly, never split a character ---------------
  local ndir; ndir=$(mktemp -d)
  printf 'short reason\nsecond line\n' > "$ndir/a"
  [ "$(_read_note "$ndir/a")" = "short reason second line " ] \
    || { echo "FAIL read_note: newlines must flatten to spaces"; exit 1; }
  [ -z "$(_read_note "$ndir/missing")" ] \
    || { echo "FAIL read_note: a missing file must yield empty"; exit 1; }
  # Under the cap -> byte-identical, no marker.
  python3 -c "open('$ndir/b','w').write('x'*100)"
  [ "$(BIRCHER_NOTE_MAX=120 _read_note "$ndir/b")" = "$(python3 -c "print('x'*100)")" ] \
    || { echo "FAIL read_note: under-cap note must pass through unchanged"; exit 1; }
  # Over the cap -> truncated AND explicitly marked (the old head -c 300 was silent).
  python3 -c "open('$ndir/c','w').write('y'*500)"
  local out; out=$(BIRCHER_NOTE_MAX=100 _read_note "$ndir/c")
  case "$out" in
    *"[truncated 400 chars]") : ;;
    *) echo "FAIL read_note: over-cap note must be marked as truncated, got tail: ${out: -40}"; exit 1 ;;
  esac
  [ "${#out}" -gt 100 ] || { echo "FAIL read_note: marker must be appended, not counted in the cap"; exit 1; }
  # Multi-byte safety: cutting 10 chars of a 3-byte-per-char string must not
  # split a character (`head -c` would have). Round-trip through UTF-8 decode.
  python3 -c "open('$ndir/d','w',encoding='utf-8').write('世'*50)"
  BIRCHER_NOTE_MAX=10 _read_note "$ndir/d" > "$ndir/d.out"
  python3 -c "
import sys
d=open('$ndir/d.out','rb').read()
try: t=d.decode('utf-8')
except UnicodeDecodeError: print('FAIL read_note: split a multi-byte character'); sys.exit(1)
assert t.startswith('世'*10), 'expected 10 whole chars, got %r' % t[:14]
" || exit 1
  rm -rf "$ndir"
  echo "_read_note OK"

  echo "self-test OK"
}

# _install_work_git_config <workdir>: prepare the work repo before a run so no AI
# attribution reaches muesli/bircher/homelab history.
# (1) Commit identity: codex writes user.name=Codex / user.email=codex@example.com
#     into the work repo's LOCAL git config, and the squash merge turns that
#     branch-commit AUTHOR into a "Co-authored-by: Codex <...>" trailer on main.
#     Force the operator identity (matching the merge author, so GitHub derives no
#     co-author). Env-overridable via BIRCHER_GIT_AUTHOR_NAME/_EMAIL.
# (2) core.hooksPath -> the bundle commit-msg hook (defense in depth against any
#     message-level AI trailer). Absolute path so it covers every worktree.
_install_work_git_config() {
  local wd="$1"
  git -C "$wd" config user.name  "${BIRCHER_GIT_AUTHOR_NAME:-Abedegno}"                2>/dev/null || true
  git -C "$wd" config user.email "${BIRCHER_GIT_AUTHOR_EMAIL:-jon@jonwilliams.org.uk}" 2>/dev/null || true
  if [ -x "$BUNDLE_DIR/githooks/commit-msg" ]; then
    git -C "$wd" config core.hooksPath "$BUNDLE_DIR/githooks" 2>/dev/null || true
  fi
}

main() {
  if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'__HELP__'
run-queue.sh — the Bircher batch runner.

Works a GitHub Issues backlog: for each item it dispatches an implementer, has a
DIFFERENT vendor review the resulting branch, gates on CI, and merges.

USAGE
  bash batch/run-queue.sh            Drain the queue (a normal wave). Prefer
                                     batch/launch.sh, which detaches properly.

  bash batch/run-queue.sh --preflight
                                     Verify both vendors authenticate AND that
                                     omnigent can launch a worker for each.
                                     Different questions: a healthy CLI does not
                                     prove the harness can dispatch it.

  bash batch/run-queue.sh --usage    Live provider quota, and which vendor would
                                     be picked right now.

  bash batch/run-queue.sh --recover-pr <code> <pr> [reviewer]
                                     Adopt an orphaned PR, run a real cross-vendor
                                     review, merge on PASS. Does NOT write back to
                                     the issue: close it and drop bircher:escalated
                                     yourself afterwards.

  bash batch/run-queue.sh --self-test
                                     Built-in test suite. No network, no side
                                     effects. Validate on Linux.

  bash batch/run-queue.sh --help     This message.

KEY ENVIRONMENT
  BIRCHER_REPO         repo to work             (default abedegno/muesli)
  WORKDIR              local checkout of it     (default /workspaces/muesli)
  OMNIGENT_SERVER      omnigent server URL      (default http://omnigent:8000)
  BIRCHER_SOURCE       issues | queue           (default queue)
  BIRCHER_INRUN_MERGE  0 = review but never merge (default 1)

Issues flow bircher:queued -> bircher:running -> closed on merge, or
bircher:escalated when the runner declined to finish. Escalation is a normal
outcome rather than a crash: it means the run would not merge something it could
not verify.

Docs: https://github.com/abedegno/bircher
__HELP__
    exit 0
  fi
  [ "${1:-}" = "--self-test" ] && { self_test; exit 0; }
  # Standalone health check (no queue run): verify both providers can auth AND
  # can actually be dispatched through the harness, then exit.
  [ "${1:-}" = "--preflight" ] && { preflight_auth && preflight_dispatch; exit $?; }
  # Standalone usage readout (no queue run): print both providers' live signals
  # and the vendor _pick_implementer would choose right now, then exit. Operator
  # sanity check for the B-2/B-3 gate (5h_pct|5h_reset|7d_pct|7d_reset).
  if [ "${1:-}" = "--usage" ]; then
    local cu xu now
    cu=$(_claude_usage) || cu="-|-|-|-"; [ -z "$cu" ] && cu="-|-|-|-"
    xu=$(_codex_usage)  || xu="-|-|-|-"; [ -z "$xu" ] && xu="-|-|-|-"
    now=$(date +%s)
    echo "claude: $cu"
    echo "codex : $xu"
    echo "pick  : $(_pick_implementer "$(echo "$cu" | cut -d'|' -f1)" "$(echo "$cu" | cut -d'|' -f2)" "$(echo "$cu" | cut -d'|' -f3)" \
                                      "$(echo "$xu" | cut -d'|' -f1)" "$(echo "$xu" | cut -d'|' -f2)" "$(echo "$xu" | cut -d'|' -f3)" "$now") (FIVEH_MAX=$FIVEH_MAX)"
    exit 0
  fi

  # Standalone single-PR recovery (no queue run): adopt an existing orphaned PR,
  # run the cross-vendor recovery review, and merge on PASS -- when the kernel
  # authorizes it. Under BIRCHER_EFFECT_MODE=kernel a PR with no run behind it
  # has no recorded spec, plan, output, CI or review, so its merge is refused;
  # that is the gate working, not a failure of this command. See recover_pr_cmd.
  #   run-queue.sh --recover-pr <code> <pr> [reviewer_vendor]
  if [ "${1:-}" = "--recover-pr" ]; then
    recover_pr_cmd "${2:-}" "${3:-}" "${4:-}"; exit $?
  fi

  #   run-queue.sh --publish <code> <worktree> <branch> [claimed_oid]
  if [ "${1:-}" = "--publish" ]; then
    publish_cmd "${2:-}" "${3:-}" "${4:-}" "${5:-}"; exit $?
  fi

  # RC-1 singleton: only one batch may drain the queue at a time. The 2026-06-22
  # run had a second/restarted instance racing the same queue dir and moving
  # files out from under this loop. flock is advisory and released when this
  # process exits (FD 9 closes). If flock is unavailable, warn and proceed
  # rather than abort the whole run.
  local lock="${BIRCHER_BATCH_LOCK:-/tmp/bircher-batch.lock}"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$lock" || { echo "[batch] cannot open lock file $lock" >&2; exit 1; }
    if ! flock -n 9; then
      echo "[batch] another run-queue.sh already holds $lock; refusing to start a second instance" >&2
      exit 1
    fi
  else
    echo "[batch] WARN: flock not found; running without singleton protection" >&2
  fi

  # Item 2: fail fast if either provider's auth is dead/stale before we launch.
  preflight_auth || exit 2
  # ...and that the HARNESS can actually launch a worker for each vendor. Runs
  # ONCE here, deliberately not from preflight_auth: the per-item quota gate below
  # re-invokes preflight_auth before every launch, and a real dispatch probe there
  # would spawn two extra sessions per item.
  preflight_dispatch || exit 2

  # Clear stale no-op signals from any prior run (gap #3).
  mkdir -p "$NOOP_DIR"; rm -f "$NOOP_DIR"/*.noop "$NOOP_DIR"/*.escalated "$NOOP_DIR"/*.pr 2>/dev/null

  # REST launch: upload the agent bundle ONCE to mint a fresh session-scoped
  # agent (config edits activate here); every item's run session binds to it.
  local holder
  holder=$(_upload_bundle "$BUNDLE_DIR" "bircher bundle upload")
  [ -n "$holder" ] || { echo "[batch] FATAL: bundle upload failed" >&2; exit 3; }
  AGENT_ID=$(_get_agent_id "$holder")
  [ -n "$AGENT_ID" ] || { echo "[batch] FATAL: no agent_id from holder $holder" >&2; _prune_session "$holder"; exit 3; }
  echo "[batch] uploaded bundle -> agent=$AGENT_ID (holder $holder)"

  # Force the operator commit identity (codex's default Codex author otherwise
  # becomes a squash Co-authored-by trailer) + install the attribution-strip
  # commit-msg hook. No AI attribution in muesli/bircher/homelab.
  _install_work_git_config "$WORKDIR"
  echo "[batch] work-repo git identity + attribution hook set on $WORKDIR (author=${BIRCHER_GIT_AUTHOR_NAME:-Abedegno})" >&2

  shopt -s nullglob
  if [ "${BIRCHER_SOURCE:-queue}" = "issues" ]; then
    echo "[batch] source=issues: generating queue from bircher:queued issues" >&2
    bash "$BUNDLE_DIR/batch/issues-to-queue.sh" || { echo "[batch] issue->queue generation failed" >&2; exit 3; }
  fi
  local items
  if [ "${BIRCHER_SOURCE:-queue}" = "issues" ] && [ -f "$QUEUE/.manifest" ]; then
    local mout line; mout=$(_manifest_items "$QUEUE/.manifest" "$QUEUE")
    items=(); while IFS= read -r line; do [ -n "$line" ] && items+=("$line"); done <<< "$mout"
  else
    items=("$QUEUE"/*.md)
  fi
  if [ ${#items[@]} -eq 0 ]; then echo "[batch] queue empty"; exit 0; fi
  mkdir -p "$(dirname "$DEFERRED_READY_FILE")"; : > "$DEFERRED_READY_FILE"
  for f in "${items[@]}"; do
    local halt=0
    # Bounded across the whole retry loop for THIS item, unlike qwait below,
    # which is re-declared per iteration and so cannot bound anything.
    local send_fails=0 send_max="${BIRCHER_SEND_RETRIES:-2}"
    while :; do
      # B-2 quota gate: start-of-run preflight cannot protect a long run
      # (run #11). Probe BOTH providers before each launch (the probes also
      # FRESHEN both usage signals: any codex exec writes a rollout with
      # rate_limits; the claude probe updates the statusLine cache where
      # configured); on failure pause-and-reprobe without consuming items.
      local qwait=0 qmax="${BIRCHER_QUOTA_MAX_WAIT:-21600}"
      until SKIP_PREFLIGHT= PREFLIGHT_TIMEOUT=60 preflight_auth >/dev/null 2>&1; do
        if [ "$qwait" -ge "$qmax" ]; then
          echo "[batch] \!\!\!\! HALT: provider quota/auth still unhealthy after ${qmax}s - stopping (queue preserved) \!\!\!\!" >&2
          exit 4
        fi
        echo "[batch] quota gate: a provider is unhealthy (likely usage-window exhaustion); pausing 15m before reprobe (waited ${qwait}s)" >&2
        sleep 900; qwait=$((qwait + 900))
      done
      # B-3 usage-aware vendor pick. wait:<epoch> = both 5h windows hot ->
      # sleep until the sooner reset (+60s skew), bounded, then re-gate.
      PICKED_VENDOR="$IMPLEMENTER"
      if [ "$IMPLEMENTER" = "auto" ]; then
        local cu xu now pick
        cu=$(_claude_usage) || cu="-|-|-|-"
        xu=$(_codex_usage)  || xu="-|-|-|-"
        [ -z "$cu" ] && cu="-|-|-|-"; [ -z "$xu" ] && xu="-|-|-|-"
        now=$(date +%s)
        pick=$(_pick_implementer "$(echo "$cu" | cut -d"|" -f1)" "$(echo "$cu" | cut -d"|" -f2)" "$(echo "$cu" | cut -d"|" -f3)" \
                                 "$(echo "$xu" | cut -d"|" -f1)" "$(echo "$xu" | cut -d"|" -f2)" "$(echo "$xu" | cut -d"|" -f3)" "$now")
        if [ "${pick#wait:}" \!= "$pick" ]; then
          local dur=$(( ${pick#wait:} + 60 - now ))
          if [ "$dur" -gt "$qmax" ] || [ "$dur" -le 0 ]; then dur=900; fi
          echo "[batch] usage gate: both 5h windows >= ${FIVEH_MAX}% - sleeping ${dur}s until the sooner reset" >&2
          sleep "$dur"; continue
        fi
        PICKED_VENDOR="$pick"
        echo "[batch] usage gate: claude[$cu] codex[$xu] -> implementer=$PICKED_VENDOR" >&2
      fi
      run_item "$f"
      case $? in
        2) halt=1; break ;;
        3) echo "[batch] usage limit hit at item start; re-gating and retrying $f" >&2; continue ;;
        5) send_fails=$((send_fails + 1))
           if [ "$(_send_retry_decision "$send_fails" "$send_max")" = give-up ]; then
             echo "[batch] $f: prompt delivery failed $send_fails times -> giving up on this item; it stays QUEUED for the next run" >&2
             break
           fi
           echo "[batch] $f: prompt delivery failed ($send_fails/$send_max) -> retrying" >&2
           sleep 30; continue ;;
        *) break ;;
      esac
    done
    if [ "$halt" = 1 ]; then
      echo "[batch] \!\!\!\! HALT: main CI red/unresolved after an in-run merge - not launching further items (queue preserved for resume) \!\!\!\!" >&2
      break
    fi
  done
  if [ "${halt:-0}" != 1 ] && [ -s "$DEFERRED_READY_FILE" ]; then
    reconcile_deferred_ready
  fi
  # Deliberately NO holder prune here: deleting the holder cascade-deletes the
  # whole run's sessions (#1388) - run #11b's history was destroyed this way.
  # Holders accumulate (one per run) and are pruned manually only when a run's
  # history is disposable.
  echo "[batch] done; scorecard: $SCORECARD (holder $holder kept - owns this run's session history)"
}
main "$@"
