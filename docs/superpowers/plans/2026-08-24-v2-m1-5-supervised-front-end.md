# Bircher v2 — Milestone 1, Plan 5: The Supervised Front End

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a vague issue into a frozen, hash-addressed bundle that a human inspects and explicitly enqueues, with every grill decision recorded as an immutable kernel fact and the enqueue itself a single transaction.

**Architecture:** The front end grills, produces spec and plan under adversarial review, and exports a bundle. It holds no authority: it cannot merge, push, label, or launch implementation. The handoff is a human reading the bundle and issuing one command; that command persists the artifacts, enqueues the run, and performs the first durable transition in one transaction, so a crash cannot leave a run enqueued without its inputs or persisted without its run.

**Tech Stack:** Python 3.11, `sqlite3` from the standard library, pytest, `gh` for reading issue state.

**Spec:** `docs/design/2026-08-23-v2-kernel-design.md` (branch `v2`, commit `6a2be96`)

## Global Constraints

- **Accepted human answers and grill decision packets are immutable kernel facts.** Conversational UI state may live outside the kernel; the decisions may not. (Spec, open question 3 — decided, not open.)
- **An approval authorizes a tuple of immutable inputs** — never a filename, branch name, issue number or "latest".
- **The front end holds no authority.** It cannot merge, push, label or launch implementation. That is the supervised handoff, and it is only real if something enforces it.
- **Milestone 1 must FIX six things about the frozen bundle**, not gesture at them: which issue fields, comments and labels form the frozen input; how that snapshot is canonicalized for hashing; what counts as a relevant change; who creates a revision; whether implementation outputs invalidate spec or plan review; and the single transaction joining artifact persistence, enqueue and the first durable transition. Each is a task or an explicit decision below.
- **Hashing is over raw bytes or a precisely versioned canonical form.** Never an informal serialization.
- **UTC integer microseconds.** Carried from M1-2.

**Depends on:** M1-2 (`Store`, `put_artifact`, `VerdictBinding`, `canonical_bytes`, `content_hash`), M1-3 (`Command`, `submit`), M1-3b (`dispatch`, `actor_for`), M1-4 (the effect adapter, for what "no authority" is enforced by).

## Reconciled against M1-3b, 2026-08-25

This plan predates the identity substrate, and two of its four tasks
reintroduce the defect M1-3b exists to remove.

**Task 3 takes `asked_by` and `answered_by` as parameters. Task 4 takes
`approved_by`.** These are caller-supplied identity strings — exactly what
`implementer_identity` and `reviewer_identity` were, and exactly how one caller
came to name both sides of its own independence check. `test_a_model_cannot_enqueue`
cannot pass as designed: the model supplies `approved_by`, so it supplies
`"human"`. The test would go green while proving nothing, which is the failure
mode this whole programme is organised around.

**The honest mechanism is not a dispatched actor.** A human approving an
enqueue is not an attempt: at enqueue time the run does not exist, so there is
no generation to dispatch against, and §4b's substrate does not reach here.
What already exists in the kernel is the right precedent — `reconcile()`
records `actor="human"` and the kernel accepts it, because reconciliation is
invoked from the operator's own path and a model session cannot reach it. The
enforcement is M1-1's boundary, not a string.

So the reconciliation is:

- **`approved_by` stops being a parameter.** The enqueue entry point stamps
  `human` itself. There is no code path a model can call that stamps it, which
  is what makes "the front end holds no authority" enforceable rather than
  asserted. A payload naming an approver is refused the way `ACTOR_FIELDS` are.
- **`record_answer` splits into two entry points**, `record_model_question` and
  `record_human_answer`. The *function called* determines the recorded actor.
  A model may call the first; the second lives behind the operator boundary.
  This is what makes "a model-authored answer must not be indistinguishable
  from a human one" a property of the mechanism rather than of a parameter the
  model fills in.
- **Every test asserting a model cannot do something must show the model
  TRYING**, through the path a model actually has, and being refused. A test
  that simply passes a different string is asserting Python's `==`.

Tasks 1 and 2 are hashing and change-detection and are unaffected.

---

## File Structure

