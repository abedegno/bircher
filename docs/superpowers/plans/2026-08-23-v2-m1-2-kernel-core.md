# Bircher v2 — Milestone 1, Plan 2: Kernel Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A durable run aggregate, an append-only fact log whose projection rebuilds current state, and content-addressed artifacts whose review verdicts invalidate when any bound input changes.

**Architecture:** SQLite with explicit Python transition functions — no workflow language, no ORM. Facts are immutable rows the database itself refuses to update or delete; current state is a projection over them, checked against the stored aggregate rather than trusted. Artifacts are addressed by SHA-256 over raw bytes, and a verdict binds a five-part tuple so yesterday's approval cannot authorize today's object.

**Tech Stack:** Python 3.11, `sqlite3` from the standard library, pytest.

**Spec:** `docs/design/2026-08-23-v2-kernel-design.md` (branch `v2`, commit `6a2be96`)

## STATUS: COMPLETE (2026-08-25)

All four tasks implemented in `v2/`, 35 tests passing. Every guard mutation-tested; each mutation caught by exactly one test, no collateral failures.

**Two places the plan was wrong, corrected during execution:**

- The canonical form's float check inspected only the top level. A payload is exactly where nested values live, so it now recurses, with its own test.
- `test_ordering_is_by_sequence_not_timestamp` **did not bind**. It froze the clock and appended two facts at the same microsecond — but ties resolve to insertion order anyway, so `ORDER BY observed_at_us` produced identical output and the mutation survived. Rewritten with a clock running *backwards*, so sequence and timestamp order actively disagree; the mutation now reds it.

**One process failure worth recording:** I mutated `projection.py` before committing it, so `git checkout` had nothing to restore and the mutation persisted silently. The corrected rule exists precisely for this — commit first, then mutate, then restore from a known state — and I broke it on an untracked file. Caught by checking rather than assuming the restore worked.

---

## Global Constraints

Copied from the spec's "Decisions that must be right in the first commit". These are the irreversible ones; getting them wrong cannot be retrofitted.

- **Fact, decision and effect are distinct kinds.** Never collapse them into a mutable status row.
- **Stable identity and a defined idempotency-key scope.** Identity semantics cannot be changed later.
- **An approval authorizes a tuple of immutable inputs** — never a filename, branch name, issue number, or "latest".
- **Every event carries its own schema version and mechanism version.** Stored events must never silently acquire new meanings when code changes.
- **UTC instants only**, stored as integer microseconds since the Unix epoch. **Integer minor units for money and tokens.** Ambiguous historical numbers cannot be repaired.
- **Hashing is over raw bytes, or over a precisely versioned canonical form.** Never an informal serialization whose behaviour can drift.
- **Database engine is explicitly reversible** (spec, "Reversible, and not worth arguing about now"). SQLite is chosen because Milestone 1 has one sequential runner and the deferred list says parallelism is not needed yet. Confine every SQLite-specific construct to `store.py` so the open question stays open.

**Decisions this plan does NOT make:** expected-version CAS and persist-before-execute belong to M1-3 (typed commands and the effect journal). This plan must not add a mutating-command path, because doing so without CAS would establish the wrong semantics in the first commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `v2/kernel/ids.py` | Stable identity and time. Injectable clock/ID source so tests are deterministic. |
| `v2/kernel/canon.py` | Versioned canonical form and SHA-256 hashing. |
| `v2/kernel/schema.sql` | Tables plus the triggers that make facts append-only. |
| `v2/kernel/store.py` | The only module that touches SQLite. Connection, migration, append, read. |
| `v2/kernel/events.py` | Event kinds, schema versions, payload construction. |
| `v2/kernel/projection.py` | Rebuild run state from facts. |
| `v2/kernel/artifacts.py` | Content-addressed artifact store and the verdict binding tuple. |
| `v2/tests/kernel/` | One test module per source module above. |

---

### Task 1: Identity, time, and the canonical form

Everything else depends on these, and all three are irreversible. Deterministic injection matters: a test that cannot fix time or IDs cannot assert on a hash.

