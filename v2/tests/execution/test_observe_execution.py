"""The remaining SHELL observers, executed with stubs.

Shrinking by design. `observe_ci_history` and `classify_recovery` moved to
v2/coordinator/, and their tests here were deleted with them -- the
replacement is tests/coordinator/test_observe.py, which covers the same
properties natively (earliest-run-decides, distinct-commits-not-runs,
in-flight-is-not-a-verdict, unknown-never-becomes-false) plus four the shell
versions never had.

What is left needs a shell harness because it is still shell. When
`observe_review` and `observe_outcome` follow, this file goes with them.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OBSERVE = REPO_ROOT / "batch" / "lib" / "observe.sh"


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


RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _extract(name, path=None, code_only=False):
    lines = (path or RUN_QUEUE).read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    body = lines[start:end + 1]
    if code_only:
        # Comments explaining the retired marker are legitimate history. The
        # property under test is that nothing WRITES or READS one, which is a
        # property of code. The repo's existing structural tests strip comments
        # for the same reason.
        body = [l for l in body if not l.strip().startswith("#")]
    return "\n".join(body)


def test_run_item_no_longer_reads_a_marker():
    """The structural half. `run_item` must not mention the marker at all --
    not parse_marker, not _marker_bodies_since, not the string itself."""
    body = _extract("run_item", code_only=True)
    for banned in ("parse_marker", "_marker_bodies_since", "bircher-status:"):
        assert banned not in body, f"run_item still reads the marker: {banned}"


def test_the_derived_tuple_carries_all_seven_fields(tmp_path):
    """A caller reading five fields where seven are emitted silently absorbs
    the last two into `ci`. Both callers must be updated together."""
    script = f"""
set -uo pipefail
REPO=demo/demo
BIRCHER_NET_TIMEOUT=5
RECOVERY_REVIEWER=codex
classify_recovery() {{ printf 'ready|codex:pass|green|derived'; }}
observe_ci_history() {{ printf 'true|2'; }}
observe_review() {{ printf 'PASS|/dev/null'; }}
_pr_is_abandoned() {{ return 1; }}
_reconcile_item_pr() {{ printf ''; }}
_normalize_ci() {{ printf 'green'; }}
_keep_blocking_checks() {{ printf ''; }}
_required_contexts() {{ printf ''; }}
_discover_pr_by_issue() {{ printf ''; }}
_branch_code_filter() {{ printf ''; }}
_effect() {{ return 0; }}
gh() {{ case "$*" in
          *head.sha*)  printf '%040d' 7 ;;
          *head.ref*)  printf 'feat-x' ;;
          *)           printf '' ;;
        esac; }}

{_extract("observe_outcome")}

observe_outcome item-1 code1 42 ""
"""
    f = tmp_path / "outcome.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True)
    fields = r.stdout.strip().split("|")
    assert len(fields) == 7, (fields, r.stderr[-800:])
    assert fields[0] == "ready", fields
    assert fields[5] == "true" and fields[6] == "2", fields


def test_the_derived_comment_carries_no_machine_marker(tmp_path):
    """Decision 3: the reviewer's findings stay, the parseable prefix goes."""
    body = _extract("observe_outcome", code_only=True)
    assert "bircher-status:" not in body, "observe_outcome still writes a marker"
