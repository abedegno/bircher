"""The author brief's six revision-16 sections (shaping spec §3, *the spec
round's question*, task-7 brief): the question, the resolution, the
hand-back's standing reasoning, the availability instruction, the epoch's
human answers, and the parse-failure section. Every one is rendered from the
round's CAUSE, never from a predicate on the epoch -- the table the brief
carries, and the rule the reviewers found broken three times before it was
written down.

Fix round 1: a review found the resolution section unreachable on the
direction-resolved path (B1/B2/B3, below) and nine assertions in this file
that could not fail against the histories the first pass's fixtures built.
Each fix is noted at its test; `conftest.py`'s docstring names the fixtures
built to carry the weight.
"""
from coordinator import author, phases
from kernel import front, slices


def test_the_question_renders_for_the_shape_ruling_cause_only(fake):
    f = fake.ruled_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" in brief.casefold()
    # Not the bare "## Dispositions": the skill's own revision section
    # mentions that heading in prose (`test_a_first_draft_asks_for_no_
    # dispositions` names the same trap), so only the requirement heading
    # `author_brief` itself renders is a real assertion.
    assert "## Dispositions are required" not in brief
    assert "## Your previous draft" not in brief
    # Fix round 1, Q3: the ruling's own reasoning AND cost, both distinctive
    # text `ruled_one_piece` now actually writes (conftest.py) -- the first
    # pass's fixture used `Front`'s own defaults, which no assertion named,
    # so a mutation that leaked both fields into the question left this
    # green.
    assert "the export flow and the import flow are one thing" not in brief
    assert "the spec would find one surface" not in brief


def test_the_question_turn_carries_no_earlier_epochs_draft(fake):
    """Fix round 1, Q4 line 22 / fix round 2, item 2: `ruled_one_piece`
    alone has no submission anywhere in its journal, so "no previous
    draft" could not fail no matter how `prior` was scoped -- and even
    WITH a first epoch's accepted spec sitting in the store, the
    QUESTION TURN's own cause (`model_ruling{shape}`) makes
    `_findings_for` return empty findings unconditionally (pinned by
    `test_the_hand_back_cause_renders_no_findings`'s sibling for
    `RESHAPE_REQUESTED`, the same branch), so the previous-draft gate
    stays shut regardless of what `prior` computes to -- a single
    mutation to `prior`'s epoch scoping cannot be observed on the
    question turn alone (the re-review's D1/D2: only the COMPOUND breaks
    it). What CAN be observed on `author_brief`'s own epoch handling: a
    REAL round in the second epoch, whose findings are genuine (a
    revision), must show the second epoch's own draft and never the
    first's."""
    f = fake.ruled_one_piece_after_an_earlier_epochs_accepted_spec()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" in brief.casefold()
    assert "## Your previous draft" not in brief
    assert "The thing, specified." not in brief   # the first epoch's accepted spec (SPEC_BYTES)

    f.front.author_round(b"# Spec\n\nThe second epoch's own draft.\n")
    f.front.review_round("request_revision", findings=b"needs a diagram")
    revision_brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "The second epoch's own draft." in revision_brief
    assert "The thing, specified." not in revision_brief   # never the first epoch's


def test_the_question_survives_a_crash_resume_of_its_turn(fake):
    """Fix round 1, Q6: a SECOND dispatch and session for the SAME round
    cause -- the coordinator's actual resume path -- not the same pure call
    made twice with nothing between it, which proves only that
    `author_brief` writes nothing."""
    f = fake.ruled_one_piece()
    cause_id = phases.round_cause(f.ctx).id
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" in brief.casefold()
    f.crash_resume()
    assert phases.round_cause(f.ctx).id == cause_id       # the resume changed nothing about the cause
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" in brief.casefold()


