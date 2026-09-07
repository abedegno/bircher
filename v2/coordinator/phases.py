"""The phase loop's context and journal-derived obligations (spec §3)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from coordinator import sessions
from coordinator.effects import perform_effect
from kernel import front
from kernel.authz import phase_of
from kernel.effect_class import EffectClass
from kernel.events import EventKind

PUBLISH_CAP = 60_000
APPROVED_AT = {"spec": ("specified", "plan_submitted", "plan_accepted", "planned"),
               "plan": ("planned",)}
_BEYOND_FRONT = ("implementing", "reviewing", "merge_requested", "merged", "ended")


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
