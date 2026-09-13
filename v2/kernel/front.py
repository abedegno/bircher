"""Front-half journal queries (spec §1-§2).

Every function here derives its answer from facts and satisfied effects.
None reads a caller's payload. Tasks 7-12 add to this module; nothing in it
writes.
"""
from __future__ import annotations

import json
from typing import NamedTuple

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


FRONT_PHASES = ("slices", "spec", "plan")

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


def epoch_of(store, run_id: str, seq: int) -> int:
    """The epoch a fact at *seq* belongs to, derived from the journal exactly
    as `visit_of` derives a fact's visit: count the `bundle_revised` facts
    that precede it. `epoch()` is this same rule applied to "now" -- every
    `bundle_revised` fact so far, `len(...)` -- rather than to one fact's own
    position in the journal (round 3, C1/C2: a fact's `epoch` stamp, like its
    `visit` stamp, is a claim the journal can check, not one to trust and
    merely bound to a range)."""
    return sum(1 for f in store.facts_of_kind(run_id, EventKind.BUNDLE_REVISED) if f.seq < seq)


def bundle_hash(store, run_id: str) -> str | None:
    revised = store.newest_fact(run_id, EventKind.BUNDLE_REVISED)
    if revised is not None:
        return revised.payload["bundle_hash"]
    enq = store.newest_fact(run_id, EventKind.RUN_ENQUEUED)
    return None if enq is None else enq.payload.get("bundle_hash")


def visit_stamp_of(store, run_id: str, f) -> int | None:
    """The visit *f* belongs to, by the ONE rule (shaping spec §6): "by the
    `visit` it carries, or ... by `front.visit_of`, the prefix walk" -- for
    a fact that carries no stamp at all. A stamp of `None` is the fact's own
    claim, not silence to fall back from: `submissions` used to spell this
    fallback as `payload.get("visit", visit_of(...))` (the default fires
    only when the KEY is absent) and `shape_ruling` as
    `payload.get("visit") or visit_of(...)` (`or` treats a `None` VALUE the
    same as an absent key) -- two readers of the same stamp disagreeing
    about what an explicit `None` means is how a `slices` submission stamped
    `visit: None` passed every check in this file (round 2, B5). One
    definition now; a caller comparing the result to a wanted visit gets
    `None` back for a `None` stamp, which correctly matches nothing."""
    if "visit" in f.payload:
        return f.payload["visit"]
    return visit_of(store, run_id, f.seq)


def submissions(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None) -> list:
    """The phase's submissions in the epoch, newest last.

    *visit* filters to one shaping visit and is passed only for phase
    `slices` (revision 16): visits partition the shaping work, and a spec
    draft hidden behind a hand-back is not what the partition is for. A
    submission written before this revision carries no `visit`; `visit_of`
    attributes it, and gives it visit 1."""
    out = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
           if f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n]
    if visit is None:
        return out
    return [f for f in out if visit_stamp_of(store, run_id, f) == visit]


def newest_submission(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None):
    subs = submissions(store, run_id, phase, epoch_n, visit=visit)
    return subs[-1] if subs else None


def newest_review_verdict(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None):
    """The newest reviewer verdict of this phase and epoch, or None. Human
    rulings are not verdicts: a `human_ruling` carries no findings a next
    round could disposition.

    *visit* narrows to one shaping visit, for phase `slices` only (revision
    16, the same partition `submissions` applies). A verdict's payload
    carries no `visit` of its own -- it is the reviewer's judgement, not the
    author's submission -- so `visit_of`'s prefix walk is the only way to
    place one; there is no stamped shortcut to fall back to here."""
    vs = [f for f in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT)
          if f.payload.get("ruling") == "review_ruling"
          and f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n]
    if visit is not None:
        vs = [v for v in vs if visit_of(store, run_id, v.seq) == visit]
    return vs[-1] if vs else None


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


def created_sessions(store, run_id: str) -> set[str]:
    """Every session a satisfied `sess-create` of this run delivered.

    The binding a payload that NAMES a session is checked against: a run
    cannot have read a human's message from, or reply into, a session it
    never created.
    """
    return {json.loads(row["external_object_id"])["id"]
            for row in satisfied_effects(store, run_id, "sess-create")}


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


