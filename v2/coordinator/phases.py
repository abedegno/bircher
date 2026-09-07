"""The phase loop's context and journal-derived obligations (spec §3)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from coordinator import sessions
from coordinator.effects import perform_effect
from kernel import front
from kernel.authz import FRONT_HALF_STATES, phase_of
from kernel.effect_class import EffectClass
from kernel.events import EventKind

PUBLISH_CAP = 60_000
APPROVED_AT = {"spec": ("specified", "plan_submitted", "plan_accepted", "planned"),
               "plan": ("planned",)}
_BEYOND_FRONT = ("implementing", "reviewing", "merge_requested", "merged", "ended")
#: The states `run_loop` works in. `planned` is one of them because the pass
#: that REACHES it still owes the plan's publication (§3 Artefacts); every
#: state past it belongs to the back half, and the loop exits 0 without
#: retiring, publishing or dispatching anything (§3: "A run already at or
#: beyond `planned` exits 0 at once").
_LOOP_STATES = FRONT_HALF_STATES | frozenset({"planned"})


@dataclass
class Ctx:
    store: object
    run_id: str
    server: str
    repo: str
    issue_number: int
    repo_dir: str
    workspaces_root: str
    bundle_dir: str
    agent_ids: dict
    host_id: str
    turn_timeout_s: int
    default_author: str
    env: dict
    fetch: object
    clock: object = time.time
    sleep: object = time.sleep
    log: object = print
    generation: int | None = None

    def state(self) -> str:
        return self.store.run_state(self.run_id)

    def phase(self) -> str:
        return phase_of(self.state())

    def epoch(self) -> int:
        return front.epoch(self.store, self.run_id)

    def effect_env(self) -> dict:
        return dict(self.env, BIRCHER_GENERATION=str(self.generation))


ROUND_CAUSE_KINDS = frozenset({
    EventKind.RUN_STARTED, EventKind.BUNDLE_REVISED, EventKind.REVIEW_VERDICT,
    EventKind.HUMAN_RULING, EventKind.HUMAN_DIRECTION, EventKind.AUTHOR_EMPTY,
    EventKind.PARKED, EventKind.COMMAND_REJECTED,
})


def _is_round_cause(f) -> bool:
    if f.kind == EventKind.COMMAND_REJECTED:
        return f.payload.get("command_name") in ("submit_spec", "submit_plan") and f.actor != "human"
    return f.kind in ROUND_CAUSE_KINDS


def round_cause(ctx: Ctx):
    """spec §3 *Obligations*: the newest fact of the run that called for an
    author round -- the enumeration in the task brief, read directly off the
    journal (never a caller's payload)."""
    facts = [f for f in ctx.store.facts_for(ctx.run_id) if _is_round_cause(f)]
    return facts[-1]


def seat_cause(ctx: Ctx):
    """The reviewer seat's cause: the newest `human_ruling {grant_round}`
    newer than the phase's newest `artifact_submitted` of the epoch, else
    that submission itself."""
    sub = front.newest_submission(ctx.store, ctx.run_id, ctx.phase(), ctx.epoch())
    grants = [f for f in ctx.store.facts_of_kind(ctx.run_id, EventKind.HUMAN_RULING)
              if f.payload.get("ruling") == "grant_round" and sub is not None and f.seq > sub.seq]
    return grants[-1] if grants else sub


def answer_to_resume(ctx: Ctx):
    """The epoch's newest `human_answer`, when it is newer than the newest
    `model_question` of the epoch and than the round cause -- the same
    session continues rather than a fresh round starting."""
    n = ctx.epoch()
    answers = front.epoch_facts(ctx.store, ctx.run_id, EventKind.HUMAN_ANSWER, n)
    if not answers:
        return None
    a = answers[-1]
    questions = front.epoch_facts(ctx.store, ctx.run_id, EventKind.MODEL_QUESTION, n)
    if questions and questions[-1].seq > a.seq:
        return None
    return a if a.seq > round_cause(ctx).seq else None


def derived_session_obligation(ctx: Ctx) -> dict | None:
    """What this pass would create, if anything: the round's session at
    `queued`/`specified`, the seat's at `*_submitted`, the parked session's
    at `*_accepted` when its park has no session yet -- else None."""
    state, phase, n = ctx.state(), ctx.phase(), ctx.epoch()
    if state in ("queued", "specified"):
        cause = round_cause(ctx).id
    elif state in ("spec_submitted", "plan_submitted"):
        cause = seat_cause(ctx).id
    elif state in ("spec_accepted", "plan_accepted"):
        park = front.current_park(ctx.store, ctx.run_id)
        if park is None or park.payload.get("session_id"):
            return None
        cause = park.id
    else:
        return None
    return {"kind": "sess-create", "run": ctx.run_id, "phase": phase, "epoch": n, "cause": cause}


def _displacing_cause(ctx: Ctx) -> str:
    """What displaced an orphan session that this pass does not derive: the
    cause of the round this pass WOULD start, or -- when there is none to
    derive -- the newest ruling, transition or verdict that put the run where
    it is."""
    ob = derived_session_obligation(ctx)
    if ob is not None:
        return ob["cause"]
    # No round to derive: the newest fact that put the run where it is.
    for f in reversed(ctx.store.facts_for(ctx.run_id)):
        if f.kind in (EventKind.HUMAN_RULING, EventKind.TRANSITION, EventKind.REVIEW_VERDICT):
            return f.id
    return ctx.store.facts_for(ctx.run_id)[0].id


def _stop(ctx: Ctx, session_id: str, cause: str) -> str:
    key = f"sess-stop:{session_id}:{ctx.generation}"
    sessions.stop_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                          session_id=session_id, cause=cause, env=ctx.effect_env())
    return key


