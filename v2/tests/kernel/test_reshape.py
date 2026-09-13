"""Revision 16: the shaping visit, the hand-back, the dispute (shaping spec
§2 *request_reshape, approve_one_piece, and the disagreement*)."""
import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import ISSUE, SLICES_BYTES, SPEC_BYTES, Front


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


def test_ruling_after_direction_is_none_off_the_approval_path(tmp_path):
    """`front.ruling_after_direction` (revision 16 fix round 1, B1/B3): the
    fifth fact `Dispute` does not carry, for the OTHER resolution path.
    `None` before any dispute, while the disputed ruling stands unresolved,
    and when the resolution is the approval rather than a direction --
    there is no "ruling after the direction" to find on any of these.

    The third assertion does not, on its own, pin the `HUMAN_RULING` kind
    guard (fix round 2): under the current kernel invariants (one
    hand-back per bundle; approval opens no visit) no command sequence
    puts a shape ruling after an approval, so the `seq > resolution.seq`
    filter alone already returns `[]` here, guard or no guard --
    `test_ruling_after_direction_ignores_a_ruling_after_an_approval` below
    is what actually exercises the guard, by appending the fact directly."""
    s, f = _shaped(tmp_path)
    assert front.ruling_after_direction(s, f.run_id) is None      # no dispute at all
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")   # the disputed ruling
    assert front.ruling_after_direction(s, f.run_id) is None      # unresolved; no direction yet
    f.approve_one_piece()
    assert front.ruling_after_direction(s, f.run_id) is None      # resolved by approval, not a direction


def test_ruling_after_direction_ignores_a_ruling_after_an_approval(tmp_path):
    """Fix round 2: the `HUMAN_RULING` kind guard, pinned for real. No
    command sequence reaches a shape ruling after an approval-resolved
    dispute (one hand-back per bundle; approval opens no visit for another
    one to land in), so the test above never exercises the guard -- its
    `seq` filter alone already returns `[]`. A fact appended directly
    (bypassing the command layer, exactly as `_strip_visit_key` does
    above) is what actually tests it: a ruling recorded after the
    approval must still not be mistaken for the answer to a direction
    that never happened."""
    from kernel.slices import SHAPE_QUESTION
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    f.approve_one_piece()
    n = front.epoch(s, f.run_id)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"question_id": SHAPE_QUESTION, "ruling": "one piece",
                           "reasoning": "a ruling appended after the approval", "cost_if_wrong": "n/a",
                           "epoch": n, "visit": front.shaping_visit(s, f.run_id, n)})
    assert front.ruling_after_direction(s, f.run_id) is None


def test_ruling_after_direction_finds_the_fifth_fact_dispute_does_not_carry(tmp_path):
    """`dispute`'s own `resolution` field IS the direction on this path, not
    the ruling that answers it -- a caller quoting `d.disputed` instead
    would render the ruling the direction OVERRULED, not the one that
    followed it."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one, first look", cost="the spec would find one surface")  # disputed
    f.direct("slice it")
    assert front.ruling_after_direction(s, f.run_id) is None      # the direction opened the visit; no ruling in it yet
    f.shape_round(reasoning="agreed, one piece after all", cost="the spec would find one surface")
    ruling = front.ruling_after_direction(s, f.run_id)
    assert ruling is not None and ruling.payload["reasoning"] == "agreed, one piece after all"
    # NOT `assert ruling.id != d.disputed.id` (fix round 2): under this
    # fixture the two rulings' reasoning always differs, so the assertion
    # above already proves `ruling` is not `d.disputed` -- any mutation
    # that made them the same fact would fail there first. The test right
    # below gives the two rulings the SAME words, so identity is the only
    # thing left to tell them apart.


def test_ruling_after_direction_is_a_different_fact_even_with_the_same_words(tmp_path):
    """The id, not the content, is what proves `ruling_after_direction`
    returns the ruling that followed the direction and not the one it
    overruled: with identical reasoning on both rulings, a check on the
    text alone could not tell them apart."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")   # disputed
    f.direct("slice it")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")   # same words, after the direction
    ruling = front.ruling_after_direction(s, f.run_id)
    d = front.dispute(s, f.run_id)
    assert ruling is not None
    assert ruling.id != d.disputed.id
    assert ruling.seq > d.disputed.seq