def frozen_labels(store, run_id: str) -> list[str]:
    """The issue's labels as `policy_frozen` recorded them, UNFILTERED
    (policy.py `freeze`): the observable the depth refusal reads, since the
    bundle canon strips every bircher: label."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return [] if fact is None else list(fact.payload.get("labels") or [])


def shape_ruling(store, run_id: str, epoch_n: int, visit: int | None = None):
    """The epoch's newest `model_ruling {question_id: "shape"}`, or the one
    written in `visit` when a visit is named (revision 16), or None."""
    from kernel.slices import SHAPE_QUESTION
    found = None
    for f in epoch_facts(store, run_id, EventKind.MODEL_RULING, epoch_n):
        if f.payload.get("question_id") != SHAPE_QUESTION:
            continue
        if visit is not None and visit_stamp_of(store, run_id, f) != visit:
            continue
        found = f
    return found


def _visit_boundaries(store, run_id: str, epoch_n: int) -> list[int]:
    """The `seq` of every fact that opens a shaping visit in this epoch, in
    order: each `reshape_requested`, and each `human_direction` recorded while
    the epoch's dispute was unresolved AT THAT POINT IN THE PREFIX.

    The prefix rule is the whole of it. A direction typed into the reshaping
    session before the disputed ruling exists resolves nothing and opens
    nothing; the direction that answers a disputed ruling opens the next
    visit. Deciding that from the prefix rather than from the journal's
    current state is what keeps a visit from disappearing when a later
    `bundle_revised` changes what "unresolved" means (spec §2)."""
    from kernel.slices import SHAPE_QUESTION
    facts = [f for f in store.facts_for(run_id)
             if f.payload.get("epoch") == epoch_n
             and f.kind in (EventKind.RESHAPE_REQUESTED, EventKind.MODEL_RULING,
                            EventKind.HUMAN_DIRECTION, EventKind.HUMAN_RULING)]
    boundaries, hand_back, disputed, resolved = [], False, False, False
    for f in facts:
        if f.kind == EventKind.RESHAPE_REQUESTED:
            boundaries.append(f.seq)
            hand_back, disputed, resolved = True, False, False
        elif (f.kind == EventKind.MODEL_RULING
              and f.payload.get("question_id") == SHAPE_QUESTION and hand_back and not disputed):
            disputed = True
        elif (f.kind == EventKind.HUMAN_DIRECTION and disputed and not resolved
              and f.payload.get("state") == "shaping"):
            # Recorded AT `shaping`, not merely after the disputed ruling:
            # `front.dispute`'s resolution search reads the same qualifier
            # (revision 16), and a direction typed elsewhere -- reachable
            # only by forcing the state, since `_one_piece_destination` never
            # sends a disputed ruling anywhere else -- would otherwise open a
            # visit `dispute` does not agree is resolved.
            boundaries.append(f.seq)
            resolved = True
        elif (f.kind == EventKind.HUMAN_RULING
              and f.payload.get("ruling") == "approve_one_piece" and disputed):
            resolved = True
    return boundaries


def shaping_visit(store, run_id: str, epoch_n: int) -> int:
    """The epoch's current shaping visit, 1-based (spec §2 *Visits, not epochs*)."""
    return len(_visit_boundaries(store, run_id, epoch_n)) + 1


def visit_of(store, run_id: str, seq: int) -> int:
    """The visit a fact was written in, for a fact that carries no `visit`
    (everything written before revision 16). Facts of the phase carry it.

    A fact with no `epoch` in its payload reads as visit 1 by design, not by
    the general rule §2 states ("attributes any other fact by the prefix
    walk"): the epoch is in principle derivable by counting `bundle_revised`
    facts with a smaller seq, but every declared consumer of this function
    (`model_ruling`, `artifact_submitted`, and a fact written before this
    revision, which §6 assertion 4 attributes by this exact shortcut) already
    carries `epoch`, so the shortcut costs nothing today."""
    fact = next((f for f in store.facts_for(run_id) if f.seq == seq), None)
    if fact is None:
        return 1
    n = fact.payload.get("epoch")
    if n is None:
        return 1
    return sum(1 for b in _visit_boundaries(store, run_id, n) if b <= seq) + 1


class Dispute(NamedTuple):
    """The four facts of a shape dispute, in journal order (spec §2 *The
    dispute*). Any of the last three may be None; `handed_back` and
    `hand_back` are present whenever a Dispute is returned at all."""
    handed_back: object          # the newest `shape` ruling BEFORE the hand-back
    hand_back: object            # the epoch's `reshape_requested`
    disputed: object | None      # the first `shape` ruling AFTER it
    resolution: object | None    # the first approve_one_piece or direction at `shaping` after that


