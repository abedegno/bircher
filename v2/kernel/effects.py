"""The effect journal, defined by semantic effect class.

Persist intent before invoking the effect; carry an idempotency key; record the
external object identifier; reconcile an uncertain result before retrying.
Every journalled mutation is a generation-fenced resource.
"""

from __future__ import annotations

from kernel.dispatch import actor_for
from kernel.events import EventKind
from kernel.ids import new_id
from kernel.mode import shadow_or_raise
from kernel.ownership import OwnershipLost, current_generation


class EffectClass:
    REF_UPDATE = "ref_update"
    PULL_REQUEST = "pull_request"
    # Merge is its own class. Folding it into PULL_REQUEST meant the effect
    # journal could not distinguish opening a PR from merging one, and the
    # authority-bearing operation shared a gate with the routine one.
    MERGE = "merge"
    STATUS_CHECK = "status_check"
    COMMENT = "comment"
    ISSUE_OR_LABEL = "issue_or_label"
    REVERT_OR_RECOVERY = "revert_or_recovery"
    CREDENTIAL_LIFECYCLE = "credential_lifecycle"
    SESSION_CONTROL = "session_control"
    ALL = frozenset({
        REF_UPDATE, PULL_REQUEST, MERGE, STATUS_CHECK, COMMENT,
        ISSUE_OR_LABEL, REVERT_OR_RECOVERY, CREDENTIAL_LIFECYCLE, SESSION_CONTROL,
    })


class NotReplayable(Exception):
    """A resolved effect's idempotency key cannot carry a fresh attempt."""


class UncertainEffect(Exception):
    """The effect's outcome is unknown. It must be reconciled before retry."""


def enter_reconciliation_required(store, run_id: str, evidence: dict) -> None:
    """Halt this run pending human reconciliation.

    A durable state, not a silent stall: it records what an operator needs to
    act. Only this run halts -- the conflict is per-run, because an unconfirmed
    attempt holds this run's resources and nothing else.
    """
    store.set_reconciliation(run_id, evidence)


def is_halted(store, run_id: str) -> bool:
    return store.reconciliation_evidence(run_id) is not None


def perform(store, run_id, generation, effect_class, idempotency_key, intent, executor):
    """Perform one externally visible effect, journalling intent first.

    There is deliberately no halt-bypass parameter: a comment saying
    production never passes one is not an enforcement, and any caller could
    have continued mutating a halted run. Tests that need the inner checks
    call :func:`_perform_unhalted` directly.
    """
    # The effect path carries its own authorization. Gating only submit() left
    # the authority-bearing operation ungated: a current owner could execute a
    # merge through perform() without an accepted verdict, green CI, or ever
    # reaching merge_requested. Rechecked HERE, immediately before execution,
    # because authorization granted at transition time can go stale.
    from kernel.authz import NotAuthorized
    from kernel.contract import (
        CONTRACTS, ContractViolation, check, merge_target,
    )

    # The class must describe what the argv actually does. Without this the
    # class is a label the caller picks, and picking a non-merge label skipped
    # the merge gate entirely. Checked for EVERY class, before anything else:
    # an effect whose shape the kernel cannot account for does not run.
    # An effect that runs no command is not an effect. This used to be
    # `if argv:` -- so an EMPTY intent skipped the contract check AND, below,
    # the merge-target check: `perform(MERGE, intent={})` executed and neither
    # ran. A guard that applies only when the caller supplies something to
    # guard is not a guard, and "no production caller does that" is the same
    # reasoning that nearly dismissed the PATH-resolution finding.
    argv = list(intent.get("argv") or [])
    if CONTRACTS.get(effect_class) is not None and not argv:
        raise NotAuthorized(
            f"{effect_class} declares an argv contract, so an effect of that "
            "class must carry a command; an empty intent would skip every "
            "check the contract exists to make"
        )
    if argv:
        try:
            check(effect_class, argv)
        except ContractViolation as exc:
            shadow_or_raise(store, run_id, NotAuthorized(str(exc)), idempotency_key,
                            effect_class=effect_class, argv=argv[:6])

    if effect_class == EffectClass.MERGE:
        from kernel.authz import revalidate_merge

        # The full authorization, re-derived from kernel state, immediately
        # before execution. Checking only `state == "merge_requested"` treated
        # a record THAT authorization happened as the authorization itself --
        # and a merge executed with the reviewed artifact deleted in between.
        authorized = revalidate_merge(store, run_id)

        # ...and the effect must act on what was authorized. Revalidation
        # proves that A merge is authorized for this run; without this, an
        # authorized run merged PR 9999 in someone else's repository.
        pr, repo = merge_target(argv)
        if True:
            if str(pr) != str(authorized.get("pr")) or repo != authorized.get("repo"):
                raise NotAuthorized(
                    f"merge targets pr={pr!r} repo={repo!r}, but the kernel "
                    f"authorized pr={authorized.get('pr')!r} "
                    f"repo={authorized.get('repo')!r}"
                )

    if is_halted(store, run_id):
        raise RuntimeError(
            f"run {run_id} is halted pending reconciliation; resolve it before "
            "performing further effects"
        )
    return _perform_unhalted(
        store, run_id, generation, effect_class, idempotency_key, intent, executor
    )


