"""The seven typed commands and centralized authorization.

A narrow interface, not a general one. Every command carries the aggregate
version it was derived from and the generation that requested it; both are
checked before anything mutates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kernel.artifacts import binding_hash
from kernel.authz import authorize, validate_review
from kernel.canon import canonical_hash
from kernel.events import EventKind
from kernel.ownership import OwnershipLost, current_generation

COMMAND_NAMES = frozenset({
    "submit_spec", "submit_plan", "record_review", "start_implementation",
    "record_ci_observation", "request_merge", "cancel_run",
    # Added with the merge-outcome transition: merge_requested was a dead end,
    # and cancel_run was the only escape -- which misreports a run that merged.
    "record_merge_outcome",
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


def _record_rejection(store, cmd, reason: str, detail: str) -> None:
    """Append the immutable record of a refusal.

    Outside any transaction: a rejection must survive whatever rolled back.
    """
    store.append_fact(
        run_id=cmd.run_id, kind=EventKind.COMMAND_REJECTED, actor="kernel",
        causal_command_id=cmd.idempotency_key,
        payload={"command_name": cmd.name, "reason": reason, "detail": detail[:300]},
    )


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
        request_hash = canonical_hash(
            {"name": cmd.name, "run_id": cmd.run_id, "payload": cmd.payload}
        )
        if prior["name"] != cmd.name or prior["request_hash"] != request_hash:
            # Same key, same run, different command. Not a retry -- a key
            # reused for different work. Answering it with the earlier result
            # would attribute that command's authority to this one.
            raise ValueError(
                f"idempotency key {cmd.idempotency_key!r} was already used in run "
                f"{cmd.run_id!r} for a different request "
                f"(stored {prior['name']!r}, now {cmd.name!r}); a key identifies "
                "one request, not one name"
            )
        return Result(accepted=bool(prior["accepted"]), result=prior["result"], replayed=True)

    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        _record_rejection(store, cmd, "halted", "run halted pending reconciliation")
        raise RuntimeError(
            f"run {cmd.run_id} is halted pending reconciliation; resolve it first"
        )

    if cmd.generation != current_generation(store, cmd.run_id):
        _record_rejection(store, cmd, "OwnershipLost", "superseded generation")
        raise OwnershipLost(
            f"generation {cmd.generation} superseded; command carries no write capability"
        )

    # One transaction across the CAS, the fact and the command row. Three
    # independent commits left a crash window in which the version advanced
    # with no command row, so the client's retry got StaleVersion for a
    # command that had been accepted -- idempotency failing in exactly the
    # crash it exists to survive.
    # Command-specific authorization: is this legal HERE, and does anything
    # authorize the effect it enables? Without this the kernel checked only the
    # envelope, and request_merge on a queued run with an empty payload was
    # accepted.
    # Every refusal is recorded, not only the stale-version one. Illegal
    # transitions, malformed bindings and failed merge authorization
    # previously left no immutable trace of the decision.
    try:
        next_state = authorize(store, cmd)
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc))
        raise

    # A review is validated BEFORE it is recorded, and recorded as a verdict in
    # its own right. Previously the verdict existed only as a verbatim copy of
    # the caller's payload nested inside a COMMAND_ACCEPTED fact, so merge
    # authorization was a search for a claim rather than a binding to
    # something the mechanism observed.
    try:
        review_binding = (
            validate_review(store, cmd) if cmd.name == "record_review" else None
        )
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc))
        raise

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
                payload={
                    "command_name": cmd.name,
                    "generation": cmd.generation,
                    # Hoisted out of the payload so independence checks read a
                    # kernel-recorded field rather than re-parsing a nested blob.
                    "implementer_identity": cmd.payload.get("implementer_identity"),
                    "payload": cmd.payload,
                },
            )
            if review_binding is not None:
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.REVIEW_VERDICT, actor="kernel",
                    causal_command_id=cmd.idempotency_key,
                    payload={
                        "verdict": cmd.payload.get("verdict"),
                        "binding_hash": binding_hash(review_binding),
                        "reviewer_identity": review_binding.reviewer_identity,
                    },
                )
            if next_state is not None:
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.TRANSITION, actor="kernel",
                    causal_command_id=cmd.idempotency_key,
                    payload={"to": next_state, "via": cmd.name},
                )
                store.set_run_state(cmd.run_id, next_state)
            store.record_command(
                cmd.idempotency_key, cmd.run_id, cmd.name, True, result,
                request_hash=canonical_hash(
                    {"name": cmd.name, "run_id": cmd.run_id, "payload": cmd.payload}
                ),
            )
    except StaleVersion:
        # Outside the transaction: the rejection must survive the rollback.
        _record_rejection(
            store, cmd, "stale_version",
            f"expected version {cmd.expected_version}",
        )
        raise

    return Result(accepted=True, result=result)
