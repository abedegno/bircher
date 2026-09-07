"""The seven typed commands and centralized authorization.

A narrow interface, not a general one. Every command carries the aggregate
version it was derived from and the generation that requested it; both are
checked before anything mutates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kernel.artifacts import binding_hash, put_artifact
from kernel.authz import NotAuthorized, authorize, validate_review
from kernel.canon import canonical_hash
from kernel.dispatch import actor_for
from kernel.events import EventKind
from kernel.mode import shadow_or_raise
from kernel.ownership import OwnershipLost, current_generation

#: The label recorded for an attempt the kernel cannot name. It is a LABEL,
#: never a gate: the gate is `actor is None`, so dispatching an actor whose
#: name happens to be this string proves nothing and grants nothing. A string
#: sentinel compared with `is` would have rested on CPython's interning of
#: short literals -- an authorization decision resting on an implementation
#: detail of the interpreter.
UNDISPATCHED = "undispatched"

#: The generation a human command carries. No dispatch record has it, so no
#: fenced path can claim it, and `dispatch()` refuses the actor "human".
HUMAN_GENERATION = -1
HUMAN_ACTOR = "human"

#: Reachable only through execute_as_human. record_review is reachable
#: through both paths; `ruling` says which.
HUMAN_COMMANDS = frozenset({
    "record_human_answer", "record_human_direction", "approve_artifact", "grant_round",
})

#: Every name `execute_as_human` will run: the four human-only commands, plus
#: `record_review` (reachable through both paths -- `ruling` says which) and
#: `cancel_run` (the spec's `kernel cancel` path, spec §4 Fallback/§5; a later
#: task's CLI records it through execute_as_human). Anything else -- park,
#: record_turn_ended, a submit, start_implementation -- is a dispatched
#: actor's command, and execute_as_human refuses it before `_submit` ever
#: sees it: without this, a caller could run ANY command as `human` with no
#: dispatch behind it, which is exactly the unfenced write capability
#: `execute_as_human` exists to grant to four commands, not the whole set.
HUMAN_EXECUTABLE = HUMAN_COMMANDS | frozenset({"record_review", "cancel_run"})

COMMAND_NAMES = frozenset({
    "submit_spec", "submit_plan", "record_review", "start_implementation",
    "record_ci_observation", "request_merge", "cancel_run",
    # Added with the merge-outcome transition: merge_requested was a dead end,
    # and cancel_run was the only escape -- which misreports a run that merged.
    "record_merge_outcome",
    # Added because the kernel could not express a run that ENDED without
    # merging. The only terminal records were `merged` (via
    # record_merge_outcome, legal only from merge_requested) and `cancelled`
    # -- so an escalated, timed-out, noop or skipped run was indistinguishable
    # in the ledger from one still in flight. The spec's first acceptance
    # criterion is that the kernel's aggregate MATCHES the scorecard, and the
    # scorecard's terminal vocabulary is
    # {merged, ready, escalated, noop, skipped, failed, timeout}: with no
    # command for six of those seven, the criterion was not merely unmet, it
    # was unsatisfiable. The first live acceptance run made this concrete --
    # scorecard `escalated`, kernel `implementing`, run never terminal.
    "record_run_outcome",
    # Added because nothing recorded what an implementation PRODUCED. The
    # reviewer named the artifact it was reviewing, so any blob the store
    # happened to hold satisfied the check -- including one from another run,
    # and including a superseded revision. The command set is closed and
    # growth here is a design change to be argued: the argument is that
    # without it there is no such thing as "this run's current output", and
    # every lineage check downstream is comparing a caller's choice against
    # itself.
    "record_implementation_output",
    # The front half (spec §2 Commands). `record_turn_ended` makes the end of
    # a model's turn a FACT the kernel holds rather than a wait the
    # coordinator performs, so "is this turn over" is answered from the
    # journal; `park` records why a pass stopped without a transition, which
    # v1 expressed only as the absence of anything.
    "record_turn_ended", "park",
    # The author's report that a turn produced nothing (spec §2 Commands,
    # spec §3): a fact the RC_FAILED escalation can be driven from, rather
    # than the coordinator inferring emptiness from the absence of a submit.
    "record_author_empty",
    # The human's commands (spec §2 Commands), reachable only through
    # `execute_as_human`.
    "record_human_answer", "record_human_direction", "approve_artifact", "grant_round",
    # The model's questions and rulings (spec §2 Commands): the author's side
    # of the grill.
    "record_model_question", "record_model_ruling",
    # The coordinator's dismissal of a refused human token, and its record of
    # an omnigent prompt item it has already sent -- either role.
    "dismiss_human_item", "record_prompt_item",
    # A relevant issue change resets the run to `queued` and opens a new
    # epoch; the kernel refuses an irrelevant one (ruling 13).
    "revise_bundle",
    # The kernel's own rendering of the reviewer's brief (spec §2 *Brief*): a
    # front-half review_ruling is refused unless its generation carries one.
    "issue_review_brief",
})


#: Payload keys that would let a caller name an actor. Refused outright.
#:
#: If a caller can populate it, it is not identity. These two fields are how
#: one caller named BOTH sides of its own independence check: a caller
#: recorded as implementer submitted its own accept naming a different
#: reviewer, and every comparison downstream was correctly implemented and
#: proved nothing.
ACTOR_FIELDS = frozenset({"actor", "implementer_identity", "reviewer_identity"})


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


def _record_rejection(store, cmd, reason: str, detail: str, actor: str) -> None:
    """Append the immutable record of a refusal.

    Outside any transaction: a rejection must survive whatever rolled back.
    Attributed to the actor whose attempt was refused, not to "kernel" -- an
    audit that cannot say WHO was refused cannot distinguish a worker retrying
    from a worker probing.
    """
    store.append_fact(
        run_id=cmd.run_id, kind=EventKind.COMMAND_REJECTED, actor=actor,
        causal_command_id=cmd.idempotency_key,
        payload={"command_name": cmd.name, "reason": reason, "detail": detail[:300]},
    )


def _side_fact(store, cmd: Command, actor: str) -> None:
    """The fact an accepted front-half command writes beside COMMAND_ACCEPTED.
    Every value is derived by the kernel from the run's state, never copied
    from the payload except the fields the command exists to carry."""
    from kernel import front
    from kernel.authz import phase_of
    state = store.run_state(cmd.run_id)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if cmd.name in ("submit_spec", "submit_plan"):
        h = cmd.payload["artifact_hash"]
        rnd = len(front.submissions(store, cmd.run_id, phase, epoch_n)) + 1
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "hash": h, "author": actor, "round": rnd},
        )
        store.set_phase_artifact(cmd.run_id, phase, h)
    elif cmd.name == "record_turn_ended":
        prompt = front.newest_prompt(store, cmd.run_id, phase, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.TURN_ENDED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session": cmd.payload["session"], "ended": cmd.payload["ended"],
                     "phase": phase, "epoch": epoch_n, "generation": cmd.generation,
                     "prompt_key": None if prompt is None else prompt["key"]},
        )
    elif cmd.name == "record_author_empty":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.AUTHOR_EMPTY, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session": cmd.payload["session"], "phase": phase,
                     "epoch": epoch_n, "generation": cmd.generation},
        )
    elif cmd.name == "park":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.PARKED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "reason": p["reason"],
                     "session_id": p.get("session_id"), "cursor_item_id": p.get("cursor_item_id"),
                     "findings_hash": p.get("findings_hash"), "verdict": p.get("verdict"),
                     "reviewer": p.get("reviewer"), "generation": cmd.generation},
        )
    elif cmd.name == "record_human_answer":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_ANSWER, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "question_ids": list(cmd.payload["question_ids"]),
                     "answer": cmd.payload["answer"], "cursor_item_id": cmd.payload.get("cursor_item_id")},
        )
    elif cmd.name == "record_human_direction":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_DIRECTION, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "text": cmd.payload["text"],
                     "cursor_item_id": cmd.payload.get("cursor_item_id")},
        )
    elif cmd.name == "approve_artifact":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "approve", "phase": phase, "epoch": epoch_n,
                     "artifact_hash": cmd.payload["artifact_hash"]},
        )
    elif cmd.name == "grant_round":
        park = front.current_park(store, cmd.run_id)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "grant_round", "phase": phase, "epoch": epoch_n,
                     "park_seq": park.seq},
        )
    elif cmd.name == "record_review" and actor == HUMAN_ACTOR:
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "request_revision", "phase": phase, "epoch": epoch_n,
                     "artifact_hash": cmd.payload["artifact_hash"]},
        )
    elif cmd.name == "record_model_question":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.MODEL_QUESTION, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "phase": phase,
                     "question_id": cmd.payload["question_id"], "question": cmd.payload["question"]},
        )
    elif cmd.name == "record_model_ruling":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.MODEL_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "question_id": p["question_id"], "ruling": p["ruling"],
                     "reasoning": p["reasoning"], "cost_if_wrong": p["cost_if_wrong"]},
        )
    elif cmd.name == "dismiss_human_item":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_ITEM_DISMISSED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "cursor_item_id": cmd.payload["cursor_item_id"],
                     "rejection": cmd.payload["rejection"]},
        )
    elif cmd.name == "record_prompt_item":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.PROMPT_ITEM, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session_id": p["session_id"], "item_id": p["item_id"], "sha256": p["sha256"]},
        )
    elif cmd.name == "issue_review_brief":
        from kernel import brief as _brief
        from kernel.policy import policy_of, policy_version
        artifact_hash = store.phase_artifact(cmd.run_id, phase)
        bundle_h = front.bundle_hash(store, cmd.run_id)
        spec_hash = store.phase_artifact(cmd.run_id, "spec") if phase == "plan" else None
        rendered = _brief.render(
            phase=phase, artefact=store.read_blob(artifact_hash), bundle=store.read_blob(bundle_h),
            spec=None if spec_hash is None else store.read_blob(spec_hash),
            policy=policy_of(store, cmd.run_id), base_sha=store.run_base_sha(cmd.run_id),
            template=_brief.TEMPLATE_VERSION,
        )
        brief_hash = put_artifact(store, rendered)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.REVIEW_BRIEF_ISSUED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "generation": cmd.generation,
                     "brief_hash": brief_hash, "brief_template": _brief.TEMPLATE_VERSION,
                     "artifact_hash": artifact_hash, "context_bundle_hash": bundle_h,
                     "policy_version": policy_version(store, cmd.run_id),
                     "base_sha": store.run_base_sha(cmd.run_id), "spec_hash": spec_hash},
        )
    elif cmd.name == "revise_bundle":
        import json
        from kernel import bundle as _bundle
        from kernel.canon import canonical_bytes
        prior_hash = front.bundle_hash(store, cmd.run_id)
        old = json.loads(store.read_blob(prior_hash))
        new = _bundle.snapshot(cmd.payload["issue"])
        new_hash = put_artifact(store, canonical_bytes(new))
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.BUNDLE_REVISED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"bundle_hash": new_hash, "prior_bundle_hash": prior_hash,
                     "reason": "issue changed", "diff": _bundle.snapshot_diff(old, new)},
        )


def submit(store, cmd: Command) -> Result:
    """The fenced path: identity is READ from the dispatch record."""
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")

    named = ACTOR_FIELDS & cmd.payload.keys()
    if named:
        raise ValueError(
            f"payload names an actor via {sorted(named)}: identity is assigned "
            "by the kernel from its dispatch record, never carried in a payload"
        )

    # Identity is READ from the dispatch record, not taken from the command.
    # An undispatched generation is an actor the kernel cannot name, and
    # attributing its work to anyone -- including "kernel" -- would be a
    # fabricated audit trail.
    actor = actor_for(store, cmd.run_id, cmd.generation)
    return _submit(store, cmd, actor, fenced=True, ruling="review_ruling")


def execute_as_human(store, cmd: Command) -> Result:
    """spec §2 *execute_as_human*: the human's commands go through the same
    journal with the actor fixed to `human`, no generation fence -- a human
    fact must land whichever generation is current -- and concurrency by
    `expected_version` alone."""
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")
    if cmd.name not in HUMAN_EXECUTABLE:
        raise ValueError(f"{cmd.name} does not go through execute_as_human")
    if cmd.generation != HUMAN_GENERATION:
        raise ValueError(
            f"a human command carries generation HUMAN_GENERATION ({HUMAN_GENERATION}), "
            f"not {cmd.generation}"
        )
    named = ACTOR_FIELDS & cmd.payload.keys()
    if named:
        raise ValueError(f"payload names an actor via {sorted(named)}")
    return _submit(store, cmd, HUMAN_ACTOR, fenced=False, ruling="human_ruling")


def _submit(store, cmd: Command, actor: str | None, *, fenced: bool, ruling: str) -> Result:
    recorded_actor = UNDISPATCHED if actor is None else actor

    from kernel.effects import is_halted

    # Every attempt is observable, accepted or not. Recording only outcomes
    # means the audit cannot tell one attempt from forty (spec section 2 lists
    # "command requested" alongside accepted and rejected). A fact is an
    # observation, not a mutation, so this is also correct on the replay path.
    store.append_fact(
        run_id=cmd.run_id, kind=EventKind.COMMAND_REQUESTED, actor=recorded_actor,
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

    if actor is None:
        _record_rejection(
            store, cmd, "NotAuthorized", "no dispatched actor for this generation",
            recorded_actor,
        )
        raise NotAuthorized(
            f"generation {cmd.generation} has no dispatched actor: the kernel "
            "cannot name who is acting, and will not attribute the work to "
            "anyone"
        )

    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        _record_rejection(store, cmd, "halted", "run halted pending reconciliation", actor)
        raise RuntimeError(
            f"run {cmd.run_id} is halted pending reconciliation; resolve it first"
        )

    if fenced and cmd.generation != current_generation(store, cmd.run_id):
        _record_rejection(store, cmd, "OwnershipLost", "superseded generation", actor)
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
    # Shadow evaluates and records; it does not act. `shadow_or_raise` below
    # can return normally instead of raising, and if execution simply fell
    # through from there, MERGE_AUTHORIZED and set_current_artifact -- gated
    # only on `cmd.name`, never on authorization having actually succeeded --
    # would run for a command `authorize()` just refused. A run already at
    # `merge_requested` receiving a second, illegal `request_merge` naming an
    # attacker's pr/repo demonstrated exactly that: the poisoned
    # MERGE_AUTHORIZED fact became the one `revalidate_merge` trusted. So a
    # refusal here returns immediately -- recorded, not acted on -- and
    # control never reaches the transaction below at all.
    try:
        next_state = authorize(store, cmd, actor, ruling=ruling)
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc), actor)
        shadow_or_raise(store, cmd.run_id, exc, cmd.idempotency_key, command_name=cmd.name)
        return Result(accepted=False, result={"name": cmd.name})

    # A review is validated BEFORE it is recorded, and recorded as a verdict in
    # its own right. Previously the verdict existed only as a verbatim copy of
    # the caller's payload nested inside a COMMAND_ACCEPTED fact, so merge
    # authorization was a search for a claim rather than a binding to
    # something the mechanism observed.
    try:
        review_binding = (
            validate_review(store, cmd, actor, ruling=ruling)
            if cmd.name == "record_review"
            else None
        )
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc), actor)
        shadow_or_raise(store, cmd.run_id, exc, cmd.idempotency_key, command_name=cmd.name)
        return Result(accepted=False, result={"name": cmd.name})

    result = {"name": cmd.name}
    try:
        with store.transaction():
            if not store.bump_version_cas(cmd.run_id, cmd.expected_version):
                raise StaleVersion(
                    f"{cmd.name} derived from version {cmd.expected_version}, "
                    "which has moved"
                )
            store.append_fact(
                run_id=cmd.run_id, kind=EventKind.COMMAND_ACCEPTED, actor=actor,
                causal_command_id=cmd.idempotency_key,
                # Payload is NESTED, not splatted: splatting let a payload key
                # named like one of the command's own fields silently overwrite
                # it in the recorded fact.
                payload={
                    "command_name": cmd.name,
                    "generation": cmd.generation,
                    "payload": cmd.payload,
                },
            )
            if cmd.name == "request_merge":
                # What was authorized, recorded by the kernel at the moment it
                # authorized it. Reaching `merge_requested` says a decision was
                # made; this says what the decision was ABOUT, so the effect
                # path can re-derive it instead of trusting its own intent.
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.MERGE_AUTHORIZED,
                    actor=actor, causal_command_id=cmd.idempotency_key,
                    payload={
                        "artifact_hash": cmd.payload.get("artifact_hash"),
                        "base_sha": cmd.payload.get("base_sha"),
                        "context_bundle_hash": cmd.payload.get("context_bundle_hash"),
                        "policy_version": cmd.payload.get("policy_version"),
                        "head_git_sha": cmd.payload.get("head_git_sha"),
                        # The target. Without it, revalidation proves a merge
                        # is authorized without proving WHICH merge.
                        "pr": cmd.payload.get("pr"),
                        "repo": cmd.payload.get("repo"),
                    },
                )
            if cmd.name == "record_implementation_output":
                store.set_current_artifact(cmd.run_id, cmd.payload["artifact_hash"])
            if cmd.name == "record_review":
                from kernel import front
                from kernel.authz import phase_of
                state_now = store.run_state(cmd.run_id)
                # A human's findings arrive as text, not a hash the caller
                # already put in the store -- there is no dispatch to have
                # journaled one first. The kernel puts the bytes itself, so
                # the fact still names a hash the store holds either way.
                findings_hash = cmd.payload.get("findings_hash")
                if findings_hash is None and isinstance(cmd.payload.get("findings"), str):
                    findings_hash = put_artifact(store, cmd.payload["findings"].encode("utf-8"))
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.REVIEW_VERDICT, actor=actor,
                    causal_command_id=cmd.idempotency_key,
                    payload={
                        "verdict": cmd.payload.get("verdict"),
                        "phase": phase_of(state_now),
                        "epoch": front.epoch(store, cmd.run_id),
                        "artifact_hash": cmd.payload.get("artifact_hash"),
                        "findings_hash": findings_hash,
                        "ruling": "review_ruling" if review_binding is not None else "human_ruling",
                        "binding_hash": None if review_binding is None else binding_hash(review_binding),
                        # From the dispatch record, not the binding: the
                        # binding says WHAT was approved, this says WHO.
                        "reviewer_identity": actor,
                        # The generation this ruling came from, so the §8
                        # proof can pair a ruling with its seat's brief
                        # (front.brief_for reads this).
                        "generation": cmd.generation,
                    },
                )
            _side_fact(store, cmd, actor)
            if next_state is not None:
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.TRANSITION, actor=actor,
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
            f"expected version {cmd.expected_version}", actor,
        )
        raise

    return Result(accepted=True, result=result)
