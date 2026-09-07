"""One waited turn of a seat (spec §3 Author round, Review round, *The turn's
end is a fact*). Shared by the author, the reviewer and the human pass."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from coordinator import sessions
from coordinator.session import AgentMismatch, LookupFailed, list_items, wait_turn
from kernel import front
# Re-exported, not re-declared: the reviewer writes the path the kernel's
# rendered brief tells it to, and two literals would let the two drift.
from kernel.brief import REVIEW_OUT  # noqa: F401
from kernel.canon import content_hash
from kernel.commands import Command, submit
from kernel.dispatch import dispatch

ARTIFACT_OUT = "bircher/artifact.md"
QUESTIONS_OUT = "bircher/questions.md"


@dataclass
class Turn:
    session_id: str
    generation: int
    workspace: str
    agent_name: str
    ended: str
    files: dict = field(default_factory=dict)
    cursor_before: str | None = None
    listing: list = field(default_factory=list)


_n = 0


def command(ctx, name: str, payload: dict):
    """One kernel command under this pass's generation.

    The generation is IN the key and every pass dispatches a fresh one, so the
    process-local counter cannot collide across passes: a restarted
    coordinator counts from 1 again, but under a generation no earlier pass
    held. Within a pass the counter is what keeps two commands of the same
    name -- two `record_model_question`s, say -- from replaying as one.
    """
    global _n
    _n += 1
    return submit(ctx.store, Command(
        name=name, run_id=ctx.run_id, expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=f"{name}:{ctx.run_id}:{ctx.generation}:{_n}", generation=ctx.generation,
        payload=payload))


def read_once(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _move_aside(workspace: str, names: list[str]) -> None:
    """Retire the previous turn's outputs, so a file read after this turn is
    this turn's. Called only where the prompt is OWED: a re-prompt of an
    adopted session moves the earlier file aside before the new turn can
    write, while a prompt already satisfied (the crash window) moves nothing,
    since the turn it belongs to may already have written its output.
    """
    for name in names:
        p = os.path.join(workspace, name)
        if os.path.exists(p):
            n = 1
            while os.path.exists(f"{p}.{n}.prev"):
                n += 1
            os.replace(p, f"{p}.{n}.prev")


def _recorded_session(ctx, role: str, ob: dict, session_id: str) -> dict | None:
    """The snapshot the JOURNAL holds for *session_id*, or None.

    The round's own seat first, then any satisfied sess-create of the run that
    delivered this session -- a session resumed across a phase or a role is
    still one this run created, and only a session it never created is absent
    from here.
    """
    seat_row = front.newest_seat(ctx.store, ctx.run_id, role, ob["phase"], ob["epoch"])
    if seat_row is not None and seat_row["session"].get("id") == session_id:
        return seat_row["session"]
    for row in front.satisfied_effects(ctx.store, ctx.run_id, "sess-create"):
        snap = json.loads(row["external_object_id"])
        if snap.get("id") == session_id:
            return snap
    return None


def _resume(ctx, role: str, ob: dict, session_id: str) -> dict:
    """Adopt a session this run created earlier, on the JOURNAL's snapshot.

    spec §11: the guard `wait_turn` performs is that the session still reports
    the agent_id its create recorded. Taking that expectation from a fresh
    fetch would compare the server's answer against itself, so a redeployed or
    hostile server could hand back any agent it liked and the check would pass
    -- and the workspace read after the turn would be the server's choice too.
    Both come from the journal; the fetch is only the thing being checked.
    """
    recorded = _recorded_session(ctx, role, ob, session_id)
    if recorded is None:
        # Not a server problem: the coordinator asked to resume a session this
        # run never created, so there is no recorded identity to check against
        # and nothing to safely adopt.
        raise AgentMismatch(session_id, "(no satisfied sess-create in this run's journal)", "(unread)")
    live = json.loads(ctx.fetch(f"{ctx.server}/v1/sessions/{session_id}"))
    observed = str(live.get("agent_id") or "")
    if observed != recorded.get("agent_id"):
        raise AgentMismatch(session_id, recorded.get("agent_id"), observed)
    return recorded


def _adopt_or_create(ctx, role: str, vendor: str, ob: dict, resume_session: str | None) -> dict:
    row = sessions.satisfied(ctx.store, ctx.run_id, ob)
    if row is not None:
        return json.loads(row["external_object_id"])
    if resume_session is not None:
        return _resume(ctx, role, ob, resume_session)
    path = sessions.worktree_path(ctx.workspaces_root, ctx.run_id, ctx.generation)
    workspace = sessions.add_worktree(ctx.repo_dir, ctx.store.run_base_sha(ctx.run_id), path)
    return sessions.create_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                   agent_id=ctx.agent_ids[vendor], host_id=ctx.host_id, workspace=workspace,
                                   phase=ob["phase"], epoch=ob["epoch"], cause=ob["cause"], env=ctx.effect_env())


def _record_prompt_item(ctx, session_id: str, prompt_hash: str) -> list[dict]:
    items = list_items(ctx.server, session_id, fetch=ctx.fetch)
    # Per session, as the kernel's own duplicate rule is: omnigent's item ids
    # are not shown to be unique across sessions, so a bare id set could read
    # another session's item as this one's and skip the record.
    known = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, "prompt_item")
             if p.payload["session_id"] == session_id}
    for it in items:
        if it["role"] == "user" and it["id"] not in known and content_hash(it["text"].encode()) == prompt_hash:
            command(ctx, "record_prompt_item", {"session_id": session_id, "item_id": it["id"], "sha256": prompt_hash})
            break
    return items


def run_turn(ctx, *, role: str, vendor: str, session_obligation: dict, prompt_cause: str,
             prompt_text: bytes, watched: list[str], read: list[str] | None = None,
             resume_session: str | None = None) -> Turn:
    """*watched* are the paths the poll ends the turn on; *read* (default the
    watched ones) are the paths read once after the stop -- under grill=model
    the questions file is read beside the artefact but never watched.

    The UNION of the two is moved aside before a re-prompt: a file this turn
    watches or reads has to be this turn's, and neither set contains the
    other. Retiring only the watched set left a stale `questions.md` in an
    adopted session under grill=model, where it is read but never watched,
    and the round recorded its questions and rulings a second time -- the
    kernel has no duplicate guard on either command. Retiring only the read
    set is the same defect the other way round: a stale watched file ends the
    turn `file` on the first poll, and the round spends its one empty-turn
    retry on a turn that never ran.
    """
    read = list(watched) if read is None else list(read)
    ctx.generation = dispatch(ctx.store, ctx.run_id, actor=vendor, role=role).generation
    snap = _adopt_or_create(ctx, role, vendor, session_obligation, resume_session)
    sid, workspace = snap["id"], snap["workspace"]
    phase, epoch = session_obligation["phase"], session_obligation["epoch"]
    prompt_ob = {"kind": "sess-prompt", "session": sid, "phase": phase, "epoch": epoch, "cause": prompt_cause}
    prompt_hash = content_hash(prompt_text)
    cursor_before = None
    if sessions.satisfied(ctx.store, ctx.run_id, prompt_ob) is None:
        _move_aside(workspace, list(dict.fromkeys([*watched, *read])))
        sessions.prompt_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                session_id=sid, phase=phase, epoch=epoch, cause=prompt_cause,
                                text=prompt_text, env=ctx.effect_env())
    listing = _record_prompt_item(ctx, sid, prompt_hash)
    prompt = front.newest_prompt(ctx.store, ctx.run_id, phase, epoch)
    deadline_us = prompt["at_us"] + ctx.turn_timeout_s * 1_000_000
    paths = [os.path.join(workspace, w) for w in watched]
    ended = front.turn_ended_for(ctx.store, ctx.run_id, prompt["key"])
    if ended is None:
        end = wait_turn(ctx.store, ctx.run_id, ctx.generation, ctx.server, sid, paths, deadline_us,
                        agent_id_expected=snap["agent_id"], fetch=ctx.fetch, clock=ctx.clock, sleep=ctx.sleep)
        command(ctx, "record_turn_ended", {"session": sid, "ended": end.ended})
        ended = front.turn_ended_for(ctx.store, ctx.run_id, prompt["key"])
    if not front.stop_satisfied_for(ctx.store, ctx.run_id, ended.id):
        sessions.stop_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                              session_id=sid, cause=ended.id, env=ctx.effect_env())
    files = {} if ended.payload["ended"] == "displaced" else {
        w: read_once(os.path.join(workspace, w)) for w in read}
    # The listing at the end of the turn (spec §4: every listing is
    # discriminated) -- a message typed during the turn is in this one.
    #
    # ONLY LookupFailed is swallowed, and it is logged: an unreadable server
    # leaves the caller discriminating the mid-turn listing, which cannot see
    # a message typed during the turn, so the fallback has to be visible
    # rather than silent. Anything else -- a bug in list_items, a store error
    # -- propagates: `except Exception` here would turn every one of those
    # into the same quiet stale listing.
    try:
        listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    except LookupFailed as exc:
        ctx.log(f"session {sid}: end-of-turn listing unreadable ({exc}); "
                "discriminating the listing taken before the turn")
    return Turn(session_id=sid, generation=ctx.generation, workspace=workspace, agent_name=snap["agent_name"],
                ended=ended.payload["ended"], files=files, cursor_before=cursor_before, listing=listing)
