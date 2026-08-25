"""Grill decisions are immutable kernel facts, and who ruled is a property of
the mechanism rather than of a parameter."""

import pytest

from kernel.grill import (
    decision_packet, packet_hash, record_human_answer, record_model_question,
    unanswered,
)
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def _qa(s, q, a):
    record_model_question(s, "r", question=q)
    record_human_answer(s, "r", question=q, answer=a)


def test_an_accepted_answer_is_an_immutable_fact(store):
    _qa(store, "scope?", "renderer only")
    assert "human_ruling" in [f.kind for f in store.facts_for("r")]


def test_a_ruling_is_attributed_to_the_human(store):
    _qa(store, "scope?", "renderer only")
    fact = [f for f in store.facts_for("r") if f.kind == "human_ruling"][0]
    assert fact.actor == "human"


def test_a_model_question_is_attributed_to_the_model(store):
    record_model_question(store, "r", question="scope?")
    fact = [f for f in store.facts_for("r") if f.kind == "model_question"][0]
    assert fact.actor == "model"


def test_a_model_authored_answer_is_not_expressible(store):
    """THE property. The plan asserted this with an `answered_by` parameter,
    which the model supplies -- so a model-authored answer would have been
    indistinguishable from a human one by construction. What enforces it is
    that the model-reachable function cannot record an answer at all."""
    import inspect

    params = set(inspect.signature(record_model_question).parameters)
    assert "answer" not in params
    assert "answered_by" not in params and "actor" not in params

    record_model_question(store, "r", question="scope?")
    assert not [f for f in store.facts_for("r") if f.kind == "human_ruling"]


def test_neither_entry_point_takes_an_actor(store):
    """A parameter that names an actor is not identity, wherever it appears."""
    import inspect

    for fn in (record_model_question, record_human_answer):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"actor", "asked_by", "answered_by"}, fn.__name__


def test_the_packet_records_who_asked_and_who_ruled(store):
    _qa(store, "scope?", "renderer only")
    packet = decision_packet(store, "r")
    assert packet["questions"][0]["asked_by"] == "model"
    assert packet["answers"][0]["answered_by"] == "human"


def test_a_question_with_no_ruling_is_visible(store):
    """The paired shape could not represent this at all: an unanswered
    question would simply not exist, and an enqueue over an unfinished grill
    would look complete."""
    record_model_question(store, "r", question="scope?")
    record_model_question(store, "r", question="deadline?")
    record_human_answer(store, "r", question="scope?", answer="renderer only")
    assert unanswered(store, "r") == ["deadline?"]


def test_nothing_is_unanswered_once_every_question_is_ruled(store):
    _qa(store, "scope?", "renderer only")
    assert unanswered(store, "r") == []


def test_the_packet_hash_changes_when_an_answer_is_added(store):
    _qa(store, "q1", "a1")
    h1 = packet_hash(store, "r")
    _qa(store, "q2", "a2")
    assert packet_hash(store, "r") != h1


def test_the_packet_hash_is_stable_for_the_same_answers(store):
    _qa(store, "q1", "a1")
    assert packet_hash(store, "r") == packet_hash(store, "r")


def test_the_packet_hash_changes_when_only_the_ANSWER_changes(store):
    """Hashing the questions and not the rulings would make two opposite
    decisions over the same grill hash identically."""
    s1 = Store.open(":memory:", clock=Clock(start_us=1_000))
    s1.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    s2 = Store.open(":memory:", clock=Clock(start_us=1_000))
    s2.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    _qa(s1, "scope?", "renderer only")
    _qa(s2, "scope?", "renderer AND main")
    assert packet_hash(s1, "r") != packet_hash(s2, "r")


def test_answers_cannot_be_revised_in_place(store):
    """Facts are append-only. A changed mind is a new answer, and the packet
    must show both -- that is what makes it an audit trail."""
    _qa(store, "scope?", "renderer only")
    record_human_answer(store, "r", question="scope?", answer="renderer and main")
    answers = decision_packet(store, "r")["answers"]
    assert len(answers) == 2
    assert [a["answer"] for a in answers] == ["renderer only", "renderer and main"]


def test_the_packet_preserves_order(store):
    """A packet that reordered rulings would show the superseded answer as the
    final one."""
    _qa(store, "q1", "first")
    record_human_answer(store, "r", question="q1", answer="second")
    seqs = [a["seq"] for a in decision_packet(store, "r")["answers"]]
    assert seqs == sorted(seqs)
