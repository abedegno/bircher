# Bircher v2 Shaping Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before the spec phase, an author decides whether an issue is one piece of work or an epic; an epic's slice plan is reviewed cross-vendor, gated by default, filed as child issues the scheduled wave works one by one, and the epic closes when its children have — every decision, filing and closure a kernel fact, every create bound at `perform`.

**Architecture:** The kernel (`v2/kernel/`) gains four states (`shaping`, `slices_submitted`, `slices_accepted`, `sliced`) that every run now starts in, one artefact grammar (`slices.py`), eight commands, one effect class (`issue_create`) and a per-obligation-kind precondition and binding table `perform` checks before any filing effect reaches GitHub. The coordinator (`v2/coordinator/`) gains a shaping round (a ruling or a slice plan), reuses the review round and the gate for the slice plan, files the children in four idempotent passes (`file_owed`), and sweeps sliced parents each wave (`sweep_sliced`). The runner (`batch/`) resumes and scores the new states, queues an unfinished filing from the journal, and calls the sweep before generating the queue. The proof (`v2/tools/prove_front_half.py`) gains four assertions over a parent and its children.

**Tech Stack:** Python 3.13 (CI; `requires-python >=3.11`), SQLite via `kernel.store.Store`, pytest 8.3.4 + pyyaml 6.0.2 run as `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`, bash 5 for `batch/` (`bash batch/run-queue.sh --self-test`), `gh` and `curl` through the kernel executor.

**Spec:** `docs/superpowers/specs/2026-09-09-shaping-phase-design.md` (main, commit `b736208`, revision 15, cross-vendor PASS at round 14). Section numbers below are the spec's. It extends `docs/superpowers/specs/2026-09-05-front-half-design.md`, whose plan is `docs/superpowers/plans/2026-09-06-front-half.md`; the conventions of that plan (the `Front` driver, the provenance table, the mutation discipline, the self-test) carry over unchanged. Where this plan had to rule on something the spec left open, the ruling is listed under *Rulings taken while planning* and the spec text it amends is named.

## Global Constraints

- **Observed, never asserted** (front-half §1, shaping §2): every guard reads the journal — facts, satisfied effect rows, the accepted plan's bytes — never a caller's payload except the fields a command exists to carry. The sweep's readings of GitHub (`closed_at`, `state`, `observed_at`, `parent_state`) are the four new declared residuals.
- **The effect is bound, not only the fact after it** (§2 *What `perform` refuses*, *Bound to the effect*, *One satisfied effect per obligation*): every argv shape this design adds is admitted only with its obligation kind; every kind is admitted on one class and operation, against the argv's actual targets; one satisfied effect per obligation. All three are checked in `perform`, before the halt check, before the executor.
- **The queue label is the last thing a child gets** (§3 pass 3, ruling 14): a child is created with `bircher:slice` and the inherited policy labels, never `bircher:queued`; the label is added only after every create and every link of the plan is satisfied, and the kernel refuses it earlier.
- **A run that entered `sliced` ends only as `sliced`** (§2, ruling 15): `record_run_outcome` accepts no other outcome from `sliced`; `cancel_run` is the other exit.
- **An issue is sliced once, by the journal** (§2, ruling 12): `create_run` refuses an issue whose earlier run holds any `issue_create` row not reconciled not-delivered, whatever that run's state.
- **The shaping states stay out of `FRONT_HALF_STATES`** (§2 *Which state set each site reads*, ruling 13): membership is stated per site; `record_model_question` and `record_model_ruling` are refused from every shaping state; `question_id: "shape"` is reserved to `record_one_piece`.
- **Every `revise_bundle` lands in `shaping`** (§2, ruling 11), from every legal state.
- **The body never rides argv**: a child's body is an artefact the store holds (`{"artifact": <sha256>}`), written to a file by the executor after the contract check and passed as `--body-file` (ruling 1 below).
- The definition of coarse is `kernel/slices.py::COARSE`, verbatim from §1; the shape author's brief and the reviewer's brief both render it from there, never a second copy.
- Facts are append-only and every kind is declared in `kernel/events.py::SCHEMA_VERSIONS`; the six kinds this plan uses are declared in Task 1.
- The suite baseline is **1439 passed, 3 skipped** (`cd v2 && uv run --with pytest --with pyyaml python -m pytest -q` on `b736208`); every task ends green, and `bash batch/run-queue.sh --self-test` ends `self-test OK` after every task that touches `batch/`.
- Commit messages carry **no AI attribution** — no `Co-Authored-By`, no `Claude-Session`, no "Generated with" — in this repo. Commit messages use the repo's `type(scope): summary` form.
- Mutation discipline (§8): commit before each mutation you run against a test; a test that stays green under the mutation named in its task is not done. The review package for each task states which line was mutated and which test went red.
- Nothing in Parts A–D files a GitHub issue, opens a PR, or pushes; Part E names the human action each live step needs before it runs.
- Never alias `cmd.payload` to a local name inside `kernel/authz.py`; write `cmd.payload.get(...)` at every read. `v2/tests/kernel/test_provenance.py` parses `authz.py`: every `cmd.payload[...]`/`.get('...')` read and every `store.<method>` call there needs a row in `docs/design/provenance-table.md`, and the asserted set is pinned by name. A task that adds such a read adds its row in the same commit.
- Every `run-queue.sh` edit above the cited lines shifts the line citations in `docs/design/effect-site-inventory.md` and `docs/design/scar-effect-matrix.md`; `test_scar_equivalence.py`, `test_routing.py` and `test_routed_contracts.py` red on a stale citation. Task 10 renumbers them.
- `v2/tests/execution/test_env_boundary_contract.py` pins the environment variables the shell and Python share; this plan adds none.

## Rulings taken while planning

1. **A child's body is an artefact, not `body.text`.** §2 says the executor writes the body file "from the intent's `body.text`". The kernel's intent bodies are `{"artifact": <sha256>}` or `{"event": "stop_session"}` (`kernel/effects.py::_check_body`), and the binding rule in §2 already compares the body file's bytes against `slices.render_child`. The create's intent carries `body: {"artifact": <sha256 of the rendered body>}`; the executor writes the blob raw to a temp file and appends `--body-file <path>` after the contract check, exactly as it appends `--data-binary @<file>` for a session event. Amend §2 *The effect class*: "from the intent's `body.text`" → "from the artefact the intent's `body.artifact` names".
2. **`Store.create_run` takes the initial state.** `Store.create_run` writes `queued` into the row and the `run_started` fact (`store.py:98-106`); the front half's `enqueue.create_run` is what §2 means by `create_run`. `Store.create_run(..., state="queued")` gains the parameter; `enqueue.create_run` passes `"shaping"`; the v1 `enqueue()` path (spec and plan bytes supplied by a person) keeps `queued`, because a run whose spec and plan already exist has nothing to shape. `projection.project` reads the state from the fact's payload as it does today.
3. **The test driver shapes by default.** `tests/kernel/front.py::Front` builds a run and drives it through the front half in 38 test files. With every run now born in `shaping`, `Front.__init__` performs a one-piece shaping round (`shape_round()`) unless `shape=False`, so a test that wants a run at `queued` gets one by the only path the kernel admits, and a test of the shaping phase itself passes `shape=False`. The cost is one author seat and one session per driver-built run; Task 3's migration rules say what moves.
4. **`advance_ungated` requires the operator role, not the actor name.** §2 says "an `operator` dispatch of the coordinator". The runner's operator dispatch is `actor="runner"`, the coordinator's `actor="coordinator"`, and `kernel/authz.py` binds roles, not actor names, everywhere else. The role is what is checked; the coordinator is the only caller.
5. **The effect check is one function in `kernel/filing.py`.** §2's precondition table, binding table, mandate and uniqueness are all reads of what the kernel holds; `perform` calls `filing.check_filing_effect(store, run_id, generation, effect_class, idempotency_key, intent)` after the contract check and before the halt check, for every effect class. A replay of a confirmed key is exempt from the uniqueness rule (it returns the row, as today).
6. **The create's confirmation reads the issue back inside the executor.** §2 says the confirmation reads `gh api /repos/<repo>/issues/<n>` for the database id. The executor does it in the same `executor()` call, after `gh issue create` returns the URL; a failure of the read-back raises and the effect is `uncertain`, reconciled by `--delivered` with the `{url, number, id}` JSON the delivered-form validates. The confirmed row's `external_object_id` is that JSON.
7. **The sweep's GitHub reads are injected, not routed.** Reads are not effects (front-half §3 *Effects*). `sweep_sliced` takes a `gh_json(argv) -> dict` callable; the CLI wires `subprocess` + `gh --json`; tests inject a dict-backed fake. `is_unblocked`'s reads in `issues-to-queue.sh` stay shell.
8. **The umbrella comment carries the plan hash's `hash8` in its first line** (§3), and the completion comment carries the same prefix: `bircher: sliced complete`. Both are refused into the bundle by the one prefix `bircher: sliced ` (§3, seventh prefix).
9. **`closed_at` and `observed_at` are ISO-8601 strings the sweep read or minted**; the kernel checks shape (non-empty string) and records them. They are asserted residuals alongside `state` and `parent_state`.
10. **Rounds in the shaping phase count as every phase's do**: `rounds_used`/`round_bound` are keyed by phase (`front.py:32-53`), so `max_rounds` bounds the slice plan's revisions with no change; `seat_bound` is run-wide, unchanged.
11. **The shape author's skill directory is `skills/shape-author/`** (§3), mapped from phase `slices` by a one-line table in `author_brief`; `spec-author` and `plan-author` keep their phase-named directories.
12. **The refused mint's note is the refusal's first line.** `_kernel_run_start` prints the Python's stderr into its own warning; `run_item` captures the function's output and puts its first 200 characters in the scorecard note (§5 *The refused mint*).

## §8 closure map

| §8 item | Task |
|---|---|
| the grammar cases (no scope, two- and seven-sentence scopes, missing non-goals, dependency on slice 9, self-dependency, three-slice cycle, six slices, one slice) | Task 1 |
| `PHASES` gains `slices`; gates default `{slices, spec}`; `bircher:autonomous` clears | Task 2 |
| every row of the state table; every extended from-set; every membership row; the human correction landing in `shaping`; the reviewer's ungated accept landing in `slices_accepted`; `revise_bundle` landing in `shaping`; `approve_artifact` → `sliced`; the ruling after a direction / after a rejected plan / from `slices_accepted`; the second ruling; the reserved `shape` id; the ungated advance under a gated policy; the slice-of-a-slice from `policy_frozen` | Task 3 |
| `record_slice_filed` against an unconfirmed effect, a wrong obligation, a duplicate; `record_filing_complete` with a link / queue label / umbrella label unsatisfied; `record_slice_closed` before completion, for an unfiled slice, for a currently closed one, again after a reopen; `record_slice_reopened` refusals; the outcome with a slice open / reopened / without `filing_complete` / without a same-generation observation / with `parent: closed` and no close / with `parent: open` and a satisfied close; `record_run_outcome(failed)` and `(merged)` refused from `sliced`; `create_run` refused after a create attempt (ended, cancelled, halted) and admitted otherwise; `revise_bundle` refused from `sliced` | Task 4 |
| every row of the `perform` table; every row of the binding table; the mandate; uniqueness; the `ISSUE_CREATE` contract refusing the three runner labels; `render_child` strict; `is_front_verdict` and `revisions_used` | Task 5 (and Task 3 for the verdict filter) |
| both endings of the shaping round; the both-files case; the ruling grammar's edge cases | Task 6 |
| the review round with a `slices` brief carrying the coarse paragraph; the gate park and its notice; the gated path from approval into filing in one loop | Task 7 |
| `file_owed` with a crash planted at each boundary; no child queued while a link is unsatisfied; dependency order; a second pass issuing no command; the umbrella comment's exact text; `render_child`'s exact text and labels | Task 8 |
| `sweep_sliced` closing / leaving / skipping; `merged` vs `closed`; three firings; a failed read mid-sweep | Task 9 |
| the runner self-test cases | Task 10 |
| each proof assertion red on its planted defect; the amended existing checks; the superseded-epoch case; the duplicate create | Task 11 |

## File structure

Kernel (`v2/kernel/`): `events.py` (six kinds), `slices.py` **new** (grammar, ruling grammar, renderers, `COARSE`), `policy.py` (`PHASES`, default gates), `authz.py` (`SHAPING_STATES`, the state table, eight commands' refusals), `commands.py` (the command set, side facts), `front.py` (`FRONT_PHASES`, filing/closing queries), `projection.py` (`FRONT_PHASES`), `enqueue.py` (`shaping`, the sliced-once refusal), `store.py` (`create_run(state=)`, `run_base_repo`), `brief.py` (phase `slices`), `effect_class.py` + `contract.py` (`ISSUE_CREATE`, the blocked-by rule, `forbid_value`), `effects.py` (seven delivered-forms, the `filing` hook), `filing.py` **new** (mandate, preconditions, binding, uniqueness), `cli.py` (the executor's `--body-file` and read-back), `bundle.py` (seventh prefix).

Coordinator (`v2/coordinator/`): `shape.py` **new** (the shaping round), `author.py` (`author_brief` phase `slices`), `phases.py` (`_LOOP_STATES`, the branches, `derived_session_obligation`), `human.py` (`classify_batch` states), `filing.py` **new** (`file_owed`), `sweep.py` **new** (`sweep_sliced`), `cli.py` (`sweep-sliced`).

Runner and skills: `batch/run-queue.sh`, `batch/issues-to-queue.sh`, `batch/lib/kernel-client.sh`, `skills/shape-author/SKILL.md` **new**.

Proof and docs: `v2/tools/prove_front_half.py`, `docs/design/provenance-table.md`, `docs/design/effect-site-inventory.md`, `docs/design/scar-effect-matrix.md`, `docs/design/ARCHITECTURE.md`, the spec (amendments from the rulings above).

Tests: `v2/tests/kernel/front.py` (the driver gains `shape_round`, `slices_round`, `advance`, `to_sliced`, `SLICES_BYTES`), `v2/tests/coordinator/fake_omnigent.py` (a `gh` that answers issue creates, reads and links), new files named per task.

---
## Part A — Kernel

### Task 1: Fact kinds, the slice-plan grammar, the ruling grammar, the renderers

**Files:**
- Modify: `v2/kernel/events.py` (class `EventKind`, dict `SCHEMA_VERSIONS`)
- Create: `v2/kernel/slices.py`
- Modify: `v2/kernel/store.py:87-106` (`create_run`), append `run_base_repo` after `run_base_sha`
- Test: `v2/tests/kernel/test_slices_grammar.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `EventKind.ARTIFACT_ADVANCED = "artifact_advanced"`, `SLICE_FILED = "slice_filed"`, `FILING_COMPLETE = "filing_complete"`, `SLICE_CLOSED = "slice_closed"`, `SLICE_REOPENED = "slice_reopened"`, `CHILDREN_OBSERVED_CLOSED = "children_observed_closed"`, each with `SCHEMA_VERSIONS[kind] == 1`.
  - `slices.COARSE: str` (§1's definition, verbatim), `slices.MIN_SLICES = 2`, `MAX_SLICES = 5`, `MIN_SENTENCES = 3`, `MAX_SENTENCES = 6`, `RULING_LINE = "Ruling: one piece"`, `SHAPE_QUESTION = "shape"`.
  - `slices.PlanError(ValueError)`; `slices.Slice(number, title, scope, non_goals, depends_on: tuple[int, ...])`; `slices.Plan(title, preamble, slices: tuple[Slice, ...])` with `.by_number() -> dict[int, Slice]` and `.edges() -> list[tuple[int, int]]` (`(slice, blocker)` pairs in slice order).
  - `slices.parse(data: bytes) -> Plan` (raises `PlanError`), `slices.sentences(text) -> int`, `slices.topo_order(plan) -> list[int]`.
  - `slices.Ruling(reasoning, cost_if_wrong)`; `slices.parse_ruling(data: bytes) -> Ruling | None`.
  - `slices.render_child(s: Slice, parent: int, hash8: str, filed: dict[int, int]) -> tuple[str, str]` (title, body; raises `PlanError` when a blocker has no filed number).
  - `slices.umbrella_body(hash8, plan, filed: dict[int, int]) -> str`; `slices.completion_body(plan, filed, states: dict[int, str]) -> str`.
  - `slices.inherited_labels(labels) -> list[str]` — the policy labels a child inherits, sorted.
  - `Store.create_run(*, run_id, base_repo, base_sha, state="queued")`; `Store.run_base_repo(run_id) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_slices_grammar.py
"""Task 1: the slice-plan grammar, the ruling grammar and the renderers
(spec §2 *The artefact*, §3 *The shaping round*, §3 *Filing the children*)."""
import pytest

from kernel import slices
from kernel.events import SCHEMA_VERSIONS, EventKind
from kernel.store import Store

GOOD = b"""# Slicing the epic

Why three pieces: the store, the API, the client.

## Slice 1: The store
Scope: Add the table. Add the migration. Add the store reads.
Non-goals: the API.
Depends on: none

## Slice 2: The API
Scope: Add the endpoint. Wire it to the store. Cover it with a handler test.
Non-goals: the client.
Depends on: 1

## Slice 3: The client
Scope: Add the page. Call the endpoint. Show the result. Cover it with a component test.
Non-goals: styling.
Depends on: 1, 2
"""


def _plan(**edits) -> bytes:
    text = GOOD.decode()
    for old, new in edits.items():
        text = text.replace(old.replace("_", " "), new)
    return text.encode()


def test_new_kinds_declared_with_schema_version():
    for attr, value in {"ARTIFACT_ADVANCED": "artifact_advanced", "SLICE_FILED": "slice_filed",
                        "FILING_COMPLETE": "filing_complete", "SLICE_CLOSED": "slice_closed",
                        "SLICE_REOPENED": "slice_reopened",
                        "CHILDREN_OBSERVED_CLOSED": "children_observed_closed"}.items():
        assert getattr(EventKind, attr) == value
        assert SCHEMA_VERSIONS[value] == 1


def test_good_plan_parses():
    p = slices.parse(GOOD)
    assert p.title == "Slicing the epic"
    assert p.preamble.startswith("Why three pieces")
    assert [s.number for s in p.slices] == [1, 2, 3]
    assert p.slices[1].depends_on == (1,) and p.slices[2].depends_on == (1, 2)
    assert p.slices[0].non_goals == "the API."
    assert p.edges() == [(2, 1), (3, 1), (3, 2)]
    assert slices.topo_order(p) == [1, 2, 3]


def test_sentences_count_full_stops_that_end_a_sentence():
    assert slices.sentences("One. Two. Three.") == 3
    assert slices.sentences("Use e.g. this. Then 3.5 of that. Done.") == 3
    assert slices.sentences("No stop") == 0


@pytest.mark.parametrize("bad, why", [
    (GOOD.replace(b"Scope: Add the table. Add the migration. Add the store reads.\n", b""), "missing"),
    (GOOD.replace(b"Add the table. Add the migration. Add the store reads.", b"Add the table. Add it."), "2 sentence"),
    (GOOD.replace(b"Add the table. Add the migration. Add the store reads.",
                  b"A. B. C. D. E. F. G."), "7 sentence"),
    (GOOD.replace(b"Non-goals: the API.\n", b"Non-goals:\n"), "Non-goals is empty"),
    (GOOD.replace(b"Non-goals: the API.\n", b""), "missing"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: 9"), "not in the plan"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: 3"), "depends on itself"),
    (GOOD.replace(b"## Slice 1: The store\nScope: Add the table. Add the migration. Add the store reads.\nNon-goals: the API.\nDepends on: none",
                  b"## Slice 1: The store\nScope: Add the table. Add the migration. Add the store reads.\nNon-goals: the API.\nDepends on: 3"), "cycle"),
    (GOOD.replace(b"## Slice 2: The API", b"## Slice 5: The API"), "1..N"),
    (b"# T\n\n## Slice 1: only\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n", "1 slices"),
    (GOOD + b"".join(b"\n## Slice %d: more\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n" % n for n in (4, 5, 6)), "6 slices"),
    (b"# T\n\nno headings at all\n", "not a slice plan"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: two"), "slice numbers"),
])
def test_refusals_name_why(bad, why):
    with pytest.raises(slices.PlanError, match=why):
        slices.parse(bad)


def test_ruling_grammar():
    ok = b"Ruling: one piece\nReasoning: one coherent change\nto one file.\nCost if wrong: a spec round.\n"
    r = slices.parse_ruling(ok)
    assert r == slices.Ruling("one coherent change to one file.", "a spec round.")
    assert slices.parse_ruling(b"  \n  Ruling: one piece\n  Reasoning: r\n  Cost if wrong: c\n") is not None
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning: r\n") is None            # no cost
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning:\nCost if wrong: c\n") is None   # empty block
    assert slices.parse_ruling(b"preface\nRuling: one piece\nReasoning: r\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"Ruling: two pieces\nReasoning: r\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning: r\nReasoning: again\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"") is None


def test_render_child_is_exact_and_strict():
    p = slices.parse(GOOD)
    title, body = slices.render_child(p.slices[2], 12, "abcd1234", {1: 40, 2: 41})
    assert title == "The client"
    assert body == ("Slice 3 of #12 (bircher shaping abcd1234)\n\n"
                    "Add the page. Call the endpoint. Show the result. Cover it with a component test.\n\n"
                    "Non-goals: styling.\n\nDepends on: #40, #41\n")
    _, first = slices.render_child(p.slices[0], 12, "abcd1234", {})
    assert first.endswith("Depends on: none\n")
    with pytest.raises(slices.PlanError, match="no filed issue"):
        slices.render_child(p.slices[2], 12, "abcd1234", {1: 40})


def test_umbrella_and_completion_bodies():
    p = slices.parse(GOOD)
    filed = {1: 40, 2: 41, 3: 42}
    assert slices.umbrella_body("abcd1234", p, filed) == (
        "bircher: sliced abcd1234\n\n- #40 Slice 1: The store\n- #41 Slice 2: The API\n- #42 Slice 3: The client\n")
    assert slices.completion_body(p, filed, {1: "merged", 2: "merged", 3: "closed"}) == (
        "bircher: sliced complete\n\n- #40 Slice 1: The store — merged\n- #41 Slice 2: The API — merged\n"
        "- #42 Slice 3: The client — closed\n")


def test_inherited_labels_are_the_policy_labels_only():
    assert slices.inherited_labels(["bircher:queued", "bircher:grill", "priority:p1", "bircher:autonomous",
                                    "bircher:running", "bircher:gate-plan"]) == [
        "bircher:autonomous", "bircher:gate-plan", "bircher:grill"]


def test_coarse_is_the_specs_definition():
    assert slices.COARSE.startswith("A slice is a coarse, coherent, independently reviewable capability")
    assert "three to five slices to an epic" in slices.COARSE


def test_store_create_run_takes_a_state(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40, state="shaping")
    assert s.run_state("r-1") == "shaping"
    assert s.newest_fact("r-1", EventKind.RUN_STARTED).payload["state"] == "shaping"
    assert s.run_base_repo("r-1") == "o/r"
    s.create_run(run_id="r-2", base_repo="o/r", base_sha="0" * 40)
    assert s.run_state("r-2") == "queued"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_slices_grammar.py -q`
Expected: FAIL — `ImportError: cannot import name 'slices'` and `AttributeError: ARTIFACT_ADVANCED`.

- [ ] **Step 3: Declare the kinds**

In `v2/kernel/events.py`, after `PROMPT_ITEM = "prompt_item"` in `EventKind`:

```python
    # The shaping phase (shaping spec §2 *Facts*). Each is written by exactly
    # one command; see kernel/commands.py::_side_fact.
    ARTIFACT_ADVANCED = "artifact_advanced"
    SLICE_FILED = "slice_filed"
    FILING_COMPLETE = "filing_complete"
    SLICE_CLOSED = "slice_closed"
    SLICE_REOPENED = "slice_reopened"
    CHILDREN_OBSERVED_CLOSED = "children_observed_closed"
```

and after `EventKind.PROMPT_ITEM: 1,` in `SCHEMA_VERSIONS`:

```python
    EventKind.ARTIFACT_ADVANCED: 1,
    EventKind.SLICE_FILED: 1,
    EventKind.FILING_COMPLETE: 1,
    EventKind.SLICE_CLOSED: 1,
    EventKind.SLICE_REOPENED: 1,
    EventKind.CHILDREN_OBSERVED_CLOSED: 1,