| File | Responsibility |
|---|---|
| `v2/kernel/bundle.py` | The frozen input snapshot: selection, canonicalization, hashing. |
| `v2/kernel/grill.py` | Grill decision packets as immutable facts. |
| `v2/kernel/enqueue.py` | The single transaction: persist, enqueue, first transition. |
| `v2/tests/kernel/test_bundle.py` | Tasks 1–2. |
| `v2/tests/kernel/test_grill.py` | Task 3. |
| `v2/tests/kernel/test_enqueue.py` | Task 4. |
| `docs/design/frozen-bundle.md` | The six decisions, recorded where a reader will find them. |

---

### Task 1: The frozen input snapshot

Decision 1 (which fields) and decision 2 (canonicalization) — fixed here rather than left to the implementer.

**Files:**
- Create: `v2/kernel/bundle.py`, `v2/tests/kernel/test_bundle.py`, `docs/design/frozen-bundle.md`

**Interfaces:**
- Produces: `FROZEN_FIELDS`, `snapshot(issue: dict) -> dict`, `bundle_hash(snapshot: dict) -> str`, `BUNDLE_CANON_VERSION`.

- [x] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_bundle.py
import pytest
from kernel.bundle import (FROZEN_FIELDS, snapshot, bundle_hash,
                           BUNDLE_CANON_VERSION)

def _issue(**over):
    i = {
        "number": 711, "title": "live transcription loses speech",
        "body": "sometimes words go missing",
        "labels": ["bircher:queued", "bug"],
        "comments": [
            {"id": 1, "author": "jon", "body": "happens on reconnect"},
            {"id": 2, "author": "bircher-bot", "body": "bircher-status: running"},
        ],
        "updated_at": "2026-08-24T10:00:00Z",
        "reactions": 3, "view_count": 91,
    }
    i.update(over)
    return i

def test_the_frozen_fields_are_fixed_and_documented():
    """Decision 1. If this set changes, every previously frozen bundle hashes
    differently -- so it is pinned by a test, not by convention."""
    assert FROZEN_FIELDS == ("number", "title", "body", "labels", "comments")

def test_volatile_fields_are_excluded():
    """updated_at, reactions and view_count change without the input changing.
    Including them would invalidate approvals for no reason."""
    s = snapshot(_issue())
    for f in ("updated_at", "reactions", "view_count"):
        assert f not in s

def test_snapshot_is_stable_across_irrelevant_change():
    a = bundle_hash(snapshot(_issue()))
    b = bundle_hash(snapshot(_issue(reactions=99, view_count=5)))
    assert a == b

def test_label_order_does_not_change_the_hash():
    a = bundle_hash(snapshot(_issue(labels=["bug", "bircher:queued"])))
    b = bundle_hash(snapshot(_issue(labels=["bircher:queued", "bug"])))
    assert a == b

def test_comment_order_is_normalized_by_id():
    i = _issue()
    j = _issue(comments=list(reversed(i["comments"])))
    assert bundle_hash(snapshot(i)) == bundle_hash(snapshot(j))

@pytest.mark.parametrize("mutate,label", [
    (lambda i: i.update({"title": "different"}), "title"),
    (lambda i: i.update({"body": "different"}), "body"),
    (lambda i: i["labels"].append("bircher:blocked"), "labels"),
    (lambda i: i["comments"].append({"id": 3, "author": "x", "body": "new"}), "comments"),
])
def test_any_frozen_field_changing_changes_the_hash(mutate, label):
    """Each frozen field checked separately: one combined assertion would pass
    while three of four went unverified."""
    i = _issue()
    before = bundle_hash(snapshot(i))
    mutate(i)
    assert bundle_hash(snapshot(i)) != before, f"{label} did not affect the hash"

def test_canon_version_is_recorded_in_the_snapshot():
    """A hash whose canonical form can change without a version is a hash that
    can drift silently."""
    assert snapshot(_issue())["canon_version"] == BUNDLE_CANON_VERSION
```

- [x] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_bundle.py -v`
Expected: FAIL, `No module named 'kernel.bundle'`.

- [x] **Step 3: Implement**

