import os
import time

import pytest

from coordinator import phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front

PLAN = b"# Plan\n\n### Task 1: do it\n"
SPEC = b"# Spec\n\nDo it.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None, issue=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels, project_config=cfg, issue=issue)
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


class Script:
    """What each session writes when prompted, keyed by the prompt's first line."""
    def __init__(self, fake, store):
        self.fake, self.store = fake, store

    def __call__(self, sid, text):
        ws = self.fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        name = self.fake.sessions[sid]["agent_name"]
        if text.startswith("# Bircher spec author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC + name.encode())
        elif text.startswith("# Bircher plan author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(PLAN + name.encode())
        elif text.startswith("# Bircher review brief"):
            phase = "spec" if "of a spec" in text else "plan"
            h = self.store.phase_artifact("r-1", phase)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"fine\n\nVERDICT: PASS {h[:8]}\n")


def _posts(fake):
    return [p for p, _ in fake.posts]


def test_model_no_gates_reaches_planned_untouched(world, monkeypatch):
    s, f, fake, ctx = world()
    fake.on_prompt = Script(fake, s)
    gh = []
    import kernel.cli
    real = kernel.cli.subprocess.run
    def run(argv, **kw):
        # kernel.cli.resolve_command rewrites "gh" to its absolute path before
        # subprocess.run ever sees the argv (kernel/cli.py), so the match is on
        # the basename, not the literal token -- as test_retire_and_publish.py
        # already does.
        if os.path.basename(argv[0]) == "gh":
            gh.append(argv)
            class R: returncode, stdout, stderr = 0, "url", ""
            return R()
        return real(argv, **kw)
    monkeypatch.setattr("kernel.cli.subprocess.run", run)
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("r-1") == "planned"
    kinds = [x.kind for x in s.facts_for("r-1")]
    assert "human_answer" not in kinds and "human_ruling" not in kinds and "parked" not in kinds
    subs = s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert [x.payload["phase"] for x in subs] == ["spec", "plan"]
    # spec §3 Rotation: a phase's first artefact is authored by the vendor
    # that did NOT review the previous phase's last accepted artefact -- codex
    # reviewed the spec, so claude authors the plan and codex reviews it. What
    # the rotation is FOR holds either way: no vendor reviews its own artefact.
    assert [x.payload["author"] for x in subs] == ["claude", "claude"]
    verdicts = s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)
    assert [v.payload["reviewer_identity"] for v in verdicts] == ["codex", "codex"]
    assert all(v.payload["reviewer_identity"] != x.payload["author"] for v, x in zip(verdicts, subs))
    assert [a[3] for a in gh] == ["1", "1"] and all("bircher: published" in a[-1] for a in gh)
    # Every session prompted, ended, stopped -- and ENDED is asserted, not
    # only claimed: the stop's cause is a turn_ended fact, so a run whose
    # sessions were stopped without one is a run that read output before the
    # turn was observed over.
    ended_sessions = {x.payload["session"] for x in s.facts_of_kind("r-1", EventKind.TURN_ENDED)}
    for row in front.satisfied_effects(s, "r-1", "sess-create"):
        sid = __import__("json").loads(row["external_object_id"])["id"]
        assert any(r["intent"]["obligation"]["session"] == sid for r in front.satisfied_effects(s, "r-1", "sess-prompt"))
        assert sid in ended_sessions, (sid, ended_sessions)
        assert any(r["intent"]["obligation"]["session"] == sid for r in front.satisfied_effects(s, "r-1", "sess-stop"))


def test_default_policy_parks_at_the_gate_and_resumes_on_approve(world, monkeypatch):
    s, f, fake, ctx = world(labels=())
    fake.on_prompt = Script(fake, s)
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("r-1") == "spec_accepted"
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "gate"
    sid = park.payload["session_id"]
    assert "approve" in fake.sessions[sid]["items"][-1]["content"][0]["text"]
    # Resume with nothing human: parked again, nothing re-sent.
    n = len(fake.sessions[sid]["items"])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert len(fake.sessions[sid]["items"]) == n
    fake.add_user_message(sid, "approve")
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("r-1") == "planned"


def test_grill_human_parks_on_questions_and_continues_in_the_same_session(world, monkeypatch):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    asked = {"n": 0}

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher spec author brief") and asked["n"] == 0:
            asked["n"] += 1
            open(os.path.join(ws, seat.QUESTIONS_OUT), "w").write("### Q1: db?\nRecommended: sqlite\n")
        elif text.startswith("Answered; continue."):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC)
        else:
            Script(fake, s)(sid, text)
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "grill"
    sid = park.payload["session_id"]
    fake.add_user_message(sid, "sqlite please")
    assert phases.run_loop(ctx) == phases.Exit.OK
    a = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    assert a.payload["question_ids"] == ["Q1"] and a.payload["answer"] == "sqlite please"
    authors = {__import__("json").loads(r["external_object_id"])["id"]
               for r in front.satisfied_effects(s, "r-1", "sess-create")
               if r["intent"]["obligation"]["phase"] == "spec"}
    # One AUTHOR session in the spec phase: the reviewer's own spec-phase
    # session is the codex one, and the server names the BUNDLE (ruling 14),
    # so the vendor is read off the bundle name rather than compared to it.
    assert authors == {sid} | {x for x in authors
                               if front.vendor_of(fake.sessions[x]["agent_name"]) == "codex"}
    assert s.run_state("r-1") == "planned"


