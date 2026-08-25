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

_effect() {
  local class="$1" key="$2" cap="$3"; shift 3
  case "${BIRCHER_EFFECT_MODE:-deny}" in
    kernel)
      local -a kcmd=(
        "${BIRCHER_PY:-python3}" -m kernel.cli
        --db "${BIRCHER_KERNEL_DB:?BIRCHER_KERNEL_DB must be set in kernel mode}"
        --run-id "${BIRCHER_RUN_ID:?BIRCHER_RUN_ID must be set in kernel mode}"
        --generation "${BIRCHER_GENERATION:?BIRCHER_GENERATION must be set in kernel mode}"
        --class "$class" --idempotency-key "$key" --
      )
      if [ "$cap" = "-" ]; then "${kcmd[@]}" "$@"
      else _net_run "$cap" "${kcmd[@]}" "$@"; fi
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
