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
    f.direct("slice it along the three parts")                               # resolves, and opens visit 3
    assert front.shaping_visit(s, f.run_id, n) == 3


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
    """The first row of the refusal table, pinned on its own for a clean
    signal: the same assertion is the first line of
    `test_the_hand_back_refuses_its_four_ways`, but that test is already red
    for an unrelated reason (`approve_one_piece`, Task 4), so its own
    pass/fail state cannot show whether THIS guard is what is pinning it."""
    s, f = _shaped(tmp_path)
    with pytest.raises(NotAuthorized, match="reasoning"):
        f.request_reshape("   ")


def test_the_hand_back_refuses_a_second_one_in_the_same_epoch(tmp_path):
    """`test_the_hand_back_refuses_its_four_ways` reaches the once-per-epoch
    guard by way of `approve_one_piece`, which does not exist until Task 4 --
    so that assertion is written but does not run yet. Pinned here by a path
    that needs nothing from Task 4: `_one_piece_destination` is also not yet
    wired (a later task), so today `record_one_piece` sends the run to
    `queued` unconditionally, dispute or none -- the same destination the
    hand-back's own transition assumes. A ruling after the hand-back
    therefore reaches `queued` on its own, and a second `request_reshape`
    there is refused by the same epoch that already holds the first one."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "queued"
    with pytest.raises(NotAuthorized, match="already"):
        f.request_reshape("again")


def test_the_hand_back_refuses_a_run_with_no_shape_ruling_in_its_epoch(tmp_path):
    """The fourth row of the refusal table, pinned on its own: the assertion
    inside `test_the_hand_back_refuses_its_four_ways` sits after the
    `approve_one_piece` call that AttributeErrors on today's driver, so it
    never runs there either, even though this row does not depend on
    `approve_one_piece` at all. A run that was never shaped has nothing to
    hand back to. Unreachable through today's transitions --
    `record_one_piece` is the only way out of `shaping` -- so the state is
    set directly, exactly as a v1 database written before the shaping phase
    existed holds it."""
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