def _halt_evidence(store, run_id, generation, effect_class, idempotency_key) -> dict:
    """What an operator needs to resolve this halt. Shared by both paths that
    raise it, so a retry-triggered halt is as actionable as an original one."""
    return {
        "run_id": run_id,
        "generation": generation,
        "affected_resources": [effect_class],
        "last_confirmed_observations": store.last_confirmed(run_id),
        "stop_attempts": 0,
        "recommended_actions": [
            f"Check whether the {effect_class} succeeded externally",
            f"Then reconcile(store, {run_id!r}, {idempotency_key!r}, resolution, version)",
        ],
    }


def _perform_unhalted(
    store, run_id, generation, effect_class, idempotency_key, intent, executor
):
    """The effect path with the run-level halt already checked by the caller."""
    from kernel.authz import NotAuthorized

    if effect_class not in EffectClass.ALL:
        raise ValueError(f"unknown effect class: {effect_class}")

    # Who asked. The journal recorded actor="kernel" on every effect fact,
    # which section 4b permits only for facts the kernel ORIGINATES. An effect
    # is requested by a dispatched attempt, and an external mutation the
    # journal cannot attribute is the same defect commands had -- in the half
    # of the system that actually touches the world.
    #
    # Resolved BEFORE the idempotency read, because that read RETURNS the
    # external object id of a confirmed effect. An undispatched caller placed
    # after it learns the id of a merge, PR or comment another attempt created
    # -- refused, but only after being told what it asked for.
    #
    # It does NOT protect the key from being consumed: effect_by_key is a read
    # and consumption happens at journal_intent, which is already past the
    # refusal either way. An earlier comment here claimed otherwise, and a
    # mutation moving the refusal below the read survived because the test
    # written from that comment checked the property the code did not have.
    actor = actor_for(store, run_id, generation)
    if actor is None:
        raise NotAuthorized(
            f"generation {generation} has no dispatched actor: an effect the "
            "journal cannot attribute is an unattributable external mutation"
        )

    existing = store.effect_by_key(idempotency_key, run_id=run_id)
    if existing is not None:
        if existing["state"] in ("uncertain", "intended"):
            # `intended` means journalled but never confirmed -- a crash,
            # KeyboardInterrupt or SystemExit between the two. Treating it as a
            # completed replay returned a null external id, neither executing
            # nor demanding reconciliation, and silently wedged the run.
            #
            # HALT HERE TOO. Only the original failure path used to halt, so a
            # process that died before its handler ran left the effect
            # `intended` -- and the retry raised while the run stayed live and
            # went on performing further external effects, which is exactly
            # the wedge the halt exists to prevent.
            if not is_halted(store, run_id):
                enter_reconciliation_required(store, run_id, _halt_evidence(
                    store, run_id, generation, existing["effect_class"],
                    idempotency_key,
                ))
            raise UncertainEffect(
                f"{idempotency_key} is {existing['state']!r} in run {run_id}: "
                "its outcome is unknown and it must be reconciled before retry"
            )
        if existing["state"] == "reconciled":
            # A RECONCILED key is spent, not replayable. Reconciliation resolves
            # the attempt that was made; it does not say a fresh one may reuse
            # its key, and the external id it leaves behind is None. Falling
            # through to the replay below returned that None without executing
            # -- and the caller could not tell "already done, here is the id"
            # from "resolved as never done, nothing happened".
            #
            # A live muesli run did exactly that: the merge was reconciled as
            # NOT landed, merge_ready_pr retried under the same
            # `merge:<pr>:<head>` key, got None back, retried five times for a
            # sha that could never arrive, and then reported the PR MERGED when
            # it was still open. Fail-closed halting is what stopped it going
            # further -- the wrong answer had already been logged.
            #
            # A retry after reconciliation is a NEW attempt and needs a new key.
            raise NotReplayable(
                f"{idempotency_key} was reconciled in run {run_id}: a resolved "
                "attempt cannot be replayed under its own key, because its "
                "recorded outcome describes that attempt and not this one. "
                "Retry under a new idempotency key."
            )
        return existing["external_object_id"]

    # Fence BEFORE journalling, so a superseded generation leaves no trace and
    # cannot consume an idempotency key a live generation may still need.
    if generation != current_generation(store, run_id):
        raise OwnershipLost(
            f"generation {generation} superseded; effect request carries no write capability"
        )

    eid = new_id("eff")
    store.journal_intent(eid, run_id, generation, effect_class, idempotency_key, intent)
    store.append_fact(
        run_id=run_id, kind=EventKind.EFFECT_INTENDED, actor=actor,
        causal_command_id=idempotency_key,
        payload={"effect_class": effect_class, "effect_id": eid},
    )
    try:
        external_id = executor(effect_class, intent, idempotency_key)
    except BaseException as exc:
        # BaseException, not Exception: KeyboardInterrupt and SystemExit are
        # exactly the crash-shaped interruptions that leave an effect's outcome
        # unknown, and catching only Exception left them as bare `intended`.
        store.mark_effect(idempotency_key, "uncertain", None, run_id=run_id)
        store.append_fact(
            run_id=run_id, kind=EventKind.EFFECT_UNCERTAIN, actor=actor,
            causal_command_id=idempotency_key,
            payload={"effect_id": eid, "error": type(exc).__name__,
                     # WHAT IT SAID, not just what it was. Without this a halt
                     # reads "RuntimeError" and nothing else, and the only way
                     # to learn why is to re-run the effect by hand against the
                     # live world -- which is exactly what a journal exists to
                     # make unnecessary. Diagnosing one halted publish cost
                     # three such round-trips. Capped, because the executor
                     # folds the failed command's stderr into this string.
                     "detail": str(exc)[:500]},
        )
        enter_reconciliation_required(store, run_id, _halt_evidence(
            store, run_id, generation, effect_class, idempotency_key))
        if not isinstance(exc, Exception):
            # KeyboardInterrupt / SystemExit: the uncertainty is now recorded
            # and the run halted, but the interrupt itself must propagate
            # unchanged. Converting it to UncertainEffect would swallow a
            # Ctrl-C and let the process carry on.
            raise
        raise UncertainEffect(
            f"{effect_class} outcome unknown ({type(exc).__name__}); reconcile before retry"
        ) from exc

    store.mark_effect(idempotency_key, "confirmed", external_id, run_id=run_id)
    store.append_fact(
        run_id=run_id, kind=EventKind.EFFECT_CONFIRMED, actor=actor,
        causal_command_id=idempotency_key,
        # The class travels with the CONFIRMATION as well as the intent. It
        # used to appear only on effect_intended, so the journal could be
        # filtered by class for what was attempted and not for what actually
        # landed -- and any reconciliation matching intents against
        # confirmations had to join through effect_id to learn what a
        # confirmation was even about.
        payload={"effect_id": eid, "external_object_id": external_id,
                 "effect_class": effect_class},
    )
    return external_id


