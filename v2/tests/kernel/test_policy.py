import pytest

from kernel import policy
from kernel.effects import NotReplayable
from kernel.enqueue import _run_exists, create_run
from kernel.events import EventKind
from kernel.store import Store


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


ISSUE = {"number": 3, "title": "T", "body": "B", "labels": ["bircher:queued"],
         "comments": []}


def test_defaults_are_the_specs():
    p = policy.Policy()
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("model", frozenset({"spec"}), 3, 16)


def test_labels_override_project_config():
    assert policy.derive(["bircher:grill"], {}).grill == "human"
    assert policy.derive(["bircher:autonomous"], {}).gates == frozenset()
    assert policy.derive(["bircher:gate-plan"], {}).gates == frozenset({"spec", "plan"})
    # Ruling 5: autonomous wins when both are present.
    assert policy.derive(["bircher:gate-plan", "bircher:autonomous"], {}).gates == frozenset()
    cfg = {"grill": "human", "gates": ["spec", "plan"], "max_rounds": 5, "max_seats": 40}
    p = policy.derive([], cfg)
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("human", frozenset({"spec", "plan"}), 5, 40)
    assert policy.derive(["bircher:autonomous"], cfg).gates == frozenset()


@pytest.mark.parametrize("cfg", [
    {"grill": "robot"}, {"gates": ["impl"]}, {"max_rounds": 0}, {"max_rounds": 6},
    {"max_seats": 3}, {"max_seats": 41}, {"max_rounds": "3"}, {"gates": "spec"},
    # Falsy non-dicts: only `None` means "unset". `project_config or {}` would
    # coerce these to `{}` and silently accept a wrong type.
    [], "", 0, False,
])
def test_out_of_range_config_is_refused(cfg):
    with pytest.raises(ValueError):
        policy.derive([], cfg)


def test_payload_round_trips():
    p = policy.derive(["bircher:gate-plan"], {"max_seats": 20})
    assert policy.from_payload(policy.to_payload(p)) == p
    assert policy.to_payload(p)["gates"] == ["plan", "spec"]


def test_create_run_freezes_policy_and_puts_the_snapshot(tmp_path):
    s = _store(tmp_path)
    out = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                     issue=dict(ISSUE, labels=["bircher:queued", "bircher:grill"]),
                     project_config={"max_seats": 20})
    assert out["replayed"] is False
    assert s.run_state("r-1") == "queued"
    assert s.has_artifact(out["bundle_hash"])
    frozen = s.newest_fact("r-1", EventKind.POLICY_FROZEN)
    assert frozen.actor == "kernel"
    assert frozen.payload["policy"] == {"grill": "human", "gates": ["spec"],
                                        "max_rounds": 3, "max_seats": 20}
    assert frozen.payload["labels"] == ["bircher:grill", "bircher:queued"]
    assert len(frozen.payload["project_config_hash"]) == 64
    assert policy.policy_of(s, "r-1") == policy.Policy(grill="human", max_seats=20)
    assert policy.policy_version(s, "r-1") == frozen.seq
    enq = s.newest_fact("r-1", EventKind.RUN_ENQUEUED)
    assert enq.payload["bundle_hash"] == out["bundle_hash"]


def test_create_run_replays_identical_and_refuses_different(tmp_path):
    s = _store(tmp_path)
    a = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=ISSUE, project_config={})
    b = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=dict(ISSUE, labels=["bircher:running"]), project_config={})
    assert b["replayed"] is True and b["bundle_hash"] == a["bundle_hash"]
    assert len(s.facts_of_kind("r-1", EventKind.POLICY_FROZEN)) == 1
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=dict(ISSUE, body="changed"), project_config={})
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=ISSUE, project_config={"max_rounds": 2})
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="1" * 40,
                   issue=ISSUE, project_config={})


def test_create_run_refuses_a_non_dict_project_config_and_leaves_no_run_row(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=ISSUE, project_config=[])
    assert _run_exists(s, "r-1") is False


def test_second_policy_frozen_is_refused(tmp_path):
    s = _store(tmp_path)
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
               issue=ISSUE, project_config={})
    with pytest.raises(policy.PolicyFrozen):
        policy.freeze(s, "r-1", labels=[], project_config={})


def test_a_run_without_policy_frozen_gets_defaults(tmp_path):
    s = _store(tmp_path)
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    assert policy.policy_of(s, "r-1") == policy.Policy()
    assert policy.policy_version(s, "r-1") is None
