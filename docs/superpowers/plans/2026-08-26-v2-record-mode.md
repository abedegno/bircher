# v2 Supersedes v1 in Record Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v2 branch the runner that actually runs, with the kernel recording each run's lifecycle, without changing what the coordinator does or which agents it launches.

**Architecture:** The kernel observes; the coordinator decides. Bash gains a command CLI and calls it at each stage transition. Authorization and the argv contract are evaluated exactly as they would be under enforcement, but in `shadow` mode a refusal becomes a recorded fact instead of an outcome. Every kernel call from bash is advisory: a broken kernel must not change a run's result.

**Tech Stack:** Python 3.11+ stdlib (`sqlite3`, `argparse`), pytest, bash.

**Spec:** `docs/superpowers/specs/2026-08-26-v2-supersedes-v1-record-mode-design.md`

## Global Constraints

- **Every kernel call from bash is advisory.** No kernel exit code may change what the run does. This is the load-bearing safety property and Task 3 tests it directly.
- **`BIRCHER_KERNEL_MODE` defaults to `shadow`** — the opposite of `BIRCHER_EFFECT_MODE`, which defaults to `deny`. One switch covers both command authorization and the argv contract.
- **`RUN_ID` is `<item-code>-<epoch-seconds>`.** Item codes recur across attempts; a colliding run identity merges two runs' facts into one aggregate.
- **The database is `$BUNDLE_DIR/.run/kernel.db`** — outside any worktree, so no session can write it.
- **Keep the v1 agent bundles.** `claude_code` and `codex` unchanged. C8 and the credential boundary are out of scope.
- **Each role change is a new dispatch**, and a dispatch re-fences the generation, so `GEN` is re-read after every one.
- Never add AI attribution to commits.

---

## File Structure

| File | Responsibility |
|---|---|
| `v2/kernel/cli.py` | *(modify)* `effect` and `command` subcommands. |
| `v2/kernel/mode.py` | The `shadow`/`enforce` switch and the shadow-rejection fact. |
| `v2/kernel/commands.py` | *(modify)* honour shadow mode. |
| `v2/kernel/effects.py` | *(modify)* honour shadow mode for the argv contract. |
| `v2/kernel/report.py` | Query shadow rejections — acceptance criterion 3. |
| `batch/lib/kernel-client.sh` | `_kernel` — the advisory bash wrapper. |
| `batch/run-queue.sh` | *(modify)* lifecycle calls inside `run_item`. |
| `v2/tests/kernel/test_mode.py` | Task 2. |
| `v2/tests/execution/test_kernel_client.py` | Task 3, including the broken-kernel test. |
| `v2/tests/execution/test_lifecycle_wiring.py` | Task 4. |
| `v2/tests/kernel/test_report.py` | Task 5. |

---

### Task 1: The command CLI

The kernel's command layer has no caller outside its test suite. This is the interface that changes that.

**Files:**
- Modify: `v2/kernel/cli.py`, `batch/lib/effect-adapter.sh`
- Test: `v2/tests/kernel/test_cli_command.py`

**Interfaces:**
- Produces: `python3 -m kernel.cli command --db D --run-id R --generation G --name N --payload-json J`, and `python3 -m kernel.cli effect ...` (the previous flat form, now under a subcommand).
- Exit codes: `0` accepted, `87` refused, `88` fenced, `2` usage.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_cli_command.py
"""The kernel's command layer gains its first caller outside the tests."""
import json
import pathlib

import pytest

from kernel.artifacts import put_artifact
from kernel.cli import main
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "k.db"
    s = Store.open(str(p), clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return str(p)


def _gen(db, actor, role):
    s = Store.open(db, clock=Clock(start_us=1))
    return dispatch(s, "r", actor=actor, role=role).generation


def test_a_command_is_accepted(db, capsys):
    s = Store.open(db, clock=Clock(start_us=1))
    spec = put_artifact(s, b"# spec")
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "submit_spec",
               "--payload-json", json.dumps({"spec_sha256": spec})])
    assert rc == 0
    assert Store.open(db, clock=Clock(start_us=1)).run_state("r") == "specified"


def test_an_illegal_command_is_refused(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "request_merge", "--payload-json", "{}"])
    assert rc == 87


def test_a_payload_that_is_not_json_is_a_usage_error(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "submit_spec", "--payload-json", "not json"])
    assert rc == 2


def test_an_unknown_command_name_is_a_usage_error(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "no_such_command", "--payload-json", "{}"])
    assert rc == 2


