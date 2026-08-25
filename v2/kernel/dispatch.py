"""The dispatch record: who the kernel started, for which attempt.

This is the identity substrate. The kernel dispatches every attempt, so it
already knows whose work it is; identity is read from here and written into
commands by the kernel.

A session receives no identity token and needs none. omnigent's agent-env
allowlist admits only proxy/SSL/locale vars, HOME, PATH, TERM, TMPDIR,
NODE_EXTRA_CA_CERTS and a bare OMNIGENT=1 marker, and under the M1-1 egress
rules a session cannot reach the server. There is nothing to authenticate
WITH -- and an assigned identity cannot be forged at all, whereas a presented
one is only as good as its verification.

**Dispatch IS the acquisition.** A worker never acquires its own generation:
`acquire(run_id, owner)` takes its owner from the caller, so identity read
from it would be exactly as forgeable as a payload field, and a worker that
acquired a fresh generation would orphan the dispatch bound to the previous
one. The kernel fences and records who in one step; a generation obtained
any other way has no dispatched actor, and every command under it is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from kernel.events import EventKind
from kernel.ids import new_id
from kernel.ownership import acquire


class Role:
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    ALL = frozenset({IMPLEMENTER, REVIEWER, OPERATOR})


@dataclass(frozen=True)
class Dispatch:
    """What the kernel handed the worker: its fence, and who it thinks it is."""
    dispatch_id: str
    generation: int
    actor: str
    role: str


def dispatch(store, run_id: str, *, actor: str, role: str) -> Dispatch:
    """Fence a new attempt and bind *actor* to it."""
    if role not in Role.ALL:
        raise ValueError(f"unknown role: {role!r}; expected one of {sorted(Role.ALL)}")
    if not actor or not isinstance(actor, str):
        raise ValueError("an attempt must be dispatched to a named actor")
    generation = acquire(store, run_id, actor)
    did = new_id("dsp")
    store.record_dispatch(did, run_id, generation, actor, role)
    store.append_fact(
        run_id=run_id, kind=EventKind.ATTEMPT_DISPATCHED, actor="kernel",
        causal_command_id=None,
        payload={"dispatch_id": did, "generation": generation,
                 "actor": actor, "role": role},
    )
    return Dispatch(dispatch_id=did, generation=generation, actor=actor, role=role)


def actor_for(store, run_id: str, generation: int) -> str | None:
    """The actor the kernel dispatched for *generation*, or None."""
    return store.dispatch_actor(run_id, generation)


def role_for(store, run_id: str, generation: int) -> str | None:
    """The role the kernel dispatched *generation* in, or None."""
    return store.dispatch_role(run_id, generation)
