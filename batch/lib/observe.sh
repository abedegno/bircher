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

# _coordinator <subcommand> [args...] -> the coordinator package's stdout.
#
# The same shape as `_kernel_*`: bash reaches Python through a subprocess.
# TEMPORARY, and the temporariness is the point -- as run_item moves into
# v2/coordinator/ the callers become Python and this helper disappears with
# the file it lives in.
_coordinator() {
  # STDERR IS NOT SUPPRESSED. An earlier version of this helper ended
  # `2>/dev/null`, which swallowed every reason the package had for failing --
  # and silently ate `verdict`'s warning that a review's final line was not a
  # bare verdict, a message an operator needs to tell "the reviewer never ran"
  # from "the reviewer rambled". `--self-test` caught it.
  #
  # Callers that genuinely want quiet redirect at their own call site. A helper
  # that hides the reason from all of them is the shape this project keeps
  # finding on the wrong end of a diagnosis.
  PYTHONPATH="$(_kernel_pythonpath)" \
    _net_run "$(_kernel_net_cap)" \
    "${BIRCHER_PY:-python3}" -m coordinator.cli "$@"
}

# observe_ci_history <branch> -> "<ci_first>|<resubmissions>"
#
# Now a thin call. The logic -- earliest finished run decides `ci_first`,
# distinct commits decide `resubmissions`, and `unknown` never collapses into
# `false` -- lives in v2/coordinator/observe.py with nineteen native tests. It
# was written here in bash first and tested by extracting this function from
# the script and driving it with stubs; that harness was the tell.
observe_ci_history() {  # <branch>
  local out=""
  out=$(_coordinator ci-history --repo "$REPO" --branch "$1") || out=""
  # A failed call is `unknown|`, never a fabricated `false|0`.
  [ -n "$out" ] || out="unknown|"
  printf '%s' "$out"
}