**Files:**
- Create: `v2/kernel/ids.py`, `v2/kernel/canon.py`, `v2/tests/kernel/test_ids.py`, `v2/tests/kernel/test_canon.py`

**Interfaces:**
- Produces: `new_id(prefix: str) -> str`, `now_us() -> int`, `Clock`, `canonical_bytes(obj) -> bytes`, `content_hash(data: bytes) -> str`, `CANON_VERSION: int`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_ids.py
import re, pytest
from kernel.ids import new_id, now_us, Clock

def test_ids_are_prefixed_and_unique():
    a, b = new_id("run"), new_id("run")
    assert a.startswith("run_") and b.startswith("run_")
    assert a != b

def test_id_shape_is_stable():
    """Identity semantics are irreversible; pin the shape so a later change is
    a visible test failure rather than a silent integration break."""
    assert re.fullmatch(r"run_[0-9a-f]{32}", new_id("run"))

def test_now_us_is_integer_microseconds_utc():
    t = now_us()
    assert isinstance(t, int)
    assert t > 1_700_000_000_000_000  # microseconds, not seconds or millis

def test_clock_is_injectable_and_monotonic_per_call():
    c = Clock(start_us=1_000_000, step_us=5)
    assert c.now_us() == 1_000_000
    assert c.now_us() == 1_000_005
```

```python
# v2/tests/kernel/test_canon.py
import pytest
from kernel.canon import canonical_bytes, content_hash, CANON_VERSION

def test_key_order_does_not_change_the_hash():
    assert content_hash(canonical_bytes({"a": 1, "b": 2})) == \
           content_hash(canonical_bytes({"b": 2, "a": 1}))

def test_different_values_change_the_hash():
    assert content_hash(canonical_bytes({"a": 1})) != content_hash(canonical_bytes({"a": 2}))

def test_hash_is_sha256_hex():
    h = content_hash(b"abc")
    assert h == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

def test_canonical_form_rejects_types_whose_encoding_could_drift():
    """floats and datetimes have no single stable textual form -- refuse them
    rather than hash something that may encode differently later."""
    with pytest.raises(TypeError):
        canonical_bytes({"x": 1.5})

def test_canon_version_is_recorded():
    assert isinstance(CANON_VERSION, int) and CANON_VERSION >= 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/ -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kernel'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/ids.py
"""Stable identity and UTC time.

Both are irreversible decisions (spec, first-commit table): integrations
depend on identity semantics, and ambiguous historical instants cannot be
repaired. Time is integer microseconds since the Unix epoch -- no timezone,
no float, sortable as an integer.
"""
import time, uuid

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

def now_us() -> int:
    return time.time_ns() // 1_000

class Clock:
    """Injectable clock. Production passes `now_us`; tests pass this."""
    def __init__(self, start_us: int, step_us: int = 1) -> None:
        self._t, self._step = start_us, step_us
    def now_us(self) -> int:
        t = self._t
        self._t += self._step
        return t
```

```python
# v2/kernel/canon.py
"""Versioned canonical form and hashing.

The spec requires hashing over raw bytes or a precisely versioned canonical
form, never an informal serialization whose behaviour can drift. Floats and
datetimes are refused: both have encodings that vary across versions and
platforms, and a hash that changes silently is worse than one that fails.
"""
import hashlib, json

CANON_VERSION = 1

def _reject(o):
    raise TypeError(f"{type(o).__name__} has no stable canonical encoding; "
                    "convert to int, str or bool before hashing")

