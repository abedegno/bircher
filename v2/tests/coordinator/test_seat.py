import json
import os
import time

import pytest

from coordinator import phases, seat
from coordinator.effects import KERNEL
from coordinator.session import AgentMismatch
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    fake = FakeOmnigent()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
    monkeypatch.setattr("coordinator.sessions.add_worktree",
                        lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
    env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
           "PATH": os.environ["PATH"]}
    clock = {"t": time.time()}               # offset from real time: effect rows carry the kernel's clock
    ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                     repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                     bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                     host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                     fetch=fake.fetch, clock=lambda: clock["t"], sleep=lambda x: clock.__setitem__("t", clock["t"] + x),
                     log=lambda m: None)
    return s, f, fake, ctx, clock


def _ob(ctx, cause):
    return {"kind": "sess-create", "run": "r-1", "phase": "spec", "epoch": 0, "cause": cause}


def test_run_turn_creates_prompts_waits_ends_stops_and_reads(world, tmp_path):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        with open(os.path.join(ws, seat.ARTIFACT_OUT), "wb") as fh:
            fh.write(b"# the spec")
    fake.on_prompt = on_prompt
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"write the spec", watched=[seat.ARTIFACT_OUT])
    assert t.agent_name == "v2_author_claude" and t.ended == "file"
    assert t.files == {seat.ARTIFACT_OUT: b"# the spec"}
    assert t.workspace == os.path.realpath(os.path.join(ctx.workspaces_root, "r-1", str(t.generation)))
    create = s.effect_by_key(f"sess-create:r-1:{t.generation}", run_id="r-1")
    assert create["intent"]["obligation"] == _ob(ctx, cause)
    body = json.loads(create["intent"]["argv"][-1])
    assert body["workspace"] == t.workspace and body["title"] == f"sess-create:r-1:{t.generation}"
    prompt_key = f"sess-prompt:{t.session_id}:{t.generation}:{cause}"
    assert s.effect_by_key(prompt_key, run_id="r-1")["state"] == "confirmed"
    pi = s.newest_fact("r-1", EventKind.PROMPT_ITEM)
    assert pi.payload["session_id"] == t.session_id and pi.payload["item_id"] == fake.sessions[t.session_id]["items"][0]["id"]
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    assert te.payload["prompt_key"] == prompt_key and te.payload["ended"] == "file"
    assert front.stop_satisfied_for(s, "r-1", te.id)
    # The journal shows the stop before the read: the read is not an effect,
    # so the proof is that the file bytes were taken after the stop confirmed.
    posts = [p for p, _ in fake.posts]
    assert posts[-1].endswith("/events")
    assert fake.posts[-1][1] == {"type": "stop_session"}