def test_the_question_is_not_asked_on_four_other_causes(fake):
    """Fix round 2, item 2: `direction_resolved_one_piece` was dropped from
    this loop. Its cause IS a `model_ruling{shape}`, but `author_brief`
    computes `question = resolution or (_question_section(ctx) ...)`, and
    `resolution` is non-`None` on that fixture -- so `_question_section`
    is never even CALLED there, and the assertion held for a reason that
    had nothing to do with the guard it was meant to exercise (the
    re-review's finding: deleting `_question_section`'s own
    `RESHAPE_REQUESTED` guard left the whole suite green). See
    `test_the_question_is_not_asked_a_second_time_in_a_handed_back_epoch`
    below for the guard, exercised on a fixture where `_question_section`
    is actually reached."""
    for f in (fake.refused_submission_retry(), fake.empty_turn_retry(),
              fake.revision_turn(), fake.after_approve_one_piece()):
        brief = author.author_brief(f.ctx, phase="spec").decode()
        assert "is this one piece of work?" not in brief.casefold()


def test_the_question_is_not_asked_a_second_time_in_a_handed_back_epoch(fake):
    """Fix round 2, item 2: `_question_section`'s `RESHAPE_REQUESTED` guard,
    pinned on a fixture that actually reaches it. `handed_back_and_
    disputed`'s round cause is the DISPUTED ruling itself -- a
    `model_ruling{shape}`, the same kind the question renders from -- and
    the dispute is still unresolved, so `_resolution_section` returns
    `None` and `author_brief`'s `resolution or _question_section(ctx)`
    actually calls `_question_section` this time. Its epoch already holds
    the hand-back: without the guard, the shaper's own reconsideration
    would be asked the fresh-perspective question a second time."""
    f = fake.handed_back_and_disputed()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" not in brief.casefold()


def test_the_question_turn_keeps_a_persons_bircher_comment(fake):
    """The snapshot drops the pipeline's seven status prefixes; a person's
    comment that happens to begin `bircher:` is theirs and stays."""
    f = fake.ruled_one_piece(comments=["bircher: parked gate", "bircher: I think this is three things"])
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "I think this is three things" in brief
    assert "parked gate" not in brief


def test_the_resolution_section_renders_without_a_dispositions_block(fake):
    """Fix round 2, item 1: the dispositions check is RESTORED. Fix round 1
    removed it on the reasoning that no branch of `_findings_for` matches a
    plain `HUMAN_RULING` cause "either way" -- reading "no branch matches
    today" as "no branch could be made to match", the exact structural-
    invariant mistake this branch has been burned by before. The re-review
    found two single-edit counterexamples that leak content into a
    `## Findings to address` / `## Dispositions are required` pair on
    exactly this turn: `_findings_for`'s terminal `return b""` rendering
    `cause.payload.get("ruling")` (which for THIS cause is the literal
    string `approve_one_piece`), or a `HUMAN_RULING` branch inserted above
    `HUMAN_DIRECTION`. This assertion is the only thing in the suite that
    catches either. (No `"## Your previous draft" not in brief` here: prior
    is always `None` on this fixture -- nothing was ever drafted before the
    hand-back -- so that half stays inert regardless; it is exercised for
    real by `test_the_parse_failure_retry_suppresses_a_real_previous_draft`
    below, on a turn that actually has one to suppress.)"""
    f = fake.approved_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    # casefold: the section's own heading is "## The shape was disputed and
    # resolved" (a heading capitalises its first word); the phrase itself,
    # not its case, is the thing under test.
    assert "the shape was disputed and resolved" in brief.casefold()
    assert "is this one piece of work?" not in brief.casefold()
    assert "## Dispositions are required" not in brief


def test_the_resolution_re_renders_a_prior_reviewers_findings(fake):
    f = fake.spec_reviewed_then_handed_back_then_approved(findings=b"the auth section is thin")
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" in brief.casefold()
    assert "the auth section is thin" in brief
    assert "## Dispositions are required" in brief
    assert "## Your previous draft" in brief


def test_the_resolution_is_not_repeated_on_a_later_round(fake):
    """The resolution renders on the round whose CAUSE is the resolution --
    once. `after_approve_one_piece`'s epoch holds a resolved dispute, same
    as `approved_one_piece`'s, but this round's cause is a later
    `review_verdict`: reading the epoch instead of the cause would render
    'the shape was disputed and resolved' on this round too."""
    f = fake.after_approve_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" not in brief.casefold()