```python
# v2/kernel/bundle.py
"""The frozen input snapshot.

Decision 1: exactly five fields form the frozen input. Volatile metadata
(updated_at, reactions, view counts) is excluded -- it changes without the
input changing, and including it would invalidate approvals for no reason.

Decision 2: the snapshot is canonicalized before hashing -- labels sorted,
comments ordered by id -- so a re-read of the same issue produces the same
hash regardless of the order GitHub returns.
"""
from __future__ import annotations
from kernel.canon import canonical_bytes, content_hash

BUNDLE_CANON_VERSION = 1

FROZEN_FIELDS = ("number", "title", "body", "labels", "comments")

def snapshot(issue: dict) -> dict:
    return {
        "canon_version": BUNDLE_CANON_VERSION,
        "number": int(issue["number"]),
        "title": issue["title"],
        "body": issue["body"],
        # Sorted: GitHub's label order is not stable and carries no meaning.
        "labels": sorted(issue.get("labels", [])),
        # Ordered by id: creation order is the meaningful one, and it is stable.
        "comments": [
            {"id": int(c["id"]), "author": c["author"], "body": c["body"]}
            for c in sorted(issue.get("comments", []), key=lambda c: int(c["id"]))
        ],
    }

def bundle_hash(snap: dict) -> str:
    return content_hash(canonical_bytes(snap))
```

- [x] **Step 4: Run, then record the decisions**

Run: `cd v2 && python -m pytest tests/kernel/test_bundle.py -v` — expect PASS.

Write `docs/design/frozen-bundle.md` recording decisions 1 and 2 with the reasoning above, so a later reader finds them in the design rather than only in code.

- [x] **Step 5: Commit with mutation evidence**

```bash
git add v2/kernel/bundle.py v2/tests/kernel/test_bundle.py docs/design/frozen-bundle.md
git commit -m "feat(v2): frozen input snapshot with fixed fields and canonicalization"
```

```bash
# Mutation: stop sorting labels. The label-order test must go red.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/bundle.py")
p.write_text(p.read_text().replace('sorted(issue.get("labels", []))', 'issue.get("labels", [])'))
EOF
cd v2 && python -m pytest tests/kernel/test_bundle.py::test_label_order_does_not_change_the_hash -v
git checkout v2/kernel/bundle.py && git status --short
```

---

### Task 2: Relevant change, revision authority, and review invalidation

Decisions 3, 4 and 5. These are judgement calls the spec requires Milestone 1 to make; this task makes them and encodes each as an executable predicate rather than prose.

**Files:**
- Modify: `v2/kernel/bundle.py`, `v2/tests/kernel/test_bundle.py`, `docs/design/frozen-bundle.md`

**Interfaces:**
- Produces: `is_relevant_change(old, new) -> bool`, `REVISION_AUTHORITY`, `invalidates(verdict_kind, changed) -> bool`.

- [x] **Step 1: Write the failing tests**

```python
# append to v2/tests/kernel/test_bundle.py
from kernel.bundle import is_relevant_change, REVISION_AUTHORITY, invalidates

def test_decision_3_a_relevant_change_is_any_frozen_field_change():
    """Defined by the frozen set, not by a separate list that could drift
    away from it."""
    a = snapshot(_issue())
    assert not is_relevant_change(a, snapshot(_issue(reactions=1)))
    assert is_relevant_change(a, snapshot(_issue(title="new")))

def test_decision_4_only_a_human_creates_a_revision():
    """The front end holds no authority. A model may propose; it may not
    create the revision that re-authorizes work."""
    assert REVISION_AUTHORITY == "human"

def test_decision_5_implementation_output_does_not_invalidate_spec_review():
    """A spec verdict binds the spec artifact and the base. Implementation
    changes the head, which the spec verdict never bound -- so invalidating it
    would discard sound approvals and push every run into re-review churn."""
    assert not invalidates("spec_review", {"head_git_sha"})
    assert not invalidates("plan_review", {"head_git_sha"})

def test_decision_5_implementation_review_does_bind_the_head():
    assert invalidates("implementation_review", {"head_git_sha"})

def test_decision_5_a_base_change_invalidates_everything():
    """Every verdict binds base_sha. Rebasing the world changes what any
    approval was about."""
    for kind in ("spec_review", "plan_review", "implementation_review"):
        assert invalidates(kind, {"base_git_sha"}), kind

def test_decision_5_a_bundle_change_invalidates_spec_and_plan():
    for kind in ("spec_review", "plan_review"):
        assert invalidates(kind, {"context_bundle_hash"}), kind
```

