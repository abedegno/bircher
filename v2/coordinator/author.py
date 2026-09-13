"""The author round (spec §3 Author round, §3 loop, §7)."""
from __future__ import annotations

import json
import os
import pathlib
import re

from coordinator import phases, seat
from coordinator.human import take_listing            # Task 19; a stub until then
from coordinator.session import AgentMismatch
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role, SeatsExhausted
from kernel.events import EventKind
from kernel.policy import policy_of, to_payload

SKILLS = pathlib.Path(__file__).resolve().parents[2] / "skills"
_Q = re.compile(r"^### (Q[^:\s]+):\s*(.+?)\s*$", re.M)

#: Where each phase's author skill lives (planning ruling 11).
_SKILL_DIR = {"slices": "shape-author"}


def parse_questions(text: str) -> list[dict]:
    out = []
    blocks = list(_Q.finditer(text))
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        body = text[m.end():end]
        rec = re.search(r"^Recommended:\s*(.+?)\s*$", body, re.M)
        rul = re.search(r"^Ruling:\s*(.+?)\s*$", body, re.M)
        out.append({"id": m.group(1), "question": m.group(2),
                    "recommended": rec.group(1) if rec else "", "ruling": rul.group(1) if rul else None})
    return out


def choose_author_vendor(ctx) -> str:
    """spec §3 Rotation: the reviewer of round r authors round r+1; a phase's
    first artefact is authored by the vendor that did not review the previous
    phase's last accepted artefact (the default for the spec).

    THIS round's seat, if it already has one, settles the question first: a
    second pass over a round the coordinator crashed part-way through adopts
    that session, and the artefact it submits was written by the vendor the
    server ran, so the vendor is read from the bundle name rather than chosen
    again (ruling 14).

    "The round's session" is the CURRENT round's, so the seat counts only when
    its create carries this round's cause. `front.newest_seat` is the newest
    author create of the PHASE and EPOCH, and a request_revision returns the
    run to the same phase and epoch -- the previous round's seat is still the
    newest one, and reading the vendor off it would hand every revision back
    to the vendor whose draft was just sent for revision, so the rotation
    would never happen. Excluding it costs nothing: the kernel's identity
    guard compares the submitting actor against the newest seat AT SUBMIT
    TIME, which by then is this round's own create, made with whichever vendor
    this function chose.
    """
    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    seat_row = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
    if seat_row is not None and seat_row["cause"] == phases.round_cause(ctx).id:
        return front.vendor_of(seat_row["session"]["agent_name"])
    verdicts = [v for v in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT)
                if v.payload.get("ruling") == "review_ruling"]
    same_phase = [v for v in verdicts if v.payload["phase"] == phase and v.payload["epoch"] == n]

    # Revision 16, in precedence order (shaping spec §2 *The vendors*). The
    # adopted-seat clause above is first and unchanged.
    d = front.dispute(store, run_id)
    if phase == "spec":
        # Every spec author round of an epoch that holds a shape ruling
        # takes the vendor that is NOT the newest shape ruling's actor -- the
        # epoch's first spec turn, and the spec round after a resolution,
        # both of which would otherwise fall through to `default_author`,
        # which is the shaper's own vendor. It yields to the same-phase
        # rotation once a spec review_verdict exists in the epoch: by then
        # the fresh perspective has already been had, and a revision turn's
        # author is whoever the rotation names.
        ruling = front.shape_ruling(store, run_id, n)
        if ruling is not None and not same_phase:
            return _other_than(ctx, ruling.actor)
    elif phase == "slices" and d is not None:
        # Both clauses below hold for the WHOLE visit a hand-back or a
        # direction opens, reviewer verdicts or none: an empty-turn retry
        # has a new cause and would otherwise fall through to
        # `default_author`, and a rejected plan followed by the ordinary
        # rotation would hand the round right back to the vendor the rule
        # excludes. `same_phase` is deliberately not read here -- within
        # the visit only the REVIEWER seat rotates, and it is always the
        # vendor that did not author, so cross-vendor review still holds.
        visit = front.shaping_visit(store, run_id, n)
        if front.visit_of(store, run_id, d.hand_back.seq) == visit:
            # The hand-back's own visit: the handed-back ruling's vendor
            # reconsiders with the spec author's counter-argument in hand --
            # unless that vendor is the hand-back's own actor, in which case
            # "reconsidering" would be one vendor arguing with itself, and
            # the other vendor takes the visit instead.
            want = d.handed_back.actor
            return _other_than(ctx, want) if want == d.hand_back.actor else want
        if d.resolution is not None and d.resolution.kind == EventKind.HUMAN_DIRECTION:
            # A direction-opened visit: the person has just overruled the
            # disputed ruling's vendor, so the visit is not that vendor's to
            # reconsider either.
            #
            # Fix round 1, row 6: the `.kind == HUMAN_DIRECTION` check
            # cannot be independently driven through the kernel -- tried
            # forging a second `reshape_requested` past `request_reshape`'s
            # own refusal, and it IS reachable that way, which is the proof
            # the check is not redundant AS CODE, only unreachable AS
            # HISTORY. It rests on two invariants elsewhere: `request_reshape`'s
            # one-hand-back-per-bundle refusal (`kernel/authz.py`, the
            # `reshape_requested exists in this epoch` row of its table) and
            # `record_human_direction`'s null destination (`kernel/authz.py`'s
            # `_TRANSITIONS`, which is why an epoch's only OTHER dispute-side
            # fact, `approve_one_piece`, moves the run OUT of `shaping`
            # instead of opening a further visit). Together they mean any
            # visit past the hand-back's is opened only by a dispute-resolving
            # direction, so `visit != visit_of(hand_back)` already implies
            # `d.resolution.kind == HUMAN_DIRECTION` here -- a relaxation of
            # either invariant is what would make this check load-bearing.
            return _other_than(ctx, d.disputed.actor)

    if same_phase:
        return same_phase[-1].payload["reviewer_identity"]
    if phase == "plan":
        spec_accepts = [v for v in verdicts if v.payload["phase"] == "spec" and v.payload["verdict"] == "accept"]
        if spec_accepts:
            vendors = sorted(ctx.agent_ids)
            other = [x for x in vendors if x != spec_accepts[-1].payload["reviewer_identity"]]
            return other[0] if other else vendors[0]
    return ctx.default_author