def test_the_brief_says_the_hand_back_is_spent_once_the_dispute_is_resolved(fake):
    """Final review, finding 3. `skills/spec-author/SKILL.md`'s own "##
    Handing back" section is unconditional and embedded in every spec
    brief, so a seat reading only the skill would believe the lever is
    always live. Once the epoch's one hand-back is spent
    (`request_reshape`: one per bundle), the composed brief says so
    explicitly, on both turns the review found reachable: the resolution's
    own turn (`approved_one_piece`, "after approve_one_piece" in the
    review's table) and a later round in the same resolved epoch
    (`after_approve_one_piece`, "a later round after the resolution")."""
    for f in (fake.approved_one_piece(), fake.after_approve_one_piece()):
        brief = author.author_brief(f.ctx, phase="spec").decode()
        assert "## Handing back is no longer available" in brief
        assert "one hand-back per bundle" in brief.casefold()


def test_the_brief_does_not_say_the_hand_back_is_spent_before_it_is(fake):
    """The negative case: an epoch with a shape ruling and no hand-back yet
    gets only the ordinary availability instruction, never the "already
    spent" correction -- `revision_turn` is exactly the composed
    availability instruction's own positive case."""
    f = fake.revision_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "## Handing back is no longer available" not in brief
    assert "## Handing back\n" in brief


def test_the_resolution_renders_on_the_direction_path_with_the_ruling_that_followed(fake):
    """Fix round 1, B1/B3: the spec names TWO facts the spec round's cause
    can be -- "the person's `approve_one_piece`, or the `shape` ruling
    recorded after their direction". On the direction path `dispute`'s own
    `resolution` field is the DIRECTION, not the ruling that answers it;
    the first pass matched the round's cause against `resolution.id`
    unconditionally, so this path never rendered at all. And where the
    resolution's `else` branch fires, it must quote the ruling that
    FOLLOWED the direction, not `d.disputed` -- the ruling the direction
    OVERRULED (B3): both are asserted here, by their distinct reasoning."""
    f = fake.direction_resolved_one_piece(
        direction_text="split it into three",
        ruling_reasoning="on reflection, only the export flow is its own piece")
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" in brief.casefold()
    assert "split it into three" in brief
    assert "on reflection, only the export flow is its own piece" in brief
    assert "the login and export flows are one thing" not in brief   # the OVERRULED ruling's words (B3)
    assert "## Dispositions are required" not in brief
    assert "is this one piece of work?" not in brief.casefold()


def test_the_resolution_names_the_hand_backs_actor_not_the_reader(fake):
    """Final review, finding 4. The section used to open "You handed this
    run back as an epic," addressing whichever seat is dispatched to write
    the spec as though IT wrote the hand-back. On the approval path the
    spec seat happens to be the hand-back's own vendor
    (`_other_than(disputed.actor)` lands there), so "You" read true by
    coincidence; on the direction path the spec seat is
    `_other_than(newest shape ruling)`, which need not be the hand-back's
    vendor. `direction_resolved_one_piece` hands back as `codex` and rules
    both shape rounds as `claude` (`Front`'s default author): "You" would
    be false for whichever vendor actually reads this brief. Naming the
    actor is true regardless of who reads it."""
    f = fake.direction_resolved_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "**codex** handed this run back as an epic" in brief
    assert "you handed this run back" not in brief.casefold()


def test_the_resolution_re_renders_findings_on_the_direction_path_too(fake):
    """Fix round 1, B2: "on both paths" -- a spec reviewed before the
    hand-back must have its findings and its draft re-rendered beneath the
    resolution whether the dispute resolves by approval or by direction.
    The first pass computed `reopened_findings` only where `resolution` was
    non-None, which B1 alone made unreachable on this path."""
    f = fake.direction_resolved_with_prior_review(findings=b"the auth section is thin")
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" in brief.casefold()
    assert "the auth section is thin" in brief
    assert "## Dispositions are required" in brief
    assert "## Your previous draft" in brief


