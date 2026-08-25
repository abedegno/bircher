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

    # Every attempt is observable, accepted or not. Recording only outcomes
    # means the audit cannot tell one attempt from forty (spec section 2 lists
    # "command requested" alongside accepted and rejected). A fact is an
    # observation, not a mutation, so this is also correct on the replay path.
    store.append_fact(
        run_id=cmd.run_id, kind=EventKind.COMMAND_REQUESTED, actor="kernel",
        causal_command_id=cmd.idempotency_key,
        payload={"name": cmd.name, "expected_version": cmd.expected_version},
    )

    # Replay BEFORE the halt gate: at-least-once delivery makes retries normal,
    # and an idempotent read of an already-accepted result must stay available
    # while the run is halted. Only genuinely new work is blocked.
    prior = store.command_result(cmd.idempotency_key, run_id=cmd.run_id)
    if prior is not None:
        if prior["name"] != cmd.name:
            # Same key, same run, different command. Not a retry -- a key
            # reused for different work. Answering it with the earlier result
            # would attribute that command's authority to this one.
            raise ValueError(
                f"idempotency key {cmd.idempotency_key!r} was already used in run "
                f"{cmd.run_id!r} for {prior['name']!r}, not {cmd.name!r}"
            )
        return Result(accepted=bool(prior["accepted"]), result=prior["result"], replayed=True)

    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        raise RuntimeError(
            f"run {cmd.run_id} is halted pending reconciliation; resolve it first"
        )

    if cmd.generation != current_generation(store, cmd.run_id):
        raise OwnershipLost(
            f"generation {cmd.generation} superseded; command carries no write capability"
        )

    # One transaction across the CAS, the fact and the command row. Three
    # independent commits left a crash window in which the version advanced
    # with no command row, so the client's retry got StaleVersion for a
    # command that had been accepted -- idempotency failing in exactly the
    # crash it exists to survive.
    result = {"name": cmd.name}
    try:
        with store.transaction():
            if not store.bump_version_cas(cmd.run_id, cmd.expected_version):
                raise StaleVersion(
                    f"{cmd.name} derived from version {cmd.expected_version}, "
                    "which has moved"
                )
            store.append_fact(
                run_id=cmd.run_id, kind=EventKind.COMMAND_ACCEPTED, actor="kernel",
                causal_command_id=cmd.idempotency_key,
                # Payload is NESTED, not splatted: splatting let a payload key
                # named like one of the command's own fields silently overwrite
                # it in the recorded fact.
                payload={"command_name": cmd.name, "generation": cmd.generation,
                         "payload": cmd.payload},
            )
            store.record_command(
                cmd.idempotency_key, cmd.run_id, cmd.name, True, result
            )
    except StaleVersion:
        # Outside the transaction: the rejection must survive the rollback.
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.COMMAND_REJECTED, actor="kernel",
            causal_command_id=cmd.idempotency_key,
            payload={"command_name": cmd.name, "reason": "stale_version",
                     "expected_version": cmd.expected_version},
        )
        raise

    return Result(accepted=True, result=result)
