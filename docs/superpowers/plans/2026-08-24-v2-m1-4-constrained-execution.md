# Bircher v2 — Milestone 1, Plan 4: Constrained Execution and the Scar/Effect Matrix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every externally visible mutation in `run-queue.sh` through an injected effect adapter that delegates to the v2 kernel, and prove the routing is exhaustive rather than assumed.

**Architecture:** The coordinator keeps its orchestration — that is where the scars live — but loses its authority. Every `gh` mutation, `git push` and status publication is replaced by a call to `_effect <class> ...`, which delegates to the kernel's journal via a CLI. A mechanical detector asserts no direct mutation survives outside the adapter, and a scar/effect matrix records, per v1 behaviour, the mutation that breaks it and the v2 test that must then fail.

**Tech Stack:** bash (the existing coordinator), Python 3.11 + pytest (kernel and detector), `gh`, `git`.

**Spec:** `docs/design/2026-08-23-v2-kernel-design.md` (branch `v2`, commit `6a2be96`)

## Global Constraints

- **A trap-based test cannot prove the boundary and must not be presented as doing so.** A PATH shim in the coordinator says nothing about a model session — a separate process that can use an absolute path, another HTTP client, an SDK or a language runtime. "Every mutation-capable command" is not enumerable: `python`, `node` and shell built-ins all originate network effects. Trapping `curl` also breaks the retained path outright, since session creation, prompting, polling and stopping are themselves mutating POSTs.
- **The authority-boundary proof is M1-1's end-to-end capability test.** The three criteria in this plan — structural routing, fault injection, provider-control classification — are **coverage evidence**, and must never be described as the proof.
- **The unit of preservation is behaviour, not functions.** A pure leaf may be called temporarily, but only after its globals, subprocesses and dynamic-scope dependencies are inventoried.
- **The port is complete only when a mutation of the corresponding v1 scar still fails an equivalent v2 test.** Porting named classifiers while losing scars encoded in orchestration order, timeout handling and fail-closed error paths satisfies the letter and loses the point.
- **`accept` never means merge.** Only the kernel authorizes a merge.
- **Reads are not journalled; every externally visible mutation is.**

**Depends on:** M1-3 (`EffectClass`, `perform`, `OwnershipLost`, `UncertainEffect`), M1-3b (`dispatch`, `actor_for`, `revalidate_merge`), M1-2 (`Store`). M1-1's gate must be green before any task here runs a model session.

## Reconciled against M1-3b, 2026-08-25

This plan was written before the identity substrate existed. Three things changed under it:

- **The effect journal still records `actor="kernel"` on every fact** — the same defect commands had, in the half of the system this plan is about. §4b makes `actor="kernel"` correct only for facts the kernel originates itself, and an effect is requested by a dispatched attempt. Task 0 below closes it, and everything downstream depends on it: an adapter that routes every mutation into a journal that cannot say *who* asked has moved the authority without making it auditable.
- **A merge effect now revalidates its full authorization** immediately before executing (`revalidate_merge`). The CLI in Task 2 therefore cannot merge merely by being asked: the run must be in `merge_requested` with a standing approval over the current artifact and green CI on the authorized head. This is the intended behaviour and Task 4's fault injection should exercise it.
- **The four residuals from M1-3b land here.** CI status and CI head are reported rather than observed; the context bundle hash is never seen by the kernel; the policy version is compared to nothing. `docs/design/provenance-table.md` names them and `test_the_residuals_are_the_ones_we_know_about` fails if a new one appears. M1-4 owns CI status and head, because routing status publication through the adapter is where the kernel first touches a check run.

---

### Task 0: The effect journal names who asked

**Files:**
- Modify: `v2/kernel/effects.py`, `v2/tests/kernel/test_effects.py`
- Create: `v2/tests/kernel/test_effect_identity.py`

**Interfaces:**
- Consumes: `actor_for` (M1-3b).

- [x] **Step 1: Write the failing tests**

```python
def test_an_effect_fact_names_the_dispatched_actor():
    s, gen = _dispatched("claude")
    perform(s, "r", gen, EffectClass.COMMENT, "k", {}, lambda *a: "ok")
    facts = [f for f in s.facts_for("r") if f.kind.startswith("effect_")]
    assert {f.actor for f in facts} == {"claude"}


def test_an_undispatched_generation_cannot_perform_an_effect():
    """Fail closed, exactly as submit() does. An effect the journal cannot
    attribute is an unattributable external mutation."""
    s = _store()
    dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER)
    self_fenced = acquire(s, "r", "claude")
    with pytest.raises(NotAuthorized, match="no dispatched actor"):
        perform(s, "r", self_fenced, EffectClass.COMMENT, "k", {}, _never_runs)


def test_a_refused_effect_does_not_execute():
    """The witness: refusing after executing is not refusing."""
```

