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


#: The six states a run passes through before `planned` (spec §2 *States*).
#: v1 went `queued -> specified -> planned` on two submits, so an author's own
#: submission was also its acceptance; the four new states are where a
#: submission waits for someone else to rule on it.
FRONT_HALF_STATES = frozenset({
    "queued", "spec_submitted", "spec_accepted",
    "specified", "plan_submitted", "plan_accepted",
})

_PHASE_OF_STATE = {
    "queued": "spec", "spec_submitted": "spec", "spec_accepted": "spec",
    "specified": "plan", "plan_submitted": "plan", "plan_accepted": "plan",
    "implementing": "implementation", "reviewing": "implementation",
}


def phase_of(state: str) -> str | None:
    """Which artefact the run is working on, derived from its state.

    The kernel derives the phase rather than reading it from a payload, so a
    review of the plan cannot be recorded against the spec.
    """
    return _PHASE_OF_STATE.get(state)


#: spec §3 loop: the reasons a pass parks for.
PARK_REASONS = frozenset({
    "grill", "no_verdict", "bound_exhausted", "gate", "budget_exhausted",
    "identical_resubmission",
})

#: spec §3 *The turn's end is a fact*: the four ways a turn ends.
TURN_ENDS = frozenset({"file", "dead", "cap", "displaced"})

#: Every non-terminal state. `record_run_outcome` and `cancel_run` are legal
#: from all of them, so listing them by hand meant each new front-half state
#: had to be remembered in two more places.
_ALL_ACTIVE = frozenset({
    "queued", "spec_submitted", "spec_accepted", "specified",
    "plan_submitted", "plan_accepted", "planned", "implementing", "reviewing",
    "merge_requested",
})

#: command -> (states it may be issued from, resulting state or None to stay).
#: Every command declares its states: a command absent from this table would be
#: legal everywhere or nowhere depending on the lookup's default, and which one
#: is an accident rather than a decision.
_TRANSITIONS: dict[str, tuple[frozenset[str], str | None]] = {
    # spec §2 Commands. The front half: a submit lands at *_submitted, and
    # only a reviewer's accept of the current hash moves on from there.
    "submit_spec": (frozenset({"queued"}), "spec_submitted"),
    "submit_plan": (frozenset({"specified"}), "plan_submitted"),
    "start_implementation": (frozenset({"planned"}), "implementing"),
    # Destination depends on state, verdict and the run's gates: computed in
    # authorize() by _review_destination. In the back half a revision request
    # must return the run to `planned` so implementation can start again;
    # landing every review in `reviewing` left it nowhere to do the revision.
    "record_review": (
        frozenset({"spec_submitted", "spec_accepted", "plan_submitted",
                   "plan_accepted", "implementing", "reviewing"}),
        None,
    ),
    "record_turn_ended": (
        frozenset({"queued", "specified", "spec_submitted", "plan_submitted"}), None,
    ),
    "park": (FRONT_HALF_STATES, None),
    # merge_requested must not be a dead end. Without an outbound transition a
    # merge that comes back uncertain can never be retried after
    # reconciliation, and the only escape -- cancel_run -- records 'cancelled'
    # for a run that in fact merged, corrupting the terminal outcome.
    "record_merge_outcome": (frozenset({"merge_requested"}), None),
    # The human's commands (spec §2 Commands), reachable only through
    # execute_as_human -- checked in authorize() below.
    "record_human_answer": (FRONT_HALF_STATES, None),
    "record_human_direction": (frozenset({"queued", "specified"}), None),
    # Destination by phase: computed in authorize().
    "approve_artifact": (frozenset({"spec_accepted", "plan_accepted"}), None),
    "grant_round": (frozenset({"queued", "specified", "spec_submitted", "plan_submitted"}), None),
    # Records what the implementation produced; does not itself transition.
    # The run stays in `implementing` until a review moves it.
    "record_implementation_output": (frozenset({"implementing"}), None),
    "record_ci_observation": (frozenset({"implementing", "reviewing"}), None),
    "request_merge": (frozenset({"reviewing"}), "merge_requested"),
    # The terminal record every path can reach. Legal from every state except
    # `ended` itself, because a run can finish from anywhere: the coordinator
    # escalates out of `implementing`, skips out of `queued`, and a merged run
    # still needs an end. Not idempotent by design -- a second one is refused
    # by the state check, so a duplicate is a visible refusal rather than two
    # contradictory terminal facts.
    "record_run_outcome": (_ALL_ACTIVE | frozenset({"merged", "cancelled"}), "ended"),
    # Cancellation is legal from anywhere: a run must always be stoppable.
    "cancel_run": (_ALL_ACTIVE, "cancelled"),
}