def test_ruling_after_direction_picks_the_ruling_nearest_the_direction(tmp_path):
    """Fix round 2: `rulings[0]`, not `rulings[-1]`. Under the current
    kernel invariants (one hand-back per bundle; a direction opens a visit
    only while its dispute is unresolved, which resolving it closes for
    good) no command sequence puts TWO shape rulings after one direction,
    so neither choice reds via the command layer alone -- facts appended
    directly construct the case the spec's own wording names: "the ruling
    recorded after their direction", the one that answers it, not some
    later, unrelated one."""
    from kernel.slices import SHAPE_QUESTION
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one, first look", cost="the spec would find one surface")
    f.direct("slice it")
    n = front.epoch(s, f.run_id)
    visit = front.shaping_visit(s, f.run_id, n)
    first = s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                          payload={"question_id": SHAPE_QUESTION, "ruling": "one piece",
                                   "reasoning": "the ruling that actually answers the direction",
                                   "cost_if_wrong": "n/a", "epoch": n, "visit": visit})
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                 payload={"question_id": SHAPE_QUESTION, "ruling": "one piece",
                          "reasoning": "an unrelated later ruling, not this direction's answer",
                          "cost_if_wrong": "n/a", "epoch": n, "visit": visit})
    ruling = front.ruling_after_direction(s, f.run_id)
    assert ruling is not None and ruling.id == first


