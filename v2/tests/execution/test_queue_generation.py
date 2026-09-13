"""Revision 16: the generator's third condition (shaping spec §5).

An open run whose journal holds an unresolved shape disagreement and no
current park is queued from the journal, exactly like a park or a filing
owed -- the pass may have died between the disputed ruling and its park, and
labels alone cannot find a run that never lost `bircher:queued`.

`_sweep` runs the REAL `issues-to-queue.sh` end to end, so what these tests
bind is the whole path: the python snippet's third `or`, that the label loop's
`is_unblocked` gate still binds (fix round 1, F2), and that the journal-derived
union's dedupe is keyed on what was actually EMITTED rather than on bare
membership in the label-derived list (fix round 1, F1 and F4) -- an issue
`is_unblocked` skipped is still a member of that list, and reading membership
there as "already handled" both let a blocked label-derived item slip through
unqueued via the label path (its own gate) AND dropped it a second time when
the journal loop mistook the skip for an emission.
"""
import os
import pathlib
import re
import subprocess

from kernel import front
from tests.kernel.test_reshape import _disagreed

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ISSUES_TO_QUEUE = REPO_ROOT / "batch" / "issues-to-queue.sh"

#: `issue list` answers from a file (empty by default -- printf '', NOT the
#: literal text '[]', which `for n in $queued_nums` would word-split into a
#: single bogus issue number "[]"; fix round 1, F3, the exact bug the report
#: had already found and fixed in the bash self-test and reintroduced here).
#: `blocked_by` answers PER ISSUE from a second file, so a scenario can block
#: one issue and not another -- a single boolean marker (this module's first
#: draft) cannot express "journal-derived AND still labelled AND blocked",
#: which is exactly the case F1 needed to fail on.
_GH_STUB = """#!/usr/bin/env bash
case "$*" in
  *"issue list"*) [ -s "$GH_LABELLED_FILE" ] && cat "$GH_LABELLED_FILE" || printf '' ;;
  *"dependencies/blocked_by"*)
    num=$(printf '%s' "$*" | grep -oE '/issues/[0-9]+/' | grep -oE '[0-9]+')
    if [ -s "$GH_BLOCKED_FILE" ] && grep -qx "$num" "$GH_BLOCKED_FILE"; then
      echo 1
    else
      echo 0
    fi ;;
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

    `blocked=True` appends the run's OWN issue number to the blocked-issues
    file `_sweep` reads (a fixed path under `tmp_path`, not an env override --
    every caller in this module shares one `tmp_path` per test, and a test
    that wants a DIFFERENT issue blocked, with no run behind it at all
    (`test_a_blocked_label_derived_issue_is_not_queued`), writes to the same
    file directly.
    """
    s, f = _disagreed(tmp_path)
    if park:
        f.park(reason="disagreement", phase="slices")
    if blocked:
        n = front.issue_number(s, f.run_id)
        blocked_file = tmp_path / "blocked-issues"
        with blocked_file.open("a") as fh:
            fh.write(f"{n}\n")
    return s, f


def _sweep(tmp_path, db_name="d.db", labelled=()):
    """Runs the real `issues-to-queue.sh` against the store `_disputed_run`
    just built (if any -- a purely label-derived scenario needs none), with
    a `gh` stub answering `issue list` from *labelled* (issue numbers still
    carrying `bircher:queued`) and `blocked_by` from whatever
    `tmp_path/"blocked-issues"` holds. Returns the queued issue numbers, read
    from the manifest `issues-to-queue.sh` writes, in FILE order and NOT
    deduplicated -- a number appearing twice is a real defect (fix round 1,
    F4), and collapsing it into a set here would hide exactly that.
    """
    gh = tmp_path / "gh"
    gh.write_text(_GH_STUB)
    gh.chmod(0o755)
    queue = tmp_path / "queue"
    labelled_file = tmp_path / "labelled-issues"
    labelled_file.write_text("\n".join(str(n) for n in labelled))
    blocked_file = tmp_path / "blocked-issues"
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ.get('PATH', '')}",
               QUEUE=str(queue), BIRCHER_KERNEL_DB=str(tmp_path / db_name),
               GH_LABELLED_FILE=str(labelled_file),
               GH_BLOCKED_FILE=str(blocked_file), REPO="o/r")
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
    return nums


def test_the_generator_lists_a_run_with_an_unresolved_dispute_and_no_park(tmp_path):
    s, f = _disputed_run(tmp_path, park=False)
    assert _sweep(tmp_path) == [front.issue_number(s, f.run_id)]


def test_a_resumed_run_is_not_withheld_by_a_blocker(tmp_path):
    """The blocked check applies to label-derived items only: an open run is
    past its sequencing, and a sibling blocker reopened while it was parked
    would withhold it every wave with nothing on the issue to say so. This
    is the journal-ONLY case -- the issue carries no `bircher:queued` label
    at all, so the label loop never sees it."""
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


def test_a_blocked_label_derived_issue_is_not_queued(tmp_path):
    """Fix round 1, F2 -- BLOCKING. The label-derived path's `is_unblocked`
    gate must still bind: a purely label-derived issue (no kernel run, no
    journal fact at all) with an open blocker must not reach the manifest.
    Without this the whole `is_unblocked "$n" || { …; continue; }` line could
    be deleted from the label loop and nothing would notice -- a fail-open on
    the sequencing gate `_emit_item`'s extraction sits behind."""
    blocked_file = tmp_path / "blocked-issues"
    blocked_file.write_text("99\n")
    assert _sweep(tmp_path, labelled=[99]) == []


def test_a_journal_resumption_also_label_queued_is_not_withheld_by_a_blocker(tmp_path):
    """Fix round 1, F1. An issue that is BOTH still `bircher:queued` (the
    label swap to `running` can fail -- it is `_effect … || true`) AND
    journal-derived with an unresolved dispute must still be queued through
    the journal path when a sibling blocker withholds the label path: the
    dedupe has to test what was ACTUALLY EMITTED, not bare membership in the
    label-derived list, or a blocked label-derived item reads as "already
    handled" and is dropped by both loops."""
    s, f = _disputed_run(tmp_path, park=False, blocked=True)
    n = front.issue_number(s, f.run_id)
    assert _sweep(tmp_path, labelled=[n]) == [n]


def test_a_journal_resumption_also_label_queued_is_emitted_exactly_once(tmp_path):
    """Fix round 1, F4. The de-duplication itself: an issue that is both
    label-queued (unblocked, so the label loop emits it) and journal-derived
    must reach the manifest ONCE, not twice -- `_emit_item`'s own comment says
    the extraction exists to prevent exactly this manifest drift. `_sweep`
    does not deduplicate its own return value, so a second `i<n>-….md` line
    would show up here as a second list entry."""
    s, f = _disputed_run(tmp_path, park=False)
    n = front.issue_number(s, f.run_id)
    assert _sweep(tmp_path, labelled=[n]) == [n]
