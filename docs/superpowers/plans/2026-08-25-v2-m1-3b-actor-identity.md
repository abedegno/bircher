# Bircher v2 — Milestone 1, Plan 3b: Actor Identity and Provenance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the kernel an identity substrate, so authorization compares things the mechanism assigned rather than strings a caller supplied.

**Architecture:** The kernel dispatches every attempt, so it already knows whose work it is. Identity is read from the kernel's own dispatch record and written into the command by the kernel — assigned, never presented, never accepted as input. `Command` loses every actor-shaped payload field.

**Tech Stack:** Python 3.11, `sqlite3`, pytest. No new dependencies.

**Spec:** `docs/design/2026-08-23-v2-kernel-design.md` §4b (branch `v2`)

## Why this plan exists

M1-3 built the command envelope — CAS, idempotency, generation fencing — and its authorization layer compared inputs correctly. Five review rounds later, a full-effort adversarial pass produced a provenance audit and found **five of the six links in the merge-authorization chain were caller assertions**. Every individual comparison was right. The chain proved nothing, because `Command` had no actor and `implementer_identity` / `reviewer_identity` were payload fields, so one caller named both sides of its own independence check.

Demonstrated, not theorised: a caller recorded as implementer submitted its own `accept` naming a different reviewer, asserted green CI, reached `merge_requested`, and then had the reviewed artifact deleted — and authorization still succeeded.

**This is not a bug-fix plan.** It is the substrate the authorization layer was built without.

## Global Constraints

- **`Command` has no field a caller can use to name an actor.** Not `implementer_identity`, not `reviewer_identity`, not `actor`. If a caller can populate it, it is not identity.
- **Identity is assigned at dispatch, never verified on arrival.** A session receives no token — omnigent's agent-env allowlist admits only proxy/SSL/locale vars, `HOME`, `PATH`, `TERM`, `TMPDIR`, `NODE_EXTRA_CA_CERTS` and a bare `OMNIGENT=1` — and under M1-1's egress rules cannot reach the server. There is nothing to verify and nothing needs verifying.
- **`actor="kernel"` is correct only for facts the kernel originates itself.** An accepted command records the dispatched actor.
- **Existence is not identity.** An artifact the store happens to hold is not this run's current output.
- **Reaching a state records that authorization happened; it is not the authorization.** The authorized binding travels to the effect.
- **Every authorization input gets a provenance row** — `observed` or `asserted`, where `asserted` means a defect or a declared residual with a reason. There is no third case.

---

## File Structure

| File | Responsibility |
|---|---|
| `v2/kernel/dispatch.py` | The dispatch record: who the kernel started, for which attempt. |
| `v2/kernel/schema.sql` | *(modify)* `dispatches` table; `runs.current_artifact_hash`. |
| `v2/kernel/commands.py` | *(modify)* actor assigned from dispatch; actor-shaped payload fields refused. |
| `v2/kernel/authz.py` | *(modify)* independence and lineage from kernel state; binding returned for the effect. |
| `v2/kernel/effects.py` | *(modify)* `perform()` takes an authorized binding and re-evaluates it. |
| `docs/design/provenance-table.md` | The required artifact: one row per authorization input. |

---

### Task 1: The dispatch record

**Files:**
- Create: `v2/kernel/dispatch.py`, `v2/tests/kernel/test_dispatch.py`
- Modify: `v2/kernel/schema.sql`, `v2/kernel/events.py`, `v2/kernel/store.py`

**Interfaces:**
- Produces: `dispatch(store, run_id, *, actor, role) -> str`, `actor_for(store, run_id, generation) -> str | None`, `Role`.

- [x] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_dispatch.py
import pytest

