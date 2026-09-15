"""The disagreement's notice, the person's reply, and the loop's park
recovery (shaping spec §2 *The park is the disagreement's notice, not its
definition*, §4 *The disagreement gate*; Task 8).
"""
from __future__ import annotations

import os
import pathlib

from coordinator import human, phases, seat
from kernel import front


def test_the_notice_and_the_prompt_say_what_happened(fake):
    # Fix round 1, B3: `disputed_vendor="codex"` makes the handed-back
    # ruling's actor ("claude") differ from the disputed ruling's own
    # ("codex") too, not only from the hand-back's ("codex") -- with all
    # three the same vendor, "claude" and "codex" each appearing SOMEWHERE
    # in the prompt says nothing about which seat they're attached to.
    f = fake.disagreed(ruling_vendor="claude", hand_back_vendor="codex", disputed_vendor="codex")
    body = phases.park_notice_body(f.ctx, f.park)
    assert "reaffirmed one piece" in body and "handed" in body
    assert "accepted" not in body                      # no reviewer accepted anything
    # §4: "The park notice names the phase ... as every notice does" -- the
    # disagreement branch is the one notice that used to skip this.
    assert "**slices** phase" in body
    # Fix round 1, X7: the sentence telling the person what to type is
    # asserted nowhere else -- §4 requires the notice to say it. A LITERAL
    # expected string, not `phases.PARK_NEEDS["disagreement"] in body`: the
    # notice is built BY interpolating that same live constant, so
    # asserting the constant against its own interpolation is self-
    # consistent under any value at all and pins nothing (caught this by
    # mutating the constant to "XXneedsXX" and watching the first draft of
    # this assertion stay green).
    assert ("Reply with the single word `approve` to accept one piece, or say "
           "how to slice it. This is your decision, not a reviewer's.") in body
    # Fix round 1, X8: the notice's run-id and session tail, shared with
    # every other park reason's notice but unpinned for this one.
    assert f"Run `{f.run_id}`" in body
    assert f"session `{f.park.payload['session_id']}`" in body
    prompt = human.park_prompt(f.ctx, f.park).decode()
    # The spec's stated point of naming vendors at all: a person can see
    # whether the disagreement crosses vendors or is one vendor
    # contradicting itself. That needs the RIGHT vendor attached to the
    # RIGHT seat, not just both names present somewhere in the text --
    # `"claude" in prompt and "codex" in prompt` is satisfied even if the
    # seats are swapped.
    assert "**claude ruled one piece:**" in prompt
    assert "**codex handed it back as an epic:**" in prompt
    assert "**codex reconsidered and reaffirmed one piece:**" in prompt
    assert f"**codex handed it back as an epic:**\n\n> {f.hand_back_reasoning}" in prompt
    assert f"**codex reconsidered and reaffirmed one piece:**\n\n> {f.ruling_reasoning}" in prompt
    gate = phases.park_notice_body(f.ctx, f.gate_park)
    assert "reaffirmed one piece" not in gate


def test_approve_at_the_disagreement_park_is_the_approval(fake):
    f = fake.disagreed()
    assert human.classify_batch([{"text": "approve"}], state="shaping",
                                grill_open=False, dispute=True) == ("approve_one_piece", "")
    assert human.classify_batch([{"text": "retry"}], state="shaping",
                                grill_open=False, dispute=True) == ("retry", "")
    assert human.classify_batch([{"text": "slice it"}], state="shaping",
                                grill_open=False, dispute=True) == ("direction", "slice it")


def test_the_dispute_reading_applies_only_at_shaping(fake):
    """Fix round 1, X3. §2 *The dispute* and §4 *The disagreement gate*
    both bind the dispute-driven reading to `shaping` and "nowhere else --
    never at a later phase's gate ... so a stale dispute can never brick a
    spec or plan gate": a run whose CURRENT epoch happens to hold an
    unresolved dispute from an earlier visit, now sitting at an ordinary
    gate, reads `approve` as the gate's approval, not as `approve_one_piece`
    -- there is no dispute to resolve at those states, and `approve_one_piece`
    itself is refused everywhere but `shaping` (spec §2's refusal table)."""
    for state in ("spec_accepted", "plan_accepted", "slices_accepted"):
        assert human.classify_batch([{"text": "approve"}], state=state,
                                    grill_open=False, dispute=True) == ("approve", "")


def test_the_grill_branch_is_skipped_at_the_other_two_shaping_states_too(fake):
    """Fix round 1, X9. `test_the_grill_branch_is_skipped_at_every_shaping_state`
    is named for all three shaping states but only ever drives `shaping`
    itself; `SHAPING_STATES` also names `slices_submitted` and
    `slices_accepted`, where a stray `grill_open=True` from a question
    asked before the hand-back must not turn a plan revision or approval
    into an `answer` either."""
    for state in ("slices_submitted", "slices_accepted"):
        assert human.classify_batch([{"text": "here are the answers"}], state=state,
                                    grill_open=True, dispute=False) == ("revision", "here are the answers")


def test_approve_is_the_approval_even_in_a_grill_epoch(fake):
    """A `bircher:grill` epoch holding an unanswered question does not make
    the person's `approve` an answer: the grill branch is skipped at every
    shaping state, and the dispute reading takes it."""
    f = fake.disagreed(policy="bircher:grill", unanswered_question=True)
    assert f.reply("approve") == "approve_one_piece"
    assert f.store.run_state(f.run_id) == "queued"


