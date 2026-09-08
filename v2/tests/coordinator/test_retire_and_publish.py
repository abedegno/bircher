import json
import os

import pytest

from coordinator import phases
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.effects import UncertainEffect, is_halted
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    fake = FakeOmnigent()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
    env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
           "PATH": os.environ["PATH"]}
    ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                     repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                     bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                     host_id="host_h", turn_timeout_s=5400, default_author="claude", env=env,
                     fetch=fake.fetch, clock=lambda: 10**9, sleep=lambda s: None, log=lambda m: None)
    return s, f, fake, ctx


def _register(fake, sid, agent="ag_claude", name="v2_author_claude"):
    fake.sessions[sid] = {"id": sid, "status": "idle", "agent_id": agent, "agent_name": name,
                          "host_id": "h", "workspace": f"/w/{sid}", "title": "t", "items": [], "labels": {}}
    fake.listed.add(sid)


def test_retire_stops_every_turn_ended_without_a_stop(world):
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    _register(fake, sid)
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})   # no stop journaled
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    keys = phases.retire_owed(ctx)
    assert keys == [f"sess-stop:{sid}:{ctx.generation}"]
    assert front.stop_satisfied_for(s, "r-1", te.id)
    assert phases.retire_owed(ctx) == []                       # once


def test_retire_derives_from_the_journal_not_the_server(world):
    """A turn_ended whose session the server lists as failed, or no longer
    lists, derives a stop all the same; the 404 halts."""
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "dead"})
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(UncertainEffect):
        phases.retire_owed(ctx)                                 # fake knows no such session -> 404
    assert is_halted(s, "r-1")


def test_retire_stops_an_orphan_with_the_displacing_cause(world):
    s, f, fake, ctx = world
    g = f._dispatch(Role.REVIEWER, "codex")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._create(g, f._newest_id())        # a satisfied create with no prompt: the effects table is append-only
    _register(fake, sid, "ag_codex", "v2_author_codex")
    # A human request_revision returns the run to queued: the seat is displaced.
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    keys = phases.retire_owed(ctx)
    assert keys == [f"sess-stop:{sid}:{ctx.generation}"]
    row = s.effect_by_key(keys[0], run_id="r-1")
    assert row["intent"]["obligation"] == {"kind": "sess-stop", "session": sid, "cause": ruling.id}
    assert phases.retire_owed(ctx) == []


def test_the_pass_does_not_retire_the_session_it_derives(world):
    """A create satisfied for the obligation this pass derives is adopted,
    not stopped: the crash-between-create-and-prompt case."""
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    cause = phases.round_cause(ctx).id
    sid = f._create(g, cause)                 # create satisfied, prompt owed
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.derived_session_obligation(ctx) == {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                                      "epoch": 0, "cause": cause}
    assert phases.retire_owed(ctx) == []


def test_round_cause_follows_the_journal(world):
    s, f, fake, ctx = world
    assert phases.round_cause(ctx).kind == "run_started"
    f.author_round(SPEC_BYTES)
    assert phases.seat_cause(ctx).kind == "artifact_submitted"
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.grant()
    assert phases.seat_cause(ctx).kind == "human_ruling"
    f.review_round("request_revision")
    assert phases.round_cause(ctx).kind == "review_verdict"
    f.direct("try again")
    assert phases.round_cause(ctx).kind == "human_direction"
    sid = f.ask_round([("q1", "?")])
    f.answer("a", question_ids=("q1",))
    assert phases.answer_to_resume(ctx).kind == "human_answer"
    f.author_round(SPEC_BYTES + b"2", resume=sid)
    f.review_round("accept")
    assert phases.round_cause(ctx).kind == "review_verdict"          # the accept opened the plan phase
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    assert phases.round_cause(ctx).kind == "bundle_revised"