def test_the_idempotency_key_defaults_to_run_name_generation(db):
    """A retry of the same stage must replay, not double-record. Without a
    stable default every retry is a new command."""
    s = Store.open(db, clock=Clock(start_us=1))
    spec = put_artifact(s, b"# spec")
    g = _gen(db, "claude", Role.IMPLEMENTER)
    args = ["command", "--db", db, "--run-id", "r", "--generation", str(g),
            "--name", "submit_spec", "--payload-json", json.dumps({"spec_sha256": spec})]
    assert main(args) == 0
    assert main(args) == 0
    s = Store.open(db, clock=Clock(start_us=1))
    accepted = [f for f in s.facts_for("r")
                if f.kind == "command_accepted"
                and f.payload.get("command_name") == "submit_spec"]
    assert len(accepted) == 1


def test_the_effect_subcommand_still_works(db, tmp_path):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    witness = tmp_path / "ran"
    rc = main(["effect", "--db", db, "--run-id", "r", "--generation", str(g),
               "--class", "comment", "--idempotency-key", "k",
               "--", "gh", "pr", "comment", "1", "--repo", "o/r",
               "--body", "hi"])
    # Refused or failed at execution is fine here; what must not happen is a
    # usage error, which would mean the subcommand split broke the adapter.
    assert rc != 2
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_cli_command.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL — `main` does not accept a subcommand.

- [ ] **Step 3: Restructure the CLI**

Replace `main` in `v2/kernel/cli.py`:

```python
def _add_common(p):
    p.add_argument("--db", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--generation", type=int, required=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-kernel")
    subs = p.add_subparsers(dest="mode", required=True)

    e = subs.add_parser("effect")
    _add_common(e)
    e.add_argument("--class", dest="effect_class", required=True,
                   choices=sorted(EffectClass.ALL))
    e.add_argument("--idempotency-key", required=True)
    e.add_argument("cmd", nargs=argparse.REMAINDER)

    c = subs.add_parser("command")
    _add_common(c)
    c.add_argument("--name", required=True)
    c.add_argument("--payload-json", default="{}")
    c.add_argument("--idempotency-key", default=None)

    a = p.parse_args(argv)
    return _do_effect(a) if a.mode == "effect" else _do_command(a)


def _do_command(a) -> int:
    from kernel.commands import COMMAND_NAMES, Command, StaleVersion, submit

    if a.name not in COMMAND_NAMES:
        print(f"unknown command: {a.name}", file=sys.stderr)
        return RC_USAGE
    try:
        payload = json.loads(a.payload_json)
    except ValueError as exc:
        print(f"payload is not JSON: {exc}", file=sys.stderr)
        return RC_USAGE
    if not isinstance(payload, dict):
        print("payload must be a JSON object", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    # A stable default so a retry of the same stage REPLAYS. Without it every
    # retry is a new command and the same stage records twice.
    key = a.idempotency_key or f"{a.run_id}:{a.name}:{a.generation}"
    try:
        r = submit(store, Command(name=a.name, run_id=a.run_id,
                                  expected_version=store.run_version(a.run_id),
                                  idempotency_key=key, generation=a.generation,
                                  payload=payload))
        print("replayed" if r.replayed else "accepted")
        return RC_OK
    except NotAuthorized as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    except OwnershipLost as exc:
        print(f"fenced: {exc}", file=sys.stderr)
        return RC_FENCED
    except (StaleVersion, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return RC_REFUSED
```

Move the existing effect body into `_do_effect(a)` unchanged, and add `import json` at the top.

- [ ] **Step 4: Update the adapter to the subcommand form**

In `batch/lib/effect-adapter.sh`, change the kernel-mode invocation:

```bash
        "${BIRCHER_PY:-python3}" -m kernel.cli effect
```

(insert `effect` immediately after `kernel.cli`; every other argument is unchanged).

- [ ] **Step 5: Run everything**

Run: `cd v2 && python -m pytest tests -q -p no:randomly -p no:rerunfailures` — expect PASS.
Run: `bash batch/run-queue.sh --self-test` — expect `self-test OK`.

- [ ] **Step 6: Commit, then mutate**

```bash
git add v2 batch/lib/effect-adapter.sh
git commit -m "feat(v2): a command CLI, so the kernel's command layer has a caller"
```

Mutation: remove the idempotency-key default (`key = a.idempotency_key`), so a retry becomes a new command. `test_the_idempotency_key_defaults_to_run_name_generation` must go red and nothing else.

---

### Task 2: The shadow/enforce switch

**Files:**
- Create: `v2/kernel/mode.py`, `v2/tests/kernel/test_mode.py`
- Modify: `v2/kernel/commands.py`, `v2/kernel/effects.py`, `v2/kernel/events.py`