#: Verdicts `record_review` may carry. A closed set: arbitrary strings were
#: accepted while only literal "accept" authorized a merge, so a typo silently
#: became a non-approval that read as a review. Where each verdict LEAVES the
#: run is no longer a property of the word alone -- see `_review_destination`.
_VERDICT_WORDS = frozenset({"accept", "request_revision", "reject"})

#: Back-half destinations, unchanged from v1.
_BACK_HALF_DESTINATIONS: dict[str, str] = {
    "accept": "reviewing", "request_revision": "planned", "reject": "reviewing",
}


def _review_destination(store, run_id: str, state: str, verdict: str, ruling: str) -> str:
    """Where a review leaves the run: the verdict, the state, and the gates.

    In the front half the same word means different things at different
    states, and an accept stops at `*_accepted` when the run's policy gates
    that phase -- so the destination cannot be a lookup on the verdict alone.

    The human clause comes FIRST: a human_ruling is never routed by the
    back-half or gate logic below it, which exist for a reviewer's dispatched
    accept and have no opinion on a human's correction.
    """
    from kernel.policy import policy_of
    phase = phase_of(state)
    if ruling == "human_ruling":
        if state not in FRONT_HALF_STATES:
            raise NotAuthorized("a human record_review is front-half only (spec §2 Commands)")
        if verdict != "request_revision":
            raise NotAuthorized(
                "the human's record_review is request_revision; approval is "
                "approve_artifact, after the reviewer"
            )
        return "queued" if phase == "spec" else "specified"
    if state in ("implementing", "reviewing"):
        return _BACK_HALF_DESTINATIONS[verdict]
    if verdict == "reject":
        raise NotAuthorized(
            "reject is not a front-half verdict: a spec or plan is accepted or "
            "revised, never rejected (spec §2 Commands)"
        )
    if state.endswith("_accepted"):
        raise NotAuthorized(
            f"record_review from {state!r} is the human's correction only "
            "(a human_ruling); a reviewer has already ruled on this hash"
        )
    if verdict == "request_revision":
        return "queued" if phase == "spec" else "specified"
    gates = policy_of(store, run_id).gates
    if phase == "spec":
        return "spec_accepted" if "spec" in gates else "specified"
    return "plan_accepted" if "plan" in gates else "planned"


#: Outcomes `record_merge_outcome` may report, and where each leaves the run.
_MERGE_OUTCOMES: dict[str, str] = {
    "merged": "merged",
    # A failed merge returns to reviewing so it can be retried after
    # reconciliation rather than wedging the run.
    "failed": "reviewing",
}


#: Outcomes `record_run_outcome` may report.
#:
#: The scorecard's own terminal vocabulary is SIX values -- skipped, failed,
#: noop, escalated, ready, timeout. `merged` is NOT among them: run-queue.sh
#: uses "merged" only as the argument to record_merge_outcome, never as a
#: scorecard outcome. An earlier version of this comment claimed the scorecard
#: had seven and that the kernel could express one of them; both halves were
#: wrong, and the correct statement is starker -- the kernel could express
#: NONE of the six, because `cancelled` is not a scorecard outcome either.
#:
#: `merged` is accepted here anyway, as the one value a mechanism can check
#: (below), so a caller on the merge path can close its ledger with it. The
#: other six are the scorecard's, verbatim: a translation layer between the two
#: is exactly where a mismatch would hide.
#:
#: WHAT THIS RECORDS IS AN ASSERTION, NOT AN OBSERVATION -- with one
#: exception. In record mode the kernel is watching a coordinator it does not
#: control, and "escalated" names a judgement no mechanism can confirm; the
#: fact means "the coordinator said this", and the report must not read it as
#: more. `merged` IS checkable, so it is checked against a confirmed merge
#: effect, on the same reasoning as record_merge_outcome.
_RUN_OUTCOMES: frozenset[str] = frozenset({
    "merged", "ready", "escalated", "noop", "skipped", "failed", "timeout",
})


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