```

- [ ] **Step 4: Write `kernel/slices.py`**

```python
# v2/kernel/slices.py
"""The slice plan: one grammar, one parser, one set of renderers (shaping
spec §2 *The artefact*, §3 *The shaping round*, §3 *Filing the children*).

Every reader of a plan -- the submit refusals, the outcome guard, the filing
step, the effect binding, the child renderer, the proof -- goes through
`parse`, so there is one reading of a plan and not five. Nothing here reads
the store or a payload; these are pure functions over bytes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: The definition of coarse, verbatim from spec §1. The shape author's brief
#: and the slice reviewer's brief both render it from here.
COARSE = (
    "A slice is a coarse, coherent, independently reviewable capability: a few "
    "related files, on the order of one to four hundred lines, that deliver a "
    "working sub-feature a reviewer can see working end to end. It is not an "
    "atomic plan-step — not a migration, a single struct, one helper, one store "
    "method or one endpoint on its own. Every slice is a full pipeline cycle, "
    "an author, a reviewer, CI and a merge, and the cross-vendor review is "
    "weaker on a lone fragment than on a slice that does something. Aggregate by "
    "layer or capability, three to five slices to an epic; a plan that sliced an "
    "issue into its plan-steps has recreated the problem."
)

MIN_SLICES, MAX_SLICES = 2, 5
MIN_SENTENCES, MAX_SENTENCES = 3, 6
RULING_LINE = "Ruling: one piece"
#: The reserved model_ruling question_id (spec §2): only record_one_piece
#: writes it, and record_model_question/record_model_ruling refuse it.
SHAPE_QUESTION = "shape"
#: The labels a child inherits from its parent (spec §1): the policy labels,
#: never the runner's or the filing step's.
POLICY_LABELS = ("bircher:autonomous", "bircher:gate-plan", "bircher:grill")

_HEADING = re.compile(r"^## Slice (\d+): (.+?)\s*$")
_LABELS = ("Scope:", "Non-goals:", "Depends on:")
_RULING_LABELS = ("Reasoning:", "Cost if wrong:")


class PlanError(ValueError):
    """The bytes are not a slice plan the kernel admits; the message names why."""


@dataclass(frozen=True)
class Slice:
    number: int
    title: str
    scope: str
    non_goals: str
    depends_on: tuple[int, ...]


@dataclass(frozen=True)
class Plan:
    title: str
    preamble: str
    slices: tuple[Slice, ...]

    def by_number(self) -> dict[int, Slice]:
        return {s.number: s for s in self.slices}

    def edges(self) -> list[tuple[int, int]]:
        """`(slice, blocker)` pairs, in slice order."""
        return [(s.number, b) for s in self.slices for b in s.depends_on]


@dataclass(frozen=True)
class Ruling:
    reasoning: str
    cost_if_wrong: str


def sentences(text: str) -> int:
    """Full stops that end a sentence: a `.` followed by whitespace or the end
    of the text. `e.g.` and `3.5` do not count; a final `done.` does."""
    return len(re.findall(r"\.(?=\s|$)", text.strip()))


def parse(data: bytes) -> Plan:
    lines = data.decode("utf-8", "replace").splitlines()
    title, pre, i = "", [], 0
    while i < len(lines) and not _HEADING.match(lines[i]):
        if not title and lines[i].startswith("# "):
            title = lines[i][2:].strip()
        else:
            pre.append(lines[i])
        i += 1
    blocks: list[tuple[int, str, list[str]]] = []
    while i < len(lines):
        m = _HEADING.match(lines[i])
        if not m:
            raise PlanError(f"line {i + 1}: expected a `## Slice N: <title>` heading, got {lines[i]!r}")
        n, t = int(m.group(1)), m.group(2).strip()
        i += 1
        body: list[str] = []
        while i < len(lines) and not _HEADING.match(lines[i]):
            body.append(lines[i])
            i += 1
        blocks.append((n, t, body))
    if not blocks:
        raise PlanError("no `## Slice N: <title>` heading: not a slice plan")
    numbers = [n for n, _, _ in blocks]
    if numbers != list(range(1, len(blocks) + 1)):
        raise PlanError(f"slice numbers must be 1..N in order with no gaps, got {numbers}")
    if not MIN_SLICES <= len(blocks) <= MAX_SLICES:
        raise PlanError(f"{len(blocks)} slices: a plan has {MIN_SLICES} to {MAX_SLICES}")
    out = []
    for n, t, body in blocks:
        if not t:
            raise PlanError(f"slice {n} has no title")
        fields = _fields(n, body)
        count = sentences(fields["Scope:"])
        if not MIN_SENTENCES <= count <= MAX_SENTENCES:
            raise PlanError(f"slice {n}: Scope has {count} sentence(s); "
                            f"{MIN_SENTENCES} to {MAX_SENTENCES} by full stops")
        if not fields["Non-goals:"]:
            raise PlanError(f"slice {n}: Non-goals is empty")
        out.append(Slice(n, t, fields["Scope:"], fields["Non-goals:"], _deps(n, fields["Depends on:"])))
    plan = Plan(title, "\n".join(pre).strip(), tuple(out))
    for s in plan.slices:
        for b in s.depends_on:
            if b == s.number:
                raise PlanError(f"slice {s.number} depends on itself")
            if b not in numbers:
                raise PlanError(f"slice {s.number} depends on slice {b}, which is not in the plan")
    topo_order(plan)  # raises on a cycle
    return plan


def _fields(n: int, body: list[str]) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    current = None
    for line in body:
        hit = next((lab for lab in _LABELS if line.startswith(lab)), None)
        if hit is not None:
            if hit in found:
                raise PlanError(f"slice {n}: `{hit}` appears twice")
            found[hit] = [line[len(hit):].strip()]
            current = hit
        elif current is not None and line.strip():
            found[current].append(line.strip())
    missing = [lab for lab in _LABELS if lab not in found]
    if missing:
        raise PlanError(f"slice {n}: missing {missing}")
    return {k: " ".join(x for x in v if x) for k, v in found.items()}


def _deps(n: int, value: str) -> tuple[int, ...]:
    if value.strip().lower() == "none":
        return ()
    out = []
    for tok in value.split(","):
        tok = tok.strip()
        if not tok.isdigit():
            raise PlanError(f"slice {n}: Depends on must be `none` or slice numbers, got {value!r}")
        out.append(int(tok))
    return tuple(out)


def topo_order(plan: Plan) -> list[int]:
    """Dependency order, ties by slice number (spec §3 pass 1); PlanError on
    a cycle."""
    deps = {s.number: set(s.depends_on) for s in plan.slices}
    order: list[int] = []
    while deps:
        ready = sorted(n for n, d in deps.items() if not d - set(order))
        if not ready:
            raise PlanError(f"dependency cycle among slices {sorted(deps)}")
        order.append(ready[0])
        del deps[ready[0]]
    return order


def parse_ruling(data: bytes) -> Ruling | None:
    """`None` when the file is not a ruling (spec §3 *The ruling file's
    grammar*): the first non-blank line is exactly `Ruling: one piece`;
    `Reasoning:` and `Cost if wrong:` each open a block that runs to the next
    label or the end; both non-empty after trimming; leading whitespace is
    ignored; any other non-blank line before the first label, a missing
    label, or a repeated label makes the file not a ruling."""
    body = [ln.strip() for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not body or body[0] != RULING_LINE:
        return None
    blocks: dict[str, list[str]] = {}
    current = None
    for line in body[1:]:
        label = next((lab for lab in _RULING_LABELS if line.startswith(lab)), None)
        if label is not None:
            if label in blocks:
                return None
            current = label
            blocks[label] = [line[len(label):].strip()]
        elif current is None:
            return None
        else:
            blocks[current].append(line)
    reasoning = " ".join(x for x in blocks.get("Reasoning:", []) if x).strip()
    cost = " ".join(x for x in blocks.get("Cost if wrong:", []) if x).strip()
    if not reasoning or not cost:
        return None
    return Ruling(reasoning, cost)


def render_child(s: Slice, parent: int, hash8: str, filed: dict[int, int]) -> tuple[str, str]:
    """`(title, body)` of the child issue for *s* (spec §3 pass 1). *filed*
    maps slice number -> issue number for the siblings already filed. STRICT:
    a dependency with no filed number raises rather than rendering `none`
    (round 11, finding 3), so an out-of-order create cannot produce a body
    the binding admits."""
    missing = [b for b in s.depends_on if b not in filed]
    if missing:
        raise PlanError(f"slice {s.number}: blocker(s) {missing} have no filed issue")
    deps = ", ".join(f"#{filed[b]}" for b in s.depends_on) or "none"
    body = (f"Slice {s.number} of #{parent} (bircher shaping {hash8})\n\n"
            f"{s.scope}\n\nNon-goals: {s.non_goals}\n\nDepends on: {deps}\n")
    return s.title, body


def umbrella_body(hash8: str, plan: Plan, filed: dict[int, int]) -> str:
    lines = [f"- #{filed[s.number]} Slice {s.number}: {s.title}" for s in plan.slices]
    return f"bircher: sliced {hash8}\n\n" + "\n".join(lines) + "\n"


def completion_body(plan: Plan, filed: dict[int, int], states: dict[int, str]) -> str:
    lines = [f"- #{filed[s.number]} Slice {s.number}: {s.title} — {states[s.number]}" for s in plan.slices]
    return "bircher: sliced complete\n\n" + "\n".join(lines) + "\n"


def inherited_labels(labels) -> list[str]:
    return sorted(lab for lab in set(labels) if lab in POLICY_LABELS)
```

- [ ] **Step 5: `Store.create_run(state=)` and `run_base_repo`**

In `v2/kernel/store.py`, replace the signature and the two writes of `create_run`:

```python
    def create_run(self, *, run_id: str, base_repo: str, base_sha: str,
                   state: str = "queued") -> None:
        """Create a run, and record the fact that says so.

        The row alone is not the truth: without a RUN_STARTED fact the
        projection has nothing to build from and returns None, so state is
        rebuildable only in principle. Facts are the truth; the row is
        derived.

        *state* is where the run is born (planning ruling 2): `queued` for a
        v1 enqueue whose spec and plan already exist, `shaping` for a run the
        front half creates from an issue (shaping spec §2).
        """
        from kernel.events import EventKind

        self._conn.execute(
            "INSERT INTO runs (run_id, state, base_repo, base_sha, created_at_us)"
            " VALUES (?,?,?,?,?)",
            (run_id, state, base_repo, base_sha, self._clock.now_us()),
        )
        self.append_fact(
            run_id=run_id, kind=EventKind.RUN_STARTED, actor="kernel",
            causal_command_id=None,
            payload={"base_sha": base_sha, "base_repo": base_repo, "state": state},
        )
```

and after `run_base_sha`:

```python
    def run_base_repo(self, run_id: str) -> str:
        row = self._conn.execute(
            "SELECT base_repo FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_slices_grammar.py -q`
Expected: PASS (13 tests). Then the whole suite: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q` — expected **1452 passed, 3 skipped**.

- [ ] **Step 7: Mutation**

Change `MAX_SLICES = 5` to `6`: `test_refusals_name_why[6 slices]` goes red. Change `render_child`'s `raise` to `return` with `deps = "none"`: `test_render_child_is_exact_and_strict` goes red. Revert both.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel/events.py v2/kernel/slices.py v2/kernel/store.py v2/tests/kernel/test_slices_grammar.py
git commit -m "feat(kernel): the slice-plan grammar, the ruling grammar, the six shaping fact kinds"
```

### Task 2: Policy — `slices` is a phase, gated by default

**Files:**
- Modify: `v2/kernel/policy.py:16-28` (`PHASES`, `Policy.gates`), `:49-53` (`derive`'s `raw_gates`)
- Modify: `v2/tests/kernel/test_policy.py:18-33`
- Test: `v2/tests/kernel/test_policy.py` (extend)

**Interfaces:**
- Produces: `policy.PHASES == ("slices", "spec", "plan")`; `Policy().gates == frozenset({"slices", "spec"})`; `derive(labels, cfg)` admits `"slices"` in `gates`; `bircher:autonomous` clears all three; `bircher:gate-plan` adds `plan`.

- [ ] **Step 1: Write the failing tests**

Replace `test_defaults_are_the_specs` and `test_labels_override_project_config` in `v2/tests/kernel/test_policy.py` with:

```python
def test_defaults_are_the_specs():
    p = policy.derive([], {})
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("model", frozenset({"slices", "spec"}), 3, 16)
    assert policy.PHASES == ("slices", "spec", "plan")


def test_labels_override_project_config():
    assert policy.derive(["bircher:grill"], {}).grill == "human"
    assert policy.derive(["bircher:autonomous"], {}).gates == frozenset()
    assert policy.derive(["bircher:gate-plan"], {}).gates == frozenset({"slices", "spec", "plan"})
    # Ruling 5 (front half): cleared last, so autonomous wins over gate-plan.
    assert policy.derive(["bircher:gate-plan", "bircher:autonomous"], {}).gates == frozenset()
    cfg = {"grill": "human", "gates": ["spec", "plan"], "max_rounds": 5, "max_seats": 40}
    p = policy.derive([], cfg)
    assert (p.grill, p.gates, p.max_rounds, p.max_seats) == ("human", frozenset({"spec", "plan"}), 5, 40)
    assert policy.derive(["bircher:autonomous"], cfg).gates == frozenset()
    # A project may gate only the slices (shaping spec §1).
    assert policy.derive([], {"gates": ["slices"]}).gates == frozenset({"slices"})
    # A slice label on the issue changes no policy: depth is the kernel's
    # refusal on policy_frozen's labels (spec §2), not a gate.
    assert policy.derive(["bircher:slice"], {}).gates == frozenset({"slices", "spec"})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_policy.py -q`
Expected: FAIL on `PHASES` and the default gates.

- [ ] **Step 3: Implement**

In `v2/kernel/policy.py`:

```python
PHASES = ("slices", "spec", "plan")
```

```python
@dataclass(frozen=True)
class Policy:
    grill: str = "model"
    gates: frozenset[str] = frozenset({"slices", "spec"})
    max_rounds: int = 3
    max_seats: int = 16
```

and in `derive`:

```python
    raw_gates = cfg.get("gates", ["slices", "spec"])
```

- [ ] **Step 4: Run the suite**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`
Expected: PASS except tests that pin the default gates elsewhere. Fix each by the same rule (`{"spec"}` → `{"slices", "spec"}` where the test names the default). Expected files: `v2/tests/kernel/test_policy.py` (`test_payload_round_trips`, `test_a_run_without_policy_frozen_gets_defaults`), `v2/tests/kernel/test_brief.py` (a rendered `gates:` line), `v2/tests/coordinator/test_author_round.py` (the policy JSON in a brief). Every other test builds its run with `bircher:autonomous` or an explicit config and does not move. End: **1452 passed, 3 skipped**.

- [ ] **Step 5: Commit**

```bash
git add v2/kernel/policy.py v2/tests
git commit -m "feat(kernel): slices is a phase, gated by default"
```

### Task 3: The cutover — the four states, `record_one_piece`, `submit_slices`, `advance_ungated`, the membership table, the driver, the migration

Every run is born in `shaping` from here on, so this task is the atomic change: the states, the three commands that move between them, every existing command's extended from-set, both `FRONT_PHASES` lists, the brief's `slices` phase, the driver that shapes by default, and the migration of the tests that the birth state moves.

**Files:**
- Modify: `v2/kernel/authz.py:31-40` (`FRONT_HALF_STATES`, `_PHASE_OF_STATE`), `:66-78` (`OUTPUT_COMMANDS`, `_ALL_ACTIVE`), `:84-155` (`_TRANSITIONS`), `:169-216` (`_review_destination`), `:542-598` (`_check_submit` split), `:702-1023` (`authorize`: the reserved id, three new branches, `approve_artifact`, the reviewer-binding check)
- Modify: `v2/kernel/commands.py:50-102` (`COMMAND_NAMES`), `:151-308` (`_side_fact`)
- Modify: `v2/kernel/front.py:56` (`FRONT_PHASES`), append `frozen_labels`, `shape_ruling`
- Modify: `v2/kernel/projection.py:22` (`FRONT_PHASES`)
- Modify: `v2/kernel/enqueue.py:151-167` (`create_run` → `shaping`)
- Modify: `v2/kernel/brief.py:24-116` (template 2 gains `{slices_section}`), `:118-186` (`_SLICES_SECTION`, `render`)
- Modify: `v2/tests/kernel/front.py` (the driver)
- Test: `v2/tests/kernel/test_shaping_states.py`
- Migrate: the files the rules in Step 8 name

**Interfaces:**
- Consumes: `slices.parse`, `slices.PlanError`, `slices.SHAPE_QUESTION`, `slices.COARSE` (Task 1); `policy.PHASES` (Task 2).
- Produces:
  - `authz.SHAPING_STATES = frozenset({"shaping", "slices_submitted", "slices_accepted"})`; `phase_of` maps the three and `sliced` to `"slices"`.
  - Commands `record_one_piece {reasoning, cost_if_wrong}` (`shaping` → `queued`, author role, the turn observed ended, the direction guard, one per epoch, both strings non-empty), `submit_slices {artifact_hash}` (`shaping` → `slices_submitted`, every `submit_spec` refusal plus the grammar and the depth refusal), `advance_ungated {}` (`slices_accepted` → `sliced`, operator role, refused when `slices ∈ gates`).
  - Facts: `model_ruling {question_id: "shape", ruling: "one piece", reasoning, cost_if_wrong, epoch}`; `artifact_submitted {phase: "slices", …}`; `artifact_advanced {phase: "slices", epoch, hash}`.
  - `front.FRONT_PHASES == projection.FRONT_PHASES == ("slices", "spec", "plan")`; `front.frozen_labels(store, run_id) -> list[str]`; `front.shape_ruling(store, run_id, epoch_n) -> Fact | None`.
  - `brief.render(phase="slices", spec=None, …)` renders template 2 with the coarse paragraph; spec and plan renders are byte-identical to today.
  - Driver: `Front(..., shape=True)`; `Front.shape_round(plan=None, *, ended="file", reasoning=…, cost=…) -> str | None`; `Front.advance()`; `Front.to_sliced(plan=SLICES_BYTES)`; `Front.revise(issue, *, reshape=True)`; `tests.kernel.front.SLICES_BYTES`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_shaping_states.py
"""Task 3: the shaping states and the three commands that move between them
(shaping spec §2 *States*, *Existing commands whose from-sets gain states*,
*Which state set each site reads*, `record_one_piece`, `advance_ungated`)."""
import pytest

from kernel import authz, front, projection
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized, phase_of
from kernel.brief import render
from kernel.dispatch import Role
from kernel.enqueue import create_run
from kernel.events import EventKind
from kernel.policy import Policy
from kernel.slices import COARSE
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


# -- the sets ------------------------------------------------------------------

def test_the_sets_and_the_phase_map():
    assert authz.SHAPING_STATES == frozenset({"shaping", "slices_submitted", "slices_accepted"})
    assert not (authz.SHAPING_STATES & authz.FRONT_HALF_STATES)
    for st in ("shaping", "slices_submitted", "slices_accepted", "sliced"):
        assert phase_of(st) == "slices"
        assert st in authz._ALL_ACTIVE
    assert front.FRONT_PHASES == ("slices", "spec", "plan") == projection.FRONT_PHASES
    assert projection.is_front_verdict({"phase": "slices"})


def test_membership_per_command():
    both = authz.FRONT_HALF_STATES | authz.SHAPING_STATES
    legal = authz.legal_states_for
    for name in ("park", "record_human_answer", "record_prompt_item", "dismiss_human_item"):
        assert legal(name) == both, name
    assert legal("record_human_direction") == frozenset({"queued", "specified", "shaping"})
    assert legal("record_model_question") == authz.FRONT_HALF_STATES
    assert legal("record_model_ruling") == authz.FRONT_HALF_STATES
    assert legal("revise_bundle") == both and authz.next_state_for("revise_bundle") == "shaping"
    assert legal("record_turn_ended") >= {"shaping", "slices_submitted"}
    assert legal("record_author_empty") >= {"shaping"}
    assert legal("grant_round") >= {"shaping", "slices_submitted"}
    assert legal("issue_review_brief") >= {"slices_submitted"}
    assert legal("record_review") >= {"slices_submitted", "slices_accepted"}
    assert legal("approve_artifact") >= {"slices_accepted"}
    assert legal("record_one_piece") == frozenset({"shaping"})
    assert legal("submit_slices") == frozenset({"shaping"})
    assert legal("advance_ungated") == frozenset({"slices_accepted"})
    assert "sliced" not in legal("revise_bundle")
    assert "sliced" in legal("cancel_run") and "sliced" in legal("record_run_outcome")


def test_create_run_is_born_in_shaping(tmp_path):
    s = _store(tmp_path)
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40, issue=ISSUE, project_config={})
    assert s.run_state("r-1") == "shaping"
    assert projection.project(s.facts_for("r-1")).state == "shaping"


# -- record_one_piece ----------------------------------------------------------

def test_one_piece_moves_to_queued_and_records_the_shape_ruling(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")                      # the driver shapes by default (planning ruling 3)
    assert s.run_state("r-1") == "queued"
    r = front.shape_ruling(s, "r-1", 0)
    assert r is not None and r.payload["ruling"] == "one piece" and r.payload["question_id"] == "shape"
    assert r.payload["reasoning"] and r.payload["cost_if_wrong"]
    f.author_round(SPEC_BYTES)               # the run proceeds as today
    assert s.run_state("r-1") == "spec_submitted"


def test_one_piece_refusals(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    with pytest.raises(NotAuthorized, match="turn_ended"):          # the turn's end is a fact
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})
    f._end_turn(g, sid)
    for bad in ({"reasoning": "", "cost_if_wrong": "c"}, {"reasoning": "r", "cost_if_wrong": "  "}, {"reasoning": "r"}):
        with pytest.raises(NotAuthorized, match="non-empty"):
            f._cmd(g, "record_one_piece", bad)
    # A direction newer than the dispatch: the interrupted turn's ruling is not recorded.
    f.direct("slice it along the store/API line")
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})
    assert s.run_state("r-1") == "shaping"


def test_one_piece_is_refused_by_a_reviewer_and_by_the_wrong_vendor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause, actor="codex")            # the seat's bundle names codex
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="vendor"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})


def test_second_ruling_in_an_epoch_is_refused_and_a_new_epoch_admits_one(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    # Back to shaping only through a revision, which opens a new epoch.
    f.revise(dict(ISSUE, body="changed", labels=["bircher:autonomous"]), reshape=False)
    assert s.run_state("r-1") == "shaping" and f.epoch() == 1
    f.shape_round()
    assert s.run_state("r-1") == "queued"
    assert front.shape_ruling(s, "r-1", 1) is not None


def test_a_rejected_slice_plan_then_a_one_piece_ruling_is_legal(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES)
    assert s.run_state("r-1") == "slices_submitted"
    f.review_round("request_revision", findings=b"one piece really")
    assert s.run_state("r-1") == "shaping"
    f.shape_round()                                     # legal: the epoch's newer decision
    assert s.run_state("r-1") == "queued"
    sub = front.newest_submission(s, "r-1", "slices", 0)
    assert front.shape_ruling(s, "r-1", 0).seq > sub.seq


def test_the_shape_id_is_reserved(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="reserved"):
        f._cmd(g, "record_model_question", {"question_id": "shape", "question": "?"})
    with pytest.raises(NotAuthorized, match="reserved"):
        f._cmd(g, "record_model_ruling", {"question_id": "shape", "ruling": "one piece",
                                          "reasoning": "r", "cost_if_wrong": "c"})


def test_model_question_and_ruling_are_refused_from_the_shaping_states(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="not legal from state 'shaping'"):
        f._cmd(g, "record_model_question", {"question_id": "Q1", "question": "?"})


# -- submit_slices -------------------------------------------------------------

def test_submit_slices_lands_in_slices_submitted_and_holds_the_artefact(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    h = f.shape_round(SLICES_BYTES)
    assert s.run_state("r-1") == "slices_submitted"
    assert s.phase_artifact("r-1", "slices") == h
    sub = front.newest_submission(s, "r-1", "slices", 0)
    assert sub.payload["phase"] == "slices" and sub.payload["round"] == 1


@pytest.mark.parametrize("bad, why", [
    (SLICES_BYTES.replace(b"Depends on: 1", b"Depends on: 9"), "not in the plan"),
    (SLICES_BYTES.replace(b"Add the table. Add the migration. Add the reads.", b"Add it. Done."), "sentence"),
    (b"# T\n\n## Slice 1: only\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n", "1 slices"),
])
def test_submit_slices_grammar_refusals_name_why(tmp_path, bad, why):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    with pytest.raises(NotAuthorized, match=why):
        f.shape_round(bad)
    assert s.run_state("r-1") == "shaping"


def test_a_slice_is_not_sliced_and_the_observable_is_policy_frozen(tmp_path):
    s = _store(tmp_path)
    child = dict(ISSUE, number=40, labels=["bircher:autonomous", "bircher:slice", "bircher:queued"])
    f = Front(s, "r-1", shape=False, issue=child)
    # The canon strips every bircher: label from the bundle (bundle.py), so
    # the bundle is NOT where the refusal reads.
    import json
    assert "bircher:slice" not in json.loads(s.read_blob(front.bundle_hash(s, "r-1")))["labels"]
    assert "bircher:slice" in front.frozen_labels(s, "r-1")
    with pytest.raises(NotAuthorized, match="a slice is not sliced"):
        f.shape_round(SLICES_BYTES)
    f.shape_round()                                      # it proceeds as one piece
    assert s.run_state("r-1") == "queued"


def test_identical_resubmission_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"again")
    with pytest.raises(NotAuthorized, match="identical"):
        f.shape_round(SLICES_BYTES)


# -- the review, the gate, the advance -------------------------------------------

def test_reviewer_accept_lands_in_slices_accepted_whatever_the_gates(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)                     # autonomous: no gates
    f.shape_round(SLICES_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "slices_accepted"       # not planned, not sliced


def test_advance_ungated_moves_on_and_is_refused_under_a_gate(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(g, "advance_ungated", {})
    f.advance()
    assert s.run_state("r-1") == "sliced"
    adv = s.newest_fact("r-1", EventKind.ARTIFACT_ADVANCED)
    assert adv.payload == {"phase": "slices", "epoch": 0, "hash": s.phase_artifact("r-1", "slices")}
    s2 = Store.open(tmp_path / "gated.db")
    f2 = Front(s2, "r-1", shape=False, labels=())        # default policy: slices gated
    f2.shape_round(SLICES_BYTES); f2.review_round("accept")
    with pytest.raises(NotAuthorized, match="gates slices"):
        f2.advance()
    f2.approve()
    assert s2.run_state("r-1") == "sliced"


def test_human_correction_at_the_slice_gate_lands_in_shaping(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False, labels=())
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "slices_accepted"
    f.human("record_review", {"phase": "slices", "artifact_hash": s.phase_artifact("r-1", "slices"),
                              "verdict": "request_revision", "findings": "merge slices 1 and 2"})
    assert s.run_state("r-1") == "shaping"


def test_one_piece_is_refused_once_a_plan_is_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="not legal from state 'slices_accepted'"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})


def test_revise_bundle_lands_in_shaping_from_a_spec_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    assert s.run_state("r-1") == "spec_submitted"
    f.revise(dict(ISSUE, body="changed"), reshape=False)
    assert s.run_state("r-1") == "shaping" and f.epoch() == 1


def test_direction_at_shaping_is_legal_and_at_slices_submitted_is_not(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.direct("one piece, do not slice it")
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["phase"] == "slices"
    f.shape_round(SLICES_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'slices_submitted'"):
        f.direct("x")


def test_grant_round_is_legal_at_slices_submitted_with_a_park(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False, project_config={"max_rounds": 1})
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"no")
    f.shape_round(SLICES_BYTES.replace(b"the API.", b"the API, the client."))
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.grant()
    assert front.round_bound(s, "r-1", "slices", 0) == 2


def test_the_slices_brief_carries_the_coarse_paragraph_and_spec_briefs_are_unchanged(tmp_path):
    bundle = b'{"number": 1}'
    b = render(phase="slices", artefact=SLICES_BYTES, bundle=bundle, spec=None,
               policy=Policy(), base_sha="0" * 40, template=2)
    assert COARSE.encode() in b and b"then the slice plan" in b and b"## What you are grading" in b
    for phase, spec in (("spec", None), ("plan", SPEC_BYTES)):
        art = SPEC_BYTES if phase == "spec" else PLAN_BYTES
        out = render(phase=phase, artefact=art, bundle=bundle, spec=spec, policy=Policy(), base_sha="0" * 40, template=2)
        assert b"## What you are grading" not in out and COARSE.encode() not in out
    with pytest.raises(ValueError, match="carries no spec"):
        render(phase="slices", artefact=SLICES_BYTES, bundle=bundle, spec=SPEC_BYTES,
               policy=Policy(), base_sha="0" * 40, template=2)


def test_to_sliced_drives_the_whole_phase(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1", shape=False).to_sliced()
    assert s.run_state("r-1") == "sliced"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_shaping_states.py -q`
Expected: FAIL — `ImportError: cannot import name 'SLICES_BYTES'`, then `AttributeError: SHAPING_STATES`.

- [ ] **Step 3: The state sets and the table in `authz.py`**

After `FRONT_HALF_STATES` (line 34):

```python
#: The shaping states (shaping spec §2 *Which state set each site reads*).
#: DELIBERATELY NOT part of FRONT_HALF_STATES: that set is the from-set of
#: record_model_question and record_model_ruling, and joining it would
#: legalise both from the shaping states -- the second of them writing the
#: very model_ruling shape record_one_piece records, with none of its
#: guards. Membership is stated per site below, never inherited (ruling 13).
SHAPING_STATES = frozenset({"shaping", "slices_submitted", "slices_accepted"})
```

`_PHASE_OF_STATE` gains four entries:

```python
_PHASE_OF_STATE = {
    "shaping": "slices", "slices_submitted": "slices", "slices_accepted": "slices", "sliced": "slices",
    "queued": "spec", "spec_submitted": "spec", "spec_accepted": "spec",
    "specified": "plan", "plan_submitted": "plan", "plan_accepted": "plan",
    "implementing": "implementation", "reviewing": "implementation",
}
```

`OUTPUT_COMMANDS` gains the two shaping outputs:

```python
OUTPUT_COMMANDS = frozenset({
    "submit_spec", "submit_plan", "submit_slices", "record_one_piece",
    "record_author_empty", "record_model_question", "record_model_ruling",
})
```

`_ALL_ACTIVE` gains the four states:

```python
_ALL_ACTIVE = frozenset({
    "shaping", "slices_submitted", "slices_accepted", "sliced",
    "queued", "spec_submitted", "spec_accepted", "specified",
    "plan_submitted", "plan_accepted", "planned", "implementing", "reviewing",
    "merge_requested",
})
```

In `_TRANSITIONS`, change these rows and add three:

```python
    "submit_spec": (frozenset({"queued"}), "spec_submitted"),
    "submit_plan": (frozenset({"specified"}), "plan_submitted"),
    # The shaping phase (shaping spec §2 *States*): the ruling and the plan
    # leave `shaping` by different doors; the ungated advance is the
    # coordinator's own fact, separate from the reviewer's accept, because
    # the transition that authorises filing must be its own fact.
    "record_one_piece": (frozenset({"shaping"}), "queued"),
    "submit_slices": (frozenset({"shaping"}), "slices_submitted"),
    "advance_ungated": (frozenset({"slices_accepted"}), "sliced"),
    "start_implementation": (frozenset({"planned"}), "implementing"),
    "record_review": (
        frozenset({"slices_submitted", "slices_accepted", "spec_submitted", "spec_accepted",
                   "plan_submitted", "plan_accepted", "implementing", "reviewing"}),
        None,
    ),
    "record_turn_ended": (
        frozenset({"shaping", "slices_submitted", "queued", "specified", "spec_submitted", "plan_submitted"}), None,
    ),
    # The membership table (shaping spec §2): the carrier session's items and
    # the gate park are legal in the shaping states too; the model's two
    # channels are NOT.
    "park": (FRONT_HALF_STATES | SHAPING_STATES, None),
    "record_human_answer": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # The three author-round states.
    "record_human_direction": (frozenset({"queued", "specified", "shaping"}), None),
    "approve_artifact": (frozenset({"slices_accepted", "spec_accepted", "plan_accepted"}), None),
    "grant_round": (frozenset({"shaping", "slices_submitted", "queued", "specified",
                               "spec_submitted", "plan_submitted"}), None),
    "record_author_empty": (frozenset({"shaping", "queued", "specified"}), None),
    "dismiss_human_item": (FRONT_HALF_STATES | SHAPING_STATES, None),
    "record_prompt_item": (FRONT_HALF_STATES | SHAPING_STATES, None),
    # Every revision lands in `shaping`, the epoch's first state (ruling 11):
    # a materially changed issue may have changed size. Refused from `sliced`
    # by this very from-set: its children are the work now (ruling 7).
    "revise_bundle": (FRONT_HALF_STATES | SHAPING_STATES, "shaping"),
    "issue_review_brief": (frozenset({"slices_submitted", "spec_submitted", "plan_submitted"}), None),
```

(`record_model_question`, `record_model_ruling`, `record_run_outcome`, `cancel_run` keep their rows; `_ALL_ACTIVE` carries the new states into the last two.)

- [ ] **Step 4: `_review_destination`, `approve_artifact`, the reviewer-binding check**

Replace the body of `_review_destination` from `if ruling == "human_ruling":` to the end:

```python
    if ruling == "human_ruling":
        if state not in FRONT_HALF_STATES | SHAPING_STATES:
            raise NotAuthorized("a human record_review is front-half only (spec §2 Commands)")
        if verdict != "request_revision":
            raise NotAuthorized(
                "the human's record_review is request_revision; approval is "
                "approve_artifact, after the reviewer"
            )
        return _REVISION_DESTINATION[phase]
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
        from kernel import front
        n = front.epoch(store, run_id)
        used, bound = front.rounds_used(store, run_id, phase, n), front.round_bound(store, run_id, phase, n)
        if used >= bound:
            raise NotAuthorized(
                f"max_rounds: {used} request_revision rulings already in {phase}/epoch {n} "
                f"against a bound of {bound}; the loop parks bound_exhausted and a human retry grants one"
            )
        return _REVISION_DESTINATION[phase]
    gates = policy_of(store, run_id).gates
    if phase == "slices":
        # WHATEVER the gates (shaping spec §2): the ungated path leaves
        # through advance_ungated, its own fact, not through the accept.
        return "slices_accepted"
    if phase == "spec":
        return "spec_accepted" if "spec" in gates else "specified"
    return "plan_accepted" if "plan" in gates else "planned"
```

and above `_review_destination`:

```python
#: Where a request_revision -- the reviewer's or the human's -- returns the
#: run: each phase's one revision destination (shaping spec §2 *States*).
_REVISION_DESTINATION = {"slices": "shaping", "spec": "queued", "plan": "specified"}
#: Where the human's approval lands each phase (shaping spec §2).
_APPROVAL_DESTINATION = {"slices": "sliced", "spec": "specified", "plan": "planned"}
```

In `authorize`, the `approve_artifact` branch's last line becomes `return _APPROVAL_DESTINATION[phase]`, and the reviewer-binding check becomes:

```python
    if cmd.name == "record_review" and ruling == "review_ruling" and current in FRONT_HALF_STATES | SHAPING_STATES:
        _require_turn_recorded(store, cmd, Role.REVIEWER)
```

- [ ] **Step 5: The reserved id, the split of `_check_submit`, and the three new branches**

In the `record_model_question`/`record_model_ruling` block, after the `qid` shape check:

```python
        from kernel.slices import SHAPE_QUESTION
        if qid == SHAPE_QUESTION:
            raise NotAuthorized(
                f"question_id {SHAPE_QUESTION!r} is reserved to record_one_piece (shaping spec §2): "
                "no path writes a shape ruling without that command's guards"
            )
```

Split `_check_submit` into three functions. The new `_check_author_seat` holds the role and vendor guard (lines 551-563 today), `_check_no_newer_direction` holds the direction guard (lines 582-598 today), and `_check_submit` calls both:

```python
def _check_author_seat(store, cmd, state: str) -> None:
    """The author role and the vendor guard (ruling 14): the artefact was
    read from the round's session, and its author is the vendor that ran it.
    Shared by the submits and by record_one_piece."""
    from kernel import front
    if role_for(store, cmd.run_id, cmd.generation) != Role.AUTHOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the author role")
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
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


def _check_no_newer_direction(store, cmd) -> None:
    """spec §2 Refusals: a human_direction newer than this generation's own
    dispatch means a human interrupted the turn this output is the product
    of. The output of the turn it interrupted is not this run's to record;
    the direction starts a fresh round instead. Shared by the submits and by
    record_one_piece (shaping spec §2, round 2 finding 5)."""
    from kernel import front
    dispatched_seq = front.dispatch_seq(store, cmd.run_id, cmd.generation)
    if dispatched_seq is None:
        raise NotAuthorized(
            f"generation {cmd.generation} has no attempt_dispatched fact: nothing to "
            "order a human_direction against"
        )
    newer = front.direction_after(store, cmd.run_id, dispatched_seq)
    if newer is not None:
        raise NotAuthorized(
            f"a human_direction (seq {newer.seq}) is newer than generation "
            f"{cmd.generation}'s dispatch: the output of the turn it interrupted is not "
            "recorded; the direction starts a fresh round"
        )


def _check_submit(store, cmd, state: str) -> None:
    """What a submission has to be, before it becomes the phase's artefact.

    The author role, an artefact the kernel holds, and a change: an identical
    resubmission in the same phase and epoch is the loop spinning, not a
    revision; the plan additionally has to be a different object from the
    spec and to have tasks in it; the slice plan has to parse (shaping spec
    §2 *The artefact*) and the issue must not itself be a slice.
    """
    from kernel import front
    _check_author_seat(store, cmd, state)
    phase, epoch_n = phase_of(state), front.epoch(store, cmd.run_id)
    if cmd.name == "submit_spec" and front.grill_open(store, cmd.run_id):
        raise NotAuthorized(
            "the grill is open: under grill=human the spec waits for a human_answer "
            "in this epoch newer than the newest model_question"
        )
    h = cmd.payload.get("artifact_hash")
    if not isinstance(h, str) or not store.has_artifact(h):
        raise NotAuthorized(f"{cmd.name} names an artefact the kernel does not hold: {h!r}")
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
    if cmd.name == "submit_slices":
        from kernel import slices as _slices
        # The grammar (shaping spec §2 *The artefact*): a shape check from the
        # bytes, the one judgement-shaped rule being the sentence bound.
        try:
            _slices.parse(store.read_blob(h))
        except _slices.PlanError as exc:
            raise NotAuthorized(f"submit_slices: {exc}") from exc
        # A slice is not sliced. The frozen BUNDLE cannot carry the label --
        # the canon strips every bircher: label as state, not input -- but
        # policy_frozen records the issue's labels unfiltered at creation,
        # and that is the observable (round 1, finding 1).
        if "bircher:slice" in front.frozen_labels(store, cmd.run_id):
            raise NotAuthorized(
                "a slice is not sliced: this issue carries bircher:slice in its policy_frozen "
                "labels; depth is one by construction (shaping spec §2)"
            )
    _check_no_newer_direction(store, cmd)
```

In `authorize`, replace the `submit_spec`/`submit_plan` branch and add the two commands beside it:

```python
    if cmd.name in ("submit_spec", "submit_plan", "submit_slices"):
        _check_submit(store, cmd, current)
        return next_state

    if cmd.name == "record_one_piece":
        from kernel import front
        # Literal reads (the provenance extractor): the two strings a ruling
        # carries, already declared residuals for record_model_ruling.
        for name, value in (("reasoning", cmd.payload.get("reasoning")),
                            ("cost_if_wrong", cmd.payload.get("cost_if_wrong"))):
            if not _non_empty_str(value):
                raise NotAuthorized(f"record_one_piece carries a non-empty {name}")
        _check_author_seat(store, cmd, current)
        _check_no_newer_direction(store, cmd)
        n = front.epoch(store, cmd.run_id)
        if front.shape_ruling(store, cmd.run_id, n) is not None:
            raise NotAuthorized(f"a shape ruling already exists in epoch {n}: one decision per epoch (shaping spec §2)")
        return next_state

    if cmd.name == "advance_ungated":
        from kernel.policy import policy_of
        if role_for(store, cmd.run_id, cmd.generation) != Role.OPERATOR:
            raise NotAuthorized("advance_ungated must come from an attempt dispatched in the operator role")
        if "slices" in policy_of(store, cmd.run_id).gates:
            raise NotAuthorized(
                "advance_ungated: this run's policy gates slices; only approve_artifact "
                "moves on from slices_accepted"
            )
        return next_state
```

- [ ] **Step 6: `commands.py`, `front.py`, `projection.py`, `enqueue.py`, `brief.py`**

`COMMAND_NAMES` gains, after `"issue_review_brief",`:

```python
    # The shaping phase (shaping spec §2 *States*): the ruling, the slice
    # plan, and the coordinator's own ungated advance.
    "record_one_piece", "submit_slices", "advance_ungated",
```

`_side_fact`: the first branch becomes `if cmd.name in ("submit_spec", "submit_plan", "submit_slices"):` (unchanged body), and two branches are added before `elif cmd.name == "record_turn_ended":`:

```python
    elif cmd.name == "record_one_piece":
        from kernel.slices import SHAPE_QUESTION
        # The existing fact and payload shape (record_model_ruling's), with
        # the reserved question id: the proof's fourth assertion reads it.
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.MODEL_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "question_id": SHAPE_QUESTION, "ruling": "one piece",
                     "reasoning": cmd.payload["reasoning"], "cost_if_wrong": cmd.payload["cost_if_wrong"]},
        )
    elif cmd.name == "advance_ungated":
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.ARTIFACT_ADVANCED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"phase": phase, "epoch": epoch_n, "hash": store.phase_artifact(cmd.run_id, phase)},
        )
```

`front.py`: `FRONT_PHASES = ("slices", "spec", "plan")`, and append:

```python
def frozen_labels(store, run_id: str) -> list[str]:
    """The issue's labels as `policy_frozen` recorded them, UNFILTERED
    (policy.py `freeze`): the observable the depth refusal reads, since the
    bundle canon strips every bircher: label."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return [] if fact is None else list(fact.payload.get("labels") or [])


def shape_ruling(store, run_id: str, epoch_n: int):
    """The epoch's `model_ruling {question_id: "shape"}`, or None."""
    from kernel.slices import SHAPE_QUESTION
    for f in epoch_facts(store, run_id, EventKind.MODEL_RULING, epoch_n):
        if f.payload.get("question_id") == SHAPE_QUESTION:
            return f
    return None