def _other_than(ctx, vendor: str) -> str:
    """The other configured vendor (revision 16): the smallest one that is
    not *vendor*, so the choice is deterministic when more than two are
    configured, and *vendor* itself when there is nothing else to pick."""
    others = [v for v in sorted(ctx.agent_ids) if v != vendor]
    return others[0] if others else vendor


def _findings_for(ctx) -> bytes:
    cause = phases.round_cause(ctx)
    store = ctx.store
    # Revision 16: two causes render their own section instead of findings.
    # The hand-back's reasoning is the shaping brief's standing section, and
    # the shape ruling is the question-turn's cause, which carries nothing.
    # Returning the reasoning here would render it twice and arm both the
    # dispositions block and the previous-draft block on the one round whose
    # intended endings include a ruling with no artefact.
    if cause.kind in (EventKind.RESHAPE_REQUESTED, EventKind.MODEL_RULING):
        return b""
    if cause.kind == EventKind.AUTHOR_EMPTY and cause.payload.get("detail"):
        return b""                      # the parse-failure section renders it
    if cause.kind == EventKind.REVIEW_VERDICT and cause.payload.get("findings_hash"):
        return store.read_blob(cause.payload["findings_hash"])
    if cause.kind == EventKind.HUMAN_DIRECTION:
        return cause.payload["text"].encode()
    if cause.kind == EventKind.BUNDLE_REVISED:
        return json.dumps(cause.payload["diff"], indent=1).encode()
    if cause.kind == EventKind.COMMAND_REJECTED:
        return cause.payload.get("detail", "").encode()
    return b""


