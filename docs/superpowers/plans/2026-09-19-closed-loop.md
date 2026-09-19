# The Back Half Closes Its Own Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run whose pull request is open cannot end; every wave resumes it and performs one authorised step (wait, review, repair, merge, park) until it merges, a person stops it, or it parks for no progress.

**Architecture:** The kernel gains one command, `request_repair`, the only door back to `planned`; a back-half journal reader (`v2/kernel/back.py`) answers "is the PR open", "which verdict is on this head", "would this repair be the third identical in a row". A pure `next_step` in the coordinator names the one next action from that journal plus ground truth the CLI reads from GitHub. The batch runner's repair loop becomes a step loop: derive when a review is owed, dispatch a repair session when a repair is owed, merge on PASS, park on no progress, and never record a run outcome while the PR is open. Waves resume every open back-half run. The revision bound goes.

**Tech Stack:** Python 3 (`cd v2 && python -m pytest -q`), bash (`bash batch/run-queue.sh --self-test`, hermetic, about six minutes, fake `gh` on PATH), SQLite kernel journal, GitHub REST via `gh`.

**Spec:** `docs/superpowers/specs/2026-09-19-back-half-closed-loop-design.md`, sections 1, 2 and 3 (the second of that spec's five PRs; section 4 landed as PR #102). Adoption of orphan PRs, state-derived labels and the round log (section 5) and the E11 proof (section 6) belong to the next PR; this one leaves the park notice on the issue and the `bircher:parked` label in place because a park without a notice is a hang.

## Global Constraints

- No AI attribution anywhere: no `Co-Authored-By`, no `Claude-Session`, no "Generated with" in commit messages or the PR body. Every commit is authored as `abedegno <jon@jonwilliams.org.uk>` (`git -c user.name=abedegno -c user.email=jon@jonwilliams.org.uk commit ...`).
- `request_repair` is legal from `implementing` and `reviewing` only, its destination is `planned`, its `cause` is one of `ci_red`, `review_fail`, `plan_conformance`, and it is the ONLY command that moves a back-half run to `planned`.
- `record_review` no longer transitions a back-half run: `accept`, `request_revision` and `reject` all leave it at `reviewing`.
- `record_run_outcome` is refused from an active state when the run holds an implementation output and its newest PR observation is absent or `open`.
- `park` is legal from `planned`, `implementing` and `reviewing`; `PARK_REASONS` gains `no_progress`; `grant_round` is legal from those three states too.
- A finding fingerprint is `sha1(path + " " + sentence)` where `path` is the finding's first file path lower-cased with its line numbers stripped, and `sentence` is the finding's first sentence with whitespace collapsed and every path token normalised the same way; a CI evidence item is a failing job name. `no_progress` means the repair about to be requested would be the third in a row, since the newest round grant, with the same cause and the same evidence set.
- The derivation tuple grows from ten to thirteen fields: `outcome|review|note|sha|ci|ci_first|resubmissions|pr|merge_base|delta_digest|fingerprints|pr_state|failing_jobs`; the bash width guard and every reader move to thirteen.
- The back-half revision bound (`BIRCHER_MAX_REVISIONS`, `_max_revisions`, `revisions_left`, the `revisions` CLI mode) is removed; rounds are counted from the journal for the scorecard only.
- Every GitHub mutation goes through the effect lifecycle (`_effect` in bash, `perform_effect` in Python).
- After any edit to `batch/run-queue.sh`, run `python3 tools/repoint-citations.py <the commit the edit started from>` exactly once per baseline (the tool is not idempotent), hand-fix `UNRESOLVED` lines, and commit `docs/design/effect-site-inventory.md` and `docs/design/scar-effect-matrix.md` with the edit.
- The front half and the shaping phase do not change. The muesli `review-gate` workflow does not change.
- `source <(...)` does not work in this harness; use a temp file. `tests/kernel/test_identity_precondition.py` needs `pyyaml` in the local venv only. Run the Python suite with the sandbox disabled (in-sandbox runs show 82 pre-existing network-denied failures).

---

## File structure

| file | responsibility after this plan |
|---|---|
| `v2/kernel/back.py` (new) | read the back half out of the journal: implementation output, CI and PR observations, verdicts by head, repair rounds, the no-progress judgement |
| `v2/kernel/authz.py` | `request_repair`; `pr_state` on CI observations; the ending rule; back-half parks and grants; frozen back-half review destinations |
| `v2/kernel/commands.py`, `v2/kernel/events.py` | `REPAIR_REQUESTED` fact; `fingerprints` on `review_verdict` (schema 3) |
| `v2/coordinator/attest.py` | `fingerprints(text)` beside `blocking_count` |
| `v2/coordinator/outcome.py` | `Derived` carries `fingerprints`, `pr_state` and `failing_jobs` (thirteen fields); the head on every outcome; `revisions_left` gone |
| `v2/coordinator/step.py` (new) | `Ground`, `Step`, `next_step` |
| `v2/coordinator/cli.py` | `step`, `back-state`, `session-last-item`, `park-notice`, `park-reply` subcommands; `derive` loses `--revisions-left`; `revisions` mode removed |
| `v2/coordinator/observe.py` | `classify` without the red-CI short circuit and without `revise` |
| `v2/coordinator/human.py`, `v2/coordinator/phases.py` | `stop`/`retry` at back-half states; the `no_progress` park notice |
| `batch/lib/kernel-client.sh` | `_kernel_record_ci` gains `pr_state`, `failing_jobs`, `pr`; `_kernel_record_review` gains `fingerprints`; `_kernel_request_repair`, `_kernel_park_back`, `_kernel_back_state` |
| `batch/run-queue.sh` | `_step_loop`, `_merge_step`, `_finish_pass`, `_park_back_half`, `_resume_back_half`, `_back_half_state`; the repair loop and its allowance go; thirteen-field readers |
| `batch/issues-to-queue.sh` | the sweep re-queues open back-half runs with implementation output |
| tests | `v2/tests/kernel/test_back_half_loop.py`, `v2/tests/coordinator/test_step.py`, `v2/tests/execution/test_step_loop.py` (new); `test_attest.py`, `test_outcome.py`, `test_observe.py`, `test_repair_loop.py`, `test_human.py`, execution tests, the bash self-test (modified) |
| docs | `docs/design/ARCHITECTURE.md` §3.3 and the repair-loop subsection of §9; a superseded note on `docs/superpowers/specs/2026-08-31-repair-loop-design.md` |

Task order keeps the tree green at every commit: additive kernel work first (Tasks 1 to 4), the pure step function (5), the CLI (6), the loop's parts as unused functions (7), the human replies (8), then the one wiring commit that replaces `run_item`'s loop, resumes back-half runs and freezes the verdict destinations (9), the removal of the bound (10), and docs and gates (11).

---

### Task 1: `request_repair`, the one door back to `planned`

**Files:**
- Modify: `v2/kernel/authz.py` (`_TRANSITIONS` near line 179; the `authorize()` chain near line 1226)
- Modify: `v2/kernel/commands.py` (`COMMAND_NAMES` near line 54; `_side_fact` near line 292)
- Modify: `v2/kernel/events.py` (`EventKind`, `SCHEMA_VERSIONS`)
- Test: `v2/tests/kernel/test_back_half_loop.py` (new)

**Interfaces:**
- Produces: command `request_repair` with payload `{"cause": "ci_red"|"review_fail"|"plan_conformance", "head_sha": <40 hex>, "evidence": [str, ...]}`, legal from `implementing`/`reviewing`, destination `planned`; fact `EventKind.REPAIR_REQUESTED = "repair_requested"` with payload `{"cause", "head_sha", "evidence", "round", "generation"}` where `round` is one more than the number of prior `repair_requested` facts.
- Consumed by Tasks 3, 5, 7.

- [ ] **Step 1: Write the failing tests.** Create `v2/tests/kernel/test_back_half_loop.py`, reusing the helpers `test_review_r3b.py` defines (copy `_store`, `_sub`, `_to_implementing`, `BASE`, `HEAD`, `BUNDLE` and their imports verbatim from that file's lines 1-58; do not import them across test files):

```python
"""The back half closes its own loop (spec §1-§3): refusals first."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.store import Store
from tests.kernel.front import Front

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    Front(s, "r", base_sha=BASE)
    return s


def _sub(s, name, key, actor=None, **payload):
    if name == "request_merge":
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    if name == "record_review":
        payload.setdefault("phase", "implementation")
    role = Role.REVIEWER if name == "record_review" else Role.IMPLEMENTER
    if actor is None:
        actor = "codex" if role == Role.REVIEWER else "claude"
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _to_implementing(s):
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    spec = put_artifact(s, b"# spec")
    _sub(s, "start_implementation", "k3", actor="claude")
    _sub(s, "record_implementation_output", "k3o", actor="claude", artifact_hash=spec)
    return s, spec


def _repair(s, key, **over):
    payload = {"cause": "ci_red", "head_sha": HEAD, "evidence": ["server (go)"]}
    payload.update(over)
    return _sub(s, "request_repair", key, actor="claude", **payload)


def test_request_repair_returns_an_implementing_run_to_planned():
    s, _ = _to_implementing(_store())
    assert _repair(s, "rr1").accepted
    assert s.run_state("r") == "planned"
    fact = [f for f in s.facts_for("r") if f.kind == EventKind.REPAIR_REQUESTED][-1]
    assert fact.payload["cause"] == "ci_red"
    assert fact.payload["head_sha"] == HEAD
    assert fact.payload["evidence"] == ["server (go)"]
    assert fact.payload["round"] == 1


def test_a_second_repair_counts_its_round():
    s, _ = _to_implementing(_store())
    _repair(s, "rr1")
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "rr2", cause="review_fail", evidence=["ab" * 20])
    facts = [f for f in s.facts_for("r") if f.kind == EventKind.REPAIR_REQUESTED]
    assert [f.payload["round"] for f in facts] == [1, 2]


def test_request_repair_is_refused_from_planned():
    s = _store()
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1")


def test_request_repair_is_refused_from_a_front_half_state():
    s = _store()
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1")


@pytest.mark.parametrize("bad", [
    {"cause": "gremlins"},
    {"head_sha": "abc"},
    {"evidence": "not a list"},
    {"evidence": [1, 2]},
])
def test_a_malformed_repair_request_is_refused(bad):
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _repair(s, "rr1", **bad)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/kernel/test_back_half_loop.py -v`
Expected: FAIL — `request_repair` is not in `COMMAND_NAMES` / `_TRANSITIONS` (a `KeyError` or `NotAuthorized` on every test, including the ones that expect acceptance).

- [ ] **Step 3: The fact kind.** In `v2/kernel/events.py`, add to `EventKind` (beside `RESHAPE_REQUESTED`):

```python
    # The closed loop (spec §1): the one door back to `planned` in the back
    # half, and why it was opened.
    REPAIR_REQUESTED = "repair_requested"
```

and to `SCHEMA_VERSIONS`: `EventKind.REPAIR_REQUESTED: 1,`.

- [ ] **Step 4: The command.** In `v2/kernel/commands.py` add `"request_repair"` to `COMMAND_NAMES` with the comment `# The closed loop (spec §1): red CI, a failed review or a plan-conformance gap returns the run to planned through this one door.` In `_side_fact`, add a branch after the `park` branch:

```python
    elif cmd.name == "request_repair":
        p = cmd.payload
        prior = store.facts_of_kind(cmd.run_id, EventKind.REPAIR_REQUESTED)
        store.append_fact(
            run_id=cmd.run_id, kind=EventKind.REPAIR_REQUESTED, actor=actor,
            causal_command_id=cmd.idempotency_key,
            payload={"cause": p["cause"], "head_sha": p["head_sha"],
                     "evidence": list(p.get("evidence") or []),
                     "round": len(list(prior)) + 1, "generation": cmd.generation},
        )
```

(`store.facts_of_kind` is used the same way at `authz.py` in the `record_author_empty` block; if it returns an iterator, `len(list(...))` handles it.)

In `v2/kernel/authz.py` add to `_TRANSITIONS`, after `record_ci_observation`:

```python
    # The closed loop (spec §1): the ONE door back to `planned` in the back
    # half. A repair round is an implementation round, so the run re-enters
    # `planned` and `start_implementation` dispatches it. Refused from
    # `planned` itself (nothing to repair yet) and from every front-half
    # state by this from-set.
    "request_repair": (frozenset({"implementing", "reviewing"}), "planned"),
```

add near `PARK_REASONS`:

```python
#: spec §1: why a run is sent back to `planned`.
REPAIR_CAUSES = frozenset({"ci_red", "review_fail", "plan_conformance"})
```

and in `authorize()`'s chain, next to the `park` block:

```python
    if cmd.name == "request_repair":
        if cmd.payload.get("cause") not in REPAIR_CAUSES:
            raise NotAuthorized(f"request_repair cause {cmd.payload.get('cause')!r} is not one of {sorted(REPAIR_CAUSES)}")
        head = cmd.payload.get("head_sha")
        if not isinstance(head, str) or not _FULL_SHA.fullmatch(head):
            raise NotAuthorized("request_repair head_sha must be a 40-hex sha")
        ev = cmd.payload.get("evidence")
        if not isinstance(ev, list) or not all(isinstance(e, str) for e in ev):
            raise NotAuthorized("request_repair evidence must be a list of strings")
        return next_state
```

If `_FULL_SHA` does not exist in `authz.py`, define it at module level as `_FULL_SHA = re.compile(r"[0-9a-f]{40}")` with `import re`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd v2 && python -m pytest -q tests/kernel -x`
Expected: all PASS, including `test_every_command_declares_its_legal_states` (the destination is fixed, so no allow-list entry is needed).

- [ ] **Step 6: Commit**

```bash
git add v2/kernel/authz.py v2/kernel/commands.py v2/kernel/events.py v2/tests/kernel/test_back_half_loop.py
git commit -m "request_repair, the one door back to planned"
```

---

### Task 2: Fingerprints, the PR's state and the failing jobs ride out of the derivation and into the journal

**Files:**
- Modify: `v2/coordinator/attest.py` (beside `blocking_count`)
- Modify: `v2/coordinator/outcome.py` (`Derived`, `derive`)
- Modify: `v2/kernel/commands.py` (`record_review` payload), `v2/kernel/events.py` (schema 3), `v2/kernel/authz.py` (validation of `fingerprints`, `pr_state`, `failing_jobs`, `pr`)
- Modify: `batch/lib/kernel-client.sh` (`_kernel_ci_status`, `_kernel_record_ci`, `_kernel_record_review`)
- Modify: `batch/run-queue.sh` (`_derived_width_ok`, both `IFS='|' read -r` readers and both fallback tuples, the `_kernel_record_ci`/`_kernel_record_review` calls in `run_item` and in `recover_pr_cmd`, every self-test fixture tuple)
- Test: `v2/tests/coordinator/test_attest.py`, `v2/tests/coordinator/test_outcome.py`, `v2/tests/coordinator/test_repair_loop.py`, `v2/tests/kernel/test_back_half_loop.py`, `v2/tests/execution/test_settled_pr_reaches_the_caller.py`, `v2/tests/execution/test_run_item_argument_wiring.py`, `v2/tests/execution/test_front_half_seam.py`

**Interfaces:**
- Produces: `attest.fingerprints(text: str) -> list[str]`; `Derived.fingerprints: str` (comma-joined hex, FAIL only), `Derived.pr_state: str` (`open|closed|merged|""`), `Derived.failing_jobs: str` (comma-joined names, red only), `Derived.FIELDS == 13`, line order `outcome|review|note|sha|ci|ci_first|resubmissions|pr|merge_base|delta_digest|fingerprints|pr_state|failing_jobs`; `record_ci_observation` payload keys `pr_state` (optional), `failing_jobs` (optional list of str), `pr` (optional string of digits); `review_verdict` payload key `fingerprints` (list or None), schema 3; `_kernel_record_ci <run_id> <generation> <status> <head> [pr_state] [failing_jobs_csv] [pr]`; `_kernel_record_review ... [head_sha] [merge_base_sha] [delta_digest] [fingerprints_csv]` (argument 12); `_kernel_ci_status` passes `pending` and `na` through unchanged (neither is ever green).
- Consumed by Tasks 3, 4, 6, 7, 9.

- [ ] **Step 1: Failing tests for the fingerprints.** Append to `v2/tests/coordinator/test_attest.py`:

```python
import hashlib

from coordinator.attest import fingerprints


def _fp(path, sentence):
    return hashlib.sha1(f"{path} {sentence}".encode("utf-8")).hexdigest()


def test_fingerprints_name_the_first_path_and_first_sentence():
    text = ("Blocking findings\n\n"
            "- `src/main/livePromptRelay.ts:206-209` provides `stopAll()` but it is never called. The window path skips it.\n"
            "- The eight-template cap is vulnerable to concurrent creates. See templates.go.\n\n"
            "Non-blocking findings: None.\n\nVERDICT: FAIL\n")
    assert fingerprints(text) == [
        _fp("src/main/livepromptrelay.ts",
            "`src/main/livepromptrelay.ts` provides `stopAll()` but it is never called."),
        _fp("templates.go", "The eight-template cap is vulnerable to concurrent creates."),
    ]


def test_a_finding_without_a_path_fingerprints_its_sentence_alone():
    text = "Blocking findings\n\n- Timer leak on unmount, seen twice.\n\nVERDICT: FAIL\n"
    assert fingerprints(text) == [_fp("", "Timer leak on unmount, seen twice.")]


def test_fingerprints_ignore_line_numbers_whitespace_and_path_case():
    a = "Blocking findings\n\n- In   Foo/Bar.py:12 the lock is   dropped.\n"
    b = "Blocking findings\n\n- In foo/bar.py:99 the lock is dropped.\n"
    assert fingerprints(a) == fingerprints(b)


def test_fingerprints_count_agrees_with_blocking_count():
    text = "## Blocking findings\n\n1. One.\n2. Two.\n\n## Suggestions\n\n- x\n"
    assert len(fingerprints(text)) == blocking_count(text) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_attest.py -k fingerprint -v`
Expected: FAIL with `ImportError: cannot import name 'fingerprints'`.

- [ ] **Step 3: Implement.** In `v2/coordinator/attest.py`, refactor `blocking_count` so both functions share one walk, and add `fingerprints`:

```python
import hashlib

#: A path-like token: `dir/file.ext` or `file.ext`, optionally `:line` or `:a-b`.
_PATH = re.compile(r"(?:[\w.-]+/)*[\w-]+\.[A-Za-z]{1,6}(?::\d+(?:-\d+)?)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _blocking_items(text: str) -> list[str]:
    """The blocking findings as text, one string per finding: the walk
    `blocking_count` performs, keeping what it counts. Every rule the merged
    counter applies (heading forms, the `None` answer, prose off the phrase,
    closing headings) applies here unchanged, because this IS that walk."""
    items, inside = [], False
    for line in (text or "").splitlines():
        m = _BLOCKING.search(line)
        if m:
            inside = True
            rest = m.group(1).strip()
            if rest and not _NONE.match(rest) and not _PROSE_TAIL.match(rest):
                items.append(rest)
            continue
        if inside and _OTHER_SECTION.search(line):
            break
        if inside and _ITEM.match(line):
            items.append(_MARKER.sub("", line, count=1).strip())
    return items


def blocking_count(text: str) -> int:
    return len(_blocking_items(text))


def fingerprints(text: str) -> list[str]:
    """One fingerprint per blocking finding (closed-loop spec §3): sha1 of the
    first file path, lower-cased and stripped of its line numbers, joined by
    one space to the finding's first sentence with whitespace collapsed and
    every path token in it normalised the same way -- so a finding that
    merely moved down twelve lines after a repair fingerprints the same, and
    one that moved to another file does not."""
    out = []
    for item in _blocking_items(text):
        m = _PATH.search(item)
        path = m.group(0).split(":")[0].lower() if m else ""
        sentence = " ".join(_SENTENCE_END.split(item.strip(), maxsplit=1)[0].split())
        sentence = _PATH.sub(lambda mm: mm.group(0).split(":")[0].lower(), sentence)
        out.append(hashlib.sha1(f"{path} {sentence}".encode("utf-8")).hexdigest())
    return out
```

Keep the regex names the merged code uses (`_BLOCKING`, `_OTHER_SECTION`, `_ITEM`, `_NONE`) and whatever it named its "prose continuing off the phrase" rule (`_PROSE_TAIL` above stands for that name). If `blocking_count`'s walk today counts a heading-line remainder or an item under a condition the sketch above does not show, keep the merged condition: the refactor must leave every existing `test_attest.py` fixture green.

- [ ] **Step 4: Run the attest tests**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_attest.py -v`
Expected: every pre-existing fixture still passes; the four new tests pass.

- [ ] **Step 5: Failing tests for the thirteen-field line.** In `v2/tests/coordinator/test_outcome.py` change `test_the_tuple_has_ten_fields_in_order` and `test_the_line_has_ten_fields_and_carries_the_range` to expect thirteen (rename them `..._thirteen_...`), and add:

```python
def test_the_line_has_thirteen_fields_with_fingerprints_state_and_jobs():
    effects = []
    out = "Blocking findings\n\n- a.go:1 One.\n- b.go:2 Two.\n\nVERDICT: FAIL\n"
    r = derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="FAIL", out=out))
    fields = r.as_line().split("|")
    assert len(fields) == 13 == Derived.FIELDS
    assert fields[10].count(",") == 1 and len(fields[10]) == 81
    assert fields[11] == "open" and fields[12] == ""


def test_a_pass_carries_no_fingerprints():
    assert derive("i", "c", "7", "", deps=_green_pass_deps([])).fingerprints == ""


def test_pr_state_is_lower_cased_and_merged_wins():
    d = _green_pass_deps([])
    d.pr_state = lambda pr: ("MERGED", "2026-09-19T00:00:00Z")
    assert derive("i", "c", "7", "", deps=d).pr_state == "merged"


def test_a_discarded_closed_pr_still_reports_its_state():
    # The settle drops a CLOSED-unmerged PR and finds nothing else; the runner
    # still has to hear "closed", or the run can never end (spec §1).
    r = derive("i1", "i1", "7", "", deps=_deps(pr_state=lambda pr: ("CLOSED", "null")))
    assert r.pr == "" and r.outcome == "timeout" and r.pr_state == "closed"


def test_red_ci_names_its_failing_jobs_sorted_and_ignoring_the_ignored():
    d = _deps(checks=lambda pr: "server (go)|fail\nclient (node)|pass\nlint|cancel")
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.ci == "red" and r.failing_jobs == "lint,server (go)"


def test_green_ci_names_no_failing_jobs():
    assert derive("i1", "i1", "7", "", deps=_deps()).failing_jobs == ""
```

In `v2/tests/coordinator/test_repair_loop.py` change the `Derived.FIELDS == 10` assertion to `13`. `test_settled_pr_reaches_the_caller.py` compares the shell's reader against `Derived.FIELDS`, so it goes red until Step 8.

- [ ] **Step 6: Widen `Derived` and `derive`.** In `outcome.py`, after `delta_digest: str = ""`:

```python
    #: The blocking findings' fingerprints (closed-loop spec §3), comma-joined
    #: hex, on a FAIL; empty otherwise. The runner records them on the verdict
    #: fact so the no-progress judgement can compare rounds.
    fingerprints: str = ""
    #: The PR's state as this derivation saw it -- open, closed or merged, or
    #: "" when there was no PR to ask about. For the PR the derivation settled
    #: on, or for the caller's own PR when the settle discarded it: a PR a
    #: person closed still has to reach the journal, or the run cannot end
    #: (spec §1).
    pr_state: str = ""
    #: The failing blocking checks' names, comma-joined and sorted, on a red;
    #: empty otherwise. A `ci_red` repair's evidence (spec §3).
    failing_jobs: str = ""
```

`FIELDS = 13`; `as_tuple` and `as_line` append `self.fingerprints, self.pr_state, self.failing_jobs` in that order, and the `as_line` docstring says THIRTEEN. Add two helpers above `derive`:

```python
def _failing_names(checks: str, ignore: str) -> list[str]:
    """The failing blocking checks' names from gh's `name|bucket` rows, with
    the ignore pattern applied as `keep_blocking` applies it."""
    from coordinator.ci import drop_ignored
    out = set()
    for line in drop_ignored(checks or "", ignore).splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[1].strip() in ("fail", "cancel"):
            out.add(parts[0].strip())
    return sorted(out)


def _pr_state_word(d: Deps, pr: str) -> str:
    """open | closed | merged | "" for *pr*."""
    if not pr:
        return ""
    state, merged_at = d.pr_state(pr)
    if merged_at and merged_at != "null":
        return "merged"
    return {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}.get((state or "").upper(), "")
```

In `derive`: keep the caller's PR as `known = pr` before `_settle_pr`; declare `failing = []` beside `merge_base, digest = "", ""`; right after `ci = _settle_ci(item, pr, d, rerun_max)` add `if ci == "red": failing = _failing_names(d.checks(pr), d.ignore)`; after the `classify` call compute `fps = ",".join(attest.fingerprints(reviewer_out)) if verdict == "FAIL" else ""` and `pr_state = _pr_state_word(d, pr or known)`; pass `fingerprints=fps, pr_state=pr_state, failing_jobs=",".join(failing)` as keywords in the final `return Derived(...)`. Update the docstring's "Returns the ten fields" to thirteen.

- [ ] **Step 7: The kernel stores them.** `v2/kernel/commands.py`: the `record_review` payload dict gains `"fingerprints": cmd.payload.get("fingerprints"),` with the comment `# closed-loop spec §3: one per blocking finding, so rounds can be compared.`; `SCHEMA_VERSIONS[EventKind.REVIEW_VERDICT]` becomes `3` (comment `# 3: fingerprints (closed loop)`). In `authz.py`'s `record_review` block, before it returns its destination:

```python
        fps = cmd.payload.get("fingerprints")
        if fps is not None and (not isinstance(fps, list) or not all(isinstance(x, str) for x in fps)):
            raise NotAuthorized("record_review fingerprints must be a list of strings or absent")
```

and add a `record_ci_observation` block to the `authorize()` chain (there is none today; its only check is the from-set):

```python
    if cmd.name == "record_ci_observation":
        # The PR's state and its failing jobs ride beside the CI status
        # (closed-loop spec §1, §3): an external observation, validated here
        # so `back.py` can trust what it reads.
        ps = cmd.payload.get("pr_state")
        if ps not in (None, "open", "closed", "merged"):
            raise NotAuthorized("record_ci_observation pr_state must be open, closed, merged or absent")
        jobs = cmd.payload.get("failing_jobs")
        if jobs is not None and (not isinstance(jobs, list) or not all(isinstance(j, str) for j in jobs)):
            raise NotAuthorized("record_ci_observation failing_jobs must be a list of strings or absent")
        prn = cmd.payload.get("pr")
        if prn is not None and (not isinstance(prn, str) or not prn.isdigit()):
            raise NotAuthorized("record_ci_observation pr must be a string of digits or absent")
        return next_state
```

Add to `v2/tests/kernel/test_back_half_loop.py`:

```python
def _ci(s, key, status, head=HEAD, pr_state="open", jobs=None, pr="42"):
    return _sub(s, "record_ci_observation", key, actor="claude", status=status,
                head_git_sha=head, pr_state=pr_state, failing_jobs=list(jobs or []), pr=pr)


def test_the_verdict_stores_its_fingerprints():
    s, spec = _to_implementing(_store())
    fp = ["ab" * 20, "cd" * 20]
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec,
         base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1,
         head_sha=HEAD, fingerprints=fp)
    fact = [f for f in s.facts_for("r") if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["fingerprints"] == fp
    assert fact.schema_version == 3


def test_a_ci_observation_carries_the_prs_state_jobs_and_number():
    s, _ = _to_implementing(_store())
    assert _ci(s, "ci1", "failure", jobs=["server (go)", "client (node)"]).accepted
    with pytest.raises(NotAuthorized):
        _ci(s, "ci2", "failure", pr_state="draft")
    with pytest.raises(NotAuthorized):
        _ci(s, "ci3", "failure", pr="forty-two")
```

(Use the same `record_review` payload keys `test_review_r3b.py`'s accepted-review tests use; the ones above mirror `_kernel_record_review`'s JSON.)

- [ ] **Step 8: Widen the bash.** In `batch/lib/kernel-client.sh`, `_kernel_ci_status` gains two arms before `*)`: `pending) printf 'pending' ;;` and `na) printf 'na' ;;` (comment: `# Neither is green: _ci_is_green accepts exactly "success". Recorded as themselves so a closed PR can be observed with no head.`). Then:

```bash
_kernel_record_ci() {  # <run_id> <generation> <status> <head_git_sha> [pr_state] [failing_jobs_csv] [pr]
  local run_id="$1" generation="$2" status="$3" head="$4" pr_state="${5:-}" jobs="${6:-}" pr="${7:-}"
  status=$(_kernel_ci_status "$status")
  # Job names come from `gh pr checks --json name`; quotes and backslashes are
  # stripped rather than escaped, because the value is spliced into JSON.
  jobs=$(printf '%s' "$jobs" | tr -d '"\\')
  local extra=""
  [ -n "$pr_state" ] && extra="$extra,\"pr_state\":\"$pr_state\""
  [ -n "$jobs" ] && extra="$extra,\"failing_jobs\":[\"${jobs//,/\",\"}\"]"
  [ -n "$pr" ] && extra="$extra,\"pr\":\"$pr\""
  # record_ci_observation
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_ci_observation \
    --payload-json "{\"status\":\"$status\",\"head_git_sha\":\"$head\"$extra}"
}
```

(Keep whatever `# record_ci_observation` comment line the merged function carries above its `_kernel command` call: `tests/execution/test_kernel_client.py` reads those.) In `_kernel_record_review` add `fps="${12:-}"` to the second `local` line and extend the range fragment:

```bash
  local range=""
  if [ -n "$head_sha" ]; then
    range="\"head_sha\":\"$head_sha\",\"merge_base_sha\":\"$merge_base\",\"delta_digest\":\"$digest\","
    # Hex digests, comma-joined by the derivation; no escaping needed.
    [ -n "$fps" ] && range="$range\"fingerprints\":[\"${fps//,/\",\"}\"],"
  fi
```

In `batch/run-queue.sh`: `_derived_width_ok` accepts `13` (and its comment says thirteen); `run_item`'s reader becomes `IFS='|' read -r outcome review note observed_head _obs_ci ci_first resubmissions _settled_pr _merge_base _delta_digest _fingerprints _pr_state _failing_jobs <<EOF` with one `local` per new name beside `local _delta_digest=""` and a reset beside `_delta_digest=""` at the top of the loop; `recover_pr_cmd`'s reader gains `r_fingerprints r_pr_state r_failing_jobs`; both fallback tuples gain `|||`; every `_kernel_record_ci` call passes the pr_state, failing-jobs and PR-number arguments (`"$_pr_state" "$_failing_jobs" "$pr"` in `run_item`; the `r_` names and the recovery's PR-number variable in `recover_pr_cmd`); every `_kernel_record_review` call passes the fingerprints as its twelfth argument. Every self-test fixture tuple (search `|green|`, `|unknown||`, `|red|`, `|pending|`) gains `|||`. In `v2/tests/execution/test_front_half_seam.py` and `test_run_item_argument_wiring.py`, the `_heredoc_to_herestring` strings name the reader line exactly: extend both to the thirteen names. Then run `python3 tools/repoint-citations.py <baseline>` once and include the two design docs.

- [ ] **Step 9: Run everything**

Run: `cd v2 && python -m pytest -q` and `bash batch/run-queue.sh --self-test > /tmp/selftest-t2.log 2>&1; echo rc=$?; grep -n "^FAIL" /tmp/selftest-t2.log`
Expected: pytest all green; self-test rc 0 and no `FAIL` lines.

- [ ] **Step 10: Commit**

```bash
git add v2 batch docs/design/effect-site-inventory.md docs/design/scar-effect-matrix.md
git commit -m "Fingerprints, the PR's state and the failing jobs reach the journal"
```

---

### Task 3: The back half, read from the journal; parks and grants in the back half

**Files:**
- Create: `v2/kernel/back.py`
- Modify: `v2/kernel/authz.py` (`PARK_REASONS`, `_TRANSITIONS["park"]`, `_TRANSITIONS["grant_round"]`, `_check_park`)
- Test: `v2/tests/kernel/test_back_half_loop.py`

**Interfaces:**
- Produces, in `kernel/back.py`: `implementation_output_recorded(store, run_id) -> bool`; `latest_ci(store, run_id) -> dict | None` (the newest `record_ci_observation` payload: `status`, `head_git_sha`, `pr_state`, `failing_jobs`); `latest_pr_state(store, run_id) -> str | None`; `verdict_for_head(store, run_id, head) -> fact | None` (newest implementation-phase `review_verdict` whose `head_sha` equals `head`); `repairs_since_grant(store, run_id) -> list` (`repair_requested` facts after the newest `HUMAN_RULING` with `ruling == "grant_round"`); `repair_for_head(store, run_id, head) -> fact | None`; `would_be_third_identical(store, run_id, cause, evidence) -> bool`.
- Produces in `authz.py`: `park` legal from `planned`, `implementing`, `reviewing`; `no_progress` in `PARK_REASONS`, refused unless `back.would_be_third_identical` holds for the park's `cause`/`evidence` payload; `grant_round` legal from the three back-half states.
- Consumed by Tasks 4, 5, 7, 9.

- [ ] **Step 1: Write the failing tests.** Append to `test_back_half_loop.py`:

```python
from kernel import back


# `_ci` is the helper Task 2 added to this file.
def test_back_reads_implementation_output_and_the_newest_observation():
    s = _store()
    assert not back.implementation_output_recorded(s, "r")
    s, _ = _to_implementing(s)
    assert back.implementation_output_recorded(s, "r")
    assert back.latest_ci(s, "r") is None
    _ci(s, "c1", "failure", jobs=["server (go)"])
    _ci(s, "c2", "success", pr_state="merged")
    assert back.latest_ci(s, "r")["status"] == "success"
    assert back.latest_pr_state(s, "r") == "merged"


def test_the_verdict_for_a_head_is_the_newest_on_that_head():
    s, spec = _to_implementing(_store())
    other = "f" * 40
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec,
         base_sha=BASE, context_bundle_hash=BUNDLE, policy_version=1, head_sha=other, fingerprints=["aa" * 20])
    assert back.verdict_for_head(s, "r", HEAD) is None
    assert back.verdict_for_head(s, "r", other).payload["fingerprints"] == ["aa" * 20]


def test_three_identical_repairs_in_a_row_is_no_progress():
    s, _ = _to_implementing(_store())
    ev = ["server (go)"]
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)
    _repair(s, "r1", evidence=ev)
    _sub(s, "start_implementation", "k4", actor="claude")
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)
    _repair(s, "r2", evidence=ev)
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ev)
    assert not back.would_be_third_identical(s, "r", "ci_red", ["client (node)"])
    assert not back.would_be_third_identical(s, "r", "review_fail", ev)


def test_evidence_order_does_not_matter():
    s, _ = _to_implementing(_store())
    _repair(s, "r1", evidence=["a", "b"])
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=["b", "a"])
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ["b", "a"])


def test_a_round_grant_resets_the_window():
    s, _ = _to_implementing(_store())
    ev = ["server (go)"]
    _repair(s, "r1", evidence=ev)
    _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=ev)
    _sub(s, "start_implementation", "k5", actor="claude")
    assert back.would_be_third_identical(s, "r", "ci_red", ev)
    _park(s, "p1", cause="ci_red", evidence=ev)
    _grant(s, "g1")
    assert not back.would_be_third_identical(s, "r", "ci_red", ev)


def test_no_progress_park_needs_the_journal_to_say_so():
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _park(s, "p0", cause="ci_red", evidence=["server (go)"])


def test_park_and_grant_are_legal_in_the_back_half():
    s, _ = _to_implementing(_store())
    ev = ["x"]
    _repair(s, "r1", evidence=ev); _sub(s, "start_implementation", "k4", actor="claude")
    _repair(s, "r2", evidence=ev); _sub(s, "start_implementation", "k5", actor="claude")
    assert _park(s, "p1", cause="ci_red", evidence=ev).accepted
    assert s.run_state("r") == "implementing"
    assert _grant(s, "g1").accepted


def test_no_progress_park_is_refused_from_the_front_half():
    s = _store()
    with pytest.raises(NotAuthorized):
        _park(s, "p1", cause="ci_red", evidence=["x"])
```

with these helpers beside `_repair`, modelled on how `human.py`'s `_human` submits the human's commands (`execute_as_human`) and on `test_review_r3b.py`'s park usage if it has one; otherwise:

```python
from kernel.commands import execute_as_human


def _park(s, key, *, cause, evidence):
    return _sub(s, "park", key, actor="claude", reason="no_progress", cause=cause,
                evidence=list(evidence), session_id=None, cursor_item_id=None)


def _grant(s, key):
    return execute_as_human(s, Command(
        name="grant_round", run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=0, payload={"cursor_item_id": None}))
```

(If `execute_as_human` requires a different `generation` sentinel, use the one `human.py` uses: `HUMAN_GENERATION`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/kernel/test_back_half_loop.py -v`
Expected: the new tests FAIL (`ModuleNotFoundError: No module named 'kernel.back'`; park refused from `implementing`).

- [ ] **Step 3: Write `v2/kernel/back.py`**

```python
"""The back half, read from the journal (closed-loop spec §1-§3).

Everything a step decision needs about a run with a pull request: whether an
implementation output exists, what CI and the PR last looked like, which
verdict sits on a head, and whether the repair about to be requested would
be the third identical one in a row. Pure reads; nothing here writes.
"""

from __future__ import annotations

from kernel.events import EventKind


def _accepted(store, run_id: str, name: str) -> list:
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.COMMAND_ACCEPTED and f.payload.get("command_name") == name]


def implementation_output_recorded(store, run_id: str) -> bool:
    """True once the run has produced an implementation (a pull request)."""
    return bool(_accepted(store, run_id, "record_implementation_output"))


def latest_ci(store, run_id: str) -> dict | None:
    """The newest CI observation's payload, or None before any."""
    obs = _accepted(store, run_id, "record_ci_observation")
    return (obs[-1].payload.get("payload") or {}) if obs else None


def latest_pr_state(store, run_id: str) -> str | None:
    ci = latest_ci(store, run_id)
    return None if ci is None else ci.get("pr_state")


def back_verdicts(store, run_id: str) -> list:
    """Implementation-phase verdicts, oldest first."""
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.REVIEW_VERDICT and f.payload.get("phase") == "implementation"]


def verdict_for_head(store, run_id: str, head: str):
    """The newest verdict recorded against exactly this head, or None."""
    for f in reversed(back_verdicts(store, run_id)):
        if f.payload.get("head_sha") == head:
            return f
    return None


def _newest_grant_seq(store, run_id: str) -> int:
    seq = 0
    for f in store.facts_for(run_id):
        if f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "grant_round":
            seq = f.seq
    return seq


def repairs_since_grant(store, run_id: str) -> list:
    """Repair requests after the newest round grant, oldest first. A grant
    resets the no-progress window (spec §3): the next round is judged
    against itself."""
    since = _newest_grant_seq(store, run_id)
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.REPAIR_REQUESTED and f.seq > since]


def repair_for_head(store, run_id: str, head: str):
    """The newest repair requested against this head, or None."""
    for f in reversed(list(store.facts_of_kind(run_id, EventKind.REPAIR_REQUESTED))):
        if f.payload.get("head_sha") == head:
            return f
    return None


def would_be_third_identical(store, run_id: str, cause: str, evidence) -> bool:
    """spec §3: no progress is the same cause with the same evidence three
    rounds running. True when the two newest repairs since the grant already
    carry this cause and this evidence set, so requesting it again would be
    the third."""
    recent = repairs_since_grant(store, run_id)[-2:]
    if len(recent) < 2:
        return False
    want = (cause, frozenset(evidence or ()))
    return all((f.payload.get("cause"), frozenset(f.payload.get("evidence") or ())) == want for f in recent)
```

(`f.seq` is the fact's journal sequence; `grant_round`'s side fact already records `park_seq`, so `seq` is the field facts expose — confirm the attribute name in `kernel/store.py`'s fact record and use it.)

- [ ] **Step 4: The kernel's legality.** In `authz.py`: add `"no_progress"` to `PARK_REASONS` with the comment `# The closed loop (spec §3): the repair about to be requested would be the third identical in a row.`; change `"park": (FRONT_HALF_STATES | SHAPING_STATES, None)` to `"park": (FRONT_HALF_STATES | SHAPING_STATES | BACK_HALF_STATES, None)` and `grant_round`'s from-set to `frozenset({...existing..., "planned", "implementing", "reviewing"})`, defining near `FRONT_HALF_STATES`:

```python
#: spec §1 (closed loop): the states a run with a pull request moves between.
BACK_HALF_STATES = frozenset({"planned", "implementing", "reviewing"})
```

In `_check_park`, after the `disagreement` block:

```python
    if cmd.payload.get("reason") == "no_progress":
        from kernel import back
        # Like `disagreement`: legal only when the journal itself says so,
        # so a coordinator cannot park a run that is still making progress.
        if not back.would_be_third_identical(store, cmd.run_id, cmd.payload.get("cause"),
                                             cmd.payload.get("evidence") or []):
            raise NotAuthorized("park no_progress: the journal shows progress; the repair is not the third identical")
```

The `park` side fact in `commands.py` keeps its shape; add `"cause": p.get("cause"), "evidence": list(p.get("evidence") or [])` to its payload so the notice can name them.

- [ ] **Step 5: Run the kernel tests**

Run: `cd v2 && python -m pytest -q tests/kernel -x`
Expected: all PASS (the front-half park tests are unaffected: their reasons and states are unchanged).

- [ ] **Step 6: Commit**

```bash
git add v2/kernel/back.py v2/kernel/authz.py v2/kernel/commands.py v2/tests/kernel/test_back_half_loop.py
git commit -m "The back half read from the journal; parks and grants reach it"
```

---

### Task 4: A run with an open pull request cannot end

**Files:**
- Modify: `v2/kernel/authz.py` (the `record_run_outcome` block near line 1322)
- Test: `v2/tests/kernel/test_back_half_loop.py`

**Interfaces:**
- Produces: `record_run_outcome` refused from any `_ALL_ACTIVE` state when `back.implementation_output_recorded` and `back.latest_pr_state` is `None` or `"open"`; unchanged from `merged`/`cancelled`, and from active states without an output or with a `closed`/`merged` observation.
- Consumed by Task 7 (the runner records the outcome only on `done`).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_run_with_an_open_pr_cannot_end():
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="open")
    with pytest.raises(NotAuthorized):
        _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed")


def test_an_unobserved_pr_counts_as_open():
    s, _ = _to_implementing(_store())
    with pytest.raises(NotAuthorized):
        _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed")


def test_a_closed_pr_lets_the_run_end():
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="closed")
    assert _sub(s, "record_run_outcome", "o1", actor="claude", outcome="failed").accepted
    assert s.run_state("r") == "ended"