- [x] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_bundle.py -v`
Expected: FAIL, `cannot import name 'is_relevant_change'`.

- [x] **Step 3: Implement**

```python
# append to v2/kernel/bundle.py

# Decision 4: only a human creates a revision. The front end grills and
# proposes; re-authorizing work is not a model's to do.
REVISION_AUTHORITY = "human"

# Decision 5: which bound inputs invalidate which verdict kind.
#
# A spec or plan verdict binds the artifact, the base and the context bundle.
# It never bound the implementation head, so implementation output does not
# invalidate it -- invalidating on head would discard sound approvals and put
# every run into re-review churn for a change the reviewer never considered.
# An implementation verdict does bind the head. Every verdict binds the base.
_BINDS = {
    "spec_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash"},
    "plan_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash"},
    "implementation_review": {"artifact_hash", "base_git_sha", "head_git_sha"},
}

def is_relevant_change(old: dict, new: dict) -> bool:
    """Decision 3: a relevant change is any change to a frozen field.

    Derived from FROZEN_FIELDS rather than listed separately, so the two
    cannot drift apart.
    """
    return any(old.get(f) != new.get(f) for f in FROZEN_FIELDS)

def invalidates(verdict_kind: str, changed: set[str]) -> bool:
    return bool(_BINDS[verdict_kind] & set(changed))
```

- [x] **Step 4: Run, record, and mutation-test**

Run: `cd v2 && python -m pytest tests/kernel/test_bundle.py -v` — expect PASS.

Record decisions 3, 4 and 5 in `docs/design/frozen-bundle.md` with the reasoning, including why implementation output deliberately does *not* invalidate spec and plan review.

```bash
git add v2/kernel/bundle.py v2/tests/kernel/test_bundle.py docs/design/frozen-bundle.md
git commit -m "feat(v2): relevant change, revision authority, review invalidation"
```

```bash
# Mutation: let a spec verdict bind the head. The decision-5 test must go red.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/bundle.py")
p.write_text(p.read_text().replace(
  '"spec_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash"},',
  '"spec_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash", "head_git_sha"},'))
EOF
cd v2 && python -m pytest tests/kernel/test_bundle.py -k decision_5 -v
git checkout v2/kernel/bundle.py && git status --short
```

---

### Task 3: Grill decisions as immutable kernel facts

Open question 3, decided: a conversation held only in model or UI state makes `goal → grilled decisions` neither durable nor auditable, and Milestone 1 claims every arrow is a durable transition. Both cannot stand.

**Files:**
- Create: `v2/kernel/grill.py`, `v2/tests/kernel/test_grill.py`
- Modify: `v2/kernel/events.py`

**Interfaces:**
- Produces: `record_answer(store, run_id, question, answer, asked_by, answered_by) -> str`, `decision_packet(store, run_id) -> dict`, `packet_hash(store, run_id) -> str`.

- [x] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_grill.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.grill import record_answer, decision_packet, packet_hash

@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s

def test_an_accepted_answer_is_an_immutable_fact(store):
    record_answer(store, "r", question="scope?", answer="renderer only",
                  asked_by="model", answered_by="human")
    kinds = [f.kind for f in store.facts_for("r")]
    assert "human_ruling" in kinds

def test_the_packet_records_who_answered_not_just_what(store):
    """A decision attributed to nobody cannot be audited, and a model-authored
    answer must not be indistinguishable from a human one."""
    record_answer(store, "r", question="scope?", answer="renderer only",
                  asked_by="model", answered_by="human")
    entry = decision_packet(store, "r")["answers"][0]
    assert entry["answered_by"] == "human" and entry["asked_by"] == "model"

def test_the_packet_hash_changes_when_an_answer_is_added(store):
    record_answer(store, "r", question="q1", answer="a1",
                  asked_by="model", answered_by="human")
    h1 = packet_hash(store, "r")
    record_answer(store, "r", question="q2", answer="a2",
                  asked_by="model", answered_by="human")
    assert packet_hash(store, "r") != h1

def test_the_packet_hash_is_stable_for_the_same_answers(store):
    record_answer(store, "r", question="q1", answer="a1",
                  asked_by="model", answered_by="human")
    assert packet_hash(store, "r") == packet_hash(store, "r")

def test_answers_cannot_be_revised_in_place(store):
    """Facts are append-only. A changed mind is a new answer, and the packet
    must show both -- that is what makes it an audit trail."""
    record_answer(store, "r", question="scope?", answer="renderer only",
                  asked_by="model", answered_by="human")
    record_answer(store, "r", question="scope?", answer="renderer and main",
                  asked_by="model", answered_by="human")
    answers = decision_packet(store, "r")["answers"]
    assert len(answers) == 2
    assert [a["answer"] for a in answers] == ["renderer only", "renderer and main"]
```