- [x] **Step 2: Implement**

Resolve `actor = actor_for(store, run_id, generation)` at the top of `_perform_unhalted`, before the idempotency read; refuse with `NotAuthorized` when it is `None`; pass it to all three `append_fact` calls. `EFFECT_RECONCILED` keeps `actor="human"` — reconciliation is an operator action, and that is a fact about a human, not an attempt.

- [x] **Step 3: Migrate `test_effects.py`** from `acquire()` to `dispatch()`, exactly as M1-3b migrated the command tests. A test that self-fences will now be refused, which is the point.

- [x] **Step 4: Mutation-test**

Three mutations, each in isolation against a committed tree: record `actor="kernel"` again; drop the `actor is None` refusal; refuse *after* invoking the executor. Each must red exactly its named test.

---

**The approach decision, and its cost.** The spec offers two routes: route every effect through an injected adapter, or stop running the coordinator and retain only extracted logic. **This plan takes the adapter route**, because the scars the spec insists on preserving are distributed across effectful orchestration — `merge_ready_pr` alone combines required-context discovery, status publication, mergeability polling, the merge, reconciliation, main-CI observation and possible revert — and extracting logic from that would discard the orchestration order the scars live in. The cost is that `run-queue.sh` remains in the loop for Milestone 1, so its authority is removed rather than its code. M1-5 and later milestones may retire it.

---

## File Structure

| File | Responsibility |
|---|---|
| `batch/lib/effect-adapter.sh` | `_effect` — the single seam every mutation goes through. |
| `v2/kernel/cli.py` | `bircher-effect` — journals and performs an effect from the shell. |
| `v2/tools/detect_direct_effects.py` | The detector: no mutation outside the adapter. |
| `v2/tests/execution/test_routing.py` | Criterion 1, and the detector's own planted positives. |
| `v2/tests/execution/test_fault_injection.py` | Criterion 2. |
| `v2/tests/execution/test_provider_control.py` | Criterion 4. |
| `docs/design/scar-effect-matrix.md` | The required Milestone 1 artifact. |
| `v2/tests/execution/test_scar_equivalence.py` | Mutation-equivalence between v1 scars and v2 tests. |

---

### Task 1: Enumerate the effect sites exhaustively

Everything downstream depends on this list being complete, and a grep that misses one produces a detector that certifies a boundary with a hole in it.

**Files:**
- Create: `docs/design/effect-site-inventory.md`

**Interfaces:**
- Produces: the inventory, with a total count the detector asserts against.

- [x] **Step 1: Enumerate, and do not trust a single pattern**

Run each of these separately and union the results. They overlap deliberately: one pattern's exclusion is another's inclusion, and *the filter is where detectors fail*.

```bash
cd /Users/jonw/bircher
grep -nE "gh (pr|issue) (merge|close|reopen|comment|edit|create|review)" batch/run-queue.sh
grep -nE "gh api [^|]*-X (POST|PUT|PATCH|DELETE)" batch/run-queue.sh
grep -nE "gh api [^|]*statuses" batch/run-queue.sh
grep -nE "git .*push" batch/run-queue.sh
grep -nE "gh .*--add-label|--remove-label" batch/run-queue.sh
```

- [x] **Step 2: Record the inventory**