```

`projection.py`: `FRONT_PHASES = ("slices", "spec", "plan")` with the comment gaining one sentence: "A `slices` verdict is the front half's too: a one-piece run that needed two slice-plan rounds must not arrive in its back half with its repair allowance spent (shaping spec §2)."

`enqueue.py`, in `create_run`'s transaction: `store.create_run(run_id=run_id, base_repo=base_repo, base_sha=base_sha, state="shaping")`, and the docstring's "the run row (`queued`)" becomes "the run row (`shaping`, shaping spec §2)".

`brief.py`: in template 2, after the verdict-rule paragraph's blank line and before `{prior_section}`, insert `{slices_section}` on its own line; add the section and widen `render`:

```python
_SLICES_SECTION = """
## What you are grading

The artefact is a slice plan: it divides the issue into two to five slices,
each to be worked as its own run through the whole pipeline. Grade it
against this definition of a slice, and nothing else:

> {coarse}

Ask of every slice whether it is a capability a reviewer could see working
end to end, or a bare step of another slice; whether its dependencies are
real; and whether the union of the slices covers the issue with nothing
invented. Do not review the design of any slice: that is its own spec
phase's work.
"""
```

```python
def render(*, phase: str, artefact: bytes, bundle: bytes, spec: bytes | None,
           policy: Policy, base_sha: str, template: int,
           prior_findings: bytes | None = None) -> bytes:
    from kernel.slices import COARSE
    if template not in _TEMPLATES:
        raise ValueError(f"unknown brief template version {template}")
    if phase not in ("slices", "spec", "plan"):
        raise ValueError(f"brief phase must be slices, spec or plan, got {phase!r}")
    if phase == "plan" and spec is None:
        raise ValueError("a plan brief carries the run's current spec")
    if phase != "plan" and spec is not None:
        raise ValueError(f"a {phase} brief carries no spec")
    if template == 1 and prior_findings is not None:
        raise ValueError("template 1 carries no prior findings")
    if template == 1 and phase == "slices":
        raise ValueError("template 1 predates the shaping phase")
    p = to_payload(policy)
    artifact_hash = content_hash(artefact)
    spec_section, slices_section = "", ""
    what = {"spec": "then the spec", "slices": "then the slice plan"}.get(phase, "")
    if phase == "plan":
        spec_section = _SPEC_SECTION.format(spec_hash=content_hash(spec),
                                            spec=spec.decode("utf-8", "replace"))
        what = "then the accepted spec, then the plan"
    if phase == "slices":
        slices_section = _SLICES_SECTION.format(coarse=COARSE)
    prior_section = ""
    if prior_findings is not None:
        prior_section = _PRIOR_SECTION.format(prior_hash=content_hash(prior_findings),
                                              prior=prior_findings.decode("utf-8", "replace"))
    text = _TEMPLATES[template].format(
        template=template, phase=phase, review_out=REVIEW_OUT, what_to_read=what,
        prior_section=prior_section, slices_section=slices_section,
        hash8=hash8(artifact_hash), grill=p["grill"], gates=", ".join(p["gates"]) or "(none)",
        max_rounds=p["max_rounds"], max_seats=p["max_seats"], base_sha=base_sha,
        bundle_hash=content_hash(bundle), bundle=bundle.decode("utf-8", "replace"),
        spec_section=spec_section, artifact_hash=artifact_hash,
        artefact=artefact.decode("utf-8", "replace"),
    )
    return text.encode("utf-8")
```

(`str.format` ignores `slices_section` for template 1, which has no placeholder; the spec/plan renders under template 2 substitute the empty string and stay byte-identical, which `test_the_slices_brief_carries_the_coarse_paragraph_and_spec_briefs_are_unchanged` and the existing `test_brief.py` pins hold.)

- [ ] **Step 7: The driver**

In `v2/tests/kernel/front.py`, add after `PLAN_BYTES`:

```python
SLICES_BYTES = b"""# Slicing T

Why: two pieces.

## Slice 1: The store
Scope: Add the table. Add the migration. Add the reads.
Non-goals: the API.
Depends on: none

## Slice 2: The API
Scope: Add the endpoint. Wire it to the store. Cover it with a test.
Non-goals: the client.
Depends on: 1
"""
```

Change `__init__`'s signature to add `shape: bool = True` after `existing: bool = False`, and its last statement to:

```python
        create_run(store, run_id=run_id, base_repo=base_repo, base_sha=base_sha,
                   issue=issue, project_config=project_config or {})
        # Every run is born in `shaping` (shaping spec §2). The driver rules
        # it one piece by default, so a test of the spec or plan phase gets
        # its run at `queued` by the only path the kernel admits (planning
        # ruling 3); a test of the shaping phase passes shape=False.
        if shape:
            self.shape_round()
```

Add to the `# -- rounds` section:

```python
    def shape_round(self, plan: bytes | None = None, *, ended: str = "file",
                    reasoning: str = "one coherent change", cost: str = "a spec round") -> str | None:
        """One shaping turn (shaping spec §3): a one-piece ruling, or -- with
        *plan* -- a slice plan submitted. Returns the plan's hash, or None."""
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        sid = self._session(g, cause)
        self._end_turn(g, sid, ended)
        if plan is None:
            self._cmd(g, "record_one_piece", {"reasoning": reasoning, "cost_if_wrong": cost})
            return None
        h = put_artifact(self.store, plan)
        self._cmd(g, "submit_slices", {"artifact_hash": h})
        return h

    def advance(self) -> None:
        """The coordinator's `advance_ungated` at slices_accepted."""
        g = self._dispatch(Role.OPERATOR, "coordinator")
        self._cmd(g, "advance_ungated", {})

    def to_sliced(self, plan: bytes = SLICES_BYTES) -> "Front":
        from kernel.policy import policy_of
        assert self.state() == "shaping", self.state()
        self.shape_round(plan)
        self.review_round("accept")
        if "slices" in policy_of(self.store, self.run_id).gates:
            self.approve()
        else:
            self.advance()
        assert self.state() == "sliced", self.state()
        return self
```

and change `revise`:

```python
    def revise(self, issue: dict, *, reshape: bool = True) -> None:
        """A relevant issue change; lands in `shaping` (ruling 11). By default
        the driver rules the new epoch one piece so the run is back at
        `queued`; a test that asserts the landing state passes reshape=False."""
        g = self._dispatch(Role.OPERATOR, "runner")
        self._cmd(g, "revise_bundle", {"issue": issue})
        if reshape:
            self.shape_round()
```

- [ ] **Step 8: Run the suite and migrate what the birth state moves**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest -q`
Expected: `test_shaping_states.py` PASS (25 tests); a set of existing tests red for exactly these reasons, each fixed by its rule and citing the rule in a trailing comment (`# Task 3 rule (a)`):

- **(a) The birth state.** A test that asserts `queued` right after `enqueue.create_run` (not the driver) asserts `shaping`: expected in `test_policy.py::test_create_run_freezes_policy_and_puts_the_snapshot`, `test_enqueue.py`, `test_projection.py`, `test_store_front.py`, `test_front_table.py`, `test_authorization.py`, `test_kernel_client.py`, `test_lifecycle_functions.py`, `test_front_half_seam.py`. A test that asserts `queued` after `Front(...)` still holds.
- **(b) Seats.** A driver-built run has spent one author seat and one generation before the test begins. A test that counts `seats_used`, dispatches, or drives to `SeatsExhausted` on a driver-built run gains one: expected in `test_rounds_and_identity.py`, `test_dispatch_front.py` (only where the run is driver-built; `_run()` there calls `create_run` directly and does not move), `test_author_round.py` (`budget`), `test_review_round.py`, `test_phase_loop.py` (budget cases). Raise the pinned `max_seats` by one rather than the count.
- **(c) Fact sequences.** A test that pins the whole fact list of a fresh run now sees the shaping round's facts first (`attempt_dispatched`, three `command_requested`/`command_accepted` pairs, `turn_ended`, `model_ruling`, `transition_performed` to `queued`). Filter by kind, or start the expected list after the transition to `queued`; never delete an assertion. Expected in `test_front_table.py`, `test_turn_guards.py`, `test_grill_commands.py`, `test_human_commands.py`.
- **(d) Revision.** `revise_bundle` lands in `shaping`: `test_revise_bundle.py`'s three `== "queued"` become `== "shaping"` with `reshape=False`, and a test that runs an author round after a revision keeps `reshape=True` (the default).
- **(e) The model-ruling proof check.** `test_prove_front_half.py::test_each_assertion_fails_on_its_defect` plants "no model_ruling" by building a run that never rules on a question; a driver-built run now carries the shape ruling, so the proof (which does not yet exclude it) sees a ruling and that planted red goes green. Mark that one planted case `xfail(strict=True, reason="Task 11 excludes the shape ruling")`; Task 11 makes the proof exclude `question_id == "shape"` and removes the mark.
- **(f) Session listings.** A test that hand-builds the fake server's session set for `assert_sessions` lists every satisfied `sess-create` of the run (`front.created_sessions`), which now includes the shaping seat's.
- **(g) `test_verdict_phase_filter.py`** gains one parametrised case: a `slices` verdict is a front verdict and `revisions_used` skips it (§8: "`is_front_verdict` true for a `slices` verdict and `revisions_used` not spending a repair round on one").
- **(h) The coordinator's `_LOOP_STATES` is unchanged in this task.** Every coordinator test builds its run with the driver and so starts the loop at `queued`; none moves for state reasons.

End: **1477 passed, 3 skipped** (1452 + 25; a migrated test changes assertions, not counts, and rule (g) adds one). If a red is not explained by a rule above, it is a defect in this task, not a migration: stop and fix the code.

- [ ] **Step 9: Mutations**

Remove `"slices"` from `_REVISION_DESTINATION`'s human path (return `"specified"` for slices): `test_human_correction_at_the_slice_gate_lands_in_shaping` goes red. Make `_review_destination` return `"sliced"` on a slices accept: `test_reviewer_accept_lands_in_slices_accepted_whatever_the_gates` goes red. Delete the reserved-id check: `test_the_shape_id_is_reserved` goes red. Read the depth label from the bundle instead of `policy_frozen`: `test_a_slice_is_not_sliced_and_the_observable_is_policy_frozen` goes red. Revert all four.

- [ ] **Step 10: Commit**

```bash
git add v2/kernel v2/tests
git commit -m "feat(kernel): the shaping states, record_one_piece, submit_slices, advance_ungated; every run is born in shaping"
```

### Task 4: The filing and closing facts, the `sliced` outcome, and "an issue is sliced once"

**Files:**
- Modify: `v2/kernel/authz.py` (`_TRANSITIONS`: five rows; `_RUN_OUTCOMES`; `authorize`: five branches and the outcome guard)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact`: five branches)
- Modify: `v2/kernel/front.py` (append the filing queries)
- Modify: `v2/kernel/enqueue.py` (`IssueAlreadySliced`, the refusal in `create_run`)
- Modify: `v2/tests/kernel/front.py` (`_journal(cls=, state=)`, `file_slice`, `satisfy`, `file_all`)
- Modify: `docs/design/provenance-table.md`, `v2/tests/kernel/test_provenance.py:131-192,227-262`, `docs/design/2026-08-23-v2-kernel-design.md:175`
- Test: `v2/tests/kernel/test_filing_commands.py`

**Interfaces:**
- Consumes: `slices.parse`, `slices.topo_order`, `slices.Plan` (Task 1); `SHAPING_STATES`, `front.shape_ruling`, the driver (Task 3).
- Produces:
  - Commands, all legal only from `sliced` under an `operator` dispatch: `record_slice_filed {slice: int, effect_key: str}`; `record_filing_complete {}`; `record_slice_closed {slice, closed_at: str, state: "merged"|"closed"}`; `record_slice_reopened {slice, observed_at: str}`; `record_children_observed_closed {parent_state: "open"|"closed", observed_at: str}`.
  - Facts: `slice_filed {epoch, slice, issue, issue_id, title, parent, plan_hash}`; `filing_complete {epoch, plan_hash, children}`; `slice_closed {epoch, slice, issue, closed_at, state}`; `slice_reopened {epoch, slice, issue, observed_at}`; `children_observed_closed {epoch, generation, slices, parent, observed_at}`.
  - `record_run_outcome`: `_RUN_OUTCOMES` gains `"sliced"`; from `sliced` only `sliced` is accepted, and only under the guard below.
  - `enqueue.IssueAlreadySliced(Exception)`; `create_run` raises it for an issue whose earlier run holds an `issue_create` row not reconciled not-delivered.
  - `front.FILING_KINDS`, `front.COMPLETION_KINDS`; `front.is_satisfied(row)`; `front.issue_number(store, run_id) -> int`; `front.accepted_slices_hash(store, run_id, epoch_n) -> str | None`; `front.accepted_plan(store, run_id, epoch_n=None) -> Plan | None`; `front.slices_filed(store, run_id, epoch_n) -> dict[int, Fact]`; `front.filing_complete(store, run_id, epoch_n) -> Fact | None`; `front.current_closure(store, run_id, epoch_n, n) -> Fact | None`; `front.observed_closed_under(store, run_id, epoch_n, generation) -> Fact | None`; `front.satisfied_obligation(store, run_id, ob) -> dict | None`; `front.filing_obligations(store, run_id, epoch_n) -> list[dict]`; `front.unsatisfied_filing(store, run_id, epoch_n) -> list[dict]`; `front.issue_create_rows(store, run_id) -> list[dict]`; `front.create_attempted_for_issue(store, base_repo, number) -> str | None`.
  - Driver: `Front._journal(generation, key, intent, external, *, cls="session_control", state="confirmed")`; `Front.file_slice(generation, n, issue, issue_id) -> key`; `Front.satisfy(generation, ob, *, cls="issue_or_label", value="ok") -> key`; `Front.file_all(generation, first_issue=40) -> dict[int, int]` (every filing obligation satisfied and `filing_complete` recorded; returns slice → issue number).

The obligation shapes, which `front.filing_obligations` is the one source of (spec §3):

| kind | fields |
|---|---|
| `slice_issue` | `run, epoch, slice, parent, plan_hash` |
| `slice_dependency` | `run, epoch, plan_hash, slice, blocker` |
| `slice_queue` | `run, epoch, plan_hash, slice` |
| `umbrella`, `umbrella_label` | `run, epoch, plan_hash` |
| `umbrella_close`, `parent_close` | `run, epoch` |

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_filing_commands.py
"""Task 4: the filing and closing facts, the sliced outcome and the
sliced-once refusal (shaping spec §2)."""
import json

import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.enqueue import IssueAlreadySliced, create_run
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


def _sliced(tmp_path, name="k.db"):
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    return s, f, g


def _ob(f, kind, **more):
    return {"kind": kind, "run": f.run_id, "epoch": f.epoch(), **more}


# -- record_slice_filed --------------------------------------------------------

def test_slice_filed_reads_the_confirmed_row(tmp_path):
    s, f, g = _sliced(tmp_path)
    key = f.file_slice(g, 1, 40, 1040)
    fact = s.newest_fact(f.run_id, EventKind.SLICE_FILED)
    assert fact.payload == {"epoch": 0, "slice": 1, "issue": 40, "issue_id": 1040, "title": "The store",
                            "parent": 12, "plan_hash": s.phase_artifact(f.run_id, "slices")}
    assert front.slices_filed(s, f.run_id, 0)[1].id == fact.id
    with pytest.raises(NotAuthorized, match="already has a slice_filed"):
        f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": key})


def test_slice_filed_refusals(tmp_path):
    s, f, g = _sliced(tmp_path)
    h, parent = s.phase_artifact(f.run_id, "slices"), 12
    with pytest.raises(NotAuthorized, match="not in the accepted plan"):
        f._cmd(g, "record_slice_filed", {"slice": 9, "effect_key": "x"})
    # An intended (unconfirmed) create.
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": parent, "plan_hash": h}
    f._journal(g, "slice:intended", {"argv": ["gh", "issue", "create"], "obligation": ob}, None,
               cls="issue_create", state="intended")
    with pytest.raises(NotAuthorized, match="not a satisfied issue_create"):
        f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": "slice:intended"})
    # A confirmed create whose obligation names another slice.
    f._journal(g, "slice:wrong", {"argv": ["gh", "issue", "create"], "obligation": dict(ob, slice=2)},
               json.dumps({"url": "u", "number": 41, "id": 1041}), cls="issue_create")
    with pytest.raises(NotAuthorized, match="obligation"):
        f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": "slice:wrong"})
    # The wrong role.
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_slice_filed", {"slice": 1, "effect_key": "slice:wrong"})


# -- record_filing_complete ----------------------------------------------------

def test_filing_complete_needs_every_obligation(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_slice(g, 1, 40, 1040); f.file_slice(g, 2, 41, 1041)
    h = s.phase_artifact(f.run_id, "slices")
    with pytest.raises(NotAuthorized, match="slice_dependency"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "slice_dependency", plan_hash=h, slice=2, blocker=1))
    with pytest.raises(NotAuthorized, match="slice_queue"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "slice_queue", plan_hash=h, slice=1)); f.satisfy(g, _ob(f, "slice_queue", plan_hash=h, slice=2))
    f.satisfy(g, _ob(f, "umbrella", plan_hash=h), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-1")
    with pytest.raises(NotAuthorized, match="umbrella_label"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "umbrella_label", plan_hash=h))
    f._cmd(g, "record_filing_complete", {})
    fc = front.filing_complete(s, f.run_id, 0)
    assert fc.payload == {"epoch": 0, "plan_hash": h, "children": [40, 41]}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_filing_complete", {})
    assert s.run_state(f.run_id) == "sliced"


def test_filing_complete_needs_every_slice_filed(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_slice(g, 1, 40, 1040)
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_filing_complete", {})


# -- closures ------------------------------------------------------------------

def test_closures_are_observations_with_an_opposite(tmp_path):
    s, f, g = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="filing_complete"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "2026-09-10T00:00:00Z", "state": "merged"})
    f.file_all(g)
    with pytest.raises(NotAuthorized, match="no slice_filed"):
        f._cmd(g, "record_slice_closed", {"slice": 9, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="merged or closed"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "done"})
    with pytest.raises(NotAuthorized, match="never closed"):
        f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t"})
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t1", "state": "merged"})
    c = front.current_closure(s, f.run_id, 0, 1)
    assert c.kind == EventKind.SLICE_CLOSED and c.payload == {"epoch": 0, "slice": 1, "issue": 40, "closed_at": "t1", "state": "merged"}
    with pytest.raises(NotAuthorized, match="currently closed"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t2", "state": "merged"})
    f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t3"})
    assert front.current_closure(s, f.run_id, 0, 1).kind == EventKind.SLICE_REOPENED
    with pytest.raises(NotAuthorized, match="never closed|not currently closed"):
        f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t4"})
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t5", "state": "closed"})   # accepted again
    assert front.current_closure(s, f.run_id, 0, 1).payload["state"] == "closed"


def test_children_observed_closed(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_all(g)
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="open or closed"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "shut", "observed_at": "t"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    obs = front.observed_closed_under(s, f.run_id, 0, g)
    assert obs.payload == {"epoch": 0, "generation": g, "slices": [1, 2], "parent": "open", "observed_at": "t"}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})


# -- the outcome ---------------------------------------------------------------

def _close_all(f, g):
    for n in (1, 2):
        f._cmd(g, "record_slice_closed", {"slice": n, "closed_at": "t", "state": "merged"})


def test_only_sliced_leaves_sliced(tmp_path):
    s, f, g = _sliced(tmp_path)
    for outcome in ("failed", "merged", "escalated"):
        with pytest.raises(NotAuthorized, match="ends only as sliced"):
            f._cmd(g, "record_run_outcome", {"outcome": outcome})
    with pytest.raises(NotAuthorized, match="filing_complete"):
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    assert s.run_state(f.run_id) == "sliced"


def test_failed_is_still_legal_from_a_shaping_state(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1", shape=False)
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "record_run_outcome", {"outcome": "failed"})
    assert s.run_state("r-1") == "ended"


def test_sliced_outcome_guard(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_all(g)
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "merged"})
    f._cmd(g, "record_slice_reopened", {"slice": 2, "observed_at": "t"})
    with pytest.raises(NotAuthorized, match="slice 2"):                         # reopened after closing
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "closed"})
    with pytest.raises(NotAuthorized, match="children_observed_closed"):        # no observation
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    g2 = f._dispatch(Role.OPERATOR, "coordinator")                            # a later firing
    with pytest.raises(NotAuthorized, match="under this generation"):          # the earlier one's does not count
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g2, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    with pytest.raises(NotAuthorized, match="umbrella_close"):
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f.satisfy(g2, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    with pytest.raises(NotAuthorized, match="parent_close"):                  # open parent, no close
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f.satisfy(g2, _ob(f, "parent_close"), value="closed")
    f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    assert s.run_state(f.run_id) == "ended"
    assert s.newest_fact(f.run_id, EventKind.TRANSITION).payload == {"to": "ended", "via": "record_run_outcome"}


def test_a_hand_closed_parent_ends_without_a_close_and_a_reopened_one_with_its_old_close(tmp_path):
    s, f, g = _sliced(tmp_path, "a.db")
    f.file_all(g); _close_all(f, g)
    f._cmd(g, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    f.satisfy(g, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    f._cmd(g, "record_run_outcome", {"outcome": "sliced"})                    # no parent_close row
    assert s.run_state(f.run_id) == "ended"
    s2, f2, g2 = _sliced(tmp_path, "b.db")
    f2.file_all(g2); _close_all(f2, g2)
    f2.satisfy(g2, _ob(f2, "parent_close"), value="closed")                   # the pipeline closed it ...
    f2.satisfy(g2, _ob(f2, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    g3 = f2._dispatch(Role.OPERATOR, "coordinator")
    f2._cmd(g3, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})   # ... and a person reopened it
    f2._cmd(g3, "record_run_outcome", {"outcome": "sliced"})                  # ruling 22: the person's
    assert s2.run_state(f2.run_id) == "ended"


# -- sliced once ---------------------------------------------------------------

def _mint_again(s, run_id="i12-epic-2"):
    create_run(s, run_id=run_id, base_repo="o/r", base_sha="0" * 40, issue=ISSUE, project_config={})


def test_create_run_refuses_after_a_create_attempt_whatever_the_state(tmp_path):
    # Ended sliced.
    s, f, g = _sliced(tmp_path, "ended.db")
    f.file_all(g); _close_all(f, g)
    f._cmd(g, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    f.satisfy(g, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    with pytest.raises(IssueAlreadySliced, match="i12-epic-1"):
        _mint_again(s)
    # Cancelled after a confirmed create.
    s, f, g = _sliced(tmp_path, "cancelled.db")
    f.file_slice(g, 1, 40, 1040)
    f.human("cancel_run", {})
    assert s.run_state(f.run_id) == "cancelled"
    with pytest.raises(IssueAlreadySliced):
        _mint_again(s)
    # Halted on an uncertain create.
    s, f, g = _sliced(tmp_path, "halted.db")
    f._journal(g, "slice:u", {"argv": ["gh", "issue", "create"], "obligation": _ob(f, "slice_issue", slice=1, parent=12,
                                                                                   plan_hash=s.phase_artifact(f.run_id, "slices"))},
               None, cls="issue_create", state="uncertain")
    with pytest.raises(IssueAlreadySliced):
        _mint_again(s)


def test_create_run_admits_a_run_cancelled_before_any_create_and_other_outcomes(tmp_path):
    s, f, g = _sliced(tmp_path, "cancelled-early.db")
    f.human("cancel_run", {})
    _mint_again(s)
    assert s.run_state("i12-epic-2") == "shaping"
    s2 = Store.open(tmp_path / "reconciled.db")
    f2 = Front(s2, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g2 = f2._dispatch(Role.OPERATOR, "coordinator")
    f2._journal(g2, "slice:nd", {"argv": ["gh", "issue", "create"], "obligation": {}}, None,
                cls="issue_create", state="reconciled")           # not delivered: no child exists
    f2.human("cancel_run", {})
    _mint_again(s2)
    s3 = Store.open(tmp_path / "failed.db")
    f3 = Front(s3, "i12-epic-1", shape=False, issue=ISSUE)
    f3._cmd(f3._dispatch(Role.OPERATOR, "runner"), "record_run_outcome", {"outcome": "failed"})
    _mint_again(s3)


def test_revise_bundle_is_refused_from_sliced(tmp_path):
    s, f, g = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="not legal from state 'sliced'"):
        f.revise(dict(ISSUE, body="changed"), reshape=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_filing_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'IssueAlreadySliced'`.

- [ ] **Step 3: The driver's effect-row helpers**

In `v2/tests/kernel/front.py`, generalise `_journal` and add three helpers to the primitives section:

```python
    def _journal(self, generation: int, key: str, intent: dict, external: str | None, *,
                 cls: str = "session_control", state: str = "confirmed") -> None:
        # The effect ROW id is a global primary key while the idempotency key
        # is unique only within a run, so the run id goes in the row id.
        self.store.journal_intent(f"e-{self.run_id}-{key}", self.run_id, generation, cls, key, intent)
        self.store.mark_effect(key, state, external, run_id=self.run_id)

    def file_slice(self, generation: int, n: int, issue: int, issue_id: int) -> str:
        """A satisfied issue_create for slice *n* -- journalled directly, as
        `_journal` does for sessions -- and its slice_filed fact."""
        from kernel import slices as _slices
        epoch_n = self.epoch()
        h = fq.accepted_slices_hash(self.store, self.run_id, epoch_n)
        parent = fq.issue_number(self.store, self.run_id)
        key = f"slice:{self.run_id}:{n}:{generation}"
        ob = {"kind": "slice_issue", "run": self.run_id, "epoch": epoch_n, "slice": n,
              "parent": parent, "plan_hash": h}
        self._journal(generation, key, {"argv": ["gh", "issue", "create", "--repo", "o/r", "--title",
                                                 "t", "--label", "bircher:slice"], "obligation": ob},
                      json.dumps({"url": f"https://github.com/o/r/issues/{issue}", "number": issue, "id": issue_id}),
                      cls="issue_create")
        self._cmd(generation, "record_slice_filed", {"slice": n, "effect_key": key})
        return key

    def satisfy(self, generation: int, ob: dict, *, cls: str = "issue_or_label", value: str = "ok") -> str:
        """A satisfied effect carrying *ob*, journalled directly."""
        key = f"{ob['kind']}:{self.run_id}:{ob.get('slice', '')}:{ob.get('blocker', '')}:{generation}"
        self._journal(generation, key, {"argv": ["gh", "issue", "edit", "1", "--repo", "o/r"],
                                        "obligation": ob}, value, cls=cls)
        return key

    def file_all(self, generation: int, first_issue: int = 40) -> dict[int, int]:
        """Every filing obligation of the accepted plan satisfied, in
        dependency order, and filing_complete recorded."""
        from kernel import slices as _slices
        plan = fq.accepted_plan(self.store, self.run_id)
        filed = {}
        for n in _slices.topo_order(plan):
            filed[n] = first_issue + n - 1
            self.file_slice(generation, n, filed[n], 1000 + filed[n])
        for ob in fq.filing_obligations(self.store, self.run_id, self.epoch()):
            if ob["kind"] == "slice_issue":
                continue
            self.satisfy(generation, ob, cls="comment" if ob["kind"] == "umbrella" else "issue_or_label",
                         value="https://github.com/o/r/issues/1#issuecomment-1" if ob["kind"] == "umbrella" else "ok")
        self._cmd(generation, "record_filing_complete", {})
        return filed
```

- [ ] **Step 4: `front.py` — the filing queries**

Append to `v2/kernel/front.py`:

```python
# -- the shaping phase (shaping spec §2, §3) ------------------------------------

FILING_KINDS = frozenset({"slice_issue", "slice_dependency", "slice_queue", "umbrella", "umbrella_label"})
COMPLETION_KINDS = frozenset({"umbrella_close", "parent_close"})


def is_satisfied(row: dict) -> bool:
    return _satisfied(row)


def issue_number(store, run_id: str) -> int:
    """The run's issue, from the bundle snapshot the kernel holds."""
    return int(json.loads(store.read_blob(bundle_hash(store, run_id)))["number"])


def accepted_slices_hash(store, run_id: str, epoch_n: int) -> str | None:
    """The hash the epoch's `human_ruling {approve, phase: slices}` or
    `artifact_advanced {phase: slices}` named: the plan a reviewer accepted
    and the gate passed, not any plan submitted (spec §2 *What perform
    refuses*)."""
    found = None
    for f in store.facts_of_kind(run_id, EventKind.HUMAN_RULING, EventKind.ARTIFACT_ADVANCED):
        p = f.payload
        if p.get("phase") != "slices" or p.get("epoch") != epoch_n:
            continue
        if f.kind == EventKind.HUMAN_RULING:
            if p.get("ruling") == "approve":
                found = p.get("artifact_hash")
        else:
            found = p.get("hash")
    return found


def accepted_plan(store, run_id: str, epoch_n: int | None = None):
    from kernel import slices
    n = epoch(store, run_id) if epoch_n is None else epoch_n
    h = accepted_slices_hash(store, run_id, n)
    return None if h is None else slices.parse(store.read_blob(h))


def slices_filed(store, run_id: str, epoch_n: int) -> dict:
    return {f.payload["slice"]: f for f in epoch_facts(store, run_id, EventKind.SLICE_FILED, epoch_n)}


def filing_complete(store, run_id: str, epoch_n: int):
    fs = epoch_facts(store, run_id, EventKind.FILING_COMPLETE, epoch_n)
    return fs[-1] if fs else None


def current_closure(store, run_id: str, epoch_n: int, n: int):
    """The slice's newest `slice_closed` or `slice_reopened` in the epoch, or
    None: a closure is an observation with a date, not a property."""
    found = None
    for f in store.facts_of_kind(run_id, EventKind.SLICE_CLOSED, EventKind.SLICE_REOPENED):
        if f.payload.get("epoch") == epoch_n and f.payload.get("slice") == n:
            found = f
    return found


def observed_closed_under(store, run_id: str, epoch_n: int, generation: int):
    for f in epoch_facts(store, run_id, EventKind.CHILDREN_OBSERVED_CLOSED, epoch_n):
        if f.payload.get("generation") == generation:
            return f
    return None


def satisfied_obligation(store, run_id: str, ob: dict) -> dict | None:
    for row in store.effects_for(run_id):
        if (row.get("intent") or {}).get("obligation") == ob and _satisfied(row):
            return row
    return None


def filing_obligations(store, run_id: str, epoch_n: int) -> list[dict]:
    """Every obligation the accepted plan implies (spec §2
    `record_filing_complete`, §3 *Filing the children*), the one source of
    their shapes: creates, then links, then queue labels, then the umbrella
    and its label."""
    plan = accepted_plan(store, run_id, epoch_n)
    h = accepted_slices_hash(store, run_id, epoch_n)
    parent = issue_number(store, run_id)
    base = {"run": run_id, "epoch": epoch_n}
    obs = [dict(base, kind="slice_issue", slice=s.number, parent=parent, plan_hash=h) for s in plan.slices]
    obs += [dict(base, kind="slice_dependency", plan_hash=h, slice=n, blocker=b) for n, b in plan.edges()]
    obs += [dict(base, kind="slice_queue", plan_hash=h, slice=s.number) for s in plan.slices]
    obs += [dict(base, kind="umbrella", plan_hash=h), dict(base, kind="umbrella_label", plan_hash=h)]
    return obs


def unsatisfied_filing(store, run_id: str, epoch_n: int) -> list[dict]:
    return [ob for ob in filing_obligations(store, run_id, epoch_n)
            if satisfied_obligation(store, run_id, ob) is None]


def issue_create_rows(store, run_id: str) -> list[dict]:
    return [r for r in store.effects_for(run_id) if r["effect_class"] == "issue_create"]


def create_attempted_for_issue(store, base_repo: str, number: int) -> str | None:
    """A run of the same repository and issue holding ANY issue_create row
    not reconciled not-delivered -- intended, confirmed, uncertain or
    reconciled delivered -- whatever that run's state (spec §2 *An issue is
    sliced once*, ruling 12). The observable is the attempt: a child may
    exist from the first attempt on."""
    for rid in store.all_run_ids():
        try:
            if store.run_base_repo(rid) != base_repo:
                continue
            enq = store.newest_fact(rid, EventKind.RUN_ENQUEUED)
            blob = None if enq is None or not enq.payload.get("bundle_hash") else store.read_blob(enq.payload["bundle_hash"])
            if blob is None or int(json.loads(blob).get("number", -1)) != number:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        for row in issue_create_rows(store, rid):
            if row["state"] == "reconciled" and row.get("external_object_id") in (None, ""):
                continue
            return rid
    return None
```

- [ ] **Step 5: `authz.py` — the five commands and the outcome guard**

`_TRANSITIONS` gains, after `"advance_ungated"`:

```python
    # The filing and closing facts (shaping spec §2): no transition; legal
    # only from `sliced`, under the coordinator's or the sweep's operator
    # dispatch (checked in authorize).
    "record_slice_filed": (frozenset({"sliced"}), None),
    "record_filing_complete": (frozenset({"sliced"}), None),
    "record_slice_closed": (frozenset({"sliced"}), None),
    "record_slice_reopened": (frozenset({"sliced"}), None),
    "record_children_observed_closed": (frozenset({"sliced"}), None),
```

`_RUN_OUTCOMES` gains `"sliced"` with a comment: `# sliced: the sweep's terminal record for an epic whose children all closed (shaping spec §2); guarded in authorize.`

Add before `authorize`:

```python
def _check_operator(store, cmd) -> None:
    if role_for(store, cmd.run_id, cmd.generation) != Role.OPERATOR:
        raise NotAuthorized(f"{cmd.name} must come from an attempt dispatched in the operator role")


def _check_slice_in_plan(store, cmd, n) -> None:
    """A payload `slice` is observed: refused unless the accepted plan has it."""
    from kernel import front
    plan = front.accepted_plan(store, cmd.run_id)
    if plan is None or type(n) is not int or n not in plan.by_number():
        raise NotAuthorized(f"{cmd.name}: slice {n!r} is not in the accepted plan")
```

Add these branches to `authorize`, after the `advance_ungated` branch:

```python
    if cmd.name == "record_slice_filed":
        import json as _json
        from kernel import front
        _check_operator(store, cmd)
        n, key = cmd.payload.get("slice"), cmd.payload.get("effect_key")
        _check_slice_in_plan(store, cmd, n)
        epoch_n = front.epoch(store, cmd.run_id)
        row = None if not isinstance(key, str) else store.effect_by_key(key, run_id=cmd.run_id)
        if row is None or row["effect_class"] != "issue_create" or not front.is_satisfied(row):
            raise NotAuthorized(f"record_slice_filed: {key!r} is not a satisfied issue_create of this run")
        want = {"kind": "slice_issue", "run": cmd.run_id, "epoch": epoch_n, "slice": n,
                "parent": front.issue_number(store, cmd.run_id),
                "plan_hash": front.accepted_slices_hash(store, cmd.run_id, epoch_n)}
        if (row["intent"].get("obligation") or {}) != want:
            raise NotAuthorized(f"record_slice_filed: {key}'s obligation {row['intent'].get('obligation')} is not {want}")
        try:
            delivered = _json.loads(row["external_object_id"])
            int(delivered["number"]); int(delivered["id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise NotAuthorized(f"record_slice_filed: {key}'s delivered value is not {{url, number, id}}") from exc
        if n in front.slices_filed(store, cmd.run_id, epoch_n):
            raise NotAuthorized(f"slice {n} already has a slice_filed in epoch {epoch_n}")
        return None

    if cmd.name == "record_filing_complete":
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is not None:
            raise NotAuthorized(f"filing_complete already recorded in epoch {epoch_n}")
        filed = front.slices_filed(store, cmd.run_id, epoch_n)
        for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
            if s.number not in filed:
                raise NotAuthorized(f"record_filing_complete: slice {s.number} has no slice_filed")
        owed = front.unsatisfied_filing(store, cmd.run_id, epoch_n)
        if owed:
            raise NotAuthorized(f"record_filing_complete: {len(owed)} obligation(s) unsatisfied, first {owed[0]}")
        return None

    if cmd.name in ("record_slice_closed", "record_slice_reopened"):
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is None:
            raise NotAuthorized(f"{cmd.name}: no filing_complete in epoch {epoch_n}; the sweep reads only a filed epic")
        n = cmd.payload.get("slice")
        if n not in front.slices_filed(store, cmd.run_id, epoch_n):
            raise NotAuthorized(f"{cmd.name}: no slice_filed for slice {n!r} in epoch {epoch_n}")
        cur = front.current_closure(store, cmd.run_id, epoch_n, n)
        if cmd.name == "record_slice_closed":
            if cmd.payload.get("state") not in ("merged", "closed"):
                raise NotAuthorized("record_slice_closed state is merged or closed")
            if not _non_empty_str(cmd.payload.get("closed_at")):
                raise NotAuthorized("record_slice_closed carries closed_at")
            if cur is not None and cur.kind == EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {n} is currently closed: a closure is recorded once until a reopening")
        else:
            if not _non_empty_str(cmd.payload.get("observed_at")):
                raise NotAuthorized("record_slice_reopened carries observed_at")
            if cur is None:
                raise NotAuthorized(f"slice {n} was never closed: nothing to reopen")
            if cur.kind != EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {n} is not currently closed")
        return None

    if cmd.name == "record_children_observed_closed":
        from kernel import front
        _check_operator(store, cmd)
        epoch_n = front.epoch(store, cmd.run_id)
        if front.filing_complete(store, cmd.run_id, epoch_n) is None:
            raise NotAuthorized(f"no filing_complete in epoch {epoch_n}")
        if cmd.payload.get("parent_state") not in ("open", "closed"):
            raise NotAuthorized("record_children_observed_closed parent_state is open or closed")
        if not _non_empty_str(cmd.payload.get("observed_at")):
            raise NotAuthorized("record_children_observed_closed carries observed_at")
        for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
            cur = front.current_closure(store, cmd.run_id, epoch_n, s.number)
            if cur is None or cur.kind != EventKind.SLICE_CLOSED:
                raise NotAuthorized(f"slice {s.number} is not currently closed: the statement cannot contradict the closing facts")
        if front.observed_closed_under(store, cmd.run_id, epoch_n, cmd.generation) is not None:
            raise NotAuthorized(f"children_observed_closed already recorded under generation {cmd.generation}")
        return None
```

and replace the `record_run_outcome` branch:

```python
    if cmd.name == "record_run_outcome":
        outcome = cmd.payload.get("outcome")
        if outcome not in _RUN_OUTCOMES:
            raise NotAuthorized(
                f"outcome {outcome!r} is not one of {sorted(_RUN_OUTCOMES)}"
            )
        if outcome == "merged" and not store.has_confirmed_effect(
            cmd.run_id, "merge"
        ):
            raise NotAuthorized(
                "no confirmed merge outcome for this run: a merged outcome "
                "reports what the mechanism observed, not what an actor claims"
            )
        # A run that entered `sliced` may have filed children; `failed` over
        # them would end the epic with its filing half done and nothing left
        # to repair it (shaping spec §2, ruling 15). cancel_run is the other exit.
        if current == "sliced" and outcome != "sliced":
            raise NotAuthorized(f"a run that entered sliced ends only as sliced, not {outcome!r}; cancel_run is the other exit")
        if outcome == "sliced":
            from kernel import front
            if current != "sliced":
                raise NotAuthorized("outcome sliced is legal only from sliced")
            _check_operator(store, cmd)
            epoch_n = front.epoch(store, cmd.run_id)
            if front.filing_complete(store, cmd.run_id, epoch_n) is None:
                raise NotAuthorized("outcome sliced: no filing_complete in this epoch")
            for s in front.accepted_plan(store, cmd.run_id, epoch_n).slices:
                cur = front.current_closure(store, cmd.run_id, epoch_n, s.number)
                if cur is None or cur.kind != EventKind.SLICE_CLOSED:
                    raise NotAuthorized(f"outcome sliced: slice {s.number} is not currently closed")
            obs = front.observed_closed_under(store, cmd.run_id, epoch_n, cmd.generation)
            if obs is None:
                raise NotAuthorized(
                    "outcome sliced: no children_observed_closed under this generation -- the firing "
                    "that ends the run is the firing that read every child (ruling 17)"
                )
            base = {"run": cmd.run_id, "epoch": epoch_n}
            if front.satisfied_obligation(store, cmd.run_id, dict(base, kind="umbrella_close")) is None:
                raise NotAuthorized("outcome sliced: the umbrella_close comment is not satisfied")
            # A hand-closed parent owes no close; a satisfied close beside an
            # observed-open parent is a person's reopening (ruling 22).
            if obs.payload.get("parent") == "open" and \
                    front.satisfied_obligation(store, cmd.run_id, dict(base, kind="parent_close")) is None:
                raise NotAuthorized("outcome sliced: the parent was observed open and its parent_close is not satisfied")
        return "ended"
```

- [ ] **Step 6: `commands.py` and `enqueue.py`**

`COMMAND_NAMES` gains:

```python
    # The filing and closing facts (shaping spec §2).
    "record_slice_filed", "record_filing_complete", "record_slice_closed",
    "record_slice_reopened", "record_children_observed_closed",
```

`_side_fact` gains, before `elif cmd.name == "record_turn_ended":`:

```python
    elif cmd.name == "record_slice_filed":
        import json as _json
        row = store.effect_by_key(cmd.payload["effect_key"], run_id=cmd.run_id)
        delivered = _json.loads(row["external_object_id"])
        plan = front.accepted_plan(store, cmd.run_id, epoch_n)
        n = cmd.payload["slice"]
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.SLICE_FILED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            # The number and the database id are OBSERVED from the confirmed
            # row's delivered value, never taken from the payload.
            payload={"epoch": epoch_n, "slice": n, "issue": int(delivered["number"]),
                     "issue_id": int(delivered["id"]), "title": plan.by_number()[n].title,
                     "parent": front.issue_number(store, cmd.run_id),
                     "plan_hash": front.accepted_slices_hash(store, cmd.run_id, epoch_n)},
        )
    elif cmd.name == "record_filing_complete":
        filed = front.slices_filed(store, cmd.run_id, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.FILING_COMPLETE, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "plan_hash": front.accepted_slices_hash(store, cmd.run_id, epoch_n),
                     "children": [filed[n].payload["issue"] for n in sorted(filed)]},
        )
    elif cmd.name in ("record_slice_closed", "record_slice_reopened"):
        n = cmd.payload["slice"]
        issue = front.slices_filed(store, cmd.run_id, epoch_n)[n].payload["issue"]
        if cmd.name == "record_slice_closed":
            payload = {"epoch": epoch_n, "slice": n, "issue": issue,
                       "closed_at": cmd.payload["closed_at"], "state": cmd.payload["state"]}
            kind = EventKind.SLICE_CLOSED
        else:
            payload = {"epoch": epoch_n, "slice": n, "issue": issue, "observed_at": cmd.payload["observed_at"]}
            kind = EventKind.SLICE_REOPENED
        store.append_fact(run_id=cmd.run_id, kind=kind, actor=actor,
                          causal_command_id=cmd.idempotency_key, payload=payload)
    elif cmd.name == "record_children_observed_closed":
        plan = front.accepted_plan(store, cmd.run_id, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.CHILDREN_OBSERVED_CLOSED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n, "generation": cmd.generation,
                     "slices": [s.number for s in plan.slices],
                     "parent": cmd.payload["parent_state"], "observed_at": cmd.payload["observed_at"]},
        )
```

`enqueue.py`: add the exception and the refusal:

```python
class IssueAlreadySliced(Exception):
    """An earlier run of this issue attempted a child create; an issue is
    sliced once (shaping spec §2, ruling 12)."""
```

and in `create_run`, after `bhash = content_hash(raw)` and before the `_run_exists` replay check:

```python
    from kernel import front
    prior = front.create_attempted_for_issue(store, base_repo, int(snap["number"]))
    if prior is not None and not _run_exists(store, run_id):
        raise IssueAlreadySliced(
            f"issue #{snap['number']} of {base_repo}: run {prior} attempted a child create, so a child "
            "may exist; an issue is sliced once (shaping spec §2). Close the children that should not "
            "be worked and open a new issue for what remains (§9)."
        )
```

(The `_run_exists` clause keeps an identical replay of the same run id a replay.)

- [ ] **Step 7: Provenance**

In `docs/design/provenance-table.md`:
- *Kernel state*: add `| \`store.effect_by_key\` | \`record_slice_filed\` | observed | the effect journal's row for the key the command names; the created issue's number and database id are read from its delivered value |`.
- *Caller-presented, bound*: add `| \`cmd.payload['slice']\` | \`record_slice_filed\`, \`record_slice_closed\`, \`record_slice_reopened\` | observed | refused unless the accepted plan has the slice (\`front.accepted_plan\`) and, for a closure, unless the epoch holds its \`slice_filed\` |` and `| \`cmd.payload['effect_key']\` | \`record_slice_filed\` | observed | refused unless it names a satisfied \`issue_create\` row of this run whose obligation names this run, epoch, slice, parent and the accepted plan's hash |`.
- The `cmd.payload['reasoning']` and `cmd.payload['cost_if_wrong']` rows' "Enters at" cells gain `, record_one_piece`.
- *Asserted — declared residuals*: add four rows, each `asserted`, reason `**Residual, shaping §2.** The sweep's observation of GitHub, shape-checked (…) and recorded; whether it is true is the sweep's, as every observation of GitHub in this design is (ruling 17).` for `cmd.payload['closed_at']` (`record_slice_closed`), `cmd.payload['state']` (`record_slice_closed`; bounded to `merged`/`closed`), `cmd.payload['observed_at']` (`record_slice_reopened`, `record_children_observed_closed`), `cmd.payload['parent_state']` (`record_children_observed_closed`; bounded to `open`/`closed`).

In `v2/tests/kernel/test_provenance.py`, add the four to the pinned set with the comment `# The shaping phase's four: the sweep's readings of GitHub (shaping spec §2, ruling 17).`, and add `"twenty-six": 26, "twenty-seven": 27, "twenty-eight": 28, "twenty-nine": 29` to `words`. In `docs/design/2026-08-23-v2-kernel-design.md:175`, change `**Twenty-five rows are asserted after Milestone 1**` to `**Twenty-nine rows are asserted after Milestone 1**` and extend the list with `, and the shaping phase's four: the sweep's closed-at, state, observed-at and parent-state readings of GitHub`, changing `Fifteen are residuals to close` to `Nineteen are residuals to close`.

- [ ] **Step 8: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_filing_commands.py tests/kernel/test_provenance.py -q`, then the suite.
Expected: PASS; **1490 passed, 3 skipped** (13 new).

- [ ] **Step 9: Mutations**

Make `record_run_outcome` skip the `current == "sliced"` refusal: `test_only_sliced_leaves_sliced` reds. Read the observation fact from any generation (drop the `generation` comparison in `observed_closed_under`): `test_sliced_outcome_guard` reds at "under this generation". Make `create_attempted_for_issue` read only ended runs: the cancelled case of `test_create_run_refuses_after_a_create_attempt_whatever_the_state` reds. Revert.

- [ ] **Step 10: Commit**

```bash
git add v2/kernel v2/tests docs/design
git commit -m "feat(kernel): the filing and closing facts, the sliced outcome, and an issue sliced once"
```

### Task 5: The effects — `issue_create`, the blocked-by rule, delivered forms, the executor, and what `perform` refuses

**Files:**
- Modify: `v2/kernel/effect_class.py` (`ISSUE_CREATE`)
- Modify: `v2/kernel/contract.py` (`Rule.forbid_value`, `check`, `CONTRACTS`)
- Modify: `v2/kernel/effects.py:55-125` (`perform`: the filing hook), `:389-433` (`_delivered_value`)
- Modify: `v2/kernel/cli.py:122-188` (`_write_raw_file`, the executor's `ISSUE_CREATE` branch)
- Create: `v2/kernel/filing.py`
- Modify: `v2/kernel/bundle.py:41-52`, `batch/run-queue.sh:3090-3095`, `v2/tests/fixtures/bircher_status_comments.tsv`, `v2/tests/kernel/test_bundle_v2.py:32-44`
- Test: `v2/tests/kernel/test_filing_effects.py`, `v2/tests/kernel/test_effect_contract.py` (extend), `v2/tests/kernel/test_executor_body.py` (extend), `v2/tests/kernel/test_reconcile_typed.py` (extend)

**Interfaces:**
- Consumes: Task 4's `front` queries; `slices.render_child`, `umbrella_body`, `completion_body`, `inherited_labels` (Task 1).
- Produces:
  - `EffectClass.ISSUE_CREATE = "issue_create"` in `ALL`.
  - `contract.Rule.forbid_value: tuple[tuple[str, frozenset[str]], ...]`; `CONTRACTS[ISSUE_CREATE]` = one rule `gh issue create` with flags `--repo --title --label`, required `--repo --title`, `--label` values may not be `bircher:queued`, `bircher:running`, `bircher:sliced`; `--body-file` is not admitted (the executor appends it). `CONTRACTS[ISSUE_OR_LABEL]` gains `gh api <path ending /issues/<n>/dependencies/blocked_by> -X POST -f issue_id=<id>`.
  - `filing.KINDS`; `filing.required_kinds(store, run_id, effect_class, argv) -> frozenset[str]`; `filing.check_filing_effect(store, run_id, generation, effect_class, key, intent) -> None` (raises `NotAuthorized`); argv composers `filing.create_argv(repo, title, labels)`, `link_argv(repo, child, blocker_id)`, `queue_argv(repo, child)`, `comment_argv(repo, issue, body)`, `umbrella_label_argv(repo, parent)`, `close_argv(repo, parent)`; `filing.expected_labels(store, run_id) -> list[str]`; `filing.filed_numbers(store, run_id, epoch_n) -> dict[int, int]`; `filing.filed_ids(store, run_id, epoch_n) -> dict[int, int]`.
  - `_delivered_value` for the seven kinds (`slice_issue` a `{url, number, id}` JSON; `slice_dependency`/`slice_queue`/`umbrella_label` `"ok"` with no value; `umbrella`/`umbrella_close` the comment URL; `parent_close` `"closed"`).
  - The executor: for `ISSUE_CREATE`, writes the intent's `body.artifact` blob raw to a temp file, runs `argv + ["--body-file", path]`, parses the issue number from the URL, reads back `gh api repos/<repo>/issues/<n> --jq .id`, returns `json.dumps({"url", "number", "id"}, sort_keys=True)`.
  - `bundle.BIRCHER_STATUS_PREFIXES` gains `"bircher: sliced "` (seventh); the bash copy too.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_filing_effects.py
"""Task 5: what `perform` refuses of a filing effect (shaping spec §2 *What
perform refuses*, *Bound to the effect*, *One satisfied effect per
obligation*), the contract, the delivered forms, the executor."""
import json

import pytest

from kernel import filing, front, slices
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.contract import CONTRACTS, ContractViolation, check
from kernel.dispatch import Role
from kernel.effects import EffectClass, NotReplayable, Resolution, _delivered_value, perform, reconcile_typed
from kernel.store import Store
from tests.kernel.front import Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous", "bircher:gate-plan"], "comments": []}


class FakeGH:
    """An executor that answers the filing argvs as GitHub would."""
    def __init__(self):
        self.calls, self.next_issue, self.next_comment = [], 40, 1

    def __call__(self, effect_class, intent, key):
        argv = list(intent["argv"])
        self.calls.append((effect_class, argv, intent.get("body")))
        if effect_class == EffectClass.ISSUE_CREATE:
            n = self.next_issue; self.next_issue += 1
            return json.dumps({"url": f"https://github.com/o/r/issues/{n}", "number": n, "id": 1000 + n}, sort_keys=True)
        if argv[:3] == ["gh", "issue", "comment"]:
            c = self.next_comment; self.next_comment += 1
            return f"https://github.com/o/r/issues/{argv[3]}#issuecomment-{c}"
        if argv[:3] == ["gh", "issue", "close"]:
            return "closed"
        return "ok"


def _sliced(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    return s, f, g, FakeGH()


def _create(s, f, g, gh, n, *, key=None, title=None, labels=None, body=None, repo="o/r", plan_hash=None, epoch=0):
    """A child create for slice *n*, composed as file_owed would; each
    keyword plants one defect. Robust to the states the refusal tests need:
    before the gate there is no accepted hash (the phase artefact is used), a
    slice outside the plan renders a placeholder, and an unfiled blocker
    leaves the body a placeholder so the KERNEL's refusal is what the test
    sees, not render_child's."""
    h = plan_hash or front.accepted_slices_hash(s, f.run_id, 0) or s.phase_artifact(f.run_id, "slices")
    plan = slices.parse(s.read_blob(h))
    filed = filing.filed_numbers(s, f.run_id, 0)
    if n in plan.by_number():
        try:
            t, b = slices.render_child(plan.by_number()[n], 12, h[:8], filed)
        except slices.PlanError:
            t, b = plan.by_number()[n].title, "unfiled blocker\n"
    else:
        t, b = "x", "x\n"
    title = t if title is None else title
    body = b if body is None else body
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": epoch, "slice": n, "parent": 12, "plan_hash": h}
    argv = filing.create_argv(repo, title, filing.expected_labels(s, f.run_id) if labels is None else labels)
    return perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, key or f"slice:{f.run_id}:{n}:{g}",
                   {"argv": argv, "obligation": ob, "body": {"artifact": put_artifact(s, body.encode())}}, gh)


# -- the contract --------------------------------------------------------------

def test_issue_create_contract():
    ok = filing.create_argv("o/r", "T", ["bircher:slice", "bircher:autonomous"])
    assert check(EffectClass.ISSUE_CREATE, ok).sig == "gh issue create"
    for bad in (["gh", "issue", "create", "--title", "T"],                              # no --repo
                ok + ["--body-file", "/tmp/x"],                                          # the executor's flag
                ok + ["--label", "bircher:queued"], ok + ["--label", "bircher:running"],
                ok + ["--label", "bircher:sliced"],
                ["gh", "issue", "edit", "1", "--repo", "o/r"]):
        with pytest.raises(ContractViolation):
            check(EffectClass.ISSUE_CREATE, bad)
    link = filing.link_argv("o/r", 41, 1040)
    assert check(EffectClass.ISSUE_OR_LABEL, link).url_path.endswith("blocked_by$")
    with pytest.raises(ContractViolation):
        check(EffectClass.ISSUE_OR_LABEL, ["gh", "api", "repos/o/r/issues/41/dependencies/blocked_by", "-X", "DELETE"])
    assert EffectClass.ISSUE_CREATE in EffectClass.ALL and EffectClass.ISSUE_CREATE in CONTRACTS


# -- the mandate ---------------------------------------------------------------

def test_every_added_argv_shape_needs_its_kind(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    cases = [
        (EffectClass.ISSUE_CREATE, filing.create_argv("o/r", "T", ["bircher:slice"]), {"slice_issue"}),
        (EffectClass.ISSUE_OR_LABEL, filing.link_argv("o/r", 41, 1040), {"slice_dependency"}),
        (EffectClass.ISSUE_OR_LABEL, filing.queue_argv("o/r", 41), {"slice_queue"}),
        (EffectClass.ISSUE_OR_LABEL, filing.umbrella_label_argv("o/r", 12), {"umbrella_label"}),
        (EffectClass.COMMENT, filing.comment_argv("o/r", 12, f"bircher: sliced {h[:8]}\n"), {"umbrella", "umbrella_close"}),
        (EffectClass.ISSUE_OR_LABEL, filing.close_argv("o/r", 12), {"parent_close"}),
    ]
    for i, (cls, argv, kinds) in enumerate(cases):
        assert filing.required_kinds(s, f.run_id, cls, argv) == frozenset(kinds), argv
        with pytest.raises(NotAuthorized, match="admitted only with an obligation"):      # none
            perform(s, f.run_id, g, cls, f"k-none-{i}", {"argv": argv}, gh)
        with pytest.raises(NotAuthorized, match="admitted only with an obligation"):      # another kind
            perform(s, f.run_id, g, cls, f"k-other-{i}",
                    {"argv": argv, "obligation": {"kind": "publish", "run": f.run_id, "phase": "spec", "hash": "x"}}, gh)
    assert gh.calls == []
    # The runner's own swap is admitted with no obligation, as today.
    assert filing.required_kinds(s, f.run_id, EffectClass.ISSUE_OR_LABEL,
                                 ["gh", "issue", "edit", "12", "--repo", "o/r", "--add-label", "bircher:running",
                                  "--remove-label", "bircher:queued"]) == frozenset()
    # A close on some OTHER issue, or on the parent of a run not in sliced, demands nothing.
    assert filing.required_kinds(s, f.run_id, EffectClass.ISSUE_OR_LABEL, filing.close_argv("o/r", 99)) == frozenset()


# -- preconditions and bindings for the create -----------------------------------

def test_create_is_bound_to_state_role_epoch_hash_slice_and_body(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    ext = json.loads(_create(s, f, g, gh, 1))
    assert ext["number"] == 40 and "artifact" in gh.calls[-1][2]
    key1 = [r for r in s.effects_for(f.run_id) if r["effect_class"] == "issue_create"][-1]["idempotency_key"]
    f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": key1})         # slice 2's blocker is filed
    with pytest.raises(NotAuthorized, match="operator dispatch"):
        _create(s, f, f._dispatch(Role.AUTHOR, "claude"), gh, 2)
    g = f._dispatch(Role.OPERATOR, "coordinator")
    with pytest.raises(NotAuthorized, match="not in the accepted plan"):
        _create(s, f, g, gh, 9)
    with pytest.raises(NotAuthorized, match="title"):
        _create(s, f, g, gh, 2, title="Wrong")
    with pytest.raises(NotAuthorized, match="render_child"):
        _create(s, f, g, gh, 2, body="Slice 2 of #12 (bircher shaping xxxxxxxx)\n\nother\n")
    with pytest.raises(NotAuthorized, match="labels"):
        _create(s, f, g, gh, 2, labels=["bircher:slice"])                     # inherited ones missing
    with pytest.raises(NotAuthorized, match="repo"):
        _create(s, f, g, gh, 2, repo="o/other")
    assert len([c for c in gh.calls if c[0] == EffectClass.ISSUE_CREATE]) == 1    # nothing else reached GitHub


def test_create_is_refused_before_the_gate_and_against_a_rejected_plan(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE)
    from tests.kernel.front import SLICES_BYTES
    rejected = f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"no")
    accepted = f.shape_round(SLICES_BYTES.replace(b"the API.", b"the API and the client."))
    f.review_round("accept")
    assert s.run_state(f.run_id) == "slices_accepted"
    g = f._dispatch(Role.OPERATOR, "coordinator")
    gh = FakeGH()
    with pytest.raises(NotAuthorized, match="sliced"):                          # before the gate
        _create(s, f, g, gh, 1)
    f.approve()
    plan = slices.parse(s.read_blob(accepted))
    t, b = slices.render_child(plan.slices[0], 12, rejected[:8], {})
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": rejected}
    with pytest.raises(NotAuthorized, match="accepted hash"):                   # the rejected plan's hash
        perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, "k", {"argv": filing.create_argv("o/r", t, filing.expected_labels(s, f.run_id)),
                                                               "obligation": ob, "body": {"artifact": put_artifact(s, b.encode())}}, gh)
    assert gh.calls == []


def test_create_is_refused_while_a_blocker_is_unfiled_and_a_stale_epoch(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="no filed issue"):
        _create(s, f, g, gh, 2)                                                 # slice 2 depends on 1
    h = front.accepted_slices_hash(s, f.run_id, 0)
    plan = front.accepted_plan(s, f.run_id)
    t, b = slices.render_child(plan.slices[0], 12, h[:8], {})
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 1, "slice": 1, "parent": 12, "plan_hash": h}
    with pytest.raises(NotAuthorized, match="epoch"):
        perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, "k", {"argv": filing.create_argv("o/r", t, filing.expected_labels(s, f.run_id)),
                                                               "obligation": ob, "body": {"artifact": put_artifact(s, b.encode())}}, gh)


# -- uniqueness and replay -----------------------------------------------------

def test_one_satisfied_effect_per_obligation(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    key = f"slice:{f.run_id}:1:{g}"
    first = _create(s, f, g, gh, 1, key=key)
    assert _create(s, f, g, gh, 1, key=key) == first                              # the confirmed key replays
    assert len([c for c in gh.calls if c[0] == EffectClass.ISSUE_CREATE]) == 1
    g2 = f._dispatch(Role.OPERATOR, "coordinator")
    with pytest.raises(NotAuthorized, match="one satisfied effect per obligation"):
        _create(s, f, g2, gh, 1, key=f"slice:{f.run_id}:1:{g2}")                 # a fresh key, same obligation
    # A create reconciled NOT delivered leaves the obligation unsatisfied: a fresh key is admitted.
    s2 = Store.open(tmp_path / "nd.db")
    f2 = Front(s2, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g2 = f2._dispatch(Role.OPERATOR, "coordinator")
    h = front.accepted_slices_hash(s2, f2.run_id, 0)
    f2._journal(g2, "slice:old", {"argv": ["gh", "issue", "create"], "obligation": {"kind": "slice_issue", "run": f2.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": h}},
                None, cls="issue_create", state="reconciled")
    assert json.loads(_create(s2, f2, g2, FakeGH(), 1))["number"] == 40


# -- the links, the labels, the umbrella, the completion pair -----------------------

def _file_two(s, f, g, gh):
    for n in (1, 2):
        ext = json.loads(_create(s, f, g, gh, n))
        key = [c for c in s.effects_for(f.run_id) if c["effect_class"] == "issue_create"][-1]["idempotency_key"]
        f._cmd(g, "record_slice_filed", {"slice": n, "effect_key": key})
    return filing.filed_numbers(s, f.run_id, 0), filing.filed_ids(s, f.run_id, 0)


def test_link_binding_and_precondition(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    dep = {"kind": "slice_dependency", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 2, "blocker": 1}
    with pytest.raises(NotAuthorized, match="slice_filed"):                     # nothing filed yet
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l0", {"argv": filing.link_argv("o/r", 41, 1040), "obligation": dep}, gh)
    numbers, ids = _file_two(s, f, g, gh)
    with pytest.raises(NotAuthorized, match="path"):                            # a sibling's number
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l1", {"argv": filing.link_argv("o/r", numbers[1], ids[1]), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="issue_id"):                        # not the blocker's id
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l2", {"argv": filing.link_argv("o/r", numbers[2], 999), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="edge"):                            # not in the plan
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l3", {"argv": filing.link_argv("o/r", numbers[1], ids[2]), "obligation": dict(dep, slice=1, blocker=2)}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l4", {"argv": filing.link_argv("o/r", numbers[2], ids[1]), "obligation": dep}, gh) == "ok"


def test_queue_label_waits_for_every_create_and_link(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    q1 = {"kind": "slice_queue", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 1}
    numbers, ids = _file_two(s, f, g, gh)
    with pytest.raises(NotAuthorized, match="slice_dependency"):                # the link is owed
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q0", {"argv": filing.queue_argv("o/r", numbers[1]), "obligation": q1}, gh)
    dep = {"kind": "slice_dependency", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 2, "blocker": 1}
    perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l", {"argv": filing.link_argv("o/r", numbers[2], ids[1]), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="child"):                           # the wrong child
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q1", {"argv": filing.queue_argv("o/r", numbers[2]), "obligation": q1}, gh)
    with pytest.raises(NotAuthorized, match="exactly"):                         # a second label
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q2", {"argv": filing.queue_argv("o/r", numbers[1]) + ["--add-label", "x"], "obligation": q1}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q3", {"argv": filing.queue_argv("o/r", numbers[1]), "obligation": q1}, gh) == "ok"


def test_umbrella_and_its_label_wait_for_every_filing_fact(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    plan = front.accepted_plan(s, f.run_id)
    um = {"kind": "umbrella", "run": f.run_id, "epoch": 0, "plan_hash": h}
    ul = {"kind": "umbrella_label", "run": f.run_id, "epoch": 0, "plan_hash": h}
    _create(s, f, g, gh, 1)                                                     # created, not yet recorded
    with pytest.raises(NotAuthorized, match="slice_filed"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u0", {"argv": filing.umbrella_label_argv("o/r", 12), "obligation": ul}, gh)
    numbers, _ = _file_two(s, f, g, gh)
    body = slices.umbrella_body(h[:8], plan, numbers)
    with pytest.raises(NotAuthorized, match="umbrella"):                        # a child, not the parent
        perform(s, f.run_id, g, EffectClass.COMMENT, "c0", {"argv": filing.comment_argv("o/r", numbers[1], body), "obligation": um}, gh)
    with pytest.raises(NotAuthorized, match="body"):
        perform(s, f.run_id, g, EffectClass.COMMENT, "c1", {"argv": filing.comment_argv("o/r", 12, body + "extra"), "obligation": um}, gh)
    assert perform(s, f.run_id, g, EffectClass.COMMENT, "c2", {"argv": filing.comment_argv("o/r", 12, body), "obligation": um}, gh).endswith("#issuecomment-1")
    with pytest.raises(NotAuthorized, match="remove"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u1", {"argv": ["gh", "issue", "edit", "12", "--repo", "o/r", "--add-label", "bircher:sliced"], "obligation": ul}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u2", {"argv": filing.umbrella_label_argv("o/r", 12), "obligation": ul}, gh) == "ok"


def test_completion_pair_needs_filing_complete_and_a_same_generation_observation(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    numbers = f.file_all(g)
    plan = front.accepted_plan(s, f.run_id)
    close = {"kind": "parent_close", "run": f.run_id, "epoch": 0}
    with pytest.raises(NotAuthorized, match="children_observed_closed"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p0", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh)
    for n in (1, 2):
        f._cmd(g, "record_slice_closed", {"slice": n, "closed_at": "t", "state": "merged"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    body = slices.completion_body(plan, numbers, {1: "merged", 2: "merged"})
    uc = {"kind": "umbrella_close", "run": f.run_id, "epoch": 0}
    with pytest.raises(NotAuthorized, match="body"):
        perform(s, f.run_id, g, EffectClass.COMMENT, "cc0", {"argv": filing.comment_argv("o/r", 12, "bircher: sliced complete\n\nnope\n"), "obligation": uc}, gh)
    assert perform(s, f.run_id, g, EffectClass.COMMENT, "cc1", {"argv": filing.comment_argv("o/r", 12, body), "obligation": uc}, gh)
    with pytest.raises(NotAuthorized, match="parent"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p1", {"argv": filing.close_argv("o/r", numbers[1]), "obligation": close}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p2", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh) == "closed"
    g2 = f._dispatch(Role.OPERATOR, "coordinator")                          # a later firing that observed again
    f._cmd(g2, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t2"})
    with pytest.raises(NotAuthorized, match="one satisfied effect per obligation"):
        perform(s, f.run_id, g2, EffectClass.ISSUE_OR_LABEL, "p3", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh)


# -- delivered forms -----------------------------------------------------------

def test_delivered_forms(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    rows = {}
    for kind, cls, argv in (("slice_issue", "issue_create", filing.create_argv("o/r", "T", ["bircher:slice"])),
                            ("slice_dependency", "issue_or_label", filing.link_argv("o/r", 41, 1040)),
                            ("slice_queue", "issue_or_label", filing.queue_argv("o/r", 41)),
                            ("umbrella", "comment", filing.comment_argv("o/r", 12, "b")),
                            ("umbrella_label", "issue_or_label", filing.umbrella_label_argv("o/r", 12)),
                            ("umbrella_close", "comment", filing.comment_argv("o/r", 12, "b")),
                            ("parent_close", "issue_or_label", filing.close_argv("o/r", 12))):
        key = f"u-{kind}"
        f._journal(g, key, {"argv": argv, "obligation": {"kind": kind, "run": f.run_id, "epoch": 0, "plan_hash": h}}, None,
                   cls=cls, state="uncertain")
        rows[kind] = key
    assert json.loads(_delivered_value(s, f.run_id, rows["slice_issue"], json.dumps({"url": "u", "number": 40, "id": 1040}))) == {"id": 1040, "number": 40, "url": "u"}
    with pytest.raises(ValueError, match="url, number, id"):
        _delivered_value(s, f.run_id, rows["slice_issue"], "40")
    for kind in ("slice_dependency", "slice_queue", "umbrella_label"):
        assert _delivered_value(s, f.run_id, rows[kind], None) == "ok"
        with pytest.raises(ValueError, match="no delivered value"):
            _delivered_value(s, f.run_id, rows[kind], "x")
    for kind in ("umbrella", "umbrella_close"):
        assert _delivered_value(s, f.run_id, rows[kind], "https://github.com/o/r/issues/12#issuecomment-3").endswith("-3")
        with pytest.raises(ValueError, match="comment URL"):
            _delivered_value(s, f.run_id, rows[kind], None)
    assert _delivered_value(s, f.run_id, rows["parent_close"], None) == "closed"
    n = reconcile_typed(s, f.run_id, [Resolution(rows["slice_queue"], True, None)], s.run_version(f.run_id))
    assert n == 1 and front.is_satisfied(s.effect_by_key(rows["slice_queue"], run_id=f.run_id))


def test_the_seventh_status_prefix():
    from kernel import bundle
    assert bundle.BIRCHER_STATUS_PREFIXES[-1] == "bircher: sliced "
    assert bundle.is_bircher_status("bircher: sliced abcd1234\n\n- #40 Slice 1: x")
    assert bundle.is_bircher_status("bircher: sliced complete\n")
    assert not bundle.is_bircher_status("bircher: slicing this by hand")
```

