"""The kernel is called at each stage transition, in the right order.

These assert the wiring exists and is ordered, by reading the source -- they
are the brief's own static tests, unmodified in intent (see task-4-brief.md
Step 1). They prove `run_item` calls the lifecycle functions in the right
place and order. They do NOT prove the functions do anything when called --
that is `test_lifecycle_functions.py`, which drives the same functions
against a real database and reads the facts back. Neither file substitutes
for the other: a static grep passes even over a recorder that records
nothing (Task 3's near-miss), and an execution test with no ordering
assertion would not catch a call moved to the wrong place in `run_item`.
"""
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _run_item():
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("run_item()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    return "\n".join(src[start:end])


def test_run_item_is_found():
    """A parser that finds nothing reports total compliance."""
    assert len(_run_item().splitlines()) > 100


@pytest.mark.parametrize("name", [
    "enqueue", "record_implementation_output", "record_ci_observation",
    "record_review", "request_merge", "record_merge_outcome",
])
def test_each_stage_calls_the_kernel(name):
    assert name in _run_item(), f"run_item never records {name}"


def test_the_reviewer_gets_its_own_dispatch():
    """validate_review refuses a review whose attempt was not dispatched in
    the reviewer role. One dispatch at session creation grants implementer
    only, so without this every run shadow-rejects its review."""
    body = _run_item()
    assert "_kernel_dispatch \"$RECOVERY_REVIEWER\" reviewer" in body


def test_the_merge_request_redispatches_as_implementer():
    """A dispatch re-fences the generation, so after the reviewer dispatch the
    implementer needs a fresh one."""
    body = _run_item()
    review = body.index("record_review")
    merge = body.index("request_merge")
    between = body[review:merge]
    assert "_kernel_dispatch \"$vendor\" implementer" in between


def test_the_run_id_carries_the_attempt_epoch():
    """Item codes recur across attempts. A colliding run id merges two runs'
    facts into one aggregate."""
    assert re.search(r'BIRCHER_RUN_ID="\$\{item\}-\$\(date \+%s\)"', _run_item())


def test_no_kernel_call_is_tested_for_success():
    """Advisory means no branch reads a kernel exit code. `if _kernel ...` or
    `_kernel ... &&` would make the recorder able to change the run."""
    for line in _run_item().splitlines():
        s = line.strip()
        if "_kernel" not in s or s.startswith("#"):
            continue
        assert not re.match(r"(if|while|until)\s+_kernel", s), s
        assert "&&" not in s.split("_kernel")[0][-4:], s


# --- Task 4 restructuring: run_item calls named functions, not `_kernel
# command` inline -- these tests are additive to the brief's, not a
# replacement for them (CONTROLLER RULING: "Keep the brief's static tests
# too. They prove run_item calls the functions; the execution tests prove
# the functions work.")

@pytest.mark.parametrize("fn", [
    "_kernel_run_start", "_kernel_record_output", "_kernel_record_ci",
    "_kernel_record_review", "_kernel_request_merge", "_kernel_record_outcome",
])
def test_run_item_calls_the_named_lifecycle_function(fn):
    assert fn in _run_item(), f"run_item never calls {fn}"


def test_run_item_never_inlines_a_kernel_command_call():
    """The lifecycle calls are extracted into named functions in
    kernel-client.sh (see test_lifecycle_functions.py); run_item itself must
    not also inline a raw `_kernel command --name ...` invocation -- that
    would be a second, unexercised copy of the same payload-building logic
    the named functions already own."""
    for line in _run_item().splitlines():
        s = line.strip()
        assert not re.match(r"_kernel\s+command\b", s), (
            f"run_item inlines a raw kernel command call instead of using a "
            f"named function: {s}"
        )