def dispute(store, run_id: str, epoch_n: int | None = None) -> Dispute | None:
    """The epoch's dispute, or None when it holds no hand-back. The current
    epoch by default; any epoch for the proof, which walks them all.

    This is the ONE definition. The destination, the refusals, the
    coordinator's classifier and notices, the runner, the queue generator and
    the proof's fifth line all read it; a second derivation is how the review
    found three incompatible answers to "is this dispute resolved"."""
    from kernel.slices import SHAPE_QUESTION
    n = epoch(store, run_id) if epoch_n is None else epoch_n
    facts = [f for f in store.facts_for(run_id) if f.payload.get("epoch") == n]
    hand_back = next((f for f in facts if f.kind == EventKind.RESHAPE_REQUESTED), None)
    if hand_back is None:
        return None
    rulings = [f for f in facts if f.kind == EventKind.MODEL_RULING
               and f.payload.get("question_id") == SHAPE_QUESTION]
    handed_back = next((f for f in reversed(rulings) if f.seq < hand_back.seq), None)
    disputed = next((f for f in rulings if f.seq > hand_back.seq), None)
    resolution = None
    if disputed is not None:
        # The bound is exact: a direction that answers the dispute is one
        # recorded AT `shaping` after the disputed ruling, not merely one
        # that comes later in the journal (a direction to the reshaping
        # session, between the hand-back and the disputed ruling, resolves
        # nothing -- `f.seq > disputed.seq` is what excludes it) and not one
        # recorded at a state this dispute never sent the run to (the
        # qualifier `_visit_boundaries`'s own direction branch reads too, so
        # the two derivations of "the resolution" cannot disagree).
        resolution = next(
            (f for f in facts
             if f.seq > disputed.seq
             and ((f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "approve_one_piece")
                  or (f.kind == EventKind.HUMAN_DIRECTION and f.payload.get("state") == "shaping"))),
            None)
    return Dispute(handed_back, hand_back, disputed, resolution)


def ruling_after_direction(store, run_id: str, epoch_n: int | None = None):
    """The `shape` ruling the directed visit's shaper recorded (shaping spec
    §3 *the spec round's question*, revision 16 fix round 1): "the spec
    round's cause is the resolution -- the person's `approve_one_piece`, OR
    the `shape` ruling recorded after their direction". `dispute`'s own
    `resolution` field IS that direction, not the ruling that answers it --
    the ruling is a fifth fact `Dispute` does not carry, so this is the one
    place that finds it, rather than every caller re-deriving it.

    `None` when there is no dispute, the resolution is the approval (nothing
    follows a `human_ruling` to look for), or the directed shaper has not
    ruled yet -- it may submit a slice plan instead, which is that visit's
    submission, not a ruling.

    Fix round 2: `rulings[0]`, not `rulings[-1]`. The spec's own words are
    "the ruling recorded after their direction" -- the one that answers it,
    not whichever is newest. The two read the same under every history the
    current kernel invariants can reach (one hand-back per bundle, and a
    direction opens a visit only while its dispute is unresolved, which
    resolving it closes for good -- so at most one shape ruling ever
    follows a direction), which is exactly why the choice was unpinned
    rather than provably safe; `rulings[0]` is the one that matches the
    sentence being implemented."""
    from kernel.slices import SHAPE_QUESTION
    d = dispute(store, run_id, epoch_n)
    if d is None or d.resolution is None or d.resolution.kind != EventKind.HUMAN_DIRECTION:
        return None
    n = epoch(store, run_id) if epoch_n is None else epoch_n
    rulings = [f for f in epoch_facts(store, run_id, EventKind.MODEL_RULING, n)
               if f.payload.get("question_id") == SHAPE_QUESTION and f.seq > d.resolution.seq]
    return rulings[0] if rulings else None


def unresolved_disagreement(store, run_id: str, epoch_n: int | None = None) -> bool:
    """`dispute`'s boolean: a hand-back, a ruling after it, no resolution."""
    d = dispute(store, run_id, epoch_n)
    return d is not None and d.disputed is not None and d.resolution is None


# -- the shaping phase (shaping spec §2, §3) ------------------------------------

FILING_KINDS = frozenset({"slice_issue", "slice_dependency", "slice_queue", "umbrella", "umbrella_label"})
COMPLETION_KINDS = frozenset({"umbrella_close", "parent_close"})


def is_satisfied(row: dict) -> bool:
    return _satisfied(row)


def issue_number(store, run_id: str) -> int:
    """The run's issue, from the bundle snapshot the kernel holds."""
    return int(json.loads(store.read_blob(bundle_hash(store, run_id)))["number"])


