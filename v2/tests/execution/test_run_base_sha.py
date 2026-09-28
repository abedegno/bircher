"""A new run's base is the default branch as origin has it now (2026-09-28).

The runner's muesli checkout had a local `main` last moved on 31 August while
`origin/main` was current, and a new run took `git rev-parse HEAD`. #782's
spec and plan were written against month-old code with no ami-eval, and the
implementer -- on the real head -- correctly refused the plan.
"""
import subprocess
from pathlib import Path

RQ = Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"


def _extract_function(src_lines, name):
    start = next(i for i, line in enumerate(src_lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(src_lines)) if src_lines[i] == "}")
    return "\n".join(src_lines[start:end + 1])


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                               "HOME": str(cwd)}).stdout.strip()


def _world(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "a").write_text("1")
    _git(origin, "add", "a")
    _git(origin, "commit", "-qm", "one")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    old = _git(work, "rev-parse", "HEAD")
    (origin / "a").write_text("2")
    _git(origin, "commit", "-qam", "two")        # origin moves on; the clone does not
    return work, old, _git(origin, "rev-parse", "HEAD")


def _base(work, fetch=True):
    fn = _extract_function(RQ.read_text().splitlines(), "_run_base_sha")
    net = '_net_run() { shift; "$@"; }' if fetch else '_net_run() { return 1; }'
    r = subprocess.run(["bash", "-c", f"set -u\n{net}\nWORKDIR={work}\n{fn}\n_run_base_sha"],
                       capture_output=True, text=True)
    return r.stdout.strip(), r.stderr


def test_a_stale_local_main_does_not_become_the_base(tmp_path):
    work, old, new = _world(tmp_path)
    assert _git(work, "rev-parse", "HEAD") == old     # the stale local branch
    base, _ = _base(work)
    assert base == new, (base, old, new)


def test_a_failed_fetch_still_bases_on_origin_not_the_local_branch(tmp_path):
    work, old, new = _world(tmp_path)
    _git(work, "fetch", "-q", "origin")                # an earlier fetch saw `new`
    _git(work, "reset", "-q", "--hard", old)           # local main left behind
    base, err = _base(work, fetch=False)
    assert base == new and "fetch" in err, (base, err)


def test_new_runs_are_minted_from_it():
    body = _extract_function(RQ.read_text().splitlines(), "run_item")
    assert "_base_sha=$(_run_base_sha)" in body
    assert 'rev-parse HEAD' not in body.split("_base_sha=$(_run_base_sha)")[0].split("local _iss")[-1]


def test_a_recovered_pr_is_minted_from_it_too():
    body = _extract_function(RQ.read_text().splitlines(), "recover_pr_cmd")
    assert "_rec_base=$(_run_base_sha)" in body
