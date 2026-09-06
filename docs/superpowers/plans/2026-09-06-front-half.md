# Bircher v2 Front Half Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run whose input is an issue body reaches `planned` with a spec and a plan that are content-addressed artefacts, each accepted by a cross-vendor reviewer and approved by the human where the run's policy says so, with every question, ruling, verdict and approval an immutable kernel fact — and the existing implementation path runs unchanged from `planned`.

**Architecture:** The kernel (`v2/kernel/`) gains four explicit front-half states (`spec_submitted`, `spec_accepted`, `plan_submitted`, `plan_accepted`), a frozen per-run policy, and the commands and refusals of spec §2; sessions become journaled effects whose bodies never ride argv. The coordinator (`v2/coordinator/`) gains a `phases` loop that dispatches short single-purpose author and reviewer sessions, waits for the turn's end as a fact, reads output files from the session's worktree, and carries the human's answers, approvals and corrections from the omnigent session into kernel commands. The runner (`batch/run-queue.sh`) calls `phases` between `create_run` and the implementer dispatch, and can resume a parked run.

**Tech Stack:** Python 3.13 (CI; `requires-python >=3.11`), SQLite via the existing `kernel.store.Store`, pytest 8.3.4 + pyyaml 6.0.2 run as `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`, bash 5 for `batch/`, `curl` against the omnigent HTTP API, `git worktree`.

**Spec:** `docs/superpowers/specs/2026-09-05-front-half-design.md` (main, commit `9bf0a14`). Section numbers below are the spec's. Every requirement in this plan argues from it; where the plan had to rule on something the spec left open or stated twice, the ruling is listed under *Rulings taken while planning* and the spec line it amends is named.

## Global Constraints

- "Everything in the second list is code in the kernel or the coordinator, never an instruction in a prompt." (spec, *Design principle*): freezing, hashing, refusing, dispatching, counting, bounding and telling the human's message from the model's are mechanism, never prompt text.
- "`specified` and `planned` are reachable only through a reviewer's accept of the current hash" (§2 *States*). No task adds another path.
- "The body never rides argv" (§3 *Review round*): no executor puts a prompt body in an argv string; the kernel writes the body to a file it minted and appends `--data-binary @<path>` **after** the contract check.
- "Every guard below reads the fact, never the caller." (§1): policy, epoch, round, park currency and session identity are derived from journal facts and satisfied effects, never from a payload field.
- "The turn's end is a fact": `record_turn_ended` precedes every read of an output file, and every output-recording command is refused until the turn's `sess-stop` is satisfied (§3 *Review round*).
- "`retire_owed` is the loop's first step" — before `publish_owed`, the state read, any listing, any dispatch and any prompt (§3 *loop*).
- `ITEM_TIMEOUT` (`batch/run-queue.sh:45`, default `5400`) is the cap of every waited turn from its own start, passed as `phases --turn-timeout "$ITEM_TIMEOUT"` and validated as a positive integer (§3 *Cap*).
- Bundle uploads stay outside the kernel; worktrees are never removed; the operator/trigger, moving the repair loop into Python, accounts mode, per-task seats and multiple projects are *Out of scope* (spec, last section).
- Facts are append-only and every kind is declared in `kernel/events.py::SCHEMA_VERSIONS`; a fact kind used anywhere in this plan is declared in Task 1.
- The suite baseline is **1052 passed, 2 skipped**; every task ends green (`cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`), including the files Task 7 migrates.
- Commit messages carry **no AI attribution** — no `Co-Authored-By`, no `Claude-Session`, no "Generated with" — in this repo. Commit messages use the repo's `type(scope): summary` form.
- Mutation discipline (§8): commit before each mutation you run against a test; a test that stays green under the mutation named in its task is not done.
- Nothing in this plan files a GitHub issue, opens a PR, or pushes; the live-proof tasks (Part E) name the human action they need before they run.
- Never alias `cmd.payload` to a local name inside `kernel/authz.py` (`p = cmd.payload; p.get(...)`): the provenance extractor names the alias, and the row would be for `p['x']`. Write `cmd.payload.get(...)` at every read.
- `v2/tests/kernel/test_provenance.py` parses `kernel/authz.py`: every `cmd.payload[...]`/`.get('...')` read, every `store.<method>` call and every `<name>.get('<key>')` call in that file must have a row in `docs/design/provenance-table.md`, and the asserted set is pinned by name. A task that adds such a read in `authz.py` adds its row (observed, or asserted with a bold **Residual**/**Intentional** reason) in the same commit; Task 21 reconciles the pinned set and the count sentence in `docs/design/2026-08-23-v2-kernel-design.md`.

## Rulings taken while planning

1. **The events rule admits one `-d` from the runner's template sites.** §3 says both "the events rule admits no `-d` at all" and "the runner's own `_send_prompt` … keeps its inline `-d`"; `_send_prompt` and `_stop_session` post to the events endpoint. Both cannot hold. Ruling: the events rule admits `-d` at most once, as its own token, value not beginning with `@` (Task 2); **the coordinator's events argv carries no `-d`** and Task 15's test asserts it; `--data-binary` from any caller is refused. §11's row "none on events" is closed as "none from the coordinator on events". Amend spec §3 line "the events rule admits no `-d` at all" to "the coordinator's events argv carries no `-d` at all; the runner's template sites keep theirs" in the same commit as this plan.
2. **`human_direction` is a human fact for park currency.** §2 defines a park as current while newer than the last transition and the last human fact, naming `human_answer` and `human_ruling`. A `record_human_direction` from `queued`/`specified` neither transitions nor is a `human_ruling`, so the park would stay current and the loop would re-enter `human_pass` after the direction. Ruling: `HUMAN_FACT_KINDS = {human_answer, human_ruling, human_direction}` (Task 7).
3. **`run_created` is `run_started`.** The spec's cause `run_created` is the existing `RUN_STARTED` fact `Store.create_run` writes; no second fact is added.
4. **`grant_round` raises `max_seats` by two.** §1: "Only a human `retry` ruling raises it, by one round at a time"; a round is one author seat and one reviewer seat.
5. **`bircher:autonomous` wins over `bircher:gate-plan`** when both labels are present: gates are cleared last (Task 5).
6. **A run with no `policy_frozen` fact** (a v1 run created by `Store.create_run` or `enqueue()`) is governed by `Policy()` defaults; the front-half commands are legal for it only in front-half states, which those runs pass through under the new table exactly as the migrated tests drive them.
7. **Every front-half command is issued under the run's current generation**, whichever role dispatched it: the coordinator's author and reviewer dispatches supersede the runner's operator generation, so `record_turn_ended`, `park`, `dismiss_human_item`, `record_prompt_item` and `revise_bundle` carry no role requirement; `submit_*`, `record_author_empty`, `record_model_question`, `record_model_ruling` require `author`; `issue_review_brief` and a `review_ruling` `record_review` require `reviewer`; `start_implementation` keeps `implementer`.
8. **`stop_session` intent body.** A stop intent carries `body: {"event": "stop_session"}`; a prompt intent carries `body: {"artifact": <sha256>}`. The executor composes the event from whichever key is present, never from argv.
9. **Project config source.** The runner reads `GET $SERVER/v1/projects/$BIRCHER_PROJECT_ID` and passes `.config.bircher` (an object with optional keys `grill`, `gates`, `max_rounds`, `max_seats`) to `create_run`; with `BIRCHER_PROJECT_ID` unset it passes `{}`. The read is not an effect. (`GET /projects/{project_id}` exists: `omnigent/server/routes/projects.py:98`, and `ProjectObject.config` is "an opaque JSON object", `schemas.py:4302-4311`.)
10. **The publication comment is capped.** `bircher: published <phase> <hash8>`, a blank line, then the artefact truncated to 60,000 characters with a final line naming the sha256 when it was cut (Task 16): GitHub caps a comment at 65,536 characters and the `COMMENT` contract carries `--body` inline, under Linux's 128 KiB argument bound. The store and `<bundle-dir>/<run>/<phase>-r<round>.md` hold the whole artefact.
11. **`record_review` always names its phase.** The payload's `phase` is required for every `record_review`, `"implementation"` in the back half; the fact records the phase derived from the state, and the two must agree (Task 7). The 22 migrated files add it to their review payloads.
12. **`turn_ended` names the prompt it ended.** The kernel writes `prompt_key` — the key of the newest satisfied `sess-prompt` of the phase and epoch — into every `turn_ended` (Task 7), so "a `turn_ended` newer than its newest satisfied prompt" (§2 Refusals) is an equality on keys, never a comparison of clocks; the deadline of §3 *The turn's start is in the journal* still uses the prompt row's `at_us`, in the coordinator (Task 14).
13. **`revise_bundle` refuses an irrelevant change.** The kernel compares the fetched issue's snapshot hash with the current bundle's and refuses when equal (Task 9); the runner's resume path submits it on every resume and treats that refusal as expected (Task 20).

14. **A vendor is named by its bundle.** The snapshot the server returns names the bundle (`agent_name` is the bundle's `name:`, `v2_author_claude`), the dispatch actor names the vendor (`claude`). The kernel maps one to the other with `front.vendor_of(agent_name)` — the name less the `v2_author_` prefix, `None` for any other bundle — and every guard, the driver, the fake server and the proof compare `vendor_of(snapshot.agent_name)` with the actor (Task 7, 12, 16, 25). Round 12 found the plan comparing the two namespaces directly.
15. **The output guard reads the session's own newest prompt.** §2's refusal quantifies over the round's session ("its newest satisfied sess-prompt"); `record_turn_ended`'s refusal quantifies over the phase ("the newest satisfied sess-prompt of this run in the current phase and epoch"). Task 10 keeps the two apart: `front.newest_prompt_of(session)` for the output guard, `front.newest_prompt(phase, epoch)` for the turn's end.

**Review history.** Round 12 (Kimi, over commit `8a5c738`) found ten things; nine are folded here (the vendor namespace, the append-only effects table in Task 16's tests, the missing halt on a dispatch over pending effects, the untested half of ruling 2, the output guard's quantifier, `prompt_item` uniqueness per session, the CAS-versus-authorize order in migration rule 2, a driver method used before it was defined, and the provenance rows distributed into Tasks 7-9); the tenth located the loopback test in Task 15.

## §11 closure map

| §11 test | Task |
|---|---|
| `test_output_refused_before_turn_ended`, `test_output_refused_while_stop_intended` | Task 10 |
| `test_turn_end_matrix`, `test_displaced_before_author_empty_and_questions` | Task 14 |
| `test_read_after_stop_partial_bytes_hashed` | Task 17 |
| `test_direction_ordering_by_seq` | Task 10 |
| `test_reconcile_title_distinguishes_same_tuple`, `test_reconcile_host_id_prefix_both_ways` | Task 4 |
| `test_contract_d_twice_refused`, `test_contract_d_equals_refused`, `test_one_parse_three_readers` | Task 2 |
| `test_executor_builds_stop_body` | Task 3 |
| `test_reconcile_stop_without_id`, `test_reconcile_create_without_id_refused` | Task 4 |
| `test_worktree_path_exists_rc_failed` | Task 15 |
| `test_agent_id_mismatch_at_poll_rc_failed` | Task 14 |
| `test_stop_no_runner_confirms`, `test_stop_503_halts` | Task 15 |

## File structure

Kernel (`v2/kernel/`): `events.py` (kinds), `schema.sql` + `store.py` (phase artefacts, readers), `contract.py` (three anchored session rules, one parser), `effects.py` (obligation replay, body pre-check, typed reconciliation), `cli.py` (`make_executor`, typed `reconcile`), `bundle.py` (canon v2, `is_bircher_status`), `policy.py` **new** (derive/freeze), `enqueue.py` (`create_run`), `dispatch.py` (`author` role, seat bound, pending-effect halt), `front.py` **new** (front-half journal queries used by authz and commands), `authz.py` (state table, front-half refusals), `commands.py` (command set, side facts, `execute_as_human`), `brief.py` **new** (review brief rendering), `grill.py` (question/ruling/answer payloads).

Coordinator (`v2/coordinator/`): `session.py` (`State.agent_id`, `wait_turn`, session effect helpers, worktrees), `effects.py` (`obligation`/`body` through `perform_effect`), `phases.py` **new** (`Ctx`, loop, `retire_owed`, `publish_owed`), `author.py` **new**, `human.py` **new**, `review.py` (artefact mode), `cli.py` (`phases`, `retire`, `approve`, `grant-round`, `revise`, `direct`, `cancel`, `parked`), `recover.py`/`observe.py` + `kernel/projection.py` (phase filter on verdict consumers).

Runner: `agents/v2_author_claude/`, `agents/v2_author_codex/`, `skills/spec-author/`, `skills/plan-author/`, `batch/lib/kernel-client.sh`, `batch/run-queue.sh`, `docs/design/ARCHITECTURE.md`, `docs/design/provenance-table.md`, `skills/muesli-loop/SKILL.md`.

Tests: `v2/tests/kernel/front.py` **new** (the front-half driver every migrated test uses), new files named per task.

---
## Part A — Kernel substrate

### Task 1: Fact kinds, phase-artefact table, store readers

**Files:**
- Modify: `v2/kernel/events.py` (class `EventKind`, dict `SCHEMA_VERSIONS`)
- Modify: `v2/kernel/schema.sql` (append one table after `dispatches`)
- Modify: `v2/kernel/store.py:45-52` (`__init__`, `open`), append new methods after `facts_for`
- Test: `v2/tests/kernel/test_store_front.py`

**Interfaces:**
- Consumes: `kernel.store.Store` (existing), `kernel.events.EventKind`.
- Produces:
  - `EventKind.POLICY_FROZEN = "policy_frozen"`, `ARTIFACT_SUBMITTED = "artifact_submitted"`, `REVIEW_BRIEF_ISSUED = "review_brief_issued"`, `PARKED = "parked"`, `TURN_ENDED = "turn_ended"`, `AUTHOR_EMPTY = "author_empty"`, `HUMAN_ANSWER = "human_answer"`, `MODEL_RULING = "model_ruling"`, `HUMAN_DIRECTION = "human_direction"`, `HUMAN_ITEM_DISMISSED = "human_item_dismissed"`, `PROMPT_ITEM = "prompt_item"`, each with `SCHEMA_VERSIONS[kind] == 1`.
  - `Store.path: str | None` (set by `open`, `None` when constructed from a connection).
  - `Store.read_blob(content_hash: str) -> bytes | None`
  - `Store.set_phase_artifact(run_id: str, phase: str, content_hash: str) -> None` (upsert)
  - `Store.phase_artifact(run_id: str, phase: str) -> str | None`
  - `Store.facts_of_kind(run_id: str, *kinds: str) -> list[Fact]` (seq order)
  - `Store.newest_fact(run_id: str, *kinds: str) -> Fact | None`
  - `Store.effects_for(run_id: str) -> list[dict]` with keys `idempotency_key, effect_class, generation, state, external_object_id, intent, at_us` in journal order
  - `Store.effect_by_key(...)` now also returns `intent: dict` and `generation: int`
  - `Store.dispatches_for(run_id: str) -> list[dict]` with keys `generation, actor, role, at_us` in generation order
  - `Store.open_run_ids(prefix: str, states: frozenset[str]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_store_front.py
"""Task 1: front-half fact kinds, phase artefacts and journal readers."""
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_store_front.py -q`
Expected: FAIL — `AttributeError: type object 'EventKind' has no attribute 'POLICY_FROZEN'`, then `AttributeError: 'Store' object has no attribute 'path'` etc. once the kinds exist.

- [ ] **Step 3: Declare the kinds**

In `v2/kernel/events.py`, add to `class EventKind` after `SHADOW_REJECTED`:

```python
    # Front half (spec §2). Each is written by exactly one command; see
    # kernel/commands.py::_side_fact.
    POLICY_FROZEN = "policy_frozen"
    ARTIFACT_SUBMITTED = "artifact_submitted"
    REVIEW_BRIEF_ISSUED = "review_brief_issued"
    PARKED = "parked"
    TURN_ENDED = "turn_ended"
    AUTHOR_EMPTY = "author_empty"
    HUMAN_ANSWER = "human_answer"
    MODEL_RULING = "model_ruling"
    HUMAN_DIRECTION = "human_direction"
    HUMAN_ITEM_DISMISSED = "human_item_dismissed"
    PROMPT_ITEM = "prompt_item"
```

and add to `SCHEMA_VERSIONS` eleven entries, one per kind, each `: 1`.

- [ ] **Step 4: Add the table**

Append to `v2/kernel/schema.sql` after the `dispatches` table:

```sql
-- One current artefact per phase. Rewritten on every accepted submission;
-- the history is in artifact_submitted facts, this is the projection.
CREATE TABLE IF NOT EXISTS phase_artifacts (
    run_id        TEXT NOT NULL,
    phase         TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, phase)
);
```

- [ ] **Step 5: Add the store methods**

In `v2/kernel/store.py`, change `__init__` and `open`:

```python
    def __init__(self, conn: sqlite3.Connection, clock: Any) -> None:
        self._conn, self._clock = conn, clock
        # Set by open(); the executor mints body files beside the db.
        self.path: str | None = None

    @classmethod
    def open(cls, path: Path | str, clock: Any = None) -> Store:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.executescript(_SCHEMA)
        store = cls(conn, clock or _SystemClock())
        store.path = str(path)
        return store
```

Replace `effect_by_key`'s SELECT and return with:

```python
        row = self._conn.execute(
            "SELECT state, external_object_id, effect_class, intent_json, generation"
            " FROM effects WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        return None if row is None else {
            "state": row[0], "external_object_id": row[1], "effect_class": row[2],
            "intent": json.loads(row[3]), "generation": row[4],
        }
```

Append after `facts_for`:

```python
    def read_blob(self, content_hash: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT bytes FROM artifacts WHERE hash = ?", (content_hash,)
        ).fetchone()
        return None if row is None else bytes(row[0])

    def set_phase_artifact(self, run_id: str, phase: str, content_hash: str) -> None:
        self._conn.execute(
            "INSERT INTO phase_artifacts (run_id, phase, artifact_hash) VALUES (?,?,?)"
            " ON CONFLICT(run_id, phase) DO UPDATE SET artifact_hash = excluded.artifact_hash",
            (run_id, phase, content_hash),
        )

    def phase_artifact(self, run_id: str, phase: str) -> str | None:
        row = self._conn.execute(
            "SELECT artifact_hash FROM phase_artifacts WHERE run_id = ? AND phase = ?",
            (run_id, phase),
        ).fetchone()
        return None if row is None else row[0]

    def facts_of_kind(self, run_id: str, *kinds: str) -> list[Fact]:
        if not kinds:
            return []
        marks = ",".join("?" * len(kinds))
        rows = self._conn.execute(
            "SELECT seq, id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json FROM facts"
            f" WHERE run_id = ? AND kind IN ({marks}) ORDER BY seq",
            (run_id, *kinds),
        ).fetchall()
        return [self._fact_from_row(r) for r in rows]

    def newest_fact(self, run_id: str, *kinds: str) -> Fact | None:
        facts = self.facts_of_kind(run_id, *kinds)
        return facts[-1] if facts else None

    def effects_for(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT idempotency_key, effect_class, generation, state,"
            " external_object_id, intent_json, at_us FROM effects"
            " WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [{
            "idempotency_key": r[0], "effect_class": r[1], "generation": r[2],
            "state": r[3], "external_object_id": r[4],
            "intent": json.loads(r[5]), "at_us": r[6],
        } for r in rows]

    def dispatches_for(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT generation, actor, role, at_us FROM dispatches"
            " WHERE run_id = ? ORDER BY generation",
            (run_id,),
        ).fetchall()
        return [{"generation": r[0], "actor": r[1], "role": r[2], "at_us": r[3]}
                for r in rows]

    def open_run_ids(self, prefix: str, states: frozenset[str]) -> list[str]:
        if not states:
            return []
        marks = ",".join("?" * len(states))
        rows = self._conn.execute(
            "SELECT run_id FROM runs WHERE run_id LIKE ? AND state IN"
            f" ({marks}) ORDER BY created_at_us",
            (prefix + "-%", *sorted(states)),
        ).fetchall()
        return [r[0] for r in rows]
```

`facts_for` already builds `Fact` objects from the same column list; factor its row-to-`Fact` expression into `_fact_from_row(self, r) -> Fact` and call it from both places (a `%` or `_` in `prefix` would be a LIKE wildcard — run ids are `<repo>-<issue>-<n>` from `run-queue.sh`, which never contain either; assert `"%" not in prefix and "_" not in prefix` at the top of `open_run_ids` and raise `ValueError` otherwise).

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_store_front.py -q`
Expected: 8 passed.

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`
Expected: 1060 passed, 2 skipped.

- [ ] **Step 7: Commit**

```bash
git add v2/kernel/events.py v2/kernel/schema.sql v2/kernel/store.py v2/tests/kernel/test_store_front.py
git commit -m "feat(kernel): front-half fact kinds, phase artefacts, journal readers"
```

---

### Task 2: Three anchored session rules and one parser

Spec: §3 *Obligations* ("The contract names endpoints"), §10, §11 rows on `-d`. Ruling 1 applies.

**Files:**
- Modify: `v2/kernel/contract.py` (whole `Rule`, `_flags_and_operands`, `CONTRACTS[SESSION_CONTROL]`, `check`)
- Test: `v2/tests/kernel/test_contract_sessions.py`

**Interfaces:**
- Consumes: `kernel.effects.EffectClass.SESSION_CONTROL` (existing), `kernel.contract.signature`, `_rule_parts` (existing).
- Produces:
  - `@dataclass(frozen=True) Parsed(flags: frozenset[str], methods: frozenset[str], operands: tuple[str, ...], values: dict[str, list[str]], joined: frozenset[str])` — `values[flag]` is the list of values given to a valued flag, in argv order; `joined` is the set of flags that appeared as `flag=value`.
  - `parse(argv: list[str], valued: frozenset[str]) -> Parsed`
  - `Rule` gains `name: str = ""`, `required_any: frozenset[str] = frozenset()`, `required: frozenset[str] = frozenset()`, `once: frozenset[str] = frozenset()`, `forbid_value_prefix: tuple[tuple[str, str], ...] = ()`.
  - `check(effect_class: str, argv: list[str]) -> Rule` — returns the matching rule (previously `None`).
  - `create_body(argv: list[str]) -> dict` — JSON-decodes the single `-d` value of a `sess-create` argv; `ContractViolation` if not exactly one `-d` or not a JSON object.
  - `_CURL_FAIL = frozenset({"-f", "-sf", "-sSf"})`, `_CURL_FLAGS = frozenset({"-s", "-S", "-sf", "-sSf", "-f", "-q", "-X", "-H", "--max-time"})`.
  - Rule names `"sess-create"`, `"sess-events"`, `"sess-delete"`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_contract_sessions.py
"""Task 2: the SESSION_CONTROL contract is three anchored rules over one parse."""
import pytest

import kernel.cli
import kernel.contract
import kernel.effects
from kernel.contract import ContractViolation, check, create_body, parse
from kernel.effects import EffectClass

SC = EffectClass.SESSION_CONTROL
CREATE = '{"agent_id": "ag_1", "host_id": "host_h1", "workspace": "/w", "title": "k1"}'


def _create(*extra, method="POST", url="http://srv/v1/sessions", d=CREATE):
    argv = ["curl", "-sSf", "-X", method, "-H", "content-type: application/json"]
    if d is not None:
        argv += ["-d", d]
    return argv + list(extra) + [url]


def _events(*extra, url="http://srv/v1/sessions/s1/events"):
    return ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
            *extra, url]


def _delete(url="http://srv/v1/sessions/s1", fail="-sSf"):
    return ["curl", fail, "-X", "DELETE", url]


def test_three_rules_match_by_name():
    assert check(SC, _create()).name == "sess-create"
    assert check(SC, _events()).name == "sess-events"
    assert check(SC, _delete()).name == "sess-delete"


def test_contract_d_twice_refused():
    with pytest.raises(ContractViolation):
        check(SC, _create("-d", CREATE))
    with pytest.raises(ContractViolation):
        check(SC, _events("-d", "{}", "-d", "{}"))


def test_contract_d_equals_refused():
    argv = ["curl", "-sSf", "-X", "POST", f"-d={CREATE}", "http://srv/v1/sessions"]
    with pytest.raises(ContractViolation):
        check(SC, argv)
    with pytest.raises(ContractViolation):
        check(SC, _events("-d={}"))


def test_create_requires_exactly_one_d():
    with pytest.raises(ContractViolation):
        check(SC, _create(d=None))


def test_d_file_value_refused_both_endpoints():
    with pytest.raises(ContractViolation):
        check(SC, _create(d="@/tmp/body.json"))
    with pytest.raises(ContractViolation):
        check(SC, _events("-d", "@/tmp/body.json"))


def test_events_accepts_one_d_from_runner_templates():
    # Ruling 1: the runner's _send_prompt/_stop_session keep their inline -d.
    assert check(SC, _events("-d", '{"type": "stop_session"}')).name == "sess-events"


def test_data_binary_is_unlisted_from_every_caller():
    with pytest.raises(ContractViolation):
        check(SC, _events("--data-binary", "@/x"))
    with pytest.raises(ContractViolation):
        check(SC, _create("--data-binary", "@/x"))


def test_failing_curl_mode_required():
    with pytest.raises(ContractViolation):
        check(SC, ["curl", "-s", "-X", "POST", "-d", CREATE, "http://srv/v1/sessions"])
    assert check(SC, ["curl", "-sf", "-X", "POST", "-d", CREATE,
                      "http://srv/v1/sessions"]).name == "sess-create"
    assert check(SC, ["curl", "-s", "-f", "-X", "POST", "-d", CREATE,
                      "http://srv/v1/sessions"]).name == "sess-create"
    with pytest.raises(ContractViolation):
        check(SC, _delete(fail="-s"))


@pytest.mark.parametrize("argv", [
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/switch-agent"],
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/fork"],
    ["curl", "-sSf", "-X", "PATCH", "http://srv/v1/sessions/s1"],
    ["curl", "-sSf", "-X", "DELETE", "http://srv/v1/sessions"],
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1"],
    ["curl", "-sSf", "-X", "POST", "-d", CREATE, "http://srv/v1/sessions/"],
    ["curl", "-sSf", "-X", "POST", "-F", "a=b", "http://srv/v1/sessions"],
    ["curl", "-sSf", "-X", "DELETE", "-d", "{}", "http://srv/v1/sessions/s1"],
])
def test_unanchored_shapes_refused(argv):
    with pytest.raises(ContractViolation):
        check(SC, argv)


def test_parse_reports_values_and_joined():
    p = parse(["curl", "-sSf", "-X", "POST", "-d", "a", "-H", "h: 1", "--max-time=9", "u"],
              frozenset({"-X", "-d", "-H", "--max-time"}))
    assert p.flags == frozenset({"-sSf", "-X", "-d", "-H", "--max-time"})
    assert p.methods == frozenset({"POST"})
    assert p.operands == ("curl", "u")
    assert p.values["-d"] == ["a"]
    assert p.values["-H"] == ["h: 1"]
    assert p.joined == frozenset({"--max-time"})


def test_create_body_decodes_single_d():
    assert create_body(_create())["agent_id"] == "ag_1"
    with pytest.raises(ContractViolation):
        create_body(_create(d="[1]"))
    with pytest.raises(ContractViolation):
        create_body(_create(d="not json"))


def test_one_parse_three_readers():
    # One parser (contract.parse) is what the contract, the executor and the
    # reconciler read; none has a second reading of argv.
    assert kernel.cli.create_body is kernel.contract.create_body
    assert kernel.effects.create_body is kernel.contract.create_body
    assert kernel.cli.parse is kernel.contract.parse
    assert kernel.effects.parse is kernel.contract.parse
    assert kernel.contract._flags_and_operands.__wrapped_by__ == "parse"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_contract_sessions.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_body' from 'kernel.contract'`.

- [ ] **Step 3: Rewrite the parser and the rule**

In `v2/kernel/contract.py` replace `Rule` and `_flags_and_operands` with:

```python
@dataclass(frozen=True)
class Parsed:
    """One reading of an argv. Every consumer of a flag's value reads it here."""
    flags: frozenset[str]
    methods: frozenset[str]
    operands: tuple[str, ...]
    values: dict[str, list[str]]
    joined: frozenset[str]


def parse(argv: list[str], valued: frozenset[str]) -> Parsed:
    flags: set[str] = set()
    methods: set[str] = set()
    operands: list[str] = []
    values: dict[str, list[str]] = {}
    joined: set[str] = set()
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            operands.append(tok)
            i += 1
            continue
        flag, sep, inline = tok.partition("=")
        flags.add(flag)
        if sep:
            joined.add(flag)
            if flag in valued:
                values.setdefault(flag, []).append(inline)
            if flag in _METHOD_FLAGS:
                methods.add(inline)
            i += 1
            continue
        if flag in valued and i + 1 < len(argv):
            values.setdefault(flag, []).append(argv[i + 1])
            if flag in _METHOD_FLAGS:
                methods.add(argv[i + 1])
            i += 2
            continue
        i += 1
    return Parsed(frozenset(flags), frozenset(methods), tuple(operands), values,
                  frozenset(joined))


def _flags_and_operands(argv: list[str], valued: frozenset[str]):
    p = parse(argv, valued)
    return p.flags, p.methods, list(p.operands)


_flags_and_operands.__wrapped_by__ = "parse"


@dataclass(frozen=True)
class Rule:
    sig: str
    url_path: str | None = None
    flags: frozenset[str] = frozenset()
    valued: frozenset[str] = frozenset()
    methods: frozenset[str] = frozenset()
    forbid_operand_prefix: tuple[str, ...] = ()
    operand_count: int | None = None
    schemes: frozenset[str] = frozenset({"http", "https"})
    name: str = ""
    required_any: frozenset[str] = frozenset()   # at least one of these flags
    required: frozenset[str] = frozenset()       # every one of these flags
    once: frozenset[str] = frozenset()           # exactly once, own token, never flag=value
    forbid_value_prefix: tuple[tuple[str, str], ...] = ()  # (flag, prefix) pairs
```

Keep the existing `merge_target` calling `_flags_and_operands` unchanged.

- [ ] **Step 4: Replace the SESSION_CONTROL rules**

```python
_CURL_FAIL = frozenset({"-f", "-sf", "-sSf"})
_CURL_FLAGS = frozenset({"-s", "-S", "-sf", "-sSf", "-f", "-q", "-X", "-H", "--max-time"})
_CURL_VALUED = frozenset({"-X", "-H", "--max-time"})

_SESSION_RULES = [
    # Create: one -d body, JSON object, never a file. The executor validates
    # the response against this body (kernel/cli.py::validate_create_response).
    Rule("curl", name="sess-create", url_path=r"^/v1/sessions$", operand_count=1,
         flags=_CURL_FLAGS | {"-d"}, valued=_CURL_VALUED | {"-d"}, methods=frozenset({"POST"}),
         required_any=_CURL_FAIL, required=frozenset({"-d"}), once=frozenset({"-d"}),
         forbid_value_prefix=(("-d", "@"),)),
    # Events: the coordinator sends no -d (the kernel composes the body and
    # appends --data-binary after this check); the runner's template sites
    # (_send_prompt, _stop_session) keep their single inline -d. Ruling 1.
    Rule("curl", name="sess-events", url_path=r"^/v1/sessions/[^/]+/events$", operand_count=1,
         flags=_CURL_FLAGS | {"-d"}, valued=_CURL_VALUED | {"-d"}, methods=frozenset({"POST"}),
         required_any=_CURL_FAIL, once=frozenset({"-d"}),
         forbid_value_prefix=(("-d", "@"),)),
    Rule("curl", name="sess-delete", url_path=r"^/v1/sessions/[^/]+$", operand_count=1,
         flags=_CURL_FLAGS, valued=_CURL_VALUED, methods=frozenset({"DELETE"}),
         required_any=_CURL_FAIL),
]
```

and set `CONTRACTS[EffectClass.SESSION_CONTROL] = _SESSION_RULES` (replacing the single unanchored rule). `-F` is gone; `--data-binary` is unlisted and therefore refused by the existing "unlisted flag" branch.

- [ ] **Step 5: Extend `check` and add `create_body`**

In `check`, after the existing `flags`/`methods`/`operand_count`/`forbid_operand_prefix` checks for a rule (all now read from `p = parse(parts, rule.valued)`), add before the rule is accepted:

```python
            if rule.required_any and not (p.flags & rule.required_any):
                reasons.append(f"{rule.name}: needs one of {sorted(rule.required_any)}")
                continue
            missing = rule.required - p.flags
            if missing:
                reasons.append(f"{rule.name}: missing {sorted(missing)}")
                continue
            bad_once = [f for f in rule.once
                        if f in p.flags and (len(p.values.get(f, [])) != 1 or f in p.joined)]
            if bad_once:
                reasons.append(f"{rule.name}: {bad_once} must appear exactly once as its own token")
                continue
            bad_prefix = [f for f, pre in rule.forbid_value_prefix
                          if any(v.startswith(pre) for v in p.values.get(f, []))]
            if bad_prefix:
                reasons.append(f"{rule.name}: {bad_prefix} value may not start with a file/prefix")
                continue
            return rule
```

The `once` check counts values, so `-d a -d b` (two values) and `-d=a` (joined) both refuse. `url_path` matching keeps `re.search`; the three patterns are anchored with `^…$` so `/v1/sessions/s1/switch-agent` and `/v1/sessions/` match none. Change the function's declared return from `None` to `Rule` and the final `raise ContractViolation(...)` stays.

Append:

```python
def create_body(argv: list[str]) -> dict:
    """The JSON body of a sess-create argv, from the single -d the rule admits."""
    p = parse(argv, _CURL_VALUED | {"-d"})
    vals = p.values.get("-d", [])
    if len(vals) != 1:
        raise ContractViolation(f"sess-create needs exactly one -d, got {len(vals)}")
    try:
        body = json.loads(vals[0])
    except ValueError as exc:
        raise ContractViolation(f"sess-create body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ContractViolation("sess-create body must be a JSON object")
    return body
```

(add `import json` at the top). In `v2/kernel/cli.py` and `v2/kernel/effects.py` add `from kernel.contract import create_body, parse` — the names are re-exported so `test_one_parse_three_readers` binds them by identity; Tasks 3 and 4 use them.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_contract_sessions.py tests/kernel/test_effect_contract.py -q`
Expected: all pass. `tests/kernel/conftest.py::valid_argv` for SESSION_CONTROL is `curl -sf -X DELETE http://srv/v1/sessions/1`, which matches `sess-delete`. If any existing test built a SESSION_CONTROL argv with `-F` or an unanchored path, change the test's argv to one of the three shapes above — the old shape was the defect.

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`
Expected: green.

- [ ] **Step 7: Mutation check, then commit**

Temporarily change `sess-events`'s `url_path` to `r"/v1/sessions/[^/]+/events$"` (drop `^`): `test_unanchored_shapes_refused` must still pass on every row (it does — the leading slash still anchors after the host); now drop the `$` from `sess-delete`: the `DELETE /v1/sessions` row must still refuse (no method match), and `test_unanchored_shapes_refused[…switch-agent]` must fail if you also drop the `$` from `sess-create`… it does not, because `switch-agent` is a POST to a path ending `/switch-agent`, which `^/v1/sessions$` never matches. The check that binds is: drop `once` from `sess-create` → `test_contract_d_twice_refused` fails. Restore, then:

```bash
git add v2/kernel/contract.py v2/kernel/cli.py v2/kernel/effects.py v2/tests/kernel/test_contract_sessions.py
git commit -m "feat(kernel): anchor the session contract to create, events and delete"
```

---
### Task 3: Obligation replay, body pre-check, the composing executor

Spec: §3 *Sessions are effects* (obligations derived from intents; a confirmed `sess-create` holds a snapshot by construction), §3 *Review round* (*The body never rides argv*), §7 rows *Session creation fails*, *Plan brief larger than one argv string*.

**Files:**
- Modify: `v2/kernel/effects.py:147-285` (`_perform_unhalted`), add `_check_body`
- Modify: `v2/kernel/cli.py:91-113` (`_executor`), `:232-260` (`_do_effect`)
- Modify: `v2/coordinator/effects.py:50-74` (`perform_effect`)
- Test: `v2/tests/kernel/test_executor_body.py`

**Interfaces:**
- Consumes: `kernel.contract.check(effect_class, argv) -> Rule`, `kernel.contract.create_body(argv) -> dict` (Task 2); `Store.read_blob`, `Store.path`, `Store.effect_by_key(...)["intent"]` (Task 1).
- Produces:
  - An intent may carry `obligation: dict` and `body: {"artifact": <sha256>} | {"event": "stop_session"}`.
  - `kernel.effects._check_body(store, intent) -> None` — `ValueError` before any row is written.
  - `kernel.effects._perform_unhalted` raises `NotReplayable` when an existing row's `intent.get("obligation")` differs from the request's.
  - `kernel.cli.make_executor(store) -> Callable[[str, dict, str], str]`; `kernel.cli._executor = make_executor(None)` stays for callers with no store (a body intent is refused there).
  - `kernel.cli.compose_event(store, body: dict) -> bytes`
  - `kernel.cli.validate_create_response(stdout: str, body: dict) -> str` (the projected snapshot as sorted JSON)
  - `kernel.cli.session_projection(snap: dict) -> dict` with keys `id, agent_id, agent_name, host_id, workspace, title`
  - `kernel.cli.SESSION_PROJECTION_KEYS`
  - `coordinator.effects.perform_effect(effect_class, key, argv, *, timeout=None, env=None, obligation=None, body=None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_executor_body.py
"""Task 3: bodies are composed by the kernel from the journal, never from argv."""
import json
import os
import subprocess

import pytest

import kernel.cli
from kernel.artifacts import put_artifact
from kernel.cli import make_executor, session_projection, validate_create_response
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, NotReplayable, _check_body, perform
from kernel.store import Store

EVENTS = ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
          "http://srv/v1/sessions/s1/events"]
CREATE_BODY = {"agent_id": "ag_1", "host_id": "host_h1", "workspace": "/w",
               "title": "sess-create:r-1:1"}
CREATE = ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
          "-d", json.dumps(CREATE_BODY), "http://srv/v1/sessions"]
SNAP = {"id": "s1", "agent_id": "ag_1", "agent_name": "v2_author_claude",
        "host_id": "h1", "workspace": "/w", "title": "sess-create:r-1:1",
        "items": [{"role": "user"}]}


def _store(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    return s


def _fake_run(seen, stdout="ok"):
    def run(argv, capture_output, text):
        assert capture_output and text
        seen["argv"] = list(argv)
        if argv[-2:-1] == ["--data-binary"]:
            path = argv[-1][1:]
            seen["path"] = path
            with open(path, "rb") as fh:
                seen["bytes"] = fh.read()
            seen["mode"] = oct(os.stat(path).st_mode & 0o777)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return run


def test_executor_builds_stop_body(tmp_path, monkeypatch):
    s = _store(tmp_path)
    seen = {}
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run(seen, '{"queued": false}'))
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    ex = make_executor(s)
    out = ex(EffectClass.SESSION_CONTROL,
             {"argv": EVENTS, "body": {"event": "stop_session"}}, "sess-stop:s1:1")
    assert json.loads(seen["bytes"]) == {"type": "stop_session"}
    assert seen["mode"] == "0o600"
    assert os.path.dirname(seen["path"]) == os.path.dirname(s.path)
    assert not os.path.exists(seen["path"])            # unlinked after the call
    assert seen["argv"][-2] == "--data-binary"
    assert all("stop_session" not in a for a in seen["argv"][:-1])
    assert out == '{"queued": false}'


def test_executor_builds_prompt_body_from_artifact(tmp_path, monkeypatch):
    s = _store(tmp_path)
    text = "brief " * 40_000                           # ~240 KB
    h = put_artifact(s, text.encode())
    seen = {}
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run(seen))
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    make_executor(s)(EffectClass.SESSION_CONTROL,
                     {"argv": EVENTS, "body": {"artifact": h}}, "sess-prompt:s1:1:f1")
    ev = json.loads(seen["bytes"])
    assert ev["type"] == "message"
    assert ev["data"]["role"] == "user"
    assert ev["data"]["content"] == [{"type": "input_text", "text": text}]
    assert max(len(a) for a in seen["argv"]) < 128 * 1024


def test_executor_body_file_removed_on_failure(tmp_path, monkeypatch):
    s = _store(tmp_path)
    seen = {}

    def failing(argv, capture_output, text):
        seen["path"] = argv[-1][1:]
        return subprocess.CompletedProcess(argv, 22, stdout="", stderr="404")
    monkeypatch.setattr(kernel.cli.subprocess, "run", failing)
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    with pytest.raises(RuntimeError):
        make_executor(s)(EffectClass.SESSION_CONTROL,
                         {"argv": EVENTS, "body": {"event": "stop_session"}}, "k")
    assert not os.path.exists(seen["path"])


def test_executor_without_store_refuses_body():
    with pytest.raises(RuntimeError):
        kernel.cli._executor(EffectClass.SESSION_CONTROL,
                             {"argv": EVENTS, "body": {"event": "stop_session"}}, "k")


def test_create_response_validated(tmp_path, monkeypatch):
    s = _store(tmp_path)
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run({}, json.dumps(SNAP)))
    out = make_executor(s)(EffectClass.SESSION_CONTROL, {"argv": CREATE}, "sess-create:r-1:1")
    assert json.loads(out) == session_projection(SNAP)
    assert "items" not in json.loads(out)
    for bad in ('{"error": "not found"}', json.dumps({**SNAP, "agent_id": "ag_2"}),
                json.dumps({**SNAP, "title": "other"}), "[]", "not json", ""):
        monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run({}, bad))
        with pytest.raises(RuntimeError):
            make_executor(s)(EffectClass.SESSION_CONTROL, {"argv": CREATE}, "sess-create:r-1:2")


def test_validate_create_response_requires_agent_name():
    with pytest.raises(RuntimeError):
        validate_create_response(json.dumps({**SNAP, "agent_name": None}), CREATE_BODY)


def test_body_artifact_missing_refused_before_journal(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"artifact": "f" * 64}})
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"event": "switch_agent"}})
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"artifact": "f" * 64, "event": "stop_session"}})
    _check_body(s, {"argv": EVENTS})                   # no body: nothing to check
    gen = dispatch(s, "r-1", actor="claude", role=Role.AUTHOR).generation
    with pytest.raises(ValueError):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-prompt:s1:1:f1",
                {"argv": EVENTS, "body": {"artifact": "f" * 64}}, lambda *a: "never")
    assert s.effects_for("r-1") == []


def test_obligation_mismatch_is_not_replayable(tmp_path):
    s = _store(tmp_path)
    gen = dispatch(s, "r-1", actor="claude", role=Role.AUTHOR).generation
    intent = {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}}
    assert perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", intent,
                   lambda *a: "ok") == "ok"
    same = perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", intent,
                   lambda *a: "never")
    assert same == "ok"
    other = {**intent, "obligation": {**intent["obligation"], "cause": "f2"}}
    with pytest.raises(NotReplayable):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", other,
                lambda *a: "never")
    with pytest.raises(NotReplayable):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1",
                {"argv": EVENTS}, lambda *a: "never")
```

`Role.AUTHOR` does not exist until Task 6; for this task use `Role.IMPLEMENTER` in the two `dispatch` calls and switch them to `Role.AUTHOR` in Task 6 (Task 6 lists this file).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_executor_body.py -q`
Expected: FAIL — `ImportError: cannot import name 'make_executor' from 'kernel.cli'`.

- [ ] **Step 3: Add `_check_body` and the obligation comparison to `effects.py`**

Above `_perform_unhalted`:

```python
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _check_body(store, intent: dict) -> None:
    """Refuse a body the executor could not compose, BEFORE any row exists.

    A body is {"artifact": <sha256 the kernel holds>} or
    {"event": "stop_session"}; anything else is a request the journal would
    record and the executor could not honour -- an `intended` row with no
    way to confirm it.
    """
    body = intent.get("body")
    if body is None:
        return
    if not isinstance(body, dict) or len(body) != 1:
        raise ValueError("intent body must be {'artifact': <sha256>} or {'event': 'stop_session'}")
    if "artifact" in body:
        h = body["artifact"]
        if not isinstance(h, str) or not _HEX64.match(h) or not store.has_artifact(h):
            raise ValueError(f"intent body names artifact {h!r}, which the kernel does not hold")
        return
    if body.get("event") != "stop_session":
        raise ValueError(f"intent body event must be 'stop_session', got {body.get('event')!r}")
```

(`import re` at the top of the module.) In `_perform_unhalted`, immediately after `existing = store.effect_by_key(...)` and inside `if existing is not None:` as its FIRST statement:

```python
        if existing["intent"].get("obligation") != intent.get("obligation"):
            # Keys name attempts; obligations are what the attempt was for.
            # The same key under a different cause is a second obligation
            # asking to be read as satisfied by the first (spec §3).
            raise NotReplayable(
                f"{idempotency_key} in run {run_id} was journaled for obligation "
                f"{existing['intent'].get('obligation')!r}, not {intent.get('obligation')!r}"
            )
```

and immediately before `eid = new_id("eff")`:

```python
    _check_body(store, intent)
```

- [ ] **Step 4: Replace `_executor` in `kernel/cli.py`**

```python
SESSION_PROJECTION_KEYS = ("id", "agent_id", "agent_name", "host_id", "workspace", "title")


def session_projection(snap: dict) -> dict:
    """The six fields the guards read; a session that has run carries its
    whole transcript under `items`, which the journal does not want."""
    return {k: snap.get(k) for k in SESSION_PROJECTION_KEYS}


def validate_create_response(stdout: str, body: dict) -> str:
    """A confirmed create holds a snapshot by construction (spec §3)."""
    try:
        snap = json.loads(stdout)
    except ValueError as exc:
        raise RuntimeError(f"sess-create returned non-JSON: {stdout[:200]!r}") from exc
    if not isinstance(snap, dict) or not snap.get("id") or not snap.get("agent_name"):
        raise RuntimeError(f"sess-create response is not a session snapshot: {stdout[:200]!r}")
    if snap.get("agent_id") != body.get("agent_id"):
        raise RuntimeError(
            f"sess-create bound agent {snap.get('agent_id')!r}, requested {body.get('agent_id')!r}")
    if "title" in body and snap.get("title") != body["title"]:
        raise RuntimeError(
            f"sess-create titled {snap.get('title')!r}, requested {body['title']!r}")
    return json.dumps(session_projection(snap), sort_keys=True)


def compose_event(store, body: dict) -> bytes:
    """The events-endpoint JSON for an intent body. Read from the journal:
    the prompt text is the artefact the intent names, never an argv string."""
    if "artifact" in body:
        data = store.read_blob(body["artifact"])
        if data is None:
            raise RuntimeError(f"artifact {body['artifact']} is not held")
        event = {"type": "message",
                 "data": {"role": "user",
                          "content": [{"type": "input_text", "text": data.decode("utf-8")}]}}
        return json.dumps(event).encode("utf-8")
    return json.dumps({"type": "stop_session"}).encode("utf-8")


def _write_event_file(store, body: dict) -> str:
    fh = tempfile.NamedTemporaryFile(
        dir=os.path.dirname(os.path.abspath(store.path)), prefix="event-",
        suffix=".json", delete=False)
    with fh:
        os.chmod(fh.name, 0o600)
        fh.write(compose_event(store, body))
    return fh.name


def make_executor(store):
    """The real executor, closed over the store the body is composed from.

    `check=False` plus an explicit raise: the raised message carries the
    command's rc and stderr, which the journal records as the halt's detail.
    Every executor failure -- a clean non-zero exit as much as a crash --
    becomes `effect_uncertain` and halts the run (spec §7, *Session creation
    fails*).
    """
    from kernel.contract import check

    def executor(effect_class, intent, idempotency_key):
        argv = list(intent["argv"])
        rule = check(effect_class, argv)
        body = intent.get("body")
        extra: list[str] = []
        path = None
        try:
            if body is not None:
                if store is None:
                    raise RuntimeError("an intent with a body needs the store it is composed from")
                path = _write_event_file(store, body)
                extra = ["--data-binary", f"@{path}"]
            r = subprocess.run(resolve_command(argv + extra), capture_output=True, text=True)
        finally:
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        if r.returncode != 0:
            raise RuntimeError(
                f"{effect_class} failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        out = r.stdout.strip()
        if rule.name == "sess-create":
            return validate_create_response(out, create_body(argv))
        return out or "ok"

    return executor


_executor = make_executor(None)
```

(`import tempfile` at the top; `create_body` is imported from `kernel.contract` in Task 2.) In `_do_effect`, replace `_executor` with `make_executor(store)`. `check` is re-run here rather than trusting `perform`'s: the executor is what appends `--data-binary`, and a rule name read from the caller would be a label the caller picks.

- [ ] **Step 5: Extend `coordinator.effects.perform_effect`**

```python
def perform_effect(effect_class: str, key: str, argv, *, timeout=None, env=None,
                   obligation: dict | None = None, body: dict | None = None) -> str:
```

Under `KERNEL` mode build the intent as

```python
    intent = {"argv": list(argv)}
    if obligation is not None:
        intent["obligation"] = obligation
    if body is not None:
        intent["body"] = body
    return perform(store, _required(env, "BIRCHER_RUN_ID"),
                   int(_required(env, "BIRCHER_GENERATION")),
                   effect_class, key, intent, make_executor(store))
```

importing `make_executor` in place of `_executor`. Under `LEGACY` mode, `body is not None` raises `EffectDenied("a body-bearing effect has no unjournalled form")` — legacy has no store to compose from.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_executor_body.py tests/kernel/test_effects.py tests/coordinator -q`
Expected: all pass. Then the full suite: green.

- [ ] **Step 7: Mutation check, then commit**

Move `_check_body(store, intent)` to after `store.journal_intent(...)`: `test_body_artifact_missing_refused_before_journal` fails on `assert s.effects_for("r-1") == []`. Restore.

```bash
git add v2/kernel/effects.py v2/kernel/cli.py v2/coordinator/effects.py v2/tests/kernel/test_executor_body.py
git commit -m "feat(kernel): compose event bodies from the journal; validate create snapshots"
```

---

### Task 4: Typed reconciliation

Spec: §3 *Reconciliation is typed, per key*; §7 rows *Effect reconciled delivered/not_delivered*, *Stop answered 404*.

**Files:**
- Modify: `v2/kernel/effects.py:287-380` (`reconcile_many`, `reconcile`), add `Resolution`, `reconcile_typed`, `_delivered_value`
- Modify: `v2/kernel/cli.py` (`reconcile` subparser, `_do_reconcile`)
- Test: `v2/tests/kernel/test_reconcile_typed.py`

**Interfaces:**
- Consumes: `kernel.contract.create_body`, `kernel.cli.session_projection`, `Store.effect_by_key(...)["intent"]`.
- Produces:
  - `@dataclass(frozen=True) Resolution(key: str, delivered: bool, value: str | None = None)`
  - `reconcile_typed(store, run_id, items: list[Resolution], expected_version: int) -> int`
  - `EFFECT_RECONCILED` payload `{"resolution": "delivered"|"not_delivered", "external_object_id": str | None}` (actor `human`, `causal_command_id` = key); legacy `reconcile_many` payload unchanged.
  - CLI: `kernel reconcile --db --run-id --expected-version N [--delivered KEY[=VALUE]]… [--not-delivered KEY]…` beside the legacy `--idempotency-key … --resolution …` form; mixing the two forms is a usage error (`RC_USAGE`).

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_reconcile_typed.py
"""Task 4: a reconciliation names what the human saw, per key, typed by obligation."""
import json
import os

import pytest

from kernel.cli import main
from kernel.dispatch import Role, dispatch
from kernel.effects import (EffectClass, Resolution, is_halted, perform,
                            reconcile_many, reconcile_typed)
from kernel.events import EventKind
from kernel.store import Store

EVENTS = ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/events"]


def _body(tmp_path, title="sess-create:r-1:1", workspace=None):
    ws = workspace or os.path.realpath(str(tmp_path / "ws"))
    return {"agent_id": "ag_1", "host_id": "host_h1", "workspace": ws, "title": title}


def _create_argv(body):
    return ["curl", "-sSf", "-X", "POST", "-d", json.dumps(body), "http://srv/v1/sessions"]


def _snap(body, **over):
    return {"id": "s1", "agent_id": body["agent_id"], "agent_name": "v2_author_claude",
            "host_id": "h1", "workspace": body["workspace"], "title": body["title"], **over}


def _boom(*a):
    raise RuntimeError("curl: (7) connection refused")


def _uncertain(s, gen, key, intent, effect_class=EffectClass.SESSION_CONTROL):
    with pytest.raises(Exception):
        perform(s, "r-1", gen, effect_class, key, intent, _boom)
    assert s.effect_by_key(key, run_id="r-1")["state"] == "uncertain"


@pytest.fixture
def run(tmp_path):
    os.makedirs(tmp_path / "ws")
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    gen = dispatch(s, "r-1", actor="claude", role=Role.AUTHOR).generation
    return s, gen


def _create_intent(body):
    return {"argv": _create_argv(body),
            "obligation": {"kind": "sess-create", "run": "r-1", "phase": "spec",
                           "epoch": 0, "cause": "f1"}}


def test_reconcile_title_distinguishes_same_tuple(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    other = json.dumps(_snap(body, title="sess-create:r-1:0"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, other)], s.run_version("r-1"))
    n = reconcile_typed(s, "r-1", [Resolution(body["title"], True, json.dumps(_snap(body)))],
                        s.run_version("r-1"))
    assert n == 1
    row = s.effect_by_key(body["title"], run_id="r-1")
    assert row["state"] == "reconciled"
    assert json.loads(row["external_object_id"])["id"] == "s1"
    assert "items" not in json.loads(row["external_object_id"])
    fact = s.newest_fact("r-1", EventKind.EFFECT_RECONCILED)
    assert fact.actor == "human"
    assert fact.payload["resolution"] == "delivered"
    assert json.loads(fact.payload["external_object_id"])["title"] == body["title"]
    assert not is_halted(s, "r-1")


def test_reconcile_host_id_prefix_both_ways(run, tmp_path):
    s, gen = run
    for i, (req, got) in enumerate([("host_h1", "h1"), ("h1", "host_h1"),
                                    ("host_h1", "host_h1"), ("h1", "h1")]):
        body = _body(tmp_path, title=f"sess-create:r-1:{i}")
        body["host_id"] = req
        _uncertain(s, gen, body["title"], _create_intent(body))
        assert reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                                     json.dumps(_snap(body, host_id=got)))],
                               s.run_version("r-1")) == 1
    body = _body(tmp_path, title="sess-create:r-1:9")
    body["host_id"] = "host_"
    _uncertain(s, gen, body["title"], _create_intent(body))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                              json.dumps(_snap(body, host_id="host_")))],
                        s.run_version("r-1"))


def test_reconcile_workspace_realpath(run, tmp_path):
    s, gen = run
    os.symlink(tmp_path / "ws", tmp_path / "link")
    body = _body(tmp_path, workspace=str(tmp_path / "link"))
    _uncertain(s, gen, body["title"], _create_intent(body))
    real = os.path.realpath(str(tmp_path / "ws"))
    assert reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                                 json.dumps(_snap(body, workspace=real)))],
                           s.run_version("r-1")) == 1


def test_reconcile_delivered_mismatched_agent_id_refused(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    for bad in [_snap(body, agent_id="ag_2"), _snap(body, agent_name=None),
                _snap(body, id=""), _snap(body, workspace="/elsewhere")]:
        with pytest.raises(ValueError):
            reconcile_typed(s, "r-1", [Resolution(body["title"], True, json.dumps(bad))],
                            s.run_version("r-1"))
    assert s.effect_by_key(body["title"], run_id="r-1")["state"] == "uncertain"
    assert is_halted(s, "r-1")


def test_reconcile_create_without_id_refused(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, None)], s.run_version("r-1"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, "s1")], s.run_version("r-1"))


def test_reconcile_stop_without_id(run):
    s, gen = run
    intent = {"argv": EVENTS, "body": {"event": "stop_session"},
              "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}}
    _uncertain(s, gen, "sess-stop:s1:1", intent)
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True, "x")], s.run_version("r-1"))
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True, None)],
                           s.run_version("r-1")) == 1
    assert s.effect_by_key("sess-stop:s1:1", run_id="r-1")["external_object_id"] == "ok"


def test_reconcile_prompt_and_publish_take_a_string(run):
    s, gen = run
    _uncertain(s, gen, "sess-prompt:s1:1:f1",
               {"argv": EVENTS, "obligation": {"kind": "sess-prompt", "session": "s1",
                                               "phase": "spec", "epoch": 0, "cause": "f1"}})
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-prompt:s1:1:f1", True, None)],
                        s.run_version("r-1"))
    assert reconcile_typed(s, "r-1", [Resolution("sess-prompt:s1:1:f1", True, "item_9")],
                           s.run_version("r-1")) == 1
    assert s.effect_by_key("sess-prompt:s1:1:f1", run_id="r-1")["external_object_id"] == "item_9"


def test_not_delivered_stores_nothing(run):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", False, None)],
                           s.run_version("r-1")) == 1
    row = s.effect_by_key("sess-stop:s1:1", run_id="r-1")
    assert row["external_object_id"] is None
    assert s.newest_fact("r-1", EventKind.EFFECT_RECONCILED).payload == {
        "resolution": "not_delivered", "external_object_id": None}
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", False, "x")], s.run_version("r-1"))


def test_forms_are_by_intent_not_class(run):
    s, gen = run
    # A runner effect: {argv} alone. Typed form refused; legacy form accepted.
    _uncertain(s, gen, "comment:1", {"argv": ["gh", "issue", "comment", "1", "--body", "x"]},
               effect_class=EffectClass.COMMENT)
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("comment:1", False)], s.run_version("r-1"))
    # A coordinator effect: obligation present. Legacy form refused.
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    with pytest.raises(ValueError):
        reconcile_many(s, "r-1", ["sess-stop:s1:1"], "seen", s.run_version("r-1"))
    assert reconcile_many(s, "r-1", ["comment:1"], "seen", s.run_version("r-1")) == 1


def test_batch_is_all_or_nothing_and_keys_once(run):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    _uncertain(s, gen, "sess-stop:s2:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s2", "cause": "f2"}})
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True),
                                   Resolution("sess-stop:s1:1", False)], s.run_version("r-1"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True),
                                   Resolution("sess-stop:s2:1", True, "bad")], s.run_version("r-1"))
    assert s.effect_by_key("sess-stop:s1:1", run_id="r-1")["state"] == "uncertain"
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True)],
                           s.run_version("r-1")) == 1
    assert is_halted(s, "r-1")                          # s2 still pending
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s2:1", False)],
                           s.run_version("r-1")) == 1
    assert not is_halted(s, "r-1")


def test_cli_typed_form(run, tmp_path, capsys):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    _uncertain(s, gen, "sess-prompt:s1:1:f1",
               {"argv": EVENTS, "obligation": {"kind": "sess-prompt", "session": "s1",
                                               "phase": "spec", "epoch": 0, "cause": "f1"}})
    db = str(tmp_path / "k.db")
    rc = main(["reconcile", "--db", db, "--run-id", "r-1",
               "--expected-version", str(s.run_version("r-1")),
               "--delivered", "sess-stop:s1:1", "--not-delivered", "sess-prompt:s1:1:f1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is False and out["pending"] == []
    assert main(["reconcile", "--db", db, "--run-id", "r-1", "--expected-version", "1",
                 "--delivered", "k", "--idempotency-key", "k", "--resolution", "x"]) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reconcile_typed.py -q`
Expected: FAIL — `ImportError: cannot import name 'Resolution' from 'kernel.effects'`.

- [ ] **Step 3: Implement `Resolution`, `_delivered_value`, `reconcile_typed`**

In `v2/kernel/effects.py`:

```python
@dataclass(frozen=True)
class Resolution:
    key: str
    delivered: bool
    value: str | None = None


def _strip_host(h) -> str:
    h = "" if h is None else str(h)
    return h[5:] if h.startswith("host_") else h


def _delivered_value(store, run_id: str, key: str, value: str | None) -> str:
    """What a delivered obligation stores, validated by its kind (spec §3)."""
    from kernel.cli import session_projection
    from kernel.contract import create_body

    row = store.effect_by_key(key, run_id=run_id)
    intent = row["intent"]
    kind = intent["obligation"].get("kind")
    if kind == "sess-stop":
        if value is not None:
            raise ValueError(f"{key}: a sess-stop takes no delivered value")
        return "ok"
    if kind in ("sess-prompt", "publish"):
        if not value:
            raise ValueError(f"{key}: a delivered {kind} needs its item id or URL")
        return value
    if kind == "sess-create":
        try:
            snap = json.loads(value or "")
        except ValueError as exc:
            raise ValueError(f"{key}: a delivered sess-create needs the session snapshot JSON") from exc
        if not isinstance(snap, dict) or not snap.get("id") or not snap.get("agent_name"):
            raise ValueError(f"{key}: snapshot must name id and agent_name")
        body = create_body(intent["argv"])
        if snap.get("agent_id") != body.get("agent_id"):
            raise ValueError(f"{key}: snapshot agent_id {snap.get('agent_id')!r} != create's {body.get('agent_id')!r}")
        want, got = _strip_host(body.get("host_id")), _strip_host(snap.get("host_id"))
        if not want or want != got:
            raise ValueError(f"{key}: snapshot host_id {snap.get('host_id')!r} != create's {body.get('host_id')!r}")
        if os.path.realpath(str(snap.get("workspace") or "")) != os.path.realpath(str(body.get("workspace") or "")):
            raise ValueError(f"{key}: snapshot workspace {snap.get('workspace')!r} != create's {body.get('workspace')!r}")
        if not (snap.get("title") == body.get("title") == key):
            raise ValueError(f"{key}: snapshot title {snap.get('title')!r} must equal the create's title and the key")
        return json.dumps(session_projection(snap), sort_keys=True)
    raise ValueError(f"{key}: obligation kind {kind!r} has no delivered form")


def reconcile_typed(store, run_id, items, expected_version) -> int:
    """Resolve uncertain obligation-bearing effects, one typed result per key.

    All-or-nothing: every key is validated before the CAS; a key named twice,
    a key without an obligation, a result its kind cannot take, or a
    not_delivered with a value refuses the whole batch and writes nothing.
    """
    from kernel.commands import StaleVersion

    items = list(items)
    if not items:
        return 0
    keys = [i.key for i in items]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a key is named twice: {sorted(k for k in keys if keys.count(k) > 1)}")
    stored: dict[str, str | None] = {}
    for item in items:
        row = store.effect_by_key(item.key, run_id=run_id)
        if row is None or row["state"] not in ("uncertain", "intended"):
            raise ValueError(f"cannot reconcile {item.key!r}: state is "
                             f"{None if row is None else row['state']!r}")
        if not isinstance(row["intent"].get("obligation"), dict):
            raise ValueError(f"{item.key!r} carries no obligation; use --idempotency-key/--resolution")
        if item.delivered:
            stored[item.key] = _delivered_value(store, run_id, item.key, item.value)
        else:
            if item.value is not None:
                raise ValueError(f"{item.key}: not_delivered takes no value")
            stored[item.key] = None

    with store.transaction():
        if not store.bump_version_cas(run_id, expected_version):
            raise StaleVersion(f"reconciliation derived from version {expected_version}, which has moved")
        for item in items:
            store.mark_effect(item.key, "reconciled", stored[item.key], run_id=run_id)
            store.append_fact(
                run_id=run_id, kind=EventKind.EFFECT_RECONCILED, actor="human",
                causal_command_id=item.key,
                payload={"resolution": "delivered" if item.delivered else "not_delivered",
                         "external_object_id": stored[item.key]},
            )
        if not pending_reconciliation(store, run_id):
            store.clear_reconciliation(run_id)
    return len(items)
```

(`import os`, `from dataclasses import dataclass` at the top.) In `reconcile_many`'s validation loop add, after the state check:

```python
        row = store.effect_by_key(key, run_id=run_id)
        if isinstance(row["intent"].get("obligation"), dict):
            raise ValueError(f"{key!r} carries an obligation; use --delivered/--not-delivered")
```

`mark_effect`'s third argument becomes the stored value (it already accepts a string; the legacy path keeps passing `None`).

- [ ] **Step 4: The CLI**

In the `reconcile` subparser make `--idempotency-key` and `--resolution` not `required`, and add:

```python
    r.add_argument("--delivered", action="append", default=[], metavar="KEY[=VALUE]")
    r.add_argument("--not-delivered", action="append", default=[], metavar="KEY")
```

In `_do_reconcile`, before opening the store:

```python
    typed = bool(a.delivered or a.not_delivered)
    legacy = bool(a.idempotency_key or a.resolution)
    if typed == legacy:
        print("reconcile takes either --delivered/--not-delivered or"
              " --idempotency-key/--resolution, not both and not neither", file=sys.stderr)
        return RC_USAGE
    items = []
    for spec in a.delivered:
        key, sep, value = spec.partition("=")
        items.append(Resolution(key, True, value if sep else None))
    items += [Resolution(k, False) for k in a.not_delivered]
```

then call `reconcile_typed(store, a.run_id, items, a.expected_version)` when `typed`, else the existing `reconcile_many`. A `VALUE` that begins with `@` is read from that file (the runner hands a session snapshot this way in Task 20): `value = open(value[1:]).read()`.

- [ ] **Step 5: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reconcile_typed.py tests/kernel/test_effects.py tests/execution/test_kernel_client.py -q`
Expected: pass. Full suite: green.

- [ ] **Step 6: Mutation check, then commit**

Replace `_strip_host` with `lambda h: str(h)`: `test_reconcile_host_id_prefix_both_ways` fails on the mixed rows. Remove the `== key` clause of the title check: `test_reconcile_title_distinguishes_same_tuple` fails. Restore both.

```bash
git add v2/kernel/effects.py v2/kernel/cli.py v2/tests/kernel/test_reconcile_typed.py
git commit -m "feat(kernel): typed reconciliation per obligation kind"
```

---
## Part B — Kernel front half

### Task 5: Bundle canon v2, frozen policy, `create_run`

**Files:**
- Modify: `v2/kernel/bundle.py` (`BUNDLE_CANON_VERSION`, `snapshot`, new `is_bircher_status`)
- Create: `v2/kernel/policy.py`
- Modify: `v2/kernel/enqueue.py` (new `create_run`, `NotReplayable` re-exported from `kernel.effects`)
- Create: `v2/tests/fixtures/bircher_status_comments.tsv`
- Test: `v2/tests/kernel/test_bundle_v2.py`, `v2/tests/kernel/test_policy.py`
- Modify: whichever existing test pins `BUNDLE_CANON_VERSION == 1` (find with `grep -rn "BUNDLE_CANON_VERSION\|canon_version" v2/tests`) — the pin moves to 2.

**Interfaces:**
- Consumes: `Store.newest_fact`, `Store.facts_of_kind` (Task 1); `kernel.artifacts.put_artifact(store, data) -> str`; `kernel.canon.canonical_bytes`, `content_hash`, `canonical_hash`; `EventKind.POLICY_FROZEN` (Task 1); `kernel.effects.NotReplayable`.
- Produces:
  - `bundle.BIRCHER_STATUS_PREFIXES: tuple[str, ...]` (five exact prefixes), `bundle.is_bircher_status(body: str) -> bool`, `bundle.BIRCHER_LABEL_PREFIX = "bircher:"`, `bundle.snapshot(issue) -> dict` at canon version 2.
  - `policy.Policy` (frozen dataclass: `grill: str = "model"`, `gates: frozenset[str] = frozenset({"spec"})`, `max_rounds: int = 3`, `max_seats: int = 16`), `policy.derive(labels: Iterable[str], project_config: dict) -> Policy`, `policy.to_payload(p) -> dict`, `policy.from_payload(d) -> Policy`, `policy.freeze(store, run_id, *, labels, project_config) -> Policy`, `policy.policy_of(store, run_id) -> Policy`, `policy.policy_version(store, run_id) -> int | None` (the `seq` of the run's `policy_frozen` fact), exception `policy.PolicyFrozen`.
  - `enqueue.create_run(store, *, run_id, base_repo, base_sha, issue: dict, project_config: dict) -> dict` returning `{"run_id", "bundle_hash", "policy", "replayed"}`.

- [ ] **Step 1: Write the fixture and the bundle tests**

`v2/tests/fixtures/bircher_status_comments.tsv` — tab-separated, first column `drop` or `keep`, second the comment body verbatim (tabs inside a body are not needed). Both the Python test here and the bash self-test in Task 20 read this one file.

```
drop	bircher: outcome=merged pr=#12
drop	  bircher: outcome=escalated
drop	bircher-status: running
drop	Outcome derived from the repository state: merged.
drop	Cross-vendor review (outcome derived from the PR): accept
drop	bircher: published spec 3f9a1c2e — see the attached artefact
keep	I think the bircher: outcome=merged line above is wrong, the PR was reverted
keep	bircher: could you also handle the dark theme?
keep	published spec looks good to me
keep	
```

`v2/tests/kernel/test_bundle_v2.py`:

```python
import pathlib

from kernel import bundle

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "bircher_status_comments.tsv"


def _issue(**over):
    base = {
        "number": 7, "title": "T", "body": "B",
        "labels": ["bug", "bircher:queued"],
        "comments": [{"id": 1, "author": "alice", "body": "please fix"}],
        "updated_at": "2026-09-06T00:00:00Z",
    }
    base.update(over)
    return base


def test_canon_is_version_2_and_freezes_the_same_fields():
    assert bundle.BUNDLE_CANON_VERSION == 2
    assert bundle.FROZEN_FIELDS == ("number", "title", "body", "labels", "comments")
    assert bundle.snapshot(_issue())["canon_version"] == 2


def test_fixture_drives_is_bircher_status():
    rows = [line.split("\t", 1) for line in FIXTURE.read_text().splitlines()]
    assert len(rows) >= 10
    for verdict, body in rows:
        assert bundle.is_bircher_status(body) is (verdict == "drop"), body


def test_prefixes_are_exactly_five_and_not_the_generic_one():
    assert bundle.BIRCHER_STATUS_PREFIXES == (
        "bircher: outcome=", "bircher-status:",
        "Outcome derived from the repository",
        "Cross-vendor review (outcome derived",
        "bircher: published ",
    )
    assert "bircher: " not in bundle.BIRCHER_STATUS_PREFIXES


def test_bircher_labels_and_status_comments_are_not_frozen():
    snap = bundle.snapshot(_issue())
    assert snap["labels"] == ["bug"]
    with_status = _issue(comments=[
        {"id": 1, "author": "alice", "body": "please fix"},
        {"id": 2, "author": "bircher", "body": "bircher: outcome=merged"},
        {"id": 3, "author": "bircher", "body": "bircher: published plan abc12345\n\n### Task 1"},
    ])
    assert [c["id"] for c in bundle.snapshot(with_status)["comments"]] == [1]


def test_every_bircher_mutation_is_not_a_relevant_change():
    fetched = _issue()
    flipped = _issue(labels=["bug", "bircher:running"])
    assert bundle.is_relevant_change(fetched, flipped) is False
    outcome = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: outcome=escalated"}])
    assert bundle.is_relevant_change(fetched, outcome) is False
    published = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: published spec 3f9a1c2e"}])
    assert bundle.is_relevant_change(fetched, published) is False
    all_three = _issue(labels=["bug", "bircher:parked"], comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: outcome=escalated"},
        {"id": 10, "author": "bircher", "body": "bircher: published spec 3f9a1c2e"}])
    assert bundle.is_relevant_change(fetched, all_three) is False


def test_a_human_comment_is_a_relevant_change_even_when_it_quotes_a_marker():
    fetched = _issue()
    human = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "alice", "body": "the bircher: outcome=merged above is wrong"}])
    assert bundle.is_relevant_change(fetched, human) is True
    label = _issue(labels=["bug", "bircher:queued", "priority"])
    assert bundle.is_relevant_change(fetched, label) is True
```

- [ ] **Step 2: Run the bundle tests to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_bundle_v2.py -q`
Expected: FAIL — `AttributeError: module 'kernel.bundle' has no attribute 'is_bircher_status'`, and the version pin `assert 1 == 2`.

- [ ] **Step 3: Implement canon v2**

In `v2/kernel/bundle.py`, replace `BUNDLE_CANON_VERSION = 1` and `snapshot`:

```python
#: Version 2 drops what bircher itself writes to an issue. Version 1 froze
#: every label and every comment, so the runner's own `bircher:running` flip
#: and the coordinator's publication comment read as a relevant change and
#: reset the run to `queued` on every pass (spec §2 Bundle revision).
BUNDLE_CANON_VERSION = 2

#: Labels bircher writes. Never frozen; they carry state, not requirement.
BIRCHER_LABEL_PREFIX = "bircher:"

#: The single definition of the predicate `run-queue.sh`'s digest also
#: applies. Five EXACT prefixes, matched with startswith on the stripped body
#: -- not the generic "bircher: ", which would silence a human discussing a
#: marker. Both copies are tested against tests/fixtures/bircher_status_comments.tsv.
BIRCHER_STATUS_PREFIXES: tuple[str, ...] = (
    "bircher: outcome=",
    "bircher-status:",
    "Outcome derived from the repository",
    "Cross-vendor review (outcome derived",
    "bircher: published ",
)


def is_bircher_status(body: str) -> bool:
    head = (body or "").lstrip()
    return any(head.startswith(p) for p in BIRCHER_STATUS_PREFIXES)


def snapshot(issue: dict) -> dict:
    """Reduce a provider issue to the frozen input, in canonical form."""
    return {
        "canon_version": BUNDLE_CANON_VERSION,
        "number": int(issue["number"]),
        "title": issue["title"],
        "body": issue["body"],
        # Sorted: label order is not stable and carries no meaning. Bircher's
        # own labels are state, not input.
        "labels": sorted(
            l for l in issue.get("labels", []) if not l.startswith(BIRCHER_LABEL_PREFIX)
        ),
        # Ordered by id: creation order is the meaningful one, and stable.
        # Bircher's own status and publication comments are not input.
        "comments": [
            {"id": int(c["id"]), "author": c["author"], "body": c["body"]}
            for c in sorted(issue.get("comments", []), key=lambda c: int(c["id"]))
            if not is_bircher_status(c.get("body") or "")
        ],
    }
```

`FROZEN_FIELDS`, `bundle_hash`, `is_relevant_change`, `propose_revision`, `revise_bundle`, `invalidates` are unchanged in this task (Task 9 turns `revise_bundle` into a command). Update the existing pin of `BUNDLE_CANON_VERSION` in the test that holds it to `2`.

- [ ] **Step 4: Run the bundle tests to see them pass**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_bundle_v2.py tests/kernel -q -k bundle`
Expected: PASS.

- [ ] **Step 5: Write the policy and create_run tests**

`v2/tests/kernel/test_policy.py`:

```python
import pytest

from kernel import policy
from kernel.effects import NotReplayable
from kernel.enqueue import create_run
from kernel.events import EventKind
from kernel.store import Store


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


ISSUE = {"number": 3, "title": "T", "body": "B", "labels": ["bircher:queued"],
         "comments": []}


def test_defaults_are_the_specs():
    p = policy.Policy()
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("model", frozenset({"spec"}), 3, 16)


def test_labels_override_project_config():
    assert policy.derive(["bircher:grill"], {}).grill == "human"
    assert policy.derive(["bircher:autonomous"], {}).gates == frozenset()
    assert policy.derive(["bircher:gate-plan"], {}).gates == frozenset({"spec", "plan"})
    # Ruling 5: autonomous wins when both are present.
    assert policy.derive(["bircher:gate-plan", "bircher:autonomous"], {}).gates == frozenset()
    cfg = {"grill": "human", "gates": ["spec", "plan"], "max_rounds": 5, "max_seats": 40}
    p = policy.derive([], cfg)
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("human", frozenset({"spec", "plan"}), 5, 40)
    assert policy.derive(["bircher:autonomous"], cfg).gates == frozenset()


@pytest.mark.parametrize("cfg", [
    {"grill": "robot"}, {"gates": ["impl"]}, {"max_rounds": 0}, {"max_rounds": 6},
    {"max_seats": 3}, {"max_seats": 41}, {"max_rounds": "3"}, {"gates": "spec"},
])
def test_out_of_range_config_is_refused(cfg):
    with pytest.raises(ValueError):
        policy.derive([], cfg)


def test_payload_round_trips():
    p = policy.derive(["bircher:gate-plan"], {"max_seats": 20})
    assert policy.from_payload(policy.to_payload(p)) == p
    assert policy.to_payload(p)["gates"] == ["plan", "spec"]


def test_create_run_freezes_policy_and_puts_the_snapshot(tmp_path):
    s = _store(tmp_path)
    out = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                     issue=dict(ISSUE, labels=["bircher:queued", "bircher:grill"]),
                     project_config={"max_seats": 20})
    assert out["replayed"] is False
    assert s.run_state("r-1") == "queued"
    assert s.has_artifact(out["bundle_hash"])
    frozen = s.newest_fact("r-1", EventKind.POLICY_FROZEN)
    assert frozen.actor == "kernel"
    assert frozen.payload["policy"] == {"grill": "human", "gates": ["spec"],
                                        "max_rounds": 3, "max_seats": 20}
    assert frozen.payload["labels"] == ["bircher:grill", "bircher:queued"]
    assert len(frozen.payload["project_config_hash"]) == 64
    assert policy.policy_of(s, "r-1") == policy.Policy(grill="human", max_seats=20)
    assert policy.policy_version(s, "r-1") == frozen.seq
    enq = s.newest_fact("r-1", EventKind.RUN_ENQUEUED)
    assert enq.payload["bundle_hash"] == out["bundle_hash"]


def test_create_run_replays_identical_and_refuses_different(tmp_path):
    s = _store(tmp_path)
    a = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=ISSUE, project_config={})
    b = create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=dict(ISSUE, labels=["bircher:running"]), project_config={})
    assert b["replayed"] is True and b["bundle_hash"] == a["bundle_hash"]
    assert len(s.facts_of_kind("r-1", EventKind.POLICY_FROZEN)) == 1
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=dict(ISSUE, body="changed"), project_config={})
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
                   issue=ISSUE, project_config={"max_rounds": 2})
    with pytest.raises(NotReplayable):
        create_run(s, run_id="r-1", base_repo="o/r", base_sha="1" * 40,
                   issue=ISSUE, project_config={})


def test_second_policy_frozen_is_refused(tmp_path):
    s = _store(tmp_path)
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
               issue=ISSUE, project_config={})
    with pytest.raises(policy.PolicyFrozen):
        policy.freeze(s, "r-1", labels=[], project_config={})


def test_a_run_without_policy_frozen_gets_defaults(tmp_path):
    s = _store(tmp_path)
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    assert policy.policy_of(s, "r-1") == policy.Policy()
    assert policy.policy_version(s, "r-1") is None
```

- [ ] **Step 6: Run the policy tests to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.policy'`.

- [ ] **Step 7: Implement `policy.py`**

`v2/kernel/policy.py`:

```python
"""The frozen per-run policy (spec §1).

Derived by the kernel inside `create_run`'s transaction from the issue's
labels and the omnigent Project config; recorded once as `policy_frozen`.
Every guard reads the fact, never a caller's claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kernel.canon import canonical_hash
from kernel.events import EventKind

GRILLS = ("human", "model")
PHASES = ("spec", "plan")
ROUNDS = range(1, 6)      # 1..5
SEATS = range(4, 41)      # 4..40


class PolicyFrozen(Exception):
    """A run's policy is written once, in its creation transaction."""


@dataclass(frozen=True)
class Policy:
    grill: str = "model"
    gates: frozenset[str] = frozenset({"spec"})
    max_rounds: int = 3
    max_seats: int = 16


def _int(cfg: dict, key: str, default: int, allowed: range) -> int:
    value = cfg.get(key, default)
    if type(value) is not int or value not in allowed:
        raise ValueError(f"policy {key} must be an int in {allowed}, got {value!r}")
    return value


def derive(labels: Iterable[str], project_config: dict) -> Policy:
    cfg = project_config or {}
    if not isinstance(cfg, dict):
        raise ValueError("project config must be an object")
    grill = cfg.get("grill", "model")
    if grill not in GRILLS:
        raise ValueError(f"policy grill must be one of {GRILLS}, got {grill!r}")
    raw_gates = cfg.get("gates", ["spec"])
    if not isinstance(raw_gates, (list, tuple, set, frozenset)):
        raise ValueError("policy gates must be a list")
    gates = set(raw_gates)
    if not gates <= set(PHASES):
        raise ValueError(f"policy gates must be within {PHASES}, got {sorted(gates)}")
    max_rounds = _int(cfg, "max_rounds", 3, ROUNDS)
    max_seats = _int(cfg, "max_seats", 16, SEATS)

    present = set(labels)
    if "bircher:grill" in present:
        grill = "human"
    if "bircher:gate-plan" in present:
        gates.add("plan")
    # Ruling 5: cleared last, so `bircher:autonomous` wins over `bircher:gate-plan`.
    if "bircher:autonomous" in present:
        gates = set()
    return Policy(grill=grill, gates=frozenset(gates),
                  max_rounds=max_rounds, max_seats=max_seats)


def to_payload(p: Policy) -> dict:
    return {"grill": p.grill, "gates": sorted(p.gates),
            "max_rounds": p.max_rounds, "max_seats": p.max_seats}


def from_payload(d: dict) -> Policy:
    return Policy(grill=d["grill"], gates=frozenset(d["gates"]),
                  max_rounds=d["max_rounds"], max_seats=d["max_seats"])


def freeze(store, run_id: str, *, labels: Iterable[str], project_config: dict) -> Policy:
    """Write `policy_frozen`. Called inside `create_run`'s transaction only."""
    if store.newest_fact(run_id, EventKind.POLICY_FROZEN) is not None:
        raise PolicyFrozen(f"run {run_id} already has a policy_frozen fact")
    labels = sorted(set(labels))
    p = derive(labels, project_config)
    store.append_fact(
        run_id=run_id, kind=EventKind.POLICY_FROZEN, actor="kernel",
        causal_command_id=None,
        payload={"policy": to_payload(p), "labels": labels,
                 "project_config_hash": canonical_hash(project_config or {})},
    )
    return p


def policy_of(store, run_id: str) -> Policy:
    """Ruling 6: a run with no `policy_frozen` (a v1 run) has the defaults."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return Policy() if fact is None else from_payload(fact.payload["policy"])


def policy_version(store, run_id: str) -> int | None:
    """The journal `seq` of the run's `policy_frozen` fact; None for a v1 run."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return None if fact is None else fact.seq
```

- [ ] **Step 8: Implement `create_run`**

In `v2/kernel/enqueue.py`, add after `enqueue` (leave `enqueue` and `_create_run_and_transition` as they are — the v1 entry point stays for its existing tests; the runner moves to `create_run` in Task 20):

```python
from kernel.bundle import snapshot as bundle_snapshot
from kernel.canon import canonical_bytes, canonical_hash, content_hash
from kernel.effects import NotReplayable
from kernel.policy import freeze, to_payload, policy_of


def create_run(store, *, run_id: str, base_repo: str, base_sha: str,
               issue: dict, project_config: dict) -> dict:
    """Spec §2: `create_run(issue, project_config)`.

    One transaction: the run row (`queued`), the canonical snapshot bytes PUT
    under `bundle_hash`, `policy_frozen`, `run_enqueued`. Replay only an
    identical request; a retry whose inputs differ is `NotReplayable` -- the
    earlier `enqueue` recomputed its answer from the RETRY's arguments and
    reported success for a policy the journal did not hold.
    """
    snap = bundle_snapshot(issue)
    raw = canonical_bytes(snap)
    bhash = content_hash(raw)
    cfg_hash = canonical_hash(project_config or {})
    labels = sorted(set(issue.get("labels", [])))

    if _run_exists(store, run_id):
        started = store.newest_fact(run_id, EventKind.RUN_STARTED)
        enq = store.newest_fact(run_id, EventKind.RUN_ENQUEUED)
        frozen = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
        held = {
            "bundle_hash": None if enq is None else enq.payload.get("bundle_hash"),
            "project_config_hash": None if frozen is None else frozen.payload.get("project_config_hash"),
            "base_sha": started.payload.get("base_sha"),
            "base_repo": started.payload.get("base_repo"),
        }
        asked = {"bundle_hash": bhash, "project_config_hash": cfg_hash,
                 "base_sha": base_sha, "base_repo": base_repo}
        if held != asked:
            raise NotReplayable(
                f"run {run_id} exists with inputs {held}; this retry carries {asked}"
            )
        return {"run_id": run_id, "bundle_hash": bhash,
                "policy": to_payload(policy_of(store, run_id)), "replayed": True}

    with store.transaction():
        store.create_run(run_id=run_id, base_repo=base_repo, base_sha=base_sha)
        put = put_artifact(store, raw)
        assert put == bhash, "bundle_hash is content_hash(canonical_bytes(snapshot))"
        p = freeze(store, run_id, labels=labels, project_config=project_config)
        store.append_fact(
            run_id=run_id, kind=EventKind.RUN_ENQUEUED, actor="human",
            causal_command_id=None,
            payload={"bundle_hash": bhash, "packet_hash": None,
                     "spec_hash": None, "plan_hash": None},
        )
    return {"run_id": run_id, "bundle_hash": bhash, "policy": to_payload(p),
            "replayed": False}
```

`_run_exists` already exists at `enqueue.py:44`. `EventKind` and `put_artifact` are already imported in that module; add the three new imports at the top with the others (no import inside the function).

- [ ] **Step 9: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_policy.py tests/kernel/test_bundle_v2.py -q`
Expected: PASS. Full suite: green (the canon pin moved to 2; every other bundle test hashes issues without `bircher:` labels or status comments, so their hashes are unchanged in content — only `canon_version` moved, and any test that pinned a literal hash string of a snapshot must recompute it via `bundle_hash(snapshot(...))` rather than a literal).

- [ ] **Step 10: Mutation check, then commit**

Change `"bircher: published "` to `"bircher: "` in `BIRCHER_STATUS_PREFIXES`: `test_prefixes_are_exactly_five_and_not_the_generic_one` and the fixture row `keep	bircher: could you also handle the dark theme?` fail. Move the `bircher:autonomous` clause above the `gate-plan` clause: `test_labels_override_project_config` fails on the ruling-5 line. Drop the `held != asked` check: `test_create_run_replays_identical_and_refuses_different` fails. Restore all three.

```bash
git add v2/kernel/bundle.py v2/kernel/policy.py v2/kernel/enqueue.py v2/tests/fixtures/bircher_status_comments.tsv v2/tests/kernel/test_bundle_v2.py v2/tests/kernel/test_policy.py
git commit -m "feat(kernel): bundle canon v2, frozen policy, create_run"
```

---

### Task 6: Dispatch — the `author` role, the seat bound, the pending-effect refusal

**Files:**
- Modify: `v2/kernel/dispatch.py`
- Create: `v2/kernel/front.py` (the first two queries; later tasks add to it)
- Modify: `v2/tests/kernel/test_executor_body.py` (Task 3's `Role.IMPLEMENTER` placeholder → `Role.AUTHOR`)
- Test: `v2/tests/kernel/test_dispatch_front.py`

**Interfaces:**
- Consumes: `policy.policy_of` (Task 5); `Store.dispatches_for`, `Store.facts_of_kind`, `Store.uncertain_effects`.
- Produces:
  - `dispatch.Role.AUTHOR = "author"`, `Role.ALL` gains it, `Role.SEATS = frozenset({AUTHOR, REVIEWER})`.
  - `dispatch.PendingEffects(Exception)` with `.keys: list[str]`; `dispatch.SeatsExhausted(Exception)`.
  - `front.seats_used(store, run_id) -> int` (count of dispatch records whose role is in `Role.SEATS`); `front.grants(store, run_id) -> int` (count of `human_ruling` facts whose payload `ruling == "grant_round"`); `front.seat_bound(store, run_id) -> int` = `policy_of(...).max_seats + 2 * grants(...)` (ruling 4).

- [ ] **Step 1: Write the failing tests**

`v2/tests/kernel/test_dispatch_front.py`:

```python
import pytest

from kernel import front
from kernel.dispatch import PendingEffects, Role, SeatsExhausted, dispatch
from kernel.enqueue import create_run
from kernel.events import EventKind
from kernel.store import Store

ISSUE = {"number": 3, "title": "T", "body": "B", "labels": [], "comments": []}


def _run(tmp_path, cfg=None):
    s = Store.open(tmp_path / "k.db")
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40,
               issue=ISSUE, project_config=cfg or {})
    return s


def test_author_is_a_role():
    assert Role.AUTHOR == "author"
    assert Role.AUTHOR in Role.ALL
    assert Role.SEATS == frozenset({Role.AUTHOR, Role.REVIEWER})


def test_seat_bound_counts_author_and_reviewer_dispatches_only(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    dispatch(s, "r-1", actor="runner", role=Role.OPERATOR)
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert front.seats_used(s, "r-1") == 3
    assert front.seat_bound(s, "r-1") == 4
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    # Operator and implementer dispatches are not seats and are not bounded by them.
    dispatch(s, "r-1", actor="runner", role=Role.OPERATOR)
    dispatch(s, "r-1", actor="claude", role=Role.IMPLEMENTER)
    assert front.seats_used(s, "r-1") == 4


def test_a_grant_raises_the_bound_by_two(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    for _ in range(2):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_RULING, actor="human",
                  causal_command_id=None,
                  payload={"ruling": "grant_round", "phase": "spec"})
    assert front.grants(s, "r-1") == 1
    assert front.seat_bound(s, "r-1") == 6
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)


def test_a_non_grant_human_ruling_is_not_a_grant(tmp_path):
    s = _run(tmp_path)
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_RULING, actor="human",
                  causal_command_id=None, payload={"ruling": "request_revision"})
    assert front.grants(s, "r-1") == 0


def test_any_dispatch_over_an_intended_or_uncertain_effect_is_refused_and_halts(tmp_path):
    """spec §2 Refusals: refused, AND the run is halted with the keys as evidence."""
    from kernel.effects import is_halted
    s = _run(tmp_path)
    g = dispatch(s, "r-1", actor="runner", role=Role.OPERATOR).generation
    key = "sess-create:r-1:%d" % g
    s.journal_intent("e1", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-create", "run": "r-1",
                                                       "phase": "spec", "epoch": 0, "cause": "x"}})
    assert not is_halted(s, "r-1")
    for role in (Role.AUTHOR, Role.REVIEWER, Role.OPERATOR, Role.IMPLEMENTER):
        with pytest.raises(PendingEffects) as exc:
            dispatch(s, "r-1", actor="claude", role=role)
        assert exc.value.keys == [key]
    assert is_halted(s, "r-1")
    evidence = s.reconciliation_evidence("r-1")
    assert evidence["pending_keys"] == [key] and evidence["affected_resources"] == ["session_control"]
    s.clear_reconciliation("r-1")
    s.mark_effect(key, "uncertain", None, run_id="r-1")
    with pytest.raises(PendingEffects):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert is_halted(s, "r-1")
    s.clear_reconciliation("r-1")
    s.mark_effect(key, "confirmed", '{"id": "s1"}', run_id="r-1")
    dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    assert not is_halted(s, "r-1")


def test_refused_dispatch_leaves_no_generation_or_fact(tmp_path):
    s = _run(tmp_path, {"max_seats": 4})
    for _ in range(2):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
        dispatch(s, "r-1", actor="codex", role=Role.REVIEWER)
    before = len(s.dispatches_for("r-1")), len(s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED))
    with pytest.raises(SeatsExhausted):
        dispatch(s, "r-1", actor="claude", role=Role.AUTHOR)
    after = len(s.dispatches_for("r-1")), len(s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED))
    assert before == after
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_dispatch_front.py -q`
Expected: FAIL — `ImportError: cannot import name 'PendingEffects' from 'kernel.dispatch'`.

(`Store.set_reconciliation`, `reconciliation_evidence`, `clear_reconciliation` and `last_confirmed` exist at `store.py:360-381`; the evidence dict mirrors `effects._halt_evidence`'s keys plus `pending_keys`, so `kernel.cli pending` reports it as any halt.)

- [ ] **Step 3: Implement `front.py`'s first queries**

`v2/kernel/front.py`:

```python
"""Front-half journal queries (spec §1-§2).

Every function here derives its answer from facts and satisfied effects.
None reads a caller's payload. Tasks 7-12 add to this module; nothing in it
writes.
"""
from __future__ import annotations

from kernel.events import EventKind
from kernel.policy import policy_of


def seats_used(store, run_id: str) -> int:
    from kernel.dispatch import Role
    return sum(1 for d in store.dispatches_for(run_id) if d["role"] in Role.SEATS)


def grants(store, run_id: str) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING)
        if f.payload.get("ruling") == "grant_round"
    )


def seat_bound(store, run_id: str) -> int:
    """Ruling 4: a granted round is one author seat and one reviewer seat."""
    return policy_of(store, run_id).max_seats + 2 * grants(store, run_id)
```

The local import of `Role` avoids the cycle `dispatch → front → dispatch`.

- [ ] **Step 4: Implement the dispatch changes**

In `v2/kernel/dispatch.py`:

```python
class Role:
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    AUTHOR = "author"
    ALL = frozenset({IMPLEMENTER, REVIEWER, OPERATOR, AUTHOR})
    #: The two roles the front half's seat budget counts (spec §1 max_seats).
    SEATS = frozenset({AUTHOR, REVIEWER})


class PendingEffects(Exception):
    """A dispatch over an intended or uncertain effect (spec §2 Refusals:
    'any dispatch when the run holds an intended or uncertain effect')."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"run holds unresolved effects: {keys}")
        self.keys = keys


class SeatsExhausted(Exception):
    """Front-half dispatches have reached max_seats plus granted rounds."""
```

and in `dispatch()`, inside the `with store.transaction():` block, before `acquire`:

```python
    # Before the transaction, so the halt survives the raise: a run holding an
    # intended or uncertain effect is halted with those keys as evidence and
    # the dispatch refused (spec §2 Refusals, §5).
    pending = store.uncertain_effects(run_id)
    if pending:
        keys = [e["idempotency_key"] for e in pending]
        store.set_reconciliation(run_id, {
            "run_id": run_id, "generation": None,
            "affected_resources": sorted({e["effect_class"] for e in pending}),
            "pending_keys": keys,
            "last_confirmed_observations": store.last_confirmed(run_id),
            "stop_attempts": 0,
            "recommended_actions": [
                "A dispatch was attempted over unresolved effects; check each key externally",
                "Then reconcile (--delivered / --not-delivered per key) and resume",
            ],
        })
        raise PendingEffects(keys)
    with store.transaction():
        if role in Role.SEATS:
            from kernel.front import seat_bound, seats_used
            used, bound = seats_used(store, run_id), seat_bound(store, run_id)
            if used >= bound:
                raise SeatsExhausted(
                    f"run {run_id} has used {used} of {bound} front-half seats"
                )
        generation = acquire(store, run_id, actor)
        ...
```

`store.uncertain_effects` already returns `intended` rows as well as `uncertain` ones (`store.py:256-269`), which is exactly the pair the spec names. Raising inside the transaction rolls back nothing written (nothing was) and leaves no generation or fact — the last test binds that.

- [ ] **Step 5: Move Task 3's placeholder to the real role**

In `v2/tests/kernel/test_executor_body.py` replace every `role=Role.IMPLEMENTER` that the Task 3 note marked as a placeholder with `role=Role.AUTHOR`.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_dispatch_front.py tests/kernel/test_executor_body.py tests/kernel/test_dispatch.py -q`
Expected: PASS. Full suite: green. Existing tests that dispatch after journaling an effect they never resolved will now raise `PendingEffects`; each such test must resolve the effect (`mark_effect(..., "confirmed", ...)`) before its next dispatch — that is the spec's rule, not a test accident, so fix the test, not the guard.

- [ ] **Step 7: Mutation check, then commit**

Change `2 * grants(...)` to `grants(...)`: `test_a_grant_raises_the_bound_by_two` fails. Remove the `pending` check: `test_any_dispatch_over_an_intended_or_uncertain_effect_is_refused_and_halts` fails. Keep the raise but drop the `set_reconciliation`: it fails at `assert is_halted`. Restore all three.

```bash
git add v2/kernel/dispatch.py v2/kernel/front.py v2/tests/kernel/test_dispatch_front.py v2/tests/kernel/test_executor_body.py
git commit -m "feat(kernel): author role, seat bound, dispatch refused over pending effects"
```

---
### Task 7: The state-table cutover, phase artefacts, `artifact_submitted`, the driver, and the migration

This is the task that supersedes v1's `queued → specified → planned` shortcut. After it, `submit_spec` lands at `spec_submitted` and only a reviewer's `accept` of the current hash reaches `specified`; the same for the plan. Every test that used the shortcut migrates to the driver this task creates.

**Files:**
- Modify: `v2/kernel/authz.py` (`_TRANSITIONS`, `FRONT_HALF_STATES`, `phase_of`, `validate_review`, `authorize`)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact`, the `REVIEW_VERDICT` payload)
- Modify: `v2/kernel/front.py` (queries listed under Produces)
- Create: `v2/tests/kernel/front.py` (the driver; a module, not a test file — pytest collects `test_*.py` only)
- Test: `v2/tests/kernel/test_front_table.py`
- Modify: the 22 files that reach `specified`/`planned` through v1 submits — listed in Step 9.

**Interfaces:**
- Consumes: `Store.phase_artifact`, `Store.set_phase_artifact`, `Store.facts_of_kind`, `Store.newest_fact`, `Store.effects_for`, `Store.dispatches_for` (Task 1); `policy.policy_of`, `policy.policy_version` (Task 5); `Role.AUTHOR`, `front.seats_used` (Task 6); `create_run` (Task 5).
- Produces:
  - `authz.FRONT_HALF_STATES = frozenset({"queued", "spec_submitted", "spec_accepted", "specified", "plan_submitted", "plan_accepted"})`; `authz.phase_of(state: str) -> str | None` (`"spec"` for `queued`/`spec_*`, `"plan"` for `specified`/`plan_*`, `"implementation"` for `implementing`/`reviewing`, else `None`); `authz.PARK_REASONS`; `authz.TURN_ENDS = frozenset({"file", "dead", "cap", "displaced"})`; `authz._VERDICT_WORDS`.
  - `authz.validate_review(store, cmd, actor, *, ruling="review_ruling") -> VerdictBinding | None` — binding checks for every ruling, dispatch checks for `review_ruling` only (Task 8 passes `ruling="human_ruling"`).
  - `front.BUNDLE_PREFIX = "v2_author_"`, `front.vendor_of(agent_name) -> str | None` (ruling 14), `front.FRONT_PHASES = ("spec", "plan")`, `front.HUMAN_FACT_KINDS`, `front.epoch(store, run_id) -> int`, `front.bundle_hash(store, run_id) -> str | None`, `front.submissions(store, run_id, phase, epoch) -> list[Fact]`, `front.newest_submission(store, run_id, phase, epoch) -> Fact | None`, `front.last_transition_seq(store, run_id) -> int`, `front.last_human_fact_seq(store, run_id) -> int`, `front.current_park(store, run_id) -> Fact | None`, `front.satisfied_effects(store, run_id, kind) -> list[dict]` (effects whose `intent["obligation"]["kind"] == kind` and whose state is `confirmed`, or `reconciled` with a non-null `external_object_id`, journal order), `front.newest_seat(store, run_id, role, phase, epoch) -> dict | None` (the newest satisfied `sess-create` whose generation's dispatch role is `role` and whose obligation names `phase` and `epoch`; returns `{"generation", "session": <projection dict>, "cause", "key"}`), `front.newest_prompt(store, run_id, phase, epoch) -> dict | None` (same for `sess-prompt`, returning `{"generation", "session_id", "cause", "key", "seq_at": at_us}`).
  - New commands: `record_turn_ended {session, ended}` (legal from `queued`, `specified`, `spec_submitted`, `plan_submitted`; fact `turn_ended {session, ended, phase, epoch, generation, prompt_key}` — `prompt_key` the key of the newest satisfied `sess-prompt` of the phase and epoch, derived by the kernel, so "a turn_ended newer than the prompt" is an equality on keys, never a clock comparison), `park {reason, session_id, cursor_item_id, findings_hash, verdict, reviewer}` (legal from every front-half state; fact `parked` with those keys plus `phase, epoch, generation`).
  - `submit_spec`/`submit_plan` payload is `{"artifact_hash": <sha256 the store holds>}`; the accepted command writes `artifact_submitted {phase, epoch, hash, author, round}` and sets the phase artefact.
  - `record_review` payload gains `phase` (required, must equal `phase_of(state)`) and, for front-half `review_ruling`s, `findings_hash` (required, held by the store); the `review_verdict` fact gains `phase, epoch, artifact_hash, findings_hash, ruling`.
  - Driver: `tests.kernel.front.Front` (`_dispatch`, `_create`, `_session`, `_end_turn`, `_cmd`, `_journal`, `_newest_id`, `_newest_author_session`, `author_round`, `review_round`, `to_specified`, `to_planned`, `to_implementing`), `reach_specified`, `reach_planned`, `reach_implementing`, constants `SPEC_BYTES`, `PLAN_BYTES`; CLI `python -m tests.kernel.front --db PATH --run-id ID --to specified|planned|implementing [--existing]`.

- [ ] **Step 1: Write the table tests**

`v2/tests/kernel/test_front_table.py`:

```python
import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import FRONT_HALF_STATES, NotAuthorized, phase_of
from kernel.commands import COMMAND_NAMES, Command, submit
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front, reach_planned


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_phase_of_every_state():
    assert FRONT_HALF_STATES == {"queued", "spec_submitted", "spec_accepted",
                                 "specified", "plan_submitted", "plan_accepted"}
    for s in ("queued", "spec_submitted", "spec_accepted"):
        assert phase_of(s) == "spec"
    for s in ("specified", "plan_submitted", "plan_accepted"):
        assert phase_of(s) == "plan"
    for s in ("implementing", "reviewing"):
        assert phase_of(s) == "implementation"
    assert phase_of("planned") is None and phase_of("merged") is None


def test_command_set_gains_turn_ended_and_park():
    assert {"record_turn_ended", "park"} <= COMMAND_NAMES


def test_submit_spec_lands_at_spec_submitted_and_records_the_artefact(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    assert s.run_state("r-1") == "spec_submitted"
    assert s.phase_artifact("r-1", "spec") == h
    sub = s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert sub.payload == {"phase": "spec", "epoch": 0, "hash": h, "author": "claude", "round": 1}
    assert sub.actor == "claude"


def test_accept_without_gate_reaches_specified_then_planned(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:autonomous",))
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "specified"
    h = f.author_round(PLAN_BYTES)
    assert s.run_state("r-1") == "plan_submitted"
    assert s.phase_artifact("r-1", "plan") == h
    f.review_round("accept")
    assert s.run_state("r-1") == "planned"
    verdicts = s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)
    assert [v.payload["phase"] for v in verdicts] == ["spec", "plan"]
    assert verdicts[-1].payload["artifact_hash"] == h
    assert verdicts[-1].payload["ruling"] == "review_ruling"
    assert verdicts[-1].payload["reviewer_identity"] == "codex"


def test_accept_under_the_gate_stops_at_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())          # default policy: gates == {spec}
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    # Task 8 adds the human's approve; here the gate is the end of the road.
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_accepted'"):
        f.author_round(PLAN_BYTES)


def test_request_revision_returns_to_the_submitting_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("request_revision", findings=b"needs a threat model")
    assert s.run_state("r-1") == "queued"
    f.author_round(SPEC_BYTES + b"\n## Threats\n")
    f.review_round("accept")
    f.author_round(PLAN_BYTES)
    f.review_round("request_revision")
    assert s.run_state("r-1") == "specified"


def test_reject_is_refused_in_the_front_half(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="reject"):
        f.review_round("reject")
    assert s.run_state("r-1") == "spec_submitted"


def test_review_phase_and_hash_must_match_the_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="phase"):
        f.review_round("accept", phase="plan")
    other = put_artifact(s, b"not the spec")
    with pytest.raises(NotAuthorized, match="current artefact"):
        f.review_round("accept", artifact_hash=other)
    assert s.run_state("r-1") == "spec_submitted"
    f.review_round("accept", artifact_hash=h)
    assert s.run_state("r-1") == "specified"


def test_review_binds_the_epochs_bundle_and_policy_version(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="context_bundle_hash"):
        f.review_round("accept", context_bundle_hash="f" * 64)
    with pytest.raises(NotAuthorized, match="policy_version"):
        f.review_round("accept", policy_version=front.policy_version(s, "r-1") + 1)
    with pytest.raises(NotAuthorized, match="findings"):
        f.review_round("accept", findings_hash="e" * 64)


def test_submit_requires_the_author_role(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    h = put_artifact(s, SPEC_BYTES)
    g = dispatch(s, "r-1", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized, match="author"):
        submit(s, Command(name="submit_spec", run_id="r-1", expected_version=s.run_version("r-1"),
                          idempotency_key="k", generation=g, payload={"artifact_hash": h}))


def test_identical_resubmission_in_the_phase_and_epoch_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("request_revision")
    with pytest.raises(NotAuthorized, match="identical"):
        f.author_round(SPEC_BYTES)
    rejected = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rejected.payload["command_name"] == "submit_spec"


def test_submit_plan_refuses_the_spec_hash_and_a_plan_without_tasks(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    f.review_round("accept")
    with pytest.raises(NotAuthorized, match="spec"):
        f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="### Task"):
        f.author_round(b"# Plan\n\nno tasks here\n")
    assert s.run_state("r-1") == "specified"


def test_reviewer_may_not_be_the_submitting_actor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", author="claude", reviewer="claude")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="independence"):
        f.review_round("accept")


def test_turn_ended_names_a_session_and_one_of_four_ends(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    with pytest.raises(NotAuthorized, match="ended"):
        f._cmd(g, "record_turn_ended", {"session": sid, "ended": "timeout"})
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "cap"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    assert te.payload == {"session": sid, "ended": "cap", "phase": "spec", "epoch": 0,
                          "generation": g, "prompt_key": prompt["key"]}


def test_park_records_the_kernels_view_and_currency(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    with pytest.raises(NotAuthorized, match="reason"):
        f._cmd(g, "park", {"reason": "tired", "session_id": None, "cursor_item_id": None,
                           "findings_hash": None, "verdict": None, "reviewer": None})
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": "s-9", "cursor_item_id": "i-3",
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    p = front.current_park(s, "r-1")
    assert p is not None and p.payload["phase"] == "spec" and p.payload["generation"] == g
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_ANSWER, actor="human",
                  causal_command_id=None, payload={"epoch": 0, "question_ids": [], "answer": "go on",
                                                   "cursor_item_id": "i-4"})
    assert front.current_park(s, "r-1") is None
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert front.current_park(s, "r-1") is not None
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_DIRECTION, actor="human",   # ruling 2
                  causal_command_id=None, payload={"phase": "spec", "epoch": 0, "text": "no",
                                                   "cursor_item_id": "i-5"})
    assert front.current_park(s, "r-1") is None
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert front.current_park(s, "r-1") is not None
    f.review_round("accept")                      # a transition consumes it
    assert front.current_park(s, "r-1") is None


def test_reach_planned_helper_and_cancel_from_new_states(tmp_path):
    s = _store(tmp_path)
    f = reach_planned(s, "r-1")
    assert s.run_state("r-1") == "planned"
    s2 = Store.open(tmp_path / "k2.db")
    f2 = Front(s2, "r-2")
    f2.author_round(SPEC_BYTES)
    g = f2._dispatch(Role.OPERATOR, "runner")
    f2._cmd(g, "cancel_run", {})
    assert s2.run_state("r-2") == "cancelled"


def test_epoch_and_bundle_hash_queries(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    assert front.epoch(s, "r-1") == 0
    enq = s.newest_fact("r-1", EventKind.RUN_ENQUEUED)
    assert front.bundle_hash(s, "r-1") == enq.payload["bundle_hash"]
    s.append_fact(run_id="r-1", kind=EventKind.BUNDLE_REVISED, actor="human",
                  causal_command_id=None, payload={"bundle_hash": "b" * 64, "reason": "x"})
    assert front.epoch(s, "r-1") == 1
    assert front.bundle_hash(s, "r-1") == "b" * 64
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_front_table.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.kernel.front'` (then `ImportError` of `FRONT_HALF_STATES` once the driver exists).

- [ ] **Step 3: The queries in `front.py`**

Append to `v2/kernel/front.py`:

```python
FRONT_PHASES = ("spec", "plan")

#: Ruling 14: the server names the bundle, the dispatch names the vendor.
BUNDLE_PREFIX = "v2_author_"


def vendor_of(agent_name) -> str | None:
    """`v2_author_claude` -> `claude`; any other bundle name -> None."""
    if isinstance(agent_name, str) and agent_name.startswith(BUNDLE_PREFIX) and len(agent_name) > len(BUNDLE_PREFIX):
        return agent_name[len(BUNDLE_PREFIX):]
    return None


#: Ruling 2: a direction consumes a park as an answer or a ruling does.
HUMAN_FACT_KINDS = frozenset({
    EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING, EventKind.HUMAN_DIRECTION,
})


def epoch(store, run_id: str) -> int:
    return len(store.facts_of_kind(run_id, EventKind.BUNDLE_REVISED))


def bundle_hash(store, run_id: str) -> str | None:
    revised = store.newest_fact(run_id, EventKind.BUNDLE_REVISED)
    if revised is not None:
        return revised.payload["bundle_hash"]
    enq = store.newest_fact(run_id, EventKind.RUN_ENQUEUED)
    return None if enq is None else enq.payload.get("bundle_hash")


def submissions(store, run_id: str, phase: str, epoch_n: int) -> list:
    return [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
            if f.payload["phase"] == phase and f.payload["epoch"] == epoch_n]


def newest_submission(store, run_id: str, phase: str, epoch_n: int):
    subs = submissions(store, run_id, phase, epoch_n)
    return subs[-1] if subs else None


def _newest_seq(store, run_id: str, *kinds: str) -> int:
    facts = store.facts_of_kind(run_id, *kinds)
    return facts[-1].seq if facts else 0


def last_transition_seq(store, run_id: str) -> int:
    return _newest_seq(store, run_id, EventKind.TRANSITION)


def last_human_fact_seq(store, run_id: str) -> int:
    return _newest_seq(store, run_id, *HUMAN_FACT_KINDS)


def current_park(store, run_id: str):
    """The newest `parked` fact, if newer than the last transition and the
    last human fact (spec §2 *States*). Either consumes it."""
    park = store.newest_fact(run_id, EventKind.PARKED)
    if park is None:
        return None
    floor = max(last_transition_seq(store, run_id), last_human_fact_seq(store, run_id))
    return park if park.seq > floor else None


def _satisfied(row: dict) -> bool:
    if row["state"] == "confirmed":
        return True
    return row["state"] == "reconciled" and row.get("external_object_id") not in (None, "")


def satisfied_effects(store, run_id: str, kind: str) -> list[dict]:
    out = []
    for row in store.effects_for(run_id):
        ob = (row.get("intent") or {}).get("obligation") or {}
        if ob.get("kind") == kind and _satisfied(row):
            out.append(row)
    return out


def _roles(store, run_id: str) -> dict[int, str]:
    return {d["generation"]: d["role"] for d in store.dispatches_for(run_id)}


def newest_seat(store, run_id: str, role: str, phase: str, epoch_n: int) -> dict | None:
    """The newest satisfied `sess-create` under a generation dispatched as
    *role* whose obligation names *phase* and *epoch* (spec §2 Refusals:
    'the newest satisfied sess-create under an author generation of this
    phase and epoch'). Phase and epoch are the obligation's, read from the
    intent; the effects table has no such columns."""
    import json
    roles = _roles(store, run_id)
    found = None
    for row in satisfied_effects(store, run_id, "sess-create"):
        ob = row["intent"]["obligation"]
        if roles.get(row["generation"]) != role:
            continue
        if ob.get("phase") != phase or ob.get("epoch") != epoch_n:
            continue
        found = {"generation": row["generation"], "key": row["idempotency_key"],
                 "cause": ob.get("cause"),
                 "session": json.loads(row["external_object_id"])}
    return found


def newest_prompt(store, run_id: str, phase: str, epoch_n: int) -> dict | None:
    found = None
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        ob = row["intent"]["obligation"]
        if ob.get("phase") != phase or ob.get("epoch") != epoch_n:
            continue
        found = {"generation": row["generation"], "key": row["idempotency_key"],
                 "cause": ob.get("cause"), "session_id": ob.get("session"),
                 "at_us": row["at_us"]}
    return found
```

- [ ] **Step 4: The table and the review split in `authz.py`**

Replace `_TRANSITIONS` and `_VERDICTS`, and add the helpers, in `v2/kernel/authz.py`:

```python
FRONT_HALF_STATES = frozenset({
    "queued", "spec_submitted", "spec_accepted",
    "specified", "plan_submitted", "plan_accepted",
})

_PHASE_OF_STATE = {
    "queued": "spec", "spec_submitted": "spec", "spec_accepted": "spec",
    "specified": "plan", "plan_submitted": "plan", "plan_accepted": "plan",
    "implementing": "implementation", "reviewing": "implementation",
}


def phase_of(state: str) -> str | None:
    return _PHASE_OF_STATE.get(state)


#: spec §3 loop: the reasons a pass parks for.
PARK_REASONS = frozenset({
    "grill", "no_verdict", "bound_exhausted", "gate", "budget_exhausted",
    "identical_resubmission",
})

#: spec §3 *The turn's end is a fact*: the four ways a turn ends.
TURN_ENDS = frozenset({"file", "dead", "cap", "displaced"})

_ALL_ACTIVE = frozenset({
    "queued", "spec_submitted", "spec_accepted", "specified",
    "plan_submitted", "plan_accepted", "planned", "implementing", "reviewing",
    "merge_requested",
})

_TRANSITIONS: dict[str, tuple[frozenset[str], str | None]] = {
    # spec §2 Commands. The front half: a submit lands at *_submitted, and
    # only a reviewer's accept of the current hash moves on from there.
    "submit_spec": (frozenset({"queued"}), "spec_submitted"),
    "submit_plan": (frozenset({"specified"}), "plan_submitted"),
    "start_implementation": (frozenset({"planned"}), "implementing"),
    # Destination depends on state, verdict and the run's gates: computed in
    # authorize() by _review_destination.
    "record_review": (
        frozenset({"spec_submitted", "spec_accepted", "plan_submitted",
                   "plan_accepted", "implementing", "reviewing"}),
        None,
    ),
    "record_turn_ended": (
        frozenset({"queued", "specified", "spec_submitted", "plan_submitted"}), None,
    ),
    "park": (FRONT_HALF_STATES, None),
    "record_merge_outcome": (frozenset({"merge_requested"}), None),
    "record_implementation_output": (frozenset({"implementing"}), None),
    "record_ci_observation": (frozenset({"implementing", "reviewing"}), None),
    "request_merge": (frozenset({"reviewing"}), "merge_requested"),
    "record_run_outcome": (_ALL_ACTIVE | frozenset({"merged", "cancelled"}), "ended"),
    "cancel_run": (_ALL_ACTIVE, "cancelled"),
}

_VERDICT_WORDS = frozenset({"accept", "request_revision", "reject"})

#: Back-half destinations, unchanged from v1.
_BACK_HALF_DESTINATIONS: dict[str, str] = {
    "accept": "reviewing", "request_revision": "planned", "reject": "reviewing",
}


def _review_destination(store, run_id: str, state: str, verdict: str, ruling: str) -> str:
    from kernel.policy import policy_of
    if state in ("implementing", "reviewing"):
        return _BACK_HALF_DESTINATIONS[verdict]
    if verdict == "reject":
        raise NotAuthorized(
            "reject is not a front-half verdict: a spec or plan is accepted or "
            "revised, never rejected (spec §2 Commands)"
        )
    phase = phase_of(state)
    if state.endswith("_accepted"):
        # Task 8 adds the human's request_revision from here; a review_ruling
        # never moves an accepted artefact.
        raise NotAuthorized(
            f"record_review from {state!r} is the human's correction only "
            "(a human_ruling); a reviewer has already ruled on this hash"
        )
    if verdict == "request_revision":
        return "queued" if phase == "spec" else "specified"
    gates = policy_of(store, run_id).gates
    if phase == "spec":
        return "spec_accepted" if "spec" in gates else "specified"
    return "plan_accepted" if "plan" in gates else "planned"
```

Replace the body of `validate_review` with:

```python
def validate_review(store, cmd, actor: str, *, ruling: str = "review_ruling") -> VerdictBinding | None:
    """Validate a review BEFORE it is recorded (spec §2 *execute_as_human*).

    BINDING checks apply to every ruling: the verdict word, the phase against
    the state, the hash against the phase's current artefact. DISPATCH checks
    apply to a review_ruling only: base, bundle, policy version, role and
    independence -- a human's correction comes from no dispatch.
    """
    from kernel import front
    verdict = cmd.payload.get("verdict")
    if verdict not in _VERDICT_WORDS:
        raise NotAuthorized(f"verdict {verdict!r} is not one of {sorted(_VERDICT_WORDS)}")

    state = store.run_state(cmd.run_id)
    phase = phase_of(state)
    if cmd.payload.get("phase") != phase:
        raise NotAuthorized(
            f"review names phase {cmd.payload.get('phase')!r} but the run is at "
            f"{state!r}, whose phase is {phase!r}"
        )

    if phase == "implementation":
        current = store.current_artifact(cmd.run_id)
        what = "this run's current output"
    else:
        current = store.phase_artifact(cmd.run_id, phase)
        what = f"the {phase} phase's current artefact"
    if current is None:
        raise NotAuthorized(f"nothing is under review: {what} is not recorded")
    given = cmd.payload.get("artifact_hash")
    if given != current:
        raise NotAuthorized(
            f"review binds artifact {str(given)[:12]}..., but {what} is "
            f"{current[:12]}...: an approval binds what was produced, not any "
            "object the store holds"
        )
    if not store.has_artifact(current):
        raise NotAuthorized(f"the kernel does not hold {current[:12]}...")

    if ruling == "human_ruling":
        return None

    binding = _binding_from(cmd.payload)
    observed_base = store.run_base_sha(cmd.run_id)
    if binding.base_sha != observed_base:
        raise NotAuthorized(
            f"review binds base {binding.base_sha!r}, but the kernel observed "
            f"{observed_base!r} for this run"
        )
    if role_for(store, cmd.run_id, cmd.generation) != Role.REVIEWER:
        raise NotAuthorized(
            "a review must come from an attempt dispatched in the reviewer role"
        )

    if phase == "implementation":
        conflicted = _conflicted_actors(store, cmd.run_id)
        if actor in conflicted:
            raise NotAuthorized(
                f"reviewer independence violated: {actor!r} is implementing this "
                f"run or produced the artifact under review (conflicted: {sorted(conflicted)})"
            )
        return binding

    epoch_n = front.epoch(store, cmd.run_id)
    expected_bundle = front.bundle_hash(store, cmd.run_id)
    if binding.context_bundle_hash != expected_bundle:
        raise NotAuthorized(
            f"review binds context_bundle_hash {binding.context_bundle_hash[:12]}..., "
            f"but epoch {epoch_n}'s bundle is {str(expected_bundle)[:12]}..."
        )
    expected_policy = front.policy_version(store, cmd.run_id)
    if binding.policy_version != expected_policy:
        raise NotAuthorized(
            f"review binds policy_version {binding.policy_version}, but this run's "
            f"policy_frozen is journal seq {expected_policy}"
        )
    findings = cmd.payload.get("findings_hash")
    if not isinstance(findings, str) or not store.has_artifact(findings):
        raise NotAuthorized(
            "a front-half review_ruling names findings_hash the kernel holds: the "
            "next author round is briefed with them"
        )
    submitted = front.newest_submission(store, cmd.run_id, phase, epoch_n)
    if submitted is not None and submitted.payload["author"] == actor:
        raise NotAuthorized(
            f"reviewer independence violated: {actor!r} submitted the {phase} under review"
        )
    return binding
```

`front.policy_version` is `kernel.policy.policy_version` re-exported: add `from kernel.policy import policy_of, policy_version  # noqa: F401` at the top of `front.py`.

In `authorize()`, after the state check (`if current not in allowed: raise`), add:

```python
    if cmd.name in ("submit_spec", "submit_plan"):
        _check_submit(store, cmd, current)
        return next_state

    if cmd.name == "record_review":
        verdict = cmd.payload.get("verdict")
        if verdict not in _VERDICT_WORDS:
            raise NotAuthorized(f"verdict {verdict!r} is not one of {sorted(_VERDICT_WORDS)}")
        return _review_destination(store, cmd.run_id, current, verdict, ruling)

    if cmd.name == "record_turn_ended":
        if cmd.payload.get("ended") not in TURN_ENDS:
            raise NotAuthorized(
                f"ended {cmd.payload.get('ended')!r} is not one of {sorted(TURN_ENDS)}"
            )
        if not isinstance(cmd.payload.get("session"), str) or not cmd.payload["session"]:
            raise NotAuthorized("record_turn_ended names the session whose turn ended")
        return None

    if cmd.name == "park":
        _check_park(store, cmd)
        return None
```

and change the signature to `def authorize(store, cmd, actor: str, *, ruling: str = "review_ruling") -> str | None:` (the existing `record_review` branch that returned `_VERDICTS[verdict]` is replaced by the one above). Add the two helpers:

```python
def _check_submit(store, cmd, state: str) -> None:
    from kernel import front
    if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
    h = cmd.payload.get("artifact_hash")
    if not isinstance(h, str) or not store.has_artifact(h):
        raise NotAuthorized(f"{cmd.name} names an artefact the kernel does not hold: {h!r}")
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if any(f.payload["hash"] == h for f in front.submissions(store, cmd.run_id, phase, epoch_n)):
        raise NotAuthorized(
            f"identical to the prior artefact: {h[:12]}... was already submitted "
            f"for {phase} in epoch {epoch_n}; a resubmission that did not change is not a revision"
        )
    if cmd.name == "submit_plan":
        if h == store.phase_artifact(cmd.run_id, "spec"):
            raise NotAuthorized("the plan is the run's current spec: a plan is a different artefact")
        if b"### Task" not in store.read_blob(h):
            raise NotAuthorized("plan has no tasks: no `### Task` heading (a shape check)")


def _check_park(store, cmd) -> None:
    if cmd.payload.get("reason") not in PARK_REASONS:
        raise NotAuthorized(f"park reason {cmd.payload.get('reason')!r} is not one of {sorted(PARK_REASONS)}")
    for key in ("session_id", "cursor_item_id", "reviewer"):
        if cmd.payload.get(key) is not None and not isinstance(cmd.payload.get(key), str):
            raise NotAuthorized(f"park {key} must be a string or null")
    if cmd.payload.get("findings_hash") is not None and not store.has_artifact(cmd.payload.get("findings_hash")):
        raise NotAuthorized("park findings_hash names an artefact the kernel does not hold")
    if cmd.payload.get("verdict") not in (None, "accept", "request_revision"):
        raise NotAuthorized("park verdict must be null, accept or request_revision")
```

Delete `_VERDICTS`; `grep -rn "_VERDICTS" v2/` must return nothing else (the projection reads verdict words from facts, not this table).

- [ ] **Step 5: Command names, side facts and the verdict payload in `commands.py`**

Add `"record_turn_ended", "park"` to `COMMAND_NAMES`. Thread `ruling` through: `submit()` calls `authorize(store, cmd, actor, ruling="review_ruling")` and `validate_review(store, cmd, actor, ruling="review_ruling")` (Task 8 generalises this). Inside the accepted transaction, replace the `if review_binding is not None:` block with:

```python
            if cmd.name == "record_review":
                from kernel import front
                from kernel.authz import phase_of
                state_now = store.run_state(cmd.run_id)
                store.append_fact(
                    run_id=cmd.run_id, kind=EventKind.REVIEW_VERDICT, actor=actor,
                    causal_command_id=cmd.idempotency_key,
                    payload={
                        "verdict": cmd.payload.get("verdict"),
                        "phase": phase_of(state_now),
                        "epoch": front.epoch(store, cmd.run_id),
                        "artifact_hash": cmd.payload.get("artifact_hash"),
                        "findings_hash": cmd.payload.get("findings_hash"),
                        "ruling": "review_ruling" if review_binding is not None else "human_ruling",
                        "binding_hash": None if review_binding is None else binding_hash(review_binding),
                        # From the dispatch record, not the binding: the
                        # binding says WHAT was approved, this says WHO.
                        "reviewer_identity": actor,
                    },
                )
            _side_fact(store, cmd, actor)
```

placed before the `if next_state is not None:` block so the side fact precedes the transition in the journal. Add:

```python
def _side_fact(store, cmd: Command, actor: str) -> None:
    """The fact an accepted front-half command writes beside COMMAND_ACCEPTED.
    Every value is derived by the kernel from the run's state, never copied
    from the payload except the fields the command exists to carry."""
    from kernel import front
    from kernel.authz import phase_of
    state = store.run_state(cmd.run_id)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if cmd.name in ("submit_spec", "submit_plan"):
        h = cmd.payload["artifact_hash"]
        rnd = len(front.submissions(store, cmd.run_id, phase, epoch_n)) + 1
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "hash": h, "author": actor, "round": rnd},
        )
        store.set_phase_artifact(cmd.run_id, phase, h)
    elif cmd.name == "record_turn_ended":
        prompt = front.newest_prompt(store, cmd.run_id, phase, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.TURN_ENDED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session": cmd.payload["session"], "ended": cmd.payload["ended"],
                     "phase": phase, "epoch": epoch_n, "generation": cmd.generation,
                     "prompt_key": None if prompt is None else prompt["key"]},
        )
    elif cmd.name == "park":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.PARKED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "reason": p["reason"],
                     "session_id": p.get("session_id"), "cursor_item_id": p.get("cursor_item_id"),
                     "findings_hash": p.get("findings_hash"), "verdict": p.get("verdict"),
                     "reviewer": p.get("reviewer"), "generation": cmd.generation},
        )
```

Tasks 8-11 extend `_side_fact` with their own `elif` arms.

- [ ] **Step 6: The driver**

`v2/tests/kernel/front.py`:

```python
"""The front-half driver every migrated test uses.

It performs the ceremony a coordinator performs -- dispatch, a satisfied
sess-create whose snapshot names the actor, a satisfied sess-prompt, the
turn's end as a fact, a satisfied sess-stop, then the command -- against
the kernel's Python API. A test that needs a run at `planned` gets one by
the only path the kernel admits. Tasks 8-12 add the guards that make each
step load-bearing; each of those tasks mutates this driver to prove it.
"""
from __future__ import annotations

import argparse
import json
import sys

from kernel import front as fq
from kernel.artifacts import put_artifact
from kernel.authz import phase_of
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.enqueue import _run_exists, create_run
from kernel.events import EventKind
from kernel.store import Store

SPEC_BYTES = b"# Spec\n\nThe thing, specified.\n"
PLAN_BYTES = b"# Plan\n\n### Task 1: do the thing\n\n- [ ] Step 1\n"


class Front:
    def __init__(self, store, run_id: str, *, author: str = "claude",
                 reviewer: str = "codex", labels=("bircher:autonomous",),
                 base_sha: str = "0" * 40, base_repo: str = "o/r",
                 issue: dict | None = None, project_config: dict | None = None,
                 existing: bool = False) -> None:
        self.store, self.run_id = store, run_id
        self.author, self.reviewer, self.base_sha = author, reviewer, base_sha
        self._n = 0
        if existing:
            assert _run_exists(store, run_id), f"run {run_id} does not exist"
            return
        issue = issue or {"number": 1, "title": "T", "body": "B",
                          "labels": list(labels), "comments": []}
        create_run(store, run_id=run_id, base_repo=base_repo, base_sha=base_sha,
                   issue=issue, project_config=project_config or {})

    # -- primitives ----------------------------------------------------------

    def _key(self, tag: str) -> str:
        self._n += 1
        return f"{tag}-{self._n}"

    def state(self) -> str:
        return self.store.run_state(self.run_id)

    def phase(self) -> str:
        return phase_of(self.state())

    def epoch(self) -> int:
        return fq.epoch(self.store, self.run_id)

    def _newest_id(self) -> str:
        return self.store.facts_for(self.run_id)[-1].id

    def _cmd(self, generation: int, name: str, payload: dict):
        return submit(self.store, Command(
            name=name, run_id=self.run_id,
            expected_version=self.store.run_version(self.run_id),
            idempotency_key=self._key(name), generation=generation, payload=payload,
        ))

    def _dispatch(self, role: str, actor: str) -> int:
        return dispatch(self.store, self.run_id, actor=actor, role=role).generation

    def _journal(self, generation: int, key: str, intent: dict, external: str | None) -> None:
        self.store.journal_intent(f"e-{key}", self.run_id, generation, "session_control",
                                  key, intent)
        self.store.mark_effect(key, "confirmed", external, run_id=self.run_id)

    def _create(self, generation: int, cause: str, *, actor: str | None = None) -> str:
        """A satisfied sess-create under *generation* -- and nothing else. The
        snapshot names the BUNDLE (`v2_author_<vendor>`), as the server does;
        the dispatch names the vendor (ruling 14)."""
        actor = actor or self.store.dispatches_for(self.run_id)[-1]["actor"]
        sid = f"s-{generation}"
        phase, epoch = self.phase(), self.epoch()
        ckey = f"sess-create:{self.run_id}:{generation}"
        body = {"agent_id": f"agent-{actor}", "host_id": "host_h1",
                "workspace": f"/w/{self.run_id}/{sid}", "title": ckey}
        self._journal(generation, ckey, {
            "argv": ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions",
                     "-H", "content-type: application/json", "-d", json.dumps(body)],
            "obligation": {"kind": "sess-create", "run": self.run_id,
                           "phase": phase, "epoch": epoch, "cause": cause},
        }, json.dumps({"id": sid, "agent_id": body["agent_id"], "agent_name": f"v2_author_{actor}",
                       "host_id": "h1", "workspace": body["workspace"], "title": ckey}))
        return sid

    def _newest_author_session(self) -> str | None:
        seat = fq.newest_seat(self.store, self.run_id, Role.AUTHOR, self.phase(), self.epoch())
        return None if seat is None else seat["session"]["id"]

    def _session(self, generation: int, cause: str, *, actor: str | None = None) -> str:
        """A satisfied sess-create and sess-prompt under *generation*."""
        sid = self._create(generation, cause, actor=actor)
        phase, epoch = self.phase(), self.epoch()
        prompt_hash = put_artifact(self.store, f"prompt for {sid}".encode())
        pkey = f"sess-prompt:{sid}:{generation}:{cause}"
        self._journal(generation, pkey, {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-prompt", "session": sid,
                           "phase": phase, "epoch": epoch, "cause": cause},
            "body": {"artifact": prompt_hash},
        }, f"item-{sid}-1")
        return sid

    def _end_turn(self, generation: int, sid: str, ended: str = "file") -> None:
        self._cmd(generation, "record_turn_ended", {"session": sid, "ended": ended})
        te = self.store.newest_fact(self.run_id, EventKind.TURN_ENDED)
        skey = f"sess-stop:{sid}:{generation}"
        self._journal(generation, skey, {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
            "body": {"event": "stop_session"},
        }, "")

    # -- rounds ----------------------------------------------------------------

    def author_round(self, artefact: bytes, *, ended: str = "file") -> str:
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        sid = self._session(g, cause)
        self._end_turn(g, sid, ended)
        h = put_artifact(self.store, artefact)
        name = "submit_spec" if self.phase() == "spec" else "submit_plan"
        self._cmd(g, name, {"artifact_hash": h})
        return h

    def review_round(self, verdict: str = "accept", findings: bytes = b"ok", **override) -> None:
        cause = self._newest_id()
        g = self._dispatch(Role.REVIEWER, self.reviewer)
        # Task 11 inserts issue_review_brief here, before the session.
        sid = self._session(g, cause)
        self._end_turn(g, sid)
        phase = self.phase()
        payload = {
            "phase": phase, "verdict": verdict,
            "artifact_hash": self.store.phase_artifact(self.run_id, phase),
            "base_sha": self.base_sha,
            "context_bundle_hash": fq.bundle_hash(self.store, self.run_id),
            "policy_version": fq.policy_version(self.store, self.run_id),
            "findings_hash": put_artifact(self.store, findings),
        }
        payload.update(override)
        self._cmd(g, "record_review", payload)

    def to_specified(self) -> "Front":
        self.author_round(SPEC_BYTES)
        self.review_round("accept")
        assert self.state() == "specified", self.state()
        return self

    def to_planned(self) -> "Front":
        self.to_specified()
        self.author_round(PLAN_BYTES)
        self.review_round("accept")
        assert self.state() == "planned", self.state()
        return self

    def to_implementing(self, *, implementer: str = "claude", output: bytes = b"impl") -> int:
        self.to_planned()
        g = self._dispatch(Role.IMPLEMENTER, implementer)
        self._cmd(g, "start_implementation", {})
        self._cmd(g, "record_implementation_output",
                  {"artifact_hash": put_artifact(self.store, output)})
        return g


def reach_specified(store, run_id: str, **kw) -> Front:
    return Front(store, run_id, **kw).to_specified()


def reach_planned(store, run_id: str, **kw) -> Front:
    return Front(store, run_id, **kw).to_planned()


def reach_implementing(store, run_id: str, **kw) -> Front:
    f = Front(store, run_id, **kw)
    f.to_implementing()
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tests.kernel.front")
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--to", required=True, choices=["specified", "planned", "implementing"])
    ap.add_argument("--existing", action="store_true",
                    help="drive a run the caller already created")
    ap.add_argument("--author", default="claude")
    ap.add_argument("--reviewer", default="codex")
    a = ap.parse_args(argv)
    store = Store.open(a.db)
    f = Front(store, a.run_id, author=a.author, reviewer=a.reviewer, existing=a.existing)
    getattr(f, f"to_{a.to}")()
    print(json.dumps({"run_id": a.run_id, "state": store.run_state(a.run_id)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Until Task 8 adds the human's `approve`, `to_specified`/`to_planned` reach their state only on a run with no gate; `test_accept_under_the_gate_stops_at_accepted` uses `author_round`/`review_round` directly for that reason.

- [ ] **Step 7: Run the new tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_front_table.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full suite and read the failures**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q 2>&1 | tail -60`
Expected: failures only in files that reach `specified`/`planned` through v1 submits, that submit `record_review` without `phase`, or that pin `COMMAND_NAMES`. Every failure is one of the shapes in Step 9.

- [ ] **Step 9: Migrate by rule**

The rule: **a test does not reach a front-half destination by submitting; it calls the driver.** Concretely, per shape:

1. **A helper that submits `submit_spec` then `submit_plan` to reach `planned`** (e.g. `tests/kernel/test_authorization.py::_advance_to_reviewing`, and the `_cmd`/`_submit` helpers in `test_commands.py`, `test_authz_r3.py`, `test_identity.py`, `test_lineage.py`, `test_merge_revalidation.py`, `test_mode.py`, `test_report.py`, `test_review_fixes.py`, `test_review_r2.py`, `test_review_r3b.py`, `test_review_r4.py`, `test_round6.py`, `test_effect_contract.py`, `test_effects.py`, `test_cli_command.py`, `test_decisions.py`, `tests/coordinator/test_recovery_table.py`, `tests/coordinator/test_revision_durability.py`): replace the two submits with `reach_planned(s, "r")` (or `reach_implementing`) from `tests.kernel.front`, and delete the now-unused `SPEC`/`PLAN` hash constants where they existed only to feed the submits. Where the test then compares `artifact_hash` to the spec hash it submitted, read it back: `s.phase_artifact("r", "spec")`. The run must exist through the driver: replace `s.create_run(...)` with `Front(s, "r", base_sha=BASE)` where the test later checks `base_sha`, so the driver's `base_sha` and the test's agree.
2. **A test whose subject is `submit_spec` itself** (its refusal for a stale version, its idempotent replay, its rejection record) keeps `submit_spec`, dispatches as `Role.AUTHOR`, and passes `{"artifact_hash": put_artifact(s, b"spec")}`; where it asserted the destination `"specified"` it now asserts `"spec_submitted"`. `tests/kernel/test_commands.py::_cmd`'s default payload becomes `{"artifact_hash": put_artifact(store, b"spec")}` and its default role `Role.AUTHOR`; `test_command_derived_from_an_older_version_is_refused` submits `submit_spec` (accepted; the version moves) and then `park` under the stale version — `park` is legal from `spec_submitted` and passes every payload check, so the CAS is the first thing that refuses it and the exception is `StaleVersion`. (`authorize` runs before the transaction's CAS in `submit`, so a second `submit_spec` would be refused as `NotAuthorized` by the identical-hash or turn guard first, not as stale.)
3. **`record_review` payloads** gain `"phase": "implementation"` (back half) — add it in each helper that builds a review payload rather than at every call site; `tests/execution/*` reviews go through `_kernel_record_review`, whose payload gains `"phase":"implementation"` in `batch/lib/kernel-client.sh` in this task (Task 20 rewrites the rest of the client).
4. **The pin of `COMMAND_NAMES`** in `test_commands.py::test_the_command_interface_is_closed_and_explicit` gains `"record_turn_ended", "park"`.
5. **Bash-driven lifecycles** (`tests/execution/test_lifecycle_functions.py`, `test_lifecycle_wiring.py`): where a test creates a run with `_kernel_run_start` and then reaches `planned` through `_kernel_submit_spec`/`_kernel_submit_plan`, replace the two bash calls with `python -m tests.kernel.front --db "$db" --run-id <id> --to planned --existing` run from `v2/` with the same interpreter the test already uses for the kernel CLI — but a run `_kernel_run_start` created has the default gates, so those tests create the run through the driver instead: drop `_kernel_run_start` from the script and call `python -m tests.kernel.front --db "$db" --run-id <id> --to planned` before the bash steps under test. Tests whose subject is `_kernel_run_start` keep it and stop at `queued`. Tests whose subject is `_kernel`'s exit-code handling of a refused command (`test_kernel_client.py:153-268`) keep `submit_spec`: it is still refused (no dispatched actor), with the same rc.
6. **`tests/execution/test_run_item_argument_wiring.py`** stubs `_kernel_submit_spec`/`_kernel_submit_plan` and asserts run_item calls them; that stays true until Task 20 removes the call sites, so this file is untouched here.

Run the suite after each file. Do not weaken an assertion to make it pass: if a test asserted something the new table makes false (a run at `specified` right after `submit_spec`), the test's premise was v1's, and the migrated test asserts the new premise (`spec_submitted`, then `specified` after `review_round`).

- [ ] **Step 10: Provenance rows**

`tests/kernel/test_provenance.py` scans `authz.py` (Global Constraints). Run it; the `missing` list names this task's reads. Add to `docs/design/provenance-table.md`, in the *Kernel state* table: `store.phase_artifact` (observed — `phase_artifacts`, written only by an accepted `submit_spec`/`submit_plan`), `store.read_blob` (observed — content-addressed bytes), `store.facts_of_kind` (observed — the append-only journal). In *Caller-presented, bound to kernel state*: `cmd.payload['phase']` (observed — refused unless equal to `phase_of(store.run_state)`), `cmd.payload['findings_hash']` (observed — refused unless the store holds it), `cmd.payload['hash']` and `cmd.payload['author']` (observed — read from the kernel's own `artifact_submitted` facts), `cmd.payload['ended']` (observed — refused unless one of `TURN_ENDS`), `cmd.payload['session']` (observed — Task 10 binds it to the newest satisfied `sess-create`/`sess-prompt`; until then a shape check), `cmd.payload['session_id']` and `cmd.payload['reviewer']` (observed — Task 10 and Task 12 bind them; a string-or-null check here). In *Asserted — declared residuals*: `cmd.payload['reason']` and `cmd.payload['cursor_item_id']` — "**Residual, §4.** The coordinator's reading of a park and of a listing: `reason` is bounded to `PARK_REASONS`, `cursor_item_id` to the §4 cursor invariant test (Task 19), neither to a kernel object." Update the row for `cmd.payload['artifact_hash']` to name `submit_spec`/`submit_plan` (held) and the phase artefact (review, approve). Extend the pinned set in `test_the_residuals_are_the_ones_we_know_about` with the two new asserted names, and extend the `words` map in `test_the_spec_and_the_table_agree_on_the_count` to `"ten"`…`"twenty"`, then set the count word in `docs/design/2026-08-23-v2-kernel-design.md` to the new total (count the rows).

- [ ] **Step 11: Run the full suite**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`
Expected: green — baseline count plus this task's 17 new tests.

- [ ] **Step 12: Mutation check, then commit**

Change `_review_destination`'s `"spec_accepted" if "spec" in gates else "specified"` to always `"specified"`: `test_accept_under_the_gate_stops_at_accepted` fails. Delete the identical-hash clause of `_check_submit`: `test_identical_resubmission_in_the_phase_and_epoch_is_refused` fails. Make `current_park` ignore `last_human_fact_seq`: `test_park_records_the_kernels_view_and_currency` fails at the `human_answer` line; drop `HUMAN_DIRECTION` from `HUMAN_FACT_KINDS`: it fails at the `human_direction` line. Restore all.

```bash
git add v2/kernel/authz.py v2/kernel/commands.py v2/kernel/front.py v2/tests/kernel/front.py v2/tests/kernel/test_front_table.py batch/lib/kernel-client.sh v2/tests docs/design/provenance-table.md docs/design/2026-08-23-v2-kernel-design.md
git commit -m "feat(kernel): front-half state table, phase artefacts, artifact_submitted; migrate the v1-flow tests"
```

---
### Task 8: The human's commands and `execute_as_human`

**Files:**
- Modify: `v2/kernel/commands.py` (`HUMAN_GENERATION`, `HUMAN_ACTOR`, `_submit`, `execute_as_human`, `_side_fact` arms)
- Modify: `v2/kernel/authz.py` (four commands in `_TRANSITIONS`, their checks, the human clauses of `_review_destination`)
- Modify: `v2/kernel/dispatch.py` (refuse the actor `human`)
- Modify: `v2/tests/kernel/front.py` (`approve`, `human`, `grant`, `answer`, `direct`; gates in `to_specified`/`to_planned`)
- Test: `v2/tests/kernel/test_human_commands.py`

**Interfaces:**
- Consumes: `front.current_park`, `front.epoch`, `phase_of`, `Store.phase_artifact` (Task 7); `put_artifact`.
- Produces:
  - `commands.HUMAN_GENERATION = -1`, `commands.HUMAN_ACTOR = "human"`, `commands.execute_as_human(store, cmd: Command) -> Result` (requires `cmd.generation == HUMAN_GENERATION`; actor fixed to `human`; no generation fence; halt, replay and `expected_version` CAS as `submit`), `commands.HUMAN_COMMANDS = frozenset({"record_human_answer", "record_human_direction", "approve_artifact", "grant_round"})` (reachable only through `execute_as_human`; `record_review` is reachable through both, with `ruling` telling which).
  - Commands: `record_human_answer {question_ids: list[str], answer: str, cursor_item_id: str|None}` (every front-half state; fact `human_answer {epoch, question_ids, answer, cursor_item_id}`); `record_human_direction {text: str, cursor_item_id: str|None}` (`queued`, `specified`; fact `human_direction {phase, epoch, text, cursor_item_id}`); `approve_artifact {artifact_hash}` (`spec_accepted → specified`, `plan_accepted → planned`; fact `human_ruling {ruling: "approve", phase, epoch, artifact_hash}`); `grant_round {}` (`queued`, `specified`, `spec_submitted`, `plan_submitted`; needs a current park; fact `human_ruling {ruling: "grant_round", phase, epoch, park_seq}`); human `record_review {phase, artifact_hash, verdict: "request_revision", findings: str}` (`spec_submitted`/`spec_accepted → queued`, `plan_submitted`/`plan_accepted → specified`; facts `review_verdict {…, ruling: "human_ruling", reviewer_identity: "human", findings_hash, binding_hash: null}` and `human_ruling {ruling: "request_revision", phase, epoch, artifact_hash}`).
  - Driver: `Front.human(name, payload) -> Result`, `Front.approve() -> None`, `Front.grant() -> None`, `Front.answer(answer, question_ids=(), cursor="i-h") -> None`, `Front.direct(text, cursor="i-h") -> None`; `to_specified`/`to_planned` approve at a gate.

- [ ] **Step 1: Write the failing tests**

`v2/tests/kernel/test_human_commands.py`:

```python
import itertools

import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.commands import (HUMAN_GENERATION, Command, StaleVersion, execute_as_human,
                             submit)
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


_N = itertools.count(1)


def _human(store, run_id, name, payload, key=None):
    return execute_as_human(store, Command(
        name=name, run_id=run_id, expected_version=store.run_version(run_id),
        idempotency_key=key or f"h-{next(_N)}",
        generation=HUMAN_GENERATION, payload=payload))


def test_human_commands_do_not_pass_through_submit(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    g = f._dispatch(Role.OPERATOR, "runner")
    h = s.phase_artifact("r-1", "spec")
    for name, payload in (("approve_artifact", {"artifact_hash": h}), ("grant_round", {}),
                          ("record_human_answer", {"question_ids": [], "answer": "x", "cursor_item_id": None}),
                          ("record_human_direction", {"text": "x", "cursor_item_id": None})):
        with pytest.raises(NotAuthorized, match="execute_as_human"):
            f._cmd(g, name, payload)
    assert s.run_state("r-1") == "spec_accepted"


def test_dispatch_refuses_the_human_actor(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1")
    with pytest.raises(ValueError, match="human"):
        dispatch(s, "r-1", actor="human", role=Role.OPERATOR)


def test_approve_moves_accepted_on_and_binds_the_hash(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:gate-plan",))
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    h = s.phase_artifact("r-1", "spec")
    with pytest.raises(NotAuthorized, match="current artefact"):
        _human(s, "r-1", "approve_artifact", {"artifact_hash": put_artifact(s, b"other")})
    _human(s, "r-1", "approve_artifact", {"artifact_hash": h})
    assert s.run_state("r-1") == "specified"
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert ruling.actor == "human"
    assert ruling.payload == {"ruling": "approve", "phase": "spec", "epoch": 0, "artifact_hash": h}
    accepted = [x for x in s.facts_of_kind("r-1", EventKind.COMMAND_ACCEPTED)
                if x.payload["command_name"] == "approve_artifact"]
    assert accepted[-1].actor == "human" and accepted[-1].payload["generation"] == HUMAN_GENERATION
    ph = f.author_round(PLAN_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "plan_accepted"
    _human(s, "r-1", "approve_artifact", {"artifact_hash": ph})
    assert s.run_state("r-1") == "planned"


def test_approve_is_illegal_from_submitted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        _human(s, "r-1", "approve_artifact", {"artifact_hash": s.phase_artifact("r-1", "spec")})


def test_human_request_revision_needs_only_four_fields(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    h = s.phase_artifact("r-1", "spec")
    with pytest.raises(NotAuthorized, match="request_revision"):
        _human(s, "r-1", "record_review", {"phase": "spec", "artifact_hash": h,
                                            "verdict": "accept", "findings": "fine"})
    _human(s, "r-1", "record_review", {"phase": "spec", "artifact_hash": h,
                                        "verdict": "request_revision", "findings": "missing the API"})
    assert s.run_state("r-1") == "queued"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    assert v.payload["ruling"] == "human_ruling" and v.payload["reviewer_identity"] == "human"
    assert v.payload["binding_hash"] is None
    assert v.payload["findings_hash"] == content_hash(b"missing the API")
    assert s.has_artifact(v.payload["findings_hash"])
    r = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert r.payload == {"ruling": "request_revision", "phase": "spec", "epoch": 0, "artifact_hash": h}
    # From *_submitted too, and to the plan's submitting state.
    f.author_round(SPEC_BYTES + b"\n## API\n"); f.review_round("accept")
    _human(s, "r-1", "approve_artifact", {"artifact_hash": s.phase_artifact("r-1", "spec")})
    ph = f.author_round(PLAN_BYTES)
    _human(s, "r-1", "record_review", {"phase": "plan", "artifact_hash": ph,
                                        "verdict": "request_revision", "findings": "no tests"})
    assert s.run_state("r-1") == "specified"


def test_human_review_is_front_half_only(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.to_implementing()
    with pytest.raises(NotAuthorized, match="front-half"):
        _human(s, "r-1", "record_review", {"phase": "implementation",
                                            "artifact_hash": s.current_artifact("r-1"),
                                            "verdict": "request_revision", "findings": "x"})


def test_grant_round_needs_a_current_park_and_consumes_it(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="park"):
        _human(s, "r-1", "grant_round", {})
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": "i-1",
                       "findings_hash": None, "verdict": "request_revision", "reviewer": "codex"})
    park = front.current_park(s, "r-1")
    _human(s, "r-1", "grant_round", {})
    assert front.grants(s, "r-1") == 1
    r = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert r.payload == {"ruling": "grant_round", "phase": "spec", "epoch": 0, "park_seq": park.seq}
    assert front.current_park(s, "r-1") is None
    with pytest.raises(NotAuthorized, match="park"):
        _human(s, "r-1", "grant_round", {})
    assert s.run_state("r-1") == "spec_submitted"


def test_grant_round_is_illegal_from_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_accepted'"):
        _human(s, "r-1", "grant_round", {})


def test_answer_and_direction_carry_the_epoch_and_phase(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    _human(s, "r-1", "record_human_answer", {"question_ids": ["q1", "q2"], "answer": "yes, both",
                                              "cursor_item_id": "i-7"})
    a = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    assert a.actor == "human"
    assert a.payload == {"epoch": 0, "question_ids": ["q1", "q2"], "answer": "yes, both",
                         "cursor_item_id": "i-7"}
    with pytest.raises(NotAuthorized, match="answer"):
        _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "", "cursor_item_id": None})
    _human(s, "r-1", "record_human_direction", {"text": "use sqlite", "cursor_item_id": "i-8"})
    d = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    assert d.payload == {"phase": "spec", "epoch": 0, "text": "use sqlite", "cursor_item_id": "i-8"}
    f.author_round(SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        _human(s, "r-1", "record_human_direction", {"text": "x", "cursor_item_id": None})


def test_execute_as_human_skips_the_fence_not_the_version_or_the_halt(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)                       # the current generation is an author's
    v = s.run_version("r-1")
    _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "a", "cursor_item_id": None}, key="k1")
    with pytest.raises(StaleVersion):
        execute_as_human(s, Command(name="record_human_answer", run_id="r-1", expected_version=v,
                                    idempotency_key="k2", generation=HUMAN_GENERATION,
                                    payload={"question_ids": [], "answer": "b", "cursor_item_id": None}))
    again = execute_as_human(s, Command(name="record_human_answer", run_id="r-1", expected_version=v,
                                        idempotency_key="k1", generation=HUMAN_GENERATION,
                                        payload={"question_ids": [], "answer": "a", "cursor_item_id": None}))
    assert again.replayed is True
    with pytest.raises(ValueError, match="HUMAN_GENERATION"):
        execute_as_human(s, Command(name="record_human_answer", run_id="r-1",
                                    expected_version=s.run_version("r-1"), idempotency_key="k3",
                                    generation=0, payload={"question_ids": [], "answer": "c", "cursor_item_id": None}))
    s.set_reconciliation("r-1", {"why": "test"})
    with pytest.raises(RuntimeError, match="halted"):
        _human(s, "r-1", "record_human_answer", {"question_ids": [], "answer": "d", "cursor_item_id": None}, key="k4")
    requested = [x for x in s.facts_of_kind("r-1", EventKind.COMMAND_REQUESTED) if x.actor == "human"]
    assert len(requested) == 4          # k1, k2, the k1 replay, k4; the generation-0 call wrote nothing


def test_driver_approves_at_a_gate(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:gate-plan",))
    f.to_planned()
    assert s.run_state("r-1") == "planned"
    assert [r.payload["ruling"] for r in s.facts_of_kind("r-1", EventKind.HUMAN_RULING)] == ["approve", "approve"]
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_human_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'HUMAN_GENERATION' from 'kernel.commands'`.

- [ ] **Step 3: `_submit` and `execute_as_human` in `commands.py`**

Add the constants and `HUMAN_COMMANDS`, then split `submit`:

```python
#: The generation a human command carries. No dispatch record has it, so no
#: fenced path can claim it, and `dispatch()` refuses the actor "human".
HUMAN_GENERATION = -1
HUMAN_ACTOR = "human"

#: Reachable only through execute_as_human. record_review is reachable
#: through both paths; `ruling` says which.
HUMAN_COMMANDS = frozenset({
    "record_human_answer", "record_human_direction", "approve_artifact", "grant_round",
})

COMMAND_NAMES = frozenset({ ...existing..., "record_turn_ended", "park",
    "record_human_answer", "record_human_direction", "approve_artifact", "grant_round",
})


def submit(store, cmd: Command) -> Result:
    """The fenced path: identity is READ from the dispatch record."""
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")
    named = ACTOR_FIELDS & cmd.payload.keys()
    if named:
        raise ValueError(
            f"payload names an actor via {sorted(named)}: identity is assigned "
            "by the kernel from its dispatch record, never carried in a payload"
        )
    actor = actor_for(store, cmd.run_id, cmd.generation)
    return _submit(store, cmd, actor, fenced=True, ruling="review_ruling")


def execute_as_human(store, cmd: Command) -> Result:
    """spec §2 *execute_as_human*: the human's commands go through the same
    journal with the actor fixed to `human`, no generation fence -- a human
    fact must land whichever generation is current -- and concurrency by
    `expected_version` alone."""
    if cmd.name not in COMMAND_NAMES:
        raise ValueError(f"unknown command: {cmd.name}")
    if cmd.generation != HUMAN_GENERATION:
        raise ValueError(
            f"a human command carries generation HUMAN_GENERATION ({HUMAN_GENERATION}), "
            f"not {cmd.generation}"
        )
    named = ACTOR_FIELDS & cmd.payload.keys()
    if named:
        raise ValueError(f"payload names an actor via {sorted(named)}")
    return _submit(store, cmd, HUMAN_ACTOR, fenced=False, ruling="human_ruling")


def _submit(store, cmd: Command, actor: str | None, *, fenced: bool, ruling: str) -> Result:
    recorded_actor = UNDISPATCHED if actor is None else actor
    from kernel.effects import is_halted
    store.append_fact(... COMMAND_REQUESTED as today ...)
    prior = store.command_result(cmd.idempotency_key, run_id=cmd.run_id)
    if prior is not None:
        ... as today ...
    if actor is None:
        ... as today (NotAuthorized: no dispatched actor) ...
    if is_halted(store, cmd.run_id) and cmd.name != "cancel_run":
        ... as today ...
    if fenced and cmd.generation != current_generation(store, cmd.run_id):
        ... as today (OwnershipLost) ...
    try:
        next_state = authorize(store, cmd, actor, ruling=ruling)
    except Exception as exc:
        ... as today ...
    try:
        review_binding = (
            validate_review(store, cmd, actor, ruling=ruling)
            if cmd.name == "record_review" else None
        )
    except Exception as exc:
        ... as today ...
    ... the transaction as today, with the review payload from Task 7 and _side_fact ...
```

The body of `_submit` is the body `submit` had, with the two named differences: the fence is skipped when `fenced` is false, and `ruling` flows into `authorize` and `validate_review`. `submit` keeps its actor-field refusal so the behaviour of every existing test is unchanged. In the `REVIEW_VERDICT` payload block from Task 7, compute the findings hash so a human's `findings` text lands as bytes:

```python
                findings_hash = cmd.payload.get("findings_hash")
                if findings_hash is None and isinstance(cmd.payload.get("findings"), str):
                    findings_hash = put_artifact(store, cmd.payload["findings"].encode("utf-8"))
```

and use `findings_hash` in the payload (`from kernel.artifacts import put_artifact` at the top of `commands.py`). Then add the arms to `_side_fact`:

```python
    elif cmd.name == "record_human_answer":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_ANSWER, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "question_ids": list(cmd.payload["question_ids"]),
                     "answer": cmd.payload["answer"], "cursor_item_id": cmd.payload.get("cursor_item_id")},
        )
    elif cmd.name == "record_human_direction":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_DIRECTION, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "text": cmd.payload["text"],
                     "cursor_item_id": cmd.payload.get("cursor_item_id")},
        )
    elif cmd.name == "approve_artifact":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "approve", "phase": phase, "epoch": epoch_n,
                     "artifact_hash": cmd.payload["artifact_hash"]},
        )
    elif cmd.name == "grant_round":
        park = front.current_park(store, cmd.run_id)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "grant_round", "phase": phase, "epoch": epoch_n,
                     "park_seq": park.seq},
        )
    elif cmd.name == "record_review" and actor == HUMAN_ACTOR:
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"ruling": "request_revision", "phase": phase, "epoch": epoch_n,
                     "artifact_hash": cmd.payload["artifact_hash"]},
        )
```

`_side_fact` runs inside the transaction before the transition, so `current_park` still sees the park the grant consumes and `phase` is still the phase the human ruled on.

- [ ] **Step 4: The table rows and checks in `authz.py`**

Add to `_TRANSITIONS`:

```python
    "record_human_answer": (FRONT_HALF_STATES, None),
    "record_human_direction": (frozenset({"queued", "specified"}), None),
    # Destination by phase: computed in authorize().
    "approve_artifact": (frozenset({"spec_accepted", "plan_accepted"}), None),
    "grant_round": (frozenset({"queued", "specified", "spec_submitted", "plan_submitted"}), None),
```

In `authorize()`, before the state check:

```python
    from kernel.commands import HUMAN_COMMANDS
    if cmd.name in HUMAN_COMMANDS and ruling != "human_ruling":
        raise NotAuthorized(
            f"{cmd.name} is the human's: it reaches the kernel only through "
            "execute_as_human, never a dispatched generation"
        )
```

and after the state check, the four branches:

```python
    if cmd.name == "record_human_answer":
        ids = cmd.payload.get("question_ids")
        if not isinstance(ids, list) or not all(isinstance(q, str) for q in ids):
            raise NotAuthorized("record_human_answer question_ids must be a list of ids")
        if not isinstance(cmd.payload.get("answer"), str) or not cmd.payload.get("answer").strip():
            raise NotAuthorized("record_human_answer carries a non-empty answer")
        if cmd.payload.get("cursor_item_id") is not None and not isinstance(cmd.payload.get("cursor_item_id"), str):
            raise NotAuthorized("cursor_item_id must be a string or null")
        return None

    if cmd.name == "record_human_direction":
        if not isinstance(cmd.payload.get("text"), str) or not cmd.payload.get("text").strip():
            raise NotAuthorized("record_human_direction carries non-empty text")
        if cmd.payload.get("cursor_item_id") is not None and not isinstance(cmd.payload.get("cursor_item_id"), str):
            raise NotAuthorized("cursor_item_id must be a string or null")
        return None

    if cmd.name == "approve_artifact":
        phase = phase_of(current)
        held = store.phase_artifact(cmd.run_id, phase)
        if cmd.payload.get("artifact_hash") != held:
            raise NotAuthorized(
                f"approve_artifact names {str(cmd.payload.get('artifact_hash'))[:12]}..., but "
                f"the {phase} phase's current artefact is {str(held)[:12]}...: the kernel holds "
                "the hash; the caller can only supply the right answer"
            )
        return "specified" if phase == "spec" else "planned"

    if cmd.name == "grant_round":
        from kernel import front
        if front.current_park(store, cmd.run_id) is None:
            raise NotAuthorized(
                "grant_round needs a current park: nothing is stalled, and a grant "
                "at a running seat would displace it"
            )
        return None
```

Replace `_review_destination` from Task 7 with this order — the human clause first, so a human ruling is never routed by the back-half or gate logic:

```python
def _review_destination(store, run_id: str, state: str, verdict: str, ruling: str) -> str:
    from kernel.policy import policy_of
    phase = phase_of(state)
    if ruling == "human_ruling":
        if state not in FRONT_HALF_STATES:
            raise NotAuthorized("a human record_review is front-half only (spec §2 Commands)")
        if verdict != "request_revision":
            raise NotAuthorized(
                "the human's record_review is request_revision; approval is "
                "approve_artifact, after the reviewer"
            )
        return "queued" if phase == "spec" else "specified"
    if state in ("implementing", "reviewing"):
        return _BACK_HALF_DESTINATIONS[verdict]
    if verdict == "reject":
        raise NotAuthorized(
            "reject is not a front-half verdict: a spec or plan is accepted or "
            "revised, never rejected (spec §2 Commands)"
        )
    if state.endswith("_accepted"):
        raise NotAuthorized(
            f"record_review from {state!r} is the human's correction only "
            "(a human_ruling); a reviewer has already ruled on this hash"
        )
    if verdict == "request_revision":
        return "queued" if phase == "spec" else "specified"
    gates = policy_of(store, run_id).gates
    if phase == "spec":
        return "spec_accepted" if "spec" in gates else "specified"
    return "plan_accepted" if "plan" in gates else "planned"
```

- [ ] **Step 5: `dispatch()` refuses the human actor**

In `v2/kernel/dispatch.py::dispatch`, after the empty-actor check:

```python
    if actor == "human":
        raise ValueError(
            "'human' is not a dispatchable actor: human commands go through "
            "execute_as_human and carry no generation"
        )
```

- [ ] **Step 6: The driver learns the human's commands**

In `v2/tests/kernel/front.py` add to `Front`:

```python
    def human(self, name: str, payload: dict):
        from kernel.commands import HUMAN_GENERATION, execute_as_human
        return execute_as_human(self.store, Command(
            name=name, run_id=self.run_id,
            expected_version=self.store.run_version(self.run_id),
            idempotency_key=self._key(f"human-{name}"), generation=HUMAN_GENERATION,
            payload=payload,
        ))

    def approve(self) -> None:
        self.human("approve_artifact",
                   {"artifact_hash": self.store.phase_artifact(self.run_id, self.phase())})

    def grant(self) -> None:
        self.human("grant_round", {})

    def answer(self, answer: str, question_ids=(), cursor: str | None = "i-h") -> None:
        self.human("record_human_answer",
                   {"question_ids": list(question_ids), "answer": answer, "cursor_item_id": cursor})

    def direct(self, text: str, cursor: str | None = "i-h") -> None:
        self.human("record_human_direction", {"text": text, "cursor_item_id": cursor})
```

and make the two reach helpers pass a gate:

```python
    def to_specified(self) -> "Front":
        self.author_round(SPEC_BYTES)
        self.review_round("accept")
        if self.state() == "spec_accepted":
            self.approve()
        assert self.state() == "specified", self.state()
        return self

    def to_planned(self) -> "Front":
        self.to_specified()
        self.author_round(PLAN_BYTES)
        self.review_round("accept")
        if self.state() == "plan_accepted":
            self.approve()
        assert self.state() == "planned", self.state()
        return self
```

- [ ] **Step 7: Provenance rows, then run the tests**

Add to `docs/design/provenance-table.md` (*Asserted — declared residuals*): `cmd.payload['question_ids']`, `cmd.payload['answer']`, `cmd.payload['text']` — "**Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path, no dispatched generation can reach it) and when (the epoch, the cursor); the content is the human's to say." Extend the pinned set and the count word as in Task 7.

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_human_commands.py tests/kernel/test_front_table.py tests/kernel/test_commands.py tests/kernel/test_provenance.py -q`
Expected: PASS. Update `test_the_command_interface_is_closed_and_explicit`'s pin with the four names. Full suite: green.

- [ ] **Step 8: Mutation check, then commit**

Make `execute_as_human` pass `fenced=True`: `test_execute_as_human_skips_the_fence_not_the_version_or_the_halt` fails at its first call (the current generation is the author's). Drop the `HUMAN_COMMANDS` refusal in `authorize`: `test_human_commands_do_not_pass_through_submit` fails. Drop the `current_park is None` check: `test_grant_round_needs_a_current_park_and_consumes_it` fails at its first line. Restore all three.

```bash
git add v2/kernel/commands.py v2/kernel/authz.py v2/kernel/dispatch.py v2/tests/kernel/front.py v2/tests/kernel/test_human_commands.py v2/tests/kernel/test_commands.py v2/tests/kernel/test_provenance.py docs/design/provenance-table.md docs/design/2026-08-23-v2-kernel-design.md
git commit -m "feat(kernel): execute_as_human and the human's four commands"
```

---
### Task 9: The grill, the cursor's facts, and bundle revision as a command

**Files:**
- Modify: `v2/kernel/authz.py` (five commands in `_TRANSITIONS`, their checks, the grill clause of `_check_submit`)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact` arms)
- Modify: `v2/kernel/front.py` (grill and cursor queries)
- Modify: `v2/kernel/bundle.py` (`snapshot_diff`; `revise_bundle` PUTs the bytes)
- Modify: `v2/kernel/grill.py` (`front_packet`)
- Modify: `v2/tests/kernel/front.py` (`_prompt`, `ask_round`, `author_round(resume=)`, `revise`)
- Test: `v2/tests/kernel/test_grill_commands.py`, `v2/tests/kernel/test_cursor_facts.py`, `v2/tests/kernel/test_revise_bundle.py`

**Interfaces:**
- Consumes: `bundle.snapshot`, `bundle.bundle_hash`, `bundle.is_relevant_change` (Task 5); `front.epoch`, `front.bundle_hash`, `front.satisfied_effects` (Task 7); `execute_as_human` (Task 8); `Store.read_blob` (Task 1).
- Produces:
  - Commands (the `prompt_item` duplicate check is per `session_id`): `record_model_question {question_id: str, question: str}` (author role; every front-half state; fact `model_question {epoch, phase, question_id, question}`); `record_model_ruling {question_id, ruling: str, reasoning: str, cost_if_wrong: str}` (author role; every front-half state; refused when `question_id` names no `model_question` of the epoch; fact `model_ruling {epoch, question_id, ruling, reasoning, cost_if_wrong}`); `dismiss_human_item {cursor_item_id: str, rejection: str}` (every front-half state, any role; fact `human_item_dismissed {epoch, cursor_item_id, rejection}`); `record_prompt_item {session_id, item_id, sha256}` (every front-half state, any role; fact `prompt_item {session_id, item_id, sha256}`); `revise_bundle {issue: dict}` (every front-half state, any role; → `queued`; fact `bundle_revised {bundle_hash, prior_bundle_hash, reason: "issue changed", diff}`; the new snapshot's canonical bytes are PUT).
  - `submit_spec` is refused while the grill is open (`front.grill_open`).
  - `front.epoch_facts(store, run_id, kind, epoch_n) -> list[Fact]` (facts of `kind` whose payload `epoch == epoch_n`), `front.grill_open(store, run_id) -> bool`, `front.human_rejections(store, run_id) -> list[Fact]`, `front.dismissed_rejection_ids(store, run_id) -> set[str]`, `front.prompt_hashes_of(store, run_id, session_id) -> set[str]` (the `body.artifact` of every satisfied `sess-prompt` of that session).
  - `bundle.snapshot_diff(old: dict, new: dict) -> dict` = `{"changed": [field, ...], "unified": str}` (unified diff of the two snapshots' indented canonical JSON, capped at 65536 characters with a final line `... truncated`); `bundle.revise_bundle` now also PUTs `canonical_bytes(new_snapshot)`.
  - `grill.front_packet(store, run_id) -> dict` = `{"canon_version": 2, "epochs": {epoch: {"questions": [...], "rulings": [...], "answers": [...]}}}` in journal order.
  - Driver: `Front._prompt(generation, sid, cause) -> None` (a satisfied `sess-prompt` alone), `Front.ask_round(questions: list[tuple[str, str]], rulings: dict[str, str] | None = None) -> str` (an author turn that ends with questions, returns the session id), `Front.author_round(artefact, *, ended="file", resume: str | None = None)` (with `resume=<sid>` the round prompts that session under a fresh generation instead of creating one), `Front.revise(issue: dict) -> None`.

- [ ] **Step 1: Write the grill tests**

`v2/tests/kernel/test_grill_commands.py`:

```python
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
    assert [r["ruling"] for r in e0["rulings"]] == ["A"]
    assert [a["answer"] for a in e0["answers"]] == ["fine"]
```

- [ ] **Step 2: Write the cursor-fact tests**

`v2/tests/kernel/test_cursor_facts.py`:

```python
import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def _refused_human_approve(s, f):
    """A human token the kernel refuses: approve at spec_submitted."""
    with pytest.raises(NotAuthorized):
        execute_as_human(s, Command(name="approve_artifact", run_id="r-1",
                                    expected_version=s.run_version("r-1"),
                                    idempotency_key=f._key("bad"), generation=HUMAN_GENERATION,
                                    payload={"artifact_hash": s.phase_artifact("r-1", "spec")}))
    return s.newest_fact("r-1", EventKind.COMMAND_REJECTED)


def test_dismiss_names_a_human_rejection_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    rej = _refused_human_approve(s, f)
    assert rej.actor == "human"
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="command_rejected"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": "not-a-fact"})
    # A rejection attributed to a dispatched actor is not a human token.
    with pytest.raises(NotAuthorized):
        f._cmd(g, "approve_artifact", {"artifact_hash": "0" * 64})
    model_rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    with pytest.raises(NotAuthorized, match="human"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": model_rej.id})
    park_before = front.current_park(s, "r-1")
    f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": rej.id})
    d = s.newest_fact("r-1", EventKind.HUMAN_ITEM_DISMISSED)
    assert d.payload == {"epoch": 0, "cursor_item_id": "i-5", "rejection": rej.id}
    assert d.actor == "runner"
    assert front.dismissed_rejection_ids(s, "r-1") == {rej.id}
    assert front.current_park(s, "r-1") == park_before      # not a human fact
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-6", "rejection": rej.id})


def test_dismiss_does_not_consume_a_park(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": "s-x", "cursor_item_id": "i-1",
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    rej = _refused_human_approve(s, f)
    f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-2", "rejection": rej.id})
    assert front.current_park(s, "r-1") is not None


def test_prompt_item_binds_to_a_satisfied_prompt_of_that_session(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    hashes = front.prompt_hashes_of(s, "r-1", sid)
    assert len(hashes) == 1
    (h,) = hashes
    with pytest.raises(NotAuthorized, match="sess-prompt"):
        f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": "a" * 64})
    with pytest.raises(NotAuthorized, match="sess-prompt"):
        f._cmd(g, "record_prompt_item", {"session_id": "s-other", "item_id": "it-1", "sha256": h})
    f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": h})
    p = s.newest_fact("r-1", EventKind.PROMPT_ITEM)
    assert p.payload == {"session_id": sid, "item_id": "it-1", "sha256": h}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": h})
```

- [ ] **Step 3: Write the revision tests**

`v2/tests/kernel/test_revise_bundle.py`:

```python
import pytest

from kernel import bundle, front
from kernel.authz import NotAuthorized
from kernel.canon import canonical_bytes, content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front

ISSUE = {"number": 1, "title": "T", "body": "B", "labels": ["bircher:queued"], "comments": []}


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_a_relevant_change_resets_to_queued_and_bumps_the_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=dict(ISSUE, labels=["bircher:queued", "bircher:autonomous"]))
    f.author_round(SPEC_BYTES); f.review_round("accept")
    f.author_round(PLAN_BYTES)
    assert s.run_state("r-1") == "plan_submitted"
    old_hash = front.bundle_hash(s, "r-1")
    new_issue = dict(ISSUE, body="B\n\nAlso handle the dark theme.",
                     labels=["bircher:running", "bircher:autonomous"])
    f.revise(new_issue)
    assert s.run_state("r-1") == "queued"
    assert front.epoch(s, "r-1") == 1
    fact = s.newest_fact("r-1", EventKind.BUNDLE_REVISED)
    new_hash = bundle.bundle_hash(bundle.snapshot(new_issue))
    assert fact.payload["bundle_hash"] == new_hash
    assert fact.payload["prior_bundle_hash"] == old_hash
    assert fact.payload["reason"] == "issue changed"
    assert fact.payload["diff"]["changed"] == ["body"]
    assert "dark theme" in fact.payload["diff"]["unified"]
    assert s.read_blob(new_hash) == canonical_bytes(bundle.snapshot(new_issue))
    assert front.bundle_hash(s, "r-1") == new_hash
    # The phase artefacts of epoch 0 are still readable, but a fresh epoch submits afresh.
    assert s.phase_artifact("r-1", "spec") is not None
    f.author_round(SPEC_BYTES)                       # same bytes, new epoch: not identical
    assert s.run_state("r-1") == "spec_submitted"
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED).payload["epoch"] == 1


def test_a_bircher_only_change_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=ISSUE)
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="relevant"):
        f._cmd(g, "revise_bundle", {"issue": dict(ISSUE, labels=["bircher:running"],
                                                  comments=[{"id": 1, "author": "bircher",
                                                             "body": "bircher: outcome=escalated"}])})
    assert s.run_state("r-1") == "spec_submitted" and front.epoch(s, "r-1") == 0


def test_revision_consumes_a_park_and_is_not_a_round(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=ISSUE)
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.revise(dict(ISSUE, title="T2"))
    assert front.current_park(s, "r-1") is None            # the transition consumed it
    assert s.run_state("r-1") == "queued"


def test_snapshot_diff_shape_and_cap():
    old = bundle.snapshot(ISSUE)
    new = bundle.snapshot(dict(ISSUE, title="T2", body="x" * 200_000))
    d = bundle.snapshot_diff(old, new)
    assert d["changed"] == ["body", "title"]
    assert len(d["unified"]) <= 65536 + len("\n... truncated")
    assert d["unified"].endswith("... truncated")
    assert bundle.snapshot_diff(old, old) == {"changed": [], "unified": ""}


def test_v1_revise_bundle_function_now_puts_the_bytes(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1", issue=ISSUE)
    snap = bundle.snapshot(dict(ISSUE, title="T3"))
    h = bundle.revise_bundle(s, "r-1", new_snapshot=snap, reason="operator")
    assert s.read_blob(h) == canonical_bytes(snap)
```

- [ ] **Step 4: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_grill_commands.py tests/kernel/test_cursor_facts.py tests/kernel/test_revise_bundle.py -q`
Expected: FAIL — `AttributeError: 'Front' object has no attribute 'ask_round'`, `module 'kernel.front' has no attribute 'grill_open'`, `no attribute 'snapshot_diff'`.

- [ ] **Step 5: Queries in `front.py`**

Append:

```python
def epoch_facts(store, run_id: str, kind: str, epoch_n: int) -> list:
    return [f for f in store.facts_of_kind(run_id, kind) if f.payload.get("epoch") == epoch_n]


def grill_open(store, run_id: str) -> bool:
    """spec §2 Refusals, submit_spec: under grill=human, no human_answer in
    the epoch, or a model_question of the epoch newer than its last answer."""
    if policy_of(store, run_id).grill != "human":
        return False
    n = epoch(store, run_id)
    answers = epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
    if not answers:
        return True
    questions = epoch_facts(store, run_id, EventKind.MODEL_QUESTION, n)
    return bool(questions) and questions[-1].seq > answers[-1].seq


def human_rejections(store, run_id: str) -> list:
    return [f for f in store.facts_of_kind(run_id, EventKind.COMMAND_REJECTED)
            if f.actor == "human"]


def dismissed_rejection_ids(store, run_id: str) -> set[str]:
    return {f.payload["rejection"]
            for f in store.facts_of_kind(run_id, EventKind.HUMAN_ITEM_DISMISSED)}


def prompt_hashes_of(store, run_id: str, session_id: str) -> set[str]:
    out = set()
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        if row["intent"]["obligation"].get("session") == session_id:
            out.add(row["intent"].get("body", {}).get("artifact"))
    out.discard(None)
    return out
```

- [ ] **Step 6: `bundle.py` — the diff, and bytes on the v1 path**

```python
import difflib
import json

DIFF_CAP = 65536


def snapshot_diff(old: dict, new: dict) -> dict:
    """What changed between two snapshots, for the next author's findings."""
    changed = sorted(k for k in FROZEN_FIELDS if old.get(k) != new.get(k))
    if not changed:
        return {"changed": [], "unified": ""}
    a = json.dumps(old, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    b = json.dumps(new, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    text = "\n".join(difflib.unified_diff(a, b, "before", "after", lineterm=""))
    if len(text) > DIFF_CAP:
        text = text[:DIFF_CAP] + "\n... truncated"
    return {"changed": changed, "unified": text}
```

and in `revise_bundle` (the v1 function), before `append_fact`:

```python
    from kernel.artifacts import put_artifact
    from kernel.canon import canonical_bytes
    h = put_artifact(store, canonical_bytes(new_snapshot))
    assert h == bundle_hash(new_snapshot)
```

(replacing `h = bundle_hash(new_snapshot)`).

- [ ] **Step 7: `grill.py` — the v2 packet**

Append to `v2/kernel/grill.py`:

```python
FRONT_PACKET_VERSION = 2


def front_packet(store, run_id: str) -> dict:
    """The front half's decision ledger, from facts, grouped by epoch: every
    model_question, every model_ruling, every human_answer, in journal order.
    The §8 proof reads this to require at least one ruling per run."""
    epochs: dict[int, dict] = {}

    def bucket(n: int) -> dict:
        return epochs.setdefault(n, {"questions": [], "rulings": [], "answers": []})

    for fact in store.facts_for(run_id):
        p = fact.payload
        if fact.kind == EventKind.MODEL_QUESTION and "epoch" in p:
            bucket(p["epoch"])["questions"].append(
                {"question_id": p["question_id"], "question": p["question"],
                 "phase": p["phase"], "seq": fact.seq})
        elif fact.kind == EventKind.MODEL_RULING:
            bucket(p["epoch"])["rulings"].append(
                {"question_id": p["question_id"], "ruling": p["ruling"],
                 "reasoning": p["reasoning"], "cost_if_wrong": p["cost_if_wrong"], "seq": fact.seq})
        elif fact.kind == EventKind.HUMAN_ANSWER:
            bucket(p["epoch"])["answers"].append(
                {"question_ids": list(p["question_ids"]), "answer": p["answer"], "seq": fact.seq})
    return {"canon_version": FRONT_PACKET_VERSION, "epochs": epochs}
```

The v1 functions (`record_model_question`, `record_human_answer`, `decision_packet`, `unanswered`, `packet_hash`) stay: `enqueue()` and its tests use them, and a v1 `model_question` fact has no `epoch`, which is why the reader above checks for one.

- [ ] **Step 8: Table rows and checks in `authz.py`**

Add to `_TRANSITIONS`:

```python
    "record_model_question": (FRONT_HALF_STATES, None),
    "record_model_ruling": (FRONT_HALF_STATES, None),
    "dismiss_human_item": (FRONT_HALF_STATES, None),
    "record_prompt_item": (FRONT_HALF_STATES, None),
    "revise_bundle": (FRONT_HALF_STATES, "queued"),
```

In `_check_submit`, after the role check:

```python
    from kernel import front
    if cmd.name == "submit_spec" and front.grill_open(store, cmd.run_id):
        raise NotAuthorized(
            "the grill is open: under grill=human the spec waits for a human_answer "
            "in this epoch newer than the newest model_question"
        )
```

In `authorize()`, after the state check, the five branches:

```python
    if cmd.name in ("record_model_question", "record_model_ruling"):
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
            raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
        qid = cmd.payload.get("question_id")
        if not isinstance(qid, str) or not qid:
            raise NotAuthorized(f"{cmd.name} names a question_id")
        if cmd.name == "record_model_question":
            if not isinstance(cmd.payload.get("question"), str) or not cmd.payload.get("question").strip():
                raise NotAuthorized("record_model_question carries a non-empty question")
            return None
        for key in ("ruling", "reasoning", "cost_if_wrong"):
            if not isinstance(cmd.payload.get(key), str) or not cmd.payload.get(key).strip():
                raise NotAuthorized(f"record_model_ruling carries a non-empty {key}")
        n = front.epoch(store, cmd.run_id)
        asked = {q.payload["question_id"] for q in front.epoch_facts(store, cmd.run_id, EventKind.MODEL_QUESTION, n)}
        if qid not in asked:
            raise NotAuthorized(
                f"record_model_ruling names question_id {qid!r}, which no "
                f"model_question of epoch {n} asked"
            )
        return None

    if cmd.name == "dismiss_human_item":
        from kernel import front
        if not isinstance(cmd.payload.get("cursor_item_id"), str) or not cmd.payload.get("cursor_item_id"):
            raise NotAuthorized("dismiss_human_item carries the cursor to move past")
        rej = cmd.payload.get("rejection")
        human = {f.id: f for f in front.human_rejections(store, cmd.run_id)}
        if rej not in human:
            raise NotAuthorized(
                f"dismiss_human_item names {rej!r}, which is not a command_rejected fact "
                "of this run attributed to human"
            )
        if rej in front.dismissed_rejection_ids(store, cmd.run_id):
            raise NotAuthorized(f"rejection {rej!r} is already dismissed: one reply per refusal")
        return None

    if cmd.name == "record_prompt_item":
        from kernel import front
        for key in ("session_id", "item_id", "sha256"):
            if not isinstance(cmd.payload.get(key), str) or not cmd.payload.get(key):
                raise NotAuthorized(f"record_prompt_item carries {key}")
        sid, item, sha = cmd.payload.get("session_id"), cmd.payload.get("item_id"), cmd.payload.get("sha256")
        if sha not in front.prompt_hashes_of(store, cmd.run_id, sid):
            raise NotAuthorized(
                f"record_prompt_item: no satisfied sess-prompt of session {sid!r} carries body.artifact {sha[:12]}..."
            )
        # Per session: omnigent's item ids are not shown to be unique across sessions.
        if any(f.payload["item_id"] == item and f.payload["session_id"] == sid
               for f in store.facts_of_kind(cmd.run_id, EventKind.PROMPT_ITEM)):
            raise NotAuthorized(f"item {item!r} of session {sid!r} is already recorded as a prompt_item")
        return None

    if cmd.name == "revise_bundle":
        from kernel import bundle as _bundle
        from kernel import front
        issue = cmd.payload.get("issue")
        if not isinstance(issue, dict):
            raise NotAuthorized("revise_bundle carries the fetched issue as an object")
        try:
            new_hash = _bundle.bundle_hash(_bundle.snapshot(issue))
        except (KeyError, TypeError, ValueError) as exc:
            raise NotAuthorized(f"revise_bundle: malformed issue: {exc}") from exc
        if new_hash == front.bundle_hash(store, cmd.run_id):
            raise NotAuthorized(
                "revise_bundle: no relevant change -- the frozen fields hash as the current bundle"
            )
        return next_state
```

`EventKind` is already imported in `authz.py`.

- [ ] **Step 9: Command names and side facts in `commands.py`**

Add the five names to `COMMAND_NAMES` (and to the pin test). Add `_side_fact` arms:

```python
    elif cmd.name == "record_model_question":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.MODEL_QUESTION, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "phase": phase,
                     "question_id": cmd.payload["question_id"], "question": cmd.payload["question"]},
        )
    elif cmd.name == "record_model_ruling":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.MODEL_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "question_id": p["question_id"], "ruling": p["ruling"],
                     "reasoning": p["reasoning"], "cost_if_wrong": p["cost_if_wrong"]},
        )
    elif cmd.name == "dismiss_human_item":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_ITEM_DISMISSED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "cursor_item_id": cmd.payload["cursor_item_id"],
                     "rejection": cmd.payload["rejection"]},
        )
    elif cmd.name == "record_prompt_item":
        p = cmd.payload
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.PROMPT_ITEM, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session_id": p["session_id"], "item_id": p["item_id"], "sha256": p["sha256"]},
        )
    elif cmd.name == "revise_bundle":
        import json
        from kernel import bundle as _bundle
        from kernel.canon import canonical_bytes
        prior_hash = front.bundle_hash(store, cmd.run_id)
        old = json.loads(store.read_blob(prior_hash))
        new = _bundle.snapshot(cmd.payload["issue"])
        new_hash = put_artifact(store, canonical_bytes(new))
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.BUNDLE_REVISED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"bundle_hash": new_hash, "prior_bundle_hash": prior_hash,
                     "reason": "issue changed", "diff": _bundle.snapshot_diff(old, new)},
        )
```

`canonical_bytes` produces JSON, so `json.loads` of the stored blob is the prior snapshot.

- [ ] **Step 10: The driver**

In `v2/tests/kernel/front.py`, split the prompt out of `_session` and add the round helpers:

```python
    def _prompt(self, generation: int, sid: str, cause: str) -> None:
        phase, epoch = self.phase(), self.epoch()
        prompt_hash = put_artifact(self.store, f"prompt for {sid} caused by {cause}".encode())
        pkey = f"sess-prompt:{sid}:{generation}:{cause}"
        self._journal(generation, pkey, {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-prompt", "session": sid,
                           "phase": phase, "epoch": epoch, "cause": cause},
            "body": {"artifact": prompt_hash},
        }, f"item-{sid}-{generation}")
```

`_session` keeps the create and then calls `self._prompt(generation, sid, cause)` instead of journaling the prompt itself. Then:

```python
    def author_round(self, artefact: bytes, *, ended: str = "file",
                     resume: str | None = None) -> str:
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        if resume is None:
            sid = self._session(g, cause)
        else:
            sid = resume
            self._prompt(g, sid, cause)
        self._end_turn(g, sid, ended)
        h = put_artifact(self.store, artefact)
        name = "submit_spec" if self.phase() == "spec" else "submit_plan"
        self._cmd(g, name, {"artifact_hash": h})
        return h

    def ask_round(self, questions, rulings=None) -> str:
        """An author turn that ends with questions rather than an artefact."""
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        sid = self._session(g, cause)
        self._end_turn(g, sid)
        for qid, text in questions:
            self._cmd(g, "record_model_question", {"question_id": qid, "question": text})
            if rulings and qid in rulings:
                self._cmd(g, "record_model_ruling",
                          {"question_id": qid, "ruling": rulings[qid],
                           "reasoning": "reasoned", "cost_if_wrong": "low"})
        return sid

    def revise(self, issue: dict) -> None:
        g = self._dispatch(Role.OPERATOR, "runner")
        self._cmd(g, "revise_bundle", {"issue": issue})
```

- [ ] **Step 11: Provenance rows, then run the tests**

Add to `docs/design/provenance-table.md`: asserted, "**Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked" — `cmd.payload['question_id']`, `cmd.payload['question']`, `cmd.payload['ruling']`, `cmd.payload['reasoning']`, `cmd.payload['cost_if_wrong']`; observed — `cmd.payload['rejection']` (refused unless a `command_rejected` fact of this run attributed to `human`), `cmd.payload['item_id']` (a per-session identity the kernel cannot see; **Residual, §4** — asserted), `cmd.payload['sha256']` (refused unless a satisfied `sess-prompt` of that session carries it as `body.artifact`), `cmd.payload['issue']` (refused unless its snapshot hashes differently from the current bundle; its content is asserted by the runner adapter, §9 — one row, observed for the comparison, with the residual named). Extend the pinned set and the count word as in Task 7.

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_provenance.py tests/kernel/test_grill_commands.py tests/kernel/test_cursor_facts.py tests/kernel/test_revise_bundle.py tests/kernel/test_front_table.py tests/kernel/test_human_commands.py tests/kernel/test_bundle.py tests/kernel/test_grill.py -q`
Expected: PASS. Full suite: green.

- [ ] **Step 12: Mutation check, then commit**

Make `grill_open` ignore the epoch (use `store.facts_of_kind` without the epoch filter): `test_the_grill_is_scoped_to_the_epoch` fails. Drop the `new_hash == front.bundle_hash` refusal: `test_a_bircher_only_change_is_refused` fails. Drop the `already dismissed` clause: `test_dismiss_names_a_human_rejection_once` fails at its last line. Restore all three.

```bash
git add v2/kernel/authz.py v2/kernel/commands.py v2/kernel/front.py v2/kernel/bundle.py v2/kernel/grill.py v2/tests/kernel/front.py v2/tests/kernel/test_grill_commands.py v2/tests/kernel/test_cursor_facts.py v2/tests/kernel/test_revise_bundle.py v2/tests/kernel/test_commands.py v2/tests/kernel/test_provenance.py docs/design/provenance-table.md docs/design/2026-08-23-v2-kernel-design.md
git commit -m "feat(kernel): grill facts, dismissals, prompt items, revise_bundle as a command"
```

---
### Task 10: The turn's end is a fact — `record_author_empty`, the turn guards, the direction guard

Closes §11: `test_output_refused_before_turn_ended`, `test_output_refused_while_stop_intended`, `test_direction_ordering_by_seq`.

**Files:**
- Modify: `v2/kernel/authz.py` (`record_author_empty` row and check; `_check_turn_ended`; `_require_turn_recorded`; the direction clause of `_check_submit`)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact` arm)
- Modify: `v2/kernel/front.py` (`turn_ended_for`, `stop_satisfied_for`, `dispatch_seq`, `direction_after`)
- Test: `v2/tests/kernel/test_turn_guards.py`

**Interfaces:**
- Consumes: `front.newest_seat`, `front.newest_prompt`, `front.satisfied_effects` (Task 7); `Role.AUTHOR`/`REVIEWER`; the driver's `_dispatch`, `_session`, `_prompt`, `_end_turn`, `_journal` (Tasks 7, 9).
- Produces:
  - `record_author_empty {session}` (author role; `queued`, `specified`; fact `author_empty {session, phase, epoch, generation}`).
  - `front.newest_prompt_of(store, run_id, session_id, phase, epoch_n) -> dict | None` (the session's own newest satisfied prompt, ruling 15), `front.turn_ended_for(store, run_id, prompt_key) -> Fact | None` (the `turn_ended` whose `prompt_key` is that key — the turn the prompt started), `front.stop_satisfied_for(store, run_id, turn_ended_id) -> bool` (a satisfied `sess-stop` whose obligation `cause` is that fact's id), `front.dispatch_seq(store, run_id, generation) -> int` (the `seq` of the generation's `attempt_dispatched` fact), `front.direction_after(store, run_id, seq) -> Fact | None` (the newest `human_direction` with `seq` greater than the given one).
  - `authz.OUTPUT_COMMANDS = frozenset({"submit_spec", "submit_plan", "record_author_empty", "record_model_question", "record_model_ruling"})` — plus `record_review` under a `review_ruling` — every one refused until the round's session has a `turn_ended` newer than its newest satisfied prompt and that turn's stop is satisfied.

- [ ] **Step 1: Write the failing tests**

`v2/tests/kernel/test_turn_guards.py`:

```python
import json

import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def _stop_key(sid, g):
    return f"sess-stop:{sid}:{g}"


def test_output_refused_before_turn_ended(tmp_path):
    """§11: nothing a turn produced is recorded before its end is a fact."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    h = put_artifact(s, SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_model_question", {"question_id": "q1", "question": "?"})
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_author_empty", {"session": sid})
    f._end_turn(g, sid)
    f._cmd(g, "submit_spec", {"artifact_hash": h})
    assert s.run_state("r-1") == "spec_submitted"


def test_output_refused_while_stop_intended(tmp_path):
    """§11: the turn ended, but its stop is only intended -- still refused."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    key = _stop_key(sid, g)
    s.journal_intent(f"e-{key}", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
                      "body": {"event": "stop_session"}})
    h = put_artifact(s, SPEC_BYTES)
    with pytest.raises(NotAuthorized, match="sess-stop"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    s.mark_effect(key, "uncertain", None, run_id="r-1")
    with pytest.raises(NotAuthorized, match="sess-stop"):
        f._cmd(g, "submit_spec", {"artifact_hash": h})
    s.mark_effect(key, "confirmed", "", run_id="r-1")
    f._cmd(g, "submit_spec", {"artifact_hash": h})


def test_a_reconciled_delivered_stop_satisfies(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    key = _stop_key(sid, g)
    s.journal_intent(f"e-{key}", "r-1", g, "session_control", key,
                     {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
                      "body": {"event": "stop_session"}})
    s.mark_effect(key, "reconciled", "delivered", run_id="r-1")
    assert front.stop_satisfied_for(s, "r-1", te.id) is True
    f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})


def test_review_ruling_needs_the_reviewers_turn_ended_and_stopped(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    sid = f._session(g, f._newest_id())
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"),
               "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="turn_ended"):
        f._cmd(g, "record_review", payload)
    f._end_turn(g, sid)
    f._cmd(g, "record_review", payload)
    assert s.run_state("r-1") == "specified"


def test_output_guard_reads_the_sessions_own_prompt(tmp_path):
    """Ruling 15: a newer prompt to ANOTHER session of the phase (a reply to
    the author while a reviewer seat runs) does not refuse the seat's ruling."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    a_gen = f._dispatch(Role.AUTHOR, "claude")
    a_sid = f._session(a_gen, f._newest_id()); f._end_turn(a_gen, a_sid)
    f._cmd(a_gen, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    g = f._dispatch(Role.REVIEWER, "codex")
    r_sid = f._session(g, f._newest_id()); f._end_turn(g, r_sid)
    f._prompt(g, a_sid, f._newest_id())                    # a newer prompt to the author session
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"ok")}
    f._cmd(g, "record_review", payload)
    assert s.run_state("r-1") == "specified"


def test_a_human_ruling_passes_the_turn_guard(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")       # a live author session, turn not ended
    f._session(g, f._newest_id())
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})
    assert s.run_state("r-1") == "queued"


def test_turn_ended_names_the_awaited_session_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    with pytest.raises(NotAuthorized, match="newest satisfied sess-prompt"):
        f._cmd(g, "record_turn_ended", {"session": "s-other", "ended": "file"})
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})
    with pytest.raises(NotAuthorized, match="one end per turn"):
        f._cmd(g, "record_turn_ended", {"session": sid, "ended": "cap"})
    # A second prompt to the same session is a second turn with its own end.
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    f._journal(g, _stop_key(sid, g), {"argv": ["curl"], "obligation": {"kind": "sess-stop", "session": sid,
                                                                         "cause": te.id},
                                       "body": {"event": "stop_session"}}, "")
    g2 = f._dispatch(Role.AUTHOR, "claude")
    f._prompt(g2, sid, f._newest_id())
    f._cmd(g2, "record_turn_ended", {"session": sid, "ended": "file"})
    assert len(s.facts_of_kind("r-1", EventKind.TURN_ENDED)) == 2


def test_author_empty_names_the_current_author_session_and_retries_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g1 = f._dispatch(Role.AUTHOR, "claude")
    s1 = f._session(g1, f._newest_id())
    f._end_turn(g1, s1, "file")
    with pytest.raises(NotAuthorized, match="newest"):
        f._cmd(g1, "record_author_empty", {"session": "s-not-it"})
    f._cmd(g1, "record_author_empty", {"session": s1})
    empty = s.newest_fact("r-1", EventKind.AUTHOR_EMPTY)
    assert empty.payload == {"session": s1, "phase": "spec", "epoch": 0, "generation": g1}
    # The retry: a fresh session whose cause is that fact.
    g2 = f._dispatch(Role.AUTHOR, "claude")
    s2 = f._session(g2, empty.id)
    f._end_turn(g2, s2, "file")
    with pytest.raises(NotAuthorized, match="retry"):
        f._cmd(g2, "record_author_empty", {"session": s2})
    # Naming the older session is refused: it is not the newest author session.
    with pytest.raises(NotAuthorized, match="newest"):
        f._cmd(g2, "record_author_empty", {"session": s1})


def test_author_empty_needs_the_author_role_and_a_front_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="not legal from state 'spec_submitted'"):
        f._cmd(g, "record_author_empty", {"session": "s-1"})


def test_direction_ordering_by_seq(tmp_path):
    """§11: a human_direction newer than the generation's attempt_dispatched
    (by journal seq) refuses the submit; one older does not."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.direct("use sqlite")                          # older than the dispatch below
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._end_turn(g, sid)
    h = put_artifact(s, SPEC_BYTES)
    f._cmd(g, "submit_spec", {"artifact_hash": h})
    assert s.run_state("r-1") == "spec_submitted"
    f.review_round("request_revision")
    g2 = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g2, f._newest_id())
    f._end_turn(g2, sid2)
    f.direct("no, postgres")                        # newer than g2's dispatch
    h2 = put_artifact(s, SPEC_BYTES + b"\nv2\n")
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g2, "submit_spec", {"artifact_hash": h2})
    assert s.run_state("r-1") == "queued"
    # The direction's own round submits fine.
    g3 = f._dispatch(Role.AUTHOR, "claude")
    sid3 = f._session(g3, f._newest_id())
    f._end_turn(g3, sid3)
    f._cmd(g3, "submit_spec", {"artifact_hash": h2})
    assert s.run_state("r-1") == "spec_submitted"


def test_direction_ordering_is_by_seq_not_by_time(tmp_path):
    """Every fact carries the same observed_at_us under a zero-step clock, so
    only the journal's seq can order the direction against the dispatch.
    (The facts table refuses UPDATE, so the tie is made by the clock.)"""
    from kernel.ids import Clock
    s = Store.open(tmp_path / "k.db", clock=Clock(start_us=1_000, step_us=0))
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._end_turn(g, sid)
    d_seq = front.dispatch_seq(s, "r-1", g)
    f.direct("later")
    direction = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    dispatched = [x for x in s.facts_of_kind("r-1", EventKind.ATTEMPT_DISPATCHED) if x.seq == d_seq][0]
    assert direction.observed_at_us == dispatched.observed_at_us
    assert front.direction_after(s, "r-1", d_seq).seq == direction.seq
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_turn_guards.py -q`
Expected: FAIL — `test_output_refused_before_turn_ended` gets no exception (the submit is accepted), `record_author_empty` is an unknown command.

- [ ] **Step 3: Queries in `front.py`**

```python
def turn_ended_for(store, run_id: str, prompt_key: str):
    """The turn_ended that ended the turn *prompt_key* started. One per
    prompt: record_turn_ended derives the key from the newest satisfied
    sess-prompt at the moment it is accepted (Task 7)."""
    for f in store.facts_of_kind(run_id, EventKind.TURN_ENDED):
        if f.payload.get("prompt_key") == prompt_key:
            return f
    return None


def stop_satisfied_for(store, run_id: str, turn_ended_id: str) -> bool:
    return any(row["intent"]["obligation"].get("cause") == turn_ended_id
               for row in satisfied_effects(store, run_id, "sess-stop"))


def newest_prompt_of(store, run_id: str, session_id: str, phase: str, epoch_n: int) -> dict | None:
    """The SESSION's newest satisfied sess-prompt in the phase and epoch (the
    output guard's quantifier, spec §2: 'its newest satisfied sess-prompt').
    newest_prompt() is the phase's, for record_turn_ended (ruling 15)."""
    found = None
    for row in satisfied_effects(store, run_id, "sess-prompt"):
        ob = row["intent"]["obligation"]
        if ob.get("session") == session_id and ob.get("phase") == phase and ob.get("epoch") == epoch_n:
            found = {"generation": row["generation"], "key": row["idempotency_key"],
                     "cause": ob.get("cause"), "session_id": session_id, "at_us": row["at_us"]}
    return found


def dispatch_seq(store, run_id: str, generation: int) -> int:
    for f in store.facts_of_kind(run_id, EventKind.ATTEMPT_DISPATCHED):
        if f.payload.get("generation") == generation:
            return f.seq
    raise LookupError(f"generation {generation} has no attempt_dispatched fact")


def direction_after(store, run_id: str, seq: int):
    facts = [f for f in store.facts_of_kind(run_id, EventKind.HUMAN_DIRECTION) if f.seq > seq]
    return facts[-1] if facts else None
```

"Newer than its newest satisfied prompt" is therefore an equality on the prompt's key, which the kernel wrote into the `turn_ended` fact when it accepted it: no clock is compared. (The turn's *deadline* is the prompt row's `at_us` plus the cap — that is the coordinator's clock, Task 14, and it never feeds a kernel refusal.)

- [ ] **Step 4: The guards in `authz.py`**

Add the row and constant:

```python
    "record_author_empty": (frozenset({"queued", "specified"}), None),

OUTPUT_COMMANDS = frozenset({
    "submit_spec", "submit_plan", "record_author_empty",
    "record_model_question", "record_model_ruling",
})
```

Add the shared guard and call it from `authorize()` — after the state check and before any command-specific branch — for `OUTPUT_COMMANDS`, and for `record_review` when `ruling == "review_ruling"`:

```python
def _require_turn_recorded(store, cmd, role: str) -> None:
    """spec §2 Refusals, the output-recording commands: the round's session
    carries a turn_ended newer than its newest satisfied sess-prompt, and
    the sess-stop that turn_ended is the cause of is satisfied."""
    from kernel import front
    state = store.run_state(cmd.run_id)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    seat = front.newest_seat(store, cmd.run_id, role, phase, epoch_n)
    if seat is None:
        raise NotAuthorized(
            f"{cmd.name}: no satisfied sess-create under a {role} generation of "
            f"{phase} in epoch {epoch_n}: there is no round's session to have ended"
        )
    sid = seat["session"]["id"]
    prompt = front.newest_prompt_of(store, cmd.run_id, sid, phase, epoch_n)
    if prompt is None:
        raise NotAuthorized(
            f"{cmd.name}: the round's session {sid} has no satisfied sess-prompt in "
            f"{phase}/{epoch_n}: no turn was awaited"
        )
    ended = front.turn_ended_for(store, cmd.run_id, prompt["key"])
    if ended is None:
        raise NotAuthorized(
            f"{cmd.name}: session {sid} carries no turn_ended newer than its newest "
            "satisfied sess-prompt: nothing a turn produced is recorded before the "
            "turn is observed ended"
        )
    if not front.stop_satisfied_for(store, cmd.run_id, ended.id):
        raise NotAuthorized(
            f"{cmd.name}: the sess-stop caused by turn_ended {ended.id} is not "
            "satisfied: the session is stopped before its output is read"
        )
```

In `authorize()`:

```python
    if cmd.name in OUTPUT_COMMANDS:
        _require_turn_recorded(store, cmd, Role.AUTHOR)
    if cmd.name == "record_review" and ruling == "review_ruling" and current in FRONT_HALF_STATES:
        _require_turn_recorded(store, cmd, Role.REVIEWER)
```

(The back half's `record_review` runs the PR review through `omnigent run`, not a session — the guard is front-half only.) Then `record_turn_ended`'s check becomes:

```python
def _check_turn_ended(store, cmd, state: str) -> None:
    from kernel import front
    if cmd.payload.get("ended") not in TURN_ENDS:
        raise NotAuthorized(f"ended {cmd.payload.get('ended')!r} is not one of {sorted(TURN_ENDS)}")
    sid = cmd.payload.get("session")
    if not isinstance(sid, str) or not sid:
        raise NotAuthorized("record_turn_ended names the session whose turn ended")
    prompt = front.newest_prompt(store, cmd.run_id, phase_of(state), front.epoch(store, cmd.run_id))
    if prompt is None or prompt["session_id"] != sid:
        raise NotAuthorized(
            f"record_turn_ended names {sid}, which is not the session the newest satisfied "
            "sess-prompt of this phase and epoch names: one turn is awaited at a time"
        )
    if front.turn_ended_for(store, cmd.run_id, prompt["key"]) is not None:
        raise NotAuthorized(f"session {sid}'s awaited turn already ended: one end per turn")
```

replacing the inline check Task 7 put in `authorize()`. Add `record_author_empty`:

```python
    if cmd.name == "record_author_empty":
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
            raise NotAuthorized("record_author_empty must come from an attempt dispatched in the author role")
        sid = cmd.payload.get("session")
        seat = front.newest_seat(store, cmd.run_id, Role.AUTHOR, phase_of(current), front.epoch(store, cmd.run_id))
        if seat is None or seat["session"]["id"] != sid:
            raise NotAuthorized(
                f"record_author_empty names {sid!r}, which is not the session the newest "
                "satisfied author sess-create of this phase and epoch delivered"
            )
        cause = seat["cause"]
        if any(f.id == cause for f in store.facts_of_kind(cmd.run_id, EventKind.AUTHOR_EMPTY)):
            raise NotAuthorized(
                f"session {sid} is itself the retry (its create's cause is an author_empty): "
                "a second empty turn is RC_FAILED, not a third session"
            )
        return None
```

And the direction clause at the end of `_check_submit`:

```python
    newer = front.direction_after(store, cmd.run_id, front.dispatch_seq(store, cmd.run_id, cmd.generation))
    if newer is not None:
        raise NotAuthorized(
            f"a human_direction (seq {newer.seq}) is newer than generation "
            f"{cmd.generation}'s dispatch: the artefact of the turn it interrupted is not "
            "submitted; the direction starts a fresh round"
        )
```

- [ ] **Step 5: Command name and side fact**

Add `"record_author_empty"` to `COMMAND_NAMES` (and the pin test) and the arm:

```python
    elif cmd.name == "record_author_empty":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.AUTHOR_EMPTY, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"session": cmd.payload["session"], "phase": phase,
                     "epoch": epoch_n, "generation": cmd.generation},
        )
```

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_turn_guards.py tests/kernel -q`
Expected: PASS. The driver already performs the full ceremony (Task 7), so every earlier test still passes; a test in Tasks 7-9 that built a session by hand without `_end_turn` now fails for the right reason — fix it by adding the missing `_end_turn`, never by weakening the guard. Full suite: green.

- [ ] **Step 7: Mutation check, then commit**

Drop the `stop_satisfied_for` clause: `test_output_refused_while_stop_intended` fails. Replace `f.seq > seq` in `direction_after` with `f.observed_at_us > <dispatch fact's observed_at_us>`: `test_direction_ordering_is_by_seq_not_by_time` fails. Drop the `cause` clause of `record_author_empty`: `test_author_empty_names_the_current_author_session_and_retries_once` fails at `match="retry"`. Restore all three.

```bash
git add v2/kernel/authz.py v2/kernel/commands.py v2/kernel/front.py v2/tests/kernel/test_turn_guards.py v2/tests/kernel/test_commands.py
git commit -m "feat(kernel): output-recording commands wait for the turn's end and stop; author_empty; direction by seq"
```

---

### Task 11: The review brief — `brief.py` and `issue_review_brief`

**Files:**
- Create: `v2/kernel/brief.py`
- Modify: `v2/kernel/authz.py` (`issue_review_brief` row and check; the brief clause of `validate_review`)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact` arm)
- Modify: `v2/kernel/front.py` (`brief_for`)
- Modify: `v2/tests/kernel/front.py` (`review_round` issues the brief)
- Test: `v2/tests/kernel/test_brief.py`

**Interfaces:**
- Consumes: `Store.read_blob`, `Store.phase_artifact`, `put_artifact`; `front.bundle_hash`, `front.epoch`, `policy.policy_of`, `policy.policy_version`, `Store.run_base_sha`.
- Produces:
  - `brief.TEMPLATE_VERSION = 1`, `brief.REVIEW_OUT = "bircher/review.md"` (the path, relative to the worktree root, the reviewer writes), `brief.render(*, phase: str, artefact: bytes, bundle: bytes, spec: bytes | None, policy: Policy, base_sha: str, template: int) -> bytes` (pure; `spec` required when `phase == "plan"` and refused when `phase == "spec"`), `brief.hash8(artifact_hash: str) -> str`.
  - Command `issue_review_brief {phase}` (reviewer role; `spec_submitted`, `plan_submitted`; refused when the generation already carries a `review_brief_issued`; fact `review_brief_issued {phase, epoch, brief_hash, brief_template, artifact_hash, context_bundle_hash, policy_version, base_sha, spec_hash}` with `spec_hash` null for a spec brief; the rendered bytes are PUT under `brief_hash`).
  - `front.brief_for(store, run_id, generation) -> Fact | None` (the `review_brief_issued` recorded under that generation).
  - A front-half `review_ruling` `record_review` is refused unless its generation carries a `review_brief_issued` whose `phase`, `artifact_hash`, `context_bundle_hash`, `policy_version` and `base_sha` equal the ruling's binding.

- [ ] **Step 1: Write the failing tests**

`v2/tests/kernel/test_brief.py`:

```python
import pytest

from kernel import brief, front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.policy import Policy
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_render_is_pure_and_names_its_inputs():
    a = brief.render(phase="spec", artefact=b"# S", bundle=b'{"body":"B"}', spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    b = brief.render(phase="spec", artefact=b"# S", bundle=b'{"body":"B"}', spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    assert a == b and isinstance(a, bytes)
    text = a.decode()
    assert brief.REVIEW_OUT in text
    assert content_hash(b"# S")[:8] in text          # the hash8 the verdict must name
    assert "VERDICT: PASS|FAIL" in text
    assert "0" * 40 in text
    assert "# S" in text and '"body":"B"' in text
    assert "grill: model" in text and "gates: spec" in text
    other = brief.render(phase="spec", artefact=b"# S2", bundle=b'{"body":"B"}', spec=None,
                         policy=Policy(), base_sha="0" * 40, template=1)
    assert other != a


def test_render_requires_the_spec_for_a_plan_and_refuses_it_for_a_spec():
    with pytest.raises(ValueError, match="spec"):
        brief.render(phase="plan", artefact=b"# P", bundle=b"{}", spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    with pytest.raises(ValueError, match="spec"):
        brief.render(phase="spec", artefact=b"# S", bundle=b"{}", spec=b"# S",
                     policy=Policy(), base_sha="0" * 40, template=1)
    with pytest.raises(ValueError, match="template"):
        brief.render(phase="spec", artefact=b"# S", bundle=b"{}", spec=None,
                     policy=Policy(), base_sha="0" * 40, template=2)
    out = brief.render(phase="plan", artefact=b"# P", bundle=b"{}", spec=b"# THE SPEC",
                       policy=Policy(), base_sha="0" * 40, template=1)
    assert b"# THE SPEC" in out


def test_issue_review_brief_renders_from_the_journal_and_puts_the_bytes(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    fact = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    p = fact.payload
    assert p["phase"] == "spec" and p["epoch"] == 0 and p["artifact_hash"] == h
    assert p["context_bundle_hash"] == front.bundle_hash(s, "r-1")
    assert p["policy_version"] == front.policy_version(s, "r-1")
    assert p["base_sha"] == "0" * 40 and p["spec_hash"] is None
    assert p["brief_template"] == brief.TEMPLATE_VERSION
    stored = s.read_blob(p["brief_hash"])
    assert content_hash(stored) == p["brief_hash"]
    expected = brief.render(phase="spec", artefact=SPEC_BYTES,
                            bundle=s.read_blob(front.bundle_hash(s, "r-1")), spec=None,
                            policy=Policy(gates=frozenset()), base_sha="0" * 40,
                            template=brief.TEMPLATE_VERSION)
    assert stored == expected
    assert front.brief_for(s, "r-1", g).id == fact.id
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "issue_review_brief", {"phase": "spec"})


def test_a_plan_brief_carries_the_current_spec(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.to_specified()
    spec_hash = s.phase_artifact("r-1", "spec")
    f.author_round(PLAN_BYTES)
    g = f._dispatch(Role.REVIEWER, "claude")
    f._cmd(g, "issue_review_brief", {"phase": "plan"})
    p = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED).payload
    assert p["spec_hash"] == spec_hash
    assert SPEC_BYTES in s.read_blob(p["brief_hash"])


def test_issue_review_brief_needs_the_reviewer_role_and_the_states_phase(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="reviewer"):
        f._cmd(g, "issue_review_brief", {"phase": "spec"})
    g = f._dispatch(Role.REVIEWER, "codex")
    with pytest.raises(NotAuthorized, match="phase"):
        f._cmd(g, "issue_review_brief", {"phase": "plan"})


def test_review_ruling_binds_to_its_generations_brief(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    # The driver issues the brief; a seat without one is refused.
    g = f._dispatch(Role.REVIEWER, "codex")
    sid = f._session(g, f._newest_id()); f._end_turn(g, sid)
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="review_brief_issued"):
        f._cmd(g, "record_review", payload)
    f.review_round("accept")
    assert s.run_state("r-1") == "specified"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    b = front.brief_for(s, "r-1", v.payload["generation"])
    assert b is not None and b.payload["artifact_hash"] == v.payload["artifact_hash"]


def test_a_brief_from_a_previous_epoch_does_not_bind(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._session(g, f._newest_id())
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    f.author_round(SPEC_BYTES + b"\nB2\n")
    # The old generation is superseded; a fresh seat renders a brief over the new bundle.
    f.review_round("accept")
    p = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED).payload
    assert p["epoch"] == 1 and p["context_bundle_hash"] == front.bundle_hash(s, "r-1")
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_brief.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.brief'`.

- [ ] **Step 3: `brief.py`**

```python
"""The review brief: a kernel artefact rendered from objects the kernel holds
(spec §2 *Brief*). Pure: the same inputs and template give the same bytes,
which is what the §8 proof re-renders and compares."""
from __future__ import annotations

from kernel.canon import content_hash
from kernel.policy import Policy, to_payload

TEMPLATE_VERSION = 1
#: Relative to the reviewer's worktree root. A convention stated in the
#: brief, not an environment variable (spec §3 Author round).
REVIEW_OUT = "bircher/review.md"

_TEMPLATES = {1: """# Bircher review brief (template v{template})

You are an INDEPENDENT, READ-ONLY reviewer of a {phase} for the issue below.
Do not edit files outside `{review_out}`, do not run git write commands, do
not push, do not open or comment on anything. You have no credentials.

## What to do

1. Read the issue snapshot, {what_to_read}.
2. Write your findings to `{review_out}` in your worktree: numbered,
   most severe first, each naming the section it concerns and what is wrong.
3. As the file's LAST non-blank line write exactly one of:

       VERDICT: PASS {hash8}
       VERDICT: FAIL {hash8}

   `{hash8}` is the first eight hex digits of the artefact's sha256 and
   names what you reviewed; a verdict without it, or with another hash,
   counts as no verdict. Findings come BEFORE the verdict line, never after.
4. End your turn.

PASS means the artefact is ready for the next phase as it stands. FAIL means
the author must revise; your findings are its brief.

## Policy

    grill: {grill}
    gates: {gates}
    max_rounds: {max_rounds}
    max_seats: {max_seats}
    base_sha: {base_sha}

## Issue snapshot (bundle {bundle_hash})

```json
{bundle}
```
{spec_section}
## The {phase} under review (sha256 {artifact_hash})

{artefact}
"""}

_SPEC_SECTION = """
## The accepted spec this plan implements (sha256 {spec_hash})

{spec}
"""


def hash8(artifact_hash: str) -> str:
    return artifact_hash[:8]


def render(*, phase: str, artefact: bytes, bundle: bytes, spec: bytes | None,
           policy: Policy, base_sha: str, template: int) -> bytes:
    if template not in _TEMPLATES:
        raise ValueError(f"unknown brief template version {template}")
    if phase not in ("spec", "plan"):
        raise ValueError(f"brief phase must be spec or plan, got {phase!r}")
    if phase == "plan" and spec is None:
        raise ValueError("a plan brief carries the run's current spec")
    if phase == "spec" and spec is not None:
        raise ValueError("a spec brief carries no spec")
    p = to_payload(policy)
    artifact_hash = content_hash(artefact)
    spec_section = ""
    what = "then the spec"
    if phase == "plan":
        spec_section = _SPEC_SECTION.format(spec_hash=content_hash(spec),
                                            spec=spec.decode("utf-8", "replace"))
        what = "then the accepted spec, then the plan"
    text = _TEMPLATES[template].format(
        template=template, phase=phase, review_out=REVIEW_OUT, what_to_read=what,
        hash8=hash8(artifact_hash), grill=p["grill"], gates=", ".join(p["gates"]) or "(none)",
        max_rounds=p["max_rounds"], max_seats=p["max_seats"], base_sha=base_sha,
        bundle_hash=content_hash(bundle), bundle=bundle.decode("utf-8", "replace"),
        spec_section=spec_section, artifact_hash=artifact_hash,
        artefact=artefact.decode("utf-8", "replace"),
    )
    return text.encode("utf-8")
```

- [ ] **Step 4: The command**

`front.py`:

```python
def brief_for(store, run_id: str, generation: int):
    for f in store.facts_of_kind(run_id, EventKind.REVIEW_BRIEF_ISSUED):
        if f.payload.get("generation") == generation:
            return f
    return None
```

`authz.py` — the row and the check:

```python
    "issue_review_brief": (frozenset({"spec_submitted", "plan_submitted"}), None),

    if cmd.name == "issue_review_brief":
        from kernel import front
        if role_for(store, cmd.run_id, cmd.generation) != Role.REVIEWER:
            raise NotAuthorized("issue_review_brief must come from an attempt dispatched in the reviewer role")
        if cmd.payload.get("phase") != phase_of(current):
            raise NotAuthorized(
                f"issue_review_brief names phase {cmd.payload.get('phase')!r}, but the run is at "
                f"{current!r} ({phase_of(current)})"
            )
        if front.brief_for(store, cmd.run_id, cmd.generation) is not None:
            raise NotAuthorized(f"generation {cmd.generation} already carries a review_brief_issued")
        return None
```

and in `validate_review`, in the front-half branch after the `policy_version` check:

```python
    issued = front.brief_for(store, cmd.run_id, cmd.generation)
    if issued is None:
        raise NotAuthorized(
            f"generation {cmd.generation} carries no review_brief_issued: a front-half "
            "review_ruling binds the brief the kernel rendered for its seat"
        )
    bp = issued.payload
    if (bp["phase"], bp["artifact_hash"], bp["context_bundle_hash"], bp["policy_version"], bp["base_sha"]) != (
            phase, binding.artifact_hash, binding.context_bundle_hash, binding.policy_version, binding.base_sha):
        raise NotAuthorized(
            "review_ruling differs from its generation's review_brief_issued in phase, "
            "artifact_hash, context_bundle_hash, policy_version or base_sha"
        )
```

`commands.py` — the name and the arm (the fact records `generation` so `brief_for` can find it):

```python
    elif cmd.name == "issue_review_brief":
        from kernel import brief as _brief
        from kernel.policy import policy_of, policy_version
        artifact_hash = store.phase_artifact(cmd.run_id, phase)
        bundle_h = front.bundle_hash(store, cmd.run_id)
        spec_hash = store.phase_artifact(cmd.run_id, "spec") if phase == "plan" else None
        rendered = _brief.render(
            phase=phase, artefact=store.read_blob(artifact_hash), bundle=store.read_blob(bundle_h),
            spec=None if spec_hash is None else store.read_blob(spec_hash),
            policy=policy_of(store, cmd.run_id), base_sha=store.run_base_sha(cmd.run_id),
            template=_brief.TEMPLATE_VERSION,
        )
        brief_hash = put_artifact(store, rendered)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.REVIEW_BRIEF_ISSUED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "generation": cmd.generation,
                     "brief_hash": brief_hash, "brief_template": _brief.TEMPLATE_VERSION,
                     "artifact_hash": artifact_hash, "context_bundle_hash": bundle_h,
                     "policy_version": policy_version(store, cmd.run_id),
                     "base_sha": store.run_base_sha(cmd.run_id), "spec_hash": spec_hash},
        )
```

The `REVIEW_VERDICT` payload from Task 7 gains `"generation": cmd.generation` so the proof can pair a ruling with its brief.

- [ ] **Step 5: The driver issues the brief**

In `Front.review_round`, replace the comment line with:

```python
        self._cmd(g, "issue_review_brief", {"phase": self.phase()})
```

between `_dispatch` and `_session`, and take the binding fields from the brief fact rather than recomputing them:

```python
        issued = fq.brief_for(self.store, self.run_id, g).payload
        payload = {
            "phase": phase, "verdict": verdict,
            "artifact_hash": issued["artifact_hash"], "base_sha": issued["base_sha"],
            "context_bundle_hash": issued["context_bundle_hash"],
            "policy_version": issued["policy_version"],
            "findings_hash": put_artifact(self.store, findings),
        }
```

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_brief.py tests/kernel -q`
Expected: PASS; add `"issue_review_brief"` to the pin test. Task 10's `test_review_ruling_needs_the_reviewers_turn_ended_and_stopped` and `test_output_guard_reads_the_sessions_own_prompt` build their seats by hand and now fail for want of a brief: in each, add `f._cmd(g, "issue_review_brief", {"phase": "spec"})` right after its `_dispatch` and take `context_bundle_hash`/`policy_version`/`base_sha` from `front.brief_for(s, "r-1", g).payload` — the refusal it tests is still the turn guard's, which runs first. Full suite: green.

- [ ] **Step 7: Mutation check, then commit**

Make `render` ignore `bundle` (drop it from the format): `test_issue_review_brief_renders_from_the_journal_and_puts_the_bytes` fails on `stored == expected`? No — both sides call `render`. The binding mutation: drop `bp["context_bundle_hash"]` from the tuple compared in `validate_review`, then in `test_a_brief_from_a_previous_epoch_does_not_bind` insert a `record_review` under the old generation `g` after the revision with the new bundle hash: it must be refused, and with the mutation it is accepted. Add that assertion to the test as its last lines:

```python
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": "0" * 40, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"x")}
    with pytest.raises(Exception):          # OwnershipLost: g is superseded
        f._cmd(g, "record_review", payload)
```

That refusal is the fence's, which shadows the brief clause (spec §2: "no epoch clause — the fence shadows it"); the binding mutation is therefore bound by `test_review_ruling_binds_to_its_generations_brief` instead: drop the brief clause entirely and it fails at `match="review_brief_issued"`. Restore.

```bash
git add v2/kernel/brief.py v2/kernel/authz.py v2/kernel/commands.py v2/kernel/front.py v2/tests/kernel/front.py v2/tests/kernel/test_brief.py v2/tests/kernel/test_commands.py
git commit -m "feat(kernel): review brief rendered by the kernel; review_ruling binds its seat's brief"
```

---

### Task 12: Rounds, seats and identity — `max_rounds`, the vendor guards

**Files:**
- Modify: `v2/kernel/authz.py` (the `max_rounds` clause of `_review_destination`; the identity clauses of `_check_submit` and `validate_review`)
- Modify: `v2/kernel/front.py` (`rounds_used`, `round_grants`)
- Test: `v2/tests/kernel/test_rounds_and_identity.py`

**Interfaces:**
- Consumes: `front.newest_seat` (Task 7), `front.grants` (Task 6), `policy_of`.
- Produces: `front.rounds_used(store, run_id, phase, epoch_n) -> int` (count of `review_verdict` facts with `ruling == "review_ruling"`, `verdict == "request_revision"`, that phase and epoch), `front.round_grants(store, run_id, phase, epoch_n) -> int` (count of `human_ruling {ruling: grant_round}` facts with that phase and epoch), `front.round_bound(store, run_id, phase, epoch_n) -> int` = `max_rounds + round_grants`.
  - `submit_spec`/`submit_plan` refused when the calling actor is not `front.vendor_of(agent_name)` of the newest satisfied author `sess-create` of the phase and epoch; a `review_ruling` refused when the reviewer actor is not `vendor_of` the newest satisfied reviewer `sess-create`'s `agent_name` (ruling 14); the `max_rounds+1`th `request_revision` `review_ruling` in a phase and epoch refused (a `human_ruling` is not counted and not bounded).

- [ ] **Step 1: Write the failing tests**

`v2/tests/kernel/test_rounds_and_identity.py`:

```python
import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_the_max_rounds_plus_oneth_revision_is_refused_until_granted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", project_config={"max_rounds": 2})
    for i in range(2):
        f.author_round(SPEC_BYTES + bytes([48 + i]))
        f.review_round("request_revision")
    assert front.rounds_used(s, "r-1", "spec", 0) == 2
    f.author_round(SPEC_BYTES + b"3")
    with pytest.raises(NotAuthorized, match="max_rounds"):
        f.review_round("request_revision")
    assert s.run_state("r-1") == "spec_submitted"
    # accept is not bounded
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": "request_revision", "reviewer": "codex"})
    f.grant()
    assert front.round_bound(s, "r-1", "spec", 0) == 3
    f.review_round("request_revision")
    assert s.run_state("r-1") == "queued"
    assert front.rounds_used(s, "r-1", "spec", 0) == 3


def test_rounds_are_per_phase_and_epoch_and_human_revisions_do_not_count(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", project_config={"max_rounds": 1})
    f.author_round(SPEC_BYTES); f.review_round("request_revision")
    h = f.author_round(SPEC_BYTES + b"2")
    f.human("record_review", {"phase": "spec", "artifact_hash": h, "verdict": "request_revision",
                              "findings": "human says no"})
    assert front.rounds_used(s, "r-1", "spec", 0) == 1
    f.author_round(SPEC_BYTES + b"3")
    with pytest.raises(NotAuthorized, match="max_rounds"):
        f.review_round("request_revision")
    f.review_round("accept")
    # The plan phase has its own allowance.
    f.author_round(b"# Plan\n\n### Task 1: x\n")
    f.review_round("request_revision")
    assert front.rounds_used(s, "r-1", "plan", 0) == 1
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    assert front.rounds_used(s, "r-1", "spec", 1) == 0


def test_submit_actor_must_be_the_sessions_vendor(tmp_path):
    """The snapshot names the BUNDLE (v2_author_claude); the dispatch the
    vendor (codex). vendor_of maps one to the other (ruling 14)."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "codex")
    sid = f._session(g, f._newest_id(), actor="claude")     # snapshot agent_name == "v2_author_claude"
    f._end_turn(g, sid)
    assert __import__("json").loads(s.effect_by_key(f"sess-create:r-1:{g}", run_id="r-1")["external_object_id"])["agent_name"] == "v2_author_claude"
    with pytest.raises(NotAuthorized, match="agent_name"):
        f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    rejected = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rejected.actor == "codex"
    # The spec's own §8 case: snapshot v2_author_claude, a claude generation -> accepted.
    g2 = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g2, f._newest_id())
    f._end_turn(g2, sid2)
    f._cmd(g2, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    assert s.run_state("r-1") == "spec_submitted"
    assert front.vendor_of("v2_author_claude") == "claude" and front.vendor_of("codex") is None
    assert front.vendor_of("v2_implementer") is None and front.vendor_of("v2_author_") is None


def test_reviewer_actor_must_be_the_seats_vendor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._session(g, f._newest_id(), actor="gemini")
    f._end_turn(g, sid)
    issued = front.brief_for(s, "r-1", g).payload
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": issued["artifact_hash"],
               "base_sha": issued["base_sha"], "context_bundle_hash": issued["context_bundle_hash"],
               "policy_version": issued["policy_version"], "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="agent_name"):
        f._cmd(g, "record_review", payload)


def test_identity_laundering_is_refused(tmp_path):
    """A Claude session's artefact submitted under a Codex generation."""
    s = _store(tmp_path)
    f = Front(s, "r-1", author="claude", reviewer="codex")
    g1 = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g1, f._newest_id())
    # The pass dies; a resumed pass dispatches as codex and adopts the session.
    g2 = f._dispatch(Role.AUTHOR, "codex")
    f._prompt(g2, sid, f._newest_id())
    f._end_turn(g2, sid)
    with pytest.raises(NotAuthorized, match="agent_name"):
        f._cmd(g2, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_rounds_and_identity.py -q`
Expected: FAIL — `module 'kernel.front' has no attribute 'rounds_used'`; the identity tests get no exception.

- [ ] **Step 3: Queries**

```python
def rounds_used(store, run_id: str, phase: str, epoch_n: int) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT)
        if f.payload.get("ruling") == "review_ruling"
        and f.payload.get("verdict") == "request_revision"
        and f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n
    )


def round_grants(store, run_id: str, phase: str, epoch_n: int) -> int:
    return sum(
        1 for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING)
        if f.payload.get("ruling") == "grant_round"
        and f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n
    )


def round_bound(store, run_id: str, phase: str, epoch_n: int) -> int:
    return policy_of(store, run_id).max_rounds + round_grants(store, run_id, phase, epoch_n)
```

- [ ] **Step 4: The guards**

In `_review_destination`, in the front-half `review_ruling` path, before returning the `request_revision` destination:

```python
    if verdict == "request_revision":
        from kernel import front
        n = front.epoch(store, run_id)
        used, bound = front.rounds_used(store, run_id, phase, n), front.round_bound(store, run_id, phase, n)
        if used >= bound:
            raise NotAuthorized(
                f"max_rounds: {used} request_revision rulings already in {phase}/epoch {n} "
                f"against a bound of {bound}; the loop parks bound_exhausted and a human retry grants one"
            )
        return "queued" if phase == "spec" else "specified"
```

In `_check_submit`, after the role check:

```python
    seat = front.newest_seat(store, cmd.run_id, Role.AUTHOR, phase, epoch_n)
    actor = actor_for(store, cmd.run_id, cmd.generation)
    named = None if seat is None else seat["session"].get("agent_name")
    if seat is None or front.vendor_of(named) != actor:
        raise NotAuthorized(
            f"{cmd.name}: the calling actor {actor!r} is not the vendor of the bundle "
            f"(agent_name {named!r}) the newest satisfied author sess-create of "
            f"{phase}/epoch {epoch_n} names: the artefact was read from that session, "
            "and its author is the vendor that ran it (ruling 14)"
        )
```

(`from kernel.dispatch import actor_for` joins the existing import.) In `validate_review`'s front-half branch, after the independence check:

```python
    seat = front.newest_seat(store, cmd.run_id, Role.REVIEWER, phase, epoch_n)
    named = None if seat is None else seat["session"].get("agent_name")
    if seat is None or front.vendor_of(named) != actor:
        raise NotAuthorized(
            f"reviewer {actor!r} is not the vendor of the bundle (agent_name {named!r}) the "
            f"newest satisfied reviewer sess-create of {phase}/epoch {epoch_n} names"
        )
```

- [ ] **Step 5: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_rounds_and_identity.py tests/kernel -q`
Expected: PASS. Full suite: green.

- [ ] **Step 6: Mutation check, then commit**

Count human rulings in `rounds_used` (drop the `ruling` clause): `test_rounds_are_per_phase_and_epoch_and_human_revisions_do_not_count` fails. Compare `agent_name` to `actor` directly (drop `vendor_of`): `test_submit_actor_must_be_the_sessions_vendor` fails on the §8 case. Compare `agent_id` instead: every identity test fails. Restore.

```bash
git add v2/kernel/authz.py v2/kernel/front.py v2/tests/kernel/test_rounds_and_identity.py
git commit -m "feat(kernel): max_rounds per phase and epoch; submit and review actors bound to their sessions' vendor"
```

---
## Part C — Coordinator

### Task 13: Verdict consumers read the implementation phase only

**Files:**
- Modify: `v2/coordinator/observe.py` (`revisions_used`, `revision_confirmed`)
- Modify: `v2/coordinator/recover.py` (`decide`'s verdict scan, `_rejected_review`)
- Modify: `v2/kernel/projection.py` (`project`)
- Test: `v2/tests/coordinator/test_verdict_phase_filter.py`

**Interfaces:**
- Consumes: the `review_verdict` payload's `phase` (Task 7).
- Produces: every back-half consumer of `review_verdict` ignores facts whose `phase` is `spec` or `plan`; `projection.RunState.verdicts` holds only implementation verdicts, and gains `front_verdicts: list` holding the rest.

- [ ] **Step 1: Write the failing tests**

`v2/tests/coordinator/test_verdict_phase_filter.py`:

```python
from coordinator.observe import revisions_used
from coordinator.recover import decide
from kernel.projection import project


class _F:
    def __init__(self, kind, payload, seq=0):
        self.kind, self.payload, self.seq = kind, payload, seq
        self.actor, self.id, self.run_id = "x", f"f{seq}", "r"
        self.schema_version = self.mechanism_version = 1
        self.causal_command_id = None
        self.observed_at_us = seq


def _journal():
    """A spec-phase FAIL followed by an implementation PASS (spec §8)."""
    return [
        _F("run_started", {"base_sha": "0" * 40, "base_repo": "o/r", "state": "queued"}, 1),
        _F("review_verdict", {"verdict": "request_revision", "phase": "spec", "epoch": 0,
                              "artifact_hash": "a" * 64, "ruling": "review_ruling",
                              "reviewer_identity": "codex", "binding_hash": "b" * 64}, 2),
        _F("transition_performed", {"to": "queued", "via": "record_review"}, 3),
        _F("review_verdict", {"verdict": "accept", "phase": "implementation", "epoch": 0,
                              "artifact_hash": "c" * 64, "ruling": "review_ruling",
                              "reviewer_identity": "codex", "binding_hash": "d" * 64}, 4),
        _F("transition_performed", {"to": "reviewing", "via": "record_review"}, 5),
    ]


def test_revisions_used_counts_implementation_verdicts_only():
    assert revisions_used(_journal()) == 0
    facts = _journal() + [_F("review_verdict", {"verdict": "request_revision", "phase": "implementation"}, 6)]
    assert revisions_used(facts) == 1


def test_a_v1_verdict_without_phase_still_counts():
    """Journals written before this plan carry no phase: they are back-half."""
    assert revisions_used([_F("review_verdict", {"verdict": "request_revision"}, 1)]) == 1


def test_projection_separates_front_verdicts():
    st = project(_journal())
    assert [v["phase"] for v in st.verdicts] == ["implementation"]
    assert [v["phase"] for v in st.front_verdicts] == ["spec"]


def test_recover_reads_the_implementation_verdict():
    action = decide(_journal(), current_binding_hash="d" * 64)
    assert "spec" not in (getattr(action, "reason", "") or "")
    assert action.kind != "escalate" or "request_revision" not in (action.reason or "")
```

Read `decide`'s return shape (`recover.Action` at `recover.py:30`) and replace the last test's assertions with the exact field the spec-phase FAIL would have poisoned: with the filter absent, `decide` sees a `request_revision` as the newest verdict and returns its "rejected review" action (`_rejected_review`, `recover.py:218`); with it present it sees the accepted implementation verdict. Assert the accepted-path `Action.kind` literally, whatever its name is in that file.

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_verdict_phase_filter.py -q`
Expected: FAIL — `revisions_used` returns 1 for the spec FAIL; `RunState` has no `front_verdicts`.

- [ ] **Step 3: Implement**

In each consumer, one predicate, defined once in `kernel/projection.py` and imported by the two coordinator modules:

```python
FRONT_PHASES = ("spec", "plan")


def is_front_verdict(payload: dict) -> bool:
    """A review_verdict of the spec or plan phase. A verdict with no phase
    was written before the front half existed and is the implementation's."""
    return payload.get("phase") in FRONT_PHASES
```

`projection.project`: `RunState` gains `front_verdicts: list = field(default_factory=list)`; the `REVIEW_VERDICT` branch appends to `front_verdicts` when `is_front_verdict(f.payload)` else to `verdicts`. `observe.revisions_used` and `revision_confirmed`: skip a `review_verdict` when `is_front_verdict(payload)`. `recover.decide` and `_rejected_review`: filter `_of(facts, "review_verdict")` the same way — `verdicts = [v for v in _of(facts, "review_verdict") if not is_front_verdict(_payload(v))]`.

- [ ] **Step 4: Run, then commit**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator -q`
Expected: PASS. Mutation: make `is_front_verdict` return `False`: three of the four tests fail. Restore.

```bash
git add v2/kernel/projection.py v2/coordinator/observe.py v2/coordinator/recover.py v2/tests/coordinator/test_verdict_phase_filter.py
git commit -m "fix(coordinator): review_verdict consumers read the implementation phase only"
```

---

### Task 14: `wait_turn` — the turn's end, observed in order

Closes §11: `test_turn_end_matrix`, `test_displaced_before_author_empty_and_questions`, `test_agent_id_mismatch_at_poll_rc_failed`.

**Files:**
- Modify: `v2/coordinator/session.py` (`State.agent_id`, `list_items`, `wait_turn`, `TurnEnd`, `AgentMismatch`)
- Test: `v2/tests/coordinator/test_wait_turn.py`

**Interfaces:**
- Consumes: `front.newest_prompt` (Task 7), `front.direction_after`, `front.dispatch_seq` (Task 10); `session.state`, `session.died`.
- Produces:
  - `session.State` gains `agent_id: str = ""` (from the snapshot's `agent_id`).
  - `session.list_items(server, session_id, *, fetch=_fetch) -> list[dict]` — `GET {server}/v1/sessions/{id}/items`, each item projected to `{"id", "role", "text"}` (`text` is the concatenation of the item's `content[*].text` for `input_text`/`output_text` parts, `""` otherwise); raises `LookupFailed`.
  - `@dataclass(frozen=True) TurnEnd(ended: str, file_present: bool)`, `ended ∈ {"file", "dead", "cap", "displaced"}`.
  - `class AgentMismatch(Exception)` with `.session_id`, `.expected`, `.observed`.
  - `session.wait_turn(store, run_id, generation, server, session_id, watched: list[pathlib.Path], deadline_us: int, *, agent_id_expected: str, fetch=_fetch, clock=time.time, sleep=time.sleep, poll_s: float = 15.0) -> TurnEnd` — each poll, in this order: (1) a `human_direction` newer than `dispatch_seq(generation)` → `displaced`; (2) `state()`; a snapshot whose `agent_id` is non-empty and differs from `agent_id_expected` → raise `AgentMismatch` (an `unknown` read has `agent_id == ""` and compares nothing); (3) any watched path exists → `file`; (4) `died(status, error_code)` → `dead`; (5) `clock() * 1e6 >= deadline_us` → `cap`; else `sleep(poll_s)` and poll again. Nothing is read or recorded here: the caller records the end and stops the session.

- [ ] **Step 1: Write the failing tests**

`v2/tests/coordinator/test_wait_turn.py`:

```python
import json

import pytest

from coordinator.session import AgentMismatch, State, TurnEnd, list_items, state, wait_turn
from kernel.dispatch import Role
from kernel.store import Store
from tests.kernel.front import Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


class Stub:
    """A fake server: status per poll, plus a hook to plant the file."""
    def __init__(self, statuses, agent_id="agent-claude", on_poll=None):
        self.statuses, self.agent_id, self.on_poll = list(statuses), agent_id, on_poll
        self.polls = 0

    def fetch(self, url):
        self.polls += 1
        status = self.statuses[min(self.polls, len(self.statuses)) - 1]
        if self.on_poll:
            self.on_poll(self.polls)
        if status == "unreachable":
            from coordinator.session import LookupFailed
            raise LookupFailed("down")
        labels = {"omnigent.last_task_error_code": "boom"} if status == "failed" else {}
        return json.dumps({"id": "s-1", "status": status, "agent_id": self.agent_id, "labels": labels})


def _seat(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    return s, f, g, sid


def _wait(s, g, sid, stub, watched, deadline_us=10**18, clock=None, agent="agent-claude"):
    ticks = iter(range(1, 10_000))
    return wait_turn(s, "r-1", g, "http://srv", sid, watched, deadline_us,
                     agent_id_expected=agent, fetch=stub.fetch,
                     clock=clock or (lambda: next(ticks)), sleep=lambda _s: None, poll_s=0)


def test_state_carries_the_agent_id():
    st = state("http://srv", "s-1", fetch=lambda u: json.dumps({"status": "idle", "agent_id": "ag_1"}))
    assert st == State(status="idle", error_code="", agent_id="ag_1")
    assert state("http://srv", "s-1", fetch=lambda u: "not json").agent_id == ""


def test_turn_end_matrix(tmp_path):
    """§11: no file -> polled, not ended, under idle/running/unknown while the
    deadline stands; the file under any status ends it `file`; failed with no
    file is `dead`; a stale prompt row is `cap` at once."""
    s, f, g, sid = _seat(tmp_path)
    out = tmp_path / "wt" / "bircher" / "artifact.md"
    out.parent.mkdir(parents=True)
    for statuses in (["idle", "idle", "running", "unreachable", "idle"],):
        def plant(n, p=out):
            if n == 5:
                p.write_text("# done")
        stub = Stub(statuses, on_poll=plant)
        assert _wait(s, g, sid, stub, [out]) == TurnEnd("file", True)
        assert stub.polls == 5
        out.unlink()
    for status in ("idle", "running", "unknown", "failed"):
        out.write_text("x")
        stub = Stub([status if status != "unknown" else "unreachable"])
        assert _wait(s, g, sid, stub, [out]).ended == "file"
        assert stub.polls == 1
        out.unlink()
    stub = Stub(["failed"])
    assert _wait(s, g, sid, stub, [out]) == TurnEnd("dead", False)
    for status in ("idle", "failed", "running"):
        stub = Stub([status])
        assert _wait(s, g, sid, stub, [out], deadline_us=5, clock=lambda: 1.0) == TurnEnd("cap", False)
        assert stub.polls <= 1
    # A file planted between the poll and the cap is still the turn's output.
    out.write_text("late")
    assert _wait(s, g, sid, Stub(["idle"]), [out], deadline_us=5, clock=lambda: 1.0).file_present is True


def test_deadline_comes_from_the_journal_not_the_process(tmp_path):
    """A restarted pass gets no fresh window: the caller computes the deadline
    from the prompt row's at_us; wait_turn takes it as given."""
    from kernel import front
    s, f, g, sid = _seat(tmp_path)
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    cap_us = 3600 * 10**6
    deadline = prompt["at_us"] + cap_us
    late = (deadline + 1) / 1e6
    assert _wait(s, g, sid, Stub(["idle"]), [tmp_path / "none"], deadline_us=deadline,
                 clock=lambda: late).ended == "cap"
    early = (deadline - 10**6) / 1e6
    stub = Stub(["idle", "failed"])
    assert _wait(s, g, sid, stub, [tmp_path / "none"], deadline_us=deadline,
                 clock=lambda: early).ended == "dead"


def test_displaced_before_author_empty_and_questions(tmp_path):
    """§11: a kernel direct during a waited turn ends it displaced at the next
    poll, before the file and status are consulted -- under grill=human with a
    questions file already present too."""
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:grill", "bircher:autonomous"))
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    q = tmp_path / "wt" / "bircher" / "questions.md"
    q.parent.mkdir(parents=True)
    q.write_text("Q1?")
    f.direct("no, do it this way")
    stub = Stub(["idle"])
    assert _wait(s, g, sid, stub, [q, tmp_path / "wt" / "bircher" / "artifact.md"]) == TurnEnd("displaced", True)
    assert stub.polls == 0                   # nothing was read


def test_agent_id_mismatch_at_poll_rc_failed(tmp_path):
    """§11: a session whose agent_id at a poll is not its snapshot's raises
    before any file is read; an unknown read compares nothing."""
    s, f, g, sid = _seat(tmp_path)
    out = tmp_path / "wt" / "bircher" / "artifact.md"
    out.parent.mkdir(parents=True)
    out.write_text("would be read")
    with pytest.raises(AgentMismatch) as exc:
        _wait(s, g, sid, Stub(["idle"], agent_id="ag_switched"), [out])
    assert (exc.value.session_id, exc.value.expected, exc.value.observed) == (sid, "agent-claude", "ag_switched")
    assert _wait(s, g, sid, Stub(["unreachable"]), [out]).ended == "file"


def test_list_items_projects_id_role_text():
    body = json.dumps({"items": [
        {"id": "i1", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"id": "i2", "role": "assistant", "content": [{"type": "output_text", "text": "hi"},
                                                    {"type": "tool_call", "name": "x"}]},
        {"id": "i3", "role": "user", "content": []},
    ]})
    items = list_items("http://srv", "s-1", fetch=lambda u: body)
    assert items == [{"id": "i1", "role": "user", "text": "hello"},
                     {"id": "i2", "role": "assistant", "text": "hi"},
                     {"id": "i3", "role": "user", "text": ""}]
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_wait_turn.py -q`
Expected: FAIL — `ImportError: cannot import name 'wait_turn'`.

- [ ] **Step 3: Implement**

In `v2/coordinator/session.py`:

```python
@dataclass(frozen=True)
class State:
    status: str = "unknown"
    error_code: str = ""
    agent_id: str = ""


def state(server: str, conv_id: str, *, fetch=_fetch) -> State:
    try:
        d = json.loads(fetch(f"{server}/v1/sessions/{conv_id}"))
    except (LookupFailed, ValueError):
        return State()
    if not isinstance(d, dict):
        return State()
    labels = d.get("labels")
    return State(
        status=d.get("status") or "",
        error_code=(labels or {}).get("omnigent.last_task_error_code") or "",
        agent_id=str(d.get("agent_id") or ""),
    )


def list_items(server: str, conv_id: str, *, fetch=_fetch) -> list[dict]:
    """Every item of the session, oldest first, as {id, role, text}."""
    try:
        d = json.loads(fetch(f"{server}/v1/sessions/{conv_id}/items"))
    except ValueError as exc:
        raise LookupFailed(f"items: {exc}") from exc
    raw = d.get("items", d) if isinstance(d, dict) else d
    if not isinstance(raw, list):
        raise LookupFailed("items: not a list")
    out = []
    for it in raw:
        parts = it.get("content") or []
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and p.get("type") in ("input_text", "output_text"))
        out.append({"id": str(it.get("id")), "role": str(it.get("role") or ""), "text": text})
    return out


@dataclass(frozen=True)
class TurnEnd:
    ended: str
    file_present: bool


class AgentMismatch(Exception):
    def __init__(self, session_id: str, expected: str, observed: str) -> None:
        super().__init__(f"session {session_id} reports agent_id {observed!r}, "
                         f"its create's snapshot said {expected!r}")
        self.session_id, self.expected, self.observed = session_id, expected, observed


def wait_turn(store, run_id: str, generation: int, server: str, session_id: str,
              watched, deadline_us: int, *, agent_id_expected: str, fetch=_fetch,
              clock=time.time, sleep=time.sleep, poll_s: float = 15.0) -> TurnEnd:
    """spec §3 *The turn's end is a fact*: the five checks, in order, each
    poll. Reads nothing the turn wrote; records nothing."""
    from kernel import front
    watched = [pathlib.Path(p) for p in watched]
    d_seq = front.dispatch_seq(store, run_id, generation)
    while True:
        present = any(p.exists() for p in watched)
        if front.direction_after(store, run_id, d_seq) is not None:
            return TurnEnd("displaced", present)
        st = state(server, session_id, fetch=fetch)
        if st.agent_id and st.agent_id != agent_id_expected:
            raise AgentMismatch(session_id, agent_id_expected, st.agent_id)
        present = any(p.exists() for p in watched)
        if present:
            return TurnEnd("file", True)
        if died(st.status, st.error_code):
            return TurnEnd("dead", False)
        if int(clock() * 1_000_000) >= deadline_us:
            return TurnEnd("cap", any(p.exists() for p in watched))
        sleep(poll_s)
```

(`import pathlib`, `import time` at the top.) The displaced check reads the journal before the server so a displaced turn costs no fetch — the §11 test asserts `polls == 0`.

- [ ] **Step 4: Run, then commit**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_wait_turn.py tests/coordinator/test_session.py -q`
Expected: PASS. Mutation: swap checks (3) and (4) so `died` is consulted before the file: `test_turn_end_matrix` fails on the `failed`-with-file row. Move the direction check after `state()`: `test_displaced_before_author_empty_and_questions` fails on `polls == 0`. Restore.

```bash
git add v2/coordinator/session.py v2/tests/coordinator/test_wait_turn.py
git commit -m "feat(coordinator): wait_turn observes the turn's end in the spec's order; State.agent_id; list_items"
```

---

### Task 15: Session effects — create, prompt, stop, worktree, and the body through real curl

Closes §11: `test_worktree_path_exists_rc_failed`, `test_stop_no_runner_confirms`, `test_stop_503_halts`.

**Files:**
- Create: `v2/coordinator/sessions.py` (the effect helpers; `session.py` stays the read side)
- Test: `v2/tests/coordinator/test_session_effects.py`, `v2/tests/coordinator/test_body_through_curl.py`

**Interfaces:**
- Consumes: `coordinator.effects.perform_effect(effect_class, key, argv, *, timeout=None, env=None, obligation=None, body=None) -> str` (Task 3); `kernel.cli.validate_create_response`, `make_executor` (Task 3); `Store.read_blob`; `put_artifact`.
- Produces, in `coordinator/sessions.py`:
  - `class WorktreeExists(Exception)`; `add_worktree(repo_dir: str, base_sha: str, path: str) -> str` — refuses an existing path (`WorktreeExists`), runs `git -C <repo_dir> worktree add --detach <path> <base_sha>`, returns `os.path.realpath(path)`; `worktree_path(workspaces_root: str, run_id: str, generation: int) -> str` = `<root>/<run_id>/<generation>`.
  - `create_session(store, *, run_id, generation, server, agent_id, host_id, workspace, phase, epoch, cause, env) -> dict` — the projection `{id, agent_id, agent_name, host_id, workspace, title}` of a satisfied `sess-create:<run>:<gen>` (a replay returns the recorded one); body `{"agent_id", "host_id", "workspace", "title": <key>}`; argv `["curl", "-sSf", "--max-time", "30", "-X", "POST", f"{server}/v1/sessions", "-H", "content-type: application/json", "-d", <json body>]`.
  - `prompt_session(store, *, run_id, generation, server, session_id, phase, epoch, cause, text: bytes, env) -> str` — PUTs `text`, performs `sess-prompt:<session>:<gen>:<cause>` with `obligation {kind: sess-prompt, session, phase, epoch, cause}` and `body {"artifact": <hash>}`; argv `["curl", "-sSf", "--max-time", "120", "-X", "POST", f"{server}/v1/sessions/{session_id}/events", "-H", "content-type: application/json"]` — **no `-d`** (ruling 1); returns the executor's stdout (the route's JSON, from which `item_id` is read by the caller).
  - `stop_session(store, *, run_id, generation, server, session_id, cause, env) -> str` — key `sess-stop:<session>:<gen>`, obligation `{kind: sess-stop, session, cause}`, body `{"event": "stop_session"}`, same argv shape with `--max-time 30`.
  - `list_sessions(server, *, fetch) -> set[str]` — the ids `GET {server}/v1/sessions` lists.
  - `satisfied(store, run_id, obligation: dict) -> dict | None` — the effect row whose stored obligation equals `obligation` and is satisfied (the "is it owed?" read every pass makes before sending).

- [ ] **Step 1: Write the effect-helper tests**

`v2/tests/coordinator/test_session_effects.py`:

```python
import json
import os
import subprocess

import pytest

from coordinator import sessions
from coordinator.effects import KERNEL
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import Front


def _env(db, run_id, gen):
    return {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db),
            "BIRCHER_RUN_ID": run_id, "BIRCHER_GENERATION": str(gen), "PATH": os.environ["PATH"]}


@pytest.fixture
def run(tmp_path):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    return s, db, g


def test_add_worktree_refuses_an_existing_path_and_returns_realpath(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    link = tmp_path / "link"
    (tmp_path / "real").mkdir()
    link.symlink_to(tmp_path / "real")
    wt = link / "r-1" / "3"
    got = sessions.add_worktree(str(repo), sha, str(wt))
    assert got == os.path.realpath(str(wt)) and "link" not in got
    assert (wt / ".git").exists()
    with pytest.raises(sessions.WorktreeExists, match=str(wt)):
        sessions.add_worktree(str(repo), sha, str(wt))


def test_worktree_path_exists_rc_failed(tmp_path):
    """§11: a path that exists before the create is refused before anything
    is added; the loop maps WorktreeExists to RC_FAILED (Task 19)."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    existing = tmp_path / "ws" / "r-1" / "7"
    existing.mkdir(parents=True)
    with pytest.raises(sessions.WorktreeExists):
        sessions.add_worktree(str(repo), "0" * 40, str(existing))
    assert not (existing / ".git").exists()


def test_create_session_journals_the_obligation_and_records_the_snapshot(run, tmp_path, monkeypatch):
    s, db, g = run
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        body = json.loads(argv[argv.index("-d") + 1])
        class R: returncode, stdout, stderr = 0, json.dumps({"id": "s-9", "agent_id": body["agent_id"],
                                                             "agent_name": "claude", "host_id": "h",
                                                             "workspace": body["workspace"],
                                                             "title": body["title"]}), ""
        return R()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake_run)
    snap = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv",
                                   agent_id="ag_1", host_id="host_h", workspace="/w/r-1/%d" % g,
                                   phase="spec", epoch=0, cause="f-1", env=_env(db, "r-1", g))
    assert snap["id"] == "s-9" and snap["agent_name"] == "claude"
    row = s.effect_by_key(f"sess-create:r-1:{g}", run_id="r-1")
    assert row["state"] == "confirmed"
    assert row["intent"]["obligation"] == {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                           "epoch": 0, "cause": "f-1"}
    assert json.loads(row["external_object_id"])["title"] == f"sess-create:r-1:{g}"
    argv = calls[0]
    assert argv[:2] == ["curl", "-sSf"] and "POST" in argv and argv[-2] == "-d"
    assert json.loads(argv[-1]) == {"agent_id": "ag_1", "host_id": "host_h",
                                    "workspace": "/w/r-1/%d" % g, "title": f"sess-create:r-1:{g}"}
    # Replay: same obligation, same key -> the recorded snapshot, no second POST.
    again = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv",
                                    agent_id="ag_1", host_id="host_h", workspace="/w/r-1/%d" % g,
                                    phase="spec", epoch=0, cause="f-1", env=_env(db, "r-1", g))
    assert again == snap and len(calls) == 1


def test_prompt_and_stop_carry_no_d_and_compose_from_the_journal(run, tmp_path, monkeypatch):
    s, db, g = run
    seen = []

    def fake_run(argv, **kw):
        path = argv[argv.index("--data-binary") + 1][1:]
        seen.append((list(argv), open(path, "rb").read()))
        class R: returncode, stdout, stderr = 0, json.dumps({"item_id": "it-1"}), ""
        return R()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake_run)
    out = sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                                  phase="spec", epoch=0, cause="f-1", text=b"hello author",
                                  env=_env(db, "r-1", g))
    assert json.loads(out)["item_id"] == "it-1"
    argv, sent = seen[-1]
    assert "-d" not in argv and "--data-binary" in argv
    assert json.loads(sent) == {"type": "message", "data": {"role": "user",
                                "content": [{"type": "input_text", "text": "hello author"}]}}
    row = s.effect_by_key(f"sess-prompt:s-9:{g}:f-1", run_id="r-1")
    assert "-d" not in row["intent"]["argv"] and "--data-binary" not in row["intent"]["argv"]
    assert s.has_artifact(row["intent"]["body"]["artifact"])
    sessions.stop_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                          cause="te-1", env=_env(db, "r-1", g))
    argv, sent = seen[-1]
    assert json.loads(sent) == {"type": "stop_session"}
    row = s.effect_by_key(f"sess-stop:s-9:{g}", run_id="r-1")
    assert row["state"] == "confirmed" and row["intent"]["obligation"] == {"kind": "sess-stop", "session": "s-9",
                                                                             "cause": "te-1"}


def test_satisfied_matches_on_the_obligation_alone(run, tmp_path, monkeypatch):
    s, db, g = run
    monkeypatch.setattr("kernel.cli.subprocess.run", lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": json.dumps({"item_id": "x"}), "stderr": ""})())
    ob = {"kind": "sess-prompt", "session": "s-9", "phase": "spec", "epoch": 0, "cause": "f-1"}
    assert sessions.satisfied(s, "r-1", ob) is None
    sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                            phase="spec", epoch=0, cause="f-1", text=b"v1", env=_env(db, "r-1", g))
    assert sessions.satisfied(s, "r-1", ob)["idempotency_key"] == f"sess-prompt:s-9:{g}:f-1"
    # A different body under the same obligation is the same obligation.
    assert sessions.satisfied(s, "r-1", dict(ob)) is not None
    assert sessions.satisfied(s, "r-1", dict(ob, cause="f-2")) is None
```

- [ ] **Step 2: Write the real-curl tests**

`v2/tests/coordinator/test_body_through_curl.py`:

```python
"""The brief goes through the real path: perform_effect -> kernel executor ->
curl -> a loopback HTTP stub. A fake executor would pass an argv body."""
import http.server
import json
import os
import platform
import shutil
import subprocess
import threading

import pytest

from coordinator import sessions
from coordinator.effects import KERNEL
from kernel.dispatch import Role
from kernel.effects import UncertainEffect, is_halted, pending_reconciliation
from kernel.store import Store
from tests.kernel.front import Front

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


class Recorder(http.server.BaseHTTPRequestHandler):
    received = []
    stop_status = 200
    stop_body = b'{"queued": false}'

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        Recorder.received.append((self.path, body))
        if self.path.endswith("/events") and json.loads(body).get("type") == "stop_session":
            self.send_response(Recorder.stop_status)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(Recorder.stop_body)
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"item_id": "it-1"}')

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    Recorder.received = []
    Recorder.stop_status, Recorder.stop_body = 200, b'{"queued": false}'
    srv = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _env(db, gen):
    return {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
            "BIRCHER_GENERATION": str(gen), "PATH": os.environ["PATH"]}


@pytest.fixture
def run(tmp_path):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    g = f._dispatch(Role.REVIEWER, "codex")
    return s, db, g


def test_a_200kb_brief_arrives_byte_identical_and_never_rides_argv(run, server, monkeypatch):
    s, db, g = run
    brief = ("# brief\n" + "x" * 1000 + "\n") * 200        # ~200 KB
    seen = []
    real_run = subprocess.run

    def spy(argv, **kw):
        seen.append(list(argv))
        return real_run(argv, **kw)
    monkeypatch.setattr("kernel.cli.subprocess.run", spy)
    sessions.prompt_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                            phase="spec", epoch=0, cause="f-1", text=brief.encode(), env=_env(db, g))
    path, body = Recorder.received[-1]
    assert path == "/v1/sessions/s-1/events"
    assert json.loads(body)["data"]["content"][0]["text"] == brief
    argv = seen[-1]
    assert max(len(a) for a in argv) < 128 * 1024
    assert all(brief[:64] not in a for a in argv)
    row = s.effect_by_key(f"sess-prompt:s-1:{g}:f-1", run_id="r-1")
    assert brief[:64] not in json.dumps(row["intent"])
    assert "--data-binary" not in row["intent"]["argv"]


@pytest.mark.skipif(platform.system() != "Linux", reason="MAX_ARG_STRLEN is Linux's")
def test_the_same_brief_as_an_argv_argument_fails_with_e2big(server):
    brief = ("x" * 1000 + "\n") * 200
    with pytest.raises(OSError) as exc:
        subprocess.run(["curl", "-sSf", "-X", "POST", f"{server}/v1/sessions/s-1/events",
                        "-d", brief], capture_output=True)
    assert exc.value.errno == 7


def test_stop_no_runner_confirms(run, server):
    """§11: a stop the server answers 2xx `{"queued": false}` (no runner bound)
    is confirmed -- a session running nowhere is what the stop wanted."""
    s, db, g = run
    sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                          cause="te-1", env=_env(db, g))
    assert s.effect_by_key(f"sess-stop:s-1:{g}", run_id="r-1")["state"] == "confirmed"
    assert json.loads(Recorder.received[-1][1]) == {"type": "stop_session"}
    assert not is_halted(s, "r-1")


def test_stop_503_halts(run, server):
    """§11: a stop the server answers 503 (runner unreachable) is uncertain,
    the run halted, the key pending -- never confirmed, never pretended."""
    s, db, g = run
    Recorder.stop_status, Recorder.stop_body = 503, b'{"detail": "runner unreachable"}'
    with pytest.raises(UncertainEffect):
        sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                              cause="te-1", env=_env(db, g))
    assert is_halted(s, "r-1")
    assert [p["idempotency_key"] for p in pending_reconciliation(s, "r-1")] == [f"sess-stop:s-1:{g}"]


def test_stop_404_halts_and_bare_delivered_reconciles(run, server):
    from kernel.effects import Resolution, reconcile_typed
    s, db, g = run
    Recorder.stop_status, Recorder.stop_body = 404, b'{"detail": "no such session"}'
    with pytest.raises(UncertainEffect):
        sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                              cause="te-1", env=_env(db, g))
    key = f"sess-stop:s-1:{g}"
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(key, True, "some-id")], s.run_version("r-1"))
    reconcile_typed(s, "r-1", [Resolution(key, True, None)], s.run_version("r-1"))
    assert not is_halted(s, "r-1")
```

`UncertainEffect` is what `perform` raises when the executor fails (`effects.py:40`); confirm the name against `kernel/effects.py` before running, and that `perform_effect` lets it propagate (it does: `perform_effect` returns `perform(...)`'s value and catches nothing).

- [ ] **Step 3: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_session_effects.py tests/coordinator/test_body_through_curl.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.sessions'`.

- [ ] **Step 4: Implement `coordinator/sessions.py`**

```python
"""Session effects (spec §3 *Sessions are effects*): every create, prompt and
stop is a SESSION_CONTROL effect with an obligation, performed through
perform_effect. The read side (state, items) is session.py."""
from __future__ import annotations

import json
import os
import subprocess

from coordinator.effects import perform_effect
from coordinator.session import LookupFailed, _fetch
from kernel.artifacts import put_artifact

SESSION_CONTROL = "session_control"
_JSON = ["-H", "content-type: application/json"]


class WorktreeExists(Exception):
    pass


def worktree_path(workspaces_root: str, run_id: str, generation: int) -> str:
    return os.path.join(workspaces_root, run_id, str(generation))


def add_worktree(repo_dir: str, base_sha: str, path: str) -> str:
    """One worktree per session at the run's base sha. Refuses an existing
    path before anything is added; returns the realpath the create names."""
    if os.path.lexists(path):
        raise WorktreeExists(f"worktree path already exists: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(["git", "-C", repo_dir, "worktree", "add", "--detach", path, base_sha],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    return os.path.realpath(path)


def _events_argv(server: str, session_id: str, max_time: str) -> list[str]:
    # No -d: the kernel composes the event from the obligation and appends
    # --data-binary @<file> after the contract check (Task 3; ruling 1).
    return ["curl", "-sSf", "--max-time", max_time, "-X", "POST",
            f"{server}/v1/sessions/{session_id}/events", *_JSON]


def create_session(store, *, run_id: str, generation: int, server: str, agent_id: str,
                   host_id: str, workspace: str, phase: str, epoch: int, cause: str, env) -> dict:
    key = f"sess-create:{run_id}:{generation}"
    body = {"agent_id": agent_id, "host_id": host_id, "workspace": workspace, "title": key}
    argv = ["curl", "-sSf", "--max-time", "30", "-X", "POST", f"{server}/v1/sessions",
            *_JSON, "-d", json.dumps(body, sort_keys=True)]
    obligation = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": epoch, "cause": cause}
    out = perform_effect(SESSION_CONTROL, key, argv, timeout=40, env=env, obligation=obligation)
    return json.loads(out)


def prompt_session(store, *, run_id: str, generation: int, server: str, session_id: str,
                   phase: str, epoch: int, cause: str, text: bytes, env) -> str:
    h = put_artifact(store, text)
    key = f"sess-prompt:{session_id}:{generation}:{cause}"
    obligation = {"kind": "sess-prompt", "session": session_id, "phase": phase, "epoch": epoch, "cause": cause}
    return perform_effect(SESSION_CONTROL, key, _events_argv(server, session_id, "120"),
                          timeout=130, env=env, obligation=obligation, body={"artifact": h})


def stop_session(store, *, run_id: str, generation: int, server: str, session_id: str,
                 cause: str, env) -> str:
    key = f"sess-stop:{session_id}:{generation}"
    obligation = {"kind": "sess-stop", "session": session_id, "cause": cause}
    return perform_effect(SESSION_CONTROL, key, _events_argv(server, session_id, "30"),
                          timeout=40, env=env, obligation=obligation, body={"event": "stop_session"})


def list_sessions(server: str, *, fetch=_fetch) -> set[str]:
    d = json.loads(fetch(f"{server}/v1/sessions"))
    raw = d.get("sessions", d.get("items", d)) if isinstance(d, dict) else d
    if not isinstance(raw, list):
        raise LookupFailed("sessions: not a list")
    return {str(x.get("id")) for x in raw if isinstance(x, dict)}


def satisfied(store, run_id: str, obligation: dict) -> dict | None:
    """The satisfied effect row carrying exactly *obligation*, else None:
    the 'is it owed?' read before every send (spec §3 *Obligations*)."""
    from kernel.front import satisfied_effects
    for row in satisfied_effects(store, run_id, obligation["kind"]):
        if row["intent"].get("obligation") == obligation:
            return row
    return None
```

`perform_effect` in `KERNEL` mode opens the store from `env["BIRCHER_KERNEL_DB"]` (Task 3 wires `obligation`/`body` into the intent and hands `make_executor(store)`); the `store` argument here is used only for `put_artifact`, on the same database.

- [ ] **Step 5: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_session_effects.py tests/coordinator/test_body_through_curl.py -q`
Expected: PASS (the E2BIG test skips on macOS and runs in CI on Linux). Full suite: green.

- [ ] **Step 6: Mutation check, then commit**

Add `"-d", "x"` to `_events_argv`: `test_prompt_and_stop_carry_no_d_and_compose_from_the_journal` fails on `"-d" not in argv`, and — since the contract admits one `-d` on events (ruling 1) — `test_a_200kb_brief_arrives_byte_identical_and_never_rides_argv` fails on the received body (the kernel's `--data-binary` and the caller's `-d` both reach curl, and curl sends both joined by `&`). Remove the `lexists` check: `test_worktree_path_exists_rc_failed` fails. Restore.

```bash
git add v2/coordinator/sessions.py v2/tests/coordinator/test_session_effects.py v2/tests/coordinator/test_body_through_curl.py
git commit -m "feat(coordinator): session effects with obligations; the brief through real curl; worktrees by realpath"
```

---
### Task 16: `phases.py` — the context, the causes, `retire_owed`, `publish_owed`

**Files:**
- Create: `v2/coordinator/phases.py` (`Ctx`, cause derivation, `retire_owed`, `publish_owed`; the loop itself lands in Task 19)
- Create: `v2/tests/coordinator/fake_omnigent.py` (the fake server every coordinator test from here on uses)
- Test: `v2/tests/coordinator/test_retire_and_publish.py`

**Interfaces:**
- Consumes: `sessions.stop_session`, `sessions.satisfied`, `sessions.list_sessions` (Task 15); `front.satisfied_effects`, `front.newest_submission`, `front.epoch`, `front.current_park` (Tasks 7-9); `perform_effect` (Task 3); `bundle.snapshot`, `Store.read_blob`.
- Produces:
  - `phases.Ctx` (dataclass): `store, run_id, server, repo: str (o/r), issue_number: int, repo_dir: str, workspaces_root: str, bundle_dir: str, agent_ids: dict[str, str] (vendor → agent_id), host_id: str, turn_timeout_s: int, default_author: str, env: dict, fetch, clock, sleep, log` and `generation: int | None` (set by each dispatch); `Ctx.state()`, `Ctx.phase()`, `Ctx.epoch()`.
  - `phases.ROUND_CAUSE_KINDS`, `phases.round_cause(ctx) -> Fact` (the fact the current author round answers: the newest fact of the run among `run_started`, `bundle_revised`, `review_verdict`, `human_ruling`, `human_direction`, `author_empty`, `parked`, and a `command_rejected` for `submit_spec`/`submit_plan` by a non-human actor — the enumeration of spec §3 *Obligations*, read as "the newest fact that called for a round"), `phases.seat_cause(ctx) -> Fact` (the reviewer seat's: the newest `human_ruling {grant_round}` newer than the phase's newest `artifact_submitted` of the epoch, else that `artifact_submitted`), `phases.answer_to_resume(ctx) -> Fact | None` (the epoch's newest `human_answer` when it is newer than the newest `model_question` of the epoch and than the round cause — the same session continues).
  - `phases.derived_session_obligation(ctx) -> dict | None` — what this pass would create: `{kind: sess-create, run, phase, epoch, cause}` for `queued`/`specified` (cause `round_cause`), for `*_submitted` (cause `seat_cause`), for `*_accepted` with a current park whose `session_id` is null (cause the park), else `None`.
  - `phases.retire_owed(ctx) -> list[str]` — performs every owed stop, first thing; returns the keys performed; raises what `perform` raises (a failing stop halts).
  - `phases.publish_owed(ctx) -> list[str]` — publishes each approved-but-unpublished phase artefact; returns the keys performed.
  - `phases.PUBLISH_CAP = 60_000`, `phases.publication_body(phase, artefact: bytes, artifact_hash) -> str` (ruling 10, below).
  - `fake_omnigent.FakeOmnigent` — `sessions: dict[id → {status, agent_id, agent_name, host_id, workspace, title, items: list, labels}]`, `next_ids`, `fetch(url)` (GET session, items, sessions list), `run(argv, **kw)` (the fake `subprocess.run` for curl: POST create → a snapshot with `agent_name` = the vendor whose `agent_id` was sent, per `agent_names: dict[agent_id → name]`; POST events `message` → appends a user item `{id, role: user, content: [{type: input_text, text}]}` and calls `on_prompt(session_id, text)`; `stop_session` → `status = "idle"`, calls `on_stop(session_id)`; 404 for an unknown session; `stop_status` overridable), `add_user_message(session_id, text) -> item_id` (the human typing), `listed: set[str]` (which sessions `GET /v1/sessions` returns).

**Ruling 10 (recorded in *Rulings taken while planning* at assembly):** the publication comment is the header line `bircher: published <phase> <hash8>`, a blank line, then the artefact text truncated to `PUBLISH_CAP` characters with a final line `... truncated; full bytes are in the kernel store under <sha256>` when it was cut. GitHub caps a comment at 65536 characters and the `COMMENT` contract carries the body inline in `--body`, so the value must stay under both that and Linux's 128 KiB argument bound; the store and `<bundle-dir>/<run>/<phase>-r<round>.md` hold the whole artefact.

- [ ] **Step 1: The fake server**

`v2/tests/coordinator/fake_omnigent.py`:

```python
"""A fake omnigent for coordinator tests: the read side through `fetch`,
the write side as a fake `subprocess.run` for the curl argv the kernel's
executor builds (Task 3 appends --data-binary @<file> to it)."""
import itertools
import json

from coordinator.session import LookupFailed


class FakeOmnigent:
    def __init__(self, agent_names=None):
        # agent_id -> the BUNDLE's name, as the server reports it (ruling 14).
        self.agent_names = dict(agent_names or {"ag_claude": "v2_author_claude", "ag_codex": "v2_author_codex"})
        self.sessions = {}
        self.listed = set()
        self.stop_status = 200
        self.on_prompt = lambda sid, text: None
        self.on_stop = lambda sid: None
        self._ids = itertools.count(1)
        self.posts = []

    # -- read side ------------------------------------------------------------
    def fetch(self, url):
        path = url.split("//", 1)[-1].split("/", 1)[1]
        if path == "v1/sessions":
            return json.dumps({"sessions": [{"id": s} for s in sorted(self.listed)]})
        parts = path.split("/")
        sid = parts[2]
        if sid not in self.sessions or sid not in self.listed:
            raise LookupFailed("404")
        s = self.sessions[sid]
        if len(parts) == 4 and parts[3] == "items":
            return json.dumps({"items": s["items"]})
        return json.dumps({k: s[k] for k in ("id", "status", "agent_id", "agent_name", "host_id",
                                             "workspace", "title", "labels")})

    # -- write side -------------------------------------------------------------
    def run(self, argv, **kw):
        class R:
            returncode, stdout, stderr = 0, "", ""
        url = next(a for a in argv if a.startswith("http"))
        path = url.split("//", 1)[-1].split("/", 1)[1]
        if "--data-binary" in argv:
            body = json.loads(open(argv[argv.index("--data-binary") + 1][1:], "rb").read())
        else:
            body = json.loads(argv[argv.index("-d") + 1])
        self.posts.append((path, body))
        r = R()
        if path == "v1/sessions":
            sid = f"s-{next(self._ids)}"
            snap = {"id": sid, "status": "idle", "agent_id": body["agent_id"],
                    "agent_name": self.agent_names[body["agent_id"]], "host_id": body["host_id"].removeprefix("host_"),
                    "workspace": body["workspace"], "title": body["title"], "items": [], "labels": {}}
            self.sessions[sid] = snap
            self.listed.add(sid)
            r.stdout = json.dumps({k: v for k, v in snap.items() if k != "items"})
            return r
        sid = path.split("/")[2]
        if sid not in self.sessions:
            r.returncode, r.stderr = 22, "curl: (22) The requested URL returned error: 404"
            return r
        s = self.sessions[sid]
        if body.get("type") == "stop_session":
            if self.stop_status != 200:
                r.returncode, r.stderr = 22, f"curl: (22) The requested URL returned error: {self.stop_status}"
                return r
            s["status"] = "idle"
            self.on_stop(sid)
            r.stdout = '{"queued": false}'
            return r
        text = body["data"]["content"][0]["text"]
        item_id = f"it-{next(self._ids)}"
        s["items"].append({"id": item_id, "role": "user", "content": [{"type": "input_text", "text": text}]})
        s["status"] = "running"
        self.on_prompt(sid, text)
        r.stdout = json.dumps({"item_id": item_id})
        return r

    def add_user_message(self, sid, text):
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "user", "content": [{"type": "input_text", "text": text}]})
        return item_id

    def add_assistant_text(self, sid, text):
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "assistant", "content": [{"type": "output_text", "text": text}]})
        return item_id
```

- [ ] **Step 2: Write the failing tests**

`v2/tests/coordinator/test_retire_and_publish.py`:

```python
import json
import os

import pytest

from coordinator import phases
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.effects import UncertainEffect, is_halted
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    fake = FakeOmnigent()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
    env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
           "PATH": os.environ["PATH"]}
    ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                     repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                     bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                     host_id="host_h", turn_timeout_s=5400, default_author="claude", env=env,
                     fetch=fake.fetch, clock=lambda: 10**9, sleep=lambda s: None, log=lambda m: None)
    return s, f, fake, ctx


def _register(fake, sid, agent="ag_claude", name="v2_author_claude"):
    fake.sessions[sid] = {"id": sid, "status": "idle", "agent_id": agent, "agent_name": name,
                          "host_id": "h", "workspace": f"/w/{sid}", "title": "t", "items": [], "labels": {}}
    fake.listed.add(sid)


def test_retire_stops_every_turn_ended_without_a_stop(world):
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    _register(fake, sid)
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "file"})   # no stop journaled
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    keys = phases.retire_owed(ctx)
    assert keys == [f"sess-stop:{sid}:{ctx.generation}"]
    assert front.stop_satisfied_for(s, "r-1", te.id)
    assert phases.retire_owed(ctx) == []                       # once


def test_retire_derives_from_the_journal_not_the_server(world):
    """A turn_ended whose session the server lists as failed, or no longer
    lists, derives a stop all the same; the 404 halts."""
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    f._cmd(g, "record_turn_ended", {"session": sid, "ended": "dead"})
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(UncertainEffect):
        phases.retire_owed(ctx)                                 # fake knows no such session -> 404
    assert is_halted(s, "r-1")


def test_retire_stops_an_orphan_with_the_displacing_cause(world):
    s, f, fake, ctx = world
    g = f._dispatch(Role.REVIEWER, "codex")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._create(g, f._newest_id())        # a satisfied create with no prompt: the effects table is append-only
    _register(fake, sid, "ag_codex", "v2_author_codex")
    # A human request_revision returns the run to queued: the seat is displaced.
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    keys = phases.retire_owed(ctx)
    assert keys == [f"sess-stop:{sid}:{ctx.generation}"]
    row = s.effect_by_key(keys[0], run_id="r-1")
    assert row["intent"]["obligation"] == {"kind": "sess-stop", "session": sid, "cause": ruling.id}
    assert phases.retire_owed(ctx) == []


def test_the_pass_does_not_retire_the_session_it_derives(world):
    """A create satisfied for the obligation this pass derives is adopted,
    not stopped: the crash-between-create-and-prompt case."""
    s, f, fake, ctx = world
    g = f._dispatch(Role.AUTHOR, "claude")
    cause = phases.round_cause(ctx).id
    sid = f._create(g, cause)                 # create satisfied, prompt owed
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.derived_session_obligation(ctx) == {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                                      "epoch": 0, "cause": cause}
    assert phases.retire_owed(ctx) == []


def test_round_cause_follows_the_journal(world):
    s, f, fake, ctx = world
    assert phases.round_cause(ctx).kind == "run_started"
    f.author_round(SPEC_BYTES)
    assert phases.seat_cause(ctx).kind == "artifact_submitted"
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.grant()
    assert phases.seat_cause(ctx).kind == "human_ruling"
    f.review_round("request_revision")
    assert phases.round_cause(ctx).kind == "review_verdict"
    f.direct("try again")
    assert phases.round_cause(ctx).kind == "human_direction"
    sid = f.ask_round([("q1", "?")])
    f.answer("a", question_ids=("q1",))
    assert phases.answer_to_resume(ctx).kind == "human_answer"
    f.author_round(SPEC_BYTES + b"2", resume=sid)
    f.review_round("accept")
    assert phases.round_cause(ctx).kind == "review_verdict"          # the accept opened the plan phase
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    assert phases.round_cause(ctx).kind == "bundle_revised"


def test_publish_owed_posts_once_per_approved_phase_and_is_idempotent(world):
    s, f, fake, ctx = world
    posted = []

    def gh(argv, **kw):
        posted.append(argv)
        class R: returncode, stdout, stderr = 0, "https://github.com/o/r/issues/1#issuecomment-9", ""
        return R()
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.publish_owed(ctx) == []
    f.to_specified()
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    import kernel.cli
    real = kernel.cli.subprocess.run
    kernel.cli.subprocess.run = lambda argv, **kw: gh(argv, **kw) if argv[0] == "gh" else real(argv, **kw)
    try:
        keys = phases.publish_owed(ctx)
        h = s.phase_artifact("r-1", "spec")
        assert keys == [f"publish:r-1:spec:{h}:{ctx.generation}"]
        body = posted[0][posted[0].index("--body") + 1]
        assert body.startswith(f"bircher: published spec {h[:8]}\n")
        assert SPEC_BYTES.decode() in body
        row = s.effect_by_key(keys[0], run_id="r-1")
        assert row["intent"]["obligation"] == {"kind": "publish", "run": "r-1", "phase": "spec", "hash": h}
        assert phases.publish_owed(ctx) == []
        f.author_round(PLAN_BYTES); f.review_round("accept")
        ctx.generation = f._dispatch(Role.OPERATOR, "runner")
        assert [k.split(":")[2] for k in phases.publish_owed(ctx)] == ["plan"]
    finally:
        kernel.cli.subprocess.run = real


def test_publication_body_is_capped():
    big = b"x" * (phases.PUBLISH_CAP + 100)
    body = phases.publication_body("plan", big, "a" * 64)
    assert body.startswith("bircher: published plan aaaaaaaa\n\n")
    assert len(body) < phases.PUBLISH_CAP + 200
    assert body.rstrip().endswith("under " + "a" * 64)
    small = phases.publication_body("spec", b"# S", "b" * 64)
    assert small == "bircher: published spec bbbbbbbb\n\n# S"
```

- [ ] **Step 3: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_retire_and_publish.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.phases'`.

- [ ] **Step 4: Implement `phases.py` (first half)**

```python
"""The phase loop's context and journal-derived obligations (spec §3)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from coordinator import sessions
from coordinator.effects import perform_effect
from kernel import front
from kernel.authz import FRONT_HALF_STATES, phase_of
from kernel.events import EventKind

PUBLISH_CAP = 60_000
APPROVED_AT = {"spec": ("specified", "plan_submitted", "plan_accepted", "planned"),
               "plan": ("planned",)}
_BEYOND_FRONT = ("implementing", "reviewing", "merge_requested", "merged", "ended")


@dataclass
class Ctx:
    store: object
    run_id: str
    server: str
    repo: str
    issue_number: int
    repo_dir: str
    workspaces_root: str
    bundle_dir: str
    agent_ids: dict
    host_id: str
    turn_timeout_s: int
    default_author: str
    env: dict
    fetch: object
    clock: object = time.time
    sleep: object = time.sleep
    log: object = print
    generation: int | None = None

    def state(self) -> str:
        return self.store.run_state(self.run_id)

    def phase(self) -> str:
        return phase_of(self.state())

    def epoch(self) -> int:
        return front.epoch(self.store, self.run_id)

    def effect_env(self) -> dict:
        return dict(self.env, BIRCHER_GENERATION=str(self.generation))


ROUND_CAUSE_KINDS = frozenset({
    EventKind.RUN_STARTED, EventKind.BUNDLE_REVISED, EventKind.REVIEW_VERDICT,
    EventKind.HUMAN_RULING, EventKind.HUMAN_DIRECTION, EventKind.AUTHOR_EMPTY,
    EventKind.PARKED, EventKind.COMMAND_REJECTED,
})


def _is_round_cause(f) -> bool:
    if f.kind == EventKind.COMMAND_REJECTED:
        return f.payload.get("command_name") in ("submit_spec", "submit_plan") and f.actor != "human"
    return f.kind in ROUND_CAUSE_KINDS


def round_cause(ctx: Ctx):
    facts = [f for f in ctx.store.facts_for(ctx.run_id) if _is_round_cause(f)]
    return facts[-1]


def seat_cause(ctx: Ctx):
    sub = front.newest_submission(ctx.store, ctx.run_id, ctx.phase(), ctx.epoch())
    grants = [f for f in ctx.store.facts_of_kind(ctx.run_id, EventKind.HUMAN_RULING)
              if f.payload.get("ruling") == "grant_round" and sub is not None and f.seq > sub.seq]
    return grants[-1] if grants else sub


def answer_to_resume(ctx: Ctx):
    n = ctx.epoch()
    answers = front.epoch_facts(ctx.store, ctx.run_id, EventKind.HUMAN_ANSWER, n)
    if not answers:
        return None
    a = answers[-1]
    questions = front.epoch_facts(ctx.store, ctx.run_id, EventKind.MODEL_QUESTION, n)
    if questions and questions[-1].seq > a.seq:
        return None
    return a if a.seq > round_cause(ctx).seq else None


def derived_session_obligation(ctx: Ctx) -> dict | None:
    state, phase, n = ctx.state(), ctx.phase(), ctx.epoch()
    if state in ("queued", "specified"):
        cause = round_cause(ctx).id
    elif state in ("spec_submitted", "plan_submitted"):
        cause = seat_cause(ctx).id
    elif state in ("spec_accepted", "plan_accepted"):
        park = front.current_park(ctx.store, ctx.run_id)
        if park is None or park.payload.get("session_id"):
            return None
        cause = park.id
    else:
        return None
    return {"kind": "sess-create", "run": ctx.run_id, "phase": phase, "epoch": n, "cause": cause}


def _displacing_cause(ctx: Ctx) -> str:
    ob = derived_session_obligation(ctx)
    if ob is not None:
        return ob["cause"]
    # No round to derive: the newest fact that put the run where it is.
    for f in reversed(ctx.store.facts_for(ctx.run_id)):
        if f.kind in (EventKind.HUMAN_RULING, EventKind.TRANSITION, EventKind.REVIEW_VERDICT):
            return f.id
    return ctx.store.facts_for(ctx.run_id)[0].id


def _stop(ctx: Ctx, session_id: str, cause: str) -> str:
    key = f"sess-stop:{session_id}:{ctx.generation}"
    sessions.stop_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                          session_id=session_id, cause=cause, env=ctx.effect_env())
    return key


def retire_owed(ctx: Ctx) -> list[str]:
    """spec §3 *retire_owed is the loop's first step*: every stop the journal
    owes, from the journal alone. A stop that fails raises and halts."""
    done = []
    store, run_id = ctx.store, ctx.run_id
    # The turn rule.
    for te in store.facts_of_kind(run_id, EventKind.TURN_ENDED):
        if not front.stop_satisfied_for(store, run_id, te.id):
            done.append(_stop(ctx, te.payload["session"], te.id))
    # The orphan rule.
    prompted = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-prompt")}
    stopped = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-stop")}
    derived = derived_session_obligation(ctx)
    for row in front.satisfied_effects(store, run_id, "sess-create"):
        ob = row["intent"]["obligation"]
        sid = __import__("json").loads(row["external_object_id"])["id"]
        if sid in prompted or sid in stopped or ob == derived:
            continue
        done.append(_stop(ctx, sid, _displacing_cause(ctx)))
    return done


def publication_body(phase: str, artefact: bytes, artifact_hash: str) -> str:
    text = artefact.decode("utf-8", "replace")
    head = f"bircher: published {phase} {artifact_hash[:8]}\n\n"
    if len(text) > PUBLISH_CAP:
        text = text[:PUBLISH_CAP] + f"\n... truncated; full bytes are in the kernel store under {artifact_hash}"
    return head + text


def publish_owed(ctx: Ctx) -> list[str]:
    done = []
    state = ctx.state()
    for phase, states in APPROVED_AT.items():
        if state not in states and state not in _BEYOND_FRONT:
            continue
        h = ctx.store.phase_artifact(ctx.run_id, phase)
        if h is None:
            continue
        ob = {"kind": "publish", "run": ctx.run_id, "phase": phase, "hash": h}
        if sessions.satisfied(ctx.store, ctx.run_id, ob) is not None:
            continue
        key = f"publish:{ctx.run_id}:{phase}:{h}:{ctx.generation}"
        body = publication_body(phase, ctx.store.read_blob(h), h)
        perform_effect("comment", key, ["gh", "issue", "comment", str(ctx.issue_number), "--repo", ctx.repo,
                                        "--body", body], timeout=60, env=ctx.effect_env(), obligation=ob)
        done.append(key)
    return done
```

`EffectClass.COMMENT`'s string value is `"comment"` — confirm against `kernel/effects.py:17` and use the constant (`from kernel.effects import EffectClass`; `EffectClass.COMMENT`) rather than the literal.

- [ ] **Step 5: Run, then commit**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_retire_and_publish.py -q`
Expected: PASS. Mutation: drop `ob == derived` from the orphan filter: `test_the_pass_does_not_retire_the_session_it_derives` fails. Make `retire_owed` skip a `turn_ended` whose `ended == "dead"`: `test_retire_derives_from_the_journal_not_the_server` fails (no halt). Restore.

```bash
git add v2/coordinator/phases.py v2/tests/coordinator/fake_omnigent.py v2/tests/coordinator/test_retire_and_publish.py
git commit -m "feat(coordinator): phases context, journal-derived causes, retire_owed, publish_owed"
```

---

### Task 17: The seat and the author round

Closes §11: `test_read_after_stop_partial_bytes_hashed`.

**Files:**
- Create: `v2/coordinator/seat.py` (`run_turn`: adopt-or-create, prompt-or-complete, wait, end, stop, read — shared by the author, the reviewer and the human pass)
- Create: `v2/coordinator/author.py` (`author_brief`, `parse_questions`, `author_round`)
- Create: `skills/spec-author/SKILL.md`, `skills/plan-author/SKILL.md` (the phase instructions the brief embeds; the bundles in Task 20 ship them too)
- Test: `v2/tests/coordinator/test_seat.py`, `v2/tests/coordinator/test_author_round.py`

**Interfaces:**
- Consumes: `Ctx`, `round_cause`, `answer_to_resume` (Task 16); `sessions.*` (Task 15); `session.wait_turn`, `list_items`, `AgentMismatch` (Task 14); `front.newest_seat`, `front.newest_prompt`, `front.satisfied_effects`; the kernel commands through `Command`/`submit`; `dispatch`, `SeatsExhausted`, `PendingEffects` (Task 6).
- Produces:
  - `seat.ARTIFACT_OUT = "bircher/artifact.md"`, `seat.QUESTIONS_OUT = "bircher/questions.md"`, `seat.REVIEW_OUT = "bircher/review.md"` (equal to `brief.REVIEW_OUT`).
  - `@dataclass seat.Turn(session_id, generation, workspace, agent_name, ended: str, files: dict[str, bytes | None], cursor_before: str | None, listing: list[dict])` — `agent_name` is the bundle's, as the snapshot reports it.
  - `seat.run_turn(ctx, *, role: str, vendor: str, session_obligation: dict, prompt_cause: str, prompt_text: bytes, watched: list[str], read: list[str] | None = None, resume_session: str | None = None) -> Turn` — dispatches `role` as `vendor` (raising `SeatsExhausted`/`PendingEffects` upward); adopts the satisfied create for `session_obligation` else adds a worktree and creates; sends the prompt for `{sess-prompt, session, phase, epoch, cause: prompt_cause}` if owed, else completes it; lists the session and records `prompt_item` for the item whose text hashes to the prompt (by id or, in the crash window, by hash); moves earlier turn files aside before a re-prompt; waits with the deadline from the prompt row's `at_us` + `turn_timeout_s`; records `record_turn_ended`; stops; reads every watched file once (present → bytes, absent → `None`); returns the `Turn`. `AgentMismatch` propagates.
  - `seat.command(ctx, name, payload) -> Result` (a `Command` under `ctx.generation` with a fresh key `f"{name}:{run}:{gen}:{n}"`), `seat.read_once(path) -> bytes | None`.
  - `author.Outcome ∈ {"submitted", "questions", "direction", "empty_retry", "reauthor", "stall", "failed", "budget"}`; `author.author_round(ctx) -> str`; `author.author_brief(ctx, *, phase, resume_answer=None) -> bytes`; `author.parse_questions(text: str) -> list[dict]` (`{"id", "question", "recommended", "ruling"}` from `### Q<id>: <question>` blocks with `Recommended:` and optional `Ruling:` lines); `author.choose_author_vendor(ctx) -> str`.

- [ ] **Step 1: The two skills**

`skills/spec-author/SKILL.md` — the spec phase's instructions, composed from the upstream brainstorming skill's design template, not forked. Content (verbatim in the file):

```markdown
# spec-author

You are writing the design spec for one GitHub issue. You have the frozen
issue snapshot, the run's policy, and — on a revision — the previous draft
and the reviewer's findings. Nothing else is in scope.

## What to produce

A design document a plan can be written from: purpose, constraints, the
approach with its alternatives and why this one, architecture and data
flow, error handling, and testing. Scale each section to its complexity.
No placeholders ("TBD", "to be decided"), no contradictions, no requirement
that could be read two ways.

## Where to write it

Write the finished document to `bircher/artifact.md` inside your worktree:
write it elsewhere first, then rename it into place, so a partial file is
never read. Then end your turn. Do not commit, push, open anything, or write
outside your worktree.

## If you must ask

Under `grill: human` you may end the turn with questions instead of an
artefact. Write them to `bircher/questions.md`, one block per question:

    ### Q<n>: <the question>
    Recommended: <your recommended answer, one line>

and end your turn without `bircher/artifact.md`. You will be prompted again
with the human's answers in this same session; then write the spec.

Under `grill: model` you rule yourself: write the same blocks with a third
line `Ruling: <your decision> — <reasoning> — <cost if wrong>` to
`bircher/questions.md`, then continue to the artefact in the same turn.

## On a revision

You are a reader of findings, not a defender of the draft. Address every
finding; where you disagree, say why in the document.
```

`skills/plan-author/SKILL.md`:

```markdown
# plan-author

You are writing the implementation plan for an accepted spec. You have the
frozen issue snapshot, the accepted spec (verbatim), the policy, and — on a
revision — the previous plan and the reviewer's findings.

## What to produce

A plan an engineer with no context can execute: for each task, the exact
files to create or modify, the interfaces it consumes and produces, the
failing test to write first, the code, the command to run, the commit. Tasks
are headed `### Task N: <name>` — a plan with no `### Task` heading is
refused. No "TBD", no "add error handling", no "similar to Task N": every
step carries its content.

## Where to write it

`bircher/artifact.md` in your worktree, written elsewhere and renamed into
place; then end your turn. Do not commit, push or open anything.

## Questions

The spec is accepted; the plan implements it. If the spec is ambiguous in a
way the plan cannot settle, record the ambiguity and your ruling in the
plan's opening section under "Rulings" — do not ask.
```

- [ ] **Step 2: Write the seat tests**

`v2/tests/coordinator/test_seat.py`:

```python
import json
import os
import time

import pytest

from coordinator import phases, seat
from coordinator.effects import KERNEL
from coordinator.session import AgentMismatch
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    fake = FakeOmnigent()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
    monkeypatch.setattr("coordinator.sessions.add_worktree",
                        lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
    env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
           "PATH": os.environ["PATH"]}
    clock = {"t": time.time()}               # offset from real time: effect rows carry the kernel's clock
    ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                     repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                     bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                     host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                     fetch=fake.fetch, clock=lambda: clock["t"], sleep=lambda x: clock.__setitem__("t", clock["t"] + x),
                     log=lambda m: None)
    return s, f, fake, ctx, clock


def _ob(ctx, cause):
    return {"kind": "sess-create", "run": "r-1", "phase": "spec", "epoch": 0, "cause": cause}


def test_run_turn_creates_prompts_waits_ends_stops_and_reads(world, tmp_path):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        with open(os.path.join(ws, seat.ARTIFACT_OUT), "wb") as fh:
            fh.write(b"# the spec")
    fake.on_prompt = on_prompt
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"write the spec", watched=[seat.ARTIFACT_OUT])
    assert t.agent_name == "v2_author_claude" and t.ended == "file"
    assert t.files == {seat.ARTIFACT_OUT: b"# the spec"}
    assert t.workspace == os.path.realpath(os.path.join(ctx.workspaces_root, "r-1", str(t.generation)))
    create = s.effect_by_key(f"sess-create:r-1:{t.generation}", run_id="r-1")
    assert create["intent"]["obligation"] == _ob(ctx, cause)
    body = json.loads(create["intent"]["argv"][-1])
    assert body["workspace"] == t.workspace and body["title"] == f"sess-create:r-1:{t.generation}"
    prompt_key = f"sess-prompt:{t.session_id}:{t.generation}:{cause}"
    assert s.effect_by_key(prompt_key, run_id="r-1")["state"] == "confirmed"
    pi = s.newest_fact("r-1", EventKind.PROMPT_ITEM)
    assert pi.payload["session_id"] == t.session_id and pi.payload["item_id"] == fake.sessions[t.session_id]["items"][0]["id"]
    te = s.newest_fact("r-1", EventKind.TURN_ENDED)
    assert te.payload["prompt_key"] == prompt_key and te.payload["ended"] == "file"
    assert front.stop_satisfied_for(s, "r-1", te.id)
    # The journal shows the stop before the read: the read is not an effect,
    # so the proof is that the file bytes were taken after the stop confirmed.
    posts = [p for p, _ in fake.posts]
    assert posts[-1].endswith("/events")
    assert fake.posts[-1][1] == {"type": "stop_session"}


def test_read_after_stop_partial_bytes_hashed(world):
    """§11: a session still writing when the stop confirms yields whatever
    bytes the single read took, and the hash the journal holds is of those."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    state = {}

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        with open(os.path.join(ws, seat.ARTIFACT_OUT), "wb") as fh:
            fh.write(b"half")
        state["path"] = os.path.join(ws, seat.ARTIFACT_OUT)

    def on_stop(sid):
        with open(state["path"], "ab") as fh:
            fh.write(b" and more")      # lands after the stop confirms, before the read
    fake.on_prompt, fake.on_stop = on_prompt, on_stop
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.files[seat.ARTIFACT_OUT] == b"half and more"
    from kernel.artifacts import put_artifact
    from kernel.canon import content_hash
    assert put_artifact(s, t.files[seat.ARTIFACT_OUT]) == content_hash(b"half and more")


def test_a_second_pass_adopts_the_session_and_completes_the_prompt(world):
    """Crash after the create confirmed, before the prompt: the next pass
    adopts and sends; crash after the prompt confirmed, before prompt_item:
    the next pass finds the item by hash and records it, sending nothing."""
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    g1 = f._dispatch(Role.AUTHOR, "claude")
    from coordinator import sessions
    ctx.generation = g1
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g1))
    os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g1, server="http://srv", agent_id="ag_claude",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=cause, env=ctx.effect_env())
    fake.on_prompt = lambda sid, text: None
    clock["t"] += 5
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.session_id == snap["id"] and t.generation != g1
    assert len([k for k, _ in fake.posts if k == "v1/sessions"]) == 1        # no second create
    assert t.ended == "cap" and t.files == {seat.ARTIFACT_OUT: None}
    # Now the crash-window case: prompt confirmed, prompt_item missing.
    n_prompts = len([k for k, _ in fake.posts if k.endswith("/events")])
    items_before = len(fake.sessions[t.session_id]["items"])
    # A fresh round with the same cause would be the same obligation; simulate
    # the window by removing the prompt_item fact's effect: the facts table is
    # append-only, so drive it the other way -- a new cause, prompt confirmed
    # by hand, then run_turn completes it.
    f.answer("continue")
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    g3 = f._dispatch(Role.AUTHOR, "claude")
    ctx.generation = g3
    sessions.prompt_session(s, run_id="r-1", generation=g3, server="http://srv", session_id=t.session_id,
                            phase="spec", epoch=0, cause=ans.id, text=b"Answered; continue.\n\ncontinue",
                            env=ctx.effect_env())
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.\n\ncontinue",
                       watched=[seat.ARTIFACT_OUT], resume_session=t.session_id)
    assert len(fake.sessions[t.session_id]["items"]) == items_before + 1       # nothing re-sent
    assert [p.payload["item_id"] for p in s.facts_of_kind("r-1", EventKind.PROMPT_ITEM)][-1] == \
        fake.sessions[t.session_id]["items"][-1]["id"]


def test_deadline_is_the_prompt_rows_at_us_plus_the_cap(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id
    ctx.turn_timeout_s = 50
    fake.on_prompt = lambda sid, text: None
    start = clock["t"]
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert t.ended == "cap"
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    assert clock["t"] * 1e6 >= prompt["at_us"] + 50 * 1e6
    assert clock["t"] - start < 100


def test_agent_mismatch_propagates_before_any_read(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        fake.sessions[sid]["agent_id"] = "ag_switched"
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(b"x")
    fake.on_prompt = on_prompt
    with pytest.raises(AgentMismatch):
        seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT])
    assert s.newest_fact("r-1", EventKind.TURN_ENDED) is None
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED) is None


def test_earlier_turn_files_are_moved_aside_before_a_reprompt(world):
    s, f, fake, ctx, clock = world
    cause = phases.round_cause(ctx).id

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(b"### Q1: x?\nRecommended: y\n")
    fake.on_prompt = on_prompt
    t = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                      prompt_cause=cause, prompt_text=b"go", watched=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT])
    assert t.files[seat.QUESTIONS_OUT].startswith(b"### Q1")
    f.answer("y", question_ids=("Q1",))
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    fake.on_prompt = lambda sid, text: None
    t2 = seat.run_turn(ctx, role=Role.AUTHOR, vendor="claude", session_obligation=_ob(ctx, cause),
                       prompt_cause=ans.id, prompt_text=b"Answered; continue.",
                       watched=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT], resume_session=t.session_id)
    assert t2.files == {seat.ARTIFACT_OUT: None, seat.QUESTIONS_OUT: None}    # the old file is not this turn's
    assert os.path.exists(os.path.join(t.workspace, seat.QUESTIONS_OUT + ".1.prev"))
```

- [ ] **Step 3: Write the author-round tests**

`v2/tests/coordinator/test_author_round.py`:

```python
import os
import time

import pytest

from coordinator import author, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels, project_config=cfg)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}           # offset from real time: effect rows carry the kernel's clock
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _writes(fake, name, data):
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, name), "wb").write(data)
    fake.on_prompt = on_prompt


def test_parse_questions():
    text = "### Q1: sqlite or postgres?\nRecommended: sqlite\n\n### Q2: port?\nRecommended: 8080\nRuling: 8080 — default — low\n"
    qs = author.parse_questions(text)
    assert qs == [{"id": "Q1", "question": "sqlite or postgres?", "recommended": "sqlite", "ruling": None},
                  {"id": "Q2", "question": "port?", "recommended": "8080", "ruling": "8080 — default — low"}]
    assert author.parse_questions("no questions here") == []


def test_an_artefact_turn_submits_and_writes_the_copy(world, tmp_path):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert s.run_state("r-1") == "spec_submitted"
    sub = s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert sub.payload["author"] == "claude" and sub.payload["round"] == 1
    copy = tmp_path / "bundle" / "r-1" / "spec-r1.md"
    assert copy.read_bytes() == SPEC_BYTES
    sid = list(fake.sessions)[0]
    prompt_text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "spec-author" in prompt_text and "bircher/artifact.md" in prompt_text
    assert '"body": "B"' in prompt_text or '"body":"B"' in prompt_text


def test_the_brief_carries_findings_on_a_revision_and_the_spec_for_a_plan(world):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    f.review_round("request_revision", findings=b"needs a threat model")
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES + b"\n## Threats\n")
    assert author.author_round(ctx) == "submitted"
    sid = list(fake.sessions)[-1]
    text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "needs a threat model" in text and SPEC_BYTES.decode() in text
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED).payload["author"] == "codex"   # rotation
    f.reviewer = "claude"                                                # the other vendor reviews
    f.review_round("accept")
    _writes(fake, seat.ARTIFACT_OUT, b"# Plan\n\n### Task 1: x\n")
    assert author.author_round(ctx) == "submitted"
    sid = list(fake.sessions)[-1]
    text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "plan-author" in text and (SPEC_BYTES + b"\n## Threats\n").decode() in text


def test_questions_under_grill_human_are_recorded_and_the_session_resumes(world):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    _writes(fake, seat.QUESTIONS_OUT, b"### Q1: db?\nRecommended: sqlite\n")
    assert author.author_round(ctx) == "questions"
    q = s.newest_fact("r-1", EventKind.MODEL_QUESTION)
    assert q.payload["question_id"] == "Q1" and q.payload["question"] == "db?"
    sid = list(fake.sessions)[0]
    f.answer("sqlite", question_ids=("Q1",), cursor=fake.sessions[sid]["items"][-1]["id"])
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert list(fake.sessions) == [sid]                                  # the same session
    assert fake.sessions[sid]["items"][-1]["content"][0]["text"].startswith("Answered; continue.")


def test_rulings_under_grill_model_are_recorded_before_the_submit(world):
    s, f, fake, ctx = world()

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(b"### Q1: db?\nRecommended: sqlite\nRuling: sqlite - simplest - low\n")
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC_BYTES)
    fake.on_prompt = on_prompt
    assert author.author_round(ctx) == "submitted"
    kinds = [x.kind for x in s.facts_for("r-1")]
    assert kinds.index("model_ruling") < kinds.index("artifact_submitted")


def test_an_empty_turn_retries_once_then_fails(world):
    s, f, fake, ctx = world()
    ctx.turn_timeout_s = 10
    fake.on_prompt = lambda sid, text: None
    assert author.author_round(ctx) == "empty_retry"
    empty = s.newest_fact("r-1", EventKind.AUTHOR_EMPTY)
    assert author.author_round(ctx) == "failed"
    create = front.newest_seat(s, "r-1", Role.AUTHOR, "spec", 0)
    assert create["cause"] == empty.id
    assert len(s.facts_of_kind("r-1", EventKind.AUTHOR_EMPTY)) == 1


def test_a_direction_read_at_the_end_of_the_turn_is_not_submitted(world):
    s, f, fake, ctx = world()

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC_BYTES)
        fake.add_user_message(sid, "actually, use postgres")
    fake.on_prompt = on_prompt
    assert author.author_round(ctx) == "direction"
    assert s.run_state("r-1") == "queued"
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED) is None
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["text"] == "actually, use postgres"
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert len(fake.sessions) == 2                                        # a fresh session


def test_identical_resubmission_reauthors_once_then_stalls(world):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    f.review_round("request_revision")
    assert author.author_round(ctx) == "reauthor"
    rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rej.payload["command_name"] == "submit_spec"
    assert author.author_round(ctx) == "stall"
    assert front.newest_seat(s, "r-1", Role.AUTHOR, "spec", 0)["cause"] == rej.id


def test_seats_exhausted_is_budget(world):
    s, f, fake, ctx = world(cfg={"max_seats": 4})
    for _ in range(2):
        f._dispatch(Role.AUTHOR, "claude"); f._dispatch(Role.REVIEWER, "codex")
    assert author.author_round(ctx) == "budget"
    assert s.newest_fact("r-1", EventKind.ATTEMPT_DISPATCHED).payload["role"] == "reviewer"
```

- [ ] **Step 4: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_seat.py tests/coordinator/test_author_round.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.seat'`.

- [ ] **Step 5: Implement `seat.py`**

```python
"""One waited turn of a seat (spec §3 Author round, Review round, *The turn's
end is a fact*). Shared by the author, the reviewer and the human pass."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from coordinator import sessions
from coordinator.session import list_items, wait_turn
from kernel import front
from kernel.canon import content_hash
from kernel.commands import Command, submit
from kernel.dispatch import dispatch

ARTIFACT_OUT = "bircher/artifact.md"
QUESTIONS_OUT = "bircher/questions.md"
REVIEW_OUT = "bircher/review.md"


@dataclass
class Turn:
    session_id: str
    generation: int
    workspace: str
    agent_name: str
    ended: str
    files: dict = field(default_factory=dict)
    cursor_before: str | None = None
    listing: list = field(default_factory=list)


_n = 0


def command(ctx, name: str, payload: dict):
    global _n
    _n += 1
    return submit(ctx.store, Command(
        name=name, run_id=ctx.run_id, expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=f"{name}:{ctx.run_id}:{ctx.generation}:{_n}", generation=ctx.generation,
        payload=payload))


def read_once(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _move_aside(workspace: str, names: list[str]) -> None:
    for name in names:
        p = os.path.join(workspace, name)
        if os.path.exists(p):
            n = 1
            while os.path.exists(f"{p}.{n}.prev"):
                n += 1
            os.replace(p, f"{p}.{n}.prev")


def _adopt_or_create(ctx, vendor: str, ob: dict, resume_session: str | None) -> dict:
    row = sessions.satisfied(ctx.store, ctx.run_id, ob)
    if row is not None:
        return json.loads(row["external_object_id"])
    if resume_session is not None:
        snap = json.loads(ctx.fetch(f"{ctx.server}/v1/sessions/{resume_session}"))
        return {k: snap.get(k) for k in ("id", "agent_id", "agent_name", "host_id", "workspace", "title")}
    path = sessions.worktree_path(ctx.workspaces_root, ctx.run_id, ctx.generation)
    workspace = sessions.add_worktree(ctx.repo_dir, ctx.store.run_base_sha(ctx.run_id), path)
    return sessions.create_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                   agent_id=ctx.agent_ids[vendor], host_id=ctx.host_id, workspace=workspace,
                                   phase=ob["phase"], epoch=ob["epoch"], cause=ob["cause"], env=ctx.effect_env())


def _record_prompt_item(ctx, session_id: str, prompt_hash: str) -> list[dict]:
    items = list_items(ctx.server, session_id, fetch=ctx.fetch)
    known = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, "prompt_item")}
    for it in items:
        if it["role"] == "user" and it["id"] not in known and content_hash(it["text"].encode()) == prompt_hash:
            command(ctx, "record_prompt_item", {"session_id": session_id, "item_id": it["id"], "sha256": prompt_hash})
            break
    return items


def run_turn(ctx, *, role: str, vendor: str, session_obligation: dict, prompt_cause: str,
             prompt_text: bytes, watched: list[str], read: list[str] | None = None,
             resume_session: str | None = None) -> Turn:
    """*watched* are the paths the poll ends the turn on; *read* (default the
    watched ones) are the paths read once after the stop -- under grill=model
    the questions file is read beside the artefact but never watched."""
    read = list(watched) if read is None else list(read)
    ctx.generation = dispatch(ctx.store, ctx.run_id, actor=vendor, role=role).generation
    snap = _adopt_or_create(ctx, vendor, session_obligation, resume_session)
    sid, workspace = snap["id"], snap["workspace"]
    phase, epoch = session_obligation["phase"], session_obligation["epoch"]
    prompt_ob = {"kind": "sess-prompt", "session": sid, "phase": phase, "epoch": epoch, "cause": prompt_cause}
    prompt_hash = content_hash(prompt_text)
    cursor_before = None
    if sessions.satisfied(ctx.store, ctx.run_id, prompt_ob) is None:
        _move_aside(workspace, watched)
        sessions.prompt_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                session_id=sid, phase=phase, epoch=epoch, cause=prompt_cause,
                                text=prompt_text, env=ctx.effect_env())
    listing = _record_prompt_item(ctx, sid, prompt_hash)
    prompt = front.newest_prompt(ctx.store, ctx.run_id, phase, epoch)
    deadline_us = prompt["at_us"] + ctx.turn_timeout_s * 1_000_000
    paths = [os.path.join(workspace, w) for w in watched]
    ended = front.turn_ended_for(ctx.store, ctx.run_id, prompt["key"])
    if ended is None:
        end = wait_turn(ctx.store, ctx.run_id, ctx.generation, ctx.server, sid, paths, deadline_us,
                        agent_id_expected=snap["agent_id"], fetch=ctx.fetch, clock=ctx.clock, sleep=ctx.sleep)
        command(ctx, "record_turn_ended", {"session": sid, "ended": end.ended})
        ended = front.turn_ended_for(ctx.store, ctx.run_id, prompt["key"])
    if not front.stop_satisfied_for(ctx.store, ctx.run_id, ended.id):
        sessions.stop_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                              session_id=sid, cause=ended.id, env=ctx.effect_env())
    files = {} if ended.payload["ended"] == "displaced" else {
        w: read_once(os.path.join(workspace, w)) for w in read}
    # The listing at the end of the turn (spec §4: every listing is
    # discriminated) -- a message typed during the turn is in this one.
    try:
        listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    except Exception:
        pass
    return Turn(session_id=sid, generation=ctx.generation, workspace=workspace, agent_name=snap["agent_name"],
                ended=ended.payload["ended"], files=files, cursor_before=cursor_before, listing=listing)
```

`_move_aside` runs only when the prompt is owed — a re-prompt of an adopted session moves the previous turn's file aside before the new turn can write; a completed prompt (crash window) moves nothing, since the turn it belongs to may already have written. A displaced turn reads nothing (`files == {}`).

- [ ] **Step 6: Implement `author.py`**

```python
"""The author round (spec §3 Author round, §3 loop, §7)."""
from __future__ import annotations

import json
import os
import pathlib
import re

from coordinator import phases, seat
from coordinator.human import take_listing            # Task 19; a stub until then, see Step 7
from coordinator.session import AgentMismatch
from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import PendingEffects, Role, SeatsExhausted
from kernel.events import EventKind
from kernel.policy import policy_of, to_payload

SKILLS = pathlib.Path(__file__).resolve().parents[2] / "skills"
_Q = re.compile(r"^### (Q[^:\s]+):\s*(.+?)\s*$", re.M)


def parse_questions(text: str) -> list[dict]:
    out = []
    blocks = list(_Q.finditer(text))
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        body = text[m.end():end]
        rec = re.search(r"^Recommended:\s*(.+?)\s*$", body, re.M)
        rul = re.search(r"^Ruling:\s*(.+?)\s*$", body, re.M)
        out.append({"id": m.group(1), "question": m.group(2),
                    "recommended": rec.group(1) if rec else "", "ruling": rul.group(1) if rul else None})
    return out


def choose_author_vendor(ctx) -> str:
    """spec §3 Rotation: the reviewer of round r authors round r+1; a phase's
    first artefact is authored by the vendor that did not review the previous
    phase's last accepted artefact (the default for the spec)."""
    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    seat_row = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
    if seat_row is not None:
        return front.vendor_of(seat_row["session"]["agent_name"])   # the round's vendor is fixed (ruling 14)
    verdicts = [v for v in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT)
                if v.payload.get("ruling") == "review_ruling"]
    same_phase = [v for v in verdicts if v.payload["phase"] == phase and v.payload["epoch"] == n]
    if same_phase:
        return same_phase[-1].payload["reviewer_identity"]
    if phase == "plan":
        spec_accepts = [v for v in verdicts if v.payload["phase"] == "spec" and v.payload["verdict"] == "accept"]
        if spec_accepts:
            vendors = sorted(ctx.agent_ids)
            other = [x for x in vendors if x != spec_accepts[-1].payload["reviewer_identity"]]
            return other[0] if other else vendors[0]
    return ctx.default_author


def _findings_for(ctx) -> bytes:
    cause = phases.round_cause(ctx)
    store = ctx.store
    if cause.kind == EventKind.REVIEW_VERDICT and cause.payload.get("findings_hash"):
        return store.read_blob(cause.payload["findings_hash"])
    if cause.kind == EventKind.HUMAN_DIRECTION:
        return cause.payload["text"].encode()
    if cause.kind == EventKind.BUNDLE_REVISED:
        return json.dumps(cause.payload["diff"], indent=1).encode()
    if cause.kind == EventKind.COMMAND_REJECTED:
        return cause.payload.get("detail", "").encode()
    return b""


def author_brief(ctx, *, phase: str, resume_answer=None) -> bytes:
    store, run_id = ctx.store, ctx.run_id
    if resume_answer is not None:
        return ("Answered; continue.\n\n" + resume_answer.payload["answer"]).encode()
    parts = [f"# Bircher {phase} author brief\n"]
    parts.append((SKILLS / f"{phase}-author" / "SKILL.md").read_text())
    parts.append("\n## Files\n\nArtefact: `%s`\nQuestions: `%s`\n" % (seat.ARTIFACT_OUT, seat.QUESTIONS_OUT))
    parts.append("\n## Policy\n\n```json\n%s\n```\n" % json.dumps(to_payload(policy_of(store, run_id)), indent=1))
    parts.append("\n## Issue snapshot\n\n```json\n%s\n```\n" % store.read_blob(front.bundle_hash(store, run_id)).decode())
    if phase == "plan":
        parts.append("\n## The accepted spec\n\n%s\n" % store.read_blob(store.phase_artifact(run_id, "spec")).decode())
    prior = front.newest_submission(store, run_id, phase, ctx.epoch())
    findings = _findings_for(ctx)
    if prior is not None and findings:
        parts.append("\n## Your previous draft\n\n%s\n" % store.read_blob(prior.payload["hash"]).decode())
    if findings:
        parts.append("\n## Findings to address\n\n%s\n" % findings.decode("utf-8", "replace"))
    return "".join(parts).encode()


def _copy(ctx, phase: str, rnd: int, data: bytes) -> None:
    d = pathlib.Path(ctx.bundle_dir) / ctx.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{phase}-r{rnd}.md").write_bytes(data)


def author_round(ctx) -> str:
    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    grill = policy_of(store, run_id).grill
    cause = phases.round_cause(ctx)
    answer = phases.answer_to_resume(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": n, "cause": cause.id}
    resume = None
    if answer is not None:
        prior_seat = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
        resume = prior_seat["session"]["id"] if prior_seat else None
    vendor = choose_author_vendor(ctx)
    watched = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human" else [seat.ARTIFACT_OUT]
    try:
        turn = seat.run_turn(ctx, role=Role.AUTHOR, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=(answer.id if answer is not None else cause.id),
                             prompt_text=author_brief(ctx, phase=phase, resume_answer=answer),
                             watched=watched, read=[seat.ARTIFACT_OUT, seat.QUESTIONS_OUT],
                             resume_session=resume)
    except SeatsExhausted:
        return "budget"
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return "failed"
    if turn.ended == "displaced":
        return "direction"
    # The human first: a message in the session is taken before anything is submitted.
    taken = take_listing(ctx, turn.session_id, turn.listing)
    if taken == "direction":
        return "direction"
    questions = turn.files.get(seat.QUESTIONS_OUT)
    artefact = turn.files.get(seat.ARTIFACT_OUT)
    if questions:
        for q in parse_questions(questions.decode("utf-8", "replace")):
            seat.command(ctx, "record_model_question", {"question_id": q["id"], "question": q["question"]})
            if q["ruling"]:
                bits = [b.strip() for b in q["ruling"].split("—")] + ["", ""]
                seat.command(ctx, "record_model_ruling", {"question_id": q["id"], "ruling": bits[0] or q["ruling"],
                                                          "reasoning": bits[1] or "-", "cost_if_wrong": bits[2] or "-"})
        if artefact is None:
            return "questions"
    if artefact is None:
        try:
            seat.command(ctx, "record_author_empty", {"session": turn.session_id})
        except NotAuthorized:
            return "failed"
        return "empty_retry"
    h = put_artifact(store, artefact)
    rnd = len(front.submissions(store, run_id, phase, n)) + 1
    _copy(ctx, phase, rnd, artefact)
    name = "submit_spec" if phase == "spec" else "submit_plan"
    try:
        seat.command(ctx, name, {"artifact_hash": h})
    except NotAuthorized as exc:
        msg = str(exc)
        if "human_direction" in msg:
            return "direction"
        if "identical" in msg or "### Task" in msg or "current spec" in msg:
            seat_row = front.newest_seat(store, run_id, Role.AUTHOR, phase, n)
            cause_kind = next((x.kind for x in store.facts_for(run_id) if x.id == seat_row["cause"]), None)
            return "stall" if cause_kind == EventKind.COMMAND_REJECTED else "reauthor"
        ctx.log(f"unexpected refusal: {msg}")
        return "failed"
    return "submitted"
```

The `—` split in the ruling line: the skill tells the author to write `Ruling: <decision> — <reasoning> — <cost>`; a ruling written without the separators lands whole in `ruling` with `-` for the other two — recorded, not refused, because a ruling is a model's word and the shape is advice.

- [ ] **Step 7: `take_listing` until Task 19**

`author.py` imports `take_listing` from `coordinator.human`, which Task 19 writes. For this task create `v2/coordinator/human.py` with only:

```python
"""Human interaction (spec §4). Task 19 fills this in."""


def take_listing(ctx, session_id: str, listing: list) -> str | None:
    """Discriminate a listing: every unread user-role item that is not one of
    the coordinator's own prompts is recorded under the batch rules. Returns
    'direction', 'ruling', 'answer' or None. Task 19 implements the rules;
    until then nothing human is taken."""
    from kernel import front
    from kernel.canon import content_hash
    known = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, "prompt_item")}
    hashes = front.prompt_hashes_of(ctx.store, ctx.run_id, session_id)
    human = [it for it in listing if it["role"] == "user" and it["id"] not in known
             and content_hash(it["text"].encode()) not in hashes]
    if not human:
        return None
    from coordinator import seat
    text = "\n\n".join(it["text"].strip() for it in human)
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    execute_as_human(ctx.store, Command(
        name="record_human_direction", run_id=ctx.run_id,
        expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=f"direction:{ctx.run_id}:{human[-1]['id']}", generation=HUMAN_GENERATION,
        payload={"text": text, "cursor_item_id": listing[-1]["id"]}))
    return "direction"
```

This is the `queued`/`specified` rule of §4 (a message read at the end of an artefact turn is a direction) and is what `test_a_direction_read_at_the_end_of_the_turn_is_not_submitted` exercises; Task 19 replaces the body with the full batch rules and keeps the signature.

- [ ] **Step 8: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_seat.py tests/coordinator/test_author_round.py -q`
Expected: PASS. Full suite: green.

- [ ] **Step 9: Mutation check, then commit**

Read the file before the stop in `run_turn` (move the `files =` line above the `stop_session` call): `test_read_after_stop_partial_bytes_hashed` fails (`b"half"`). In `choose_author_vendor`, return the seat's `agent_name` without `vendor_of`: `test_the_brief_carries_findings_on_a_revision_and_the_spec_for_a_plan` fails at the kernel's identity guard on its second round. Skip `_move_aside`: `test_earlier_turn_files_are_moved_aside_before_a_reprompt` fails. Submit before `take_listing`: `test_a_direction_read_at_the_end_of_the_turn_is_not_submitted` fails. Restore all three.

```bash
git add v2/coordinator/seat.py v2/coordinator/author.py v2/coordinator/human.py skills/spec-author/SKILL.md skills/plan-author/SKILL.md v2/tests/coordinator/test_seat.py v2/tests/coordinator/test_author_round.py
git commit -m "feat(coordinator): the seat's waited turn and the author round"
```

---
### Task 18: The review round — `review.py` in artefact mode

**Files:**
- Modify: `v2/coordinator/review.py` (`extract_verdict(text, hash8=None)`, `review_round`, `choose_reviewer_vendor`)
- Test: `v2/tests/coordinator/test_review_round.py`

**Interfaces:**
- Consumes: `seat.run_turn`, `seat.command`, `seat.REVIEW_OUT` (Task 17); `phases.seat_cause` (Task 16); `front.brief_for`, `front.rounds_used`, `front.round_bound` (Tasks 11-12); `brief.hash8`.
- Produces:
  - `review.extract_verdict(text: str, hash8: str | None = None) -> str | None` — with `hash8` the last non-blank line must be `VERDICT: PASS <hash8>` or `VERDICT: FAIL <hash8>` (decoration tolerated as today); another hash, no hash, or a verdict followed by findings is `None`. Without `hash8` the PR-review grammar is unchanged.
  - `review.ReviewOutcome` (dataclass): `verdict: str | None`, `findings_hash: str | None`, `reviewer: str`, `recorded: bool`, `rounds_left: int`, `session_id: str | None`, `cursor: str | None`, `status: str` ∈ `{"recorded", "no_verdict", "bound_exhausted", "budget", "failed", "no_brief"}`.
  - `review.review_round(ctx) -> ReviewOutcome` — dispatches the reviewer (`choose_reviewer_vendor`: the vendor that did not author the artefact under review), `issue_review_brief`, reads the brief bytes by the fact's `brief_hash`, runs the seat's turn with the brief as the prompt and `REVIEW_OUT` watched, PUTs the file's bytes as the findings artefact, extracts the verdict against the artefact's `hash8`, and records `record_review(accept)` or, with rounds left, `record_review(request_revision)`; returns the outcome for the loop to park on.
  - `review.choose_reviewer_vendor(ctx) -> str`.

- [ ] **Step 1: Write the failing tests**

`v2/tests/coordinator/test_review_round.py`:

```python
import os
import time

import pytest

from coordinator import author, phases, review, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.mark.parametrize("text,hash8,expected", [
    ("findings\n\nVERDICT: PASS deadbeef\n", "deadbeef", "PASS"),
    ("findings\n**VERDICT: FAIL deadbeef**\n", "deadbeef", "FAIL"),
    ("VERDICT: PASS deadbeef\nmore findings after\n", "deadbeef", None),
    ("findings\nVERDICT: PASS cafebabe\n", "deadbeef", None),
    ("findings\nVERDICT: PASS\n", "deadbeef", None),
    ("", "deadbeef", None),
    ("VERDICT: PASS", None, "PASS"),                    # the PR grammar, unchanged
    ("VERDICT: PASS deadbeef", None, None),
])
def test_extract_verdict_requires_the_hash_in_artefact_mode(text, hash8, expected):
    assert review.extract_verdict(text, hash8) == expected


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", project_config=cfg)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _reviewer_writes(fake, text):
    def on_prompt(sid, prompt):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.REVIEW_OUT), "w").write(text)
    fake.on_prompt = on_prompt


def test_a_pass_records_accept_and_the_brief_is_the_first_prompt(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)                         # author claude
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"looks fine\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.verdict == "PASS" and out.reviewer == "codex"
    assert s.run_state("r-1") == "specified"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    assert v.payload["findings_hash"] == content_hash(f"looks fine\n\nVERDICT: PASS {h[:8]}\n".encode())
    sid = list(fake.sessions)[-1]
    first = fake.sessions[sid]["items"][0]["content"][0]["text"]
    brief = front.brief_for(s, "r-1", v.payload["generation"]).payload
    assert content_hash(first.encode()) == brief["brief_hash"]
    create = s.effect_by_key(f"sess-create:r-1:{v.payload['generation']}", run_id="r-1")
    assert create["intent"]["obligation"]["cause"] == front.newest_submission(s, "r-1", "spec", 0).id


def test_a_fail_with_rounds_left_records_request_revision(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"1. no threat model\n\nVERDICT: FAIL {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.verdict == "FAIL"
    assert s.run_state("r-1") == "queued"
    assert s.newest_fact("r-1", EventKind.REVIEW_VERDICT).payload["verdict"] == "request_revision"


def test_a_fail_with_no_rounds_left_is_bound_exhausted_and_records_nothing(world):
    s, f, fake, ctx = world(cfg={"max_rounds": 1})
    f.author_round(SPEC_BYTES); f.review_round("request_revision")
    f.author_round(SPEC_BYTES + b"2")
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"still wrong\n\nVERDICT: FAIL {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "bound_exhausted" and out.verdict == "FAIL" and out.rounds_left == 0
    assert out.findings_hash == content_hash(f"still wrong\n\nVERDICT: FAIL {h[:8]}\n".encode())
    assert s.run_state("r-1") == "spec_submitted"
    assert len([v for v in s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)]) == 1


def test_no_verdict_shapes(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    for text in (f"VERDICT: PASS {h[:8]}\nthen findings\n", "VERDICT: PASS cafebabe\n", "no verdict at all\n"):
        _reviewer_writes(fake, text)
        out = review.review_round(ctx)
        assert out.status == "no_verdict" and out.verdict is None
        assert out.findings_hash == content_hash(text.encode())
    ctx.turn_timeout_s = 10
    fake.on_prompt = lambda sid, prompt: None
    out = review.review_round(ctx)
    assert out.status == "no_verdict" and out.findings_hash is None
    assert s.run_state("r-1") == "spec_submitted"


def test_the_seat_after_a_grant_has_the_grant_as_cause(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    f.grant()
    grant = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    h = s.phase_artifact("r-1", "spec")
    _reviewer_writes(fake, f"ok\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded"
    create = s.effect_by_key(f"sess-create:r-1:{out.generation}", run_id="r-1")
    assert create["intent"]["obligation"]["cause"] == grant.id


def test_a_dead_pass_between_create_and_prompt_is_adopted_with_a_fresh_brief(world):
    s, f, fake, ctx = world()
    f.author_round(SPEC_BYTES)
    h = s.phase_artifact("r-1", "spec")
    cause = phases.seat_cause(ctx).id
    g1 = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g1, "issue_review_brief", {"phase": "spec"})
    from coordinator import sessions
    ctx.generation = g1
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g1)); os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g1, server="http://srv", agent_id="ag_codex",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=cause, env=ctx.effect_env())
    _reviewer_writes(fake, f"ok\n\nVERDICT: PASS {h[:8]}\n")
    out = review.review_round(ctx)
    assert out.status == "recorded" and out.session_id == snap["id"]
    assert len(fake.sessions) == 1
    briefs = s.facts_of_kind("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    assert len(briefs) == 2 and briefs[0].payload["brief_hash"] == briefs[1].payload["brief_hash"]


def test_reviewer_is_the_other_vendor(world):
    s, f, fake, ctx = world()
    ctx.default_author = "codex"
    f.author = "codex"
    f.author_round(SPEC_BYTES)
    assert review.choose_reviewer_vendor(ctx) == "claude"
```

- [ ] **Step 2: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_review_round.py -q`
Expected: FAIL — `extract_verdict() takes 1 positional argument but 2 were given`; `review_round` missing.

- [ ] **Step 3: Implement**

In `v2/coordinator/review.py`, extend `extract_verdict`:

```python
def extract_verdict(text: str, hash8: str | None = None) -> str | None:
    ... (the existing trimming of the last non-blank line into `last`) ...
    if hash8 is None:
        if last == "VERDICT: PASS":
            return "PASS"
        if last == "VERDICT: FAIL":
            return "FAIL"
        return None
    # Artefact mode: the verdict names what it reviewed (spec §3 Review round).
    if last == f"VERDICT: PASS {hash8}":
        return "PASS"
    if last == f"VERDICT: FAIL {hash8}":
        return "FAIL"
    return None
```

and add:

```python
from dataclasses import dataclass


@dataclass
class ReviewOutcome:
    status: str
    verdict: str | None = None
    findings_hash: str | None = None
    reviewer: str = ""
    recorded: bool = False
    rounds_left: int = 0
    session_id: str | None = None
    cursor: str | None = None
    generation: int | None = None


def choose_reviewer_vendor(ctx) -> str:
    """spec §3 Rotation: the vendor that did not author the artefact under review."""
    from kernel import front
    sub = front.newest_submission(ctx.store, ctx.run_id, ctx.phase(), ctx.epoch())
    author_vendor = sub.payload["author"]
    others = [v for v in sorted(ctx.agent_ids) if v != author_vendor]
    return others[0] if others else author_vendor


def review_round(ctx) -> ReviewOutcome:
    from coordinator import phases, seat
    from coordinator.session import AgentMismatch
    from kernel import front
    from kernel.artifacts import put_artifact
    from kernel.authz import NotAuthorized
    from kernel.brief import hash8
    from kernel.dispatch import Role, SeatsExhausted, dispatch

    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    cause = phases.seat_cause(ctx)
    vendor = choose_reviewer_vendor(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": n, "cause": cause.id}
    # The seat is dispatched here, before the brief, so the brief's generation
    # is the seat's; run_turn re-uses ctx.generation when it is the newest.
    try:
        ctx.generation = dispatch(store, run_id, actor=vendor, role=Role.REVIEWER).generation
    except SeatsExhausted:
        return ReviewOutcome("budget", reviewer=vendor)
    try:
        seat.command(ctx, "issue_review_brief", {"phase": phase})
    except NotAuthorized as exc:
        ctx.log(f"brief refused: {exc}")
        return ReviewOutcome("no_brief", reviewer=vendor, generation=ctx.generation)
    issued = front.brief_for(store, run_id, ctx.generation).payload
    brief_bytes = store.read_blob(issued["brief_hash"])
    try:
        turn = seat.run_turn(ctx, role=Role.REVIEWER, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=cause.id, prompt_text=brief_bytes, watched=[seat.REVIEW_OUT],
                             reuse_generation=True)
    except SeatsExhausted:
        return ReviewOutcome("budget", reviewer=vendor)
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return ReviewOutcome("failed", reviewer=vendor)
    data = turn.files.get(seat.REVIEW_OUT)
    cursor = turn.listing[-1]["id"] if turn.listing else None
    if data is None:
        return ReviewOutcome("no_verdict", reviewer=vendor, session_id=turn.session_id, cursor=cursor,
                             generation=turn.generation)
    findings_hash = put_artifact(store, data)
    verdict = extract_verdict(data.decode("utf-8", "replace"), hash8(issued["artifact_hash"]))
    left = front.round_bound(store, run_id, phase, n) - front.rounds_used(store, run_id, phase, n)
    base = dict(reviewer=vendor, findings_hash=findings_hash, session_id=turn.session_id, cursor=cursor,
                generation=turn.generation, rounds_left=left, verdict=verdict)
    if verdict is None:
        return ReviewOutcome("no_verdict", **base)
    if verdict == "FAIL" and left <= 0:
        return ReviewOutcome("bound_exhausted", **base)
    seat.command(ctx, "record_review", {
        "phase": phase, "verdict": "accept" if verdict == "PASS" else "request_revision",
        "artifact_hash": issued["artifact_hash"], "base_sha": issued["base_sha"],
        "context_bundle_hash": issued["context_bundle_hash"], "policy_version": issued["policy_version"],
        "findings_hash": findings_hash,
    })
    return ReviewOutcome("recorded", recorded=True, **base)
```

`run_turn` gains `reuse_generation: bool = False`: when true and `ctx.generation` is the run's current generation, it does not dispatch again (the reviewer's seat was dispatched before the brief, and the brief must be under the seat's generation — spec §2 *Brief*). Add to `seat.run_turn`'s first lines:

```python
    from kernel.ownership import current_generation
    if not (reuse_generation and ctx.generation == current_generation(ctx.store, ctx.run_id)):
        ctx.generation = dispatch(ctx.store, ctx.run_id, actor=vendor, role=role).generation
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_review_round.py tests/coordinator/test_ci_and_review.py -q`
Expected: PASS (the PR-review grammar tests in `test_ci_and_review.py` are unchanged). Full suite: green.

- [ ] **Step 5: Mutation check, then commit**

Accept a verdict without the hash in artefact mode: `test_extract_verdict_requires_the_hash_in_artefact_mode` fails on its fifth row. Record `request_revision` when `left <= 0`: `test_a_fail_with_no_rounds_left_is_bound_exhausted_and_records_nothing` fails (the kernel refuses it under `max_rounds`, and the outcome is an exception, not `bound_exhausted`). Restore.

```bash
git add v2/coordinator/review.py v2/coordinator/seat.py v2/tests/coordinator/test_review_round.py
git commit -m "feat(coordinator): review round in artefact mode; verdicts name their hash"
```

---

### Task 19: The human pass, the loop, and the CLI

**Files:**
- Modify: `v2/coordinator/human.py` (`cursor`, `unread_human_items`, `classify_batch`, `take_listing`, `human_pass`, `reply_refusals`)
- Modify: `v2/coordinator/phases.py` (`stall`, `run_loop`, `retire`, `Exit`)
- Modify: `v2/coordinator/cli.py` (`phases`, `retire`, `approve`, `grant-round`, `revise`, `direct`, `cancel`, `parked`)
- Test: `v2/tests/coordinator/test_human.py`, `v2/tests/coordinator/test_phase_loop.py`, `v2/tests/coordinator/test_front_cli.py`

**Interfaces:**
- Consumes: everything above; `execute_as_human`, `HUMAN_GENERATION` (Task 8); `bundle.is_relevant_change`, `revise_bundle` command (Task 9).
- Produces:
  - `human.cursor(store, run_id, session_id, listing) -> str | None` — the `cursor_item_id` of the run's newest fact carrying one, else the session's first item id, else `None`.
  - `human.unread_human_items(ctx, session_id, listing) -> list[dict]` — every user-role item after the cursor that is not one of the coordinator's prompts (excluded by `prompt_item` id, or by content hash where a satisfied prompt effect's `body.artifact` matches and no `prompt_item` names it).
  - `human.classify_batch(items, *, state, grill_open: bool) -> tuple[str, str]` — `("answer", text)` when a `model_question` of the epoch is newer than the last `human_answer` (`grill_open`); else at `*_submitted`/`*_accepted`: `("approve", "")` when the batch is exactly one message equal, trimmed and case-folded, to `approve`; `("retry", "")` for `retry`; else `("revision", text)`; at `queued`/`specified`: `("retry", "")` for the single token, else `("direction", text)`. `text` is the messages' texts joined by two newlines, each trimmed.
  - `human.take_listing(ctx, session_id, listing) -> str | None` — records the batch: `record_human_answer` (`question_ids` = the epoch's questions newer than its last answer), `approve_artifact(hash)`, `grant_round`, human `record_review(request_revision, findings=text)`, or `record_human_direction`; every fact carries `cursor_item_id = listing[-1]["id"]`. A refusal (`NotAuthorized`) is dismissed: `dismiss_human_item(cursor, rejection)` under `ctx.generation`, and the reply is owed (below). Returns the kind taken, or `None`.
  - `human.reply_refusals(ctx) -> list[str]` — for every `human_item_dismissed` whose reply obligation `{sess-prompt, session, phase, epoch, cause: <rejection id>}` is unsatisfied and whose session the server lists, sends the refusal's `detail` as a prompt once and records its `prompt_item`.
  - `human.human_pass(ctx, park) -> str` — lists the park's session (the confirmed carrier `sess-create` whose cause is the park, else `park.session_id`); takes any human message (`take_listing`); if none: sends the owed prompts — the park's own (cause the `parked` fact; text per reason: a grill re-lists nothing, a gate `"…Reply with the single word approve, or give corrections."`, a stall `"…Reply with the single word retry, or give corrections."` with the artefact and the findings) and every owed refusal reply — confirms each, records `prompt_item`, discriminates the confirm listing; returns `"taken"` when a human fact was recorded, else `"parked"`.
  - `phases.stall(ctx, reason, *, session_id, findings_hash, verdict, reviewer) -> str` — lists the author session and takes a human message (no park then), else records `park` with the listing's cursor and calls `human_pass`.
  - `phases.Exit` (`OK = 0`, `FAILED = 1`, `USAGE = 2`, `PARKED = 4`); `phases.run_loop(ctx) -> int` — the §3 loop verbatim: `retire_owed`; `publish_owed`; state; current park → `human_pass`; `queued`/`specified` → `author_round` and its outcomes (`questions` → `stall("grill")`, `budget` → `stall("budget_exhausted")`, `stall` → `stall("identical_resubmission")`, `failed` → `Exit.FAILED`, others → next iteration); `*_submitted` → `review_round` (`no_verdict` → `stall("no_verdict")`, `bound_exhausted` → `stall(...)`, `budget` → `stall("budget_exhausted")`, `no_brief` → `Exit.FAILED`, `failed` → `Exit.FAILED`); `*_accepted` → `stall("gate")`; `planned` → `Exit.OK`; any other state → `Exit.OK`; `PendingEffects`/`UncertainEffect` → `Exit.FAILED` after logging the keys; `WorktreeExists`/`AgentMismatch` → `Exit.FAILED`.
  - `phases.retire(ctx) -> list[str]` — for every satisfied coordinator `sess-create` with no satisfied `sess-stop` naming its session, a stop with the `cancelled` fact (the run's newest `transition_performed {to: cancelled}`) as cause; raises on a halted run after naming the sessions.
  - CLI (all take `--db`, `--run-id`, and where they act on a server `--server`): `phases --turn-timeout N --repo o/r --issue N --repo-dir P --workspaces-root P --bundle-dir P --host-id H --agent-claude ID --agent-codex ID [--default-author claude]` (a missing or non-integer `--turn-timeout` is `RC_USAGE` before any effect); `retire`; `approve --phase spec|plan`; `grant-round`; `revise --phase --findings FILE`; `direct --phase --text FILE`; `cancel` (records `cancel_run` through `execute_as_human`, then `retire`); `parked` (prints the current park as JSON or nothing, rc 0/1). `RC_PARKED = 4`, `RC_FAILED = 1`.

- [ ] **Step 1: Write the human tests**

`v2/tests/coordinator/test_human.py`:

```python
import os
import time

import pytest

from coordinator import human, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",)):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _session_with_prompt(s, f, fake, ctx, text=b"the brief"):
    """An author session the coordinator prompted, with its prompt_item."""
    g = f._dispatch(Role.AUTHOR, "claude")
    ctx.generation = g
    from coordinator import sessions
    ws = os.path.join(ctx.workspaces_root, "r-1", str(g)); os.makedirs(ws)
    snap = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv", agent_id="ag_claude",
                                   host_id="host_h", workspace=os.path.realpath(ws), phase="spec", epoch=0,
                                   cause=phases.round_cause(ctx).id, env=ctx.effect_env())
    sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id=snap["id"],
                            phase="spec", epoch=0, cause=phases.round_cause(ctx).id, text=text, env=ctx.effect_env())
    item = fake.sessions[snap["id"]]["items"][-1]["id"]
    f._cmd(g, "record_prompt_item", {"session_id": snap["id"], "item_id": item,
                                     "sha256": __import__("kernel.canon", fromlist=["content_hash"]).content_hash(text)})
    return snap["id"], g


@pytest.mark.parametrize("batch,state,expected", [
    (["approve"], "spec_accepted", ("approve", "")),
    ([" Approve "], "spec_accepted", ("approve", "")),
    (["approve?"], "spec_accepted", ("revision", "approve?")),
    (["approve."], "spec_accepted", ("revision", "approve.")),
    (["approve", "but fix the title"], "spec_accepted", ("revision", "approve\n\nbut fix the title")),
    (["retry"], "spec_submitted", ("retry", "")),
    (["retry"], "queued", ("retry", "")),
    (["use sqlite"], "queued", ("direction", "use sqlite")),
    (["no", "and also no"], "plan_submitted", ("revision", "no\n\nand also no")),
])
def test_classify_batch(batch, state, expected):
    items = [{"id": f"i{n}", "role": "user", "text": t} for n, t in enumerate(batch)]
    assert human.classify_batch(items, state=state, grill_open=False) == expected


def test_a_batch_while_questions_are_open_is_one_answer():
    items = [{"id": "i1", "role": "user", "text": "sqlite"}, {"id": "i2", "role": "user", "text": "and 8080"}]
    assert human.classify_batch(items, state="queued", grill_open=True) == ("answer", "sqlite\n\nand 8080")


def test_the_discriminator_excludes_the_coordinators_own_prompts(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_assistant_text(sid, "working...")
    h1 = fake.add_user_message(sid, "actually use postgres")
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    unread = human.unread_human_items(ctx, sid, listing)
    assert [it["id"] for it in unread] == [h1]
    # Crash window: a second prompt confirmed with no prompt_item -- excluded by hash.
    from coordinator import sessions
    f.answer("x")
    ans = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    g2 = f._dispatch(Role.AUTHOR, "claude"); ctx.generation = g2
    sessions.prompt_session(s, run_id="r-1", generation=g2, server="http://srv", session_id=sid, phase="spec",
                            epoch=0, cause=ans.id, text=b"Answered; continue.", env=ctx.effect_env())
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    assert [it["id"] for it in human.unread_human_items(ctx, sid, listing)] == [h1]


def test_cursor_is_the_newest_facts_cursor_else_the_first_item(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    listing = __import__("coordinator.session", fromlist=["list_items"]).list_items("http://srv", sid, fetch=fake.fetch)
    assert human.cursor(s, "r-1", sid, listing) == listing[0]["id"]
    f.direct("x", cursor="i-42")
    assert human.cursor(s, "r-1", sid, listing) == "i-42"


def test_take_listing_records_by_state_with_the_listings_cursor(world):
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "use sqlite")
    last = fake.add_assistant_text(sid, "ok")
    from coordinator.session import list_items
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) == "direction"
    d = s.newest_fact("r-1", EventKind.HUMAN_DIRECTION)
    assert d.payload == {"phase": "spec", "epoch": 0, "text": "use sqlite", "cursor_item_id": last}
    # The same listing again: nothing unread, nothing recorded.
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) is None
    assert len(s.facts_of_kind("r-1", EventKind.HUMAN_DIRECTION)) == 1


def test_a_refused_approve_is_dismissed_and_answered_once(world):
    s, f, fake, ctx = world(labels=())
    f.author_round(SPEC_BYTES)                                   # spec_submitted: approve is illegal here
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "approve")
    from coordinator.session import list_items
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) == "dismissed"
    rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rej.actor == "human"
    d = s.newest_fact("r-1", EventKind.HUMAN_ITEM_DISMISSED)
    assert d.payload["rejection"] == rej.id
    sent = human.reply_refusals(ctx)
    assert len(sent) == 1
    reply = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert "not legal from state 'spec_submitted'" in reply
    assert human.reply_refusals(ctx) == []                         # once
    # A second pass over the same listing sends nothing and records nothing.
    n = len(s.facts_for("r-1"))
    assert human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch)) is None
    assert len(s.facts_for("r-1")) == n


def test_a_dismissed_reply_is_owed_across_a_later_human_fact(world):
    s, f, fake, ctx = world(labels=())
    f.author_round(SPEC_BYTES)
    sid, g = _session_with_prompt(s, f, fake, ctx)
    fake.add_user_message(sid, "approve")
    from coordinator.session import list_items
    human.take_listing(ctx, sid, list_items("http://srv", sid, fetch=fake.fetch))
    f.human("record_review", {"phase": "spec", "artifact_hash": s.phase_artifact("r-1", "spec"),
                              "verdict": "request_revision", "findings": "no"})      # a newer human fact
    assert len(human.reply_refusals(ctx)) == 1


def test_human_pass_at_a_gate_prompts_once_and_takes_the_approval(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    assert s.run_state("r-1") == "spec_accepted"
    from coordinator.session import list_items
    listing = list_items("http://srv", sid, fetch=fake.fetch)
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "parked"
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "gate" and park.payload["cursor_item_id"] == listing[-1]["id"]
    prompt = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert "single word `approve`" in prompt and SPEC_BYTES.decode() in prompt
    row = s.effect_by_key(f"sess-prompt:{sid}:{ctx.generation}:{park.id}", run_id="r-1")
    assert row["state"] == "confirmed"
    # The next pass: nothing human -> parked again, nothing re-sent.
    n_items = len(fake.sessions[sid]["items"])
    assert human.human_pass(ctx, park) == "parked"
    assert len(fake.sessions[sid]["items"]) == n_items
    # The human approves.
    fake.add_user_message(sid, "approve")
    assert human.human_pass(ctx, park) == "taken"
    assert s.run_state("r-1") == "specified"
    assert front.current_park(s, "r-1") is None


def test_a_message_in_the_confirm_listing_consumes_the_park(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.on_prompt = lambda sid_, text: fake.add_user_message(sid_, "approve")   # typed between list and prompt
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "taken"
    assert s.run_state("r-1") == "specified"
    park = s.newest_fact("r-1", EventKind.PARKED)
    ruling = s.newest_fact("r-1", EventKind.HUMAN_RULING)
    assert ruling.seq > park.seq and front.current_park(s, "r-1") is None


def test_a_human_message_before_the_park_means_no_park(world):
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.add_user_message(sid, "approve")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None) == "taken"
    assert s.newest_fact("r-1", EventKind.PARKED) is None
    assert s.run_state("r-1") == "specified"


def test_no_fact_carries_a_cursor_past_an_unrecorded_human_item(world):
    """Asserted over every fact the loop wrote, against the fake's full list."""
    s, f, fake, ctx = world(labels=())
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    fake.add_user_message(sid, "first")
    fake.add_user_message(sid, "second")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    phases.stall(ctx, "gate", session_id=sid, findings_hash=None, verdict=None, reviewer=None)
    items = fake.sessions[sid]["items"]
    order = {it["id"]: n for n, it in enumerate(items)}
    recorded_text = " ".join(x.payload.get("text", "") + x.payload.get("findings", "") + x.payload.get("answer", "")
                             for x in s.facts_for("r-1"))
    for x in s.facts_for("r-1"):
        c = x.payload.get("cursor_item_id")
        if not c:
            continue
        for it in items:
            if it["role"] == "user" and order[it["id"]] <= order[c] and it["text"] not in ("the brief",):
                assert it["text"] in recorded_text or any(
                    p.payload["item_id"] == it["id"] for p in s.facts_of_kind("r-1", EventKind.PROMPT_ITEM)), it


def test_grill_park_with_a_carrier_session(world):
    """budget_exhausted at the plan's first dispatch: the spec author's session carries the prompt."""
    s, f, fake, ctx = world()
    sid, g = _session_with_prompt(s, f, fake, ctx)
    f.author_round(SPEC_BYTES, resume=sid); f.review_round("accept")
    ctx.generation = f._dispatch(Role.OPERATOR, "runner")
    assert phases.stall(ctx, "budget_exhausted", session_id=None, findings_hash=None, verdict=None,
                        reviewer=None) == "parked"
    park = front.current_park(s, "r-1")
    assert park.payload["session_id"] == sid                       # the run's most recent session
    assert "single word `retry`" in fake.sessions[sid]["items"][-1]["content"][0]["text"]
```

- [ ] **Step 2: Write the loop tests**

`v2/tests/coordinator/test_phase_loop.py`:

```python
import os
import time

import pytest

from coordinator import phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front

PLAN = b"# Plan\n\n### Task 1: do it\n"
SPEC = b"# Spec\n\nDo it.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None, issue=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels, project_config=cfg, issue=issue)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


class Script:
    """What each session writes when prompted, keyed by the prompt's first line."""
    def __init__(self, fake, store):
        self.fake, self.store = fake, store

    def __call__(self, sid, text):
        ws = self.fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        name = self.fake.sessions[sid]["agent_name"]
        if text.startswith("# Bircher spec author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC + name.encode())
        elif text.startswith("# Bircher plan author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(PLAN + name.encode())
        elif text.startswith("# Bircher review brief"):
            phase = "spec" if "of a spec" in text else "plan"
            h = self.store.phase_artifact("r-1", phase)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"fine\n\nVERDICT: PASS {h[:8]}\n")


def _posts(fake):
    return [p for p, _ in fake.posts]


def test_model_no_gates_reaches_planned_untouched(world, monkeypatch):
    s, f, fake, ctx = world()
    fake.on_prompt = Script(fake, s)
    gh = []
    import kernel.cli
    real = kernel.cli.subprocess.run
    def run(argv, **kw):
        if argv[0] == "gh":
            gh.append(argv)
            class R: returncode, stdout, stderr = 0, "url", ""
            return R()
        return real(argv, **kw)
    monkeypatch.setattr("kernel.cli.subprocess.run", run)
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("r-1") == "planned"
    kinds = [x.kind for x in s.facts_for("r-1")]
    assert "human_answer" not in kinds and "human_ruling" not in kinds and "parked" not in kinds
    subs = s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert [x.payload["phase"] for x in subs] == ["spec", "plan"]
    assert [x.payload["author"] for x in subs] == ["claude", "codex"]           # rotation
    verdicts = s.facts_of_kind("r-1", EventKind.REVIEW_VERDICT)
    assert [v.payload["reviewer_identity"] for v in verdicts] == ["codex", "claude"]
    assert [a[3] for a in gh] == ["1", "1"] and all("bircher: published" in a[-1] for a in gh)
    # Every session prompted, ended, stopped.
    for row in front.satisfied_effects(s, "r-1", "sess-create"):
        sid = __import__("json").loads(row["external_object_id"])["id"]
        assert any(r["intent"]["obligation"]["session"] == sid for r in front.satisfied_effects(s, "r-1", "sess-prompt"))
        assert any(r["intent"]["obligation"]["session"] == sid for r in front.satisfied_effects(s, "r-1", "sess-stop"))


def test_default_policy_parks_at_the_gate_and_resumes_on_approve(world, monkeypatch):
    s, f, fake, ctx = world(labels=())
    fake.on_prompt = Script(fake, s)
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("r-1") == "spec_accepted"
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "gate"
    sid = park.payload["session_id"]
    assert "approve" in fake.sessions[sid]["items"][-1]["content"][0]["text"]
    # Resume with nothing human: parked again, nothing re-sent.
    n = len(fake.sessions[sid]["items"])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert len(fake.sessions[sid]["items"]) == n
    fake.add_user_message(sid, "approve")
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("r-1") == "planned"


def test_grill_human_parks_on_questions_and_continues_in_the_same_session(world, monkeypatch):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    asked = {"n": 0}

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher spec author brief") and asked["n"] == 0:
            asked["n"] += 1
            open(os.path.join(ws, seat.QUESTIONS_OUT), "w").write("### Q1: db?\nRecommended: sqlite\n")
        elif text.startswith("Answered; continue."):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC)
        else:
            Script(fake, s)(sid, text)
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "grill"
    sid = park.payload["session_id"]
    fake.add_user_message(sid, "sqlite please")
    assert phases.run_loop(ctx) == phases.Exit.OK
    a = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    assert a.payload["question_ids"] == ["Q1"] and a.payload["answer"] == "sqlite please"
    authors = {__import__("json").loads(r["external_object_id"])["id"]
               for r in front.satisfied_effects(s, "r-1", "sess-create")
               if r["intent"]["obligation"]["phase"] == "spec"}
    assert authors == {sid} | {x for x in authors if fake.sessions[x]["agent_name"] == "codex"}   # one author session
    assert s.run_state("r-1") == "planned"


def test_bound_exhausted_parks_and_retry_grants(world, monkeypatch):
    s, f, fake, ctx = world(cfg={"max_rounds": 1})
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if "author brief" in text:
            n = len(s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED))
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC + str(n).encode())
        else:
            h = s.phase_artifact("r-1", "spec")
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"no\n\nVERDICT: FAIL {h[:8]}\n")
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    park = front.current_park(s, "r-1")
    assert park.payload["reason"] == "bound_exhausted" and park.payload["verdict"] == "request_revision"
    assert s.has_artifact(park.payload["findings_hash"])
    assert front.rounds_used(s, "r-1", "spec", 0) == 1
    sid = park.payload["session_id"]
    fake.add_user_message(sid, "retry")
    assert phases.run_loop(ctx) == phases.Exit.PARKED                      # one more round, then parked again
    assert front.grants(s, "r-1") == 1 and front.rounds_used(s, "r-1", "spec", 0) == 2


def test_revise_bundle_on_resume_restarts_the_epoch(world, monkeypatch):
    s, f, fake, ctx = world(labels=(), issue={"number": 1, "title": "T", "body": "B", "labels": [], "comments": []})
    fake.on_prompt = Script(fake, s)
    monkeypatch.setattr("coordinator.phases.publish_owed", lambda c: [])
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    ctx.generation = f._dispatch(__import__("kernel.dispatch", fromlist=["Role"]).Role.OPERATOR, "runner")
    seat.command(ctx, "revise_bundle", {"issue": {"number": 1, "title": "T", "body": "B and C",
                                                  "labels": ["bircher:running"], "comments": []}})
    assert s.run_state("r-1") == "queued" and front.epoch(s, "r-1") == 1
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    subs = s.facts_of_kind("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert subs[-1].payload["epoch"] == 1
    briefed = [p for p, b in fake.posts if p.endswith("/events")]
    assert len(briefed) >= 4
    text = [b for p, b in fake.posts if p.endswith("/events") and "author brief" in str(b)][-1]
    assert "B and C" in str(text)


def test_a_pending_effect_or_halt_exits_failed_before_any_dispatch(world):
    s, f, fake, ctx = world()
    from kernel.dispatch import Role
    g = f._dispatch(Role.OPERATOR, "runner")
    s.journal_intent("e", "r-1", g, "session_control", f"sess-create:r-1:{g}",
                     {"argv": ["curl"], "obligation": {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                                       "epoch": 0, "cause": "x"}})
    n = len(s.dispatches_for("r-1"))
    assert phases.run_loop(ctx) == phases.Exit.FAILED
    assert len(s.dispatches_for("r-1")) == n


def test_a_run_beyond_the_front_half_exits_ok_at_once(world):
    s, f, fake, ctx = world()
    f.to_implementing()
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert fake.posts == []


def test_retire_stops_every_session_of_a_cancelled_run(world):
    s, f, fake, ctx = world()
    fake.on_prompt = lambda sid, text: None
    ctx.turn_timeout_s = 10
    from coordinator import author
    author.author_round(ctx)                                        # a session exists, turn ended, stopped
    from kernel.dispatch import Role
    g = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g, f._newest_id())                             # a create with no prompt and no stop
    __import__("tests.coordinator.test_retire_and_publish", fromlist=["_register"])._register(fake, sid2)
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    execute_as_human(s, Command(name="cancel_run", run_id="r-1", expected_version=s.run_version("r-1"),
                                idempotency_key="cancel", generation=HUMAN_GENERATION, payload={}))
    ctx.generation = f._dispatch(Role.OPERATOR, "operator")
    keys = phases.retire(ctx)
    assert keys == [f"sess-stop:{sid2}:{ctx.generation}"]
    cancelled = [t for t in s.facts_of_kind("r-1", EventKind.TRANSITION) if t.payload["to"] == "cancelled"][-1]
    assert s.effect_by_key(keys[0], run_id="r-1")["intent"]["obligation"]["cause"] == cancelled.id
```

`cancel_run` reaches `execute_as_human` because `HUMAN_COMMANDS` gates only the four human-only names; `cancel_run` is legal from every active state for any actor (Task 7's table) and the human is one — add `"cancel_run"` to the names `execute_as_human` accepts if `_submit`'s halt exemption needs it (it does not: the exemption reads `cmd.name`).

- [ ] **Step 3: Write the CLI tests**

`v2/tests/coordinator/test_front_cli.py`:

```python
import json

import pytest

from coordinator.cli import RC_PARKED, RC_USAGE, main
from kernel import front
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _common(db):
    return ["--db", str(db), "--run-id", "r-1"]


def test_phases_requires_a_positive_integer_turn_timeout(tmp_path, capsys):
    db = tmp_path / "k.db"
    Front(Store.open(db), "r-1")
    base = ["phases", *_common(db), "--server", "http://srv", "--repo", "o/r", "--issue", "1",
            "--repo-dir", str(tmp_path), "--workspaces-root", str(tmp_path / "ws"), "--bundle-dir", str(tmp_path),
            "--host-id", "h", "--agent-claude", "a", "--agent-codex", "b"]
    assert main(base) == RC_USAGE
    assert main(base + ["--turn-timeout", "abc"]) == RC_USAGE
    assert main(base + ["--turn-timeout", "0"]) == RC_USAGE
    s = Store.open(db)
    assert s.dispatches_for("r-1") == []                       # nothing happened


def test_approve_grant_direct_revise_and_parked(tmp_path, capsys):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert main(["parked", *_common(db)]) == 1
    from kernel.dispatch import Role
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert main(["parked", *_common(db)]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "gate"
    assert main(["approve", *_common(db), "--phase", "plan"]) == 1        # wrong phase: refused
    assert main(["approve", *_common(db), "--phase", "spec"]) == 0
    assert s.run_state("r-1") == "specified"
    text = tmp_path / "d.txt"; text.write_text("use sqlite")
    assert main(["direct", *_common(db), "--phase", "plan", "--text", str(text)]) == 0
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["text"] == "use sqlite"
    f.author_round(b"# Plan\n\n### Task 1: x\n")
    findings = tmp_path / "f.txt"; findings.write_text("no tests")
    assert main(["revise", *_common(db), "--phase", "plan", "--findings", str(findings)]) == 0
    assert s.run_state("r-1") == "specified"
    assert main(["grant-round", *_common(db)]) == 1                        # no current park


def test_cancel_records_cancelled_and_retires(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "r-1")
    stopped = []
    monkeypatch.setattr("coordinator.phases.retire", lambda ctx: stopped.append(ctx.run_id) or [])
    assert main(["cancel", *_common(db), "--server", "http://srv"]) == 0
    assert s.run_state("r-1") == "cancelled" and stopped == ["r-1"]
```

- [ ] **Step 4: Run to see them fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_human.py tests/coordinator/test_phase_loop.py tests/coordinator/test_front_cli.py -q`
Expected: FAIL — `module 'coordinator.human' has no attribute 'classify_batch'`, `no attribute 'run_loop'`, unknown subcommands.

- [ ] **Step 5: Implement `human.py`**

Replace the Task 17 stub with:

```python
"""Human interaction (spec §4): the cursor, the discriminator, the batch
rules, dismissals and their replies, and the human pass."""
from __future__ import annotations

import json

from coordinator import seat, sessions
from coordinator.session import LookupFailed, list_items
from kernel import front
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
from kernel.events import EventKind

APPROVE, RETRY = "approve", "retry"


def cursor(store, run_id: str, session_id: str, listing: list) -> str | None:
    for f in reversed(store.facts_for(run_id)):
        c = f.payload.get("cursor_item_id") if isinstance(f.payload, dict) else None
        if c:
            return c
    return listing[0]["id"] if listing else None


def _own_prompts(ctx, session_id: str) -> tuple[set, set]:
    ids = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, EventKind.PROMPT_ITEM)}
    hashes = front.prompt_hashes_of(ctx.store, ctx.run_id, session_id)
    return ids, hashes


def unread_human_items(ctx, session_id: str, listing: list) -> list:
    ids, hashes = _own_prompts(ctx, session_id)
    cur = cursor(ctx.store, ctx.run_id, session_id, listing)
    after = []
    seen = cur is None
    for it in listing:
        if seen:
            after.append(it)
        if it["id"] == cur:
            seen = True
    if not seen:
        after = listing
    return [it for it in after if it["role"] == "user" and it["id"] not in ids
            and content_hash(it["text"].encode()) not in hashes]


def classify_batch(items: list, *, state: str, grill_open: bool) -> tuple[str, str]:
    texts = [it["text"].strip() for it in items]
    joined = "\n\n".join(texts)
    if grill_open:
        return "answer", joined
    single = texts[0].casefold() if len(texts) == 1 else None
    if single == RETRY:
        return "retry", ""
    if state in ("spec_submitted", "spec_accepted", "plan_submitted", "plan_accepted"):
        if single == APPROVE:
            return "approve", ""
        return "revision", joined
    return "direction", joined


def _human(ctx, name: str, payload: dict, key: str):
    return execute_as_human(ctx.store, Command(
        name=name, run_id=ctx.run_id, expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=key, generation=HUMAN_GENERATION, payload=payload))


def take_listing(ctx, session_id: str, listing: list) -> str | None:
    store, run_id = ctx.store, ctx.run_id
    items = unread_human_items(ctx, session_id, listing)
    if not items:
        return None
    state, n = store.run_state(run_id), front.epoch(store, run_id)
    open_qs = [q for q in front.epoch_facts(store, run_id, EventKind.MODEL_QUESTION, n)]
    answers = front.epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
    newer_q = [q for q in open_qs if not answers or q.seq > answers[-1].seq]
    kind, text = classify_batch(items, state=state, grill_open=bool(newer_q))
    cur = listing[-1]["id"]
    key = f"human:{run_id}:{items[-1]['id']}"
    phase = front.FRONT_PHASES[0] if state in ("queued", "spec_submitted", "spec_accepted") else "plan"
    try:
        if kind == "answer":
            _human(ctx, "record_human_answer", {"question_ids": [q.payload["question_id"] for q in newer_q],
                                                "answer": text, "cursor_item_id": cur}, key)
        elif kind == "approve":
            _human(ctx, "approve_artifact", {"artifact_hash": store.phase_artifact(run_id, phase)}, key)
        elif kind == "retry":
            _human(ctx, "grant_round", {}, key)
        elif kind == "revision":
            _human(ctx, "record_review", {"phase": phase, "artifact_hash": store.phase_artifact(run_id, phase),
                                          "verdict": "request_revision", "findings": text}, key)
        else:
            _human(ctx, "record_human_direction", {"text": text, "cursor_item_id": cur}, key)
    except NotAuthorized:
        rej = store.newest_fact(run_id, EventKind.COMMAND_REJECTED)
        seat.command(ctx, "dismiss_human_item", {"cursor_item_id": cur, "rejection": rej.id})
        return "dismissed"
    return kind


def _send(ctx, session_id: str, cause: str, text: bytes) -> None:
    phase, n = ctx.phase(), ctx.epoch()
    ob = {"kind": "sess-prompt", "session": session_id, "phase": phase, "epoch": n, "cause": cause}
    if sessions.satisfied(ctx.store, ctx.run_id, ob) is None:
        sessions.prompt_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                session_id=session_id, phase=phase, epoch=n, cause=cause, text=text,
                                env=ctx.effect_env())
    seat._record_prompt_item(ctx, session_id, content_hash(text))


def reply_refusals(ctx) -> list[str]:
    store, run_id = ctx.store, ctx.run_id
    try:
        listed = sessions.list_sessions(ctx.server, fetch=ctx.fetch)
    except (LookupFailed, ValueError):
        return []
    sent = []
    rejections = {f.id: f for f in front.human_rejections(store, run_id)}
    for d in store.facts_of_kind(run_id, EventKind.HUMAN_ITEM_DISMISSED):
        rej = rejections.get(d.payload["rejection"])
        sid = _session_for_reply(ctx, d)
        if rej is None or sid is None or sid not in listed:
            continue
        ob = {"kind": "sess-prompt", "session": sid, "phase": ctx.phase(), "epoch": ctx.epoch(), "cause": rej.id}
        if sessions.satisfied(store, run_id, ob) is not None:
            continue
        text = f"Bircher refused that: {rej.payload.get('detail', '')}".encode()
        _send(ctx, sid, rej.id, text)
        sent.append(rej.id)
    return sent


def _session_for_reply(ctx, dismissal) -> str | None:
    """The session the dismissed item was read from: the newest session the
    run prompted before the dismissal."""
    rows = front.satisfied_effects(ctx.store, ctx.run_id, "sess-prompt")
    return rows[-1]["intent"]["obligation"]["session"] if rows else None


def _park_session(ctx, park) -> str | None:
    for row in front.satisfied_effects(ctx.store, ctx.run_id, "sess-create"):
        if row["intent"]["obligation"].get("cause") == park.id:
            return json.loads(row["external_object_id"])["id"]
    return park.payload.get("session_id")


def park_prompt(ctx, park) -> bytes:
    store, run_id = ctx.store, ctx.run_id
    reason, phase = park.payload["reason"], park.payload["phase"]
    if reason == "grill":
        return b""                                       # the author's own questions are the prompt
    h = store.phase_artifact(run_id, phase)
    art = store.read_blob(h).decode("utf-8", "replace") if h else "(no artefact yet)"
    if reason == "gate":
        return (f"The {phase} below was accepted by the reviewer and waits for your approval.\n\n"
                f"Reply with the single word `approve`, or give corrections.\n\n---\n\n{art}").encode()
    findings = ""
    if park.payload.get("findings_hash"):
        findings = "\n\n## Final findings\n\n" + store.read_blob(park.payload["findings_hash"]).decode("utf-8", "replace")
    return (f"The run is stalled ({reason}) at {phase}.\n\n"
            f"Reply with the single word `retry`, or give corrections.\n\n---\n\n{art}{findings}").encode()


def human_pass(ctx, park) -> str:
    sid = _park_session(ctx, park)
    if sid is None:
        return "parked"
    try:
        listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    except LookupFailed:
        return "parked"
    if take_listing(ctx, sid, listing) not in (None, "dismissed"):
        return "taken"
    text = park_prompt(ctx, park)
    if text:
        _send(ctx, sid, park.id, text)
    reply_refusals(ctx)
    listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    if take_listing(ctx, sid, listing) not in (None, "dismissed"):
        return "taken"
    return "parked"
```

The three ruling shapes (`approve_artifact`, `grant_round`, the human `record_review`) carry no `cursor_item_id` (Task 8). After one is accepted the run has transitioned or been granted, so the next listing belongs to a new park or a new turn and sets its cursor from a listing in which the ruling's item is already behind the new cursor; `unread_human_items` reads only after the cursor. `test_no_fact_carries_a_cursor_past_an_unrecorded_human_item` binds the invariant over every fact the loop writes.

- [ ] **Step 6: The loop, `stall` and `retire` in `phases.py`**

```python
class Exit:
    OK, FAILED, USAGE, PARKED = 0, 1, 2, 4


def stall(ctx: Ctx, reason: str, *, session_id, findings_hash, verdict, reviewer) -> str:
    from coordinator import human
    from coordinator.session import list_items
    store, run_id = ctx.store, ctx.run_id
    # "The author session" is the run's most recent author session, whatever
    # phase made it (spec §4).
    if session_id is None:
        session_id = _newest_author_session(ctx)
    listing = []
    if session_id is not None:
        try:
            listing = list_items(ctx.server, session_id, fetch=ctx.fetch)
        except Exception:
            session_id, listing = None, []
        if listing and human.take_listing(ctx, session_id, listing) not in (None, "dismissed"):
            return "taken"
    cur = listing[-1]["id"] if listing else None
    if cur is None and session_id is None:
        cur = _last_cursor(ctx)
    from coordinator import seat
    seat.command(ctx, "park", {"reason": reason, "session_id": session_id, "cursor_item_id": cur,
                               "findings_hash": findings_hash, "verdict": verdict, "reviewer": reviewer})
    park = front.current_park(store, run_id)
    if session_id is None:
        return "parked"
    return human.human_pass(ctx, park)


def _last_cursor(ctx: Ctx):
    for f in reversed(ctx.store.facts_for(ctx.run_id)):
        if f.payload.get("cursor_item_id"):
            return f.payload["cursor_item_id"]
    return None


def run_loop(ctx: Ctx) -> int:
    from coordinator import author, review
    from coordinator.session import AgentMismatch
    from coordinator.sessions import WorktreeExists
    from kernel.dispatch import PendingEffects, Role, SeatsExhausted, dispatch
    from kernel.effects import UncertainEffect, is_halted, pending_reconciliation
    store, run_id = ctx.store, ctx.run_id
    if is_halted(store, run_id) or pending_reconciliation(store, run_id):
        ctx.log(f"run {run_id} halted or holds pending effects: {pending_reconciliation(store, run_id)}")
        return Exit.FAILED
    try:
        while True:
            ctx.generation = dispatch(store, run_id, actor="coordinator", role=Role.OPERATOR).generation
            retire_owed(ctx)
            publish_owed(ctx)
            state = ctx.state()
            if state == "planned" or state not in FRONT_HALF_STATES:
                return Exit.OK
            park = front.current_park(store, run_id)
            if park is not None:
                from coordinator import human
                if human.human_pass(ctx, park) == "parked":
                    return Exit.PARKED
                continue
            if state in ("queued", "specified"):
                out = author.author_round(ctx)
                if out == "questions":
                    if stall(ctx, "grill", session_id=_newest_author_session(ctx), findings_hash=None,
                             verdict=None, reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "budget":
                    if stall(ctx, "budget_exhausted", session_id=None, findings_hash=None, verdict=None,
                             reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "stall":
                    if stall(ctx, "identical_resubmission", session_id=_newest_author_session(ctx),
                             findings_hash=None, verdict=None, reviewer=None) == "parked":
                        return Exit.PARKED
                elif out == "failed":
                    return Exit.FAILED
                continue
            if state in ("spec_submitted", "plan_submitted"):
                out = review.review_round(ctx)
                if out.status == "recorded":
                    continue
                if out.status in ("failed", "no_brief"):
                    return Exit.FAILED
                reason = {"no_verdict": "no_verdict", "bound_exhausted": "bound_exhausted",
                          "budget": "budget_exhausted"}[out.status]
                if stall(ctx, reason, session_id=_newest_author_session(ctx), findings_hash=out.findings_hash,
                         verdict=(None if out.verdict is None else
                                  ("accept" if out.verdict == "PASS" else "request_revision")),
                         reviewer=out.reviewer or None) == "parked":
                    return Exit.PARKED
                continue
            if state in ("spec_accepted", "plan_accepted"):
                if stall(ctx, "gate", session_id=_newest_author_session(ctx), findings_hash=None, verdict=None,
                         reviewer=None) == "parked":
                    return Exit.PARKED
                continue
            return Exit.OK
    except (PendingEffects, UncertainEffect) as exc:
        ctx.log(f"halted: {exc}")
        return Exit.FAILED
    except (WorktreeExists, AgentMismatch) as exc:
        ctx.log(f"failed: {exc}")
        return Exit.FAILED


def _newest_author_session(ctx: Ctx) -> str | None:
    from kernel.dispatch import Role
    roles = {d["generation"]: d["role"] for d in ctx.store.dispatches_for(ctx.run_id)}
    found = None
    for row in front.satisfied_effects(ctx.store, ctx.run_id, "sess-create"):
        if roles.get(row["generation"]) == Role.AUTHOR:
            found = __import__("json").loads(row["external_object_id"])["id"]
    return found


def retire(ctx: Ctx) -> list[str]:
    """spec §5 *Cancellation retires the run's sessions*."""
    from kernel.effects import is_halted
    store, run_id = ctx.store, ctx.run_id
    cancelled = [t for t in store.facts_of_kind(run_id, EventKind.TRANSITION) if t.payload.get("to") == "cancelled"]
    cause = cancelled[-1].id if cancelled else _displacing_cause(ctx)
    stopped = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-stop")}
    owed = []
    for row in front.satisfied_effects(store, run_id, "sess-create"):
        sid = __import__("json").loads(row["external_object_id"])["id"]
        if sid not in stopped:
            owed.append(sid)
    if is_halted(store, run_id) and owed:
        raise RuntimeError(f"run {run_id} is halted; these sessions could not be stopped: {owed}")
    return [_stop(ctx, sid, cause) for sid in owed]
```

The loop's operator fence per iteration is the coordinator's own (`actor="coordinator"`): the runner's resume fence (§5) is taken by `run_item` before `phases` is called, and every seat dispatch inside supersedes it; `retire_owed` and `publish_owed` perform effects under the coordinator's generation.

- [ ] **Step 7: The CLI**

In `v2/coordinator/cli.py`, add `RC_FAILED = 1`, `RC_PARKED = 4`, the subparsers, and a `_ctx(a)` builder:

```python
    ph = subs.add_parser("phases")
    for sp in (ph,):
        sp.add_argument("--db", required=True); sp.add_argument("--run-id", required=True)
    ph.add_argument("--server", required=True); ph.add_argument("--repo", required=True)
    ph.add_argument("--issue", type=int, required=True); ph.add_argument("--repo-dir", required=True)
    ph.add_argument("--workspaces-root", required=True); ph.add_argument("--bundle-dir", required=True)
    ph.add_argument("--host-id", required=True); ph.add_argument("--agent-claude", required=True)
    ph.add_argument("--agent-codex", required=True); ph.add_argument("--default-author", default="claude")
    ph.add_argument("--turn-timeout", default=None)
    for name in ("retire", "cancel"):
        sp = subs.add_parser(name)
        sp.add_argument("--db", required=True); sp.add_argument("--run-id", required=True)
        sp.add_argument("--server", required=True)
    for name in ("approve", "grant-round", "revise", "direct", "parked"):
        sp = subs.add_parser(name)
        sp.add_argument("--db", required=True); sp.add_argument("--run-id", required=True)
        if name in ("approve", "revise", "direct"):
            sp.add_argument("--phase", required=True, choices=["spec", "plan"])
        if name == "revise":
            sp.add_argument("--findings", required=True)
        if name == "direct":
            sp.add_argument("--text", required=True)
```

and the handlers:

```python
def _human_cmd(store, run_id, name, payload):
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    import time as _t
    try:
        execute_as_human(store, Command(name=name, run_id=run_id, expected_version=store.run_version(run_id),
                                        idempotency_key=f"cli:{name}:{run_id}:{int(_t.time() * 1e6)}",
                                        generation=HUMAN_GENERATION, payload=payload))
    except Exception as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_FAILED
    return RC_OK


    if a.mode == "phases":
        if a.turn_timeout is None or not str(a.turn_timeout).isdigit() or int(a.turn_timeout) <= 0:
            print("phases: --turn-timeout must be a positive integer (seconds)", file=sys.stderr)
            return RC_USAGE
        from coordinator import phases as _phases
        from kernel.store import Store
        ctx = _phases.Ctx(store=Store.open(a.db), run_id=a.run_id, server=a.server, repo=a.repo,
                          issue_number=a.issue, repo_dir=a.repo_dir, workspaces_root=a.workspaces_root,
                          bundle_dir=a.bundle_dir, agent_ids={"claude": a.agent_claude, "codex": a.agent_codex},
                          host_id=a.host_id, turn_timeout_s=int(a.turn_timeout), default_author=a.default_author,
                          env=dict(os.environ, BIRCHER_KERNEL_DB=a.db, BIRCHER_RUN_ID=a.run_id), fetch=_fetch,
                          log=lambda m: print(m, file=sys.stderr))
        return _phases.run_loop(ctx)
    if a.mode in ("retire", "cancel"):
        from coordinator import phases as _phases
        from kernel.dispatch import Role, dispatch
        from kernel.store import Store
        store = Store.open(a.db)
        if a.mode == "cancel":
            rc = _human_cmd(store, a.run_id, "cancel_run", {})
            if rc != RC_OK:
                return rc
        ctx = _phases.Ctx(store=store, run_id=a.run_id, server=a.server, repo="", issue_number=0, repo_dir="",
                          workspaces_root="", bundle_dir="", agent_ids={}, host_id="", turn_timeout_s=1,
                          default_author="", env=dict(os.environ, BIRCHER_KERNEL_DB=a.db, BIRCHER_RUN_ID=a.run_id),
                          fetch=_fetch, log=lambda m: print(m, file=sys.stderr))
        ctx.generation = dispatch(store, a.run_id, actor="operator", role=Role.OPERATOR).generation
        try:
            for k in _phases.retire(ctx):
                print(k)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return RC_FAILED
        return RC_OK
    if a.mode == "parked":
        from kernel import front
        from kernel.store import Store
        park = front.current_park(Store.open(a.db), a.run_id)
        if park is None:
            return RC_FAILED
        print(json.dumps(dict(park.payload, seq=park.seq, id=park.id)))
        return RC_OK
    if a.mode in ("approve", "grant-round", "revise", "direct"):
        from kernel.store import Store
        store = Store.open(a.db)
        if a.mode == "approve":
            h = store.phase_artifact(a.run_id, a.phase)
            return _human_cmd(store, a.run_id, "approve_artifact", {"artifact_hash": h})
        if a.mode == "grant-round":
            return _human_cmd(store, a.run_id, "grant_round", {})
        if a.mode == "revise":
            h = store.phase_artifact(a.run_id, a.phase)
            return _human_cmd(store, a.run_id, "record_review", {"phase": a.phase, "artifact_hash": h,
                                                                  "verdict": "request_revision",
                                                                  "findings": open(a.findings).read()})
        return _human_cmd(store, a.run_id, "record_human_direction", {"text": open(a.text).read(),
                                                                       "cursor_item_id": None})
```

`approve --phase plan` at `spec_accepted` is refused by the kernel (the phase of the state is `spec`, and the plan artefact is `None`): the CLI prints the refusal and returns `RC_FAILED`, as the test expects. `cancel` is the one gesture: `cancel_run` through `execute_as_human`, then `retire`; `test_cancel_records_cancelled_and_retires` stubs `retire`.

- [ ] **Step 8: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator -q`
Expected: PASS. Full suite: green.

- [ ] **Step 9: Mutation check, then commit**

Make `unread_human_items` exclude by id only (drop the hash clause): `test_the_discriminator_excludes_the_coordinators_own_prompts` fails on its crash-window half. Send the park prompt before recording the park in `stall`: `test_a_message_in_the_confirm_listing_consumes_the_park` fails (the ruling's seq is not greater than the park's — the park is written after). Make `reply_refusals` consider only dismissals newer than the last human fact: `test_a_dismissed_reply_is_owed_across_a_later_human_fact` fails. Restore all three.

```bash
git add v2/coordinator/human.py v2/coordinator/phases.py v2/coordinator/cli.py v2/tests/coordinator/test_human.py v2/tests/coordinator/test_phase_loop.py v2/tests/coordinator/test_front_cli.py
git commit -m "feat(coordinator): the human pass, the phase loop, and the operator CLI"
```

---
## Part D — Runner and documentation

### Task 20: The runner — bundles, `create_run`, the §6 seam, parking and resumption

**Files:**
- Create: `agents/v2_author_claude/config.yaml`, `agents/v2_author_codex/config.yaml`
- Modify: `v2/kernel/bundle.py` (`from_gh`)
- Modify: `batch/lib/kernel-client.sh` (`_kernel_run_start`, `_kernel_find_run`, `_kernel_reconcile`, `_kernel_state`; delete `_kernel_submit_spec`/`_kernel_submit_plan`)
- Modify: `batch/run-queue.sh` (`run_item`: resume path, `create_run` inputs, the §6 seam, the parked sidecar; `is_bircher_status`'s fifth prefix; bundle uploads; `main`'s lock flag)
- Modify: `v2/coordinator/cli.py` (`state` subcommand)
- Test: `v2/tests/kernel/test_bundle_from_gh.py`, `v2/tests/execution/test_front_half_seam.py`, `v2/tests/execution/test_bircher_status_bash.py`

**Interfaces:**
- Consumes: `create_run` (Task 5), `Store.open_run_ids` (Task 1), `reconcile --delivered/--not-delivered` (Task 4), `coordinator.cli phases` and its rc `RC_PARKED = 4` (Task 19), `bundle.BIRCHER_STATUS_PREFIXES` and the fixture (Task 5).
- Produces:
  - `bundle.from_gh(raw: dict) -> dict` — the `gh issue view --json number,title,body,labels,comments` object reduced to the shape `snapshot` takes: `labels` → the label names; each comment → `{"id": <int from the comment url's trailing issuecomment-<n>>, "author": <author.login or "unknown">, "body"}`.
  - `_kernel_run_start <run_id> <base_repo> <base_sha> <issue_json_path> <project_config_json_path>` — calls `create_run`; prints the bundle hash; returns 1 on `NotReplayable` (the log names both input hashes) and on any other failure — **not advisory**: a run that cannot be created is not run.
  - `_kernel_find_run <code> [open]` — with `open`, the newest run id with the code as prefix whose state is not `ended`/`cancelled`; else as today.
  - `_kernel_state <run_id>` — prints the run's state (via `coordinator.cli state --db --run-id`).
  - `_kernel_reconcile <run_id> <expected_version> [--delivered KEY[=VALUE]]... [--not-delivered KEY]...` (typed) beside the legacy `<run_id> <resolution> <expected_version> <key>...`; for `--delivered sess-create:*=<id>` it fetches `GET $SERVER/v1/sessions/<id>`, refuses a fetch that fails or returns no `agent_name`, writes the body to a temp file and passes `KEY=@file`.
  - `run_item`: (1) the resume path before minting; (2) `create_run` with the fetched issue and the Project config; (3) the operator fence, then the label, then `phases`; (4) `RC_PARKED` → sidecar + `outcome=parked`, queue file kept, return 0; (5) the implementer dispatched after `phases`, `start_implementation`, state read back, session created only at `implementing`; (6) the implementer's brief = the directives + spec + plan + issue snapshot from the store.
  - `BIRCHER_HAVE_LOCK=1` exported by `main` when `flock` succeeded; resumption refuses without it.
  - `BIRCHER_PROJECT_ID` (optional): the omnigent Project whose `config.bircher` is the policy default (ruling 9); `BIRCHER_WORKSPACES_ROOT` (default `/workspaces`).

- [ ] **Step 1: The two author bundles**

`agents/v2_author_claude/config.yaml` — `v2_implementer`'s confinement, an author's prompt, no GitHub at all:

```yaml
spec_version: 1
name: v2_author_claude
description: >-
  Bircher v2 author (Claude) — writes a spec or a plan for one issue in its
  own worktree from the brief it is sent, and CANNOT push, open a PR, comment
  or label. Every external effect is the kernel's.

executor:
  type: omnigent
  config:
    harness: claude-sdk

prompt: |
  You are a Bircher v2 author. Your first message is a brief that names the
  phase (spec or plan), the files to write, the policy, the issue and any
  findings. Follow it exactly: write the artefact where it says, then end
  your turn. You hold no credentials and no network beyond your model
  provider; do not attempt git push, gh, or any HTTP call. Do not write
  outside your worktree.

os_env:
  type: caller_process
  cwd: .
  sandbox:
    type: linux_landlock
    write_paths:
      - .
    egress_rules:
      # The model provider only. No GitHub: an author reads a brief and
      # writes a file.
      - "GET,POST api.anthropic.com/**"

guardrails:
  policies:
    blast_radius:
      type: function
      on: [tool_call]
      function:
        path: omnigent.inner.nessie.policies.blast_radius
        arguments:
          gate_pushes: true
```

`agents/v2_author_codex/config.yaml` — the same with the codex harness. The egress hosts are the codex CLI's under a ChatGPT-account login; Task 22 probes them under Landlock and corrects this list from the proxy's denial log before the first codex-authored round:

```yaml
spec_version: 1
name: v2_author_codex
description: >-
  Bircher v2 author (Codex) — the same confinement as v2_author_claude with
  the codex harness.

executor:
  type: omnigent
  model: gpt-5.6-sol
  config:
    harness: codex

prompt: |
  You are a Bircher v2 author. Your first message is a brief that names the
  phase (spec or plan), the files to write, the policy, the issue and any
  findings. Follow it exactly: write the artefact where it says, then end
  your turn. You hold no credentials and no network beyond your model
  provider; do not attempt git push, gh, or any HTTP call. Do not write
  outside your worktree.

os_env:
  type: caller_process
  cwd: .
  sandbox:
    type: linux_landlock
    write_paths:
      - .
    egress_rules:
      - "GET,POST api.openai.com/**"
      - "GET,POST chatgpt.com/**"

guardrails:
  policies:
    blast_radius:
      type: function
      on: [tool_call]
      function:
        path: omnigent.inner.nessie.policies.blast_radius
        arguments:
          gate_pushes: true
```

- [ ] **Step 2: `bundle.from_gh` and its test**

`v2/tests/kernel/test_bundle_from_gh.py`:

```python
from kernel import bundle

RAW = {
    "number": 7, "title": "T", "body": "B",
    "labels": [{"name": "bug", "color": "x"}, {"name": "bircher:queued", "color": "y"}],
    "comments": [
        {"id": "IC_kwDOA", "author": {"login": "alice"}, "body": "second",
         "url": "https://github.com/o/r/issues/7#issuecomment-222"},
        {"id": "IC_kwDOB", "author": {"login": "bob"}, "body": "first",
         "url": "https://github.com/o/r/issues/7#issuecomment-111"},
        {"id": "IC_kwDOC", "author": None, "body": "ghost",
         "url": "https://github.com/o/r/issues/7#issuecomment-333"},
    ],
}


def test_from_gh_reduces_to_the_snapshot_shape():
    issue = bundle.from_gh(RAW)
    assert issue["labels"] == ["bug", "bircher:queued"]
    assert issue["comments"] == [
        {"id": 222, "author": "alice", "body": "second"},
        {"id": 111, "author": "bob", "body": "first"},
        {"id": 333, "author": "unknown", "body": "ghost"},
    ]
    snap = bundle.snapshot(issue)
    assert [c["id"] for c in snap["comments"]] == [111, 222, 333]
    assert snap["labels"] == ["bug"]


def test_from_gh_tolerates_missing_fields():
    assert bundle.from_gh({"number": "3", "title": "t", "body": None}) == \
        {"number": 3, "title": "t", "body": "", "labels": [], "comments": []}
```

`bundle.py`:

```python
import re

_COMMENT_ID = re.compile(r"issuecomment-(\d+)$")


def from_gh(raw: dict) -> dict:
    """`gh issue view --json number,title,body,labels,comments` to the shape
    snapshot() takes. The comment id is the numeric one in the url: gh's
    `id` is a node id string, and the canon sorts comments numerically."""
    comments = []
    for c in raw.get("comments") or []:
        m = _COMMENT_ID.search(c.get("url") or "")
        if not m:
            continue
        comments.append({"id": int(m.group(1)),
                         "author": ((c.get("author") or {}).get("login")) or "unknown",
                         "body": c.get("body") or ""})
    return {"number": int(raw["number"]), "title": raw.get("title") or "",
            "body": raw.get("body") or "",
            "labels": [l["name"] if isinstance(l, dict) else str(l) for l in raw.get("labels") or []],
            "comments": comments}
```

- [ ] **Step 3: `kernel-client.sh`**

Replace `_kernel_run_start`:

```bash
# _kernel_run_start <run_id> <base_repo> <base_sha> <issue_json> <project_config_json>
# -> prints the bundle hash; rc 1 on NotReplayable or any failure. NOT
# advisory: a run the kernel would not create is not run (spec §7).
_kernel_run_start() {
  local run_id="$1" base_repo="$2" base_sha="$3" issue_json="$4" cfg_json="$5"
  local src='
import json, os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.bundle import from_gh
from kernel.effects import NotReplayable
from kernel.enqueue import create_run
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
issue = from_gh(json.load(open(os.environ["K_ISSUE_JSON"])))
cfg = json.load(open(os.environ["K_CFG_JSON"])) if os.environ.get("K_CFG_JSON") else {}
try:
    out = create_run(s, run_id=os.environ["K_RUN_ID"], base_repo=os.environ["K_BASE_REPO"],
                     base_sha=os.environ["K_BASE_SHA"], issue=issue, project_config=cfg)
except NotReplayable as exc:
    print(f"create_run NotReplayable: {exc}", file=sys.stderr); sys.exit(1)
print(out["bundle_hash"])
'
  local out
  out=$( K_RUN_ID="$run_id" K_BASE_REPO="$base_repo" K_BASE_SHA="$base_sha" \
         K_ISSUE_JSON="$issue_json" K_CFG_JSON="$cfg_json" \
         BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c "$src" 2>&1 ) || { _kernel_warn "run start FAILED: $run_id -- $out"; return 1; }
  printf '%s' "$out"
}
```

`_kernel_find_run` gains the filter:

```bash
_kernel_find_run() {  # <code> [open]
  local code="$1" mode="${2:-}" out=""
  out=$( K_CODE="$code" K_MODE="$mode" BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c '
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
code = os.environ["K_CODE"]
if os.environ["K_MODE"] == "open":
    closed = frozenset({"ended", "cancelled"})
    runs = [r for r in s.open_run_ids(code, frozenset()) if s.run_state(r) not in closed]
else:
    runs = [r for r in s.all_run_ids() if r.startswith(code + "-")]
print(runs[-1] if runs else "")' 2>/dev/null
  ) || out=""
  printf '%s' "$out"
}
```

(`Store.open_run_ids(prefix, states)` from Task 1 returns every run with the prefix when `states` is empty; the state filter is applied here so the closed set is written once.) Add:

```bash
_kernel_state() {  # <run_id> -> the state, or empty
  local out=""
  out=$( PYTHONPATH="$(_kernel_pythonpath)" _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m coordinator.cli state --db "${BIRCHER_KERNEL_DB:-}" --run-id "$1" 2>/dev/null ) || out=""
  printf '%s' "$out"
}
```

and the typed `_kernel_reconcile`:

```bash
# _kernel_reconcile <run_id> <resolution> <expected_version> <key>...   (legacy form)
# _kernel_reconcile <run_id> <expected_version> --delivered K[=V]... --not-delivered K...  (typed form)
_kernel_reconcile() {
  local run_id="$1"; shift
  case "${2:-}" in
    --delivered|--not-delivered)
      local version="$1"; shift
      local args=() tmp
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --delivered)
            local spec="$2"; shift 2
            case "$spec" in
              sess-create:*=*)
                local id="${spec#*=}"
                tmp=$(mktemp) || return 1
                if ! _net_run "$BIRCHER_NET_TIMEOUT" curl -sf --max-time 30 "$SERVER/v1/sessions/$id" > "$tmp"; then
                  echo "[kernel] reconcile: GET /v1/sessions/$id failed; refusing --delivered for ${spec%%=*}" >&2
                  rm -f "$tmp"; return 1
                fi
                if ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("agent_name") else 1)' "$tmp"; then
                  echo "[kernel] reconcile: session $id has no agent_name (agent row gone); refusing" >&2
                  rm -f "$tmp"; return 1
                fi
                args+=(--delivered "${spec%%=*}=@$tmp") ;;
              *) args+=(--delivered "$spec") ;;
            esac ;;
          --not-delivered) args+=(--not-delivered "$2"); shift 2 ;;
          *) echo "[kernel] reconcile: unexpected argument $1" >&2; return 2 ;;
        esac
      done
      _kernel reconcile --run-id "$run_id" "${args[@]}" --expected-version "$version" ;;
    *)
      local resolution="$1" version="$2"; shift 2
      [ "$#" -gt 0 ] || return 0
      local args=() k
      for k in "$@"; do args+=(--idempotency-key "$k"); done
      _kernel reconcile --run-id "$run_id" "${args[@]}" --resolution "$resolution" --expected-version "$version" ;;
  esac
}
```

Its usage text, printed on a bad argument, names the `RC_FAILED` consequence of a delivered session the server later stops listing (spec §3 *Reconciliation is typed*). Delete `_kernel_submit_spec`, `_kernel_submit_plan` and the "deliberate reuse" comment above `submit_plan`.

`coordinator/cli.py` gains `state`:

```python
    stt = subs.add_parser("state")
    stt.add_argument("--db", required=True); stt.add_argument("--run-id", required=True)
    ...
    if a.mode == "state":
        from kernel.store import Store
        print(Store.open(a.db).run_state(a.run_id))
        return RC_OK
```

- [ ] **Step 4: `run_item` — the resume path and the seam**

Replace the block of `run_item` from `BIRCHER_RUN_ID="${item}-$(date +%s)"` (`:3985`) through `_kernel_start_implementation` (`:4086`) with:

```bash
  local code_lc; code_lc=$(printf '%s' "$item" | cut -d- -f1 | tr 'A-Z' 'a-z')
  BIRCHER_KERNEL_DB="${BIRCHER_KERNEL_DB:-$BUNDLE_DIR/.run/kernel.db}"; export BIRCHER_KERNEL_DB
  mkdir -p "$(dirname "$BIRCHER_KERNEL_DB")" 2>/dev/null || true
  local _iss; _iss=$(_item_issue "$prompt")
  local _base_sha; _base_sha=$(git -C "$WORKDIR" rev-parse HEAD 2>/dev/null)
  : "${_base_sha:=0000000000000000000000000000000000000000}"

  # The fetched issue and the Project config: the two inputs create_run
  # snapshots and derives the policy from (spec §1). Fetched here, by the
  # runner's credential; asserted, not observed (§9).
  local _issue_json _cfg_json
  _issue_json=$(mktemp) && _cfg_json=$(mktemp)
  if [ -n "$_iss" ]; then
    _net_run "$BIRCHER_NET_TIMEOUT" gh issue view "$_iss" --repo "$REPO" \
      --json number,title,body,labels,comments > "$_issue_json" || { echo "[batch] $item: issue fetch failed" >&2; return 5; }
  else
    python3 -c 'import json,sys; print(json.dumps({"number":0,"title":sys.argv[1],"body":sys.argv[2],"labels":[],"comments":[]}))' \
      "$item" "$prompt" > "$_issue_json"
  fi
  _project_config > "$_cfg_json"

  # RESUME OR MINT (spec §5). The kernel is the truth; the sidecar a hint.
  local resumed=0 _open
  _open=$(_kernel_find_run "$code_lc" open)
  if [ -n "$_open" ]; then
    local _pend; _pend=$(_kernel_pending "$_open")
    if _pending_blocks "$_pend"; then
      echo "[batch] $item: run $_open is halted or holds pending effects; skipping until reconciled: $_pend" >&2
      return 0
    fi
    local _st; _st=$(_kernel_state "$_open")
    case "$_st" in
      queued|spec_submitted|spec_accepted|specified|plan_submitted|plan_accepted|planned) ;;
      *) echo "[batch] $item: run $_open is at $_st (beyond the front half); skipping" >&2; return 0 ;;
    esac
    if [ "$_st" = planned ] && _kernel_implementation_started "$_open"; then
      echo "[batch] $item: run $_open already started implementation; skipping" >&2; return 0
    fi
    if [ "${BIRCHER_HAVE_LOCK:-0}" != 1 ]; then
      echo "[batch] $item: refusing to resume $_open without the batch lock" >&2; return 0
    fi
    BIRCHER_RUN_ID="$_open"; export BIRCHER_RUN_ID; resumed=1
    _write_parked_sidecar "$code_lc" "$BIRCHER_RUN_ID" "$_st" "resume"
  else
    BIRCHER_RUN_ID="${item}-$(date +%s)"; export BIRCHER_RUN_ID
    if ! _kernel_run_start "$BIRCHER_RUN_ID" "$REPO" "$_base_sha" "$_issue_json" "$_cfg_json" >/dev/null; then
      mkdir -p "$(dirname "$SCORECARD")"
      json_row "$item" "" "failed" "false" "" "" 0 "create_run refused (see log)" "failed" >> "$SCORECARD"
      mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
      return 0
    fi
  fi

  # The resume fence: operator, actor runner -- not a seat (§5).
  BIRCHER_GENERATION=$(_kernel_dispatch runner operator); export BIRCHER_GENERATION
  [ -n "$BIRCHER_GENERATION" ] || { echo "[batch] $item: operator dispatch failed" >&2; return 5; }
  [ -n "$_iss" ] && _effect issue_or_label "running:$_iss" - gh issue edit "$_iss" --repo "$REPO" --add-label bircher:running --remove-label bircher:queued >/dev/null 2>&1 || true
  if [ "$resumed" = 1 ]; then
    # Re-snapshot the issue; a relevant change re-freezes it (§5). The
    # kernel refuses an irrelevant one, and that refusal is expected.
    _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
      --name revise_bundle --payload-json "$(python3 -c 'import json,sys; print(json.dumps({"issue": json.load(open(sys.argv[1]))}))' "$_issue_json")"
  fi

  local host_id; host_id=$(_local_host_id 2>/dev/null) || host_id=""
  local _prc=0
  PYTHONPATH="$(_kernel_pythonpath)" \
  BIRCHER_AGENT_V2_AUTHOR_CLAUDE="$AGENT_AUTHOR_CLAUDE" BIRCHER_AGENT_V2_AUTHOR_CODEX="$AGENT_AUTHOR_CODEX" \
    "${BIRCHER_PY:-python3}" -m coordinator.cli phases \
      --db "$BIRCHER_KERNEL_DB" --run-id "$BIRCHER_RUN_ID" --server "$SERVER" --repo "$REPO" --issue "${_iss:-0}" \
      --repo-dir "$WORKDIR" --workspaces-root "${BIRCHER_WORKSPACES_ROOT:-/workspaces}" --bundle-dir "$BUNDLE_DIR" \
      --host-id "$host_id" --agent-claude "$AGENT_AUTHOR_CLAUDE" --agent-codex "$AGENT_AUTHOR_CODEX" \
      --turn-timeout "$ITEM_TIMEOUT" || _prc=$?
  case "$_prc" in
    0) ;;
    4)
      local _park; _park=$("${BIRCHER_PY:-python3}" -m coordinator.cli parked --db "$BIRCHER_KERNEL_DB" --run-id "$BIRCHER_RUN_ID" 2>/dev/null || echo '{}')
      _write_parked_sidecar "$code_lc" "$BIRCHER_RUN_ID" "$(_kernel_state "$BIRCHER_RUN_ID")" "$(printf '%s' "$_park" | _json_get reason)"
      mkdir -p "$(dirname "$SCORECARD")"
      json_row "$item" "" "parked" "false" "" "" 0 "parked: $(printf '%s' "$_park" | _json_get reason)" "parked" >> "$SCORECARD"
      return 0 ;;                                   # the queue file stays where it is
    *)
      echo "[batch] $item: phases exited $_prc; recording failed" >&2
      _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "failed"
      mkdir -p "$(dirname "$SCORECARD")"
      json_row "$item" "" "failed" "false" "" "" 0 "phases rc=$_prc" "failed" >> "$SCORECARD"
      mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
      return 0 ;;
  esac
  rm -f "$QUEUE/$code_lc.parked"

  # §6: the implementer is dispatched AFTER phases, afresh.
  BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer); export BIRCHER_GENERATION
  _kernel_start_implementation "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION"
  local _st_after; _st_after=$(_kernel_state "$BIRCHER_RUN_ID")
  if [ "$_st_after" != implementing ]; then
    echo "[batch] $item: state after start_implementation is '$_st_after', not implementing; RC_FAILED, no session" >&2
    _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "failed"
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "failed" "false" "" "" 0 "start_implementation refused at $_st_after" "failed" >> "$SCORECARD"
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi
  # The implementer's brief: the directives, then the spec and plan the
  # kernel holds, then the issue snapshot -- never the queue prompt (§6).
  prompt="$(_implementer_brief "$BIRCHER_RUN_ID" "$vendor")"
  local conv_id bound_outcome="ok"
  conv_id=$(_create_session "$AGENT_ID" "$host_id" "$WORKDIR")
  ... (the existing session-create failure branch, unchanged) ...
  local _out_hash=""
```

The directives block that today precedes `BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)` moves into `_implementer_brief`; `_kernel_submit_spec`/`_kernel_submit_plan` calls and `_spec_hash` are gone. New helpers beside `run_item`:

```bash
_project_config() {   # -> the Project's config.bircher, or {}
  if [ -z "${BIRCHER_PROJECT_ID:-}" ]; then echo '{}'; return 0; fi
  _http_json GET "/v1/projects/$BIRCHER_PROJECT_ID" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("{}"); sys.exit(0)
cfg = ((d.get("config") or {}).get("bircher")) or {}
print(json.dumps(cfg if isinstance(cfg, dict) else {}))' || echo '{}'
}

_pending_blocks() {   # <pending-json> -> 0 when halted or pending non-empty
  python3 -c 'import json,sys
try:
    d = json.loads(sys.argv[1] or "{}")
except Exception:
    sys.exit(1)
sys.exit(0 if (d.get("halted") or d.get("pending")) else 1)' "$1"
}

_kernel_implementation_started() {   # <run_id> -> 0 when a start_implementation was accepted
  local out; out=$( PYTHONPATH="$(_kernel_pythonpath)" _net_run "$(_kernel_net_cap)" \
    "${BIRCHER_PY:-python3}" -c '
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(any(f.kind == "command_accepted" and f.payload.get("command_name") == "start_implementation"
          for f in s.facts_for(sys.argv[1])))' "$1" 2>/dev/null ) || out=""
  [ "$out" = True ]
}

_write_parked_sidecar() {   # <code> <run_id> <state> <reason>
  mkdir -p "$QUEUE"
  python3 -c 'import json,sys; print(json.dumps({"run_id": sys.argv[1], "state": sys.argv[2], "reason": sys.argv[3]}))' \
    "$2" "$3" "$4" > "$QUEUE/$1.parked"
}

_implementer_brief() {   # <run_id> <vendor> -> the prompt on stdout
  local run_id="$1" vendor="$2"
  printf 'IMPLEMENTER VENDOR DIRECTIVE: dispatch the implement sub-agent to %s; the cross-vendor reviewer MUST be the opposite vendor (%s). Do not set any model or model_override.\n\nWORK REPO DIRECTIVE: the repository for this task is %s, checked out at %s. This OVERRIDES any path written in your agent bundle. Wherever the bundle says /workspaces/muesli, read %s. Set your isolated worktree up with:\n    git -C %s fetch origin main\n    git -C %s worktree add -b <code>-<slug> /tmp/wt-<code> origin/main\nand open the pull request against %s. Do not fetch, branch from, push to, or open a pull request against any other repository.\n\n' \
    "$vendor" "$RECOVERY_REVIEWER" "$REPO" "$WORKDIR" "$WORKDIR" "$WORKDIR" "$WORKDIR" "$REPO"
  PYTHONPATH="$(_kernel_pythonpath)" "${BIRCHER_PY:-python3}" -c '
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel import front
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
run = sys.argv[1]
spec = s.read_blob(s.phase_artifact(run, "spec")).decode()
plan = s.read_blob(s.phase_artifact(run, "plan")).decode()
issue = s.read_blob(front.bundle_hash(s, run)).decode()
print("# The accepted spec\n\n" + spec + "\n\n# The accepted plan\n\n" + plan + "\n\n# The issue snapshot\n\n```json\n" + issue + "\n```\n")' "$run_id"
}
```

`main` uploads the two author bundles beside `v2_implementer` and exports their ids:

```bash
  AGENT_AUTHOR_CLAUDE=$(_get_agent_id "$(_upload_bundle "$BUNDLE_DIR/agents/v2_author_claude" "v2_author_claude upload")")
  AGENT_AUTHOR_CODEX=$(_get_agent_id "$(_upload_bundle "$BUNDLE_DIR/agents/v2_author_codex" "v2_author_codex upload")")
  [ -n "$AGENT_AUTHOR_CLAUDE" ] && [ -n "$AGENT_AUTHOR_CODEX" ] || { echo "[batch] FATAL: author bundle upload failed" >&2; exit 3; }
  export AGENT_AUTHOR_CLAUDE AGENT_AUTHOR_CODEX
```

and sets `BIRCHER_HAVE_LOCK=1; export BIRCHER_HAVE_LOCK` right after `flock -n 9` succeeds (and nowhere else). The `bircher: published ` prefix joins the embedded `is_bircher_status` in `_format_issue_comments`.

- [ ] **Step 5: The runner tests**

`v2/tests/execution/test_bircher_status_bash.py` — drives the embedded predicate against the fixture:

```python
import pathlib
import re
import subprocess

RUNNER = pathlib.Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "bircher_status_comments.tsv"


def _bash_predicate_source() -> str:
    src = RUNNER.read_text()
    m = re.search(r"def is_bircher_status\(body\):.*?return \(.*?\)\n", src, re.S)
    assert m, "the embedded is_bircher_status is gone or moved"
    return m.group(0)


def test_the_bash_copy_agrees_with_the_fixture_and_the_kernel():
    from kernel import bundle
    ns = {}
    exec(_bash_predicate_source(), ns)
    for line in FIXTURE.read_text().splitlines():
        verdict, body = line.split("\t", 1)
        assert ns["is_bircher_status"](body) is (verdict == "drop"), body
        assert ns["is_bircher_status"](body) == bundle.is_bircher_status(body), body
```

`v2/tests/execution/test_front_half_seam.py` — the `test_run_item_argument_wiring.py` pattern (source `run-queue.sh` with the network and kernel functions stubbed to a call log, run `run_item` on a queue file, read the log). Read that file's harness (`_log_call`, the stubbed function list at `:268`, the `run_item` invocation) and add these cases, each a test function:

1. `test_run_item_calls_phases_between_run_start_and_the_implementer`: the call log has `_kernel_run_start` (five arguments, the fourth and fifth existing files), then `_kernel_dispatch runner operator`, then `coordinator.cli phases` with `--turn-timeout <ITEM_TIMEOUT>` and both `--agent-*` ids, then `_kernel_dispatch <vendor> implementer`, then `_kernel_start_implementation`, then `_kernel_state`, then `_create_session` — in that order; `_kernel_submit_spec` and `_kernel_submit_plan` appear nowhere (and are not defined: `type _kernel_submit_spec` fails).
2. `test_parked_rc_writes_the_sidecar_and_keeps_the_queue_file`: a fake `python3 -m coordinator.cli phases` exiting 4 → `$QUEUE/<code>.parked` holds `{run_id, state, reason}`, the scorecard row's outcome is `parked`, the queue file is still in `$QUEUE`, no `_create_session` call.
3. `test_state_other_than_implementing_after_start_is_failed_with_no_session`: a fake `phases` that exits 0 leaving the fake kernel at `plan_submitted` → `_kernel_state` returns `plan_submitted`, the scorecard row is `failed`, `_create_session` never called.
4. `test_resume_finds_the_open_run_and_refences_as_operator`: a pre-existing `.parked` sidecar naming a run the fake `_kernel_find_run … open` returns → `BIRCHER_RUN_ID` is that run, `_kernel_run_start` is not called, the first dispatch is `runner operator`, `revise_bundle` is submitted with the fetched issue.
5. `test_sidecar_is_rebuilt_from_the_kernel`: no sidecar, but `_kernel_find_run … open` returns a run → the sidecar is written before `phases`; a sidecar naming a different run is overwritten with the kernel's answer.
6. `test_pending_or_halted_skips_before_any_fence`: `_kernel_pending` returns `{"halted": false, "pending": ["sess-create:r:3"]}` → `run_item` returns 0 with no `_kernel_dispatch` call and the queue file untouched; the same with `{"halted": true, "pending": []}`.
7. `test_resume_refused_without_the_lock`: `BIRCHER_HAVE_LOCK` unset and an open run → no dispatch, a log line naming the lock, rc 0.
8. `test_a_run_beyond_the_front_half_is_skipped_not_relaunched`: `_kernel_state` returns `implementing` → no dispatch, no mint.
9. `test_leak_guard_never_mints_over_an_open_run`: an open run exists → `_kernel_run_start` not called even when the sidecar is missing.
10. `test_create_run_refusal_records_failed`: `_kernel_run_start` stub returns 1 → scorecard `failed`, file moved to processed, no dispatch.
11. `test_reconcile_typed_forms`: `_kernel_reconcile r 7 --delivered sess-create:r:1=s-9 --not-delivered sess-prompt:s-9:1:f` with a fake `curl` returning a session JSON → the `_kernel reconcile` call carries `--delivered sess-create:r:1=@<file>` whose file holds that JSON, `--not-delivered sess-prompt:s-9:1:f`, `--expected-version 7`; a fake `curl` failing → rc 1 and no `_kernel` call; the legacy form `_kernel_reconcile r delivered 7 k1 k2` still produces `--idempotency-key k1 --idempotency-key k2 --resolution delivered`.
12. `test_main_exports_the_lock_flag_only_under_flock`: `BIRCHER_HAVE_LOCK=1` is in the environment `run_item` sees when `flock` succeeded and absent when `flock` is unavailable.

Each test asserts on the call log and the files, never on log prose alone.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_bundle_from_gh.py tests/execution -q` and `bash batch/run-queue.sh --self-test` (the existing self-tests must still pass; the ones that asserted the two ceremonial submits are removed with the submits).
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agents/v2_author_claude agents/v2_author_codex v2/kernel/bundle.py batch/lib/kernel-client.sh batch/run-queue.sh v2/coordinator/cli.py v2/tests/kernel/test_bundle_from_gh.py v2/tests/execution/test_front_half_seam.py v2/tests/execution/test_bircher_status_bash.py v2/tests/execution/test_run_item_argument_wiring.py
git commit -m "feat(runner): author bundles, create_run from the fetched issue, phases at the seam, parking and resumption"
```

---

### Task 21: Documentation and the provenance table

**Files:**
- Modify: `docs/design/ARCHITECTURE.md` (§3.2 module list, §4 steps 2-5, §6b env table, §9 gaps)
- Modify: `docs/design/provenance-table.md` (new rows; the front-half section)
- Modify: `docs/design/2026-08-23-v2-kernel-design.md` (the asserted-row count sentence)
- Modify: `v2/tests/kernel/test_provenance.py` (the pinned residual set)
- Modify: `skills/muesli-loop/SKILL.md` (§2)
- Test: `v2/tests/kernel/test_provenance.py` (existing; must pass)

- [ ] **Step 1: The provenance table**

Run `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_provenance.py -q` and read the `missing` list. Add one row per name under the right section. The rows this plan introduces, with their classification:

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `store.phase_artifact` | `validate_review`, `authorize` (`approve_artifact`, `submit_plan`) | observed | `phase_artifacts`, written only by an accepted `submit_spec`/`submit_plan` |
| `store.read_blob` | `_check_submit` (the `### Task` shape check) | observed | content-addressed bytes the store holds |
| `store.run_state` | every front-half check | observed | the aggregate's own state |
| `cmd.payload['phase']` | `record_review`, `issue_review_brief` | observed | refused unless equal to `phase_of(store.run_state)` |
| `cmd.payload['artifact_hash']` | `submit_*`, `approve_artifact`, `record_review` | observed | refused unless the store holds it (submit) / equal to `store.phase_artifact` (approve, review) |
| `cmd.payload['findings_hash']` | `record_review` (front half) | observed | refused unless the store holds it |
| `cmd.payload['question_id']`, `['question']`, `['ruling']`, `['reasoning']`, `['cost_if_wrong']` | `record_model_question`, `record_model_ruling` | asserted | **Intentional and permanent.** A question and a ruling are the model's words; the kernel binds the epoch and phase they were asked in (`model_question` of the epoch, else refused) and records them verbatim. |
| `cmd.payload['answer']`, `['question_ids']`, `['text']`, `['findings']` | the human's commands | asserted | **Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path) and when (the epoch, the cursor); the content is the human's. |
| `cmd.payload['cursor_item_id']`, `['session_id']`, `['item_id']`, `['sha256']`, `['reason']`, `['verdict']`, `['reviewer']`, `['rejection']`, `['session']`, `['ended']`, `['issue']` | `park`, `record_prompt_item`, `dismiss_human_item`, `record_turn_ended`, `record_author_empty`, `revise_bundle` | observed where a kernel object binds them (`sha256` to a satisfied prompt's `body.artifact`; `rejection` to a `command_rejected` fact; `session` to the newest satisfied `sess-create`/`sess-prompt`; `issue` to the current bundle's hash), asserted otherwise | each row states which: `cursor_item_id` and `reason` are **Residual, §4** — the coordinator's reading of a listing, bound by the §8 cursor invariant test and by `PARK_REASONS`, not by the kernel |

Write the rows in the table's own format, one input per row, and move `payload['policy_version']`'s reason to: "**Residual, M1-4, back half only.** Compared against the run's `policy_frozen` seq for a front-half review (observed there); type-checked and compared to nothing for an implementation review." Add a `## Front half` section beneath the existing ones with the spec's §9 rows that are not authz inputs, as prose rows: policy derivation (observed), policy inputs (asserted, **Residual, C8**), bundle bytes (observed), the session's vendor (observed from the create's snapshot), the artefact's author (observed), the stop's teardown (asserted, **Residual, §3**), the runner's lock (asserted, **Residual, §5**).

Update the pinned set in `test_the_residuals_are_the_ones_we_know_about` to the new asserted names, and the count word in `docs/design/2026-08-23-v2-kernel-design.md` (`**<word> rows are asserted after Milestone 1**`) to the new total — count the rows, do not guess.

- [ ] **Step 2: ARCHITECTURE.md**

§3.2: add `sessions.py`, `seat.py`, `author.py`, `human.py`, `phases.py` to the coordinator's module list with one line each. §4: replace steps 2-4 with: 2. **Run start.** `create_run` with the fetched issue and the Project config; the kernel snapshots the issue, PUTs its canonical bytes, and freezes the policy. 3. **Operator fence, then label.** 4. **The front half.** `phases`: author rounds and review seats as sessions under the `v2_author_*` bundles, every session an effect with an obligation, every turn's end a fact; parks for the human (grill, gate, stall) exit `RC_PARKED` and the next pass resumes; `specified` and `planned` are reached only through a reviewer's accept, and published to the issue. 5. **Implementer, after `planned`.** Dispatched afresh, `start_implementation`, the state read back, then the session. Renumber the rest. Replace the `GAP — steps 5 and 7 both review` note's first sentence with a pointer to the front half having landed and the back half's duplication remaining. §6b: add `BIRCHER_PROJECT_ID` (optional; the omnigent Project whose `config.bircher` is the policy default) and `BIRCHER_WORKSPACES_ROOT` (`/workspaces`) rows. §9: gap 15's "blocked on" gains "raised to 5400 in E1 (Task 22)"; add gap 16: "author worktrees accumulate under `/workspaces/<run>/<gen>`; nothing prunes them (Out of scope)".

- [ ] **Step 3: `skills/muesli-loop/SKILL.md` §2**

Replace the first bullet ("If the item is under-specified … write a short spec and STOP") with: "Ambiguity is the front half's job: you are implementing a plan the kernel holds as accepted. Do not stop to re-specify. If the plan cannot be carried out for a reason it did not foresee — a dependency missing, a test that cannot be made to pass without changing the design — escalate as below." Keep the `.escalated` and no-op bullets.

- [ ] **Step 4: Run, then commit**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_provenance.py tests/kernel/test_frozen_bundle_doc.py -q`
Expected: PASS. Full suite: green.

```bash
git add docs/design/ARCHITECTURE.md docs/design/provenance-table.md docs/design/2026-08-23-v2-kernel-design.md v2/tests/kernel/test_provenance.py skills/muesli-loop/SKILL.md
git commit -m "docs(design): the front half in the architecture, the provenance table and the loop skill"
```

---
## Part E — Live proof

These four tasks run against the NAS deployment. Each names the human action it needs before it runs; none files an issue, opens a PR or pushes on its own — the runs do that through the kernel, as the design says. Every run's journal is kept (`BIRCHER_KERNEL_DB` per repo) and the proof script of Task 25 is run over each.

### Task 22: E1 — the deployment preconditions

**Files:**
- Create: `docs/design/front-half-live-log.md` (the record of E1-E4: what ran, when, the run ids, the proof output)
- Modify (in `abedegno/homelab`, by the human): `docker/omnigent/docker-compose.yml:181` `HARNESS_TURN_TIMEOUT_S: "480"` → `"5400"` for `omnigent-runner-bircher` (equal to `ITEM_TIMEOUT`, so the harness's idle watchdog never ends a turn the coordinator is still waiting on), and a container restart. **Human action: the restart, not while a wave is running** (ARCHITECTURE §9 gap 15).

- [ ] **Step 1: The codex-under-Landlock probe**

On the NAS, from `/workspaces/bircher-v2` at this branch, with the bircher-smoke checkout as `WORKDIR`:

```bash
cd /workspaces/bircher-v2
holder=$(bash -c 'source batch/run-queue.sh --source-only; _upload_bundle agents/v2_author_codex "probe"')
agent=$(bash -c 'source batch/run-queue.sh --source-only; _get_agent_id '"$holder")
mkdir -p /workspaces/probe-codex && git -C /workspaces/bircher-smoke worktree add --detach /workspaces/probe-codex HEAD
sid=$(curl -sSf -X POST "$OMNIGENT_SERVER/v1/sessions" -H 'content-type: application/json' \
  -d "{\"agent_id\":\"$agent\",\"host_id\":\"$(grep -m1 host_id: /root/.omnigent/config.yaml | awk '{print $2}')\",\"workspace\":\"/workspaces/probe-codex\",\"title\":\"probe-codex\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sSf -X POST "$OMNIGENT_SERVER/v1/sessions/$sid/events" -H 'content-type: application/json' \
  -d '{"type":"message","data":{"role":"user","content":[{"type":"input_text","text":"Create the directory bircher and write the single line hello to bircher/artifact.md, then end your turn."}]}}'
```

(If `run-queue.sh` has no `--source-only` guard, add one at the top of `main`'s dispatch: `[ "${1:-}" = "--source-only" ] && return 0` — the self-test already sources functions this way; check `:8430-8440` first.) Watch the session in the UI; when it ends, `cat /workspaces/probe-codex/bircher/artifact.md` must print `hello`. If the harness dies with a permission or egress error, read the runner's egress log for the denied host and add it to `agents/v2_author_codex/config.yaml`'s `egress_rules`, re-upload, and probe again; record each denied host and the final rule list in the live log. A harness that cannot run under Landlock at all is a blocker for the codex-authored rounds: record it, and Tasks 23-25 run with `default_author=claude` and the reviewer as the other vendor only where a codex *session* is not needed — which is nowhere, so E2-E4 wait on this.

- [ ] **Step 2: The timeout**

After the human restarts the runner with `HARNESS_TURN_TIMEOUT_S: "5400"`, confirm from inside the container: `docker exec omnigent-runner-bircher env | grep HARNESS_TURN_TIMEOUT_S` prints `5400`. Record it.

- [ ] **Step 3: Record and commit**

`docs/design/front-half-live-log.md` gets an `## E1` section: the date, the probe's session id, the artefact's bytes, the egress rules that were needed, the timeout confirmation.

```bash
git add docs/design/front-half-live-log.md agents/v2_author_codex/config.yaml
git commit -m "docs(record): E1 — codex under Landlock probed; runner turn timeout raised"
```

---

### Task 23: E2 — `abedegno/bircher-smoke` under `(human, {spec})`

**Human actions:** create one smoke issue with the labels `bircher:queued` and `bircher:grill`, whose body is deliberately vague (one sentence naming a small feature of the smoke repo and no file); answer the author's questions in the session when the run parks; approve when asked.

- [ ] **Step 1: Launch**

```bash
cd /workspaces/bircher-v2 && \
  BIRCHER_REPO=abedegno/bircher-smoke WORKDIR=/workspaces/bircher-smoke \
  BIRCHER_KERNEL_DB=/workspaces/bircher-v2/.run/kernel-smoke.db \
  bash batch/launch.sh --source issues --log .run/e2.log
```

- [ ] **Step 2: Observe the parks**

The first pass ends with `outcome=parked` and `queue/<code>.parked` naming reason `grill`; the author session in the UI ends with the questions. Answer them in that session. Launch again: the pass resumes, records one `human_answer` with every open question id, re-prompts the same session ("Answered; continue."), the spec is written and reviewed by the other vendor, and — `spec ∈ gates` — the run parks at `spec_accepted` with the gate prompt. Reply `approve` in the session. Launch again: `approve_artifact`, the plan round, its review, `planned`, the implementer, the PR, the merge.

- [ ] **Step 3: Prove**

Run Task 25's script over the run: `cd v2 && python -m tools.prove_front_half --db /workspaces/bircher-v2/.run/kernel-smoke.db --run-id <id> --server $OMNIGENT_SERVER --expect-human`. Every assertion but the zero-touch ones must hold (`--expect-human` admits `human_answer`, `human_ruling` and `parked` facts). Record the run id, the sequence of parks, and the proof output in the live log under `## E2`.

```bash
git add docs/design/front-half-live-log.md
git commit -m "docs(record): E2 — bircher-smoke under (human, {spec}) reached planned and merged"
```

---

### Task 24: E3 — muesli under `(model, {spec})`

**Human actions:** pick a genuinely vague open muesli issue from the pre-registered list (the list is in the live log's `## Pre-registered` section, written before E3 starts: issue numbers whose bodies name no file, function or acceptance test), label it `bircher:queued` (no other `bircher:` label — the default policy is `(model, {spec})`); approve when asked; nothing else.

- [ ] **Step 1: Launch**

```bash
cd /workspaces/bircher-v2 && \
  BIRCHER_KERNEL_DB=/workspaces/bircher-v2/.run/kernel-muesli.db \
  bash batch/launch.sh --source issues --log .run/e3.log
```

- [ ] **Step 2: Observe**

No park before `spec_accepted`: under `grill=model` the author rules its own questions (`model_ruling` facts). The gate park is the one touch. After `approve`, the run proceeds to `planned` and through the back half unattended.

- [ ] **Step 3: Prove and record**

`python -m tools.prove_front_half --db … --run-id <id> --server … --expect-approval` (admits exactly one `human_ruling {approve}` and one `parked {gate}`, nothing else human). Record under `## E3`.

```bash
git add docs/design/front-half-live-log.md
git commit -m "docs(record): E3 — muesli under (model, {spec}); the approval was the only touch"
```

---

### Task 25: E4 — muesli under `(model, {})` overnight, and the proof script

**Files:**
- Create: `v2/tools/__init__.py`, `v2/tools/prove_front_half.py`
- Test: `v2/tests/kernel/test_prove_front_half.py`

**Interfaces:**
- Produces: `python -m tools.prove_front_half --db PATH --run-id ID --server URL [--expect-human | --expect-approval] [--issues FILE]` — exit 0 when every assertion holds, 1 with each failing assertion on stderr; `prove_front_half.assert_journal(store, run_id, *, mode) -> list[str]` (the journal assertions; a list of failures) and `prove_front_half.assert_sessions(store, run_id, *, fetch) -> list[str]` (the server-side ones).

- [ ] **Step 1: Write the test**

`v2/tests/kernel/test_prove_front_half.py` — drives the journal assertions over a run the driver produced, and a fake server for the session assertions:

```python
import json

from kernel import front
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front
from tools import prove_front_half as prove


def _fake_fetch_for(s):
    """Lists every session the journal created, with its snapshot's agent_id
    and the prompted items as user-role items."""
    sessions = {}
    for row in front.satisfied_effects(s, "r-1", "sess-create"):
        snap = json.loads(row["external_object_id"])
        sessions[snap["id"]] = dict(snap, items=[])
    for row in front.satisfied_effects(s, "r-1", "sess-prompt"):
        ob = row["intent"]["obligation"]
        text = s.read_blob(row["intent"]["body"]["artifact"]).decode()
        sessions[ob["session"]]["items"].append({"id": row["external_object_id"], "role": "user",
                                                 "content": [{"type": "input_text", "text": text}]})

    def fetch(url):
        path = url.split("/", 3)[-1]
        if path == "v1/sessions":
            return json.dumps({"sessions": [{"id": k} for k in sessions]})
        sid = path.split("/")[2]
        if path.endswith("/items"):
            return json.dumps({"items": sessions[sid]["items"]})
        return json.dumps({k: v for k, v in sessions[sid].items() if k != "items"})
    return fetch, sessions


def test_a_clean_autonomous_run_passes(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session()); f.review_round("accept")
    f.author_round(PLAN_BYTES); f.review_round("accept")
    assert prove.assert_journal(s, "r-1", mode="zero") == []
    fetch, sessions = _fake_fetch_for(s)
    assert prove.assert_sessions(s, "r-1", fetch=fetch) == []


def test_each_assertion_fails_on_its_defect(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept"); f.approve()
    f.author_round(PLAN_BYTES); f.review_round("accept")
    fails = prove.assert_journal(s, "r-1", mode="zero")
    assert any("human_ruling" in x for x in fails) and any("model_ruling" in x for x in fails)
    assert prove.assert_journal(s, "r-1", mode="approval") == [x for x in
                                                              prove.assert_journal(s, "r-1", mode="approval")
                                                              if "model_ruling" in x]
    fetch, sessions = _fake_fetch_for(s)
    sid = next(iter(sessions))
    sessions[sid]["agent_id"] = "ag_switched"
    assert any("agent_id" in x for x in prove.assert_sessions(s, "r-1", fetch=fetch))
    sessions[sid]["agent_id"] = json.loads(front.satisfied_effects(s, "r-1", "sess-create")[0]["external_object_id"])["agent_id"]
    sessions[sid]["items"].append({"id": "typed", "role": "user", "content": [{"type": "input_text", "text": "hi"}]})
    assert any("prompt_item" in x for x in prove.assert_sessions(s, "r-1", fetch=fetch))
```

`Front._newest_author_session()` is the driver method Task 7 defines.

- [ ] **Step 2: Implement**

`v2/tools/prove_front_half.py` — every assertion of spec §8 *Proof assertions*, each a function returning a failure string or `None`:

```python
"""The §8 proof assertions over a run's journal and its sessions."""
from __future__ import annotations

import argparse
import json
import sys

from kernel import brief, front
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.policy import from_payload
from kernel.store import Store
from coordinator.session import _fetch, list_items

HUMAN = {EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING, EventKind.PARKED}


def assert_journal(store, run_id: str, *, mode: str = "zero") -> list[str]:
    fails = []
    facts = store.facts_for(run_id)
    kinds = [f.kind for f in facts]
    human = [f for f in facts if f.kind in HUMAN]
    if mode == "zero" and human:
        fails.append(f"human facts present: {[(f.kind, f.payload.get('ruling') or f.payload.get('reason')) for f in human]}")
    if mode == "approval":
        extra = [f for f in human if not ((f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "approve")
                                          or (f.kind == EventKind.PARKED and f.payload.get("reason") == "gate"))]
        if extra:
            fails.append(f"human facts beyond the approval: {[f.kind for f in extra]}")
    if EventKind.EFFECT_RECONCILED in kinds or store.reconciliation_evidence(run_id) is not None:
        fails.append("the run was reconciled or halted")
    if EventKind.MODEL_RULING not in kinds:
        fails.append("no model_ruling: the author did not rule on a question")
    subs = {f.payload["phase"]: f.payload["hash"] for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)}
    if set(subs) != {"spec", "plan"} or subs["spec"] == subs["plan"]:
        fails.append(f"artifact_submitted for spec and plan with different hashes: {subs}")
    roles = {d["generation"]: d for d in store.dispatches_for(run_id)}
    creates = front.satisfied_effects(store, run_id, "sess-create")
    seen_obs = []
    for row in creates:
        snap = json.loads(row["external_object_id"])
        d = roles.get(row["generation"])
        if d and d["role"] in Role.SEATS and front.vendor_of(snap.get("agent_name")) != d["actor"]:
            fails.append(f"{row['idempotency_key']}: snapshot names {snap.get('agent_name')!r}, dispatch actor {d['actor']!r}")
        ob = row["intent"]["obligation"]
        if ob in seen_obs:
            fails.append(f"two sess-creates share an obligation: {ob}")
        seen_obs.append(ob)
    prompts = front.satisfied_effects(store, run_id, "sess-prompt")
    stops = front.satisfied_effects(store, run_id, "sess-stop")
    prompted = {r["intent"]["obligation"]["session"] for r in prompts}
    stopped = {r["intent"]["obligation"]["session"] for r in stops}
    for row in creates:
        sid = json.loads(row["external_object_id"])["id"]
        if sid not in prompted and sid not in stopped:
            fails.append(f"session {sid} was neither prompted nor stopped")
    for row in prompts:
        te = front.turn_ended_for(store, run_id, row["idempotency_key"])
        if te is None:
            fails.append(f"prompt {row['idempotency_key']} has no turn_ended")
        elif not front.stop_satisfied_for(store, run_id, te.id):
            fails.append(f"turn_ended {te.id} has no satisfied stop")
    for v in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT):
        if v.payload.get("ruling") != "review_ruling" or v.payload.get("phase") not in front.FRONT_PHASES:
            continue
        b = front.brief_for(store, run_id, v.payload["generation"])
        if b is None or b.payload["epoch"] != v.payload["epoch"]:
            fails.append(f"review_ruling gen {v.payload['generation']} has no brief in its epoch")
            continue
        bp = b.payload
        data = store.read_blob(bp["brief_hash"])
        pol = from_payload(store.newest_fact(run_id, EventKind.POLICY_FROZEN).payload["policy"])
        rendered = brief.render(phase=bp["phase"], artefact=store.read_blob(bp["artifact_hash"]),
                                bundle=store.read_blob(bp["context_bundle_hash"]),
                                spec=None if bp["spec_hash"] is None else store.read_blob(bp["spec_hash"]),
                                policy=pol, base_sha=bp["base_sha"], template=bp["brief_template"])
        if data != rendered:
            fails.append(f"brief {bp['brief_hash'][:12]} is not render() over its named objects")
        if v.payload["artifact_hash"] != bp["artifact_hash"]:
            fails.append("review_ruling artifact_hash differs from its brief's")
        fh = v.payload.get("findings_hash")
        if fh and brief.hash8(bp["artifact_hash"]) not in store.read_blob(fh).decode("utf-8", "replace"):
            fails.append("the reviewer's verdict line does not name the brief's hash8")
    return fails


def assert_sessions(store, run_id: str, *, fetch=_fetch) -> list[str]:
    fails = []
    listed = {x["id"] for x in json.loads(fetch(f"{store_server}/v1/sessions")).get("sessions", [])} \
        if False else None
    prompt_items = {p.payload["item_id"] for p in store.facts_of_kind(run_id, EventKind.PROMPT_ITEM)}
    prompt_hashes = {r["intent"]["body"]["artifact"] for r in front.satisfied_effects(store, run_id, "sess-prompt")}
    briefs = {b.payload["generation"]: b.payload["brief_hash"]
              for b in store.facts_of_kind(run_id, EventKind.REVIEW_BRIEF_ISSUED)}
    roles = {d["generation"]: d["role"] for d in store.dispatches_for(run_id)}
    prompts_by_session = {}
    for r in front.satisfied_effects(store, run_id, "sess-prompt"):
        prompts_by_session.setdefault(r["intent"]["obligation"]["session"], []).append(r)
    stops = {r["intent"]["obligation"]["session"] for r in front.satisfied_effects(store, run_id, "sess-stop")}
    for row in front.satisfied_effects(store, run_id, "sess-create"):
        snap = json.loads(row["external_object_id"])
        sid = snap["id"]
        try:
            live = json.loads(fetch(f"{{server}}/v1/sessions/{sid}".replace("{server}", "")))
        except Exception as exc:
            fails.append(f"session {sid} is not listed: {exc}")
            continue
        if live.get("agent_id") != snap["agent_id"]:
            fails.append(f"session {sid}: agent_id {live.get('agent_id')!r} != snapshot {snap['agent_id']!r}")
        items = list_items("", sid, fetch=fetch)
        users = [it for it in items if it["role"] == "user"]
        for it in users:
            if it["id"] not in prompt_items and content_hash(it["text"].encode()) not in prompt_hashes:
                fails.append(f"session {sid}: user item {it['id']} is no prompt_item and matches no prompt effect")
        if roles.get(row["generation"]) == Role.REVIEWER:
            ps = prompts_by_session.get(sid, [])
            if ps:
                gen = ps[0]["generation"]
                if not users or content_hash(users[0]["text"].encode()) != briefs.get(gen):
                    fails.append(f"reviewer session {sid}: first user item is not the brief of generation {gen}")
            else:
                if users or sid not in stops:
                    fails.append(f"reviewer session {sid}: unprompted, yet has items or no stop")
    return fails
```

Replace the two placeholder-looking lines in `assert_sessions` (`listed = … if False else None` and the `{{server}}` string) with a `server` parameter: `def assert_sessions(store, run_id, *, server: str = "", fetch=_fetch)` and `fetch(f"{server}/v1/sessions/{sid}")`, `list_items(server, sid, fetch=fetch)` — the test passes `server=""`. Then `main`:

```python
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--run-id", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--expect-human", action="store_true"); ap.add_argument("--expect-approval", action="store_true")
    ap.add_argument("--issues", default=None, help="the pre-registered issue list; the run's issue must be in it")
    a = ap.parse_args(argv)
    store = Store.open(a.db)
    mode = "human" if a.expect_human else "approval" if a.expect_approval else "zero"
    fails = assert_journal(store, a.run_id, mode=mode) + assert_sessions(store, a.run_id, server=a.server)
    if a.issues:
        enq = store.newest_fact(a.run_id, EventKind.RUN_ENQUEUED)
        snap = json.loads(store.read_blob(enq.payload["bundle_hash"]))
        allowed = {int(x) for x in open(a.issues).read().split()}
        if snap["number"] not in allowed:
            fails.append(f"issue #{snap['number']} is not in the pre-registered list")
    for f in fails:
        print(f, file=sys.stderr)
    print("PROOF: " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
```

`mode="human"` admits every human fact and checks the rest. Note `--issues` also requires the body to name no file, function or acceptance test — that is the pre-registration's own rule, applied when the list is written (Task 24), not something the script can read from a body.

- [ ] **Step 3: Run the test, commit the script**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_prove_front_half.py -q`
Expected: PASS.

```bash
git add v2/tools/__init__.py v2/tools/prove_front_half.py v2/tests/kernel/test_prove_front_half.py
git commit -m "feat(tools): the front-half proof script"
```

- [ ] **Step 4: E4 — the overnight run**

**Human actions:** write the pre-registered list to the live log (`## Pre-registered`: issue numbers whose bodies name no file, function or acceptance test) and label one of them `bircher:queued` and `bircher:autonomous`; then nothing until morning.

```bash
cd /workspaces/bircher-v2 && \
  BIRCHER_KERNEL_DB=/workspaces/bircher-v2/.run/kernel-muesli.db \
  bash batch/launch.sh --source issues --log .run/e4.log
```

In the morning: `python -m tools.prove_front_half --db /workspaces/bircher-v2/.run/kernel-muesli.db --run-id <id> --server $OMNIGENT_SERVER --issues docs/design/preregistered.txt` prints `PROOF: PASS` and the PR merged. Record the run id, the scorecard row, and the proof output under `## E4`. That is the done criterion of the spec's *The claim*.

```bash
git add docs/design/front-half-live-log.md docs/design/preregistered.txt
git commit -m "docs(record): E4 — muesli under (model, {}) merged with zero touches; proof passed"
```

---

## Self-review notes

Recorded here so the reader sees what was checked, per the writing-plans skill:

- **Spec coverage.** §1 → Tasks 5, 6, 12. §2 States/commands/refusals → Tasks 7-12 (every row of the commands table has a task; every refusal has a test named in its task). §2 Brief → 11. §2 Grill → 9. §2 Bundle revision → 5, 9, 20. §3 loop → 19; Sessions are effects/obligations/reconciliation → 3, 4, 15, 16; Author round → 17; Review round → 18; the body never rides argv → 3, 15; the contract → 2; the turn's start and end → 10, 14, 17; Rotation → 17, 18; Artefacts → 16. §4 → 19. §5 → 19, 20. §6 → 20, 21. §7 → each row's task is named in Task 19's loop outcomes and Task 17/18's outcomes. §8 kernel/coordinator/runner tests → the tests of each task; proof → 25. §9 → 21. §10 → the file structure. §11 → the closure map.
- **Placeholder scan.** No "TBD", "TODO", "implement later", "add error handling", "similar to Task N" remain; every code step carries its code.
- **Type consistency.** `Front` methods (`_dispatch`, `_create`, `_session`, `_prompt`, `_end_turn`, `_cmd`, `_journal`, `_newest_id`, `_newest_author_session`, `human`, `approve`, `grant`, `answer`, `direct`, `ask_round`, `revise`, `author_round(resume=)`, `review_round(**override)`) are defined in Tasks 7-9 and used with those names after; `front.*` queries are defined before use (Tasks 6, 7, 9, 10, 11, 12); `seat.run_turn`'s signature (Task 17, extended with `reuse_generation` in Task 18) matches its callers; `phases.Ctx` fields match every construction site; `ReviewOutcome.generation` is set in every constructor call in `review_round`.
