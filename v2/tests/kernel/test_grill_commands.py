import pytest

from kernel import front, grill
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_questions_and_rulings_are_facts_of_the_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")                                   # grill=model
    sid = f.ask_round([("q1", "sqlite or postgres?")], rulings={"q1": "sqlite"})
    q = s.newest_fact("r-1", EventKind.MODEL_QUESTION)
    assert q.payload == {"epoch": 0, "phase": "spec", "question_id": "q1", "question": "sqlite or postgres?"}
    r = s.newest_fact("r-1", EventKind.MODEL_RULING)
    assert r.payload == {"epoch": 0, "question_id": "q1", "ruling": "sqlite",
                         "reasoning": "reasoned", "cost_if_wrong": "low"}
    assert front.grill_open(s, "r-1") is False
    f.author_round(SPEC_BYTES, resume=sid)               # a model grill never blocks
    assert s.run_state("r-1") == "spec_submitted"


def test_a_ruling_must_name_a_question_of_the_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id()); f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="question_id"):
        f._cmd(g, "record_model_ruling", {"question_id": "nope", "ruling": "x",
                                          "reasoning": "y", "cost_if_wrong": "z"})


def test_questions_and_rulings_need_the_author_role(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="author"):
        f._cmd(g, "record_model_question", {"question_id": "q1", "question": "?"})


def test_submit_spec_waits_for_the_human_under_grill_human(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:grill", "bircher:autonomous"))
    assert front.grill_open(s, "r-1") is True             # no answer yet
    with pytest.raises(NotAuthorized, match="grill"):
        f.author_round(SPEC_BYTES)
    sid = f.ask_round([("q1", "which db?"), ("q2", "which port?")])
    with pytest.raises(NotAuthorized, match="grill"):
        f.author_round(SPEC_BYTES, resume=sid)
    f.answer("sqlite, 8080", question_ids=("q1", "q2"))
    assert front.grill_open(s, "r-1") is False
    a = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    assert a.payload["question_ids"] == ["q1", "q2"] and a.payload["epoch"] == 0
    # A newer question re-opens the grill; a newer answer closes it.
    f.ask_round([("q3", "tls?")])
    assert front.grill_open(s, "r-1") is True
    with pytest.raises(NotAuthorized, match="grill"):
        f.author_round(SPEC_BYTES, resume=sid)
    f.answer("yes", question_ids=("q3",))
    f.author_round(SPEC_BYTES, resume=sid)
    assert s.run_state("r-1") == "spec_submitted"


def test_the_grill_is_scoped_to_the_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:grill", "bircher:autonomous"))
    f.answer("go")
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    f.revise({"number": 1, "title": "T", "body": "B, and also C", "labels": [], "comments": []})
    assert s.run_state("r-1") == "queued" and front.epoch(s, "r-1") == 1
    assert front.grill_open(s, "r-1") is True             # epoch 0's answer does not count
    with pytest.raises(NotAuthorized, match="grill"):
        f.author_round(SPEC_BYTES + b"\n## C\n")
    f.answer("still go")
    f.author_round(SPEC_BYTES + b"\n## C\n")


def test_front_packet_groups_by_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.ask_round([("q1", "a?")], rulings={"q1": "A"})
    f.answer("fine", question_ids=("q1",))
    p = grill.front_packet(s, "r-1")
    assert p["canon_version"] == 2
    e0 = p["epochs"][0]
    assert [q["question_id"] for q in e0["questions"]] == ["q1"]
    # Filtered, not shortened: the driver's birth shape_round records the
    # epoch's `shape` ruling too (Task 3 rule (c)).
    assert [r["ruling"] for r in e0["rulings"] if r["question_id"] != "shape"] == ["A"]
    assert [r["ruling"] for r in e0["rulings"]] == ["one piece", "A"]
    assert [a["answer"] for a in e0["answers"]] == ["fine"]