def test_a_run_without_an_implementation_still_ends():
    s = _store()
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    assert _sub(s, "record_run_outcome", "o1", actor="claude", outcome="timeout").accepted


def test_a_cancelled_run_ends():
    # The generation is taken BEFORE the cancel: the runner records the
    # terminal fact under the generation it already holds, and `dispatch`
    # may refuse a seat on a cancelled run.
    s, _ = _to_implementing(_store())
    _ci(s, "c1", "failure", pr_state="open")
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    assert submit(s, Command(name="cancel_run", run_id="r", expected_version=s.run_version("r"),
                             idempotency_key="x1", generation=gen, payload={})).accepted
    assert submit(s, Command(name="record_run_outcome", run_id="r", expected_version=s.run_version("r"),
                             idempotency_key="o1", generation=gen, payload={"outcome": "escalated"})).accepted
    assert s.run_state("r") == "ended"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/kernel/test_back_half_loop.py -k "cannot_end or counts_as_open" -v`
Expected: the two refusal tests FAIL (`DID NOT RAISE`).

- [ ] **Step 3: Implement.** In the `record_run_outcome` block of `authorize()`, before `if current == "sliced" and outcome != "sliced":`:

```python
        # The closed loop (spec §1): a run whose pull request is open does not
        # end. Its exits are a merge (from `merged`) or a person's stop (from
        # `cancelled`); both are outside _ALL_ACTIVE and pass this check. An
        # unobserved PR counts as open: silence must not end a run.
        if current in _ALL_ACTIVE:
            from kernel import back
            if back.implementation_output_recorded(store, cmd.run_id) and \
                    back.latest_pr_state(store, cmd.run_id) in (None, "open"):
                raise NotAuthorized(
                    "a run with an open pull request cannot end: merge it, or a person stops it (spec §1)"
                )
