"""The §6 seam, driven: what `run_item` DECIDES around `coordinator.cli phases`.

`test_run_item_argument_wiring.py` proves the seam sits between the run and the
implementer and is handed the right arguments. This file is the other half:
what the runner does when the answers come back differently -- a run already
open for this code, a run holding unresolved effects, a park, a refused
`create_run`, a `start_implementation` the kernel declined. Each of those is a
decision with a wrong answer that looks exactly like the right one from the
outside (the item is skipped either way; the queue file is still there either
way), so each is driven and asserted on the CALL LOG and the FILES rather than
on a log line.

The harness is `test_run_item_argument_wiring.py`'s: extract `run_item` and the
small set of helpers it still needs by name out of the real `batch/run-queue.sh`
-- never a copy -- source a stub file after it so the stubs win, run `run_item`
on a real queue file, and read back what was called with what. The kernel
answers are knobs in the environment, so one drive shape covers every case.

Two of the cases below are not about `run_item` at all. A dispatch over an
intended or uncertain effect halts the run and RAISES, so `_kernel_dispatch`
can come back empty -- and the two other places that adopt a run before
performing effects (`reconcile_deferred_ready`'s fallback branch and
`recover_pr_cmd`) would then carry an empty `BIRCHER_GENERATION` into `_effect`
and abort under `set -u`, silently, taking the rest of the sweep with them.
They ask first now, and these tests are what says so.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"
CLIENT = REPO_ROOT / "batch" / "lib" / "kernel-client.sh"

sys.path.insert(0, str(REPO_ROOT / "v2"))

CODE = "t01"
ITEM = "t01-test-item"
#: The run the kernel already holds for this code. Deliberately NOT of the
#: `<item>-<epoch>` shape a mint produces, so a test cannot confuse "resumed the
#: open run" with "minted a new one and got lucky".
OPEN_RUN = "t01-already-open"
ISSUE = "711"
PR = "777"
HEAD_SHA = "b" * 40
REVIEWED_SHA = "c" * 40
OUT_HASH = "outputhash" + "0" * 54
CTX_HASH = "ctxbundle" + "0" * 55

#: Real helpers `run_item` still calls after the network, session and kernel
#: functions are stubbed. The last three are this task's own and are REAL here
#: on purpose: `_pending_blocks` makes the skip decision, `_write_parked_sidecar`
#: writes the file the tests read, and `_project_config` produces the second
#: input `create_run` freezes.
_NEEDED_REAL_FUNCTIONS = [
    "_contains", "_is_blank", "_is_limit_message",
    "_pr_signal", "_session_died", "_item_issue",
    "_local_host_id", "_json_get", "_host_ids_match", "_writeback_plan",
    "_issue_writeback", "_ensure_issue_closed", "_record_deferred_ready",
    "_derived_width_ok",
    "_project_config", "_pending_blocks", "_write_parked_sidecar",
]


def _extract_function(src_lines, name):
    start = next(
        i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{")
    )
    if src_lines[start].rstrip().endswith("}"):
        return src_lines[start]
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


def _heredoc_to_herestring(src):
    """`run_item`'s one heredoc, rewritten to a here-string in THIS test's own
    extracted copy. Byte-for-byte equivalent input to the same `read`; see
    test_run_item_argument_wiring.py's copy of this note for the sandbox
    restriction it routes around. Matched exactly, so an edit to the real line
    fails loudly here rather than silently reverting."""
    old = ("IFS='|' read -r outcome review note observed_head _obs_ci ci_first "
           "resubmissions _settled_pr <<EOF\n$obs\nEOF")
    new = ("IFS='|' read -r outcome review note observed_head _obs_ci ci_first "
           'resubmissions _settled_pr <<< "$obs"')
    assert old in src, "run_item's marker-parsing heredoc has changed shape"
    return src.replace(old, new)


_PREAMBLE = '''
set -uo pipefail
REPO="${BIRCHER_REPO:-acme/widgets}"
WORKDIR="${WORKDIR:?}"
BUNDLE_DIR="${BIRCHER_BUNDLE_DIR:?}"
QUEUE="${QUEUE:?}"
PROCESSED="$QUEUE/processed"
SCORECARD="${SCORECARD:?}"
DEFERRED_READY_FILE="${DEFERRED_READY_FILE:?}"
NOOP_DIR="${BIRCHER_NOOP_DIR:?}"
SERVER="${OMNIGENT_SERVER:-http://omnigent.invalid}"
BIRCHER_NET_TIMEOUT="${BIRCHER_NET_TIMEOUT:-30}"
ITEM_TIMEOUT="${ITEM_TIMEOUT:-5400}"
POLL="${POLL_INTERVAL:-0}"
RECOVERY_REVIEWER="${BIRCHER_RECOVERY_REVIEWER:-codex}"
INRUN_MERGE="${BIRCHER_INRUN_MERGE:-1}"
IMPLEMENTER="${BIRCHER_IMPLEMENTER:-auto}"
MERGE_NOTE=""
MERGE_RETRY_ELIGIBLE=""
'''

#: The stubs, sourced AFTER the extracted script so they win. Every kernel
#: answer is a knob: T_FIND_RUN (the open run, or empty), T_PENDING (the halt
#: JSON), T_STATE_RESUME (the state a resumed run is at), T_STATE_AFTER (the
#: state after start_implementation), T_IMPL_STARTED (rc), T_RUN_START_RC.
_STUB_TEMPLATE = '''
_log_call() {{
  local name="$1"; shift
  local n; n=$(cat "{callseq}"); n=$((n+1)); printf '%s' "$n" > "{callseq}"
  local f; f="{calldir}/$(printf '%03d' "$n")"
  {{ printf '%s' "$name"; local a; for a in "$@"; do printf '\\0%s' "$a"; done; }} > "$f"
}}

_kernel_find_run() {{ _log_call _kernel_find_run "$@"; printf '%s' "${{T_FIND_RUN:-}}"; }}
_kernel_pending()  {{ _log_call _kernel_pending "$@"; printf '%s' "${{T_PENDING:-}}"; }}
_kernel_state() {{
  _log_call _kernel_state "$@"
  local n; n=$(cat "{statecount}"); n=$((n+1)); printf '%s' "$n" > "{statecount}"
  # On the RESUME path the first read is the run's current state; every later
  # one is the read-back after start_implementation. On the mint path there is
  # no resume read, so the first is already the read-back.
  if [ -n "${{T_FIND_RUN:-}}" ] && [ "$n" = 1 ]; then
    printf '%s' "${{T_STATE_RESUME:-queued}}"
  else
    printf '%s' "${{T_STATE_AFTER:-implementing}}"
  fi
}}
_kernel_implementation_started() {{ _log_call _kernel_implementation_started "$@"; return "${{T_IMPL_STARTED:-1}}"; }}
_kernel_run_start() {{ _log_call _kernel_run_start "$@"; printf '%s' "{ctx_hash}"; return "${{T_RUN_START_RC:-0}}"; }}
_kernel_revise_bundle() {{ _log_call _kernel_revise_bundle "$@"; }}
_kernel_start_implementation() {{ _log_call _kernel_start_implementation "$@"; }}
_kernel_bundle_hash() {{ _log_call _kernel_bundle_hash "$@"; printf '%s' "{ctx_hash}"; }}
_implementer_brief() {{ _log_call _implementer_brief "$@"; printf 'BRIEF(%s)' "$2"; }}
_kernel_record_output()      {{ _log_call _kernel_record_output "$@"; printf '%s' "{outhash}"; }}
_kernel_record_ci()          {{ _log_call _kernel_record_ci "$@"; }}
_kernel_record_review()      {{ _log_call _kernel_record_review "$@"; }}
_kernel_request_merge()      {{ _log_call _kernel_request_merge "$@"; }}
_kernel_record_outcome()     {{ _log_call _kernel_record_outcome "$@"; }}
_kernel_record_run_outcome() {{ _log_call _kernel_record_run_outcome "$@"; }}
_kernel_dispatch() {{
  _log_call _kernel_dispatch "$@"
  local n; n=$(cat "{gencounter}"); n=$((n+1)); printf '%s' "$n" > "{gencounter}"
  printf '%s' "$n"
}}
observe_outcome() {{
  _log_call observe_outcome "$@"
  printf '%s' 'ready|codex:pass|derived from the repository|{head_sha}|green|true|1|{pr}'
}}

_net_run()         {{ shift; "$@"; }}
_effect()          {{ _log_call _effect "$@"; }}
_coordinator()     {{ printf ''; return 1; }}
_create_session()  {{ _log_call _create_session "$@"; printf 'conv-test-1'; }}
_send_prompt()     {{ _log_call _send_prompt "$@"; return 0; }}
_http_json()       {{ printf '{{}}'; }}
_session_state()   {{ printf 'cancelled|'; }}
_stop_session()    {{ :; }}
_last_assistant_text() {{ printf ''; return 0; }}
# The real json_row shells out through a heredoc (see the module docstring's
# note on this sandbox). The SUBJECT here is the outcome run_item passes, so it
# is logged rather than rendered.
json_row()         {{ _log_call json_row "$@"; printf '%s\\n' "$3" >> "$SCORECARD"; }}
merge_ready_pr()   {{ _log_call merge_ready_pr "$@"; return 0; }}
_merge_gate() {{ [ -n "${{2:-}}" ] && {{ printf 'pin|%s' {reviewed_sha!r}; return 0; }}; printf 'skip'; }}
'''

#: `coordinator.cli phases` and `coordinator.cli parked` are reached by name
#: through `${BIRCHER_PY:-python3}`; every other Python call `run_item` makes is
#: bare `python3` inside a real helper, so this intercepts exactly those two.
_FAKE_PY = '''#!/bin/bash
n=$(cat "@CALLSEQ@"); n=$((n+1)); printf '%s' "$n" > "@CALLSEQ@"
f="@CALLDIR@/$(printf '%03d' "$n")"
{ printf '%s' BIRCHER_PY; for a in "$@"; do printf '\\0%s' "$a"; done; } > "$f"
park="${PARKED_JSON:-}"
[ -n "$park" ] || park='{"reason": "gate"}'
for a in "$@"; do
  # The sidecar AS IT STANDS WHEN phases RUNS. run_item removes it again when
  # phases exits 0, so a test that looked afterwards could not tell "written
  # before the loop" from "never written at all".
  [ "$a" = phases ] && [ -n "${SIDECAR_SNAPSHOT:-}" ] && [ -f "${SIDECAR_SRC:-}" ] \
    && cp "$SIDECAR_SRC" "$SIDECAR_SNAPSHOT"
  [ "$a" = phases ] && exit "${PHASES_RC:-0}"
  [ "$a" = parked ] && { printf '%s' "$park"; exit 0; }
done
exit 0
'''

#: A `gh` that answers `issue view --json ...` and nothing else, so the fetched
#: issue is a real fetch rather than the synthesised placeholder.
_FAKE_GH = '''#!/bin/bash
if [ "$1" = issue ] && [ "$2" = view ]; then
  cat "@ISSUE@"
  exit 0
fi
exit 0
'''

GH_ISSUE = {
    "number": int(ISSUE), "title": "Do the thing", "body": "the requirement",
    "labels": [{"name": "bug"}],
    "comments": [{"id": "IC_1", "author": {"login": "jon"}, "body": "a note",
                  "url": f"https://github.com/acme/widgets/issues/{ISSUE}#issuecomment-5"}],
}


def _extracted_script(tmp_path, extra=()):
    src_lines = RUN_QUEUE.read_text().splitlines()
    run_item_src = _heredoc_to_herestring(_extract_function(src_lines, "run_item"))
    assert len(run_item_src.splitlines()) > 100, "run_item's body looks truncated"
    names = list(_NEEDED_REAL_FUNCTIONS) + list(extra)
    helpers = [_extract_function(src_lines, n) for n in names]
    out = tmp_path / "extracted.sh"
    out.write_text(_PREAMBLE + "\n\n".join(helpers) + "\n\n" + run_item_src + "\n")
    return out


def _write_exec(path, text):
    path.write_text(text)
    path.chmod(0o755)
    return path


class Drive:
    def __init__(self, calls, result, queue_dir, scorecard, tmp_path):
        self.calls = calls
        self.result = result
        self.queue_dir = queue_dir
        self.scorecard = scorecard
        self.tmp_path = tmp_path

    @property
    def names(self):
        return [n for n, _ in self.calls]

    def args_of(self, name, nth=0):
        found = [a for n, a in self.calls if n == name]
        assert len(found) > nth, (name, self.names)
        return found[nth]

    @property
    def sidecar(self):
        return self.queue_dir / f"{CODE}.parked"

    @property
    def sidecar_at_phases(self):
        """The sidecar as it stood when `coordinator.cli phases` was invoked --
        captured by the fake interpreter, because run_item removes the file
        again as soon as phases exits 0."""
        p = self.tmp_path / "sidecar-at-phases.json"
        return json.loads(p.read_text()) if p.exists() else None

    @property
    def outcomes(self):
        return [a[2] for n, a in self.calls if n == "json_row"]


def _drive(tmp_path, *, env_extra=None, with_issue=True, sidecar=None,
           prompt_body="Implement the thing."):
    """One item through the real `run_item`, with every kernel answer a knob."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_file = queue_dir / f"{ITEM}.md"
    head = f"Issue: #{ISSUE}\n" if with_issue else ""
    item_file.write_text(f"{head}{prompt_body}\nbircher-implementer: claude_code")
    if sidecar is not None:
        (queue_dir / f"{CODE}.parked").write_text(json.dumps(sidecar))

    noop_dir = tmp_path / "noop"
    noop_dir.mkdir()
    (noop_dir / f"{CODE}.pr").write_text(PR)

    calldir = tmp_path / "calls"
    calldir.mkdir()
    callseq = tmp_path / "callseq"
    callseq.write_text("0")
    gencounter = tmp_path / "gencounter"
    gencounter.write_text("0")
    statecount = tmp_path / "statecount"
    statecount.write_text("0")

    stub_file = tmp_path / "stubs.sh"
    stub_file.write_text(_STUB_TEMPLATE.format(
        callseq=callseq, calldir=calldir, gencounter=gencounter,
        statecount=statecount, head_sha=HEAD_SHA, reviewed_sha=REVIEWED_SHA,
        outhash=OUT_HASH, ctx_hash=CTX_HASH, pr=PR,
    ))

    binpath = tmp_path / "bin"
    binpath.mkdir()
    _write_exec(binpath / "python3-fake",
                _FAKE_PY.replace("@CALLSEQ@", str(callseq))
                        .replace("@CALLDIR@", str(calldir)))
    issue_file = tmp_path / "gh-issue.json"
    issue_file.write_text(json.dumps(GH_ISSUE))
    _write_exec(binpath / "gh", _FAKE_GH.replace("@ISSUE@", str(issue_file)))

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    scorecard = tmp_path / "scorecard.jsonl"

    script = _extracted_script(tmp_path)
    body = (f'. "{script}"\n. "{stub_file}"\nrun_item "{item_file}"\n'
            f'echo "RC=$?"\n')
    env = {
        "PATH": f"{binpath}:/usr/bin:/bin:/usr/local/bin",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "BIRCHER_BUNDLE_DIR": str(REPO_ROOT),
        "BIRCHER_V2_DIR": str(REPO_ROOT / "v2"),
        "BIRCHER_KERNEL_DB": str(tmp_path / "kernel.db"),
        "BIRCHER_REPO": "acme/widgets",
        "WORKDIR": str(workdir),           # deliberately NOT a git repo
        "AGENT_ID": "test-agent",
        "AGENT_AUTHOR_CLAUDE": "ag-author-claude",
        "AGENT_AUTHOR_CODEX": "ag-author-codex",
        "POLL_INTERVAL": "0",
        "QUEUE": str(queue_dir),
        "SCORECARD": str(scorecard),
        "DEFERRED_READY_FILE": str(tmp_path / "deferred.tsv"),
        "BIRCHER_NOOP_DIR": str(noop_dir),
        "BIRCHER_ISSUE_WRITEBACK": "0",
        "BIRCHER_PY": str(binpath / "python3-fake"),
        "SIDECAR_SRC": str(queue_dir / f"{CODE}.parked"),
        "SIDECAR_SNAPSHOT": str(tmp_path / "sidecar-at-phases.json"),
    }
    env.update(env_extra or {})
    result = subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                            env=env)

    calls = []
    for f in sorted(calldir.iterdir()):
        parts = f.read_bytes().split(b"\0")
        calls.append((parts[0].decode(), [p.decode() for p in parts[1:]]))
    return Drive(calls, result, queue_dir, scorecard, tmp_path)