from kernel.dispatch import Role, actor_for, dispatch
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_dispatch_binds_an_actor_to_the_generation_it_acquired(store):
    gen = acquire(store, "r", "attempt_1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    assert actor_for(store, "r", gen) == "claude"


def test_a_generation_with_no_dispatch_has_no_actor(store):
    """An ungated caller must not inherit somebody else's identity."""
    gen = acquire(store, "r", "attempt_1")
    assert actor_for(store, "r", gen) is None


def test_each_generation_gets_its_own_actor(store):
    g1 = acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    g2 = acquire(store, "r", "a2")
    dispatch(store, "r", actor="codex", role=Role.REVIEWER)
    assert actor_for(store, "r", g1) == "claude"
    assert actor_for(store, "r", g2) == "codex"


def test_dispatch_records_a_fact(store):
    acquire(store, "r", "a1")
    dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER)
    assert "attempt_dispatched" in [f.kind for f in store.facts_for("r")]


def test_dispatch_requires_a_known_role(store):
    acquire(store, "r", "a1")
    with pytest.raises(ValueError, match="role"):
        dispatch(store, "r", actor="claude", role="boss")
```

- [x] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_dispatch.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL, `No module named 'kernel.dispatch'`.

- [x] **Step 3: Extend the schema and event kinds**

```sql
CREATE TABLE IF NOT EXISTS dispatches (
  id         TEXT PRIMARY KEY,
  run_id     TEXT    NOT NULL,
  generation INTEGER NOT NULL,
  actor      TEXT    NOT NULL,
  role       TEXT    NOT NULL,
  at_us      INTEGER NOT NULL,
  UNIQUE (run_id, generation)
);
```

Add `ATTEMPT_DISPATCHED = "attempt_dispatched"` to `EventKind` and `SCHEMA_VERSIONS`.

- [x] **Step 4: Implement**

```python
# v2/kernel/dispatch.py
"""The dispatch record: who the kernel started, for which attempt.

This is the identity substrate. The kernel dispatches every attempt, so it
already knows whose work it is; identity is read from here and written into
commands by the kernel. A session receives no token and needs none -- an
assigned identity cannot be forged, whereas a presented one is only as good
as its verification.
"""

from __future__ import annotations

from kernel.events import EventKind
from kernel.ids import new_id
from kernel.ownership import current_generation


class Role:
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    ALL = frozenset({IMPLEMENTER, REVIEWER, OPERATOR})


def dispatch(store, run_id: str, *, actor: str, role: str) -> str:
    """Bind *actor* to the run's current generation."""
    if role not in Role.ALL:
        raise ValueError(f"unknown role: {role!r}; expected one of {sorted(Role.ALL)}")
    generation = current_generation(store, run_id)
    did = new_id("dsp")
    store.record_dispatch(did, run_id, generation, actor, role)
    store.append_fact(
        run_id=run_id, kind=EventKind.ATTEMPT_DISPATCHED, actor="kernel",
        causal_command_id=None,
        payload={"dispatch_id": did, "generation": generation,
                 "actor": actor, "role": role},
    )
    return did


def actor_for(store, run_id: str, generation: int) -> str | None:
    """The actor the kernel dispatched for *generation*, or None.

    Exact-generation lookup, deliberately: a fallback to the most recent
    dispatch is how one attempt inherits another attempt's identity.
    """
    return store.dispatch_actor(run_id, generation)
```

- [x] **Step 5: Commit, then mutation-test**

```bash
git add v2 && git commit -m "feat(v2): the dispatch record as the identity substrate"
```

Mutation: make `dispatch_actor` fall back to the most recent dispatch when the generation does not match. `test_each_generation_gets_its_own_actor` must go red.

---

### Task 2: The kernel assigns the actor; the payload cannot name one

**Files:**
- Modify: `v2/kernel/commands.py`, and every test that passed an identity in a payload.

**Interfaces:**
- Consumes: `actor_for` (Task 1).
- Produces: `ACTOR_FIELDS` — the refused payload keys.

- [x] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("field", ["actor", "implementer_identity", "reviewer_identity"])
def test_a_command_carrying_an_actor_field_is_refused(field):
    """If a caller can populate it, it is not identity. This is exactly how
    one caller named both sides of its own independence check."""
    s = _store()
    g = acquire(s, "r", "a")
    with pytest.raises(ValueError, match="assigned"):
        submit(s, Command(name="submit_spec", run_id="r", expected_version=0,
                          idempotency_key=f"k-{field}", generation=g,
                          payload={field: "anyone"}))


def test_the_accepted_fact_records_the_dispatched_actor():
    s = _store()
    acquire(s, "r", "a")
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    _submit_spec(s)
    fact = [f for f in s.facts_for("r") if f.kind == "command_accepted"][0]
    assert fact.actor == "claude", (
        "the accepted fact attributes the work to the kernel, so the audit "
        "trail cannot say who did it"
    )


def test_an_undispatched_generation_cannot_submit():
    """No dispatch record means the kernel does not know who this is."""
    s = _store()
    g = acquire(s, "r", "a")
    with pytest.raises(NotAuthorized, match="no dispatched actor"):
        _submit_spec(s, generation=g)
```

