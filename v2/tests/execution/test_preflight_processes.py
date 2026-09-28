"""The runner container's process table (2026-09-28).

omnigent host's orphan reaper stalled on 16 September and ~35,900 zombies
filled the container's pids limit; from then on every wave died at its first
command with a Go "failed to create new OS thread" trace from `gh`, which
named neither the cause nor the fix. The wave now reads the table first.
"""
import subprocess
from pathlib import Path

RQ = Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"


def _extract_function(src_lines, name):
    start = next(i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


def _run(tmp_path, current, maximum):
    root = tmp_path / "cg"
    root.mkdir()
    if current is not None:
        (root / "pids.current").write_text(f"{current}\n")
    if maximum is not None:
        (root / "pids.max").write_text(f"{maximum}\n")
    fn = _extract_function(RQ.read_text().splitlines(), "preflight_processes")
    return subprocess.run(["bash", "-c", f"set -u\n{fn}\npreflight_processes"],
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "BIRCHER_CGROUP_ROOT": str(root)})


def test_a_nearly_full_table_refuses_the_wave_and_says_why(tmp_path):
    r = _run(tmp_path, 38076, 38079)
    assert r.returncode == 1
    assert "38076 of 38079" in r.stderr and "zombie" in r.stderr, r.stderr


def test_half_full_warns_but_runs(tmp_path):
    r = _run(tmp_path, 20000, 38079)
    assert r.returncode == 0
    assert "WARN" in r.stderr and "20000 of 38079" in r.stderr, r.stderr


def test_a_healthy_table_passes_quietly(tmp_path):
    r = _run(tmp_path, 8, 38079)
    assert r.returncode == 0 and "WARN" not in r.stderr
    assert "8 of 38079" in r.stdout


def test_no_limit_is_not_a_failure(tmp_path):
    assert _run(tmp_path, 8, "max").returncode == 0


def test_no_cgroup_files_is_not_a_failure(tmp_path):
    """macOS, or a host without cgroup v2: nothing to read, nothing to refuse."""
    assert _run(tmp_path, None, None).returncode == 0


def test_the_wave_checks_it_before_anything_else():
    body = _extract_function(RQ.read_text().splitlines(), "main").splitlines()
    guard = next(i for i, l in enumerate(body) if l.strip().startswith("preflight_processes"))
    kernel = next(i for i, l in enumerate(body) if l.strip() == "preflight_kernel || exit 2")
    assert guard < kernel