def test_read_after_stop_partial_bytes_hashed(world):
    """§11: a session still writing when the stop confirms yields whatever
    bytes the single read took, and the hash the journal holds is of those."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    state = {}

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        with open(os.path.join(ws, seat.ARTIFACT_OUT), "wb") as fh:
            fh.write(b"half")
        state["path"] = os.path.join(ws, seat.ARTIFACT_OUT)

    def on_stop(sid):
        with open(state["path"], "ab") as fh:
            fh.write(b" and more")      # lands after the stop confirms, before the read
    fake.on_prompt, fake.on_stop = on_prompt, on_stop
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.files[seat.ARTIFACT_OUT] == b"half and more"
    from kernel.artifacts import put_artifact
    from kernel.canon import content_hash
    assert put_artifact(s, t.files[seat.ARTIFACT_OUT]) == content_hash(b"half and more")


def test_a_second_pass_adopts_the_session_and_completes_the_prompt(world):
    """Crash after the create confirmed, before the prompt: the next pass
    adopts and sends; crash after the prompt confirmed, before prompt_item:
    the next pass finds the item by hash and records it, sending nothing."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    g1 = f._dispatch(Role.AUTHOR, "claude")
    from coordinator import sessions
    ctx.generation = g1
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g1))
    os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g1, server="http://srv", agent_id="ag_claude",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=cause, env=ctx.effect_env())
    fake.on_prompt = lambda sid, text: None
    clock["t"] += 5
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.session_id == snap["id"] and t.generation != g1
    assert len([k for k, _ in fake.posts if k == "v1/sessions"]) == 1        # no second create
    assert t.ended == "cap" and t.files == {seat.ARTIFACT_OUT: None}
    # Now the crash-window case: prompt confirmed, prompt_item missing.
    n_prompts = len([k for k, _ in fake.posts if k.endswith("/events")])
    items_before = len(fake.sessions[t.session_id]["items"])
    # A fresh round with the same cause would be the same obligation; simulate
    # the window by removing the prompt_item fact's effect: the facts table is
    # append-only, so drive it the other way -- a new cause, prompt confirmed
    # by hand, then run_turn completes it.
    f.answer("continue")
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    g3 = f._dispatch(Role.AUTHOR, "claude")
    ctx.generation = g3
    sessions.prompt_session(s, run_id="r-1", generation=g3, server="http://srv", session_id=t.session_id,
                            phase="spec", epoch=0, cause=ans.id, text=b"Answered; continue.\n\ncontinue",
                            env=ctx.effect_env())
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.\n\ncontinue",
                       watched=[seat.ARTIFACT_OUT], resume_session=t.session_id)
    assert len(fake.sessions[t.session_id]["items"]) == items_before + 1       # nothing re-sent
    assert [p.payload["item_id"] for p in s.facts_of_kind("r-1", EventKind.PROMPT_ITEM)][-1] == \
        fake.sessions[t.session_id]["items"][-1]["id"]


def test_deadline_is_the_prompt_rows_at_us_plus_the_cap(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    ctx.turn_timeout_s = 50
    fake.on_prompt = lambda sid, text: None
    start = clock["t"]
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.ended == "cap"
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    assert clock["t"] * 1e6 >= prompt["at_us"] + 50 * 1e6
    assert clock["t"] - start < 100


def test_agent_mismatch_propagates_before_any_read(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        fake.sessions[sid]["agent_id"] = "ag_switched"
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(b"x")
    fake.on_prompt = on_prompt
    with pytest.raises(AgentMismatch):
        seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert s.newest_fact("r-1", EventKind.TURN_ENDED) is None
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED) is None


def test_earlier_turn_files_are_moved_aside_before_a_reprompt(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(b"### Q1: x?\nRecommended: y\n")
    fake.on_prompt = on_prompt
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT])
    assert t.files[seat.QUESTIONS_OUT].startswith(b"### Q1")
    f.answer("y", question_ids=("Q1",))
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    fake.on_prompt = lambda sid, text: None
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.",
                       watched=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT], resume_session=t.session_id)
    assert t2.files == {seat.ARTIFACT_OUT: None, seat.QUESTIONS_OUT: None}    # the old file is not this turn's
    assert os.path.exists(os.path.join(t.workspace, seat.QUESTIONS_OUT + ".1.prev"))