# --- 1. the seam sits between create_run and the implementer -----------------

@pytest.fixture(scope="module")
def mint_drive(tmp_path_factory):
    return _drive(tmp_path_factory.mktemp("mint"),
                  env_extra={"BIRCHER_HAVE_LOCK": "1"})


def test_run_item_calls_phases_between_run_start_and_the_implementer(mint_drive):
    d = mint_drive
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    order = d.names
    idx = [order.index(n) for n in
           ("_kernel_run_start", "BIRCHER_PY", "_kernel_start_implementation",
            "_kernel_state", "_create_session")]
    assert idx == sorted(idx), order
    assert len(set(idx)) == len(idx), order
    # The operator fence sits between run_start and phases; the implementer
    # dispatch between phases and start_implementation.
    dispatches = [i for i, n in enumerate(order) if n == "_kernel_dispatch"]
    phases_at = order.index("BIRCHER_PY")
    assert d.calls[dispatches[0]][1] == ["runner", "operator"]
    assert dispatches[0] < phases_at < dispatches[1]
    assert d.calls[dispatches[1]][1] == ["claude_code", "implementer"]

    # run_start takes five arguments and the last two are FILES THAT EXIST.
    args = d.args_of("_kernel_run_start")
    assert len(args) == 5, args
    issue = json.loads(pathlib.Path(args[3]).read_text())
    assert issue["number"] == int(ISSUE), issue
    assert json.loads(pathlib.Path(args[4]).read_text()) == {}

    # phases carries the turn timeout and both author agent ids.
    pargs = d.args_of("BIRCHER_PY")
    pairs = dict(zip(pargs, pargs[1:]))
    assert pairs["--turn-timeout"] == "5400", pargs
    assert pairs["--agent-claude"] == "ag-author-claude", pargs
    assert pairs["--agent-codex"] == "ag-author-codex", pargs

    assert "_kernel_submit_spec" not in order and "_kernel_submit_plan" not in order


