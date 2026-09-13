"""The disagreement's notice, the person's reply, and the loop's park
recovery (shaping spec §2 *The park is the disagreement's notice, not its
definition*, §4 *The disagreement gate*; Task 8).
"""
from __future__ import annotations

import pathlib

from coordinator import human, phases
from kernel import front


def test_the_notice_and_the_prompt_say_what_happened(fake):
    f = fake.disagreed(ruling_vendor="claude", hand_back_vendor="codex")
    body = phases.park_notice_body(f.ctx, f.park)
    assert "reaffirmed one piece" in body and "handed" in body
    assert "accepted" not in body                      # no reviewer accepted anything
    prompt = human.park_prompt(f.ctx, f.park).decode()
    assert "claude" in prompt and "codex" in prompt
    assert f.ruling_reasoning in prompt and f.hand_back_reasoning in prompt
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