Add to `v2/tests/kernel/test_executor_body.py`:

```python
def test_issue_create_writes_the_body_file_after_the_check_and_reads_the_id_back(tmp_path, monkeypatch):
    """Planning ruling 1 and 6: the body is the artefact the intent names,
    passed as --body-file after the contract check; the confirmation reads
    the created issue's database id back and returns {url, number, id}."""
    import json
    from kernel import cli
    from kernel.artifacts import put_artifact
    from kernel.store import Store
    s = Store.open(tmp_path / "k.db")
    h = put_artifact(s, b"Slice 1 of #12 (bircher shaping abcd1234)\n\nbody\n")
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        class R: returncode, stderr = 0, ""
        r = R()
        if "create" in argv:
            path = argv[argv.index("--body-file") + 1]
            assert open(path, "rb").read() == s.read_blob(h)
            r.stdout = "https://github.com/o/r/issues/40\n"
        else:
            r.stdout = "1040\n"
        return r
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "resolve_command", lambda argv: argv)
    ex = cli.make_executor(s)
    out = ex("issue_create", {"argv": ["gh", "issue", "create", "--repo", "o/r", "--title", "T", "--label", "bircher:slice"],
                              "body": {"artifact": h}}, "k")
    assert json.loads(out) == {"id": 1040, "number": 40, "url": "https://github.com/o/r/issues/40"}
    assert seen[0][:3] == ["gh", "issue", "create"] and "--body-file" in seen[0]
    assert seen[1][:3] == ["gh", "api", "repos/o/r/issues/40"] and seen[1][3:] == ["--jq", ".id"]
    assert not __import__("os").path.exists(seen[0][seen[0].index("--body-file") + 1])
```

And in `v2/tests/kernel/test_bundle_v2.py`, rename `test_prefixes_are_exactly_six_and_not_the_generic_one` to `..._seven_...` and add `"bircher: sliced ",` as the seventh element; in `v2/tests/fixtures/bircher_status_comments.tsv` add three rows: `drop\tbircher: sliced abcd1234`, `drop\tbircher: sliced complete`, `keep\tbircher: slicing this by hand feels wrong`.

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_filing_effects.py -q`
Expected: FAIL — `ImportError: cannot import name 'filing'`.

- [ ] **Step 3: The class, the contract**

`v2/kernel/effect_class.py`:

```python
    # Creating an issue mints work another run will consume (shaping spec
    # §2 *The effect class*): a blast radius of its own, kept distinct from
    # editing or labelling one (ruling 6).
    ISSUE_CREATE = "issue_create"
    ALL = frozenset({
        REF_UPDATE, PULL_REQUEST, MERGE, STATUS_CHECK, COMMENT,
        ISSUE_OR_LABEL, REVERT_OR_RECOVERY, CREDENTIAL_LIFECYCLE, SESSION_CONTROL,
        ISSUE_CREATE,
    })
```

`v2/kernel/contract.py`: add a field to `Rule` after `forbid_value_prefix`:

```python
    #: (flag, values) pairs: the flag may not carry any of those values. The
    #: filing step creates children with `bircher:slice` and the inherited
    #: policy labels; `bircher:queued`, `bircher:running` and `bircher:sliced`
    #: are the runner's and the filing step's to set LATER (shaping spec §2),
    #: and a created issue carrying `queued` would be runnable before its
    #: links exist.
    forbid_value: tuple[tuple[str, frozenset], ...] = ()
```

in `check`, after the `bad_prefix` block and before `return rule`:

```python
        bad_value = [f for f, vals in rule.forbid_value
                     if set(p.values.get(f, [])) & set(vals)]
        if bad_value:
            reasons.append(f"{rule.sig}: {bad_value} carry a value the rule refuses")
            continue
```

and in `CONTRACTS`, the `ISSUE_OR_LABEL` list gains a fourth rule and a new entry is added:

```python
    EffectClass.ISSUE_OR_LABEL: [
        Rule("gh issue edit",
             flags=_GH_COMMON | {"--add-label", "--remove-label"},
             valued=frozenset({"--repo", "--add-label", "--remove-label"})),
        Rule("gh issue close", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
        Rule("gh issue reopen", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
        # The write half of the endpoint the queue generator reads
        # (`is_unblocked`, issues-to-queue.sh): a blocked-by link between two
        # issues (shaping spec §2).
        Rule("gh api", url_path=r"/issues/\d+/dependencies/blocked_by$", flags=frozenset({"-X", "-f"}),
             valued=frozenset({"-X", "-f"}), methods=frozenset({"POST"})),
    ],
    # The child issue (shaping spec §2 *The effect class*). No --body-file:
    # the executor appends it after this check, from the artefact the intent's
    # body names (planning ruling 1). The three runner labels are refused as
    # values, so no create can queue a child before its links exist.
    EffectClass.ISSUE_CREATE: [
        Rule("gh issue create", flags=_GH_COMMON | {"--title", "--label"},
             valued=frozenset({"--repo", "--title", "--label"}),
             required=frozenset({"--repo", "--title"}),
             forbid_value=(("--label", frozenset({"bircher:queued", "bircher:running", "bircher:sliced"})),)),
    ],
```

- [ ] **Step 4: `kernel/filing.py`**

```python
# v2/kernel/filing.py
"""What `perform` refuses of a filing effect, and the argvs the filing step
composes (shaping spec §2 *What perform refuses*, *Bound to the effect, not
only to the metadata*, *One satisfied effect per obligation*).

A command's from-set guards the FACT; the create is an effect performed
between commands, and `perform` checked the run's state only for a merge.
So every obligation kind this design adds carries a precondition the kernel
checks when the effect is journalled (ruling 19); the argv is bound to the
targets the kernel holds (ruling 21); every argv shape this design adds is
admitted only with its kind (round 11, finding 1); and one satisfied effect
exists per obligation (ruling 23). All of it reads what the kernel already
holds -- the plan, the obligation rows, the facts these rows name.

The composers are here too, so the coordinator and the binding read one
argv shape and not two.
"""
from __future__ import annotations

import re

from kernel import front, slices
from kernel.authz import NotAuthorized
from kernel.contract import parse
from kernel.dispatch import Role, role_for
from kernel.effect_class import EffectClass
from kernel.events import EventKind

KINDS = front.FILING_KINDS | front.COMPLETION_KINDS
_WITH_HASH = frozenset({"slice_issue", "slice_dependency", "slice_queue", "umbrella", "umbrella_label"})
_WITH_SLICE = frozenset({"slice_issue", "slice_dependency", "slice_queue"})
_BLOCKED_BY = re.compile(r"^repos/([^/]+/[^/]+)/issues/(\d+)/dependencies/blocked_by$")
_VALUED = frozenset({"--repo", "--title", "--label", "--body", "--add-label", "--remove-label",
                     "--comment", "-X", "-f", "--jq"})


# -- composers -------------------------------------------------------------------

def create_argv(repo: str, title: str, labels: list[str]) -> list[str]:
    argv = ["gh", "issue", "create", "--repo", repo, "--title", title]
    for lab in labels:
        argv += ["--label", lab]
    return argv


def link_argv(repo: str, child: int, blocker_id: int) -> list[str]:
    return ["gh", "api", f"repos/{repo}/issues/{child}/dependencies/blocked_by", "-X", "POST",
            "-f", f"issue_id={blocker_id}"]


def queue_argv(repo: str, child: int) -> list[str]:
    return ["gh", "issue", "edit", str(child), "--repo", repo, "--add-label", "bircher:queued"]


def comment_argv(repo: str, issue: int, body: str) -> list[str]:
    return ["gh", "issue", "comment", str(issue), "--repo", repo, "--body", body]


def umbrella_label_argv(repo: str, parent: int) -> list[str]:
    return ["gh", "issue", "edit", str(parent), "--repo", repo, "--add-label", "bircher:sliced",
            "--remove-label", "bircher:running"]


def close_argv(repo: str, parent: int) -> list[str]:
    return ["gh", "issue", "close", str(parent), "--repo", repo]


# -- what the kernel holds ---------------------------------------------------------

def expected_labels(store, run_id: str) -> list[str]:
    """`bircher:slice` plus the parent's inherited policy labels (spec §1)."""
    return sorted(["bircher:slice", *slices.inherited_labels(front.frozen_labels(store, run_id))])


def filed_numbers(store, run_id: str, epoch_n: int) -> dict[int, int]:
    return {n: f.payload["issue"] for n, f in front.slices_filed(store, run_id, epoch_n).items()}


def filed_ids(store, run_id: str, epoch_n: int) -> dict[int, int]:
    return {n: f.payload["issue_id"] for n, f in front.slices_filed(store, run_id, epoch_n).items()}


def _parsed(argv: list[str]):
    return parse(list(argv), _VALUED)


def _int(tok: str) -> int | None:
    return int(tok) if isinstance(tok, str) and tok.isdigit() else None


# -- the mandate -----------------------------------------------------------------

def required_kinds(store, run_id: str, effect_class: str, argv: list[str]) -> frozenset[str]:
    """The obligation kinds an argv SHAPE is admitted with; empty when the
    shape is none this design added. Every shape here is admitted only with
    its kind, whether or not the effect announces one (round 11, finding 1)."""
    if effect_class == EffectClass.ISSUE_CREATE:
        return frozenset({"slice_issue"})
    p = _parsed(argv)
    ops = list(p.operands)
    if ops[:2] == ["gh", "api"] and len(ops) > 2 and _BLOCKED_BY.match(ops[2]):
        return frozenset({"slice_dependency"})
    added = set(p.values.get("--add-label", []))
    if "bircher:queued" in added:
        return frozenset({"slice_queue"})
    if "bircher:sliced" in added:
        return frozenset({"umbrella_label"})
    if ops[:3] == ["gh", "issue", "comment"] and any(
            v.startswith("bircher: sliced ") for v in p.values.get("--body", [])):
        return frozenset({"umbrella", "umbrella_close"})
    if ops[:3] == ["gh", "issue", "close"] and len(ops) > 3 and store.run_state(run_id) == "sliced" \
            and _int(ops[3]) == front.issue_number(store, run_id):
        return frozenset({"parent_close"})
    return frozenset()


# -- the check -------------------------------------------------------------------

def check_filing_effect(store, run_id: str, generation: int, effect_class: str, key: str, intent: dict) -> None:
    argv = list(intent.get("argv") or [])
    ob = intent.get("obligation") or {}
    kind = ob.get("kind")
    demanded = required_kinds(store, run_id, effect_class, argv)
    if demanded and kind not in demanded:
        raise NotAuthorized(
            f"{effect_class} {argv[:4]}: this argv shape is admitted only with an obligation of kind "
            f"{sorted(demanded)}, not {kind!r} (shaping spec §2 *Bound to the effect*)"
        )
    if kind not in KINDS:
        return
    if store.effect_by_key(key, run_id=run_id) is not None:
        # A replay of a confirmed key, or an uncertain retry: `_perform_unhalted`'s
        # key rules decide, and uniqueness must not refuse the replay.
        return
    _common(store, run_id, generation, ob)
    plan = front.accepted_plan(store, run_id)
    epoch_n = front.epoch(store, run_id)
    _per_kind(store, run_id, generation, ob, plan, epoch_n)
    _binding(store, run_id, effect_class, argv, intent, ob, plan, epoch_n)
    if front.satisfied_obligation(store, run_id, ob) is not None:
        raise NotAuthorized(f"one satisfied effect per obligation: {ob} is already satisfied (ruling 23)")


def _common(store, run_id: str, generation: int, ob: dict) -> None:
    kind = ob["kind"]
    if store.run_state(run_id) != "sliced":
        raise NotAuthorized(f"{kind}: the run is {store.run_state(run_id)!r}, not sliced")
    if role_for(store, run_id, generation) != Role.OPERATOR:
        raise NotAuthorized(f"{kind}: a filing effect comes from the coordinator's operator dispatch, not an author or reviewer")
    epoch_n = front.epoch(store, run_id)
    if ob.get("run") != run_id or ob.get("epoch") != epoch_n:
        raise NotAuthorized(f"{kind}: obligation names run {ob.get('run')!r} epoch {ob.get('epoch')!r}; this is {run_id!r} epoch {epoch_n}")
    accepted = front.accepted_slices_hash(store, run_id, epoch_n)
    if kind in _WITH_HASH and ob.get("plan_hash") != accepted:
        raise NotAuthorized(f"{kind}: obligation names plan_hash {str(ob.get('plan_hash'))[:12]}, not the accepted hash {str(accepted)[:12]}")
    plan = front.accepted_plan(store, run_id, epoch_n)
    if kind in _WITH_SLICE and ob.get("slice") not in plan.by_number():
        raise NotAuthorized(f"{kind}: slice {ob.get('slice')!r} is not in the accepted plan")
    if kind == "slice_dependency" and ob.get("blocker") not in plan.by_number():
        raise NotAuthorized(f"slice_dependency: blocker {ob.get('blocker')!r} is not in the accepted plan")


def _per_kind(store, run_id: str, generation: int, ob: dict, plan, epoch_n: int) -> None:
    kind = ob["kind"]
    filed = front.slices_filed(store, run_id, epoch_n)
    if kind == "slice_issue":
        missing = [b for b in plan.by_number()[ob["slice"]].depends_on if b not in filed]
        if missing:
            raise NotAuthorized(f"slice_issue: blocker(s) {missing} of slice {ob['slice']} have no filed issue")
    elif kind == "slice_dependency":
        if (ob["slice"], ob["blocker"]) not in plan.edges():
            raise NotAuthorized(f"slice_dependency: the edge {ob['slice']} <- {ob['blocker']} is not in the plan")
        for n in (ob["slice"], ob["blocker"]):
            if n not in filed:
                raise NotAuthorized(f"slice_dependency: slice {n} has no slice_filed in epoch {epoch_n}")
    elif kind == "slice_queue":
        owed = [o for o in front.unsatisfied_filing(store, run_id, epoch_n)
                if o["kind"] in ("slice_issue", "slice_dependency")]
        if owed:
            raise NotAuthorized(f"slice_queue: {len(owed)} create/link obligation(s) unsatisfied, first {owed[0]['kind']} "
                                f"of slice {owed[0]['slice']}: a queued child has every link")
    elif kind in ("umbrella", "umbrella_label"):
        for s in plan.slices:
            if s.number not in filed:
                raise NotAuthorized(f"{kind}: slice {s.number} has no slice_filed")
    else:  # umbrella_close, parent_close
        if front.filing_complete(store, run_id, epoch_n) is None:
            raise NotAuthorized(f"{kind}: no filing_complete in epoch {epoch_n}")
        if front.observed_closed_under(store, run_id, epoch_n, generation) is None:
            raise NotAuthorized(f"{kind}: no children_observed_closed under generation {generation}")


def _binding(store, run_id: str, effect_class: str, argv: list[str], intent: dict, ob: dict, plan, epoch_n: int) -> None:
    kind = ob["kind"]
    repo, parent = store.run_base_repo(run_id), front.issue_number(store, run_id)
    p = _parsed(argv)
    ops = list(p.operands)
    numbers, ids = filed_numbers(store, run_id, epoch_n), filed_ids(store, run_id, epoch_n)
    hash8 = front.accepted_slices_hash(store, run_id, epoch_n)[:8]
    if kind == "slice_issue":
        if effect_class != EffectClass.ISSUE_CREATE:
            raise NotAuthorized("slice_issue is admitted on issue_create only")
        s = plan.by_number()[ob["slice"]]
        title, body = slices.render_child(s, parent, hash8, numbers)
        if p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"slice_issue: --repo {p.values.get('--repo')} is not the run's repo {repo!r}")
        if p.values.get("--title") != [title]:
            raise NotAuthorized(f"slice_issue: --title {p.values.get('--title')} is not the slice's title {title!r}")
        blob = store.read_blob((intent.get("body") or {}).get("artifact") or "")
        if blob != body.encode("utf-8"):
            raise NotAuthorized("slice_issue: the body artefact is not render_child over the accepted plan")
        if sorted(p.values.get("--label", [])) != expected_labels(store, run_id):
            raise NotAuthorized(f"slice_issue: labels {sorted(p.values.get('--label', []))} are not {expected_labels(store, run_id)}")
        return
    if kind == "slice_dependency":
        m = _BLOCKED_BY.match(ops[2]) if ops[:2] == ["gh", "api"] and len(ops) > 2 else None
        if m is None or m.group(1) != repo or int(m.group(2)) != numbers[ob["slice"]]:
            raise NotAuthorized(f"slice_dependency: the path does not name issue {numbers[ob['slice']]} of {repo}")
        if p.values.get("-f") != [f"issue_id={ids[ob['blocker']]}"]:
            raise NotAuthorized(f"slice_dependency: -f {p.values.get('-f')} is not issue_id={ids[ob['blocker']]}, the blocker's database id")
        return
    if kind == "slice_queue":
        child = numbers[ob["slice"]]
        if ops[:4] != ["gh", "issue", "edit", str(child)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"slice_queue: the argv does not edit child {child} of {repo}")
        if p.values.get("--add-label") != ["bircher:queued"] or p.values.get("--remove-label"):
            raise NotAuthorized("slice_queue: adds exactly bircher:queued and removes nothing")
        return
    if kind in ("umbrella", "umbrella_close"):
        if ops[:4] != ["gh", "issue", "comment", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"{kind}: the comment is not on the parent #{parent} of {repo}")
        if kind == "umbrella":
            want = slices.umbrella_body(hash8, plan, numbers)
        else:
            states = {s.number: front.current_closure(store, run_id, epoch_n, s.number).payload["state"] for s in plan.slices}
            want = slices.completion_body(plan, numbers, states)
        if p.values.get("--body") != [want]:
            raise NotAuthorized(f"{kind}: the body is not the rendered {kind} text")
        return
    if kind == "umbrella_label":
        if ops[:4] != ["gh", "issue", "edit", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"umbrella_label: the argv does not edit the parent #{parent}")
        if p.values.get("--add-label") != ["bircher:sliced"] or p.values.get("--remove-label") != ["bircher:running"]:
            raise NotAuthorized("umbrella_label: adds bircher:sliced and removes bircher:running, nothing else")
        return
    if kind == "parent_close":
        if ops[:4] != ["gh", "issue", "close", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"parent_close: the argv does not close the parent #{parent} of {repo}")
```

- [ ] **Step 5: The hook in `perform`, the delivered forms, the executor, the prefix**

In `v2/kernel/effects.py::perform`, after the `EffectClass.MERGE` block and before `if is_halted(store, run_id):`:

```python
    # The filing effects (shaping spec §2): the mandate, the preconditions,
    # the binding and the uniqueness, checked where the mutation would
    # happen and not after it. For every class: the mandate is what makes an
    # obligation-less queue label or close refusable at all.
    from kernel.filing import check_filing_effect
    try:
        check_filing_effect(store, run_id, generation, effect_class, idempotency_key, intent)
    except NotAuthorized as exc:
        shadow_or_raise(store, run_id, exc, idempotency_key, effect_class=effect_class, argv=argv[:6])
```

In `_delivered_value`, before the final `raise ValueError(...)`:

```python
    if kind == "slice_issue":
        try:
            d = json.loads(value or "")
        except ValueError as exc:
            raise ValueError(f"{key}: a delivered slice_issue needs {{url, number, id}} JSON") from exc
        if not (isinstance(d, dict) and d.get("url") and type(d.get("number")) is int and type(d.get("id")) is int):
            raise ValueError(f"{key}: a delivered slice_issue needs {{url, number, id}} JSON")
        return json.dumps({"url": d["url"], "number": d["number"], "id": d["id"]}, sort_keys=True)
    if kind in ("slice_dependency", "slice_queue", "umbrella_label"):
        if value is not None:
            raise ValueError(f"{key}: a delivered {kind} takes no delivered value")
        return "ok"
    if kind in ("umbrella", "umbrella_close"):
        if not value:
            raise ValueError(f"{key}: a delivered {kind} needs its comment URL")
        return value
    if kind == "parent_close":
        if value not in (None, "closed"):
            raise ValueError(f"{key}: a delivered parent_close is the observed state 'closed'")
        return "closed"
```

In `v2/kernel/cli.py`, add after `_write_event_file`:

```python
def _write_raw_file(store, body: dict) -> str:
    """The artefact the intent names, raw: an issue body (planning ruling 1)."""
    data = store.read_blob(body["artifact"])
    if data is None:
        raise RuntimeError(f"artifact {body['artifact']} is not held")
    fh = tempfile.NamedTemporaryFile(
        dir=os.path.dirname(os.path.abspath(store.path)), prefix="body-",
        suffix=".md", delete=False)
    with fh:
        os.chmod(fh.name, 0o600)
        fh.write(data)
    return fh.name


_ISSUE_URL = re.compile(r"/issues/(\d+)/?$")


def _read_back_issue(argv: list[str], url: str) -> str:
    """The created issue's number from its URL and its database id from a
    read-back (planning ruling 6), as {url, number, id} JSON."""
    m = _ISSUE_URL.search(url.strip())
    if not m:
        raise RuntimeError(f"issue_create returned no issue URL: {url[:200]!r}")
    repo = argv[argv.index("--repo") + 1]
    r = subprocess.run(resolve_command(["gh", "api", f"repos/{repo}/issues/{m.group(1)}", "--jq", ".id"]),
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        raise RuntimeError(f"issue_create read-back failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    return json.dumps({"url": url.strip(), "number": int(m.group(1)), "id": int(r.stdout.strip())}, sort_keys=True)
```

(add `import re` at the top of `cli.py` if absent) and change the executor's body handling:

```python
        try:
            if body is not None:
                if store is None:
                    raise RuntimeError("an intent with a body needs the store it is composed from")
                if effect_class == EffectClass.ISSUE_CREATE:
                    path = _write_raw_file(store, body)
                    extra = ["--body-file", path]
                else:
                    path = _write_event_file(store, body)
                    extra = ["--data-binary", f"@{path}"]
            r = subprocess.run(resolve_command(argv + extra), capture_output=True, text=True)
        finally:
            ...
        if r.returncode != 0:
            raise RuntimeError(
                f"{effect_class} failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        out = r.stdout.strip()
        if rule.name == "sess-create":
            return validate_create_response(out, create_body(argv))
        if effect_class == EffectClass.ISSUE_CREATE:
            return _read_back_issue(argv, out)
        return out or "ok"
```

(`from kernel.effect_class import EffectClass` at the top of `cli.py`.) `_check_body` in `effects.py` already admits `{"artifact": h}`; the create's `body` needs no new shape.

`v2/kernel/bundle.py`: the tuple gains `"bircher: sliced ",` last, with the comment `# The umbrella and the completion comment (shaping spec §3): a notice about the run's own children, never the issue's author.`; the docstring's "Five EXACT prefixes" becomes "Seven EXACT prefixes". `batch/run-queue.sh:3095`: `head.startswith("bircher: parked "))` becomes `head.startswith("bircher: parked ") or\n            head.startswith("bircher: sliced "))`.

- [ ] **Step 6: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_filing_effects.py tests/kernel/test_executor_body.py tests/kernel/test_bundle_v2.py tests/execution/test_bircher_status_bash.py tests/kernel/test_effect_contract.py -q`, then the suite.
Expected: PASS; **1504 passed, 3 skipped** (14 new: 13 in the new file, 1 in the executor test). `test_effect_contract.py` may pin `EffectClass.ALL`'s size or the contract keys; extend the pin by `issue_create`.

- [ ] **Step 7: Mutations**

Delete the `if demanded and kind not in demanded` raise: `test_every_added_argv_shape_needs_its_kind` reds. Return early from `_binding` for `slice_issue`: `test_create_is_bound_to_state_role_epoch_hash_slice_and_body` reds. Delete the uniqueness raise: `test_one_satisfied_effect_per_obligation` reds. Make `render_child` total (`deps = "none"` when a blocker is missing): `test_create_is_refused_while_a_blocker_is_unfiled_and_a_stale_epoch` reds via the precondition, and `test_render_child_is_exact_and_strict` reds. Revert.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel v2/tests batch/run-queue.sh
git commit -m "feat(kernel): issue_create, the filing effects bound at perform, seven delivered forms, the seventh status prefix"
```

## Part B — Coordinator

### Task 6: The shaping round

**Files:**
- Create: `v2/coordinator/shape.py`
- Create: `skills/shape-author/SKILL.md`
- Modify: `v2/coordinator/seat.py:19-20` (`SHAPE_OUT`)
- Modify: `v2/coordinator/author.py:91-157` (`author_brief` phase `slices`)
- Modify: `v2/coordinator/phases.py:11-24` (`_LOOP_STATES`), `:61-71` (`_is_round_cause`), `:107-123` (`derived_session_obligation`), `:339-436` (`run_loop`: the `shaping` branch)
- Test: `v2/tests/coordinator/test_shape_round.py`

**Interfaces:**
- Consumes: `slices.parse_ruling`, `slices.COARSE`, `slices.RULING_LINE` (Task 1); `record_one_piece`, `submit_slices` (Task 3); `seat.run_turn`, `author.choose_author_vendor`, `author._copy`, `author._findings_for`, `phases.round_cause`, `human.take_listing`.
- Produces:
  - `seat.SHAPE_OUT = "bircher/shape.md"`.
  - `shape.shape_round(ctx) -> str` in `{"one_piece", "submitted", "empty_retry", "direction", "budget", "stall", "reauthor", "failed"}`.
  - `author.author_brief(ctx, phase="slices")`: the `shape-author` skill, the definition of coarse (`slices.COARSE`), both grammars, the two ways to end the turn; on a revision the previous plan, the findings and the dispositions requirement.
  - `phases._LOOP_STATES = FRONT_HALF_STATES | SHAPING_STATES | {"planned", "sliced"}`; `run_loop` at `shaping` runs the shaping round and continues.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/coordinator/test_shape_round.py
"""Task 6: the shaping round (shaping spec §3 *The shaping round*)."""
import os
import time

import pytest

from coordinator import phases, seat, shape
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.slices import COARSE
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}
RULING = b"Ruling: one piece\nReasoning: one coherent change.\nCost if wrong: a spec round.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None, issue=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, project_config=cfg, issue=issue or ISSUE, shape=False)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "i12-epic-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="i12-epic-1", server="http://srv", repo="o/r", issue_number=12,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _writer(fake, files: dict):
    """What the shaping session writes when prompted: {path: bytes}."""
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        for name, data in files.items():
            open(os.path.join(ws, name), "wb").write(data)
    return on_prompt


def _one_pass(ctx):
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(ctx.store, ctx.run_id, actor="coordinator", role=Role.OPERATOR).generation


def test_the_brief_carries_the_skill_the_definition_and_both_grammars(world):
    s, f, fake, ctx = world()
    _one_pass(ctx)
    from coordinator.author import author_brief
    b = author_brief(ctx, phase="slices").decode()
    assert b.startswith("# Bircher slices author brief")
    assert "name: shape-author" in b
    assert COARSE in b and b.count(COARSE) == 1
    assert "Ruling: one piece" in b and "## Slice 1: <title>" in b and "Depends on:" in b
    assert seat.SHAPE_OUT in b and seat.ARTIFACT_OUT in b
    assert "names no files" in b


def test_one_piece_ending(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.SHAPE_OUT: RULING})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "one_piece"
    assert s.run_state("i12-epic-1") == "queued"
    r = front.shape_ruling(s, "i12-epic-1", 0)
    assert r.payload["reasoning"] == "one coherent change." and r.payload["cost_if_wrong"] == "a spec round."
    # The turn ended on the file, and the session was stopped (the turn's end is a fact).
    assert s.newest_fact("i12-epic-1", EventKind.TURN_ENDED).payload["ended"] == "file"


def test_slice_plan_ending(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.ARTIFACT_OUT: SLICES_BYTES})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "submitted"
    assert s.run_state("i12-epic-1") == "slices_submitted"
    assert s.read_blob(s.phase_artifact("i12-epic-1", "slices")) == SLICES_BYTES
    assert os.path.exists(os.path.join(ctx.bundle_dir, "i12-epic-1", "slices-r1.md"))


def test_both_files_the_ruling_is_read(world):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, {seat.SHAPE_OUT: RULING, seat.ARTIFACT_OUT: SLICES_BYTES})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "one_piece"
    assert s.run_state("i12-epic-1") == "queued"
    assert s.phase_artifact("i12-epic-1", "slices") is None


@pytest.mark.parametrize("files", [
    {},
    {seat.SHAPE_OUT: b"Ruling: two pieces\nReasoning: r\nCost if wrong: c\n"},
    {seat.SHAPE_OUT: b"Ruling: one piece\nReasoning:\nCost if wrong: c\n"},
    {seat.SHAPE_OUT: b"preface\nRuling: one piece\nReasoning: r\nCost if wrong: c\n"},
])
def test_a_ruling_that_does_not_parse_is_the_empty_turn(world, files):
    s, f, fake, ctx = world()
    fake.on_prompt = _writer(fake, files)
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "empty_retry"
    assert s.newest_fact("i12-epic-1", EventKind.AUTHOR_EMPTY) is not None
    assert s.run_state("i12-epic-1") == "shaping"
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "failed"          # the retry's own empty turn is RC_FAILED


def test_a_direction_during_the_turn_displaces_the_output(world):
    s, f, fake, ctx = world()
    def on_prompt(sid, text):
        fake.add_user_message(sid, "one piece, do not slice it")
        _writer(fake, {seat.ARTIFACT_OUT: SLICES_BYTES})(sid, text)
    fake.on_prompt = on_prompt
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "direction"
    assert s.run_state("i12-epic-1") == "shaping"
    assert s.newest_fact("i12-epic-1", EventKind.HUMAN_DIRECTION).payload["text"] == "one piece, do not slice it"
    assert s.phase_artifact("i12-epic-1", "slices") is None


def test_a_grammar_refusal_is_the_next_rounds_findings(world):
    s, f, fake, ctx = world()
    six = SLICES_BYTES + b"".join(b"\n## Slice %d: more\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n" % n for n in (3, 4, 5, 6))
    fake.on_prompt = _writer(fake, {seat.ARTIFACT_OUT: six})
    _one_pass(ctx)
    assert shape.shape_round(ctx) == "reauthor"
    rej = s.newest_fact("i12-epic-1", EventKind.COMMAND_REJECTED)
    assert rej.payload["command_name"] == "submit_slices" and "6 slices" in rej.payload["detail"]
    from coordinator.author import author_brief
    _one_pass(ctx)
    assert "6 slices" in author_brief(ctx, phase="slices").decode()


