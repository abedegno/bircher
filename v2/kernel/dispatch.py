"""The dispatch record: who the kernel started, for which attempt.

This is the identity substrate. The kernel dispatches every attempt, so it
already knows whose work it is; identity is read from here and written into
commands by the kernel.

A session receives no identity token and needs none. omnigent's agent-env
allowlist admits only proxy/SSL/locale vars, HOME, PATH, TERM, TMPDIR,
NODE_EXTRA_CA_CERTS and a bare OMNIGENT=1 marker, the runner's own auth
secrets are stripped at every spawn boundary, and under the M1-1 egress rules
a session cannot reach the server. There is nothing to authenticate WITH --
and an assigned identity cannot be forged at all, whereas a presented one is
only as good as its verification.
"""

from __future__ import annotations

from kernel.events import EventKind
from kernel.ids import new_id
from kernel.ownership import current_generation


class Role:
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    ALL = frozenset({IMPLEMENTER, REVIEWER, OPERATOR})


def dispatch(store, run_id: str, *, actor: str, role: str) -> str:
    """Bind *actor* to the run's current generation."""
    if role not in Role.ALL:
        raise ValueError(f"unknown role: {role!r}; expected one of {sorted(Role.ALL)}")
    if not actor:
        raise ValueError("an attempt must be dispatched to a named actor")
    generation = current_generation(store, run_id)
    did = new_id("dsp")
    store.record_dispatch(did, run_id, generation, actor, role)
    store.append_fact(
        run_id=run_id, kind=EventKind.ATTEMPT_DISPATCHED, actor="kernel",
        causal_command_id=None,
        payload={"dispatch_id": did, "generation": generation,
                 "actor": actor, "role": role},
    )
    return did


def actor_for(store, run_id: str, generation: int) -> str | None:
    """The actor the kernel dispatched for *generation*, or None."""
    return store.dispatch_actor(run_id, generation)


def actor_in_role(store, run_id: str, role: str) -> str | None:
    """The actor most recently dispatched in *role*."""
    return store.dispatch_role_actor(run_id, role)
