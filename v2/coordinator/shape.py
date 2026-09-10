"""The shaping round (shaping spec §3 *The shaping round*): the author
round's twin, with two watched paths and two ways to end."""
from __future__ import annotations

from coordinator import author, phases, seat
from coordinator.human import take_listing
from coordinator.session import AgentMismatch
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role, SeatsExhausted


def shape_round(ctx) -> str:
    """One shaping turn: `one_piece` (the ruling recorded, the run at
    `queued`), `submitted` (a slice plan at `slices_submitted`),
    `empty_retry`, `direction`, `budget`, `stall`, `reauthor` or `failed` --
    the author round's vocabulary, so the loop parks the same way."""
    store, run_id = ctx.store, ctx.run_id
    n = ctx.epoch()
    cause = phases.round_cause(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": "slices", "epoch": n, "cause": cause.id}
    vendor = author.choose_author_vendor(ctx)
    try:
        turn = seat.run_turn(ctx, role=Role.AUTHOR, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=cause.id, prompt_text=author.author_brief(ctx, phase="slices"),
                             watched=[seat.SHAPE_OUT, seat.ARTIFACT_OUT])
    except SeatsExhausted:
        return "budget"
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return "failed"
    if turn.ended == "displaced":
        return "direction"
    # The human first: a message in the session is taken before anything is recorded.
    if take_listing(ctx, turn.session_id, turn.listing) == "direction":
        return "direction"
    shape_file = turn.files.get(seat.SHAPE_OUT)
    artefact = turn.files.get(seat.ARTIFACT_OUT)
    ruling = slices.parse_ruling(shape_file) if shape_file else None
    if shape_file and ruling is None:
        # A ruling with no reasoning is not a ruling (spec §3): the empty turn,
        # WHETHER OR NOT an artefact sits beside it. The bullet is
        # unconditional over the artefact's presence, and falling through to
        # the submit took the branch the author did not choose -- their ruling
        # was malformed, not withdrawn -- spending a review round on a plan
        # written by a turn that had decided not to slice (final review,
        # finding 4). `_move_aside` retires both files before the re-prompt.
        ctx.log(f"{seat.SHAPE_OUT} does not parse as a ruling; the empty turn: {shape_file[:200]!r}")
        return author.record_empty(ctx, turn.session_id)
    if ruling is not None:
        if artefact is not None:
            # The ruling is read first; the artefact beside it is ignored, and
            # `_move_aside` retires it before any re-prompt (spec §3).
            ctx.log(f"{seat.ARTIFACT_OUT} beside a ruling is ignored")
        try:
            seat.command(ctx, "record_one_piece", {"reasoning": ruling.reasoning, "cost_if_wrong": ruling.cost_if_wrong})
        except NotAuthorized as exc:
            if "human_direction" in str(exc):
                return "direction"
            ctx.log(f"unexpected refusal: {exc}")
            return "failed"
        return "one_piece"
    if artefact is None:
        return author.record_empty(ctx, turn.session_id)
    h = put_artifact(store, artefact)
    rnd = len(front.submissions(store, run_id, "slices", n)) + 1
    author._copy(ctx, "slices", rnd, artefact)
    try:
        seat.command(ctx, "submit_slices", {"artifact_hash": h})
    except NotAuthorized as exc:
        # A grammar refusal, the depth refusal or an identical resubmission
        # is the next round's findings (spec §7), as the grill refusal is in
        # the spec phase: `_findings_for` reads the command_rejected cause.
        return author.refusal_outcome(ctx, "slices", str(exc), ("submit_slices:", "identical", "a slice is not sliced"))
    return "submitted"