def test_the_loop_from_birth_to_planned_untouched(world):
    """The whole front half from `shaping`: one piece, spec, plan, no human."""
    s, f, fake, ctx = world()
    PLAN, SPEC = b"# Plan\n\n### Task 1: do it\n", b"# Spec\n\nDo it.\n"
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher slices author brief"):
            open(os.path.join(ws, seat.SHAPE_OUT), "wb").write(RULING)
        elif text.startswith("# Bircher spec author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC)
        elif text.startswith("# Bircher plan author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(PLAN)
        elif text.startswith("# Bircher review brief"):
            phase = "spec" if "of a spec" in text else "plan"
            h = s.phase_artifact("i12-epic-1", phase)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"fine\n\nVERDICT: PASS {h[:8]}\n")
    fake.on_prompt = on_prompt
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "planned"
    kinds = [x.kind for x in s.facts_for("i12-epic-1")]
    assert "parked" not in kinds and "human_ruling" not in kinds
    assert front.shape_ruling(s, "i12-epic-1", 0) is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_shape_round.py -q`
Expected: FAIL — `ImportError: cannot import name 'shape'`.

- [ ] **Step 3: The skill**

```markdown
# skills/shape-author/SKILL.md
---
name: shape-author
description: Decide whether one GitHub issue is one piece of work or an epic, and if an epic, write its slice plan. Loaded by a Bircher v2 author session in the shaping phase, before any spec.
---

# shape-author

You are deciding the SHAPE of one GitHub issue before anyone specs it. You
have the frozen issue snapshot, the run's policy, the definition of a slice
in the brief, and — on a revision — your previous slice plan and the
reviewer's findings. Nothing else is in scope.

## The decision

Read the issue and decide: is this one coherent piece of work a single spec,
plan and pull request can carry, or several? Use the definition of coarse in
the brief, and nothing finer. A one-piece issue costs you one ruling; an
epic costs you a slice plan the other vendor reviews.

## One piece

Write exactly this to `bircher/shape.md` and end your turn:

    Ruling: one piece
    Reasoning: <why one spec can carry it, one or more lines>
    Cost if wrong: <what the spec phase discovers if it was an epic after all>

The first non-blank line must be exactly `Ruling: one piece`; both blocks
must be non-empty. Do not also write `bircher/artifact.md`: a ruling beside
a plan is read as the ruling and the plan is discarded.

## An epic

Write the slice plan to `bircher/artifact.md` — write it elsewhere first,
then rename it into place, so a partial file is never read — and end your
turn without `bircher/shape.md`. The grammar is fixed and the kernel refuses
a plan that does not fit it:

    # <title of the slice plan>

    <preamble: why this issue is several pieces — free text>

    ## Slice 1: <title>
    Scope: <three to six sentences>
    Non-goals: <one line>
    Depends on: none

    ## Slice 2: <title>
    Scope: ...
    Non-goals: ...
    Depends on: 1

Two to five slices, numbered 1..N in order. Each has exactly one `Scope:`
(three to six sentences by full stops), one `Non-goals:` and one
`Depends on:` (`none` or a comma-separated list of slice numbers, no cycles,
no self-dependency). A slice plan names no files, tasks or acceptance
tests: the design of each slice is its own spec phase's work. Every slice
is filed as its own issue and worked through the whole pipeline; a plan
that sliced the issue into its plan-steps has recreated the problem.

## On a revision

You are a reader of findings, not a defender of the plan. End the revised
plan with a section headed `## Dispositions`, one line per finding of the
round before, by its number:

    N. accepted — <what changed, in one sentence>
    N. rejected — <why the finding is wrong or out of scope>

You may also rule the issue one piece on a revision: write `bircher/shape.md`
instead of a plan. The reviewer reads the dispositions first and will not
re-raise a finding you resolved or refused with reasons.

Do not commit, push, open anything, or write outside your worktree.
```

- [ ] **Step 4: `seat.py`, `author.py`**

`v2/coordinator/seat.py`, after `QUESTIONS_OUT`:

```python
#: The shaping round's ruling file (shaping spec §3): read before the artefact.
SHAPE_OUT = "bircher/shape.md"
```

`v2/coordinator/author.py`: add after `_Q`:

```python
#: Where each phase's author skill lives (planning ruling 11).
_SKILL_DIR = {"slices": "shape-author"}
```

and change `author_brief`'s body after the `resume_answer` return:

```python
    parts = [f"# Bircher {phase} author brief\n"]
    parts.append((SKILLS / _SKILL_DIR.get(phase, f"{phase}-author") / "SKILL.md").read_text())
    if phase == "slices":
        from kernel.slices import COARSE
        parts.append("\n## Files\n\nRuling: `%s`\nSlice plan: `%s`\n\nEnd the turn with exactly one of them.\n"
                     % (seat.SHAPE_OUT, seat.ARTIFACT_OUT))
        # The definition of coarse, rendered from the one place it lives
        # (shaping spec §1): never a second copy in a skill file.
        parts.append("\n## The definition of a slice\n\n> %s\n\nA slice plan names no files, tasks or acceptance tests.\n" % COARSE)
    else:
        parts.append("\n## Files\n\nArtefact: `%s`\nQuestions: `%s`\n" % (seat.ARTIFACT_OUT, seat.QUESTIONS_OUT))
    pol = policy_of(store, run_id)
    parts.append("\n## Policy\n\n```json\n%s\n```\n" % json.dumps(to_payload(pol), indent=1))
```

(the rest of the function is unchanged: the grill paragraph is `phase == "spec"` only; the snapshot; the accepted spec for `plan`; the previous draft, the findings and the dispositions requirement apply to `slices` as to every phase since they key on `front.newest_submission(store, run_id, phase, epoch)`).

- [ ] **Step 5: `shape.py`**

```python
# v2/coordinator/shape.py
"""The shaping round (shaping spec §3 *The shaping round*): the author
round's twin, with two watched paths and two ways to end."""
from __future__ import annotations

from coordinator import author, phases, seat
from coordinator.human import take_listing
from coordinator.session import AgentMismatch
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role, SeatsExhausted
from kernel.events import EventKind


def shape_round(ctx) -> str:
    """One shaping turn: `one_piece` (the ruling recorded, the run at
    `queued`), `submitted` (a slice plan at `slices_submitted`),
    `empty_retry`, `direction`, `budget`, `stall`, `reauthor` or `failed` --
    the author round's vocabulary, so the loop parks the same way."""
    store, run_id = ctx.store, ctx.run_id
    n = ctx.epoch()
    cause = phases.round_cause(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": "slices", "epoch": n, "cause": cause.id}
    vendor = author.choose_author_vendor(ctx)
    try:
        turn = seat.run_turn(ctx, role=Role.AUTHOR, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=cause.id, prompt_text=author.author_brief(ctx, phase="slices"),
                             watched=[seat.SHAPE_OUT, seat.ARTIFACT_OUT])
    except SeatsExhausted:
        return "budget"
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return "failed"
    if turn.ended == "displaced":
        return "direction"
    # The human first: a message in the session is taken before anything is recorded.
    if take_listing(ctx, turn.session_id, turn.listing) == "direction":
        return "direction"
    shape_file = turn.files.get(seat.SHAPE_OUT)
    artefact = turn.files.get(seat.ARTIFACT_OUT)
    ruling = slices.parse_ruling(shape_file) if shape_file else None
    if shape_file and ruling is None:
        # A ruling with no reasoning is not a ruling (spec §3): the empty turn.
        ctx.log(f"{seat.SHAPE_OUT} does not parse as a ruling; the empty turn: {shape_file[:200]!r}")
    if ruling is not None:
        if artefact is not None:
            # The ruling is read first; the artefact beside it is ignored, and
            # `_move_aside` retires it before any re-prompt (spec §3).
            ctx.log(f"{seat.ARTIFACT_OUT} beside a ruling is ignored")
        try:
            seat.command(ctx, "record_one_piece", {"reasoning": ruling.reasoning, "cost_if_wrong": ruling.cost_if_wrong})
        except NotAuthorized as exc:
            if "human_direction" in str(exc):
                return "direction"
            ctx.log(f"unexpected refusal: {exc}")
            return "failed"
        return "one_piece"
    if artefact is None:
        try:
            seat.command(ctx, "record_author_empty", {"session": turn.session_id})
        except NotAuthorized as exc:
            ctx.log(f"author_empty refused: {exc}")
            return "failed"
        return "empty_retry"
    h = put_artifact(store, artefact)
    rnd = len(front.submissions(store, run_id, "slices", n)) + 1
    author._copy(ctx, "slices", rnd, artefact)
    try:
        seat.command(ctx, "submit_slices", {"artifact_hash": h})
    except NotAuthorized as exc:
        msg = str(exc)
        if "human_direction" in msg:
            return "direction"
        # A grammar refusal, the depth refusal or an identical resubmission
        # is the next round's findings (spec §7), as the grill refusal is in
        # the spec phase: `_findings_for` reads the command_rejected cause.
        if "submit_slices:" in msg or "identical" in msg or "a slice is not sliced" in msg:
            seat_row = front.newest_seat(store, run_id, Role.AUTHOR, "slices", n)
            cause_kind = next((x.kind for x in store.facts_for(run_id) if x.id == seat_row["cause"]), None)
            return "stall" if cause_kind == EventKind.COMMAND_REJECTED else "reauthor"
        ctx.log(f"unexpected refusal: {msg}")
        return "failed"
    return "submitted"
```

- [ ] **Step 6: `phases.py`**

```python
from kernel.authz import FRONT_HALF_STATES, SHAPING_STATES, phase_of
...
#: The states `run_loop` works in. `planned` and `sliced` are the two the
#: loop reaches and then exits from: the pass that reaches `planned` still
#: owes the plan's publication, and the pass that reaches `sliced` owes the
#: filing (shaping spec §2, §3). Every state past them belongs to nothing
#: this loop touches.
_LOOP_STATES = FRONT_HALF_STATES | SHAPING_STATES | frozenset({"planned", "sliced"})
```

`_is_round_cause`:

```python
def _is_round_cause(f) -> bool:
    if f.kind == EventKind.COMMAND_REJECTED:
        return f.payload.get("command_name") in ("submit_spec", "submit_plan", "submit_slices",
                                                  "record_one_piece") and f.actor != "human"
    return f.kind in ROUND_CAUSE_KINDS
```

`derived_session_obligation`: the three `if` heads become `if state in ("queued", "specified", "shaping"):`, `elif state in ("spec_submitted", "plan_submitted", "slices_submitted"):`, `elif state in ("spec_accepted", "plan_accepted", "slices_accepted"):`.

In `run_loop`, after the `park is not None` block and before `if state in ("queued", "specified"):`:

```python
            if state == "shaping":
                from coordinator import shape
                out = shape.shape_round(ctx)
                if out == "budget":
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
```

- [ ] **Step 7: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_shape_round.py -q`, then the suite.
Expected: PASS; **1515 passed, 3 skipped** (11 new).

- [ ] **Step 8: Mutations**

Watch only `ARTIFACT_OUT` in `run_turn`'s `watched`: `test_one_piece_ending` reds (the turn ends `cap`). Record the plan when both files exist (swap the order of the two `if`s): `test_both_files_the_ruling_is_read` reds. Revert.

- [ ] **Step 9: Commit**

```bash
git add v2/coordinator skills/shape-author v2/tests/coordinator/test_shape_round.py
git commit -m "feat(coordinator): the shaping round -- a ruling or a slice plan, and the loop's branch at shaping"
```

### Task 7: The slice review, the gate, and the ungated advance

**Files:**
- Modify: `v2/coordinator/phases.py:339-436` (`run_loop`: the `slices_submitted` and `slices_accepted` branches)
- Modify: `v2/coordinator/human.py:111-138` (`classify_batch` states)
- Modify: `v2/tests/coordinator/test_human.py:60-75` (one parametrised case)
- Test: `v2/tests/coordinator/test_slices_review_and_gate.py`

**Interfaces:**
- Consumes: `review.review_round` (phase from the state; unchanged), `stall`, `human.human_pass`, `approve_artifact` at `slices_accepted`, `advance_ungated` (Task 3).
- Produces: `run_loop` at `slices_submitted` runs the review round; at `slices_accepted` parks at the gate when `slices ∈ gates`, else records `advance_ungated` and continues; `classify_batch` treats `slices_submitted`/`slices_accepted` as gate states (`approve` is a ruling, prose is a correction).

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/coordinator/test_slices_review_and_gate.py
"""Task 7: the review round at slices_submitted, the gate at slices_accepted,
the ungated advance (shaping spec §3 *The review round*, *The gate*)."""
import os
import time

import pytest

from coordinator import human, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.events import EventKind
from kernel.slices import COARSE
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": [], "comments": []}
REVISED = SLICES_BYTES.replace(b"the API.", b"the API and the client.") + b"\n## Dispositions\n\n1. accepted — merged.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=(), cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, project_config=cfg, issue=dict(ISSUE, labels=list(labels)), shape=False)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "i12-epic-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}
        ctx = phases.Ctx(store=s, run_id="i12-epic-1", server="http://srv", repo="o/r", issue_number=12,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


class Script:
    """The shaper writes a plan; the reviewer answers with *verdict*."""
    def __init__(self, fake, store, verdict="PASS", plans=(SLICES_BYTES, REVISED)):
        self.fake, self.store, self.verdict, self.plans, self.n = fake, store, verdict, list(plans), 0
        self.briefs = []

    def __call__(self, sid, text):
        ws = self.fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        if text.startswith("# Bircher slices author brief"):
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(self.plans[min(self.n, len(self.plans) - 1)])
            self.n += 1
        elif text.startswith("# Bircher review brief"):
            self.briefs.append(text)
            h = self.store.phase_artifact("i12-epic-1", "slices")
            v = self.verdict if isinstance(self.verdict, str) else self.verdict.pop(0)
            open(os.path.join(ws, seat.REVIEW_OUT), "w").write(f"1. [high] §1 too fine\n\nVERDICT: {v} {h[:8]}\n")


def _carrier(s):
    return phases._newest_author_session(type("C", (), {"store": s, "run_id": "i12-epic-1"})())


def test_ungated_review_and_advance(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",))
    fake.on_prompt = Script(fake, s)
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    kinds = [x.kind for x in s.facts_for("i12-epic-1")]
    assert "parked" not in kinds and "human_ruling" not in kinds
    assert s.newest_fact("i12-epic-1", EventKind.ARTIFACT_ADVANCED).payload["phase"] == "slices"
    # The reviewer's brief carried the coarse paragraph and named the slice plan.
    assert COARSE in fake.on_prompt.briefs[0] and "of a slices" in fake.on_prompt.briefs[0]
    v = front.newest_review_verdict(s, "i12-epic-1", "slices", 0)
    assert v.payload["verdict"] == "accept" and v.payload["reviewer_identity"] == "codex"


def test_gated_by_default_parks_and_approve_moves_on(world):
    s, f, fake, ctx = world()
    fake.on_prompt = Script(fake, s)
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("i12-epic-1") == "slices_accepted"
    park = front.current_park(s, "i12-epic-1")
    assert park.payload["reason"] == "gate" and park.payload["phase"] == "slices"
    # The notice on the issue, and the prompt in the carrier with the plan attached.
    notices = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"] and "bircher: parked gate" in a[a.index("--body") + 1]]
    assert len(notices) == 1 and "**slices** phase" in notices[0][notices[0].index("--body") + 1]
    sid = _carrier(s)
    last = fake.sessions[sid]["items"][-1]["content"][0]["text"]
    assert last.startswith(human.NOT_FOR_THE_AGENT) and "## Slice 1: The store" in last
    fake.add_user_message(sid, "approve")
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    assert s.newest_fact("i12-epic-1", EventKind.HUMAN_RULING).payload == {
        "ruling": "approve", "phase": "slices", "epoch": 0,
        "artifact_hash": s.phase_artifact("i12-epic-1", "slices"),
        "cursor_item_id": fake.sessions[sid]["items"][-1]["id"]}


def test_a_correction_at_the_gate_is_the_next_rounds_findings(world):
    s, f, fake, ctx = world()
    script = Script(fake, s)
    fake.on_prompt = script
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    fake.add_user_message(_carrier(s), "merge slices 1 and 2; the store alone is a fragment")
    assert phases.run_loop(ctx) == phases.Exit.PARKED           # a new round, a new plan, the gate again
    assert s.run_state("i12-epic-1") == "slices_accepted"
    subs = front.submissions(s, "i12-epic-1", "slices", 0)
    assert len(subs) == 2 and s.read_blob(subs[-1].payload["hash"]) == REVISED
    assert s.newest_fact("i12-epic-1", EventKind.REVIEW_VERDICT).payload["ruling"] == "review_ruling"
    hr = [x for x in s.facts_of_kind("i12-epic-1", EventKind.HUMAN_RULING) if x.payload["ruling"] == "request_revision"]
    assert len(hr) == 1


def test_a_fail_then_a_pass_with_dispositions(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",))
    fake.on_prompt = Script(fake, s, verdict=["FAIL", "PASS"])
    assert phases.run_loop(ctx) == phases.Exit.OK
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.rounds_used(s, "i12-epic-1", "slices", 0) == 1
    # Rotation: codex reviewed round 1, so codex authored round 2 and claude reviewed it.
    subs = front.submissions(s, "i12-epic-1", "slices", 0)
    assert [x.payload["author"] for x in subs] == ["claude", "codex"]
    assert "## The previous round's findings" in fake.on_prompt.briefs[1]


def test_bound_exhausted_parks_at_slices_submitted(world):
    s, f, fake, ctx = world(labels=("bircher:autonomous",), cfg={"max_rounds": 1})
    fake.on_prompt = Script(fake, s, verdict="FAIL", plans=(SLICES_BYTES, REVISED, REVISED + b"\nmore\n"))
    assert phases.run_loop(ctx) == phases.Exit.PARKED
    assert s.run_state("i12-epic-1") == "slices_submitted"
    assert front.current_park(s, "i12-epic-1").payload["reason"] == "bound_exhausted"


@pytest.mark.parametrize("state", ["slices_submitted", "slices_accepted"])
def test_classify_batch_treats_the_slice_gate_as_a_gate(state):
    approve = [{"id": "i1", "role": "user", "text": "Approve"}]
    prose = [{"id": "i1", "role": "user", "text": "merge 1 and 2"}]
    assert human.classify_batch(approve, state=state, grill_open=False) == ("approve", "")
    assert human.classify_batch(prose, state=state, grill_open=False) == ("revision", "merge 1 and 2")
    assert human.classify_batch(prose, state="shaping", grill_open=False) == ("direction", "merge 1 and 2")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_slices_review_and_gate.py -q`
Expected: FAIL — the loop exits `OK` at `slices_submitted` ("a front-half state no branch above claims"), and `classify_batch` returns `direction` for the gate states.

- [ ] **Step 3: `human.py`**

```python
    if state in ("slices_submitted", "slices_accepted", "spec_submitted", "spec_accepted",
                 "plan_submitted", "plan_accepted"):
        if single == APPROVE:
            return "approve", ""
        return "revision", joined
```

- [ ] **Step 4: `phases.py`**

The review branch's head becomes `if state in ("slices_submitted", "spec_submitted", "plan_submitted"):` (body unchanged), and the gate branch becomes:

```python
            if state in ("slices_accepted", "spec_accepted", "plan_accepted"):
                if state == "slices_accepted" and "slices" not in policy_of(store, run_id).gates:
                    # The coordinator's own transition when the policy holds no
                    # slice gate (shaping spec §2 `advance_ungated`): its own
                    # fact, because filing children is an effect with an
                    # obligation and the transition that authorises it must be
                    # one. Under the pass's operator generation.
                    seat.command(ctx, "advance_ungated", {})
                    continue
                if stall(ctx, "gate", session_id=_newest_author_session(ctx), findings_hash=None, verdict=None,
                         reviewer=None) == "parked":
                    return Exit.PARKED
                continue
```

with `from coordinator import author, human, review, seat` and `from kernel.policy import policy_of` in `run_loop`'s imports.

- [ ] **Step 5: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_slices_review_and_gate.py tests/coordinator/test_human.py -q`, then the suite.
Expected: PASS; **1522 passed, 3 skipped** (7 new).

- [ ] **Step 6: Mutations**

Make the ungated branch call `stall` regardless of gates: `test_ungated_review_and_advance` reds. Drop `slices_accepted` from `classify_batch`'s gate tuple: `test_gated_by_default_parks_and_approve_moves_on` reds (the approve is read as a direction and refused). Revert.

- [ ] **Step 7: Commit**

```bash
git add v2/coordinator v2/tests/coordinator
git commit -m "feat(coordinator): the slice plan's review, the gate, and the ungated advance"
```

### Task 8: `file_owed` — the four passes

**Files:**
- Create: `v2/coordinator/filing.py`
- Modify: `v2/coordinator/phases.py` (`run_loop`: the `sliced` branch)
- Modify: `v2/tests/coordinator/fake_omnigent.py` (`gh` answers creates, reads, links, labels, closes)
- Test: `v2/tests/coordinator/test_file_owed.py`

**Interfaces:**
- Consumes: `kernel.filing` composers and `expected_labels`/`filed_numbers`/`filed_ids` (Task 5); `front.filing_obligations`, `satisfied_obligation`, `slices_filed`, `filing_complete` (Task 4); `slices.render_child`, `umbrella_body`, `topo_order` (Task 1); `perform_effect`, `seat.command`.
- Produces:
  - `coordinator.filing.file_owed(ctx) -> list[str]` (the keys performed this pass): idempotent per obligation; four passes; `record_slice_filed` after each create; `record_filing_complete` last, only when no `filing_complete` exists.
  - Keys: `slice:<run>:<n>:<generation>`, `link:<run>:<n>:<blocker>:<generation>`, `queue:<run>:<n>:<generation>`, `umbrella:<run>:<generation>`, `umbrella-label:<run>:<generation>`.
  - `run_loop` at `sliced`: `file_owed`, then `Exit.OK`.
  - `FakeOmnigent.issues: dict[int, dict]` (`state`, `closedAt`, `closedByPullRequestsReferences`, `labels`, `blocked_by`, `title`, `body`), `FakeOmnigent.prs: dict[int, str]`, `FakeOmnigent.next_issue`; `gh` answers: `issue create` (a new issue, URL), `api repos/<r>/issues/<n> --jq .id` (`1000 + n`), `api …/blocked_by -X POST` (records the link), `issue edit` (labels), `issue comment` (URL), `issue close`, `issue view --json …` (the issue's fields), `pr view --json state`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/coordinator/test_file_owed.py
"""Task 8: file_owed's four idempotent passes (shaping spec §3 *Filing the
children*, §7's crash rows)."""
import os
import time

import pytest

from coordinator import filing, phases, seat
from coordinator.effects import KERNEL
from kernel import front, slices
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous", "bircher:gate-plan"], "comments": []}
THREE = SLICES_BYTES + b"""
## Slice 3: The client
Scope: Add the page. Call the endpoint. Show the result.
Non-goals: styling.
Depends on: 1, 2
"""
REVERSED = b"""# T

## Slice 1: The API
Scope: Add the endpoint. Wire it. Test it.
Non-goals: x.
Depends on: 2

## Slice 2: The store
Scope: Add the table. Add the reads. Test them.
Non-goals: y.
Depends on: none
"""


class Crash(Exception):
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(plan=SLICES_BYTES, labels=("bircher:autonomous", "bircher:gate-plan")):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "i12-epic-1", labels=labels, issue=dict(ISSUE, labels=list(labels)), shape=False).to_sliced(plan)
        fake = FakeOmnigent()
        fake.issues[12] = {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": [], "labels": ["bircher:running"], "blocked_by": []}
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "i12-epic-1",
               "PATH": os.environ["PATH"]}
        ctx = phases.Ctx(store=s, run_id="i12-epic-1", server="http://srv", repo="o/r", issue_number=12,
                         repo_dir="", workspaces_root="", bundle_dir=str(tmp_path / "bundle"), agent_ids={},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=time.time, sleep=lambda x: None, log=lambda m: None)
        from kernel.dispatch import Role, dispatch
        ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
        return s, f, fake, ctx
    return make


def _children(fake):
    return {n: i for n, i in fake.issues.items() if n != 12}


def test_full_filing_in_one_pass(world):
    s, f, fake, ctx = world()
    keys = filing.file_owed(ctx)
    assert len(keys) == 2 + 1 + 2 + 2                      # creates, link, queue labels, umbrella + label
    kids = _children(fake)
    assert sorted(kids) == [40, 41]
    h = s.phase_artifact("i12-epic-1", "slices")
    plan = slices.parse(SLICES_BYTES)
    assert kids[40]["title"] == "The store" and kids[40]["body"] == slices.render_child(plan.slices[0], 12, h[:8], {})[1]
    assert kids[41]["body"] == slices.render_child(plan.slices[1], 12, h[:8], {1: 40})[1]
    assert kids[41]["blocked_by"] == [1040]
    for k in (40, 41):
        assert sorted(kids[k]["labels"]) == ["bircher:autonomous", "bircher:gate-plan", "bircher:queued", "bircher:slice"]
    umbrella = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]]
    assert len(umbrella) == 1 and umbrella[0][umbrella[0].index("--body") + 1] == slices.umbrella_body(h[:8], plan, {1: 40, 2: 41})
    assert fake.issues[12]["labels"] == ["bircher:sliced"]
    filed = front.slices_filed(s, "i12-epic-1", 0)
    assert {n: x.payload["issue"] for n, x in filed.items()} == {1: 40, 2: 41}
    assert front.filing_complete(s, "i12-epic-1", 0).payload["children"] == [40, 41]
    # The queue label was the LAST thing each child got: the link came first.
    order = [a[:4] for a in fake.gh]
    assert order.index(["gh", "api", "repos/o/r/issues/41/dependencies/blocked_by", "-X"]) < \
        order.index(["gh", "issue", "edit", "40"])


def test_dependency_order_files_the_blocker_first(world):
    s, f, fake, ctx = world(plan=REVERSED)
    filing.file_owed(ctx)
    creates = [a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]
    assert creates[0][creates[0].index("--title") + 1] == "The store"        # slice 2 first
    assert front.slices_filed(s, "i12-epic-1", 0)[1].payload["issue"] == 41


def test_a_second_pass_over_a_complete_filing_issues_nothing(world, monkeypatch):
    s, f, fake, ctx = world()
    filing.file_owed(ctx)
    calls = {"effects": 0, "commands": 0}
    real_perform, real_command = filing.perform_effect, seat.command
    monkeypatch.setattr(filing, "perform_effect", lambda *a, **k: calls.__setitem__("effects", calls["effects"] + 1) or real_perform(*a, **k))
    monkeypatch.setattr(seat, "command", lambda *a, **k: calls.__setitem__("commands", calls["commands"] + 1) or real_command(*a, **k))
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    assert filing.file_owed(ctx) == []
    assert calls == {"effects": 0, "commands": 0}


@pytest.mark.parametrize("after", [1, 2, 3, 4, 5, 6])
def test_a_crash_at_each_boundary_is_repaired_by_the_next_pass(world, monkeypatch, after):
    """Effects in order: create 1, create 2, link 2<-1, queue 1, queue 2,
    umbrella, umbrella label. A crash after the Nth leaves the next pass
    exactly the remainder, and NO child carries bircher:queued while any
    link of the plan is unsatisfied."""
    s, f, fake, ctx = world()
    real = filing.perform_effect
    count = {"n": 0}

    def crashing(*a, **k):
        out = real(*a, **k)
        count["n"] += 1
        if count["n"] == after:
            raise Crash(after)
        return out
    monkeypatch.setattr(filing, "perform_effect", crashing)
    with pytest.raises(Crash):
        filing.file_owed(ctx)
    kids = _children(fake)
    owed_links = [ob for ob in front.unsatisfied_filing(s, "i12-epic-1", 0) if ob["kind"] == "slice_dependency"]
    if owed_links:
        assert not any("bircher:queued" in i["labels"] for i in kids.values())
    assert front.filing_complete(s, "i12-epic-1", 0) is None
    monkeypatch.setattr(filing, "perform_effect", real)
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    second = filing.file_owed(ctx)
    assert len(second) == 7 - after
    assert len([a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]) == 2     # never a duplicate create
    assert front.filing_complete(s, "i12-epic-1", 0) is not None
    assert sorted(_children(fake)) == [40, 41]


def test_a_crash_between_a_create_and_its_fact_records_the_fact_next_pass(world, monkeypatch):
    s, f, fake, ctx = world()
    real = seat.command

    def crashing(ctx_, name, payload):
        if name == "record_slice_filed" and payload["slice"] == 1:
            raise Crash("fact")
        return real(ctx_, name, payload)
    monkeypatch.setattr(seat, "command", crashing)
    with pytest.raises(Crash):
        filing.file_owed(ctx)
    assert sorted(_children(fake)) == [40] and front.slices_filed(s, "i12-epic-1", 0) == {}
    monkeypatch.setattr(seat, "command", real)
    from kernel.dispatch import Role, dispatch
    ctx.generation = dispatch(s, "i12-epic-1", actor="coordinator", role=Role.OPERATOR).generation
    filing.file_owed(ctx)
    assert front.slices_filed(s, "i12-epic-1", 0)[1].payload["issue"] == 40
    assert len([a for a in fake.gh if a[:3] == ["gh", "issue", "create"]]) == 2


def test_three_slices_and_the_loop_exits_ok_at_sliced(world):
    s, f, fake, ctx = world(plan=THREE)
    assert phases.run_loop(ctx) == phases.Exit.OK
    kids = _children(fake)
    assert sorted(kids) == [40, 41, 42] and kids[42]["blocked_by"] == [1040, 1041]
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.filing_complete(s, "i12-epic-1", 0).payload["children"] == [40, 41, 42]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_file_owed.py -q`
Expected: FAIL — `ImportError: cannot import name 'filing' from 'coordinator'`.

- [ ] **Step 3: The fake's `gh`**

In `v2/tests/coordinator/fake_omnigent.py`, add to `__init__`: `self.issues = {}`, `self.prs = {}`, `self.next_issue = 40`; replace the `gh` branch of `run` with:

```python
        if os.path.basename(str(argv[0])) == "gh":
            self.gh.append(list(argv))
            r = R()
            r.stdout = self._gh(list(argv))
            return r
```

and add the method:

```python
    def _gh(self, argv: list[str]) -> str:
        """What GitHub answers a filing or sweep argv (shaping spec §3):
        creates mint issues from `next_issue`, reads answer from `issues` and
        `prs`, links and labels mutate `issues`, comments and closes answer
        as gh does."""
        def val(flag):
            return argv[argv.index(flag) + 1] if flag in argv else None
        def vals(flag):
            return [argv[i + 1] for i, a in enumerate(argv) if a == flag]
        sub = argv[1:3]
        if sub == ["issue", "create"]:
            n = self.next_issue; self.next_issue += 1
            body = open(val("--body-file")).read() if "--body-file" in argv else ""
            self.issues[n] = {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": [],
                              "labels": vals("--label"), "blocked_by": [], "title": val("--title"), "body": body}
            return f"https://github.com/o/r/issues/{n}\n"
        if sub == ["api", *sub[1:]] and argv[1] == "api":
            path = argv[2]
            if path.endswith("/dependencies/blocked_by") and "-X" in argv:
                n = int(path.split("/")[3])
                self.issues[n]["blocked_by"].append(int(val("-f").split("=", 1)[1]))
                return "{}"
            if "--jq" in argv and val("--jq") == ".id":
                return f"{1000 + int(path.split('/')[3])}\n"
            if path.endswith("/dependencies/blocked_by"):
                n = int(path.split("/")[3])
                return json.dumps([{"state": "open"} for _ in self.issues[n]["blocked_by"]])
            return "{}"
        if sub == ["issue", "edit"]:
            n = int(argv[3])
            labs = set(self.issues.setdefault(n, {"labels": [], "state": "OPEN"})["labels"])
            labs |= set(vals("--add-label")); labs -= set(vals("--remove-label"))
            self.issues[n]["labels"] = sorted(labs)
            return ""
        if sub == ["issue", "comment"]:
            return f"https://github.com/o/r/issues/{argv[3]}#issuecomment-{next(self._ids)}"
        if sub == ["issue", "close"]:
            self.issues[int(argv[3])]["state"] = "CLOSED"
            return ""
        if sub == ["issue", "view"]:
            fields = val("--json").split(",")
            return json.dumps({k: self.issues[int(argv[3])].get(k) for k in fields})
        if sub == ["pr", "view"]:
            return json.dumps({"state": self.prs.get(int(argv[3]), "OPEN")})
        return f"https://github.com/o/r/issues/1#issuecomment-{next(self._ids)}"
```

(The `sub == ["api", ...]` line simplifies to `if argv[1] == "api":`; write it that way.)

- [ ] **Step 4: `coordinator/filing.py`**

```python
# v2/coordinator/filing.py
"""Filing the children (shaping spec §3 *Filing the children*): four passes
over the accepted plan, each complete before the next, idempotent per
obligation. Every step checks its own fact or effect and performs only what
is missing, so a pass that resumes after any crash repairs exactly the
remainder; the kernel refuses anything performed out of order
(kernel/filing.py), so the order here is the behaviour and the kernel's
refusal is the guard."""
from __future__ import annotations

from coordinator import seat
from coordinator.effects import perform_effect
from kernel import filing as kfiling
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.effect_class import EffectClass


def file_owed(ctx) -> list[str]:
    store, run_id = ctx.store, ctx.run_id
    if ctx.state() != "sliced":
        return []
    n = ctx.epoch()
    if front.filing_complete(store, run_id, n) is not None:
        return []
    plan = front.accepted_plan(store, run_id, n)
    h = front.accepted_slices_hash(store, run_id, n)
    parent, repo, gen = front.issue_number(store, run_id), ctx.repo, ctx.generation
    labels = kfiling.expected_labels(store, run_id)
    base = {"run": run_id, "epoch": n}
    done: list[str] = []

    def owed(ob):
        return front.satisfied_obligation(store, run_id, ob) is None

    # Pass 1: the creates and their facts, in dependency order.
    for k in slices.topo_order(plan):
        ob = dict(base, kind="slice_issue", slice=k, parent=parent, plan_hash=h)
        if owed(ob):
            title, body = slices.render_child(plan.by_number()[k], parent, h[:8], kfiling.filed_numbers(store, run_id, n))
            key = f"slice:{run_id}:{k}:{gen}"
            perform_effect(EffectClass.ISSUE_CREATE, key, kfiling.create_argv(repo, title, labels), timeout=60,
                           env=ctx.effect_env(), obligation=ob, body={"artifact": put_artifact(store, body.encode("utf-8"))})
            done.append(key)
        if k not in front.slices_filed(store, run_id, n):
            # The key of the satisfied create -- whichever generation minted it.
            row = front.satisfied_obligation(store, run_id, ob)
            seat.command(ctx, "record_slice_filed", {"slice": k, "effect_key": row["idempotency_key"]})
    numbers, ids = kfiling.filed_numbers(store, run_id, n), kfiling.filed_ids(store, run_id, n)
    # Pass 2: the links.
    for k, b in plan.edges():
        ob = dict(base, kind="slice_dependency", plan_hash=h, slice=k, blocker=b)
        if owed(ob):
            key = f"link:{run_id}:{k}:{b}:{gen}"
            perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.link_argv(repo, numbers[k], ids[b]), timeout=60,
                           env=ctx.effect_env(), obligation=ob)
            done.append(key)
    # Pass 3: the queue labels -- only now, so a queued child has every link.
    for s in plan.slices:
        ob = dict(base, kind="slice_queue", plan_hash=h, slice=s.number)
        if owed(ob):
            key = f"queue:{run_id}:{s.number}:{gen}"
            perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.queue_argv(repo, numbers[s.number]), timeout=60,
                           env=ctx.effect_env(), obligation=ob)
            done.append(key)
    # Pass 4: the umbrella and its label.
    ob = dict(base, kind="umbrella", plan_hash=h)
    if owed(ob):
        key = f"umbrella:{run_id}:{gen}"
        perform_effect(EffectClass.COMMENT, key, kfiling.comment_argv(repo, parent, slices.umbrella_body(h[:8], plan, numbers)),
                       timeout=60, env=ctx.effect_env(), obligation=ob)
        done.append(key)
    ob = dict(base, kind="umbrella_label", plan_hash=h)
    if owed(ob):
        key = f"umbrella-label:{run_id}:{gen}"
        perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.umbrella_label_argv(repo, parent), timeout=60,
                       env=ctx.effect_env(), obligation=ob)
        done.append(key)
    # Last, once: the kernel's statement that nothing about filing is owed.
    if front.filing_complete(store, run_id, n) is None:
        seat.command(ctx, "record_filing_complete", {})
    return done
