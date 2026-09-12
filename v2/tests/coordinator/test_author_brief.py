"""The author brief's six revision-16 sections (shaping spec §3, *the spec
round's question*, task-7 brief): the question, the resolution, the
hand-back's standing reasoning, the availability instruction, the epoch's
human answers, and the parse-failure section. Every one is rendered from the
round's CAUSE, never from a predicate on the epoch -- the table the brief
carries, and the rule the reviewers found broken three times before it was
written down.
"""
from coordinator import author, phases
from kernel import slices


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
    assert "the spec would find one surface" not in brief      # the ruling's own words


def test_the_question_survives_a_crash_resume_of_its_turn(fake):
    f = fake.ruled_one_piece()
    author.author_brief(f.ctx, phase="spec")
    brief = author.author_brief(f.ctx, phase="spec").decode()   # same cause, second pass
    assert "is this one piece of work?" in brief.casefold()


def test_the_question_is_not_asked_on_four_other_causes(fake):
    for f in (fake.refused_submission_retry(), fake.empty_turn_retry(),
              fake.revision_turn(), fake.after_approve_one_piece()):
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
    f = fake.approved_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    # casefold: the section's own heading is "## The shape was disputed and
    # resolved" (a heading capitalises its first word); the phrase itself,
    # not its case, is the thing under test.
    assert "the shape was disputed and resolved" in brief.casefold()
    assert "## Dispositions are required" not in brief
    assert "is this one piece of work?" not in brief.casefold()


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


def test_the_shaping_brief_carries_the_hand_backs_reasoning_every_round(fake):
    f = fake.handed_back(reasoning="the issue names a store, an API and a page")
    for _ in range(2):                                    # the visit's first round and its retry
        brief = author.author_brief(f.ctx, phase="slices").decode()
        assert "a store, an API and a page" in brief
    f.direct("slice it")                                  # a direction opens the next visit
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief
    assert brief.index("a store, an API and a page") < brief.index("slice it")
    assert "## Your previous draft" not in brief          # never an earlier visit's


def test_a_composed_spec_turn_carries_the_availability_instruction(fake):
    f = fake.revision_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "Ruling: epic" in brief
    assert "rename it into place" in brief
    f2 = fake.handed_back()
    brief2 = author.author_brief(f2.ctx, phase="spec").decode()
    # NOT plain presence: `skills/spec-author/SKILL.md` carries its own
    # "## Handing back" section, with the same grammar and much of the same
    # wording, unconditionally (shaping spec §3 -- "the grammar reaches the
    # seat two ways, or it reaches nobody"), so every spec brief already
    # contains one copy regardless of state -- "Ruling: epic" and "rename it
    # into place" both live there too. What the runtime instruction adds is
    # a SECOND "## Handing back" section; counting is the one check the
    # skill's own baseline copy cannot satisfy by itself.
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
    f = fake.answered_then_handed_back_from_the_resume_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()   # a seat of the other vendor
    assert "## The answers already given" in brief
    assert f.answer_text in brief


def test_a_malformed_hand_back_retry_names_it_and_shows_the_grammar(fake):
    f = fake.malformed_reshape()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "your previous turn's file did not parse" in brief.casefold()
    assert "Ruling: epic" in brief
    assert "## Dispositions are required" not in brief
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
    names, through `author_brief`'s `suppress` wiring rather than through
    this function alone."""
    f = fake.handed_back()
    assert author._findings_for(f.ctx) == b""
