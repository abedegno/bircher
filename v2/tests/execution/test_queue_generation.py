"""Revision 16: the generator's third condition (shaping spec §5).

An open run whose journal holds an unresolved shape disagreement and no
current park is queued from the journal, exactly like a park or a filing
owed -- the pass may have died between the disputed ruling and its park, and
labels alone cannot find a run that never lost `bircher:queued`.

`_sweep` runs the REAL `issues-to-queue.sh` end to end, with a `gh` stub
that finds no labelled issues at all -- so what these tests bind is the
whole path: the python snippet's third `or`, and -- for the blocked case --
that the journal-derived union happens AFTER `is_unblocked`, not before.
"""
import os
import pathlib
import re
import subprocess

from kernel import front
from tests.kernel.test_reshape import _disagreed

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ISSUES_TO_QUEUE = REPO_ROOT / "batch" / "issues-to-queue.sh"

#: `issue list` -> nothing labelled: every issue these tests see must reach
#: the queue through the journal sweep, never through the label path.
#: `blocked_by` answers from a marker file `_disputed_run` plants (or not),
#: so ONE stub serves both the plain case and the blocked case.
_GH_STUB = """#!/usr/bin/env bash
case "$*" in
  *"issue list"*) echo '[]' ;;
  *"blocked_by"*) if [ -f "$GH_BLOCKED_MARKER" ]; then echo 1; else echo 0; fi ;;
  *"--json title"*) echo "T" ;;
  *"--json body"*) echo "B" ;;
  *"--json comments"*) echo '[]' ;;
  *) echo '[]' ;;
esac
"""


def _disputed_run(tmp_path, *, park=False, blocked=False):
    """This module's own fixture, built on the kernel's `_disagreed`
    (`tests/kernel/test_reshape.py`): a run at `shaping` with an unresolved
    dispute, plus what a GENERATOR test needs on top of the bare dispute --
    the park the third condition must NOT require, and a GitHub blocking
    link the label-derived check must not be applied to.

    Named `_disputed_run`, not `_disagreed`: the kernel fixture of that name
    (`tests/kernel/test_reshape.py`) takes `(tmp_path, name, labels)` and
    builds a run with no opinion about a park or a blocker at all. A caller
    that read the two names as interchangeable would pass this module's
    keyword arguments to that one and get a `TypeError` -- or, should the
    two signatures ever converge by accident, silently build the wrong run.
    """
    s, f = _disagreed(tmp_path)
    if park:
        f.park(reason="disagreement", phase="slices")
    if blocked:
        (tmp_path / "blocked").touch()
    return s, f


def _sweep(tmp_path, db_name="d.db"):
    """Runs the real `issues-to-queue.sh` against the store `_disputed_run`
    just built, with a `gh` stub that finds no labelled issues at all -- so
    the only issues that reach the queue are the ones the journal sweep
    itself resumes. Returns the queued issue numbers, read from the
    manifest `issues-to-queue.sh` writes, sorted.
    """
    gh = tmp_path / "gh"
    gh.write_text(_GH_STUB)
    gh.chmod(0o755)
    queue = tmp_path / "queue"
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ.get('PATH', '')}",
               QUEUE=str(queue), BIRCHER_KERNEL_DB=str(tmp_path / db_name),
               GH_BLOCKED_MARKER=str(tmp_path / "blocked"), REPO="o/r")
    r = subprocess.run(["bash", str(ISSUES_TO_QUEUE)], cwd=str(tmp_path),
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"issues-to-queue.sh exited {r.returncode}\n{r.stdout}\n{r.stderr}"
    manifest = queue / ".manifest"
    if not manifest.exists():
        return []
    nums = []
    for line in manifest.read_text().splitlines():
        m = re.match(r"^i(\d+)-", line)
        if m:
            nums.append(int(m.group(1)))
    return sorted(nums)


def test_the_generator_lists_a_run_with_an_unresolved_dispute_and_no_park(tmp_path):
    s, f = _disputed_run(tmp_path, park=False)
    assert _sweep(tmp_path) == [front.issue_number(s, f.run_id)]


def test_a_resumed_run_is_not_withheld_by_a_blocker(tmp_path):
    """The blocked check applies to label-derived items only: an open run is
    past its sequencing, and a sibling blocker reopened while it was parked
    would withhold it every wave with nothing on the issue to say so."""
    s, f = _disputed_run(tmp_path, park=False, blocked=True)
    assert _sweep(tmp_path) == [front.issue_number(s, f.run_id)]


def test_a_resolved_dispute_is_not_queued_by_the_third_condition(tmp_path):
    """The control: a run whose dispute the person already resolved must not
    be swept in by THIS condition -- it is `queued`, not `shaping`, and
    `unresolved_disagreement` is False. Without this, a mutation that always
    took the third `or` (e.g. dropping the `unresolved_disagreement` guard
    entirely) would still pass the two tests above."""
    s, f = _disagreed(tmp_path)
    f.approve_one_piece()
    assert s.run_state(f.run_id) == "queued"
    assert front.unresolved_disagreement(s, f.run_id) is False
    assert _sweep(tmp_path) == []
