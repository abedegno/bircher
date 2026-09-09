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
    """The §4 invariant, over every cursor-bearing fact a real pass wrote.

    A fact's WINDOW is what it claims to have consumed: the items after the
    previous cursor and at or before its own. Every user item in a window has
    to be accounted for -- one of the coordinator's own prompts, or the human
    message this very fact is the record of. A `parked` fact is the record of
    no human message, so its window must hold none: a park carrying a cursor
    past a message nobody read is the human being ignored.
    """
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    from coordinator.session import list_items

    def take():
        return human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch))

    fake.add_user_message(sid, "use sqlite")
    assert take() == "direction"                                     # a human_direction
    f.author_round(SPEC_BYTES, resume=sid)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")            # the author round superseded ours
    fake.add_user_message(sid, "approve")                            # illegal at spec_submitted
    assert take() == "dismissed"                                     # a human_item_dismissed
    assert len(human.reply_refusals(ctx)) == 1
    f.review_round("accept")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None,
                        verdict=None, reviewer=None) == "parked"     # a parked
    park = front.current_park(s, "r-1")
    fake.add_user_message(sid, "approve")
    assert human.human_pass(ctx, park) == "taken"                    # a human_ruling
    assert s.run_state("r-1") == "specified"

    items = fake.sessions[sid]["items"]
    order = {it["id"]: n for n, it in enumerate(items)}
    ours = {p.payload["item_id"] for p in s.facts_of_kind("r-1", EventKind.PROMPT_ITEM)}
    #: The four kinds that ARE the record of a human message (spec §4). A
    #: `parked` fact is not one of them.
    records_a_message = {EventKind.HUMAN_ANSWER, EventKind.HUMAN_DIRECTION,
                         EventKind.HUMAN_RULING, EventKind.HUMAN_ITEM_DISMISSED}
    examined, kinds, prev = 0, set(), -1
    for x in s.facts_for("r-1"):
        c = x.payload.get("cursor_item_id")
        if not c or c not in order:
            continue
        examined += 1
        kinds.add(x.kind)
        for it in items:
            if it["role"] != "user" or not (prev < order[it["id"]] <= order[c]):
                continue
            assert it["id"] in ours or x.kind in records_a_message, (x.kind, x.payload, it)
        prev = max(prev, order[c])
    assert examined >= 3, f"the scan examined {examined} facts: it is asserting nothing"
    assert kinds >= {EventKind.PARKED, EventKind.HUMAN_DIRECTION,
                     EventKind.HUMAN_ITEM_DISMISSED, EventKind.HUMAN_RULING}, kinds


def test_an_approval_moves_the_cursor_past_the_item_it_was_read_from(world):
    """spec §4: the cursor rides the rulings too. Without it the approval's own
    message stays ahead of the cursor and the next listing reads it again."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.add_user_message(sid, "approve")
    from coordinator.session import list_items
    listing = list_items("http://srv", sid, fetch=fake.fetch)
    assert human.take_listing(ctx, sid, listing) == "approve"
    assert s.run_state("r-1") == "specified"
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert ruling.payload["cursor_item_id"] == listing[-1]["id"]
    assert human.unread_human_items(ctx, sid, listing) == []
    assert human.take_listing(ctx, sid, listing) is None


def test_an_item_read_twice_replays_and_takes_nothing(world):
    """The same item under the same key REPLAYS: the earlier result comes
    back, no fact is written, and nothing was taken. Reporting the kind for a
    replay would tell the loop a human fact landed when none did."""
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "use sqlite")
    from coordinator.session import list_items
    listing = list_items("http://srv", sid, fetch=fake.fetch)
    assert human.take_listing(ctx, sid, listing) == "direction"
    n = len(s.facts_of_kind("r-1", EventKind.HUMAN_DIRECTION))
    f.direct("typed at the shell", cursor=listing[0]["id"])     # the cursor moves BACK
    logged = []
    ctx.log = logged.append
    assert human.take_listing(ctx, sid, listing) is None
    assert len(s.facts_of_kind("r-1", EventKind.HUMAN_DIRECTION)) == n + 1   # only the shell's
    assert any("replayed" in m for m in logged), logged


def test_a_dismissals_reply_goes_to_its_own_session_and_is_owed_once(world):
    """spec §4: the reply goes back to the session the refused token was read
    from, and stays satisfied. Both are derived from the dismissal's own fact
    -- its session, phase and epoch -- so a newer session does not steal the
    reply and a later phase does not make it owed all over again."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")       # the author round superseded ours
    fake.add_user_message(sid, "approve")                       # illegal at spec_submitted
    from coordinator.session import list_items
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) == "dismissed"
    d = s.newest_fact("r-1", EventKind.HUMAN_ITEM_DISMISSED)
    assert d.payload["session_id"] == sid and d.payload["phase"] == "spec" and d.payload["epoch"] == 0
    newer, _ = _session_with_prompt(s, f, fake, ctx, text=b"a newer session")
    assert newer != sid
    assert len(human.reply_refusals(ctx)) == 1
    assert "not legal from state 'spec_submitted'" in fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert not any("Bircher refused" in it["content"][0]["text"] for it in fake.sessions[newer]["items"])
    # The run moves on to the plan phase: the reply is not owed a second time.
    f.review_round("accept"); f.approve()
    assert s.run_state("r-1") == "specified" and ctx.phase() == "plan"
    n_items = len(fake.sessions[sid]["items"])
    assert human.reply_refusals(ctx) == []
    assert len(fake.sessions[sid]["items"]) == n_items