def test_the_shaping_brief_carries_the_hand_backs_reasoning_every_round(fake):
    """Fix round 1, Q5 (part 1): the standing section persists across a
    genuine retry -- a fresh round cause (an empty turn), not the same pure
    call made twice with nothing between it, which would prove
    determinism, not a retry."""
    f = fake.handed_back(reasoning="the issue names a store, an API and a page")
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief
    f.empty_turn()
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief


def test_the_standing_section_never_shows_an_earlier_visits_draft(fake):
    """Fix round 1, Q4 line 89 / Q5 (part 3): `handed_back` alone never has
    a submission in any visit, and a bare hand-back's own cause carries no
    findings either way, so "not in brief" could not fail regardless of
    `prior`'s visit scoping -- the previous-draft block needs `findings`
    truthy too. Here visit 1 holds a rejected plan and visit 2 gets a
    direction that resolves nothing and stays in visit 2 as real findings
    (`_findings_for`'s `HUMAN_DIRECTION` branch): `findings` is truthy with
    no submission yet in visit 2, which is the history where an unscoped
    `prior` would actually differ from a scoped one."""
    f = fake.handed_back_after_a_rejected_visit_one_plan(
        reasoning="the issue names a store, an API and a page")
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief
    assert "consider the auth flow too" in brief          # this round's own findings, the direction
    assert "## Your previous draft" not in brief          # visit 1's rejected plan must not leak in as visit 2's


def test_a_resolving_direction_opens_the_next_visit_ahead_of_its_own_findings(fake):
    """Fix round 1, Q5 (part 2): `handed_back` alone has no disputed ruling,
    so a direction there resolves nothing and opens no visit
    (`front._visit_boundaries`'s direction branch requires `disputed`) --
    the spec's "the one a resolving direction opens" was asserted but never
    built. Here the disputed ruling exists first, so the direction is a
    real resolution."""
    f = fake.handed_back_and_disputed(reasoning="the issue names a store, an API and a page")
    assert front.shaping_visit(f.store, f.run_id, 0) == 2
    f.direct("slice it")
    assert front.shaping_visit(f.store, f.run_id, 0) == 3
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief
    assert brief.index("a store, an API and a page") < brief.index("slice it")


def test_a_composed_spec_turn_carries_the_availability_instruction(fake):
    """Fix round 1, Q4 lines 95/96: `"Ruling: epic" in brief` and `"rename
    it into place" in brief` are satisfied by `skills/spec-author/SKILL.md`'s
    own "## Handing back" section on its own (shaping spec §3 -- "the
    grammar reaches the seat two ways, or it reaches nobody"), unconditional
    on any state, so a version of this test carrying both beside the count
    assertions below proves nothing beyond what the count already proves.
    Counting is the one check the skill's baseline copy cannot satisfy by
    itself."""
    f = fake.revision_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    f2 = fake.handed_back()
    brief2 = author.author_brief(f2.ctx, phase="spec").decode()
    assert brief.count("## Handing back") == 2
    assert brief2.count("## Handing back") == 1


def test_the_availability_instruction_does_not_double_with_the_question(fake):
    """The question turn is ALSO a composed spec turn of an epoch holding a
    shape ruling and no hand-back -- exactly the availability instruction's
    own condition -- so `question is None` has to guard it, or the same
    grammar renders twice on the one turn that least needs telling twice."""
    f = fake.ruled_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert brief.count("## Handing back") == 1              # the skill's own copy only


