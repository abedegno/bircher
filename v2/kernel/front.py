"""Front-half journal queries (spec §1-§2).

Every function here derives its answer from facts and satisfied effects.
None reads a caller's payload. Tasks 7-12 add to this module; nothing in it
writes.
"""
from __future__ import annotations

from kernel.events import EventKind
from kernel.policy import policy_of


def seats_used(store, run_id: str) -> int:
    from kernel.dispatch import Role
    return sum(1 for d in store.dispatches_for(run_id) if d["role"] in Role.SEATS)


def grants(store, run_id: str) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING)
        if f.payload.get("ruling") == "grant_round"
    )


def seat_bound(store, run_id: str) -> int:
    """Ruling 4: a granted round is one author seat and one reviewer seat."""
    return policy_of(store, run_id).max_seats + 2 * grants(store, run_id)
