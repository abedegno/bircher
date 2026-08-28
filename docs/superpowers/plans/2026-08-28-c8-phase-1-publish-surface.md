# C8 Phase 1: the publish surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the kernel the ability to verify an implementer's nominated commit and publish it — push plus PR — from the credential domain the implementer does not have.

**Architecture:** A new pure-ish kernel module observes a git worktree and returns the object id to publish, or refuses. A new coordinator subcommand drives it and performs the two existing effect classes. `run_item` is not touched.

**Tech Stack:** Python 3.11+ (kernel), bash 3.2-compatible (coordinator), git, `gh`.

**Spec:** `docs/superpowers/specs/2026-08-28-c8-the-kernel-publishes-design.md`

## Global Constraints

- The kernel verifies **provenance and shape, never content**. No path policy.
- **Nothing model-authored decides what is published.** The observed branch tip decides; a claimed object id may only agree or cause a refusal.
- The base compared against is **`store.run_base_sha(run_id)`** — the base the kernel recorded — never the checkout's current HEAD.
- **No new effect classes and no new credential domain.** Publication uses `ref_update` and `pull_request` through `_effect`.
- Every refusal names **which check failed and the two values involved**.
- `run_item`, `parse_marker` and the marker branch are **out of scope** and must not change.
- Bash must run under `set -uo pipefail` and on bash 3.2 (no associative arrays, no `${x^^}`).

---

### Task 1: `verify_nomination`

**Files:**
- Create: `v2/kernel/nomination.py`
- Test: `v2/tests/kernel/test_nomination.py`

**Interfaces:**
- Produces: `verify_nomination(store, run_id, worktree, branch, claimed_oid=None) -> str` and `class NotPublishable(Exception)`.
- Consumes: `store.run_base_sha(run_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_nomination.py
"""What the kernel will publish is OBSERVED, not reported."""
import subprocess

import pytest

from kernel.ids import Clock
from kernel.nomination import NotPublishable, verify_nomination
from kernel.store import Store

ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _git(wt, *args):
    r = subprocess.run(["git", "-C", str(wt), *args], capture_output=True,
                       text=True, env=ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    (wt / "f").write_text("base")
    _git(wt, "add", "f")
    _git(wt, "commit", "-qm", "base")
    return wt


def _store(base):
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=base)
    return s


def test_the_branch_tip_is_what_is_published(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    assert verify_nomination(_store(base), "r", repo, "work") == tip


def test_a_commit_not_descended_from_the_RECORDED_base_is_refused(repo):
    """The base the KERNEL recorded, not the checkout's HEAD. A recovery on the
    predecessor branch bound `git rev-parse HEAD` at recovery time against a
    base recorded hours earlier and every review was refused."""
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")

    stale = "0" * 40
    with pytest.raises(NotPublishable, match="does not descend"):
        verify_nomination(_store(stale), "r", repo, "work")


def test_a_merge_commit_in_the_lineage_is_refused(repo):
    """A merge imports history the kernel never saw."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "side")
    (repo / "s").write_text("side")
    _git(repo, "add", "s"); _git(repo, "commit", "-qm", "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "m").write_text("main")
    _git(repo, "add", "m"); _git(repo, "commit", "-qm", "main2")
    _git(repo, "checkout", "-qb", "work")
    _git(repo, "merge", "--no-ff", "-m", "merge", "side")

    with pytest.raises(NotPublishable, match="merge commit"):
        verify_nomination(_store(base), "r", repo, "work")


def test_a_claimed_oid_that_differs_from_the_tip_is_REFUSED(repo):
    """Never a tiebreak and never a fallback: the observation decides, and a
    claim can only disagree with it. Both values appear in the refusal."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(NotPublishable) as e:
        verify_nomination(_store(base), "r", repo, "work", claimed_oid="b" * 40)
    assert tip in str(e.value) and "b" * 40 in str(e.value)


def test_a_matching_claim_is_accepted(repo):
    """The claim is allowed to be right; it is simply not allowed to decide."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    assert verify_nomination(_store(base), "r", repo, "work", claimed_oid=tip) == tip


def test_a_missing_branch_is_refused(repo):
    base = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(NotPublishable, match="no such branch"):
        verify_nomination(_store(base), "r", repo, "nope")


def test_a_branch_identical_to_base_publishes_nothing(repo):
    """No commit is a noop, not a publication of the base."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    with pytest.raises(NotPublishable, match="no commit"):
        verify_nomination(_store(base), "r", repo, "work")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && PATH="/Users/jonw/.local/bin:$PATH" uv run --offline --with pytest --with pyyaml pytest tests/kernel/test_nomination.py -q`