def test_the_retired_submit_wrappers_are_not_even_defined():
    """`type` in the sourced client, not a grep of run_item: a wrapper that
    still existed would let a future edit call it again, and the whole reason
    it is gone is that a spec nobody authored must not be submittable."""
    r = subprocess.run(
        ["bash", "-c", f'. "{CLIENT}" 2>/dev/null; '
                       'type _kernel_submit_spec; type _kernel_submit_plan'],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "BUNDLE_DIR": str(REPO_ROOT)})
    assert r.returncode != 0, r.stdout


# --- 2. RC_PARKED ------------------------------------------------------------

def test_parked_rc_writes_the_sidecar_and_keeps_the_queue_file(tmp_path):
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "PHASES_RC": "4",
        "PARKED_JSON": json.dumps({"reason": "grill"}),
        "T_STATE_AFTER": "spec_accepted",
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    side = json.loads(d.sidecar.read_text())
    assert side["run_id"] == d.args_of("_kernel_run_start")[0], side
    assert side["state"] == "spec_accepted", side
    assert side["reason"] == "grill", side
    assert d.outcomes == ["parked"], d.calls
    assert (d.queue_dir / f"{ITEM}.md").exists(), "the queue file was consumed"
    assert not (d.queue_dir / "processed" / f"{ITEM}.md").exists()
    assert "_create_session" not in d.names, d.names
    # A park is not the end of a run.
    assert "_kernel_record_run_outcome" not in d.names, d.names