Write `docs/design/effect-site-inventory.md` with one row per site: line, command, effect class (from M1-3's eight), and whether it is a mutation or a read. These are the sites confirmed present at the time of writing — **re-run Step 1 and reconcile before relying on them**, because line numbers move:

| Line | Call | Effect class |
|---|---|---|
| 366 | `gh issue reopen` | `issue_or_label` |
| 1276 | `gh api repos/$REPO/statuses/$sha -X POST` | `status_check` |
| 1503 | `gh pr merge --squash --delete-branch` | `pull_request` |
| 1707 | `git push origin HEAD:main` | `ref_update` |
| 1800 | `gh api repos/$REPO/pulls/$pr/update-branch -X PUT` | `ref_update` |
| 1883 | `gh api repos/$REPO/pulls/$pr/update-branch -X PUT` | `ref_update` |
| 1963 | `gh pr close` | `pull_request` |
| 2122 | `gh pr comment` | `comment` |
| 2964 | `gh issue comment` | `comment` |
| 2965 | `gh issue edit --remove-label` | `issue_or_label` |
| 2966 | `gh issue edit --add-label` | `issue_or_label` |
| 2982 | `gh issue close` | `issue_or_label` |
| 3143 | `gh issue edit --add-label bircher:running` | `issue_or_label` |

Record the total. Note explicitly which `gh api` calls are **reads** (e.g. 1331 `compare`, 1348 `git/trees`, 2262 `branches/protection`, 2512 required contexts) so a later reader can tell an omission from a classification.

- [x] **Step 3: Commit**

```bash
git add docs/design/effect-site-inventory.md
git commit -m "docs(v2): exhaustive effect-site inventory for run-queue.sh

Unions five independent grep patterns rather than trusting one, because
a detector built on an incomplete list certifies a boundary with a hole
in it. Classifies reads explicitly so an omission is distinguishable
from a classification."
```

---

### Task 2: The effect adapter seam

**Files:**
- Create: `batch/lib/effect-adapter.sh`, `v2/kernel/cli.py`
- Modify: `batch/run-queue.sh` (every mutation site from Task 1)

**Interfaces:**
- Consumes: `perform`, `EffectClass`, `Store` (M1-3).
- Produces: `_effect <class> <idempotency_key> <argv...>` in shell; `bircher-effect` CLI.

- [x] **Step 1: Write the adapter**

```bash
# batch/lib/effect-adapter.sh
# The single seam every externally visible mutation passes through.
#
# BIRCHER_EFFECT_MODE:
#   kernel  - delegate to the v2 kernel, which journals intent before acting
#   deny    - refuse every effect (fault injection; the constrained mode)
#   legacy  - v1 behaviour, direct execution. Retained only for bisection.
_effect() {
  local class="$1" key="$2"; shift 2
  case "${BIRCHER_EFFECT_MODE:-deny}" in
    kernel)
      python3 -m kernel.cli --run-id "${BIRCHER_RUN_ID:?}" \
        --generation "${BIRCHER_GENERATION:?}" --class "$class" \
        --idempotency-key "$key" -- "$@"
      ;;
    deny)
      echo "effect refused: $class $key ($*)" >&2
      return 87   # distinct code so fault-injection tests can assert the reason
      ;;
    legacy)
      "$@"
      ;;
    *)
      echo "unknown BIRCHER_EFFECT_MODE: ${BIRCHER_EFFECT_MODE}" >&2
      return 2
      ;;
  esac
}
```

**Default is `deny`.** An unset variable must not silently restore v1 authority — fail closed, in the direction that stops work rather than the one that performs an unmediated mutation.

- [x] **Step 2: Write the CLI**

```python
# v2/kernel/cli.py
"""bircher-effect -- journal and perform one effect from the shell.

The coordinator keeps its orchestration; this is where its authority goes.
"""
from __future__ import annotations
import argparse, subprocess, sys
from kernel.store import Store
from kernel.effects import EffectClass, perform, UncertainEffect
from kernel.ownership import OwnershipLost

def _executor(effect_class, intent, idempotency_key):
    """Run the real command from the kernel's credential domain."""
    r = subprocess.run(intent["argv"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{effect_class} failed: {r.stderr[:200]}")
    return r.stdout.strip() or "ok"

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-effect")
    p.add_argument("--db", default="v2/kernel.db")
    p.add_argument("--run-id", required=True)
    p.add_argument("--generation", type=int, required=True)
    p.add_argument("--class", dest="effect_class", required=True,
                   choices=sorted(EffectClass.ALL))
    p.add_argument("--idempotency-key", required=True)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("no command given", file=sys.stderr)
        return 2
    store = Store.open(a.db)
    try:
        print(perform(store, a.run_id, a.generation, a.effect_class,
                      a.idempotency_key, {"argv": cmd}, _executor))
        return 0
    except OwnershipLost as e:
        print(f"fenced: {e}", file=sys.stderr)
        return 88
    except UncertainEffect as e:
        print(f"uncertain, run halted: {e}", file=sys.stderr)
        return 89

if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 3: Route one site, and prove the seam works before touching the rest**

Start with the merge-authorizing status publication at `batch/run-queue.sh:1276`, because it is the effect the spec singles out as authority-bearing.

```bash
# before
gh api "repos/$REPO/statuses/$sha" -X POST -f state=success ...
# after
_effect status_check "status:${sha}" \
  gh api "repos/$REPO/statuses/$sha" -X POST -f state=success ...
```

Run the coordinator's own selftest to confirm nothing else broke:

```bash
bash batch/run-queue.sh --selftest
```

- [x] **Step 4: Route every remaining site from the inventory**

Idempotency keys must be **derived from the object, not from a counter** — a retry after a crash must produce the same key, or the journal cannot suppress the duplicate. Use `<class>:<stable-object-id>`: `status:$sha`, `merge:$pr`, `comment:$pr:$(printf '%s' "$body" | sha256sum | cut -c1-16)`, `label:$issue:$add`.

- [x] **Step 5: Commit**

```bash
git add batch/lib/effect-adapter.sh v2/kernel/cli.py batch/run-queue.sh
git commit -m "feat(v2): route every run-queue effect through the adapter seam

Default mode is deny: an unset BIRCHER_EFFECT_MODE must not silently
restore v1 authority. Idempotency keys derive from the object rather
than a counter, so a retry after a crash produces the same key and the
journal can suppress the duplicate."
```

---

### Task 3: The routing detector, and its own planted positives

This is a **verification tool**, so it needs more adversarial review than the thing it verifies: its failures are silent by construction — a detector that certifies everything prints exactly what a working one prints.

**Files:**
- Create: `v2/tools/detect_direct_effects.py`, `v2/tests/execution/test_routing.py`

**Interfaces:**
- Produces: `find_direct_effects(path) -> list[Finding]`.

- [x] **Step 1: Write the detector**

```python
# v2/tools/detect_direct_effects.py
"""Find externally visible mutations that bypass the _effect adapter.

Read what this throws away before you read what it does: every exclusion
below is a place the detector can go silently blind.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

MUTATION = re.compile(r"""
    gh\s+(pr|issue)\s+(merge|close|reopen|comment|edit|create|review)
  | gh\s+api\b[^|\n]*-X\s+(POST|PUT|PATCH|DELETE)
  | gh\s+api\b[^|\n]*statuses
  | git\s+(-C\s+\S+\s+)?push
""", re.VERBOSE)

@dataclass(frozen=True)
class Finding:
    line: int
    text: str

def find_direct_effects(path: str) -> list[Finding]:
    out = []
    for n, raw in enumerate(open(path), 1):
        line = raw.rstrip("\n")
        stripped = line.lstrip()
        # EXCLUSION 1: comments. Prose about gh pr merge is not a call.
        if stripped.startswith("#"):
            continue
        # EXCLUSION 2: lines already routed. Anchored to the call position so
        # a comment mentioning _effect cannot launder a real mutation.
        if re.search(r"(^|\|\||&&|;|\bthen\b|\$\()\s*_effect\s", line):
            continue
        # EXCLUSION 3: the adapter's own dispatch line, which must execute
        # directly -- that is its purpose. Narrowed to that one construct: an
        # earlier version skipped the whole file, which would have hidden any
        # unrouted mutation added to the adapter itself.
        if re.match(r'^"?\$@"?$', stripped) or stripped == 'legacy)':
            continue
        if MUTATION.search(line):
            out.append(Finding(n, stripped[:120]))
    return out
```

- [x] **Step 2: Write the tests, including a planted positive per exclusion**

Each exclusion gets its own planted positive, drawn from the real shape it must not blind the detector to. A single "known bad line" test would pass while three exclusions went unchecked.

```python
# v2/tests/execution/test_routing.py
import pathlib, textwrap, pytest
from tools.detect_direct_effects import find_direct_effects

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

def _scan(tmp_path, body):
    p = tmp_path / "sample.sh"
    p.write_text(textwrap.dedent(body))
    return find_direct_effects(str(p))

def test_planted_positive_bare_mutation_is_found(tmp_path):
    assert _scan(tmp_path, '''
        gh pr merge "$pr" --repo "$REPO" --squash
    ''')

def test_planted_positive_git_push_is_found(tmp_path):
    assert _scan(tmp_path, '''
        git push origin HEAD:main -q
    ''')

def test_planted_positive_gh_api_post_is_found(tmp_path):
    assert _scan(tmp_path, '''
        gh api "repos/$REPO/statuses/$sha" -X POST -f state=success
    ''')

def test_exclusion_1_a_comment_mentioning_a_mutation_is_not_a_call(tmp_path):
    assert not _scan(tmp_path, '''
        # historically this called gh pr merge directly
    ''')

def test_exclusion_2_cannot_be_laundered_by_a_trailing_comment(tmp_path):
    """The planted positive for the _effect exclusion. A line that MENTIONS
    _effect while performing a bare mutation must still be caught."""
    assert _scan(tmp_path, '''
        gh pr merge "$pr" --repo "$REPO"   # replaces _effect pull_request
    ''')

def test_exclusion_3_only_hides_the_adapter_dispatch_not_the_whole_file(tmp_path):
    """The planted positive for exclusion 3. A real unrouted mutation added to
    the adapter file must still be caught."""
    p = tmp_path / "effect-adapter.sh"
    p.write_text('gh pr merge "$pr" --repo "$REPO"\n')
    assert find_direct_effects(str(p))

def test_a_routed_mutation_is_not_flagged(tmp_path):
    assert not _scan(tmp_path, '''
        _effect pull_request "merge:$pr" gh pr merge "$pr" --repo "$REPO"
    ''')

def test_routed_after_a_conditional_is_not_flagged(tmp_path):
    assert not _scan(tmp_path, '''
        [ -n "$pr" ] && _effect comment "c:$pr" gh pr comment "$pr" --body x
    ''')

def test_criterion_1_run_queue_has_no_unrouted_mutation():
    """Acceptance criterion 1 (structural routing). Coverage evidence -- NOT
    the authority-boundary proof, which is M1-1's capability test."""
    findings = find_direct_effects(str(REPO_ROOT / "batch" / "run-queue.sh"))
    assert not findings, "unrouted mutations:\n" + "\n".join(
        f"  {f.line}: {f.text}" for f in findings)
```

- [x] **Step 3: Run**

Run: `cd v2 && python -m pytest tests/execution/test_routing.py -v`
Expected: every planted positive passes, and criterion 1 passes once Task 2 routed all sites. If criterion 1 fails, the inventory was incomplete — go back to Task 1 rather than widening an exclusion.

- [x] **Step 4: Adversarially review the exclusions**

For each of the three exclusions, write down in `v2/tools/detect_direct_effects.py` what shape it could blind the detector to, and confirm a test covers it. In a sibling programme four detectors passed vacuously and **in every case the analysis was correct and the exclusion was wrong**.

- [x] **Step 5: Commit**

```bash
git add v2/tools/detect_direct_effects.py v2/tests/execution/test_routing.py
git commit -m "feat(v2): detector for unrouted effects, with per-exclusion positives

A detector's failures are silent by construction, so each of its three
exclusions carries its own planted positive -- including a mutation that
merely mentions _effect in a comment, which the anchored exclusion must
still catch."
```

---

### Task 4: Fault injection and provider-control classification

Criteria 2 and 4. They belong together because the second exists to stop the first from proving too much: if every effect were denied, session control would break and the run could not proceed at all.

**Files:**
- Create: `v2/tests/execution/test_fault_injection.py`, `v2/tests/execution/test_provider_control.py`

- [x] **Step 1: Write the fault-injection tests**

```python
# v2/tests/execution/test_fault_injection.py
"""Criterion 2: every effect branch is driven and the adapter denies it.

Coverage evidence, not the authority-boundary proof."""
import subprocess, pytest

CLASSES = ["ref_update", "pull_request", "status_check", "comment",
           "issue_or_label", "revert_or_recovery"]

def _adapter(class_, *argv, mode="deny"):
    return subprocess.run(
        ["bash", "-c",
         f'source batch/lib/effect-adapter.sh; _effect {class_} key {" ".join(argv)}'],
        capture_output=True, text=True, env={"BIRCHER_EFFECT_MODE": mode, "PATH": "/usr/bin:/bin"})

@pytest.mark.parametrize("class_", CLASSES)
def test_deny_mode_refuses_every_effect_class(class_):
    r = _adapter(class_, "true")
    assert r.returncode == 87, f"{class_} was not denied (rc={r.returncode})"

@pytest.mark.parametrize("class_", CLASSES)
def test_denial_does_not_execute_the_command(class_, tmp_path):
    """A denial that still runs the command is not a denial. Uses a
    filesystem side effect as the witness."""
    witness = tmp_path / "ran"
    _adapter(class_, "touch", str(witness))
    assert not witness.exists(), f"{class_}: command executed despite denial"

def test_unset_mode_fails_closed():
    """An unset variable must not restore v1 authority."""
    r = subprocess.run(
        ["bash", "-c", 'source batch/lib/effect-adapter.sh; _effect comment k true'],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 87, "unset BIRCHER_EFFECT_MODE did not fail closed"

def test_unknown_mode_is_refused_rather_than_defaulting():
    r = _adapter("comment", "true", mode="yolo")
    assert r.returncode == 2
```

- [x] **Step 2: Write the provider-control tests**

```python
# v2/tests/execution/test_provider_control.py
"""Criterion 4: provider-control effects are PERMITTED kernel effects, tested
separately from forbidden GitHub and repository mutations.

Session creation, prompting, polling and stopping are themselves mutating
POSTs. A boundary that denied them would break the retained path outright --
which is exactly why trapping `curl` cannot prove anything."""
from kernel.effects import EffectClass

PERMITTED = {EffectClass.SESSION_CONTROL}
FORBIDDEN_TO_MODELS = {
    EffectClass.REF_UPDATE, EffectClass.PULL_REQUEST, EffectClass.STATUS_CHECK,
    EffectClass.COMMENT, EffectClass.ISSUE_OR_LABEL,
    EffectClass.REVERT_OR_RECOVERY, EffectClass.CREDENTIAL_LIFECYCLE,
}

def test_the_two_sets_partition_the_eight_classes():
    """No class may be silently unclassified -- that is how PR creation fell
    out of an earlier journal."""
    assert PERMITTED | FORBIDDEN_TO_MODELS == EffectClass.ALL
    assert not (PERMITTED & FORBIDDEN_TO_MODELS)

def test_session_control_is_permitted_and_journalled():
    """Permitted does not mean unjournalled: ownership ambiguity from a
    session stop is exactly what the journal must record."""
    assert EffectClass.SESSION_CONTROL in EffectClass.ALL
    assert EffectClass.SESSION_CONTROL in PERMITTED
```

- [x] **Step 3: Run and commit**

Run: `cd v2 && python -m pytest tests/execution/ -v`

```bash
git add v2/tests/execution/test_fault_injection.py v2/tests/execution/test_provider_control.py
git commit -m "feat(v2): fault-injection and provider-control classification

Criterion 2 drives every effect branch and asserts denial, with a
filesystem witness proving the command did not run -- a denial that
still executes is not a denial. Criterion 4 partitions all eight classes
so none can be silently unclassified, and records that session control
is permitted BUT still journalled, because stop ambiguity is what the
journal exists to capture."
```

---

### Task 5: The scar/effect matrix and mutation equivalence

The required Milestone 1 artifact. Without it, "the full retained path" can mean a happy path.

**Files:**
- Create: `docs/design/scar-effect-matrix.md`, `v2/tests/execution/test_scar_equivalence.py`

- [x] **Step 1: Build the matrix**

One row per v1 behaviour. Columns exactly as the spec names them: source location, the mutation that breaks it, the v2 owner, the test fixture, the injected fault, the expected durable events, and the effects it is permitted or forbidden to perform.

Seed it with the behaviours the spec names and the inventory found:

| v1 behaviour | Source | Mutation that breaks it | v2 owner | Test fixture | Fault injected | Expected durable events | Effects |
|---|---|---|---|---|---|---|---|
| CI failure classification | `run-queue.sh:337` `_classify_ci_failure` | reclassify `infrastructure_failure` as `code_failure` | kernel classifier | `fixtures/ci_runner_timeout.json` | CI returns a runner timeout | `external_observation` | none |
| Merge marker parsing | `run-queue.sh:129` `parse_marker` | accept a marker from any comment author | kernel command validation | `fixtures/marker_foreign_author.json` | comment by a non-runner identity | `command_rejected` | none |
| Merge orchestration | `run-queue.sh:1436` `merge_ready_pr` | skip required-context discovery | kernel `request_merge` | `fixtures/pr_context_pending.json` | a required context is pending | `command_rejected`, no `effect_intended` | `pull_request` |
| Merge-authorizing status | `run-queue.sh:1276` | publish success before contexts pass | kernel effect journal | `fixtures/pr_context_pending.json` | context still pending | `effect_intended` then refusal | `status_check` |
| Recovery push bound | `run-queue.sh:1707` | drop `_net_run` timeout wrapper | kernel effect executor | `fixtures/push_hangs.sh` | push hangs | `effect_uncertain` | `ref_update` |
| PR recovery checkout | `run-queue.sh:1868` `recover_pr_cmd` | join commands with `;` instead of `&&` | kernel artifact import | `fixtures/fetch_fails.sh` | fetch fails | no `review_verdict` recorded | none |

Every row that is **excluded** from the port carries an explicit disposition and reason. Completion means every retained row passes and every excluded row is dispositioned.

- [x] **Step 2: Write the equivalence tests**

```python
# v2/tests/execution/test_scar_equivalence.py
"""The port is complete only when a mutation of the v1 scar still fails an
equivalent v2 test.

Porting named classifiers while losing scars encoded in orchestration order,
timeout handling and fail-closed error paths satisfies the letter and loses
the point -- so each test names the v1 line it corresponds to."""
import pytest, re, pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MATRIX = REPO_ROOT / "docs" / "design" / "scar-effect-matrix.md"

def _table():
    return [l for l in MATRIX.read_text().splitlines()
            if l.startswith("|") and "---" not in l]

def _header_cells():
    return _table()[0].split("|")[1:-1]

def _rows():
    return [r.split("|")[1:-1] for r in _table()[1:]]

def test_every_matrix_row_names_a_real_v1_location():
    """A row citing a line that does not exist is a claim about source that is
    false -- the defect class this programme exists to catch."""
    src = (REPO_ROOT / "batch" / "run-queue.sh").read_text().splitlines()
    for row in _rows():
        m = re.search(r"run-queue\.sh:(\d+)", row[1])
        assert m, f"row has no source citation: {row[0]}"
        n = int(m.group(1))
        assert 0 < n <= len(src), f"{row[0]}: line {n} is out of range"

def test_every_matrix_row_has_a_disposition_or_an_owner():
    for row in _rows():
        assert row[3].strip(), f"{row[0].strip()}: no v2 owner and no disposition"

EXPECTED_COLUMNS = ["v1 behaviour", "source", "mutation that breaks it",
                    "v2 owner", "test fixture", "fault injected",
                    "expected durable events", "effects"]

def test_column_order_is_what_the_index_assertions_assume():
    """Without this, reordering the table would silently make every check
    below inspect the wrong column and still pass."""
    header = [c.strip().lower() for c in _header_cells()]
    assert header == EXPECTED_COLUMNS

def test_every_retained_row_has_a_fixture_and_an_injected_fault():
    """A row with no fault is a happy path wearing a matrix row's costume."""
    for row in _rows():
        assert row[4].strip(), f"{row[0].strip()}: no test fixture"
        assert row[5].strip(), f"{row[0].strip()}: no injected fault"
```

- [x] **Step 3: Run the equivalence check for one scar end to end**

Take the `_classify_ci_failure` row. Mutate v1 so an infrastructure failure classifies as a code failure, confirm v1's own selftest catches it (or record that it does not), then confirm the v2 test for the same behaviour fails under the equivalent mutation. Record both results in the matrix row.

```bash
git stash list   # ensure a clean tree first
python3 - <<'EOF'
import pathlib
p = pathlib.Path("batch/run-queue.sh"); t = p.read_text()
# Mutation: collapse the infrastructure classification into code failure.
p.write_text(t.replace('echo "infrastructure_failure"', 'echo "code_failure"', 1))
EOF
bash batch/run-queue.sh --selftest        # expect FAIL; record if it passes
git checkout batch/run-queue.sh && git status --short
```

**If v1's selftest passes under that mutation, say so in the matrix.** It means the v1 scar was never bound by a test either, and the v2 test is new coverage rather than a port — a materially different claim, and one the matrix must not blur.

- [x] **Step 4: Commit**

```bash
git add docs/design/scar-effect-matrix.md v2/tests/execution/test_scar_equivalence.py
git commit -m "feat(v2): scar/effect matrix with mutation equivalence

One row per v1 behaviour with its source line, breaking mutation, v2
owner, injected fault, expected durable events and permitted effects.
Tests assert every row cites a real line, has an owner or an explicit
disposition, and names an injected fault -- a row without one is a happy
path wearing a matrix row's costume.

Where v1's own selftest survives the mutation, the matrix records that
the v2 test is new coverage rather than a port. Those are different
claims and the artifact must not blur them."
```

---

## Done means

Every mutation site in the inventory routes through `_effect`; the detector finds no unrouted mutation and carries a planted positive for each of its three exclusions; `deny` mode refuses all six mutating classes with a filesystem witness proving nothing executed, and an unset mode fails closed; all eight effect classes are partitioned into permitted and forbidden with none unclassified; and every scar/effect matrix row cites a real v1 line, names an injected fault, and has either a passing v2 equivalent or an explicit disposition. The authority-boundary proof remains M1-1's capability test — everything here is coverage evidence.


---

## Executed 2026-08-25 — what this plan got wrong

Task 0 and Tasks 1–5 are implemented and committed. 249 kernel + execution
tests pass; `bash batch/run-queue.sh --self-test` passes; the detector reports
**0 unrouted mutations** across all 13 sites. Every guard carries a mutation
that reds its named test, run one at a time against a committed tree with a
dirty-tree abort in the harness.

Seven things the plan specified incorrectly, each found by executing it:

1. **`--selftest` does not exist.** The verification step in Task 2 named a
   flag that appears nowhere in `run-queue.sh`; it is `--self-test`. Running
   the plan's command starts a real queue run instead, which is how the error
   surfaced — preflight refused, on a box with no `timeout(1)`.

2. **`_effect` needed the cap as an argument.** Every routed site sits inside
   `_net_run "$cap"`, which runs `timeout -k` and needs a real executable —
   so neither `_net_run … _effect …` (a shell function) nor
   `_effect … _net_run …` (the kernel would exec a function name) works. The
   plan's three-mode adapter had no way to preserve the bound, and #62's scar
   is that `timeout` *without* `-k` is not a bound at all.

3. **Line 1503 is class `merge`, not `pull_request`.** M1-3 split merge into
   its own class precisely so the authority-bearing operation would not share
   a gate with the routine one, and it is the only class `perform()`
   revalidates. Routing it as the plan specified would have moved the merge
   through the adapter while bypassing every check the revalidation exists to
   run — the letter satisfied, the point lost.

4. **The detector as specified could never pass.** Its regex produces six
   false positives on the real file — mutation-shaped text inside quoted
   literals — and the plan's own instruction is to go back to Task 1 rather
   than widen an exclusion. But the inventory was complete; the detector was
   wrong. It now tests where a match *starts*, joins `\`-continuations into
   logical lines (both authority-bearing sites are written across them), scans
   heredocs fed to an interpreter, and reports suppressions instead of
   dropping them.

5. **`_classify_ci_failure` returns `infra`/`genuine`**, not
   `infrastructure_failure`/`code_failure`. The plan's seed matrix row named a
   mutation that cannot be applied — a claim about the source that is false,
   which is the defect class this programme exists to catch.

6. **`parse_marker` performs no author check.** The plan's row proposed
   "accept a marker from any comment author" as the breaking mutation; there
   is nothing there to break. This turned out to matter: the spec names
   *"validating the marker against a runner-issued attempt identity"* as a
   Milestone 1 acceptance test, and §4b established there is no runner-issued
   identity to validate against. The matrix records the disposition rather
   than leaving an acceptance test that would be satisfied by inspection.

7. **The effect journal recorded `actor="kernel"` on every fact** — the gap
   Task 0 was added to close. Commands got their identity substrate in M1-3b;
   effects, the half of the system that touches the world, did not.

Two process notes:

- **A mutation survived, and the comment was the reason.** Moving the
  undispatched-actor refusal below the idempotency read left the suite green:
  the test asserted that a refused effect consumes no idempotency key, which
  is true on both sides of the move. The comment claimed that property and the
  test was written from the comment. The real property is an information leak
  — the read *returns* a confirmed effect's external object id — and two tests
  now bind it.
- **One "survival" was an invalid mutation.** Repointing a `«top-level»`
  citation from line 20 to line 19 kept it valid, because line 19 is the
  `# shellcheck source=` comment: still top-level, still containing the
  anchor. Rerun against a line inside a function, it reds correctly.

## Carried forward

- Seven routed sites pass a `-` cap, meaning unbounded — exactly as v1 left
  them. They are spelled out rather than defaulted so they stay visible.
- `recover_pr_cmd` chaining is unprobed and dispositioned in the matrix.
- `_classify_ci_failure` and `parse_marker` are bound by v1's self-test but
  not yet ported to v2; both are M1-5.
- The four provenance residuals still stand. Routing status publication puts
  the kernel next to a check run for the first time, but the kernel still
  records what an actor reports rather than observing the run itself.
