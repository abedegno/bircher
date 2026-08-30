"""A CI list crossing into Python must not be capped by argv.

Linux limits ONE argument to 128KB (MAX_ARG_STRLEN); macOS is far more
permissive. `_normalize_ci` and `_keep_blocking_checks` passed the whole check
list as an argv element, so on Linux a large list failed `execve` with
"Argument list too long" -- and the shell reads a failed call as `pending`,
while `_wait_ci` LOOPS on pending. An oversized input therefore hung rather
than erroring.

Found by bircher's first CI run, minutes after CI existed. The local macOS
suite had passed this test for weeks.

Both commands now take `-` and read the payload from stdin, which has no such
ceiling.
"""
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
V2 = REPO_ROOT / "v2"

#: Comfortably past Linux's 128KB per-argument ceiling.
BIG = "fail\n" + "".join(f"pass-{i:039d}\n" for i in range(6000))


def _cli(args, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "coordinator.cli", *args],
        input=stdin, capture_output=True, text=True, cwd=V2,
        env={"PYTHONPATH": str(V2), "PATH": "/usr/bin:/bin"})


def test_the_fixture_actually_exceeds_the_linux_argument_limit():
    """Otherwise this file proves nothing on the platform that has the limit."""
    assert len(BIG.encode()) > 131072, (
        f"fixture is {len(BIG.encode())} bytes; under MAX_ARG_STRLEN it cannot "
        "demonstrate the failure it exists for")


def test_ci_normalize_reads_a_large_payload_from_stdin():
    r = _cli(["ci-normalize", "--buckets", "-"], BIG)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "red", (
        "a list beginning with `fail` must be RED; reading it as anything else "
        "on the merge-safety path is the expensive direction")


def test_ci_keep_blocking_reads_a_large_payload_from_stdin():
    r = _cli(["ci-keep-blocking", "--lines", "-", "--required", ""], BIG)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "a large list must survive the boundary intact"


def test_passing_the_same_payload_as_an_argument_is_what_fails():
    """The control. Without this, the two tests above would pass equally well
    on a platform with no limit, and would prove nothing about the defect.

    Skipped rather than asserted where the platform has no such ceiling --
    said out loud, because a silently-skipped control is not a control.
    """
    import pytest
    r = subprocess.run(
        [sys.executable, "-m", "coordinator.cli", "ci-normalize",
         "--buckets", BIG],
        capture_output=True, text=True, cwd=V2,
        env={"PYTHONPATH": str(V2), "PATH": "/usr/bin:/bin"})
    if r.returncode == 0:
        pytest.skip("this platform accepts a >128KB argv element (macOS); the "
                    "ceiling this guards exists on Linux, where CI runs")
    assert r.returncode != 0


def test_a_normal_sized_payload_still_works_as_an_argument():
    """The `-` convention must not have broken every ordinary caller."""
    r = _cli(["ci-normalize", "--buckets", "pass\npending"], "")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "pending"
