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
from kernel.dispatch import Role, role_for
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
    # record_review's destination depends on the verdict: a revision request
    # must return the run to `planned` so implementation can start again.
    # Landing every review in `reviewing` left a revision request with nowhere
    # to do the revision.
    "record_review": (frozenset({"implementing", "reviewing"}), None),
    # merge_requested must not be a dead end. Without an outbound transition a
    # merge that comes back uncertain can never be retried after
    # reconciliation, and the only escape -- cancel_run -- records 'cancelled'
    # for a run that in fact merged, corrupting the terminal outcome.
    "record_merge_outcome": (frozenset({"merge_requested"}), None),
    # Records what the implementation produced; does not itself transition.
    # The run stays in `implementing` until a review moves it.
    "record_implementation_output": (frozenset({"implementing"}), None),
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

#: Verdicts `record_review` may carry, and where each leaves the run. A closed
#: set: arbitrary strings were accepted while only literal "accept" authorized
#: a merge, so a typo silently became a non-approval that read as a review.
_VERDICTS: dict[str, str] = {
    "accept": "reviewing",
    "request_revision": "planned",
    "reject": "reviewing",
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
            policy_version=version,
        )
    except (KeyError, TypeError, ValueError) as exc:
        # The gate must REJECT malformed input, not crash on it. A bare
        # KeyError escaping submit() means no rejection fact and an unhandled
        # exception on a validation path.
        raise NotAuthorized(f"malformed verdict binding: {exc}") from exc


def validate_review(store, cmd, actor: str) -> VerdictBinding:
    """Validate a review BEFORE it is recorded, so the verdict is an
    observation rather than a claim.

    An earlier version validated nothing here and compared the caller's own
    payload at merge time. The hash comparison was real; what it compared was
    two things the same actor said -- one caller could record its own
    approval, naming any reviewer and any hashes, then present the same tuple
    for merge.

    *actor* is the reviewer, resolved by the kernel from its dispatch record.
    It is a parameter rather than a payload field because a reviewer that can
    name itself can name someone else.
    """
    verdict = cmd.payload.get("verdict")
    if verdict not in _VERDICTS:
        raise NotAuthorized(
            f"verdict {verdict!r} is not one of {sorted(_VERDICTS)}"
        )

    binding = _binding_from(cmd.payload)

    # The artifact must be one the kernel actually holds. Without this an
    # independent reviewer could approve hashes for objects that do not exist,
    # and merge then compared one caller-supplied tuple against another.
    if not store.has_artifact(binding.artifact_hash):
        raise NotAuthorized(
            f"review binds artifact {binding.artifact_hash[:12]}..., which the "
            "kernel does not hold: an approval binds objects the mechanism has, "
            "not hashes the actor supplies"
        )

    # Existence is not identity. The old check asked only whether the store
    # held the blob, so a review could bind an artifact from another run, or a
    # superseded revision of this one, and the merge chain compared that
    # caller-chosen hash against itself the whole way down.
    current = store.current_artifact(cmd.run_id)
    if current is None:
        raise NotAuthorized(
            "this run has recorded no implementation output: there is nothing "
            "that is currently under review"
        )
    if binding.artifact_hash != current:
        raise NotAuthorized(
            f"review binds artifact {binding.artifact_hash[:12]}..., but this "
            f"run's current output is {current[:12]}...: an approval binds what "
            "the implementation produced, not any object the store holds"
        )

    observed_base = store.run_base_sha(cmd.run_id)
    if binding.base_sha != observed_base:
        raise NotAuthorized(
            f"review binds base {binding.base_sha!r}, but the kernel observed "
            f"{observed_base!r} for this run: an approval binds inputs the "
            "mechanism saw, not ones the actor asserts"
        )

    if role_for(store, cmd.run_id, cmd.generation) != Role.REVIEWER:
        raise NotAuthorized(
            "a review must come from an attempt dispatched in the reviewer "
            "role: the role is assigned with the fence, so an implementer "
            "cannot elect itself reviewer"
        )

    implementer = _implementer_of(store, cmd.run_id)
    if implementer is not None and actor == implementer:
        raise NotAuthorized(
            f"reviewer independence violated: {actor!r} implemented this run "
            "and cannot review its own work"
        )
    return binding


def _implementer_of(store, run_id: str) -> str | None:
    """Who performed the implementation, from the kernel's own facts.

    Independence is reviewer-vs-IMPLEMENTER, not reviewer-vs-submitter. An
    earlier version compared against the current owner -- which is whoever
    most recently acquired the generation, i.e. usually the submitter -- so a
    reviewer submitting its own review was refused while the actual conflict
    went unchecked.

    Reads the fact's ACTOR, which the kernel wrote from its dispatch record.
    It previously read `implementer_identity` out of the payload -- a string
    the implementer chose, so an implementer could name someone else as the
    implementer and then review its own work as itself.
    """
    implementer = None
    for fact in store.facts_for(run_id):
        if (
            fact.kind == EventKind.COMMAND_ACCEPTED
            and fact.payload.get("command_name") == "start_implementation"
        ):
            # LAST, not first. Returning the first let the revision loop --
            # request_revision -> planned -> start_implementation -- put a new
            # implementer in place whose own review then passed the check.
            implementer = fact.actor
    return implementer


