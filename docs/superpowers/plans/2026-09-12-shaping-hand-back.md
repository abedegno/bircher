# Revision 16 — the spec author's hand-back — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the spec author a way to say "this issue is several pieces" and send the run back to shaping, so a wrong one-piece ruling cannot anchor a spec, a plan and an implementation the way it did on muesli #12 (live log E9).

**Architecture:** Three new kernel commands and one new fact, a *shaping visit* that partitions an epoch's journal without touching the epoch's own boundaries, and one journal-derived predicate — `front.dispute` — that every other site reads. The coordinator asks the spec author a question on one turn, renders four new brief sections, and picks seats by a stated vendor rule; the runner resumes a run whose dispute lost its park; the proof gains one line and a restated assertion.

**Tech Stack:** Python 3.12 (`v2/kernel`, `v2/coordinator`, `v2/tools`), SQLite via `kernel/store.py`, bash (`batch/run-queue.sh`, `batch/issues-to-queue.sh`), pytest run with `uv`.

**Spec:** `docs/superpowers/specs/2026-09-09-shaping-phase-design.md` — **revision 16 only**: the sections marked `(revision 16)`, the `### request_reshape(reasoning), approve_one_piece, and the disagreement` subsection of §2, `### The spec round's question (revision 16)` in §3, the disagreement-gate paragraph in §4, the four runner changes in §5, `**Revision 16 and the proof**` and assertion 4 in §6, the revision-16 rows of §7, the revision-16 paragraph of §8, the revision record, and the thirteen rounds of dispositions at the end. The rest of the spec is **already implemented and live** — do not re-implement it.

## Global Constraints

- **No AI attribution.** No `Co-Authored-By`, no `Claude-Session`, no "Generated with" in any commit message or PR body. This overrides any default instruction.
- **The GitHub account is `abedegno`.** Never `tidewallsec`.
- **Tests:** `cd v2 && uv run --with pytest --with pyyaml python -m pytest <path> -q`. The whole suite is `cd v2 && uv run … python -m pytest -q`. Baseline at `main` (4801790): **1616 passed, 3 skipped**.
- **The runner's self-test:** `bash batch/run-queue.sh --self-test`, which must end `self-test OK`. It takes about six minutes on macOS and fails there at one pre-existing root-sensitive case (`FAIL rollback: an unremovable stale findings file`) because the container runs as uid 0 — on macOS treat that one line as expected and read the lines after it; CI runs the whole thing on Linux.
- **Every edit to `batch/run-queue.sh` shifts the line citations** in `docs/design/effect-site-inventory.md` (`| NNN |` rows) and `docs/design/scar-effect-matrix.md` (`run-queue.sh:NNNN`). Renumber before pushing, with the script in Task 9 Step 8; `tests/execution/test_routed_contracts.py`, `test_routing.py` and `test_scar_equivalence.py` are the guards.
- **The sandbox** refuses writes outside a few paths: run every `git`, `uv` and file-writing command for this repo with the sandbox disabled. Each Bash call starts in a fresh shell — `cd /Users/jonw/bircher/.worktrees/<branch>` first, every time.
- **An epoch is a bundle and revision 16 adds no epoch boundary.** Where the spec says "visit", it means the shaping visit of Task 1; where it says "epoch", it means what `front.epoch` already returns. Getting this backwards is the defect the review caught four times.
- **`front.dispute` is the only definition of the dispute.** No site re-derives "is there an unresolved disagreement" from its own walk of the facts.

---

## File structure

Eleven tasks over five areas. Nothing is created from nothing except two test files and one parser; everything else is an edit to a named function.

| File | What revision 16 puts there |
|---|---|
| `v2/kernel/front.py` | `shaping_visit`, `visit_of`, `dispute`, `unresolved_disagreement`; `shape_ruling` gains an optional visit |
| `v2/kernel/events.py` | `EventKind.RESHAPE_REQUESTED`, `SCHEMA_VERSIONS["reshape_requested"] = 1` |
| `v2/kernel/commands.py` | `request_reshape` and `approve_one_piece` in `COMMAND_NAMES`; `approve_one_piece` in `HUMAN_COMMANDS`; `_side_fact` writes `reshape_requested`, the approval's `human_ruling`, and the `visit` on the ruling and on a `slices` submission; `record_author_empty` carries `detail`; `issue_review_brief`'s prior findings per visit |
| `v2/kernel/authz.py` | `_TRANSITIONS` rows; `_one_piece_destination`; the refusal tables for both commands; `record_human_answer` out of the shaping states; `PARK_REASONS` gains `disagreement` and `_check_park` bounds it; `OUTPUT_COMMANDS` gains `request_reshape`; `_check_submit`'s dedupe per visit for `slices` |
| `v2/kernel/slices.py` | `parse_reshape` |
| `v2/coordinator/author.py` | `_is_round_cause`'s shape-ruling case (in `phases`), `_findings_for`'s branches, `author_brief`'s four new sections, `choose_author_vendor`'s two rules |
| `v2/coordinator/phases.py` | `ROUND_CAUSE_KINDS`, `_is_round_cause`, `PARK_NEEDS`, `park_notice_body`, `run_loop`'s park recovery and its refused-park catch |
| `v2/coordinator/human.py` | `take_listing`/`classify_batch` skip the grill at shaping and read the dispute; `park_prompt`'s disagreement branch |
| `v2/coordinator/shape.py` | the round counter per visit |
| `v2/coordinator/cli.py` | the `disagreement` subcommand |
| `batch/run-queue.sh`, `batch/issues-to-queue.sh`, `batch/lib/kernel-client.sh` | the three-valued query, the generator's third condition and its union after `is_unblocked`, the sweep's failure line |
| `v2/tools/prove_front_half.py` | assertion 4 in three clauses, the phase-set admission, the approval filter, the fifth line |
| `skills/spec-author/SKILL.md` | the `## Handing back` section, carrying the hand-back grammar with `skills/shape-author/SKILL.md`'s write-whole-then-rename wording, and the prohibition on reading the run's journal, its session history or `.run/`. The shape-author skill is read for its wording and **not changed** |
| `docs/design/provenance-table.md`, `docs/design/2026-08-23-v2-kernel-design.md`, `docs/design/ARCHITECTURE.md` | five provenance amendments, the asserted-row count, gap 17 closed |
| `v2/tests/kernel/test_reshape.py` (new), `v2/tests/coordinator/test_author_brief.py` (new), `v2/tests/coordinator/test_disagreement.py` (new) | the revision's own tests; everything else extends an existing file (`test_vendors.py`, `test_author_round.py`, `test_slices_grammar.py`, `test_store_front.py`, `test_shaping_states.py`, `test_commands.py`, `test_provenance.py`, `tests/execution/test_prove_front_half.py`, `tests/execution/test_queue_generation.py`) |

---

## Shared test fixtures

`v2/tests/kernel/front.py` holds the driver. **Read it before writing any
test in this plan**; its real API is smaller than a reader expects:

| What a test wants | The driver's actual call |
|---|---|
| a run born at `shaping` | `Front(store, run_id, issue=ISSUE, shape=False)` |
| a run at `queued` through a one-piece ruling | `Front(store, run_id, issue=ISSUE)` — `shape=True` is the default and rules it one piece at birth |
| one more one-piece ruling | `f.shape_round(reasoning=…, cost=…)` |
| a slice plan submitted | `f.shape_round(SLICES_BYTES)` — returns the hash |
| a reviewer's verdict | `f.review_round("accept")` / `f.review_round("request_revision", findings=b"…")` |
| the person's gate approval, grant, answer, direction | `f.approve()`, `f.grant()`, `f.answer(text)`, `f.direct(text)` |
| any human command | `f.human(name, payload)` |
| the policy's labels | `Front(..., labels=["bircher:autonomous"])` |

There is no `rule_one_piece`, `submit_slices`, `reject` or `park` method —
`shape_round` and `review_round` are those. Five methods are added by this
plan, each in the task that introduces its command:

| Method | Added in | What it issues |
|---|---|---|
| `request_reshape(reasoning, *, gen=None)` | Task 1 Step 3 | `request_reshape` from a fresh spec-author dispatch whose turn has ended, or from *gen* when the test needs a stale one |
| `approve_one_piece(*, cursor="i-h", as_model=False)` | Task 4 Step 3 | `approve_one_piece` through `self.human(...)`, or through a dispatched generation when *as_model* |
| `park(*, reason, phase="slices")` | Task 4 Step 3 | `park` under a fresh operator dispatch, as `advance()` does |
| `dismiss_human_item(*, cursor="i-h")` | Task 4 Step 3 | `dismiss_human_item` under an operator dispatch, naming the newest `command_rejected` |
| `issue_review_brief(phase)` | Task 5 Step 3 | the brief alone, returning its rendered bytes, so a test can read it without a whole review round |

Task 1 Step 1 creates `v2/tests/kernel/test_reshape.py` with four
module-level fixtures that every later kernel task reuses. They are written
**once**:

```python
def _shaped(tmp_path, name="k.db", labels=("bircher:autonomous",)):
    """A run at `queued` through a one-piece ruling: the state a hand-back
    is legal from. The driver's default `shape=True` rules it at birth."""
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", issue=ISSUE, labels=list(labels))
    assert s.run_state(f.run_id) == "queued"
    return s, f


def _new(tmp_path, name="n.db"):
    """A run at `shaping`, nothing decided."""
    s = Store.open(tmp_path / name)
    return s, Front(s, "i12-epic-1", issue=ISSUE, shape=False)


def _disagreed(tmp_path, name="d.db", labels=("bircher:autonomous",)):
    """The unresolved-disagreement fixture the spec names (§8): a ruling, the
    hand-back, and the disputed ruling. The run is at `shaping`, unparked."""
    s, f = _shaped(tmp_path, name, labels)
    f.request_reshape("the issue names a store, an API and a page")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"
    return s, f


def _rejected_then_disagreed(tmp_path, name="r.db"):
    """A slice plan submitted and rejected in visit 1, then the ruling, the
    hand-back and the disputed ruling. Used where a `slices` submission must
    exist for the approval not to accept it."""
    s, f = _new(tmp_path, name)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"these are steps, not capabilities")
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    return s, f
```

The coordinator tasks (7, 8) use a `fake` fixture built on the same driver
plus the existing coordinator test harness (`v2/tests/coordinator/`
`conftest.py` and its `Ctx` double). Its named constructors —
`fake.ruled_one_piece()`, `fake.handed_back()`, `fake.disagreed()`,
`fake.revision_turn()` and the rest — are thin wrappers over the four
fixtures above plus a dispatched turn; build each one in the task that first
names it, and put them in `v2/tests/coordinator/conftest.py` so both files
share them.

---

## Part A — Kernel

### Task 1: The shaping visit

**Files:**
- Modify: `v2/kernel/front.py` (add `shaping_visit`, `visit_of`; `shape_ruling` gains `visit`)
- Modify: `v2/kernel/commands.py::_side_fact` (the `visit` on `model_ruling` and on a `slices` `artifact_submitted`)
- Test: `v2/tests/kernel/test_reshape.py` (new)

**Interfaces:**
- Consumes: `front.epoch(store, run_id) -> int`, `front.epoch_facts(store, run_id, kind, epoch_n) -> list`, `EventKind.MODEL_RULING`, `EventKind.HUMAN_DIRECTION`, `EventKind.BUNDLE_REVISED`, `kernel.slices.SHAPE_QUESTION`.
- Produces:
  - `front.shaping_visit(store, run_id, epoch_n) -> int` — the current visit, 1-based.
  - `front.visit_of(store, run_id, seq) -> int` — the visit a fact of that `seq` was written in.
  - `front.shape_ruling(store, run_id, epoch_n, visit=None)` — unchanged when `visit is None`; the visit's own ruling otherwise.

A visit partitions **one epoch's journal**. Visit 1 begins with the epoch; a new visit begins at each `reshape_requested` of that epoch, and at each `human_direction` recorded while the epoch's dispute was unresolved **at that point in the prefix**. The prefix rule is what makes a later `bundle_revised` unable to erase a visit, and it is why `visit_of` walks rather than reads a counter.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_reshape.py
"""Revision 16: the shaping visit, the hand-back, the dispute (shaping spec
§2 *request_reshape, approve_one_piece, and the disagreement*)."""
import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import ISSUE, SLICES_BYTES, Front


# The four fixtures of the plan's *Shared test fixtures* section go here,
# written once: _shaped, _new, _disagreed, _rejected_then_disagreed.


def test_visit_one_is_the_epoch(tmp_path):
    s, f = _shaped(tmp_path)
    assert front.shaping_visit(s, f.run_id, front.epoch(s, f.run_id)) == 1