def retire_owed(ctx: Ctx) -> list[str]:
    """spec §3 *retire_owed is the loop's first step*: every stop the journal
    owes, from the journal alone -- the turn rule (a turn_ended with no
    satisfied stop) and the orphan rule (a satisfied sess-create this pass
    neither prompted, stopped, nor is itself about to adopt). A stop that
    fails raises and halts."""
    done = []
    store, run_id = ctx.store, ctx.run_id
    # The turn rule.
    for te in store.facts_of_kind(run_id, EventKind.TURN_ENDED):
        if not front.stop_satisfied_for(store, run_id, te.id):
            done.append(_stop(ctx, te.payload["session"], te.id))
    # The orphan rule.
    prompted = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-prompt")}
    stopped = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-stop")}
    derived = derived_session_obligation(ctx)
    for row in front.satisfied_effects(store, run_id, "sess-create"):
        ob = row["intent"]["obligation"]
        sid = json.loads(row["external_object_id"])["id"]
        if sid in prompted or sid in stopped or ob == derived:
            continue
        done.append(_stop(ctx, sid, _displacing_cause(ctx)))
    return done


def publication_body(phase: str, artefact: bytes, artifact_hash: str) -> str:
    """Ruling 10: the header line, a blank line, then the artefact text --
    truncated to PUBLISH_CAP with a pointer to the full bytes when it was
    cut. GitHub caps a comment at 65536 characters and the COMMENT contract
    carries the body inline in --body, so the value must stay under both
    that and Linux's 128 KiB argument bound; the store and the bundle
    directory hold the whole artefact regardless."""
    text = artefact.decode("utf-8", "replace")
    head = f"bircher: published {phase} {artifact_hash[:8]}\n\n"
    if len(text) > PUBLISH_CAP:
        text = text[:PUBLISH_CAP] + f"\n... truncated; full bytes are in the kernel store under {artifact_hash}"
    return head + text


