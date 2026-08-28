"""`recover_pr_cmd`'s halted branch, EXECUTED.

Nothing ran this branch. The bash `--self-test`'s `--recover-pr` blocks set no
kernel database, so the halt path was never entered, and every guard on it was
defended by grepping the function's source.

What that cost, proven by a reviewer: hoisting `_kernel_reconcile` above the
`_done` check -- reinstating verbatim the every-key-at-one-observed-version
defect that two prior rounds were spent removing, and adding a false "also
unresolved" log line -- left all 585 tests passing.
"""
import json
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _extract(name):
    lines = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _drive_halt(tmp_path, pending, pr="730"):
    """Run the real reconciliation block with a stubbed kernel that REPORTS a
    halt, and record every reconcile it attempts."""
    body = _extract("recover_pr_cmd")
    log = tmp_path / "log"
    pend = json.dumps({"version": 11, "state": "merge_requested",
                       "halted": True, "pending": pending})

    script = f'''
set -uo pipefail
REPO=demo/demo
WORKDIR={tmp_path}
RECOVERY_REVIEWER=claude_code
BIRCHER_NET_TIMEOUT=5
MERGE_NOTE=""
MERGE_UNREVIEWED_NOTE=""
LOG={log}
_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}

_kernel_pending() {{ printf '%s' {pend!r}; }}
_kernel_reconcile() {{ _log "RECONCILE version=$3 keys=${{*:4}}"; }}
_kernel_adopt_run() {{ BIRCHER_RUN_ID=run-1; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=3; export BIRCHER_GENERATION; }}
_kernel_dispatch() {{ printf '3'; }}
_kernel_record_output() {{ printf 'outhash'; }}
_kernel_put_artifact() {{ printf 'ctxhash'; }}
_kernel_record_ci() {{ :; }}
_kernel_record_review() {{ :; }}
_kernel_request_merge() {{ :; }}
_install_work_git_config() {{ :; }}
_net_run() {{ shift; "$@"; }}
_effect() {{ _log "effect $1"; return 0; }}
_issue_writeback() {{ :; }}
merge_ready_pr() {{ _log "merge"; return 0; }}
# recovery says "not ready", so the run stops after the halt handling
recover_from_ground_truth() {{ printf '%s' 'escalated|na|probe|' ; }}
gh() {{ echo ""; }}
git() {{ echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; }}

{body}

recover_pr_cmd probe {pr} claude_code || true
'''
    f = tmp_path / "halt.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True, cwd=str(REPO_ROOT))
    return [l for l in (log.read_text().splitlines() if log.exists() else [])]


def test_every_key_for_this_PR_is_reconciled_in_ONE_call(tmp_path):
    """Two uncertain merges for this PR: ONE reconcile call carrying both, at
    the version this invocation observed.

    Resolving them in separate calls cannot be made safe from here -- a CAS
    cannot distinguish this caller's own version bump from a foreign writer's --
    so the kernel does them under a single CAS in one transaction.
    """
    calls = _drive_halt(tmp_path, [
        {"effect_class": "merge", "idempotency_key": "merge:730:aaa"},
        {"effect_class": "merge", "idempotency_key": "merge:730:bbb"},
    ])
    recs = [c for c in calls if c.startswith("RECONCILE")]
    assert len(recs) == 1, f"expected exactly ONE call carrying both keys: {recs}"
    assert recs[0] == "RECONCILE version=11 keys=merge:730:aaa merge:730:bbb", recs


def test_the_reconcile_uses_the_version_this_invocation_OBSERVED(tmp_path):
    """Not an incremented guess and not a re-read: the version reported by the
    same `pending` call the keys came from."""
    calls = _drive_halt(tmp_path, [
        {"effect_class": "merge", "idempotency_key": "merge:730:aaa"}])
    recs = [c for c in calls if c.startswith("RECONCILE")]
    assert recs == ["RECONCILE version=11 keys=merge:730:aaa"], recs


def test_another_prs_uncertain_merge_is_never_reconciled(tmp_path):
    """The observation is about this PR. A merge key for another PR held by the
    same adopted run must not be resolved by it -- not even now that the call
    carries several keys at once."""
    calls = _drive_halt(tmp_path, [
        {"effect_class": "merge", "idempotency_key": "merge:999:zzz"},
        {"effect_class": "merge", "idempotency_key": "merge:730:aaa"},
    ])
    recs = [c for c in calls if c.startswith("RECONCILE")]
    assert recs == ["RECONCILE version=11 keys=merge:730:aaa"], (
        f"another PR's merge was reconciled from this PR's observation: {recs}")
