import os
import time

import pytest

from coordinator import human, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",)):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _session_with_prompt(s, f, fake, ctx, text=b"the brief"):
    """An author session the coordinator prompted, with its prompt_item."""
    g = f._dispatch(Role.AUTHOR, "claude")
    ctx.generation = g
    from coordinator import sessions
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g)); os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv", agent_id="ag_claude",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=phases.round_cause(ctx).id, env=ctx.effect_env())
    sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id=snap["id"],
                            phase="spec", epoch=0, cause=phases.round_cause(ctx).id, text=text, env=ctx.effect_env())
    item = fake.sessions[snap["id"]]["items"][-1]["id"]
    f._cmd(g, "record_prompt_item", {"session_id": snap["id"], "item_id": item,
                                     "sha256": __import__("kernel.canon", fromlist=["content_hash"]).content_hash(text)})
    return snap["id"], g


@pytest.mark.parametrize("batch,state,expected", [
    (["approve"], "spec_accepted", ("approve", "")),
    ([" Approve "], "spec_accepted", ("approve", "")),
    (["approve?"], "spec_accepted", ("revision", "approve?")),
    (["approve."], "spec_accepted", ("revision", "approve.")),
    (["approve", "but fix the title"], "spec_accepted", ("revision", "approve\n\nbut fix the title")),
    (["retry"], "spec_submitted", ("retry", "")),
    (["retry"], "queued", ("retry", "")),
    (["use sqlite"], "queued", ("direction", "use sqlite")),
    (["no", "and also no"], "plan_submitted", ("revision", "no\n\nand also no")),
])
def test_classify_batch(batch, state, expected):
    items = [{"id": f"i{n}", "role": "user", "text": t} for n, t in enumerate(batch)]
    assert human.classify_batch(items, state=state, grill_open=False) == expected


def test_a_batch_while_questions_are_open_is_one_answer():
    items = [{"id": "i1", "role": "user", "text": "sqlite"}, {"id": "i2", "role": "user", "text": "and 8080"}]
    assert human.classify_batch(items, state="queued", grill_open=True) == ("answer", "sqlite\n\nand 8080")


def test_the_discriminator_excludes_the_coordinators_own_prompts(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_assistant_text(sid, "working...")
    h1 = fake.add_user_message(sid, "actually use postgres")
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    unread = human.unread_human_items(ctx, sid, listing)
    assert [it["id"] for it in unread] == [h1]
    # Crash window: a second prompt confirmed with no prompt_item -- excluded by hash.
    from coordinator import sessions
    f.answer("x")
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    g2 = f._dispatch(Role.AUTHOR, "claude"); ctx.generation = g2
    sessions.prompt_session(s, run_id="r-1", generation=g2, server="http://srv", session_id=sid, phase="spec",
                            epoch=0, cause=ans.id, text=b"Answered; continue.", env=ctx.effect_env())
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    assert [it["id"] for it in human.unread_human_items(ctx, sid, listing)] == [h1]


def test_cursor_is_the_newest_facts_cursor_else_the_first_item(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    assert human.cursor(s, "r-1", sid, listing) == listing[0]["id"]
    f.direct("x", cursor="i-42")
    assert human.cursor(s, "r-1", sid, listing) == "i-42"


def test_take_listing_records_by_state_with_the_listings_cursor(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "use sqlite")
    last = fake.add_assistant_text(sid, "ok")
    from coordinator.session import list_items
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) == "direction"
    d = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    assert d.payload == {"phase": "spec", "epoch": 0, "text": "use sqlite", "cursor_item_id": last}
    # The same listing again: nothing unread, nothing recorded.
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) is None
    assert len(s.facts_of_kind("r-1", EventKind.HUMAN_DIRECTION)) == 1