def validate_review(store, cmd, actor: str, *, ruling: str = "review_ruling") -> VerdictBinding | None:
    """Validate a review BEFORE it is recorded (spec §2 *execute_as_human*).

    BINDING checks apply to every ruling: the verdict word, the phase against
    the state, the hash against the phase's current artefact. DISPATCH checks
    apply to a review_ruling only: base, bundle, policy version, role and
    independence -- a human's correction comes from no dispatch.

    An earlier version validated nothing here and compared the caller's own
    payload at merge time. The hash comparison was real; what it compared was
    two things the same actor said -- one caller could record its own
    approval, naming any reviewer and any hashes, then present the same tuple
    for merge.

    *actor* is the reviewer, resolved by the kernel from its dispatch record.
    It is a parameter rather than a payload field because a reviewer that can
    name itself can name someone else.
    """
    from kernel import front
    verdict = cmd.payload.get("verdict")
    if verdict not in _VERDICT_WORDS:
        raise NotAuthorized(f"verdict {verdict!r} is not one of {sorted(_VERDICT_WORDS)}")

    state = store.run_state(cmd.run_id)
    phase = phase_of(state)
    if cmd.payload.get("phase") != phase:
        raise NotAuthorized(
            f"review names phase {cmd.payload.get('phase')!r} but the run is at "
            f"{state!r}, whose phase is {phase!r}"
        )

    # Existence is not identity. Holding the blob only says the kernel has it;
    # a review must bind what this run is CURRENTLY carrying for this phase,
    # or an approval of a superseded revision -- or of another run's object --
    # counts, and every comparison downstream is caller-chosen against itself.
    if phase == "implementation":
        current = store.current_artifact(cmd.run_id)
        what = "this run's current output"
    else:
        current = store.phase_artifact(cmd.run_id, phase)
        what = f"the {phase} phase's current artefact"
    if current is None:
        raise NotAuthorized(f"nothing is under review: {what} is not recorded")
    given = cmd.payload.get("artifact_hash")
    if given != current:
        raise NotAuthorized(
            f"review binds artifact {str(given)[:12]}..., but {what} is "
            f"{current[:12]}...: an approval binds what was produced, not any "
            "object the store holds"
        )
    if not store.has_artifact(current):
        raise NotAuthorized(f"the kernel does not hold {current[:12]}...")

    if ruling == "human_ruling":
        # A review_ruling's findings arrive as a hash the store already holds
        # (checked below, for that path only); a human's arrive as text, put
        # into the store by the kernel itself, in `submit()`'s transaction.
        # Without this, an omitted or non-string `findings` reached that
        # transaction and was recorded as `findings_hash: None` -- a
        # request_revision with no findings a subsequent author round could
        # be briefed with.
        findings = cmd.payload.get("findings")
        if not isinstance(findings, str) or not findings.strip():
            raise NotAuthorized("a human record_review carries non-empty findings")
        return None

    binding = _binding_from(cmd.payload)
    observed_base = store.run_base_sha(cmd.run_id)
    if binding.base_sha != observed_base:
        raise NotAuthorized(
            f"review binds base {binding.base_sha!r}, but the kernel observed "
            f"{observed_base!r} for this run"
        )
    if role_for(store, cmd.run_id, cmd.generation) != Role.REVIEWER:
        raise NotAuthorized(
            "a review must come from an attempt dispatched in the reviewer role"
        )

    if phase == "implementation":
        conflicted = _conflicted_actors(store, cmd.run_id)
        if actor in conflicted:
            raise NotAuthorized(
                f"reviewer independence violated: {actor!r} is implementing this "
                f"run or produced the artifact under review (conflicted: {sorted(conflicted)})"
            )
        return binding

    epoch_n = front.epoch(store, cmd.run_id)
    expected_bundle = front.bundle_hash(store, cmd.run_id)
    if binding.context_bundle_hash != expected_bundle:
        raise NotAuthorized(
            f"review binds context_bundle_hash {binding.context_bundle_hash[:12]}..., "
            f"but epoch {epoch_n}'s bundle is {str(expected_bundle)[:12]}..."
        )
    expected_policy = front.policy_version(store, cmd.run_id)
    if binding.policy_version != expected_policy:
        raise NotAuthorized(
            f"review binds policy_version {binding.policy_version}, but this run's "
            f"policy_frozen is journal seq {expected_policy}"
        )
    findings = cmd.payload.get("findings_hash")
    if not isinstance(findings, str) or not store.has_artifact(findings):
        raise NotAuthorized(
            "a front-half review_ruling names findings_hash the kernel holds: the "
            "next author round is briefed with them"
        )
    submitted = front.newest_submission(store, cmd.run_id, phase, epoch_n)
    if submitted is not None and submitted.payload["author"] == actor:
        raise NotAuthorized(
            f"reviewer independence violated: {actor!r} submitted the {phase} under review"
        )
    return binding