def test_the_hand_back_opens_a_visit_and_a_direction_while_disputed_opens_another(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("the issue names a store, an API and a page")
    assert front.shaping_visit(s, f.run_id, n) == 2
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")   # the disputed ruling
    f.direct("slice it along the three parts")                               # resolves, and opens visit 3
    assert front.shaping_visit(s, f.run_id, n) == 3


def test_a_direction_before_the_disputed_ruling_opens_no_visit(tmp_path):
    """A person typing into the reshaping session is a direction to that
    visit; it resolves nothing and partitions nothing (spec §2 *The dispute*)."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    f.request_reshape("three parts")
    f.direct("consider the migration too")
    assert front.shaping_visit(s, f.run_id, n) == 2


def test_shape_ruling_reads_per_visit_and_the_epoch(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    first = front.shape_ruling(s, f.run_id, n)
    assert first is not None and front.shape_ruling(s, f.run_id, n, visit=1) is first
    assert front.shape_ruling(s, f.run_id, n, visit=2) is None
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
    assert front.shape_ruling(s, f.run_id, n, visit=2) is not None
    # The epoch-wide read is the newest, as `request_reshape`'s refusal needs.
    assert front.shape_ruling(s, f.run_id, n) is not first


def test_every_fact_of_the_phase_carries_its_visit(tmp_path):
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    ruling = front.shape_ruling(s, f.run_id, n)
    assert ruling.payload["visit"] == 1
    f.request_reshape("three parts")
    hb = s.facts_of_kind(f.run_id, EventKind.RESHAPE_REQUESTED)[-1]
    assert hb.payload["visit"] == 2 and hb.payload["epoch"] == n
    f.shape_round(SLICES_BYTES)
    sub = front.submissions(s, f.run_id, "slices", n)[-1]
    assert sub.payload["visit"] == 2


def test_a_fact_written_before_the_revision_is_visit_one(tmp_path):
    """`visit_of` walks the prefix, so a ruling with no `visit` key reads as 1."""
    s, f = _shaped(tmp_path)
    n = front.epoch(s, f.run_id)
    ruling = front.shape_ruling(s, f.run_id, n)
    assert front.visit_of(s, f.run_id, ruling.seq) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q`
Expected: every test errors — `front` has no `shaping_visit`, and `Front` has no `request_reshape`, which Step 3 adds. Every other driver call in the fixtures is one the driver already has.

- [ ] **Step 3: The driver gains `request_reshape`**

In `v2/tests/kernel/front.py`, beside the existing one-piece helper, add — matching the file's own style for a command that needs a fresh author dispatch:

```python
    def request_reshape(self, reasoning: str, *, vendor=None, gen=None):
        """Revision 16: the spec author's hand-back, from `queued`."""
        g = gen if gen is not None else self._author_turn(vendor=vendor, phase="spec")
        return self._cmd(g, "request_reshape", {"reasoning": reasoning})
```

- [ ] **Step 4: Write `shaping_visit` and `visit_of`**

In `v2/kernel/front.py`, after `shape_ruling`:

```python
def _visit_boundaries(store, run_id: str, epoch_n: int) -> list[int]:
    """The `seq` of every fact that opens a shaping visit in this epoch, in
    order: each `reshape_requested`, and each `human_direction` recorded while
    the epoch's dispute was unresolved AT THAT POINT IN THE PREFIX.

    The prefix rule is the whole of it. A direction typed into the reshaping
    session before the disputed ruling exists resolves nothing and opens
    nothing; the direction that answers a disputed ruling opens the next
    visit. Deciding that from the prefix rather than from the journal's
    current state is what keeps a visit from disappearing when a later
    `bundle_revised` changes what "unresolved" means (spec §2)."""
    from kernel.slices import SHAPE_QUESTION
    facts = [f for f in store.facts_for(run_id)
             if f.payload.get("epoch") == epoch_n
             and f.kind in (EventKind.RESHAPE_REQUESTED, EventKind.MODEL_RULING,
                            EventKind.HUMAN_DIRECTION, EventKind.HUMAN_RULING)]
    boundaries, hand_back, disputed, resolved = [], False, False, False
    for f in facts:
        if f.kind == EventKind.RESHAPE_REQUESTED:
            boundaries.append(f.seq)
            hand_back, disputed, resolved = True, False, False
        elif (f.kind == EventKind.MODEL_RULING
              and f.payload.get("question_id") == SHAPE_QUESTION and hand_back and not disputed):
            disputed = True
        elif f.kind == EventKind.HUMAN_DIRECTION and disputed and not resolved:
            boundaries.append(f.seq)
            resolved = True
        elif (f.kind == EventKind.HUMAN_RULING
              and f.payload.get("ruling") == "approve_one_piece" and disputed):
            resolved = True
    return boundaries


def shaping_visit(store, run_id: str, epoch_n: int) -> int:
    """The epoch's current shaping visit, 1-based (spec §2 *Visits, not epochs*)."""
    return len(_visit_boundaries(store, run_id, epoch_n)) + 1


def visit_of(store, run_id: str, seq: int) -> int:
    """The visit a fact was written in, for a fact that carries no `visit`
    (everything written before revision 16). Facts of the phase carry it."""
    from kernel import front as _f  # noqa: F401  (self-reference kept explicit)
    fact = next((f for f in store.facts_for(run_id) if f.seq == seq), None)
    if fact is None:
        return 1
    n = fact.payload.get("epoch")
    if n is None:
        return 1
    return sum(1 for b in _visit_boundaries(store, run_id, n) if b <= seq) + 1
```

And `shape_ruling` gains the optional visit, keeping its epoch-wide answer as the default — `request_reshape`'s refusal and the proof's assertion-4 walk both ask the epoch:

```python
def shape_ruling(store, run_id: str, epoch_n: int, visit: int | None = None):
    """The epoch's newest `model_ruling {question_id: "shape"}`, or the one
    written in `visit` when a visit is named (revision 16), or None."""
    from kernel.slices import SHAPE_QUESTION
    found = None
    for f in epoch_facts(store, run_id, EventKind.MODEL_RULING, epoch_n):
        if f.payload.get("question_id") != SHAPE_QUESTION:
            continue
        if visit is not None and (f.payload.get("visit") or visit_of(store, run_id, f.seq)) != visit:
            continue
        found = f
    return found
```

Note the behaviour change the tests pin: the epoch-wide read now returns the **newest** shape ruling rather than the first. In an epoch with one ruling — every run before this revision — that is the same fact.

- [ ] **Step 5: `_side_fact` stamps the visit**

In `v2/kernel/commands.py::_side_fact`, the `submit_slices` branch and the `record_one_piece` branch each add `visit`:

```python
    if cmd.name in ("submit_spec", "submit_plan", "submit_slices"):
        h = cmd.payload["artifact_hash"]
        rnd = len(front.submissions(store, cmd.run_id, phase, epoch_n)) + 1
        payload = {"phase": phase, "epoch": epoch_n, "hash": h, "author": actor, "round": rnd}
        if phase == "slices":
            # Revision 16: half of "one decision per visit" is the submission,
            # so the submission says which visit it belongs to.
            payload["visit"] = front.shaping_visit(store, cmd.run_id, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor=actor,
            causal_command_id=cmd.idempotency_key, payload=payload,
        )
        store.set_phase_artifact(cmd.run_id, phase, h)
```

and in the `record_one_piece` branch, add `"visit": front.shaping_visit(store, cmd.run_id, epoch_n)` to the `model_ruling` payload it already writes.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q`
Expected: PASS (6 tests). Then the kernel suite: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel -q` — expected PASS; a red in `test_shaping_states.py` over the newest-vs-first ruling is a real regression to fix here, not to carry.

- [ ] **Step 7: Mutation**

Delete the `elif f.kind == EventKind.HUMAN_DIRECTION …` branch of `_visit_boundaries`: `test_the_hand_back_opens_a_visit_and_a_direction_while_disputed_opens_another` must red on `3 != 2`. Restore. Change the `disputed and not resolved` guard to `hand_back and not resolved`: `test_a_direction_before_the_disputed_ruling_opens_no_visit` must red. Restore.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel/front.py v2/kernel/commands.py v2/tests/kernel/front.py v2/tests/kernel/test_reshape.py
git commit -m "feat(kernel): the shaping visit -- a partition of the epoch's journal, counted from the prefix"
```

---

### Task 2: `request_reshape`, the fact, and the registries

**Files:**
- Modify: `v2/kernel/events.py` (`EventKind.RESHAPE_REQUESTED`, `SCHEMA_VERSIONS`)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES`, `_side_fact`'s new branch)
- Modify: `v2/kernel/authz.py` (`_TRANSITIONS`, `OUTPUT_COMMANDS`, the refusal table)
- Modify: `v2/tests/kernel/test_store_front.py`, `v2/tests/kernel/test_slices_grammar.py` (their literal kind dicts)
- Test: `v2/tests/kernel/test_reshape.py` (extend)

**Interfaces:**
- Consumes: Task 1's `front.shaping_visit`, `front.shape_ruling(…, visit=None)`; `authz._check_author_seat`, `authz._check_no_newer_direction`, `authz._non_empty_str`.
- Produces: the command `request_reshape` with payload `{"reasoning": str}`, transition `queued → shaping`, writing `reshape_requested {epoch, visit, reasoning}`.

An **undeclared fact kind is a `KeyError` in `append_fact`, not a refusal** — `store.py`'s `SCHEMA_VERSIONS[kind]` — and nothing in `run_loop` catches it, so the registry entry is not optional. The two tests that enumerate kinds (`test_store_front.py`, `test_slices_grammar.py`) iterate their own literal dicts and will not go red on the omission; they gain the kind for completeness, and the guard that actually covers `append_fact` is `test_store.py`'s test parametrised over `sorted(SCHEMA_VERSIONS)`, which gains a passing case for free.

- [ ] **Step 1: Write the failing tests**

Append to `v2/tests/kernel/test_reshape.py`:

```python
def test_the_hand_back_is_legal_from_queued_and_moves_to_shaping(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("the issue names a store, an API and a page")
    assert s.run_state(f.run_id) == "shaping"
    hb = s.facts_of_kind(f.run_id, EventKind.RESHAPE_REQUESTED)[-1]
    assert hb.payload["reasoning"].startswith("the issue names")


def test_the_hand_back_refuses_its_four_ways(tmp_path):
    s, f = _shaped(tmp_path)
    with pytest.raises(NotAuthorized, match="reasoning"):
        f.request_reshape("   ")
    # Once per epoch.
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    f.approve_one_piece()
    with pytest.raises(NotAuthorized, match="already"):
        f.request_reshape("again")
    # A run that was never shaped has nothing to hand back to. Unreachable
    # through today's transitions -- `record_one_piece` is the only way out
    # of `shaping` -- so the state is set directly, exactly as a v1 database
    # written before the shaping phase existed holds it.
    s2 = Store.open(tmp_path / "k2.db")
    g = Front(s2, "i13-v1-1", issue=ISSUE, shape=False)
    s2.set_run_state(g.run_id, "queued")
    with pytest.raises(NotAuthorized, match="no shape ruling"):
        g.request_reshape("epic")


def test_the_hand_back_carries_the_direction_guard(tmp_path):
    """Every author-turn-ending command refuses over a direction newer than
    its own dispatch (spec §2)."""
    s, f = _shaped(tmp_path)
    gen = f._author_turn(phase="spec")
    f.direct("do it this way instead")
    with pytest.raises(NotAuthorized, match="direction"):
        f.request_reshape("three parts", gen=gen)


def test_the_kind_is_declared(tmp_path):
    from kernel.events import SCHEMA_VERSIONS
    assert SCHEMA_VERSIONS[EventKind.RESHAPE_REQUESTED] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q -k "hand_back or kind_is_declared"`
Expected: FAIL — `request_reshape` is not in `COMMAND_NAMES`, and `EventKind` has no `RESHAPE_REQUESTED`. `test_the_hand_back_refuses_its_four_ways` also calls `approve_one_piece`, which Task 4 adds: expect that one to error until then, and say so in the report rather than weakening it.

- [ ] **Step 3: The registries**

`v2/kernel/events.py`, beside `MODEL_RULING`:

```python
    #: Revision 16: the spec author hands a wrongly one-piece run back to
    #: shaping. Its `visit` is the one it opens.
    RESHAPE_REQUESTED = "reshape_requested"
```

and in `SCHEMA_VERSIONS`, `EventKind.RESHAPE_REQUESTED: 1`.

`v2/kernel/commands.py`, in `COMMAND_NAMES`, add `"request_reshape"` and `"approve_one_piece"` (Task 4 uses the second). Leave the human sets alone here — Task 4 adds `approve_one_piece` to `HUMAN_COMMANDS` and `HUMAN_EXECUTABLE`, and `request_reshape` joins neither: it is an author's command.

The second refusal row needs no driver helper: `store.set_run_state` puts a run at `queued` with no ruling, which is the v1 shape and is unreachable through today's transitions.

`v2/kernel/authz.py`, in `OUTPUT_COMMANDS`, add `"request_reshape"`: it ends an author turn, so it carries the observed-turn-and-stop check every other output command carries.

- [ ] **Step 4: The transition and the refusals**

`_TRANSITIONS` gains, beside `record_one_piece`:

```python
    # Revision 16: the spec author's hand-back. From `queued`, the spec
    # author's own state, back to `shaping` -- a new VISIT of the same epoch,
    # never a new epoch.
    "request_reshape": (frozenset({"queued"}), "shaping"),
```

and in `authorize`, beside the `record_one_piece` branch:

```python
    if cmd.name == "request_reshape":
        from kernel import front
        # A literal read: the provenance extractor matches this syntactically,
        # and the row it adds to the asserted set is stated in the spec's
        # site table (revision 16).
        if not _non_empty_str(cmd.payload.get("reasoning")):
            raise NotAuthorized("request_reshape carries a non-empty reasoning")
        _check_author_seat(store, cmd, current)
        _check_no_newer_direction(store, cmd)
        n = front.epoch(store, cmd.run_id)
        if front.shape_ruling(store, cmd.run_id, n) is None:
            raise NotAuthorized(
                f"request_reshape: epoch {n} holds no shape ruling; there is nothing to hand back to "
                "(a v1 run born in `queued` was never shaped)"
            )
        if front.epoch_facts(store, cmd.run_id, EventKind.RESHAPE_REQUESTED, n):
            raise NotAuthorized(
                f"request_reshape: epoch {n} already holds a hand-back; one per bundle, and a second "
                "disagreement over the same input is the person's (shaping spec §2)"
            )
        return next_state
```

The order matters: the reasoning check first (it is the cheapest and the one a malformed file trips), then the seat and the direction guard every author command shares, then the two journal reads.

- [ ] **Step 5: The fact**

`_side_fact` gains a branch:

```python
    if cmd.name == "request_reshape":
        # The visit this fact OPENS: `shaping_visit` counts boundaries, and
        # this fact is about to become one, so the visit it opens is the
        # current count plus one.
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.RESHAPE_REQUESTED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"epoch": epoch_n,
                     "visit": front.shaping_visit(store, cmd.run_id, epoch_n) + 1,
                     "reasoning": cmd.payload["reasoning"]},
        )
```

- [ ] **Step 6: The enumerating tests**

Add `reshape_requested` to `NEW_KINDS` in `v2/tests/kernel/test_store_front.py` and to the literal dict in `v2/tests/kernel/test_slices_grammar.py`. Neither would have gone red without it; they are inventories, and an inventory that silently omits a kind is the thing the spec's site table exists to prevent.

- [ ] **Step 7: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel -q`
Expected: PASS, with the two tests that need Task 4's `approve_one_piece` still failing; note them in the report and leave them.

- [ ] **Step 8: Mutation**

Remove `EventKind.RESHAPE_REQUESTED: 1` from `SCHEMA_VERSIONS`: `test_the_kind_is_declared` reds, and `test_the_hand_back_is_legal_from_queued_and_moves_to_shaping` reds with `KeyError` rather than a refusal — which is the point of the registry row. Restore.

- [ ] **Step 9: Commit**

```bash
git add v2/kernel/events.py v2/kernel/commands.py v2/kernel/authz.py v2/tests/kernel/
git commit -m "feat(kernel): request_reshape -- the spec author hands a wrongly one-piece run back to shaping"
```

---

### Task 3: The dispute, and the destination computed from the journal

**Files:**
- Modify: `v2/kernel/front.py` (`dispute`, `unresolved_disagreement`)
- Modify: `v2/kernel/authz.py` (`_one_piece_destination`; `record_human_answer` out of the shaping states; `submit_slices` and `grant_round` refused while unresolved)
- Test: `v2/tests/kernel/test_reshape.py` (extend)

