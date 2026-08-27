"""A broken kernel must not be able to change a run's outcome.

This is the property that decides whether wiring the kernel into a working
runner was safe. Everything else in record mode is a nice-to-have; this one is
the reason it is safe to deploy at all.

Every negative test below proves the client survives a broken kernel. None of
them proves the client can drive a WORKING one -- a suite built only from
"failure is survivable" assertions is green whether or not the kernel is ever
actually reached, and `test_a_real_call_creates_a_run_and_records_a_fact` /
`test_a_real_dispatch_creates_an_attempt_dispatched_fact` are the tests that
would catch a client silently mis-wired to fail every call (e.g. `python -m
kernel.cli` run with no PYTHONPATH, `No module named kernel` on every
invocation; or a typo'd env var name in the dispatch call, always echoing an
empty generation).

`_kernel` and `_kernel_dispatch` both route their python invocation through
`_net_run` (`batch/run-queue.sh`'s existing network-call bound, already used
by every other kernel-owned effect) rather than inventing a second bounding
mechanism. Because `_net_run` is not part of kernel-client.sh -- it lives in
run-queue.sh, resolved at call time, exactly like `_effect`'s use of it in
effect-adapter.sh -- every test here supplies its own `_net_run`, the same
way `test_fault_injection.py` does for effect-adapter.sh. Most tests use an
ENFORCING stub (a real fork/kill, not a passthrough) so the timeout tests
below prove an actual bound, not merely that a cap value was threaded
through.
"""
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



from kernel.dispatch import dispatch  # noqa: E402
from kernel.events import EventKind  # noqa: E402
from kernel.store import Store  # noqa: E402


#: A real, ENFORCING `_net_run`, standing in for run-queue.sh's copy (which
#: requires a real `timeout(1)`/`gtimeout(1)` binary -- absent on this box).
#: Backgrounds the wrapped command and a `sleep $cap` watchdog as two DIRECT
#: children (no subshell wrapping either one), polls until either finishes,
#: and kills whichever is still alive.
#:
#: Two non-obvious things this works around, both found by watching this
#: stub hang instead of the property it exists to prove:
#:
#: 1. Cancelling the watchdog EARLY (the command finished before the cap)
#:    must kill `sleep` itself, not a subshell wrapping it. An earlier draft
#:    used `( sleep "$cap"; kill -TERM "$pid" ) &` and killed that subshell
#:    once the real command finished. The subshell died; the `sleep` it had
#:    already forked as ITS OWN child did not -- signals target the exact
#:    PID given, not a process's children -- so the orphaned `sleep` kept
#:    running for the rest of the cap, inherited the same stdout/stderr
#:    pipes, and `subprocess.run(capture_output=True)` blocks until every
#:    holder of those pipes closes them. Every "fast" test took the FULL cap
#:    anyway, silently, because nothing here asserts on wall-clock time
#:    except the two hang tests -- a slow-but-passing suite is exactly the
#:    kind of defect a stopwatch catches and a green run does not.
#: 2. This box's `/bin/bash` is 3.2 (Apple ships no later GPLv3 bash), which
#:    has no `wait -n`, so the two children are raced with a `kill -0` poll
#:    rather than a blocking wait-for-either.
#: 3. The sleepy-python test fixture (`_sleepy_python`, below) hits the same
#:    class of bug from the OTHER side: a `#!/bin/sh` wrapper around `sleep`
#:    is a child the wrapper's own SIGTERM does not reach unless the wrapper
#:    `exec`s it.
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
         "BIRCHER_KERNEL_DB": "/nonexistent/k.db",
         "BIRCHER_V2_DIR": str(V2_DIR),
         # Small by default so no test pays the production 20s default even
         # when its call fails instantly; the two hang tests shrink it
         # further still (2s) to keep well clear of the 30s sleepy-python.
         "BIRCHER_KERNEL_TIMEOUT": "5"}
    e.update(env or {})
    preamble = net_run or ""
    return subprocess.run(["bash", "-c", f'{preamble}\n. "{CLIENT}"\n{script}'],
                          capture_output=True, text=True, env=e,
                          cwd=_NEUTRAL_CWD)


def _sleepy_python(tmp_path):
    """A stand-in for a hung kernel process: ignores every argument and
    sleeps far longer than any bound this suite sets.

    `exec`'d, not merely run: without it `sleep` is a CHILD of this script's
    `/bin/sh`, and the enforcing `_net_run` stub's SIGTERM only reaches the
    shell -- the un-execed `sleep` is reparented to init and keeps running
    for its full 30s, holding pytest's captured stdout/stderr pipe open the
    whole time (subprocess.run's capture waits for every writer to close it,
    not just the direct child bash exits). `exec` replaces the shell with
    `sleep` in place, so the same PID this test signals IS the process that
    needs to die.
    """
    p = tmp_path / "sleepy-python"
    p.write_text("#!/bin/sh\nexec sleep 30\n")
    p.chmod(0o755)
    return p


def test_a_kernel_call_against_a_missing_database_succeeds_anyway():
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr


def test_a_kernel_call_with_no_python_succeeds_anyway():
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec; echo "rc=$?"',
             env={"BIRCHER_PY": "/nonexistent/python"})
    assert "rc=0" in r.stdout, r.stderr


def test_a_kernel_call_never_writes_to_stdout():
    """Call sites capture stdout. A kernel diagnostic leaking into it would
    corrupt whatever the caller was reading.

    Also asserts the `_kernel_warn` marker landed on stderr: without this,
    the test is satisfied by `kernel-client.sh` not existing at all --
    "command not found" writes nothing to stdout either, and a passing test
    that is equally happy with the file absent is the defect this whole
    project exists to catch.
    """
    r = _run('out=$(_kernel command --run-id r --generation 1 --name submit_spec); echo "[$out]"')
    assert "[]" in r.stdout, r.stdout
    assert "[batch:kernel]" in r.stderr, r.stderr


