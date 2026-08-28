"""`--publish` EXECUTED.

Structural tests on this branch have repeatedly defended defects -- a grep for
a guard passes when the guard is `&& false`. These extract the real function
and drive it with stubs, asserting what it DID.
"""
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _init_repo(path, origin):
    """A real repo: publish_cmd reads the worktree's own remote."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True, env=env)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin],
                       check=True, env=env)


def _extract(name):
    lines = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _drive(tmp_path, tag, *, find_run="run-1", run_base="basesha",
           verify="deadbeef", origin="https://github.com/demo/demo.git"):
    """Run publish_cmd for real; return the ordered list of what it called."""
    log = tmp_path / f"log-{tag}"
    wt = tmp_path / f"wt-{tag}"
    wt.mkdir()
    _init_repo(wt, origin)
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
_effect() {{ _log "EFFECT $1 $2 pwd=$PWD -- ${{*:4}}"; return 0; }}
_net_run() {{ shift; "$@"; }}
_kernel_warn() {{ :; }}

{_extract("publish_cmd")}

publish_cmd probe {wt} probe-branch || true
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


def test_both_effects_run_IN_THE_WORKTREE_not_the_coordinators_cwd(tmp_path):
    """`origin` and the object id are properties of the nominated worktree.

    Found live: run from the coordinator's checkout, `git push origin`
    resolved a different remote and the oid was not present at all. The kernel
    halted the run on an uncertain effect.
    """
    calls = _drive(tmp_path, "cwd")
    assert calls, calls
    for c in calls:
        if c.startswith("EFFECT"):
            assert f"pwd={tmp_path / 'wt-cwd'}" in c, c



def test_the_push_and_the_PR_must_name_the_same_repository(tmp_path):
    """`git push origin` resolves through the worktree's remote; `gh pr create
    --repo` names one explicitly. They silently disagreed for four live runs:
    the push landed on the smoke repo and the PR was attempted against muesli.
    Nothing at all should happen when they differ."""
    calls = _drive(tmp_path, "mismatch",
                   origin="https://github.com/someone/else.git")
    assert not calls, f"a repository mismatch still acted: {calls}"


def test_a_worktree_with_no_origin_publishes_nothing(tmp_path):
    calls = _drive(tmp_path, "noorigin", origin="")
    assert not calls, calls


def test_the_ssh_and_https_spellings_of_the_same_repo_agree(tmp_path):
    """`git@github.com:demo/demo.git` and the https URL are one repository. A
    guard that only understood one spelling would refuse correct setups."""
    calls = _drive(tmp_path, "ssh", origin="git@github.com:demo/demo.git")
    assert [c for c in calls if c.startswith("EFFECT ref_update")], calls
