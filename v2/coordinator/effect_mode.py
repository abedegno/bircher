"""What `BIRCHER_EFFECT_MODE` means, in one place.

The switch itself has to live at the outermost caller in each language --
`legacy` exists to run WITHOUT the kernel, so it cannot be routed through the
kernel to share an implementation. What must not be duplicated is this: the
three names, what each means, and the default.

`test_effect_mode.py` parses `batch/lib/effect-adapter.sh` and requires it to
implement exactly these arms with exactly this default, so the two entry points
cannot drift apart into one that journals and one that does not.
"""

from __future__ import annotations

import os

#: Refuse. THE DEFAULT, and deliberately the opposite of `kernel_mode`'s
#: `enforce`: this switch answers "may this mutation happen at all", where
#: failing closed is right. `kernel_mode` answers "is the kernel's model of the
#: run correct yet", where failing closed would stop a working runner over a
#: modelling bug.
DENY = "deny"

#: Run the command directly, journalling nothing. v1 behaviour, kept as the
#: bisecting tool for a SUSPECTED KERNEL FAULT -- which is exactly why it must
#: not require the kernel to be reachable.
LEGACY = "legacy"

#: Argv contract, authorization, journal. What a wave runs in.
KERNEL = "kernel"

MODES = (DENY, LEGACY, KERNEL)


class UnknownMode(ValueError):
    """An unrecognised mode. Never defaulted."""


def effect_mode(env=None) -> str:
    """The configured mode.

    An unrecognised value RAISES rather than defaulting. A typo that silently
    meant `deny` would look like a working boundary while performing nothing;
    one that silently meant `legacy` would perform everything and journal none
    of it. Both are worse than stopping.
    """
    env = os.environ if env is None else env
    mode = env.get("BIRCHER_EFFECT_MODE") or DENY
    if mode not in MODES:
        raise UnknownMode(f"unknown BIRCHER_EFFECT_MODE: {mode!r}")
    return mode