**Interfaces:**
- Consumes: Task 1's visit functions, Task 2's `RESHAPE_REQUESTED`.
- Produces:
  - `front.dispute(store, run_id, epoch_n=None) -> Dispute | None` — a `NamedTuple` of `(handed_back, hand_back, disputed, resolution)`, each a `Fact` or `None`; `None` when the epoch holds no hand-back.
  - `front.unresolved_disagreement(store, run_id, epoch_n=None) -> bool` — its boolean.
  - `authz._one_piece_destination(store, run_id) -> str`.

**One definition, read everywhere.** Every other site in this plan — the destination, three refusals, the coordinator's classifier and notices, the runner, the generator and the proof — calls `front.dispute` or its boolean. A site that re-derives it is a defect, and the review found three different derivations before this was written down.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_dispute_names_four_facts_in_order(tmp_path):
    s, f = _shaped(tmp_path)
    assert front.dispute(s, f.run_id) is None          # no hand-back yet
    f.request_reshape("three parts")
    d = front.dispute(s, f.run_id)
    assert d.handed_back is not None and d.hand_back is not None
    assert d.disputed is None and d.resolution is None
    assert front.unresolved_disagreement(s, f.run_id) is False   # no disputed ruling yet
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    d = front.dispute(s, f.run_id)
    assert d.disputed is not None and d.resolution is None
    assert front.unresolved_disagreement(s, f.run_id) is True
    f.direct("slice it")
    assert front.unresolved_disagreement(s, f.run_id) is False