**Interfaces:**
- Produces: `kernel_mode() -> str`, `SHADOW`, `ENFORCE`, `shadow_or_raise(store, run_id, exc, context) -> None`.
- Consumes: `EventKind.SHADOW_REJECTED`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_mode.py
"""Shadow mode records what enforcement would have refused.

Turning enforcement on because the tests pass would be a claim outrunning its
evidence. Shadow produces the evidence: run real traffic, then read what would
have been refused and why.
"""
import pytest

from kernel.artifacts import put_artifact
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.mode import ENFORCE, SHADOW
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_the_default_is_shadow(monkeypatch):
    monkeypatch.delenv("BIRCHER_KERNEL_MODE", raising=False)
    from kernel.mode import kernel_mode
    assert kernel_mode() == SHADOW


def test_an_unknown_mode_is_refused(monkeypatch):
    """A typo must not silently mean shadow -- that is the direction that
    disables every guard without saying so."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", "yolo")
    from kernel.mode import kernel_mode
    with pytest.raises(ValueError, match="BIRCHER_KERNEL_MODE"):
        kernel_mode()


def test_shadow_accepts_a_command_enforcement_would_refuse(store, monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    r = submit(store, Command(name="request_merge", run_id="r",
                              expected_version=store.run_version("r"),
                              idempotency_key="k", generation=g, payload={}))
    assert r.accepted


def test_shadow_records_what_would_have_been_refused(store, monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="request_merge", run_id="r",
                          expected_version=store.run_version("r"),
                          idempotency_key="k", generation=g, payload={}))
    shadow = [f for f in store.facts_for("r") if f.kind == "shadow_rejected"]
    assert len(shadow) == 1
    assert shadow[0].payload["command_name"] == "request_merge"
    assert "queued" in shadow[0].payload["reason"]


def test_enforce_still_refuses(store, monkeypatch):
    """The control. If shadow were the only behaviour, every test above would
    pass with the guards deleted."""
    from kernel.authz import NotAuthorized
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", ENFORCE)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        submit(store, Command(name="request_merge", run_id="r",
                              expected_version=store.run_version("r"),
                              idempotency_key="k", generation=g, payload={}))


def test_shadow_covers_the_argv_contract_too(store, monkeypatch):
    """One switch, not two: commands shadowed while effects enforce is a state
    nobody reasoned about."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    ran = []
    perform(store, "r", g, EffectClass.COMMENT, "k",
            {"argv": ["git", "push", "origin", ":main"]},
            lambda c, i, kk: ran.append(i) or "done")
    assert ran, "shadow did not let the effect through"
    assert [f for f in store.facts_for("r") if f.kind == "shadow_rejected"]


def test_enforce_still_refuses_a_contract_violation(store, monkeypatch):
    from kernel.authz import NotAuthorized
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", ENFORCE)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        perform(store, "r", g, EffectClass.COMMENT, "k",
                {"argv": ["git", "push", "origin", ":main"]},
                lambda *a: "done")


def test_a_shadow_rejection_is_recorded_before_the_command_proceeds(store, monkeypatch):
    """Order matters: a crash mid-command must leave the refusal recorded, or
    the evidence is lost in exactly the runs worth studying."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="request_merge", run_id="r",
                          expected_version=store.run_version("r"),
                          idempotency_key="k", generation=g, payload={}))
    kinds = [f.kind for f in store.facts_for("r")]
    assert kinds.index("shadow_rejected") < kinds.index("command_accepted")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_mode.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL — `No module named 'kernel.mode'`.

- [ ] **Step 3: Add the event kind**

In `v2/kernel/events.py`, add `SHADOW_REJECTED = "shadow_rejected"` to `EventKind` and `EventKind.SHADOW_REJECTED: 1` to `SCHEMA_VERSIONS`.

- [ ] **Step 4: Write the module**

```python
# v2/kernel/mode.py
"""Shadow or enforce, for both command authorization and the argv contract.

Enforcement turned on because a test suite passes is a claim outrunning its
evidence. Shadow produces evidence: every guard is evaluated exactly as it
would be, and a refusal becomes a fact instead of an outcome.

