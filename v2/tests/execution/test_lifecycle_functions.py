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
`_kernel_submit_spec`, `_kernel_submit_plan`, `_kernel_start_implementation`,
`_kernel_record_output`, `_kernel_record_ci`, `_kernel_record_review`,
`_kernel_request_merge`, `_kernel_record_outcome`) is exercised against a
real temporary SQLite database, and the facts it leaves behind are read back
and asserted on.

Real defects surfaced writing these tests, none visible to a source-text
assertion:

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

3. (Fix round 1, IMPORTANT (b)) The original wiring never called
   `submit_spec`/`submit_plan`/`start_implementation`, so a run never left
   `queued` and every downstream command was refused for the identical
   reason ("not legal from state 'queued'") regardless of what it was --
   proven here by actually replaying the sequence against a live database
   (`test_every_stage_actually_reaches_the_kernel`, before this fix: five
   NotAuthorized rejections, all state-illegal, none of them informative).
   `_kernel_submit_spec`/`_kernel_submit_plan` PUT the queue item's prompt
   as the spec artifact and reuse it as a stand-in plan artifact (v1 has no
   separate plan document); `_kernel_start_implementation` follows, under
   the same implementer generation. All three, in order, now precede
   `_kernel_record_output`.

With the run correctly advanced to `implementing`, empirical replay
(verified directly against a live database, not reasoned about) shows:
`submit_spec`, `submit_plan`, `start_implementation`,
`record_implementation_output` and `record_ci_observation` are all
ACCEPTED. `record_review`, `request_merge` and `record_merge_outcome` remain
accepted all the way to the merge gate. That was not always true, and the
history is the point:

  - `record_review` used to be refused -- "verdict 'codex:pass' is not one
    of ['accept', 'reject', 'request_revision']". The marker's `review=`
    field is `<vendor>:<verdict>`, a vocabulary the kernel does not share.
    An earlier version of this docstring recorded that as deferred ("NOT
    fixed here... a separate, larger decision"), and it stayed deferred
    until a real end-to-end run produced it live.
  - `request_merge` / `record_merge_outcome` were then refused as
    state-illegal, because the run never left `implementing`. One unmapped
    word, three refusals, and the whole merge-authorisation chain down.

Both are fixed. `_kernel_verdict` translates the marker's vocabulary
(`:pass` -> accept, `:fail` -> request_revision, `na` -> not recorded at
all, because a run nobody reviewed must not carry a verdict), and
`_kernel_record_review` / `_kernel_request_merge` now carry the verdict
BINDING the kernel requires -- artifact, base, context bundle, policy
version. Translating the word alone would have moved the refusal to
"malformed verdict binding: 'policy_version'" and read like progress.

What remains refused is `record_merge_outcome`, and it is not a gap: this
driver performs no merge effect, and a merged outcome demands a confirmed
one. See test_the_one_remaining_refusal_is_the_evidence_check_not_a_gap.

"refused for an informative, state-correct reason" and "never even reaches
the kernel" are different failure shapes, and this file
tells them apart: every stage's COMMAND_REQUESTED fact must land even when
its COMMAND_ACCEPTED does not.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "batch" / "lib" / "kernel-client.sh"
V2_DIR = REPO_ROOT / "v2"

sys.path.insert(0, str(V2_DIR))

#: The cwd every bash-level subprocess runs in, and the reason these tests can
#: see a missing PYTHONPATH at all.
#:
#: `python3 -m kernel.cli` puts the CHILD'S CWD on sys.path. `subprocess.run`
#: with no `cwd=` inherits pytest's, so under the project's own documented
#: command -- `cd v2 && python -m pytest tests`, which the plan prescribes in
#: ten places -- `kernel` is a subdirectory of that cwd and imports with no
#: PYTHONPATH at all. Every mutation of the PYTHONPATH guards then SURVIVES:
#: dropping the prefix from both sites of effect-adapter.sh gave 519 passed
#: from v2/ and 2 failed from the repo root. The guard was invisible to the
#: tests written to bind it, and which of the two results you got depended
#: only on where you happened to be standing.
#:
#: REPO_ROOT has no `kernel` package, so a child started here can import it
#: only if something put it on the path deliberately -- which is the property
#: under test. Asserted rather than assumed, because the day someone adds
#: repo-root/kernel/ every one of these tests goes quietly blind again.
_NEUTRAL_CWD = str(REPO_ROOT)
assert not (REPO_ROOT / "kernel").exists(), (
    "REPO_ROOT now contains a `kernel` package, so it is no longer a neutral "
    "cwd: bash-level tests would import it from cwd and stop binding the "
    "PYTHONPATH guards")



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
                          capture_output=True, text=True, env=e,
                          cwd=_NEUTRAL_CWD)


