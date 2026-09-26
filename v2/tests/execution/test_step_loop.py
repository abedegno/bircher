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
# Models the kernel: an ACCEPTED output becomes the run's current artifact;
# REFUSE_OUTPUT=1 models the refusal (the blob is stored, the output is not).
_kernel_record_output() { _log "record_output gen=$2"; [ "${REFUSE_OUTPUT:-0}" = 1 ] || printf 'hash1' > "$T/current_artifact"; printf 'hash1'; }
_kernel_record_ci() { _log "record_ci status=$3 head=$4 pr_state=${5:-} jobs=${6:-} pr=${7:-}"; }
_kernel_record_review() { _log "record_review $3 artifact=$4 head=${9:-} fps=${12:-}"; }
_kernel_dispatch() { _log "dispatch $1 $2"; printf '7'; }
_kernel_implementer() { printf '%s' "${IMPLEMENTER:-}"; }
_kernel_request_repair() { _log "request_repair $3 head=$4 ev=${5:-}"; }
_kernel_back_state() { printf '42|open|%s|%s' "$HEAD" "$(cat "$T/current_artifact" 2>/dev/null || echo hash0)"; }
_kernel_conflicted() { printf '%s' "${CONFLICTED:-}"; }
_park_back_half() { _log "park $3 ${4:-} ${5:-}"; return "${PARK_RC:-0}"; }
_repair_round() { _log "repair_round pr=$3 branch=$4 round=$6 vendor=$7"; _log "brief: $5"; return 0; }
_pr_branch() { printf 'feat-x'; }
_is_blank() { [ -z "${1//[[:space:]]/}" ]; }
item=i1; code=i1; pr="$PR0"; _iss=5; vendor="${VENDOR0:-claude_code}"; RECOVERY_REVIEWER="${REVIEWER0:-codex}"
_base_sha=$(printf 'b%.0s' $(seq 40)); _ctx_hash=ctx; _ffile="$T/findings"
BIRCHER_RUN_ID=r; BIRCHER_GENERATION=1; BIRCHER_KERNEL_DB="$T/k.db"
outcome=""; review=""; note=""; observed_head=""; _obs_ci=""; ci_first=""; resubmissions=""; _settled_pr=""
_merge_base=""; _delta_digest=""; _fingerprints=""; _pr_state=""; _failing_jobs=""; _out_hash=""; _rev_round=0
_step=""; _step_cause=""; _step_evidence=""; _step_reason=""; merge_rc=0
'''


def _tuple(outcome, review, ci, *, head=HEAD, pr="42", fps="", pr_state="open", jobs=""):
    return f"{outcome}|{review}|n|{head}|{ci}|true|0|{pr}|{'c' * 40}|{'e' * 64}|{fps}|{pr_state}|{jobs}"


def _run(tmp_path, derive_lines, step_lines, states, *, pr0="42", findings="", park_rc=0,
         conflicted=None, vendor0=None, reviewer0=None, refuse_output=False, implementer=None):
    """*conflicted* (the journal's answer), *vendor0* and *reviewer0* (this
    wave's own pick) drive `_seat_vendors`, which is extracted and called
    before `_step_loop` exactly as `_resume_back_half` calls it. Left unset,
    the seating is a no-op over the preamble's defaults."""
    src = RQ.read_text().splitlines()
    (tmp_path / "derive").write_text("".join(l + "\n" for l in derive_lines))
    (tmp_path / "step").write_text("".join(l + "\n" for l in step_lines))
    (tmp_path / "state").write_text("".join(s + "\n" for s in states))
    (tmp_path / "findings").write_text(findings)
    script = (PREAMBLE + _extract_function(src, "_derived_width_ok") + "\n"
              + _extract_function(src, "_seat_vendors") + "\n"
              + _extract_function(src, "_step_loop") + "\n"
              + '_seat_vendors "$BIRCHER_RUN_ID"\n'
              + '_step_loop; printf "%s|%s|%s|%s|%s" "$_step" "$outcome" "$pr" "$_rev_round" "$_out_hash"\n')
    env = dict(os.environ, T=str(tmp_path), PR0=pr0, HEAD=HEAD, PARK_RC=str(park_rc),
               CONFLICTED=conflicted or "", VENDOR0=vendor0 or "claude_code",
               REVIEWER0=reviewer0 or "codex", REFUSE_OUTPUT="1" if refuse_output else "0",
               IMPLEMENTER=implementer or "")
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


def test_done_names_escalated_when_the_pr_state_says_nothing():
    """M4. The other route into `done` is a TERMINAL RUN STATE, and it arrives
    carrying whatever word the derivation produced. `waiting` is not in
    `_kernel_record_run_outcome`'s vocabulary: it maps to `unrecognised`, the
    kernel refuses it, and the run is left with no terminal fact at all. The
    two PR-state cases above do not fire when the PR's state is unreadable, so
    the word has to be mapped here."""
    out, _ = _run(_tmp(), [_tuple("waiting", "na", "pending", head="", pr_state="")],
                  ["done||||no"], [])
    assert out.startswith("done|escalated|42|0|")


def test_done_names_escalated_for_a_repair_word_with_no_pr_state():
    out, _ = _run(_tmp(), [_tuple("repair", "codex:fail", "green", head="", pr_state="")],
                  ["done||||no"], [])
    assert out.startswith("done|escalated|42|0|")


# --- F4: whatever the PR's state, the journal hears about it -----------------

def test_a_vanished_pr_with_no_readable_state_is_still_observed():
    """The derivation settled on NO pull request and could not say what
    happened to the one it was given. Recording only `closed|merged` left the
    journal with nothing, `back.latest_pr_state` frozen, and the run unable to
    end -- forever. The observation goes in whenever a PR was KNOWN; an empty
    `pr_state` is dropped from the payload and reads as open, which is the
    fail-safe direction."""
    out, log = _run(_tmp(), [_tuple("timeout", "na", "na", head="", pr="", pr_state="")], [], [])
    assert out.startswith("none|timeout||0|")
    assert "record_ci status=na head= pr_state= jobs= pr=42" in log


def test_no_pr_was_ever_known_records_nothing():
    """The other side of the same guard: with no PR to speak of there is
    nothing to observe, and an observation naming no PR is not a fact."""
    out, log = _run(_tmp(), [_tuple("timeout", "na", "na", head="", pr="", pr_state="closed")],
                    [], [], pr0="")
    assert out.startswith("none|timeout||0|")
    assert not any(l.startswith("record_ci") for l in log)


# --- F6: a refused verdict is not a silent reviewer --------------------------

def test_a_verdict_this_pass_recorded_that_the_journal_lacks_is_a_refusal():
    """The pass recorded `codex:pass` and `step` still says `review`, which
    means the journal holds no verdict for this head: the KERNEL refused the
    record (almost always the seating). Parking `no_verdict` there blames the
    reviewer for a refusal it did not make and hands a person a question they
    cannot answer."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["review||||no"], ["reviewing"])
    assert out.startswith("wait|waiting|42|0|")
    assert not any(l.startswith("park") for l in log)


def test_no_verdict_recorded_this_pass_still_parks():
    """The other side: nothing was recorded, so the reviewer really was
    silent and a person is the right next step."""
    out, log = _run(_tmp(), [_tuple("escalated", "codex:na", "green")], ["review||||no"], ["reviewing"])
    assert out.startswith("park|parked|42|0|")
    # The derivation's note travels as the park's cause, so the notice can
    # say why there was no verdict (the tuple's note is `n`).
    assert "park no_verdict n " in log, log


# --- F1: the reviewer is seated from the journal, not from the wave's pick ---

def test_the_reviewer_is_the_vendor_the_journal_does_not_name_as_implementer():
    """This wave's usage gate picked codex as the implementer, so `run_item`
    seated claude_code as the reviewer -- but the JOURNAL says claude_code is
    the run's implementer, and the kernel refuses a review from an actor in
    the conflicted set. `_seat_vendors` reads the set and turns both chairs
    round: claude_code implements, codex reviews."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"],
                    conflicted="claude_code", vendor0="codex", reviewer0="claude_code")
    assert out.startswith("merge|ready|42|0|")
    assert "dispatch codex reviewer" in log, log
    assert "dispatch claude_code implementer" in log, log
    assert "dispatch claude_code reviewer" not in log, log


def test_the_repair_session_goes_to_the_journals_implementer_too():
    """Both seats, not just the reviewer's: a repair dispatched to the vendor
    the gate picked would put a second implementer on the run and conflict
    the only reviewer left."""
    out, log = _run(_tmp(), [_tuple("failed", "na", "red", jobs="x"), _tuple("ready", "codex:pass", "green")],
                    ["repair|ci_red|x||no", "merge||||no"], ["implementing", "planned", "implementing"],
                    conflicted="claude_code", vendor0="codex", reviewer0="claude_code")
    assert out.startswith("merge|ready|42|1|")
    assert any(l.startswith("repair_round") and l.endswith("vendor=claude_code") for l in log), log


def test_an_unreadable_conflicted_set_leaves_this_waves_pick_alone():
    """Empty is "the journal did not say", not "nobody is conflicted".
    Seating on silence is the guess this helper exists to stop."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"],
                    conflicted="", vendor0="codex", reviewer0="claude_code")
    assert out.startswith("merge|ready|42|0|")
    assert "dispatch claude_code reviewer" in log, log


def test_both_vendors_conflicted_seats_the_one_who_started_the_implementation():
    """A pass that died between a repair's `start_implementation` and its
    output puts both vendors in the set: the old producer and the new
    implementer. Left on this wave's pick, every wave paid for a review the
    kernel then refused. The vendor who started the implementation takes the
    implementer seat again, its output makes it the only conflicted actor,
    and the other vendor reviews."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"],
                    conflicted="claude_code,codex", vendor0="claude_code", reviewer0="codex",
                    implementer="codex")
    assert "dispatch claude_code reviewer" in log, log
    assert "dispatch codex implementer" in log, log
    assert "dispatch codex reviewer" not in log, log


def test_both_vendors_conflicted_with_no_implementer_answer_leaves_the_seats():
    """No answer is not an answer: the wave's pick stands, as before."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")], ["merge||||no"], ["reviewing"],
                    conflicted="claude_code,codex", vendor0="codex", reviewer0="claude_code")
    assert out.startswith("merge|ready|42|0|")
    assert "dispatch claude_code reviewer" in log, log


def _tmp():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="steploop-"))


def test_a_resumed_pass_takes_the_implementer_seat_before_the_loop():
    """Live on muesli #768: a wave resumed the run at `implementing` holding
    the runner's OPERATOR fence, recorded the output under it, and the kernel
    refused it. `_resume_back_half` must dispatch the implementer after the
    seats are chosen and before `_step_loop` runs, or the first output of a
    resumed pass is refused again."""
    body = _extract_function(RQ.read_text().splitlines(), "_resume_back_half").splitlines()
    seat = next(i for i, l in enumerate(body) if '_seat_vendors "$BIRCHER_RUN_ID"' in l)
    loop = next(i for i, l in enumerate(body) if l.strip() == "_step_loop")
    between = body[seat:loop]
    assert any('_kernel_dispatch "$vendor" implementer' in l and "BIRCHER_GENERATION=" in l for l in between), between


def test_a_refused_output_leaves_the_review_bound_to_what_the_kernel_holds():
    """The helper echoes the hash it stored even when the kernel refuses the
    output. Binding the review to that echo is the second refusal of the live
    failure; the binding must name the run's current artifact instead."""
    out, log = _run(_tmp(), [_tuple("ready", "codex:pass", "green")],
                    ["merge||||no"], ["implementing"], refuse_output=True)
    assert f"record_review codex:pass artifact=hash0 head={HEAD} fps=" in log
    assert not any("artifact=hash1" in l for l in log)
    assert out.endswith("|hash0")