The default is `shadow`, which is deliberately the OPPOSITE of
BIRCHER_EFFECT_MODE's `deny`. That switch answers "may this mutation happen at
all", where failing closed is right. This one answers "is the kernel's model of
the run correct yet", where failing closed stops a working runner over a
modelling bug.
"""

from __future__ import annotations

import os

from kernel.events import EventKind

SHADOW = "shadow"
ENFORCE = "enforce"
_MODES = (SHADOW, ENFORCE)


def kernel_mode() -> str:
    """The configured mode. An unrecognised value raises rather than
    defaulting: a typo that silently meant `shadow` would disable every guard
    without saying so."""
    mode = os.environ.get("BIRCHER_KERNEL_MODE", SHADOW)
    if mode not in _MODES:
        raise ValueError(
            f"BIRCHER_KERNEL_MODE={mode!r} is not one of {list(_MODES)}"
        )
    return mode


def shadow_or_raise(store, run_id: str, exc: Exception, **context) -> None:
    """In enforce, re-raise. In shadow, record and return.

    The fact is appended BEFORE the caller proceeds, so a crash mid-command
    still leaves the refusal recorded -- the runs worth studying are exactly
    the ones that go wrong.
    """
    if kernel_mode() == ENFORCE:
        raise exc
    store.append_fact(
        run_id=run_id, kind=EventKind.SHADOW_REJECTED, actor="kernel",
        causal_command_id=None,
        payload={"error": type(exc).__name__, "reason": str(exc)[:400], **context},
    )
```

- [ ] **Step 5: Honour it in `commands.py`**

In `submit()`, both `authorize` and `validate_review` currently do:

```python
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc), actor)
        raise
```

Change each to:

```python
    except Exception as exc:
        _record_rejection(store, cmd, type(exc).__name__, str(exc), actor)
        shadow_or_raise(store, cmd.run_id, exc, command_name=cmd.name)
        next_state = None          # in the authorize block
```

and in the `validate_review` block, `review_binding = None`. Add `from kernel.mode import shadow_or_raise` at the top.

- [ ] **Step 6: Honour it in `effects.py`**

In `_perform_unhalted`, the contract check becomes:

```python
        try:
            check(effect_class, argv)
        except ContractViolation as exc:
            shadow_or_raise(store, run_id, NotAuthorized(str(exc)),
                            effect_class=effect_class, argv=argv[:6])
```

Leave the merge-target and empty-argv checks enforcing in both modes: they guard the one class that has semantic authorization, and shadowing them would mean shadow mode published an unauthorized merge.

- [ ] **Step 7: Run everything, then commit**

Run: `cd v2 && python -m pytest tests -q -p no:randomly -p no:rerunfailures` — expect PASS.

```bash
git add v2
git commit -m "feat(v2): shadow mode, so enforcement can be argued from evidence"
```

- [ ] **Step 8: Mutate**

Three, each in isolation against a committed tree:
1. Default to `ENFORCE` — `test_the_default_is_shadow` reds.
2. Make an unknown mode fall back to shadow — `test_an_unknown_mode_is_refused` reds.
3. Append the shadow fact *after* proceeding — `test_a_shadow_rejection_is_recorded_before_the_command_proceeds` reds.

---

### Task 3: The advisory bash client

The load-bearing safety property. A broken kernel must not change a run's outcome.

**Files:**
- Create: `batch/lib/kernel-client.sh`, `v2/tests/execution/test_kernel_client.py`
- Modify: `batch/run-queue.sh` (source the new file)

**Interfaces:**
- Produces: `_kernel <subcommand> <args...>` and `_kernel_dispatch <actor> <role>` (echoes the new generation, empty on failure).

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/execution/test_kernel_client.py
"""A broken kernel must not be able to change a run's outcome.

This is the property that decides whether wiring the kernel into a working
runner was safe. Everything else in record mode is a nice-to-have; this one is
the reason it is safe to deploy at all.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "batch" / "lib" / "kernel-client.sh"


def _run(script, env=None):
    e = {"PATH": "/usr/bin:/bin:/usr/local/bin",
         "BIRCHER_KERNEL_DB": "/nonexistent/k.db",
         "BIRCHER_V2_DIR": str(REPO_ROOT / "v2")}
    e.update(env or {})
    return subprocess.run(["bash", "-c", f'. "{CLIENT}"\n{script}'],
                          capture_output=True, text=True, env=e)


def test_a_kernel_call_against_a_missing_database_succeeds_anyway():
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr


def test_a_kernel_call_with_no_python_succeeds_anyway():
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec; echo "rc=$?"',
             env={"BIRCHER_PY": "/nonexistent/python"})
    assert "rc=0" in r.stdout, r.stderr


def test_a_kernel_call_never_writes_to_stdout():
    """Call sites capture stdout. A kernel diagnostic leaking into it would
    corrupt whatever the caller was reading."""
    r = _run('out=$(_kernel command --run-id r --generation 1 --name submit_spec); echo "[$out]"')
    assert "[]" in r.stdout, r.stdout


def test_a_failure_warns_on_stderr():
    """Advisory is not silent: an operator must be able to see the recorder is
    down without the run changing."""
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec')
    assert "kernel" in r.stderr.lower()