def _db_env(db):
    return {"BIRCHER_KERNEL_DB": str(db)}


def _sleepy_python(tmp_path):
    """A stand-in for a hung kernel process. See test_kernel_client.py's copy
    of this helper for why it's `exec`'d rather than run as a child."""
    p = tmp_path / "sleepy-python"
    p.write_text("#!/bin/sh\nexec sleep 30\n")
    p.chmod(0o755)
    return p


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


def test_a_hung_python_does_not_block_run_start(tmp_path):
    """Task 3 built this hardness for `_kernel`/`_kernel_dispatch`;
    `_kernel_run_start` and `_kernel_put_artifact` bypass `_kernel` and call
    `_net_run` directly (fix round 1, IMPORTANT (c)), so they need the same
    proof: a hung interpreter must not block the caller."""
    sleepy = _sleepy_python(tmp_path)
    started = time.monotonic()
    r = _run('_kernel_run_start r abedegno/muesli ' + BASE_SHA + '\necho SURVIVED',
             env={"BIRCHER_PY": str(sleepy), "BIRCHER_KERNEL_TIMEOUT": "2"})
    elapsed = time.monotonic() - started
    assert "SURVIVED" in r.stdout, r.stdout
    assert elapsed < 15, f"took {elapsed:.1f}s -- the bound did not fire"
    assert "[batch:kernel]" in r.stderr, r.stderr


def test_a_missing_interpreter_does_not_fail_run_start():
    r = _run('_kernel_run_start r abedegno/muesli ' + BASE_SHA + '; echo "rc=$?"',
             env={"BIRCHER_PY": "/nonexistent/python"})
    assert "rc=0" in r.stdout, r.stderr


def test_a_hung_python_does_not_block_put_artifact(tmp_path):
    sleepy = _sleepy_python(tmp_path)
    started = time.monotonic()
    r = _run("h=$(_kernel_put_artifact 'hello'); echo \"[$h]\"\necho SURVIVED",
             env={"BIRCHER_PY": str(sleepy), "BIRCHER_KERNEL_TIMEOUT": "2"})
    elapsed = time.monotonic() - started
    assert "SURVIVED" in r.stdout, r.stdout
    assert "[]" in r.stdout, r.stdout
    assert elapsed < 15, f"took {elapsed:.1f}s -- the bound did not fire"


def test_a_missing_interpreter_does_not_fail_put_artifact():
    r = _run("h=$(_kernel_put_artifact 'hello'); echo \"[$h]\"; echo \"rc=$?\"",
             env={"BIRCHER_PY": "/nonexistent/python"})
    assert "[]" in r.stdout, r.stdout
    assert "rc=0" in r.stdout, r.stdout


# --- _kernel_submit_spec / _kernel_submit_plan / _kernel_start_implementation