def test_a_read_but_unwatched_file_is_moved_aside_too(world):
    """Under grill=model the questions file is READ beside the artefact but
    never watched. Retiring only the watched set left it in place across a
    re-prompt of the same session, and the round recorded its questions and
    rulings a second time -- the kernel guards neither against a duplicate."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    qs = b"### Q1: db?\nRecommended: sqlite\nRuling: sqlite - simplest - low\n"

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(qs)
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(b"# the spec")
    fake.on_prompt = on_prompt
    both = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT]
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT], read=both)
    assert t.files == {seat.ARTIFACT_OUT: b"# the spec", seat.QUESTIONS_OUT: qs}
    f.answer("sqlite", question_ids=("Q1",))
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    fake.on_prompt = lambda sid, text: None                    # the next turn writes nothing
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.",
                       watched=[seat.ARTIFACT_OUT], read=both, resume_session=t.session_id)
    assert t2.files == {seat.ARTIFACT_OUT: None, seat.QUESTIONS_OUT: None}
    for name in both:
        assert os.path.exists(os.path.join(t.workspace, name + ".1.prev"))


def test_a_watched_but_unread_file_is_moved_aside_too(world):
    """The UNION, not either set: nothing enforces read >= watched, and a
    stale WATCHED file ends the turn `file` on the first poll -- so the round
    spends its one empty-turn retry on a turn that never ran."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    ctx.turn_timeout_s = 10

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(b"### Q1: x?\nRecommended: y\n")
    fake.on_prompt = on_prompt
    watched = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT]
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=watched, read=[seat.ARTIFACT_OUT])
    assert t.ended == "file" and t.files == {seat.ARTIFACT_OUT: None}   # questions watched, not read
    f.answer("y", question_ids=("Q1",))
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    fake.on_prompt = lambda sid, text: None                    # the next turn writes nothing
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.",
                       watched=watched, read=[seat.ARTIFACT_OUT], resume_session=t.session_id)
    assert t2.ended == "cap"        # NOT `file` off the previous turn's questions.md
    assert os.path.exists(os.path.join(t.workspace, seat.QUESTIONS_OUT + ".1.prev"))


def _recorded_elsewhere(s, f, fake, ctx):
    """A session this run created under one obligation, so a later pass that
    resumes it under a different prompt cause takes the resume branch."""
    from coordinator import sessions
    g = f._dispatch(Role.AUTHOR, "claude")
    ctx.generation = g
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g))
    os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv", agent_id="ag_claude",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=phases.round_cause(ctx).id, env=ctx.effect_env())
    f.answer("carry on")                       # a fresh cause, so the create is not satisfied for it
    return snap, s.newest_fact("r-1", EventKind.HUMAN_ANSWER).id


def test_an_adopted_session_is_checked_against_the_journals_snapshot(world):
    """§11: `agent_id_expected` must come from the create the journal RECORDED.
    Taking it from a fresh fetch compares the server's answer against itself,
    so a redeployed or hostile server naming any agent it likes passes."""
    s, f, fake, ctx, clock = world
    snap, cause2 = _recorded_elsewhere(s, f, fake, ctx)
    fake.sessions[snap["id"]]["agent_id"] = "ag_switched"
    with pytest.raises(AgentMismatch):
        seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause2),
                      prompt_cause=cause2, prompt_text=b"go", watched=[seat.ARTIFACT_OUT],
                      resume_session=snap["id"])
    assert s.newest_fact("r-1", EventKind.TURN_ENDED) is None
    assert [k for k, _ in fake.posts if k.endswith("/events")] == []      # nothing sent


def test_an_adopted_sessions_workspace_is_the_journals_not_the_servers(world, tmp_path):
    """The read location is server-chosen too. The turn reads the workspace
    the create recorded, so a server that renames it cannot redirect the read."""
    s, f, fake, ctx, clock = world
    snap, cause2 = _recorded_elsewhere(s, f, fake, ctx)
    elsewhere = tmp_path / "server-chosen"
    os.makedirs(os.path.join(elsewhere, "bircher"))
    fake.sessions[snap["id"]]["workspace"] = str(elsewhere)     # the server moves it

    def on_prompt(sid, text):
        open(os.path.join(elsewhere, seat.ARTIFACT_OUT), "wb").write(b"# planted")
    fake.on_prompt = on_prompt
    ctx.turn_timeout_s = 10
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause2),
                      prompt_cause=cause2, prompt_text=b"go", watched=[seat.ARTIFACT_OUT],
                      resume_session=snap["id"])
    assert t.workspace == snap["workspace"] and t.workspace != str(elsewhere)
    assert t.ended == "cap" and t.files == {seat.ARTIFACT_OUT: None}   # the planted file is not read


def test_resuming_a_session_the_journal_never_recorded_is_refused(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    with pytest.raises(AgentMismatch, match="no satisfied sess-create"):
        seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT],
                      resume_session="s-nobody")