def publish_owed(ctx: Ctx) -> list[str]:
    """spec §3 *Artefacts*: the approved artefact posted to the issue once,
    by obligation -- one publish per (run, phase, hash)."""
    done = []
    state = ctx.state()
    for phase, states in APPROVED_AT.items():
        if state not in states and state not in _BEYOND_FRONT:
            continue
        h = ctx.store.phase_artifact(ctx.run_id, phase)
        if h is None:
            continue
        ob = {"kind": "publish", "run": ctx.run_id, "phase": phase, "hash": h}
        if sessions.satisfied(ctx.store, ctx.run_id, ob) is not None:
            continue
        key = f"publish:{ctx.run_id}:{phase}:{h}:{ctx.generation}"
        body = publication_body(phase, ctx.store.read_blob(h), h)
        perform_effect(EffectClass.COMMENT, key, ["gh", "issue", "comment", str(ctx.issue_number), "--repo", ctx.repo,
                                                  "--body", body], timeout=60, env=ctx.effect_env(), obligation=ob)
        done.append(key)
    return done


class Exit:
    """The loop's exit codes (spec §3): `planned` is 0, a park is 4, and
    anything the loop cannot act on itself is 1. USAGE is the CLI's, kept
    here so one table names all four."""
    OK, FAILED, USAGE, PARKED = 0, 1, 2, 4


def stall(ctx: Ctx, reason: str, *, session_id, findings_hash, verdict, reviewer) -> str:
    """The loop's one way of needing a human (spec §3 loop, §4).

    The session is LISTED first and discriminated: a message already in it is
    the answer to the stall the pass was about to declare, and taking it means
    no park at all. Otherwise the park is recorded BEFORE the prompt is sent,
    because the prompt's obligation is caused by the `parked` fact -- a prompt
    sent first would have no cause to carry.
    """
    from coordinator import human, seat
    from coordinator.session import LookupFailed, list_items
    store, run_id = ctx.store, ctx.run_id
    # "The author session" is the run's most recent author session, whatever
    # phase made it (spec §4).
    if session_id is None:
        session_id = _newest_author_session(ctx)
    listing = []
    if session_id is not None:
        try:
            listing = list_items(ctx.server, session_id, fetch=ctx.fetch)
        except LookupFailed as exc:
            # The carrier cannot be read, so it cannot carry the prompt
            # either: park with no session rather than park against one the
            # human will never see. Logged, because a park nobody is told
            # about is the failure mode this whole path exists to avoid.
            ctx.log(f"session {session_id}: unreadable at {reason} ({exc}); parking with no carrier")
            session_id, listing = None, []
        if listing and human.take_listing(ctx, session_id, listing) not in (None, "dismissed"):
            return "taken"
    cur = listing[-1]["id"] if listing else None
    if cur is None and session_id is None:
        cur = _last_cursor(ctx)
    seat.command(ctx, "park", {"reason": reason, "session_id": session_id, "cursor_item_id": cur,
                               "findings_hash": findings_hash, "verdict": verdict, "reviewer": reviewer})
    park = front.current_park(store, run_id)
    if session_id is None:
        return "parked"
    return human.human_pass(ctx, park)


def _last_cursor(ctx: Ctx):
    """The run's newest recorded cursor, for a park with no session to list:
    the park must not claim to have read past anything this pass could not
    see."""
    for f in reversed(ctx.store.facts_for(ctx.run_id)):
        if f.payload.get("cursor_item_id"):
            return f.payload["cursor_item_id"]
    return None