```

- [ ] **Step 4: Run the kernel suite and the execution suite**

Run: `cd v2 && python -m pytest -q tests/kernel tests/execution -x`
Expected: all PASS. If an execution test drives `run_item` to `record_run_outcome` with a PR open and asserts the run ended, that test now asserts the old behaviour; change its expectation to a refusal (the runner's `_kernel` wrapper is advisory, so the pass completes and the run stays open) and note it in the report — Task 7 gives the runner its proper ending.

- [ ] **Step 5: Commit**

```bash
git add v2/kernel/authz.py v2/tests/kernel/test_back_half_loop.py v2/tests/execution
git commit -m "A run with an open pull request cannot end"
```

---

### Task 5: `next_step`, pure, replayed

**Files:**
- Create: `v2/coordinator/step.py`
- Test: `v2/tests/coordinator/test_step.py` (new)

**Interfaces:**
- Produces: `Ground(pr_state: str, head: str, ci: str, failing_jobs: tuple, verdict: str | None, fingerprints: tuple)`; `Step(kind: str, cause: str = "", evidence: tuple = (), reason: str = "", redispatch: bool = False)` with `kind` in `wait|review|repair|merge|park|done`; `next_step(store, run_id, ground) -> Step`.
- Consumed by Task 6.

- [ ] **Step 1: Write the failing tests** in `v2/tests/coordinator/test_step.py`. Build journals with the kernel test helpers (copy `_store`, `_sub`, `_to_implementing`, `_ci`, `_repair`, `BASE`, `HEAD`, `BUNDLE` from `tests/kernel/test_back_half_loop.py` verbatim into this file; `sys.path` already makes `kernel` importable from coordinator tests, as `test_outcome.py`'s `_round_number` tests show):

```python
from coordinator.step import Ground, Step, next_step

GREEN = dict(pr_state="open", head=HEAD, ci="green", failing_jobs=(), verdict=None, fingerprints=())


def g(**over):
    return Ground(**dict(GREEN, **over))


def test_a_pending_ci_waits():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(ci="pending")).kind == "wait"


def test_no_head_waits():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(head="")).kind == "wait"


def test_an_unknown_pr_state_waits():
    # Silence must not move a run: "" means the derivation could not ask.
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(pr_state="")).kind == "wait"


def test_red_ci_is_a_repair_not_an_ending():
    # #764's shape: the implementer's session died with CI red on its head.
    s, _ = _to_implementing(_store())
    st = next_step(s, "r", g(ci="red", failing_jobs=("server (go)", "client (node)")))
    assert st == Step("repair", "ci_red", ("client (node)", "server (go)"))


def test_green_without_a_verdict_reviews():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g()).kind == "review"


def test_a_pass_merges():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict="accept", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD)
    assert next_step(s, "r", g(verdict="PASS")).kind == "merge"


def test_a_fail_is_a_review_fail_repair_with_the_fingerprints():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD, fingerprints=["aa" * 20])
    st = next_step(s, "r", g(verdict="FAIL", fingerprints=("aa" * 20,)))
    assert st == Step("repair", "review_fail", ("aa" * 20,))