def _producer_of_current_artifact(store, run_id: str) -> str | None:
    """Who produced the artifact this run is currently carrying.

    THE actor independence is about. `_implementer_of` reads whoever last
    issued `start_implementation`, which is not necessarily who produced the
    thing under review: with alice starting and bob recording the output, bob
    reviewed his own artifact and the merge executed. That gap arrived with
    `record_implementation_output` in M1-3b and nothing moved the check onto
    it.
    """
    current = store.current_artifact(run_id)
    if current is None:
        return None
    producer = None
    for fact in store.facts_for(run_id):
        if (
            fact.kind == EventKind.COMMAND_ACCEPTED
            and fact.payload.get("command_name") == "record_implementation_output"
            and (fact.payload.get("payload") or {}).get("artifact_hash") == current
        ):
            producer = fact.actor
    return producer


def _conflicted_actors(store, run_id: str) -> set[str]:
    """Everyone who cannot review this run's current output.

    BOTH the producer of the artifact and whoever is currently implementing:
    an actor mid-implementation has a conflict even before it has produced
    anything, and an actor that produced the artifact has one even if someone
    else started the attempt.
    """
    return {a for a in (_producer_of_current_artifact(store, run_id),
                        _implementer_of(store, run_id)) if a is not None}


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
        # ONLY an implementation-phase verdict authorizes a merge. A spec or
        # plan accept is a `review_verdict` with a binding_hash of the same
        # shape, so without this the SPEC reviewer's approval authorizes a
        # merge whenever the run's current output happens to be the spec
        # artefact and the requester presents the tuple that accept bound --
        # and the requester chooses the bundle and the policy version, both
        # declared residuals. A back-half reject binding any other tuple does
        # not supersede it, so the run reaches merge_requested and
        # revalidation passes, with no implementation review having accepted
        # anything. Verdicts written before the front half existed carry no
        # phase and were all implementation ones.
        if fact.payload.get("phase") not in (None, "implementation"):
            continue
        if fact.payload.get("binding_hash") == wanted:
            latest = fact.payload.get("verdict")
    return latest == "accept"


def _check_submit(store, cmd, state: str) -> None:
    """What a submission has to be, before it becomes the phase's artefact.

    The author role, an artefact the kernel holds, and a change: an identical
    resubmission in the same phase and epoch is the loop spinning, not a
    revision, and the plan additionally has to be a different object from the
    spec and to have tasks in it.
    """
    from kernel import front
    if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
    h = cmd.payload.get("artifact_hash")
    if not isinstance(h, str) or not store.has_artifact(h):
        raise NotAuthorized(f"{cmd.name} names an artefact the kernel does not hold: {h!r}")
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if any(f.payload["hash"] == h for f in front.submissions(store, cmd.run_id, phase, epoch_n)):
        raise NotAuthorized(
            f"identical to the prior artefact: {h[:12]}... was already submitted "
            f"for {phase} in epoch {epoch_n}; a resubmission that did not change is not a revision"
        )
    if cmd.name == "submit_plan":
        if h == store.phase_artifact(cmd.run_id, "spec"):
            raise NotAuthorized("the plan is the run's current spec: a plan is a different artefact")
        if b"### Task" not in store.read_blob(h):
            raise NotAuthorized("plan has no tasks: no `### Task` heading (a shape check)")