def test_the_grill_branch_is_skipped_at_every_shaping_state(fake):
    """`record_human_answer` is refused from every shaping state, so a
    coordinator that classified a reply as `answer` there would dismiss it."""
    f = fake.grill_open_at_shaping()
    assert human.classify_batch([{"text": "here are the answers"}], state="shaping",
                                grill_open=True, dispute=False)[0] == "direction"


def test_retry_at_the_disagreement_park_is_refused_and_answered(fake):
    f = fake.disagreed()
    assert f.reply("retry") == "dismissed"
    assert "grant_round" in f.session_replies[-1]
    assert front.unresolved_disagreement(f.store, f.run_id) is True


def test_the_loop_records_the_park_when_a_pass_died_before_it(fake):
    f = fake.disagreed(park=False)
    assert phases.run_loop(f.ctx) == phases.Exit.PARKED
    assert front.current_park(f.store, f.run_id).payload["reason"] == "disagreement"
    assert f.seats_dispatched == 0                      # no author, no reviewer


def test_an_approve_before_the_park_is_taken_with_no_park_recorded(fake):
    f = fake.disagreed(park=False, pending_reply="approve")
    assert phases.run_loop(f.ctx) != phases.Exit.PARKED
    assert f.store.run_state(f.run_id) == "queued"
    assert front.current_park(f.store, f.run_id) is None


def test_a_refused_disagreement_park_does_not_crash_the_pass(fake):
    f = fake.disagreed(park=False, resolve_in_the_gap=True)
    assert phases.run_loop(f.ctx) != phases.Exit.FAILED
    assert front.current_park(f.store, f.run_id) is None


def test_a_disagreement_park_refused_for_another_reason_fails_rather_than_spins(fake, monkeypatch):
    """Fix round 1, L2. `except NotAuthorized: ... continue` inside
    `while True` is an infinite-loop shape: it terminates today only
    because the ONE refusal the kernel can raise here (the resolved-in-the-
    gap one) also clears `unresolved_disagreement`, making the branch's own
    condition false on the next pass. There is no real-kernel history that
    refuses this park for any OTHER reason while leaving the dispute
    unresolved (`_check_park`'s only reason-specific guard for
    `disagreement` IS that one), so exercising the general case means
    forcing the refusal directly rather than building a history for it."""
    f = fake.disagreed(park=False)
    calls = {"n": 0}

    def fake_stall(ctx, reason, **kw):
        calls["n"] += 1
        from kernel.authz import NotAuthorized as _NotAuthorized
        raise _NotAuthorized("park disagreement: refused for an unrelated reason")
    monkeypatch.setattr(phases, "stall", fake_stall)
    assert phases.run_loop(f.ctx) == phases.Exit.FAILED
    assert calls["n"] == 1                    # one attempt, not a spin


def test_the_shape_rounds_counter_is_per_visit(fake):
    f = fake.handed_back()
    f.submit_plan()
    assert (pathlib.Path(f.bundle_dir) / f.run_id / "slices-v2-r1.md").exists()
    assert (pathlib.Path(f.bundle_dir) / f.run_id / "slices-v1-r1.md").exists()


def test_the_round_number_itself_is_per_visit_not_per_epoch(fake):
    """The test above pins the NAME's visit but, with nothing ever
    submitted in visit 1, it cannot tell a per-visit round counter from a
    per-epoch one -- rnd is 1 either way. `handed_back_after_a_rejected_visit_one_plan`
    already gives visit 1 a real submission, so visit 2's first plan is
    round 1 of ITS OWN count and round 2 of the epoch's: an epoch-wide
    counter would name this file "slices-v2-r2.md" instead."""
    f = fake.handed_back_after_a_rejected_visit_one_plan()
    f.submit_plan()
    assert (pathlib.Path(f.bundle_dir) / f.run_id / "slices-v2-r1.md").exists()


def test_a_real_hand_back_drives_the_loop_through_the_reshaped_arm(fake):
    """Fix round 1, M1/X13. Every other test in this module builds its
    history directly through `Front`, bypassing `author.author_round` (and
    the `reshaped` outcome it returns into `run_loop`) entirely -- so
    nothing ever actually exercises the branch that reads a REAL hand-back
    turn's result and continues into the shaping round. This is also the
    only test that shows the whole of Task 8 composing rather than each
    piece poked in isolation: the spec clause choosing the fresh
    perspective for the turn that hands the run back, the slices clause
    choosing the handed-back ruling's own vendor for the reconsideration,
    and the disagreement park at the end of it, all from one real pass
    through `run_loop` with no fixture shortcuts."""
    f = fake.ruled_one_piece(ruling_vendor="claude")

    def on_prompt(sid, text):
        ws = f.omni.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher spec author brief"):
            open(os.path.join(ws, seat.RESHAPE_OUT), "wb").write(
                b"Ruling: epic\nReasoning: the issue names a store and a page.\n")
        elif text.startswith("# Bircher slices author brief"):
            open(os.path.join(ws, seat.SHAPE_OUT), "wb").write(
                b"Ruling: one piece\nReasoning: still one piece on reconsideration.\n"
                b"Cost if wrong: a spec round.\n")
    f.omni.on_prompt = on_prompt

    assert phases.run_loop(f.ctx) == phases.Exit.PARKED
    park = front.current_park(f.store, f.run_id)
    assert park.payload["reason"] == "disagreement"
    d = front.dispute(f.store, f.run_id)
    assert d.handed_back.actor == "claude"    # the original shaper
    assert d.hand_back.actor == "codex"       # the fresh perspective wrote the hand-back
    assert d.disputed.actor == "claude"       # reconsidered by the handed-back ruling's own vendor