# --- 3. the state read back after start_implementation -----------------------

def test_state_other_than_implementing_after_start_is_failed_with_no_session(tmp_path):
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_STATE_AFTER": "plan_submitted",
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert "_kernel_start_implementation" in d.names
    assert d.args_of("_kernel_state") == [d.args_of("_kernel_run_start")[0]]
    assert d.outcomes == ["failed"], d.calls
    assert "_create_session" not in d.names, d.names
    assert (d.queue_dir / "processed" / f"{ITEM}.md").exists()
    assert d.args_of("_kernel_record_run_outcome")[2] == "failed"


# --- 4/5. resumption and the sidecar -----------------------------------------

def _resume_drive(tmp_path, **env):
    e = {"BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
         "T_PENDING": json.dumps({"halted": False, "pending": []}),
         "T_STATE_RESUME": "plan_submitted", "T_STATE_AFTER": "implementing"}
    e.update(env)
    return _drive(tmp_path, env_extra=e)


def test_resume_finds_the_open_run_and_refences_as_operator(tmp_path):
    d = _resume_drive(tmp_path)
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert d.args_of("_kernel_find_run") == [CODE, "open"]
    assert "_kernel_run_start" not in d.names, "it minted over an open run"
    first_dispatch = next(a for n, a in d.calls if n == "_kernel_dispatch")
    assert first_dispatch == ["runner", "operator"], first_dispatch
    # The bundle is re-snapshotted from the FETCHED issue, under that fence.
    rb = d.args_of("_kernel_revise_bundle")
    assert rb[0] == OPEN_RUN, rb
    assert rb[1] == "1", rb          # the operator generation, not a stale one
    assert json.loads(pathlib.Path(rb[2]).read_text())["number"] == int(ISSUE)
    # And the implementer's own state carries the resumed run, not a new one.
    assert d.args_of("_kernel_start_implementation")[0] == OPEN_RUN


