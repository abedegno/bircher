import os
import time

import pytest

from coordinator import author, phases, review, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.mark.parametrize("text,hash8,expected", [
    ("findings\n\nVERDICT: PASS deadbeef\n", "deadbeef", "PASS"),
    ("findings\n**VERDICT: FAIL deadbeef**\n", "deadbeef", "FAIL"),
    ("VERDICT: PASS deadbeef\nmore findings after\n", "deadbeef", None),
    ("findings\nVERDICT: PASS cafebabe\n", "deadbeef", None),
    ("findings\nVERDICT: PASS\n", "deadbeef", None),
    ("", "deadbeef", None),
    ("VERDICT: PASS", None, "PASS"),                    # the PR grammar, unchanged
    ("VERDICT: PASS deadbeef", None, None),
])
def test_extract_verdict_requires_the_hash_in_artefact_mode(text, hash8, expected):
    assert review.extract_verdict(text, hash8) == expected


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", project_config=cfg)
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


def _reviewer_writes(fake, text):
    def on_prompt(sid, prompt):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.REVIEW_OUT), "w").write(text)
    fake.on_prompt = on_prompt


def test_a_pass_records_accept_and_the_brief_is_the_first_prompt(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)                         # author claude
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"looks fine\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.verdict == "PASS" and out.reviewer == "codex"
    assert s.run_state("r-1") == "specified"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    assert v.payload["findings_hash"] == content_hash(f"looks fine\n\nVERDICT: PASS {h[:8]}\n".encode())
    sid = list(fake.sessions)[-1]
    first = fake.sessions[sid]["items"][0]["content"][0]["text"]
    brief = front.brief_for(s, "r-1", v.payload["generation"]).payload
    assert content_hash(first.encode()) == brief["brief_hash"]
    create = s.effect_by_key(f"sess-create:r-1:{v.payload['generation']}", run_id="r-1")
    assert create["intent"]["obligation"]["cause"] == front.newest_submission(s, "r-1", "spec", 0).id


def test_a_fail_with_rounds_left_records_request_revision(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"1. no threat model\n\nVERDICT: FAIL {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.verdict == "FAIL"
    assert s.run_state("r-1") == "queued"
    assert s.newest_fact("r-1", EventKind.REVIEW_VERDICT).payload["verdict"] == "request_revision"


def test_a_fail_with_no_rounds_left_is_bound_exhausted_and_records_nothing(world):
    s, f, fake, ctx = world(cfg={"max_rounds": 1})
    f.author_round(SPEC_BYTES); f.review_round("request_revision")
    f.author_round(SPEC_BYTES + b"2")
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"still wrong\n\nVERDICT: FAIL {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "bound_exhausted" and out.verdict == "FAIL" and out.rounds_left == 0
    assert out.findings_hash == content_hash(f"still wrong\n\nVERDICT: FAIL {h[:8]}\n".encode())
    assert s.run_state("r-1") == "spec_submitted"
    assert len([v for v in s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)]) == 1


def test_no_verdict_shapes(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    for text in (f"VERDICT: PASS {h[:8]}\nthen findings\n", "VERDICT: PASS cafebabe\n", "no verdict at all\n"):
        _reviewer_writes(fake, text)
        out = review.review_round(ctx)
        assert out.status == "no_verdict" and out.verdict is None
        assert out.findings_hash == content_hash(text.encode())
    ctx.turn_timeout_s = 10
    fake.on_prompt = lambda sid, prompt: None
    out = review.review_round(ctx)
    assert out.status == "no_verdict" and out.findings_hash is None
    assert s.run_state("r-1") == "spec_submitted"


def test_the_seat_after_a_grant_has_the_grant_as_cause(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    f.grant()
    grant = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"ok\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded"
    create = s.effect_by_key(f"sess-create:r-1:{out.generation}", run_id="r-1")
    assert create["intent"]["obligation"]["cause"] == grant.id


def test_a_dead_pass_between_create_and_prompt_is_adopted_with_a_fresh_brief(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    cause = phases.seat_cause(ctx).id
    g1 = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g1, "issue_review_brief", {"phase": "spec"})
    from coordinator import sessions
    ctx.generation = g1
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g1)); os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g1, server="http://srv", agent_id="ag_codex",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=cause, env=ctx.effect_env())
    _reviewer_writes(fake, f"ok\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.session_id == snap["id"]
    assert len(fake.sessions) == 1
    briefs = s.facts_of_kind("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    assert len(briefs) == 2 and briefs[0].payload["brief_hash"] == briefs[1].payload["brief_hash"]


def test_reviewer_is_the_other_vendor(world):
    s, f, fake, ctx = world()
    ctx.default_author = "codex"
    f.author = "codex"
    f.author_round(SPEC_BYTES)
    assert review.choose_reviewer_vendor(ctx) == "claude"
