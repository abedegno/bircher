"""Performing an effect from Python.

The one migration step where a mistake means effects execute UNJOURNALLED, so
it follows a written design rather than being improvised:
`docs/superpowers/specs/2026-08-29-coordinator-effect-path-design.md`.

Both entry points -- this and `batch/lib/effect-adapter.sh` -- call the same
`perform()`, write to the same database and bind to the same run and
generation, so a run whose effects come partly from each still produces ONE
coherent journal. That is what makes the migration safe to do incrementally.
"""

from __future__ import annotations

import os
import subprocess

from coordinator.effect_mode import DENY, KERNEL, LEGACY, effect_mode


class EffectDenied(Exception):
    """The operator asked for nothing to happen, and nothing did."""


class NotDispatched(Exception):
    """No run or generation to bind the effect to."""


def _required(env, name: str) -> str:
    """Mirrors the adapter's `${VAR:?}`.

    A Python default of None here would journal an effect against no attempt --
    the fence exists precisely so a late or foreign result cannot claim one.
    """
    v = (env.get(name) or "").strip()
    if not v:
        raise NotDispatched(f"{name} is required in kernel mode")
    return v


def _run_unjournalled(argv, timeout) -> str:
    """`legacy`: run it, record nothing, and do not touch the kernel."""
    r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"legacy effect failed rc={r.returncode}: "
                           f"{r.stderr.strip()[:200]}")
    return r.stdout.strip() or "ok"


def perform_effect(effect_class: str, key: str, argv, *, timeout=None, env=None) -> str:
    """Perform one externally visible mutation, per the configured mode."""
    env = os.environ if env is None else env
    mode = effect_mode(env)

    if mode == DENY:
        # NOT a journalled refusal. The bash adapter does not reach the kernel
        # under `deny`, and a Python path that did would put facts in a
        # database the operator asked to leave alone.
        raise EffectDenied(f"effect refused: {effect_class} {key}")

    if mode == LEGACY:
        return _run_unjournalled(argv, timeout)

    # Imported HERE, not at module scope: `legacy` must work when the kernel is
    # unimportable, which is the situation it exists to diagnose.
    from kernel.cli import _executor
    from kernel.effects import perform
    from kernel.store import Store

    assert mode == KERNEL
    store = Store.open(_required(env, "BIRCHER_KERNEL_DB"))
    return perform(store, _required(env, "BIRCHER_RUN_ID"),
                   int(_required(env, "BIRCHER_GENERATION")),
                   effect_class, key, {"argv": list(argv)}, _executor)