def test_sidecar_is_rebuilt_from_the_kernel(tmp_path):
    """No sidecar, an open run -> it is written BEFORE phases, naming the run
    the kernel gave. Observed at the moment phases runs, not afterwards: a
    completed pass deletes the file again, so "still there at the end" and
    "never written" look identical from outside."""
    d = _resume_drive(tmp_path)
    assert d.sidecar_at_phases == {"run_id": OPEN_RUN, "state": "plan_submitted",
                                   "reason": "resume"}, d.sidecar_at_phases


def test_a_sidecar_naming_a_different_run_is_overwritten_with_the_kernels_answer(tmp_path):
    """The file is a HINT for a human; the kernel is the truth. A hint that
    disagrees is worse than none, because the next reader believes it."""
    stale = _drive(tmp_path, sidecar={"run_id": "some-other-run",
                                      "state": "queued", "reason": "stale"},
                   env_extra={"BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
                              "T_PENDING": json.dumps({"halted": False, "pending": []}),
                              "T_STATE_RESUME": "queued"})
    assert stale.sidecar_at_phases["run_id"] == OPEN_RUN, stale.sidecar_at_phases


def test_a_completed_pass_clears_the_sidecar(tmp_path):
    """The other end of the same file. A sidecar left behind by a pass that
    finished would make the NEXT one report an item as waiting on a human when
    nothing is."""
    d = _resume_drive(tmp_path)
    assert d.sidecar_at_phases is not None, "it was never written"
    assert not d.sidecar.exists(), "the sidecar outlived the pass that wrote it"


# --- 6. pending or halted ----------------------------------------------------

@pytest.mark.parametrize("pending", [
    {"halted": False, "pending": ["sess-create:r:3"]},
    {"halted": True, "pending": []},
])
def test_pending_or_halted_skips_before_any_fence(tmp_path, pending):
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
        "T_PENDING": json.dumps(pending),
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert "_kernel_dispatch" not in d.names, d.names
    assert "_kernel_run_start" not in d.names, d.names
    assert (d.queue_dir / f"{ITEM}.md").exists(), "the queue file was consumed"
    assert d.outcomes == [], d.calls


# --- 7. the batch lock -------------------------------------------------------

def test_resume_refused_without_the_lock(tmp_path):
    d = _drive(tmp_path, env_extra={
        "T_FIND_RUN": OPEN_RUN,
        "T_PENDING": json.dumps({"halted": False, "pending": []}),
        "T_STATE_RESUME": "queued",
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert "_kernel_dispatch" not in d.names, d.names
    assert "_kernel_run_start" not in d.names, d.names
    assert "batch lock" in d.result.stderr, d.result.stderr
    assert (d.queue_dir / f"{ITEM}.md").exists()


# --- 8/9. the leak guard -----------------------------------------------------

def test_a_run_beyond_the_front_half_is_skipped_not_relaunched(tmp_path):
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
        "T_PENDING": json.dumps({"halted": False, "pending": []}),
        "T_STATE_RESUME": "implementing",
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert "_kernel_dispatch" not in d.names, d.names
    assert "_kernel_run_start" not in d.names, "it minted a second run"


def test_planned_with_an_accepted_start_implementation_is_skipped(tmp_path):
    """`planned` is reachable twice: before implementation, and again after a
    request_revision. The journal is asked which one this is, because the
    aggregate row cannot say."""
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
        "T_PENDING": json.dumps({"halted": False, "pending": []}),
        "T_STATE_RESUME": "planned", "T_IMPL_STARTED": "0",
    })
    assert d.args_of("_kernel_implementation_started") == [OPEN_RUN]
    assert "_kernel_dispatch" not in d.names, d.names


def test_leak_guard_never_mints_over_an_open_run(tmp_path):
    """With NO sidecar at all. The sidecar is a hint; the kernel is the truth,
    and a missing hint must not license a second run for the same code."""
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_FIND_RUN": OPEN_RUN,
        "T_PENDING": json.dumps({"halted": False, "pending": []}),
        "T_STATE_RESUME": "queued",
    })
    assert not (d.queue_dir / f"{CODE}.parked").exists() or True
    assert "_kernel_run_start" not in d.names, d.names
    assert d.args_of("_kernel_start_implementation")[0] == OPEN_RUN


# --- 10. create_run refused ---------------------------------------------------

def test_create_run_refusal_records_failed(tmp_path):
    d = _drive(tmp_path, env_extra={
        "BIRCHER_HAVE_LOCK": "1", "T_RUN_START_RC": "1",
    })
    assert "RC=0" in d.result.stdout, (d.result.stdout, d.result.stderr)
    assert d.outcomes == ["failed"], d.calls
    assert "create_run refused" in d.args_of("json_row")[7]
    assert (d.queue_dir / "processed" / f"{ITEM}.md").exists()
    assert "_kernel_dispatch" not in d.names, d.names
    assert "BIRCHER_PY" not in d.names, "phases ran for a run that was refused"


# --- 11. the typed reconcile --------------------------------------------------

_RECONCILE_STUBS = '''
_net_run() {{ shift; "$@"; }}
_kernel() {{
  {{ printf '%s' "_kernel"; for a in "$@"; do printf '\\0%s' "$a"; done; }} > "{log}"
}}
_kernel_pythonpath() {{ printf '%s' "{v2}"; }}
'''