- [x] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_grill.py -v`
Expected: FAIL, `No module named 'kernel.grill'`.

- [x] **Step 3: Implement**

```python
# v2/kernel/grill.py
"""Grill decision packets as immutable kernel facts.

Conversational UI state may live outside the kernel; the decisions may not.
A changed mind is a new answer appended, never an edit -- the packet is an
audit trail, and an audit trail that can be rewritten is not one.
"""
from __future__ import annotations
from kernel.canon import canonical_bytes, content_hash
from kernel.events import EventKind

def record_answer(store, run_id: str, *, question: str, answer: str,
                  asked_by: str, answered_by: str) -> str:
    return store.append_fact(
        run_id=run_id, kind=EventKind.HUMAN_RULING, actor=answered_by,
        causal_command_id=None,
        payload={"question": question, "answer": answer,
                 "asked_by": asked_by, "answered_by": answered_by})

def decision_packet(store, run_id: str) -> dict:
    answers = [
        {"question": f.payload["question"], "answer": f.payload["answer"],
         "asked_by": f.payload["asked_by"], "answered_by": f.payload["answered_by"],
         "seq": f.seq}
        for f in store.facts_for(run_id) if f.kind == EventKind.HUMAN_RULING
    ]
    return {"run_id": run_id, "answers": answers}

def packet_hash(store, run_id: str) -> str:
    return content_hash(canonical_bytes(decision_packet(store, run_id)))
```

- [x] **Step 4: Run and commit**

Run: `cd v2 && python -m pytest tests/kernel/test_grill.py -v` — expect PASS.

```bash
git add v2/kernel/grill.py v2/tests/kernel/test_grill.py
git commit -m "feat(v2): grill decisions as immutable kernel facts

A changed mind appends a second answer rather than editing the first, so
the packet is an audit trail rather than a current-state record. Records
asked_by and answered_by separately: a model-authored answer must not be
indistinguishable from a human one."
```

---

### Task 4: The single enqueue transaction

Decision 6. A crash between persisting artifacts and enqueueing must leave neither, not one — this is the arrow the whole design calls durable.

**Files:**
- Create: `v2/kernel/enqueue.py`, `v2/tests/kernel/test_enqueue.py`

**Interfaces:**
- Consumes: `put_artifact`, `bundle_hash`, `packet_hash`, `Store`.
- Produces: `enqueue(store, *, run_id, base_repo, base_sha, spec_bytes, plan_bytes, bundle_snapshot, approved_by) -> dict`.

- [x] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_enqueue.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.enqueue import enqueue, NotApproved

@pytest.fixture
def store():
    return Store.open(":memory:", clock=Clock(start_us=1_000))

def _args(**over):
    a = dict(run_id="r", base_repo="o/r", base_sha="a" * 40,
             spec_bytes=b"# spec", plan_bytes=b"# plan",
             bundle_snapshot={"canon_version": 1, "number": 1, "title": "t",
                              "body": "b", "labels": [], "comments": []},
             approved_by="human")
    a.update(over)
    return a

def test_enqueue_persists_artifacts_and_creates_the_run(store):
    r = enqueue(store, **_args())
    assert store.run_version("r") >= 0
    assert r["spec_hash"] and r["plan_hash"] and r["bundle_hash"]

def test_enqueue_writes_the_first_durable_transition(store):
    enqueue(store, **_args())
    kinds = [f.kind for f in store.facts_for("r")]
    assert "run_started" in kinds and "artifact_created" in kinds

def test_a_model_cannot_enqueue(store):
    """The supervised handoff. The front end holds no authority: a human
    inspects the bundle and explicitly enqueues it."""
    with pytest.raises(NotApproved, match="human"):
        enqueue(store, **_args(approved_by="model"))

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
    assert store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0

def test_enqueue_is_idempotent_on_the_same_run_id(store):
    a = enqueue(store, **_args())
    b = enqueue(store, **_args())
    assert a["spec_hash"] == b["spec_hash"]
    assert store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
```

