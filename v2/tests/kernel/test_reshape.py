"""Revision 16: the shaping visit, the hand-back, the dispute (shaping spec
§2 *request_reshape, approve_one_piece, and the disagreement*)."""
import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import ISSUE, SLICES_BYTES, Front


# The four fixtures of the plan's *Shared test fixtures* section go here,
# written once: _shaped, _new, _disagreed, _rejected_then_disagreed.

def _shaped(tmp_path, name="k.db", labels=("bircher:autonomous",)):
    """A run at `queued` through a one-piece ruling: the state a hand-back
    is legal from. The driver's default `shape=True` rules it at birth."""
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", issue=ISSUE, labels=list(labels))
    assert s.run_state(f.run_id) == "queued"
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