- [x] **Step 2: Implement**

In `submit()`, before anything else: refuse any payload containing a key in `ACTOR_FIELDS`; resolve `actor = actor_for(store, cmd.run_id, cmd.generation)` and refuse when `None`; pass it to every `append_fact(actor=actor)` for accepted and rejected commands alike.

- [x] **Step 3: Mutation-test**

Remove `reviewer_identity` from `ACTOR_FIELDS`; its parametrized case must go red and no other.

---

### Task 3: Independence and artifact lineage from kernel state

**Files:**
- Modify: `v2/kernel/authz.py`, `v2/kernel/store.py`, `v2/kernel/schema.sql`

- [x] **Step 1: Write the failing tests**

```python
def test_independence_compares_dispatched_identities():
    """Both sides observed. A reviewer cannot name itself as someone else,
    because it does not name itself at all."""
    s = _store()
    _dispatch_and_implement(s, actor="claude")
    _dispatch_as(s, actor="claude", role=Role.REVIEWER)
    with pytest.raises(NotAuthorized, match="independen"):
        _record_review(s, verdict="accept")


def test_a_review_must_bind_this_runs_current_artifact():
    """Existence is not identity: any blob in the store satisfied the old
    check, regardless of lineage or run."""
    s = _store()
    _dispatch_and_implement(s, actor="claude", artifact=b"# v1")
    unrelated = put_artifact(s, b"# unrelated")
    _dispatch_as(s, actor="codex", role=Role.REVIEWER)
    with pytest.raises(NotAuthorized, match="current"):
        _record_review(s, verdict="accept", artifact_hash=unrelated)


def test_a_revision_invalidates_an_acceptance_of_the_previous_artifact():
    """A new implementation makes the old artifact stale; an acceptance over
    it must not authorize a merge."""
```

- [x] **Step 2: Implement**

`runs.current_artifact_hash`, set by `start_implementation` from the artifact it names; `validate_review` compares the binding's `artifact_hash` against it; independence reads the dispatched actors for the implementer and reviewer generations rather than payload strings.

- [x] **Step 3: Mutation-test** each guard separately, confirming one red test each.

---

### Task 4: The authorized binding travels to the effect

**Files:**
- Modify: `v2/kernel/effects.py`, `v2/kernel/authz.py`

- [x] **Step 1: Write the failing test**

```python
def test_a_merge_effect_revalidates_its_binding_at_execution():
    """Reaching merge_requested records that authorization happened; it is
    not the authorization. The reviewed artifact was deleted between the two
    and the merge still executed."""
    s, binding, gen = _authorized_merge(_store())
    s.delete_artifact(binding.artifact_hash)   # the world moved
    with pytest.raises(NotAuthorized, match="revalidat"):
        perform(s, "r", gen, EffectClass.MERGE, "m",
                {"binding": binding.as_dict()}, _executor)
```

- [x] **Step 2: Implement**

`perform()` requires an authorized binding in the intent for `MERGE`, and re-runs the full merge authorization against it immediately before invoking the executor — verdict, lineage, independence, CI head, state.

- [x] **Step 3: Mutation-test** — remove the revalidation; the test must go red.