def test_submit_spec_and_plan_reuse_the_same_put_artifact(tmp_path):
    """Fix round 1, IMPORTANT (b): submit_spec and submit_plan both take an
    already-PUT hash as an argument (the same PUT-before-reference contract
    record_implementation_output has), and run_item PUTs the prompt ONCE and
    passes that one hash to both -- proven here by putting it once and
    feeding the same hash to both functions, then checking the artifact
    really is held and both commands were requested."""
    db = tmp_path / "kernel.db"
    run_id = "run-spec"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))
    r = _run('g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen = r.stdout.strip().strip("[]")
    assert gen == "1", (r.stdout, r.stderr)

    prompt = "do the thing"
    r = _run(
        f"h=$(_kernel_put_artifact {prompt!r}); "
        f'_kernel_submit_spec {run_id} {gen} "$h"; '
        f'_kernel_submit_plan {run_id} {gen} "$h"',
        env=_db_env(db),
    )
    assert r.returncode == 0, r.stderr

    store = Store.open(db)
    expected_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert store.has_artifact(expected_hash)
    facts = store.facts_for(run_id)
    accepted = {
        f.payload["command_name"] for f in facts if f.kind == EventKind.COMMAND_ACCEPTED
    }
    assert {"submit_spec", "submit_plan"} <= accepted, accepted
    assert store.run_state(run_id) == "planned"


def test_start_implementation_needs_the_implementer_role(tmp_path):
    """authorize() refuses start_implementation from any generation not
    dispatched as implementer (kernel/authz.py). Dispatching as REVIEWER and
    driving submit_spec/submit_plan/start_implementation on that generation
    must be refused, not silently accepted."""
    db = tmp_path / "kernel.db"
    run_id = "run-wrong-role"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))
    r = _run('g=$(_kernel_dispatch codex reviewer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen = r.stdout.strip().strip("[]")
    assert gen == "1", (r.stdout, r.stderr)

    _run(f'h=$(_kernel_put_artifact "x"); '
         f'_kernel_submit_spec {run_id} {gen} "$h"; '
         f'_kernel_submit_plan {run_id} {gen} "$h"; '
         f'_kernel_start_implementation {run_id} {gen}',
         env=_db_env(db))

    store = Store.open(db)
    assert store.run_state(run_id) == "planned", (
        "start_implementation from a reviewer-dispatched generation must be "
        "refused, leaving the run in 'planned'"
    )
    facts = store.facts_for(run_id)
    rejected = {
        (f.payload["command_name"], f.payload["reason"])
        for f in facts if f.kind == EventKind.COMMAND_REJECTED
    }
    assert ("start_implementation", "NotAuthorized") in rejected, rejected


# --- the full lifecycle, driven for real ------------------------------------

def _drive_full_lifecycle(db, run_id, prompt="do the thing"):
    """Drives the exact sequence `run_item` drives, end to end, entirely
    through the named shell functions: run start, implementer dispatch,
    submit_spec, submit_plan, start_implementation, output, CI, reviewer
    dispatch, review, implementer redispatch, merge request, outcome.
    Returns the three generations observed on stdout."""
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))

    r = _run('g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
             env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen1 = r.stdout.strip().strip("[]")
    assert gen1 == "1", (r.stdout, r.stderr)

    r = _run(f'h=$(_kernel_put_artifact {prompt!r}); '
             f'_kernel_submit_spec {run_id} {gen1} "$h"; '
             f'_kernel_submit_plan {run_id} {gen1} "$h"; printf %s "$h"',
             env=_db_env(db))
    spec_hash = r.stdout.strip()
    _run(f"_kernel_start_implementation {run_id} {gen1}", env=_db_env(db))

    body = "bircher-status: outcome=ready ci=success head=" + HEAD_SHA
    r = _run(f"_kernel_record_output {run_id} {gen1} {body!r}", env=_db_env(db))
    out_hash = r.stdout.strip()
    # `green`, not `success`: the PRODUCTION vocabulary. run-queue.sh reads
    # `ci=green` out of the marker and passes it straight here, so a driver
    # that said `success` was testing a value production never sends -- and
    # the mapping it depends on could not have been wrong in a way this
    # noticed.
    _run(f"_kernel_record_ci {run_id} {gen1} green {HEAD_SHA}", env=_db_env(db))

    r = _run('g=$(_kernel_dispatch codex reviewer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen2 = r.stdout.strip().strip("[]")
    assert gen2 == "2", (r.stdout, r.stderr)

    _run(f"_kernel_record_review {run_id} {gen2} codex:pass "
         f"{out_hash} {BASE_SHA} {spec_hash}", env=_db_env(db))

    r = _run('g=$(_kernel_dispatch claude_code implementer); echo "[$g]"',
              env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen3 = r.stdout.strip().strip("[]")
    assert gen3 == "3", (r.stdout, r.stderr)

    _run(f"_kernel_request_merge {run_id} {gen3} 42 abedegno/muesli {HEAD_SHA} "
         f"{out_hash} {BASE_SHA} {spec_hash}", env=_db_env(db))
    _run(f"_kernel_record_outcome {run_id} {gen3} merged", env=_db_env(db))
    return gen1, gen2, gen3


def test_every_stage_actually_reaches_the_kernel(tmp_path):
    """Drives the full lifecycle against one real database, then reopens it
    and asserts a COMMAND_REQUESTED fact landed for every command-shaped
    stage -- what a refusal and a silent no-op both LOOK like from
    run_item's side (an advisory call that returns 0) is a REQUEST that
    landed either way, so this is the assertion a wiring failure could not
    fake."""
    db = tmp_path / "kernel.db"
    run_id = "run-full"
    _drive_full_lifecycle(db, run_id)

    store = Store.open(db)
    facts = store.facts_for(run_id)
    requested_names = {
        f.payload.get("name") for f in facts if f.kind == EventKind.COMMAND_REQUESTED
    }
    assert requested_names == {
        "submit_spec", "submit_plan", "start_implementation",
        "record_implementation_output", "record_ci_observation",
        "record_review", "request_merge", "record_merge_outcome",
    }, requested_names

    dispatched = [f for f in facts if f.kind == EventKind.ATTEMPT_DISPATCHED]
    assert [(f.payload["actor"], f.payload["role"]) for f in dispatched] == [
        ("claude_code", "implementer"), ("codex", "reviewer"),
        ("claude_code", "implementer"),
    ], dispatched


def test_the_three_missing_transitions_are_now_accepted(tmp_path):
    """Fix round 1, IMPORTANT (b): before this fix, the run never left
    `queued` and every one of the five commands below was refused for the
    SAME reason ("not legal from state 'queued'") -- verified empirically
    against a live database before writing this fix, not merely reasoned
    about. With submit_spec/submit_plan/start_implementation wired in ahead
    of it, the run reaches `implementing`, and record_implementation_output
    / record_ci_observation -- refused before purely because of the missing
    state, not because of anything wrong with THEM -- are now accepted too."""
    db = tmp_path / "kernel.db"
    run_id = "run-advances"
    _drive_full_lifecycle(db, run_id)

    store = Store.open(db)
    assert store.run_state(run_id) == "merge_requested", (
        "the lifecycle now runs to the merge gate: record_review carries a "
        "translated verdict AND a real binding, so the run reaches 'reviewing' "
        "and request_merge is authorized. It previously stopped at "
        "'implementing' because the marker's `vendor:verdict` shape was not "
        "the kernel's vocabulary."
    )
    facts = store.facts_for(run_id)
    accepted_names = {
        f.payload["command_name"] for f in facts if f.kind == EventKind.COMMAND_ACCEPTED
    }
    assert accepted_names == {
        "submit_spec", "submit_plan", "start_implementation",
        "record_implementation_output", "record_ci_observation",
        "record_review", "request_merge",
    }, accepted_names

    expected_hash = hashlib.sha256(b"do the thing").hexdigest()
    assert store.has_artifact(expected_hash)
    assert store.current_artifact(run_id) == hashlib.sha256(
        ("bircher-status: outcome=ready ci=success head=" + HEAD_SHA).encode()
    ).hexdigest()


def test_the_one_remaining_refusal_is_the_evidence_check_not_a_gap(tmp_path):
    """Three commands used to be refused; one still is, and for a different
    KIND of reason.

    The old three were a cascade from a single defect: the marker's
    `vendor:verdict` shape was not the kernel's vocabulary, so record_review
    was refused, so the run never reached `reviewing`, so request_merge and
    record_merge_outcome were refused for being state-illegal. That was a
    wiring gap wearing three faces -- and a live run produced exactly it.

    What remains is not a gap. This driver never PERFORMS a merge effect, and
    record_merge_outcome with outcome=merged demands a confirmed one, because
    a merge outcome reports what the mechanism observed rather than what an
    actor claims. The refusal is the gate doing its job; production reaches it
    through merge_ready_pr, which performs the merge through `_effect`.
    """
    db = tmp_path / "kernel.db"
    run_id = "run-refusals"
    _drive_full_lifecycle(db, run_id)

    store = Store.open(db)
    rejected = {
        f.payload["command_name"]: f.payload["detail"]
        for f in store.facts_for(run_id) if f.kind == EventKind.COMMAND_REJECTED
    }
    assert set(rejected) == {"record_merge_outcome"}, rejected
    assert "no confirmed merge effect" in rejected["record_merge_outcome"], rejected
    assert "not legal from state" not in rejected["record_merge_outcome"], (
        "a state-illegal refusal here would mean the run never reached the "
        "merge gate -- the cascade is back")


# --- the terminal record, driven for real -------------------------------------

def test_an_escalated_run_actually_reaches_a_terminal_state(tmp_path):
    """The production shape of the defect the first live acceptance run hit.

    That run went queued -> specified -> planned -> implementing and then
    stopped emitting facts: the coordinator escalated, and the kernel had no
    command that could say so. `implementing` is indistinguishable from a run
    still in progress, so the ledger claimed an in-flight run forever.

    Driven through the shell function rather than the Python API, because the
    wiring is what was missing -- the kernel could always be asked, if
    something asked it.
    """
    db = tmp_path / "kernel.db"
    run_id = "run-escalated"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))
    r = _run('g=$(_kernel_dispatch codex implementer); echo "[$g]"',
             env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    gen = r.stdout.strip().strip("[]")
    assert gen == "1", (r.stdout, r.stderr)

    _run(f'h=$(_kernel_put_artifact "do the thing"); '
         f'_kernel_submit_spec {run_id} {gen} "$h"; '
         f'_kernel_submit_plan {run_id} {gen} "$h"', env=_db_env(db))
    _run(f"_kernel_start_implementation {run_id} {gen}", env=_db_env(db))

    store = Store.open(db)
    assert store.run_state(run_id) == "implementing", "precondition"

    r = _run(f"_kernel_record_run_outcome {run_id} {gen} escalated",
             env=_db_env(db))
    assert r.stderr == "", r.stderr  # a real, accepted call warns nothing

    reopened = Store.open(db)
    assert reopened.run_state(run_id) == "ended", (
        "the run never reached a terminal state, so the ledger still reads as "
        "in-flight -- the exact defect this command exists to close")
    accepted = [f for f in reopened.facts_for(run_id)
                if (f.payload or {}).get("command_name") == "record_run_outcome"]
    assert accepted, "no record_run_outcome fact landed"


def test_a_bogus_outcome_is_refused_rather_than_recorded(tmp_path):
    """The vocabulary is the scorecard's. A run whose ledger accepted any
    string would 'match' the scorecard by construction, typos included."""
    db = tmp_path / "kernel.db"
    run_id = "run-bogus"
    _run(f'_kernel_run_start {run_id} abedegno/muesli {BASE_SHA}', env=_db_env(db))
    _run('_kernel_dispatch codex implementer',
         env={**_db_env(db), "BIRCHER_RUN_ID": run_id})
    _run(f"_kernel_record_run_outcome {run_id} 1 totally-made-up", env=_db_env(db))

    assert Store.open(db).run_state(run_id) != "ended", (
        "an outcome outside the vocabulary ended the run anyway")