def test_the_rendered_block_parses_back(fake):
    f = fake.ruled_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    # The skill's own "## Handing back" section carries the same grammar
    # (see the note above), so the first "Ruling: epic" in the WHOLE brief
    # is the skill's, not the question section's. Anchor on the question's
    # own heading first, then on the INDENTED code line specifically -- the
    # sentence right before it also says "Ruling: epic" in prose, so the
    # first bare occurrence even within this section is still the wrong one.
    after_question = brief.split("## Before you write anything", 1)[1]
    block = after_question.split("\n    Ruling: epic\n", 1)[1]
    text = "Ruling: epic\n" + block.split("\n\n", 1)[0]
    assert slices.parse_reshape(text.encode()) is not None


def test_the_epochs_human_answers_are_a_standing_section(fake):
    """Fix round 1, Q7: the fixture now reaches a REAL spec round at
    `queued` (the person's approval resolves the dispute) rather than
    composing a `phase="spec"` brief while the run actually sat at
    `shaping` -- the seat the spec's clause is about is a spec turn after
    the dispute resolves, not a mismatched phase on a shaping-state run."""
    f = fake.answered_then_handed_back_from_the_resume_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()   # a seat of the other vendor
    assert "## The answers already given" in brief
    assert f.answer_text in brief


def test_a_malformed_hand_back_retry_names_it_and_shows_the_grammar(fake):
    f = fake.malformed_reshape()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "your previous turn's file did not parse" in brief.casefold()
    # Fix round 1, Q4 line 147: the skill's own "## Handing back" section
    # already contains "Ruling: epic" (and the availability instruction
    # adds a second copy, since this epoch holds a shape ruling and no
    # hand-back -- shaping spec §3's own stated redundancy, "the grammar
    # reaches the seat two ways"). A plain `in brief` check is satisfied by
    # either and proves nothing about THIS section specifically; scoping to
    # the text between this section's own heading and the next one does.
    section = brief.split("## Your previous turn's file did not parse", 1)[1]
    section = section.split("\n## ", 1)[0]
    assert "\n    Ruling: epic\n" in section
    assert "## Dispositions are required" not in brief
    # NOT "## Your previous draft" not in brief (fix round 1, Q4 line 149):
    # `malformed_reshape` never submits a spec, so `prior` is `None`
    # whatever `_findings_for` does with this cause -- unbindable here for
    # the same reason line 22 and line 56 were (see
    # `test_the_hand_back_cause_renders_no_findings`'s own note); removed
    # rather than kept as a check that cannot fail.
    # `test_the_parse_failure_retry_suppresses_a_real_previous_draft` below
    # is where that half is exercised for real.


def test_the_parse_failure_retry_suppresses_a_real_previous_draft(fake):
    """Fix round 2, item 1: catches C2 (`if prior is not None and findings:`
    weakened to `if prior is not None:`, which leaves the whole suite
    green against `malformed_reshape` -- it never submits anything, so
    `prior` is `None` regardless of the guard). Here a plan WAS submitted
    and rejected in the SAME visit as the malformed retry, so `prior` is
    genuinely non-`None`; the guard, not the absence of a draft, is what
    keeps it from rendering."""
    f = fake.malformed_reshape_with_a_prior_submission()
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "your previous turn's file did not parse" in brief.casefold()
    assert "## Your previous draft" not in brief


def test_the_round_cause_admits_the_shape_ruling_and_not_a_grill_ruling(fake):
    assert phases._is_round_cause(fake.fact_shape_ruling) is True
    assert phases._is_round_cause(fake.fact_grill_ruling) is False
    assert phases._is_round_cause(fake.fact_reshape_requested) is True
    assert phases._is_round_cause(fake.fact_refused_request_reshape) is True


def test_the_hand_back_cause_renders_no_findings(fake):
    """A pin on `_findings_for`'s contract for this cause, not a
    discriminator on its own: no OTHER branch keys on a `reshape_requested`
    kind, so `_findings_for`'s final `return b""` already agrees with the
    explicit early return, and removing the latter reds nothing here.
    `test_the_shaping_brief_carries_the_hand_backs_reasoning_every_round` is
    what actually exercises the "twice" risk the explicit branch's comment
    names, through `author_brief`'s section ordering rather than through
    this function alone."""
    f = fake.handed_back()
    assert author._findings_for(f.ctx) == b""
