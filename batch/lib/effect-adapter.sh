# The single seam every externally visible mutation passes through.
#
# The coordinator keeps its orchestration -- that is where the scars live --
# and loses its authority. Nothing below decides whether an effect is allowed;
# it decides only WHO decides, and in kernel mode that is the kernel.
#
#   _effect <class> <idempotency-key> <cap> <argv...>
#
# BIRCHER_EFFECT_MODE:
#   kernel  - delegate to the v2 kernel, which journals intent before acting
#   deny    - refuse every effect (fault injection; the constrained mode)
#   legacy  - v1 behaviour, direct execution. Retained only for bisection.
#
# WHAT LEGACY COSTS. It runs the command directly, so it bypasses ALL of the
# kernel path: no argv contract, no executable resolution, no effect journal,
# no authorization recheck. A run in legacy mode has none of the guarantees
# rounds 6 and 7 built. That is deliberate -- the mode exists to compare
# against v1 -- but it is an escape hatch, and
# test_fault_injection.py::test_legacy_mode_bypasses_the_entire_kernel_path
# pins the fact so narrowing or removing it is a visible change rather than a
# silent one.
#
# `run-queue.sh --self-test` sets this to legacy, so a green self-test says
# nothing about the v2 boundary. The two are easily confused.
#
# Default is deny. An unset variable must not silently restore v1 authority:
# fail closed, in the direction that stops work rather than the one that
# performs an unmediated mutation.
#
# WHY THE CAP IS AN ARGUMENT. Every routed site is wrapped in
# `_net_run "$cap"`, which runs `timeout -k GRACE CAP` -- and `timeout` needs a
# real executable, so neither `_net_run "$cap" _effect ...` (a shell function)
# nor `_effect ... _net_run "$cap" ...` (the kernel would exec a function name)
# works. Taking the cap here keeps `_net_run` wrapping the process that
# actually performs the network call, in kernel and legacy mode alike. That
# matters: #62's scar is that `timeout` WITHOUT -k is not a bound at all
# against a push stuck in credential negotiation, and porting the call while
# dropping -k would satisfy the letter and lose the point.
#
# A cap of `-` means UNBOUNDED, which is what v1 does at the sites that were
# never wrapped. It is spelled out rather than defaulted so those sites stay
# visible: they are the ones a later task should bound, and a silent default
# would hide them.

_EFFECT_RC_DENIED=87      # refused by the adapter
_EFFECT_RC_BADMODE=2      # unknown mode

# Where `-m kernel.cli` is imported from.
#
# THE SCAR. Without this, kernel mode invokes `python3 -m kernel.cli` with
# whatever PYTHONPATH the coordinator happened to have -- which is none --
# and every effect dies with `No module named kernel` before it reaches the
# kernel at all. In kernel mode `_effect` is NOT advisory: it is the
# execution path, so that failure does not merely lose the recording, it
# loses the effect. It took down prompt delivery in the first live
# acceptance run.
#
# The identical defect was found and fixed in `_kernel`
# (batch/lib/kernel-client.sh) before it ever ran; the fix was applied to
# that instance and the class was assumed closed. It was not: this file had
# the same shape and no test that could see it.
#
# Precedence matches `_kernel_pythonpath` for the two cases that helper
# handles -- BIRCHER_V2_DIR, then BUNDLE_DIR/v2 -- and then DIVERGES: this one
# adds a self-locating fallback for callers (and tests) that set neither
# variable, resolving <bundle>/v2 from this file's own location two
# directories up.
#
# So "the two agree" is false precisely where it would matter, and saying they
# agree would be the same shape of unearned claim this file exists to fix. With
# neither variable set, `_kernel_pythonpath` yields a bare "/v2" and this
# yields a real path. That divergence is deliberate -- `_effect` is the
# EXECUTION path in kernel mode and must not depend on a caller remembering to
# export something -- but it is a difference, not an agreement.
_effect_pythonpath() {
  if [ -n "${BIRCHER_V2_DIR:-}" ]; then printf '%s' "$BIRCHER_V2_DIR"
  elif [ -n "${BUNDLE_DIR:-}" ]; then printf '%s' "$BUNDLE_DIR/v2"
  else printf '%s' "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/v2"
  fi
}

_effect() {
  local class="$1" key="$2" cap="$3"; shift 3
  case "${BIRCHER_EFFECT_MODE:-deny}" in
    kernel)
      local -a kcmd=(
        "${BIRCHER_PY:-python3}" -m kernel.cli effect
        --db "${BIRCHER_KERNEL_DB:?BIRCHER_KERNEL_DB must be set in kernel mode}"
        --run-id "${BIRCHER_RUN_ID:?BIRCHER_RUN_ID must be set in kernel mode}"
        --generation "${BIRCHER_GENERATION:?BIRCHER_GENERATION must be set in kernel mode}"
        --class "$class" --idempotency-key "$key" --
      )
      if [ "$cap" = "-" ]; then PYTHONPATH="$(_effect_pythonpath)" "${kcmd[@]}" "$@"
      else PYTHONPATH="$(_effect_pythonpath)" _net_run "$cap" "${kcmd[@]}" "$@"; fi
      ;;
    deny)
      echo "effect refused: $class $key ($*)" >&2
      return $_EFFECT_RC_DENIED
      ;;
    legacy)
      if [ "$cap" = "-" ]; then "$@"
      else _net_run "$cap" "$@"; fi
      ;;
    *)
      echo "unknown BIRCHER_EFFECT_MODE: ${BIRCHER_EFFECT_MODE}" >&2
      return $_EFFECT_RC_BADMODE
      ;;
  esac
}
