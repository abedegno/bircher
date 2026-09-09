"""Task 6: the shaping round (shaping spec §3 *The shaping round*)."""
import os
import time

import pytest

from coordinator import phases, seat, shape
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.slices import COARSE
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}
RULING = b"Ruling: one piece\nReasoning: one coherent change.\nCost if wrong: a spec round.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None, issue=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, project_config=cfg, issue=issue or ISSUE, shape=False)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "i12-epic-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="i12-epic-1", server="http://srv", repo="o/r", issue_number=12,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _writer(fake, files: dict):
    """What the shaping session writes when prompted: {path: bytes}."""
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        for name, data in files.items():
            open(os.path.join(ws, name), "wb").write(data)
    return on_prompt


def _one_pass(ctx):
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(ctx.store, ctx.run_id, actor="coordinator", role=Role.OPERATOR).generation


def test_the_brief_carries_the_skill_the_definition_and_both_grammars(world):
    s, f, fake, ctx = world()
    _one_pass(ctx)
    from coordinator.author import author_brief
    b = author_brief(ctx, phase="slices").decode()
    assert b.startswith("# Bircher slices author brief")
    assert "name: shape-author" in b
    assert COARSE in b and b.count(COARSE) == 1
    assert "Ruling: one piece" in b and "## Slice 1: <title>" in b and "Depends on:" in b
    assert seat.SHAPE_OUT in b and seat.ARTIFACT_OUT in b
    assert "names no files" in b


def test_one_piece_ending(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.SHAPE_OUT: RULING})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "one_piece"
    assert s.run_state("i12-epic-1") == "queued"
    r = front.shape_ruling(s, "i12-epic-1", 0)
    assert r.payload["reasoning"] == "one coherent change." and r.payload["cost_if_wrong"] == "a spec round."
    # The turn ended on the file, and the session was stopped (the turn's end is a fact).
    assert s.newest_fact("i12-epic-1", EventKind.TURN_ENDED).payload["ended"] == "file"


def test_slice_plan_ending(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.ARTIFACT_OUT: SLICES_BYTES})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "submitted"
    assert s.run_state("i12-epic-1") == "slices_submitted"
    assert s.read_blob(s.phase_artifact("i12-epic-1", "slices")) == SLICES_BYTES
    assert os.path.exists(os.path.join(ctx.bundle_dir, "i12-epic-1", "slices-r1.md"))


def test_both_files_the_ruling_is_read(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.SHAPE_OUT: RULING, seat.ARTIFACT_OUT: SLICES_BYTES})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "one_piece"
    assert s.run_state("i12-epic-1") == "queued"
    assert s.phase_artifact("i12-epic-1", "slices") is None


@pytest.mark.parametrize("files", [
    {},
    {seat.SHAPE_OUT: b"Ruling: two pieces\nReasoning: r\nCost if wrong: c\n"},
    {seat.SHAPE_OUT: b"Ruling: one piece\nReasoning:\nCost if wrong: c\n"},
    {seat.SHAPE_OUT: b"preface\nRuling: one piece\nReasoning: r\nCost if wrong: c\n"},
])
def test_a_ruling_that_does_not_parse_is_the_empty_turn(world, files):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, files)
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "empty_retry"
    assert s.newest_fact("i12-epic-1", EventKind.AUTHOR_EMPTY) is not None
    assert s.run_state("i12-epic-1") == "shaping"
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "failed"          # the retry's own empty turn is RC_FAILED


def test_a_direction_during_the_turn_displaces_the_output(world):
    s, f, fake, ctx = world()
    def on_prompt(sid, text):
        fake.add_user_message(sid, "one piece, do not slice it")
        _writer(fake, {seat.ARTIFACT_OUT: SLICES_BYTES})(sid, text)
    fake.on_prompt = on_prompt
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "direction"
    assert s.run_state("i12-epic-1") == "shaping"
    assert s.newest_fact("i12-epic-1", EventKind.HUMAN_DIRECTION).payload["text"] == "one piece, do not slice it"
    assert s.phase_artifact("i12-epic-1", "slices") is None


def test_a_grammar_refusal_is_the_next_rounds_findings(world):
    s, f, fake, ctx = world()
    six = SLICES_BYTES + b"".join(b"\n## Slice %d: more\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n" % n for n in (3, 4, 5, 6))
    fake.on_prompt = _writer(fake, {seat.ARTIFACT_OUT: six})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "reauthor"
    rej = s.newest_fact("i12-epic-1", EventKind.COMMAND_REJECTED)
    assert rej.payload["command_name"] == "submit_slices" and "6 slices" in rej.payload["detail"]
    from coordinator.author import author_brief
    _one_pass(ctx)
    assert "6 slices" in author_brief(ctx, phase="slices").decode()


def test_the_loop_from_birth_to_planned_untouched(world):
    """The whole front half from `shaping`: one piece, spec, plan, no human."""
    s, f, fake, ctx = world()
    PLAN, SPEC = b"# Plan\n\n### Task 1: do it\n", b"# Spec\n\nDo it.\n"
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher slices author brief"):
            open(os.path.join(ws, seat.SHAPE_OUT), "wb").write(RULING)
        elif text.startswith("# Bircher spec author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC)
        elif text.startswith("# Bircher plan author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(PLAN)
        elif text.startswith("# Bircher review brief"):
            phase = "spec" if "of a spec" in text else "plan"
            h = s.phase_artifact("i12-epic-1", phase)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"fine\n\nVERDICT: PASS {h[:8]}\n")
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "planned"
    kinds = [x.kind for x in s.facts_for("i12-epic-1")]
    assert "parked" not in kinds and "human_ruling" not in kinds
    assert front.shape_ruling(s, "i12-epic-1", 0) is not None
