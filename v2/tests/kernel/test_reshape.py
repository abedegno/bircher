"""Revision 16: the shaping visit, the hand-back, the dispute (shaping spec
§2 *request_reshape, approve_one_piece, and the disagreement*)."""
import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import ISSUE, SLICES_BYTES, Front


# The four fixtures of the plan's *Shared test fixtures* section go here,
# written once: _shaped, _new, _disagreed, _rejected_then_disagreed.

def _shaped(tmp_path, name="k.db", labels=("bircher:autonomous",)):
    """A run at `queued` through a one-piece ruling: the state a hand-back
    is legal from. The driver's default `shape=True` rules it at birth.

    `labels` is merged INTO the issue, not passed beside it: `Front.__init__`
    only falls back to its own `labels=` parameter when no `issue` is given,
    so `issue=ISSUE, labels=list(labels)` would silently keep `ISSUE`'s own
    `bircher:autonomous` regardless of what a caller asked for -- every
    caller of this fixture would get the same policy no matter which labels
    it named. The assertion below is the fixture proving that to itself,
    once, rather than a caller discovering it by way of a policy that never
    varied."""
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", issue={**ISSUE, "labels": list(labels)})
    assert s.run_state(f.run_id) == "queued"
    assert front.frozen_labels(s, f.run_id) == list(labels)
    return s, f


def _new(tmp_path, name="n.db"):
    """A run at `shaping`, nothing decided."""
    s = Store.open(tmp_path / name)
    return s, Front(s, "i12-epic-1", issue=ISSUE, shape=False)


def _disagreed(tmp_path, name="d.db", labels=("bircher:autonomous",)):
    """The unresolved-disagreement fixture the spec names (§8): a ruling, the
    hand-back, and the disputed ruling. The run is at `shaping`, unparked."""
    s, f = _shaped(tmp_path, name, labels)
    f.request_reshape("the issue names a store, an API and a page")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"
    return s, f


def _rejected_then_disagreed(tmp_path, name="r.db"):
    """A slice plan submitted and rejected in visit 1, then the ruling, the
    hand-back and the disputed ruling. Used where a `slices` submission must
    exist for the approval not to accept it."""
    s, f = _new(tmp_path, name)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"these are steps, not capabilities")
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    return s, f


def test_the_fixture_actually_varies_the_policy(tmp_path):
    """B1: the fixture's own assertion only fires when a caller asks for
    labels that differ from `ISSUE`'s, and until this test no caller did --
    so the merge it guards was unguarded. Task 3 loops over three gate
    policies through `_shaped`; if the labels do not reach the frozen bundle,
    that test proves one policy three times and goes green.

    Three label sets, three different gate sets. Reverting the merge in
    `_shaped` -- passing `issue=ISSUE, labels=list(labels)` as
    `Front.__init__`'s signature invites -- reds this."""
    from kernel.policy import policy_of
    seen = []
    for i, labels in enumerate(([], ["bircher:autonomous"], ["bircher:gate-plan"])):
        s, f = _shaped(tmp_path, f"pol{i}.db", labels)
        assert front.frozen_labels(s, f.run_id) == labels
        seen.append(tuple(sorted(policy_of(s, f.run_id).gates)))
    assert len(set(seen)) == 3, f"three label sets must yield three policies, got {seen}"


def test_visit_one_is_the_epoch(tmp_path):
    s, f = _shaped(tmp_path)
    assert front.shaping_visit(s, f.run_id, front.epoch(s, f.run_id)) == 1