Expected: collection error — `No module named 'kernel.nomination'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/nomination.py
"""What the kernel will publish, decided by observing a worktree.

Subprocess git lives here rather than in `effects.py` because this is an
OBSERVATION of the filesystem, in the same sense `_ci_is_green` is an
observation of a recorded fact. The kernel is allowed to look; what it must
never do is take the session's word for what it would have seen.
"""

from __future__ import annotations

import subprocess


class NotPublishable(Exception):
    """The nominated work is not something the kernel will publish."""


def _git(worktree, *args) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(worktree), *args],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip()


def verify_nomination(store, run_id, worktree, branch, claimed_oid=None) -> str:
    """Return the object id to publish, or raise NotPublishable.

    Provenance and shape only. Content is the review's job -- see the spec's
    decision 1 for why a path policy was rejected.
    """
    rc, tip = _git(worktree, "rev-parse", "--verify", f"{branch}^{{commit}}")
    if rc != 0 or not tip:
        raise NotPublishable(
            f"no such branch {branch!r} in {worktree}: nothing was nominated")

    base = store.run_base_sha(run_id)
    if not base:
        raise NotPublishable(
            f"run {run_id} recorded no base sha; there is nothing to descend from")

    if tip == base:
        raise NotPublishable(
            f"branch {branch!r} is at the recorded base {base[:12]} -- no commit "
            "was made, so there is nothing to publish")

    rc, _ = _git(worktree, "merge-base", "--is-ancestor", base, tip)
    if rc != 0:
        raise NotPublishable(
            f"{tip[:12]} does not descend from the base this run recorded "
            f"({base[:12]}): the work is built on something the kernel did not see")

    rc, merges = _git(worktree, "rev-list", "--merges", f"{base}..{tip}")
    if rc != 0:
        raise NotPublishable(
            f"could not walk {base[:12]}..{tip[:12]} in {worktree}")
    if merges:
        raise NotPublishable(
            f"lineage {base[:12]}..{tip[:12]} contains a merge commit "
            f"({merges.splitlines()[0][:12]}): it imports history the kernel "
            "never observed")

    if claimed_oid is not None and claimed_oid != tip:
        raise NotPublishable(
            f"the session nominated {claimed_oid[:12]} but {branch!r} is at "
            f"{tip[:12]}: the observation decides and a claim may only agree "
            "with it")

    return tip
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && PATH="/Users/jonw/.local/bin:$PATH" uv run --offline --with pytest --with pyyaml pytest tests/kernel/test_nomination.py -q`
Expected: PASS.

- [ ] **Step 5: Mutate each guard, one at a time**

Commit first. Then, one at a time, prove each applied with `git diff`, run the named test, restore with `git checkout`:

| mutation | must red |
|---|---|
| `if rc != 0:` → `if False:` on the ancestor check | `test_a_commit_not_descended_from_the_RECORDED_base_is_refused` |
| `if merges:` → `if False:` | `test_a_merge_commit_in_the_lineage_is_refused` |
| `claimed_oid != tip` → `False` | `test_a_claimed_oid_that_differs_from_the_tip_is_REFUSED` |
| `base = store.run_base_sha(run_id)` → `base = tip` | ancestor + no-commit tests |

- [ ] **Step 6: Commit**

```bash
git add v2/kernel/nomination.py v2/tests/kernel/test_nomination.py
git commit -m "feat(kernel): verify a nominated commit by observing the worktree"
```

---

### Task 2: expose it to the coordinator

**Files:**
- Modify: `v2/kernel/cli.py`
- Test: `v2/tests/kernel/test_nomination.py` (append)