```

- [ ] **Step 5: The loop's pass at `sliced`**

In `run_loop`, after `if state == "planned": return Exit.OK`:

```python
            if state == "sliced":
                # The pass that reaches `sliced` owes the filing (shaping spec
                # §3); the run then waits for its children and the sweep.
                from coordinator import filing
                filing.file_owed(ctx)
                return Exit.OK
```

- [ ] **Step 6: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_file_owed.py -q`, then the suite.
Expected: PASS; **1533 passed, 3 skipped** (11 new). Note that `test_gated_by_default_parks_and_approve_moves_on` (Task 7) now files the children on the approving pass — the test's `sliced` assertion still holds and the fake's `issues` dict fills; that is the spec's "the gated path from approval into filing in one loop", asserted here by adding `assert sorted(n for n in fake.issues if n != 12) == [40, 41]` to that test.

- [ ] **Step 7: Mutations**

Move pass 3 before pass 2: the kernel refuses the label (`slice_queue` precondition) and `test_full_filing_in_one_pass` reds — and the crash test's "no queued child while a link is owed" would red without the kernel. Skip the `owed(ob)` check on the umbrella: `test_a_second_pass_over_a_complete_filing_issues_nothing` reds. Revert.

- [ ] **Step 8: Commit**

```bash
git add v2/coordinator v2/tests/coordinator
git commit -m "feat(coordinator): file_owed -- four idempotent passes that file the children"
```

### Task 9: `sweep_sliced` and the CLI

**Files:**
- Create: `v2/coordinator/sweep.py`
- Modify: `v2/coordinator/cli.py` (`sweep-sliced`)
- Test: `v2/tests/coordinator/test_sweep_sliced.py`, `v2/tests/coordinator/test_cli.py` (one subprocess test)

**Interfaces:**
- Consumes: the closing commands (Task 4), `slices.completion_body`, `kfiling.comment_argv`/`close_argv` (Task 5), `perform_effect`, `seat.command`, `phases.Ctx`.
- Produces:
  - `sweep.ReadFailed(Exception)`; `sweep.gh_json(argv) -> dict` (subprocess `gh … --json`, raises `ReadFailed` on a non-zero exit or non-JSON).
  - `sweep.sweep_sliced(store, *, server, repo, env, gh_json=gh_json, now=None, log=print) -> list[str]` (the run ids ended this firing).
  - `python -m coordinator.cli sweep-sliced --db … --server … --repo …` → `RC_OK`, one line per run ended.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/coordinator/test_sweep_sliced.py
"""Task 9: the sweep (shaping spec §3 *The sweep*, §7's closure rows)."""
import json
import os

import pytest

from coordinator import sweep
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


class GH:
    """A dict-backed `gh_json`: `issues[n]` and `prs[n]` are what GitHub holds."""
    def __init__(self):
        self.issues = {12: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []},
                       40: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []},
                       41: {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []}}
        self.prs = {}
        self.fail = set()
        self.reads = []

    def __call__(self, argv):
        n = int(argv[3])
        self.reads.append(n)
        if n in self.fail:
            raise sweep.ReadFailed(f"gh exited 1 for #{n}")
        if argv[1:3] == ["pr", "view"]:
            return {"state": self.prs.get(n, "OPEN")}
        fields = argv[argv.index("--json") + 1].split(",")
        return {k: self.issues[n][k] for k in fields}

    def merge(self, n, pr):
        self.issues[n] = {"state": "CLOSED", "closedAt": f"2026-09-10T0{n % 10}:00:00Z",
                          "closedByPullRequestsReferences": [{"number": pr}]}
        self.prs[pr] = "MERGED"

    def close(self, n, pr=None):
        self.issues[n] = {"state": "CLOSED", "closedAt": "2026-09-10T00:00:00Z",
                          "closedByPullRequestsReferences": [] if pr is None else [{"number": pr}]}
        if pr is not None:
            self.prs[pr] = "CLOSED"

    def reopen(self, n):
        self.issues[n] = {"state": "OPEN", "closedAt": None, "closedByPullRequestsReferences": []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(run_ids=("i12-epic-1",)):
        db = tmp_path / "k.db"
        s = Store.open(db)
        fronts = {}
        for i, rid in enumerate(run_ids):
            f = Front(s, rid, shape=False, issue=dict(ISSUE, number=12 + i)).to_sliced()
            g = f._dispatch(Role.OPERATOR, "coordinator")
            f.file_all(g)
            fronts[rid] = f
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "PATH": os.environ["PATH"]}
        return s, fronts, fake, env
    return make


def _sweep(s, env, gh, **kw):
    return sweep.sweep_sliced(s, server="http://srv", repo="o/r", env=env, gh_json=gh, log=lambda m: None,
                              now=lambda: "2026-09-10T12:00:00Z", **kw)


def test_all_closed_ends_the_run_with_the_completion_comment_and_the_close(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.close(41, 91)
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert s.run_state("i12-epic-1") == "ended"
    closures = {f.payload["slice"]: f.payload for f in s.facts_of_kind("i12-epic-1", EventKind.SLICE_CLOSED)}
    assert closures[1]["state"] == "merged" and closures[2]["state"] == "closed"
    assert closures[1]["closed_at"] == "2026-09-10T00:00:00Z"
    obs = s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED)
    assert obs.payload["parent"] == "open" and obs.payload["slices"] == [1, 2]
    comment = [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]][-1]
    assert comment[comment.index("--body") + 1] == (
        "bircher: sliced complete\n\n- #40 Slice 1: The store — merged\n- #41 Slice 2: The API — closed\n")
    assert [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]] == [["gh", "issue", "close", "12", "--repo", "o/r"]]
    # The parent was read AFTER the children, in the same firing.
    assert gh.reads == [40, 41, 12] or gh.reads == [40, 41, 90, 91, 12]


def test_one_open_child_keeps_the_parent_open(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90)
    assert _sweep(s, env, gh) == []
    assert s.run_state("i12-epic-1") == "sliced"
    assert front.current_closure(s, "i12-epic-1", 0, 1).payload["state"] == "merged"
    assert front.current_closure(s, "i12-epic-1", 0, 2) is None
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED) is None
    assert not [a for a in fake.gh if a[:3] in (["gh", "issue", "comment"], ["gh", "issue", "close"])]


def test_skips_a_halted_run_and_one_without_filing_complete(world, tmp_path):
    s, fronts, fake, env = world()
    s.set_reconciliation("i12-epic-1", {"why": "test"})
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91)
    assert _sweep(s, env, gh) == [] and gh.reads == []
    s2 = Store.open(tmp_path / "k2.db")
    Front(s2, "i13-epic-1", shape=False, issue=dict(ISSUE, number=13)).to_sliced()   # sliced, nothing filed
    assert _sweep(s2, dict(env, BIRCHER_KERNEL_DB=str(tmp_path / "k2.db")), gh) == [] and gh.reads == []


def test_three_firings_with_a_reopening(world):
    s, fronts, fake, env = world()
    gh = GH()
    gh.merge(40, 90)
    _sweep(s, env, gh)                                             # A closed, recorded
    gh.reopen(40); gh.merge(41, 91)
    assert _sweep(s, env, gh) == []                                # A reopened, B closed, no outcome
    assert front.current_closure(s, "i12-epic-1", 0, 1).kind == EventKind.SLICE_REOPENED
    assert front.current_closure(s, "i12-epic-1", 0, 2).kind == EventKind.SLICE_CLOSED
    gh.merge(40, 92)
    assert _sweep(s, env, gh) == ["i12-epic-1"]                    # A closed again: the outcome
    assert s.run_state("i12-epic-1") == "ended"


def test_a_failed_read_ends_the_firing_before_completion(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91); gh.fail.add(41)
    assert _sweep(s, env, gh) == []
    assert front.current_closure(s, "i12-epic-1", 0, 1).payload["state"] == "merged"     # recorded before the failure
    assert front.current_closure(s, "i12-epic-1", 0, 2) is None
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED) is None
    assert not [a for a in fake.gh if a[:3] in (["gh", "issue", "comment"], ["gh", "issue", "close"])]
    gh.fail.clear()
    assert _sweep(s, env, gh) == ["i12-epic-1"]


def test_a_hand_closed_parent_ends_without_a_close(world):
    s, fronts, fake, env = world()
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91); gh.close(12)
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert s.newest_fact("i12-epic-1", EventKind.CHILDREN_OBSERVED_CLOSED).payload["parent"] == "closed"
    assert [a for a in fake.gh if a[:3] == ["gh", "issue", "comment"]]
    assert not [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]]


def test_a_parent_reopened_after_the_pipeline_closed_it_is_the_persons(world):
    s, fronts, fake, env = world()
    f = fronts["i12-epic-1"]
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f.satisfy(g, {"kind": "parent_close", "run": f.run_id, "epoch": 0}, value="closed")   # closed once, before a crash
    gh = GH(); gh.merge(40, 90); gh.merge(41, 91)                  # and the parent is open again
    assert _sweep(s, env, gh) == ["i12-epic-1"]
    assert not [a for a in fake.gh if a[:3] == ["gh", "issue", "close"]]                # no second close


def test_the_cli_runs_the_sweep_as_a_subprocess(world, tmp_path, monkeypatch):
    import subprocess, sys
    s, fronts, fake, env = world()
    gh_stub = tmp_path / "gh"
    gh_stub.write_text('#!/usr/bin/env bash\ncase " $* " in *" view 12 "*) echo \'{"state":"OPEN"}\' ;; '
                       '*"pr view"*) echo \'{"state":"MERGED"}\' ;; *" comment "*) echo "https://github.com/o/r/issues/12#issuecomment-1" ;; '
                       '*" close "*) echo "" ;; *) echo \'{"state":"CLOSED","closedAt":"t","closedByPullRequestsReferences":[{"number":9}]}\' ;; esac\n')
    gh_stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    r = subprocess.run([sys.executable, "-m", "coordinator.cli", "sweep-sliced", "--db", str(tmp_path / "k.db"),
                        "--server", "http://srv", "--repo", "o/r"],
                       cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))), capture_output=True, text=True,
                       env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", BIRCHER_EFFECT_MODE="legacy"))
    assert r.returncode == 0, r.stderr
    assert "i12-epic-1" in r.stdout
```

(The subprocess test runs under `BIRCHER_EFFECT_MODE=legacy` so the completion effects run the stub `gh` unjournalled; the kernel's `gh` resolution is bypassed by `legacy` — this test proves the CLI wiring and the read path, not the journal, which the in-process tests above prove.)

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_sweep_sliced.py -q`
Expected: FAIL — `ImportError: cannot import name 'sweep'`.

- [ ] **Step 3: `coordinator/sweep.py`**

```python
# v2/coordinator/sweep.py
"""The sweep (shaping spec §3 *The sweep*): each firing of the scheduled
wave, before it generates the queue, reads every child of every filed epic,
records what it saw, and ends the epic whose children all closed.

Reads are not effects (front-half §3); they are injected as `gh_json` so
tests answer them from a dict and the CLI answers them with `gh`. A read
that fails ends this run's firing there: the facts recorded before it stand,
nothing after it is read or recorded, no completion is attempted (§3).
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess

from coordinator import phases, seat
from coordinator.effects import perform_effect
from kernel import filing as kfiling
from kernel import front, slices
from kernel.dispatch import Role, dispatch
from kernel.effect_class import EffectClass
from kernel.effects import is_halted
from kernel.events import EventKind


class ReadFailed(Exception):
    """A `gh` read did not answer."""


def gh_json(argv: list[str]) -> dict:
    r = subprocess.run(list(argv), capture_output=True, text=True)
    if r.returncode != 0:
        raise ReadFailed(f"{' '.join(argv[:4])} rc={r.returncode}: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except ValueError as exc:
        raise ReadFailed(f"{' '.join(argv[:4])}: not JSON: {r.stdout[:200]!r}") from exc


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _closure(gh, repo: str, issue: dict) -> tuple[str, str] | None:
    """`(state, closed_at)` for a closed child -- `merged` when a pull request
    that closed it is MERGED, `closed` otherwise -- or None when open."""
    if issue.get("state") != "CLOSED":
        return None
    state = "closed"
    for ref in issue.get("closedByPullRequestsReferences") or []:
        pr = gh(["gh", "pr", "view", str(ref["number"]), "--repo", repo, "--json", "state"])
        if pr.get("state") == "MERGED":
            state = "merged"
            break
    return state, str(issue.get("closedAt") or "")


def sweep_sliced(store, *, server: str, repo: str, env: dict, gh_json=gh_json, now=None, log=print) -> list[str]:
    now = now or _now
    ended = []
    for run_id in store.all_run_ids():
        if store.run_state(run_id) != "sliced":
            continue
        n = front.epoch(store, run_id)
        if is_halted(store, run_id) or store.uncertain_effects(run_id):
            log(f"sweep: {run_id} is halted or holds pending effects; skipped")
            continue
        if front.filing_complete(store, run_id, n) is None:
            log(f"sweep: {run_id} has no filing_complete; the loop's, not the sweep's")
            continue
        parent = front.issue_number(store, run_id)
        ctx = phases.Ctx(store=store, run_id=run_id, server=server, repo=repo, issue_number=parent, repo_dir="",
                         workspaces_root="", bundle_dir="", agent_ids={}, host_id="", turn_timeout_s=1,
                         default_author="", env=dict(env, BIRCHER_RUN_ID=run_id), fetch=None, log=log)
        ctx.generation = dispatch(store, run_id, actor="coordinator", role=Role.OPERATOR).generation
        plan = front.accepted_plan(store, run_id, n)
        numbers = kfiling.filed_numbers(store, run_id, n)
        try:
            for s in plan.slices:
                issue = gh_json(["gh", "issue", "view", str(numbers[s.number]), "--repo", repo,
                                 "--json", "state,closedAt,closedByPullRequestsReferences"])
                seen = _closure(gh_json, repo, issue)
                cur = front.current_closure(store, run_id, n, s.number)
                if seen is not None and (cur is None or cur.kind != EventKind.SLICE_CLOSED):
                    seat.command(ctx, "record_slice_closed", {"slice": s.number, "closed_at": seen[1] or now(), "state": seen[0]})
                elif seen is None and cur is not None and cur.kind == EventKind.SLICE_CLOSED:
                    seat.command(ctx, "record_slice_reopened", {"slice": s.number, "observed_at": now()})
            all_closed = all((c := front.current_closure(store, run_id, n, s.number)) is not None
                             and c.kind == EventKind.SLICE_CLOSED for s in plan.slices)
            if not all_closed:
                continue
            parent_view = gh_json(["gh", "issue", "view", str(parent), "--repo", repo, "--json", "state"])
        except ReadFailed as exc:
            log(f"sweep: {run_id}: a read failed, the firing ends here for this run: {exc}")
            continue
        parent_state = "closed" if parent_view.get("state") == "CLOSED" else "open"
        seat.command(ctx, "record_children_observed_closed", {"parent_state": parent_state, "observed_at": now()})
        base = {"run": run_id, "epoch": n}
        states = {s.number: front.current_closure(store, run_id, n, s.number).payload["state"] for s in plan.slices}
        ob = dict(base, kind="umbrella_close")
        if front.satisfied_obligation(store, run_id, ob) is None:
            perform_effect(EffectClass.COMMENT, f"umbrella-close:{run_id}:{ctx.generation}",
                           kfiling.comment_argv(repo, parent, slices.completion_body(plan, numbers, states)),
                           timeout=60, env=ctx.effect_env(), obligation=ob)
        ob = dict(base, kind="parent_close")
        if parent_state == "open" and front.satisfied_obligation(store, run_id, ob) is None:
            perform_effect(EffectClass.ISSUE_OR_LABEL, f"parent-close:{run_id}:{ctx.generation}",
                           kfiling.close_argv(repo, parent), timeout=60, env=ctx.effect_env(), obligation=ob)
        seat.command(ctx, "record_run_outcome", {"outcome": "sliced"})
        ended.append(run_id)
    return ended
```

- [ ] **Step 4: The CLI**

In `v2/coordinator/cli.py`, add the parser beside `phases`:

```python
    sw = subs.add_parser("sweep-sliced")
    sw.add_argument("--db", required=True); sw.add_argument("--server", required=True)
    sw.add_argument("--repo", required=True)
```

and the branch before `if a.mode in ("retire", "cancel"):`:

```python
    if a.mode == "sweep-sliced":
        from kernel.store import Store

        from coordinator.sweep import sweep_sliced
        store = Store.open(a.db)
        for rid in sweep_sliced(store, server=a.server, repo=a.repo,
                                env=dict(os.environ, BIRCHER_KERNEL_DB=a.db),
                                log=lambda m: print(m, file=sys.stderr)):
            print(rid)
        return RC_OK
```

- [ ] **Step 5: Run**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_sweep_sliced.py -q`, then the suite.
Expected: PASS; **1541 passed, 3 skipped** (8 new).

- [ ] **Step 6: Mutations**

Read only the slices with no closure (`if cur is None` around the read): `test_three_firings_with_a_reopening` reds. Continue past a `ReadFailed` (`pass` instead of `continue`): `test_a_failed_read_ends_the_firing_before_completion` reds — and the kernel's outcome guard refuses too, which is the point of both. Revert.

- [ ] **Step 7: Commit**

```bash
git add v2/coordinator v2/tests/coordinator
git commit -m "feat(coordinator): sweep_sliced -- closures as observations, the completing firing, the sliced outcome"
```

## Part C — Runner

### Task 10: The runner — labels, the resume gate, the two `run_item` branches, the generator, the sweep call, the self-test

**Files:**
- Modify: `batch/run-queue.sh` (`_preflight_labels`; `run_item` at the resume gate `:4318-4328`, the mint `:4362-4371`, the non-zero branch `:4440-4445`, the post-loop seam `:4446-4451`; the `source=issues` branch `:9155-9163`; `self_test` before `echo "self-test OK"`)
- Modify: `batch/lib/kernel-client.sh` (`_kernel_run_start` catches `IssueAlreadySliced`; new `_kernel_sliced_children`)
- Modify: `batch/issues-to-queue.sh:20-24` (`is_unblocked`), `:43-58` (the journal sweep)
- Modify: `docs/design/effect-site-inventory.md`, `docs/design/scar-effect-matrix.md` (line citations)

**Interfaces:**
- Consumes: `coordinator.cli sweep-sliced` (Task 9); `front.filing_complete` (Task 4); `enqueue.IssueAlreadySliced` (Task 4).
- Produces (shell functions the self-test drives):
  - `_front_half_resumable <state>` → rc 0 for the eleven front-half states (`shaping slices_submitted slices_accepted sliced queued spec_submitted spec_accepted specified plan_submitted plan_accepted planned`), rc 1 otherwise.
  - `_finish_sliced_item <item> <queue-file> <run_id>` → scorecard row `sliced` naming the children, the file moved to `$PROCESSED`.
  - `_interrupted_sliced_item <item> <rc> <run_id>` → scorecard row `escalated` "filing interrupted", the file untouched, no outcome.
  - `_kernel_sliced_children <run_id>` → the `slice_filed` issue numbers, space-separated.
  - `_kernel_run_start` prints `create_run refused: …` on `IssueAlreadySliced` and returns 1; `run_item`'s refused-mint note carries that text.
  - `issues-to-queue.sh` queues open `sliced` runs with no `filing_complete` in the current epoch; `is_unblocked` returns 1 and says so when the read fails.
  - The `issues` source calls `sweep-sliced` before generating the queue.

- [ ] **Step 1: The self-test cases (they fail first)**

Add before `rm -rf "$_st_kdb"` / `echo "self-test OK"` in `self_test`:

```bash
  # --- shaping phase (spec §5): the resume list, the two sliced branches, the labels, the generator, the sweep call --
  local st
  for st in shaping slices_submitted slices_accepted sliced queued spec_submitted spec_accepted specified plan_submitted plan_accepted planned; do
    _front_half_resumable "$st" || { echo "FAIL resume gate: $st must resume"; exit 1; }
  done
  for st in implementing reviewing merge_requested merged ended cancelled ""; do
    _front_half_resumable "$st" && { echo "FAIL resume gate: '$st' must not resume"; exit 1; }
  done
  echo "resume gate lists the eleven front-half states OK"

  local sdir; sdir=$(mktemp -d)
  mkdir -p "$sdir/queue" "$sdir/processed"
  printf 'item\n' > "$sdir/queue/i12-epic.md"
  ( export SCORECARD="$sdir/scorecard.jsonl" PROCESSED="$sdir/processed"
    _kernel_sliced_children() { printf '40 41'; }
    _finish_sliced_item "i12-epic" "$sdir/queue/i12-epic.md" "i12-epic-1" ) \
    || { echo "FAIL _finish_sliced_item exited non-zero"; exit 1; }
  [ -f "$sdir/processed/i12-epic.md" ] && [ ! -f "$sdir/queue/i12-epic.md" ] || { echo "FAIL _finish_sliced_item: the queue file must move to processed"; exit 1; }
  grep -q '"outcome": "sliced"' "$sdir/scorecard.jsonl" && grep -q '40 41' "$sdir/scorecard.jsonl" \
    || { echo "FAIL _finish_sliced_item: the row must be sliced and name the children"; exit 1; }
  printf 'item\n' > "$sdir/queue/i13-epic.md"
  ( export SCORECARD="$sdir/scorecard.jsonl" PROCESSED="$sdir/processed"
    _interrupted_sliced_item "i13-epic" 1 "i13-epic-1" ) \
    || { echo "FAIL _interrupted_sliced_item exited non-zero"; exit 1; }
  [ -f "$sdir/queue/i13-epic.md" ] || { echo "FAIL _interrupted_sliced_item: the queue file must stay"; exit 1; }
  grep -q 'filing interrupted' "$sdir/scorecard.jsonl" || { echo "FAIL _interrupted_sliced_item: the row must say the filing was interrupted"; exit 1; }
  rm -rf "$sdir"
  echo "sliced branches OK"

  # The labels: the two new ones are required, and a repo missing bircher:sliced is named.
  local sldir; sldir=$(mktemp -d)
  cat > "$sldir/gh" <<'SH'
#!/usr/bin/env bash
cat "$GH_LABELS"
SH
  chmod +x "$sldir/gh"
  printf 'bircher:queued\nbircher:running\nbircher:escalated\nbircher:slice\nbircher:sliced\n' > "$sldir/all"
  printf 'bircher:queued\nbircher:running\nbircher:escalated\nbircher:slice\n' > "$sldir/no-sliced"
  ( PATH="$sldir:$PATH" GH_LABELS="$sldir/all" REPO=o/r _preflight_labels >/dev/null 2>&1 ) || {
    echo "FAIL _preflight_labels: refused a repo with all five labels"; exit 1; }
  local slmsg; slmsg=$( PATH="$sldir:$PATH" GH_LABELS="$sldir/no-sliced" REPO=o/r _preflight_labels 2>&1 ) && {
    echo "FAIL _preflight_labels: accepted a repo missing bircher:sliced"; exit 1; }
  case "$slmsg" in *bircher:sliced*) ;; *) echo "FAIL _preflight_labels: the refusal does not name bircher:sliced"; exit 1 ;; esac
  rm -rf "$sldir"
  echo "shaping labels in preflight OK"

  # The generator queues a sliced run without filing_complete, not one with, and fails closed on an unreadable blocker.
  local gdir; gdir=$(mktemp -d)
  cat > "$gdir/gh" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"dependencies/blocked_by"*) [ "${GH_BLOCKERS_FAIL:-0}" = 1 ] && exit 1; echo 0 ;;
  *"issue list"*) echo "" ;;
  *"--json title"*) echo "T" ;;
  *"--json body"*) echo "B" ;;
  *) echo '[]' ;;
esac
SH
  chmod +x "$gdir/gh"
  ( cd "$BUNDLE_DIR/v2" && PYTHONPATH=. "${BIRCHER_PY:-python3}" -c '
import sys
from tests.kernel.front import Front
from kernel.dispatch import Role
from kernel.store import Store
s = Store.open(sys.argv[1])
issue = {"number": 78, "title": "E", "body": "B", "labels": ["bircher:autonomous"], "comments": []}
Front(s, "i78-epic-1", shape=False, issue=issue).to_sliced()          # sliced, nothing filed: queued
f = Front(s, "i79-epic-1", shape=False, issue=dict(issue, number=79)).to_sliced()
f.file_all(f._dispatch(Role.OPERATOR, "coordinator"))                 # filing complete: the sweep ends it
' "$gdir/k.db" ) || { echo "FAIL sliced-run fixture could not be built"; exit 1; }
  ( cd "$gdir" && QUEUE="$gdir/queue" PATH="$gdir:$PATH" BIRCHER_KERNEL_DB="$gdir/k.db" REPO=o/r \
      bash "$BUNDLE_DIR/batch/issues-to-queue.sh" >/dev/null 2>&1 ) || { echo "FAIL issues-to-queue with sliced runs exited non-zero"; exit 1; }
  ls "$gdir/queue"/i78-*.md >/dev/null 2>&1 || { echo "FAIL a sliced run without filing_complete must be queued"; exit 1; }
  ls "$gdir/queue"/i79-*.md >/dev/null 2>&1 && { echo "FAIL a sliced run with filing_complete must not be queued"; exit 1; }
  # is_unblocked alone, extracted as the script extracts the runner's helpers.
  ( cd "$gdir" && eval "$(sed -n '/^is_unblocked()/,/^}/p' "$BUNDLE_DIR/batch/issues-to-queue.sh")" \
      && REPO=o/r PATH="$gdir:$PATH" GH_BLOCKERS_FAIL=1 is_unblocked 5 2>/dev/null ) && {
    echo "FAIL is_unblocked must fail closed when the blockers cannot be read"; exit 1; }
  ( cd "$gdir" && eval "$(sed -n '/^is_unblocked()/,/^}/p' "$BUNDLE_DIR/batch/issues-to-queue.sh")" \
      && REPO=o/r PATH="$gdir:$PATH" is_unblocked 5 2>/dev/null ) || {
    echo "FAIL is_unblocked must admit an issue with no open blocker"; exit 1; }
  # The refused mint: a second run for an issue whose run attempted a create.
  ( cd "$BUNDLE_DIR/v2" && PYTHONPATH=. "${BIRCHER_PY:-python3}" -c '
import sys
from tests.kernel.front import Front
from kernel.dispatch import Role
from kernel.store import Store
s = Store.open(sys.argv[1])
f = Front(s, "i80-epic-1", shape=False, issue={"number": 80, "title": "E", "body": "B", "labels": ["bircher:autonomous"], "comments": []}).to_sliced()
f.file_slice(f._dispatch(Role.OPERATOR, "coordinator"), 1, 90, 1090)
' "$gdir/k.db" ) || { echo "FAIL create-attempted fixture could not be built"; exit 1; }
  printf '{"number": 80, "title": "E", "body": "B", "labels": [{"name": "bircher:autonomous"}], "comments": []}' > "$gdir/issue.json"
  printf '{}' > "$gdir/cfg.json"
  local mint; mint=$( BIRCHER_KERNEL_DB="$gdir/k.db" _kernel_run_start "i80-epic-2" o/r "$(printf '0%.0s' $(seq 40))" "$gdir/issue.json" "$gdir/cfg.json" 2>&1 ) && {
    echo "FAIL _kernel_run_start must refuse an issue whose run attempted a create"; exit 1; }
  case "$mint" in *"attempted a child create"*) ;; *) echo "FAIL the refused mint must say why: $mint"; exit 1 ;; esac
  rm -rf "$gdir"
  echo "generator and refused mint OK"
```

(`_kernel_run_start`'s issue JSON is `gh issue view --json number,title,body,labels,comments` output, so `labels` are objects; `from_gh` reads `.name`.)

- [ ] **Step 2: Run the self-test to verify failure**

Run: `bash batch/run-queue.sh --self-test`
Expected: stops at `FAIL resume gate: shaping must resume` (`_front_half_resumable: command not found`).

- [ ] **Step 3: `run-queue.sh`**

`_preflight_labels`: `for l in bircher:running bircher:escalated bircher:slice bircher:sliced; do`.

Add beside `_preflight_labels`:

```bash
# _front_half_resumable <state> -> rc 0 when run_item may resume a run in
# *state* (front-half spec §5; shaping spec §5): the eleven states the phase
# loop works in or exits from. Pinned by the self-test, because the shaping
# phase's four were once missing from this list and every resume of a parked
# shaping run was escalated before the loop was called.
_front_half_resumable() {
  case "$1" in
    shaping|slices_submitted|slices_accepted|sliced|queued|spec_submitted|spec_accepted|specified|plan_submitted|plan_accepted|planned) return 0 ;;
    *) return 1 ;;
  esac
}

# _finish_sliced_item <item> <queue-file> <run_id>: the scorecard row for a
# parent whose children are filed, and the queue file's retirement (shaping
# spec §5). The loop is finished with this item; what remains is the sweep's,
# and the sweep reads the journal, never the queue. No implementer, no
# outcome -- the run waits for its children.
_finish_sliced_item() {
  local item="$1" f="$2" run_id="$3" kids
  kids=$(_kernel_sliced_children "$run_id")
  mkdir -p "$(dirname "$SCORECARD")"
  json_row "$item" "" "sliced" "false" "" "" 0 "children filed: ${kids:-none}; the sweep ends run '$run_id' when they close" "n/a" >> "$SCORECARD"
  mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
}

# _interrupted_sliced_item <item> <rc> <run_id>: a non-zero exit AT sliced
# records no outcome (shaping spec §5). A coordinator that died between two
# confirmed filing effects exits this way with nothing pending; the kernel
# would refuse `failed` here in any case (a run that entered sliced ends only
# as sliced), and the queue file stays so the next pass repairs the filing.
_interrupted_sliced_item() {
  local item="$1" rc="$2" run_id="$3"
  echo "[batch] $item: phases exited $rc at sliced; the filing was interrupted and the next pass repairs it" >&2
  mkdir -p "$(dirname "$SCORECARD")"
  json_row "$item" "" "escalated" "false" "" "" 0 "phases rc=$rc at sliced: filing interrupted; run '$run_id' stays open and the next pass repairs it" "n/a" >> "$SCORECARD"
}
```

In `run_item`, the resume gate's `case "$_st" in queued|…|planned) ;; *) … esac` becomes:

```bash
    local _st; _st=$(_kernel_state "$_open")
    if ! _front_half_resumable "$_st"; then
      # THE DAMAGING ONE. A pass that dies after `start_implementation`
      # leaves the run at `implementing`, and every later pass then logs
      # this line and moves on -- forever, with the queue file still there
      # and nothing on the channel a human reads. The row is the handoff.
      echo "[batch] $item: run $_open is at $_st (beyond the front half); skipping" >&2
      mkdir -p "$(dirname "$SCORECARD")"
      json_row "$item" "" "escalated" "false" "" "" 0 "run '$_open' is at '$_st', beyond the front half; this pass drives nothing and the item needs a human" "n/a" >> "$SCORECARD"
      return 0
    fi
```

The mint (`:4362-4371`): capture the refusal —

```bash
    BIRCHER_RUN_ID="${item}-$(date +%s)"; export BIRCHER_RUN_ID
    local _mint_out
    if ! _mint_out=$(_kernel_run_start "$BIRCHER_RUN_ID" "$REPO" "$_base_sha" "$_issue_json" "$_cfg_json" 2>&1); then
      # The sidecar names a run; there is no run. Leaving a stale one behind
      # would tell the next pass -- and the human reading the queue directory
      # -- that an item is waiting on them when nothing is.
      rm -f "$QUEUE/$code.parked"
      mkdir -p "$(dirname "$SCORECARD")"
      # The refusal's own words (shaping spec §5 *The refused mint*): an
      # issue an earlier run sliced says so here, not a bare "see log".
      json_row "$item" "" "failed" "false" "" "" 0 "create_run refused: $(printf '%s' "$_mint_out" | tr '\n' ' ' | cut -c1-200)" "failed" >> "$SCORECARD"
      mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
      return 0
    fi
```

The non-zero branch: before `echo "[batch] $item: phases exited $_prc; recording failed" >&2`:

```bash
      if [ "$(_kernel_state "$BIRCHER_RUN_ID")" = sliced ]; then
        _interrupted_sliced_item "$item" "$_prc" "$BIRCHER_RUN_ID"
        return 0
      fi
```

The post-loop seam: after `esac` and `rm -f "$QUEUE/$code.parked"`, before the `# §6: the implementer is dispatched AFTER phases` comment:

```bash
  # THE SLICED BRANCH (shaping spec §5): a parent whose children are filed
  # exits `phases` 0 at `sliced`. No implementer -- start_implementation is
  # legal only from planned, and taking that path scored a successful
  # slicing as a failed implementation.
  if [ "$(_kernel_state "$BIRCHER_RUN_ID")" = sliced ]; then
    _finish_sliced_item "$item" "$f" "$BIRCHER_RUN_ID"
    return 0
  fi
```

The `issues` source, before `REPO="$REPO" bash "$BUNDLE_DIR/batch/issues-to-queue.sh"`:

```bash
    # The sweep first (shaping spec §3 *The sweep*, §5): every filed epic's
    # children are read and the finished epics ended before the queue is
    # generated, so a parent that ends this wave is not also queued by it.
    if [ -n "${BIRCHER_KERNEL_DB:-}" ] && [ -f "$BIRCHER_KERNEL_DB" ]; then
      PYTHONPATH="$(_kernel_pythonpath)" "${BIRCHER_PY:-python3}" -m coordinator.cli sweep-sliced \
        --db "$BIRCHER_KERNEL_DB" --server "$SERVER" --repo "$REPO" \
        || echo "[batch] sweep-sliced failed; generating the queue anyway" >&2
    fi
```

