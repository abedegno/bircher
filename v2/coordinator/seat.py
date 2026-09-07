"""One waited turn of a seat (spec §3 Author round, Review round, *The turn's
end is a fact*). Shared by the author, the reviewer and the human pass."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from coordinator import sessions
from coordinator.session import list_items, wait_turn
from kernel import front
from kernel.canon import content_hash
from kernel.commands import Command, submit
from kernel.dispatch import dispatch

ARTIFACT_OUT = "bircher/artifact.md"
QUESTIONS_OUT = "bircher/questions.md"
REVIEW_OUT = "bircher/review.md"


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


def _adopt_or_create(ctx, vendor: str, ob: dict, resume_session: str | None) -> dict:
    row = sessions.satisfied(ctx.store, ctx.run_id, ob)
    if row is not None:
        return json.loads(row["external_object_id"])
    if resume_session is not None:
        snap = json.loads(ctx.fetch(f"{ctx.server}/v1/sessions/{resume_session}"))
        return {k: snap.get(k) for k in ("id", "agent_id", "agent_name", "host_id", "workspace", "title")}
    path = sessions.worktree_path(ctx.workspaces_root, ctx.run_id, ctx.generation)
    workspace = sessions.add_worktree(ctx.repo_dir, ctx.store.run_base_sha(ctx.run_id), path)
    return sessions.create_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                   agent_id=ctx.agent_ids[vendor], host_id=ctx.host_id, workspace=workspace,
                                   phase=ob["phase"], epoch=ob["epoch"], cause=ob["cause"], env=ctx.effect_env())


def _record_prompt_item(ctx, session_id: str, prompt_hash: str) -> list[dict]:
    items = list_items(ctx.server, session_id, fetch=ctx.fetch)
    known = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, "prompt_item")}
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
    the questions file is read beside the artefact but never watched."""
    read = list(watched) if read is None else list(read)
    ctx.generation = dispatch(ctx.store, ctx.run_id, actor=vendor, role=role).generation
    snap = _adopt_or_create(ctx, vendor, session_obligation, resume_session)
    sid, workspace = snap["id"], snap["workspace"]
    phase, epoch = session_obligation["phase"], session_obligation["epoch"]
    prompt_ob = {"kind": "sess-prompt", "session": sid, "phase": phase, "epoch": epoch, "cause": prompt_cause}
    prompt_hash = content_hash(prompt_text)
    cursor_before = None
    if sessions.satisfied(ctx.store, ctx.run_id, prompt_ob) is None:
        _move_aside(workspace, watched)
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
    try:
        listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    except Exception:
        pass
    return Turn(session_id=sid, generation=ctx.generation, workspace=workspace, agent_name=snap["agent_name"],
                ended=ended.payload["ended"], files=files, cursor_before=cursor_before, listing=listing)
