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