#: The fresh-perspective question (shaping spec §3, revision 16): rendered
#: once, on the spec round whose cause is the shape ruling. Its own section,
#: not a finding -- the author has nothing to disposition yet.
QUESTION = (
    "\n## Before you write anything\n\n"
    "Is this one piece of work? One piece is a single capability a reviewer could\n"
    "see working end to end. If the spec you are about to write would specify more\n"
    "than one such capability, do not write it. Write `%s` instead and end the\n"
    "turn.\n" % seat.RESHAPE_OUT)

#: The hand-back's grammar (shaping spec §3, revision 16), shared by three
#: renderings: the question turn, the availability instruction on every later
#: composed spec turn, and the malformed-retry section -- one copy, so the
#: three cannot drift into three different readings of what the kernel parses.
HAND_BACK_GRAMMAR = (
    "Write the file whole somewhere else in the worktree and rename it into place:\n"
    "a watched file counts as landed the moment it is non-empty. Write exactly\n"
    "this, with the first non-blank line exactly `%s`:\n\n"
    "    %s\n"
    "    Reasoning: <why this is more than one piece; name the pieces you see>\n"
    % (slices.RESHAPE_LINE, slices.RESHAPE_LINE))


def _question_section(ctx) -> str | None:
    """The fresh-perspective question (shaping spec §3). A rendering of the
    round's CAUSE, never a predicate on the epoch: the first spec turn after
    the shape ruling, and a crash-resume of that turn, whose cause is
    unchanged. An epoch that holds a hand-back has had its question."""
    from kernel.slices import SHAPE_QUESTION
    cause = phases.round_cause(ctx)
    if not (cause.kind == EventKind.MODEL_RULING
            and cause.payload.get("question_id") == SHAPE_QUESTION):
        return None
    if front.epoch_facts(ctx.store, ctx.run_id, EventKind.RESHAPE_REQUESTED, ctx.epoch()):
        return None
    return QUESTION + "\n" + HAND_BACK_GRAMMAR


def _resolution_section(ctx) -> str | None:
    """The person's decision, rendered as its own section rather than as a
    finding (shaping spec §3): the spec round in an epoch whose dispute was
    resolved is briefed with what they decided and writes the spec.

    Fix round 1, B1/B3: the spec names TWO facts as "the resolution" the spec
    round's cause can be -- "the person's `approve_one_piece`, or the `shape`
    ruling recorded after their direction". `dispute`'s own `resolution`
    field is the approval on one path and the DIRECTION on the other, never
    the post-direction ruling; matching the round's cause against
    `d.resolution.id` unconditionally is why the direction path never
    rendered. `front.ruling_after_direction` is the fifth fact `Dispute`
    does not carry, and quoting IT rather than `d.disputed` is what fixes
    B3 too: `d.disputed` is the ruling the direction OVERRULED, not the one
    that followed it.

    Final review, finding 4: this section used to open "You handed this
    run back as an epic," addressing the reader as though they wrote the
    hand-back. On the approval path the spec seat IS the hand-back's vendor
    (`_other_than(disputed.actor)` lands there), so it read true by
    coincidence; on the direction path the spec seat is
    `_other_than(newest shape ruling)`, which can be -- and in a real run
    was -- a different vendor than the one who wrote `reshape.md`. Naming
    the hand-back's actor (`d.hand_back.actor`) instead of addressing the
    reader is true on both paths."""
    d = front.dispute(ctx.store, ctx.run_id)
    if d is None or d.resolution is None:
        return None
    cause = phases.round_cause(ctx)
    if d.resolution.kind == EventKind.HUMAN_RULING:
        if cause.id != d.resolution.id:
            return None
        what = "A person approved the shaper's ruling: this is one piece of work."
    else:
        ruling = front.ruling_after_direction(ctx.store, ctx.run_id)
        if ruling is None or cause.id != ruling.id:
            return None
        what = ("A person directed the shaper:\n\n> %s\n\nand the shaper then ruled:\n\n> %s"
                % (d.resolution.payload["text"], ruling.payload["reasoning"]))
    return ("\n## The shape was disputed and resolved\n\n"
            "**%s** handed this run back as an epic. The shaper reconsidered and reaffirmed\n"
            "one piece. %s\n\nWrite the spec.\n" % (d.hand_back.actor, what))


