"""The single transaction joining artifact persistence, enqueue and the first
durable transition.

A crash between the three must leave none of them. A run without its inputs
cannot be replayed; artifacts without a run are unreferenced. SQLite gives one
transaction, so all three go inside it.

**There is no `approved_by` parameter.** The plan had one, with a test that
passed `approved_by="model"` and expected a refusal -- but a model calling
this would pass `"human"`, so the test asserts Python's `==` and the boundary
it claims to prove is a string the caller chooses. What makes "the front end
holds no authority" enforceable is that `enqueue` is reachable only from the
operator's own path. That is enforced by the FILESYSTEM boundary, not the
M1-1 network boundary this used to cite: a model session cannot write the
kernel database. See kernel/dispatch.py and
tests/kernel/test_identity_precondition.py. A model session reaches `propose_enqueue`, which
records that it asked and enqueues nothing.
"""

from __future__ import annotations

from kernel.artifacts import put_artifact
from kernel.bundle import bundle_hash
from kernel.events import EventKind


class NotApproved(Exception):
    """Enqueue attempted without a human at the operator's path."""


def propose_enqueue(store, run_id: str, *, reason: str) -> None:
    """The model path. Records the request and enqueues nothing.

    Deliberately takes no store-mutating action beyond the fact: this is the
    supervised handoff, and the whole content of it is that the model's last
    act is to ask.
    """
    store.append_fact(
        run_id=run_id, kind=EventKind.ENQUEUE_PROPOSED, actor="model",
        causal_command_id=None, payload={"reason": reason},
    )


def _run_exists(store, run_id: str) -> bool:
    return store._conn.execute(
        "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone() is not None


def enqueue(store, *, run_id: str, base_repo: str, base_sha: str,
            spec_bytes: bytes, plan_bytes: bytes, bundle_snapshot: dict,
            packet_hash: str | None = None,
            unanswered_questions: list[str] | None = None) -> dict:
    """Persist the inputs, create the run, and record the enqueue. One
    transaction, human authority.

    Idempotent on run_id: an operator retrying after a partial failure must
    not double-enqueue, and at-least-once is how every other path here
    behaves.
    """
    if unanswered_questions:
        raise NotApproved(
            f"the grill has {len(unanswered_questions)} unanswered question(s): "
            f"{unanswered_questions[:3]}; an enqueue over an unfinished grill "
            "approves inputs the human never ruled on"
        )

    bhash = bundle_hash(bundle_snapshot)

    if _run_exists(store, run_id):
        # Recompute rather than re-persist. Returning the same hashes lets a
        # retry be a read, which is what makes it safe to retry at all.
        from kernel.canon import content_hash
        return {"run_id": run_id, "spec_hash": content_hash(spec_bytes),
                "plan_hash": content_hash(plan_bytes), "bundle_hash": bhash,
                "replayed": True}

    with store.transaction():
        spec_hash = put_artifact(store, spec_bytes)
        plan_hash = put_artifact(store, plan_bytes)
        _create_run_and_transition(
            store, run_id, base_repo, base_sha, spec_hash, plan_hash, bhash,
            packet_hash,
        )
    return {"run_id": run_id, "spec_hash": spec_hash, "plan_hash": plan_hash,
            "bundle_hash": bhash, "replayed": False}


def _create_run_and_transition(store, run_id, base_repo, base_sha,
                               spec_hash, plan_hash, bhash, packet_hash):
    """The run row, its RUN_STARTED fact, the artifact facts, and the enqueue.

    Separated so a fault can be injected between artifact persistence and this
    -- the crash window the single transaction exists to close.
    """
    store.create_run(run_id=run_id, base_repo=base_repo, base_sha=base_sha)
    for kind, h in (("spec", spec_hash), ("plan", plan_hash)):
        store.append_fact(
            run_id=run_id, kind=EventKind.ARTIFACT_CREATED, actor="human",
            causal_command_id=None, payload={"role": kind, "hash": h},
        )
    store.append_fact(
        run_id=run_id, kind=EventKind.RUN_ENQUEUED, actor="human",
        causal_command_id=None,
        payload={"bundle_hash": bhash, "packet_hash": packet_hash,
                 "spec_hash": spec_hash, "plan_hash": plan_hash},
    )