def run_loop(ctx: Ctx) -> int:
    """The §3 loop. Every iteration re-reads the state and counts from the
    journal; nothing survives in memory across iterations, and nothing
    survives a crash that the journal does not already say.

    The operator fence per iteration is the coordinator's own
    (`actor="coordinator"`): the runner's resume fence (§5) is taken by
    `run_item` before `phases` is called, and every seat dispatch inside
    supersedes it; `retire_owed` and `publish_owed` perform their effects
    under the coordinator's generation.
    """
    from coordinator import author, human, review
    from coordinator.session import AgentMismatch
    from coordinator.sessions import WorktreeExists
    from kernel.dispatch import PendingEffects, Role, dispatch
    from kernel.effects import UncertainEffect, is_halted, pending_reconciliation
    store, run_id = ctx.store, ctx.run_id
    if is_halted(store, run_id) or pending_reconciliation(store, run_id):
        ctx.log(f"run {run_id} halted or holds pending effects: {pending_reconciliation(store, run_id)}")
        return Exit.FAILED
    try:
        while True:
            # Before retiring, publishing or dispatching anything: a run
            # the back half owns is not this loop's to touch, and stopping
            # its sessions or re-publishing its artefacts would be acting on
            # someone else's run.
            if ctx.state() not in _LOOP_STATES:
                return Exit.OK
            ctx.generation = dispatch(store, run_id, actor="coordinator", role=Role.OPERATOR).generation
            retire_owed(ctx)
            publish_owed(ctx)
            state = ctx.state()
            if state == "planned":
                return Exit.OK
            # The park comes first: the state alone is ambiguous, and a run
            # at `spec_submitted` with a current park needs the human, not
            # another review.
            park = front.current_park(store, run_id)
            if park is not None:
                if human.human_pass(ctx, park) == "parked":
                    return Exit.PARKED
                continue
            if state in ("queued", "specified"):
                out = author.author_round(ctx)
                if out == "questions":
                    if stall(ctx, "grill", session_id=_newest_author_session(ctx), findings_hash=None,
                             verdict=None, reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "budget":
                    if stall(ctx, "budget_exhausted", session_id=None, findings_hash=None, verdict=None,
                             reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "stall":
                    if stall(ctx, "identical_resubmission", session_id=_newest_author_session(ctx),
                             findings_hash=None, verdict=None, reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "failed":
                    return Exit.FAILED
                continue
            if state in ("spec_submitted", "plan_submitted"):
                out = review.review_round(ctx)
                if out.status == "recorded":
                    continue
                if out.status in ("failed", "no_brief"):
                    return Exit.FAILED
                reason = {"no_verdict": "no_verdict", "bound_exhausted": "bound_exhausted",
                          "budget": "budget_exhausted"}[out.status]
                if stall(ctx, reason, session_id=_newest_author_session(ctx), findings_hash=out.findings_hash,
                         verdict=(None if out.verdict is None else
                                  ("accept" if out.verdict == "PASS" else "request_revision")),
                         reviewer=out.reviewer or None) == "parked":
                    return Exit.PARKED
                continue
            if state in ("spec_accepted", "plan_accepted"):
                if stall(ctx, "gate", session_id=_newest_author_session(ctx), findings_hash=None, verdict=None,
                         reviewer=None) == "parked":
                    return Exit.PARKED
                continue
            # A front-half state no branch above claims: exit rather than
            # spin. Unreachable today; the loop is total anyway.
            return Exit.OK
    except (PendingEffects, UncertainEffect) as exc:
        ctx.log(f"halted: {exc}")
        return Exit.FAILED
    except (WorktreeExists, AgentMismatch) as exc:
        ctx.log(f"failed: {exc}")
        return Exit.FAILED


def _newest_author_session(ctx: Ctx) -> str | None:
    """The run's most recent author session, whatever phase or epoch made it
    (spec §4): the carrier a park with no session of its own prompts in."""
    from kernel.dispatch import Role
    roles = {d["generation"]: d["role"] for d in ctx.store.dispatches_for(ctx.run_id)}
    found = None
    for row in front.satisfied_effects(ctx.store, ctx.run_id, "sess-create"):
        if roles.get(row["generation"]) == Role.AUTHOR:
            found = json.loads(row["external_object_id"])["id"]
    return found


def retire(ctx: Ctx) -> list[str]:
    """spec §5 *Cancellation retires the run's sessions*."""
    from kernel.effects import is_halted
    store, run_id = ctx.store, ctx.run_id
    cancelled = [t for t in store.facts_of_kind(run_id, EventKind.TRANSITION) if t.payload.get("to") == "cancelled"]
    cause = cancelled[-1].id if cancelled else _displacing_cause(ctx)
    stopped = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-stop")}
    owed = []
    for row in front.satisfied_effects(store, run_id, "sess-create"):
        sid = json.loads(row["external_object_id"])["id"]
        if sid not in stopped:
            owed.append(sid)
    if is_halted(store, run_id) and owed:
        raise RuntimeError(f"run {run_id} is halted; these sessions could not be stopped: {owed}")
    return [_stop(ctx, sid, cause) for sid in owed]
