# Bircher v2 — Milestone 1, Plan 3: Typed Commands and the Effect Journal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seven typed commands that mutate only under expected-version compare-and-swap, decisions that arrive as data and are revalidated against the hashes they claim, and an effect journal that persists intent before any externally visible mutation and refuses one issued by a superseded generation.

**Architecture:** Ownership of a run is acquired by CAS and yields a monotonically increasing fence generation. Every command carries the aggregate version it was derived from; a command derived from version 12 cannot mutate version 15. Every externally visible mutation is journalled by effect class before it is attempted, bound to the generation that requested it, and reconciled before any retry.

**Tech Stack:** Python 3.11, `sqlite3` from the standard library, pytest.

**Spec:** `docs/design/2026-08-23-v2-kernel-design.md` (branch `v2`, commit `6a2be96`)

## STATUS: COMPLETE (2026-08-25)

All five tasks implemented in `v2/`, 73 tests passing across the kernel. Every guard mutation-tested.

**The ownership test took three attempts to bind, and the plan predicted only the first failure.**

1. *Sampled.* Acquire twice sequentially, assert distinct generations. CAS and read-then-write both pass — no contention single-threaded. The plan anticipated this and said to add a forced-interleaving variant rather than declare the guard bound.
2. *Hooked the wrong point.* Intercepting the `SELECT` fires **before** the victim reads, so the victim picks up the interloper's value and loses nothing; the mutation stayed green. The window is between the read and the write, so the hook must fire on the **write**.
3. *Needed a different assertion.* Under read-then-write the victim writes `old+1` and clobbers the interloper, leaving both holding the **same number**. "No lost update" is therefore expressed as *two owners can never hold the same generation* — which is what the spec requires.

Also: `sqlite3.Connection.execute` is read-only and cannot be monkeypatched, so the connection is wrapped in a delegating proxy; `Store._conn` is an ordinary attribute.

**Mutations run, each caught by its named test:** version predicate dropped; generation check dropped; replay short-circuit removed; `head_git_sha` dropped from `BOUND_INPUTS` (exactly its own parametrization); journalling moved after execution; effect generation fence removed (both the refusal test *and* the no-row test, which is why that mutation is checked against two); halt made global rather than per-run.

---

## Global Constraints

- **Expected-version compare-and-swap on every mutating command.** Irreversible (spec, first-commit table): without it, stale decisions silently apply to newer state.
- **Persist-before-execute for irreversible effects.** Irreversible: retrofitting once calls are scattered through coordinators is expensive.
- **Every journalled mutation is a generation-fenced resource.** Uniform rule. Any exception must be classified explicitly with its reason, never omitted — an earlier spec revision listed five fenced resources against eight journal classes and silently lost PR merge, issue mutation, recovery writes, credential lifecycle and session control.
- **Binding results alone is insufficient.** Every accepted result *and every effect request* binds to the generation. Fencing returned data while leaving an attempt's external effects unfenced misses the actual failure.
- **A result from an older generation is observation-only and carries no reusable write capability.**
- **`accept` from the judgement layer means "no unresolved blockers for the pinned review bundle". It does not mean merge.** Only the kernel authorizes a merge.
- **Reads are not journalled; every externally visible mutation is.**
- **UTC integer microseconds; integer minor units for money and tokens.** Carried from M1-2.

**Depends on:** M1-2 (`Store`, `Fact`, `EventKind`, `project`, `VerdictBinding`, `content_hash`). This plan adds event kinds to `SCHEMA_VERSIONS`; it never redefines an existing one, because a stored event must not acquire a new meaning.

**Not in this plan:** the real GitHub adapter. Effects are journalled and dispatched through an injected `EffectExecutor` protocol; M1-4 supplies the implementation that talks to GitHub from the kernel's credential domain. A fake executor is sufficient — and better — for proving the journal's ordering guarantees.

---

## File Structure

| File | Responsibility |
|---|---|
| `v2/kernel/ownership.py` | CAS ownership acquisition and the fence generation. |
| `v2/kernel/commands.py` | The seven typed commands, expected-version CAS, idempotency. |
| `v2/kernel/decisions.py` | Decision-as-data: schema validation and hash revalidation. |
| `v2/kernel/effects.py` | Effect classes, journal, persist-before-execute, reconciliation. |
| `v2/kernel/schema.sql` | *(modify)* `runs.version`, `runs.owner_generation`, `commands`, `effects`. |
| `v2/kernel/events.py` | *(modify)* new event kinds and their schema versions. |
| `v2/tests/kernel/` | One test module per source module above. |

---

### Task 1: Ownership acquisition and the fence generation

"Ownership recorded" is not exclusion. Acquisition must be a compare-and-swap, and dispatch must be tied to the generation it acquired.

**Files:**
- Create: `v2/kernel/ownership.py`, `v2/tests/kernel/test_ownership.py`
- Modify: `v2/kernel/schema.sql`

**Interfaces:**
- Produces: `acquire(store, run_id, owner) -> int` (the new generation), `current_generation(store, run_id) -> int`, `OwnershipLost`.

- [ ] **Step 1: Extend the schema**

```sql
-- append to v2/kernel/schema.sql
ALTER TABLE runs ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN owner_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN owner TEXT;
```

Note: `ALTER TABLE ... ADD COLUMN` is not idempotent under `executescript`. Guard the migration in `store.py` by checking `PRAGMA table_info(runs)` before applying, so `Store.open` stays safe to call twice.

- [ ] **Step 2: Write the failing tests**

