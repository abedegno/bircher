"""The supervised handoff: one transaction, human authority."""

import inspect

import pytest

from kernel.enqueue import NotApproved, enqueue, propose_enqueue
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def store():
    return Store.open(":memory:", clock=Clock(start_us=1_000))


def _args(**over):
    a = dict(run_id="r", base_repo="o/r", base_sha="a" * 40,
             spec_bytes=b"# spec", plan_bytes=b"# plan",
             bundle_snapshot={"canon_version": 1, "number": 1, "title": "t",
                              "body": "b", "labels": [], "comments": []})
    a.update(over)
    return a


def _count(store, table):
    return store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- the transaction ----------------------------------------------------------

def test_enqueue_persists_artifacts_and_creates_the_run(store):
    r = enqueue(store, **_args())
    assert store.run_state("r") == "queued"
    assert r["spec_hash"] and r["plan_hash"] and r["bundle_hash"]


def test_enqueue_writes_the_first_durable_transition(store):
    enqueue(store, **_args())
    kinds = [f.kind for f in store.facts_for("r")]
    assert "run_started" in kinds
    assert "artifact_created" in kinds
    assert "run_enqueued" in kinds


def test_a_failure_midway_leaves_nothing(store, monkeypatch):
    """The single transaction. A crash between persisting artifacts and
    creating the run must leave NEITHER -- a run without its inputs cannot be
    replayed, and artifacts without a run are unreferenced."""
    import kernel.enqueue as mod

    def boom(*a, **k):
        raise RuntimeError("crash after artifacts")

    monkeypatch.setattr(mod, "_create_run_and_transition", boom)
    with pytest.raises(RuntimeError):
        enqueue(store, **_args())
    assert _count(store, "runs") == 0
    assert _count(store, "artifacts") == 0
    assert _count(store, "facts") == 0


def test_the_fault_injection_point_is_real(store, monkeypatch):
    """The known-positive for the test above: without patching, the same call
    succeeds. A monkeypatch on a name nothing calls would make the crash test
    pass vacuously."""
    r = enqueue(store, **_args())
    assert not r["replayed"]
    assert _count(store, "runs") == 1


def test_enqueue_is_idempotent_on_the_same_run_id(store):
    a = enqueue(store, **_args())
    b = enqueue(store, **_args())
    assert a["spec_hash"] == b["spec_hash"]
    assert a["bundle_hash"] == b["bundle_hash"]
    assert b["replayed"] and not a["replayed"]
    assert _count(store, "runs") == 1


def test_a_retry_does_not_duplicate_the_enqueue_fact(store):
    enqueue(store, **_args())
    enqueue(store, **_args())
    assert len([f for f in store.facts_for("r") if f.kind == "run_enqueued"]) == 1


# --- the supervised handoff ---------------------------------------------------

def test_enqueue_has_no_parameter_that_names_an_approver():
    """THE property. The plan had `approved_by` and a test passing
    `approved_by="model"` -- but a model calling this passes "human", so the
    test asserts `==` and the boundary it claims to prove is a string the
    caller chooses."""
    params = set(inspect.signature(enqueue).parameters)
    assert not params & {"approved_by", "actor", "approver", "authority"}


def test_the_enqueue_facts_are_attributed_to_the_human(store):
    """Reachable only from the operator's path, so this is a record rather
    than a claim -- the same enforcement reconcile() relies on."""
    enqueue(store, **_args())
    for kind in ("artifact_created", "run_enqueued"):
        actors = {f.actor for f in store.facts_for("r") if f.kind == kind}
        assert actors == {"human"}, kind


def test_a_model_proposing_an_enqueue_does_not_enqueue(store):
    """The model path, through the function a model can actually reach."""
    store.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    propose_enqueue(store, "r", reason="the bundle looks ready")
    kinds = [f.kind for f in store.facts_for("r")]
    assert "enqueue_proposed" in kinds
    assert "run_enqueued" not in kinds


def test_a_proposal_is_attributed_to_the_model(store):
    store.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    propose_enqueue(store, "r", reason="x")
    fact = [f for f in store.facts_for("r") if f.kind == "enqueue_proposed"][0]
    assert fact.actor == "model"


def test_the_model_path_takes_no_argument_that_reaches_the_operator_path():
    params = set(inspect.signature(propose_enqueue).parameters) - {"store", "run_id"}
    assert params == {"reason"}


# --- the grill must be finished -----------------------------------------------

def test_an_enqueue_over_unanswered_questions_is_refused(store):
    """An enqueue over an unfinished grill approves inputs the human never
    ruled on. The front end can see this via grill.unanswered(); the kernel
    refuses it rather than trusting the front end to check."""
    with pytest.raises(NotApproved, match="unanswered"):
        enqueue(store, **_args(unanswered_questions=["deadline?"]))
    assert _count(store, "runs") == 0


def test_a_finished_grill_enqueues(store):
    """The control."""
    assert enqueue(store, **_args(unanswered_questions=[]))["run_id"] == "r"


def test_the_enqueue_records_the_packet_hash(store):
    """The rulings the human gave are bound to the run they authorized."""
    enqueue(store, **_args(packet_hash="p" * 64))
    fact = [f for f in store.facts_for("r") if f.kind == "run_enqueued"][0]
    assert fact.payload["packet_hash"] == "p" * 64