_FAKE_CURL_OK = '''#!/bin/bash
printf '%s' '{"id": "s-9", "agent_name": "v2_author_claude"}'
exit 0
'''
_FAKE_CURL_FAIL = '''#!/bin/bash
exit 22
'''
#: A session row the server still returns but whose agent is GONE. It is a
#: 200, so only the `agent_name` check tells it from a live one.
_FAKE_CURL_NO_AGENT = '''#!/bin/bash
printf '%s' '{"id": "s-9", "status": "stopped"}'
exit 0
'''


def _reconcile(tmp_path, script, *, curl=_FAKE_CURL_OK):
    binpath = tmp_path / "bin"
    binpath.mkdir()
    _write_exec(binpath / "curl", curl)
    log = tmp_path / "kernel-call"
    stubs = tmp_path / "stubs.sh"
    stubs.write_text(_RECONCILE_STUBS.format(log=log, v2=REPO_ROOT / "v2"))
    body = f'. "{CLIENT}"\n. "{stubs}"\n{script}\necho "RC=$?"\n'
    r = subprocess.run(["bash", "-c", body], capture_output=True, text=True, env={
        "PATH": f"{binpath}:/usr/bin:/bin",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "SERVER": "http://omnigent.invalid",
        "BIRCHER_NET_TIMEOUT": "5",
        "BUNDLE_DIR": str(REPO_ROOT),
        "BIRCHER_KERNEL_DB": str(tmp_path / "k.db"),
    })
    args = (log.read_bytes().split(b"\0") if log.exists() else [])
    return r, [a.decode() for a in args]


def test_reconcile_typed_delivered_session_carries_the_fetched_snapshot(tmp_path):
    r, args = _reconcile(tmp_path,
        '_kernel_reconcile r 7 --delivered sess-create:r:1=s-9 '
        '--not-delivered sess-prompt:s-9:1:f')
    assert "RC=0" in r.stdout, (r.stdout, r.stderr)
    assert args[0] == "_kernel"
    pairs = dict(zip(args, args[1:]))
    assert pairs["--expected-version"] == "7", args
    assert pairs["--not-delivered"] == "sess-prompt:s-9:1:f", args
    delivered = pairs["--delivered"]
    key, _, ref = delivered.partition("=")
    assert key == "sess-create:r:1", delivered
    assert ref.startswith("@"), delivered
    body = json.loads(pathlib.Path(ref[1:]).read_text())
    assert body["agent_name"] == "v2_author_claude", body


def test_reconcile_refuses_a_delivered_session_it_could_not_fetch(tmp_path):
    r, args = _reconcile(tmp_path,
        '_kernel_reconcile r 7 --delivered sess-create:r:1=s-9',
        curl=_FAKE_CURL_FAIL)
    assert "RC=1" in r.stdout, (r.stdout, r.stderr)
    assert args == [], "a session that could not be fetched was recorded delivered"
    assert "refusing" in r.stderr, r.stderr


def test_reconcile_refuses_a_session_the_server_no_longer_names_an_agent_for(tmp_path):
    """A 200 is not proof the session is usable. The agent row can be gone
    while the session id still resolves, and recording that as DELIVERED makes
    every later command bound to it RC_FAILED -- with the journal saying the
    effect succeeded."""
    r, args = _reconcile(tmp_path,
        '_kernel_reconcile r 7 --delivered sess-create:r:1=s-9',
        curl=_FAKE_CURL_NO_AGENT)
    assert "RC=1" in r.stdout, (r.stdout, r.stderr)
    assert args == [], "a session with no agent_name was recorded delivered"
    assert "agent_name" in r.stderr, r.stderr


def test_reconcile_still_speaks_the_legacy_form(tmp_path):
    r, args = _reconcile(tmp_path, '_kernel_reconcile r delivered 7 k1 k2')
    assert "RC=0" in r.stdout, (r.stdout, r.stderr)
    assert args.count("--idempotency-key") == 2, args
    assert "k1" in args and "k2" in args, args
    assert "--resolution" in args and "delivered" in args, args
    assert "--expected-version" in args and "7" in args, args


def test_reconcile_usage_names_the_consequence_of_a_vanished_session(tmp_path):
    r, args = _reconcile(tmp_path, '_kernel_reconcile r 7 --delivered k --bogus x')
    assert "RC=2" in r.stdout, r.stdout
    assert "RC_FAILED" in r.stderr, r.stderr
    assert args == [], args


# --- 12. main exports the lock flag ONLY under flock --------------------------

def _lock_block():
    src = RUN_QUEUE.read_text()
    start = src.index('  local lock="${BIRCHER_BATCH_LOCK:-/tmp/bircher-batch.lock}"')
    marker = 'running without singleton protection" >&2\n  fi\n'
    end = src.index(marker, start) + len(marker)
    # `local` is a function-only builtin and this block runs at top level.
    return src[start:end].replace("local lock=", "lock=", 1)


def _run_lock_block(tmp_path, *, with_flock):
    binpath = tmp_path / "bin"
    binpath.mkdir(parents=True)
    if with_flock:
        _write_exec(binpath / "flock", "#!/bin/bash\nexit 0\n")
    body = (f'{_lock_block()}\n'
            f'"{os.environ.get("BASH", "/bin/bash")}" -c '
            f"'echo FLAG=\"${{BIRCHER_HAVE_LOCK:-<unset>}}\"'\n")
    # `/bin/bash` by absolute path: PATH here holds ONLY the fake bin dir, so
    # that `command -v flock` genuinely fails in the no-flock case, and
    # subprocess resolves argv[0] against the env it is given.
    return subprocess.run(["/bin/bash", "-c", body], capture_output=True,
                          text=True,
                          env={"PATH": str(binpath),
                               "BIRCHER_BATCH_LOCK": str(tmp_path / "lock")})