```python
# v2/tests/kernel/test_ownership.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.ownership import acquire, current_generation, OwnershipLost

@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s

def test_first_acquisition_yields_generation_one(store):
    assert acquire(store, "r", owner="attempt_1") == 1

def test_generations_increase_monotonically(store):
    gens = [acquire(store, "r", owner=f"attempt_{i}") for i in range(3)]
    assert gens == [1, 2, 3]

def test_acquisition_is_atomic_not_merely_recorded(store):
    """Two acquisitions must not both believe they hold the same generation.
    'Ownership recorded' is not exclusion."""
    g1 = acquire(store, "r", owner="a")
    g2 = acquire(store, "r", owner="b")
    assert g1 != g2
    assert current_generation(store, "r") == g2

def test_superseded_generation_is_detectable(store):
    g_old = acquire(store, "r", owner="a")
    acquire(store, "r", owner="b")
    assert g_old < current_generation(store, "r")

def test_acquire_records_a_fact(store):
    acquire(store, "r", owner="a")
    kinds = [f.kind for f in store.facts_for("r")]
    assert "ownership_acquired" in kinds
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_ownership.py -v`
Expected: FAIL, `No module named 'kernel.ownership'`.

- [ ] **Step 4: Implement**

```python
# v2/kernel/ownership.py
"""Atomic ownership acquisition with a monotonic fence generation.

Acquisition is a compare-and-swap, not a write: two attempts must never
believe they hold the same generation. Dispatch is tied to the generation
acquired here, and every effect request is checked against it.
"""
from kernel.events import EventKind

class OwnershipLost(Exception):
    """Raised when an operation is attempted under a superseded generation."""

def acquire(store, run_id: str, owner: str) -> int:
    """Increment the fence generation atomically and return the new value.

    The UPDATE is the CAS: it reads and writes owner_generation in one
    statement, so concurrent callers serialise and each observes a distinct
    generation. A read-then-write would let both read the same value.
    """
    cur = store._conn.execute(
        "UPDATE runs SET owner_generation = owner_generation + 1, owner = ?"
        " WHERE run_id = ? RETURNING owner_generation", (owner, run_id))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"no such run: {run_id}")
    generation = row[0]
    store.append_fact(run_id=run_id, kind=EventKind.OWNERSHIP_ACQUIRED,
                      actor=owner, causal_command_id=None,
                      payload={"generation": generation, "owner": owner})
    return generation

def current_generation(store, run_id: str) -> int:
    row = store._conn.execute(
        "SELECT owner_generation FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such run: {run_id}")
    return row[0]
```

Add to `v2/kernel/events.py`:

```python
    OWNERSHIP_ACQUIRED = "ownership_acquired"
    EFFECT_INTENDED    = "effect_intended"
    EFFECT_CONFIRMED   = "effect_confirmed"
    EFFECT_UNCERTAIN   = "effect_uncertain"
    EFFECT_RECONCILED  = "effect_reconciled"
```

and to `SCHEMA_VERSIONS`, each mapped to `1`.

Add to `v2/kernel/store.py`:

```python
    def create_run(self, *, run_id: str, base_repo: str, base_sha: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, state, base_repo, base_sha, created_at_us)"
            " VALUES (?,?,?,?,?)",
            (run_id, "queued", base_repo, base_sha, self._clock.now_us()))
```

- [ ] **Step 5: Run, then mutation-test the atomicity**

Run: `cd v2 && python -m pytest tests/kernel/test_ownership.py -v` — expect PASS.

```bash
git add v2/kernel/ownership.py v2/kernel/events.py v2/kernel/store.py v2/kernel/schema.sql v2/tests/kernel/test_ownership.py
git commit -m "feat(v2): CAS ownership acquisition with monotonic fence generation"
```

```bash
# Mutation: turn the CAS into read-then-write, the exact defect the design
# forbids. test_acquisition_is_atomic_not_merely_recorded must go red.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/ownership.py"); t = p.read_text()
t = t.replace(
  '''    cur = store._conn.execute(
        "UPDATE runs SET owner_generation = owner_generation + 1, owner = ?"
        " WHERE run_id = ? RETURNING owner_generation", (owner, run_id))
    row = cur.fetchone()''',
  '''    row = store._conn.execute(
        "SELECT owner_generation FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is not None:
        store._conn.execute("UPDATE runs SET owner_generation = ?, owner = ?"
                            " WHERE run_id = ?", (row[0] + 1, owner, run_id))
        row = (row[0] + 1,)''')
p.write_text(t)
EOF
cd v2 && python -m pytest tests/kernel/test_ownership.py -v
git checkout v2/kernel/ownership.py && git status --short
```

**Read the result carefully.** Single-threaded, read-then-write still produces distinct generations, so this mutation may leave the suite green — and that is itself the finding: *the test samples an ordering rather than forcing one*. If it stays green, add a forced-interleaving test using a production-nil hook rather than declaring the guard bound:

```python
def test_concurrent_acquisition_cannot_produce_a_shared_generation(store, monkeypatch):
    """Forces the interleaving instead of hoping for it. -count=N raises the
    odds of catching a race; it guarantees nothing."""
    import kernel.ownership as own
    seen = []
    real = store._conn.execute
    def interleave(sql, *a):
        if sql.startswith("SELECT owner_generation") and not seen:
            seen.append(1)
            own.acquire(store, "r", owner="interloper")   # runs entirely between read and write
        return real(sql, *a)
    monkeypatch.setattr(store._conn, "execute", interleave)
    g = own.acquire(store, "r", owner="victim")
    assert g != 1 or own.current_generation(store, "r") != g, \
        "a competing acquisition produced the same generation"
```

- [ ] **Step 6: Commit the mutation evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for ownership atomicity

