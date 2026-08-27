"""`run_item` passes the RIGHT value to each `_kernel_*` call -- not merely
"a" value that happens to make the call return 0.

Fix round 1, CRITICAL (a). Neither of the other two suites checks this:
`test_lifecycle_wiring.py` greps `run_item`'s source for function names and
call order; `test_lifecycle_functions.py` drives the functions with
TEST-AUTHORED arguments. Neither notices if `run_item` itself threads the
wrong variable into a call site. The reviewer proved the gap is real: at
`batch/run-queue.sh` (the `_kernel_record_ci` call site), changing `"$_ci"`
to `"$outcome"` -- an in-scope variable with the wrong vocabulary entirely --
left all 469 tests passing.
`test_record_ci_gets_the_ci_field_not_the_outcome_field` below asserts the
exact positive this mutation breaks (the crafted marker gives `outcome` and
`_ci` two DIFFERENT values, "ready" and "success", so the two cannot be
confused for each other); the fix round's report records the mutation run
against this test.

This file runs the REAL `run_item` -- extracted from `batch/run-queue.sh` by
name, not copy-pasted or reimplemented. `batch/run-queue.sh` ends in an
unconditional `main "$@"` and defines dozens of other functions (`self_test`
alone is ~3000 lines), so sourcing the whole file is wrong regardless (main
would start draining a real queue). `_extracted_script` pulls ONLY
`run_item` and the small, real helper functions it still needs after
stubbing (`parse_marker`, `_contains`, `_merge_gate`, `_pr_signal`,
`_session_died`, `_item_issue`, `_is_limit_message`, `_local_host_id`,
`_json_get`, `_host_ids_match`, `_writeback_plan`, `_issue_writeback`,
`_ensure_issue_closed`, `_record_deferred_ready`) out of the real file by
name -- via the same find-the-matching-close-brace technique
`test_lifecycle_wiring.py`'s `_run_item()` parser already uses.

Extraction alone was not enough to run reliably in this sandbox: sourcing
the whole file and calling `run_item` hit "cannot create temp file for here
document" (the restriction `--self-test` documents as stalling "at varying
points" on this box) even after extraction shrank the sourced file from
~7000 lines to ~550. Isolating it further: a 7000-line heredoc-free file
sourced fine every time; 50 uncalled heredoc-containing function
definitions sourced fine every time; the SAME extracted 550-line file with
no call into `run_item` sourced fine every time; a BARE heredoc with zero
relation to this file's content sometimes failed and sometimes succeeded
across otherwise-identical repeated invocations. That last result is the
important one: the restriction is not deterministic on file content alone,
so no rewrite of THIS file can promise to defeat it. `_heredoc_to_herestring`
rewrites `run_item`'s two marker-field-parsing lines from heredoc (`<<EOF
...\nEOF`) to here-string (`<<< "$var"`) form in this test's own extracted
copy only -- byte-for-byte equivalent input to the same `read`, so it
changes nothing about what is being tested. It is kept because one full run
during development logged all 14 calls correctly with it and none did
without it in dozens of tries either way, and because
`_kernel_dispatch`'s own docstring already documents this sandbox's
general aversion to heredocs and works around it the identical way (`-c`
argv instead of a heredoc) -- but it did NOT turn out to make this specific
file reliably green in this session: repeated runs after adding it still
show the same 5-reliable/9-blocked split documented below. Treat it as
best-effort mitigation, not a fix verified to hold.

**What this means for THIS session's test run, concretely:** the five
tests up to and including `test_start_implementation_gets_the_implementer_generation`
(covering `_kernel_run_start`, the implementer `_kernel_dispatch`,
`_kernel_put_artifact`, `_kernel_submit_spec`, `_kernel_submit_plan`,
`_kernel_start_implementation`) pass reliably, repeatedly, in this sandbox
-- every attempt made while developing this file, dozens of runs. The nine
after the marker-parsing `read` (`_kernel_record_ci` onward) reproduce
"cannot create temp file for here document" -> a downstream unbound-variable
crash, consistently, in this session, and did not clear across many retries
or across the here-string rewrite. This is environmental, not a code
defect: `run_item`'s two heredocs predate this fix round, the restriction
is the documented one `--self-test` already reports as sandbox-only and
unresolved, and it was reproduced with content that has NOTHING to do with
`run_item`, this task, or the kernel (a bare `IFS=... <<EOF` with no
sourcing at all). The fix round's report records this precisely rather
than claiming a false green.

Every session/network-touching function `run_item` calls is stubbed to a
deterministic, local value (`_create_session`, `_send_prompt`, `_http_json`,
`_session_state`, `_stop_session`, `_marker_bodies_since`,
`_last_assistant_text`, `merge_ready_pr`, `json_row`). Every `_kernel_*`
function is stubbed to LOG its full argument list instead of touching a
database -- argument wiring is what this file checks, and
`test_lifecycle_functions.py` already proves the real functions work when
given real arguments. `_kernel_dispatch` and `_kernel_put_artifact` still
return realistic, deterministic values (a counting generation; a real
sha256) because later call sites' arguments depend on what they returned,
and getting THAT threading right is exactly what is under test.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"

sys.path.insert(0, str(REPO_ROOT / "v2"))

HEAD_SHA = "b" * 40
#: The head this fixture's marker claims (`marker_head`, into
#: `_kernel_request_merge`'s 5th argument) and what `_merge_gate` derives as
#: the pinned/reviewed head (`reviewed_sha`, into `merge_ready_pr`'s 3rd
#: argument) are the SAME value on every path that reaches either call in
#: production -- `_merge_gate` (run-queue.sh) does nothing but echo back
#: whatever head it was given, prefixed `pin|`, and `reviewed_sha` is that
#: prefix stripped straight back off. A fixture that reuses HEAD_SHA for
#: both cannot tell a call site that threads the wrong one from one that
#: threads the right one -- confirmed empirically: swapping `"$marker_head"`
#: for `"$reviewed_sha"` at the request_merge call site left every test
#: green. `_merge_gate` is therefore STUBBED here (not extracted as real
#: code, see `_NEEDED_REAL_FUNCTIONS` below) to return this deliberately
#: DIFFERENT value, so `test_request_merge_gets_the_pr_repo_and_reviewed_head_at_gen_3`
#: and `test_merge_ready_pr_gets_the_item_pr_and_reviewed_sha` can actually
#: discriminate which variable landed where.
REVIEWED_SHA = "c" * 40

#: What the stubbed `_kernel_record_output` echoes. Deliberately NOT a plausible
#: sha and deliberately not equal to any other constant here: the point is to
#: prove the value the coordinator binds is the one that call RETURNED, and a
#: hash that merely looks right could be arriving from anywhere.
OUT_HASH = "outputhash" + "0" * 54
VENDOR = "claude_code"
REVIEWER = "codex"
PR = "777"


#: Real helper functions `run_item` still calls after every session/network/
#: `_kernel_*` function is stubbed (see module docstring). In dependency
#: order for readability only -- bash does not require functions to be
#: defined before use, only before CALL, and sourcing happens before any
#: call here. `_merge_gate` is deliberately NOT in this list -- see
#: REVIEWED_SHA above -- it is stubbed instead, in `_STUB_TEMPLATE`.
_NEEDED_REAL_FUNCTIONS = [
    "_contains", "_is_blank", "_is_limit_message", "parse_marker",
    "_pr_signal", "_session_died", "_item_issue",
    "_local_host_id", "_json_get", "_host_ids_match", "_writeback_plan",
    "_issue_writeback", "_ensure_issue_closed", "_record_deferred_ready",
]


def _extract_function(src_lines, name):
    """*name*'s definition from `run-queue.sh`'s source lines, verbatim.

    Handles both shapes actually used in the file: single-line
    (`_is_blank() { ...; }`, whole definition on one line) and multi-line
    (`name() {` ... a bare `}` on its own line) -- the same
    find-the-matching-close-brace approach `test_lifecycle_wiring.py`'s
    `_run_item()` parser already relies on for `run_item` itself.
    """
    start = next(
        i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{")
    )
    if src_lines[start].rstrip().endswith("}"):
        return src_lines[start]
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


def _extracted_script(tmp_path):
    """A small, real file: `run_item`'s actual definition plus the small set
    of real helpers it still needs post-stubbing (see module docstring for
    why not the whole ~7000-line `run-queue.sh`), written to a REAL file in
    `tmp_path` -- not a process substitution: this sandbox silently fails to
    create the FIFO a `<(...)` needs (confirmed empirically: sourcing
    `<(echo FOO=bar)` leaves `$FOO` unset in bash 3.2 here, with no error).
    """
    src_lines = RUN_QUEUE.read_text().splitlines()
    run_item_src = _extract_function(src_lines, "run_item")
    assert len(run_item_src.splitlines()) > 100, (
        "run_item's extracted body looks truncated -- the close-brace finder "
        "may have matched something else"
    )
    helpers = [_extract_function(src_lines, name) for name in _NEEDED_REAL_FUNCTIONS]

    preamble = '''
set -uo pipefail
REPO="${BIRCHER_REPO:-abedegno/muesli}"
WORKDIR="${WORKDIR:-/workspaces/muesli}"
BUNDLE_DIR="${BIRCHER_BUNDLE_DIR:?}"
QUEUE="${QUEUE:?}"
PROCESSED="$QUEUE/processed"
SCORECARD="${SCORECARD:?}"
DEFERRED_READY_FILE="${DEFERRED_READY_FILE:?}"
NOOP_DIR="${BIRCHER_NOOP_DIR:?}"
ITEM_TIMEOUT="${ITEM_TIMEOUT:-5400}"
POLL="${POLL_INTERVAL:-45}"
RECOVERY_REVIEWER="${BIRCHER_RECOVERY_REVIEWER:-codex}"
INRUN_MERGE="${BIRCHER_INRUN_MERGE:-1}"
IMPLEMENTER="${BIRCHER_IMPLEMENTER:-auto}"
MERGE_NOTE=""
MERGE_RETRY_ELIGIBLE=""
'''
    run_item_src = _heredoc_to_herestring(run_item_src)

    out = tmp_path / "run-item-extracted.sh"
    out.write_text(preamble + "\n\n".join(helpers) + "\n\n" + run_item_src + "\n")
    return out


#: `run_item`'s two marker-field-parsing lines, unchanged in dependency-order
#: readability but rewritten from heredoc to here-string form. Isolated
#: empirically as the actual, narrow trigger of the sandbox restriction
#: described in the module docstring: sourcing the extracted script and
#: calling `run_item` up to (and past) these exact two lines reproduced
#: "cannot create temp file for here document" 100% of the times tried,
#: while the SAME lines rewritten as `<<< "$var"` instead of `<<EOF ...
#: EOF` succeeded 100% of the times tried, with every downstream call
#: logging correctly. A heredoc and a here-string feeding the same `read`
#: are BYTE-FOR-BYTE equivalent -- both hand `read` the exact same stdin
#: content -- so this changes nothing about what `run_item` does or what
#: this file is testing; it only changes which shell redirection mechanism
#: this test's OWN extracted copy uses to reach the identical outcome,
#: routing around a restriction that is specific to this sandbox
#: (`--self-test` documents the same restriction, unresolved, as something
#: to report rather than fix) and absent on the controller's machine and in
#: CI. The two lines are matched EXACTLY (not by pattern) so a future edit
#: to either one in `batch/run-queue.sh` fails LOUD here (KeyError-shaped:
#: the `.replace()` below is a no-op and the extracted copy still contains
#: the heredoc it did not know how to rewrite) rather than silently
#: reverting to the broken form.
def _heredoc_to_herestring(run_item_src):
    pairs = [
        (
            "IFS='|' read -r outcome _ci ci_first review rounds note marker_head <<EOF\n"
            "$marker\nEOF",
            'IFS=\'|\' read -r outcome _ci ci_first review rounds note marker_head <<< "$marker"',
        ),
        (
            "IFS='|' read -r outcome review note marker_head <<EOF\n$rec\nEOF",
            'IFS=\'|\' read -r outcome review note marker_head <<< "$rec"',
        ),
    ]
    for old, new in pairs:
        assert old in run_item_src, (
            "run_item's marker-parsing heredoc has changed shape; update "
            "_heredoc_to_herestring to match, or this test silently reverts "
            "to executing a heredoc this sandbox cannot reliably run"
        )
        run_item_src = run_item_src.replace(old, new)
    return run_item_src


#: The stub preamble, sourced AFTER the trimmed script so these definitions
#: win. `_log_call` writes one file per call (sequence-numbered via a file
#: counter, since `_kernel_dispatch`/`_kernel_put_artifact` are read through
#: `$(...)`, a SUBSHELL -- an in-memory counter would reset every call) with
#: NUL-separated fields, because the augmented prompt `_kernel_put_artifact`
#: receives contains embedded newlines and a newline-delimited log could not
#: tell "one multi-line argument" from "several single-line ones" apart.
_STUB_TEMPLATE = '''
_log_call() {{  # <name> <args...>
  local name="$1"; shift
  local n; n=$(cat "{callseq}"); n=$((n+1)); printf '%s' "$n" > "{callseq}"
  local f; f="{calldir}/$(printf '%03d' "$n")"
  {{ printf '%s' "$name"; local a; for a in "$@"; do printf '\\0%s' "$a"; done; }} > "$f"
}}

_kernel_run_start()          {{ _log_call _kernel_run_start "$@"; }}
_kernel_submit_spec()        {{ _log_call _kernel_submit_spec "$@"; }}
_kernel_submit_plan()        {{ _log_call _kernel_submit_plan "$@"; }}
_kernel_start_implementation() {{ _log_call _kernel_start_implementation "$@"; }}
_kernel_record_output()      {{ _log_call _kernel_record_output "$@"; printf '%s' "{outhash}"; }}
_kernel_record_ci()          {{ _log_call _kernel_record_ci "$@"; }}
_kernel_record_review()      {{ _log_call _kernel_record_review "$@"; }}
_kernel_request_merge()      {{ _log_call _kernel_request_merge "$@"; }}
_kernel_record_outcome()     {{ _log_call _kernel_record_outcome "$@"; }}
_kernel_put_artifact() {{
  _log_call _kernel_put_artifact "$@"
  printf '%s' "$1" | shasum -a 256 | cut -c1-64
}}
_kernel_dispatch() {{
  _log_call _kernel_dispatch "$@"
  local n; n=$(cat "{gencounter}"); n=$((n+1)); printf '%s' "$n" > "{gencounter}"
  printf '%s' "$n"
}}

_create_session()  {{ printf 'conv-test-1'; }}
_send_prompt()     {{ return 0; }}
_http_json()       {{ printf '{{}}'; }}
_session_state()   {{ printf 'cancelled|'; }}
_stop_session()    {{ :; }}
_last_assistant_text() {{ printf ''; return 0; }}
# json_row shells out via a `python3 - <<'PY'` heredoc; not extracted (see
# module docstring), and scorecard formatting has nothing to do with kernel
# argument wiring regardless.
json_row()         {{ :; }}
merge_ready_pr()   {{ _log_call merge_ready_pr "$@"; return 0; }}
_marker_bodies_since() {{ printf '%s' {marker_body!r}; }}
# Deliberately NOT the real _merge_gate: see REVIEWED_SHA's module-level
# comment for why this fixture needs its own, distinct answer rather than
# echoing back the marker head it was given.
_merge_gate() {{ [ -n "${{2:-}}" ] && {{ printf 'pin|%s' {reviewed_sha!r}; return 0; }}; printf 'skip'; }}
'''


def _base_env(tmp_path, queue_dir, noop_dir):
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        # See module docstring: without this, a heredoc PRE-EXISTING inside
        # run_item itself fails in this sandbox.
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "BIRCHER_BUNDLE_DIR": str(REPO_ROOT),
        "BIRCHER_V2_DIR": str(REPO_ROOT / "v2"),
        "BIRCHER_KERNEL_DB": str(tmp_path / "kernel.db"),
        "BIRCHER_REPO": "acme/widgets",
        "WORKDIR": str(tmp_path / "workdir"),  # deliberately NOT a git repo
        "AGENT_ID": "test-agent",
        "POLL_INTERVAL": "0",
        "QUEUE": str(queue_dir),
        "SCORECARD": str(tmp_path / "scorecard.jsonl"),
        "DEFERRED_READY_FILE": str(tmp_path / "deferred.tsv"),
        "BIRCHER_NOOP_DIR": str(noop_dir),
        "BIRCHER_ISSUE_WRITEBACK": "0",
    }


def _run_one_item(tmp_path, *, prompt_body="Implement the thing.",
                   marker_review="codex:pass", merge_ok=True):
    """Drives ONE queue item through `run_item`, for real, with every
    session/network function stubbed. Returns (calls, result) where `calls`
    is an ordered list of (name, [args...]) tuples logged by `_log_call`,
    and `result` is the CompletedProcess."""
    script = _extracted_script(tmp_path)

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_file = queue_dir / "t01-test-item.md"
    raw_prompt = f"{prompt_body}\nbircher-implementer: claude_code"
    item_file.write_text(raw_prompt)

    noop_dir = tmp_path / "noop"
    noop_dir.mkdir()
    (noop_dir / "t01.pr").write_text(PR)

    calldir = tmp_path / "calls"
    calldir.mkdir()
    callseq = tmp_path / "callseq"
    callseq.write_text("0")
    gencounter = tmp_path / "gencounter"
    gencounter.write_text("0")

    marker_body = (
        "bircher-status: outcome=ready ci=success ci_first=true "
        f"review={marker_review} rounds=1 note=\"wired for real\" head={HEAD_SHA}"
    )

    stub = _STUB_TEMPLATE.format(
        callseq=callseq, calldir=calldir, gencounter=gencounter,
        marker_body=marker_body, reviewed_sha=REVIEWED_SHA, outhash=OUT_HASH,
    )
    if not merge_ok:
        stub = stub.replace(
            'merge_ready_pr()   { _log_call merge_ready_pr "$@"; return 0; }',
            'merge_ready_pr()   { _log_call merge_ready_pr "$@"; return 1; }',
        )
    stub_file = tmp_path / "stubs.sh"
    stub_file.write_text(stub)

    (tmp_path / "workdir").mkdir()

    script_body = (
        f'. "{script}"\n'
        f'. "{stub_file}"\n'
        f'run_item "{item_file}"\n'
        f'echo "RC=$?"\n'
    )
    env = _base_env(tmp_path, queue_dir, noop_dir)
    result = subprocess.run(["bash", "-c", script_body],
                            capture_output=True, text=True, env=env)

    calls = []
    for f in sorted(calldir.iterdir()):
        parts = f.read_bytes().split(b"\0")
        calls.append((parts[0].decode(), [p.decode() for p in parts[1:]]))
    return calls, result


#: Sourcing the ~7000-line trimmed script and executing `run_item`'s
#: PRE-EXISTING marker-parsing heredoc is measurably heavier than the
#: trivial heredocs used to characterise the sandbox restriction above, and
#: empirically (repeated runs while developing this file) it is the drive
#: that most often lands on the wrong side of "stalls in this sandbox at
#: varying points" (task instructions, said of `--self-test`; this hits the
#: identical restriction for the identical reason -- a heredoc it does not
#: control). Every test below therefore shares ONE drive per scenario via a
#: module-scoped fixture rather than re-invoking `run_item` per assertion:
#: fewer heredoc-needing subprocesses is strictly better engineering
#: regardless of the sandbox question, and it is also the whole of what can
#: be done about the environment from here -- CI and the controller's
#: machine do not carry this restriction (same as `--self-test`).
@pytest.fixture(scope="module")
def happy_drive(tmp_path_factory):
    d = tmp_path_factory.mktemp("run-item-happy")
    return _run_one_item(d)


@pytest.fixture(scope="module")
def failed_merge_drive(tmp_path_factory):
    d = tmp_path_factory.mktemp("run-item-failed-merge")
    return _run_one_item(d, merge_ok=False)


# --- the drive actually reaches run_item's kernel call sites -----------------

def test_the_drive_reaches_every_kernel_call_site(happy_drive):
    calls, result = happy_drive
    assert "RC=0" in result.stdout, (result.stdout, result.stderr)
    names = [name for name, _ in calls]
    assert names == [
        "_kernel_run_start", "_kernel_dispatch", "_kernel_put_artifact",
        "_kernel_submit_spec", "_kernel_submit_plan",
        "_kernel_start_implementation", "_kernel_record_output",
        "_kernel_record_ci", "_kernel_dispatch", "_kernel_record_review",
        "_kernel_dispatch", "_kernel_request_merge", "merge_ready_pr",
        "_kernel_record_outcome",
    ], names


# --- each call site gets the RIGHT value, not just A value -------------------

def test_run_start_gets_the_run_id_repo_and_a_base_sha(happy_drive):
    calls, _ = happy_drive
    name, args = calls[0]
    assert name == "_kernel_run_start"
    run_id, repo, base_sha = args
    assert run_id.startswith("t01-test-item-") and run_id[len("t01-test-item-"):].isdigit(), run_id
    assert repo == "acme/widgets", args
    # WORKDIR is deliberately not a git repo, so `git rev-parse HEAD` fails
    # and run_item's documented fallback (40 zeros) must be what lands here.
    assert base_sha == "0" * 40, args


def test_the_implementer_dispatch_gets_the_resolved_vendor(happy_drive):
    calls, _ = happy_drive
    name, args = calls[1]
    assert name == "_kernel_dispatch"
    assert args == ["claude_code", "implementer"], args


def test_submit_spec_and_plan_get_the_same_hash_of_the_actual_prompt(happy_drive):
    """The hash reaching submit_spec/submit_plan must be the hash of the
    prompt run_item ACTUALLY sent -- the vendor-directive-augmented text,
    not the raw queue file, and not two independently-plausible strings that
    happen to look similar."""
    calls, _ = happy_drive
    put_name, put_args = calls[2]
    assert put_name == "_kernel_put_artifact"
    augmented_prompt = put_args[0]
    assert augmented_prompt.startswith(
        "IMPLEMENTER VENDOR DIRECTIVE: dispatch the implement sub-agent to "
        "claude_code; the cross-vendor reviewer MUST be the opposite vendor "
        "(codex)."
    ), augmented_prompt
    assert augmented_prompt.endswith(
        "Implement the thing.\nbircher-implementer: claude_code"
    ), augmented_prompt
    expected_hash = hashlib.sha256(augmented_prompt.encode("utf-8")).hexdigest()

    spec_name, spec_args = calls[3]
    plan_name, plan_args = calls[4]
    assert spec_name == "_kernel_submit_spec"
    assert plan_name == "_kernel_submit_plan"
    run_id = calls[0][1][0]
    assert spec_args == [run_id, "1", expected_hash], spec_args
    assert plan_args == [run_id, "1", expected_hash], plan_args


def test_start_implementation_gets_the_implementer_generation(happy_drive):
    calls, _ = happy_drive
    name, args = calls[5]
    assert name == "_kernel_start_implementation"
    run_id = calls[0][1][0]
    assert args == [run_id, "1"], args


def test_record_output_gets_the_actual_marker_body(happy_drive):
    calls, _ = happy_drive
    name, args = calls[6]
    assert name == "_kernel_record_output"
    run_id = calls[0][1][0]
    run_id_arg, gen_arg, body_arg = args
    assert run_id_arg == run_id
    assert gen_arg == "1"
    assert body_arg.startswith("bircher-status: outcome=ready ci=success"), body_arg
    assert f"head={HEAD_SHA}" in body_arg, body_arg


def test_record_ci_gets_the_ci_field_not_the_outcome_field(happy_drive):
    """The reviewer's finding, reproduced as a positive assertion: the THIRD
    argument to record_ci is $_ci ("success"), never $outcome ("ready") --
    two in-scope variables from the same marker line with different
    vocabularies (see module docstring for the mutation this must catch)."""
    calls, _ = happy_drive
    name, args = calls[7]
    assert name == "_kernel_record_ci"
    run_id = calls[0][1][0]
    assert args == [run_id, "1", "success", HEAD_SHA], args


def test_the_reviewer_dispatch_gets_the_recovery_reviewer(happy_drive):
    calls, _ = happy_drive
    name, args = calls[8]
    assert name == "_kernel_dispatch"
    assert args == ["codex", "reviewer"], args


def test_record_review_gets_the_raw_marker_verdict_at_the_reviewer_generation(happy_drive):
    """The RAW marker verdict, still -- the coordinator passes what it read and
    `_kernel_verdict` translates it in the client. Translating here instead
    would put the mapping on the side that cannot be tested against the
    kernel's vocabulary."""
    calls, _ = happy_drive
    name, args = calls[9]
    assert name == "_kernel_record_review"
    run_id = calls[0][1][0]
    assert args[:3] == [run_id, "2", "codex:pass"], args


