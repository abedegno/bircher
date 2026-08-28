"""The observers, EXECUTED with a stubbed `gh`.

Phase 1's three defects were all invisible to unit tests that stubbed the
effect seam. These extract the real shell functions and drive them.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OBSERVE = REPO_ROOT / "batch" / "lib" / "observe.sh"


def _drive(tmp_path, tag, runs_json, call="observe_ci_history feat-x"):
    """Run an observer for real against a stubbed `gh api`."""
    out = tmp_path / f"gh-{tag}.json"
    out.write_text(runs_json)
    script = f"""
set -uo pipefail
REPO=demo/demo
gh() {{ cat {out}; }}
. "{OBSERVE}"
{call}
"""
    f = tmp_path / f"obs-{tag}.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr


def test_one_green_run_is_first_time_green_with_no_resubmissions(tmp_path):
    out, err = _drive(tmp_path, "green", "aaa|success|2026-08-01T10:00:00Z\n")
    assert out == "true|0", (out, err)


def test_the_EARLIEST_run_decides_ci_first_not_the_latest(tmp_path):
    """A branch that went red then green passed on the SECOND try. Reading the
    newest run would call every eventually-green branch first-time-green, which
    is the metric inverted."""
    out, err = _drive(tmp_path, "redgreen",
                      "bbb|success|2026-08-01T12:00:00Z\n"
                      "aaa|failure|2026-08-01T10:00:00Z\n")
    assert out == "false|1", (out, err)


def test_resubmissions_counts_DISTINCT_commits_not_runs(tmp_path):
    """Re-running CI on the same commit is not a resubmission. Counting runs
    would inflate every flaky branch into a fix loop that never happened.
    Observed live on muesli: three runs on one sha."""
    out, err = _drive(tmp_path, "rerun",
                      "aaa|failure|2026-08-01T10:00:00Z\n"
                      "aaa|success|2026-08-01T10:30:00Z\n")
    assert out == "false|0", (out, err)


def test_runs_still_in_flight_are_not_read_as_a_verdict(tmp_path):
    """A null conclusion is 'not finished', not 'not success'."""
    out, err = _drive(tmp_path, "pending", "aaa||2026-08-01T10:00:00Z\n")
    assert out == "unknown|", (out, err)


def test_no_runs_at_all_is_unknown_not_false(tmp_path):
    """No CI history is the absence of evidence. Reporting `false` would put a
    claim in the scorecard that nothing observed."""
    out, err = _drive(tmp_path, "none", "")
    assert out == "unknown|", (out, err)


def test_an_api_failure_is_unknown_not_a_silent_zero(tmp_path):
    """`gh` exiting non-zero must not read as 'no runs, so first-time green'."""
    script = f"""
set -uo pipefail
REPO=demo/demo
gh() {{ return 1; }}
. "{OBSERVE}"
observe_ci_history feat-x
"""
    f = tmp_path / "obs-fail.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    assert r.stdout.strip() == "unknown|", (r.stdout, r.stderr)