def test_the_hand_back_opens_a_visit_and_a_direction_while_disputed_opens_another(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("the issue names a store, an API and a page")
    assert front.shaping_visit(s, f.run_id, n) == 2
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")   # the disputed ruling
    # Task 3's `_one_piece_destination` parks the disputed ruling at `shaping`
    # rather than sending it to `queued`; the direction below is recorded AT
    # `shaping`, which is the qualifier both `_visit_boundaries` and
    # `front.dispute` now read (revision 16, its mirror is the next test).
    assert s.run_state(f.run_id) == "shaping"
    f.direct("slice it along the three parts")                               # resolves, and opens visit 3
    assert front.shaping_visit(s, f.run_id, n) == 3


def test_a_direction_recorded_at_queued_after_a_disputed_ruling_opens_no_visit(tmp_path):
    """The mirror of the test above, and the canary for the qualifier both
    `_visit_boundaries` and `front.dispute` must read the same way: a
    direction is the resolution only when it is recorded AT `shaping`
    (spec §2 *The dispute*), not merely after the disputed ruling. A real
    dispute never lands the run at `queued` -- that is
    `_one_piece_destination`'s whole job -- so `set_run_state` is the only
    way to reach this state and exercise the qualifier at all. Removing the
    qualifier from either function reds this test: `_visit_boundaries` would
    open a third visit, and `dispute`'s resolution search would call the
    dispute resolved."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")  # disputed
    assert front.unresolved_disagreement(s, f.run_id) is True
    s.set_run_state(f.run_id, "queued")
    f.direct("slice it")
    assert front.shaping_visit(s, f.run_id, n) == 2            # no third visit opened
    assert front.unresolved_disagreement(s, f.run_id) is True  # and nothing is resolved


def test_a_direction_before_the_disputed_ruling_opens_no_visit(tmp_path):
    """A person typing into the reshaping session is a direction to that
    visit; it resolves nothing and partitions nothing (spec §2 *The dispute*)."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("three parts")
    f.direct("consider the migration too")
    assert front.shaping_visit(s, f.run_id, n) == 2


def test_shape_ruling_reads_per_visit_and_the_epoch(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    first = front.shape_ruling(s, f.run_id, n)
    # `==` rather than `is`: the store constructs a fresh Fact per query (no
    # caching anywhere), so two separate reads of the same row are never the
    # same object -- the frozen dataclass's field-wise equality is what "the
    # same fact" means here.
    assert first is not None and front.shape_ruling(s, f.run_id, n, visit=1) == first
    assert front.shape_ruling(s, f.run_id, n, visit=2) is None
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    assert front.shape_ruling(s, f.run_id, n, visit=2) is not None
    # The epoch-wide read is the newest, as `request_reshape`'s refusal needs.
    assert front.shape_ruling(s, f.run_id, n) != first


def test_every_fact_of_the_phase_carries_its_visit(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    ruling = front.shape_ruling(s, f.run_id, n)
    assert ruling.payload["visit"] == 1
    f.request_reshape("three parts")
    hb = s.facts_of_kind(f.run_id, EventKind.RESHAPE_REQUESTED)[-1]
    assert hb.payload["visit"] == 2 and hb.payload["epoch"] == n
    f.shape_round(SLICES_BYTES)
    sub = front.submissions(s, f.run_id, "slices", n)[-1]
    assert sub.payload["visit"] == 2


def test_a_fact_written_before_the_revision_is_visit_one(tmp_path):
    """`visit_of` walks the prefix, so a ruling with no `visit` key reads as 1."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    ruling = front.shape_ruling(s, f.run_id, n)
    assert front.visit_of(s, f.run_id, ruling.seq) == 1


def _strip_visit_key(store, seq: int) -> None:
    """Rewrite one fact's payload with its `visit` key removed -- the shape
    a pre-revision-16 journal actually has, which no fixture in this file
    otherwise produces (every fact this driver writes now stamps one).
    Facts are append-only by a database trigger (`schema.sql`'s
    `facts_no_update`); this drops it for the one direct write and restores
    it, so the store's invariant holds for every fact but the one under
    test."""
    import json
    row = store._conn.execute(
        "SELECT payload_json FROM facts WHERE seq = ?", (seq,)
    ).fetchone()
    payload = json.loads(row[0])
    del payload["visit"]
    store._conn.execute("DROP TRIGGER facts_no_update")
    store._conn.execute(
        "UPDATE facts SET payload_json = ? WHERE seq = ?", (json.dumps(payload), seq)
    )
    store._conn.execute(
        "CREATE TRIGGER facts_no_update BEFORE UPDATE ON facts "
        "BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END"
    )


def test_visit_of_agrees_with_every_stamp_and_finds_a_stripped_one(tmp_path):
    """`visit_of` is the function seven later tasks read (spec §2: "attributes
    any other fact by the prefix walk"), and it is pinned by nothing until
    this: the walk must reproduce every fact's OWN stamp -- the invariant a
    `b <= seq` -> `b < seq` typo silently breaks, attributing a boundary fact
    to the visit it CLOSES rather than the one it OPENS -- and it must still
    get the right answer for a fact that carries no stamp at all, the shape
    of every fact written before this revision."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")  # visit 2's ruling

    # 1. Every fact that carries a `visit` agrees with the walk, at its own seq.
    stamped = [fct for fct in s.facts_for(f.run_id) if "visit" in fct.payload]
    assert len(stamped) >= 3       # the hand-back and the two rulings, at least
    for fct in stamped:
        assert front.visit_of(s, f.run_id, fct.seq) == fct.payload["visit"], (fct.kind, fct.seq)

    # 2. A fact with NO `visit` key -- the pre-revision-16 shape. The walk
    # still attributes it correctly, and a NAMED-visit read of it depends on
    # shape_ruling's `or visit_of(...)` fallback, not the (now-absent) stamp.
    disputed = front.shape_ruling(s, f.run_id, n, visit=2)
    assert disputed is not None and disputed.payload["visit"] == 2
    _strip_visit_key(s, disputed.seq)
    assert front.visit_of(s, f.run_id, disputed.seq) == 2
    restripped = front.shape_ruling(s, f.run_id, n, visit=2)
    assert restripped is not None and restripped.seq == disputed.seq


def test_a_revise_bundle_opens_a_new_epoch_whose_visits_restart_at_one(tmp_path):
    """`_visit_boundaries` scopes to `f.payload.get("epoch") == epoch_n`: the
    reason a later `revise_bundle` cannot carry an earlier epoch's boundaries
    into the new one, and the reason visits restart at 1 in each epoch (spec
    §2 -- the sentence the prefix walk exists to satisfy)."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    assert front.shaping_visit(s, f.run_id, 0) == 2
    f.revise(dict(ISSUE, body="changed"), reshape=False)
    assert s.run_state(f.run_id) == "shaping" and f.epoch() == 1
    # The new epoch is untouched by the old one's hand-back.
    assert front.shaping_visit(s, f.run_id, 1) == 1
    # The old epoch's boundary is still counted -- revise_bundle erased nothing.
    assert front.shaping_visit(s, f.run_id, 0) == 2


def test_the_hand_back_is_legal_from_queued_and_moves_to_shaping(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("the issue names a store, an API and a page")
    assert s.run_state(f.run_id) == "shaping"
    hb = s.facts_of_kind(f.run_id, EventKind.RESHAPE_REQUESTED)[-1]
    assert hb.payload["reasoning"].startswith("the issue names")


def test_the_hand_back_refuses_empty_or_whitespace_reasoning(tmp_path):
    """The refusal table's first row (spec §2), pinned on its own.

    The plan had all four rows in one test. Its later assertions sat behind a
    call to `approve_one_piece`, which does not exist yet, so they never ran
    and the guards they named were covered by nothing. One test per row is
    what makes a mutation's red name the guard it removed."""
    s, f = _shaped(tmp_path)
    with pytest.raises(NotAuthorized, match="reasoning"):
        f.request_reshape("   ")


def test_the_hand_back_refuses_a_second_one_in_the_same_epoch(tmp_path):
    """The refusal table's third row: one hand-back per bundle.

    Task 3's `_one_piece_destination` now parks the disputed ruling at
    `shaping` instead of sending it on to `queued` -- the path this test
    used before reached `queued` directly off the disputed ruling and no
    longer does, so `request_reshape` (legal only from `queued`) would
    refuse on the STATE, not on "already holds a hand-back", and the
    `match="already"` below would no longer pin the row it names. Resolving
    the dispute with a direction first, and letting the visit it opens rule
    again, is the path that actually reaches `queued` with the epoch's
    hand-back still standing. The other path to this row -- a run returned
    to `queued` by the person's `approve_one_piece`, which leaves the
    ruling in place -- is the same guard reached differently, and is
    pinned in the task that adds that command."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"          # the disputed ruling parks
    f.direct("slice it")                               # resolves, and opens visit 3
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "queued"
    with pytest.raises(NotAuthorized, match="already"):
        f.request_reshape("again")


def test_the_hand_back_refuses_a_run_with_no_shape_ruling_in_its_epoch(tmp_path):
    """The refusal table's second row: a run that was never shaped has
    nothing to hand back to.

    Unreachable through today's transitions -- `record_one_piece` is the only
    way out of `shaping` -- so the state is set directly, exactly as a v1
    database written before the shaping phase existed holds it. The guard is
    kept for those databases, and this is the only way to execute it."""
    s2 = Store.open(tmp_path / "k2.db")
    g = Front(s2, "i13-v1-1", issue=ISSUE, shape=False)
    s2.set_run_state(g.run_id, "queued")
    with pytest.raises(NotAuthorized, match="no shape ruling"):
        g.request_reshape("epic")


def test_the_hand_back_carries_the_direction_guard(tmp_path):
    """Every author-turn-ending command refuses over a direction newer than
    its own dispatch (spec §2).

    `Front` has no `_author_turn` helper; `request_reshape`'s own `gen=`
    escape hatch is how a test holds a stale, already-dispatched generation
    to call it with -- the same dispatch/session/end_turn ceremony
    `request_reshape` performs for itself when `gen` is None, done here by
    hand so the direction can land in between."""
    s, f = _shaped(tmp_path)
    cause = f._newest_id()
    gen = f._dispatch(Role.AUTHOR, f.author)
    sid = f._session(gen, cause)
    f._end_turn(gen, sid)
    f.direct("do it this way instead")
    with pytest.raises(NotAuthorized, match="direction"):
        f.request_reshape("three parts", gen=gen)


def test_the_kind_is_declared(tmp_path):
    from kernel.events import SCHEMA_VERSIONS
    assert SCHEMA_VERSIONS[EventKind.RESHAPE_REQUESTED] == 1


# -- Task 3: front.dispute, the destination, and the three refusals ---------

def test_the_dispute_names_four_facts_in_order(tmp_path):
    s, f = _shaped(tmp_path)
    assert front.dispute(s, f.run_id) is None          # no hand-back yet
    f.request_reshape("three parts")
    d = front.dispute(s, f.run_id)
    assert d.handed_back is not None and d.hand_back is not None
    assert d.disputed is None and d.resolution is None
    assert front.unresolved_disagreement(s, f.run_id) is False   # no disputed ruling yet
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    d = front.dispute(s, f.run_id)
    assert d.disputed is not None and d.resolution is None
    assert front.unresolved_disagreement(s, f.run_id) is True
    f.direct("slice it")
    assert front.unresolved_disagreement(s, f.run_id) is False


def test_a_direction_before_the_disputed_ruling_is_not_the_resolution(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.direct("consider the migration")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert front.unresolved_disagreement(s, f.run_id) is True


def test_the_first_ruling_proceeds_and_the_disputed_one_stays(tmp_path):
    s, f = _shaped(tmp_path)                 # the first ruling already moved it to `queued`
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"          # the disputed ruling stays, and parks
    f.direct("slice it")
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "queued"           # the person answered; this one proceeds


def test_the_disputed_ruling_stays_at_shaping_whatever_the_gates(tmp_path):
    """The gate was never the policy's -- it is the disagreement's. Under
    `bircher:autonomous`, the one policy that merges unattended, the run
    still waits for a person (spec §2, the revision record's ruling)."""
    for labels in ([], ["bircher:autonomous"], ["bircher:gate-plan"]):
        s, f = _shaped(tmp_path, f"g{len(labels)}{labels}.db".replace("/", ""), labels=labels)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one", cost="the spec would find one surface")
        assert s.run_state(f.run_id) == "shaping"


def test_while_unresolved_three_commands_are_refused(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    with pytest.raises(NotAuthorized, match="the shape is disputed"):
        f.shape_round(SLICES_BYTES)
    with pytest.raises(NotAuthorized, match="the shape is disputed"):
        f.grant()


def test_a_human_answer_is_refused_from_every_shaping_state(tmp_path):
    """Not only while a dispute is unresolved: the grill is epoch-scoped and
    can stand open while a hand-back's visit runs, and an answer recorded at
    `shaping` would be dropped from the shaper's brief and delivered later to
    the spec author as the reply to its question (spec §2)."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    assert s.run_state(f.run_id) == "shaping"
    with pytest.raises(NotAuthorized, match="shaping"):
        f.answer("slice it along these lines")


def test_grant_round_is_refused_while_unresolved_even_with_a_park(tmp_path):
    """Isolates the grant_round dispute guard from the `grant_round needs a
    current park` refusal it would otherwise trip on first: `_shaped` plus a
    hand-back and a disputed ruling records no `park` fact on its own, so
    without a park of its own this test could not tell a removed dispute
    guard from the ordinary empty-park refusal both raise NotAuthorized for.
    Parking (with an ordinary, legal reason -- `disagreement` is not this
    task's to add) first means grant_round has a park to grant, and the
    guard under test is the only thing left standing between it and
    granting the disputed ruling around the person (spec §2, the row 'a
    retry cannot consume the park in place of the decision')."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    with pytest.raises(NotAuthorized, match="the shape is disputed"):
        f.grant()


def test_the_human_direction_fact_carries_the_state_it_was_recorded_at(tmp_path):
    """`record_human_direction` does not transition, so `_side_fact` reads
    the run's state at the moment the direction lands, not before and not
    after some later move -- the value `dispute`'s resolution search and
    `_visit_boundaries`'s direction branch both qualify on (spec §2)."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    assert s.run_state(f.run_id) == "shaping"
    f.direct("slice it along these lines")
    d = s.newest_fact(f.run_id, EventKind.HUMAN_DIRECTION)
    assert d.payload["state"] == "shaping"


# -- Task 4: approve_one_piece and the disagreement park --------------------

def test_the_approval_resolves_and_moves_to_queued(tmp_path):
    s, f = _disagreed(tmp_path)            # ruling -> hand-back -> disputed ruling
    assert s.run_state(f.run_id) == "shaping"
    f.approve_one_piece()
    assert s.run_state(f.run_id) == "queued"
    assert front.unresolved_disagreement(s, f.run_id) is False
    r = s.facts_of_kind(f.run_id, EventKind.HUMAN_RULING)[-1]
    assert r.payload["ruling"] == "approve_one_piece"
    assert r.payload["phase"] == "slices"
    # The visit is the DISPUTED ruling's (visit 2: visit 1 is the handed-back
    # ruling, the hand-back opens visit 2, the disputed ruling is recorded in
    # it) -- read through `visit_of`, not the approval's own turn, which
    # opens no visit. `_disagreed` is the smallest dispute there is (one
    # hand-back, one disputed ruling), so this is already a two-visit
    # history, not a one-visit one: the disputed ruling can never be visit 1,
    # since the hand-back that produces it always opens visit 2 first.
    assert r.payload["epoch"] == 0 and r.payload["visit"] == 2


def test_the_approval_refuses_its_four_ways(tmp_path):
    s, f = _disagreed(tmp_path)
    with pytest.raises(NotAuthorized, match="cursor"):
        f.approve_one_piece(cursor=None)
    with pytest.raises(NotAuthorized, match="human"):
        f.approve_one_piece(as_model=True)
    f.approve_one_piece()
    with pytest.raises(NotAuthorized, match="unresolved"):   # a second approval
        f.approve_one_piece()
    s2, g = _shaped(tmp_path, "b.db")                         # no dispute at all
    with pytest.raises(NotAuthorized, match="unresolved"):
        g.approve_one_piece()


def test_the_approval_is_refused_away_from_shaping(tmp_path):
    """The refusal table's third row, distinct from "unresolved": reachable
    only by forcing the run's state, because an unresolved dispute and a
    state other than `shaping` never coexist through any legal transition --
    the ruling that disputes it PARKS at `shaping`, and nothing else can move
    the run while the dispute stands. Defense for a database moved by hand,
    exactly as `test_the_hand_back_refuses_a_run_with_no_shape_ruling_in_its_epoch`
    exercises its own state guard the same way."""
    s, f = _disagreed(tmp_path)
    assert front.unresolved_disagreement(s, f.run_id) is True
    s.set_run_state(f.run_id, "queued")
    with pytest.raises(NotAuthorized, match="shaping-state park"):
        f.approve_one_piece()


def test_the_approval_does_not_accept_a_slice_plan(tmp_path):
    """`accepted_slices_hash` filters on ruling == 'approve'; the approval's
    own word keeps it out of that filter.

    The epoch here never holds an ACCEPTED plan -- a slice plan and a
    one-piece dispute cannot coexist past `sliced` in one epoch, so
    `accepted_slices_hash` reads None before this call regardless of which
    word the approval uses, and the bare `is None` below would pass under
    the mutation this test exists to catch. The direct read of the fact's
    own `ruling` is what actually reds when the word changes."""
    s, f = _rejected_then_disagreed(tmp_path)   # a slices submission exists, rejected
    f.approve_one_piece()
    r = s.facts_of_kind(f.run_id, EventKind.HUMAN_RULING)[-1]
    assert r.payload["ruling"] == "approve_one_piece"
    assert front.accepted_slices_hash(s, f.run_id, 0) is None


def test_the_disagreement_park_is_bounded_to_an_unresolved_dispute(tmp_path):
    s, f = _disagreed(tmp_path)
    f.park(reason="disagreement", phase="slices")            # legal
    assert front.current_park(s, f.run_id).payload["reason"] == "disagreement"
    s2, g = _shaped(tmp_path, "c.db")
    g.request_reshape("three parts")                          # hand-back, no disputed ruling
    with pytest.raises(NotAuthorized, match="disagreement"):
        g.park(reason="disagreement", phase="slices")


def test_dismiss_human_item_leaves_the_dispute_standing(tmp_path):
    """A refused token is dismissed so the cursor moves past it; the
    dismissal is not a resolution."""
    s, f = _disagreed(tmp_path)
    f.dismiss_human_item()
    assert front.unresolved_disagreement(s, f.run_id) is True
    assert s.run_state(f.run_id) == "shaping"


def test_the_park_is_refused_when_the_dispute_resolved_in_the_gap(tmp_path):
    """The window `run_loop` catches: a person's approval lands between
    `stall`'s listing and its `park`."""
    s, f = _disagreed(tmp_path)
    f.approve_one_piece()
    with pytest.raises(NotAuthorized, match="disagreement"):
        f.park(reason="disagreement", phase="slices")


def test_the_hand_back_is_refused_after_an_approval(tmp_path):
    """One hand-back per bundle. A run returned to `queued` by the person's
    approval is refused by the once-per-epoch row, not by the absence of a
    ruling -- the approval leaves the ruling in place (spec §2, the second
    refusal row)."""
    s, f = _disagreed(tmp_path)
    f.approve_one_piece()
    assert s.run_state(f.run_id) == "queued"
    with pytest.raises(NotAuthorized, match="already"):
        f.request_reshape("again")
