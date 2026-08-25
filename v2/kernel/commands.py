"""The seven typed commands and centralized authorization.

A narrow interface, not a general one. Every command carries the aggregate
version it was derived from and the generation that requested it; both are
checked before anything mutates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kernel.events import EventKind
from kernel.ownership import OwnershipLost, current_generation

COMMAND_NAMES = frozenset({
    "submit_spec", "submit_plan", "record_review", "start_implementation",
    "record_ci_observation", "request_merge", "cancel_run",
})


class StaleVersion(Exception):
    """The command was derived from an aggregate version that has since moved."""


@dataclass(frozen=True)
class Command:
    name: str
    run_id: str
    expected_version: int
    idempotency_key: str
    generation: int
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    accepted: bool
    result: dict
    replayed: bool = False


def submit(store, cmd: Command) -> Result:
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")

    from kernel.effects import is_halted

    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        raise RuntimeError(
            f"run {cmd.run_id} is halted pending reconciliation; resolve it first"
        )

    prior = store.command_result(cmd.idempotency_key)
    if prior is not None:
        # Replay: return the original outcome and mutate nothing. The version
        # must not advance, or a retry would consume a version.
        return Result(accepted=bool(prior["accepted"]), result=prior["result"], replayed=True)

    if cmd.generation != current_generation(store, cmd.run_id):
        raise OwnershipLost(
            f"generation {cmd.generation} superseded; command carries no write capability"
        )

    # The CAS. rowcount 0 means the aggregate moved under us.
    cur = store._conn.execute(
        "UPDATE runs SET version = version + 1 WHERE run_id = ? AND version = ?",
        (cmd.run_id, cmd.expected_version),
    )
    if cur.rowcount == 0:
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.COMMAND_REJECTED, actor="kernel",
            causal_command_id=cmd.idempotency_key,
            payload={"name": cmd.name, "reason": "stale_version",
                     "expected_version": cmd.expected_version},
        )
        raise StaleVersion(
            f"{cmd.name} derived from version {cmd.expected_version}, which has moved"
        )

    store.append_fact(
        run_id=cmd.run_id, kind=EventKind.COMMAND_ACCEPTED, actor="kernel",
        causal_command_id=cmd.idempotency_key,
        payload={"name": cmd.name, "generation": cmd.generation, **cmd.payload},
    )
    result = {"name": cmd.name}
    store.record_command(cmd.idempotency_key, cmd.run_id, cmd.name, True, result)
    return Result(accepted=True, result=result)