def pending_reconciliation(store, run_id: str) -> list[dict]:
    return store.uncertain_effects(run_id)


def reconcile_many(store, run_id, keys, resolution, expected_version) -> int:
    """Resolve SEVERAL uncertain effects under ONE CAS, in one transaction.

    Why this exists rather than a loop in the caller. A CAS cannot distinguish
    the caller's own version bump from a foreign writer's, so resolving N keys
    one at a time is unsafe in both available shapes: re-reading the version
    absorbs a foreign change, and incrementing locally absorbs it one step
    later, because an advisory wrapper cannot confirm that the previous
    reconciliation actually happened. Two rounds of review were spent
    discovering that those are the same defect.

    The caller was then reduced to one key per invocation, which is correct and
    leaves a run with several uncertain effects halted with nothing owning the
    follow-up. The fix is not a cleverer caller: it is doing the whole thing
    atomically HERE, where the version is only bumped once and only by us.

    Returns the number resolved. Refuses the whole batch if any key is not
    unresolved -- a partial reconciliation is a state nobody asked for.
    """
    from kernel.commands import StaleVersion

    keys = list(keys)
    if not keys:
        return 0
    for key in keys:
        state = store.effect_state(key, run_id=run_id)
        if state not in ("uncertain", "intended"):
            raise ValueError(
                f"cannot reconcile {key!r} in run {run_id!r}: state is "
                f"{state!r}, expected 'uncertain' or 'intended'"
            )

    with store.transaction():
        if not store.bump_version_cas(run_id, expected_version):
            raise StaleVersion(
                f"reconciliation derived from version {expected_version}, "
                "which has moved"
            )
        for key in keys:
            store.mark_effect(key, "reconciled", None, run_id=run_id)
            store.append_fact(
                run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
                causal_command_id=key, payload={"resolution": resolution},
            )
        # Cleared once, at the end, and only if nothing is left -- including
        # effects this batch did not name.
        if not pending_reconciliation(store, run_id):
            store.clear_reconciliation(run_id)
    return len(keys)