def canonical_bytes(obj) -> bytes:
    def check(o):
        if isinstance(o, float):
            _reject(o)
        if isinstance(o, dict):
            for v in o.values():
                check(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                check(v)
        return o
    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd v2 && python -m pytest tests/kernel/ -v`
Expected: all PASS.

- [ ] **Step 5: Mutation-test the canonical form**

Commit first, so restoring is `git checkout` against a known state rather than a `/tmp` copy of unknown provenance.

```bash
git add v2/kernel/ids.py v2/kernel/canon.py v2/tests/kernel/
git commit -m "feat(v2): identity, UTC time, versioned canonical form"
```

Now break exactly one thing and confirm the named test goes red:

```bash
# Mutation: drop key-order stability.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/canon.py")
p.write_text(p.read_text().replace("sort_keys=True, ", ""))
EOF
cd v2 && python -m pytest tests/kernel/test_canon.py::test_key_order_does_not_change_the_hash -v
# Expected: FAIL. If it passes, the test does not bind and must be rewritten.
git checkout v2/kernel/canon.py && git status --short   # expect clean
```

Record the mutation and its result in the commit message of step 6. One mutation at a time; prove each applied before believing its result; a collection error is an invalid mutation, never a survival.

- [ ] **Step 6: Commit the mutation evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for the canonical form

Removing sort_keys from canonical_bytes turns
test_key_order_does_not_change_the_hash red, so that test binds the
property it names rather than passing incidentally."
```

---

### Task 2: The append-only fact log

The spec's first irreversible decision: fact, decision and effect are distinct, and collapsing them into mutable status rows makes audit and replay guesswork. Append-only is enforced by the database, not by convention — a rule that lives only in application code is a rule the next caller can forget.

**Files:**
- Create: `v2/kernel/schema.sql`, `v2/kernel/store.py`, `v2/kernel/events.py`, `v2/tests/kernel/test_store.py`

**Interfaces:**
- Consumes: `new_id`, `Clock`, `canonical_bytes`, `content_hash` (Task 1).
- Produces: `Store.open(path) -> Store`, `Store.append_fact(**kw) -> str`, `Store.facts_for(run_id) -> list[Fact]`, `Store.put_blob(content_hash, data)`, `EventKind`, `SCHEMA_VERSIONS`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_store.py
import sqlite3, pytest
from kernel.store import Store
from kernel.events import EventKind, SCHEMA_VERSIONS
from kernel.ids import Clock

@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "k.db", clock=Clock(start_us=1_000_000))

def test_append_returns_a_stable_id_and_reads_back(store):
    fid = store.append_fact(run_id="run_1", kind=EventKind.RUN_STARTED,
                            actor="kernel", causal_command_id=None, payload={"base_sha": "abc"})
    facts = store.facts_for("run_1")
    assert [f.id for f in facts] == [fid]
    assert facts[0].payload == {"base_sha": "abc"}

def test_every_fact_carries_schema_and_mechanism_version(store):
    store.append_fact(run_id="run_1", kind=EventKind.RUN_STARTED,
                      actor="kernel", causal_command_id=None, payload={})
    f = store.facts_for("run_1")[0]
    assert f.schema_version == SCHEMA_VERSIONS[EventKind.RUN_STARTED]
    assert f.mechanism_version >= 1

def test_facts_cannot_be_updated(store):
    """Enforced by the database. An application-level rule is one the next
    caller can forget; a trigger is not."""
    store.append_fact(run_id="run_1", kind=EventKind.RUN_STARTED,
                      actor="kernel", causal_command_id=None, payload={})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("UPDATE facts SET actor='tamper'")

def test_facts_cannot_be_deleted(store):
    store.append_fact(run_id="run_1", kind=EventKind.RUN_STARTED,
                      actor="kernel", causal_command_id=None, payload={})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM facts")

def test_ordering_is_total_and_by_sequence_not_timestamp(store):
    """Two facts can share a microsecond. Ordering must not depend on the
    clock, or replay order becomes nondeterministic."""
    c = Clock(start_us=5_000, step_us=0)
    s = Store.open(":memory:", clock=c)
    a = s.append_fact(run_id="r", kind=EventKind.RUN_STARTED, actor="k",
                      causal_command_id=None, payload={})
    b = s.append_fact(run_id="r", kind=EventKind.RUN_STARTED, actor="k",
                      causal_command_id=None, payload={})
    seqs = [f.seq for f in s.facts_for("r")]
    assert seqs == sorted(seqs) and len(set(seqs)) == 2
    assert [f.id for f in s.facts_for("r")] == [a, b]

