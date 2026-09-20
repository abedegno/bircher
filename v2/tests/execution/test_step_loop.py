"""`_step_loop` driven for real with the world scripted (closed-loop spec §2).

The derivation, the step CLI and the kernel are stubs that read their answers
from files and log what they were asked; the loop under test is the real
function extracted from run-queue.sh.
"""

import os
import subprocess
from pathlib import Path

RQ = Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"
HEAD = "d" * 40


def _extract_function(src_lines, name):
    start = next(i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


PREAMBLE = r'''
set -uo pipefail
LOG="$T/calls.log"
_log() { printf '%s\n' "$*" >> "$LOG"; }
_next() { local f="$T/$1" line=""; IFS= read -r line < "$f" || true; tail -n +2 "$f" > "$f.tmp"; mv "$f.tmp" "$f"; printf '%s' "$line"; }
observe_outcome() { _log "observe pr=${3:-}"; _next derive; }
_coordinator() { if [ "$1" = step ]; then _log "step $*"; _next step; else _log "coordinator $*"; fi; }
_kernel_state() { _next state; }
_kernel_record_output() { _log "record_output"; printf 'hash1'; }
_kernel_record_ci() { _log "record_ci status=$3 head=$4 pr_state=${5:-} jobs=${6:-} pr=${7:-}"; }
_kernel_record_review() { _log "record_review $3 artifact=$4 head=${9:-} fps=${12:-}"; }
_kernel_dispatch() { printf '7'; }
_kernel_request_repair() { _log "request_repair $3 head=$4 ev=${5:-}"; }
_kernel_back_state() { printf '42|open|%s|hash0' "$HEAD"; }
_park_back_half() { _log "park $3 ${4:-} ${5:-}"; return "${PARK_RC:-0}"; }
_repair_round() { _log "repair_round pr=$3 branch=$4 round=$6 vendor=$7"; _log "brief: $5"; return 0; }
_pr_branch() { printf 'feat-x'; }
_is_blank() { [ -z "${1//[[:space:]]/}" ]; }
item=i1; code=i1; pr="$PR0"; _iss=5; vendor=claude_code; RECOVERY_REVIEWER=codex
_base_sha=$(printf 'b%.0s' $(seq 40)); _ctx_hash=ctx; _ffile="$T/findings"
BIRCHER_RUN_ID=r; BIRCHER_GENERATION=1; BIRCHER_KERNEL_DB="$T/k.db"
outcome=""; review=""; note=""; observed_head=""; _obs_ci=""; ci_first=""; resubmissions=""; _settled_pr=""
_merge_base=""; _delta_digest=""; _fingerprints=""; _pr_state=""; _failing_jobs=""; _out_hash=""; _rev_round=0
_step=""; _step_cause=""; _step_evidence=""; _step_reason=""; merge_rc=0
'''


def _tuple(outcome, review, ci, *, head=HEAD, pr="42", fps="", pr_state="open", jobs=""):
    return f"{outcome}|{review}|n|{head}|{ci}|true|0|{pr}|{'c' * 40}|{'e' * 64}|{fps}|{pr_state}|{jobs}"


def _run(tmp_path, derive_lines, step_lines, states, *, pr0="42", findings="", park_rc=0):
    src = RQ.read_text().splitlines()
    (tmp_path / "derive").write_text("".join(l + "\n" for l in derive_lines))
    (tmp_path / "step").write_text("".join(l + "\n" for l in step_lines))
    (tmp_path / "state").write_text("".join(s + "\n" for s in states))
    (tmp_path / "findings").write_text(findings)
    script = (PREAMBLE + _extract_function(src, "_derived_width_ok") + "\n"
              + _extract_function(src, "_step_loop") + "\n"
              + '_step_loop; printf "%s|%s|%s|%s|%s" "$_step" "$outcome" "$pr" "$_rev_round" "$_out_hash"\n')
    env = dict(os.environ, T=str(tmp_path), PR0=pr0, HEAD=HEAD, PARK_RC=str(park_rc))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    log_file = tmp_path / "calls.log"
    return r.stdout.strip(), (log_file.read_text().splitlines() if log_file.exists() else [])


def test_red_ci_requests_a_repair_then_merges_on_the_next_pass():
    out, log = _run(
        _tmp(), [_tuple("failed", "na", "red", jobs="server (go)"), _tuple("ready", "codex:pass", "green")],
        ["repair|ci_red|server (go)||no", "merge||||no"],
        ["implementing", "planned", "implementing"])
    assert out.startswith("merge|ready|42|1|")
    assert f"request_repair ci_red head={HEAD} ev=server (go)" in log
    assert "repair_round pr=42 branch=feat-x round=1 vendor=claude_code" in log
    assert any(l.startswith("brief: CI is red on ddddddd") and "server (go)" in l for l in log)
    assert "record_ci status=red head=" + HEAD + " pr_state=open jobs=server (go) pr=42" in log
    assert not any(l.startswith("record_review na") for l in log)
    assert sum(l.startswith("record_review codex:pass") for l in log) == 1


def test_a_fail_is_repaired_from_the_findings_file():
    fp = "ab" * 20
    out, log = _run(
        _tmp(), [_tuple("failed", "codex:fail", "green", fps=fp), _tuple("escalated", "na", "pending", head="")],
        [f"repair|review_fail|{fp}||no", "wait||||no"],
        ["implementing", "planned", "implementing"], findings="blocking: the thing")
    assert out.startswith("wait|waiting|42|1|")
    assert "brief: blocking: the thing" in log
    assert f"record_review codex:fail artifact=hash1 head={HEAD} fps={fp}" in log


def test_three_identical_fails_park_for_no_progress():
    fp = "ab" * 20
    out, log = _run(_tmp(), [_tuple("failed", "codex:fail", "green", fps=fp)],
                    [f"park|review_fail|{fp}|no_progress|no"], ["reviewing"])
    assert out.startswith("park|parked|42|0|")
    assert f"park no_progress review_fail {fp}" in log
    assert not any(l.startswith("repair_round") for l in log)


def test_no_verdict_parks_no_verdict():
    out, log = _run(_tmp(), [_tuple("escalated", "codex:na", "green")], ["review||||no"], ["reviewing"])
    assert out.startswith("park|parked|42|0|")
    assert any(l.startswith("park no_verdict") for l in log)


def test_a_refused_park_waits_instead():
    out, _ = _run(_tmp(), [_tuple("escalated", "codex:na", "green")], ["review||||no"], ["reviewing"], park_rc=1)
    assert out.startswith("wait|waiting|42|0|")


def test_a_repair_already_requested_is_redispatched_without_a_new_request():
    out, log = _run(_tmp(), [_tuple("failed", "na", "red", jobs="x"), _tuple("ready", "codex:pass", "green")],
                    ["repair|ci_red|x||yes", "merge||||no"], ["planned", "implementing"])
    assert out.startswith("merge|ready|42|1|")
    assert not any(l.startswith("request_repair") for l in log)
    assert any(l.startswith("repair_round") for l in log)


def test_a_closed_pr_is_observed_and_the_loop_steps_aside():
    out, log = _run(_tmp(), [_tuple("timeout", "na", "na", head="", pr="", pr_state="closed")], [], [])
    assert out.startswith("none|timeout||0|")
    assert "record_ci status=na head= pr_state=closed jobs= pr=42" in log
    assert not any(l.startswith("step") for l in log)


def test_a_crashed_derivation_waits_and_records_nothing():
    out, log = _run(_tmp(), [""], [], [])
    assert out.startswith("wait|waiting|42|0|")
    assert log == ["observe pr=42"]


def test_at_reviewing_the_binding_names_the_artifact_on_record():
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"])
    assert out == "merge|ready|42|0|hash0"
    assert not any(l.startswith("record_output") for l in log)
    assert f"record_review codex:pass artifact=hash0 head={HEAD} fps=" in log


def test_done_names_ready_from_a_merged_pr():
    """`done` can arrive with the derivation's own word `repair` or `waiting`
    -- neither is in `_kernel_record_run_outcome`'s vocabulary
    (merged|ready|escalated|noop|skipped|failed|timeout), and the kernel
    refuses an unrecognised payload, leaving the run with no terminal fact. A
    merged PR names its own terminal word instead of handing that word
    through."""
    out, log = _run(_tmp(), [_tuple("repair", "codex:fail", "green", head="", pr_state="merged")],
                    ["done||||no"], [])
    assert out.startswith("done|ready|42|0|")


def test_done_names_escalated_from_a_closed_pr():
    out, log = _run(_tmp(), [_tuple("waiting", "na", "pending", head="", pr_state="closed")],
                    ["done||||no"], [])
    assert out.startswith("done|escalated|42|0|")


def _tmp():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="steploop-"))