def accepted_slices_hash(store, run_id: str, epoch_n: int) -> str | None:
    """The hash the epoch's `human_ruling {approve, phase: slices}` or
    `artifact_advanced {phase: slices}` named: the plan a reviewer accepted
    and the gate passed, not any plan submitted (spec §2 *What perform
    refuses*)."""
    found = None
    for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING, EventKind.ARTIFACT_ADVANCED):
        p = f.payload
        if p.get("phase") != "slices" or p.get("epoch") != epoch_n:
            continue
        if f.kind == EventKind.HUMAN_RULING:
            if p.get("ruling") == "approve":
                found = p.get("artifact_hash")
        else:
            found = p.get("hash")
    return found


def accepted_plan(store, run_id: str, epoch_n: int | None = None):
    from kernel import slices
    n = epoch(store, run_id) if epoch_n is None else epoch_n
    h = accepted_slices_hash(store, run_id, n)
    return None if h is None else slices.parse(store.read_blob(h))


def slices_filed(store, run_id: str, epoch_n: int) -> dict:
    return {f.payload["slice"]: f for f in epoch_facts(store, run_id, EventKind.SLICE_FILED, epoch_n)}


def filing_complete(store, run_id: str, epoch_n: int):
    fs = epoch_facts(store, run_id, EventKind.FILING_COMPLETE, epoch_n)
    return fs[-1] if fs else None


def current_closure(store, run_id: str, epoch_n: int, n: int):
    """The slice's newest `slice_closed` or `slice_reopened` in the epoch, or
    None: a closure is an observation with a date, not a property."""
    found = None
    for f in store.facts_of_kind(run_id, EventKind.SLICE_CLOSED, EventKind.SLICE_REOPENED):
        if f.payload.get("epoch") == epoch_n and f.payload.get("slice") == n:
            found = f
    return found


def observed_closed_under(store, run_id: str, epoch_n: int, generation: int):
    for f in epoch_facts(store, run_id, EventKind.CHILDREN_OBSERVED_CLOSED, epoch_n):
        if f.payload.get("generation") == generation:
            return f
    return None


def satisfied_obligation(store, run_id: str, ob: dict) -> dict | None:
    for row in store.effects_for(run_id):
        if (row.get("intent") or {}).get("obligation") == ob and _satisfied(row):
            return row
    return None


def filing_obligations(store, run_id: str, epoch_n: int) -> list[dict]:
    """Every obligation the accepted plan implies (spec §2
    `record_filing_complete`, §3 *Filing the children*), the one source of
    their shapes: creates, then links, then queue labels, then the umbrella
    and its label."""
    plan = accepted_plan(store, run_id, epoch_n)
    h = accepted_slices_hash(store, run_id, epoch_n)
    parent = issue_number(store, run_id)
    base = {"run": run_id, "epoch": epoch_n}
    obs = [dict(base, kind="slice_issue", slice=s.number, parent=parent, plan_hash=h) for s in plan.slices]
    obs += [dict(base, kind="slice_dependency", plan_hash=h, slice=n, blocker=b) for n, b in plan.edges()]
    obs += [dict(base, kind="slice_queue", plan_hash=h, slice=s.number) for s in plan.slices]
    obs += [dict(base, kind="umbrella", plan_hash=h), dict(base, kind="umbrella_label", plan_hash=h)]
    return obs


def unsatisfied_filing(store, run_id: str, epoch_n: int) -> list[dict]:
    return [ob for ob in filing_obligations(store, run_id, epoch_n)
            if satisfied_obligation(store, run_id, ob) is None]


def issue_create_rows(store, run_id: str) -> list[dict]:
    return [r for r in store.effects_for(run_id) if r["effect_class"] == "issue_create"]


def create_attempted_for_issue(store, base_repo: str, number: int) -> tuple[str, list[dict]] | None:
    """A run of the same repository and issue holding ANY issue_create row
    not reconciled not-delivered -- intended, confirmed, uncertain or
    reconciled delivered -- whatever that run's state (spec §2 *An issue is
    sliced once*, ruling 12), AND those rows. The observable is the attempt: a
    child may exist from the first attempt on.

    `(run_id, rows)` rather than the run id alone because the spec says the
    refusal "names the run and its rows": a person told only that some earlier
    run attempted a create has to go and find which children exist before they
    can act on the refusal, and the journal already knows.
    """
    for rid in store.all_run_ids():
        try:
            if store.run_base_repo(rid) != base_repo:
                continue
            enq = store.newest_fact(rid, EventKind.RUN_ENQUEUED)
            blob = None if enq is None or not enq.payload.get("bundle_hash") else store.read_blob(enq.payload["bundle_hash"])
            if blob is None or int(json.loads(blob).get("number", -1)) != number:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        rows = [row for row in issue_create_rows(store, rid)
                if not (row["state"] == "reconciled" and row.get("external_object_id") in (None, ""))]
        if rows:
            return rid, rows
    return None