def test_dispatch_echoes_empty_on_failure():
    r = _run('g=$(_kernel_dispatch claude implementer); echo "[$g]"')
    assert "[]" in r.stdout


def test_set_e_does_not_kill_the_caller():
    """run-queue.sh does not use `set -e`, but a future caller might, and an
    advisory call that aborts under it is not advisory."""
    r = _run('set -e\n_kernel command --run-id r --generation 1 --name submit_spec\necho SURVIVED')
    assert "SURVIVED" in r.stdout
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/execution/test_kernel_client.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL — no such file `batch/lib/kernel-client.sh`.

- [ ] **Step 3: Write the client**

```bash
# batch/lib/kernel-client.sh
# The coordinator's interface to the v2 kernel.
#
# EVERY CALL HERE IS ADVISORY. A non-zero exit, a missing database, a Python
# traceback, an absent interpreter -- none of it may change what the run does.
# The kernel records; the coordinator decides. If that is ever not true, this
# file is where it broke.
#
# stdout is reserved for values the caller consumes (`_kernel_dispatch` echoes
# a generation). Diagnostics go to stderr, because call sites capture stdout
# and a stray line would corrupt whatever they were reading.

_kernel_warn() { echo "[batch:kernel] $*" >&2; }

# _kernel <subcommand> <args...>  -- always returns 0
_kernel() {
  local sub="$1"; shift
  ( "${BIRCHER_PY:-python3}" -m kernel.cli "$sub" \
      --db "${BIRCHER_KERNEL_DB:-}" "$@" >/dev/null 2>&1 ) \
    || _kernel_warn "call failed (advisory): $sub $*"
  return 0
}

# _kernel_dispatch <actor> <role> -- echoes the new generation, or nothing.
#
# A role change is a NEW dispatch and a dispatch re-fences the generation, so
# every caller re-reads it. Reusing a stale generation would fence the run out
# of its own kernel record.
_kernel_dispatch() {  # <actor> <role>
  local actor="$1" role="$2" gen=""
  gen=$( K_ACTOR="$actor" K_ROLE="$role" "${BIRCHER_PY:-python3}" - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.dispatch import dispatch
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(dispatch(s, os.environ["BIRCHER_RUN_ID"],
               actor=os.environ["K_ACTOR"], role=os.environ["K_ROLE"]).generation)
PY
  ) || gen=""
  [ -n "$gen" ] || _kernel_warn "dispatch failed (advisory): $actor/$role"
  printf '%s' "$gen"
  return 0
}
```

`_kernel_dispatch` exports `K_ACTOR`/`K_ROLE` for the heredoc itself, so callers pass them as ordinary arguments and never set them by hand. `BIRCHER_RUN_ID` and `BIRCHER_KERNEL_DB` come from the run's environment, exported in Task 4 Step 3.

- [ ] **Step 4: Source it from the coordinator**

In `batch/run-queue.sh`, immediately after the existing `. "$BUNDLE_DIR/batch/lib/effect-adapter.sh"` line:

```bash
# shellcheck source=lib/kernel-client.sh
. "$BUNDLE_DIR/batch/lib/kernel-client.sh"
```

- [ ] **Step 5: Run, then commit**

Run: `cd v2 && python -m pytest tests -q -p no:randomly -p no:rerunfailures` — expect PASS.
Run: `bash batch/run-queue.sh --self-test` — expect `self-test OK`.

```bash
git add batch/lib/kernel-client.sh batch/run-queue.sh v2/tests/execution/test_kernel_client.py
git commit -m "feat(v2): the advisory kernel client -- a broken recorder cannot break a run"
```

- [ ] **Step 6: Mutate**

Remove the `|| _kernel_warn` so `_kernel` propagates the failure; `test_a_kernel_call_against_a_missing_database_succeeds_anyway` and `test_set_e_does_not_kill_the_caller` must red.

---

### Task 4: Lifecycle wiring

**Files:**
- Modify: `batch/run-queue.sh` (inside `run_item`)
- Create: `v2/tests/execution/test_lifecycle_wiring.py`

**Interfaces:**
- Consumes: `_kernel`, `_kernel_dispatch` (Task 3); the command CLI (Task 1).

- [ ] **Step 1: Write the failing tests**

These assert the wiring exists and is ordered, by reading the source. A behavioural end-to-end test needs a live server and belongs in the acceptance run, not the suite.