def test_a_failure_warns_on_stderr():
    """Advisory is not silent: an operator must be able to see the recorder is
    down without the run changing.

    Asserts the literal `_kernel_warn` marker, not merely the substring
    "kernel" -- bash's own "_kernel: command not found" also contains
    "kernel", so the looser assertion passed even with the file deleted.
    """
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec')
    assert "[batch:kernel]" in r.stderr, r.stderr


def test_dispatch_echoes_empty_on_failure():
    r = _run('g=$(_kernel_dispatch claude implementer); echo "[$g]"')
    assert "[]" in r.stdout


def test_set_e_does_not_kill_the_caller():
    """run-queue.sh does not use `set -e`, but a future caller might, and an
    advisory call that aborts under it is not advisory."""
    r = _run('set -e\n_kernel command --run-id r --generation 1 --name submit_spec\necho SURVIVED')
    assert "SURVIVED" in r.stdout


def test_a_real_call_creates_a_run_and_records_a_fact(tmp_path):
    """Against a REAL database, `_kernel command` must actually reach the
    kernel: create the run's dispatch out-of-band (as the coordinator will in
    Task 4), then submit_spec through `_kernel` and read the database back.

    Without this test, every test above passes whether `_kernel` reaches
    `kernel.cli` at all or fails on every call -- e.g. no PYTHONPATH set, so
    `-m kernel.cli` cannot import `kernel` from the coordinator's cwd. A
    client that always fails is still fully "advisory" by every test above,
    and would ship a recorder that never records anything.
    """
    db = tmp_path / "kernel.db"
    store = Store.open(db)
    store.create_run(run_id="r-live", base_repo="abedegno/muesli", base_sha="deadbeef")
    d = dispatch(store, "r-live", actor="claude", role="implementer")
    assert d.generation == 1

    r = _run(
        '_kernel command --run-id r-live --generation 1 --name submit_spec '
        '--payload-json "{}"; echo "rc=$?"',
        env={"BIRCHER_KERNEL_DB": str(db)},
    )
    assert "rc=0" in r.stdout, r.stderr
    assert r.stderr == "", r.stderr  # a real, successful call warns nothing

    reopened = Store.open(db)
    kinds = [f.kind for f in reopened.facts_for("r-live")]
    assert EventKind.COMMAND_ACCEPTED in kinds, kinds
    assert reopened.run_state("r-live") == "specified"


def test_a_real_dispatch_creates_an_attempt_dispatched_fact(tmp_path):
    """Against a REAL database with a REAL run, `_kernel_dispatch` must
    actually fence a generation and record who it belongs to.

    `_kernel_dispatch`'s only other coverage is the failure path
    (`test_dispatch_echoes_empty_on_failure`), and Task 4's own tests are
    static source-text assertions on `run_item`'s body -- none of them
    execute `_kernel_dispatch` against a live kernel. A typo'd env var name
    in the python source it runs would pass all of those and echo an empty
    generation on every real call; its stdout feeds `BIRCHER_GENERATION` at
    four Task 4 call sites.
    """
    db = tmp_path / "kernel.db"
    store = Store.open(db)
    store.create_run(run_id="r-disp", base_repo="abedegno/muesli", base_sha="deadbeef")

    r = _run(
        'g=$(_kernel_dispatch claude implementer); echo "[$g]"',
        env={"BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-disp"},
    )
    assert "[1]" in r.stdout, (r.stdout, r.stderr)
    assert r.stderr == "", r.stderr  # a real, successful dispatch warns nothing

    reopened = Store.open(db)
    kinds = [f.kind for f in reopened.facts_for("r-disp")]
    assert EventKind.ATTEMPT_DISPATCHED in kinds, kinds


def test_a_hung_python_does_not_block_a_kernel_call(tmp_path):
    """The reviewer's finding: an unbounded python process turns 'the run
    completes' into 'the run stalls forever at this line', which changes a
    run's outcome more completely than any exit code could. Bounded by
    BIRCHER_KERNEL_TIMEOUT, shrunk here so the test itself stays fast."""
    sleepy = _sleepy_python(tmp_path)
    started = time.monotonic()
    r = _run('_kernel command --run-id r --generation 1 --name submit_spec\necho SURVIVED',
             env={"BIRCHER_PY": str(sleepy), "BIRCHER_KERNEL_TIMEOUT": "2"})
    elapsed = time.monotonic() - started
    assert "SURVIVED" in r.stdout, r.stdout
    assert elapsed < 15, f"took {elapsed:.1f}s -- the bound did not fire"
    assert "[batch:kernel]" in r.stderr, r.stderr


def test_a_hung_python_does_not_block_dispatch(tmp_path):
    """Same property, the other call site: `_kernel_dispatch`'s stdout feeds
    BIRCHER_GENERATION directly, so a hang there is exactly as dangerous."""
    sleepy = _sleepy_python(tmp_path)
    started = time.monotonic()
    r = _run('g=$(_kernel_dispatch claude implementer); echo "[$g]"\necho SURVIVED',
             env={"BIRCHER_PY": str(sleepy), "BIRCHER_KERNEL_TIMEOUT": "2"})
    elapsed = time.monotonic() - started
    assert "SURVIVED" in r.stdout, r.stdout
    assert "[]" in r.stdout, r.stdout
    assert elapsed < 15, f"took {elapsed:.1f}s -- the bound did not fire"
