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


def perform(store, run_id, generation, effect_class, idempotency_key, intent, executor):
    if effect_class not in EffectClass.ALL:
        raise ValueError(f"unknown effect class: {effect_class}")

    existing = store.effect_by_key(idempotency_key)
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
    rows = store._conn.execute(
        "SELECT idempotency_key, effect_class, generation FROM effects"
        " WHERE run_id = ? AND state = 'uncertain' ORDER BY at_us",
        (run_id,),
    ).fetchall()
    return [
        {"idempotency_key": r[0], "effect_class": r[1], "generation": r[2]} for r in rows
    ]


def reconcile(store, run_id, idempotency_key, resolution, expected_version) -> None:
    """Resolve a halt. An audited command under expected-version CAS -- never a
    manual state edit."""
    from kernel.commands import StaleVersion

    cur = store._conn.execute(
        "UPDATE runs SET version = version + 1 WHERE run_id = ? AND version = ?",
        (run_id, expected_version),
    )
    if cur.rowcount == 0:
        raise StaleVersion(
            f"reconciliation derived from version {expected_version}, which has moved"
        )
    store.mark_effect(idempotency_key, "reconciled", None)
    store.clear_reconciliation(run_id)
    store.append_fact(
        run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
        causal_command_id=idempotency_key, payload={"resolution": resolution},
    )
