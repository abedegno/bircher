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
# its head sha and conclusion. Verified against the live API 2026-08-28.
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
      first_t = ""; first_c = ""
      for (i in finished) {
        split(finished[i], f, "|")
        if (first_t == "" || f[3] < first_t) { first_t = f[3]; first_c = f[2] }
      }
      d = 0; for (s in seen) d++
      printf "%s|%d", (first_c == "success" ? "true" : "false"), d - 1
    }'
}

# observe_review <pr> <reviewed_sha> -> "<verdict>|<log_path>"
#   verdict: PASS | FAIL | NONE
#
# DECISION 1 OF PHASE 2. The verdict that authorises a merge is now read from a
# reviewer run-queue dispatched, not from a string the coordinator wrote about
# a reviewer it dispatched. Both spellings produce `codex:pass`; only one of
# them observed anything.
#
# NONE is not a soft PASS. A reviewer that crashed, timed out, or produced no
# parseable verdict has approved nothing, and the classifier must route that to
# `escalated`. Reading silence as approval is how a merge gets authorised by an
# absence.
observe_review() {  # <pr> <reviewed_sha>
  local pr="$1" sha="$2" prompt out rc log v
  log="${BIRCHER_REVIEW_LOG:-/tmp/review-$pr.log}"
  # The sha travels IN the prompt: the reviewer is told what to read, never
  # asked to work it out. See #66 -- a reviewer that re-derives its own head
  # can bless a commit pushed after the review began.
  prompt=$(_recovery_review_prompt "$pr" "$sha")
  ( cd "$BUNDLE_DIR" && omnigent run "agents/$RECOVERY_REVIEWER" \
      --server "$SERVER" -p "$prompt" ) >"$log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    # A dead reviewer's stdout is not evidence. Mining it for "VERDICT: PASS"
    # would let a crash that happened to echo its own prompt authorise a merge.
    printf 'NONE|%s' "$log"
    return 0
  fi
  out=$(cat "$log" 2>/dev/null)
  v=$(_extract_verdict "$out")
  case "$v" in
    PASS|FAIL) printf '%s|%s' "$v" "$log" ;;
    *)         printf 'NONE|%s' "$log" ;;
  esac
}