- [x] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_enqueue.py -v`
Expected: FAIL, `No module named 'kernel.enqueue'`.

- [x] **Step 3: Implement**

```python
# v2/kernel/enqueue.py
"""The single transaction joining artifact persistence, enqueue and the first
durable transition.

A crash between the three must leave none of them. A run without its inputs
cannot be replayed; artifacts without a run are unreferenced. SQLite gives us
one transaction, so all three go inside it.
"""
from __future__ import annotations
from kernel.artifacts import put_artifact
from kernel.bundle import bundle_hash
from kernel.events import EventKind

class NotApproved(Exception):
    """Enqueue attempted without an explicit human approval."""

def _create_run_and_transition(store, run_id, base_repo, base_sha,
                               spec_hash, plan_hash, bhash, approved_by):
    store.create_run(run_id=run_id, base_repo=base_repo, base_sha=base_sha)
    store.append_fact(run_id=run_id, kind=EventKind.RUN_STARTED, actor=approved_by,
                      causal_command_id=None,
                      payload={"base_sha": base_sha, "state": "queued"})
    for kind, h in (("spec", spec_hash), ("plan", plan_hash), ("bundle", bhash)):
        store.append_fact(run_id=run_id, kind=EventKind.ARTIFACT_CREATED,
                          actor=approved_by, causal_command_id=None,
                          payload={"artifact_kind": kind, "artifact_hash": h})

def enqueue(store, *, run_id, base_repo, base_sha, spec_bytes, plan_bytes,
            bundle_snapshot, approved_by) -> dict:
    if approved_by != "human":
        raise NotApproved(
            "enqueue requires explicit human approval; the front end holds no authority")

    if store.run_exists(run_id):
        return store.enqueue_result(run_id)

    with store.transaction():
        spec_hash = put_artifact(store, spec_bytes)
        plan_hash = put_artifact(store, plan_bytes)
        bhash = bundle_hash(bundle_snapshot)
        _create_run_and_transition(store, run_id, base_repo, base_sha,
                                   spec_hash, plan_hash, bhash, approved_by)
    return {"run_id": run_id, "spec_hash": spec_hash,
            "plan_hash": plan_hash, "bundle_hash": bhash}
```

Add to `v2/kernel/store.py` (the `contextmanager` import goes at module level, beside the existing `import json, sqlite3`):

```python
# module level:
from contextlib import contextmanager