def test_an_unreadable_confirm_listing_parks(world):
    """The second listing is guarded exactly as the first: the prompt is
    already sent and the park already recorded, so a server that goes away
    between the two reads leaves the pass parked, not crashed."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    seat.command(ctx, "park", {"reason": "gate", "session_id": sid, "cursor_item_id": None,
                               "findings_hash": None, "verdict": None, "reviewer": None})
    park = front.current_park(s, "r-1")
    reads = {"n": 0}
    real_fetch = fake.fetch

    def failing_fetch(url):
        # The first two reads are human_pass's own listing and the one
        # `_record_prompt_item` takes after the prompt; the third is the
        # confirm listing, and that is the one that goes away.
        if "/items" in url:            # the listing URL carries a paging query
            reads["n"] += 1
            if reads["n"] > 2:
                raise __import__("coordinator.session", fromlist=["LookupFailed"]).LookupFailed("gone")
        return real_fetch(url)
    ctx.fetch = failing_fetch
    logged = []
    ctx.log = logged.append
    assert human.human_pass(ctx, park) == "parked"
    assert any("confirm listing unreadable" in m for m in logged), logged


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


def test_the_humans_message_past_the_first_page_is_still_unread(world):
    """THE DEFECT the fake used to hide: a spec-authoring session passes 100
    items in the ordinary case (two per tool call), and the items route's
    first page is the OLDEST 100. The human's `approve` is always the newest
    item, so it was never in the page the discriminator read, and `human_pass`
    parked forever while the approval sat in the UI."""
    s, f, fake, ctx = world()
    fake.page_size = 25                                   # several pages, not one
    sid, g = _session_with_prompt(s, f, fake, ctx)
    for i in range(120):
        fake.add_assistant_text(sid, f"thinking {i}")
    approve = fake.add_user_message(sid, "approve")

    from coordinator.session import list_items
    listing = list_items("http://srv", sid, fetch=fake.fetch)
    assert listing[-1]["id"] == approve
    assert [it["id"] for it in human.unread_human_items(ctx, sid, listing)] == [approve]


def test_the_servers_cancellation_notice_is_not_the_human(world):
    """THE DEFECT, live on 2026-09-08. Stopping a session that is still
    working is normal (spec section 3: a session that wrote its file and kept
    working is interrupted rather than left running), and the harness then
    adds an item in the USER's voice -- role `user`, text beginning
    "[System: interrupted] The user interrupted and abandoned their previous
    request". The coordinator read its own stop as a direction from the
    human: the round was displaced, the finished plan sitting in the worktree
    was never read, and the run repeated that until its seats ran out.

    Only `response_id` tells them apart: `cancel_...` for the server's item,
    `turn_...` for every real message.
    """
    from coordinator.session import list_items
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(
        sid,
        "[System: interrupted]\nThe user interrupted and abandoned their previous request "
        "(the user message immediately before this one). Do not resume or act on it.",
        response_id="cancel_9f1")
    listing = list_items("http://srv", sid, fetch=fake.fetch)

    assert human.unread_human_items(ctx, sid, listing) == []
    assert human.take_listing(ctx, sid, listing) is None
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION) is None, \
        "the server's own notice must not become a human_direction"


def test_a_real_message_in_the_same_session_is_still_read(world):
    """The guard is a denylist for one known shape, not an allowlist: missing
    a person's message is the failure section 4 forbids outright."""
    from coordinator.session import list_items
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "[System: interrupted]\nignore me", response_id="cancel_9f1")
    mine = fake.add_user_message(sid, "stop and rethink the approach")
    listing = list_items("http://srv", sid, fetch=fake.fetch)

    unread = human.unread_human_items(ctx, sid, listing)
    assert [it["id"] for it in unread] == [mine]


def test_a_gate_approval_is_not_eaten_by_the_models_own_questions(world):
    """THE DEFECT, live on 2026-09-08 on the DEFAULT policy (grill=model,
    gates={spec}). Under grill=model the author records its own questions and
    rules them itself, so `model_question` facts accumulate that no
    human_answer will ever follow. `take_listing` read those raw facts as "the
    grill is open" and classified every later message as an answer -- so the
    person's `approve` at the gate became a `human_answer` naming twelve
    questions the model had already ruled on, and the run parked at the same
    gate again. The gate was unreachable for the policy every run uses.
    """
    from coordinator.session import list_items
    s, f, fake, ctx = world(labels=())             # default policy: grill=model, gates={spec}
    sid, g = _session_with_prompt(s, f, fake, ctx)
    # The author's own questions, ruled by the model, never answered by anyone.
    f.ask_round([("Q1", "sqlite or postgres?"), ("Q2", "which port?")],
                rulings={"Q1": "sqlite", "Q2": "8080"})
    f.author_round(SPEC_BYTES, resume=sid)
    f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    fake.add_user_message(sid, "approve")
    listing = list_items("http://srv", sid, fetch=fake.fetch)

    kind = human.take_listing(ctx, sid, listing)
    assert s.newest_fact("r-1", EventKind.HUMAN_ANSWER) is None, \
        "an approval must not be recorded as an answer to the model's own questions"
    assert kind == "approve"
    assert s.newest_fact("r-1", EventKind.HUMAN_RULING).payload["ruling"] == "approve"
    assert s.run_state("r-1") == "specified", "the gate is passed, not parked at again"


def test_the_park_prompt_tells_the_agent_it_is_not_for_it(world):
    """Finding 13. The park prompt lands in a live agent session, so the
    agent reads it and a person's reply wakes it. Its first paragraph is
    addressed to the model: do nothing, write nothing, end the turn."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "gate", "session_id": sid, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    park = front.current_park(s, "r-1")
    text = human.park_prompt(ctx, park).decode() if hasattr(human, "park_prompt") else None
    assert text is not None, "the park prompt builder must be a module-level function to test"
    assert text.startswith(human.NOT_FOR_THE_AGENT), text[:200]
    assert "do not write or change any file" in text
    assert "approve" in text
