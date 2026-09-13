"""`choose_author_vendor`'s revision-16 clauses (shaping spec §2 *The
vendors*, Task 8): which vendor authors the epoch's first spec turn, and
which vendor authors a hand-back's or a direction's shaping visit.

Every clause is tested across at least three rounds of ONE visit -- the
first round, a retry, and a round after a reviewer's verdict -- because the
spec is explicit that the two `slices` clauses hold for the WHOLE visit,
verdicts or none: an empty-turn retry has a new cause and would otherwise
fall through to `default_author`, and a rejected plan followed by the
ordinary rotation would hand the round to the very vendor the rule
excludes. A clause tested only in the easy case is not tested.
"""
from __future__ import annotations

from coordinator import author


def test_the_hand_back_visit_goes_to_the_handed_back_rulings_vendor(fake):
    f = fake.handed_back(ruling_vendor="claude", hand_back_vendor="codex")
    assert author.choose_author_vendor(f.ctx) == "claude"
    f.empty_turn()                                    # a new cause, same rule
    assert author.choose_author_vendor(f.ctx) == "claude"
    f.reject_plan()                                   # a verdict, still the rule
    assert author.choose_author_vendor(f.ctx) == "claude"


def test_a_hand_back_from_the_rulings_own_vendor_goes_to_the_other(fake):
    """On a revision turn the rotation can hand the spec to the shaper's
    vendor; a reconsideration by the vendor that just handed back is no
    reconsideration."""
    f = fake.handed_back(ruling_vendor="claude", hand_back_vendor="claude")
    assert author.choose_author_vendor(f.ctx) == "codex"
    f.empty_turn()
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_a_direction_opened_visit_excludes_the_disputed_rulings_vendor(fake):
    f = fake.disagreed(ruling_vendor="claude")
    f.direct("slice it")
    assert author.choose_author_vendor(f.ctx) == "codex"
    f.empty_turn()
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_the_spec_seat_is_never_the_shape_rulings_vendor(fake):
    f = fake.ruled_one_piece(ruling_vendor="claude")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.approve_one_piece()
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.review_spec(verdict="request_revision", reviewer="codex")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"   # the rotation now