def author_brief(ctx, *, phase: str, resume_answer=None) -> bytes:
    store, run_id = ctx.store, ctx.run_id
    if resume_answer is not None:
        # Say that the previous turn's files are gone. The coordinator moves
        # them aside before every re-prompt (spec section 2: a file at the
        # watched path is this turn's only if no earlier turn left it), and an
        # artefact written before the human answered must never be submitted
        # as though it answered them. But the session's own context still says
        # it wrote one. Live on 2026-09-08 an author replied "the spec is
        # already complete" and ended its turn without writing anything: no
        # file appeared, and the coordinator polled the empty path for the
        # whole 90-minute cap. What the coordinator does to the worktree, the
        # prompt has to say.
        return ("Answered; continue.\n\n"
                "Your previous draft has been moved aside and no longer exists at "
                f"`{seat.ARTIFACT_OUT}`. Write the {phase} there again from scratch, "
                "incorporating the answers below, however complete you believe an "
                "earlier draft was. A turn that ends with no file at that path "
                "counts as an empty turn.\n\n"
                + resume_answer.payload["answer"]).encode()
    parts = [f"# Bircher {phase} author brief\n"]
    parts.append((SKILLS / _SKILL_DIR.get(phase, f"{phase}-author") / "SKILL.md").read_text())
    if phase == "slices":
        from kernel.slices import COARSE
        parts.append("\n## Files\n\nRuling: `%s`\nSlice plan: `%s`\n\nEnd the turn with exactly one of them.\n"
                     % (seat.SHAPE_OUT, seat.ARTIFACT_OUT))
        # The definition of coarse, rendered from the one place it lives
        # (shaping spec §1): never a second copy in a skill file.
        parts.append("\n## The definition of a slice\n\n> %s\n\nA slice plan names no files, tasks or acceptance tests.\n" % COARSE)
    else:
        parts.append("\n## Files\n\nArtefact: `%s`\nQuestions: `%s`\n" % (seat.ARTIFACT_OUT, seat.QUESTIONS_OUT))
    # Revision 16: the question and the resolution are their own sections,
    # not findings. Reading the CAUSE, not the epoch: the reviewers found the
    # same defect three times by reading "does this epoch hold a ruling"
    # instead of "is the ruling this round's cause".
    resolution = _resolution_section(ctx) if phase == "spec" else None
    question = resolution or (_question_section(ctx) if phase == "spec" else None)
    reopened_findings = None
    if resolution is not None:
        # A spec was reviewed before the hand-back: its findings are not
        # dropped by the hand-back, and a reviewer's outstanding catch still
        # reaches the author -- re-rendered beneath the resolution, not
        # through `_findings_for`, whose HUMAN_DIRECTION branch would render
        # the person's DIRECTION text here, not the reviewer's findings. On
        # BOTH resolution paths (fix round 1, B2): `resolution` is truthy for
        # the direction path too now, so this reads the same regardless of
        # which fact resolved the dispute.
        #
        # Guarded on `findings_hash` (fix round 1, minor 4), matching
        # `_findings_for`'s own REVIEW_VERDICT branch: a `review_ruling` is
        # expected to carry one, but nothing here enforces it, and reading
        # it unguarded would crash a resolution brief over a defect
        # somewhere else entirely.
        verdict = front.newest_review_verdict(store, run_id, "spec", ctx.epoch())
        d = front.dispute(store, run_id)
        if (verdict is not None and verdict.payload.get("findings_hash")
                and d is not None and verdict.seq < d.hand_back.seq):
            reopened_findings = store.read_blob(verdict.payload["findings_hash"])
    if phase == "slices":
        # The hand-back's reasoning, standing in every visit after it, ahead
        # of whatever the round's cause renders -- so a direction typed into
        # the reshaping session does not displace it.
        d = front.dispute(store, run_id)
        if d is not None:
            parts.append("\n## Why this was handed back\n\nThe spec author disputed the shape:\n\n> %s\n"
                         % d.hand_back.payload["reasoning"])
    if question is not None:
        parts.append(question)
    cause = phases.round_cause(ctx)
    if cause.kind == EventKind.AUTHOR_EMPTY and cause.payload.get("detail"):
        parts.append("\n## Your previous turn's file did not parse\n\n%s\n\n%s\n"
                     % (cause.payload["detail"], HAND_BACK_GRAMMAR))
    if phase == "spec":
        n = ctx.epoch()
        d = front.dispute(store, run_id, n)
        # The availability instruction: every composed spec turn of an epoch
        # that holds a shape ruling and no hand-back yet, so a revision turn
        # briefed with a reviewer's "several pieces" finding can act on it.
        if (front.shape_ruling(store, run_id, n) is not None
                and d is None
                and question is None):
            parts.append("\n## Handing back\n\nIf this issue is more than one piece of work, do not write the\n"
                         "spec. Hand it back instead and end the turn.\n\n%s\n" % HAND_BACK_GRAMMAR)
        elif d is not None and d.resolution is not None:
            # Final review, finding 3 (a judgement call, not a correctness
            # bug): the spec-author SKILL.md's own "## Handing back" section
            # is unconditional and embedded in every spec brief -- it has no
            # way to know the epoch already spent its one hand-back
            # (`request_reshape`: one per bundle, shaping spec §2). Left
            # alone, a seat that still believes the issue is an epic hands
            # back again, is refused, and the refusal becomes the next
            # round's findings -- the one reachable way to spend two extra
            # seats and earn a second park in the same epoch (attack 9). This
            # is cheaper than that: say plainly, on exactly the turns this
            # can happen, that the lever above is already spent.
            #
            # `d.resolution is not None`, not merely `d is not None`: a spec
            # phase turn only exists once the dispute is resolved (an
            # unresolved one keeps the run at `shaping`), so requiring the
            # resolution keeps this section off the one synthetic case a
            # unit test builds on purpose -- a hand-back with nothing
            # resolved yet -- while still covering both real turns: the
            # resolution's own (`question` is the resolution section) and
            # any later round in the resolved epoch.
            parts.append(
                "\n## Handing back is no longer available\n\n"
                "The skill above describes handing this issue back as an epic. That "
                "already happened once in this epoch (one hand-back per bundle) and a "
                "person has since decided this is one piece of work -- `request_reshape` "
                "would refuse a second one, so writing `%s` again would only cost a "
                "round. Write the spec.\n" % seat.RESHAPE_OUT)
        answers = front.epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
        if answers:
            # Standing, because a hand-back from the `grill: human` resume
            # turn is legal and the next seat is another vendor's: without
            # this the person's answers reach nobody.
            parts.append("\n## The answers already given\n\n%s\n"
                         % "\n\n".join(a.payload["answer"] for a in answers))
    pol = policy_of(store, run_id)
    parts.append("\n## Policy\n\n```json\n%s\n```\n" % json.dumps(to_payload(pol), indent=1))
    # The policy as an instruction, not as data. The skill describes both
    # grills and the JSON above states which one is in force, and a spec
    # author read that as permission to skip the asking: under grill=human it
    # wrote the artefact on the first turn, the kernel refused the submit
    # (spec section 1 -- the author MUST ask at least once), and the run was
    # discarded. What the kernel enforces, the brief says in the imperative.
    if phase == "spec" and pol.grill == "human" and front.grill_open(store, run_id):
        parts.append(
            "\n## This turn: ask, do not write the spec\n\n"
            "This run is `grill: human` and the human has not answered yet. End this\n"
            "turn with `%s` and NO `%s`. The kernel refuses a spec submitted before\n"
            "the human has answered, so a spec written now is a turn thrown away. You\n"
            "will be prompted again in this same session with the answers, and you\n"
            "write the spec then.\n" % (seat.QUESTIONS_OUT, seat.ARTIFACT_OUT))
    parts.append("\n## Issue snapshot\n\n```json\n%s\n```\n" % store.read_blob(front.bundle_hash(store, run_id)).decode())
    if phase == "plan":
        parts.append("\n## The accepted spec\n\n%s\n" % store.read_blob(store.phase_artifact(run_id, "spec")).decode())
    # Fix round 1, Q1: no separate `suppress` flag. Requirement 2's
    # suppression -- no dispositions block, no previous draft, on the
    # question turn, the resolution turn (unless reopened) and the
    # parse-failure retry -- is already `_findings_for`'s doing: its early
    # returns for `MODEL_RULING`/`RESHAPE_REQUESTED` and `AUTHOR_EMPTY` with
    # a `detail`, and its unconditional fallthrough for the plain
    # `HUMAN_RULING` (`approve_one_piece`) resolution, which no branch here
    # matches at all. A flag re-stating that here bound nothing a mutation
    # of `_findings_for` itself did not already trip (Task 7 fix round 1,
    # Q1): every cause it would have suppressed already yields empty
    # `findings`, and the previous-draft block below needs `findings` truthy
    # regardless of what `prior` is. `reopened_findings` is the one real
    # override, for the resolution that re-renders a reviewer's findings
    # from before the hand-back.
    prior = front.newest_submission(
        store, run_id, phase, ctx.epoch(),
        visit=(front.shaping_visit(store, run_id, ctx.epoch()) if phase == "slices" else None))
    findings = reopened_findings if reopened_findings is not None else _findings_for(ctx)
    if prior is not None and findings:
        parts.append("\n## Your previous draft\n\n%s\n" % store.read_blob(prior.payload["hash"]).decode())
    if findings:
        parts.append("\n## Findings to address\n\n%s\n" % findings.decode("utf-8", "replace"))
        # Spec section 3, Author round: a revision answers every finding.
        # Without this an author can only obey: every finding becomes an
        # addition, the document grows, and the next reviewer re-derives
        # everything against the larger surface (a spec went 19KB -> 92KB
        # in six rounds that way on 2026-09-08). With it, a wrong finding
        # can be refused on the record, and the reviewer is told not to
        # relitigate what was resolved.
        parts.append(
            "\n## Dispositions are required\n\n"
            "End the artefact with a section headed `## Dispositions` that answers\n"
            "EVERY finding above by its number, one line each, in one of two forms:\n\n"
            "    N. accepted — <what changed, in one sentence>\n"
            "    N. rejected — <why the finding is wrong or out of scope>\n\n"
            "Reject when a finding is wrong, adds scope the spec did not decide, or\n"
            "asks for something the issue rules out; say why. Do not accept a finding\n"
            "you cannot address in this revision. The reviewer reads this section\n"
            "first and will not re-raise a finding you resolved or refused with\n"
            "reasons, so the section is how the document stops growing.\n")
    return "".join(parts).encode()


