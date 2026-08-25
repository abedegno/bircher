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
    # merge_requested must not be a dead end. Without an outbound transition a
    # merge that comes back uncertain can never be retried after
    # reconciliation, and the only escape -- cancel_run -- records 'cancelled'
    # for a run that in fact merged, corrupting the terminal outcome.
    "record_merge_outcome": (frozenset({"merge_requested"}), None),
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

#: Outcomes `record_merge_outcome` may report, and where each leaves the run.
_MERGE_OUTCOMES: dict[str, str] = {
    "merged": "merged",
    # A failed merge returns to reviewing so it can be retried after
    # reconciliation rather than wedging the run.
    "failed": "reviewing",
}


def legal_states_for(name: str) -> frozenset[str]:
    return _TRANSITIONS[name][0]


def next_state_for(name: str) -> str | None:
    return _TRANSITIONS[name][1]


def _binding_from(payload: dict) -> VerdictBinding:
    """Build a binding from *payload*, refusing anything malformed.

    policy_version is checked with `type(...) is int` rather than coerced:
    int(1.9) == 1, so a float silently bound to a review recorded with 1 -- in
    a system whose canonical form refuses floats precisely because their
    encoding drifts. bool is excluded too; True == 1 is not a policy version.
    """
    version = payload.get("policy_version")
    if type(version) is not int:
        raise NotAuthorized(
            f"policy_version must be an int, got {type(version).__name__}"
        )
    try:
        return VerdictBinding(
            artifact_hash=payload["artifact_hash"],
            base_sha=payload["base_sha"],
            context_bundle_hash=payload["context_bundle_hash"],
            reviewer_identity=payload["reviewer_identity"],
            policy_version=version,
        )
    except (KeyError, TypeError, ValueError) as exc:
        # The gate must REJECT malformed input, not crash on it. A bare
        # KeyError escaping submit() means no rejection fact and an unhandled
        # exception on a validation path.
        raise NotAuthorized(f"malformed verdict binding: {exc}") from exc


def validate_review(store, cmd) -> VerdictBinding:
    """Validate a review BEFORE it is recorded, so the verdict is an
    observation rather than a claim.

    An earlier version validated nothing here and compared the caller's own
    payload at merge time. The hash comparison was real; what it compared was
    two things the same actor said -- one caller could record its own
    approval, naming any reviewer and any hashes, then present the same tuple
    for merge.
    """
    binding = _binding_from(cmd.payload)

    observed_base = store.run_base_sha(cmd.run_id)
    if binding.base_sha != observed_base:
        raise NotAuthorized(
            f"review binds base {binding.base_sha!r}, but the kernel observed "
            f"{observed_base!r} for this run: an approval binds inputs the "
            "mechanism saw, not ones the actor asserts"
        )

    implementer = _implementer_of(store, cmd.run_id)
    if implementer is not None and binding.reviewer_identity == implementer:
        raise NotAuthorized(
            f"reviewer independence violated: {binding.reviewer_identity!r} "
            "implemented this run and cannot review its own work"
        )
    return binding


def _implementer_of(store, run_id: str) -> str | None:
    """Who performed the implementation, from the kernel's own facts.

    Independence is reviewer-vs-IMPLEMENTER, not reviewer-vs-submitter. An
    earlier version compared against the current owner -- which is whoever
    most recently acquired the generation, i.e. usually the submitter -- so a
    reviewer submitting its own review was refused while the actual conflict
    went unchecked.
    """
    for fact in store.facts_for(run_id):
        if (
            fact.kind == EventKind.COMMAND_ACCEPTED
            and fact.payload.get("command_name") == "start_implementation"
        ):
            return fact.payload.get("implementer_identity")
    return None


def _merge_is_authorized(store, run_id: str, payload: dict) -> bool:
    """True when a KERNEL-RECORDED verdict binds exactly the inputs presented.

    Reads REVIEW_VERDICT facts -- written by the kernel after validation --
    rather than the caller's payload echoed inside a COMMAND_ACCEPTED fact.
    "accept" means no unresolved blockers for the pinned bundle; it authorizes
    nothing once any bound input has moved.
    """
    wanted = binding_hash(_binding_from(payload))
    for fact in store.facts_for(run_id):
        if fact.kind != EventKind.REVIEW_VERDICT:
            continue
        if fact.payload.get("verdict") != "accept":
            continue
        if fact.payload.get("binding_hash") == wanted:
            return True
    return False


def authorize(store, cmd) -> str | None:
    """Authorize *cmd* against the run's current state. Returns the next state.

    Raises :class:`NotAuthorized` when the command is illegal here, or when
    nothing authorizes the effect it would enable.
    """
    if cmd.name == "start_implementation" and not cmd.payload.get(
        "implementer_identity"
    ):
        raise NotAuthorized(
            "start_implementation must name its implementer_identity: "
            "reviewer independence cannot be checked against an unknown actor"
        )

    allowed, next_state = _TRANSITIONS[cmd.name]
    current = store.run_state(cmd.run_id)
    if current not in allowed:
        raise NotAuthorized(
            f"{cmd.name} is not legal from state {current!r}; "
            f"legal from {sorted(allowed)}"
        )

    if cmd.name == "record_merge_outcome":
        outcome = cmd.payload.get("outcome")
        if outcome not in _MERGE_OUTCOMES:
            raise NotAuthorized(
                f"outcome {outcome!r} is not one of {sorted(_MERGE_OUTCOMES)}"
            )
        return _MERGE_OUTCOMES[outcome]

    if cmd.name == "request_merge" and not _merge_is_authorized(
        store, cmd.run_id, cmd.payload
    ):
        raise NotAuthorized(
            "no accepted review binds the inputs presented for merge: an "
            "approval authorizes a tuple of immutable inputs, and one of them "
            "has moved or was never approved"
        )

    return next_state
