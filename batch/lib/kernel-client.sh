# The coordinator's interface to the v2 kernel.
#
# EVERY CALL HERE IS ADVISORY. A non-zero exit, a missing database, a Python
# traceback, an absent interpreter, A HUNG PROCESS -- none of it may change
# what the run does. The kernel records; the coordinator decides. If that is
# ever not true, this file is where it broke.
#
# A hang is the sharpest version of that failure: every OTHER failure still
# lets the caller move on with a non-zero exit code to react to. A process
# that never returns turns "the run completes" into "the run stalls forever
# at this line", which changes a run's outcome more completely than any exit
# code could. Both functions below are bounded by `_net_run` for exactly
# this reason -- see `_kernel_net_cap`.
#
# stdout is reserved for values the caller consumes (`_kernel_dispatch` echoes
# a generation). Diagnostics go to stderr, because call sites capture stdout
# and a stray line would corrupt whatever they were reading.

_kernel_warn() { echo "[batch:kernel] $*" >&2; }

# The v2 checkout, so `-m kernel.cli` and `_kernel_dispatch`'s python source
# below resolve `import kernel...` regardless of the coordinator's cwd. Both
# functions read the SAME variable so they agree on where the kernel package
# lives; the fallback is $BUNDLE_DIR/v2 because kernel-client.sh is sourced
# from inside run-queue.sh, which has already derived BUNDLE_DIR by the time
# it sources this file, and v2/ sits alongside batch/ in every checkout
# layout `_derive_bundle_dir` supports (flattened and nested).
#
# Without this, `python3 -m kernel.cli ...` fails "No module named kernel" on
# EVERY call from the coordinator's actual working directory -- and because
# every call here is advisory, that failure is swallowed exactly like a
# genuinely broken kernel would be. A green suite over that bug would prove
# nothing except that a broken client is easy to build.
_kernel_pythonpath() { printf '%s' "${BIRCHER_V2_DIR:-$BUNDLE_DIR/v2}"; }

# The wall-clock bound on every kernel call, in seconds. This is a local
# sqlite write with no network I/O, so 20s is generous rather than tight --
# a cold interpreter start on a loaded box should still finish in well under
# a second -- but it is still a BOUND: a hung python now returns control to
# the coordinator in 20s instead of never. Overridable so a caller (or a
# test) can shrink it rather than wait out the default.
_kernel_net_cap() { printf '%s' "${BIRCHER_KERNEL_TIMEOUT:-20}"; }

# _kernel <subcommand> <args...>  -- always returns 0
#
# Routed through `_net_run` -- batch/run-queue.sh's existing network-call
# bound, already wrapping every other kernel-owned effect via `_effect` in
# effect-adapter.sh -- rather than a second bounding mechanism invented here.
# `_net_run` is not defined in this file; it resolves at call time, exactly
# like `_effect`'s own use of it, so kernel-client.sh being sourced before
# `_net_run` is defined later in run-queue.sh does not matter.
#
# A timeout is not a distinct case: `_net_run` returns non-zero exactly like
# a crashed python would, so it falls straight into the same failure handling
# below -- warn (unless it's a shadow refusal), return 0, change nothing.
#
# A shadow-mode refusal (exit 87, message prefixed "shadow-refused:") is a
# NORMAL outcome, not a failure: BIRCHER_KERNEL_MODE defaults to shadow, so
# most refusals take this path rather than raising, and it means the kernel
# evaluated the command exactly as enforcement would and recorded the
# refusal -- nothing failed. It is treated identically to success: no
# warning, nothing changed, rc 0. Every OTHER non-zero exit -- a missing
# database, a missing interpreter, an uncaught traceback, a timeout, a real
# enforce-mode refusal -- warns, because those are exactly the states an
# operator needs to see the recorder is unhealthy in.
_kernel() {
  local sub="$1"; shift
  local out
  out=$( PYTHONPATH="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m kernel.cli "$sub" \
           --db "${BIRCHER_KERNEL_DB:-}" "$@" 2>&1 >/dev/null ) && return 0
  case "$out" in
    shadow-refused:*) ;;
    *) _kernel_warn "call failed (advisory): $sub $* -- $out" ;;
  esac
  return 0
}

# _kernel_dispatch <actor> <role> -- echoes the new generation, or nothing.
#
# A role change is a NEW dispatch and a dispatch re-fences the generation, so
# every caller re-reads it. Reusing a stale generation would fence the run out
# of its own kernel record.
#
# The python source is passed as a `-c` ARGUMENT, not piped in over stdin via
# a heredoc as an earlier draft did. Two independent reasons, either one
# sufficient on its own:
#
#   1. Bash drops a heredoc's content when the command it feeds is
#      BACKGROUNDED from inside a function (or any separate process) rather
#      than run in the foreground of the current shell -- the backgrounded
#      reader sees immediate EOF instead of the heredoc body. `_net_run`
#      backgrounds its command internally (real GNU `timeout`, or the
#      fork/kill stub tests use in place of it), so a heredoc here would
#      silently execute an EMPTY python program under `_net_run` -- dispatch
#      would always fail, quietly, only once bounded.
#   2. This sandbox refuses to create the temp file a heredoc needs at all
#      ("cannot create temp file for here document: Operation not
#      permitted"), independent of backgrounding.
#
# An argv has neither problem: it survives being backgrounded through any
# wrapper, and needs no temp file.
_kernel_dispatch() {  # <actor> <role>
  local actor="$1" role="$2" gen=""
  local src='
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.dispatch import dispatch
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(dispatch(s, os.environ["BIRCHER_RUN_ID"],
               actor=os.environ["K_ACTOR"], role=os.environ["K_ROLE"]).generation)
'
  gen=$( K_ACTOR="$actor" K_ROLE="$role" \
         BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c "$src" 2>/dev/null
  ) || gen=""
  [ -n "$gen" ] || _kernel_warn "dispatch failed (advisory): $actor/$role"
  printf '%s' "$gen"
  return 0
}