**Interfaces:**
- Produces: `python3 -m kernel.cli verify-nomination --db D --run-id R --worktree W --branch B [--claimed-oid O]`, printing the object id on stdout and exiting 0, or printing the refusal to stderr and exiting `RC_REFUSED`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_cli_prints_the_oid_it_will_publish(repo, capsys):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")
    tip = _git(repo, "rev-parse", "HEAD")

    import pathlib
    from kernel.cli import main
    db = pathlib.Path(str(repo.parent / "k.db"))
    s = Store.open(db); s.create_run(run_id="r", base_repo="o/r", base_sha=base)

    rc = main(["verify-nomination", "--db", str(db), "--run-id", "r",
               "--worktree", str(repo), "--branch", "work"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == tip


def test_the_cli_refuses_with_a_nonzero_code_and_says_why(repo, capsys):
    import pathlib
    from kernel.cli import main
    db = pathlib.Path(str(repo.parent / "k2.db"))
    s = Store.open(db); s.create_run(run_id="r", base_repo="o/r", base_sha="0" * 40)
    _git(repo, "checkout", "-qb", "work")
    (repo / "f").write_text("changed")
    _git(repo, "commit", "-aqm", "work")

    rc = main(["verify-nomination", "--db", str(db), "--run-id", "r",
               "--worktree", str(repo), "--branch", "work"])
    assert rc != 0
    assert "does not descend" in capsys.readouterr().err
```

- [ ] **Step 2: Run and watch them fail** — `invalid choice: 'verify-nomination'`.

- [ ] **Step 3: Implement**

In `v2/kernel/cli.py`, beside the `pending`/`reconcile` parsers:

```python
    v = subs.add_parser("verify-nomination")
    v.add_argument("--db", required=True)
    v.add_argument("--run-id", required=True)
    v.add_argument("--worktree", required=True)
    v.add_argument("--branch", required=True)
    # OPTIONAL, and it may only agree. See the spec's decision 2.
    v.add_argument("--claimed-oid", default=None)
```

dispatch, beside the others:

```python
    if a.mode == "verify-nomination":
        return _do_verify_nomination(a)
```

and:

```python
def _do_verify_nomination(a) -> int:
    from kernel.nomination import NotPublishable, verify_nomination
    store = Store.open(a.db)
    try:
        print(verify_nomination(store, a.run_id, a.worktree, a.branch,
                                a.claimed_oid))
    except NotPublishable as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    return RC_OK
```

- [ ] **Step 4: Run the tests** — expect PASS, and the full suite green.

- [ ] **Step 5: Commit**

```bash
git add v2/kernel/cli.py v2/tests/kernel/test_nomination.py
git commit -m "feat(cli): verify-nomination, so the coordinator can ask"
```

---

### Task 3: the coordinator publishes

**Files:**
- Modify: `batch/lib/kernel-client.sh`
- Modify: `batch/run-queue.sh`
- Test: `v2/tests/execution/test_publish_execution.py` (create)

**Interfaces:**
- Consumes: `verify-nomination` from Task 2.
- Produces: `_kernel_verify_nomination <run_id> <worktree> <branch> [claimed_oid]` echoing the oid or empty; `_kernel_run_base <run_id>` echoing the recorded base or empty; and `publish_cmd <code> <worktree> <branch> [claimed_oid]` reached by `--publish`.
- **Adopts an existing run and never mints one.** See the comment in Step 4.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/execution/test_publish_execution.py
"""`--publish` EXECUTED. Structural tests on this branch have repeatedly
defended defects; this drives the real function with stubs and asserts what it
DID."""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _extract(name):
    lines = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _drive(tmp_path, oid="abc123"):
    log = tmp_path / "log"
    script = f'''
set -uo pipefail
REPO=demo/demo
BIRCHER_NET_TIMEOUT=5
LOG={log}
_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}
_kernel_adopt_run() {{ BIRCHER_RUN_ID=r; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=1; export BIRCHER_GENERATION; }}
_kernel_verify_nomination() {{ _log "VERIFY $*"; printf '%s' "{oid}"; }}
_effect() {{ _log "EFFECT $1 $2 -- ${{*:4}}"; return 0; }}
_net_run() {{ shift; "$@"; }}
_kernel_record_output() {{ printf 'outhash'; }}
_kernel_warn() {{ :; }}

{_extract("publish_cmd")}

publish_cmd probe /tmp/wt-probe probe-branch || true
'''
    f = tmp_path / "pub.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True)
    return log.read_text().splitlines() if log.exists() else []


def test_the_pushed_ref_carries_the_VERIFIED_oid(tmp_path):
    calls = _drive(tmp_path, oid="deadbeef")
    push = next(c for c in calls if c.startswith("EFFECT ref_update"))
    assert "deadbeef:refs/heads/probe-branch" in push, push


def test_nothing_is_pushed_when_verification_refuses(tmp_path):
    log = tmp_path / "log"
    script = f'''
set -uo pipefail
REPO=demo/demo
BIRCHER_NET_TIMEOUT=5
LOG={log}
_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}
_kernel_adopt_run() {{ BIRCHER_RUN_ID=r; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=1; export BIRCHER_GENERATION; }}
_kernel_verify_nomination() {{ printf ''; }}
_effect() {{ _log "EFFECT $1"; return 0; }}
_net_run() {{ shift; "$@"; }}
_kernel_warn() {{ :; }}

{_extract("publish_cmd")}

publish_cmd probe /tmp/wt-probe probe-branch || true
'''
    f = tmp_path / "pub2.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    assert not calls, f"a refused verification still performed effects: {calls}"
```

- [ ] **Step 2: Run and watch it fail** — `StopIteration` from `_extract`, because `publish_cmd` does not exist.

- [ ] **Step 3: Implement the client helper**

In `batch/lib/kernel-client.sh`, beside `_kernel_pending`:

```bash
# _kernel_verify_nomination <run_id> <worktree> <branch> [claimed_oid]
#   -> echoes the object id the kernel will publish, or EMPTY on refusal.
#
# stdout is captured, like `_kernel_pending`: the answer is the point. Empty
# means refused, and the caller must publish nothing -- an empty oid reaching a
# push would be the "no answer read as an answer" shape this project has been
# caught by three times.
_kernel_verify_nomination() {  # <run_id> <worktree> <branch> [claimed_oid]
  local run_id="$1" wt="$2" branch="$3" claimed="${4:-}" out="" extra=()
  [ -n "$claimed" ] && extra=(--claimed-oid "$claimed")
  out=$( PYTHONPATH="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m kernel.cli verify-nomination \
           --db "${BIRCHER_KERNEL_DB:-}" --run-id "$run_id" \
           --worktree "$wt" --branch "$branch" "${extra[@]}" 2>/dev/null
  ) || out=""
  printf '%s' "$out"
}
```

- [ ] **Step 4: Implement the subcommand**

In `batch/run-queue.sh`, beside `recover_pr_cmd`:

```bash
# publish_cmd <code> <worktree> <branch> [claimed_oid] -- publish an
# implementer's nominated commit from the kernel's credential domain.
#
# The implementer cannot do any of this: its egress denies git-receive-pack and
# its API rules are GET-only. This is the other half of the sentence in its own
# prompt.
publish_cmd() {
  local code="${1:?usage: --publish <code> <worktree> <branch> [oid]}"
  local wt="${2:?usage: --publish <code> <worktree> <branch> [oid]}"
  local branch="${3:?usage: --publish <code> <worktree> <branch> [oid]}"
  local claimed="${4:-}"

  # ADOPT ONLY. A run whose base was never recorded cannot have its provenance
  # checked: `git rev-parse HEAD` in the implementer's worktree is the TIP of
  # the work, not the base it started from, so minting here would record
  # base == tip and every nomination would be refused as "no commit".
  #
  # The predecessor branch shipped exactly this shape once already -- adoption
  # seeded a minted run with a synthesized spec and plan so a caller would not
  # meet refusals, and it fabricated the history the merge gate exists to
  # check. Refusing is the correct answer for work the kernel never dispatched.
  _kernel_adopt_run "$code" "$REPO" "" codex implementer >/dev/null
  local recorded; recorded=$(_kernel_run_base "${BIRCHER_RUN_ID:-}")
  if [ -z "$recorded" ]; then
    echo "[batch:publish] $code: no run with a recorded base for this code -- the kernel did not dispatch this work and cannot vouch for where it came from" >&2
    return 1
  fi

  local oid; oid=$(_kernel_verify_nomination "${BIRCHER_RUN_ID:-}" "$wt" "$branch" "$claimed")
  if [ -z "$oid" ]; then
    echo "[batch:publish] $code: the kernel refused the nomination -> publishing nothing" >&2
    return 1
  fi
  echo "[batch:publish] $code: kernel will publish $oid on '$branch'" >&2

  _effect ref_update "publish:$code:$oid" "$BIRCHER_NET_TIMEOUT" \
    git push origin "$oid:refs/heads/$branch" || {
      echo "[batch:publish] $code: push refused or failed" >&2; return 1; }

  _effect pull_request "publish-pr:$code:$oid" "$BIRCHER_NET_TIMEOUT" \
    gh pr create --repo "$REPO" --head "$branch" --base main \
      --title "$code" --body "Published by the Bircher kernel from $oid." \
    || { echo "[batch:publish] $code: PR creation refused or failed" >&2; return 1; }
}
```

and in the argument dispatch, beside `--recover-pr`:

```bash
  "--publish")
    publish_cmd "${2:-}" "${3:-}" "${4:-}" "${5:-}"; exit $?
    ;;
```

- [ ] **Step 5: Run the tests and the self-test**

Run: the full pytest suite, then `bash batch/run-queue.sh --self-test`.
Expected: green. If `#71` fails, re-run — it is timing-flaky on macOS; confirm against `HEAD~1` before blaming the change.

- [ ] **Step 6: Mutate**

| mutation | must red |
|---|---|
| `"$oid:refs/heads/$branch"` → `"HEAD:refs/heads/$branch"` | `test_the_pushed_ref_carries_the_VERIFIED_oid` |
| delete the `[ -z "$oid" ]` guard | `test_nothing_is_pushed_when_verification_refuses` |
| delete the `[ -z "$recorded" ]` guard | `test_a_run_without_a_recorded_base_publishes_nothing` |

- [ ] **Step 7: Commit**

```bash
git add batch/lib/kernel-client.sh batch/run-queue.sh v2/tests/execution/test_publish_execution.py
git commit -m "feat(batch): --publish, the kernel's half of the implementer contract"
```

---

### Task 3a: `_kernel_run_base`

**Files:** Modify `batch/lib/kernel-client.sh`; test in `test_lifecycle_functions.py`.

```bash
# _kernel_run_base <run_id> -> the base sha the kernel recorded, or empty.
# Empty means "no such run", which for publication means the kernel never
# dispatched this work and has nothing to check provenance against.
_kernel_run_base() {  # <run_id>
  local run_id="$1" out=""
  out=$( K_RUN="$run_id" PYTHONPATH="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c 'import os,sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR","v2"))
from kernel.store import Store
try:
    print(Store.open(os.environ["BIRCHER_KERNEL_DB"]).run_base_sha(os.environ["K_RUN"]) or "")
except Exception:
    print("")' 2>/dev/null
  ) || out=""
  printf '%s' "$out"
}
```

Test it against a real store: a recorded run echoes its base; an unknown run
echoes empty. Mutate `or ""` to `or "0"*40` and the unknown-run test must red.

---

### Task 4: prove it against a real session

**Files:**
- Modify: `docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md` (append a C8 Phase 1 section)

This task is operational, on `abedegno/bircher-smoke`. It performs real effects and must not run against `abedegno/muesli`.

- [ ] **Step 1: Deploy and dispatch a v2_implementer session**

Deploy the branch to `/workspaces/bircher-v2`. Dispatch `agents/v2_implementer` against a clone of the smoke repo with a trivial task, in its own worktree and branch.

- [ ] **Step 2: Record what the session could NOT do**

Capture the session's own failure to push. That failure is acceptance criterion 1 and it is evidence, not an error.

- [ ] **Step 3: Publish**

```bash
BIRCHER_EFFECT_MODE=kernel BIRCHER_KERNEL_MODE=enforce \
BIRCHER_KERNEL_DB=/workspaces/bircher-v2/.run/kernel-c8.db \
BIRCHER_REPO=abedegno/bircher-smoke WORKDIR=/workspaces/bircher-smoke \
  bash batch/run-queue.sh --publish c8probe /tmp/wt-c8probe c8probe-branch
```

- [ ] **Step 4: Check the journal**

Assert `effect_intended`/`effect_confirmed` for `ref_update` and `pull_request`, both naming the verified oid. Read the shadow report and **state its contents whatever they are** — under enforce an empty report is worth nothing on its own; the positive evidence is that every command reached `command_accepted`.

- [ ] **Step 5: Check the two refusals against the live kernel**

Re-run `--publish` against a branch built on a stale base, and against a correct branch with a wrong `claimed_oid`. Both must publish nothing and name both values.

- [ ] **Step 6: Write the record and commit**

State each of the spec's five Phase 1 criteria and whether it held. A criterion that could not fail is recorded as untested, not as passed.

---

## Done means

`verify_nomination` refuses a stale base, a merge in the lineage, a disagreeing claim, and an empty branch, each with both values named — and each refusal is mutation-proved. `--publish` pushes only the verified object id and performs nothing at all when verification refuses. A real `v2_implementer` session on the throwaway repo produces a commit it cannot publish, and the kernel publishes it, with `ref_update` and `pull_request` journalled against the object id the kernel observed. `run_item`, `parse_marker` and the marker branch are byte-identical to their state at the start of this plan.

**Not delivered, and stated so nobody infers otherwise:** the marker is still how `run_item` learns outcomes. Phase 2 retires it. Until then the v1 path is unchanged and C8's boundary applies only to work published through `--publish`.
