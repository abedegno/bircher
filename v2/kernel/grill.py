"""Grill decision packets as immutable kernel facts.

Conversational UI state may live outside the kernel; the decisions may not. A
conversation held only in model or UI state makes `goal -> grilled decisions`
neither durable nor auditable, and Milestone 1 claims every arrow is a durable
transition. Both cannot stand.

A changed mind is a NEW answer appended, never an edit. The packet is an audit
trail, and an audit trail that can be rewritten is not one.

**Two entry points, not an `answered_by` parameter.** The plan had
`record_answer(..., asked_by, answered_by)` with a test asserting that "a
model-authored answer must not be indistinguishable from a human one" -- a
property that parameter cannot deliver, because the model supplies it. The
function a caller can reach is what decides: a model session may reach
`record_model_question`, and there is no argument it can pass to reach
`record_human_answer`. That one lives on the operator's side of the M1-1
boundary, the same enforcement `reconcile()` already relies on.
"""

from __future__ import annotations

from kernel.canon import canonical_bytes, content_hash
from kernel.events import EventKind

PACKET_CANON_VERSION = 1


def record_model_question(store, run_id: str, *, question: str) -> None:
    """The model path. A question is not a decision and carries no authority."""
    store.append_fact(
        run_id=run_id, kind=EventKind.MODEL_QUESTION, actor="model",
        causal_command_id=None, payload={"question": question},
    )


def record_human_answer(store, run_id: str, *, question: str, answer: str) -> None:
    """The operator path. The human's ruling, attributed to the human because
    only the human can reach this function -- not because a parameter said so.
    """
    store.append_fact(
        run_id=run_id, kind=EventKind.HUMAN_RULING, actor="human",
        causal_command_id=None,
        payload={"question": question, "answer": answer},
    )


def decision_packet(store, run_id: str) -> dict:
    """Every question asked and every ruling given, in order.

    Questions and answers are separate facts with separate kinds, so the
    packet can show a question the human never answered -- which the paired
    shape could not represent at all.
    """
    questions, answers = [], []
    for fact in store.facts_for(run_id):
        if fact.kind == EventKind.MODEL_QUESTION:
            questions.append({"question": fact.payload["question"],
                              "asked_by": fact.actor, "seq": fact.seq})
        elif fact.kind == EventKind.HUMAN_RULING:
            answers.append({"question": fact.payload["question"],
                            "answer": fact.payload["answer"],
                            "answered_by": fact.actor, "seq": fact.seq})
    return {"canon_version": PACKET_CANON_VERSION,
            "questions": questions, "answers": answers}


def unanswered(store, run_id: str) -> list[str]:
    """Questions with no ruling. An enqueue over these is an enqueue over an
    unfinished grill, and the front end must be able to say so."""
    packet = decision_packet(store, run_id)
    answered = {a["question"] for a in packet["answers"]}
    return [q["question"] for q in packet["questions"]
            if q["question"] not in answered]


def packet_hash(store, run_id: str) -> str:
    return content_hash(canonical_bytes(decision_packet(store, run_id)))
