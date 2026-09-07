"""The dispatch record: who the kernel started, for which attempt.

This is the identity substrate. The kernel dispatches every attempt, so it
already knows whose work it is; identity is read from here and written into
commands by the kernel.

A session receives no identity token and needs none. omnigent's agent-env
allowlist admits only proxy/SSL/locale vars, HOME, PATH, TERM, TMPDIR,
NODE_EXTRA_CA_CERTS and a bare OMNIGENT=1 marker, and under the M1-1 egress
rules a session cannot reach the server. There is nothing to authenticate
WITH.

**And an assigned identity is only as good as the restriction on who may
assign it.** An earlier version of this docstring claimed it "cannot be forged
at all"; that is false, and round 6 demonstrated it. `dispatch()` takes the
actor as a caller-supplied string, so whoever can call it names BOTH sides of
the independence check -- one actor dispatched itself as `mallory` and
`mallory-the-reviewer`, passed independence, and reached `merge_requested`.
That is the payload-field defect displaced one level, not removed.

What IS true is narrower: identity is unforgeable *by a model session*,
because a model session cannot write the kernel database. Landlock's
`write_paths` confines its writes to its worktree, and `BIRCHER_KERNEL_DB` is
required with no default, so the database never lands inside one. The property
is "only the coordinator can assign identity", it rests on those two settings,
and `tests/kernel/test_identity_precondition.py` is what checks them.

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
    AUTHOR = "author"
    ALL = frozenset({IMPLEMENTER, REVIEWER, OPERATOR, AUTHOR})
    #: The two roles the front half's seat budget counts (spec §1 max_seats).
    SEATS = frozenset({AUTHOR, REVIEWER})


class PendingEffects(Exception):
    """A dispatch over an intended or uncertain effect (spec §2 Refusals:
    'any dispatch when the run holds an intended or uncertain effect')."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"run holds unresolved effects: {keys}")
        self.keys = keys


class SeatsExhausted(Exception):
    """Front-half dispatches have reached max_seats plus granted rounds."""


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
    if actor == "human":
        raise ValueError(
            "'human' is not a dispatchable actor: human commands go through "
            "execute_as_human and carry no generation"
        )
    # Before the transaction, so the halt survives the raise: a run holding an
    # intended or uncertain effect is halted with those keys as evidence and
    # the dispatch refused (spec §2 Refusals, §5).
    pending = store.uncertain_effects(run_id)
    if pending:
        keys = [e["idempotency_key"] for e in pending]
        store.set_reconciliation(run_id, {
            "run_id": run_id, "generation": None,
            "affected_resources": sorted({e["effect_class"] for e in pending}),
            "pending_keys": keys,
            "last_confirmed_observations": store.last_confirmed(run_id),
            "stop_attempts": 0,
            "recommended_actions": [
                "A dispatch was attempted over unresolved effects; check each key externally",
                "Then reconcile (--delivered / --not-delivered per key) and resume",
            ],
        })
        raise PendingEffects(keys)
    # ONE transaction across the fence and the record. Two statements left a
    # window in which a failure between them fenced a generation with no actor
    # behind it: every command under it is then refused for having no
    # dispatched actor, and the resulting state is indistinguishable from a
    # caller self-fencing. M1-2 put submit()'s three writes in one transaction
    # for exactly this reason; dispatch was written afterwards and did not.
    did = new_id("dsp")
    with store.transaction():
        if role in Role.SEATS:
            from kernel.front import seat_bound, seats_used
            used, bound = seats_used(store, run_id), seat_bound(store, run_id)
            if used >= bound:
                raise SeatsExhausted(
                    f"run {run_id} has used {used} of {bound} front-half seats"
                )
        generation = acquire(store, run_id, actor)
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
