"""A broken kernel must not be able to change a run's outcome.

This is the property that decides whether wiring the kernel into a working
runner was safe. Everything else in record mode is a nice-to-have; this one is
the reason it is safe to deploy at all.

Every negative test below proves the client survives a broken kernel. None of
them proves the client can drive a WORKING one -- a suite built only from
"failure is survivable" assertions is green whether or not the kernel is ever
actually reached, and `test_a_real_call_creates_a_run_and_records_a_fact` is
the one test that would catch a client silently mis-wired to fail every call
(e.g. `python -m kernel.cli` run with no PYTHONPATH, `No module named kernel`
on every invocation).
"""
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "batch" / "lib" / "kernel-client.sh"
V2_DIR = REPO_ROOT / "v2"

sys.path.insert(0, str(V2_DIR))

from kernel.dispatch import dispatch  # noqa: E402
from kernel.events import EventKind  # noqa: E402
from kernel.store import Store  # noqa: E402


def _run(script, env=None):
    e = {"PATH": "/usr/bin:/bin:/usr/local/bin",
         "BIRCHER_KERNEL_DB": "/nonexistent/k.db",
         "BIRCHER_V2_DIR": str(V2_DIR)}
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