```python
# v2/tests/execution/test_lifecycle_wiring.py
"""The kernel is called at each stage transition, in the right order."""
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _run_item():
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("run_item()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    return "\n".join(src[start:end])


def test_run_item_is_found():
    """A parser that finds nothing reports total compliance."""
    assert len(_run_item().splitlines()) > 100


@pytest.mark.parametrize("name", [
    "enqueue", "record_implementation_output", "record_ci_observation",
    "record_review", "request_merge", "record_merge_outcome",
])
def test_each_stage_calls_the_kernel(name):
    assert name in _run_item(), f"run_item never records {name}"


def test_the_reviewer_gets_its_own_dispatch():
    """validate_review refuses a review whose attempt was not dispatched in
    the reviewer role. One dispatch at session creation grants implementer
    only, so without this every run shadow-rejects its review."""
    body = _run_item()
    assert "_kernel_dispatch \"$RECOVERY_REVIEWER\" reviewer" in body


def test_the_merge_request_redispatches_as_implementer():
    """A dispatch re-fences the generation, so after the reviewer dispatch the
    implementer needs a fresh one."""
    body = _run_item()
    review = body.index("record_review")
    merge = body.index("request_merge")
    between = body[review:merge]
    assert "_kernel_dispatch \"$vendor\" implementer" in between


def test_the_run_id_carries_the_attempt_epoch():
    """Item codes recur across attempts. A colliding run id merges two runs'
    facts into one aggregate."""
    assert re.search(r'BIRCHER_RUN_ID="\$\{item\}-\$\(date \+%s\)"', _run_item())


def test_no_kernel_call_is_tested_for_success():
    """Advisory means no branch reads a kernel exit code. `if _kernel ...` or
    `_kernel ... &&` would make the recorder able to change the run."""
    for line in _run_item().splitlines():
        s = line.strip()
        if "_kernel" not in s or s.startswith("#"):
            continue
        assert not re.match(r"(if|while|until)\s+_kernel", s), s
        assert "&&" not in s.split("_kernel")[0][-4:], s
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/execution/test_lifecycle_wiring.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL — no kernel calls in `run_item`.

- [ ] **Step 3: Wire the run's start**

In `run_item`, immediately after the `echo "[batch] === $item ==="` line:

```bash
  # The kernel's record of this run. Item codes recur across attempts, so the
  # epoch makes each attempt its own aggregate.
  BIRCHER_RUN_ID="${item}-$(date +%s)"; export BIRCHER_RUN_ID
  BIRCHER_KERNEL_DB="${BIRCHER_KERNEL_DB:-$BUNDLE_DIR/.run/kernel.db}"
  export BIRCHER_KERNEL_DB
  mkdir -p "$(dirname "$BIRCHER_KERNEL_DB")" 2>/dev/null || true
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation 0 --name enqueue \
    --payload-json "{\"item\":\"$item\"}"
```

- [ ] **Step 4: Wire the implementer dispatch**

Immediately after the line that reports `session $conv_id (agent $AGENT_ID)`:

```bash
  BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
  export BIRCHER_GENERATION
```

- [ ] **Step 5: Wire the marker stages**

After `marker=$(parse_marker "$body")` succeeds and the fields are split, add:

```bash
  _k_out=$(printf '%s' "$body" | shasum -a 256 | cut -c1-64)
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
    --name record_implementation_output --payload-json "{\"artifact_hash\":\"$_k_out\"}"
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
    --name record_ci_observation \
    --payload-json "{\"status\":\"$_ci\",\"head_git_sha\":\"$marker_head\"}"

  # A role change is a NEW dispatch and re-fences the generation.
  BIRCHER_GENERATION=$(_kernel_dispatch "$RECOVERY_REVIEWER" reviewer)
  export BIRCHER_GENERATION
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
    --name record_review --payload-json "{\"verdict\":\"$review\"}"

  BIRCHER_GENERATION=$(_kernel_dispatch "$vendor" implementer)
  export BIRCHER_GENERATION
```

The marker fields are already bound by the existing line in `run_item`:

```bash
IFS='|' read -r outcome _ci ci_first review rounds note marker_head <<EOF
```

so the calls above use `$_ci`, `$marker_head` and `$review` directly — there is no `_k_` prefix to introduce. `$outcome` is the run's outcome, not the merge's; the merge outcome comes from `merge_ready_pr`'s own result in Step 6.

- [ ] **Step 6: Wire merge request and outcome**

Immediately before the `merge_ready_pr` call:

```bash
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
    --name request_merge \
    --payload-json "{\"pr\":\"$pr\",\"repo\":\"$REPO\",\"head_git_sha\":\"$marker_head\"}"
```

and immediately after it returns:

```bash
  _kernel command --run-id "$BIRCHER_RUN_ID" --generation "$BIRCHER_GENERATION" \
    --name record_merge_outcome --payload-json "{\"outcome\":\"$_k_outcome\"}"
