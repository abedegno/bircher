"""Front-half journal queries (spec §1-§2).

Every function here derives its answer from facts and satisfied effects.
None reads a caller's payload. Tasks 7-12 add to this module; nothing in it
writes.
"""
from __future__ import annotations

import json

from kernel.events import EventKind
from kernel.policy import policy_of, policy_version  # noqa: F401


def seats_used(store, run_id: str) -> int:
    from kernel.dispatch import Role
    return sum(1 for d in store.dispatches_for(run_id) if d["role"] in Role.SEATS)


def grants(store, run_id: str) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING)
        if f.payload.get("ruling") == "grant_round"
    )


def seat_bound(store, run_id: str) -> int:
    """Ruling 4: a granted round is one author seat and one reviewer seat."""
    return policy_of(store, run_id).max_seats + 2 * grants(store, run_id)


def rounds_used(store, run_id: str, phase: str, epoch_n: int) -> int:
    """How many `request_revision` review_rulings this phase and epoch have
    already spent. A human's own request_revision is a `human_ruling`
    review_verdict, not a `review_ruling` one, and does not count."""
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT)
        if f.payload.get("ruling") == "review_ruling"
        and f.payload.get("verdict") == "request_revision"
        and f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n
    )


def round_grants(store, run_id: str, phase: str, epoch_n: int) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING)
        if f.payload.get("ruling") == "grant_round"
        and f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n
    )


def round_bound(store, run_id: str, phase: str, epoch_n: int) -> int:
    return policy_of(store, run_id).max_rounds + round_grants(store, run_id, phase, epoch_n)


FRONT_PHASES = ("spec", "plan")

#: Ruling 14: the server names the bundle, the dispatch names the vendor.
BUNDLE_PREFIX = "v2_author_"


def vendor_of(agent_name) -> str | None:
    """`v2_author_claude` -> `claude`; any other bundle name -> None."""
    if isinstance(agent_name, str) and agent_name.startswith(BUNDLE_PREFIX) and len(agent_name) > len(BUNDLE_PREFIX):
        return agent_name[len(BUNDLE_PREFIX):]
    return None


#: Ruling 2: a direction consumes a park as an answer or a ruling does.
HUMAN_FACT_KINDS = frozenset({
    EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING, EventKind.HUMAN_DIRECTION,
})


def epoch(store, run_id: str) -> int:
    return len(store.facts_of_kind(run_id, EventKind.BUNDLE_REVISED))


def bundle_hash(store, run_id: str) -> str | None:
    revised = store.newest_fact(run_id, EventKind.BUNDLE_REVISED)
    if revised is not None:
        return revised.payload["bundle_hash"]
    enq = store.newest_fact(run_id, EventKind.RUN_ENQUEUED)
    return None if enq is None else enq.payload.get("bundle_hash")


def submissions(store, run_id: str, phase: str, epoch_n: int) -> list:
    return [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
            if f.payload["phase"] == phase and f.payload["epoch"] == epoch_n]


def newest_submission(store, run_id: str, phase: str, epoch_n: int):
    subs = submissions(store, run_id, phase, epoch_n)
    return subs[-1] if subs else None


def _newest_seq(store, run_id: str, *kinds: str) -> int:
    facts = store.facts_of_kind(run_id, *kinds)
    return facts[-1].seq if facts else 0


def last_transition_seq(store, run_id: str) -> int:
    return _newest_seq(store, run_id, EventKind.TRANSITION)


def last_human_fact_seq(store, run_id: str) -> int:
    return _newest_seq(store, run_id, *HUMAN_FACT_KINDS)


def current_park(store, run_id: str):
    """The newest `parked` fact, if newer than the last transition and the
    last human fact (spec §2 *States*). Either consumes it."""
    park = store.newest_fact(run_id, EventKind.PARKED)
    if park is None:
        return None
    floor = max(last_transition_seq(store, run_id), last_human_fact_seq(store, run_id))
    return park if park.seq > floor else None


def _satisfied(row: dict) -> bool:
    if row["state"] == "confirmed":
        return True
    return row["state"] == "reconciled" and row.get("external_object_id") not in (None, "")


def satisfied_effects(store, run_id: str, kind: str) -> list[dict]:
    out = []
    for row in store.effects_for(run_id):
        ob = (row.get("intent") or {}).get("obligation") or {}
        if ob.get("kind") == kind and _satisfied(row):
            out.append(row)
    return out


def _roles(store, run_id: str) -> dict[int, str]:
    return {d["generation"]: d["role"] for d in store.dispatches_for(run_id)}


