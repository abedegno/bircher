"""Shadow or enforce, for both command authorization and the argv contract.

Enforcement turned on because a test suite passes is a claim outrunning its
evidence. Shadow produces evidence: every guard is evaluated exactly as it
would be, and a refusal becomes a fact instead of an outcome.

The default is `shadow`, which is deliberately the OPPOSITE of
BIRCHER_EFFECT_MODE's `deny`. That switch answers "may this mutation happen at
all", where failing closed is right. This one answers "is the kernel's model of
the run correct yet", where failing closed stops a working runner over a
modelling bug.
"""

from __future__ import annotations

import os

from kernel.events import EventKind

SHADOW = "shadow"
ENFORCE = "enforce"
_MODES = (SHADOW, ENFORCE)


def kernel_mode() -> str:
    """The configured mode. An unrecognised value raises rather than
    defaulting: a typo that silently meant `shadow` would disable every guard
    without saying so."""
    mode = os.environ.get("BIRCHER_KERNEL_MODE", SHADOW)
    if mode not in _MODES:
        raise ValueError(
            f"BIRCHER_KERNEL_MODE={mode!r} is not one of {list(_MODES)}"
        )
    return mode


def shadow_or_raise(store, run_id: str, exc: Exception, **context) -> None:
    """In enforce, re-raise. In shadow, record and return.

    The fact is appended BEFORE the caller proceeds, so a crash mid-command
    still leaves the refusal recorded -- the runs worth studying are exactly
    the ones that go wrong.
    """
    if kernel_mode() == ENFORCE:
        raise exc
    store.append_fact(
        run_id=run_id, kind=EventKind.SHADOW_REJECTED, actor="kernel",
        causal_command_id=None,
        payload={"error": type(exc).__name__, "reason": str(exc)[:400], **context},
    )