def _ci_is_green(store, run_id: str, head_git_sha) -> bool:
    """True when the most recent CI observation for the run reports success.

    Merge authorization previously required only a matching verdict, so a run
    could reach merge_requested having never reported CI at all.
    """
    latest = None
    for fact in store.facts_for(run_id):
        if (
            fact.kind == EventKind.COMMAND_ACCEPTED
            and fact.payload.get("command_name") == "record_ci_observation"
        ):
            latest = fact.payload.get("payload") or {}
    if latest is None or latest.get("status") != "success":
        return False
    # CI must be green ON THE HEAD BEING MERGED. Reading only `status` and
    # discarding head_git_sha let green CI on an unrelated or older head
    # authorize the merge.
    return latest.get("head_git_sha") == head_git_sha


def _merge_is_authorized(store, run_id: str, payload: dict) -> bool:
    """True when a KERNEL-RECORDED verdict binds exactly the inputs presented.

    Reads REVIEW_VERDICT facts -- written by the kernel after validation --
    rather than the caller's payload echoed inside a COMMAND_ACCEPTED fact.
    "accept" means no unresolved blockers for the pinned bundle; it authorizes
    nothing once any bound input has moved.
    """
    wanted = binding_hash(_binding_from(payload))
    # The LATEST verdict for this binding decides. Scanning for any historical
    # `accept` ignored a later `reject` or `request_revision` over the same
    # inputs, so a withdrawn approval still authorized a merge.
    latest = None
    for fact in store.facts_for(run_id):
        if fact.kind != EventKind.REVIEW_VERDICT:
            continue
        if fact.payload.get("binding_hash") == wanted:
            latest = fact.payload.get("verdict")
    return latest == "accept"


def authorize(store, cmd, actor: str) -> str | None:
    """Authorize *cmd* against the run's current state. Returns the next state.

    Raises :class:`NotAuthorized` when the command is illegal here, or when
    nothing authorizes the effect it would enable.
    """
    if cmd.name == "start_implementation" and role_for(
        store, cmd.run_id, cmd.generation
    ) != Role.IMPLEMENTER:
        raise NotAuthorized(
            "implementation must come from an attempt dispatched in the "
            "implementer role"
        )

    allowed, next_state = _TRANSITIONS[cmd.name]
    current = store.run_state(cmd.run_id)
    if current not in allowed:
        raise NotAuthorized(
            f"{cmd.name} is not legal from state {current!r}; "
            f"legal from {sorted(allowed)}"
        )

    if cmd.name == "record_implementation_output":
        if role_for(store, cmd.run_id, cmd.generation) != Role.IMPLEMENTER:
            raise NotAuthorized(
                "only an attempt dispatched in the implementer role may record "
                "an implementation output"
            )
        artifact = cmd.payload.get("artifact_hash")
        if not isinstance(artifact, str) or not store.has_artifact(artifact):
            raise NotAuthorized(
                f"implementation output {artifact!r} is not an artifact the "
                "kernel holds"
            )
        return None

    if cmd.name == "record_review":
        verdict = cmd.payload.get("verdict")
        if verdict not in _VERDICTS:
            raise NotAuthorized(
                f"verdict {verdict!r} is not one of {sorted(_VERDICTS)}"
            )
        return _VERDICTS[verdict]

    if cmd.name == "record_merge_outcome":
        outcome = cmd.payload.get("outcome")
        if outcome not in _MERGE_OUTCOMES:
            raise NotAuthorized(
                f"outcome {outcome!r} is not one of {sorted(_MERGE_OUTCOMES)}"
            )
        if outcome == "merged" and not store.has_confirmed_effect(
            cmd.run_id, "merge"
        ):
            raise NotAuthorized(
                "no confirmed merge effect for this run: a merge outcome "
                "reports what the mechanism observed, not what an actor claims"
            )
        return _MERGE_OUTCOMES[outcome]

    if cmd.name == "request_merge":
        current = store.current_artifact(cmd.run_id)
        if cmd.payload.get("artifact_hash") != current:
            raise NotAuthorized(
                "merge binds an artifact that is not this run's current "
                "output: a revision recorded after the approval supersedes it, "
                "and an approval of the superseded object authorizes nothing"
            )

    if cmd.name == "request_merge":
        if not _ci_is_green(store, cmd.run_id, cmd.payload.get("head_git_sha")):
            raise NotAuthorized(
                "no successful CI observation for the head being merged: a "
                "merge needs evidence the mechanism observed, bound to the "
                "object being merged"
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
