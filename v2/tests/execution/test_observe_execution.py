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


def _review_script(tmp_path, log, *, verdict_fn, omnigent_fn, extra=""):
    return f"""
set -uo pipefail
REPO=demo/demo
RECOVERY_REVIEWER=codex
BUNDLE_DIR={tmp_path}
SERVER=http://x
BIRCHER_REVIEW_LOG={log}
_recovery_review_prompt() {{ printf 'review pr %s at %s' "$1" "$2"; }}
_extract_verdict() {{ {verdict_fn} }}
omnigent() {{ {omnigent_fn} }}
{extra}
. "{OBSERVE}"
observe_review 42 cafebabe0000
"""


def _run(tmp_path, name, script):
    f = tmp_path / name
    f.write_text(script)
    return subprocess.run(["bash", str(f)], capture_output=True, text=True)


def test_a_reviewer_that_says_PASS_is_read_as_PASS(tmp_path):
    r = _run(tmp_path, "rev-pass.sh", _review_script(
        tmp_path, tmp_path / "rlog",
        verdict_fn="printf '%s' \"$(printf '%s' \"$1\" | grep -oE 'VERDICT: [A-Z]+' | sed 's/VERDICT: //')\";",
        omnigent_fn="echo 'findings here'; echo 'VERDICT: PASS';"))
    assert r.stdout.strip().startswith("PASS|"), (r.stdout, r.stderr)


def test_a_reviewer_that_produces_NO_verdict_is_NONE_not_PASS(tmp_path):
    """A reviewer that crashed, timed out, or rambled has not approved
    anything. Defaulting to PASS would authorise a merge on silence."""
    r = _run(tmp_path, "rev-none.sh", _review_script(
        tmp_path, tmp_path / "rlog2",
        verdict_fn="printf '';",
        omnigent_fn="echo 'I could not complete the review.';"))
    assert r.stdout.strip().startswith("NONE|"), (r.stdout, r.stderr)


def test_a_reviewer_that_EXITS_NONZERO_is_NONE(tmp_path):
    """A dead reviewer's stdout is not evidence. Mining it for a verdict would
    let a crash that echoed its own prompt authorise a merge."""
    r = _run(tmp_path, "rev-rc1.sh", _review_script(
        tmp_path, tmp_path / "rlog3",
        verdict_fn="printf 'PASS';",
        omnigent_fn="echo 'VERDICT: PASS'; return 1;"))
    assert r.stdout.strip().startswith("NONE|"), (
        f"a reviewer that died had its stdout mined for a verdict: {r.stdout}")


def test_the_review_is_dispatched_against_the_SHA_it_was_given(tmp_path):
    """#66: the prompt carries the sha run-queue observed. A reviewer asked to
    find its own head can bless a concurrent push."""
    seen = tmp_path / "seen"
    r = _run(tmp_path, "rev-sha.sh", _review_script(
        tmp_path, tmp_path / "rlog4",
        verdict_fn="printf 'PASS';",
        omnigent_fn=f"printf '%s\\n' \"$*\" > {seen}; echo 'VERDICT: PASS';"))
    assert seen.exists(), (r.stdout, r.stderr)
    assert "cafebabe0000" in seen.read_text(), seen.read_text()