def reconcile(store, run_id, idempotency_key, resolution, expected_version) -> None:
    """Resolve a halt. An audited command under expected-version CAS -- never a
    manual state edit."""
    from kernel.commands import StaleVersion

    # Reconciliation must name an effect that is actually uncertain. mark_effect
    # is a bare UPDATE by key, so an unknown or already-confirmed key would
    # otherwise succeed silently, bump the version and clear the halt.
    state = store.effect_state(idempotency_key, run_id=run_id)
    if state not in ("uncertain", "intended"):
        raise ValueError(
            f"cannot reconcile {idempotency_key!r} in run {run_id!r}: state is "
            f"{state!r}, expected 'uncertain' or 'intended'"
        )

    # One transaction: CAS, effect update, halt clear and audit fact were four
    # autocommitted operations, so a crash could clear the safety halt and
    # consume the version with no immutable audit event -- the opposite of "an
    # audited command with expected-version CAS".
    with store.transaction():
        if not store.bump_version_cas(run_id, expected_version):
            raise StaleVersion(
                f"reconciliation derived from version {expected_version}, "
                "which has moved"
            )
        store.mark_effect(idempotency_key, "reconciled", None, run_id=run_id)
        # Only unhalt when nothing else is still uncertain. Clearing
        # unconditionally unhalted a run with a second outstanding effect.
        if not pending_reconciliation(store, run_id):
            store.clear_reconciliation(run_id)
        store.append_fact(
            # "human", not an attempt: reconciliation is an operator action, and
        # that is a fact about a person rather than about a dispatched actor.
        run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
            causal_command_id=idempotency_key, payload={"resolution": resolution},
        )
