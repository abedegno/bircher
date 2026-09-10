"""Task 8: file_owed's four idempotent passes (shaping spec §3 *Filing the
children*, §7's crash rows)."""
import os
import time

import pytest

from coordinator import filing, phases, seat
from coordinator.effects import KERNEL
from kernel import front, slices
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous", "bircher:gate-plan"], "comments": []}
THREE = SLICES_BYTES + b"""
## Slice 3: The client
Scope: Add the page. Call the endpoint. Show the result.
Non-goals: styling.
Depends on: 1, 2
"""
REVERSED = b"""# T

## Slice 1: The API
Scope: Add the endpoint. Wire it. Test it.
Non-goals: x.
Depends on: 2

## Slice 2: The store
Scope: Add the table. Add the reads. Test them.
Non-goals: y.
Depends on: none
"""


class Crash(Exception):
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(plan=SLICES_BYTES, labels=("bircher:autonomous", "bircher:gate-plan")):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, issue=dict(ISSUE, labels=list(labels)), shape=False).to_sliced(plan)
        fake = FakeOmnigent()
        fake.issues[12] = {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": [], "labels": ["bircher:running"], "blocked_by": []}
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "i12-epic-1",
               "PATH": os.environ["PATH"]}
        ctx = phases.Ctx(store=s, run_id="i12-epic-1", server="http://srv", repo="o/r", issue_number=12,
                         repo_dir="", workspaces_root="", bundle_dir=str(tmp_path / "bundle"), agent_ids={},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=time.time, sleep=lambda x: None, log=lambda m: None)
        from kernel.dispatch import Role, dispatch
        ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
        return s, f, fake, ctx
    return make


def _children(fake):
    return {n: i for n, i in fake.issues.items() if n != 12}


def test_full_filing_in_one_pass(world):
    s, f, fake, ctx = world()
    keys = filing.file_owed(ctx)
    assert len(keys) == 2 + 1 + 2 + 2                      # creates, link, queue labels, umbrella + label
    kids = _children(fake)
    assert sorted(kids) == [40, 41]
    h = s.phase_artifact("i12-epic-1", "slices")
    plan = slices.parse(SLICES_BYTES)
    assert kids[40]["title"] == "The store" and kids[40]["body"] == slices.render_child(plan.slices[0], 12, h[:8], {})[1]
    assert kids[41]["body"] == slices.render_child(plan.slices[1], 12, h[:8], {1: 40})[1]
    assert kids[41]["blocked_by"] == [1040]
    for k in (40, 41):
        assert sorted(kids[k]["labels"]) == ["bircher:autonomous", "bircher:gate-plan", "bircher:queued", "bircher:slice"]
    umbrella = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]]
    assert len(umbrella) == 1 and umbrella[0][umbrella[0].index("--body") + 1] == slices.umbrella_body(h[:8], plan, {1: 40, 2: 41})
    assert fake.issues[12]["labels"] == ["bircher:sliced"]
    filed = front.slices_filed(s, "i12-epic-1", 0)
    assert {n: x.payload["issue"] for n, x in filed.items()} == {1: 40, 2: 41}
    assert front.filing_complete(s, "i12-epic-1", 0).payload["children"] == [40, 41]
    # The queue label was the LAST thing each child got: the link came first.
    order = [a[:4] for a in fake.gh]
    assert order.index(["gh", "api", "repos/o/r/issues/41/dependencies/blocked_by", "-X"]) < \
        order.index(["gh", "issue", "edit", "40"])


def test_dependency_order_files_the_blocker_first(world):
    s, f, fake, ctx = world(plan=REVERSED)
    filing.file_owed(ctx)
    creates = [a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]
    assert creates[0][creates[0].index("--title") + 1] == "The store"        # slice 2 first
    assert front.slices_filed(s, "i12-epic-1", 0)[1].payload["issue"] == 41


def test_a_second_pass_over_a_complete_filing_issues_nothing(world, monkeypatch):
    s, f, fake, ctx = world()
    filing.file_owed(ctx)
    calls = {"effects": 0, "commands": 0}
    real_perform, real_command = filing.perform_effect, seat.command
    monkeypatch.setattr(filing, "perform_effect", lambda *a, **k: calls.__setitem__("effects", calls["effects"] + 1) or real_perform(*a, **k))
    monkeypatch.setattr(seat, "command", lambda *a, **k: calls.__setitem__("commands", calls["commands"] + 1) or real_command(*a, **k))
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    assert filing.file_owed(ctx) == []
    assert calls == {"effects": 0, "commands": 0}


@pytest.mark.parametrize("after", [1, 2, 3, 4, 5, 6])
def test_a_crash_at_each_boundary_is_repaired_by_the_next_pass(world, monkeypatch, after):
    """Effects in order: create 1, create 2, link 2<-1, queue 1, queue 2,
    umbrella, umbrella label. A crash after the Nth leaves the next pass
    exactly the remainder, and NO child carries bircher:queued while any
    link of the plan is unsatisfied."""
    s, f, fake, ctx = world()
    real = filing.perform_effect
    count = {"n": 0}

    def crashing(*a, **k):
        out = real(*a, **k)
        count["n"] += 1
        if count["n"] == after:
            raise Crash(after)
        return out
    monkeypatch.setattr(filing, "perform_effect", crashing)
    with pytest.raises(Crash):
        filing.file_owed(ctx)
    kids = _children(fake)
    owed_links = [ob for ob in front.unsatisfied_filing(s, "i12-epic-1", 0) if ob["kind"] == "slice_dependency"]
    if owed_links:
        assert not any("bircher:queued" in i["labels"] for i in kids.values())
    assert front.filing_complete(s, "i12-epic-1", 0) is None
    monkeypatch.setattr(filing, "perform_effect", real)
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    second = filing.file_owed(ctx)
    assert len(second) == 7 - after
    assert len([a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]) == 2     # never a duplicate create
    assert front.filing_complete(s, "i12-epic-1", 0) is not None
    assert sorted(_children(fake)) == [40, 41]


def test_a_crash_between_a_create_and_its_fact_records_the_fact_next_pass(world, monkeypatch):
    s, f, fake, ctx = world()
    real = seat.command

    def crashing(ctx_, name, payload):
        if name == "record_slice_filed" and payload["slice"] == 1:
            raise Crash("fact")
        return real(ctx_, name, payload)
    monkeypatch.setattr(seat, "command", crashing)
    with pytest.raises(Crash):
        filing.file_owed(ctx)
    assert sorted(_children(fake)) == [40] and front.slices_filed(s, "i12-epic-1", 0) == {}
    monkeypatch.setattr(seat, "command", real)
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    filing.file_owed(ctx)
    assert front.slices_filed(s, "i12-epic-1", 0)[1].payload["issue"] == 40
    assert len([a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]) == 2


def test_three_slices_and_the_loop_exits_ok_at_sliced(world):
    s, f, fake, ctx = world(plan=THREE)
    assert phases.run_loop(ctx) == phases.Exit.OK
    kids = _children(fake)
    assert sorted(kids) == [40, 41, 42] and kids[42]["blocked_by"] == [1040, 1041]
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.filing_complete(s, "i12-epic-1", 0).payload["children"] == [40, 41, 42]