```

where `_k_outcome` is `merged` when the merge succeeded and `failed` otherwise.

- [ ] **Step 7: Run, then commit**

Run: `cd v2 && python -m pytest tests -q -p no:randomly -p no:rerunfailures` — expect PASS.
Run: `bash batch/run-queue.sh --self-test` — expect `self-test OK`.

```bash
git add batch/run-queue.sh v2/tests/execution/test_lifecycle_wiring.py
git commit -m "feat(v2): record each run's lifecycle in the kernel"
```

- [ ] **Step 8: Mutate**

1. Delete the reviewer dispatch — `test_the_reviewer_gets_its_own_dispatch` reds.
2. Change `BIRCHER_RUN_ID` to `"${item}"` — `test_the_run_id_carries_the_attempt_epoch` reds.
3. Change one call to `if _kernel command ... ; then` — `test_no_kernel_call_is_tested_for_success` reds.

---

### Task 5: The shadow report

Acceptance criterion 3: shadow rejections must be queryable, with a count and a reason per command name. Without this the evidence exists and nobody can read it, which is the same as not having it.

**Files:**
- Create: `v2/kernel/report.py`, `v2/tests/kernel/test_report.py`

**Interfaces:**
- Produces: `shadow_summary(store) -> list[dict]`, and `python3 -m kernel.report --db D`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/kernel/test_report.py
"""What would enforcement have refused? Evidence you cannot read is not
evidence."""
import pytest

from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.ids import Clock
from kernel.report import shadow_summary
from kernel.store import Store


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", "shadow")
    s = Store.open(":memory:", clock=Clock(start_us=1))
    for r in ("r1", "r2"):
        s.create_run(run_id=r, base_repo="o/r", base_sha="a" * 40)
    return s


def _bad_merge(s, run):
    g = dispatch(s, run, actor="claude", role=Role.IMPLEMENTER).generation
    submit(s, Command(name="request_merge", run_id=run,
                      expected_version=s.run_version(run),
                      idempotency_key=f"k-{run}", generation=g, payload={}))


def test_an_empty_store_summarises_to_nothing(store):
    assert shadow_summary(store) == []


def test_rejections_are_counted_by_command_name(store):
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    rows = shadow_summary(store)
    assert len(rows) == 1
    assert rows[0]["command_name"] == "request_merge"
    assert rows[0]["count"] == 2


def test_each_row_carries_a_reason(store):
    """A count with no reason tells you enforcement would break something and
    not what."""
    _bad_merge(store, "r1")
    assert "queued" in shadow_summary(store)[0]["example_reason"]


def test_rows_are_ordered_by_count_descending(store):
    """The first row is what to fix first."""
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    g = dispatch(store, "r1", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="submit_plan", run_id="r1",
                          expected_version=store.run_version("r1"),
                          idempotency_key="p", generation=g, payload={}))
    rows = shadow_summary(store)
    assert [r["count"] for r in rows] == sorted(
        [r["count"] for r in rows], reverse=True)


def test_the_summary_spans_runs(store):
    _bad_merge(store, "r1")
    _bad_merge(store, "r2")
    assert shadow_summary(store)[0]["runs"] == 2
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd v2 && python -m pytest tests/kernel/test_report.py -q -p no:randomly -p no:rerunfailures`
Expected: FAIL — `No module named 'kernel.report'`.

- [ ] **Step 3: Implement**