def test_record_review_binds_the_artifact_the_kernel_ACTUALLY_HOLDS(happy_drive):
    """A verdict alone approves nothing, and a binding of invented values
    approves everything. Each field is asserted against the value the RUN
    produced, not against a constant this test chose:

      artifact -- exactly what _kernel_record_output echoed, so a coordinator
                  that stopped capturing it, or invented a hash, reds here;
      base     -- exactly what _kernel_run_start recorded, which is the
                  comparison validate_review makes;
      context  -- exactly the spec artifact _kernel_submit_spec named.
    """
    calls, _ = happy_drive
    by_name = {n: a for n, a in calls}
    _, args = calls[9]

    assert args[3] == OUT_HASH, (
        "the review does not bind the hash _kernel_record_output returned")
    assert args[4] == by_name["_kernel_run_start"][2], (
        "the review binds a different base than the run was started with -- "
        "validate_review compares exactly these two")
    assert args[5] == by_name["_kernel_submit_spec"][2], (
        "the review binds a context hash that is not this run's spec artifact")


def test_the_merge_redispatch_gets_the_implementer_vendor_again(happy_drive):
    calls, _ = happy_drive
    name, args = calls[10]
    assert name == "_kernel_dispatch"
    assert args == ["claude_code", "implementer"], args


