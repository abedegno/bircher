"""The author round (spec §3 Author round, §3 loop, §7)."""
from __future__ import annotations

import json
import pathlib
import re

from coordinator import phases, seat
from coordinator.human import take_listing            # Task 19; a stub until then
from coordinator.session import AgentMismatch
from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role, SeatsExhausted
from kernel.events import EventKind
from kernel.policy import policy_of, to_payload

SKILLS = pathlib.Path(__file__).resolve().parents[2] / "skills"
_Q = re.compile(r"^### (Q[^:\s]+):\s*(.+?)\s*$", re.M)


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
    if same_phase:
        return same_phase[-1].payload["reviewer_identity"]
    if phase == "plan":
        spec_accepts = [v for v in verdicts if v.payload["phase"] == "spec" and v.payload["verdict"] == "accept"]
        if spec_accepts:
            vendors = sorted(ctx.agent_ids)
            other = [x for x in vendors if x != spec_accepts[-1].payload["reviewer_identity"]]
            return other[0] if other else vendors[0]
    return ctx.default_author


def _findings_for(ctx) -> bytes:
    cause = phases.round_cause(ctx)
    store = ctx.store
    if cause.kind == EventKind.REVIEW_VERDICT and cause.payload.get("findings_hash"):
        return store.read_blob(cause.payload["findings_hash"])
    if cause.kind == EventKind.HUMAN_DIRECTION:
        return cause.payload["text"].encode()
    if cause.kind == EventKind.BUNDLE_REVISED:
        return json.dumps(cause.payload["diff"], indent=1).encode()
    if cause.kind == EventKind.COMMAND_REJECTED:
        return cause.payload.get("detail", "").encode()
    return b""


def author_brief(ctx, *, phase: str, resume_answer=None) -> bytes:
    store, run_id = ctx.store, ctx.run_id
    if resume_answer is not None:
        return ("Answered; continue.\n\n" + resume_answer.payload["answer"]).encode()
    parts = [f"# Bircher {phase} author brief\n"]
    parts.append((SKILLS / f"{phase}-author" / "SKILL.md").read_text())
    parts.append("\n## Files\n\nArtefact: `%s`\nQuestions: `%s`\n" % (seat.ARTIFACT_OUT, seat.QUESTIONS_OUT))
    parts.append("\n## Policy\n\n```json\n%s\n```\n" % json.dumps(to_payload(policy_of(store, run_id)), indent=1))
    parts.append("\n## Issue snapshot\n\n```json\n%s\n```\n" % store.read_blob(front.bundle_hash(store, run_id)).decode())
    if phase == "plan":
        parts.append("\n## The accepted spec\n\n%s\n" % store.read_blob(store.phase_artifact(run_id, "spec")).decode())
    prior = front.newest_submission(store, run_id, phase, ctx.epoch())
    findings = _findings_for(ctx)
    if prior is not None and findings:
        parts.append("\n## Your previous draft\n\n%s\n" % store.read_blob(prior.payload["hash"]).decode())
    if findings:
        parts.append("\n## Findings to address\n\n%s\n" % findings.decode("utf-8", "replace"))
    return "".join(parts).encode()


def _copy(ctx, phase: str, rnd: int, data: bytes) -> None:
    d = pathlib.Path(ctx.bundle_dir) / ctx.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{phase}-r{rnd}.md").write_bytes(data)


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
    watched = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human" else [seat.ARTIFACT_OUT]
    try:
        turn = seat.run_turn(ctx, role=Role.AUTHOR, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=(answer.id if answer is not None else cause.id),
                             prompt_text=author_brief(ctx, phase=phase, resume_answer=answer),
                             watched=watched, read=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT],
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
        try:
            seat.command(ctx, "record_author_empty", {"session": turn.session_id})
        except NotAuthorized as exc:
            # The kernel refuses a second empty turn in a row (the seat's own
            # create was caused by an author_empty): that is RC_FAILED, and
            # the reason belongs in the log rather than only in the journal.
            ctx.log(f"author_empty refused: {exc}")
            return "failed"
        return "empty_retry"
    h = put_artifact(store, artefact)
    rnd = len(front.submissions(store, run_id, phase, n)) + 1
    _copy(ctx, phase, rnd, artefact)
    name = "submit_spec" if phase == "spec" else "submit_plan"
    try:
        seat.command(ctx, name, {"artifact_hash": h})
    except NotAuthorized as exc:
        msg = str(exc)
        if "human_direction" in msg:
            return "direction"
        if "identical" in msg or "### Task" in msg or "current spec" in msg:
            seat_row = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
            cause_kind = next((x.kind for x in store.facts_for(run_id) if x.id == seat_row["cause"]), None)
            return "stall" if cause_kind == EventKind.COMMAND_REJECTED else "reauthor"
        ctx.log(f"unexpected refusal: {msg}")
        return "failed"
    return "submitted"
