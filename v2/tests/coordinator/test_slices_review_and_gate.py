"""Task 7: the review round at slices_submitted, the gate at slices_accepted,
the ungated advance (shaping spec §3 *The review round*, *The gate*)."""
import os
import time

import pytest

from coordinator import human, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.slices import COARSE
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": [], "comments": []}
REVISED = SLICES_BYTES.replace(b"the API.", b"the API and the client.") + b"\n## Dispositions\n\n1. accepted - merged.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=(), cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, project_config=cfg, issue=dict(ISSUE, labels=list(labels)), shape=False)
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


class Script:
    """The shaper writes a plan; the reviewer answers with *verdict*."""
    def __init__(self, fake, store, verdict="PASS", plans=(SLICES_BYTES, REVISED)):
        self.fake, self.store, self.verdict, self.plans, self.n = fake, store, verdict, list(plans), 0
        self.briefs = []

    def __call__(self, sid, text):
        ws = self.fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher slices author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(self.plans[min(self.n, len(self.plans) - 1)])
            self.n += 1
        elif text.startswith("# Bircher review brief"):
            self.briefs.append(text)
            h = self.store.phase_artifact("i12-epic-1", "slices")
            v = self.verdict if isinstance(self.verdict, str) else self.verdict.pop(0)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"1. [high] §1 too fine\n\nVERDICT: {v} {h[:8]}\n")


def _carrier(s):
    return phases._newest_author_session(type("C", (), {"store": s, "run_id": "i12-epic-1"})())


def test_ungated_review_and_advance(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",))
    fake.on_prompt = Script(fake, s)
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    kinds = [x.kind for x in s.facts_for("i12-epic-1")]
    assert "parked" not in kinds and "human_ruling" not in kinds
    assert s.newest_fact("i12-epic-1", EventKind.ARTIFACT_ADVANCED).payload["phase"] == "slices"
    # The reviewer's brief carried the coarse paragraph and named the slice plan.
    assert COARSE in fake.on_prompt.briefs[0] and "of a slices" in fake.on_prompt.briefs[0]
    v = front.newest_review_verdict(s, "i12-epic-1", "slices", 0)
    assert v.payload["verdict"] == "accept" and v.payload["reviewer_identity"] == "codex"


def test_gated_by_default_parks_and_approve_moves_on(world):
    s, f, fake, ctx = world()
    fake.on_prompt = Script(fake, s)
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("i12-epic-1") == "slices_accepted"
    park = front.current_park(s, "i12-epic-1")
    assert park.payload["reason"] == "gate" and park.payload["phase"] == "slices"
    # The notice on the issue, and the prompt in the carrier with the plan attached.
    notices = [a for a in fake.gh if os.path.basename(a[0]) == "gh" and a[1:3] == ["issue", "comment"]
               and "bircher: parked gate" in a[a.index("--body") + 1]]
    assert len(notices) == 1 and "**slices** phase" in notices[0][notices[0].index("--body") + 1]
    sid = _carrier(s)
    last = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert last.startswith(human.NOT_FOR_THE_AGENT) and "## Slice 1: The store" in last
    fake.add_user_message(sid, "approve")
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    assert s.newest_fact("i12-epic-1", EventKind.HUMAN_RULING).payload == {
        "ruling": "approve", "phase": "slices", "epoch": 0,
        "artifact_hash": s.phase_artifact("i12-epic-1", "slices"),
        "cursor_item_id": fake.sessions[sid]["items"][-1]["id"]}


def test_a_correction_at_the_gate_is_the_next_rounds_findings(world):
    s, f, fake, ctx = world()
    script = Script(fake, s)
    fake.on_prompt = script
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    fake.add_user_message(_carrier(s), "merge slices 1 and 2; the store alone is a fragment")
    assert phases.run_loop(ctx) == phases.Exit.PARKED           # a new round, a new plan, the gate again
    assert s.run_state("i12-epic-1") == "slices_accepted"
    subs = front.submissions(s, "i12-epic-1", "slices", 0)
    assert len(subs) == 2 and s.read_blob(subs[-1].payload["hash"]) == REVISED
    assert s.newest_fact("i12-epic-1", EventKind.REVIEW_VERDICT).payload["ruling"] == "review_ruling"
    hr = [x for x in s.facts_of_kind("i12-epic-1", EventKind.HUMAN_RULING) if x.payload["ruling"] == "request_revision"]
    assert len(hr) == 1


def test_a_fail_then_a_pass_with_dispositions(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",))
    fake.on_prompt = Script(fake, s, verdict=["FAIL", "PASS"])
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.rounds_used(s, "i12-epic-1", "slices", 0) == 1
    # Rotation: codex reviewed round 1, so codex authored round 2 and claude reviewed it.
    subs = front.submissions(s, "i12-epic-1", "slices", 0)
    assert [x.payload["author"] for x in subs] == ["claude", "codex"]
    assert "## The previous round's findings" in fake.on_prompt.briefs[1]


def test_bound_exhausted_parks_at_slices_submitted(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",), cfg={"max_rounds": 1})
    fake.on_prompt = Script(fake, s, verdict="FAIL", plans=(SLICES_BYTES, REVISED, REVISED + b"\nmore\n"))
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("i12-epic-1") == "slices_submitted"
    assert front.current_park(s, "i12-epic-1").payload["reason"] == "bound_exhausted"


@pytest.mark.parametrize("state", ["slices_submitted", "slices_accepted"])
def test_classify_batch_treats_the_slice_gate_as_a_gate(state):
    approve = [{"id": "i1", "role": "user", "text": "Approve"}]
    prose = [{"id": "i1", "role": "user", "text": "merge 1 and 2"}]
    assert human.classify_batch(approve, state=state, grill_open=False) == ("approve", "")
    assert human.classify_batch(prose, state=state, grill_open=False) == ("revision", "merge 1 and 2")
    assert human.classify_batch(prose, state="shaping", grill_open=False) == ("direction", "merge 1 and 2")
