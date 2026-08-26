"""The lifecycle functions `run_item` calls, driven for real against a real
database.

`v2/tests/execution/test_lifecycle_wiring.py` (Task 4's brief) is a purely
static suite: it reads `run_item`'s source and checks it CONTAINS the right
strings in the right order. That proves `run_item` calls these functions. It
proves nothing about what happens when they run -- which is exactly the shape
of the defect Task 3's preflight caught (`_kernel` with no PYTHONPATH failed
`No module named kernel` on every call, and every one of Task 3's
then-existing tests still passed, because they only asserted that a FAILING
call was survivable).

This file is the other half: every function in `batch/lib/kernel-client.sh`
added for Task 4 (`_kernel_run_start`, `_kernel_put_artifact`,
`_kernel_record_output`, `_kernel_record_ci`, `_kernel_record_review`,
`_kernel_request_merge`, `_kernel_record_outcome`) is exercised against a
real temporary SQLite database, and the facts it leaves behind are read back
and asserted on.

Two real defects surfaced writing these tests, neither visible to a
source-text assertion:

1. The plan's `_kernel_run_start` step (`_kernel command --name enqueue
   ...`) does not work: "enqueue" is not in `kernel.commands.COMMAND_NAMES`,
   so the call fails "unknown command: enqueue" (exit 2, not a
   shadow-refusal) and the run's row is never created. Every later command
   for the run then crashes `store.run_version()` on a `None` row -- an
   uncaught TypeError `_kernel` treats exactly like a healthy shadow-refusal.
   The whole lifecycle would record NOTHING. `_kernel_run_start` instead
   calls `store.create_run()` directly, the same way `_kernel_dispatch`
   already calls `dispatch()` directly rather than routing through
   `submit()`. `test_run_start_creates_a_real_run_others_can_build_on` is
   the test that would have caught the original approach: it asserts the run
   actually exists afterward, not merely that the call survived.

2. `record_implementation_output` refuses any artifact_hash the store does
   not already hold (`kernel/authz.py`). The plan computed a hash with
   `shasum` and asserted it without ever PUTting the bytes it hashed, so
   the command was ALWAYS refused for naming an artifact the kernel does not
   hold -- a real property to be checked, not a wiring bug. `_kernel_record_output`
   PUTs first and takes its hash from that PUT, so the two can never disagree.
   `test_record_output_puts_before_it_names_the_hash` is the test that would
   have caught the original approach.

Every stage past artifact PUT and dispatch (`record_review`, `request_merge`,
`record_merge_outcome`) is expected to be permanently shadow-refused given
the payload shapes the plan specifies -- `record_review`'s payload carries
only a verdict, never the full binding (`artifact_hash`/`base_sha`/
`context_bundle_hash`/`policy_version`) `record_review` requires to be
authorized at all, and `request_merge`'s payload never carries an
`artifact_hash` either. That is not a defect this task's brief asks it to
fix (CONTEXT: "Expect most commands in a first real run to be
shadow-refused; that is the point, not a bug") -- but "always refused" and
"never even reaches the kernel" are different failure shapes, and this file
tells them apart: every stage's COMMAND_REQUESTED fact must land even when
its COMMAND_ACCEPTED does not.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "batch" / "lib" / "kernel-client.sh"
V2_DIR = REPO_ROOT / "v2"

sys.path.insert(0, str(V2_DIR))

from kernel.commands import Command, submit  # noqa: E402
from kernel.events import EventKind  # noqa: E402
from kernel.store import Store  # noqa: E402

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40

# Same enforcing `_net_run` stand-in test_kernel_client.py uses: run-queue.sh's
# real one needs a `timeout(1)`/`gtimeout(1)` binary this box may not have,
# and `_net_run` is not part of kernel-client.sh -- it resolves at call time
# from run-queue.sh, so any caller of these functions supplies its own, the
# same way test_fault_injection.py does for effect-adapter.sh.
_NET_RUN_STUB = '''
_net_run() {
  local cap="$1"; shift
  "$@" &
  local pid=$!
  sleep "$cap" &
  local watcher=$!
  while kill -0 "$pid" 2>/dev/null && kill -0 "$watcher" 2>/dev/null; do
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null
  fi
  kill "$watcher" 2>/dev/null
  wait "$pid" 2>/dev/null; local rc=$?
  wait "$watcher" 2>/dev/null
  return "$rc"
}
'''


def _run(script, env=None, net_run=_NET_RUN_STUB):
    e = {"PATH": "/usr/bin:/bin:/usr/local/bin",
         "BIRCHER_V2_DIR": str(V2_DIR),
         "BIRCHER_KERNEL_TIMEOUT": "5"}
    e.update(env or {})
    preamble = net_run or ""
    return subprocess.run(["bash", "-c", f'{preamble}\n. "{CLIENT}"\n{script}'],
                          capture_output=True, text=True, env=e)


def _db_env(db):
    return {"BIRCHER_KERNEL_DB": str(db)}


# --- _kernel_run_start -------------------------------------------------------

def test_run_start_creates_a_real_run_others_can_build_on(tmp_path):
    """The test that would have caught the `--name enqueue` defect: it
    asserts the run actually exists afterward, not merely that the shell
    call returned 0 (every _kernel call returns 0 -- that is the whole
    advisory contract, and it is exactly as true of a call that reached
    nothing as of one that worked)."""
    db = tmp_path / "kernel.db"
    r = _run('_kernel_run_start run-1 abedegno/muesli ' + BASE_SHA,
             env=_db_env(db))
    assert r.returncode == 0

    store = Store.open(db)
    assert store.run_state("run-1") == "queued"
    assert store.run_base_sha("run-1") == BASE_SHA
    kinds = [f.kind for f in store.facts_for("run-1")]
    assert EventKind.RUN_STARTED in kinds, kinds


def test_run_start_is_idempotent_on_a_repeated_run_id(tmp_path):
    """A retried call for the same run_id must not raise past the advisory
    boundary or corrupt the row already there."""
    db = tmp_path / "kernel.db"
    _run('_kernel_run_start run-2 abedegno/muesli ' + BASE_SHA, env=_db_env(db))
    r2 = _run('_kernel_run_start run-2 abedegno/muesli ' + BASE_SHA, env=_db_env(db))
    assert r2.returncode == 0
    store = Store.open(db)
    assert store.run_state("run-2") == "queued"


def test_run_start_against_a_missing_database_succeeds_anyway(tmp_path):
    r = _run('_kernel_run_start run-3 abedegno/muesli ' + BASE_SHA,
             env={"BIRCHER_KERNEL_DB": str(tmp_path / "nonexistent" / "k.db")})
    assert r.returncode == 0


# --- _kernel_put_artifact / _kernel_record_output ---------------------------

def test_record_output_puts_before_it_names_the_hash(tmp_path):
    """The test that would have caught the shasum-without-a-PUT defect: the
    hash landed in the artifacts table, and it is the SAME hash a Python
    caller computes over the same bytes -- not merely "some string"."""
    db = tmp_path / "kernel.db"
    body = "bircher-status: outcome=ready ci=success head=" + HEAD_SHA
    _run('_kernel_run_start run-4 abedegno/muesli ' + BASE_SHA, env=_db_env(db))
    r = _run(
        f'g=$(_kernel_dispatch claude_code implementer); '
        f'_kernel_record_output run-4 "$g" {body!r}',
        env={**_db_env(db), "BIRCHER_RUN_ID": "run-4"},
    )
    assert r.returncode == 0, r.stderr

    store = Store.open(db)
    expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert store.has_artifact(expected_hash), "the PUT never happened"
    kinds = [f.kind for f in store.facts_for("run-4")]
    assert EventKind.COMMAND_REQUESTED in kinds, kinds


def test_put_artifact_echoes_the_real_content_hash(tmp_path):
    db = tmp_path / "kernel.db"
    _run('_kernel_run_start run-5 abedegno/muesli ' + BASE_SHA, env=_db_env(db))
    r = _run("h=$(_kernel_put_artifact 'hello world'); echo \"[$h]\"", env=_db_env(db))
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert f"[{expected}]" in r.stdout, (r.stdout, r.stderr)
    store = Store.open(db)
    assert store.has_artifact(expected)


def test_put_artifact_against_a_missing_database_echoes_nothing(tmp_path):
    r = _run("h=$(_kernel_put_artifact 'hello'); echo \"[$h]\"",
             env={"BIRCHER_KERNEL_DB": str(tmp_path / "nope" / "k.db")})
    assert "[]" in r.stdout, r.stdout


# --- every stage's request lands, even when its acceptance does not --------

def test_every_stage_actually_reaches_the_kernel(tmp_path):
    """Drives the exact sequence `run_item` drives -- run start, implementer
    dispatch, output, CI, reviewer dispatch, review, implementer redispatch,
    merge request, outcome -- against one real database, then reopens it and
    asserts a COMMAND_REQUESTED fact landed for every command-shaped stage.

    Most of these are expected to end up COMMAND_REJECTED: the run never
    leaves `queued` (nothing in this task's wiring calls submit_spec /
    submit_plan / start_implementation -- see
    test_authorized_stages_are_actually_accepted below for the stages that
    it CAN reach), and record_review / request_merge's payload shapes never
    carry a full verdict binding. That is the documented, correct behaviour
    of shadow mode evaluating a real authorization decision -- CONTEXT: "most
    commands in a first real run will be shadow-refused; that is the point,
    not a bug." What this test is actually checking is the thing a refusal
    and a silent no-op both LOOK like from run_item's side (an advisory call
    that returns 0): that a REQUEST was actually recorded for every stage,
    not that every stage succeeded.
    """
    db = tmp_path / "kernel.db"
    run_id = "run-full"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))

    r = _run(f'g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
             env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen1 = r.stdout.strip().strip("[]")
    assert gen1 == "1", (r.stdout, r.stderr)

    body = "bircher-status: outcome=ready ci=success head=" + HEAD_SHA
    _run(f"_kernel_record_output {run_id} {gen1} {body!r}", env=_db_env(db))
    _run(f"_kernel_record_ci {run_id} {gen1} success {HEAD_SHA}", env=_db_env(db))

    r = _run('g=$(_kernel_dispatch codex reviewer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen2 = r.stdout.strip().strip("[]")
    assert gen2 == "2", (r.stdout, r.stderr)

    _run(f"_kernel_record_review {run_id} {gen2} accept", env=_db_env(db))

    r = _run('g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen3 = r.stdout.strip().strip("[]")
    assert gen3 == "3", (r.stdout, r.stderr)

    _run(f"_kernel_request_merge {run_id} {gen3} 42 abedegno/muesli {HEAD_SHA}",
         env=_db_env(db))
    _run(f"_kernel_record_outcome {run_id} {gen3} merged", env=_db_env(db))

    store = Store.open(db)
    facts = store.facts_for(run_id)
    requested_names = {
        f.payload.get("name") for f in facts if f.kind == EventKind.COMMAND_REQUESTED
    }
    assert requested_names == {
        "record_implementation_output", "record_ci_observation",
        "record_review", "request_merge", "record_merge_outcome",
    }, requested_names

    dispatched = [f for f in facts if f.kind == EventKind.ATTEMPT_DISPATCHED]
    assert [(f.payload["actor"], f.payload["role"]) for f in dispatched] == [
        ("claude_code", "implementer"), ("codex", "reviewer"),
        ("claude_code", "implementer"),
    ], dispatched

    # The one stage this wiring CAN authorize (state=queued, role=implementer,
    # a real artifact already PUT) is accepted, not refused.
    accepted_names = {
        f.payload["command_name"] for f in facts if f.kind == EventKind.COMMAND_ACCEPTED
    }
    assert accepted_names == set(), (
        "nothing here calls submit_spec/submit_plan/start_implementation, so "
        f"the run never leaves 'queued' and record_implementation_output "
        f"(legal only from 'implementing') is refused too: {accepted_names}"
    )
    rejected_reasons = {
        (f.payload["command_name"], f.payload["reason"])
        for f in facts if f.kind == EventKind.COMMAND_REJECTED
    }
    for name in ("record_implementation_output", "record_ci_observation"):
        assert (name, "NotAuthorized") in rejected_reasons, rejected_reasons


# --- the stages this wiring CAN authorize really do get accepted -----------

def test_authorized_stages_are_actually_accepted(tmp_path):
    """`test_every_stage_actually_reaches_the_kernel` proves every call
    reaches the kernel even when the state gate refuses it. This proves the
    other half: when a stage IS legally reachable, `_kernel_record_output`
    and `_kernel_record_ci` do not merely reach the kernel, they get
    ACCEPTED -- the payload shape, the PUT-before-reference ordering and the
    generation threading are all actually correct, not just present.

    submit_spec / submit_plan / start_implementation are driven directly
    against the store rather than through the shell: this task's wiring does
    not call them (CONTEXT confirms the plan never wires them), so there is
    no shell function to exercise for them, and driving them through
    kernel.commands.submit() -- the exact function `_kernel command` itself
    calls -- advances the run through its REAL transition, not a shortcut
    around it (no direct store.set_run_state mutation this test would have
    to trust separately).
    """
    db = tmp_path / "kernel.db"
    run_id = "run-happy"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))

    r = _run('g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen = r.stdout.strip().strip("[]")
    assert gen == "1", (r.stdout, r.stderr)

    store = Store.open(db)
    for name in ("submit_spec", "submit_plan", "start_implementation"):
        res = submit(store, Command(
            name=name, run_id=run_id, expected_version=store.run_version(run_id),
            idempotency_key=f"{run_id}:{name}:{gen}", generation=int(gen), payload={},
        ))
        assert res.accepted, (name, res)
    assert store.run_state(run_id) == "implementing"

    body = "bircher-status: outcome=ready ci=success head=" + HEAD_SHA
    r = _run(f"_kernel_record_output {run_id} {gen} {body!r}", env=_db_env(db))
    assert r.returncode == 0, r.stderr
    r = _run(f"_kernel_record_ci {run_id} {gen} success {HEAD_SHA}", env=_db_env(db))
    assert r.returncode == 0, r.stderr

    reopened = Store.open(db)
    expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert reopened.current_artifact(run_id) == expected_hash
    facts = reopened.facts_for(run_id)
    accepted = {
        f.payload["command_name"] for f in facts if f.kind == EventKind.COMMAND_ACCEPTED
    }
    assert "record_implementation_output" in accepted, accepted
    assert "record_ci_observation" in accepted, accepted
