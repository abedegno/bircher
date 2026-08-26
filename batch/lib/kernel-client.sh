# The coordinator's interface to the v2 kernel.
#
# EVERY CALL HERE IS ADVISORY. A non-zero exit, a missing database, a Python
# traceback, an absent interpreter -- none of it may change what the run does.
# The kernel records; the coordinator decides. If that is ever not true, this
# file is where it broke.
#
# stdout is reserved for values the caller consumes (`_kernel_dispatch` echoes
# a generation). Diagnostics go to stderr, because call sites capture stdout
# and a stray line would corrupt whatever they were reading.

_kernel_warn() { echo "[batch:kernel] $*" >&2; }

# The v2 checkout, so `-m kernel.cli` and the dispatch heredoc below resolve
# `import kernel...` regardless of the coordinator's cwd. Both functions read
# the SAME variable so they agree on where the kernel package lives; the
# fallback is $BUNDLE_DIR/v2 because kernel-client.sh is sourced from inside
# run-queue.sh, which has already derived BUNDLE_DIR by the time it sources
# this file, and v2/ sits alongside batch/ in every checkout layout
# `_derive_bundle_dir` supports (flattened and nested).
#
# Without this, `python3 -m kernel.cli ...` fails "No module named kernel" on
# EVERY call from the coordinator's actual working directory -- and because
# every call here is advisory, that failure is swallowed exactly like a
# genuinely broken kernel would be. A green suite over that bug would prove
# nothing except that a broken client is easy to build.
_kernel_pythonpath() { printf '%s' "${BIRCHER_V2_DIR:-$BUNDLE_DIR/v2}"; }

# _kernel <subcommand> <args...>  -- always returns 0
#
# A shadow-mode refusal (exit 87, message prefixed "shadow-refused:") is a
# NORMAL outcome, not a failure: BIRCHER_KERNEL_MODE defaults to shadow, so
# most refusals take this path rather than raising, and it means the kernel
# evaluated the command exactly as enforcement would and recorded the
# refusal -- nothing failed. It is treated identically to success: no
# warning, nothing changed, rc 0. Every OTHER non-zero exit -- a missing
# database, a missing interpreter, an uncaught traceback, a real enforce-mode
# refusal -- warns, because those are exactly the states an operator needs to
# see the recorder is unhealthy in.
_kernel() {
  local sub="$1"; shift
  local out
  out=$( PYTHONPATH="$(_kernel_pythonpath)" \
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
_kernel_dispatch() {  # <actor> <role>
  local actor="$1" role="$2" gen=""
  gen=$( K_ACTOR="$actor" K_ROLE="$role" \
         BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
         "${BIRCHER_PY:-python3}" - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.dispatch import dispatch
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(dispatch(s, os.environ["BIRCHER_RUN_ID"],
               actor=os.environ["K_ACTOR"], role=os.environ["K_ROLE"]).generation)
PY
  ) || gen=""
  [ -n "$gen" ] || _kernel_warn "dispatch failed (advisory): $actor/$role"
  printf '%s' "$gen"
  return 0
}