def _copy(ctx, phase: str, rnd: int, data: bytes) -> None:
    d = pathlib.Path(ctx.bundle_dir) / ctx.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{phase}-r{rnd}.md").write_bytes(data)


def record_empty(ctx, session_id: str, detail: str = "") -> str:
    """`record_author_empty` for a turn that produced nothing: `empty_retry`,
    or `failed` when the kernel refuses it. The kernel refuses a second empty
    turn in a row (the seat's own create was itself caused by an
    author_empty): that is RC_FAILED, and the reason belongs in the log
    rather than only in the journal. Shared by the author round and the
    shaping round -- the same refusal, the same two outcomes.

    *detail* (revision 16): a malformed hand-back is an empty turn WITH a
    reason -- a plain empty turn says nothing wrong was tried, a malformed
    one says something was tried and misread, and the retry's brief (Task 7)
    needs to tell those apart to say what to fix rather than what to do."""
    payload = {"session": session_id}
    if detail:
        payload["detail"] = detail
    try:
        seat.command(ctx, "record_author_empty", payload)
    except NotAuthorized as exc:
        ctx.log(f"author_empty refused: {exc}")
        return "failed"
    return "empty_retry"


def refusal_outcome(ctx, phase: str, msg: str, markers: tuple[str, ...]) -> str:
    """What a submit refusal means for the round's return value (spec §7),
    shared by the author round's `submit_spec`/`submit_plan` and the shaping
    round's `submit_slices`: `direction` when a human_direction interrupted
    the turn; `stall`/`reauthor` when *markers* -- the refusal messages that
    mean "the next round's findings", not a failure -- match the message:
    `stall` when the round's own seat was already caused by a
    command_rejected (the loop parks rather than spending another seat on
    the same refusal), `reauthor` otherwise, so the finding reaches the next
    author round through `_findings_for`. Anything else is logged and
    `failed`.
    """
    if "human_direction" in msg:
        return "direction"
    if any(m in msg for m in markers):
        n = ctx.epoch()
        seat_row = front.newest_seat(ctx.store, ctx.run_id, Role.AUTHOR, phase, n)
        cause_kind = next((x.kind for x in ctx.store.facts_for(ctx.run_id) if x.id == seat_row["cause"]), None)
        return "stall" if cause_kind == EventKind.COMMAND_REJECTED else "reauthor"
    ctx.log(f"unexpected refusal: {msg}")
    return "failed"