def test_unknown_event_kind_is_refused(store):
    """An event whose schema version is not declared cannot be stored: it
    would acquire meaning later, which the spec forbids."""
    with pytest.raises(KeyError):
        store.append_fact(run_id="r", kind="invented_kind", actor="k",
                          causal_command_id=None, payload={})
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_store.py -v`
Expected: FAIL, `No module named 'kernel.store'`.

- [ ] **Step 3: Write the schema**

```sql
-- v2/kernel/schema.sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS facts (
  seq                INTEGER PRIMARY KEY AUTOINCREMENT,  -- total order, clock-independent
  id                 TEXT    NOT NULL UNIQUE,
  run_id             TEXT    NOT NULL,
  kind               TEXT    NOT NULL,
  schema_version     INTEGER NOT NULL,
  mechanism_version  INTEGER NOT NULL,
  causal_command_id  TEXT,
  actor              TEXT    NOT NULL,
  observed_at_us     INTEGER NOT NULL,   -- UTC microseconds since epoch
  payload_json       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_by_run ON facts(run_id, seq);

-- Append-only, enforced by the database rather than by convention.
CREATE TRIGGER IF NOT EXISTS facts_no_update BEFORE UPDATE ON facts
BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS facts_no_delete BEFORE DELETE ON facts
BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;

CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  state         TEXT NOT NULL,
  base_repo     TEXT NOT NULL,
  base_sha      TEXT NOT NULL,
  created_at_us INTEGER NOT NULL
);
```

- [ ] **Step 4: Implement the store and event kinds**

```python
# v2/kernel/events.py
"""Event kinds and their schema versions.

A stored event must never acquire a new meaning when code changes, so every
kind declares its version here and the store refuses any kind absent from
this table.
"""
class EventKind:
    RUN_STARTED       = "run_started"
    COMMAND_REQUESTED = "command_requested"
    COMMAND_ACCEPTED  = "command_accepted"
    COMMAND_REJECTED  = "command_rejected"
    ARTIFACT_CREATED  = "artifact_created"
    REVIEW_VERDICT    = "review_verdict"
    TRANSITION        = "transition_performed"
    OBSERVATION       = "external_observation"
    HUMAN_RULING      = "human_ruling"

SCHEMA_VERSIONS = {
    EventKind.RUN_STARTED: 1,
    EventKind.COMMAND_REQUESTED: 1,
    EventKind.COMMAND_ACCEPTED: 1,
    EventKind.COMMAND_REJECTED: 1,
    EventKind.ARTIFACT_CREATED: 1,
    EventKind.REVIEW_VERDICT: 1,
    EventKind.TRANSITION: 1,
    EventKind.OBSERVATION: 1,
    EventKind.HUMAN_RULING: 1,
}

MECHANISM_VERSION = 1
```

```python
# v2/kernel/store.py
"""The only module that touches SQLite.

The engine is a reversible decision (spec); keeping every SQLite-specific
construct here is what keeps it reversible.
"""
from __future__ import annotations
import json, sqlite3
from dataclasses import dataclass
from pathlib import Path
from kernel.canon import canonical_bytes
from kernel.events import SCHEMA_VERSIONS, MECHANISM_VERSION
from kernel.ids import new_id, now_us

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

@dataclass(frozen=True)
class Fact:
    seq: int
    id: str
    run_id: str
    kind: str
    schema_version: int
    mechanism_version: int
    causal_command_id: str | None
    actor: str
    observed_at_us: int
    payload: dict