# on Store:
    @contextmanager
    def transaction(self):
        """One explicit transaction. Store.open uses isolation_level=None, so
        autocommit is on and BEGIN must be issued by hand -- without this the
        three writes would commit independently and a crash could leave a run
        without its inputs."""
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def run_exists(self, run_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is not None

    def enqueue_result(self, run_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT payload_json FROM facts WHERE run_id = ? AND kind = ?",
            (run_id, "artifact_created")).fetchall()
        payloads = [json.loads(r[0]) for r in rows]
        by_kind = {p["artifact_kind"]: p["artifact_hash"] for p in payloads}
        return {"run_id": run_id, "spec_hash": by_kind.get("spec"),
                "plan_hash": by_kind.get("plan"), "bundle_hash": by_kind.get("bundle")}
```

- [x] **Step 4: Run the whole suite**

Run: `cd v2 && python -m pytest tests/ -v`
Expected: every kernel and execution test passes.

- [x] **Step 5: Mutation-test the transaction**

```bash
git add v2/kernel/enqueue.py v2/kernel/store.py v2/tests/kernel/test_enqueue.py
git commit -m "feat(v2): single enqueue transaction with human-approval gate"
```

```bash
# Mutation: drop the transaction, so the writes commit independently.
# test_a_failure_midway_leaves_nothing must go red.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/enqueue.py"); t = p.read_text()
t = t.replace("    with store.transaction():\n", "    if True:\n")
p.write_text(t)
EOF
cd v2 && python -m pytest tests/kernel/test_enqueue.py::test_a_failure_midway_leaves_nothing -v
# Expected: FAIL -- artifacts survive the crash.
git checkout v2/kernel/enqueue.py && git status --short
```

- [x] **Step 6: Commit the evidence and the decision record**

Append decision 6 to `docs/design/frozen-bundle.md`, then:

```bash
git add docs/design/frozen-bundle.md
git commit -m "test(v2): mutation evidence for the enqueue transaction

Removing the transaction lets artifacts survive a crash that prevented
the run being created, and the named test goes red. Records decision 6
alongside the other five."
```

---

## Done means

All six frozen-bundle decisions are fixed, each encoded as an executable predicate and recorded in `docs/design/frozen-bundle.md`: the five frozen fields, the canonicalization, relevant change derived from the frozen set, human-only revision authority, an invalidation table where implementation output deliberately does not invalidate spec or plan review, and one transaction joining persistence, enqueue and the first durable transition. Grill answers are append-only facts that record who answered as well as what. A model cannot enqueue. Every decision carries a mutation proving its test binds it.


---

## Executed 2026-08-25 — what this plan got wrong

All four tasks implemented and committed. 320 tests pass. Every decision
carries a mutation that reds its named test, run one at a time against a
committed tree with a dirty-tree abort in the harness.

**Three of the plan's tests asserted `==` where they claimed to prove a
boundary**, all the same shape and all traceable to the same cause — the plan
predates M1-3b:

1. `test_a_model_cannot_enqueue` passed `approved_by="model"` and expected a
   refusal. A model calling `enqueue` passes `"human"`. `approved_by` is gone;
   what enforces the handoff is that `enqueue` is reachable only from the
   operator's path, and a model reaches `propose_enqueue`, which enqueues
   nothing.
2. `test_the_packet_records_who_answered_not_just_what` asserted that a
   model-authored answer must not be indistinguishable from a human one, via
   an `answered_by` parameter the model supplies. `record_answer` split into
   `record_model_question` and `record_human_answer`; the function reached is
   what decides.
3. `assert REVISION_AUTHORITY == "human"` compares a constant to a constant.
   It states decision 4 without enforcing it. Now `propose_revision` and
   `revise_bundle`, with a test asserting the model path has no parameter that
   could reach the other one.

Four smaller corrections:

4. **`is_relevant_change` had to take raw issues, not snapshots.** Given two
   snapshots the answer is `old != new` and the function proves nothing — the
   whole content of decision 3 is that volatile metadata does not count, which
   only shows when something volatile is present to be ignored.
5. **`invalidates` had no behaviour for an unknown kind.** A default of
   `False` makes a typo silently mean "nothing invalidates this" — fail-open.
   It raises.
6. **The packet needed to represent an unanswered question.** The plan's
   paired `record_answer` could not express one at all, so an enqueue over an
   unfinished grill would look complete. Questions and rulings are separate
   facts, `unanswered()` reports the gap, and the kernel refuses the enqueue
   rather than trusting the front end to have checked.
7. **`sorted`, not `set`.** Deduplicating labels during canonicalization would
   make a genuine change invisible. Caught by writing the mutation before
   believing the code.

## Scope note

The four tasks are the kernel-side substrate: snapshot, decisions, grill facts,
enqueue transaction. The conversational front end itself — grilling, the
adversarial review rounds, bundle export to a human-readable form — is
orchestration above this line and is not built here. Nothing in "Done means"
required it, but a reader should not infer from "supervised front end" that a
UI exists.