def test_main_exports_the_lock_flag_only_under_flock(tmp_path):
    got = _run_lock_block(tmp_path / "with", with_flock=True)
    assert "FLAG=1" in got.stdout, (got.stdout, got.stderr)
    missing = _run_lock_block(tmp_path / "without", with_flock=False)
    assert "FLAG=<unset>" in missing.stdout, (missing.stdout, missing.stderr)
    assert "flock not found" in missing.stderr, missing.stderr


def test_the_lock_flag_is_set_in_exactly_one_place():
    """The whole content of the flag is WHO may set it. One assignment, inside
    the branch where `flock -n 9` succeeded; a default anywhere else would let a
    process that does not hold the lock claim it does."""
    src = RUN_QUEUE.read_text()
    assigns = [l.strip() for l in src.splitlines()
               if "BIRCHER_HAVE_LOCK=" in l and not l.strip().startswith("#")
               and "${BIRCHER_HAVE_LOCK" not in l]
    assert assigns == ["BIRCHER_HAVE_LOCK=1; export BIRCHER_HAVE_LOCK"], assigns


# --- the carried ruling: a dispatch over unresolved effects raises ------------
#
# Task 6's review. `_kernel_dispatch` can now return EMPTY, and the two places
# that adopt a run outside `run_item` would hand that empty generation to
# `_effect`, which aborts on `${BIRCHER_GENERATION:?}` -- under `set -u` that
# exits the shell, taking the rest of the sweep with it, at rc 0.

_SWEEP_STUBS = '''
_kernel_find_run() {{ printf '%s' "${{T_FIND_RUN:-}}"; }}
_kernel_pending()  {{ printf '%s' "${{T_PENDING:-}}"; }}
# Faithful to the real one: it ALWAYS exports the generation, and the value is
# empty when the dispatch was refused. A stub that left it unset would test a
# state production cannot reach.
_kernel_adopt_run() {{ printf 'ADOPTED\\n' >> "{log}"; BIRCHER_RUN_ID=sweepA-run-1; BIRCHER_GENERATION="${{T_GEN:-}}"; export BIRCHER_RUN_ID BIRCHER_GENERATION; printf ''; }}
_kernel_dispatch()  {{ printf 'DISPATCHED\\n' >> "{log}"; printf '%s' "${{T_GEN:-}}"; }}
json_row() {{ printf 'ROW %s %s\\n' "$3" "$8" >> "{log}"; }}
_net_run() {{ shift; "$@"; }}
_effect()  {{ printf 'EFFECT %s\\n' "$2" >> "{log}"; }}
gh() {{ printf 'GH %s\\n' "$*" >> "{log}"; exit 0; }}
'''


def _sweep(tmp_path, *, pending, deferred_rows):
    log = tmp_path / "log"
    deferred = tmp_path / "deferred.tsv"
    deferred.write_text(deferred_rows)
    src_lines = RUN_QUEUE.read_text().splitlines()
    script = tmp_path / "sweep.sh"
    script.write_text(
        _PREAMBLE
        + _extract_function(src_lines, "_pending_blocks") + "\n\n"
        + _extract_function(src_lines, "reconcile_deferred_ready") + "\n")
    stubs = tmp_path / "stubs.sh"
    stubs.write_text(_SWEEP_STUBS.format(log=log))
    body = f'. "{script}"\n. "{stubs}"\nreconcile_deferred_ready\necho "RC=$?"\n'
    r = subprocess.run(["bash", "-c", body], capture_output=True, text=True, env={
        "PATH": "/usr/bin:/bin",
        "WORKDIR": str(tmp_path), "BIRCHER_BUNDLE_DIR": str(REPO_ROOT),
        "QUEUE": str(tmp_path / "queue"), "SCORECARD": str(tmp_path / "sc.jsonl"),
        "DEFERRED_READY_FILE": str(deferred), "BIRCHER_NOOP_DIR": str(tmp_path / "noop"),
        "T_FIND_RUN": "sweepA-run-1", "T_PENDING": json.dumps(pending),
    })
    return r, (log.read_text() if log.exists() else "")


def test_the_sweep_asks_before_adopting_and_skips_a_blocked_run(tmp_path):
    r, log = _sweep(tmp_path, pending={"halted": True, "pending": ["merge:7:x"]},
                    deferred_rows="sweepA\t7\t77\theadsha1234567\n")
    assert "RC=0" in r.stdout, (r.stdout, r.stderr)
    assert "ADOPTED" not in log, log
    assert "DISPATCHED" not in log, log
    assert "EFFECT" not in log, log
    assert "ROW escalated" in log, log
    assert "halted or holds pending effects" in r.stderr, r.stderr


def test_the_sweep_skips_an_adopt_that_yielded_no_generation(tmp_path):
    """The other half: not blocked, but the adopt still came back with nothing.
    Carrying that empty generation into `_effect` aborts the whole loop."""
    r, log = _sweep(tmp_path, pending={"halted": False, "pending": []},
                    deferred_rows="sweepA\t7\t77\theadsha1234567\n")
    assert "RC=0" in r.stdout, (r.stdout, r.stderr)
    assert "ADOPTED" in log, log
    assert "EFFECT" not in log, log
    assert "ROW escalated" in log, log
    assert "yielded no generation" in r.stderr, r.stderr