def author_round(ctx) -> str:
    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    grill = policy_of(store, run_id).grill
    cause = phases.round_cause(ctx)
    answer = phases.answer_to_resume(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": n, "cause": cause.id}
    resume = None
    if answer is not None:
        prior_seat = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
        resume = prior_seat["session"]["id"] if prior_seat else None
    vendor = choose_author_vendor(ctx)
    # Revision 16: the spec round watches the hand-back beside the artefact
    # -- `bircher/reshape.md` ends the turn exactly as the artefact does.
    # `questions.md` is READ but not watched under grill=model, so the
    # skill's "write the questions and continue" does not end the turn
    # early (as today); under grill=human it is watched too, as today. The
    # spec §3 paragraph this implements is about the SPEC round only; the
    # plan phase's `read` is untouched -- still both files, unconditionally,
    # as before this revision (`read=None` here would fall back to `watched`,
    # which drops `questions.md` under grill=model and silently stops a plan
    # author's questions file from ever being read).
    if phase == "spec":
        watched = ([seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human"
                   else [seat.ARTIFACT_OUT]) + [seat.RESHAPE_OUT]
        read = [seat.RESHAPE_OUT, seat.QUESTIONS_OUT, seat.ARTIFACT_OUT]
    else:
        watched = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human" else [seat.ARTIFACT_OUT]
        read = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT]
    try:
        turn = seat.run_turn(ctx, role=Role.AUTHOR, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=(answer.id if answer is not None else cause.id),
                             prompt_text=author_brief(ctx, phase=phase, resume_answer=answer),
                             watched=watched, read=read,
                             resume_session=resume)
    except SeatsExhausted:
        return "budget"
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return "failed"
    if turn.ended == "displaced":
        return "direction"
    # The human first: a message in the session is taken before anything is submitted.
    taken = take_listing(ctx, turn.session_id, turn.listing)
    if taken == "direction":
        return "direction"
    hand_back = turn.files.get(seat.RESHAPE_OUT)
    reshape = slices.parse_reshape(hand_back) if hand_back else None
    if hand_back and reshape is None:
        # A hand-back with no reasoning, or the wrong ruling word, is not a
        # hand-back (spec §3): the empty turn, WHETHER OR NOT an artefact or
        # a questions file sits beside it -- the same unconditional bullet
        # `shape.py`'s ruling check reads. WITH a reason, unlike a plain
        # empty turn: `record_empty`'s `detail` says what to fix, and the
        # retry's brief renders it (Task 7). `_move_aside` retires every
        # file of this turn before the re-prompt.
        ctx.log(f"{seat.RESHAPE_OUT} does not parse; the empty turn: {hand_back[:200]!r}")
        return record_empty(ctx, turn.session_id,
                           detail=f"reshape.md did not parse: the first non-blank line must be "
                                  f"exactly `{slices.RESHAPE_LINE}` and a non-empty `Reasoning:` "
                                  f"block must follow")
    if reshape is not None:
        if turn.files.get(seat.ARTIFACT_OUT) is not None or turn.files.get(seat.QUESTIONS_OUT) is not None:
            # The hand-back is read first, unconditionally: the shape
            # decides before the questions are worth asking, and a spec
            # written against a shape its own author disputed is not
            # submitted. Neither file is read below; `_move_aside` retires
            # both before any re-prompt.
            ctx.log(f"{seat.ARTIFACT_OUT} or {seat.QUESTIONS_OUT} beside a hand-back is ignored")
        try:
            seat.command(ctx, "request_reshape", {"reasoning": reshape.reasoning})
        except NotAuthorized as exc:
            # A direction mid-turn, or a hand-back this epoch already holds
            # (authz: "request_reshape: ... already holds a hand-back"):
            # the same vocabulary `submit_spec`/`submit_slices` refusals use,
            # through the site both share.
            return refusal_outcome(ctx, phase, str(exc), ("request_reshape:",))
        return "reshaped"
    questions = turn.files.get(seat.QUESTIONS_OUT)
    artefact = turn.files.get(seat.ARTIFACT_OUT)
    if questions:
        asked = parse_questions(questions.decode("utf-8", "replace"))
        if not asked:
            # A questions file no `### Q<n>:` heading matches asks the run
            # NOTHING. Returning "questions" for it parks the loop on a grill
            # with no model_question to answer, and every pass after it spends
            # another seat on the same unreadable file; the turn produced no
            # artefact and no question, which is the empty turn.
            ctx.log(f"questions file matches no `### Q<n>:` block, ignored: {questions[:200]!r}")
        for q in asked:
            seat.command(ctx, "record_model_question", {"question_id": q["id"], "question": q["question"]})
            if q["ruling"]:
                # The skill asks for `<decision> — <reasoning> — <cost>`. A
                # ruling written without the separators lands whole in
                # `ruling` with `-` for the other two -- recorded, not
                # refused, because a ruling is a model's word and the shape
                # is advice.
                bits = [b.strip() for b in q["ruling"].split("—")] + ["", ""]
                seat.command(ctx, "record_model_ruling", {"question_id": q["id"], "ruling": bits[0] or q["ruling"],
                                                          "reasoning": bits[1] or "-", "cost_if_wrong": bits[2] or "-"})
        if asked and artefact is None:
            return "questions"
    if artefact is None:
        return record_empty(ctx, turn.session_id)
    h = put_artifact(store, artefact)
    rnd = len(front.submissions(store, run_id, phase, n)) + 1
    _copy(ctx, phase, rnd, artefact)
    name = "submit_spec" if phase == "spec" else "submit_plan"
    try:
        seat.command(ctx, name, {"artifact_hash": h})
    except NotAuthorized as exc:
        # "the grill is open" -- under grill=human the kernel refuses a spec
        # submitted before the human has answered (spec section 1: the author
        # MUST ask at least once). An author that wrote the artefact instead
        # of questions is not a failed run: the refusal becomes the next
        # round's findings through `_findings_for`, which reads a
        # `command_rejected` cause, and the author asks. Found live on
        # 2026-09-08, when it took the unexpected-refusal path and discarded
        # a run that had done nothing wrong except be told "may" where the
        # kernel meant "must".
        return refusal_outcome(ctx, phase, str(exc), ("identical", "### Task", "current spec", "the grill is open"))
    return "submitted"