---

### Task 5: The provenance table

**Files:**
- Create: `docs/design/provenance-table.md`, `v2/tests/kernel/test_provenance.py`

- [x] **Step 1: Write the table**

One row per authorization input: the input, where it enters the system, whether the kernel **observed** it or an actor **asserted** it, and for every `asserted` row either a defect reference or a stated residual with its reason.

- [x] **Step 2: Make it checkable**

```python
def test_no_authorization_input_is_asserted_without_a_reason():
    """An input whose row reads 'asserted' is a defect or a declared residual.
    There is no third case, and a table nothing checks is prose."""
    for row in _rows():
        if row["provenance"] == "asserted":
            assert row["reason"], f"{row['input']}: asserted with no reason"


def test_every_authorization_input_appears_in_the_table():
    """A missing row is an input nobody classified, which is how five of six
    links stayed asserted through three rounds of repair."""
```

- [x] **Step 3: Commit** — the table is a required Milestone 1 artifact, not documentation.

---

## Done means

No `Command` payload can name an actor; every accepted and rejected fact records the dispatched actor rather than `"kernel"`; independence compares two dispatched identities; a review binds this run's current artifact; a merge effect revalidates its binding immediately before executing; and the provenance table has no `asserted` row without a stated reason. Each guard carries a mutation that reds its named test and nothing else.


---

## Executed 2026-08-25 — what this plan got wrong

All five tasks are implemented and committed (`6080e6b` … `fe6330e`). 187 tests
pass. Every guard carries a mutation that reds its named test; the mutations and
their results are recorded in the commit messages.

Four things the plan specified incorrectly, each found by executing it:

1. **`dispatch()` had to become the acquisition.** The plan had dispatch bind an
   actor to the generation a worker had already acquired. But `acquire()` takes
   its owner from the caller, and it *always increments* — so a worker would
   fence itself into a generation with no dispatch, orphaning the record. Fence
   and identity have to be written in one operation.

2. **The plan's Task 1 mutation was wrong.** It claimed a "fall back to the most
   recent dispatch" mutation would red `test_each_generation_gets_its_own_actor`.
   It did not: both generations in that test have exact dispatches, so the
   fallback never fires. Worse, the test that *should* have caught it —
   `..._with_no_dispatch_has_no_actor` — had no dispatch anywhere in the run, so
   the fallback returned `None` for want of any row rather than the right one.
   **The mutation survived.** The test now requires a dispatch on another
   generation, so a fallback has something wrong to return.

3. **Lineage needed a new command, which the plan did not anticipate.** Task 3
   assumed `start_implementation` could set the current artifact "from the
   artifact it names" — but it names none, and an implementation has produced
   nothing when it starts. Nothing anywhere recorded an implementation's
   *output*, which is why "existence" was standing in for "identity" in the
   first place. `record_implementation_output` closes it, and the closed command
   set grows by one with the argument recorded in the spec.

4. **`reviewer_identity` had to leave the verdict binding**, which the plan
   treated as untouched. Once the payload cannot name a reviewer, a merge
   requester cannot present the fifth member of the tuple — and should not be
   able to choose it. The binding is now four immutable inputs; who approved is
   a kernel-observed fact recorded beside the verdict.

Two process notes worth keeping:

- **A parametrize driven by the constant under test adapts to its own
  mutation.** `@parametrize("field", sorted(ACTOR_FIELDS))` deleted its case
  instead of failing it when a field was removed. Driven from a literal now.
- **The Task 5 mutations ran against uncommitted files and accumulated**, so
  three of five results were contaminated by earlier unrestored mutations. The
  harness now aborts unless exactly one file is dirty before it runs the suite.

## Residuals, carried to M1-4

Named in `docs/design/provenance-table.md` and enforced by
`test_the_residuals_are_the_ones_we_know_about`: CI status and CI head are
reported by an actor rather than observed; the context bundle hash is never seen
by the kernel; the policy version is type-checked but compared to nothing. The
verdict is asserted permanently and by intent.