def test_publish_owed_posts_once_per_approved_phase_and_is_idempotent(world):
    s, f, fake, ctx = world
    posted = []

    def gh(argv, **kw):
        posted.append(argv)
        class R: returncode, stdout, stderr = 0, "https://github.com/o/r/issues/1#issuecomment-9", ""
        return R()
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.publish_owed(ctx) == []
    f.to_specified()
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    import kernel.cli
    real = kernel.cli.subprocess.run
    # kernel.cli.resolve_command rewrites "gh" to its absolute path before
    # subprocess.run ever sees the argv (kernel/cli.py, tests/kernel/test_launcher.py),
    # so the match is on the basename, not the literal token.
    kernel.cli.subprocess.run = (
        lambda argv, **kw: gh(argv, **kw) if os.path.basename(argv[0]) == "gh" else real(argv, **kw))
    try:
        keys = phases.publish_owed(ctx)
        h = s.phase_artifact("r-1", "spec")
        assert keys == [f"publish:r-1:spec:{h}:{ctx.generation}"]
        body = posted[0][posted[0].index("--body") + 1]
        assert body.startswith(f"bircher: published spec {h[:8]}\n")
        assert SPEC_BYTES.decode() in body
        row = s.effect_by_key(keys[0], run_id="r-1")
        assert row["intent"]["obligation"] == {"kind": "publish", "run": "r-1", "phase": "spec", "hash": h}
        assert phases.publish_owed(ctx) == []
        f.author_round(PLAN_BYTES); f.review_round("accept")
        ctx.generation = f._dispatch(Role.OPERATOR, "runner")
        assert [k.split(":")[2] for k in phases.publish_owed(ctx)] == ["plan"]
    finally:
        kernel.cli.subprocess.run = real


def test_publication_body_is_capped():
    big = b"x" * (phases.PUBLISH_CAP + 100)
    body = phases.publication_body("plan", big, "a" * 64)
    assert body.startswith("bircher: published plan aaaaaaaa\n\n")
    assert len(body) < phases.PUBLISH_CAP + 200
    assert body.rstrip().endswith("under " + "a" * 64)
    small = phases.publication_body("spec", b"# S", "b" * 64)
    assert small == "bircher: published spec bbbbbbbb\n\n# S"


def test_notify_owed_comments_once_per_park_and_names_what_is_needed(world, monkeypatch):
    """spec section 4. A parked run is indistinguishable from an idle one in
    omnigent -- the inbox counts elicitations, which nothing here raises -- so
    the question sat in a transcript nobody watched and an overnight park
    looked exactly like a hang. The notice goes where the backlog already is.
    """
    s, f, fake, ctx = world
    posted = []

    def gh(argv, **kw):
        posted.append(argv)
        class R: returncode, stdout, stderr = 0, "https://github.com/o/r/issues/1#issuecomment-9", ""
        return R()
    import kernel.cli
    real = kernel.cli.subprocess.run
    kernel.cli.subprocess.run = (
        lambda argv, **kw: gh(argv, **kw) if os.path.basename(argv[0]) == "gh" else real(argv, **kw))
    monkeypatch.setenv("BIRCHER_OMNIGENT_UI", "https://omni.example/")
    try:
        ctx.generation = f._dispatch(Role.OPERATOR, "runner")
        assert phases.notify_owed(ctx) == [], "no park, no notice"

        g = f._dispatch(Role.OPERATOR, "runner")
        f._cmd(g, "park", {"reason": "gate", "session_id": "sess-7", "cursor_item_id": None,
                           "findings_hash": None, "verdict": None, "reviewer": None})
        ctx.generation = f._dispatch(Role.OPERATOR, "runner")
        park = front.current_park(s, "r-1")
        keys = phases.notify_owed(ctx)
        assert keys == [f"park-notice:r-1:{park.id}:{ctx.generation}"]

        body = posted[-1][posted[-1].index("--body") + 1]
        assert body.startswith("bircher: parked gate\n"), "the prefix keeps it out of the bundle"
        assert "approve" in body, "the notice says what to type"
        assert "https://omni.example/c/sess-7" in body, "and where to type it"
        assert ctx.run_id in body
        assert "read by the next wave" in body, "the notice says WHEN a reply is acted on"

        # Owed once: a second pass over the same park sends nothing.
        ctx.generation = f._dispatch(Role.OPERATOR, "runner")
        assert phases.notify_owed(ctx) == []
        assert len(posted) == 1
    finally:
        kernel.cli.subprocess.run = real


def test_the_park_notice_is_not_frozen_into_the_bundle():
    from kernel import bundle
    assert bundle.is_bircher_status("bircher: parked gate\n\nThis run is waiting for you.")
    assert not bircher_status_of_a_person()


def bircher_status_of_a_person() -> bool:
    from kernel import bundle
    # A person may reasonably open a comment this way; it must still be frozen.
    return bundle.is_bircher_status("bircher parked the run again, annoyingly")