def test_a_repair_already_requested_on_this_head_is_redispatched_from_planned():
    s, _ = _to_implementing(_store())
    _repair(s, "r1", evidence=["server (go)"])
    assert s.run_state("r") == "planned"
    st = next_step(s, "r", g(ci="red", failing_jobs=("server (go)",)))
    assert st.kind == "repair" and st.redispatch is True and st.cause == "ci_red"


def test_the_769_sequence_repairs_five_times_and_merges():
    # Six verdicts on six heads: FAIL x5 with different findings, then PASS.
    s, spec = _to_implementing(_store())
    heads = [f"{i + 1:02d}" * 20 for i in range(6)]
    for i, h in enumerate(heads[:5]):
        _sub(s, "record_review", f"v{i}", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[f"{i:02x}" * 20])
        st = next_step(s, "r", g(head=h, verdict="FAIL", fingerprints=(f"{i:02x}" * 20,)))
        assert st.kind == "repair", (i, st)
        _sub(s, f"request_repair", f"rr{i}", actor="claude", cause="review_fail", head_sha=h, evidence=list(st.evidence))
        _sub(s, "start_implementation", f"si{i}", actor="claude")
    _sub(s, "record_review", "v5", verdict="accept", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=heads[5])
    assert next_step(s, "r", g(head=heads[5], verdict="PASS")).kind == "merge"


def test_three_identical_fails_park_for_no_progress():
    s, spec = _to_implementing(_store())
    fp = "ab" * 20
    for i in range(2):
        h = f"{i + 1:02d}" * 20
        _sub(s, "record_review", f"v{i}", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[fp])
        _sub(s, "request_repair", f"rr{i}", actor="claude", cause="review_fail", head_sha=h, evidence=[fp])
        _sub(s, "start_implementation", f"si{i}", actor="claude")
    h = "9" * 40
    _sub(s, "record_review", "v2", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[fp])
    st = next_step(s, "r", g(head=h, verdict="FAIL", fingerprints=(fp,)))
    assert st == Step("park", "review_fail", (fp,), reason="no_progress")


def test_a_merged_or_cancelled_run_is_done():
    s, _ = _to_implementing(_store())
    _sub(s, "cancel_run", "x1", actor="claude")
    assert next_step(s, "r", g()).kind == "done"


def test_a_closed_pr_is_done_too():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(pr_state="closed")).kind == "done"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_step.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coordinator.step'`.

- [ ] **Step 3: Write `v2/coordinator/step.py`**

```python
"""The one next action for a run with a pull request (closed-loop spec §2).

Pure with respect to the world: `ground` is what the CLI read from GitHub,
`store` is the journal, and the answer is one Step. The runner performs it
and records the resulting fact; the next call starts again from the journal.
"""

from __future__ import annotations

from dataclasses import dataclass

from kernel import back


@dataclass(frozen=True)
class Ground:
    pr_state: str            # open | closed | merged | "" (unknown)
    head: str                # the PR's head, 40 hex, or ""
    ci: str                  # green | red | pending | na
    failing_jobs: tuple      # names of the failing jobs when red
    verdict: str | None      # PASS | FAIL | None, for THIS head, from the journal
    fingerprints: tuple      # that verdict's fingerprints


@dataclass(frozen=True)
class Step:
    kind: str                # wait | review | repair | merge | park | done
    cause: str = ""          # ci_red | review_fail, for repair and park
    evidence: tuple = ()
    reason: str = ""         # park reason
    redispatch: bool = False # repair: already requested for this head; dispatch the session, request nothing


_TERMINAL = frozenset({"merged", "cancelled", "ended"})


def next_step(store, run_id: str, g: Ground) -> Step:
    state = store.run_state(run_id)
    if state in _TERMINAL:
        return Step("done")
    if g.pr_state in ("closed", "merged"):
        # The PR left the loop by another hand; the runner records the outcome.
        return Step("done")
    # No head, or a PR state the derivation could not read: nothing to act
    # on. (The round's session is the runner's concern: it cancels it before
    # deriving, as it always has.)
    if not g.head or g.pr_state != "open":
        return Step("wait")
    if state == "merge_requested":
        return Step("merge")
    if g.ci in ("pending", "na"):
        return Step("wait")

    if g.ci == "red":
        cause, evidence = "ci_red", tuple(sorted(g.failing_jobs))
    elif g.verdict is None:
        return Step("review")
    elif g.verdict == "PASS":
        return Step("merge")
    else:
        cause, evidence = "review_fail", tuple(g.fingerprints)

    # A repair already requested against this head and not yet worked: the
    # run sits at `planned`; dispatch the session, request nothing new.
    if state == "planned" and back.repair_for_head(store, run_id, g.head) is not None:
        return Step("repair", cause, evidence, redispatch=True)
    if back.would_be_third_identical(store, run_id, cause, evidence):
        return Step("park", cause, evidence, reason="no_progress")
    return Step("repair", cause, evidence)
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_step.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/coordinator/step.py v2/tests/coordinator/test_step.py
git commit -m "next_step: the one next action, from the journal and the ground"
```

---

### Task 6: The CLI reads the journal for the runner: `step`, `back-state`, `session-last-item`, `park-notice`

**Files:**
- Modify: `v2/coordinator/cli.py` (parsers near the `parked`/`state` group; handlers beside `a.mode == "parked"`)
- Modify: `v2/coordinator/phases.py` (`PARK_NEEDS`, `park_notice_body`)
- Test: `v2/tests/coordinator/test_step.py`

**Interfaces:**
- Produces: `python -m coordinator.cli step --db D --run-id R --pr-state S --head H --ci C [--failing-jobs csv]` printing `kind|cause|evidence_csv|reason|redispatch` (`redispatch` is `yes`/`no`), reading the verdict and fingerprints for `H` from the journal; `back-state --db --run-id` printing `pr|pr_state|head|artifact` from the newest CI observation and `store.current_artifact`; `session-last-item --server --id` printing the session's last item id (or nothing); `park-notice --db --run-id` printing the current park's notice body (rc 1 when there is no park); `PARK_NEEDS["no_progress"]`; a `no_progress` notice naming the cause and evidence; a `no_verdict` notice at the implementation phase that offers `retry` or `stop`.
- Consumes: Task 5's `next_step`, Task 3's `back.py`.
- Consumed by Tasks 7, 9.

- [ ] **Step 1: Write the failing tests.** Append to `v2/tests/coordinator/test_step.py` (this file already holds the kernel helpers copied in Task 5; add `_ci` and `_park` copied verbatim from `tests/kernel/test_back_half_loop.py`, and `import pytest`, `from coordinator.cli import main`, `from kernel import front`, `from kernel.ids import Clock`, `from kernel.store import Store`, `from tests.kernel.front import Front`):

```python
def _file_store(tmp_path):
    db = str(tmp_path / "k.db")
    s = Store.open(db, clock=Clock(start_us=1))
    Front(s, "r", base_sha=BASE)
    return s, db


def test_the_step_cli_prints_the_step_line(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD,
                 "--ci", "red", "--failing-jobs", "server (go),client (node)"]) == 0
    assert capsys.readouterr().out == "repair|ci_red|client (node),server (go)||no"


def test_the_step_cli_reads_the_verdict_and_fingerprints_from_the_journal(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD, fingerprints=["ab" * 20])
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD, "--ci", "green"]) == 0
    assert capsys.readouterr().out == f"repair|review_fail|{'ab' * 20}||no"


def test_the_step_cli_says_redispatch_for_a_repair_already_requested(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    _repair(s, "r1", evidence=["server (go)"])
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD,
                 "--ci", "red", "--failing-jobs", "server (go)"]) == 0
    assert capsys.readouterr().out == "repair|ci_red|server (go)||yes"


def test_the_step_cli_refuses_a_run_the_kernel_does_not_hold(tmp_path):
    _, db = _file_store(tmp_path)
    with pytest.raises(Exception):
        main(["step", "--db", db, "--run-id", "nope", "--pr-state", "open", "--head", HEAD, "--ci", "green"])


def test_back_state_prints_the_newest_observation_and_the_artifact(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    _ci(s, "c1", "failure", jobs=["x"])
    assert main(["back-state", "--db", db, "--run-id", "r"]) == 0
    assert capsys.readouterr().out == f"42|open|{HEAD}|{spec}"


def test_back_state_before_any_observation_is_blank_but_for_the_artifact(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    assert main(["back-state", "--db", db, "--run-id", "r"]) == 0
    assert capsys.readouterr().out == f"|||{spec}"


def test_session_last_item_prints_the_last_id(monkeypatch, capsys):
    monkeypatch.setattr("coordinator.session.list_items",
                        lambda server, sid, **kw: [{"id": "a", "role": "user", "text": "x"},
                                                   {"id": "b", "role": "assistant", "text": "y"}])
    assert main(["session-last-item", "--server", "http://x", "--id", "s1"]) == 0
    assert capsys.readouterr().out == "b"


def test_the_no_progress_notice_names_the_cause_and_the_two_words(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    for k in ("r1", "r2"):
        _repair(s, k, evidence=["server (go)"])
        _sub(s, "start_implementation", "k" + k, actor="claude")
    _park(s, "p1", cause="ci_red", evidence=["server (go)"])
    assert main(["park-notice", "--db", db, "--run-id", "r"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("bircher: parked no_progress")
    assert "ci_red" in out and "server (go)" in out and "`retry`" in out and "`stop`" in out


def test_park_notice_without_a_park_is_rc_1(tmp_path):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    assert main(["park-notice", "--db", db, "--run-id", "r"]) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_step.py -k "cli or back_state or last_item or notice" -v`
Expected: FAIL (argparse rejects the unknown modes with `SystemExit`).

- [ ] **Step 3: The parsers.** In `cli.py`, next to the `parked`/`state` group:

```python
    # The closed loop (spec §2): the runner's window onto the journal. Every
    # mode is read-only; the runner performs what `step` names and records
    # the resulting fact itself.
    st = subs.add_parser("step")
    st.add_argument("--db", required=True); st.add_argument("--run-id", required=True)
    st.add_argument("--pr-state", default="", dest="pr_state")
    st.add_argument("--head", default="")
    st.add_argument("--ci", default="na")
    st.add_argument("--failing-jobs", default="", dest="failing_jobs")
    bs = subs.add_parser("back-state")
    bs.add_argument("--db", required=True); bs.add_argument("--run-id", required=True)
    sl = subs.add_parser("session-last-item")
    sl.add_argument("--server", required=True); sl.add_argument("--id", required=True)
    pn = subs.add_parser("park-notice")
    pn.add_argument("--db", required=True); pn.add_argument("--run-id", required=True)
```

- [ ] **Step 4: The handlers.** Beside `if a.mode == "parked":`:

```python
    if a.mode == "step":
        from kernel import back
        from kernel.store import Store

        from coordinator.step import Ground, next_step
        if not os.path.exists(a.db):
            print(f"no kernel database at {a.db}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        store = Store.open(a.db)
        store.run_state(a.run_id)  # raises for a run the kernel does not hold
        verdict, fps = None, ()
        fact = back.verdict_for_head(store, a.run_id, a.head) if a.head else None
        if fact is not None:
            verdict = "PASS" if fact.payload.get("verdict") == "accept" else "FAIL"
            fps = tuple(fact.payload.get("fingerprints") or ())
        jobs = tuple(j for j in a.failing_jobs.split(",") if j)
        s = next_step(store, a.run_id, Ground(pr_state=a.pr_state, head=a.head, ci=a.ci,
                                              failing_jobs=jobs, verdict=verdict, fingerprints=fps))
        print(f"{s.kind}|{s.cause}|{','.join(s.evidence)}|{s.reason}|{'yes' if s.redispatch else 'no'}", end="")
        return RC_OK

    if a.mode == "back-state":
        from kernel import back
        from kernel.store import Store
        if not os.path.exists(a.db):
            print(f"no kernel database at {a.db}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        store = Store.open(a.db)
        store.run_state(a.run_id)
        ci = back.latest_ci(store, a.run_id) or {}
        print(f"{ci.get('pr') or ''}|{ci.get('pr_state') or ''}|{ci.get('head_git_sha') or ''}|"
              f"{store.current_artifact(a.run_id) or ''}", end="")
        return RC_OK

    if a.mode == "session-last-item":
        from coordinator import session as _session
        try:
            listing = _session.list_items(a.server, a.id)
        except _session.LookupFailed as exc:
            print(f"session-last-item: {exc}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        print(listing[-1]["id"] if listing else "", end="")
        return RC_OK

    if a.mode == "park-notice":
        from types import SimpleNamespace

        from kernel import front
        from kernel.store import Store

        from coordinator.phases import park_notice_body
        store = Store.open(a.db)
        park = front.current_park(store, a.run_id)
        if park is None:
            return RC_FAILED
        print(park_notice_body(SimpleNamespace(run_id=a.run_id), park), end="")
        return RC_OK
```

(`session-last-item` calls `_session.list_items` through the module attribute on purpose, so a test can substitute it; `park_notice_body` reads only `ctx.run_id`, so a namespace stands in for a `Ctx`.)

- [ ] **Step 5: The notice.** In `phases.py` add to `PARK_NEEDS`:

```python
    # The closed loop (spec §3): three repair rounds in a row hit the same
    # wall. Two words, because a correction has no seat to go to in the back
    # half -- the next round is briefed from the journal, not from a reply.
    "no_progress": ("Reply with the single word `retry` in the session below for another "
                    "repair round, or `stop` to close the run without merging."),
```

and a back-half override for `no_verdict`:

```python
#: The same reasons read differently once a pull request exists: there is
#: no author to correct, only a round to grant or a run to stop.
_BACK_HALF_NEEDS = {
    "no_verdict": ("The reviewer produced no verdict. Reply with the single word `retry` in "
                   "the session below to review again, or `stop` to close the run without merging."),
}
```

In `park_notice_body`, before the generic return, add the `no_progress` body and use the override:

```python
    phase = park.payload.get("phase") or "implementation"
    if reason == "no_progress":
        ev = ", ".join(park.payload.get("evidence") or []) or "no evidence recorded"
        return (f"bircher: parked no_progress\n\n"
                f"Three repair rounds in a row ended the same way ({park.payload.get('cause')}: {ev}). "
                f"This run is waiting for you at the **{phase}** phase.\n\n{PARK_NEEDS['no_progress']}\n\n"
                f"{where}\n\nRun `{ctx.run_id}`. Your reply is read by the next wave, not the moment "
                "you send it, so nothing appears to happen until one runs.")
    needs = PARK_NEEDS.get(reason, 'Open the session below and reply.')
    if phase == "implementation":
        needs = _BACK_HALF_NEEDS.get(reason, needs)
```

and make the generic return use `phase` and `needs` in place of `park.payload.get('phase')` and the `PARK_NEEDS.get(...)` expression.

