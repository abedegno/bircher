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
from kernel.dispatch import Role, actor_for, role_for
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

#: The shaping states (shaping spec §2 *Which state set each site reads*).
#: DELIBERATELY NOT part of FRONT_HALF_STATES: that set is the from-set of
#: record_model_question and record_model_ruling, and joining it would
#: legalise both from the shaping states -- the second of them writing the
#: very model_ruling shape record_one_piece records, with none of its
#: guards. Membership is stated per site below, never inherited (ruling 13).
SHAPING_STATES = frozenset({"shaping", "slices_submitted", "slices_accepted"})

_PHASE_OF_STATE = {
    "shaping": "slices", "slices_submitted": "slices", "slices_accepted": "slices", "sliced": "slices",
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

#: spec §2 Refusals: every output-recording command is refused until the
#: round's session carries a turn_ended newer than its newest satisfied
#: sess-prompt, and that turn's sess-stop is satisfied. `record_review` under
#: a review_ruling joins this set too, checked separately in authorize()
#: because its role is REVIEWER rather than AUTHOR.
OUTPUT_COMMANDS = frozenset({
    "submit_spec", "submit_plan", "submit_slices", "record_one_piece",
    "record_author_empty", "record_model_question", "record_model_ruling",
})

#: Every non-terminal state. `record_run_outcome` and `cancel_run` are legal
#: from all of them, so listing them by hand meant each new front-half state
#: had to be remembered in two more places.
_ALL_ACTIVE = frozenset({
    "shaping", "slices_submitted", "slices_accepted", "sliced",
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
    # The shaping phase (shaping spec §2 *States*): the ruling and the plan
    # leave `shaping` by different doors; the ungated advance is the
    # coordinator's own fact, separate from the reviewer's accept, because
    # the transition that authorises filing must be its own fact.
    "record_one_piece": (frozenset({"shaping"}), "queued"),
    "submit_slices": (frozenset({"shaping"}), "slices_submitted"),
    "advance_ungated": (frozenset({"slices_accepted"}), "sliced"),
    # The filing and closing facts (shaping spec §2): no transition; legal
    # only from `sliced`, under the coordinator's or the sweep's operator
    # dispatch (checked in authorize).
    "record_slice_filed": (frozenset({"sliced"}), None),
    "record_filing_complete": (frozenset({"sliced"}), None),
    "record_slice_closed": (frozenset({"sliced"}), None),
    "record_slice_reopened": (frozenset({"sliced"}), None),
    "record_children_observed_closed": (frozenset({"sliced"}), None),
    "start_implementation": (frozenset({"planned"}), "implementing"),
    # Destination depends on state, verdict and the run's gates: computed in
    # authorize() by _review_destination. In the back half a revision request
    # must return the run to `planned` so implementation can start again;
    # landing every review in `reviewing` left it nowhere to do the revision.
    "record_review": (
        frozenset({"slices_submitted", "slices_accepted", "spec_submitted", "spec_accepted",
                   "plan_submitted", "plan_accepted", "implementing", "reviewing"}),
        None,
    ),
    "record_turn_ended": (
        frozenset({"shaping", "slices_submitted", "queued", "specified", "spec_submitted", "plan_submitted"}), None,
    ),
    # The membership table (shaping spec §2): the carrier session's items and
    # the gate park are legal in the shaping states too; the model's two
    # channels are NOT.
    "park": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # merge_requested must not be a dead end. Without an outbound transition a
    # merge that comes back uncertain can never be retried after
    # reconciliation, and the only escape -- cancel_run -- records 'cancelled'
    # for a run that in fact merged, corrupting the terminal outcome.
    "record_merge_outcome": (frozenset({"merge_requested"}), None),
    # The human's commands (spec §2 Commands), reachable only through
    # execute_as_human -- checked in authorize() below.
    "record_human_answer": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # The three author-round states.
    "record_human_direction": (frozenset({"queued", "specified", "shaping"}), None),
    # Destination by phase: computed in authorize().
    "approve_artifact": (frozenset({"slices_accepted", "spec_accepted", "plan_accepted"}), None),
    "grant_round": (frozenset({"shaping", "slices_submitted", "queued", "specified",
                               "spec_submitted", "plan_submitted"}), None),
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
    # The model's questions and rulings (spec §2 Commands): every front-half
    # state, since a grill can happen at any point before planned. Neither
    # transitions; a question and a ruling are facts about the epoch, not
    # moves in it. DELIBERATELY NOT extended with SHAPING_STATES (shaping
    # spec §2): refused from every shaping state, because record_model_ruling
    # writes the very fact shape record_one_piece writes, with none of its
    # guards -- and the `shape` question id is refused from anywhere.
    "record_model_question": (FRONT_HALF_STATES, None),
    "record_model_ruling": (FRONT_HALF_STATES, None),
    # The author's report that a turn produced nothing (spec §2 Commands):
    # only `shaping`, `queued` and `specified`, the three states an author
    # round can start from. Does not transition -- the RC_FAILED escalation is
    # the coordinator's, not a kernel move.
    "record_author_empty": (frozenset({"shaping", "queued", "specified"}), None),
    # The coordinator's dismissal of a refused human token (spec §2 Commands):
    # every front-half and shaping state, any role. Does not transition -- it
    # is a reply to a rejection, not a move.
    "dismiss_human_item": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # The coordinator's record of an omnigent prompt item it has already sent
    # (spec §2 Commands): every front-half and shaping state, any role. Does
    # not transition -- it is an observation of what was sent.
    "record_prompt_item": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # Every revision lands in `shaping`, the epoch's first state (ruling 11):
    # a materially changed issue may have changed size. Refused from `sliced`
    # by this very from-set: its children are the work now (ruling 7).
    "revise_bundle": (FRONT_HALF_STATES | SHAPING_STATES, "shaping"),
    # The kernel renders the reviewer's brief (spec §2 *Brief*): legal only
    # from the states a reviewer seat is dispatched into. Does not
    # transition -- it is an artefact the seat reads, not a move.
    "issue_review_brief": (frozenset({"slices_submitted", "spec_submitted", "plan_submitted"}), None),
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


#: Where a request_revision -- the reviewer's or the human's -- returns the
#: run: each phase's one revision destination (shaping spec §2 *States*).
_REVISION_DESTINATION = {"slices": "shaping", "spec": "queued", "plan": "specified"}
#: Where the human's approval lands each phase (shaping spec §2).
_APPROVAL_DESTINATION = {"slices": "sliced", "spec": "specified", "plan": "planned"}


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
        if state not in FRONT_HALF_STATES | SHAPING_STATES:
            raise NotAuthorized("a human record_review is front-half only (spec §2 Commands)")
        if verdict != "request_revision":
            raise NotAuthorized(
                "the human's record_review is request_revision; approval is "
                "approve_artifact, after the reviewer"
            )
        return _REVISION_DESTINATION[phase]
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
        from kernel import front
        n = front.epoch(store, run_id)
        used, bound = front.rounds_used(store, run_id, phase, n), front.round_bound(store, run_id, phase, n)
        if used >= bound:
            raise NotAuthorized(
                f"max_rounds: {used} request_revision rulings already in {phase}/epoch {n} "
                f"against a bound of {bound}; the loop parks bound_exhausted and a human retry grants one"
            )
        return _REVISION_DESTINATION[phase]
    gates = policy_of(store, run_id).gates
    if phase == "slices":
        # WHATEVER the gates (shaping spec §2): the ungated path leaves
        # through advance_ungated, its own fact, not through the accept.
        return "slices_accepted"
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
    # sliced: the sweep's terminal record for an epic whose children all
    # closed (shaping spec §2); guarded in authorize.
    "sliced",
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
        _check_cursor(cmd)
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
    issued = front.brief_for(store, cmd.run_id, cmd.generation)
    if issued is None:
        raise NotAuthorized(
            f"generation {cmd.generation} carries no review_brief_issued: a front-half "
            "review_ruling binds the brief the kernel rendered for its seat"
        )
    bp = issued.payload
    if (bp["phase"], bp["artifact_hash"], bp["context_bundle_hash"], bp["policy_version"], bp["base_sha"]) != (
            phase, binding.artifact_hash, binding.context_bundle_hash, binding.policy_version, binding.base_sha):
        raise NotAuthorized(
            "review_ruling differs from its generation's review_brief_issued in phase, "
            "artifact_hash, context_bundle_hash, policy_version or base_sha"
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
    seat = front.newest_seat(store, cmd.run_id, Role.REVIEWER, phase, epoch_n)
    named = None if seat is None else seat["session"].get("agent_name")
    if seat is None or front.vendor_of(named) != actor:
        raise NotAuthorized(
            f"reviewer {actor!r} is not the vendor of the bundle (agent_name {named!r}) the "
            f"newest satisfied reviewer sess-create of {phase}/epoch {epoch_n} names"
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


def _check_author_seat(store, cmd, state: str) -> None:
    """The author role and the vendor guard (ruling 14): the artefact was
    read from the round's session, and its author is the vendor that ran it.
    Shared by the submits and by record_one_piece."""
    from kernel import front
    if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    seat = front.newest_seat(store, cmd.run_id, Role.AUTHOR, phase, epoch_n)
    actor = actor_for(store, cmd.run_id, cmd.generation)
    named = None if seat is None else seat["session"].get("agent_name")
    if seat is None or front.vendor_of(named) != actor:
        raise NotAuthorized(
            f"{cmd.name}: the calling actor {actor!r} is not the vendor of the bundle "
            f"(agent_name {named!r}) the newest satisfied author sess-create of "
            f"{phase}/epoch {epoch_n} names: the artefact was read from that session, "
            "and its author is the vendor that ran it (ruling 14)"
        )


def _check_no_newer_direction(store, cmd) -> None:
    """spec §2 Refusals: a human_direction newer than this generation's own
    dispatch means a human interrupted the turn this output is the product
    of. The output of the turn it interrupted is not this run's to record;
    the direction starts a fresh round instead. Shared by the submits and by
    record_one_piece (shaping spec §2, round 2 finding 5)."""
    from kernel import front
    dispatched_seq = front.dispatch_seq(store, cmd.run_id, cmd.generation)
    if dispatched_seq is None:
        raise NotAuthorized(
            f"generation {cmd.generation} has no attempt_dispatched fact: nothing to "
            "order a human_direction against"
        )
    newer = front.direction_after(store, cmd.run_id, dispatched_seq)
    if newer is not None:
        raise NotAuthorized(
            f"a human_direction (seq {newer.seq}) is newer than generation "
            f"{cmd.generation}'s dispatch: the output of the turn it interrupted is not "
            "recorded; the direction starts a fresh round"
        )


def _check_submit(store, cmd, state: str) -> None:
    """What a submission has to be, before it becomes the phase's artefact.

    The author role, an artefact the kernel holds, and a change: an identical
    resubmission in the same phase and epoch is the loop spinning, not a
    revision; the plan additionally has to be a different object from the
    spec and to have tasks in it; the slice plan has to parse (shaping spec
    §2 *The artefact*) and the issue must not itself be a slice.
    """
    from kernel import front
    _check_author_seat(store, cmd, state)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if cmd.name == "submit_spec" and front.grill_open(store, cmd.run_id):
        raise NotAuthorized(
            "the grill is open: under grill=human the spec waits for a human_answer "
            "in this epoch newer than the newest model_question"
        )
    h = cmd.payload.get("artifact_hash")
    if not isinstance(h, str) or not store.has_artifact(h):
        raise NotAuthorized(f"{cmd.name} names an artefact the kernel does not hold: {h!r}")
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
    if cmd.name == "submit_slices":
        from kernel import slices as _slices
        # The grammar (shaping spec §2 *The artefact*): a shape check from the
        # bytes, the one judgement-shaped rule being the sentence bound.
        try:
            _slices.parse(store.read_blob(h))
        except _slices.PlanError as exc:
            raise NotAuthorized(f"submit_slices: {exc}") from exc
        # A slice is not sliced. The frozen BUNDLE cannot carry the label --
        # the canon strips every bircher: label as state, not input -- but
        # policy_frozen records the issue's labels unfiltered at creation,
        # and that is the observable (round 1, finding 1).
        if "bircher:slice" in front.frozen_labels(store, cmd.run_id):
            raise NotAuthorized(
                "a slice is not sliced: this issue carries bircher:slice in its policy_frozen "
                "labels; depth is one by construction (shaping spec §2)"
            )
    _check_no_newer_direction(store, cmd)


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_turn_ended(store, cmd, state: str) -> None:
    """spec §3 *The turn's end is a fact*: names the session the phase and
    epoch's newest satisfied sess-prompt names (ruling 15: the PHASE's
    newest, not the session's own -- record_turn_ended is how the kernel
    learns which session is even the round's), and refuses a second end for
    the same turn."""
    from kernel import front
    if cmd.payload.get("ended") not in TURN_ENDS:
        raise NotAuthorized(f"ended {cmd.payload.get('ended')!r} is not one of {sorted(TURN_ENDS)}")
    sid = cmd.payload.get("session")
    if not isinstance(sid, str) or not sid:
        raise NotAuthorized("record_turn_ended names the session whose turn ended")
    prompt = front.newest_prompt(store, cmd.run_id, phase_of(state), front.epoch(store, cmd.run_id))
    if prompt is None or prompt["session_id"] != sid:
        raise NotAuthorized(
            f"record_turn_ended names {sid}, which is not the session the newest satisfied "
            "sess-prompt of this phase and epoch names: one turn is awaited at a time"
        )
    if front.turn_ended_for(store, cmd.run_id, prompt["key"]) is not None:
        raise NotAuthorized(f"session {sid}'s awaited turn already ended: one end per turn")


def _check_cursor(cmd) -> None:
    """spec §4: every fact the coordinator records FROM A LISTING carries the
    cursor it had read to -- `human_answer`, `human_ruling`,
    `record_human_direction`, `parked`. The three ruling shapes
    (`approve_artifact`, `grant_round`, the human's `record_review`) write
    `human_ruling` facts and so carry it too. Without it a ruling that starts
    no new session and no new park -- `grant_round` is one; it transitions
    nothing -- leaves the item it was read from still ahead of the cursor,
    and the next listing reads that item again.

    Shape only. The value is the coordinator's own reading of a listing, a
    declared residual in the provenance table; what the kernel can check is
    that it is a string or absent. ONE literal `cmd.payload.get(...)` read
    shared by all five commands: the provenance extractor matches the literal
    syntactically, and five copies would be five chances to spell it
    differently.
    """
    if cmd.payload.get("cursor_item_id") is not None and not isinstance(cmd.payload.get("cursor_item_id"), str):
        raise NotAuthorized("cursor_item_id must be a string or null")


def _check_park(store, cmd) -> None:
    """A park records why a pass stopped. Bounded here so the reason is one
    of the six the loop has, not free text the coordinator invents."""
    if cmd.payload.get("reason") not in PARK_REASONS:
        raise NotAuthorized(f"park reason {cmd.payload.get('reason')!r} is not one of {sorted(PARK_REASONS)}")
    # Three literal `.get(...)` reads, not a loop over a variable key: the
    # provenance extractor matches `cmd.payload.get("literal")` syntactically,
    # and a dynamic key defeats it -- these three rows would then be unbound
    # to the source that actually reads them.
    if cmd.payload.get("session_id") is not None and not isinstance(cmd.payload.get("session_id"), str):
        raise NotAuthorized("park session_id must be a string or null")
    _check_cursor(cmd)
    if cmd.payload.get("reviewer") is not None and not isinstance(cmd.payload.get("reviewer"), str):
        raise NotAuthorized("park reviewer must be a string or null")
    if cmd.payload.get("findings_hash") is not None and not store.has_artifact(cmd.payload.get("findings_hash")):
        raise NotAuthorized("park findings_hash names an artefact the kernel does not hold")
    if cmd.payload.get("verdict") not in (None, "accept", "request_revision"):
        raise NotAuthorized("park verdict must be null, accept or request_revision")


def _require_turn_recorded(store, cmd, role: str) -> None:
    """spec §2 Refusals, the output-recording commands: the round's session
    carries a turn_ended newer than its newest satisfied sess-prompt, and
    the sess-stop that turn_ended is the cause of is satisfied."""
    from kernel import front
    state = store.run_state(cmd.run_id)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    seat = front.newest_seat(store, cmd.run_id, role, phase, epoch_n)
    if seat is None:
        raise NotAuthorized(
            f"{cmd.name}: no satisfied sess-create under a {role} generation of "
            f"{phase} in epoch {epoch_n}: there is no round's session to have ended"
        )
    sid = seat["session"]["id"]
    prompt = front.newest_prompt_of(store, cmd.run_id, sid, phase, epoch_n)
    if prompt is None:
        raise NotAuthorized(
            f"{cmd.name}: the round's session {sid} has no satisfied sess-prompt in "
            f"{phase}/{epoch_n}: no turn was awaited"
        )
    ended = front.turn_ended_for(store, cmd.run_id, prompt["key"])
    if ended is None:
        raise NotAuthorized(
            f"{cmd.name}: session {sid} carries no turn_ended newer than its newest "
            "satisfied sess-prompt: nothing a turn produced is recorded before the "
            "turn is observed ended"
        )
    if not front.stop_satisfied_for(store, cmd.run_id, ended.id):
        raise NotAuthorized(
            f"{cmd.name}: the sess-stop caused by turn_ended {ended.id} is not "
            "satisfied: the session is stopped before its output is read"
        )


def _check_operator(store, cmd) -> None:
    if role_for(store, cmd.run_id, cmd.generation) != Role.OPERATOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the operator role")


def _check_slice_in_plan(store, cmd, n) -> None:
    """A payload `slice` is observed: refused unless the accepted plan has it."""
    from kernel import front
    plan = front.accepted_plan(store, cmd.run_id)
    if plan is None or type(n) is not int or n not in plan.by_number():
        raise NotAuthorized(f"{cmd.name}: slice {n!r} is not in the accepted plan")


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

    # spec §2 Refusals: every output-recording command waits for the round's
    # session to have ended its turn and for that turn's stop to be
    # satisfied. record_review joins this only under a review_ruling and only
    # in the front half -- the back half's record_review runs the PR review
    # through `omnigent run`, not a session.
    if cmd.name in OUTPUT_COMMANDS:
        _require_turn_recorded(store, cmd, Role.AUTHOR)
    if cmd.name == "record_review" and ruling == "review_ruling" and current in FRONT_HALF_STATES | SHAPING_STATES:
        _require_turn_recorded(store, cmd, Role.REVIEWER)

    if cmd.name in ("record_model_question", "record_model_ruling"):
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
            raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
        qid = cmd.payload.get("question_id")
        if not isinstance(qid, str) or not qid:
            raise NotAuthorized(f"{cmd.name} names a question_id")
        from kernel.slices import SHAPE_QUESTION
        if qid == SHAPE_QUESTION:
            raise NotAuthorized(
                f"question_id {SHAPE_QUESTION!r} is reserved to record_one_piece (shaping spec §2): "
                "no path writes a shape ruling without that command's guards"
            )
        if cmd.name == "record_model_question":
            if not isinstance(cmd.payload.get("question"), str) or not cmd.payload.get("question").strip():
                raise NotAuthorized("record_model_question carries a non-empty question")
            return None
        # Literal `.get(...)` calls, not a loop over a variable key: the
        # provenance extractor (test_provenance.py::authorization_inputs)
        # matches `cmd.payload.get("literal")` syntactically, and a dynamic
        # key defeats it -- the three rows the table declares would then be
        # unbound to the source that actually reads them.
        for name, value in (
            ("ruling", cmd.payload.get("ruling")),
            ("reasoning", cmd.payload.get("reasoning")),
            ("cost_if_wrong", cmd.payload.get("cost_if_wrong")),
        ):
            if not _non_empty_str(value):
                raise NotAuthorized(f"record_model_ruling carries a non-empty {name}")
        n = front.epoch(store, cmd.run_id)
        asked = {q.payload["question_id"] for q in front.epoch_facts(store, cmd.run_id, EventKind.MODEL_QUESTION, n)}
        if qid not in asked:
            raise NotAuthorized(
                f"record_model_ruling names question_id {qid!r}, which no "
                f"model_question of epoch {n} asked"
            )
        return None

    if cmd.name == "dismiss_human_item":
        from kernel import front
        if not isinstance(cmd.payload.get("cursor_item_id"), str) or not cmd.payload.get("cursor_item_id"):
            raise NotAuthorized("dismiss_human_item carries the cursor to move past")
        # WHICH SESSION the refused token was read from (spec §4: the reply
        # goes back to that session, once). Bound to a session this run
        # actually created, so the fact cannot name a session the reply could
        # never reach -- `reply_refusals` derives its obligation from this
        # fact alone, and an unbound value would send the reply somewhere the
        # human is not looking, or nowhere at all.
        sid = cmd.payload.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise NotAuthorized("dismiss_human_item names the session the item was read from")
        if sid not in front.created_sessions(store, cmd.run_id):
            raise NotAuthorized(
                f"dismiss_human_item names session {sid!r}, which no satisfied sess-create of "
                "this run delivered"
            )
        rej = cmd.payload.get("rejection")
        if not isinstance(rej, str) or not rej:
            raise NotAuthorized("dismiss_human_item names the command_rejected fact it dismisses")
        human = {f.id: f for f in front.human_rejections(store, cmd.run_id)}
        if rej not in human:
            raise NotAuthorized(
                f"dismiss_human_item names {rej!r}, which is not a command_rejected fact "
                "of this run attributed to human"
            )
        if rej in front.dismissed_rejection_ids(store, cmd.run_id):
            raise NotAuthorized(f"rejection {rej!r} is already dismissed: one reply per refusal")
        return None

    if cmd.name == "record_prompt_item":
        from kernel import front
        # Literal `.get(...)` calls throughout, for the same reason as
        # record_model_ruling above: the shape check and the read must both
        # be literal, so the provenance extractor sees all three regardless
        # of which line it walks.
        sid, item, sha = (cmd.payload.get("session_id"), cmd.payload.get("item_id"),
                          cmd.payload.get("sha256"))
        for name, value in (("session_id", sid), ("item_id", item), ("sha256", sha)):
            if not isinstance(value, str) or not value:
                raise NotAuthorized(f"record_prompt_item carries {name}")
        if sha not in front.prompt_hashes_of(store, cmd.run_id, sid):
            raise NotAuthorized(
                f"record_prompt_item: no satisfied sess-prompt of session {sid!r} carries body.artifact {sha[:12]}..."
            )
        # Per session: omnigent's item ids are not shown to be unique across sessions.
        if any(f.payload["item_id"] == item and f.payload["session_id"] == sid
               for f in store.facts_of_kind(cmd.run_id, EventKind.PROMPT_ITEM)):
            raise NotAuthorized(f"item {item!r} of session {sid!r} is already recorded as a prompt_item")
        return None

    if cmd.name == "revise_bundle":
        from kernel import bundle as _bundle
        from kernel import front
        issue = cmd.payload.get("issue")
        if not isinstance(issue, dict):
            raise NotAuthorized("revise_bundle carries the fetched issue as an object")
        try:
            new_hash = _bundle.bundle_hash(_bundle.snapshot(issue))
        except (KeyError, TypeError, ValueError) as exc:
            raise NotAuthorized(f"revise_bundle: malformed issue: {exc}") from exc
        prior_hash = front.bundle_hash(store, cmd.run_id)
        # A v1 run's RUN_ENQUEUED names a bundle_hash whose bytes were never
        # PUT (enqueue() computed the hash but only persisted spec and plan),
        # and a bare Store.create_run has no RUN_ENQUEUED at all -- either way
        # there is nothing here to diff against. Without this check, _side_fact
        # crashes mid-transaction on `json.loads(store.read_blob(None))` with
        # no command_rejected recorded.
        if prior_hash is None or store.read_blob(prior_hash) is None:
            raise NotAuthorized(
                "revise_bundle: the current bundle's bytes are not in the store "
                "(a v1 run); nothing to diff against"
            )
        if new_hash == prior_hash:
            raise NotAuthorized(
                "revise_bundle: no relevant change -- the frozen fields hash as the current bundle"
            )
        return next_state

    if cmd.name == "issue_review_brief":
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.REVIEWER:
            raise NotAuthorized("issue_review_brief must come from an attempt dispatched in the reviewer role")
        if cmd.payload.get("phase") != phase_of(current):
            raise NotAuthorized(
                f"issue_review_brief names phase {cmd.payload.get('phase')!r}, but the run is at "
                f"{current!r} ({phase_of(current)})"
            )
        if front.brief_for(store, cmd.run_id, cmd.generation) is not None:
            raise NotAuthorized(f"generation {cmd.generation} already carries a review_brief_issued")
        return None

    if cmd.name in ("submit_spec", "submit_plan", "submit_slices"):
        _check_submit(store, cmd, current)
        return next_state

    if cmd.name == "record_one_piece":
        from kernel import front
        # Literal reads (the provenance extractor): the two strings a ruling
        # carries, already declared residuals for record_model_ruling.
        for name, value in (("reasoning", cmd.payload.get("reasoning")),
                            ("cost_if_wrong", cmd.payload.get("cost_if_wrong"))):
            if not _non_empty_str(value):
                raise NotAuthorized(f"record_one_piece carries a non-empty {name}")
        _check_author_seat(store, cmd, current)
        _check_no_newer_direction(store, cmd)
        n = front.epoch(store, cmd.run_id)
        if front.shape_ruling(store, cmd.run_id, n) is not None:
            raise NotAuthorized(f"a shape ruling already exists in epoch {n}: one decision per epoch (shaping spec §2)")
        return next_state

    if cmd.name == "advance_ungated":
        from kernel.policy import policy_of
        if role_for(store, cmd.run_id, cmd.generation) != Role.OPERATOR:
            raise NotAuthorized("advance_ungated must come from an attempt dispatched in the operator role")
        if "slices" in policy_of(store, cmd.run_id).gates:
            raise NotAuthorized(
                "advance_ungated: this run's policy gates slices; only approve_artifact "
                "moves on from slices_accepted"
            )
        return next_state

    if cmd.name == "record_slice_filed":
        import json as _json
        from kernel import front
        _check_operator(store, cmd)
        n, key = cmd.payload.get("slice"), cmd.payload.get("effect_key")
        _check_slice_in_plan(store, cmd, n)
        epoch_n = front.epoch(store, cmd.run_id)
        row = None if not isinstance(key, str) else store.effect_by_key(key, run_id=cmd.run_id)
        if row is None or row["effect_class"] != "issue_create" or not front.is_satisfied(row):
            raise NotAuthorized(f"record_slice_filed: {key!r} is not a satisfied issue_create of this run")
        want = {"kind": "slice_issue", "run": cmd.run_id, "epoch": epoch_n, "slice": n,
                "parent": front.issue_number(store, cmd.run_id),
                "plan_hash": front.accepted_slices_hash(store, cmd.run_id, epoch_n)}
        if (row["intent"].get("obligation") or {}) != want:
            raise NotAuthorized(f"record_slice_filed: {key}'s obligation {row['intent'].get('obligation')} is not {want}")
        try:
            delivered = _json.loads(row["external_object_id"])
            int(delivered["number"]); int(delivered["id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise NotAuthorized(f"record_slice_filed: {key}'s delivered value is not {{url, number, id}}") from exc
        if n in front.slices_filed(store, cmd.run_id, epoch_n):
            raise NotAuthorized(f"slice {n} already has a slice_filed in epoch {epoch_n}")
        return None

    if cmd.name == "record_filing_complete":
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is not None:
            raise NotAuthorized(f"filing_complete already recorded in epoch {epoch_n}")
        filed = front.slices_filed(store, cmd.run_id, epoch_n)
        for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
            if s.number not in filed:
                raise NotAuthorized(f"record_filing_complete: slice {s.number} has no slice_filed")
        owed = front.unsatisfied_filing(store, cmd.run_id, epoch_n)
        if owed:
            raise NotAuthorized(f"record_filing_complete: {len(owed)} obligation(s) unsatisfied, first {owed[0]}")
        return None

    if cmd.name in ("record_slice_closed", "record_slice_reopened"):
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is None:
            raise NotAuthorized(f"{cmd.name}: no filing_complete in epoch {epoch_n}; the sweep reads only a filed epic")
        n = cmd.payload.get("slice")
        if n not in front.slices_filed(store, cmd.run_id, epoch_n):
            raise NotAuthorized(f"{cmd.name}: no slice_filed for slice {n!r} in epoch {epoch_n}")
        cur = front.current_closure(store, cmd.run_id, epoch_n, n)
        if cmd.name == "record_slice_closed":
            if cmd.payload.get("state") not in ("merged", "closed"):
                raise NotAuthorized("record_slice_closed state is merged or closed")
            if not _non_empty_str(cmd.payload.get("closed_at")):
                raise NotAuthorized("record_slice_closed carries closed_at")
            if cur is not None and cur.kind == EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {n} is currently closed: a closure is recorded once until a reopening")
        else:
            if not _non_empty_str(cmd.payload.get("observed_at")):
                raise NotAuthorized("record_slice_reopened carries observed_at")
            if cur is None:
                raise NotAuthorized(f"slice {n} was never closed: nothing to reopen")
            if cur.kind != EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {n} is not currently closed")
        return None

    if cmd.name == "record_children_observed_closed":
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is None:
            raise NotAuthorized(f"no filing_complete in epoch {epoch_n}")
        if cmd.payload.get("parent_state") not in ("open", "closed"):
            raise NotAuthorized("record_children_observed_closed parent_state is open or closed")
        if not _non_empty_str(cmd.payload.get("observed_at")):
            raise NotAuthorized("record_children_observed_closed carries observed_at")
        for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
            cur = front.current_closure(store, cmd.run_id, epoch_n, s.number)
            if cur is None or cur.kind != EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {s.number} is not currently closed: the statement cannot contradict the closing facts")
        if front.observed_closed_under(store, cmd.run_id, epoch_n, cmd.generation) is not None:
            raise NotAuthorized(f"children_observed_closed already recorded under generation {cmd.generation}")
        return None

    if cmd.name == "record_review":
        verdict = cmd.payload.get("verdict")
        if verdict not in _VERDICT_WORDS:
            raise NotAuthorized(f"verdict {verdict!r} is not one of {sorted(_VERDICT_WORDS)}")
        return _review_destination(store, cmd.run_id, current, verdict, ruling)

    if cmd.name == "record_turn_ended":
        _check_turn_ended(store, cmd, current)
        return None

    if cmd.name == "record_author_empty":
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
            raise NotAuthorized("record_author_empty must come from an attempt dispatched in the author role")
        sid = cmd.payload.get("session")
        seat = front.newest_seat(store, cmd.run_id, Role.AUTHOR, phase_of(current), front.epoch(store, cmd.run_id))
        if seat is None or seat["session"]["id"] != sid:
            raise NotAuthorized(
                f"record_author_empty names {sid!r}, which is not the session the newest "
                "satisfied author sess-create of this phase and epoch delivered"
            )
        cause = seat["cause"]
        if any(f.id == cause for f in store.facts_of_kind(cmd.run_id, EventKind.AUTHOR_EMPTY)):
            raise NotAuthorized(
                f"session {sid} is itself the retry (its create's cause is an author_empty): "
                "a second empty turn is RC_FAILED, not a third session"
            )
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
        _check_cursor(cmd)
        return None

    if cmd.name == "record_human_direction":
        if not isinstance(cmd.payload.get("text"), str) or not cmd.payload.get("text").strip():
            raise NotAuthorized("record_human_direction carries non-empty text")
        _check_cursor(cmd)
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
        _check_cursor(cmd)
        return _APPROVAL_DESTINATION[phase]

    if cmd.name == "grant_round":
        from kernel import front
        if front.current_park(store, cmd.run_id) is None:
            raise NotAuthorized(
                "grant_round needs a current park: nothing is stalled, and a grant "
                "at a running seat would displace it"
            )
        _check_cursor(cmd)
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
        # A run that entered `sliced` may have filed children; `failed` over
        # them would end the epic with its filing half done and nothing left
        # to repair it (shaping spec §2, ruling 15). cancel_run is the other exit.
        # BEFORE the `merged` check below, not after: from `sliced` the STATE
        # refuses every other outcome, `merged` included, and the refusal that
        # names why is the state's one -- "no confirmed merge effect" would
        # invite a caller to go and get one.
        if current == "sliced" and outcome != "sliced":
            raise NotAuthorized(f"a run that entered sliced ends only as sliced, not {outcome!r}; cancel_run is the other exit")
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
        if outcome == "sliced":
            from kernel import front
            if current != "sliced":
                raise NotAuthorized("outcome sliced is legal only from sliced")
            _check_operator(store, cmd)
            epoch_n = front.epoch(store, cmd.run_id)
            if front.filing_complete(store, cmd.run_id, epoch_n) is None:
                raise NotAuthorized("outcome sliced: no filing_complete in this epoch")
            for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
                cur = front.current_closure(store, cmd.run_id, epoch_n, s.number)
                if cur is None or cur.kind != EventKind.SLICE_CLOSED:
                    raise NotAuthorized(f"outcome sliced: slice {s.number} is not currently closed")
            obs = front.observed_closed_under(store, cmd.run_id, epoch_n, cmd.generation)
            if obs is None:
                raise NotAuthorized(
                    "outcome sliced: no children_observed_closed under this generation -- the firing "
                    "that ends the run is the firing that read every child (ruling 17)"
                )
            base = {"run": cmd.run_id, "epoch": epoch_n}
            if front.satisfied_obligation(store, cmd.run_id, dict(base, kind="umbrella_close")) is None:
                raise NotAuthorized("outcome sliced: the umbrella_close comment is not satisfied")
            # A hand-closed parent owes no close; a satisfied close beside an
            # observed-open parent is a person's reopening (ruling 22).
            if obs.payload.get("parent") == "open" and \
                    front.satisfied_obligation(store, cmd.run_id, dict(base, kind="parent_close")) is None:
                raise NotAuthorized("outcome sliced: the parent was observed open and its parent_close is not satisfied")
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