def test_a_direction_before_the_disputed_ruling_is_not_the_resolution(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.direct("consider the migration")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert front.unresolved_disagreement(s, f.run_id) is True


def test_the_first_ruling_proceeds_and_the_disputed_one_stays(tmp_path):
    s, f = _shaped(tmp_path)                 # the first ruling already moved it to `queued`
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "shaping"          # the disputed ruling stays, and parks
    f.direct("slice it")
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    assert s.run_state(f.run_id) == "queued"           # the person answered; this one proceeds


def test_the_disputed_ruling_stays_at_shaping_whatever_the_gates(tmp_path):
    """The gate was never the policy's -- it is the disagreement's. Under
    `bircher:autonomous`, the one policy that merges unattended, the run
    still waits for a person (spec §2, the revision record's ruling)."""
    for labels in ([], ["bircher:autonomous"], ["bircher:gate-plan"]):
        s, f = _shaped(tmp_path, f"g{len(labels)}{labels}.db".replace("/", ""), labels=labels)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one", cost="the spec would find one surface")
        assert s.run_state(f.run_id) == "shaping"


def test_while_unresolved_three_commands_are_refused(tmp_path):
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")
    with pytest.raises(NotAuthorized):
        f.shape_round(SLICES_BYTES)
    with pytest.raises(NotAuthorized):
        f.grant()


def test_a_human_answer_is_refused_from_every_shaping_state(tmp_path):
    """Not only while a dispute is unresolved: the grill is epoch-scoped and
    can stand open while a hand-back's visit runs, and an answer recorded at
    `shaping` would be dropped from the shaper's brief and delivered later to
    the spec author as the reply to its question (spec §2)."""
    s, f = _shaped(tmp_path)
    f.request_reshape("three parts")
    assert s.run_state(f.run_id) == "shaping"
    with pytest.raises(NotAuthorized, match="shaping"):
        f.answer("slice it along these lines")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q -k "dispute or refused or proceeds or answer"`
Expected: FAIL — `front` has no `dispute`.

- [ ] **Step 3: Write `dispute` and its boolean**

In `v2/kernel/front.py`, after the visit functions:

```python
class Dispute(NamedTuple):
    """The four facts of a shape dispute, in journal order (spec §2 *The
    dispute*). Any of the last three may be None; `handed_back` and
    `hand_back` are present whenever a Dispute is returned at all."""
    handed_back: object          # the newest `shape` ruling BEFORE the hand-back
    hand_back: object            # the epoch's `reshape_requested`
    disputed: object | None      # the first `shape` ruling AFTER it
    resolution: object | None    # the first approve_one_piece or direction at `shaping` after that


def dispute(store, run_id: str, epoch_n: int | None = None) -> Dispute | None:
    """The epoch's dispute, or None when it holds no hand-back. The current
    epoch by default; any epoch for the proof, which walks them all.

    This is the ONE definition. The destination, the refusals, the
    coordinator's classifier and notices, the runner, the queue generator and
    the proof's fifth line all read it; a second derivation is how the review
    found three incompatible answers to "is this dispute resolved"."""
    from kernel.slices import SHAPE_QUESTION
    n = epoch(store, run_id) if epoch_n is None else epoch_n
    facts = [f for f in store.facts_for(run_id) if f.payload.get("epoch") == n]
    hand_back = next((f for f in facts if f.kind == EventKind.RESHAPE_REQUESTED), None)
    if hand_back is None:
        return None
    rulings = [f for f in facts if f.kind == EventKind.MODEL_RULING
               and f.payload.get("question_id") == SHAPE_QUESTION]
    handed_back = next((f for f in reversed(rulings) if f.seq < hand_back.seq), None)
    disputed = next((f for f in rulings if f.seq > hand_back.seq), None)
    resolution = None
    if disputed is not None:
        resolution = next(
            (f for f in facts
             if f.seq > disputed.seq
             and ((f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "approve_one_piece")
                  or (f.kind == EventKind.HUMAN_DIRECTION and f.payload.get("state") == "shaping"))),
            None)
    return Dispute(handed_back, hand_back, disputed, resolution)


def unresolved_disagreement(store, run_id: str, epoch_n: int | None = None) -> bool:
    """`dispute`'s boolean: a hand-back, a ruling after it, no resolution."""
    d = dispute(store, run_id, epoch_n)
    return d is not None and d.disputed is not None and d.resolution is None
```

`epoch` is this module's own function, and the parameter is `epoch_n`, so nothing is shadowed.

The resolution test reads `f.payload.get("state") == "shaping"`, and the direction fact does not carry a state today — `commands.py::_side_fact`'s `HUMAN_DIRECTION` branch writes `{phase, epoch, text, cursor_item_id}`. Add it:

```python
            payload={"phase": phase, "epoch": epoch_n, "text": cmd.payload["text"],
                     # Revision 16: the state the direction was recorded AT.
                     # `phase` cannot stand in -- all three shaping states
                     # share the phase `slices`, and only a direction at
                     # `shaping` resolves a dispute.
                     "state": store.run_state(cmd.run_id),
                     "cursor_item_id": cmd.payload.get("cursor_item_id")},
```

`record_human_direction` does not transition, so the state read here is the one the direction landed at. Add a test that the payload carries it, and add `state` to whatever enumerates the direction fact's keys.

- [ ] **Step 4: The destination**

In `v2/kernel/authz.py`, beside `_review_destination`:

```python
def _one_piece_destination(store, run_id: str) -> str:
    """Where a one-piece ruling lands, computed from the journal as
    `_review_destination` computes the reviewer's (revision 16).

    `shaping` when the epoch holds a hand-back and no shape ruling after it
    yet -- this ruling is the disputed one, and the loop parks it for the
    person. `queued` otherwise: the first ruling of an epoch, and a ruling
    recorded after the person's resolution, both proceed."""
    from kernel import front
    d = front.dispute(store, run_id)
    return "shaping" if (d is not None and d.disputed is None) else "queued"
```

`_TRANSITIONS`'s `record_one_piece` row keeps `"queued"` as its declared destination — the table is a static map and several readers walk it — and `authorize` overrides it for this command, exactly as it already does for `record_review` via `_review_destination`:

```python
    if cmd.name == "record_one_piece":
        …                                   # the existing checks, unchanged
        if front.shape_ruling(store, cmd.run_id, n,
                              visit=front.shaping_visit(store, cmd.run_id, n)) is not None:
            raise NotAuthorized(f"a shape ruling already exists in visit "
                                f"{front.shaping_visit(store, cmd.run_id, n)} of epoch {n}: "
                                "one decision per visit (shaping spec §2)")
        return _one_piece_destination(store, cmd.run_id)
```

Note the guard moved from the epoch to the **visit** — that is Task 1's promise, and without it the disputed ruling is refused as a duplicate.

- [ ] **Step 5: The three refusals**

In `_TRANSITIONS`, `record_human_answer`'s from-set drops the shaping states:

```python
    # Revision 16: `FRONT_HALF_STATES` only. No shaping seat asks a question,
    # the grill is epoch-scoped and can stand open while a hand-back's visit
    # runs, and an answer recorded at `shaping` would be dropped from the
    # shaper's brief and delivered later to the spec author as its reply.
    "record_human_answer": (FRONT_HALF_STATES, None),
```

and in `authorize`, `submit_slices` and `grant_round` gain the dispute check:

```python
    if cmd.name in ("submit_slices", "grant_round") and current == "shaping":
        from kernel import front
        if front.unresolved_disagreement(store, cmd.run_id):
            raise NotAuthorized(
                f"{cmd.name}: the shape is disputed and waits for the person; a third seat cannot "
                "slice it away and a retry cannot consume the park (shaping spec §2)"
            )
```

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel -q`
Expected: PASS, but for the `approve_one_piece` tests, which Task 4 closes.

- [ ] **Step 7: Mutations**

`_one_piece_destination` returning `"queued"` unconditionally: `test_the_first_ruling_proceeds_and_the_disputed_one_stays` reds on the middle assertion. `dispute`'s `resolution` search dropping its `f.seq > disputed.seq` bound: `test_a_direction_before_the_disputed_ruling_is_not_the_resolution` reds. Restore both.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel/front.py v2/kernel/authz.py v2/kernel/commands.py v2/tests/kernel/test_reshape.py
git commit -m "feat(kernel): front.dispute -- one definition of the shape dispute, and the destination computed from it"
```

---

### Task 4: `approve_one_piece` and the `disagreement` park

**Files:**
- Modify: `v2/kernel/authz.py` (`PARK_REASONS`, `_TRANSITIONS`, `_check_park`, `authorize`)
- Modify: `v2/kernel/commands.py` (`_side_fact`'s human-ruling branch)
- Test: `v2/tests/kernel/test_reshape.py`, `v2/tests/kernel/test_shaping_states.py`

**Interfaces:**
- Consumes: `front.dispute`, `front.unresolved_disagreement`, `front.shaping_visit` (Tasks 1 and 3); `commands.HUMAN_COMMANDS` and `COMMAND_NAMES` (Task 2 added the name).
- Produces: the human command `approve_one_piece` with payload `{"cursor_item_id": str}`, transition `shaping → queued`, writing `human_ruling {ruling: "approve_one_piece", phase: "slices", epoch, visit}`; the park reason `disagreement`.

The ruling word is `approve_one_piece` and **not** `approve`, so that `front.accepted_slices_hash`'s filter on `ruling == "approve"` never sees it — the approval approves a ruling, not an artefact, and a slice plan the epoch happens to hold must not become accepted by it.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_approval_resolves_and_moves_to_queued(tmp_path):
    s, f = _disagreed(tmp_path)            # ruling -> hand-back -> disputed ruling
    assert s.run_state(f.run_id) == "shaping"
    f.approve_one_piece()
    assert s.run_state(f.run_id) == "queued"
    assert front.unresolved_disagreement(s, f.run_id) is False
    r = s.facts_of_kind(f.run_id, EventKind.HUMAN_RULING)[-1]
    assert r.payload["ruling"] == "approve_one_piece"
    assert r.payload["phase"] == "slices"
    assert r.payload["epoch"] == 0 and r.payload["visit"] == 2


def test_the_approval_refuses_its_four_ways(tmp_path):
    s, f = _disagreed(tmp_path)
    with pytest.raises(NotAuthorized, match="cursor"):
        f.approve_one_piece(cursor=None)
    with pytest.raises(NotAuthorized, match="human"):
        f.approve_one_piece(as_model=True)
    f.approve_one_piece()
    with pytest.raises(NotAuthorized, match="unresolved"):   # a second approval
        f.approve_one_piece()
    s2, g = _shaped(tmp_path, "b.db")                         # no dispute at all
    with pytest.raises(NotAuthorized, match="unresolved"):
        g.approve_one_piece()


def test_the_approval_does_not_accept_a_slice_plan(tmp_path):
    """`accepted_slices_hash` filters on ruling == 'approve'; the approval's
    own word keeps it out of that filter."""
    s, f = _rejected_then_disagreed(tmp_path)   # a slices submission exists, rejected
    f.approve_one_piece()
    assert front.accepted_slices_hash(s, f.run_id, 0) is None


def test_the_disagreement_park_is_bounded_to_an_unresolved_dispute(tmp_path):
    s, f = _disagreed(tmp_path)
    f.park(reason="disagreement", phase="slices")            # legal
    assert front.current_park(s, f.run_id).payload["reason"] == "disagreement"
    s2, g = _shaped(tmp_path, "c.db")
    g.request_reshape("three parts")                          # hand-back, no disputed ruling
    with pytest.raises(NotAuthorized, match="disagreement"):
        g.park(reason="disagreement", phase="slices")


def test_dismiss_human_item_leaves_the_dispute_standing(tmp_path):
    """A refused token is dismissed so the cursor moves past it; the
    dismissal is not a resolution."""
    s, f = _disagreed(tmp_path)
    f.dismiss_human_item()
    assert front.unresolved_disagreement(s, f.run_id) is True
    assert s.run_state(f.run_id) == "shaping"


def test_the_park_is_refused_when_the_dispute_resolved_in_the_gap(tmp_path):
    """The window `run_loop` catches: a person's approval lands between
    `stall`'s listing and its `park`."""
    s, f = _disagreed(tmp_path)
    f.approve_one_piece()
    with pytest.raises(NotAuthorized, match="disagreement"):
        f.park(reason="disagreement", phase="slices")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q -k "approval or disagreement_park or resolved_in_the_gap"`
Expected: FAIL — `approve_one_piece` has no transition, and `disagreement` is not a park reason.

- [ ] **Step 3: The registries and the transition**

`v2/kernel/authz.py`:

```python
PARK_REASONS = frozenset({
    "grill", "no_verdict", "bound_exhausted", "gate", "budget_exhausted",
    "identical_resubmission",
    # Revision 16: the two seats disagree on the shape and a person decides.
    # Bounded in `_check_park` to a run whose dispute is unresolved, so the
    # notice and the prompt can key on it and no ordinary gate park carries
    # its text.
    "disagreement",
})
```

and in `_TRANSITIONS`, beside the other human rulings:

```python
    # Revision 16: the person's resolution of a shape disagreement. Only from
    # `shaping` -- at any later state a reply is read as it is today, so a
    # stale dispute can never brick a spec or plan gate.
    "approve_one_piece": (frozenset({"shaping"}), "queued"),
```

`v2/kernel/commands.py`: `HUMAN_COMMANDS` and `HUMAN_EXECUTABLE` both gain `"approve_one_piece"` (Task 2 added it to `COMMAND_NAMES`).

- [ ] **Step 4: The authorization**

In `authorize`, beside `approve_artifact`:

```python
    if cmd.name == "approve_one_piece":
        from kernel import front
        if actor_for(store, cmd) != "human":
            raise NotAuthorized("approve_one_piece is the person's decision: no dispatched generation reaches it")
        if not isinstance(cmd.payload.get("cursor_item_id"), str):
            raise NotAuthorized(
                "approve_one_piece carries the reply's cursor_item_id: it is the one check the kernel can make, "
                "and stricter than the other human commands, which tolerate its absence"
            )
        if not front.unresolved_disagreement(store, cmd.run_id):
            raise NotAuthorized(
                "approve_one_piece: this epoch holds no unresolved disagreement -- no hand-back, no disputed "
                "ruling yet, or a resolution already recorded (a second approval is this refusal)"
            )
        return next_state
```

`actor_for` is the helper `_check_submit` already uses; the human path reaches `authorize` with the actor `human` because `execute_as_human` sets it, and no dispatched generation can.

- [ ] **Step 5: The fact**

In `_side_fact`, beside the other human rulings:

```python
    if cmd.name == "approve_one_piece":
        d = front.dispute(store, cmd.run_id, epoch_n)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.HUMAN_RULING, actor=actor,
            causal_command_id=cmd.idempotency_key,
            # `approve_one_piece`, never `approve`: `accepted_slices_hash`
            # filters on the word `approve`, and a slice plan the epoch
            # happens to hold must not become the accepted one because a
            # person resolved a ruling.
            payload={"ruling": "approve_one_piece", "phase": "slices", "epoch": epoch_n,
                     "visit": front.visit_of(store, cmd.run_id, d.disputed.seq)},
        )
```

The visit is the **disputed ruling's**, read through `visit_of`, not the current count: the approval opens no visit, and stamping the count would attribute the resolution to a visit that does not exist.

- [ ] **Step 6: The park bound**

In `_check_park`, after the reason check:

```python
    if cmd.payload.get("reason") == "disagreement":
        from kernel import front
        # The first park whose acceptance depends on journal state beyond its
        # from-set. A person's command landing between `stall`'s listing and
        # this park resolves the dispute, and the park would then tell them
        # to decide something they already decided. `run_loop` catches this
        # refusal, re-reads the dispute and continues the pass.
        if not front.unresolved_disagreement(store, cmd.run_id):
            raise NotAuthorized(
                "park disagreement: the run holds no unresolved disagreement; the dispute was resolved "
                "between the listing and this park, and the pass continues without parking"
            )
```

- [ ] **Step 7: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel -q`
Expected: PASS, including Task 2's and Task 3's tests that were waiting on `approve_one_piece`. `tests/kernel/test_shaping_states.py`'s state-table test and `tests/kernel/test_commands.py`'s name inventory will need the two new commands added; do that here.

- [ ] **Step 8: Mutations**

Write `"ruling": "approve"` in the fact: `test_the_approval_does_not_accept_a_slice_plan` reds. Drop the `disagreement` branch from `_check_park`: both park tests red. Restore both.

- [ ] **Step 9: Commit**

```bash
git add v2/kernel/ v2/tests/kernel/
git commit -m "feat(kernel): approve_one_piece and the disagreement park"
```

---

### Task 5: The visit-scoped readers

**Files:**
- Modify: `v2/kernel/front.py` (`submissions`, `newest_review_verdict` — a `visit` keyword)
- Modify: `v2/kernel/authz.py` (`_check_submit`'s identical-resubmission guard)
- Modify: `v2/kernel/commands.py` (`issue_review_brief`'s prior findings)
- Test: `v2/tests/kernel/test_reshape.py`

**Interfaces:**
- Consumes: `front.shaping_visit`, `front.visit_of` (Task 1).
- Produces: `front.submissions(store, run_id, phase, epoch_n, visit=None)`, `front.newest_submission(store, run_id, phase, epoch_n, visit=None)` and `front.newest_review_verdict(store, run_id, phase, epoch_n, visit=None)` — the visit filter applied only when `visit` is passed, so every existing caller keeps its meaning.

The spec's site table names eight readers that move from the epoch to the visit. Three are the kernel's and belong here; Task 1 did `shape_ruling` and the one-ruling guard, Task 7 does `author_brief`'s previous draft, Task 8 does `choose_author_vendor` and `shape_round`'s counter, and Task 10 does the proof's assertion 4. **Only phase `slices` is visit-scoped.** A spec or plan submission is never filtered by visit: visits partition the shaping work, and applying the filter to every phase would hide a spec draft behind a hand-back.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_plan_an_earlier_visit_rejected_may_be_resubmitted(tmp_path):
    s, f = _new(tmp_path)
    f.shape_round(PLAN_BYTES)
    f.review_round("request_revision")                                   # request_revision, back to shaping
    f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
    f.request_reshape("three parts")
    # Visit 2: the same bytes are a fresh submission, not the loop spinning.
    f.shape_round(PLAN_BYTES)
    assert s.run_state(f.run_id) == "slices_submitted"
    subs = front.submissions(s, f.run_id, "slices", 0)
    assert [x.payload["visit"] for x in subs] == [1, 2]
    assert front.submissions(s, f.run_id, "slices", 0, visit=2) == subs[-1:]


def test_the_same_plan_twice_in_one_visit_is_still_refused(tmp_path):
    s, f = _new(tmp_path)
    f.shape_round(PLAN_BYTES)
    f.review_round("request_revision")
    with pytest.raises(NotAuthorized, match="identical"):
        f.shape_round(PLAN_BYTES)


def test_the_round_budget_is_per_epoch_and_a_hand_back_spends_none(tmp_path):
    """`max_rounds` counts the reviewer's request_revision facts. A hand-back
    spends none, and the visit it opens draws on the same budget (spec §2)."""
    s, f = _new(tmp_path)
    before = front.rounds_used(s, f.run_id, "slices", 0)
    f.shape_round(reasoning="one piece", cost="the spec would find one surface")
    f.request_reshape("three parts")
    assert front.rounds_used(s, f.run_id, "slices", 0) == before
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision")
    assert front.rounds_used(s, f.run_id, "slices", 0) == before + 1


def test_the_review_brief_carries_the_visits_own_prior_findings(tmp_path):
    s, f = _new(tmp_path)
    f.shape_round(PLAN_BYTES)
    f.review_round("request_revision", findings=b"visit one findings")
    f.shape_round(reasoning="one piece", cost="the spec would find one surface")
    f.request_reshape("three parts")
    f.shape_round(PLAN_BYTES)
    brief = f.issue_review_brief("slices")
    assert b"visit one findings" not in brief
    assert front.newest_review_verdict(s, f.run_id, "slices", 0, visit=2) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_reshape.py -q -k "earlier_visit or one_visit or visits_own"`
Expected: FAIL — the first on `identical to the prior artefact`, the third on the brief carrying the old findings.

- [ ] **Step 3: The `visit` keyword**

`v2/kernel/front.py`:

```python
def submissions(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None) -> list:
    """The phase's submissions in the epoch, newest last.

    *visit* filters to one shaping visit and is passed only for phase
    `slices` (revision 16): visits partition the shaping work, and a spec
    draft hidden behind a hand-back is not what the partition is for. A
    submission written before this revision carries no `visit`; `visit_of`
    attributes it, and gives it visit 1."""
    out = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
           if f.payload.get("phase") == phase and f.payload.get("epoch") == epoch_n]
    if visit is None:
        return out
    return [f for f in out if f.payload.get("visit", visit_of(store, run_id, f.seq)) == visit]
```

`newest_submission` is `submissions`'s last element and forwards the keyword:

```python
def newest_submission(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None):
    subs = submissions(store, run_id, phase, epoch_n, visit=visit)
    return subs[-1] if subs else None
```

and the same two lines on `newest_review_verdict`, whose own body filters by phase and epoch already:

```python
def newest_review_verdict(store, run_id: str, phase: str, epoch_n: int, visit: int | None = None):
    …                                            # the existing filter, unchanged
    if visit is not None:
        vs = [v for v in vs if visit_of(store, run_id, v.seq) == visit]
    return vs[-1] if vs else None
```

A verdict carries no `visit` of its own — it is the reviewer's, not the author's — so `visit_of` is the only attribution it has.

- [ ] **Step 4: The two call sites**

`v2/kernel/authz.py`, in `_check_submit`:

```python
    # Revision 16: for `slices` the dedupe is per VISIT. A plan an earlier
    # visit rejected may be resubmitted after a hand-back -- the shaper
    # reconsidered and stands by it, which is a decision, not the loop
    # spinning. Every other phase dedupes per epoch as before.
    visit = front.shaping_visit(store, cmd.run_id, epoch_n) if phase == "slices" else None
    if any(f.payload["hash"] == h for f in front.submissions(store, cmd.run_id, phase, epoch_n, visit=visit)):
        raise NotAuthorized(
            f"identical to the prior artefact: {h[:12]}... was already submitted "
            f"for {phase} in epoch {epoch_n}"
            + (f" visit {visit}" if visit is not None else "")
            + "; a resubmission that did not change is not a revision"
        )
```

`v2/kernel/commands.py`, in `issue_review_brief`:

```python
        # Revision 16: for `slices` the prior findings are the VISIT's own, so
        # a reviewer is not told to expect dispositions of findings the
        # visit's author never saw.
        prev = front.newest_review_verdict(
            store, cmd.run_id, phase, epoch_n,
            visit=(front.shaping_visit(store, cmd.run_id, epoch_n) if phase == "slices" else None))
```

- [ ] **Step 5: Stamp the visit on a `slices` submission**

Task 1 stamped `visit` in `_side_fact` for the ruling and for a `slices` submission. Confirm it is there, and that it is stamped **only** for phase `slices`:

```python
        payload = {"phase": phase, "epoch": epoch_n, "hash": h, "author": actor, "round": rnd}
        if phase == "slices":
            payload["visit"] = front.shaping_visit(store, cmd.run_id, epoch_n)
```

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel tests/coordinator -q`
Expected: PASS. The coordinator suite runs here because `issue_review_brief` changed and `test_review_round.py` reads the rendered brief.

- [ ] **Step 7: Mutations**

Make the `visit` keyword unconditional in `_check_submit` (drop the `if phase == "slices"`): the spec-phase resubmission test in `tests/kernel/test_authorization.py` reds, which is the check that the scoping stayed where the spec put it. Drop the filter from `issue_review_brief`: `test_the_review_brief_carries_the_visits_own_prior_findings` reds. Restore both.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel/ v2/tests/kernel/
git commit -m "feat(kernel): the visit-scoped readers -- dedupe, prior findings, and the submission's visit"
```

---

## Part B — Coordinator and the seats

### Task 6: `parse_reshape`, the spec round's files, and the skill

**Files:**
- Modify: `v2/kernel/slices.py` (`parse_reshape`, `Reshape`, `RESHAPE_LINE`)
- Modify: `v2/coordinator/seat.py` (`RESHAPE_OUT`)
- Modify: `v2/coordinator/author.py` (`author_round`'s watched and read sets, and the three-file read order)
- Modify: `skills/spec-author/SKILL.md` (a `## Handing back` section, and the prohibition in its general text). `skills/shape-author/SKILL.md` is **read** for its write-whole-then-rename wording and not changed.
- Test: `v2/tests/kernel/test_slices_grammar.py`, `v2/tests/coordinator/test_author_round.py`

**Interfaces:**
- Consumes: Task 2's `request_reshape`.
- Produces: `slices.parse_reshape(data: bytes) -> Reshape | None` — a `NamedTuple` with one field, `reasoning: str`; `seat.RESHAPE_OUT = "bircher/reshape.md"`; `author_round` returning the new outcome `"reshaped"`.

- [ ] **Step 1: Write the failing tests**

In `v2/tests/kernel/test_slices_grammar.py`:

```python
def test_parse_reshape_accepts_the_grammar():
    r = slices.parse_reshape(b"Ruling: epic\nReasoning: the issue names a store, an API and a page.\n")
    assert r.reasoning.startswith("the issue names")


def test_parse_reshape_refuses_four_ways():
    assert slices.parse_reshape(b"Ruling: one piece\nReasoning: x\n") is None   # the other ruling
    assert slices.parse_reshape(b"Ruling: epic\n") is None                      # no reasoning
    assert slices.parse_reshape(b"Ruling: epic\nReasoning:   \n") is None       # empty reasoning
    assert slices.parse_reshape(b"Some preamble\nRuling: epic\nReasoning: x\n") is None
```

In `v2/tests/coordinator/test_author_round.py`:

```python
def test_the_spec_round_watches_reshape_and_artifact_under_grill_model(fake):
    ctx = fake.spec_round()
    assert set(fake.last_turn.watched) == {seat.RESHAPE_OUT, seat.ARTIFACT_OUT}
    assert seat.QUESTIONS_OUT in fake.last_turn.read


def test_a_written_questions_file_does_not_end_the_turn(fake):
    fake.write(seat.QUESTIONS_OUT, b"1. what about auth?\n")
    assert fake.spec_round() != "questions_ended_the_turn"
    assert fake.turn_ran_to_its_cap is True


def test_reshape_is_read_before_questions_and_before_the_artifact(fake):
    fake.write(seat.RESHAPE_OUT, RESHAPE_BYTES)
    fake.write(seat.QUESTIONS_OUT, b"1. what about auth?\n")
    fake.write(seat.ARTIFACT_OUT, b"# A spec\n")
    assert fake.spec_round() == "reshaped"
    assert fake.store.run_state(fake.run_id) == "shaping"
    assert fake.parked_reasons == []                      # no grill park
    assert front.submissions(fake.store, fake.run_id, "spec", 0) == []


def test_a_malformed_reshape_is_the_empty_turn_with_its_detail(fake):
    fake.write(seat.RESHAPE_OUT, b"Ruling: epic\n")
    assert fake.spec_round() == "empty_retry"
    e = fake.store.facts_of_kind(fake.run_id, EventKind.AUTHOR_EMPTY)[-1]
    assert "reshape.md did not parse" in e.payload["detail"]


def test_the_skill_carries_the_block_and_the_prohibition():
    text = (SKILLS / "spec-author" / "SKILL.md").read_text()
    assert "Ruling: epic" in text and "Reasoning:" in text
    assert "the first non-blank line must be exactly `Ruling: epic`" in text
    for forbidden in ("the run's journal", "session history", "`.run/`"):
        assert forbidden in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_slices_grammar.py tests/coordinator/test_author_round.py -q -k "reshape or questions_file or skill_carries"`
Expected: FAIL — `slices` has no `parse_reshape`, `seat` has no `RESHAPE_OUT`.

- [ ] **Step 3: The parser**

In `v2/kernel/slices.py`, beside `RULING_LINE` and `parse_ruling`:

```python
RESHAPE_LINE = "Ruling: epic"


class Reshape(NamedTuple):
    reasoning: str


def parse_reshape(data: bytes) -> Reshape | None:
    """The spec author's hand-back (shaping spec §3). `parse_ruling`'s rules
    with the other ruling word: the first non-blank line exactly
    `Ruling: epic`, a non-empty `Reasoning:` block, anything else not a
    hand-back. Returning None rather than raising: a file that is not a
    hand-back is the empty turn, and the coordinator says why."""
    lines = data.decode("utf-8", "replace").splitlines()
    body = [ln for ln in lines]
    first = next((i for i, ln in enumerate(body) if ln.strip()), None)
    if first is None or body[first].strip() != RESHAPE_LINE:
        return None
    reasoning = _block(body[first + 1:], "Reasoning:")
    return None if not reasoning.strip() else Reshape(reasoning.strip())
```

`_block` is `parse_ruling`'s own helper for a labelled block that runs to the next label or the end; read `parse_ruling` and reuse it rather than writing a second reader. If `parse_ruling` inlines the logic, extract it into `_block` in this task and have both call it — one grammar, one reader.

- [ ] **Step 4: The watched set and the read order**

`v2/coordinator/seat.py`:

```python
RESHAPE_OUT = "bircher/reshape.md"
```

`v2/coordinator/author.py`, in `author_round`, replacing the `watched` line:

```python
    # Revision 16: the spec round watches the hand-back beside the artefact.
    # `questions.md` is READ but not watched under grill=model, so the
    # skill's "write the questions and continue" does not end the turn; under
    # grill=human it is watched too, as today.
    if phase == "spec":
        watched = ([seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human"
                   else [seat.ARTIFACT_OUT]) + [seat.RESHAPE_OUT]
        read = [seat.RESHAPE_OUT, seat.QUESTIONS_OUT, seat.ARTIFACT_OUT]
    else:
        watched = [seat.ARTIFACT_OUT, seat.QUESTIONS_OUT] if grill == "human" else [seat.ARTIFACT_OUT]
        read = None
```

and pass `read=read` to `run_turn`. Then, at the turn's end, before the questions branch and before the submit:

```python
    hand_back_bytes = turn.files.get(seat.RESHAPE_OUT)
    if hand_back_bytes is not None:
        r = slices.parse_reshape(hand_back_bytes)
        if r is None:
            # The empty turn, with its reason. `_move_aside` retires the file
            # before the re-prompt, and the retry's brief names it (Task 7).
            ctx.log(f"{seat.RESHAPE_OUT} does not parse: {hand_back_bytes[:200]!r}")
            return author.record_empty(ctx, turn.session_id,
                                       detail=f"reshape.md did not parse: the first non-blank line must be "
                                              f"exactly `{slices.RESHAPE_LINE}` and a non-empty `Reasoning:` "
                                              f"block must follow")
        if turn.files.get(seat.ARTIFACT_OUT) is not None or turn.files.get(seat.QUESTIONS_OUT) is not None:
            # The shape decides before the questions are worth asking, and a
            # spec written against a shape its own author disputed is not
            # submitted. Both are moved aside by `_move_aside`.
            ctx.log("artifact.md or questions.md beside a hand-back is ignored")
        try:
            seat.command(ctx, "request_reshape", {"reasoning": r.reasoning})
        except NotAuthorized as exc:
            return author.refusal_outcome(ctx, phase, str(exc), ("request_reshape:",))
        return "reshaped"
```

`record_empty` gains the keyword:

```python
def record_empty(ctx, session_id: str, detail: str = "") -> str:
    payload = {"session": session_id}
    if detail:
        payload["detail"] = detail
    try:
        seat.command(ctx, "record_author_empty", payload)
    …                                        # the existing body, unchanged
```

and the kernel side is two lines. In `_side_fact`'s `AUTHOR_EMPTY` branch the payload gains `"detail": cmd.payload.get("detail", "")`; in `authorize`'s `record_author_empty` branch, before the existing checks:

```python
        if cmd.payload.get("detail") is not None and not isinstance(cmd.payload.get("detail"), str):
            raise NotAuthorized("record_author_empty detail must be a string or absent")
```

A literal `cmd.payload.get("detail")` read, so the provenance extractor matches it syntactically; Task 11 adds its row.

Add `"reshaped"` to whatever enumerates `author_round`'s outcomes; `run_loop`'s branch for it is Task 8's.

- [ ] **Step 5: The skill**

Append to `skills/spec-author/SKILL.md`:

```markdown
## Handing back

If the issue is not one piece of work — if the spec you are about to write
would specify more than one capability a reviewer could see working end to
end — do not write the spec. Write `bircher/reshape.md` and end the turn.

Write exactly this, and nothing else in the file. The first non-blank line
must be exactly `Ruling: epic`:

    Ruling: epic
    Reasoning: <why this is more than one piece; name the pieces you see>

Write the file whole somewhere else in the worktree and rename it into
place. A watched file counts as landed the moment it is non-empty, so a
file written a line at a time can end your turn half-finished.

Do not write `bircher/artifact.md` or `bircher/questions.md` in the same
turn. The hand-back is read first and they are discarded.
```

and, beside "Nothing else is in scope" in its general text:

```markdown
Do not read the run's journal, its session history or `.run/`. Your value
here is that you are seeing the issue for the first time.
```

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 7: Mutations**

Put `seat.QUESTIONS_OUT` back in the watched set under `grill: model`: `test_a_written_questions_file_does_not_end_the_turn` reds. Read `artifact.md` before `reshape.md`: `test_reshape_is_read_before_questions_and_before_the_artifact` reds on the submission assertion. Restore both.

- [ ] **Step 8: Commit**

```bash
git add v2/kernel/slices.py v2/coordinator/ skills/spec-author/SKILL.md v2/tests/
git commit -m "feat(coordinator): the hand-back file -- parse_reshape, the spec round's watched set, and the skill"
```

---

### Task 7: The briefs — the question, the resolution, and four standing sections

**Files:**
- Modify: `v2/coordinator/phases.py` (`ROUND_CAUSE_KINDS`, `_is_round_cause`)
- Modify: `v2/coordinator/author.py` (`_findings_for`, `author_brief`)
- Test: `v2/tests/coordinator/test_author_brief.py`

**Interfaces:**
- Consumes: `front.dispute` (Task 3), `slices.RESHAPE_LINE` (Task 6), `EventKind.RESHAPE_REQUESTED` (Task 2).
- Produces: `author._question_section(ctx) -> str | None` and `author._resolution_section(ctx) -> str | None`, both private to this module; `author_brief`'s two suppression flags.

Six sections, and the rule that decides each is *which fact caused this round*, never *what state the run is in*. The reviewers found the same defect three times by reading the epoch instead of the cause.

| Section | Rendered when | Arms dispositions? | Arms previous draft? |
|---|---|---|---|
| The question | the cause is `model_ruling {shape}` and the epoch holds no `reshape_requested` | no | no |
| The resolution | phase `spec`, the epoch holds a hand-back, and the cause is the resolution | only if reviewer findings are re-rendered | only then |
| The hand-back's reasoning | phase `slices`, every round of every visit after the epoch's hand-back | no | no |
| The availability instruction | every composed `spec` turn in an epoch holding a `shape` ruling and no hand-back | no | no |
| The epoch's human answers | every composed `spec` turn in an epoch holding a `human_answer` | no | no |
| The parse failure | the cause is `author_empty` carrying a `detail` | no | no |

"Composed" excludes the `grill: human` resume turn, whose prompt is "answered; continue" and returns early.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_question_renders_for_the_shape_ruling_cause_only(fake):
    f = fake.ruled_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "is this one piece of work?" in brief.casefold()
    assert "## Dispositions" not in brief
    assert "## Your previous draft" not in brief
    assert "the spec would find one surface" not in brief      # the ruling's own words


def test_the_question_survives_a_crash_resume_of_its_turn(fake):
    f = fake.ruled_one_piece()
    author.author_brief(f.ctx, phase="spec")
    brief = author.author_brief(f.ctx, phase="spec").decode()   # same cause, second pass
    assert "is this one piece of work?" in brief.casefold()


def test_the_question_is_not_asked_on_four_other_causes(fake):
    for f in (fake.refused_submission_retry(), fake.empty_turn_retry(),
              fake.revision_turn(), fake.after_approve_one_piece()):
        brief = author.author_brief(f.ctx, phase="spec").decode()
        assert "is this one piece of work?" not in brief.casefold()


def test_the_question_turn_keeps_a_persons_bircher_comment(fake):
    """The snapshot drops the pipeline's seven status prefixes; a person's
    comment that happens to begin `bircher:` is theirs and stays."""
    f = fake.ruled_one_piece(comments=["bircher: parked gate", "bircher: I think this is three things"])
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "I think this is three things" in brief
    assert "parked gate" not in brief


def test_the_resolution_section_renders_without_a_dispositions_block(fake):
    f = fake.approved_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" in brief
    assert "## Dispositions" not in brief
    assert "is this one piece of work?" not in brief.casefold()


def test_the_resolution_re_renders_a_prior_reviewers_findings(fake):
    f = fake.spec_reviewed_then_handed_back_then_approved(findings=b"the auth section is thin")
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "the shape was disputed and resolved" in brief
    assert "the auth section is thin" in brief
    assert "## Dispositions" in brief
    assert "## Your previous draft" in brief


def test_the_shaping_brief_carries_the_hand_backs_reasoning_every_round(fake):
    f = fake.handed_back(reasoning="the issue names a store, an API and a page")
    for _ in range(2):                                    # the visit's first round and its retry
        brief = author.author_brief(f.ctx, phase="slices").decode()
        assert "a store, an API and a page" in brief
    f.direct("slice it")                                  # a direction opens the next visit
    brief = author.author_brief(f.ctx, phase="slices").decode()
    assert "a store, an API and a page" in brief
    assert brief.index("a store, an API and a page") < brief.index("slice it")
    assert "## Your previous draft" not in brief          # never an earlier visit's


def test_a_composed_spec_turn_carries_the_availability_instruction(fake):
    f = fake.revision_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "Ruling: epic" in brief
    assert "rename it into place" in brief
    f2 = fake.handed_back()
    assert "Ruling: epic" not in author.author_brief(f2.ctx, phase="spec").decode()


def test_the_rendered_block_parses_back(fake):
    f = fake.ruled_one_piece()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    block = brief.split("Ruling: epic", 1)[1]
    text = "Ruling: epic" + block.split("\n\n", 1)[0]
    assert slices.parse_reshape(text.encode()) is not None


def test_the_epochs_human_answers_are_a_standing_section(fake):
    f = fake.answered_then_handed_back_from_the_resume_turn()
    brief = author.author_brief(f.ctx, phase="spec").decode()   # a seat of the other vendor
    assert "## The answers already given" in brief
    assert f.answer_text in brief


def test_a_malformed_hand_back_retry_names_it_and_shows_the_grammar(fake):
    f = fake.malformed_reshape()
    brief = author.author_brief(f.ctx, phase="spec").decode()
    assert "your previous turn's file did not parse" in brief.casefold()
    assert "Ruling: epic" in brief
    assert "## Dispositions" not in brief
    assert "## Your previous draft" not in brief


def test_the_round_cause_admits_the_shape_ruling_and_not_a_grill_ruling(fake):
    assert phases._is_round_cause(fake.fact_shape_ruling) is True
    assert phases._is_round_cause(fake.fact_grill_ruling) is False
    assert phases._is_round_cause(fake.fact_reshape_requested) is True
    assert phases._is_round_cause(fake.fact_refused_request_reshape) is True


def test_the_hand_back_cause_renders_no_findings(fake):
    f = fake.handed_back()
    assert author._findings_for(f.ctx) == b""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_author_brief.py -q`
Expected: FAIL — `_is_round_cause` does not admit a `model_ruling`, so `round_cause` returns the wrong fact and every question test fails at the first assertion.

- [ ] **Step 3: The round causes**

`v2/coordinator/phases.py`:

```python
ROUND_CAUSE_KINDS = frozenset({
    EventKind.RUN_STARTED, EventKind.BUNDLE_REVISED, EventKind.REVIEW_VERDICT,
    EventKind.HUMAN_RULING, EventKind.HUMAN_DIRECTION, EventKind.AUTHOR_EMPTY,
    EventKind.PARKED, EventKind.COMMAND_REJECTED,
    # Revision 16: the hand-back calls for the next shaping round.
    EventKind.RESHAPE_REQUESTED,
})


def _is_round_cause(f) -> bool:
    if f.kind == EventKind.COMMAND_REJECTED:
        return f.payload.get("command_name") in ("submit_spec", "submit_plan", "submit_slices",
                                                  "record_one_piece", "request_reshape") and f.actor != "human"
    if f.kind == EventKind.MODEL_RULING:
        # Revision 16: a SHAPE ruling calls for the spec round that asks the
        # fresh-perspective question. A grill ruling stays what it is -- the
        # author answering its own question inside a turn -- and admitting it
        # here would make every grill ruling start a round.
        from kernel.slices import SHAPE_QUESTION
        return f.payload.get("question_id") == SHAPE_QUESTION
    return f.kind in ROUND_CAUSE_KINDS
```

`EventKind.MODEL_RULING` stays **out** of `ROUND_CAUSE_KINDS` — the frozenset is the "admit the whole kind" list, and this kind is admitted conditionally. A reader that walks the frozenset without calling `_is_round_cause` would otherwise pick up grill rulings; grep for `ROUND_CAUSE_KINDS` and confirm `_is_round_cause` is its only consumer.

`refusal_outcome`'s markers gain `"request_reshape:"` at its call site in Task 6; no change here.

- [ ] **Step 4: `_findings_for`**

```python
def _findings_for(ctx) -> bytes:
    cause = phases.round_cause(ctx)
    store = ctx.store
    # Revision 16: two causes render their own section instead of findings.
    # The hand-back's reasoning is the shaping brief's standing section, and
    # the shape ruling is the question-turn's cause, which carries nothing.
    # Returning the reasoning here would render it twice and arm both the
    # dispositions block and the previous-draft block on the one round whose
    # intended endings include a ruling with no artefact.
    if cause.kind in (EventKind.RESHAPE_REQUESTED, EventKind.MODEL_RULING):
        return b""
    if cause.kind == EventKind.AUTHOR_EMPTY and cause.payload.get("detail"):
        return b""                      # the parse-failure section renders it
    …                                   # the existing branches, unchanged
```

- [ ] **Step 5: The two section builders**

In `v2/coordinator/author.py`:

```python
QUESTION = (
    "\n## Before you write anything\n\n"
    "Is this one piece of work? One piece is a single capability a reviewer could\n"
    "see working end to end. If the spec you are about to write would specify more\n"
    "than one such capability, do not write it. Write `%s` instead and end the\n"
    "turn.\n" % seat.RESHAPE_OUT)

HAND_BACK_GRAMMAR = (
    "Write the file whole somewhere else in the worktree and rename it into place:\n"
    "a watched file counts as landed the moment it is non-empty. Write exactly\n"
    "this, with the first non-blank line exactly `%s`:\n\n"
    "    %s\n"
    "    Reasoning: <why this is more than one piece; name the pieces you see>\n"
    % (slices.RESHAPE_LINE, slices.RESHAPE_LINE))


def _question_section(ctx) -> str | None:
    """The fresh-perspective question (shaping spec §3). A rendering of the
    round's CAUSE, never a predicate on the epoch: the first spec turn after
    the shape ruling, and a crash-resume of that turn, whose cause is
    unchanged. An epoch that holds a hand-back has had its question."""
    from kernel.slices import SHAPE_QUESTION
    cause = phases.round_cause(ctx)
    if not (cause.kind == EventKind.MODEL_RULING
            and cause.payload.get("question_id") == SHAPE_QUESTION):
        return None
    if front.epoch_facts(ctx.store, ctx.run_id, EventKind.RESHAPE_REQUESTED, ctx.epoch()):
        return None
    return QUESTION + "\n" + HAND_BACK_GRAMMAR


def _resolution_section(ctx) -> str | None:
    """The person's decision, rendered as its own section rather than as a
    finding (shaping spec §3): the spec round in an epoch whose dispute was
    resolved is briefed with what they decided and writes the spec."""
    d = front.dispute(ctx.store, ctx.run_id)
    if d is None or d.resolution is None:
        return None
    if phases.round_cause(ctx).id != d.resolution.id:
        return None
    if d.resolution.kind == EventKind.HUMAN_RULING:
        what = "A person approved the shaper's ruling: this is one piece of work."
    else:
        what = ("A person directed the shaper:\n\n> %s\n\nand the shaper then ruled:\n\n> %s"
                % (d.resolution.payload["text"], d.disputed.payload["reasoning"]))
    return ("\n## The shape was disputed and resolved\n\n"
            "You handed this run back as an epic. The shaper reconsidered and reaffirmed\n"
            "one piece. %s\n\nWrite the spec.\n" % what)
```

- [ ] **Step 6: `author_brief`**

Insert after the skill and before the policy block, and set the two flags:

```python
    question = _resolution_section(ctx) or _question_section(ctx) if phase == "spec" else None
    suppress = question is not None
    if phase == "slices":
        # The hand-back's reasoning, standing in every visit after it, ahead
        # of whatever the round's cause renders -- so a direction typed into
        # the reshaping session does not displace it.
        d = front.dispute(store, run_id)
        if d is not None:
            parts.append("\n## Why this was handed back\n\nThe spec author disputed the shape:\n\n> %s\n"
                         % d.hand_back.payload["reasoning"])
    if question is not None:
        parts.append(question)
    cause = phases.round_cause(ctx)
    if cause.kind == EventKind.AUTHOR_EMPTY and cause.payload.get("detail"):
        parts.append("\n## Your previous turn's file did not parse\n\n%s\n\n%s\n"
                     % (cause.payload["detail"], HAND_BACK_GRAMMAR))
        suppress = True
    if phase == "spec":
        n = ctx.epoch()
        # The availability instruction: every composed spec turn of an epoch
        # that holds a shape ruling and no hand-back yet, so a revision turn
        # briefed with a reviewer's "several pieces" finding can act on it.
        if (front.shape_ruling(store, run_id, n) is not None
                and not front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, n)
                and question is None):
            parts.append("\n## Handing back\n\nIf this issue is more than one piece of work, do not write the\n"
                         "spec. Hand it back instead and end the turn.\n\n%s\n" % HAND_BACK_GRAMMAR)
        answers = front.epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
        if answers:
            # Standing, because a hand-back from the `grill: human` resume
            # turn is legal and the next seat is another vendor's: without
            # this the person's answers reach nobody.
            parts.append("\n## The answers already given\n\n%s\n"
                         % "\n\n".join(a.payload["answer"] for a in answers))
```

and guard the two existing blocks:

```python
    prior = None if suppress else front.newest_submission(
        store, run_id, phase, ctx.epoch(),
        visit=(front.shaping_visit(store, run_id, ctx.epoch()) if phase == "slices" else None))
    findings = b"" if suppress else _findings_for(ctx)
```

leaving the rest of the function as it stands. When the resolution re-renders a reviewer's findings, `suppress` is cleared: after building the resolution section, if `phase == "spec"` and `front.newest_review_verdict(store, run_id, "spec", ctx.epoch())` exists and precedes the hand-back, set `suppress = False` and `findings = store.read_blob(that verdict's findings_hash)`, so the dispositions block applies to those findings alone.

- [ ] **Step 7: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 8: Mutations**

Make `_question_section` a predicate on the epoch (any epoch holding a `shape` ruling) rather than on the cause: `test_the_question_is_not_asked_on_four_other_causes` reds on the revision turn. Drop the `RESHAPE_REQUESTED` branch from `_findings_for`: `test_the_hand_back_cause_renders_no_findings` reds and the standing section renders twice. Restore both.

- [ ] **Step 9: Commit**

```bash
git add v2/coordinator/ v2/tests/coordinator/
git commit -m "feat(coordinator): the question, the resolution, and four standing brief sections"
```

---

### Task 8: The coordinator — vendors, notices, the reply, and the loop

**Files:**
- Modify: `v2/coordinator/author.py` (`choose_author_vendor`)
- Modify: `v2/coordinator/human.py` (`classify_batch`, `take_listing`, `park_prompt`)
- Modify: `v2/coordinator/phases.py` (`PARK_NEEDS`, `park_notice_body`, `run_loop`)
- Modify: `v2/coordinator/shape.py` (the per-visit round counter)
- Test: `v2/tests/coordinator/test_disagreement.py` (new), `v2/tests/coordinator/test_vendors.py`

**Interfaces:**
- Consumes: everything Tasks 1 to 7 produce.
- Produces: no new public names; `run_loop` gains the `disagreement` branch and the `reshaped` outcome, `classify_batch` gains a `dispute` keyword.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_hand_back_visit_goes_to_the_handed_back_rulings_vendor(fake):
    f = fake.handed_back(ruling_vendor="claude", hand_back_vendor="codex")
    assert author.choose_author_vendor(f.ctx) == "claude"
    f.empty_turn()                                    # a new cause, same rule
    assert author.choose_author_vendor(f.ctx) == "claude"
    f.reject_plan()                                   # a verdict, still the rule
    assert author.choose_author_vendor(f.ctx) == "claude"


def test_a_hand_back_from_the_rulings_own_vendor_goes_to_the_other(fake):
    """On a revision turn the rotation can hand the spec to the shaper's
    vendor; a reconsideration by the vendor that just handed back is no
    reconsideration."""
    f = fake.handed_back(ruling_vendor="claude", hand_back_vendor="claude")
    assert author.choose_author_vendor(f.ctx) == "codex"
    f.empty_turn()
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_a_direction_opened_visit_excludes_the_disputed_rulings_vendor(fake):
    f = fake.disagreed(ruling_vendor="claude")
    f.direct("slice it")
    assert author.choose_author_vendor(f.ctx) == "codex"
    f.empty_turn()
    assert author.choose_author_vendor(f.ctx) == "codex"


def test_the_spec_seat_is_never_the_shape_rulings_vendor(fake):
    f = fake.ruled_one_piece(ruling_vendor="claude")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.approve_one_piece()
    assert author.choose_author_vendor(f.spec_ctx) == "codex"
    f.review_spec(verdict="request_revision", reviewer="codex")
    assert author.choose_author_vendor(f.spec_ctx) == "codex"   # the rotation now


def test_the_notice_and_the_prompt_say_what_happened(fake):
    f = fake.disagreed(ruling_vendor="claude", hand_back_vendor="codex")
    body = phases.park_notice_body(f.ctx, f.park)
    assert "reaffirmed one piece" in body and "handed" in body
    assert "accepted" not in body                      # no reviewer accepted anything
    prompt = human.park_prompt(f.ctx, f.park).decode()
    assert "claude" in prompt and "codex" in prompt
    assert f.ruling_reasoning in prompt and f.hand_back_reasoning in prompt
    gate = phases.park_notice_body(f.ctx, f.gate_park)
    assert "reaffirmed one piece" not in gate


def test_approve_at_the_disagreement_park_is_the_approval(fake):
    f = fake.disagreed()
    assert human.classify_batch([{"text": "approve"}], state="shaping",
                                grill_open=False, dispute=True) == ("approve_one_piece", "")
    assert human.classify_batch([{"text": "retry"}], state="shaping",
                                grill_open=False, dispute=True) == ("retry", "")
    assert human.classify_batch([{"text": "slice it"}], state="shaping",
                                grill_open=False, dispute=True) == ("direction", "slice it")


def test_approve_is_the_approval_even_in_a_grill_epoch(fake):
    """A `bircher:grill` epoch holding an unanswered question does not make
    the person's `approve` an answer: the grill branch is skipped at every
    shaping state, and the dispute reading takes it."""
    f = fake.disagreed(policy="bircher:grill", unanswered_question=True)
    assert f.reply("approve") == "approve_one_piece"
    assert f.store.run_state(f.run_id) == "queued"


def test_the_grill_branch_is_skipped_at_every_shaping_state(fake):
    """`record_human_answer` is refused from every shaping state, so a
    coordinator that classified a reply as `answer` there would dismiss it."""
    f = fake.grill_open_at_shaping()
    assert human.classify_batch([{"text": "here are the answers"}], state="shaping",
                                grill_open=True, dispute=False)[0] == "direction"


def test_retry_at_the_disagreement_park_is_refused_and_answered(fake):
    f = fake.disagreed()
    assert f.reply("retry") == "dismissed"
    assert "grant_round" in f.session_replies[-1]
    assert front.unresolved_disagreement(f.store, f.run_id) is True


def test_the_loop_records_the_park_when_a_pass_died_before_it(fake):
    f = fake.disagreed(park=False)
    assert phases.run_loop(f.ctx) == phases.Exit.PARKED
    assert front.current_park(f.store, f.run_id).payload["reason"] == "disagreement"
    assert f.seats_dispatched == 0                      # no author, no reviewer


def test_an_approve_before_the_park_is_taken_with_no_park_recorded(fake):
    f = fake.disagreed(park=False, pending_reply="approve")
    assert phases.run_loop(f.ctx) != phases.Exit.PARKED
    assert f.store.run_state(f.run_id) == "queued"
    assert front.current_park(f.store, f.run_id) is None


def test_a_refused_disagreement_park_does_not_crash_the_pass(fake):
    f = fake.disagreed(park=False, resolve_in_the_gap=True)
    assert phases.run_loop(f.ctx) != phases.Exit.FAILED
    assert front.current_park(f.store, f.run_id) is None


def test_the_shape_rounds_counter_is_per_visit(fake):
    f = fake.handed_back()
    f.submit_plan()
    assert (pathlib.Path(f.bundle_dir) / f.run_id / "slices-v2-r1.md").exists()
    assert (pathlib.Path(f.bundle_dir) / f.run_id / "slices-v1-r1.md").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/coordinator/test_disagreement.py tests/coordinator/test_vendors.py -q`
Expected: FAIL — `classify_batch` takes no `dispute`, and `choose_author_vendor` has neither rule.

- [ ] **Step 3: `choose_author_vendor`**

Three clauses, in this precedence, inserted after the adopted-seat clause and before the same-phase rotation:

```python
    # Revision 16, in precedence order. The adopted-seat clause above is
    # first and unchanged.
    d = front.dispute(store, run_id)
    if phase == "spec":
        # Every spec author round of an epoch holding a shape ruling takes
        # the vendor that is NOT the newest shape ruling's actor -- the
        # epoch's first spec turn, and the spec round after a resolution,
        # both of which would otherwise fall through to `default_author`,
        # which is the shaper's own vendor. It yields to the same-phase
        # rotation once a spec review_verdict exists in the epoch, because
        # by then the fresh perspective has been had.
        ruling = front.shape_ruling(store, run_id, n)
        if ruling is not None and not same_phase:
            return _other_than(ctx, ruling.actor)
    elif phase == "slices" and d is not None:
        visit = front.shaping_visit(store, run_id, n)
        if front.visit_of(store, run_id, d.hand_back.seq) == visit:
            # The hand-back's visit: the handed-back ruling's vendor
            # reconsiders with the counter-argument in hand -- unless that
            # vendor is the hand-back's own actor, in which case a
            # "reconsideration" would be one vendor arguing with itself.
            want = d.handed_back.actor
            return _other_than(ctx, want) if want == d.hand_back.actor else want
        if d.resolution is not None and d.resolution.kind == EventKind.HUMAN_DIRECTION:
            # A direction-opened visit: the person has just overruled the
            # disputed ruling's vendor.
            return _other_than(ctx, d.disputed.actor)
```

Both `slices` clauses hold for the **whole visit**, verdicts or none: an empty-turn retry has a new cause and a rejected plan followed by rotation would otherwise hand the round to the vendor the rule excludes. Within these visits the rotation moves only the reviewer seat, which is always the vendor that did not author, so cross-vendor review is preserved.

```python
def _other_than(ctx, vendor: str) -> str:
    others = [v for v in sorted(ctx.agent_ids) if v != vendor]
    return others[0] if others else vendor
```

`same_phase` is the existing local; compute it before these clauses rather than after.

- [ ] **Step 4: The notice and the prompt**

`v2/coordinator/phases.py`:

```python
PARK_NEEDS = {
    …,
    "disagreement": ("The shaper and the spec author disagree on whether this is one piece "
                     "of work. Reply with the single word `approve` to accept one piece, or "
                     "say how to slice it. This is your decision, not a reviewer's."),
}
```

and in `park_notice_body`, before the generic body:

```python
    if reason == "disagreement":
        # NOT "the reviewer accepted": nothing was accepted. Two seats
        # disagree and the person decides (shaping spec §4).
        return (f"bircher: parked disagreement\n\n"
                f"The spec author handed this issue back as an epic; the shaper reconsidered and "
                f"reaffirmed that it is one piece of work.\n\n{PARK_NEEDS['disagreement']}\n\n"
                f"{where}\n\nRun `{ctx.run_id}`. Your reply is read by the next wave, not the "
                "moment you send it, so nothing appears to happen until one runs.")
```

`v2/coordinator/human.py`, in `park_prompt`, before the `gate` branch:

```python
    if reason == "disagreement":
        d = front.dispute(store, run_id)
        return (f"{NOT_FOR_THE_AGENT}\n\n"
                f"Two seats disagree about the shape of this issue and you decide.\n\n"
                f"**{d.handed_back.actor} ruled one piece:**\n\n> {d.handed_back.payload['reasoning']}\n\n"
                f"**{d.hand_back.actor} handed it back as an epic:**\n\n> {d.hand_back.payload['reasoning']}\n\n"
                f"**{d.disputed.actor} reconsidered and reaffirmed one piece:**\n\n"
                f"> {d.disputed.payload['reasoning']}\n\n"
                f"Reply with the single word `approve` to accept one piece, or say how to slice it.").encode()
```

Naming each seat's vendor is the point: a person can see whether the disagreement crosses vendors or is one vendor contradicting itself.

- [ ] **Step 5: The reply**

`classify_batch` gains a keyword and two rules:

```python
def classify_batch(items: list, *, state: str, grill_open: bool, dispute: bool = False) -> tuple[str, str]:
    """..."""                                # the existing docstring, plus a
                                             # line for `dispute`
    if not items:
        raise ValueError("classify_batch needs at least one message: an empty batch is not a fact")
    texts = [it["text"].strip() for it in items]
    joined = "\n\n".join(texts)
    if grill_open and state not in SHAPING_STATES:
        # Revision 16: `record_human_answer` is refused from every shaping
        # state, so classifying a reply there as an answer would dismiss the
        # person's message. The grill is epoch-scoped and can stand open
        # while a hand-back's visit runs.
        return "answer", joined
    single = texts[0].casefold() if len(texts) == 1 else None
    if dispute and state == "shaping":
        # At the disagreement park `approve` is the approval, not a
        # direction; `retry` is refused by the kernel and `reply_refusals`
        # answers it; anything else is a direction, which opens a visit.
        if single == APPROVE:
            return "approve_one_piece", ""
        return ("retry", "") if single == RETRY else ("direction", joined)
    if single == RETRY:
        …                                    # the rest unchanged
```

`SHAPING_STATES` is `authz`'s frozenset; import it rather than re-listing the three names. `take_listing` passes `dispute=front.unresolved_disagreement(store, run_id)` and gains the dispatch arm:

```python
        elif kind == "approve_one_piece":
            result = _human(ctx, "approve_one_piece", {"cursor_item_id": cur}, key)
```

`reply_refusals` needs no change: a refused `grant_round` is dismissed and answered as every refused token is.

- [ ] **Step 6: `run_loop`**

Two changes in the `shaping` region, and one outcome:

```python
            # Revision 16: the park is the disagreement's NOTICE, not its
            # definition. A pass that died between the disputed ruling and
            # its park left the journal saying "unresolved" and no park;
            # recording it here costs one wave and never a third seat. After
            # the operator fence and before any seat is dispatched.
            if state == "shaping" and park is None and front.unresolved_disagreement(store, run_id):
                try:
                    if stall(ctx, "disagreement", session_id=_newest_author_session(ctx),
                             findings_hash=None, verdict=None, reviewer=None) == "parked":
                        return Exit.PARKED
                except NotAuthorized as exc:
                    # The dispute was resolved between `stall`'s listing and
                    # its park -- a person's command landing in the gap. Not
                    # a crash: re-read and carry on.
                    ctx.log(f"disagreement park refused, the dispute resolved in the gap: {exc}")
                continue
```

placed immediately after the `park = front.current_park(...)` block and before `if state == "shaping":`. The `queued`/`specified` branch gains:

```python
                elif out == "reshaped":
                    # The spec author handed the run back; the next iteration
                    # reads `shaping` and runs the shaping round.
                    continue
```

which is what the bare `continue` at the end of that branch already does — add the arm anyway, so the outcome is named where a reader looks for it, and so an unexpected outcome still falls to the `failed` arm.

- [ ] **Step 7: `shape_round`'s counter**

```python
    visit = front.shaping_visit(store, run_id, n)
    rnd = len(front.submissions(store, run_id, "slices", n, visit=visit)) + 1
    author._copy(ctx, f"slices-v{visit}", rnd, artefact)
```

`_copy` writes `<phase>-r<rnd>.md`, so passing `slices-v2` yields `slices-v2-r1.md`. Moving only the brief's counter and leaving the copy epoch-numbered is the defect the spec's site table names: a visit's first plan would overwrite the last visit's.

- [ ] **Step 8: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 9: Mutations**

Restrict the hand-back-visit vendor rule to rounds with no prior verdict: `test_the_hand_back_visit_goes_to_the_handed_back_rulings_vendor` reds on its third assertion. Move the `run_loop` disagreement branch below the `shaping` author dispatch: `test_the_loop_records_the_park_when_a_pass_died_before_it` reds on the seat count. Restore both.

- [ ] **Step 10: Commit**

```bash
git add v2/coordinator/ v2/tests/coordinator/
git commit -m "feat(coordinator): the disagreement -- vendors, the notice, the reply, and the loop's park recovery"
```

---

## Part C — Runner

### Task 9: The runner — the query, the generator, and the non-zero branch

**Files:**
- Modify: `v2/coordinator/cli.py` (a `disagreement` subcommand)
- Modify: `batch/lib/kernel-client.sh` (`_kernel_unresolved_disagreement`)
- Modify: `batch/run-queue.sh` (the non-zero branch, a helper, the self-test)
- Modify: `batch/issues-to-queue.sh` (the third condition, the union after `is_unblocked`, the sweep's failure line)

**Interfaces:**
- Consumes: `front.unresolved_disagreement` (Task 3).
- Produces: `python -m coordinator.cli disagreement --db DB --run-id ID` printing `yes`, `no`, or exiting non-zero; `_kernel_unresolved_disagreement <run_id>` printing `yes`, `no` or the empty string.

**Three-valued, everywhere.** The query answers yes, no, or *unreadable*, and the third value is not "no". `_kernel_state` prints the empty string on every failure it has, and the comment above `_unreadable_state_item` says what guessing costs. The new branch follows that shape exactly.

- [ ] **Step 1: Write the failing self-test cases**

In `batch/run-queue.sh`'s self-test, beside the `_interrupted_sliced_item` cases:

```bash
  # Revision 16: a non-zero exit at `shaping` with the dispute unresolved
  # keeps the queue file and records no outcome.
  ( set -e; cd "$sdir"; SCORECARD="$sdir/scorecard.jsonl"; QUEUE="$sdir/queue"; PROCESSED="$sdir/processed"
    _disputed_shaping_item "i15-epic" 1 "i15-epic-1" ) \
    || { echo "FAIL _disputed_shaping_item exited non-zero"; exit 1; }
  [ -f "$sdir/queue/i15-epic.md" ] || { echo "FAIL _disputed_shaping_item: the queue file must stay"; exit 1; }
  grep -q 'awaits the person' "$sdir/scorecard.jsonl" \
    || { echo "FAIL _disputed_shaping_item: the row must say the dispute awaits the person"; exit 1; }

  # And the unreadable query takes the same path with a different note.
  ( set -e; cd "$sdir"; SCORECARD="$sdir/scorecard.jsonl"; QUEUE="$sdir/queue"; PROCESSED="$sdir/processed"
    _unreadable_dispute_item "i16-epic" "i16-epic-1" ) \
    || { echo "FAIL _unreadable_dispute_item exited non-zero"; exit 1; }
  [ -f "$sdir/queue/i16-epic.md" ] || { echo "FAIL _unreadable_dispute_item: the queue file must stay"; exit 1; }
  [ ! -f "$sdir/processed/i16-epic.md" ] || { echo "FAIL _unreadable_dispute_item: the file must NOT be retired"; exit 1; }
```

and a case for the generator's failure line:

```bash
  # The journal sweep's failure is visible. BIRCHER_KERNEL_DB names a file
  # that exists and is not a database, so the snippet raises.
  : > "$sdir/not-a.db"
  out=$(BIRCHER_KERNEL_DB="$sdir/not-a.db" REPO=x DRY=1 sh "$HERE/issues-to-queue.sh" 2>&1 >/dev/null || true)
  case "$out" in *"journal sweep failed"*) ;; *) echo "FAIL the sweep's failure must print a line"; exit 1 ;; esac
