"""`--publish` EXECUTED.

Structural tests on this branch have repeatedly defended defects -- a grep for
a guard passes when the guard is `&& false`. These extract the real function
and drive it with stubs, asserting what it DID.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _extract(name):
    lines = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _drive(tmp_path, tag, *, find_run="run-1", run_base="basesha",
           verify="deadbeef"):
    """Run publish_cmd for real; return the ordered list of what it called."""
    log = tmp_path / f"log-{tag}"
    script = f"""
set -uo pipefail
REPO=demo/demo
BIRCHER_NET_TIMEOUT=5
LOG={log}
_log() {{ printf '%s\\n' "$*" >> "$LOG"; }}
_kernel_find_run() {{ _log "FIND $*"; printf '%s' '{find_run}'; }}
_kernel_run_base() {{ _log "BASE $*"; printf '%s' '{run_base}'; }}
_kernel_adopt_run() {{ _log "ADOPT $*"
                       BIRCHER_RUN_ID=run-1; export BIRCHER_RUN_ID
                       BIRCHER_GENERATION=1; export BIRCHER_GENERATION; }}
_kernel_verify_nomination() {{ _log "VERIFY $*"; printf '%s' '{verify}'; }}
_effect() {{ _log "EFFECT $1 $2 -- ${{*:4}}"; return 0; }}
_net_run() {{ shift; "$@"; }}
_kernel_warn() {{ :; }}

{_extract("publish_cmd")}

publish_cmd probe /tmp/wt-probe probe-branch || true
"""
    f = tmp_path / f"pub-{tag}.sh"
    f.write_text(script)
    subprocess.run(["bash", str(f)], capture_output=True, text=True)
    return log.read_text().splitlines() if log.exists() else []


def test_the_pushed_ref_carries_the_VERIFIED_oid(tmp_path):
    """Not HEAD, not the claim: the oid the kernel read for itself."""
    calls = _drive(tmp_path, "push", verify="deadbeef")
    push = next(c for c in calls if c.startswith("EFFECT ref_update"))
    assert "deadbeef:refs/heads/probe-branch" in push, push


def test_the_pr_is_opened_from_the_published_branch(tmp_path):
    calls = _drive(tmp_path, "pr")
    pr = next(c for c in calls if c.startswith("EFFECT pull_request"))
    assert "--head probe-branch" in pr, pr
    assert "--repo demo/demo" in pr, pr


def test_nothing_is_pushed_when_verification_refuses(tmp_path):
    """An empty oid is a refusal, never a value to push."""
    calls = _drive(tmp_path, "refused", verify="")
    assert not [c for c in calls if c.startswith("EFFECT")], calls


def test_a_run_without_a_recorded_base_publishes_nothing(tmp_path):
    calls = _drive(tmp_path, "nobase", run_base="")
    assert not [c for c in calls if c.startswith("EFFECT")], calls
    assert not [c for c in calls if c.startswith("VERIFY")], calls


def test_work_the_kernel_never_dispatched_is_refused_WITHOUT_minting_a_run(tmp_path):
    """The refusal must not have a side effect. Adopt mints when it finds
    nothing, so reaching it here would answer 'was this dispatched?' by
    dispatching it, and the next call would find its own junk run and treat
    that as provenance."""
    calls = _drive(tmp_path, "norun", find_run="")
    assert not [c for c in calls if c.startswith("ADOPT")], calls
    assert not [c for c in calls if c.startswith("EFFECT")], calls