def test_bound_exhausted_parks_and_retry_grants(world, monkeypatch):
    s, f, fake, ctx = world(cfg={"max_rounds": 1})
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if "author brief" in text:
            n = len(s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED))
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC + str(n).encode())
        else:
            h = s.phase_artifact("r-1", "spec")
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"no\n\nVERDICT: FAIL {h[:8]}\n")
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "bound_exhausted" and park.payload["verdict"] == "request_revision"
    assert s.has_artifact(park.payload["findings_hash"])
    assert front.rounds_used(s, "r-1", "spec", 0) == 1
    sid = park.payload["session_id"]
    fake.add_user_message(sid, "retry")
    assert phases.run_loop(ctx) == phases.Exit.PARKED                      # one more round, then parked again
    assert front.grants(s, "r-1") == 1 and front.rounds_used(s, "r-1", "spec", 0) == 2


def test_revise_bundle_on_resume_restarts_the_epoch(world, monkeypatch):
    s, f, fake, ctx = world(labels=(), issue={"number": 1, "title": "T", "body": "B", "labels": [], "comments": []})
    fake.on_prompt = Script(fake, s)
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    ctx.generation = f._dispatch(__import__("kernel.dispatch", fromlist=["Role"]).Role.OPERATOR, "runner")
    seat.command(ctx, "revise_bundle", {"issue": {"number": 1, "title": "T", "body": "B and C",
                                                  "labels": ["bircher:running"], "comments": []}})
    assert s.run_state("r-1") == "queued" and front.epoch(s, "r-1") == 1
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    subs = s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert subs[-1].payload["epoch"] == 1
    briefed = [p for p, b in fake.posts if p.endswith("/events")]
    assert len(briefed) >= 4
    text = [b for p, b in fake.posts if p.endswith("/events") and "author brief" in str(b)][-1]
    assert "B and C" in str(text)


def test_a_pending_effect_or_halt_exits_failed_before_any_dispatch(world):
    s, f, fake, ctx = world()
    from kernel.dispatch import Role
    g = f._dispatch(Role.OPERATOR, "runner")
    s.journal_intent("e", "r-1", g, "session_control", f"sess-create:r-1:{g}",
                     {"argv": ["curl"], "obligation": {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                                       "epoch": 0, "cause": "x"}})
    n = len(s.dispatches_for("r-1"))
    assert phases.run_loop(ctx) == phases.Exit.FAILED
    assert len(s.dispatches_for("r-1")) == n


def test_a_run_beyond_the_front_half_exits_ok_at_once(world):
    s, f, fake, ctx = world()
    f.to_implementing()
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert fake.posts == []


def test_retire_stops_every_session_of_a_cancelled_run(world):
    s, f, fake, ctx = world()
    fake.on_prompt = lambda sid, text: None
    ctx.turn_timeout_s = 10
    from coordinator import author
    author.author_round(ctx)                                        # a session exists, turn ended, stopped
    from kernel.dispatch import Role
    g = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g, f._newest_id())                             # a create with no prompt and no stop
    __import__("tests.coordinator.test_retire_and_publish", fromlist=["_register"])._register(fake, sid2)
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    execute_as_human(s, Command(name="cancel_run", run_id="r-1", expected_version=s.run_version("r-1"),
                                idempotency_key="cancel", generation=HUMAN_GENERATION, payload={}))
    ctx.generation = f._dispatch(Role.OPERATOR, "operator")
    keys = phases.retire(ctx)
    assert keys == [f"sess-stop:{sid2}:{ctx.generation}"]
    cancelled = [t for t in s.facts_of_kind("r-1", EventKind.TRANSITION) if t.payload["to"] == "cancelled"][-1]
    assert s.effect_by_key(keys[0], run_id="r-1")["intent"]["obligation"]["cause"] == cancelled.id


def test_a_retry_at_a_no_verdict_park_is_read_once(world, monkeypatch):
    """The reviewer's spin reproduction (spec §4, the cursor).

    `grant_round` transitions nothing and starts no session, so the pass that
    takes it leaves the run where it was. Without a cursor on the ruling the
    `retry` the human typed is STILL ahead of the cursor on the next listing,
    and the next stall reads the same message again -- refusing it this time,
    because no park is current by then, and answering the human with a
    refusal for a retry they already spent. Bounded so a genuine spin fails
    the test rather than hanging it.
    """
    s, f, fake, ctx = world()
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    reasons = []
    real_stall = phases.stall

    def counting_stall(ctx_, reason, **kw):
        reasons.append(reason)
        assert len(reasons) <= 4, f"the loop is spinning: {reasons}"
        return real_stall(ctx_, reason, **kw)
    monkeypatch.setattr("coordinator.phases.stall", counting_stall)

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if "author brief" in text:
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC)
        # The reviewer writes no review.md at all: every review round is a
        # no_verdict, which is the path that parks without moving the run.
    fake.on_prompt = on_prompt

    assert phases.run_loop(ctx) == phases.Exit.PARKED
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "no_verdict"
    sid = park.payload["session_id"]

    retry = fake.add_user_message(sid, "retry")
    logged = []
    ctx.log = logged.append
    assert phases.run_loop(ctx) in (phases.Exit.PARKED, phases.Exit.OK)
    assert len(reasons) <= 2, reasons
    # The grant carries the cursor of the listing it was read from, so the
    # retry is behind it...
    grant = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert grant.payload["ruling"] == "grant_round" and grant.payload["cursor_item_id"] == retry
    # ...and no later listing reads it again.
    assert not any("read twice" in m for m in logged), logged
    # Taken once: one grant, and no dismissal, so the human was never
    # answered with a refusal for a retry they had already spent.
    assert front.grants(s, "r-1") == 1
    assert [x.payload["ruling"] for x in s.facts_of_kind("r-1", EventKind.HUMAN_RULING)] == ["grant_round"]
    assert s.facts_of_kind("r-1", EventKind.HUMAN_ITEM_DISMISSED) == []