def test_ruling_after_direction_ignores_a_grill_ruling(tmp_path):
    """Fix round 2: the `question_id == SHAPE_QUESTION` filter. A grill
    `model_ruling` recorded in the directed visit is not a shape ruling,
    and returning it would hand a resolution brief a grill answer's text
    that has nothing to do with the dispute -- or raise, if a grill
    ruling's payload shape ever differed from a shape ruling's."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one, first look", cost="the spec would find one surface")
    f.direct("slice it")
    n = front.epoch(s, f.run_id)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                 payload={"question_id": "Q1", "ruling": "sqlite", "reasoning": "simplest",
                          "cost_if_wrong": "low", "epoch": n, "visit": front.shaping_visit(s, f.run_id, n)})
    assert front.ruling_after_direction(s, f.run_id) is None


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
    # A second approval. The first one moved the run to `queued`, so the
    # table's own state check refuses this before the dispute check is
    # reached -- the spec names the no-unresolved-dispute row for this case,
    # and both rows are true of it; the refusal a person meets is the state's.
    with pytest.raises(NotAuthorized, match="not legal from state 'queued'"):
        f.approve_one_piece()
    # No dispute at all, reached from `shaping` so the dispute check is what
    # answers. `_new` is a run that has decided nothing.
    s2, g = _new(tmp_path, "b.db")
    assert s2.run_state(g.run_id) == "shaping"
    with pytest.raises(NotAuthorized, match="no unresolved disagreement"):
        g.approve_one_piece()


def test_the_approval_carries_the_replys_cursor(tmp_path):
    """Every human ruling carries the cursor of the item it was read from,
    and this one is no exception. The proof's `assert_sessions` collects
    `cursor_item_id` from EVERY `human_ruling` fact as its read-watermark, so
    a ruling that omits it does not fail here -- it silently corrupts that
    check the first time a live run records one."""
    s, f = _disagreed(tmp_path)
    f.approve_one_piece(cursor="item-77")
    r = s.facts_of_kind(f.run_id, EventKind.HUMAN_RULING)[-1]
    assert r.payload["cursor_item_id"] == "item-77"
    # The same key every sibling ruling writes, under the same name.
    s2, g = _new(tmp_path, "sib.db")
    g.shape_round(SLICES_BYTES)
    g.review_round("accept")
    g.approve()
    sib = s2.facts_of_kind(g.run_id, EventKind.HUMAN_RULING)[-1]
    assert "cursor_item_id" in sib.payload


def test_the_approval_refuses_rather_than_crashes_without_a_dispute(tmp_path):
    """`_side_fact` computes the visit from the disputed ruling. If the
    authorization gate above it ever stopped holding, the next line would be
    an AttributeError -- a crash where this kernel's contract is a refusal,
    and a refusal is what every caller is written to handle."""
    from kernel.commands import Command, _side_fact
    s, f = _new(tmp_path)
    assert front.dispute(s, f.run_id) is None
    cmd = Command(name="approve_one_piece", run_id=f.run_id,
                  expected_version=s.run_version(f.run_id), idempotency_key="k-no-dispute",
                  generation=0, payload={"cursor_item_id": "i-h"})
    with pytest.raises(NotAuthorized, match="no disputed ruling to resolve"):
        _side_fact(s, cmd, actor="human")


def test_the_approval_is_refused_away_from_shaping(tmp_path):
    """The refusal table's third row: the dispute is a shaping-state park, and
    at any later state a reply is read as it is today, so a stale dispute can
    never brick a spec or plan gate.

    Reachable only by forcing the run's state, because an unresolved dispute
    and a state other than `shaping` never coexist through any legal
    transition -- the ruling that disputes it is held AT `shaping`, and
    nothing else can move the run while the dispute stands. The refusal is
    the transition table's, not a check inside the command: declaring the
    command legal from the later states so it could phrase its own message
    would make the table state a legality that does not exist."""
    s, f = _disagreed(tmp_path)
    assert front.unresolved_disagreement(s, f.run_id) is True
    s.set_run_state(f.run_id, "spec_submitted")
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        f.approve_one_piece()
    assert front.unresolved_disagreement(s, f.run_id) is True


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


# -- Task 5: the visit-scoped readers ----------------------------------------

def test_a_plan_an_earlier_visit_rejected_may_be_resubmitted(tmp_path):
    # `shape_round`'s *plan* argument goes to `submit_slices`, which the
    # slices grammar gates: SLICES_BYTES parses, PLAN_BYTES (a `### Task`
    # plan, meant for `submit_plan`) does not -- so this is the slice plan,
    # despite the name every other fixture here uses for it.
    s, f = _new(tmp_path)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision")                                   # request_revision, back to shaping
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    f.request_reshape("three parts")
    # Visit 2: the same bytes are a fresh submission, not the loop spinning.
    f.shape_round(SLICES_BYTES)
    assert s.run_state(f.run_id) == "slices_submitted"
    subs = front.submissions(s, f.run_id, "slices", 0)
    assert [x.payload["visit"] for x in subs] == [1, 2]
    assert front.submissions(s, f.run_id, "slices", 0, visit=2) == subs[-1:]


def test_an_earlier_visit_can_be_read_after_a_later_one_exists(tmp_path):
    """The visit filters select ONE visit, not that visit and every later
    one. No other test asks an older visit a question once a newer visit
    exists, so `== visit` relaxed to `>= visit` passes the whole suite --
    in both readers. The proof's assertion 4 walks every visit of every
    epoch in order, so it is the first caller that would meet the
    difference, and it would meet it as a wrong verdict rather than as an
    error."""
    s, f = _new(tmp_path)
    f.shape_round(SLICES_BYTES)                                   # submission, visit 1
    f.review_round("request_revision", findings=b"findings from visit one")
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    f.request_reshape("three parts")                              # opens visit 2
    f.shape_round(SLICES_BYTES)                                   # submission, visit 2
    f.review_round("request_revision", findings=b"findings from visit two")

    subs = front.submissions(s, f.run_id, "slices", 0)
    assert [x.payload["visit"] for x in subs] == [1, 2]
    assert front.submissions(s, f.run_id, "slices", 0, visit=1) == subs[:1]
    assert front.submissions(s, f.run_id, "slices", 0, visit=2) == subs[1:]

    v1 = front.newest_review_verdict(s, f.run_id, "slices", 0, visit=1)
    v2 = front.newest_review_verdict(s, f.run_id, "slices", 0, visit=2)
    assert v1 is not None and v2 is not None and v1.seq < v2.seq
    assert s.read_blob(v1.payload["findings_hash"]) == b"findings from visit one"
    assert s.read_blob(v2.payload["findings_hash"]) == b"findings from visit two"


def test_a_spec_is_deduped_across_visits_not_within_one(tmp_path):
    """Only phase `slices` is visit-scoped, and this is the history that
    proves it rather than asserting it.

    A hand-back from a REVISION turn is legal, and the spec says so: such an
    epoch "legitimately holds a spec submission" written before the
    hand-back. So an epoch can hold a spec in visit 1, be handed back, be
    disputed, be resolved by the person, and reach `queued` again in visit 2
    -- where the spec author may resubmit. That resubmission must still be
    deduped against visit 1's spec, because a spec that did not change is
    not a revision whatever the shaper did in between.

    Scoping the dedupe to the current visit for EVERY phase -- which reads
    as a harmless simplification -- admits it. Nothing else in the suite
    notices: this is the only reachable history where an epoch holds a
    submission of a non-shaping phase in an earlier visit."""
    s, f = _new(tmp_path)
    f.shape_round()                                        # one piece, visit 1
    f.author_round(SPEC_BYTES)                             # a spec in visit 1
    f.review_round("request_revision", findings=b"this looks like three things")
    f.request_reshape("three parts: a store, an API, a page")   # the revision turn hands back
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    f.approve_one_piece()                                  # the person resolves it
    assert s.run_state(f.run_id) == "queued"
    assert front.shaping_visit(s, f.run_id, 0) == 2
    with pytest.raises(NotAuthorized, match="identical to the prior artefact"):
        f.author_round(SPEC_BYTES)


def test_the_same_plan_twice_in_one_visit_is_still_refused(tmp_path):
    s, f = _new(tmp_path)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision")
    with pytest.raises(NotAuthorized, match="identical"):
        f.shape_round(SLICES_BYTES)


def test_the_round_budget_is_per_epoch_and_a_hand_back_spends_none(tmp_path):
    """`max_rounds` counts the reviewer's request_revision facts. A hand-back
    spends none, and the visit it opens draws on the same budget (spec §2)."""
    s, f = _new(tmp_path)
    before = front.rounds_used(s, f.run_id, "slices", 0)
    f.shape_round(reasoning="one piece", cost="the spec would find one surface")
    f.request_reshape("three parts")
    assert front.rounds_used(s, f.run_id, "slices", 0) == before
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision")
    assert front.rounds_used(s, f.run_id, "slices", 0) == before + 1


def test_the_review_brief_carries_the_visits_own_prior_findings(tmp_path):
    s, f = _new(tmp_path)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"visit one findings")
    f.shape_round(reasoning="one piece", cost="the spec would find one surface")
    f.request_reshape("three parts")
    f.shape_round(SLICES_BYTES)
    brief = f.issue_review_brief("slices")
    assert b"visit one findings" not in brief
    assert front.newest_review_verdict(s, f.run_id, "slices", 0, visit=2) is None


def test_submissions_and_shape_ruling_agree_on_a_visit_none_stamp(tmp_path):
    """Round 2, B5: `front.submissions`'s per-visit filter used to read
    `payload.get("visit", visit_of(...))` -- a default that fires only when
    the KEY is absent, so an explicit `visit: None` never reached the
    fallback and matched no visit at all. `front.shape_ruling` spelled the
    same fallback with `or`, which treats a `None` VALUE the same as an
    absent key and silently falls back to the walk regardless. Two readers
    of the same stamp, two answers -- `front.visit_stamp_of` is the one
    definition now, and both agree: a stamp of `None` matches nothing,
    consistently, for the submission AND the ruling side alike."""
    from kernel.artifacts import put_artifact
    s, f = _new(tmp_path)      # nothing decided yet: no OTHER real visit-1 ruling to confound the read
    n = front.epoch(s, f.run_id)
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": n, "hash": h, "author": "claude", "round": 1, "visit": None})
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": n, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged with a None stamp", "cost_if_wrong": "n/a", "visit": None})
    # Neither reader matches the None-stamped fact to visit 1, the epoch's
    # only real visit -- both now spell "the stamp is absent" the same way
    # (it isn't), rather than one falling back to the walk and the other not.
    assert front.submissions(s, f.run_id, "slices", n, visit=1) == []
    assert front.shape_ruling(s, f.run_id, n, visit=1) is None
    # An UNFILTERED read still finds the submission -- the fact exists, it
    # simply belongs to no visit a caller can ask for by number.
    unfiltered = front.submissions(s, f.run_id, "slices", n)
    assert len(unfiltered) == 1
    assert front.visit_stamp_of(s, f.run_id, unfiltered[0]) is None