- [ ] **Step 6: Run the tests**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_step.py tests/coordinator/test_phases.py tests/coordinator/test_cli.py -v`
Expected: all PASS (the front-half notice tests are unchanged: their phases are not `implementation`).

- [ ] **Step 7: Commit**

```bash
git add v2/coordinator/cli.py v2/coordinator/phases.py v2/tests/coordinator/test_step.py
git commit -m "CLI: step, back-state, session-last-item, park-notice"
```

---

### Task 7: The loop's parts: the head on every outcome, the repair and park helpers, `_step_loop`, `_finish_pass`

**Files:**
- Modify: `v2/coordinator/outcome.py` (`derive`: the head and the findings)
- Modify: `batch/lib/kernel-client.sh` (`_kernel_request_repair`, `_kernel_park_back`, `_kernel_back_state`, new)
- Modify: `batch/run-queue.sh` (`_park_carrier_prompt`, `_park_back_half`, `_step_loop`, `_finish_pass`, new, placed after `_repair_round`; `_issue_writeback`; the old loop's review guard)
- Test: `v2/tests/coordinator/test_outcome.py`, `v2/tests/coordinator/test_repair_loop.py`, `v2/tests/execution/test_step_loop.py` (new)

**Interfaces:**
- Produces: `Derived.sha` is the PR's head as captured once CI settled, on EVERY outcome with a PR (no longer only on `ready`/`revise`); `Derived.findings` rides out on every FAIL; `_kernel_request_repair <run_id> <generation> <cause> <head> [evidence_csv]`; `_kernel_park_back <run_id> <generation> <reason> [session_id] [cursor_item_id] [cause] [evidence_csv]`; `_kernel_back_state <run_id>` printing `pr|pr_state|head|artifact`; `_park_back_half <item> <code> <reason> [cause] [evidence_csv]` (rc 0 only when the kernel recorded the park); `_step_loop` and `_finish_pass <item> <queue-file> <issue>` with the dynamic-scope contract stated in their comments below; `_issue_writeback` does nothing for the outcomes `waiting` and `parked`.
- Consumes: Tasks 2, 6.
- Consumed by Task 9, which wires them into `run_item` and adds `_resume_back_half`.

- [ ] **Step 1: Failing tests for the derivation.** In `v2/tests/coordinator/test_outcome.py` replace `test_a_TERMINAL_outcome_carries_no_sha` with:

```python
def test_the_head_rides_out_on_every_outcome_with_a_pr():
    """The closed loop (spec §1) repairs a red head and records CI against it,
    so the head is evidence on every outcome, not only the merge-authorising
    one. The merge gate is `outcome == ready`, not the presence of a sha."""
    head = "a" * 40
    failed = derive("i1", "i1", "7", "", deps=_deps(review=lambda pr, sha: ("FAIL", "")))
    assert failed.outcome == "failed" and failed.sha == head
    esc = derive("i1", "i1", "7", "", deps=_deps(review=lambda pr, sha: (None, "")))
    assert esc.outcome == "escalated" and esc.sha == head
    red = derive("i1", "i1", "7", "", deps=_deps(checks=lambda pr: "build|fail"))
    assert red.ci == "red" and red.sha == head
    none = derive("i1", "i1", "", "", deps=_deps(pr_state=lambda pr: ("", "")))
    assert none.pr == "" and none.sha == ""
```

and in `test_repair_loop.py` rename `test_the_findings_ride_out_only_on_a_revise` to `test_the_findings_ride_out_on_every_fail` and change the `terminal` assertion to `assert "blocking: the thing" in terminal.findings` (the `passing` assertion stays: a PASS carries none).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_outcome.py::test_the_head_rides_out_on_every_outcome_with_a_pr tests/coordinator/test_repair_loop.py::test_the_findings_ride_out_on_every_fail -v`
Expected: both FAIL (`sha == ""` on failed; `findings == ""` on the terminal FAIL).

- [ ] **Step 3: The derivation.** In `derive`, rename `reviewed_sha` to `head_sha` and move its capture to right after `ci = _settle_ci(...)`, before the `if ci == "green":` branch, with this comment: `# THE HEAD CI RAN ON, captured once CI has settled and never re-read (#66), on every colour: a red head is what a ci_red repair names (closed-loop spec §1) and what the CI observation is recorded against.` Keep the 40-hex check and its log line. Inside the green branch the review is dispatched at `head_sha` as before, and the merge-base/digest/status block stays inside `if head_sha:` there. Replace the `sha_out` rule and its long comment with:

```python
    # The head rides out whenever there is one (closed-loop spec §1). The
    # merge gate is the runner's `outcome == ready`; a sha on a failed or
    # escalated derivation authorises nothing and is the evidence a repair
    # round and a CI observation need.
    sha_out = head_sha
```

and the findings rule with `findings=(reviewer_out if verdict == "FAIL" else "")` (comment: `# The findings ride out on every FAIL: the repair round is briefed from them.`).

- [ ] **Step 4: Run the coordinator suite**

Run: `cd v2 && python -m pytest -q tests/coordinator`
Expected: all PASS. `test_a_red_pr_never_reaches_the_reviewer` and `test_the_reviewed_sha_is_captured_BEFORE_the_review_is_dispatched` still hold.

- [ ] **Step 5: The kernel-client helpers.** Append to `batch/lib/kernel-client.sh` (keep the bare `# <command>` comment lines: `tests/execution/test_kernel_client.py` reads them):

```bash
# _kernel_request_repair <run_id> <generation> <cause> <head_git_sha> [evidence_csv]
#
# The one door back to `planned` in the back half (closed-loop spec §1).
# *evidence_csv* is failing job names or finding fingerprints, comma-joined;
# quotes and backslashes are stripped because the value is spliced into JSON.
_kernel_request_repair() {  # <run_id> <generation> <cause> <head> [evidence_csv]
  local run_id="$1" generation="$2" cause="$3" head="$4" ev="${5:-}"
  ev=$(printf '%s' "$ev" | tr -d '"\\')
  local arr="[]"; [ -n "$ev" ] && arr="[\"${ev//,/\",\"}\"]"
  # request_repair
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name request_repair --payload-json "{\"cause\":\"$cause\",\"head_sha\":\"$head\",\"evidence\":$arr}"
}

# _kernel_park_back <run_id> <generation> <reason> [session_id] [cursor_item_id] [cause] [evidence_csv]
#
# A back-half park (closed-loop spec §3). The carrier session and its cursor
# are what `park-reply` reads; cause and evidence are what `no_progress` is
# checked against by the kernel.
_kernel_park_back() {  # <run_id> <generation> <reason> [session_id] [cursor] [cause] [evidence_csv]
  local run_id="$1" generation="$2" reason="$3" sid="${4:-}" cur="${5:-}" cause="${6:-}" ev="${7:-}"
  ev=$(printf '%s' "$ev" | tr -d '"\\')
  local arr="[]"; [ -n "$ev" ] && arr="[\"${ev//,/\",\"}\"]"
  local jsid=null jcur=null jcause=null
  [ -n "$sid" ] && jsid="\"$sid\""
  [ -n "$cur" ] && jcur="\"$cur\""
  [ -n "$cause" ] && jcause="\"$cause\""
  # park
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name park --payload-json "{\"reason\":\"$reason\",\"session_id\":$jsid,\"cursor_item_id\":$jcur,\"findings_hash\":null,\"verdict\":null,\"reviewer\":null,\"cause\":$jcause,\"evidence\":$arr}"
}

# _kernel_back_state <run_id> -> `pr|pr_state|head|artifact` from the journal
# (coordinator.cli back-state), or empty when the kernel would not answer.
_kernel_back_state() {  # <run_id>
  local out=""
  out=$( PYTHONPATH="$(_kernel_pythonpath)" _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m coordinator.cli back-state \
           --db "${BIRCHER_KERNEL_DB:-}" --run-id "$1" 2>/dev/null ) || out=""
  printf '%s' "$out"
}
```

If `test_kernel_client.py` keeps an inventory of `_kernel_*` functions against command names, add `_kernel_request_repair -> request_repair` and `_kernel_park_back -> park` to it.

- [ ] **Step 6: The bash parts.** In `batch/run-queue.sh`, after `_repair_round`:

```bash
# _park_carrier_prompt <item> <reason> [cause] [evidence_csv] -> the prompt
# the park's carrier session opens with: what happened and the two words.
_park_carrier_prompt() {  # <item> <reason> [cause] [evidence_csv]
  local ev="${4:-}"
  printf 'bircher: the run for %s is parked (%s%s%s).\n\nReply with the single word `retry` for another round, or `stop` to close the run without merging. Anything else is ignored. Your reply is read by the next wave, not the moment you send it.\n' \
    "$1" "$2" "${3:+: $3}" "${ev:+ on $ev}"
}

# _park_back_half <item> <code> <reason> [cause] [evidence_csv]
#
# A back-half park (closed-loop spec §3): a fresh CARRIER session so the
# person has somewhere to type `retry` or `stop` that the next wave can read
# (the repair sessions are stopped and cannot carry a reply), the park fact
# naming that session and its cursor, the notice on the issue keyed on the
# park fact, and the sidecar. Reads BIRCHER_RUN_ID, BIRCHER_GENERATION and
# _iss by dynamic scope. rc 0 only when the kernel recorded the park: a
# caller must not report `parked` for a run the journal does not park.
_park_back_half() {  # <item> <code> <reason> [cause] [evidence_csv]
  local item="$1" code="$2" reason="$3" cause="${4:-}" ev="${5:-}"
  local host_id conv_id="" cur=""
  host_id=$(_local_host_id 2>/dev/null) || host_id=""
  conv_id=$(_create_session "$AGENT_ID" "$host_id" "$WORKDIR")
  if [ -n "$conv_id" ]; then
    _send_prompt "$conv_id" "$(_park_carrier_prompt "$item" "$reason" "$cause" "$ev")" || conv_id=""
  fi
  if [ -n "$conv_id" ]; then
    cur=$(_coordinator session-last-item --server "${SERVER:-}" --id "$conv_id") || cur=""
  else
    echo "[batch] $item: no carrier session for the park; a reply must come through the operator's shell (coordinator.cli grant-round / cancel)" >&2
  fi
  _kernel_park_back "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$reason" "$conv_id" "$cur" "$cause" "$ev"
  local _park; _park=$("${BIRCHER_PY:-python3}" -m coordinator.cli parked --db "$BIRCHER_KERNEL_DB" --run-id "$BIRCHER_RUN_ID" 2>/dev/null) || _park=""
  if [ -z "$_park" ]; then
    echo "[batch] $item: the kernel did NOT record the park ($reason) -> not reporting parked" >&2
    return 1
  fi
  local _pid; _pid=$(printf '%s' "$_park" | _json_get id)
  if [ -n "$_iss" ]; then
    local _notice; _notice=$("${BIRCHER_PY:-python3}" -m coordinator.cli park-notice --db "$BIRCHER_KERNEL_DB" --run-id "$BIRCHER_RUN_ID" 2>/dev/null) || _notice=""
    [ -n "$_notice" ] && _effect comment "park-notice:$BIRCHER_RUN_ID:$_pid" - gh issue comment "$_iss" --repo "$REPO" --body "$_notice" >/dev/null 2>&1 || true
  fi
  _write_parked_sidecar "$code" "$BIRCHER_RUN_ID" "$(_kernel_state "$BIRCHER_RUN_ID")" "$reason"
  return 0
}
```