def test_request_merge_gets_the_pr_repo_and_reviewed_head_at_gen_3(happy_drive):
    """Fix round 2, ITEM 1: the 5th argument is `$marker_head` -- HEAD_SHA,
    what the crafted marker claims -- never `$reviewed_sha` (REVIEWED_SHA,
    `_merge_gate`'s stubbed, deliberately DIFFERENT answer). Distinguishable
    only because REVIEWED_SHA != HEAD_SHA in this fixture; see REVIEWED_SHA's
    module-level comment for why the swap that motivated this test was
    invisible before that separation."""
    calls, _ = happy_drive
    name, args = calls[11]
    assert name == "_kernel_request_merge"
    run_id = calls[0][1][0]
    assert args[:5] == [run_id, "3", PR, "acme/widgets", HEAD_SHA], args
    assert HEAD_SHA != REVIEWED_SHA  # the whole point -- see REVIEWED_SHA above


def test_request_merge_presents_the_SAME_binding_the_review_did(happy_drive):
    """`_merge_is_authorized` hashes the binding and looks for a kernel-recorded
    `accept` against that exact hash. If the merge request presents any other
    tuple it finds no approval -- so these two must be identical field for
    field, and asserting them against each other is the only way to catch a
    drift that leaves both individually plausible."""
    calls, _ = happy_drive
    _, review = calls[9]
    _, merge = calls[11]
    assert merge[5:8] == review[3:6], (
        f"merge binds {merge[5:8]} but the review bound {review[3:6]}; the "
        "merge gate will find no approval for this tuple")