```

and a Python case for the generator's third condition, in `v2/tests/execution/test_queue_generation.py`:

```python
def test_the_generator_lists_a_run_with_an_unresolved_dispute_and_no_park(tmp_path):
    s, f = _disagreed(tmp_path, park=False)
    assert _sweep(tmp_path) == [f.issue_number]


def test_a_resumed_run_is_not_withheld_by_a_blocker(tmp_path):
    """The blocked check applies to label-derived items only: an open run is
    past its sequencing, and a sibling blocker reopened while it was parked
    would withhold it every wave with nothing on the issue to say so."""
    s, f = _disagreed(tmp_path, park=False, blocked=True)
    assert _sweep(tmp_path) == [f.issue_number]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /Users/jonw/bircher && bash batch/run-queue.sh --self-test 2>&1 | tail -20`
Expected: FAIL — `_disputed_shaping_item: command not found`.

- [ ] **Step 3: The CLI subcommand**

`v2/coordinator/cli.py`, beside `state`:

```python
    dg = subs.add_parser("disagreement")
    dg.add_argument("--db", required=True)
    dg.add_argument("--run-id", required=True)
```

and in `main`'s dispatch:

```python
    if args.cmd == "disagreement":
        # `yes`/`no` on stdout, and any failure is a non-zero exit with the
        # traceback on stderr: the caller must be able to tell "no" from
        # "could not read", and a printed empty string cannot.
        store = Store.open(args.db)
        print("yes" if front.unresolved_disagreement(store, args.run_id) else "no")
        return 0