(Invoke `coordinator.cli parked` exactly as `run_item`'s phases branch invokes it today, so the same PYTHONPATH arrangement applies.) Then the loop:

```bash
# _step_loop -- THE CLOSED LOOP (closed-loop spec §2). One pass: derive the
# ground truth, record it, ask `next_step` for the one action it names, do it,
# and go round again until the action ends the pass (wait, park, merge,
# done) or there is no pull request to loop on.
#
# DYNAMIC SCOPE, stated: reads item code pr _iss vendor RECOVERY_REVIEWER
# _base_sha _ctx_hash _ffile BIRCHER_RUN_ID BIRCHER_GENERATION; assigns
# outcome review note observed_head _obs_ci ci_first resubmissions
# _settled_pr _merge_base _delta_digest _fingerprints _pr_state _failing_jobs
# _out_hash _rev_round _step _step_cause _step_evidence _step_reason. Every
# one of those is declared `local` by BOTH callers (run_item and
# _resume_back_half); a name missing there is unbound under `set -u` and
# kills the pass, which is the failure `_out_hash` once had.
#
# `_step` on return: wait | park | merge | done | none. `none` means no PR:
# the caller's own ending (timeout, escalated) stands. `merge` means the
# caller performs `_merge_step`. `wait` and `park` mean the run is not over.
_step_loop() {
  local obs _stepline _st_now _redispatch _rbranch _findings _known_pr
  _step=""; _step_cause=""; _step_evidence=""; _step_reason=""
  while :; do
    _merge_base=""; _delta_digest=""; _fingerprints=""; _pr_state=""; _failing_jobs=""
    echo "[batch] $item: deriving the ground truth from the repository (repair rounds so far: $_rev_round)" >&2
    # `0` is the old allowance argument, kept until Task 10 removes it: the
    # findings ride out on every FAIL now, so it decides nothing.
    obs=$(observe_outcome "$item" "$code" "$pr" "$_iss" 0 "$_ffile")
    # A CRASHED OR MALFORMED DERIVATION IS NOT A VERDICT: nothing is recorded,
    # the run stays open, and the next wave derives again.
    if [ -z "${obs//[[:space:]]/}" ] || ! _derived_width_ok "$obs"; then
      echo "[batch] $item: derivation produced no usable tuple -> waiting for the next wave" >&2
      outcome=waiting; review=na; ci_first=unknown; resubmissions=""; observed_head=""
      note="outcome derivation failed (no tuple, or a malformed one); the next wave re-derives"
      _step=wait; return 0
    fi
    IFS='|' read -r outcome review note observed_head _obs_ci ci_first resubmissions _settled_pr _merge_base _delta_digest _fingerprints _pr_state _failing_jobs <<EOF
$obs
EOF
    : "${outcome:=timeout}" "${ci_first:=unknown}"
    # ADOPT WHAT THE DERIVATION SETTLED ON, including nothing: an empty
    # `_settled_pr` in a well-formed tuple means the PR it was given is gone.
    _known_pr="${pr:-}"
    if [ "${_settled_pr:-}" != "${pr:-}" ]; then
      echo "[batch] $item: derivation settled on PR #${_settled_pr:-none} (was ${pr:-none}) -> adopting" >&2
      pr="${_settled_pr:-}"
    fi
    if [ -z "${pr:-}" ]; then
      # A PR a person closed is observed so the run can end (spec §1); the
      # caller's own ending then stands.
      case "$_pr_state" in
        closed|merged) _kernel_record_ci "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" na "" "$_pr_state" "" "$_known_pr" ;;
      esac
      _step=none; return 0
    fi
    _st_now=$(_kernel_state "$BIRCHER_RUN_ID")
    if [ -n "${observed_head:-}" ]; then
      if [ "$_st_now" = implementing ]; then
        local _body="derived: outcome=$outcome review=$review head=$observed_head note=$note"
        _out_hash=$(_kernel_record_output "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_body")
      fi
      # At `reviewing` the output on record is the one the binding must name.
      if [ -z "$_out_hash" ]; then
        _out_hash=$(_kernel_back_state "$BIRCHER_RUN_ID"); _out_hash="${_out_hash##*|}"
      fi
      _kernel_record_ci "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "${_obs_ci:-na}" "$observed_head" "$_pr_state" "$_failing_jobs" "$pr"
      # A VERDICT IS RECORDED ONLY WHEN THERE IS ONE. `na` on a red or a
      # silent reviewer is not a verdict, and the kernel would refuse it.
      case "$review" in
        *:pass|*:fail)
          BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer); export BIRCHER_GENERATION
          _kernel_record_review "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$review" "$_out_hash" "$_base_sha" "$_ctx_hash" "" "" "$observed_head" "$_merge_base" "$_delta_digest" "$_fingerprints"
          BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer); export BIRCHER_GENERATION
          ;;
      esac
    fi
    _stepline=$(_coordinator step --db "${BIRCHER_KERNEL_DB:-}" --run-id "$BIRCHER_RUN_ID" \
                  --pr-state "$_pr_state" --head "${observed_head:-}" --ci "${_obs_ci:-na}" \
                  --failing-jobs "$_failing_jobs") || _stepline=""
    if [ -z "$_stepline" ]; then
      echo "[batch] $item: could not read the next step from the journal -> waiting for the next wave" >&2
      outcome=waiting; note="${note:+$note; }next step unreadable; the next wave re-derives"; _step=wait; return 0
    fi
    IFS='|' read -r _step _step_cause _step_evidence _step_reason _redispatch <<EOF
$_stepline
EOF
    echo "[batch] $item: next step: $_step${_step_cause:+ ($_step_cause: ${_step_evidence:-no evidence})}" >&2
    case "$_step" in
      wait)
        outcome=waiting; note="${note:+$note; }waiting on PR #$pr (ci=${_obs_ci:-na}); the next wave resumes"
        return 0 ;;
      done)
        # The PR left the loop by another hand, or the run is already over:
        # the caller records the outcome the derivation named.
        return 0 ;;
      merge)
        outcome=ready
        return 0 ;;
      review)
        # This pass just reviewed the head and got no verdict; a person says
        # whether to review again (`retry`) or stop.
        if _park_back_half "$item" "$code" no_verdict; then
          outcome=parked; note="${note:+$note; }parked no_verdict"; return 0
        fi
        outcome=waiting; note="${note:+$note; }no verdict, and the park was refused; the next wave re-derives"; _step=wait; return 0 ;;
      park)
        if _park_back_half "$item" "$code" "$_step_reason" "$_step_cause" "$_step_evidence"; then
          outcome=parked; note="${note:+$note; }parked $_step_reason ($_step_cause: $_step_evidence)"; return 0
        fi
        outcome=waiting; note="${note:+$note; }park $_step_reason refused; the next wave re-derives"; _step=wait; return 0 ;;
      repair) ;;
      *)
        echo "[batch] $item: unknown step '$_step' -> waiting for the next wave" >&2
        outcome=waiting; _step=wait; return 0 ;;
    esac
    # A REPAIR. Request it -- the one door back to `planned` (spec §1) --
    # unless a pass that died before dispatching already requested it for
    # this head.
    if [ "$_redispatch" != yes ]; then
      _kernel_request_repair "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$_step_cause" "$observed_head" "$_step_evidence"
      if [ "$(_kernel_state "$BIRCHER_RUN_ID")" != planned ]; then
        echo "[batch] $item: the kernel did not open a repair round -> waiting for the next wave" >&2
        outcome=waiting; note="${note:+$note; }repair request refused"; _step=wait; return 0
      fi
    fi
    # THE BRIEF. A review_fail round is briefed on the reviewer's verbatim
    # findings, which this derivation wrote; a ci_red round on the failing
    # jobs by name.
    _findings=""
    if [ "$_step_cause" = review_fail ]; then
      [ -n "$_ffile" ] && [ -s "$_ffile" ] && _findings=$(cat "$_ffile")
      if _is_blank "$_findings"; then
        echo "[batch] $item: a review_fail repair is owed but no findings were written -> waiting for the next wave" >&2
        outcome=waiting; note="${note:+$note; }no findings to brief the repair"; _step=wait; return 0
      fi
    else
      _findings="CI is red on ${observed_head:0:7}. Failing jobs: ${_step_evidence:-unknown}. Make CI green on this branch without disabling, skipping or weakening the failing checks."
    fi
    _rev_round=$((_rev_round + 1))
    _rbranch=$(_pr_branch "$pr")
    if [ -z "${_rbranch//[[:space:]]/}" ]; then
      echo "[batch] $item: could not read PR #$pr's head branch -> waiting for the next wave" >&2
      outcome=waiting; note="${note:+$note; }could not read PR #$pr's head branch"; _step=wait; return 0
    fi
    if ! _repair_round "$item" "$code" "$pr" "$_rbranch" "$_findings" "$_rev_round" "$vendor"; then
      outcome=waiting; note="${note:+$note; }repair round $_rev_round could not be started; the next wave retries"; _step=wait; return 0
    fi
  done
}

# _finish_pass <item> <queue-file> <issue> -- the pass's ending, shared by
# run_item and _resume_back_half. Reads outcome pr ci_first review
# resubmissions elapsed note bound_outcome vendor rounds _step merge_rc by
# dynamic scope. A `wait` or `park` step leaves the run OPEN: no terminal
# fact, and the queue file stays where it is for the next wave, as a
# front-half park's does.
_finish_pass() {  # <item> <queue-file> <issue>
  local item="$1" f="$2" issue="$3"
  mkdir -p "$(dirname "$SCORECARD")"
  case "${_step:-}" in
    wait|park) ;;
    *) _kernel_record_run_outcome "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$outcome" ;;
  esac
  json_row "$item" "${pr:-}" "$outcome" "$ci_first" "${review:-}" "${resubmissions:-}" "$elapsed" "$note" "$bound_outcome" "$vendor" "${rounds:-}" >> "$SCORECARD"
  _issue_writeback "$issue" "$outcome" "${pr:-}" "${review:-}" "${resubmissions:-}" "${ci_first:-}" "${rounds:-}"
  [ "$outcome" = "ready" ] && _ensure_issue_closed "$issue" "${pr:-}"
  echo "[batch] $item -> outcome=$outcome pr=${pr:-none} review=${review:-na} rounds=${rounds:-?} bound=$bound_outcome step=${_step:-none}"
  case "${_step:-}" in
    wait|park) return "$merge_rc" ;;
  esac
  mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
  return "$merge_rc"
}
```

In `_issue_writeback`, right after `[ -n "$issue" ] || return 0`, add:

```bash
  # A pass that leaves the run open writes nothing on the issue: `waiting`
  # every half hour is noise, and a park's notice is its own comment, keyed
  # on the park fact. Labels stay as they are (state-derived labels are the
  # next PR's).
  case "$outcome" in waiting|parked) return 0 ;; esac
```

In the OLD loop still wired in `run_item` (Task 9 replaces it), wrap its `_kernel_record_review` call in the same `case "$review" in *:pass|*:fail)` guard as above: with the head now riding out on red, the old block would otherwise submit `na` as a verdict.

- [ ] **Step 7: Drive `_step_loop` for real.** Create `v2/tests/execution/test_step_loop.py`:

```python
"""`_step_loop` driven for real with the world scripted (closed-loop spec §2).

The derivation, the step CLI and the kernel are stubs that read their answers
from files and log what they were asked; the loop under test is the real
function extracted from run-queue.sh.
"""

import os
import subprocess
from pathlib import Path

RQ = Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"
HEAD = "d" * 40


def _extract_function(src_lines, name):
    start = next(i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


PREAMBLE = r'''
set -uo pipefail
LOG="$T/calls.log"
_log() { printf '%s\n' "$*" >> "$LOG"; }
_next() { local f="$T/$1" line=""; IFS= read -r line < "$f" || true; tail -n +2 "$f" > "$f.tmp"; mv "$f.tmp" "$f"; printf '%s' "$line"; }
observe_outcome() { _log "observe pr=${3:-}"; _next derive; }
_coordinator() { if [ "$1" = step ]; then _log "step $*"; _next step; else _log "coordinator $*"; fi; }
_kernel_state() { _next state; }
_kernel_record_output() { _log "record_output"; printf 'hash1'; }
_kernel_record_ci() { _log "record_ci status=$3 head=$4 pr_state=${5:-} jobs=${6:-} pr=${7:-}"; }
_kernel_record_review() { _log "record_review $3 artifact=$4 head=${9:-} fps=${12:-}"; }
_kernel_dispatch() { printf '7'; }
_kernel_request_repair() { _log "request_repair $3 head=$4 ev=${5:-}"; }
_kernel_back_state() { printf '42|open|%s|hash0' "$HEAD"; }
_park_back_half() { _log "park $3 ${4:-} ${5:-}"; return "${PARK_RC:-0}"; }
_repair_round() { _log "repair_round pr=$3 branch=$4 round=$6 vendor=$7"; _log "brief: $5"; return 0; }
_pr_branch() { printf 'feat-x'; }
_is_blank() { [ -z "${1//[[:space:]]/}" ]; }
item=i1; code=i1; pr="$PR0"; _iss=5; vendor=claude_code; RECOVERY_REVIEWER=codex
_base_sha=$(printf 'b%.0s' $(seq 40)); _ctx_hash=ctx; _ffile="$T/findings"
BIRCHER_RUN_ID=r; BIRCHER_GENERATION=1; BIRCHER_KERNEL_DB="$T/k.db"
outcome=""; review=""; note=""; observed_head=""; _obs_ci=""; ci_first=""; resubmissions=""; _settled_pr=""
_merge_base=""; _delta_digest=""; _fingerprints=""; _pr_state=""; _failing_jobs=""; _out_hash=""; _rev_round=0
_step=""; _step_cause=""; _step_evidence=""; _step_reason=""; merge_rc=0
'''


def _tuple(outcome, review, ci, *, head=HEAD, pr="42", fps="", pr_state="open", jobs=""):
    return f"{outcome}|{review}|n|{head}|{ci}|true|0|{pr}|{'c' * 40}|{'e' * 64}|{fps}|{pr_state}|{jobs}"


def _run(tmp_path, derive_lines, step_lines, states, *, pr0="42", findings="", park_rc=0):
    src = RQ.read_text().splitlines()
    (tmp_path / "derive").write_text("".join(l + "\n" for l in derive_lines))
    (tmp_path / "step").write_text("".join(l + "\n" for l in step_lines))
    (tmp_path / "state").write_text("".join(s + "\n" for s in states))
    (tmp_path / "findings").write_text(findings)
    script = (PREAMBLE + _extract_function(src, "_derived_width_ok") + "\n"
              + _extract_function(src, "_step_loop") + "\n"
              + '_step_loop; printf "%s|%s|%s|%s|%s" "$_step" "$outcome" "$pr" "$_rev_round" "$_out_hash"\n')
    env = dict(os.environ, T=str(tmp_path), PR0=pr0, HEAD=HEAD, PARK_RC=str(park_rc))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    log_file = tmp_path / "calls.log"
    return r.stdout.strip(), (log_file.read_text().splitlines() if log_file.exists() else [])


def test_red_ci_requests_a_repair_then_merges_on_the_next_pass():
    out, log = _run(
        _tmp(), [_tuple("failed", "na", "red", jobs="server (go)"), _tuple("ready", "codex:pass", "green")],
        ["repair|ci_red|server (go)||no", "merge||||no"],
        ["implementing", "planned", "implementing"])
    assert out.startswith("merge|ready|42|1|")
    assert f"request_repair ci_red head={HEAD} ev=server (go)" in log
    assert "repair_round pr=42 branch=feat-x round=1 vendor=claude_code" in log
    assert any(l.startswith("brief: CI is red on ddddddd") and "server (go)" in l for l in log)
    assert "record_ci status=red head=" + HEAD + " pr_state=open jobs=server (go) pr=42" in log
    assert not any(l.startswith("record_review na") for l in log)
    assert sum(l.startswith("record_review codex:pass") for l in log) == 1


def test_a_fail_is_repaired_from_the_findings_file():
    fp = "ab" * 20
    out, log = _run(
        _tmp(), [_tuple("failed", "codex:fail", "green", fps=fp), _tuple("escalated", "na", "pending", head="")],
        [f"repair|review_fail|{fp}||no", "wait||||no"],
        ["implementing", "planned", "implementing"], findings="blocking: the thing")
    assert out.startswith("wait|waiting|42|1|")
    assert "brief: blocking: the thing" in log
    assert f"record_review codex:fail artifact=hash1 head={HEAD} fps={fp}" in log


def test_three_identical_fails_park_for_no_progress():
    fp = "ab" * 20
    out, log = _run(_tmp(), [_tuple("failed", "codex:fail", "green", fps=fp)],
                    [f"park|review_fail|{fp}|no_progress|no"], ["reviewing"])
    assert out.startswith("park|parked|42|0|")
    assert f"park no_progress review_fail {fp}" in log
    assert not any(l.startswith("repair_round") for l in log)


def test_no_verdict_parks_no_verdict():
    out, log = _run(_tmp(), [_tuple("escalated", "codex:na", "green")], ["review||||no"], ["reviewing"])
    assert out.startswith("park|parked|42|0|")
    assert any(l.startswith("park no_verdict") for l in log)


def test_a_refused_park_waits_instead():
    out, _ = _run(_tmp(), [_tuple("escalated", "codex:na", "green")], ["review||||no"], ["reviewing"], park_rc=1)
    assert out.startswith("wait|waiting|42|0|")


def test_a_repair_already_requested_is_redispatched_without_a_new_request():
    out, log = _run(_tmp(), [_tuple("failed", "na", "red", jobs="x"), _tuple("ready", "codex:pass", "green")],
                    ["repair|ci_red|x||yes", "merge||||no"], ["planned", "implementing"])
    assert out.startswith("merge|ready|42|1|")
    assert not any(l.startswith("request_repair") for l in log)
    assert any(l.startswith("repair_round") for l in log)


def test_a_closed_pr_is_observed_and_the_loop_steps_aside():
    out, log = _run(_tmp(), [_tuple("timeout", "na", "na", head="", pr="", pr_state="closed")], [], [])
    assert out.startswith("none|timeout||0|")
    assert "record_ci status=na head= pr_state=closed jobs= pr=42" in log
    assert not any(l.startswith("step") for l in log)


def test_a_crashed_derivation_waits_and_records_nothing():
    out, log = _run(_tmp(), [""], [], [])
    assert out.startswith("wait|waiting|42|0|")
    assert log == ["observe pr=42"]


def test_at_reviewing_the_binding_names_the_artifact_on_record():
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"])
    assert out == "merge|ready|42|0|hash0"
    assert not any(l.startswith("record_output") for l in log)
    assert f"record_review codex:pass artifact=hash0 head={HEAD} fps=" in log


def _tmp():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="steploop-"))
```

- [ ] **Step 8: Run the new test, then everything**

Run: `cd v2 && python -m pytest -q tests/execution/test_step_loop.py -v && python -m pytest -q` and `bash batch/run-queue.sh --self-test > /tmp/selftest-t7.log 2>&1; echo rc=$?; grep -n "^FAIL" /tmp/selftest-t7.log`
Expected: all PASS; self-test rc 0. If `test_binding_variables_are_declared_at_run_item_scope` or a routing/inventory test objects to the new functions, satisfy it the way its message says (one `local` per name; an inventory row per effect site) rather than loosening it. Then `python3 tools/repoint-citations.py <baseline>` once, and include the docs.

- [ ] **Step 9: Commit**

```bash
git add v2 batch docs/design/effect-site-inventory.md docs/design/scar-effect-matrix.md
git commit -m "The loop's parts: head on every outcome, repair and park helpers, _step_loop"
```

---

### Task 8: A person's `retry` or `stop` reaches a parked back-half run

**Files:**
- Modify: `v2/coordinator/human.py` (`STOP`, `classify_batch`)
- Modify: `v2/coordinator/cli.py` (`park-reply`)
- Test: `v2/tests/coordinator/test_human.py`, `v2/tests/coordinator/test_step.py`

**Interfaces:**
- Produces: `classify_batch` returns `("retry", "")` for the bare word `retry` and `("stop", "")` for the bare word `stop` at `planned`, `implementing`, `reviewing`, `merge_requested`, and `("direction", text)` for anything else there; `python -m coordinator.cli park-reply --db --run-id --server` printing `nopark` (no current park), `none` (a park, no reply or no carrier), `retry` (a round was granted), `stop` (the run was cancelled), `other` (a reply that is neither word; nothing recorded), `refused` (the kernel refused the ruling; nothing recorded); rc 3 when the carrier session cannot be read.
- Consumes: Task 3's back-half `grant_round`; the kernel's existing human-executable `cancel_run`.
- Consumed by Task 9's `_resume_back_half`.

- [ ] **Step 1: Failing tests.** Append to `v2/tests/coordinator/test_human.py`:

```python
@pytest.mark.parametrize("state", ["planned", "implementing", "reviewing", "merge_requested"])
def test_the_back_half_takes_retry_and_stop_and_nothing_else(state):
    from coordinator.human import classify_batch
    assert classify_batch([{"text": " Retry "}], state=state, grill_open=False) == ("retry", "")
    assert classify_batch([{"text": "stop"}], state=state, grill_open=False) == ("stop", "")
    assert classify_batch([{"text": "stop."}], state=state, grill_open=False) == ("direction", "stop.")
    assert classify_batch([{"text": "please fix it"}], state=state, grill_open=False)[0] == "direction"


def test_stop_is_not_a_ruling_in_the_front_half():
    from coordinator.human import classify_batch
    assert classify_batch([{"text": "stop"}], state="spec_submitted", grill_open=False) == ("revision", "stop")
```

and to `v2/tests/coordinator/test_step.py`:

```python
def _parked_with_carrier(tmp_path):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    for k in ("r1", "r2"):
        _repair(s, k, evidence=["x"])
        _sub(s, "start_implementation", "k" + k, actor="claude")
    _sub(s, "park", "p1", actor="claude", reason="no_progress", cause="ci_red", evidence=["x"],
         session_id="sess-1", cursor_item_id="it-1")
    return s, db


def _listing(*replies):
    items = [{"id": "it-1", "role": "user", "text": "notice"}, {"id": "it-2", "role": "assistant", "text": "ok"}]
    for i, text in enumerate(replies, start=3):
        items.append({"id": f"it-{i}", "role": "user", "text": text})
    return items


def test_park_reply_grants_a_round_on_retry(tmp_path, capsys, monkeypatch):
    s, db = _parked_with_carrier(tmp_path)
    monkeypatch.setattr("coordinator.session.list_items", lambda server, sid, **kw: _listing("Retry"))
    assert main(["park-reply", "--db", db, "--run-id", "r", "--server", "http://x"]) == 0
    assert capsys.readouterr().out == "retry"
    assert front.current_park(s, "r") is None
    assert not back.would_be_third_identical(s, "r", "ci_red", ["x"])


def test_park_reply_cancels_the_run_on_stop(tmp_path, capsys, monkeypatch):
    s, db = _parked_with_carrier(tmp_path)
    monkeypatch.setattr("coordinator.session.list_items", lambda server, sid, **kw: _listing("stop"))
    assert main(["park-reply", "--db", db, "--run-id", "r", "--server", "http://x"]) == 0
    assert capsys.readouterr().out == "stop"
    assert s.run_state("r") == "cancelled"


def test_park_reply_reads_only_after_the_cursor(tmp_path, capsys, monkeypatch):
    s, db = _parked_with_carrier(tmp_path)
    monkeypatch.setattr("coordinator.session.list_items", lambda server, sid, **kw: _listing())
    assert main(["park-reply", "--db", db, "--run-id", "r", "--server", "http://x"]) == 0
    assert capsys.readouterr().out == "none"
    assert front.current_park(s, "r") is not None


def test_park_reply_leaves_other_text_alone(tmp_path, capsys, monkeypatch):
    s, db = _parked_with_carrier(tmp_path)
    monkeypatch.setattr("coordinator.session.list_items", lambda server, sid, **kw: _listing("try harder"))
    assert main(["park-reply", "--db", db, "--run-id", "r", "--server", "http://x"]) == 0
    assert capsys.readouterr().out == "other"
    assert front.current_park(s, "r") is not None


def test_park_reply_without_a_park_says_so(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    assert main(["park-reply", "--db", db, "--run-id", "r", "--server", "http://x"]) == 0
    assert capsys.readouterr().out == "nopark"
```

(add `from kernel import back` to the test file's imports).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_human.py -k "back_half or front_half" tests/coordinator/test_step.py -k park_reply -v`
Expected: FAIL (`stop` classifies as `direction`; argparse rejects `park-reply`).

- [ ] **Step 3: `classify_batch`.** In `human.py` add `STOP = "stop"` beside `APPROVE, RETRY`, import `BACK_HALF_STATES` from `kernel.authz`, and insert before `if single == RETRY:`:

```python
    if state in BACK_HALF_STATES or state == "merge_requested":
        # The back half (closed-loop spec §1): a parked run with a pull
        # request takes the two words its notice asked for. There is no
        # author seat to correct, so anything else is a direction, which the
        # kernel refuses here and the reader reports as `other`.
        if single == STOP:
            return "stop", ""
        return ("retry", "") if single == RETRY else ("direction", joined)
```

(`planned` is in both halves; `retry` there already meant `grant_round`, and `stop` now means `cancel_run`, which `record_human_direction`'s from-set never allowed a direction to reach anyway.)

- [ ] **Step 4: `park-reply`.** Parser, beside the Task 6 group:

```python
    pr_ = subs.add_parser("park-reply")
    pr_.add_argument("--db", required=True); pr_.add_argument("--run-id", required=True)
    pr_.add_argument("--server", required=True)
```

Handler:

```python
    if a.mode == "park-reply":
        # The back half's reply reader (closed-loop spec §1): the carrier
        # session's user items after the park's cursor, discriminated by
        # `classify_batch`, and the one ruling they make recorded as the
        # human. No dismissal: a refused or unrecognised reply is reported
        # and read again next wave, which costs nothing.
        from kernel import front
        from kernel.authz import NotAuthorized
        from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
        from kernel.store import Store

        from coordinator import session as _session
        from coordinator.human import classify_batch
        store = Store.open(a.db)
        state = store.run_state(a.run_id)
        park = front.current_park(store, a.run_id)
        if park is None:
            print("nopark", end="")
            return RC_OK
        sid, cur = park.payload.get("session_id"), park.payload.get("cursor_item_id")
        if not sid:
            print("none", end="")
            return RC_OK
        try:
            listing = _session.list_items(a.server, sid)
        except _session.LookupFailed as exc:
            print(f"park-reply: cannot read session {sid}: {exc}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        seen = cur is None or all(it["id"] != cur for it in listing)
        after = []
        for it in listing:
            if seen and it["role"] == "user":
                after.append(it)
            if it["id"] == cur:
                seen = True
        if not after:
            print("none", end="")
            return RC_OK
        kind, _ = classify_batch(after, state=state, grill_open=False)
        name = {"retry": "grant_round", "stop": "cancel_run"}.get(kind)
        if name is None:
            print("other", end="")
            return RC_OK
        try:
            execute_as_human(store, Command(
                name=name, run_id=a.run_id, expected_version=store.run_version(a.run_id),
                idempotency_key=f"human:{a.run_id}:{after[-1]['id']}", generation=HUMAN_GENERATION,
                payload={"cursor_item_id": listing[-1]["id"]}))
        except NotAuthorized as exc:
            print(f"park-reply: {name} refused: {exc}", file=sys.stderr)
            print("refused", end="")
            return RC_OK
        print(kind, end="")
        return RC_OK
```

If the kernel's `cancel_run` authorization rejects a payload carrying `cursor_item_id`, send `cancel_run` with an empty payload instead and say so in the report.

- [ ] **Step 5: Run the tests**

Run: `cd v2 && python -m pytest -q tests/coordinator -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add v2/coordinator/human.py v2/coordinator/cli.py v2/tests/coordinator
git commit -m "park-reply: retry and stop reach a parked back-half run"
```

---

### Task 9: Wire the loop: `run_item` steps, waves resume every open back-half run, verdicts stop transitioning

**Files:**
- Modify: `batch/run-queue.sh` (`run_item`'s resume decision and the whole tail from `local _rev_key=""` to the end of the function; `_back_half_state`, `_resume_back_half`, `_merge_step`, new)
- Modify: `batch/issues-to-queue.sh` (the journal sweep)
- Modify: `v2/kernel/authz.py` (`_BACK_HALF_DESTINATIONS`)
- Test: `v2/tests/kernel/test_back_half_loop.py`, `v2/tests/kernel/test_review_r3b.py` and `test_authorization.py` where they pin `request_revision -> planned` in the implementation phase, `v2/tests/execution/*` that read `run_item`'s source, the bash self-test

**Interfaces:**
- Produces: `_BACK_HALF_DESTINATIONS = {"accept": "reviewing", "request_revision": "reviewing", "reject": "reviewing"}`; `_back_half_state <state>` (rc 0 for `implementing|reviewing|merge_requested|merged|cancelled`); `_resume_back_half <item> <code> <queue-file> <issue> <state>`; `_merge_step` (the former in-run merge block, reading `pr observed_head _out_hash _base_sha _ctx_hash _merge_base _iss item` and assigning `note outcome merge_rc reviewed_sha` by dynamic scope); `run_item` resumes a run at any back-half state, or at `planned` with implementation started, through `_resume_back_half`; the sweep re-queues every open run in `planned|implementing|reviewing|merge_requested|merged|cancelled` that holds implementation output.
- Consumes: Tasks 4, 6, 7, 8.

- [ ] **Step 1: The kernel freeze, test first.** Add to `test_back_half_loop.py`:

```python
@pytest.mark.parametrize("verdict", ["accept", "request_revision", "reject"])
def test_no_back_half_verdict_transitions_the_run(verdict):
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict=verdict, artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD)
    assert s.run_state("r") == "reviewing"
    with pytest.raises(NotAuthorized):
        _sub(s, "start_implementation", "k9", actor="claude")
```

Run `cd v2 && python -m pytest -q tests/kernel/test_back_half_loop.py -k transitions -v`; expected: the `request_revision` case FAILS (`planned`). Then set `_BACK_HALF_DESTINATIONS = {"accept": "reviewing", "request_revision": "reviewing", "reject": "reviewing"}` in `authz.py` with the comment `# The closed loop (spec §1): a verdict is evidence, not a transition. The one door back to planned is request_repair.`, and fix every kernel test that asserted the old `planned` destination for the implementation phase (`grep -n "request_revision" tests/kernel/*.py`) to assert `reviewing`; front-half destinations (`_FRONT_HALF_DESTINATIONS` or whatever the spec/plan phases use) are untouched. Run `python -m pytest -q tests/kernel`; expected: all PASS.

- [ ] **Step 2: `_merge_step`.** In `run-queue.sh`, after `_finish_pass`, add a function holding the in-run merge block that today sits under `if [ "$INRUN_MERGE" != "0" ] && [ "$outcome" = "ready" ] && [ -n "${pr:-}" ]; then` in `run_item` (from `local _gate; _gate=$(_merge_gate "" "${observed_head:-}")` through `_record_deferred_ready "$item" "$pr" "$merge_rc" "$_iss" "$reviewed_sha"` and its closing `fi`), moved verbatim with its comments:

```bash
# _merge_step -- the in-run merge (B-1), performed when the step is `merge`.
# Reads pr observed_head _out_hash _base_sha _ctx_hash _merge_base _iss item
# BIRCHER_RUN_ID BIRCHER_GENERATION by dynamic scope; assigns note outcome
# merge_rc. The body is the merge block `run_item` carried until the closed
# loop, unchanged: the reviewed head comes from the derivation, the fallback
# status names the reviewed range, the kernel requests and records the merge,
# and an unreviewed merge downgrades the outcome.
_merge_step() {
  [ "$INRUN_MERGE" != "0" ] && [ "$outcome" = "ready" ] && [ -n "${pr:-}" ] || return 0
  local _gate; _gate=$(_merge_gate "" "${observed_head:-}")
  local reviewed_sha=""
  <the block's body, verbatim, from `case "$_gate" in` to `_record_deferred_ready ...` and its `fi`>
}
```

Write the body out in full from the current file; do not abbreviate it in the source.

- [ ] **Step 3: `run_item`'s tail.** Replace everything in `run_item` from the line `local _rev_key=""` (the declarations block that begins `# AT RUN_ITEM SCOPE, indent 2`) to the function's closing `}` with:

```bash
  local _rev_round=0
  local _merge_base=""
  local _delta_digest=""
  local _fingerprints=""
  local _pr_state=""
  local _failing_jobs=""
  local _settled_pr=""
  local _step=""
  local _step_cause=""
  local _step_evidence=""
  local _step_reason=""
  local merge_rc=0
  local _ffile; _ffile=$(_findings_path "$code")
  [ -n "$_ffile" ] && mkdir -p "$NOOP_DIR"
  if [ "${_blind:-0}" = 1 ]; then
    # The cancel was never confirmed, so the coordinator may still be running,
    # and deriving would race it. The run stays open; the next wave resumes
    # it from the journal (closed-loop spec §2).
    outcome="waiting"; review="na"; ci_first="unknown"; resubmissions=""; observed_head=""
    note="server unreachable at cap; could not confirm the session stopped, so derivation was skipped to avoid racing a live coordinator; the next wave resumes"
    _step=wait
    echo "[batch] $item: blind at teardown -> waiting for the next wave" >&2
  else
    _step_loop
    [ -n "$_ffile" ] && { rm -f "$_ffile" 2>/dev/null || true; }
  fi
  # `rounds` is the number of REPAIR ROUNDS this pass dispatched -- observed,
  # because the runner performed each one, and cross-checkable against the
  # run's repair_requested facts. Empty when none.
  rounds=""
  [ "$_rev_round" != 0 ] && rounds="$_rev_round"
  [ "$_step" = merge ] && _merge_step
  _finish_pass "$item" "$f" "$(_item_issue "$prompt")"
}
```

Keep the `local outcome ci_first review rounds note resubmissions observed_head _obs_ci` line above it, and `local _out_hash=""` where it is declared earlier in the function. Delete the old `_rev_key`, `_rev_terminal` declarations and every use.

- [ ] **Step 4: Resumption.** Add, after `_front_half_resumable`:

```bash
# _back_half_state <state> -> rc 0 for a state the closed loop resumes from
# (closed-loop spec §2). `merged` and `cancelled` are here so a pass that
# died between the transition and its terminal fact gets that fact written.
_back_half_state() {
  case "$1" in implementing|reviewing|merge_requested|merged|cancelled) return 0 ;; *) return 1 ;; esac
}

# _resume_back_half <item> <code> <queue-file> <issue> <state>
#
# One pass of the closed loop for a run another pass started (spec §2: waves
# resume every open back-half run). Reads vendor, RECOVERY_REVIEWER,
# BIRCHER_RUN_ID and BIRCHER_GENERATION from run_item's scope; declares every
# name `_step_loop`, `_merge_step` and `_finish_pass` assign.
_resume_back_half() {  # <item> <code> <queue-file> <issue> <state>
  local item="$1" code="$2" f="$3" _iss="$4" _st="$5"
  local start; start=$(date +%s)
  local elapsed=0 bound_outcome="ok" merge_rc=0
  local outcome="" ci_first="unknown" review="" rounds="" note="" resubmissions="" observed_head="" _obs_ci=""
  local pr="" _out_hash="" _rev_round=0
  local _merge_base="" _delta_digest="" _fingerprints="" _pr_state="" _failing_jobs="" _settled_pr=""
  local _step="" _step_cause="" _step_evidence="" _step_reason=""
  local _base_sha; _base_sha=$(_kernel_run_base "$BIRCHER_RUN_ID")
  local _ctx_hash; _ctx_hash=$(_kernel_bundle_hash "$BIRCHER_RUN_ID")
  local _ffile; _ffile=$(_findings_path "$code")
  [ -n "$_ffile" ] && mkdir -p "$NOOP_DIR"
  echo "[batch] $item: resuming run $BIRCHER_RUN_ID at $_st (closed loop)" >&2
  case "$_st" in
    merged)
      outcome=ready; note="run resumed at merged: recording the outcome an earlier pass lost"; _step=done
      elapsed=$(( $(date +%s) - start )); _finish_pass "$item" "$f" "$_iss"; return $? ;;
    cancelled)
      outcome=escalated; note="stopped by a person"; _step=done
      elapsed=$(( $(date +%s) - start )); _finish_pass "$item" "$f" "$_iss"; return $? ;;
  esac
  # A PARKED RUN READS ITS REPLY FIRST and derives nothing until a person
  # says `retry`: every derivation is a review, and a run waiting on a human
  # must not spend one per wave.
  local _reply; _reply=$(_coordinator park-reply --db "${BIRCHER_KERNEL_DB:-}" --run-id "$BIRCHER_RUN_ID" --server "${SERVER:-}") || _reply=""
  case "$_reply" in
    nopark) ;;
    retry) echo "[batch] $item: a person granted another round" >&2 ;;
    stop)
      outcome=escalated; note="stopped by a person"; _step=done
      elapsed=$(( $(date +%s) - start )); _finish_pass "$item" "$f" "$_iss"; return $? ;;
    *)
      outcome=parked; note="parked; no reply yet (${_reply:-reply unreadable})"; _step=park
      elapsed=$(( $(date +%s) - start )); _finish_pass "$item" "$f" "$_iss"; return $? ;;
  esac
  local _bs; _bs=$(_kernel_back_state "$BIRCHER_RUN_ID"); pr="${_bs%%|*}"
  _step_loop
  [ -n "$_ffile" ] && { rm -f "$_ffile" 2>/dev/null || true; }
  [ "$_rev_round" != 0 ] && rounds="$_rev_round"
  [ "$_step" = merge ] && _merge_step
  elapsed=$(( $(date +%s) - start ))
  _finish_pass "$item" "$f" "$_iss"
}
```

In `run_item`, declare `local _side=""` just before `local resumed=0 _open`, and replace the two skip branches (`if ! _front_half_resumable "$_st"; then ... fi` and `if [ "$_st" = planned ] && _kernel_implementation_started "$_open"; then ... fi`) with:

```bash
    if _back_half_state "$_st" || { [ "$_st" = planned ] && _kernel_implementation_started "$_open"; }; then
      # The back half owns it (closed-loop spec §2): a state past the seam, or
      # `planned` reached through request_repair -- which the journal, not
      # the state name, can tell from `planned` before implementation.
      _side=back
    elif _front_half_resumable "$_st"; then
      _side=front
    else
      echo "[batch] $item: run $_open is at '${_st:-unreadable}', which no pass resumes; skipping" >&2
      mkdir -p "$(dirname "$SCORECARD")"
      json_row "$item" "" "escalated" "false" "" "" 0 "run '$_open' is at '${_st:-unreadable}', which no pass resumes; this pass drives nothing and the item needs a human" "n/a" >> "$SCORECARD"
      return 0
    fi
```

and after the `if [ "$resumed" = 1 ]; then _kernel_revise_bundle ...; fi` block (before `local host_id; host_id=$(_local_host_id ...)` and the `phases` call) insert:

```bash
  if [ "$_side" = back ]; then
    _resume_back_half "$item" "$code" "$f" "$_iss" "$_st"
    return $?
  fi
```

- [ ] **Step 5: The sweep.** In `batch/issues-to-queue.sh`'s journal sweep, import `back` beside `front` (`from kernel import back, front`) and extend the condition:

```python
    # ... or (closed-loop spec §2) any open run with a pull request: waves
    # resume every one, whatever its labels, until it merges, a person stops
    # it, or it parks.
    if front.current_park(s, rid) is not None or (
            s.run_state(rid) == "sliced" and front.filing_complete(s, rid, front.epoch(s, rid)) is None) or (
            front.unresolved_disagreement(s, rid)) or (
            s.run_state(rid) in ("planned", "implementing", "reviewing", "merge_requested", "merged", "cancelled")
            and back.implementation_output_recorded(s, rid)):
        out.append(m.group(1))
```

Add a self-test block next to the existing `--- a parked open run is queued from the journal` block, built the same way (its own temp dir, its own `k.db`, the same fake `gh` on PATH), whose fixture drives a run to `implementing` with an implementation output (use `Front(s, "i78-open-pr-1", base_sha=...)`, `.to_planned()`, then `submit` `start_implementation` and `record_implementation_output` with `put_artifact`, exactly as `tests/kernel/test_review_r3b.py::_to_implementing` does), runs `issues-to-queue.sh`, and asserts `ls "$pdir/queue"/i78-*.md` and the manifest line; and a second fixture at `ended` that must NOT be queued. Messages: `FAIL open back-half run i78 was not queued from the journal`, `FAIL an ended run was queued`.

- [ ] **Step 6: The execution tests.** Run `cd v2 && python -m pytest -q tests/execution`. For every failure that reads `run_item`'s source for text now living in `_step_loop`, `_merge_step` or `_finish_pass` (the reader line, the adoption message, the merge request's redispatch, the fallback status description, the terminal fact before the scorecard row), point the assertion at the function that now holds it (`_extract_function(src, "_step_loop")`, and so on) and keep its intent; `test_front_half_seam.py` and `test_run_item_argument_wiring.py` extract `run_item` and rewrite its reader heredoc, so their `_heredoc_to_herestring` now applies to `_step_loop`'s copy (append its source to the extracted script). Do not delete a test; a test that pins the old loop's allowance wording (`repair rounds left`, `revise`) is replaced by one that pins the new loop's equivalent line. Then the self-test: `bash batch/run-queue.sh --self-test`; fix fixtures the same way.

- [ ] **Step 7: Run everything**

Run: `cd v2 && python -m pytest -q` and `bash batch/run-queue.sh --self-test > /tmp/selftest-t9.log 2>&1; echo rc=$?; grep -n "^FAIL" /tmp/selftest-t9.log`
Expected: all green. Then `python3 tools/repoint-citations.py <baseline>` once; hand-fix `UNRESOLVED` lines (the merge block's citations now point into `_merge_step`).

- [ ] **Step 8: Commit**

```bash
git add v2 batch docs/design/effect-site-inventory.md docs/design/scar-effect-matrix.md
git commit -m "run_item steps; waves resume every open back-half run; verdicts stop transitioning"
```

---

### Task 10: The bound goes

**Files:**
- Modify: `v2/coordinator/observe.py` (`classify`; delete `revision_confirmed`), `v2/coordinator/outcome.py` (`Deps.revisions_left`, `classify` call), `v2/coordinator/wiring.py` and `v2/coordinator/cli.py` (`revisions_left`, the `revisions` mode, `--revisions-left`), `batch/run-queue.sh` (`_max_revisions`, `_revision_is_recorded`, `_terminal_review_flag`, `_findings_path`, `observe_outcome`, their self-tests), `README.md` and `docs/` mentions of `BIRCHER_MAX_REVISIONS`
- Test: `v2/tests/coordinator/test_observe.py`, `test_repair_loop.py`, `test_cli.py`, `v2/tests/execution/*`

**Interfaces:**
- Produces: `classify(pr, ci, verdict, *, reviewer)` (no `revisions_left`) returning `repair` for red CI and for a FAIL, `waiting` for pending CI, `ready`, `escalated` (no verdict) and `timeout` (no PR) as before; `observe_outcome <item> <code> <pr> [issue] [findings_out]` (five arguments); `_findings_path <code>` always the path; `revisions_used` and `_round_number` stay (the status description's round number).
- Consumes: Task 9 (nothing reads the allowance any more).

- [ ] **Step 1: Failing tests.** In `test_observe.py` replace `test_red_ci_fails_without_consulting_the_verdict` with:

```python
def test_red_ci_is_a_repair_not_an_ending():
    o = classify("7", "red", "PASS", reviewer="codex")
    assert o.outcome == "repair" and o.ci == "red" and o.review == "na"


def test_pending_ci_waits():
    assert classify("7", "pending", None, reviewer="codex").outcome == "waiting"


def test_a_fail_is_a_repair():
    o = classify("7", "green", "FAIL", reviewer="codex")
    assert o.outcome == "repair" and o.review == "codex:fail"
```

change the parametrized `test_a_green_pr_is_classified_by_its_verdict` row for `FAIL` to expect `repair`, and `test_pending_ci_escalates_rather_than_guessing` to expect `waiting`. In `test_repair_loop.py` delete the `revisions_left`-parametrised tests (`test_a_fail_revises_only_while_rounds_remain`, `test_the_default_reproduces_the_behaviour_before_the_loop`, `test_only_a_FAIL_revises`, `test_a_revision_keeps_the_reviewer_verdict_as_evidence`, the `revision_confirmed` tests) and every `revisions_left=` keyword; keep the `revisions_used` tests, the thirteen-field test and `test_the_findings_ride_out_on_every_fail`; the CLI test that passes `--revisions-left` drops the flag.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_observe.py tests/coordinator/test_repair_loop.py -v`
Expected: the new classify tests FAIL (`failed`/`escalated` instead of `repair`/`waiting`).

- [ ] **Step 3: The coordinator.** `classify` becomes:

```python
def classify(pr: str | None, ci: str, verdict: str | None, *, reviewer: str) -> Outcome:
    """Ground truth to outcome. PURE -- no I/O, no globals.

    `repair` and `waiting` are the closed loop's words (spec §2): the runner
    acts on `next_step`, and these reach the scorecard only when a pass ends
    on them. The reviewed sha is deliberately NOT an input: it is evidence
    attached to the result, not a classification input.
    """
    if not pr:
        return Outcome("timeout", "na", "na",
                       "no PR at timeout (reaped before implement delivered)")
    if ci == "red":
        return Outcome("repair", "na", "red", "PR up, CI red; a repair round is owed")
    if ci == "pending":
        return Outcome("waiting", "na", "pending", "CI still pending; the next wave resumes")
    if verdict == "PASS":
        return Outcome("ready", f"{reviewer}:pass", "green", "out-of-band review PASS")
    if verdict == "FAIL":
        return Outcome("repair", f"{reviewer}:fail", "green", "out-of-band review FAIL; a repair round is owed")
    # NONE, empty, or anything unrecognised. A reviewer that crashed, timed out
    # or rambled has approved NOTHING; reading silence as approval is how a
    # merge gets authorised by an absence.
    return Outcome("escalated", f"{reviewer}:na", "green",
                   "review produced no verdict; needs human")
```

Delete `revision_confirmed`. In `outcome.py` delete `Deps.revisions_left` and pass no `revisions_left` to `classify`. In `wiring.live_deps` and `cli.py`'s `derive` handler drop the parameter and the `--revisions-left` argument and its comment; delete the `revisions` parser and handler. `Derived.findings`'s comment says "on every FAIL".

- [ ] **Step 4: The runner.** Delete `_max_revisions`, `_revision_is_recorded`, `_terminal_review_flag` and their comments and self-test blocks (the `_max_revisions OK` block, the `_revision_is_recorded` block, and the `BIRCHER_MAX_REVISIONS=0` assertions around `_findings_path`); `_findings_path` becomes `printf '%s' "${NOOP_DIR}/${1}.findings"` with a comment that the findings file always exists for a FAIL now; `observe_outcome` loses its `revisions_left` argument (the findings path is now `$5`; update its comment, and `_step_loop`'s call becomes `observe_outcome "$item" "$code" "$pr" "$_iss" "$_ffile"`). Remove `BIRCHER_MAX_REVISIONS` from the env-boundary list if it is registered there (`grep -rn BIRCHER_MAX_REVISIONS batch v2 tools`). In `README.md` and `docs/` replace the `BIRCHER_MAX_REVISIONS` entries with one sentence: repair rounds are unbounded; a run parks `no_progress` after three identical rounds.

- [ ] **Step 5: Run everything**

Run: `cd v2 && python -m pytest -q` and `bash batch/run-queue.sh --self-test > /tmp/selftest-t10.log 2>&1; echo rc=$?; grep -n "^FAIL" /tmp/selftest-t10.log` and `grep -rn "revisions_left\|BIRCHER_MAX_REVISIONS\|_max_revisions\|revision_confirmed" v2 batch README.md docs --include=*.py --include=*.sh --include=*.md | grep -v "superpowers/specs\|superpowers/plans\|front-half-live-log"`
Expected: green, and the grep prints nothing.

- [ ] **Step 6: Commit** (after `repoint-citations` once more)

```bash
git add v2 batch README.md docs
git commit -m "The revision bound goes: rounds are unbounded, no_progress parks"
```

---

### Task 11: Documentation, whole-suite gates, the pull request

**Files:**
- Modify: `docs/design/ARCHITECTURE.md` (§2b, §3.3, §4, §5, §9)
- Modify: `docs/superpowers/specs/2026-08-31-repair-loop-design.md` (a superseded note at the top)
- Modify: `docs/design/effect-site-inventory.md` (the park-notice comment site, if the inventory test did not already force it)

- [ ] **Step 1: ARCHITECTURE.md.** In §2b, the `repair loop` row's target column becomes `kernel-owned closed loop; unbounded, parks no_progress`. In §3.3: the state diagram's `--record_review--> (by verdict)` line becomes `--record_review--> reviewing` and gains `--request_repair--> planned` beneath it; the `record_review` table becomes one row per verdict all landing in `reviewing`, with a sentence: "In the back half a verdict is evidence, not a transition (closed-loop spec §1). The one door back to `planned` is `request_repair` (`REPAIR_CAUSES`: `ci_red`, `review_fail`, `plan_conformance`), legal from `implementing` and `reviewing`. The front-half phases keep their own destinations."; the sentence `record_run_outcome is legal from every state except ended` gains "and refused from an active state while the run's pull request is open or unobserved"; `park` and `grant_round` are described as legal in the back half; `repair_requested` joins the fact-kinds list. In §4, rewrite the steps that describe the loop (from "derive" through the merge) as: derive the ground truth; record the output, the CI observation with the PR's state and failing jobs, and the verdict with its fingerprints; ask `next_step`; on `repair`, `request_repair` then a repair session on the PR's branch, and derive again; on `merge`, the in-run merge; on `wait`, end the pass with the run open; on `park`, the carrier session, the park fact and the notice; and that waves resume every open back-half run. In §5, the coordinator-review row's bound column becomes `unbounded; no_progress after three identical rounds`. In §9 add a dated subsection after "The repair loop, as of 2026-08-31":

```markdown
### The closed loop, as of 2026-09-19

Built on the repair loop and replacing its bound. `run_item`'s while-loop is
`_step_loop`: derive, record, ask `next_step`, perform the one step it names.
`request_repair` is the only door back to `planned`; `record_run_outcome` is
refused while the PR is open; the sweep re-queues every open back-half run,
so a dead session, a crash or an outage costs one wave, not the run. Progress
is judged by evidence sets -- failing job names, finding fingerprints -- and
three identical rounds park `no_progress`, answered by `retry` or `stop` in a
carrier session.

NOT YET PROVEN LIVE. The E11 criterion (a run that ends red is merged with no
human touch) is the next PR's, with orphan adoption, state-derived labels and
the round log. What is proven here is by test: the kernel refusals, the
no-progress judgement on #764/#769-shaped journals, `_step_loop` against a
scripted world, and the self-test's sweep fixtures.

Known limits carried forward: a resumed run at `reviewing` re-reviews a head
that already holds a verdict (one extra review per crash); a legacy run at
`planned` through the old `request_revision` transition repairs without a
`repair_requested` fact for that round; a merge that keeps failing is retried
each wave until a person stops it.
```

- [ ] **Step 2: The superseded note.** At the top of `docs/superpowers/specs/2026-08-31-repair-loop-design.md`, under the title: `> **Superseded 2026-09-19** by [the back-half closed loop](2026-09-19-back-half-closed-loop-design.md): the bound, `revise` and the runner-owned while-loop are gone. Kept as the record of what the repair loop proved.`

- [ ] **Step 3: The gates.**

Run: `cd v2 && python -m pytest -q 2>&1 | tail -3` and `bash batch/run-queue.sh --self-test > /tmp/selftest-final.log 2>&1; echo rc=$?; grep -n "^FAIL" /tmp/selftest-final.log` and `python3 tools/repoint-citations.py <the baseline of the last run-queue.sh edit>` (once) and `git diff --stat main...HEAD | tail -1`
Expected: pytest green; self-test rc 0; no `UNRESOLVED` lines; the diff touches only the files this plan names.

- [ ] **Step 4: Commit and the PR.**

```bash
git add docs
git commit -m "docs: the closed loop"
git push -u origin plan/closed-loop
```

Write the PR body to a file (no attribution lines, no session links) and open it with `gh pr create --repo abedegno/bircher --base main --title "The back half closes its own loop" --body-file <file>`. The body: the spec's one-paragraph summary of §1-§3; what each task landed (one line each); the gates run and their results; the "not yet proven live" paragraph from Step 1 verbatim; what the next PR carries (adoption, labels, round log, E11).

---

## Self-review notes

- Spec §1 (state machine): Tasks 1, 3, 4, 9 (`request_repair`, parks and grants, the ending rule, the freeze). Spec §2 (the step function and the loop): Tasks 5, 6, 7, 9; waves resume through Task 9's sweep and `_resume_back_half`. Spec §3 (unbounded rounds, fingerprints, `no_progress`, `retry` resets the window): Tasks 2, 3, 8, 10. The park notice on the issue and the carrier session are Task 7's `_park_back_half`; the human replies are Task 8's `park-reply`.
- Type consistency: `Derived` has thirteen fields everywhere (Tasks 2, 7, 9); `Ground` has six fields (Task 5) and the `step` CLI builds exactly those (Task 6); `_kernel_record_ci` has seven arguments (Task 2) and every call passes them (Tasks 2, 7); `_step` vocabulary `wait|park|merge|done|none` is shared by `_step_loop`, `_finish_pass`, `run_item` and `_resume_back_half` (Tasks 7, 9); `park-reply`'s six words are matched by `_resume_back_half`'s `case` (Tasks 8, 9).
- Ordering: every commit leaves the tree green. Task 7 adds unused functions and guards the old loop's verdict record; Task 9 wires the loop and freezes the verdict destinations in the same commit, because the old loop is the only thing the freeze breaks; Task 10 removes what nothing reads any more.