def _check_park(store, cmd) -> None:
    """A park records why a pass stopped. Bounded here so the reason is one
    of the six the loop has, not free text the coordinator invents."""
    if cmd.payload.get("reason") not in PARK_REASONS:
        raise NotAuthorized(f"park reason {cmd.payload.get('reason')!r} is not one of {sorted(PARK_REASONS)}")
    for key in ("session_id", "cursor_item_id", "reviewer"):
        if cmd.payload.get(key) is not None and not isinstance(cmd.payload.get(key), str):
            raise NotAuthorized(f"park {key} must be a string or null")
    if cmd.payload.get("findings_hash") is not None and not store.has_artifact(cmd.payload.get("findings_hash")):
        raise NotAuthorized("park findings_hash names an artefact the kernel does not hold")
    if cmd.payload.get("verdict") not in (None, "accept", "request_revision"):
        raise NotAuthorized("park verdict must be null, accept or request_revision")


def authorize(store, cmd, actor: str, *, ruling: str = "review_ruling") -> str | None:
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

    from kernel.commands import HUMAN_COMMANDS
    if cmd.name in HUMAN_COMMANDS and ruling != "human_ruling":
        raise NotAuthorized(
            f"{cmd.name} is the human's: it reaches the kernel only through "
            "execute_as_human, never a dispatched generation"
        )

    allowed, next_state = _TRANSITIONS[cmd.name]
    current = store.run_state(cmd.run_id)
    if current not in allowed:
        raise NotAuthorized(
            f"{cmd.name} is not legal from state {current!r}; "
            f"legal from {sorted(allowed)}"
        )

    if cmd.name in ("submit_spec", "submit_plan"):
        _check_submit(store, cmd, current)
        return next_state

    if cmd.name == "record_review":
        verdict = cmd.payload.get("verdict")
        if verdict not in _VERDICT_WORDS:
            raise NotAuthorized(f"verdict {verdict!r} is not one of {sorted(_VERDICT_WORDS)}")
        return _review_destination(store, cmd.run_id, current, verdict, ruling)

    if cmd.name == "record_turn_ended":
        if cmd.payload.get("ended") not in TURN_ENDS:
            raise NotAuthorized(
                f"ended {cmd.payload.get('ended')!r} is not one of {sorted(TURN_ENDS)}"
            )
        if not isinstance(cmd.payload.get("session"), str) or not cmd.payload["session"]:
            raise NotAuthorized("record_turn_ended names the session whose turn ended")
        return None

    if cmd.name == "park":
        _check_park(store, cmd)
        return None

    if cmd.name == "record_human_answer":
        ids = cmd.payload.get("question_ids")
        if not isinstance(ids, list) or not all(isinstance(q, str) for q in ids):
            raise NotAuthorized("record_human_answer question_ids must be a list of ids")
        if not isinstance(cmd.payload.get("answer"), str) or not cmd.payload.get("answer").strip():
            raise NotAuthorized("record_human_answer carries a non-empty answer")
        if cmd.payload.get("cursor_item_id") is not None and not isinstance(cmd.payload.get("cursor_item_id"), str):
            raise NotAuthorized("cursor_item_id must be a string or null")
        return None

    if cmd.name == "record_human_direction":
        if not isinstance(cmd.payload.get("text"), str) or not cmd.payload.get("text").strip():
            raise NotAuthorized("record_human_direction carries non-empty text")
        if cmd.payload.get("cursor_item_id") is not None and not isinstance(cmd.payload.get("cursor_item_id"), str):
            raise NotAuthorized("cursor_item_id must be a string or null")
        return None

    if cmd.name == "approve_artifact":
        phase = phase_of(current)
        held = store.phase_artifact(cmd.run_id, phase)
        if cmd.payload.get("artifact_hash") != held:
            raise NotAuthorized(
                f"approve_artifact names {str(cmd.payload.get('artifact_hash'))[:12]}..., but "
                f"the {phase} phase's current artefact is {str(held)[:12]}...: the kernel holds "
                "the hash; the caller can only supply the right answer"
            )
        return "specified" if phase == "spec" else "planned"

    if cmd.name == "grant_round":
        from kernel import front
        if front.current_park(store, cmd.run_id) is None:
            raise NotAuthorized(
                "grant_round needs a current park: nothing is stalled, and a grant "
                "at a running seat would displace it"
            )
        return None

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

    if cmd.name == "record_run_outcome":
        outcome = cmd.payload.get("outcome")
        if outcome not in _RUN_OUTCOMES:
            raise NotAuthorized(
                f"outcome {outcome!r} is not one of {sorted(_RUN_OUTCOMES)}"
            )
        # The one outcome a mechanism can contradict. Without this, a run that
        # never merged anything could still close its ledger as `merged` --
        # the exact claim-outruns-evidence shape record_merge_outcome already
        # refuses one command away.
        if outcome == "merged" and not store.has_confirmed_effect(
            cmd.run_id, "merge"
        ):
            raise NotAuthorized(
                "no confirmed merge outcome for this run: a merged outcome "
                "reports what the mechanism observed, not what an actor claims"
            )
        return "ended"

    if cmd.name == "request_merge":
        # The authorization has to record a TARGET, or the effect has nothing
        # to be bound to and any PR satisfies it.
        pr, repo = cmd.payload.get("pr"), cmd.payload.get("repo")
        if pr in (None, "") or not isinstance(repo, str) or not repo:
            raise NotAuthorized(
                "request_merge must name the pr and repo it authorizes: an "
                "authorization with no target authorizes every target"
            )

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