- [ ] **Step 4: `kernel-client.sh`**

In `_kernel_run_start`'s Python, import and catch the new refusal:

```python
from kernel.effects import NotReplayable
from kernel.enqueue import IssueAlreadySliced, create_run
...
except NotReplayable as exc:
    print(f"create_run NotReplayable: {exc}", file=sys.stderr); sys.exit(1)
except IssueAlreadySliced as exc:
    print(f"create_run refused: {exc}", file=sys.stderr); sys.exit(1)
```

Add after `_kernel_state`:

```bash
# _kernel_sliced_children <run_id> -> the issue numbers of the children the
# run filed (its slice_filed facts), space-separated; empty if none.
_kernel_sliced_children() {  # <run_id>
  local out=""
  out=$( K_RUN_ID="$1" BIRCHER_V2_DIR="$(_kernel_pythonpath)" _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c '
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel import front
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
rid = os.environ["K_RUN_ID"]
filed = front.slices_filed(s, rid, front.epoch(s, rid))
print(" ".join(str(filed[n].payload["issue"]) for n in sorted(filed)))' 2>/dev/null ) || out=""
  printf '%s' "$out"
}
```

- [ ] **Step 5: `issues-to-queue.sh`**

```bash
# unblocked = no OPEN blocked_by dependency. FAILS CLOSED (shaping spec §5):
# a blocker that cannot be read is not "no blocker" -- the links are an
# epic's sequencing now, and a child started because its blocker could not
# be read is the round-4 defect by another door. The next wave reads again.
is_unblocked() {
  local blockers
  blockers=$(gh api "/repos/$REPO/issues/$1/dependencies/blocked_by" \
    --jq '[.[] | select(.state=="open")] | length' 2>/dev/null) || {
    echo "skip #$1 (blockers unreadable; the next wave reads again)" >&2; return 1; }
  [ "${blockers:-0}" -eq 0 ]
}
```

and the journal sweep's Python gains the `sliced` case:

```python
for rid in s.all_run_ids():
    m = re.match(r"^i(\d+)-", rid)
    if not m or s.run_state(rid) in closed:
        continue
    # A parked run, whatever its labels (finding 12); and a sliced run whose
    # filing is not complete (shaping spec §5) -- the crash-anywhere-during-
    # filing case -- so the next pass reaches file_owed. Whether a queue
    # file exists is never the test; the kernel's fact is.
    if front.current_park(s, rid) is not None or (
            s.run_state(rid) == "sliced" and front.filing_complete(s, rid, front.epoch(s, rid)) is None):
        out.append(m.group(1))
```

- [ ] **Step 6: The self-test and the suite; renumber the citations**

Run: `bash batch/run-queue.sh --self-test` — expected `self-test OK` with the four new `OK` lines. Then `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/execution -q` — expected reds in `test_scar_equivalence.py`, `test_routing.py`, `test_routed_contracts.py` or `test_provider_control.py` naming stale line citations. Renumber: read the hunk headers of `git diff batch/run-queue.sh` (`@@ -a,b +c,d @@`: `d - b` lines were inserted at old line `a`), then

```bash
python3 - <<'PY'
import pathlib, re, subprocess
hunks = re.findall(r"^@@ -(\d+),(\d+) \+\d+,(\d+) @@", subprocess.run(
    ["git", "diff", "-U0", "batch/run-queue.sh"], capture_output=True, text=True).stdout, re.M)
shifts = [(int(a), int(d) - int(b)) for a, b, d in hunks]
for p in ("docs/design/effect-site-inventory.md", "docs/design/scar-effect-matrix.md"):
    path = pathlib.Path(p)
    def fix(m):
        n = int(m.group(1))
        return f"| {n + sum(k for at, k in shifts if n > at)} |"
    path.write_text(re.sub(r"\| (\d{3,5}) \|", fix, path.read_text()))
PY
```

then the four execution test files again — green — and the whole suite: **1541 passed, 3 skipped** (no new pytest tests in this task; the self-test carries them).

- [ ] **Step 7: Mutations**

Drop `sliced` from `_front_half_resumable`: the self-test reds at "sliced must resume". Make `_interrupted_sliced_item` move the file: reds at "the queue file must stay". Make `is_unblocked` `echo 0` on failure again: reds at "must fail closed". Revert.

- [ ] **Step 8: Commit**

```bash
git add batch docs/design/effect-site-inventory.md docs/design/scar-effect-matrix.md
git commit -m "feat(runner): resume and score the shaping states, queue an unfinished filing, sweep before generating, fail closed on an unreadable blocker"
```

## Part D — Proof and documentation

### Task 11: The proof — four assertions over a parent and its children, and the amended existing checks

**Files:**
- Modify: `v2/tools/prove_front_half.py:54-210` (`assert_journal`), append `assert_slices`, `main` (`--repo`, `--children`)
- Modify: `v2/tests/kernel/test_prove_front_half.py` (remove the Task 3 `xfail`; extend)
- Test: `v2/tests/kernel/test_prove_slices.py`

**Interfaces:**
- Consumes: `front.accepted_plan`, `slices_filed`, `shape_ruling`, `satisfied_effects` (Tasks 3–4); `slices.render_child`, `umbrella_body` (Task 1); `sweep.ReadFailed` (Task 9).
- Produces:
  - `assert_journal`: the model-ruling check excludes `question_id == "shape"` and is skipped for a sliced parent (one with a `slice_filed`); the phase-set check reads the final epoch only: a sliced parent submits exactly `{slices}` with the accepted hash, a one-piece run `{spec, plan}` with different hashes and optionally `slices`; the verdict-binding block re-renders `slices` briefs (through `front.FRONT_PHASES`, already extended).
  - `assert_slices(store, run_id, *, repo, gh_json) -> list[str]`: the four assertions of §6.
  - `main(... --repo <repo> --children)`: runs `assert_slices` over the parent and `assert_journal` + `assert_sessions` over each child's newest run (`i<issue>-*`), under the parent's mode.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_prove_slices.py
"""Task 11: the four slice assertions and the amended existing checks
(shaping spec §6)."""
import json

from kernel import front, slices
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front
from tools import prove_front_half as prove

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


class GH:
    """What GitHub holds after a correct filing of *f*: built from the
    journal, then edited by a test to plant a defect."""
    def __init__(self, s, f):
        n = f.epoch()
        plan = front.accepted_plan(s, f.run_id, n)
        h = front.accepted_slices_hash(s, f.run_id, n)
        numbers = {k: x.payload["issue"] for k, x in front.slices_filed(s, f.run_id, n).items()}
        self.issues = {}
        for sl in plan.slices:
            t, b = slices.render_child(sl, 12, h[:8], {d: numbers[d] for d in sl.depends_on})
            self.issues[numbers[sl.number]] = {"title": t, "body": b, "blocked_by": [{"number": numbers[d]} for d in sl.depends_on]}
        self.issues[12] = {"comments": [{"body": slices.umbrella_body(h[:8], plan, numbers)}]}

    def __call__(self, argv):
        n = int(argv[3]) if argv[1] == "issue" else int(argv[2].split("/")[3])
        if argv[1] == "api":
            return self.issues[n]["blocked_by"]
        fields = argv[argv.index("--json") + 1].split(",")
        return {k: self.issues[n].get(k) for k in fields}


def _parent(tmp_path, name="k.db"):
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f.file_all(g)
    return s, f


def test_a_sliced_parent_passes_the_journal_checks_and_the_four_assertions(tmp_path):
    s, f = _parent(tmp_path)
    assert prove.assert_journal(s, f.run_id, mode="zero") == []
    assert prove.assert_slices(s, f.run_id, repo="o/r", gh_json=GH(s, f)) == []


def test_assertion_1_reds_on_a_wrong_body_a_duplicate_create_and_an_unrecorded_create(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[41]["body"] = gh.issues[40]["body"]                               # every child the first slice's scope
    assert any("body" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    s2, f2 = _parent(tmp_path, "dup.db")
    g = f2._dispatch(Role.OPERATOR, "coordinator")
    h = front.accepted_slices_hash(s2, f2.run_id, 0)
    f2._journal(g, "slice:dup", {"argv": ["gh", "issue", "create"], "obligation": {"kind": "slice_issue", "run": f2.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": h}},
                json.dumps({"url": "u", "number": 77, "id": 1077}), cls="issue_create")   # a second delivered create for slice 1
    assert any("delivered creates" in x for x in prove.assert_slices(s2, f2.run_id, repo="o/r", gh_json=GH(s2, f2)))


def test_assertion_2_reds_on_a_missing_and_an_extra_link(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[41]["blocked_by"] = []
    assert any("link" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    gh = GH(s, f)
    gh.issues[40]["blocked_by"] = [{"number": 41}]
    assert any("link" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))


def test_assertion_3_reds_on_two_umbrellas_and_on_a_wrong_list(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[12]["comments"].append(dict(gh.issues[12]["comments"][0]))
    assert any("umbrella" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    gh = GH(s, f)
    gh.issues[12]["comments"][0]["body"] = gh.issues[12]["comments"][0]["body"].replace("#41", "#99")
    assert any("umbrella" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))


def test_assertion_4_passes_a_ruling_after_a_rejected_plan_and_a_superseded_epoch_and_reds_on_a_planted_ruling(tmp_path):
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES); f.review_round("request_revision", findings=b"one piece"); f.shape_round()
    f.author_round(SPEC_BYTES); f.review_round("accept"); f.author_round(PLAN_BYTES); f.review_round("accept")
    assert not [x for x in prove.assert_slices(s, "r-1", repo="o/r", gh_json=None) if "epoch" in x or "decision" in x]
    s2 = Store.open(tmp_path / "b.db")
    f2 = Front(s2, "r-1", shape=False, issue=ISSUE)
    f2.shape_round(SLICES_BYTES)                                                # a plan waits ...
    f2.revise(dict(ISSUE, body="changed"), reshape=False)                       # ... and the issue changes: epoch 1
    f2.to_sliced()
    f2.file_all(f2._dispatch(Role.OPERATOR, "coordinator"))
    assert prove.assert_slices(s2, "r-1", repo="o/r", gh_json=GH(s2, f2)) == []
    # A shape ruling planted beside the filing (the kernel refuses this path; the proof must still see it).
    s2.append_fact(run_id="r-1", kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                   payload={"epoch": 1, "question_id": "shape", "ruling": "one piece", "reasoning": "r", "cost_if_wrong": "c"})
    assert any("decision" in x for x in prove.assert_slices(s2, "r-1", repo="o/r", gh_json=GH(s2, f2)))


def test_the_amended_journal_checks(tmp_path):
    # A one-piece run whose only ruling is the shape one fails the ruling check, as a run with none does.
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE); f.author_round(SPEC_BYTES); f.review_round("accept"); f.author_round(PLAN_BYTES); f.review_round("accept")
    assert any("model_ruling" in x for x in prove.assert_journal(s, "r-1", mode="zero"))
    # {slices, spec, plan} in the final epoch passes the phase-set check; {spec} alone fails it.
    s2 = Store.open(tmp_path / "b.db")
    f2 = Front(s2, "r-1", shape=False, issue=ISSUE)
    f2.shape_round(SLICES_BYTES); f2.review_round("request_revision", findings=b"no"); f2.shape_round()
    f2.ask_round([("Q1", "?")], {"Q1": "yes"}); f2.author_round(SPEC_BYTES); f2.review_round("accept")
    f2.author_round(PLAN_BYTES); f2.review_round("accept")
    assert not [x for x in prove.assert_journal(s2, "r-1", mode="zero") if "artifact_submitted" in x]
    s3 = Store.open(tmp_path / "c.db")
    f3 = Front(s3, "r-1", issue=ISSUE); f3.ask_round([("Q1", "?")], {"Q1": "yes"}); f3.author_round(SPEC_BYTES)
    assert any("artifact_submitted" in x for x in prove.assert_journal(s3, "r-1", mode="zero"))
    # A spec in epoch 1, then sliced in epoch 2, passes; a final epoch holding {slices, spec} fails.
    s4 = Store.open(tmp_path / "d.db")
    f4 = Front(s4, "r-1", issue=ISSUE); f4.author_round(SPEC_BYTES)
    f4.revise(dict(ISSUE, body="changed"), reshape=False); f4.to_sliced(); f4.file_all(f4._dispatch(Role.OPERATOR, "coordinator"))
    assert not [x for x in prove.assert_journal(s4, "r-1", mode="zero") if "artifact_submitted" in x]
    s4.append_fact(run_id="r-1", kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                   payload={"phase": "spec", "epoch": 1, "hash": "x", "author": "claude", "round": 1})
    assert any("artifact_submitted" in x for x in prove.assert_journal(s4, "r-1", mode="zero"))


def test_the_verdict_binding_block_re_renders_a_slices_brief(tmp_path):
    from tests.kernel.test_prove_front_half import _review_round_with_real_brief
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES)
    _review_round_with_real_brief(f)
    f.advance(); f.file_all(f._dispatch(Role.OPERATOR, "coordinator"))
    assert not [x for x in prove.assert_journal(s, "r-1", mode="zero") if "brief" in x]
    b = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    s.put_blob(b.payload["brief_hash"], b"not the brief")            # the planted defect: the bytes moved
    assert any("render()" in x for x in prove.assert_journal(s, "r-1", mode="zero"))
```

(`Store.put_blob` overwrites by content hash in this store; if it refuses, plant the defect by appending a second `review_brief_issued` fact naming a blob of other bytes.)

- [ ] **Step 2: Run to verify failure**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_prove_slices.py -q`
Expected: FAIL — `AttributeError: module 'tools.prove_front_half' has no attribute 'assert_slices'`.

- [ ] **Step 3: Amend `assert_journal`**

Replace the model-ruling and phase-set checks (lines 81-87) with:

```python
    from kernel.slices import SHAPE_QUESTION
    n = front.epoch(store, run_id)
    sliced_parent = bool(front.slices_filed(store, run_id, n))
    # A ruling on a question the AUTHOR raised: the shape ruling every
    # one-piece run carries would satisfy this vacuously (shaping spec §6).
    # A sliced parent is exempt -- its decision is slice_filed.
    if not sliced_parent and not any(
            f.kind == EventKind.MODEL_RULING and f.payload.get("question_id") != SHAPE_QUESTION for f in facts):
        fails.append("no model_ruling: the author did not rule on a question")

    # The FINAL epoch only (shaping spec §6, round 12): a run that submitted
    # a spec before its issue changed and was then sliced is a valid history.
    subs = {f.payload["phase"]: f.payload["hash"]
            for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED) if f.payload.get("epoch") == n}
    if sliced_parent:
        if set(subs) != {"slices"} or subs["slices"] != front.accepted_slices_hash(store, run_id, n):
            fails.append(f"a sliced parent's final epoch submits exactly the accepted slice plan: {subs}")
    elif (not {"spec", "plan"} <= set(subs) or not set(subs) <= {"slices", "spec", "plan"}
          or subs["spec"] == subs["plan"]):
        fails.append(f"artifact_submitted for spec and plan with different hashes (and at most a slice plan): {subs}")
```

- [ ] **Step 4: `assert_slices` and `main`**

Append after `assert_sessions`:

```python
def assert_slices(store, run_id: str, *, repo: str, gh_json) -> list[str]:
    """The four assertions of shaping spec §6 over a parent. `gh_json` is
    the sweep's reader (coordinator.sweep.gh_json) or a fake; it is not
    called for a run that filed nothing (assertion 4 still runs)."""
    import collections
    from kernel import slices
    fails: list[str] = []
    n = front.epoch(store, run_id)
    plan = front.accepted_plan(store, run_id, n)
    h = front.accepted_slices_hash(store, run_id, n)
    filed = front.slices_filed(store, run_id, n)
    parent = front.issue_number(store, run_id)
    numbers = {k: f.payload["issue"] for k, f in filed.items()}
    if plan is not None and filed:
        # 1. The children are the plan, body and all -- and exactly one delivered create per slice.
        if sorted(filed) != [s.number for s in plan.slices]:
            fails.append(f"slice_filed {sorted(filed)} does not match the plan's slices, by number")
        creates = front.satisfied_effects(store, run_id, "slice_issue")
        per = collections.Counter(r["intent"]["obligation"].get("slice") for r in creates)
        for s in plan.slices:
            if per.get(s.number, 0) != 1:
                fails.append(f"slice {s.number}: {per.get(s.number, 0)} delivered creates, want exactly one")
        for k in per:
            if k not in filed:
                fails.append(f"a delivered create for slice {k} has no slice_filed")
        for s in plan.slices:
            if s.number not in filed:
                continue
            live = gh_json(["gh", "issue", "view", str(numbers[s.number]), "--repo", repo, "--json", "title,body"])
            title, body = slices.render_child(s, parent, h[:8], {d: numbers[d] for d in s.depends_on})
            if live.get("title") != title or (live.get("body") or "").rstrip("\n") != body.rstrip("\n"):
                fails.append(f"child #{numbers[s.number]}: title or body is not render_child over slice {s.number}")
        # 2. The dependencies are the links.
        for s in plan.slices:
            if s.number not in filed:
                continue
            links = gh_json(["gh", "api", f"repos/{repo}/issues/{numbers[s.number]}/dependencies/blocked_by"])
            got = sorted(int(x["number"]) for x in links)
            want = sorted(numbers[d] for d in s.depends_on)
            if got != want:
                fails.append(f"child #{numbers[s.number]}: blocked-by links {got} are not the plan's {want} (a missing or extra link)")
        # 3. The umbrella names the children.
        comments = gh_json(["gh", "issue", "view", str(parent), "--repo", repo, "--json", "comments"]).get("comments") or []
        ums = [c for c in comments if (c.get("body") or "").startswith(f"bircher: sliced {h[:8]}")]
        if len(ums) != 1:
            fails.append(f"{len(ums)} umbrella comments for plan {h[:8]}, want exactly one")
        elif ums[0]["body"].rstrip("\n") != slices.umbrella_body(h[:8], plan, numbers).rstrip("\n"):
            fails.append("the umbrella comment does not list exactly the filed children")
    # 4. One decision per epoch, and every filing in the last.
    for f in store.facts_of_kind(run_id, EventKind.SLICE_FILED):
        if f.payload.get("epoch") != n:
            fails.append(f"slice_filed in epoch {f.payload.get('epoch')}, outside the final epoch {n}")
    for e in range(n + 1):
        ruling = front.shape_ruling(store, run_id, e)
        if ruling is None:
            continue
        subs = front.submissions(store, run_id, "slices", e)
        if subs and ruling.seq < subs[-1].seq:
            fails.append(f"epoch {e}: a slice plan was submitted after the shape ruling (one decision per epoch)")
        if front.slices_filed(store, run_id, e):
            fails.append(f"epoch {e}: a shape ruling beside a filing (one decision per epoch)")
    if bool(front.shape_ruling(store, run_id, n)) == bool(filed):
        fails.append(f"final epoch {n}: exactly one decision, a slice_filed or a shape ruling")
    return fails
```

In `main`: add `ap.add_argument("--repo", default="")` and `ap.add_argument("--children", action="store_true", help="prove the parent's slicing and each child's run")`; after computing `fails`:

```python
    if a.children:
        from coordinator.sweep import gh_json
        if not a.repo:
            print("--children needs --repo", file=sys.stderr)
            return 2
        fails += assert_slices(store, a.run_id, repo=a.repo, gh_json=gh_json)
        for f in store.facts_of_kind(a.run_id, EventKind.SLICE_FILED):
            kids = [r for r in store.all_run_ids() if r.startswith(f"i{f.payload['issue']}-")]
            if not kids:
                fails.append(f"child #{f.payload['issue']} has no run yet")
                continue
            fails += [f"child #{f.payload['issue']} ({kids[-1]}): {x}" for x in
                      assert_journal(store, kids[-1], mode=mode) + assert_sessions(store, kids[-1], server=a.server)]
```

- [ ] **Step 5: Run; remove the Task 3 `xfail`**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_prove_slices.py tests/kernel/test_prove_front_half.py -q`, then the suite.
Expected: PASS; **1548 passed, 3 skipped** (7 new; the `xfail(strict=True)` from Task 3 rule (e) now passes and is removed, its test asserting the red on a run whose only ruling is the shape one).

- [ ] **Step 6: Mutations**

Compare bodies by marker line only in assertion 1: `test_assertion_1_reds_on_a_wrong_body…` reds. Count `slice_filed` instead of delivered creates: the duplicate case reds. Read submissions across all epochs in the phase-set check: the spec-then-sliced case reds. Revert.

- [ ] **Step 7: Commit**

```bash
git add v2/tools v2/tests/kernel
git commit -m "feat(proof): four slice assertions over a parent and its children; the journal checks read the final epoch and exclude the shape ruling"
```

### Task 12: Documentation and the spec's amendments

**Files:**
- Modify: `docs/design/ARCHITECTURE.md` (§3.2, §3.3, §4 step 4)
- Modify: `docs/design/effect-site-inventory.md` (a new section)
- Modify: `docs/superpowers/specs/2026-09-09-shaping-phase-design.md` (rulings 1, 2, 3, 12 of this plan)
- Modify: `README.md:72`, `skills/muesli-loop/SKILL.md` (the label vocabulary, if it lists one)

- [ ] **Step 1: `ARCHITECTURE.md`**

In §3.2 (the coordinator), add to the list of what `phases` does: "a shaping round before the spec — a one-piece ruling or a slice plan (`coordinator/shape.py`); the slice plan's review and gate; `file_owed` (`coordinator/filing.py`), which files an accepted plan's children in four idempotent passes; and `sweep_sliced` (`coordinator/sweep.py`), run by each wave before it generates the queue". In §3.3 (the kernel), add: "the shaping states (`shaping`, `slices_submitted`, `slices_accepted`, `sliced`), the slice-plan grammar (`kernel/slices.py`), the `issue_create` effect class, and `kernel/filing.py` — what `perform` refuses of a filing effect: every argv shape the design adds needs its obligation kind, each kind is bound to its argv's targets, and one satisfied effect exists per obligation". In §4 step 4, prefix: "**The shaping phase.** Every run is born in `shaping`: the author rules the issue one piece (the run proceeds to `queued`) or writes a slice plan the other vendor reviews; gated by default, the accepted plan is filed as child issues labelled `bircher:queued` only once their blocked-by links exist, the parent carries `bircher:sliced` as an umbrella, and the sweep closes it when its children close. Spec: `docs/superpowers/specs/2026-09-09-shaping-phase-design.md`." Add `bircher:slice`, `bircher:sliced` wherever the label vocabulary is listed (`README.md:72`, `skills/muesli-loop/SKILL.md` if it lists labels).

- [ ] **Step 2: The effect-site inventory**

Append a section (the routed-count heading and its table are untouched; `test_routed_contracts.py` reads only that heading):

```markdown
## Kernel-executor sites (not shell)

The shaping phase performs its effects from Python through `perform` (`v2/coordinator/filing.py`, `v2/coordinator/sweep.py`), never from `run-queue.sh`, so the routing detector above has no line to cite. They are listed here so the inventory stays the authority on what mutates:

| Where | Call | Effect class | Bound by |
|---|---|---|---|
| `coordinator/filing.py` pass 1 | `gh issue create --repo --title --label … --body-file` | `issue_create` | `kernel/filing.py`: obligation `slice_issue` required; state `sliced`, operator dispatch, the accepted hash, the slice in the plan; title, body (`render_child`) and labels bound; one per obligation |
| `coordinator/filing.py` pass 2 | `gh api …/issues/<n>/dependencies/blocked_by -X POST -f issue_id=` | `issue_or_label` | `slice_dependency`; the child's and the blocker's filed numbers and ids |
| `coordinator/filing.py` pass 3 | `gh issue edit <child> --add-label bircher:queued` | `issue_or_label` | `slice_queue`; refused while any create or link is unsatisfied |
| `coordinator/filing.py` pass 4 | `gh issue comment <parent> --body 'bircher: sliced <hash8>…'` | `comment` | `umbrella`; the rendered body |
| `coordinator/filing.py` pass 4 | `gh issue edit <parent> --add-label bircher:sliced --remove-label bircher:running` | `issue_or_label` | `umbrella_label` |
| `coordinator/sweep.py` | `gh issue comment <parent> --body 'bircher: sliced complete…'` | `comment` | `umbrella_close`; `filing_complete` and a same-generation `children_observed_closed` |
| `coordinator/sweep.py` | `gh issue close <parent>` | `issue_or_label` | `parent_close`; as above, and only when the parent was observed open |
```

- [ ] **Step 3: The spec's amendments**

In `docs/superpowers/specs/2026-09-09-shaping-phase-design.md`, add a final section:

```markdown
## Amendments from the plan (2026-09-09)

Planning found four places where the text and the code it extends could not both hold; each is ruled in `docs/superpowers/plans/2026-09-09-shaping-phase.md` and amended here:

1. §2 *The effect class*: "the body file written by the executor from the intent's `body.text`" reads "from the artefact the intent's `body.artifact` names" — the kernel's intent bodies are `{"artifact": <sha256>}`, and the binding compares that blob with `render_child` (plan ruling 1).
2. §2 *States*, `create_run` → `shaping`: `Store.create_run` takes the initial state; the front half's `enqueue.create_run` passes `shaping`, the v1 `enqueue()` path keeps `queued` (plan ruling 2).
3. §8: the kernel test driver shapes every run one piece by default (`Front(shape=True)`), so tests of later phases reach `queued` by the only path the kernel admits (plan ruling 3).
4. §5 *The refused mint*: the scorecard note carries the refusal's first 200 characters, which read "run i12-… attempted a child create" (plan ruling 12).
```

- [ ] **Step 4: Run and commit**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/execution tests/kernel/test_provenance.py -q` — the inventory's routed-count heading is unchanged and the provenance count sentence was reconciled in Task 4. Expected: PASS.

```bash
git add docs README.md skills
git commit -m "docs: the shaping phase in the architecture, the kernel-executor sites, the spec's amendments from planning"
```

## Part E — Live proof

Nothing in Parts A–D touches GitHub or the runner. Each task below names the human action it needs before it runs; none runs without it.

### Task 13: E8 — the labels, the deployment, and a smoke epic on `abedegno/bircher-smoke`

**Human actions before this task:** (1) create the two labels on both repos — `gh label create bircher:slice --repo abedegno/bircher-smoke`, `gh label create bircher:sliced --repo abedegno/bircher-smoke`, and the same for `abedegno/muesli`; (2) approve shipping the bundle to the runner; (3) file the smoke epic (an issue on `bircher-smoke` labelled `bircher:queued` with no policy label, whose body describes three coherent pieces — a store, an API and a page over it — and names no files); (4) when the run parks at the slice gate, reply `approve` in the carrier session.

- [ ] **Step 1: Ship the bundle**

From the local checkout at the merged plan's HEAD: `git bundle create /tmp/claude-501/nas/fh-shaping.bundle <runner's HEAD>..main`, PUT it to the runner container through Portainer (the docker-archive path used on 2026-09-09: `COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs`), then in the container `cd /workspaces/bircher-v2 && git fetch /workspaces/fh-shaping.bundle main && git merge --ff-only FETCH_HEAD`. Run `bash batch/run-queue.sh --self-test` there: `self-test OK`.

- [ ] **Step 2: The wave**

With the smoke issue queued: `BIRCHER_REPO=abedegno/bircher-smoke BIRCHER_KERNEL_DB=.run/kernel-smoke.db bash batch/launch.sh --source issues --log .run/smoke-shaping.log`. Expected in the journal (`coordinator.cli state`): `shaping` → `slices_submitted` → `slices_accepted`, a `parked {gate, phase: slices}` fact, the notice `bircher: parked gate` on the issue naming the **slices** phase, and the plan in the carrier session under `NOT_FOR_THE_AGENT`. Exit `PARKED`; the scorecard row `parked`.

- [ ] **Step 3: The approval and the filing**

After the human's `approve`, the next scheduled wave (or a hand-launched one): `human_ruling {approve, phase: slices}`, `sliced`, two to five child issues on the smoke repo each carrying `bircher:slice`, `bircher:queued` and no policy label, blocked-by links per the plan, the umbrella comment `bircher: sliced <hash8>` listing them, the parent carrying `bircher:sliced`, `filing_complete` in the journal, the scorecard row `sliced` naming the children. The queue file is in `processed/`.

- [ ] **Step 4: The proof**

`cd v2 && PYTHONPATH=. python3 -m tools.prove_front_half --db ../.run/kernel-smoke.db --run-id <parent run> --server $SERVER --repo abedegno/bircher-smoke --children --expect-approval` → `PROOF: PASS` on the parent's four assertions (the children have no runs yet, so `--children` reports each as "has no run yet": run it without `--children` for the parent's journal, then with `--children` after Step 5 for the assertions). Record the output in `docs/design/front-half-live-log.md` as **E8**.

- [ ] **Step 5: The children and the sweep**

Let the scheduled wave work the unblocked child (the one with `Depends on: none`) through the front half under the default policy — a smoke child's spec gate needs a human `approve`, as E2 did — or close the children by hand (`gh issue close`, the smoke repo is throwaway). Either way, watch the next firing's log for `sweep:` lines: closures recorded (`slice_closed {state: closed}` for hand-closed children), `children_observed_closed {parent: open}`, the comment `bircher: sliced complete` naming each child's final state, the parent closed, `record_run_outcome(sliced)`, the run `ended`. Then a `bircher:queued` re-label of the closed parent on the following wave: the refused mint row `create_run refused: … attempted a child create` and the file in `processed/`. Record both in the live log.

### Task 14: E9 — muesli #12 under the default policy

**Human actions before this task:** (1) cancel #12's earlier run (E6, whose PR #757 was closed unmerged) so a fresh run can be minted: on the runner, `python3 -m coordinator.cli cancel --db .run/kernel-muesli.db --run-id <E6's run id> --server $SERVER`; (2) confirm #12 carries no policy label (the default policy: `gates = {slices, spec}`, `grill: model`); (3) add `bircher:queued` to #12; (4) at the slice gate, read the plan on the issue's notice and in the session, and reply `approve` or give corrections; (5) at each child's spec gate, likewise.

- [ ] **Step 1: The wave and the gate**

The scheduled wave picks #12 up: `shaping` → a slice plan (three to five coarse slices of the team-sharing epic) → the other vendor's review with dispositions → `slices_accepted` → the park and the notice. Read the plan against §1's definition: a slice is a capability a reviewer could see working end to end, not a migration or an endpoint alone. Corrections are the next round's findings; `approve` files the children.

- [ ] **Step 2: The children**

The filed children carry `bircher:queued` and the links; the wave works the unblocked ones first, each through the whole front half and the v1 back half, each merging its own reviewable pull request — the claim, extended (§0). Record each child's outcome in the live log as E9's rows; run `prove_front_half --children --repo abedegno/muesli --expect-approval` over the parent once every child has a run.

- [ ] **Step 3: The umbrella closes**

When the last child merges, the sweep posts `bircher: sliced complete` on #12, closes it, and ends the run `sliced`. Record the firing's log line and the journal's terminal facts. The claim of §0 is met for an epic when this step has happened once.

## Self-review notes

- **Spec coverage.** §0: Task 14. §1: Tasks 1 (`COARSE`, `inherited_labels`), 2 (policy), 10 (preflight labels). §2 *States* and the from-set table: Task 3; *Which state set each site reads*: Task 3 (every row, the runner's in Task 10); *An issue is sliced once*: Task 4; *The artefact* and its refusals: Tasks 1, 3; `record_one_piece`: Task 3; `advance_ungated`: Task 3; the five filing/closing commands: Task 4; *The effect class*, the contract rule, the delivered forms: Task 5; *What `perform` refuses*, *Bound to the effect*, *One satisfied effect per obligation*: Task 5; *Facts*: Tasks 1, 3, 4. §3 *The shaping round*, the ruling grammar, the brief: Tasks 1, 6; *The review round*: Tasks 3 (the brief), 7; *The gate*: Task 7; *Filing the children*: Task 8; the seventh prefix: Task 5; *The sweep*: Task 9. §4: Tasks 3 (the direction guard, the reserved id), 7. §5: Task 10, each of the seven changes and the note. §6: Task 11, four assertions and the amended checks. §7: every row is a test named in Tasks 4, 5, 8, 9, 10 or a live step in 13. §8: the closure map above. §9: Task 4 (`revise_bundle` from `sliced`), Task 12 (the human path in the docs). §10: the rulings are cited where each binds.
- **Placeholders.** None: every step has its code; the migration in Task 3 names its rules and the files each is expected to touch, and says a red outside the rules is a defect.
- **Type consistency.** `front.accepted_slices_hash(store, run_id, epoch_n)` everywhere with the epoch; `front.accepted_plan(store, run_id, epoch_n=None)`; `slices.render_child(s, parent, hash8, filed: dict[int, int]) -> (title, body)`; the obligation shapes of Task 4's table are what `front.filing_obligations` returns, what `file_owed` composes, what `check_filing_effect` reads, and what the driver's `file_slice`/`satisfy` journal; the fact payloads of Task 4 are what `assert_slices` and `_kernel_sliced_children` read; `filing.filed_numbers`/`filed_ids` are the maps every binding and the sweep use; `seat.command` and `perform_effect` are the only write paths in the coordinator's three new modules.
- **Counts.** 1439 → 1452 (T1, +13) → 1452 (T2) → 1477 (T3, +25) → 1490 (T4, +13) → 1504 (T5, +14) → 1515 (T6, +11) → 1522 (T7, +7) → 1533 (T8, +11) → 1541 (T9, +8) → 1541 (T10) → 1548 (T11, +7) → 1548 (T12). A task that lands on a different count explains the difference in its review package.