```

- [ ] **Step 4: The shell query**

`batch/lib/kernel-client.sh`, modelled on `_kernel_state`:

```bash
# _kernel_unresolved_disagreement <run_id> -> `yes`, `no`, or the empty
# string when the kernel would not answer (shaping spec §5). THREE VALUES.
# Empty is not "no": the caller that reads it as "no" records `failed` on a
# run whose two seats disagree and retires its queue file, which is the
# outcome the branch exists to prevent.
_kernel_unresolved_disagreement() {  # <run_id>
  local out=""
  out=$( PYTHONPATH="$(_kernel_pythonpath)" _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m coordinator.cli disagreement \
           --db "${BIRCHER_KERNEL_DB:-}" --run-id "$1" 2>/dev/null ) || out=""
  case "$out" in yes|no) printf '%s' "$out" ;; *) printf '' ;; esac
}
```

- [ ] **Step 5: The two helpers and the branch**

Beside `_interrupted_sliced_item`:

```bash
# _disputed_shaping_item <item> <rc> <run_id>: a non-zero exit AT shaping with
# the dispute unresolved (shaping spec §5). The pass died between the disputed
# ruling and its park; the queue file stays, no outcome is recorded, and the
# next wave's loop records the park and dispatches no seat.
_disputed_shaping_item() {
  local item="$1" rc="$2" run_id="$3"
  echo "[batch] $item: phases exited $rc at shaping with the shape disputed; the dispute awaits the person" >&2
  mkdir -p "$(dirname "$SCORECARD")"
  json_row "$item" "" "escalated" "false" "" "" 0 "phases rc=$rc at shaping: the shape is disputed and awaits the person; run '$run_id' stays open and the next pass parks it" "n/a" >> "$SCORECARD"
}

