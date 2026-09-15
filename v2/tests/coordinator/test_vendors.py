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
    # Fix round 1, B2: the easy half of the visit is not the whole visit.
    # Without `and not same_phase` never firing on this clause, the round
    # after a reviewer's `request_revision` would fall through to the
    # ordinary rotation and hand the seat to whoever reviewed it. `author`/
    # `reviewer` are explicit -- left at their defaults, `Front.author`
    # ("claude") always submits and the only other vendor ("codex") always
    # reviews, which is what the correct rule ALSO returns here, so the
    # mutation would pass unnoticed. Submitted by "codex" (what the correct
    # rule would actually pick to author this visit) and reviewed by
    # "claude" makes the two readings diverge for real.
    f.reject_plan(author="codex", reviewer="claude")
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_a_direction_opened_visit_excludes_the_disputed_rulings_vendor(fake):
    f = fake.disagreed(ruling_vendor="claude")
    f.direct("slice it")
    assert author.choose_author_vendor(f.ctx) == "codex"
    f.empty_turn()
    assert author.choose_author_vendor(f.ctx) == "codex"
    # Fix round 1, B1: same gap as B2 -- see its comment. Submitted by
    # "codex" (the correct rule's own answer) and reviewed by "claude" so
    # a same_phase-guard mutation's rotation fallback ("claude") actually
    # differs from the correct answer ("codex").
    f.reject_plan(author="codex", reviewer="claude")
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_the_spec_seat_is_never_the_shape_rulings_vendor(fake):
    f = fake.ruled_one_piece(ruling_vendor="claude")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.approve_one_piece()
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.review_spec(verdict="request_revision", reviewer="codex")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"   # the rotation now


def test_the_spec_clause_yields_to_the_rotation_once_a_verdict_exists(fake):
    """With only two vendors configured, `_other_than(ruling.actor)` and
    "whoever reviewed the fresh-perspective spec" usually coincide, which
    is why the assertion above stays "codex" whether or not the spec
    clause's `not same_phase` guard fires at all. Reviewed by the SHAPER
    itself -- legal, since the shaper ("claude") did not submit this
    artefact -- makes the two diverge: with the guard, the verdict's own
    reviewer ("claude") wins; without it, `_other_than("claude")` ("codex")
    would win instead and this reds."""
    f = fake.ruled_one_piece(ruling_vendor="claude")
    f.review_spec(verdict="request_revision", reviewer="claude", author="codex")
    assert author.choose_author_vendor(f.spec_ctx) == "claude"


def test_the_spec_clause_reads_the_newest_ruling_after_a_real_resolution(fake):
    """B4 (X5 + X12). Two things `test_the_spec_seat_is_never_the_shape_rulings_vendor`
    cannot show:

    X5 -- the spec clause reads the epoch's NEWEST shape ruling, not its
    first. In every OTHER test in this file the two are the same ruling
    (no hand-back epoch ever gets a second one), so a "first ruling"
    misreading would pass unnoticed everywhere else. Here they differ:
    the epoch's first ruling is codex's (`ruling_vendor`), its newest is
    claude's (`disputed_vendor`).

    X12 -- "the spec round after a resolution" is a real case, not the one
    `approve_one_piece()` reaches in the test above: THAT epoch holds no
    dispute at all, so the call is a silent no-op
    (`Scenario.approve_one_piece` swallows the refusal) and the assertion
    after it re-tests the assertion before it. Here the dispute is real
    (a hand-back, a disputed ruling, no resolution yet), so
    `approve_one_piece` actually fires and the run actually reaches
    `queued` -- checked directly, not assumed.

    `ctx.default_author` is "claude" in every fixture in this file (Task 7's
    `_ctx`); picking vendors so the correct answer is "codex" means a
    mutation that falls through to `default_author` -- gating the clause on
    "no hand-back in this epoch", the shape X12 actually takes -- also
    reds, rather than coincidentally matching it."""
    f = fake.disagreed(ruling_vendor="codex", hand_back_vendor="claude",
                       disputed_vendor="claude", park=False)
    f.approve_one_piece()
    assert f.store.run_state(f.run_id) == "queued"           # the resolution actually happened
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