def test_a_refused_approve_is_dismissed_and_answered_once(world):
    s, f, fake, ctx = world(labels=())
    f.author_round(SPEC_BYTES)                                   # spec_submitted: approve is illegal here
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "approve")
    from coordinator.session import list_items
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) == "dismissed"
    rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rej.actor == "human"
    d = s.newest_fact("r-1", EventKind.HUMAN_ITEM_DISMISSED)
    assert d.payload["rejection"] == rej.id
    sent = human.reply_refusals(ctx)
    assert len(sent) == 1
    reply = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert "not legal from state 'spec_submitted'" in reply
    assert human.reply_refusals(ctx) == []                         # once
    # A second pass over the same listing sends nothing and records nothing.
    n = len(s.facts_for("r-1"))
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) is None
    assert len(s.facts_for("r-1")) == n


def test_a_dismissed_reply_is_owed_across_a_later_human_fact(world):
    s, f, fake, ctx = world(labels=())
    f.author_round(SPEC_BYTES)
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "approve")
    from coordinator.session import list_items
    human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch))
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})      # a newer human fact
    assert len(human.reply_refusals(ctx)) == 1


def test_human_pass_at_a_gate_prompts_once_and_takes_the_approval(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    from coordinator.session import list_items
    listing = list_items("http://srv", sid, fetch=fake.fetch)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "parked"
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "gate" and park.payload["cursor_item_id"] == listing[-1]["id"]
    prompt = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert "single word `approve`" in prompt and SPEC_BYTES.decode() in prompt
    row = s.effect_by_key(f"sess-prompt:{sid}:{ctx.generation}:{park.id}", run_id="r-1")
    assert row["state"] == "confirmed"
    # The next pass: nothing human -> parked again, nothing re-sent.
    n_items = len(fake.sessions[sid]["items"])
    assert human.human_pass(ctx, park) == "parked"
    assert len(fake.sessions[sid]["items"]) == n_items
    # The human approves.
    fake.add_user_message(sid, "approve")
    assert human.human_pass(ctx, park) == "taken"
    assert s.run_state("r-1") == "specified"
    assert front.current_park(s, "r-1") is None


def test_a_message_in_the_confirm_listing_consumes_the_park(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.on_prompt = lambda sid_, text: fake.add_user_message(sid_, "approve")   # typed between list and prompt
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "taken"
    assert s.run_state("r-1") == "specified"
    park = s.newest_fact("r-1", EventKind.PARKED)
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert ruling.seq > park.seq and front.current_park(s, "r-1") is None


def test_a_human_message_before_the_park_means_no_park(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.add_user_message(sid, "approve")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "taken"
    assert s.newest_fact("r-1", EventKind.PARKED) is None
    assert s.run_state("r-1") == "specified"


def test_no_fact_carries_a_cursor_past_an_unrecorded_human_item(world):
    """Asserted over every fact the loop wrote, against the fake's full list."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.add_user_message(sid, "first")
    fake.add_user_message(sid, "second")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None)
    items = fake.sessions[sid]["items"]
    order = {it["id"]: n for n, it in enumerate(items)}
    recorded_text = " ".join(x.payload.get("text", "") + x.payload.get("findings", "") + x.payload.get("answer", "")
                             for x in s.facts_for("r-1"))
    for x in s.facts_for("r-1"):
        c = x.payload.get("cursor_item_id")
        if not c:
            continue
        for it in items:
            if it["role"] == "user" and order[it["id"]] <= order[c] and it["text"] not in ("the brief",):
                assert it["text"] in recorded_text or any(
                    p.payload["item_id"] == it["id"] for p in s.facts_of_kind("r-1", EventKind.PROMPT_ITEM)), it


def test_grill_park_with_a_carrier_session(world):
    """budget_exhausted at the plan's first dispatch: the spec author's session carries the prompt."""
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "budget_exhausted", session_id=None, findings_hash=None, verdict=None,
                        reviewer=None) == "parked"
    park = front.current_park(s, "r-1")
    assert park.payload["session_id"] == sid                       # the run's most recent session
    assert "single word `retry`" in fake.sessions[sid]["items"][-1]["content"][0]["text"]