class Store:
    def __init__(self, conn: sqlite3.Connection, clock) -> None:
        self._conn, self._clock = conn, clock

    @classmethod
    def open(cls, path, clock=None) -> "Store":
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.executescript(_SCHEMA)
        return cls(conn, clock or type("C", (), {"now_us": staticmethod(now_us)}))

    def append_fact(self, *, run_id: str, kind: str, actor: str,
                    causal_command_id: str | None, payload: dict) -> str:
        schema_version = SCHEMA_VERSIONS[kind]   # KeyError on an undeclared kind
        fid = new_id("fact")
        self._conn.execute(
            "INSERT INTO facts (id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (fid, run_id, kind, schema_version, MECHANISM_VERSION, causal_command_id,
             actor, self._clock.now_us(), canonical_bytes(payload).decode("utf-8")),
        )
        return fid

    def put_blob(self, content_hash: str, data: bytes) -> None:
        """Insert an immutable blob. Idempotent: the same bytes hash the same,
        so a repeated write is a no-op rather than a conflict."""
        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts (hash, bytes) VALUES (?,?)",
            (content_hash, data))

    def facts_for(self, run_id: str) -> list[Fact]:
        rows = self._conn.execute(
            "SELECT seq, id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json"
            " FROM facts WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [Fact(*r[:-1], payload=json.loads(r[-1])) for r in rows]
```

- [ ] **Step 5: Run the tests**

Run: `cd v2 && python -m pytest tests/kernel/test_store.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit, then mutation-test the append-only guarantee**

```bash
git add v2/kernel/schema.sql v2/kernel/store.py v2/kernel/events.py v2/tests/kernel/test_store.py
git commit -m "feat(v2): append-only fact log with database-enforced immutability"
```

```bash
# Mutation: remove the UPDATE trigger.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/schema.sql"); t = p.read_text()
p.write_text(t.replace("CREATE TRIGGER IF NOT EXISTS facts_no_update BEFORE UPDATE ON facts\nBEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;", ""))
EOF
cd v2 && python -m pytest tests/kernel/test_store.py::test_facts_cannot_be_updated -v
# Expected: FAIL (the UPDATE now succeeds). If it PASSES, the test is not
# binding immutability -- most likely it asserts on the wrong exception.
git checkout v2/kernel/schema.sql && git status --short   # expect clean
```

```bash
git commit --allow-empty -m "test(v2): mutation evidence for append-only facts

Dropping the facts_no_update trigger turns test_facts_cannot_be_updated
red, so immutability is bound by the database rather than by convention."
```

---

### Task 3: The projection

Current state is rebuildable from facts. The spec does not require full event sourcing everywhere; it requires that immutable facts and mutable derived state be distinguishable — which is only true if the projection is checked against the aggregate rather than assumed to match it.

**Files:**
- Create: `v2/kernel/projection.py`, `v2/tests/kernel/test_projection.py`

**Interfaces:**
- Consumes: `Store`, `Fact`, `EventKind`.
- Produces: `project(facts: list[Fact]) -> RunState`, `RunState(run_id, state, base_sha, artifacts, verdicts)`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_projection.py
import pytest
from kernel.store import Store
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.projection import project

@pytest.fixture
def store():
    return Store.open(":memory:", clock=Clock(start_us=1_000))

def _start(s, run="r"):
    s.append_fact(run_id=run, kind=EventKind.RUN_STARTED, actor="kernel",
                  causal_command_id=None, payload={"base_sha": "aaa", "state": "queued"})

def test_projection_rebuilds_state_from_facts_alone(store):
    _start(store)
    store.append_fact(run_id="r", kind=EventKind.TRANSITION, actor="kernel",
                      causal_command_id="cmd_1", payload={"to": "implementing"})
    assert project(store.facts_for("r")).state == "implementing"

def test_projection_is_deterministic_and_order_dependent(store):
    _start(store)
    for to in ("implementing", "reviewing", "merged"):
        store.append_fact(run_id="r", kind=EventKind.TRANSITION, actor="kernel",
                          causal_command_id="c", payload={"to": to})
    facts = store.facts_for("r")
    assert project(facts).state == "merged"
    # Replaying a prefix must give the earlier state, or ordering is not real.
    assert project(facts[:-1]).state == "reviewing"

def test_projection_ignores_unknown_kinds_without_losing_known_ones(store):
    """Forward compatibility: a fact written by a newer mechanism version must
    not break replay of the facts this version understands."""
    _start(store)
    store.append_fact(run_id="r", kind=EventKind.OBSERVATION, actor="github",
                      causal_command_id=None, payload={"anything": 1})
    store.append_fact(run_id="r", kind=EventKind.TRANSITION, actor="kernel",
                      causal_command_id="c", payload={"to": "implementing"})
    assert project(store.facts_for("r")).state == "implementing"

def test_empty_history_projects_to_no_run(store):
    assert project([]) is None
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_projection.py -v`
Expected: FAIL, `No module named 'kernel.projection'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/projection.py
"""Rebuild current run state from facts.

Facts are the truth; this is derived. Unknown kinds are skipped rather than
raising, so a fact written by a newer mechanism version does not break replay
of the ones this version understands.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from kernel.events import EventKind

@dataclass
class RunState:
    run_id: str
    state: str
    base_sha: str
    artifacts: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)

def project(facts) -> RunState | None:
    st: RunState | None = None
    for f in facts:
        if f.kind == EventKind.RUN_STARTED:
            st = RunState(run_id=f.run_id, state=f.payload.get("state", "queued"),
                          base_sha=f.payload["base_sha"])
        elif st is None:
            continue
        elif f.kind == EventKind.TRANSITION:
            st.state = f.payload["to"]
        elif f.kind == EventKind.ARTIFACT_CREATED:
            st.artifacts.append(f.payload["artifact_hash"])
        elif f.kind == EventKind.REVIEW_VERDICT:
            st.verdicts.append(f.payload)
    return st
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest tests/kernel/test_projection.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit with mutation evidence**

```bash
git add v2/kernel/projection.py v2/tests/kernel/test_projection.py
git commit -m "feat(v2): projection rebuilding run state from facts"
```

```bash
# Mutation: make the projection order-independent by sorting transitions.
# The prefix assertion must catch it.
cd v2 && python -m pytest tests/kernel/test_projection.py::test_projection_is_deterministic_and_order_dependent -v
```

Break `project` by applying only the *first* transition (`if st.state == "queued": st.state = f.payload["to"]`), confirm the test fails, then `git checkout v2/kernel/projection.py`.

```bash
git commit --allow-empty -m "test(v2): mutation evidence for projection ordering

Applying only the first transition turns the prefix assertion red, so
the test binds replay order rather than merely the final state."
```

---

### Task 4: Content-addressed artifacts and verdict invalidation

The spec calls this "the minimum mechanism preventing yesterday's approval from authorizing today's object", and names v1's failure to have it as the reason v2 exists.

**Files:**
- Create: `v2/kernel/artifacts.py`, `v2/tests/kernel/test_artifacts.py`
- Modify: `v2/kernel/schema.sql` (add `artifacts` and `verdicts` tables)

**Interfaces:**
- Consumes: `content_hash`, `canonical_bytes`, `Store`.
- Produces: `put_artifact(store, data: bytes) -> str`, `VerdictBinding(artifact_hash, base_sha, context_bundle_hash, reviewer_identity, policy_version)`, `binding_hash(b) -> str`, `is_valid(stored, current) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_artifacts.py
import pytest
from kernel.artifacts import VerdictBinding, binding_hash, is_valid

def _b(**over):
    base = dict(artifact_hash="a" * 64, base_sha="b" * 40,
                context_bundle_hash="c" * 64, reviewer_identity="codex@v1",
                policy_version=3)
    base.update(over)
    return VerdictBinding(**base)

def test_identical_bindings_are_valid():
    assert is_valid(_b(), _b())

@pytest.mark.parametrize("field,value", [
    ("artifact_hash", "d" * 64),
    ("base_sha", "e" * 40),
    ("context_bundle_hash", "f" * 64),
    ("reviewer_identity", "someone-else@v1"),
    ("policy_version", 4),
])
def test_changing_any_bound_input_invalidates_the_verdict(field, value):
    """The spec binds five inputs. Each is tested separately: a single
    combined assertion would pass while four of the five went unchecked."""
    assert not is_valid(_b(), _b(**{field: value})), f"{field} did not invalidate"

def test_binding_hash_is_stable_across_field_order():
    assert binding_hash(_b()) == binding_hash(_b())

def test_binding_does_not_accept_a_mutable_reference():
    """An approval authorizes a tuple of immutable inputs -- never a filename,
    branch name, issue number or 'latest' (spec, first-commit table)."""
    with pytest.raises(ValueError, match="immutable"):
        VerdictBinding(artifact_hash="refs/heads/main", base_sha="b" * 40,
                       context_bundle_hash="c" * 64, reviewer_identity="r",
                       policy_version=1)
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_artifacts.py -v`
Expected: FAIL, `No module named 'kernel.artifacts'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/artifacts.py
"""Content-addressed artifacts and the verdict binding tuple.

A verdict binds five immutable inputs. Changing any one invalidates it --
this is the minimum mechanism preventing yesterday's approval from
authorizing today's object, and the property v1 intends but does not have.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from kernel.canon import canonical_bytes, content_hash

_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

@dataclass(frozen=True)
class VerdictBinding:
    artifact_hash: str
    base_sha: str
    context_bundle_hash: str
    reviewer_identity: str
    policy_version: int

    def __post_init__(self) -> None:
        if not _HEX64.match(self.artifact_hash):
            raise ValueError("artifact_hash must be an immutable content hash, "
                             "not a name, branch or reference")
        if not _HEX40.match(self.base_sha):
            raise ValueError("base_sha must be an immutable commit id")
        if not _HEX64.match(self.context_bundle_hash):
            raise ValueError("context_bundle_hash must be an immutable content hash")

def binding_hash(b: VerdictBinding) -> str:
    return content_hash(canonical_bytes(asdict(b)))

def is_valid(stored: VerdictBinding, current: VerdictBinding) -> bool:
    return binding_hash(stored) == binding_hash(current)

def put_artifact(store, data: bytes) -> str:
    h = content_hash(data)
    store.put_blob(h, data)   # store.py owns every SQLite construct
    return h
```

Add to `v2/kernel/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS artifacts (
  hash  TEXT PRIMARY KEY,
  bytes BLOB NOT NULL
);
CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest tests/kernel/ -v`
Expected: all PASS, including the five parametrized invalidation cases.

- [ ] **Step 5: Mutation-test the invalidation, one field at a time**

This is the guard the whole design rests on, so mutate it precisely rather than generally. Commit first.

```bash
git add v2/kernel/artifacts.py v2/kernel/schema.sql v2/tests/kernel/test_artifacts.py
git commit -m "feat(v2): content-addressed artifacts and verdict binding"
```

```bash
# Mutation: drop reviewer_identity from the binding hash. Exactly one
# parametrized case must go red -- if more go red, the mutation was wrong;
# if none do, the binding does not cover the field it claims to.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/artifacts.py"); t = p.read_text()
p.write_text(t.replace("return content_hash(canonical_bytes(asdict(b)))",
    "d = asdict(b); d.pop('reviewer_identity'); return content_hash(canonical_bytes(d))"))
EOF
cd v2 && python -m pytest tests/kernel/test_artifacts.py -v
# Expected: exactly test_changing_any_bound_input_invalidates_the_verdict[reviewer_identity-someone-else@v1] FAILS.
git checkout v2/kernel/artifacts.py && git status --short   # expect clean
```

- [ ] **Step 6: Commit the evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for verdict invalidation

Dropping reviewer_identity from the binding hash turns exactly the
reviewer_identity parametrization red and no other -- so each of the
five bound inputs is independently checked rather than covered by a
single assertion that could pass with four unverified."
```

---

## Done means

A run's state is rebuildable from facts alone and matches the stored aggregate; the database refuses to update or delete a fact or an artifact; every event carries its own schema and mechanism version and an undeclared kind cannot be stored; a verdict binds five immutable inputs and changing any one of them invalidates it, with a per-field mutation proving each is independently checked; and no mutating-command path exists yet, because that requires the CAS semantics M1-3 introduces.