def latest_merge_authorization(store, run_id: str) -> dict | None:
    """What the kernel authorized for merge, as the kernel recorded it."""
    latest = None
    for fact in store.facts_for(run_id):
        if fact.kind == EventKind.MERGE_AUTHORIZED:
            latest = fact.payload
    return latest


def revalidate_merge(store, run_id: str) -> dict:
    """Re-run merge authorization immediately before the effect executes.

    Reaching `merge_requested` RECORDS that authorization happened; it is not
    the authorization. Between the transition and the effect the world moves:
    the reviewed artifact can be deleted, a revision can land, CI can be
    superseded, the reviewer's independence can lapse. The effect path
    previously checked only the state name, and a merge executed with the
    reviewed artifact deleted out from under it.

    Every input here is re-derived from kernel state. Nothing is read from the
    effect's intent, which is supplied by the caller asking for the effect.
    """
    state = store.run_state(run_id)
    if state != "merge_requested":
        raise NotAuthorized(
            f"a merge effect requires the kernel to have authorized it: run "
            f"is {state!r}, not 'merge_requested'"
        )

    authorized = latest_merge_authorization(store, run_id)
    if authorized is None:
        raise NotAuthorized(
            "the run is in merge_requested with no recorded merge "
            "authorization: revalidation has nothing to check"
        )

    artifact = authorized.get("artifact_hash")
    if not store.has_artifact(artifact):
        raise NotAuthorized(
            f"revalidation failed: the approved artifact {str(artifact)[:12]}... "
            "is no longer held by the kernel"
        )
    current = store.current_artifact(run_id)
    if artifact != current:
        raise NotAuthorized(
            "revalidation failed: the run's current output moved after the "
            "merge was authorized"
        )
    if not _merge_is_authorized(store, run_id, authorized):
        raise NotAuthorized(
            "revalidation failed: no accepted review binds the authorized "
            "inputs any more"
        )
    if not _ci_is_green(store, run_id, authorized.get("head_git_sha")):
        raise NotAuthorized(
            "revalidation failed: CI is no longer green on the authorized head"
        )

    reviewer = _reviewer_of(store, run_id, binding_hash(_binding_from(authorized)))
    if reviewer is None:
        raise NotAuthorized(
            "revalidation failed: no kernel-recorded reviewer for the "
            "authorized binding"
        )
    if reviewer in _conflicted_actors(store, run_id):
        raise NotAuthorized(
            f"revalidation failed: {reviewer!r} is now both reviewer and "
            "implementer of this run"
        )
    return authorized


def _reviewer_of(store, run_id: str, wanted: str) -> str | None:
    """Who the kernel recorded as accepting *wanted*, latest first."""
    reviewer = None
    for fact in store.facts_for(run_id):
        if (
            fact.kind == EventKind.REVIEW_VERDICT
            # The same phase filter `_merge_is_authorized` applies: the
            # reviewer this names is the one whose accept authorized the
            # merge, so it must be read from the same set of verdicts.
            and fact.payload.get("phase") in (None, "implementation")
            and fact.payload.get("binding_hash") == wanted
            and fact.payload.get("verdict") == "accept"
        ):
            reviewer = fact.payload.get("reviewer_identity")
    return reviewer