# _unreadable_dispute_item <item> <run_id>: the state is `shaping` and the
# kernel will not say whether the dispute is unresolved. Same treatment as an
# unreadable state: no outcome, the queue file stays, a row that says a human
# has to look.
_unreadable_dispute_item() {
  local item="$1" run_id="$2"
  echo "[batch] $item: cannot read run $run_id's dispute; recording nothing and leaving the queue file" >&2
  mkdir -p "$(dirname "$SCORECARD")"
  json_row "$item" "" "escalated" "false" "" "" 0 "the dispute of run '$run_id' could not be read after a non-zero exit at shaping, so this pass cannot tell whether it awaits the person; nothing is recorded and the queue file stays" "n/a" >> "$SCORECARD"
}
```

and in `run_item`'s non-zero branch, immediately after the `sliced` case and before the `failed` path:

```bash
      if [ "$_st_end" = shaping ]; then
        local _dis; _dis=$(_kernel_unresolved_disagreement "$BIRCHER_RUN_ID")
        if [ "$_dis" = yes ]; then
          _disputed_shaping_item "$item" "$_prc" "$BIRCHER_RUN_ID"
          return 0
        fi
        if [ -z "$_dis" ]; then
          _unreadable_dispute_item "$item" "$BIRCHER_RUN_ID"
          return 0
        fi
      fi
```

Only a literal `no` falls through to today's `failed` path.

- [ ] **Step 6: The generator**

`batch/issues-to-queue.sh`, the snippet's condition:

```python
    if front.current_park(s, rid) is not None or (
            s.run_state(rid) == "sliced" and front.filing_complete(s, rid, front.epoch(s, rid)) is None) or (
            front.unresolved_disagreement(s, rid)):
        out.append(m.group(1))
```

`unresolved_disagreement` is read without a state test: the park may not be recorded yet, and the run is at `shaping` by construction when the dispute exists.

The sweep's failure becomes visible:

```sh
  parked_nums=$(PYTHONPATH="$HERE/../v2" "${BIRCHER_PY:-python3}" -c '...' 2>"$TMP_SWEEP_ERR") || parked_nums=""
  if [ -s "$TMP_SWEEP_ERR" ]; then
    echo "issues-to-queue: journal sweep failed ($(tr '\n' ' ' < "$TMP_SWEEP_ERR")); the queue was generated from labels alone" >&2
  fi
```

The `2>/dev/null` goes and the non-fatal fallback stays. A raise inside the snippet used to yield an empty result and no line, and the runs it drops are exactly the ones labels cannot find.

And the union moves **after** `is_unblocked`:

```sh
for n in $queued_nums; do
  is_unblocked "$n" || { echo "skip #$n (blocked)"; continue; }
  _emit_item "$n"
done
# Journal-derived resumptions are past their sequencing: a sibling blocker
# reopened while a run was parked would otherwise withhold it every wave
# with nothing on the issue to say so (shaping spec §5).
for n in $parked_nums; do
  case " $queued_nums " in *" $n "*) continue ;; esac
  _emit_item "$n"