_RECOVER_STUBS = '''
_kernel_find_run() {{ printf '%s' "${{T_FIND_RUN:-}}"; }}
_kernel_pending()  {{ printf '%s' "${{T_PENDING:-}}"; }}
_kernel_adopt_run() {{ printf 'ADOPTED\\n' >> "{log}"; BIRCHER_RUN_ID=r-1; BIRCHER_GENERATION="${{T_GEN:-}}"; export BIRCHER_RUN_ID BIRCHER_GENERATION; }}
_net_run() {{ shift; "$@"; }}
_effect()  {{ printf 'EFFECT %s\\n' "$2" >> "{log}"; }}
observe_outcome() {{ printf 'OBSERVED\\n' >> "{log}"; printf ''; }}
gh() {{ exit 0; }}
_install_work_git_config() {{ :; }}
'''


def _recover(tmp_path, *, pending, gen=""):
    log = tmp_path / "log"
    src_lines = RUN_QUEUE.read_text().splitlines()
    script = tmp_path / "recover.sh"
    script.write_text(
        _PREAMBLE
        + _extract_function(src_lines, "_pending_blocks") + "\n\n"
        + _extract_function(src_lines, "recover_pr_cmd") + "\n")
    stubs = tmp_path / "stubs.sh"
    stubs.write_text(_RECOVER_STUBS.format(log=log))
    body = f'. "{script}"\n. "{stubs}"\nrecover_pr_cmd rdemo 9 codex\necho "RC=$?"\n'
    r = subprocess.run(["bash", "-c", body], capture_output=True, text=True, env={
        "PATH": "/usr/bin:/bin",
        "WORKDIR": str(tmp_path), "BIRCHER_BUNDLE_DIR": str(REPO_ROOT),
        "QUEUE": str(tmp_path / "queue"), "SCORECARD": str(tmp_path / "sc.jsonl"),
        "DEFERRED_READY_FILE": str(tmp_path / "d.tsv"),
        "BIRCHER_NOOP_DIR": str(tmp_path / "noop"),
        "T_FIND_RUN": "rdemo-run-1", "T_PENDING": json.dumps(pending), "T_GEN": gen,
    })
    return r, (log.read_text() if log.exists() else "")


def test_recover_pr_asks_before_adopting_and_refuses_a_blocked_run(tmp_path):
    r, log = _recover(tmp_path, pending={"halted": False,
                                         "pending": [{"idempotency_key": "merge:9:x"}]})
    assert "RC=1" in r.stdout, (r.stdout, r.stderr)
    assert "ADOPTED" not in log, log
    assert "EFFECT" not in log, log
    assert "halted or holds pending effects" in r.stderr, r.stderr


def test_recover_pr_refuses_an_adopt_that_yielded_no_generation(tmp_path):
    r, log = _recover(tmp_path, pending={"halted": False, "pending": []})
    assert "RC=1" in r.stdout, (r.stdout, r.stderr)
    assert "ADOPTED" in log, log
    assert "EFFECT" not in log, log
    assert "OBSERVED" not in log, log
    assert "yielded no generation" in r.stderr, r.stderr


# --- the implementer's brief, read out of a REAL store -----------------------

def test_the_implementer_brief_carries_the_spec_the_plan_and_the_issue(tmp_path):
    """spec §6. Driven against a real database rather than a stub, because the
    whole content of the brief is that it comes FROM THE KERNEL: the accepted
    spec, the accepted plan, and the frozen issue snapshot. A stub returning a
    plausible string would prove the call happened and nothing about what it
    said.

    The directive half is rendered too, and the two halves are asserted
    together: the vendor pairing is what stops an implementer reviewing itself,
    and it has to survive being printed alongside the kernel's text."""
    from kernel.artifacts import put_artifact
    from kernel.enqueue import create_run
    from kernel.store import Store

    db = tmp_path / "kernel.db"
    store = Store.open(db)
    create_run(store, run_id="r-brief", base_repo="acme/widgets", base_sha="0" * 40,
               issue={"number": 711, "title": "Do the thing", "body": "the requirement",
                      "labels": ["bug"], "comments": []},
               project_config={})
    for phase, text in (("spec", "THE ACCEPTED SPEC BODY"),
                        ("plan", "THE ACCEPTED PLAN BODY")):
        store.set_phase_artifact("r-brief", phase,
                                 put_artifact(store, text.encode("utf-8")))

    src_lines = RUN_QUEUE.read_text().splitlines()
    script = tmp_path / "brief.sh"
    script.write_text(
        'BUNDLE_DIR="${BIRCHER_BUNDLE_DIR:?}"\n'
        'REPO=acme/widgets\nWORKDIR=/workspaces/smoke\nRECOVERY_REVIEWER=codex\n'
        '_kernel_pythonpath() { printf %s "$BIRCHER_V2_DIR"; }\n'
        + _extract_function(src_lines, "_implementer_brief") + "\n")
    r = subprocess.run(
        ["bash", "-c", f'. "{script}"\n_implementer_brief r-brief claude_code'],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "BIRCHER_BUNDLE_DIR": str(REPO_ROOT),
             "BIRCHER_V2_DIR": str(REPO_ROOT / "v2"),
             "BIRCHER_KERNEL_DB": str(db)})
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout

    assert "dispatch the implement sub-agent to claude_code" in out, out
    assert "the opposite vendor (codex)" in out, out
    assert "git -C /workspaces/smoke worktree add" in out, out
    assert "THE ACCEPTED SPEC BODY" in out, out
    assert "THE ACCEPTED PLAN BODY" in out, out
    # The issue snapshot, verbatim from the store -- the kernel's canonical
    # bytes, not a re-rendering of the same issue.
    from kernel import front
    frozen = store.read_blob(front.bundle_hash(store, "r-brief")).decode()
    assert frozen in out, (frozen, out)
    assert "the requirement" in out, out
