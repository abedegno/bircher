"""Task 1: front-half fact kinds, phase artefacts and journal readers."""
import pytest

from kernel.events import SCHEMA_VERSIONS, EventKind
from kernel.store import Store

NEW_KINDS = {
    "POLICY_FROZEN": "policy_frozen",
    "ARTIFACT_SUBMITTED": "artifact_submitted",
    "REVIEW_BRIEF_ISSUED": "review_brief_issued",
    "PARKED": "parked",
    "TURN_ENDED": "turn_ended",
    "AUTHOR_EMPTY": "author_empty",
    "HUMAN_ANSWER": "human_answer",
    "MODEL_RULING": "model_ruling",
    "HUMAN_DIRECTION": "human_direction",
    "HUMAN_ITEM_DISMISSED": "human_item_dismissed",
    "PROMPT_ITEM": "prompt_item",
}


def _store(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    return s


def test_new_kinds_declared_with_schema_version():
    for attr, value in NEW_KINDS.items():
        assert getattr(EventKind, attr) == value
        assert SCHEMA_VERSIONS[value] == 1


def test_open_sets_path(tmp_path):
    s = Store.open(tmp_path / "k.db")
    assert s.path == str(tmp_path / "k.db")


def test_read_blob_roundtrip_and_missing(tmp_path):
    s = _store(tmp_path)
    s.put_blob("a" * 64, b"spec bytes")
    assert s.read_blob("a" * 64) == b"spec bytes"
    assert s.read_blob("b" * 64) is None


def test_phase_artifact_upserts_per_phase(tmp_path):
    s = _store(tmp_path)
    assert s.phase_artifact("r-1", "spec") is None
    s.set_phase_artifact("r-1", "spec", "a" * 64)
    s.set_phase_artifact("r-1", "spec", "b" * 64)
    s.set_phase_artifact("r-1", "plan", "c" * 64)
    assert s.phase_artifact("r-1", "spec") == "b" * 64
    assert s.phase_artifact("r-1", "plan") == "c" * 64


def test_facts_of_kind_and_newest(tmp_path):
    s = _store(tmp_path)
    s.append_fact(run_id="r-1", kind=EventKind.PARKED, actor="claude",
                  causal_command_id=None, payload={"n": 1})
    s.append_fact(run_id="r-1", kind=EventKind.TURN_ENDED, actor="claude",
                  causal_command_id=None, payload={"n": 2})
    s.append_fact(run_id="r-1", kind=EventKind.PARKED, actor="claude",
                  causal_command_id=None, payload={"n": 3})
    parked = s.facts_of_kind("r-1", EventKind.PARKED)
    assert [f.payload["n"] for f in parked] == [1, 3]
    both = s.facts_of_kind("r-1", EventKind.PARKED, EventKind.TURN_ENDED)
    assert [f.payload["n"] for f in both] == [1, 2, 3]
    assert s.newest_fact("r-1", EventKind.TURN_ENDED).payload["n"] == 2
    assert s.newest_fact("r-1", EventKind.HUMAN_ANSWER) is None
    assert s.facts_of_kind("r-2", EventKind.PARKED) == []


def test_effects_for_and_effect_by_key_carry_intent(tmp_path):
    s = _store(tmp_path)
    s.journal_intent("e1", "r-1", 3, "session_control", "k1",
                     {"argv": ["curl"], "obligation": {"kind": "sess-create"}})
    s.journal_intent("e2", "r-1", 3, "session_control", "k2", {"argv": ["curl"]})
    s.mark_effect("k1", "confirmed", "{\"id\": \"s1\"}", run_id="r-1")
    rows = s.effects_for("r-1")
    assert [r["idempotency_key"] for r in rows] == ["k1", "k2"]
    assert rows[0]["state"] == "confirmed"
    assert rows[0]["external_object_id"] == "{\"id\": \"s1\"}"
    assert rows[0]["intent"]["obligation"] == {"kind": "sess-create"}
    assert rows[0]["generation"] == 3
    assert rows[1]["state"] == "intended"
    one = s.effect_by_key("k1", run_id="r-1")
    assert one["intent"]["argv"] == ["curl"]
    assert one["generation"] == 3
    assert s.effects_for("r-2") == []


def test_dispatches_for_in_generation_order(tmp_path):
    s = _store(tmp_path)
    s.record_dispatch("d2", "r-1", 2, "codex", "reviewer")
    s.record_dispatch("d1", "r-1", 1, "claude", "author")
    rows = s.dispatches_for("r-1")
    assert [(r["generation"], r["actor"], r["role"]) for r in rows] == [
        (1, "claude", "author"), (2, "codex", "reviewer")]


def test_open_run_ids_filters_prefix_and_state(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="muesli-711-1", base_repo="o/r", base_sha="0" * 40)
    s.create_run(run_id="muesli-711-2", base_repo="o/r", base_sha="0" * 40)
    s.create_run(run_id="muesli-7110-1", base_repo="o/r", base_sha="0" * 40)
    s.set_run_state("muesli-711-2", "ended")
    assert s.open_run_ids("muesli-711", frozenset({"queued"})) == ["muesli-711-1"]
    assert s.open_run_ids("muesli-711", frozenset({"ended"})) == ["muesli-711-2"]


@pytest.mark.parametrize("prefix", ["muesli-%", "muesli-_11"])
def test_open_run_ids_rejects_like_wildcards_in_prefix(tmp_path, prefix):
    s = Store.open(tmp_path / "k.db")
    with pytest.raises(ValueError):
        s.open_run_ids(prefix, frozenset({"queued"}))
