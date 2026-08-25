"""The effect journal, defined by semantic effect class.

Persist intent before invoking the effect; carry an idempotency key; record the
external object identifier; reconcile an uncertain result before retrying.
Every journalled mutation is a generation-fenced resource.
"""

from __future__ import annotations

from kernel.events import EventKind
from kernel.ids import new_id
from kernel.ownership import OwnershipLost, current_generation


class EffectClass:
    REF_UPDATE = "ref_update"
    PULL_REQUEST = "pull_request"
    STATUS_CHECK = "status_check"
    COMMENT = "comment"
    ISSUE_OR_LABEL = "issue_or_label"
    REVERT_OR_RECOVERY = "revert_or_recovery"
    CREDENTIAL_LIFECYCLE = "credential_lifecycle"
    SESSION_CONTROL = "session_control"
    ALL = frozenset({
        REF_UPDATE, PULL_REQUEST, STATUS_CHECK, COMMENT,
        ISSUE_OR_LABEL, REVERT_OR_RECOVERY, CREDENTIAL_LIFECYCLE, SESSION_CONTROL,
    })


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


def perform(
    store, run_id, generation, effect_class, idempotency_key, intent, executor,
    *, _bypass_halt: bool = False,
):
    if effect_class not in EffectClass.ALL:
        raise ValueError(f"unknown effect class: {effect_class}")

    # The halt gates EFFECTS, not just commands. Gating only submit() left the
    # mutating path open: a halted run could retry the very effect whose
    # outcome is unknown, under a fresh key, which is the duplicate external
    # mutation the halt exists to prevent.
    #
    # _bypass_halt exists only so a test can drive a SECOND effect to uncertain
    # on an already-halted run; production callers never pass it.
    if not _bypass_halt and is_halted(store, run_id):
        raise RuntimeError(
            f"run {run_id} is halted pending reconciliation; resolve it before "
            "performing further effects"
        )

    existing = store.effect_by_key(idempotency_key, run_id=run_id)
    if existing is not None:
        if existing["state"] == "uncertain":
            raise UncertainEffect(f"{idempotency_key} needs reconciliation before retry")
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
        run_id=run_id, kind=EventKind.EFFECT_INTENDED, actor="kernel",
        causal_command_id=idempotency_key,
        payload={"effect_class": effect_class, "effect_id": eid},
    )
    try:
        external_id = executor(effect_class, intent, idempotency_key)
    except Exception as exc:
        store.mark_effect(idempotency_key, "uncertain", None)
        store.append_fact(
            run_id=run_id, kind=EventKind.EFFECT_UNCERTAIN, actor="kernel",
            causal_command_id=idempotency_key,
            payload={"effect_id": eid, "error": type(exc).__name__},
        )
        enter_reconciliation_required(store, run_id, {
            "run_id": run_id,
            "generation": generation,
            "affected_resources": [effect_class],
            "last_confirmed_observations": store.last_confirmed(run_id),
            "stop_attempts": 0,
            "recommended_actions": [
                f"Check whether the {effect_class} succeeded externally",
                f"Then reconcile(store, {run_id!r}, {idempotency_key!r}, resolution, version)",
            ],
        })
        raise UncertainEffect(
            f"{effect_class} outcome unknown ({type(exc).__name__}); reconcile before retry"
        ) from exc

    store.mark_effect(idempotency_key, "confirmed", external_id)
    store.append_fact(
        run_id=run_id, kind=EventKind.EFFECT_CONFIRMED, actor="kernel",
        causal_command_id=idempotency_key,
        payload={"effect_id": eid, "external_object_id": external_id},
    )
    return external_id


def pending_reconciliation(store, run_id: str) -> list[dict]:
    return store.uncertain_effects(run_id)


def reconcile(store, run_id, idempotency_key, resolution, expected_version) -> None:
    """Resolve a halt. An audited command under expected-version CAS -- never a
    manual state edit."""
    from kernel.commands import StaleVersion

    # Reconciliation must name an effect that is actually uncertain. mark_effect
    # is a bare UPDATE by key, so an unknown or already-confirmed key would
    # otherwise succeed silently, bump the version and clear the halt.
    state = store.effect_state(idempotency_key, run_id=run_id)
    if state != "uncertain":
        raise ValueError(
            f"cannot reconcile {idempotency_key!r} in run {run_id!r}: state is "
            f"{state!r}, expected 'uncertain'"
        )

    if not store.bump_version_cas(run_id, expected_version):
        raise StaleVersion(
            f"reconciliation derived from version {expected_version}, which has moved"
        )
    store.mark_effect(idempotency_key, "reconciled", None)
    # Only unhalt when nothing else is still uncertain. Clearing
    # unconditionally unhalted the run while a second uncertain effect was
    # outstanding -- and, with the gate above, that run could then act again.
    if not pending_reconciliation(store, run_id):
        store.clear_reconciliation(run_id)
    store.append_fact(
        run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
        causal_command_id=idempotency_key, payload={"resolution": resolution},
    )