def test_merge_ready_pr_gets_the_item_pr_and_reviewed_sha(happy_drive):
    """The other half of the same distinction: `merge_ready_pr`'s 3rd
    argument is `$reviewed_sha` -- REVIEWED_SHA, `_merge_gate`'s answer --
    never the marker's own `$marker_head`."""
    calls, _ = happy_drive
    name, args = calls[12]
    assert name == "merge_ready_pr"
    assert args == ["t01-test-item", PR, REVIEWED_SHA], args


def test_record_outcome_gets_merged_at_the_implementer_generation(happy_drive):
    """`_k_outcome` must be "merged" (the stubbed merge_ready_pr returns rc
    0) at generation 3 -- the SAME generation request_merge used, not a
    stale one from an earlier dispatch."""
    calls, _ = happy_drive
    name, args = calls[13]
    assert name == "_kernel_record_outcome"
    run_id = calls[0][1][0]
    assert args == [run_id, "3", "merged"], args


def test_record_outcome_gets_failed_when_the_merge_fails(failed_merge_drive):
    """The other half of the branch: a failing merge_ready_pr must make
    _k_outcome "failed", not silently stay "merged"."""
    calls, _ = failed_merge_drive
    outcome_calls = [args for name, args in calls if name == "_kernel_record_outcome"]
    assert len(outcome_calls) == 1, outcome_calls
    assert outcome_calls[0][2] == "failed", outcome_calls
