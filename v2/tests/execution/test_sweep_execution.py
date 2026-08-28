"""`reconcile_deferred_ready`'s kernel branch, EXECUTED.

The branch that adopts a deferred row's recorded run had zero executing
coverage in either suite, and the review proved what that costs: two mutations
survived a full run.

  - `"${BIRCHER_RUN_BASE:-$_rec_base}"` -> `"$_rec_base"`, re-introducing the
    exact defect a prior review named, left 572 tests passing. The test that
    was supposed to cover it bound the PRODUCER (`_kernel_adopt_run`'s export)
    and never the consumers, which are the sites the finding was about.
  - `if [ -n "$deferred_run" ] && false; then` left 572 passing, because the
    test asserted the condition's TEXT appears in the body -- which the mutated
    line still contains. A substring cannot see a truth value.

The bash `--self-test` does drive the sweep, but every row it writes has an
EMPTY fifth field (pinned by its own assertion), so all of them take the
fallback arm. The new path was never run by anything.
"""
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _extract_function(src_lines, name):
    start = next(i for i, l in enumerate(src_lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


def _drive_sweep(tmp_path, row):
    """Run the real `reconcile_deferred_ready` over one deferred row.

    Everything it reaches outside the branch under test is stubbed and logs
    what it was called with, so the assertions are about what the sweep DID,
    not about what its source says.
    """
    src = RUN_QUEUE.read_text().splitlines()
    body = _extract_function(src, "reconcile_deferred_ready")
    assert len(body.splitlines()) > 40, "extraction looks truncated"

    deferred = tmp_path / "deferred.tsv"
    deferred.write_text(row + "\n")
    log = tmp_path / "log"

    script = f'''
set -uo pipefail
REPO=demo/demo
DEFERRED_READY_FILE={deferred}
RECOVERY_REVIEWER=codex
BIRCHER_NET_TIMEOUT=5
MERGE_NOTE=""
LOG={log}

_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}
gh() {{ case "$*" in *"--json state"*) echo OPEN ;; *) echo "" ;; esac; }}
_net_run() {{ shift; "$@"; }}
_effect() {{ _log "effect $1"; return 0; }}
_restamp_if_delta_unchanged() {{ return 0; }}
_ensure_issue_closed() {{ :; }}
merge_ready_pr() {{ _log "merge run=${{BIRCHER_RUN_ID:-<unset>}} gen=${{BIRCHER_GENERATION:-<unset>}}"; return 0; }}
_kernel_adopt_run() {{ _log "ADOPT_BY_CODE $1"; BIRCHER_RUN_ID="adopted-by-code"; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=9; export BIRCHER_GENERATION; }}
_kernel_dispatch() {{ _log "dispatch $1 $2 for ${{BIRCHER_RUN_ID:-<unset>}}"; printf '7'; }}

{body}

reconcile_deferred_ready || true
'''
    f = tmp_path / "sweep.sh"
    f.write_text(script)
    r = subprocess.run(["bash", str(f)], capture_output=True, text=True,
                       cwd=str(REPO_ROOT))
    return (log.read_text().splitlines() if log.exists() else []), r


def test_a_recorded_run_id_is_ADOPTED_not_guessed(tmp_path):
    """The five-field row names the run that opened this PR. The sweep must use
    it and must NOT fall back to guessing by item code."""
    calls, r = _drive_sweep(
        tmp_path, "itemX\t42\t7\t" + "a" * 40 + "\titemX-1787000000")
    joined = "\n".join(calls)
    assert "ADOPT_BY_CODE" not in joined, (
        f"the sweep guessed by item code despite a recorded run id:\n{joined}\n"
        f"stderr: {r.stderr[-300:]}")
    assert any("dispatch codex reviewer for itemX-1787000000" in c for c in calls), (
        f"the sweep did not dispatch against the recorded run:\n{joined}")


def test_a_legacy_four_field_row_still_falls_back(tmp_path):
    """A row written before the run id existed must still sweep, by code."""
    calls, r = _drive_sweep(tmp_path, "itemY\t43\t8\t" + "b" * 40)
    joined = "\n".join(calls)
    assert "ADOPT_BY_CODE itemY" in joined, (
        f"a legacy row no longer falls back to adoption:\n{joined}\n"
        f"stderr: {r.stderr[-300:]}")


def test_a_phantom_recorded_run_is_SKIPPED_not_guessed_around(tmp_path):
    """The fifth field exists so the sweep stops guessing by item code. If the
    recorded id yields no generation, falling back to adoption hands this PR's
    status and merge attempts to the NEWEST run for that code -- which, after a
    requeue, is a different attempt entirely. That is the attribution defect the
    field was added to remove, reintroduced by the fallback meant to make the
    field safe. Codex flagged that this case had no coverage; it now has."""
    src = RUN_QUEUE.read_text().splitlines()
    body = _extract_function(src, "reconcile_deferred_ready")
    deferred = tmp_path / "deferred.tsv"
    deferred.write_text("itemZ\t44\t9\t" + "c" * 40 + "\tphantom-run-id\n")
    log = tmp_path / "log"

    script = f'''
set -uo pipefail
REPO=demo/demo
DEFERRED_READY_FILE={deferred}
RECOVERY_REVIEWER=codex
BIRCHER_NET_TIMEOUT=5
MERGE_NOTE=""
LOG={log}
_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}
gh() {{ case "$*" in *"--json state"*) echo OPEN ;; *) echo "" ;; esac; }}
_net_run() {{ shift; "$@"; }}
_effect() {{ _log "effect $1"; return 0; }}
_restamp_if_delta_unchanged() {{ return 0; }}
_ensure_issue_closed() {{ :; }}
merge_ready_pr() {{ _log "merge run=${{BIRCHER_RUN_ID:-<unset>}}"; return 0; }}
_kernel_adopt_run() {{ _log "ADOPT_BY_CODE $1"; BIRCHER_RUN_ID="newest-for-code"; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=9; export BIRCHER_GENERATION; }}
# the phantom: the kernel does not know this run, so no generation comes back
_kernel_dispatch() {{ _log "dispatch for ${{BIRCHER_RUN_ID:-<unset>}}"; printf ''; }}

{body}

reconcile_deferred_ready || true
'''
    f = tmp_path / "sweep-phantom.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True, cwd=str(REPO_ROOT))
    calls = log.read_text().splitlines() if log.exists() else []
    joined = "\n".join(calls)

    assert "ADOPT_BY_CODE" not in joined, (
        f"a phantom recorded id fell back to guessing by code:\n{joined}")
    assert not any(c.startswith("merge ") for c in calls), (
        f"the sweep merged under a run it could not confirm:\n{joined}")