Records whether the read-then-write mutation was caught by the plain
test or required the forced-interleaving variant. A sampled ordering is
not a bound guard."
```

---

### Task 2: Typed commands with expected-version CAS and idempotency

**Files:**
- Create: `v2/kernel/commands.py`, `v2/tests/kernel/test_commands.py`
- Modify: `v2/kernel/schema.sql`

**Interfaces:**
- Consumes: `Store`, `acquire`, `current_generation`, `OwnershipLost`.
- Produces: `Command(name, run_id, expected_version, idempotency_key, generation, payload)`, `submit(store, cmd) -> Result`, `StaleVersion`, `COMMAND_NAMES`.

- [ ] **Step 1: Extend the schema**

```sql
CREATE TABLE IF NOT EXISTS commands (
  idempotency_key TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL,
  name            TEXT NOT NULL,
  accepted        INTEGER NOT NULL,
  result_json     TEXT NOT NULL,
  at_us           INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS commands_no_update BEFORE UPDATE ON commands
BEGIN SELECT RAISE(ABORT, 'command results are immutable'); END;
```

- [ ] **Step 2: Write the failing tests**

```python
# v2/tests/kernel/test_commands.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.commands import Command, submit, StaleVersion, COMMAND_NAMES

@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s

def _cmd(store, name="submit_spec", version=0, key="k1", generation=None, **payload):
    return Command(name=name, run_id="r", expected_version=version,
                   idempotency_key=key,
                   generation=generation if generation is not None else acquire(store, "r", "a"),
                   payload=payload or {"spec_sha256": "a" * 64})

def test_the_interface_is_exactly_seven_commands():
    """A narrow interface, not a general one. If this list grows, it is a
    design change and must be argued, not absorbed."""
    assert sorted(COMMAND_NAMES) == sorted([
        "submit_spec", "submit_plan", "record_review", "start_implementation",
        "record_ci_observation", "request_merge", "cancel_run"])

def test_command_at_the_current_version_is_accepted(store):
    assert submit(store, _cmd(store)).accepted

def test_command_derived_from_an_older_version_is_refused(store):
    """A command derived from version 12 cannot mutate version 15."""
    submit(store, _cmd(store, key="k1"))          # bumps version 0 -> 1
    with pytest.raises(StaleVersion):
        submit(store, _cmd(store, version=0, key="k2"))

def test_replaying_an_idempotency_key_returns_the_first_result(store):
    a = submit(store, _cmd(store, key="same"))
    b = submit(store, _cmd(store, key="same"))
    assert a.result == b.result
    assert b.replayed is True

def test_replay_does_not_advance_the_version(store):
    submit(store, _cmd(store, key="same"))
    v = store.run_version("r")
    submit(store, _cmd(store, key="same"))
    assert store.run_version("r") == v, "a replayed command mutated state"

def test_command_from_a_superseded_generation_is_refused(store):
    stale_gen = acquire(store, "r", "a")
    acquire(store, "r", "b")                       # supersedes it
    with pytest.raises(Exception):
        submit(store, _cmd(store, generation=stale_gen, key="k9"))

def test_unknown_command_name_is_refused(store):
    with pytest.raises(ValueError, match="unknown command"):
        submit(store, _cmd(store, name="merge_everything", key="k8"))
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_commands.py -v`
Expected: FAIL, `No module named 'kernel.commands'`.

- [ ] **Step 4: Implement**

```python
# v2/kernel/commands.py
"""The seven typed commands and centralized authorization.

A narrow interface, not a general one. Every command carries the aggregate
version it was derived from and the generation that requested it; both are
checked before anything mutates.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from kernel.events import EventKind
from kernel.ownership import current_generation, OwnershipLost

COMMAND_NAMES = frozenset({
    "submit_spec", "submit_plan", "record_review", "start_implementation",
    "record_ci_observation", "request_merge", "cancel_run",
})

class StaleVersion(Exception):
    """The command was derived from an aggregate version that has since moved."""

@dataclass(frozen=True)
class Command:
    name: str
    run_id: str
    expected_version: int
    idempotency_key: str
    generation: int
    payload: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Result:
    accepted: bool
    result: dict
    replayed: bool = False

def submit(store, cmd: Command) -> Result:
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")

    prior = store.command_result(cmd.idempotency_key)
    if prior is not None:
        # Replay. Returns the original outcome and mutates nothing -- the
        # version must not advance, or a retry would consume a version.
        return Result(accepted=bool(prior["accepted"]), result=prior["result"],
                      replayed=True)

    if cmd.generation != current_generation(store, cmd.run_id):
        raise OwnershipLost(
            f"generation {cmd.generation} superseded; command carries no write capability")

    # The CAS. rowcount 0 means the aggregate moved under us.
    cur = store._conn.execute(
        "UPDATE runs SET version = version + 1 WHERE run_id = ? AND version = ?",
        (cmd.run_id, cmd.expected_version))
    if cur.rowcount == 0:
        store.append_fact(run_id=cmd.run_id, kind=EventKind.COMMAND_REJECTED,
                          actor="kernel", causal_command_id=cmd.idempotency_key,
                          payload={"name": cmd.name, "reason": "stale_version",
                                   "expected_version": cmd.expected_version})
        raise StaleVersion(
            f"{cmd.name} derived from version {cmd.expected_version}, which has moved")

    store.append_fact(run_id=cmd.run_id, kind=EventKind.COMMAND_ACCEPTED,
                      actor="kernel", causal_command_id=cmd.idempotency_key,
                      payload={"name": cmd.name, "generation": cmd.generation,
                               **cmd.payload})
    result = {"name": cmd.name}
    store.record_command(cmd.idempotency_key, cmd.run_id, cmd.name, True, result)
    return Result(accepted=True, result=result)
```

Add to `v2/kernel/store.py`:

```python
    def run_version(self, run_id: str) -> int:
        return self._conn.execute(
            "SELECT version FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]

    def command_result(self, idempotency_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT accepted, result_json FROM commands WHERE idempotency_key = ?",
            (idempotency_key,)).fetchone()
        return None if row is None else {"accepted": row[0], "result": json.loads(row[1])}

    def record_command(self, key: str, run_id: str, name: str,
                       accepted: bool, result: dict) -> None:
        self._conn.execute(
            "INSERT INTO commands (idempotency_key, run_id, name, accepted,"
            " result_json, at_us) VALUES (?,?,?,?,?,?)",
            (key, run_id, name, int(accepted), json.dumps(result, sort_keys=True),
             self._clock.now_us()))
```

- [ ] **Step 5: Run, then mutation-test the CAS**

Run: `cd v2 && python -m pytest tests/kernel/test_commands.py -v` — expect PASS.

```bash
git add v2/kernel/commands.py v2/kernel/store.py v2/kernel/schema.sql v2/tests/kernel/test_commands.py
git commit -m "feat(v2): seven typed commands with expected-version CAS and idempotency"
```

```bash
# Mutation: drop the version predicate, so a stale command applies to newer
# state -- the exact failure the first-commit table names.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/commands.py")
p.write_text(p.read_text().replace(
  '" WHERE run_id = ? AND version = ?",\n        (cmd.run_id, cmd.expected_version))',
  '" WHERE run_id = ?",\n        (cmd.run_id,))'))
EOF
cd v2 && python -m pytest tests/kernel/test_commands.py::test_command_derived_from_an_older_version_is_refused -v
# Expected: FAIL -- the stale command is now accepted.
git checkout v2/kernel/commands.py && git status --short
```

- [ ] **Step 6: Commit the evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for expected-version CAS

Removing the version predicate from the UPDATE lets a command derived
from an older version mutate newer state, and the named test goes red."
```

---

### Task 3: Decisions as data, revalidated against their hashes

The kernel confirms every referenced hash **still matches** before acting. This is where "yesterday's approval cannot authorize today's object" stops being a property of the artifact store and becomes a property of the decision path.

**Files:**
- Create: `v2/kernel/decisions.py`, `v2/tests/kernel/test_decisions.py`

**Interfaces:**
- Consumes: `VerdictBinding`, `binding_hash`, `Store`.
- Produces: `validate_decision(store, decision: dict, observed: dict) -> None`, `DecisionRejected`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_decisions.py
import pytest
from kernel.decisions import validate_decision, DecisionRejected

def _decision(**over):
    d = {
        "decision_id": "dec_1", "run_id": "r", "decision_type": "review_ruling",
        "based_on": {"state_version": 47, "spec_sha256": "a" * 64,
                     "plan_sha256": "b" * 64, "base_git_sha": "c" * 40,
                     "head_git_sha": "d" * 40, "review_bundle_sha256": "e" * 64},
        "finding_rulings": [{"finding_id": "f1", "disposition": "blocking",
                             "rationale": "why", "confidence": 0.86}],
        "recommendation": "request_revision",
    }
    d.update(over)
    return d

def _observed(**over):
    o = {"state_version": 47, "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
         "base_git_sha": "c" * 40, "head_git_sha": "d" * 40,
         "review_bundle_sha256": "e" * 64, "reviewer_identity": "codex",
         "implementer_identity": "claude"}
    o.update(over)
    return o

def test_matching_decision_is_accepted():
    validate_decision(None, _decision(), _observed())

@pytest.mark.parametrize("field", [
    "spec_sha256", "plan_sha256", "base_git_sha", "head_git_sha",
    "review_bundle_sha256", "state_version",
])
def test_any_drifted_hash_rejects_the_decision(field):
    """Each referenced input is checked separately. A single combined
    comparison would pass while five of six went unverified."""
    changed = "9" * (40 if field.endswith("git_sha") else 64) if field != "state_version" else 48
    with pytest.raises(DecisionRejected, match=field):
        validate_decision(None, _decision(), _observed(**{field: changed}))

def test_reviewer_must_be_independent_of_the_implementer():
    with pytest.raises(DecisionRejected, match="independence"):
        validate_decision(None, _decision(), _observed(reviewer_identity="claude"))

def test_accept_does_not_authorize_a_merge():
    """'accept' means no unresolved blockers for the pinned bundle. Only the
    kernel authorizes a merge, and it does so through request_merge."""
    d = _decision(recommendation="accept")
    validate_decision(None, d, _observed())
    assert d["recommendation"] != "merge"

def test_unknown_decision_type_is_refused():
    with pytest.raises(DecisionRejected, match="decision_type"):
        validate_decision(None, _decision(decision_type="just_merge_it"), _observed())

def test_missing_based_on_field_is_refused():
    d = _decision()
    del d["based_on"]["head_git_sha"]
    with pytest.raises(DecisionRejected, match="head_git_sha"):
        validate_decision(None, d, _observed())
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_decisions.py -v`
Expected: FAIL, `No module named 'kernel.decisions'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/decisions.py
"""Decision-as-data validation.

A decision arrives as data, not as an action. The kernel validates its
schema, confirms every referenced hash STILL matches what it observes,
confirms the decision type is legal, and checks reviewer independence --
before any transition is computed.
"""
from __future__ import annotations

class DecisionRejected(Exception):
    pass

BOUND_INPUTS = ("state_version", "spec_sha256", "plan_sha256",
                "base_git_sha", "head_git_sha", "review_bundle_sha256")

LEGAL_TYPES = frozenset({"review_ruling", "ci_ruling", "human_ruling"})

def validate_decision(store, decision: dict, observed: dict) -> None:
    if decision.get("decision_type") not in LEGAL_TYPES:
        raise DecisionRejected(
            f"decision_type {decision.get('decision_type')!r} is not legal")

    based_on = decision.get("based_on") or {}
    for name in BOUND_INPUTS:
        if name not in based_on:
            raise DecisionRejected(f"based_on is missing {name}")
        if based_on[name] != observed.get(name):
            # The decision was computed against inputs that have since moved.
            raise DecisionRejected(
                f"{name} drifted: decision saw {based_on[name]!r}, "
                f"kernel observes {observed.get(name)!r}")

    if observed.get("reviewer_identity") == observed.get("implementer_identity"):
        raise DecisionRejected(
            "reviewer independence violated: reviewer and implementer are the same actor")
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest tests/kernel/test_decisions.py -v`
Expected: all PASS, including all six drift parametrizations.

- [ ] **Step 5: Mutation-test, one bound input at a time**

```bash
git add v2/kernel/decisions.py v2/tests/kernel/test_decisions.py
git commit -m "feat(v2): decision-as-data validation with hash revalidation"
```

```bash
# Mutation: stop checking head_git_sha. EXACTLY one parametrization must go
# red. More than one means the mutation was wrong; none means the loop does
# not cover the field it claims to.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/decisions.py")
p.write_text(p.read_text().replace(
  '''BOUND_INPUTS = ("state_version", "spec_sha256", "plan_sha256",
                "base_git_sha", "head_git_sha", "review_bundle_sha256")''',
  '''BOUND_INPUTS = ("state_version", "spec_sha256", "plan_sha256",
                "base_git_sha", "review_bundle_sha256")'''))
EOF
cd v2 && python -m pytest tests/kernel/test_decisions.py -v
git checkout v2/kernel/decisions.py && git status --short
```

- [ ] **Step 6: Commit the evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for hash revalidation

Dropping head_git_sha from BOUND_INPUTS turns exactly its own
parametrization red, so each of the six referenced inputs is
independently checked."
```

---

### Task 4: The effect journal

Persist intent before invoking the effect; carry an idempotency key; record the external object identifier; reconcile an uncertain result before retrying. Defined by effect class, not by an operation list — an earlier spec revision scoped it to three operations and lost PR creation, the very effect that motivated the credential boundary.

**Files:**
- Create: `v2/kernel/effects.py`, `v2/tests/kernel/test_effects.py`
- Modify: `v2/kernel/schema.sql`

**Interfaces:**
- Consumes: `Store`, `current_generation`, `OwnershipLost`.
- Produces: `EffectClass`, `EffectExecutor` (protocol), `perform(store, run_id, generation, effect_class, idempotency_key, intent, executor) -> str`, `UncertainEffect`, `pending_reconciliation(store, run_id)`.

- [ ] **Step 1: Extend the schema**

```sql
CREATE TABLE IF NOT EXISTS effects (
  id                 TEXT PRIMARY KEY,
  run_id             TEXT NOT NULL,
  generation         INTEGER NOT NULL,
  effect_class       TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL UNIQUE,
  state              TEXT NOT NULL,          -- intended | confirmed | uncertain | reconciled
  external_object_id TEXT,
  intent_json        TEXT NOT NULL,
  at_us              INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS effects_no_delete BEFORE DELETE ON effects
BEGIN SELECT RAISE(ABORT, 'the effect journal is append-only'); END;
```

- [ ] **Step 2: Write the failing tests**

```python
# v2/tests/kernel/test_effects.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.ownership import acquire, OwnershipLost
from kernel.effects import (EffectClass, perform, UncertainEffect,
                            pending_reconciliation)

@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s

class Recorder:
    """Fake executor. Records the order of calls so the test can prove intent
    was persisted BEFORE the effect was attempted."""
    def __init__(self, store, fail=None):
        self.store, self.fail, self.calls = store, fail, []
    def __call__(self, effect_class, intent, idempotency_key):
        rows = self.store._conn.execute(
            "SELECT state FROM effects WHERE idempotency_key=?", (idempotency_key,)).fetchall()
        self.calls.append(("executed", [r[0] for r in rows]))
        if self.fail:
            raise self.fail
        return "ext_123"

def test_the_eight_effect_classes_are_all_declared():
    """The journal is defined by effect class. If this set shrinks, effects
    silently stop being journalled -- which is how PR creation was lost once."""
    assert sorted(EffectClass.ALL) == sorted([
        "ref_update", "pull_request", "status_check", "comment",
        "issue_or_label", "revert_or_recovery", "credential_lifecycle",
        "session_control"])

def test_intent_is_persisted_before_the_effect_is_attempted(store):
    """The whole point of persist-before-execute. The executor observes an
    'intended' row already present when it runs."""
    gen = acquire(store, "r", "a")
    rec = Recorder(store)
    perform(store, "r", gen, EffectClass.PULL_REQUEST, "k1", {"title": "x"}, rec)
    assert rec.calls == [("executed", ["intended"])]

def test_confirmed_effect_records_the_external_object_id(store):
    gen = acquire(store, "r", "a")
    perform(store, "r", gen, EffectClass.PULL_REQUEST, "k1", {}, Recorder(store))
    row = store._conn.execute(
        "SELECT state, external_object_id FROM effects WHERE idempotency_key='k1'").fetchone()
    assert row == ("confirmed", "ext_123")

def test_a_superseded_generation_cannot_perform_an_effect(store):
    """Binding results alone fences returned data, not the effects an attempt
    already performed. Effect REQUESTS are fenced too."""
    stale = acquire(store, "r", "a")
    acquire(store, "r", "b")
    with pytest.raises(OwnershipLost):
        perform(store, "r", stale, EffectClass.REF_UPDATE, "k2", {}, Recorder(store))

def test_a_superseded_generation_leaves_no_journal_row(store):
    stale = acquire(store, "r", "a")
    acquire(store, "r", "b")
    with pytest.raises(OwnershipLost):
        perform(store, "r", stale, EffectClass.REF_UPDATE, "k3", {}, Recorder(store))
    assert store._conn.execute(
        "SELECT COUNT(*) FROM effects WHERE idempotency_key='k3'").fetchone()[0] == 0

def test_an_uncertain_effect_blocks_retry_until_reconciled(store):
    """An ambiguous result must be reconciled before any retry, or the retry
    can duplicate an external mutation that already happened."""
    gen = acquire(store, "r", "a")
    with pytest.raises(UncertainEffect):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k4", {},
                Recorder(store, fail=TimeoutError("no response")))
    assert [e["idempotency_key"] for e in pending_reconciliation(store, "r")] == ["k4"]
    with pytest.raises(UncertainEffect, match="reconcil"):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k4", {}, Recorder(store))

def test_replaying_a_confirmed_key_does_not_re_execute(store):
    gen = acquire(store, "r", "a")
    rec = Recorder(store)
    perform(store, "r", gen, EffectClass.COMMENT, "k5", {}, rec)
    perform(store, "r", gen, EffectClass.COMMENT, "k5", {}, rec)
    assert len(rec.calls) == 1, "a confirmed effect was executed twice"
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_effects.py -v`
Expected: FAIL, `No module named 'kernel.effects'`.

- [ ] **Step 4: Implement**

```python
# v2/kernel/effects.py
"""The effect journal, defined by semantic effect class.

Persist intent before invoking the effect; carry an idempotency key; record
the external object identifier; reconcile an uncertain result before
retrying. Every journalled mutation is a generation-fenced resource.
"""
from __future__ import annotations
import json
from kernel.events import EventKind
from kernel.ids import new_id
from kernel.ownership import current_generation, OwnershipLost

class EffectClass:
    REF_UPDATE           = "ref_update"
    PULL_REQUEST         = "pull_request"
    STATUS_CHECK         = "status_check"
    COMMENT              = "comment"
    ISSUE_OR_LABEL       = "issue_or_label"
    REVERT_OR_RECOVERY   = "revert_or_recovery"
    CREDENTIAL_LIFECYCLE = "credential_lifecycle"
    SESSION_CONTROL      = "session_control"
    ALL = frozenset({REF_UPDATE, PULL_REQUEST, STATUS_CHECK, COMMENT,
                     ISSUE_OR_LABEL, REVERT_OR_RECOVERY,
                     CREDENTIAL_LIFECYCLE, SESSION_CONTROL})

class UncertainEffect(Exception):
    """The effect's outcome is unknown. It must be reconciled before retry."""

def perform(store, run_id: str, generation: int, effect_class: str,
            idempotency_key: str, intent: dict, executor) -> str:
    if effect_class not in EffectClass.ALL:
        raise ValueError(f"unknown effect class: {effect_class}")

    existing = store.effect_by_key(idempotency_key)
    if existing is not None:
        if existing["state"] == "uncertain":
            raise UncertainEffect(
                f"{idempotency_key} needs reconciliation before retry")
        return existing["external_object_id"]

    # Fence BEFORE journalling, so a superseded generation leaves no trace and
    # cannot consume the idempotency key a live generation may still need.
    if generation != current_generation(store, run_id):
        raise OwnershipLost(
            f"generation {generation} superseded; effect request carries no write capability")

    eid = new_id("eff")
    store.journal_intent(eid, run_id, generation, effect_class,
                         idempotency_key, intent)
    store.append_fact(run_id=run_id, kind=EventKind.EFFECT_INTENDED, actor="kernel",
                      causal_command_id=idempotency_key,
                      payload={"effect_class": effect_class, "effect_id": eid})
    try:
        external_id = executor(effect_class, intent, idempotency_key)
    except Exception as exc:
        store.mark_effect(idempotency_key, "uncertain", None)
        store.append_fact(run_id=run_id, kind=EventKind.EFFECT_UNCERTAIN, actor="kernel",
                          causal_command_id=idempotency_key,
                          payload={"effect_id": eid, "error": type(exc).__name__})
        raise UncertainEffect(
            f"{effect_class} outcome unknown ({type(exc).__name__}); "
            "reconcile before retry") from exc

    store.mark_effect(idempotency_key, "confirmed", external_id)
    store.append_fact(run_id=run_id, kind=EventKind.EFFECT_CONFIRMED, actor="kernel",
                      causal_command_id=idempotency_key,
                      payload={"effect_id": eid, "external_object_id": external_id})
    return external_id

def pending_reconciliation(store, run_id: str) -> list[dict]:
    rows = store._conn.execute(
        "SELECT idempotency_key, effect_class, generation FROM effects"
        " WHERE run_id = ? AND state = 'uncertain' ORDER BY at_us", (run_id,)).fetchall()
    return [{"idempotency_key": r[0], "effect_class": r[1], "generation": r[2]}
            for r in rows]
```

Add to `v2/kernel/store.py`:

```python
    def journal_intent(self, effect_id, run_id, generation, effect_class,
                       idempotency_key, intent) -> None:
        self._conn.execute(
            "INSERT INTO effects (id, run_id, generation, effect_class,"
            " idempotency_key, state, external_object_id, intent_json, at_us)"
            " VALUES (?,?,?,?,?,'intended',NULL,?,?)",
            (effect_id, run_id, generation, effect_class, idempotency_key,
             json.dumps(intent, sort_keys=True), self._clock.now_us()))

    def mark_effect(self, idempotency_key: str, state: str,
                    external_object_id: str | None) -> None:
        self._conn.execute(
            "UPDATE effects SET state = ?, external_object_id = ?"
            " WHERE idempotency_key = ?", (state, external_object_id, idempotency_key))

    def effect_by_key(self, idempotency_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT state, external_object_id FROM effects WHERE idempotency_key = ?",
            (idempotency_key,)).fetchone()
        return None if row is None else {"state": row[0], "external_object_id": row[1]}
```

- [ ] **Step 5: Run, then mutation-test persist-before-execute**

Run: `cd v2 && python -m pytest tests/kernel/test_effects.py -v` — expect PASS.

```bash
git add v2/kernel/effects.py v2/kernel/store.py v2/kernel/schema.sql v2/tests/kernel/test_effects.py
git commit -m "feat(v2): effect journal by class, persist-before-execute, generation-fenced"
```

```bash
# Mutation 1: execute before journalling. The ordering test must go red.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/effects.py"); t = p.read_text()
t = t.replace(
  """    eid = new_id("eff")
    store.journal_intent(eid, run_id, generation, effect_class,
                         idempotency_key, intent)""",
  """    eid = new_id("eff")""")
t = t.replace(
  """        external_id = executor(effect_class, intent, idempotency_key)""",
  """        external_id = executor(effect_class, intent, idempotency_key)
        store.journal_intent(eid, run_id, generation, effect_class,
                             idempotency_key, intent)""")
p.write_text(t)
EOF
cd v2 && python -m pytest tests/kernel/test_effects.py::test_intent_is_persisted_before_the_effect_is_attempted -v
# Expected: FAIL -- the executor now sees no 'intended' row.
git checkout v2/kernel/effects.py && git status --short
```

```bash
# Mutation 2: remove the generation fence. BOTH superseded-generation tests
# must go red -- one asserts the refusal, the other that no row is written.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/effects.py"); t = p.read_text()
t = t.replace("""    if generation != current_generation(store, run_id):
        raise OwnershipLost(
            f"generation {generation} superseded; effect request carries no write capability")
""", "")
p.write_text(t)
EOF
cd v2 && python -m pytest tests/kernel/test_effects.py -v -k superseded
git checkout v2/kernel/effects.py && git status --short
```

- [ ] **Step 6: Commit the evidence**

```bash
git commit --allow-empty -m "test(v2): mutation evidence for the effect journal

Journalling after execution turns the ordering test red; removing the
generation fence turns both superseded-generation tests red. The second
mutation is deliberately checked against two tests, because fencing the
refusal without fencing the journal write would leave a superseded
attempt able to consume an idempotency key a live generation still needs."
```

---

### Task 5: `reconciliation_required` — a durable state, not a silent stall

An unconfirmed stop leaves the attempt non-terminal and halts that run. Safety without an operating design is not enough for a system whose purpose is running overnight.

**Files:**
- Modify: `v2/kernel/effects.py`, `v2/kernel/schema.sql`
- Create: `v2/tests/kernel/test_reconciliation.py`

**Interfaces:**
- Produces: `enter_reconciliation_required(store, run_id, evidence) -> None`, `reconcile(store, run_id, idempotency_key, resolution, expected_version) -> None`, `is_halted(store, run_id) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_reconciliation.py
import pytest
from kernel.store import Store
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.effects import (EffectClass, perform, UncertainEffect,
                            enter_reconciliation_required, reconcile, is_halted)
from kernel.commands import Command, submit, StaleVersion

@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    for r in ("r", "other"):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s

def _fail(store):
    gen = acquire(store, "r", "a")
    def boom(*a):
        raise TimeoutError("no response")
    with pytest.raises(UncertainEffect):
        perform(store, "r", gen, EffectClass.PULL_REQUEST, "k1", {}, boom)
    return gen

def test_an_uncertain_effect_halts_the_run(store):
    _fail(store)
    assert is_halted(store, "r")

def test_the_halt_records_the_evidence_an_operator_needs(store):
    gen = _fail(store)
    ev = store.reconciliation_evidence("r")
    for key in ("run_id", "generation", "affected_resources",
                "last_confirmed_observations", "recommended_actions"):
        assert key in ev, f"evidence is missing {key}"
    assert ev["generation"] == gen

def test_unrelated_runs_continue(store):
    """Spec: unrelated queued runs continue; only the affected run halts. The
    wedge is per-run because the conflict is per-run."""
    _fail(store)
    assert not is_halted(store, "other")
    gen = acquire(store, "other", "b")
    assert submit(store, Command(name="submit_spec", run_id="other",
                                 expected_version=0, idempotency_key="ok",
                                 generation=gen, payload={})).accepted

def test_a_halted_run_refuses_further_commands(store):
    _fail(store)
    gen = acquire(store, "r", "a")
    with pytest.raises(Exception, match="reconcil"):
        submit(store, Command(name="submit_spec", run_id="r", expected_version=1,
                              idempotency_key="nope", generation=gen, payload={}))

def test_resolution_is_an_audited_cas_command_not_a_manual_edit(store):
    """The spec requires resolution be an audited command with expected-version
    CAS, never a manual state edit."""
    _fail(store)
    v = store.run_version("r")
    with pytest.raises(StaleVersion):
        reconcile(store, "r", "k1", resolution="no_pr_created", expected_version=v + 5)
    reconcile(store, "r", "k1", resolution="no_pr_created", expected_version=v)
    assert not is_halted(store, "r")

def test_reconciliation_is_recorded_as_a_fact(store):
    _fail(store)
    reconcile(store, "r", "k1", resolution="no_pr_created",
              expected_version=store.run_version("r"))
    assert "effect_reconciled" in [f.kind for f in store.facts_for("r")]
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_reconciliation.py -v`
Expected: FAIL, `ImportError: cannot import name 'enter_reconciliation_required'`.

- [ ] **Step 3: Implement**

Add to `v2/kernel/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS reconciliation (
  run_id        TEXT PRIMARY KEY,
  evidence_json TEXT NOT NULL,
  at_us         INTEGER NOT NULL
);
```

Add to `v2/kernel/effects.py`:

```python
def enter_reconciliation_required(store, run_id: str, evidence: dict) -> None:
    """Halt this run pending human reconciliation.

    A durable state, not a silent stall: it records what an operator needs to
    act, and the runner surfaces it. Only this run halts -- the conflict is
    per-run, because an unconfirmed attempt holds this run's resources and
    nothing else.
    """
    store.set_reconciliation(run_id, evidence)

def is_halted(store, run_id: str) -> bool:
    return store.reconciliation_evidence(run_id) is not None

def reconcile(store, run_id: str, idempotency_key: str, resolution: str,
              expected_version: int) -> None:
    """Resolve a halt. An audited command under expected-version CAS -- never
    a manual state edit."""
    from kernel.commands import StaleVersion
    cur = store._conn.execute(
        "UPDATE runs SET version = version + 1 WHERE run_id = ? AND version = ?",
        (run_id, expected_version))
    if cur.rowcount == 0:
        raise StaleVersion(
            f"reconciliation derived from version {expected_version}, which has moved")
    store.mark_effect(idempotency_key, "reconciled", None)
    store.clear_reconciliation(run_id)
    store.append_fact(run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
                      causal_command_id=idempotency_key,
                      payload={"resolution": resolution})
```

In `perform`, replace the bare `raise UncertainEffect(...)` in the exception path with a call that first records the halt:

```python
        enter_reconciliation_required(store, run_id, {
            "run_id": run_id,
            "generation": generation,
            "affected_resources": [effect_class],
            "last_confirmed_observations": store.last_confirmed(run_id),
            "stop_attempts": 0,
            "recommended_actions": [
                f"Check whether the {effect_class} succeeded externally",
                f"Then: reconcile(store, {run_id!r}, {idempotency_key!r}, resolution, version)",
            ],
        })
```

Add to `v2/kernel/store.py`:

```python
    def set_reconciliation(self, run_id: str, evidence: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reconciliation (run_id, evidence_json, at_us)"
            " VALUES (?,?,?)",
            (run_id, json.dumps(evidence, sort_keys=True), self._clock.now_us()))

    def reconciliation_evidence(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT evidence_json FROM reconciliation WHERE run_id = ?",
            (run_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def clear_reconciliation(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM reconciliation WHERE run_id = ?", (run_id,))

    def last_confirmed(self, run_id: str) -> list[dict]:
        """The most recent confirmed effects, for the operator's evidence
        packet: what the kernel knows actually happened before the halt."""
        rows = self._conn.execute(
            "SELECT effect_class, external_object_id, at_us FROM effects"
            " WHERE run_id = ? AND state = 'confirmed' ORDER BY at_us DESC LIMIT 10",
            (run_id,)).fetchall()
        return [{"effect_class": r[0], "external_object_id": r[1], "at_us": r[2]}
                for r in rows]
```

In `kernel/commands.py`, refuse commands on a halted run, immediately after the unknown-name check:

```python
    from kernel.effects import is_halted
    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        raise RuntimeError(
            f"run {cmd.run_id} is halted pending reconciliation; resolve it first")
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest tests/kernel/ -v`
Expected: all PASS, across every module in this plan and M1-2.

- [ ] **Step 5: Mutation-test the blast radius of the halt**

```bash
git add v2/kernel/ v2/tests/kernel/test_reconciliation.py
git commit -m "feat(v2): reconciliation_required as a durable per-run halt"
```

```bash
# Mutation: make the halt global rather than per-run. test_unrelated_runs_continue
# must go red -- the spec's policy is that only the affected run halts, and a
# scheduler-wide halt would silently stop overnight work.
# Note the mutation calls only methods that already exist: one that raised
# AttributeError would produce a collection error across the module, which is an
# invalid mutation rather than evidence either way.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("v2/kernel/effects.py")
p.write_text(p.read_text().replace(
  "return store.reconciliation_evidence(run_id) is not None",
  "return store._conn.execute('SELECT COUNT(*) FROM reconciliation').fetchone()[0] > 0"))
EOF
cd v2 && python -m pytest tests/kernel/test_reconciliation.py -v
git checkout v2/kernel/effects.py && git status --short
```

- [ ] **Step 6: Run the whole suite and commit**

```bash
cd v2 && python -m pytest tests/kernel/ -v
git commit --allow-empty -m "test(v2): mutation evidence for per-run halt scope

Making the halt global turns test_unrelated_runs_continue red, binding
the spec's stated policy that only the affected run halts rather than
the scheduler."
```

---

## Done means

Ownership is acquired by CAS and yields a monotonically increasing generation; a command derived from a stale version is refused and a replayed idempotency key mutates nothing; a decision is rejected when any one of its six referenced inputs has drifted, proven by a per-field mutation; every one of the eight effect classes journals intent before execution and refuses a request from a superseded generation without consuming its idempotency key; an uncertain effect halts exactly its own run with operator-actionable evidence and is resolvable only by an audited CAS command. No effect touches GitHub — the executor is injected, and M1-4 supplies the real one.
