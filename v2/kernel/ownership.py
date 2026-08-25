"""Atomic ownership acquisition with a monotonic fence generation.

Acquisition is a compare-and-swap, not a write: "ownership recorded" is not
exclusion, and two attempts must never believe they hold the same generation.
Dispatch is tied to the generation acquired here, and every effect request is
checked against it.
"""

from __future__ import annotations

from kernel.events import EventKind


class OwnershipLost(Exception):
    """Raised when an operation is attempted under a superseded generation."""


def acquire(store, run_id: str, owner: str) -> int:
    """Increment the fence generation atomically and return the new value.

    The UPDATE is the CAS: it reads and writes owner_generation in one
    statement, so concurrent callers serialise and each observes a distinct
    generation. A read-then-write would let two callers read the same value.
    """
    generation = store.acquire_generation(run_id, owner)
    if generation is None:
        raise KeyError(f"no such run: {run_id}")
    store.append_fact(
        run_id=run_id,
        kind=EventKind.OWNERSHIP_ACQUIRED,
        actor=owner,
        causal_command_id=None,
        payload={"generation": generation, "owner": owner},
    )
    return generation


def current_generation(store, run_id: str) -> int:
    generation = store.current_generation(run_id)
    if generation is None:
        raise KeyError(f"no such run: {run_id}")
    return generation