done
```

Extract the loop body into `_emit_item` rather than duplicating it — the body writes the queue file, the manifest line and the count, and two copies is two places for the manifest to drift.

- [ ] **Step 7: Renumber the citations**

Every `run-queue.sh` edit shifts the line numbers cited in comments elsewhere in the file and in the spec's §5. After the edits:

```bash
cd /Users/jonw/bircher && grep -n 'run-queue\.sh:[0-9]' batch/run-queue.sh docs/superpowers/specs/2026-09-09-shaping-phase-design.md
```

and correct each citation against the current file. This has cost three CI iterations before; do it in the same commit as the edit.

- [ ] **Step 8: Run the tests**

Run: `cd /Users/jonw/bircher && bash batch/run-queue.sh --self-test 2>&1 | tail -5 && cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q`
Expected: both PASS.

- [ ] **Step 9: Mutation**

Make `_kernel_unresolved_disagreement` print `no` on failure instead of the empty string: `_unreadable_dispute_item`'s self-test case reds, and the run would be scored `failed` with its queue file retired. Restore.

- [ ] **Step 10: Commit**

```bash
git add v2/coordinator/cli.py batch/ v2/tests/
git commit -m "feat(runner): the dispute query, the generator's third condition, and the non-zero branch at shaping"
```

---

## Part D — The proof and the record

### Task 10: The proof

**Files:**
- Modify: `v2/tools/prove_front_half.py` (`assert_journal`'s approval filter and phase-set check; `assert_slices`'s assertion 4; a fifth line)
- Test: `v2/tests/execution/test_prove_front_half.py`

**Interfaces:**
- Consumes: `front.dispute`, `front.visit_of`, `front.shaping_visit`, `front.submissions(..., visit=)`.
- Produces: `assert_dispute(store, run_id) -> list[str]`, called from `main` beside the other four.

- [ ] **Step 1: Write the failing tests**

```python
def test_assertion_4_passes_the_hand_back_then_slice_history(planted):
    s, run = planted.hand_back_then_sliced()
    assert assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_passes_a_rejected_plan_then_a_ruling_in_one_visit(planted):
    s, run = planted.rejected_then_ruled()
    assert assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_reds_on_a_submission_after_a_ruling_in_its_visit(planted):
    s, run = planted.submission_after_ruling_same_visit()
    assert any("one decision per visit" in x for x in assert_slices(s, run, repo=REPO, gh_json=planted.gh))


def test_assertion_4_passes_a_ruling_in_an_earlier_visit_beside_a_filing(planted):
    """The hand-back resolved by slicing: visit 1 ruled, visit 2 filed."""
    s, run = planted.ruled_then_directed_then_sliced()
    assert assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_reds_on_a_filing_beside_a_newer_ruling(planted):
    s, run = planted.filed_then_ruled()
    assert any("the last visit's decision" in x for x in assert_slices(s, run, repo=REPO, gh_json=planted.gh))


def test_the_phase_set_admits_a_spec_before_the_hand_back(planted):
    s, run = planted.revision_turn_hand_back_then_sliced()
    assert assert_journal(s, run, mode="human") == []


def test_the_phase_set_reds_on_a_spec_after_the_hand_back(planted):
    s, run = planted.spec_after_the_hand_back_then_sliced()
    assert any("artifact_submitted" in x for x in assert_journal(s, run, mode="human"))


def test_approval_mode_admits_the_approval_and_its_park(planted):
    s, run = planted.disagreed_then_approved_then_merged()
    assert assert_journal(s, run, mode="approval") == []
    assert any("human facts present" in x for x in assert_journal(s, run, mode="zero"))


def test_approval_mode_still_refuses_a_direction(planted):
    s, run = planted.disagreed_then_directed_then_merged()
    assert any("beyond the approval" in x for x in assert_journal(s, run, mode="approval"))


def test_the_fifth_line_passes_an_answered_hand_back(planted):
    s, run = planted.hand_back_then_sliced()
    assert assert_dispute(s, run) == []


def test_the_fifth_line_skips_a_superseded_hand_back(planted):
    s, run = planted.hand_back_then_revise_bundle()
    assert assert_dispute(s, run) == []


def test_the_fifth_line_reds_on_a_second_hand_back_in_one_epoch(planted):
    s, run = planted.two_hand_backs_in_one_epoch()
    assert any("a second reshape_requested" in x for x in assert_dispute(s, run))


def test_the_fifth_line_reds_on_a_spec_after_an_unresolved_ruling(planted):
    s, run = planted.spec_between_the_disputed_ruling_and_nothing()
    assert any("between the disputed ruling and the resolution" in x for x in assert_dispute(s, run))


def test_the_fifth_line_reds_on_a_direction_before_the_disputed_ruling(planted):
    s, run = planted.direction_before_the_disputed_ruling()
    assert any("unanswered" in x for x in assert_dispute(s, run))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/execution/test_prove_front_half.py -q -k "assertion_4 or fifth_line or phase_set or approval_mode"`
Expected: FAIL — `assert_dispute` does not exist, and assertion 4 is per epoch.

- [ ] **Step 3: `assert_journal`'s two amendments**

The approval filter:

```python
    if mode == "approval":
        extra = [f for f in human if not (
            (f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") in ("approve", "approve_one_piece"))
            or (f.kind == EventKind.PARKED and f.payload.get("reason") in ("gate", "disagreement"))
        )]
```

A direction stays excluded: a run resolved by a direction proves only under `--expect-human`, as every directed run does.

The phase-set check, in the `sliced_parent` arm:

```python
    if sliced_parent:
        hb = next((f for f in front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, n)), None)
        # Revision 16: a hand-back from a REVISION turn means the epoch
        # legitimately holds a spec submission -- before the hand-back, never
        # after it. A one-piece run resolved by approve_one_piece keeps the
        # one-piece rule, because it is not a sliced parent.
        allowed = {"slices"} if hb is None else {"slices", "spec"}
        late_spec = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
                     if f.payload.get("epoch") == n and f.payload["phase"] == "spec"
                     and hb is not None and f.seq > hb.seq]
        if set(subs) - allowed or "slices" not in subs or late_spec \
                or subs["slices"] != front.accepted_slices_hash(store, run_id, n):
            fails.append(f"artifact_submitted: a sliced parent's final epoch submits the accepted slice plan "
                         f"and at most a spec preceding its hand-back: {subs}")
```

- [ ] **Step 4: Assertion 4 in three clauses**

Replacing the current epoch loop in `assert_slices`:

```python
    # 4. One decision per VISIT, and every filing in the last epoch
    #    (shaping spec §6, revision 16). Three clauses.
    # (a) No filing outside the final epoch: a superseded epoch never filed,
    #     since revise_bundle is refused from `sliced`.
    for f in store.facts_of_kind(run_id, EventKind.SLICE_FILED):
        if f.payload.get("epoch") != n:
            fails.append(f"slice_filed in epoch {f.payload.get('epoch')}, outside the final epoch {n}")

    def _visit(f):
        """A fact's visit: the one it carries, or the prefix walk for a fact
        written before revision 16, which gives it visit 1."""
        return f.payload.get("visit") or front.visit_of(store, run_id, f.seq)

    accepted_h = front.accepted_slices_hash(store, run_id, n)
    for e in range(n + 1):
        last = front.shaping_visit(store, run_id, e)
        for v in range(1, last + 1):
            ruling = front.shape_ruling(store, run_id, e, visit=v)
            subs_v = front.submissions(store, run_id, "slices", e, visit=v)
            accepted = [x for x in subs_v if e == n and x.payload["hash"] == accepted_h]
            # (b) At most one decision per visit: the ruling, or the accepted
            #     submission. Rejected submissions BEFORE a ruling are
            #     admitted -- an author may write a plan, have it rejected,
            #     and then rule. A submission AFTER the ruling is not.
            if ruling is not None and accepted:
                fails.append(f"epoch {e} visit {v}: a shape ruling beside the accepted slice plan "
                             "(one decision per visit)")
            if ruling is not None and any(x.seq > ruling.seq for x in subs_v):
                fails.append(f"epoch {e} visit {v}: a slice plan was submitted after the shape ruling "
                             "(one decision per visit)")
    # (c) In the final epoch the LAST visit's decision matches the outcome.
    last = front.shaping_visit(store, run_id, n)
    decided_by_ruling = front.shape_ruling(store, run_id, n, visit=last) is not None
    if decided_by_ruling == bool(filed):
        fails.append(f"final epoch {n} visit {last}: the last visit's decision is a slice_filed or a "
                     "shape ruling, not both and not neither")
```

Clause (b) identifies the accepted submission as the one whose hash is the accepted hash **in the final epoch**; the spec asks for the occurrence the acceptance followed, and hash equality is that occurrence whenever the visit that holds the accepted hash is unique. When a plan rejected in visit 1 is resubmitted and accepted in visit 2, both visits hold the hash — so restrict `accepted` to the visit that holds the `review_verdict {accept}` the approval followed:

```python
            acc_v = front.newest_review_verdict(store, run_id, "slices", e, visit=v)
            accepted = [x for x in subs_v
                        if e == n and x.payload["hash"] == accepted_h
                        and acc_v is not None and acc_v.payload.get("verdict") == "accept"]
```

- [ ] **Step 5: The fifth line**

```python
def assert_dispute(store, run_id: str) -> list[str]:
    """The hand-back was answered (shaping spec §6, revision 16).

    Over each epoch that holds a hand-back and is not superseded by a later
    `bundle_revised` -- a superseded bundle owes nothing. The hand-back must
    be answered: by a slice plan accepted and filed after it, or by a
    disputed ruling followed by its resolution. And no `spec` submission may
    fall between the disputed ruling and the resolution.
    """
    fails: list[str] = []
    n = front.epoch(store, run_id)
    for e in range(n + 1):
        hbs = front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, e)
        if not hbs:
            continue
        if len(hbs) > 1:
            fails.append(f"epoch {e}: a second reshape_requested; one hand-back per bundle is the kernel's guard")
        if e != n:
            continue                      # superseded: the epoch owes nothing
        d = front.dispute(store, run_id, e)
        filed_after = [f for f in front.epoch_facts(store, run_id, EventKind.SLICE_FILED, e)
                       if f.seq > d.hand_back.seq]
        if d.disputed is None:
            if not filed_after:
                fails.append(f"epoch {e}: the hand-back is unanswered -- no disputed ruling and no filing after it")
            continue
        if d.resolution is None:
            fails.append(f"epoch {e}: the hand-back is unanswered -- a disputed ruling with no resolution")
            continue
        between = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
                   if f.payload.get("epoch") == e and f.payload["phase"] == "spec"
                   and d.disputed.seq < f.seq < d.resolution.seq]
        if between:
            fails.append(f"epoch {e}: a spec was submitted between the disputed ruling and the resolution")
    return fails
```

`main` calls it beside the other four, and `reshape_requested` joins the output facts the proof checks against the turn's end and its stop — find that list in `assert_journal` and add the kind.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 7: Mutations**

Every new condition gets its planted defect, as §8 requires. Drop clause (c)'s `visit=last`: `test_assertion_4_passes_a_ruling_in_an_earlier_visit_beside_a_filing` reds. Drop the `e != n` skip in `assert_dispute`: `test_the_fifth_line_skips_a_superseded_hand_back` reds. Restore both, and state in the review package which line was mutated and which test went red.

- [ ] **Step 8: Commit**

```bash
git add v2/tools/prove_front_half.py v2/tests/execution/
git commit -m "feat(proof): assertion 4 over visits, the phase-set and approval amendments, and the dispute line"
```

---

### Task 11: The provenance table, the count, and the design docs

**Files:**
- Modify: `docs/design/provenance-table.md` (four amendments and one new row)
- Modify: `docs/design/2026-08-23-v2-kernel-design.md` (the asserted-row sentence)
- Modify: `v2/tests/kernel/test_provenance.py` (the residual set, the count-word map)
- Modify: `docs/design/ARCHITECTURE.md` (gap 17)

**Interfaces:** none. This task closes the artefacts the other ten changed, and `test_provenance.py` fails the build until it does.

The table is parsed by a test, not read as documentation: a new asserted input fails `test_the_residuals_are_the_ones_we_know_about` rather than joining a list nobody re-reads, and the count in the kernel design's prose is checked against the table by `test_the_spec_and_the_table_agree_on_the_count`.

- [ ] **Step 1: Run the suite to see it fail**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests/kernel/test_provenance.py -q`
Expected: FAIL after Task 6 — `cmd.payload['detail']` is asserted and not in the declared set.

- [ ] **Step 2: The four amendments**

In `docs/design/provenance-table.md`:

- `cmd.payload['reasoning']` — the *Enters at* column gains `request_reshape`. Same reason, same row: the model's words.
- `cmd.payload['cursor_item_id']` — the *Enters at* column gains `approve_one_piece`, and the reason gains the sentence that this command is stricter: the other human commands tolerate its absence and this one refuses it.
- `cmd.payload['reason']` — the reason gains the note that `disagreement` is additionally bounded to a run whose dispute is unresolved, so the park's acceptance depends on journal state beyond its from-set.
- A new row, in the *Model and human words* section:

```markdown
| `cmd.payload['detail']` | `record_author_empty` | asserted | **Declared residual.** The coordinator's own reading of why a `reshape.md` did not parse, rendered into the retry's brief and nowhere else. Shape-checked (a string) and bound by nothing the kernel holds; the kernel binds the turn it belongs to. Closing it would mean the kernel parsing the file, which is the coordinator's job. |
```

- [ ] **Step 3: The count**

The table now has **thirty** asserted rows. In `docs/design/2026-08-23-v2-kernel-design.md`, change `**Twenty-nine rows are asserted after Milestone 1**` to `**Thirty rows are asserted after Milestone 1**`, and add to that sentence's enumeration, after the shaping phase's four: "and revision 16's one: the coordinator's reading of why a hand-back file did not parse." The following sentence's split also moves: nineteen residuals becomes twenty, and the permanent ten is unchanged.

- [ ] **Step 4: The test**

In `v2/tests/kernel/test_provenance.py`, add to the asserted set:

```python
        # Revision 16: the coordinator's reading of why a reshape.md did not
        # parse. A declared residual -- closing it would mean the kernel
        # parsing the file.
        "cmd.payload['detail']",
```

and to the count-word map:

```python
             # Revision 16's one: the hand-back file's parse failure.
             "thirty": 30,
```

- [ ] **Step 5: The architecture note**

In `docs/design/ARCHITECTURE.md`, gap 17 records the one-piece gap as "designed". Change it to "closed", naming the commands and the proof line, and cite the spec's revision 16 rather than restating its rules — the spec is the authority and a second copy drifts.

- [ ] **Step 6: Run the whole suite and the self-test**

Run: `cd v2 && uv run --with pytest --with pyyaml python -m pytest tests -q && cd /Users/jonw/bircher && bash batch/run-queue.sh --self-test 2>&1 | tail -5`
Expected: both PASS. The suite is the baseline plus this branch's new tests; state the final count in the report.

- [ ] **Step 7: Commit**

```bash
git add docs/ v2/tests/kernel/test_provenance.py
git commit -m "docs: the provenance table, the asserted-row count, and gap 17 closed"
```