def newest_seat(store, run_id: str, role: str, phase: str, epoch_n: int) -> dict | None:
    """The newest satisfied `sess-create` under a generation dispatched as
    *role* whose obligation names *phase* and *epoch* (spec §2 Refusals:
    'the newest satisfied sess-create under an author generation of this
    phase and epoch'). Phase and epoch are the obligation's, read from the
    intent; the effects table has no such columns."""
    roles = _roles(store, run_id)
    found = None
    for row in satisfied_effects(store, run_id, "sess-create"):
        ob = row["intent"]["obligation"]
        if roles.get(row["generation"]) != role:
            continue
        if ob.get("phase") != phase or ob.get("epoch") != epoch_n:
            continue
        found = {"generation": row["generation"], "key": row["idempotency_key"],
                 "cause": ob.get("cause"),
                 "session": json.loads(row["external_object_id"])}
    return found


def newest_prompt(store, run_id: str, phase: str, epoch_n: int) -> dict | None:
    found = None
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        ob = row["intent"]["obligation"]
        if ob.get("phase") != phase or ob.get("epoch") != epoch_n:
            continue
        found = {"generation": row["generation"], "key": row["idempotency_key"],
                 "cause": ob.get("cause"), "session_id": ob.get("session"),
                 "at_us": row["at_us"]}
    return found


def turn_ended_for(store, run_id: str, prompt_key: str):
    """The turn_ended that ended the turn *prompt_key* started. One per
    prompt: record_turn_ended derives the key from the newest satisfied
    sess-prompt at the moment it is accepted (Task 7)."""
    for f in store.facts_of_kind(run_id, EventKind.TURN_ENDED):
        if f.payload.get("prompt_key") == prompt_key:
            return f
    return None


def stop_satisfied_for(store, run_id: str, turn_ended_id: str) -> bool:
    return any(row["intent"]["obligation"].get("cause") == turn_ended_id
               for row in satisfied_effects(store, run_id, "sess-stop"))


def newest_prompt_of(store, run_id: str, session_id: str, phase: str, epoch_n: int) -> dict | None:
    """The SESSION's newest satisfied sess-prompt in the phase and epoch (the
    output guard's quantifier, spec §2: 'its newest satisfied sess-prompt').
    newest_prompt() is the phase's, for record_turn_ended (ruling 15)."""
    found = None
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        ob = row["intent"]["obligation"]
        if ob.get("session") == session_id and ob.get("phase") == phase and ob.get("epoch") == epoch_n:
            found = {"generation": row["generation"], "key": row["idempotency_key"],
                     "cause": ob.get("cause"), "session_id": session_id, "at_us": row["at_us"]}
    return found


def dispatch_seq(store, run_id: str, generation: int) -> int | None:
    for f in store.facts_of_kind(run_id, EventKind.ATTEMPT_DISPATCHED):
        if f.payload.get("generation") == generation:
            return f.seq
    return None


def direction_after(store, run_id: str, seq: int):
    facts = [f for f in store.facts_of_kind(run_id, EventKind.HUMAN_DIRECTION) if f.seq > seq]
    return facts[-1] if facts else None


def epoch_facts(store, run_id: str, kind: str, epoch_n: int) -> list:
    return [f for f in store.facts_of_kind(run_id, kind) if f.payload.get("epoch") == epoch_n]


def grill_open(store, run_id: str) -> bool:
    """spec §2 Refusals, submit_spec: under grill=human, no human_answer in
    the epoch, or a model_question of the epoch newer than its last answer."""
    if policy_of(store, run_id).grill != "human":
        return False
    n = epoch(store, run_id)
    answers = epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
    if not answers:
        return True
    questions = epoch_facts(store, run_id, EventKind.MODEL_QUESTION, n)
    return bool(questions) and questions[-1].seq > answers[-1].seq


def human_rejections(store, run_id: str) -> list:
    return [f for f in store.facts_of_kind(run_id, EventKind.COMMAND_REJECTED)
            if f.actor == "human"]


def dismissed_rejection_ids(store, run_id: str) -> set[str]:
    return {f.payload["rejection"]
            for f in store.facts_of_kind(run_id, EventKind.HUMAN_ITEM_DISMISSED)}


def brief_for(store, run_id: str, generation: int):
    for f in store.facts_of_kind(run_id, EventKind.REVIEW_BRIEF_ISSUED):
        if f.payload.get("generation") == generation:
            return f
    return None


def prompt_hashes_of(store, run_id: str, session_id: str) -> set[str]:
    out = set()
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        if row["intent"]["obligation"].get("session") == session_id:
            out.add(row["intent"].get("body", {}).get("artifact"))
    out.discard(None)
    return out
