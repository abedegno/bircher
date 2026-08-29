"""RETIRED.

Every shell function this file exercised is now Python:

    observe_review    -> coordinator/review.py::dispatch
    observe_outcome   -> coordinator/outcome.py::derive
    run_item's marker -> deleted in C8 Phase 2

Their replacements are `tests/coordinator/test_ci_and_review.py` (which also
proves the review PROMPT renders byte-identically to the bash it came from) and
`tests/coordinator/test_outcome.py`, which drives `derive` through eighteen
cases -- more than this file ever had, and without a shell-extraction rig.

Kept as a stub rather than deleted so the retirement is visible in the tree
rather than only in a commit message.
"""


def test_the_shell_observers_are_gone():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    src = (root / "batch" / "run-queue.sh").read_text()
    lib = (root / "batch" / "lib" / "observe.sh").read_text()
    assert "observe_review() {" not in lib
    assert "_wait_ci() {" not in src
    assert "_ci_failure_kind() {" not in src
    assert "_rerun_and_wait_ci() {" not in src