```python
# v2/kernel/report.py
"""What would enforcement have refused?

The input to switching commands from shadow to enforce one at a time. A count
tells you enforcement would break something; the reason tells you what, and
whether the fix is the guard or the wiring.
"""

from __future__ import annotations

import argparse
import json

from kernel.events import EventKind
from kernel.store import Store


def shadow_summary(store) -> list[dict]:
    """One row per command name, most frequent first."""
    seen: dict[str, dict] = {}
    for run_id in store.all_run_ids():
        for fact in store.facts_for(run_id):
            if fact.kind != EventKind.SHADOW_REJECTED:
                continue
            name = fact.payload.get("command_name") or fact.payload.get(
                "effect_class") or "(unknown)"
            row = seen.setdefault(name, {"command_name": name, "count": 0,
                                         "runs": set(),
                                         "example_reason": fact.payload.get("reason", "")})
            row["count"] += 1
            row["runs"].add(run_id)
    rows = [{**r, "runs": len(r["runs"])} for r in seen.values()]
    return sorted(rows, key=lambda r: r["count"], reverse=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-kernel-report")
    p.add_argument("--db", required=True)
    a = p.parse_args(argv)
    print(json.dumps(shadow_summary(Store.open(a.db)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the store accessor**

`shadow_summary` needs every run. In `v2/kernel/store.py`:

```python
    def all_run_ids(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT run_id FROM runs ORDER BY created_at_us").fetchall()]
```

- [ ] **Step 5: Run, then commit**

Run: `cd v2 && python -m pytest tests -q -p no:randomly -p no:rerunfailures` — expect PASS.

```bash
git add v2
git commit -m "feat(v2): summarise what enforcement would have refused"
```

- [ ] **Step 6: Mutate**

Sort ascending instead of descending — `test_rows_are_ordered_by_count_descending` reds. Drop `example_reason` — `test_each_row_carries_a_reason` reds.

---

### Task 6: Cutover and the acceptance run

The spec's acceptance criteria 1 and 2 are properties of a real run, not of the
suite. Without a task they get skipped, and "v2 supersedes v1" becomes a claim
nobody checked — which is the failure this whole programme is about.

**Files:**
- Create: `docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md`

- [ ] **Step 1: Deploy the branch to the runner**

The v2 checkout already exists at `/workspaces/bircher-v2` on
`omnigent-runner-bircher`, alongside the live v1 checkout. Update it:

```bash
cd /workspaces/bircher-v2 && git fetch -q origin v2 && git reset -q --hard origin/v2
```

Do NOT touch `/workspaces/bircher`. That is on `main` with the live queue state
and the v1 coordinator runs from it.

- [ ] **Step 2: Run one real queue item through the v2 checkout**

With `BIRCHER_KERNEL_MODE=shadow` (the default) and `BIRCHER_EFFECT_MODE=kernel`.
Pick an item whose outcome you are willing to have land, because the effects
are real — this is the live coordinator, not a simulation.

- [ ] **Step 3: Check criterion 1 — the aggregate matches the scorecard**

```bash
cd /workspaces/bircher-v2/v2 && python3 - <<'PY'
import os, sys
from kernel.projection import project
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
for run_id in s.all_run_ids():
    print(run_id, project(s.facts_for(run_id)).state)
PY
tail -1 /workspaces/bircher-v2/.run/scorecard.jsonl
```

Record both, and whether they agree. **If they disagree, that is the finding** —
write down which stage diverged rather than adjusting the projection to match.

- [ ] **Step 4: Check criterion 2 — every mutation is journalled**

Compare the effect journal against what the run actually did:

```bash
cd /workspaces/bircher-v2/v2 && python3 - <<'PY'
import os
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
for run_id in s.all_run_ids():
    for f in s.facts_for(run_id):
        if f.kind.startswith("effect_"):
            print(run_id, f.kind, f.payload.get("effect_class"), f.actor)
PY
```

Every `gh`/`git` mutation the run performed should appear. The three
`session_control` sites the coordinator does NOT route (session create at
`_http_json POST /v1/sessions`, session stop at `_http_json POST .../events`,
and the dispositioned bundle upload) will be absent — expected, and recorded
in `docs/design/effect-site-inventory.md`.

- [ ] **Step 5: Read the shadow report**

```bash
cd /workspaces/bircher-v2/v2 && python3 -m kernel.report --db "$BIRCHER_KERNEL_DB"
```

This is the deliverable. Every row is a command that enforcement would have
refused. For each: is the guard wrong, or is the wiring wrong? Record the
verdict per row — that list is the input to enabling enforcement.

- [ ] **Step 6: Write the record and commit**

Write `docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md` with
the run id, the two states from Step 3, the journal from Step 4, the shadow
table from Step 5, and a verdict per shadow row. State plainly whether
criteria 1 and 2 held.

**A zero-row shadow report is a result to be suspicious of, not celebrated.**
It means either the wiring is right or the kernel was never called; Step 4's
journal distinguishes them.

---

## Done means

`python3 -m kernel.cli command ...` exists and the kernel's command layer has a caller outside its tests. `BIRCHER_KERNEL_MODE` defaults to `shadow` and covers both authorization and the argv contract, with an unknown value refused rather than defaulted. Every kernel call from bash is advisory, proven by a suite that runs them against a missing database and an absent interpreter and asserts the caller survives. `run_item` records enqueue, dispatch, implementation output, CI, review, merge request and outcome, with a fresh dispatch at each role change. `python3 -m kernel.report --db …` prints what enforcement would have refused, most frequent first. `--self-test` stays green and every guard carries a mutation that reds its named test.

**Not delivered, and stated so nobody infers otherwise:** the credential boundary. This keeps `claude_code` and `codex`, so the implementer still holds a token and still opens its own PR. M1-1 is proven but not in force until `v2_implementer` is swapped in, which is what makes C8 necessary.
