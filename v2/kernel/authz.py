"""Command-specific authorization: legal transitions and merge authority.

`submit()` originally checked the command name, the halt, the generation and
the version CAS -- the command ENVELOPE -- and nothing about whether the
command made sense. A review submitted `request_merge` against a brand-new
`queued` run with an empty payload and it was accepted, which contradicts the
spec's "Only the kernel authorizes a merge".

This module is the content of that authorization. It is deliberately a table
plus two predicates rather than a workflow engine: the spec says "a relational
database with explicit Python transition functions is sufficient. Do not build
a workflow language."
"""

from __future__ import annotations

from kernel.artifacts import VerdictBinding, binding_hash
from kernel.events import EventKind


class NotAuthorized(Exception):
    """The command is not legal in the run's current state, or nothing
    authorizes the effect it would enable."""


#: command -> (states it may be issued from, resulting state or None to stay).
#: Every command declares its states: a command absent from this table would be
#: legal everywhere or nowhere depending on the lookup's default, and which one
#: is an accident rather than a decision.
_TRANSITIONS: dict[str, tuple[frozenset[str], str | None]] = {
    "submit_spec": (frozenset({"queued"}), "specified"),
    "submit_plan": (frozenset({"specified"}), "planned"),
    "start_implementation": (frozenset({"planned"}), "implementing"),
    "record_review": (frozenset({"implementing", "reviewing"}), "reviewing"),
    "record_ci_observation": (frozenset({"implementing", "reviewing"}), None),
    "request_merge": (frozenset({"reviewing"}), "merge_requested"),
    # Cancellation is legal from anywhere: a run must always be stoppable.
    "cancel_run": (
        frozenset({
            "queued", "specified", "planned", "implementing", "reviewing",
            "merge_requested",
        }),
        "cancelled",
    ),
}


def legal_states_for(name: str) -> frozenset[str]:
    return _TRANSITIONS[name][0]


def next_state_for(name: str) -> str | None:
    return _TRANSITIONS[name][1]


def _binding_from(payload: dict) -> VerdictBinding:
    return VerdictBinding(
        artifact_hash=payload["artifact_hash"],
        base_sha=payload["base_sha"],
        context_bundle_hash=payload["context_bundle_hash"],
        reviewer_identity=payload["reviewer_identity"],
        policy_version=int(payload["policy_version"]),
    )


def _merge_is_authorized(store, run_id: str, payload: dict) -> bool:
    """True when an accepted review binds EXACTLY the inputs presented now.

    "accept" from the judgement layer means "no unresolved blockers for the
    pinned review bundle" -- it does not mean merge, and it authorizes nothing
    once any bound input has moved.
    """
    wanted = binding_hash(_binding_from(payload))
    for fact in store.facts_for(run_id):
        if fact.kind != EventKind.COMMAND_ACCEPTED:
            continue
        if fact.payload.get("command_name") != "record_review":
            continue
        inner = fact.payload.get("payload") or {}
        if inner.get("verdict") != "accept":
            continue
        try:
            if binding_hash(_binding_from(inner)) == wanted:
                return True
        except (KeyError, ValueError):
            continue
    return False


def authorize(store, cmd) -> str | None:
    """Authorize *cmd* against the run's current state. Returns the next state.

    Raises :class:`NotAuthorized` when the command is illegal here, or when
    nothing authorizes the effect it would enable.
    """
    allowed, next_state = _TRANSITIONS[cmd.name]
    current = store.run_state(cmd.run_id)
    if current not in allowed:
        raise NotAuthorized(
            f"{cmd.name} is not legal from state {current!r}; "
            f"legal from {sorted(allowed)}"
        )

    if cmd.name == "request_merge" and not _merge_is_authorized(
        store, cmd.run_id, cmd.payload
    ):
        raise NotAuthorized(
            "no accepted review binds the inputs presented for merge: an "
            "approval authorizes a tuple of immutable inputs, and one of them "
            "has moved or was never approved"
        )

    return next_state
