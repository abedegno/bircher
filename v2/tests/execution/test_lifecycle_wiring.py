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
    "_kernel_run_start", "_kernel_submit_spec", "_kernel_submit_plan",
    "_kernel_start_implementation", "_kernel_record_output", "_kernel_record_ci",
    "_kernel_record_review", "_kernel_request_merge", "_kernel_record_outcome",
])
def test_run_item_calls_the_named_lifecycle_function(fn):
    assert fn in _run_item(), f"run_item never calls {fn}"


def test_the_three_missing_transitions_precede_the_implementation_output():
    """Fix round 1, IMPORTANT (b): without submit_spec / submit_plan /
    start_implementation, the run never leaves `queued`, and every later
    command is refused for the same reason regardless of what it is --
    reviewed empirically against a live database, not merely reasoned about
    (see test_lifecycle_functions.py). All three must run, in state-machine
    order, before record_implementation_output (which requires state
    'implementing')."""
    body = _run_item()
    spec = body.index("_kernel_submit_spec")
    plan = body.index("_kernel_submit_plan")
    start = body.index("_kernel_start_implementation")
    output = body.index("_kernel_record_output")
    assert spec < plan < start < output, (spec, plan, start, output)


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


# --- the terminal record, and why some sites deliberately lack one -------------
#
# run_item has SIX exits that write a scorecard row, and before
# record_run_outcome existed, five of them left the kernel with no terminal
# fact at all -- the run sat in `implementing` forever, indistinguishable from
# one still in flight. The first live acceptance run demonstrated it: scorecard
# `escalated`, kernel `implementing`.
#
# Wiring the three reachable sites fixes those three instances. This test is
# what closes the CLASS: a new exit added later either records an outcome or
# names itself here with a reason, and there is no third option that stays
# green.

#: Scorecard writes that deliberately record NO kernel outcome, and why. Each
#: key is a distinguishing substring of the site's own line.
_NO_KERNEL_OUTCOME = {
    "queue file missing at read time":
        "the item never launched; BIRCHER_RUN_ID is not assigned yet, so "
        "there is no run in the kernel to end",
    "empty/blank prompt":
        "same -- refused before the run id is minted",
    "REST session create failed":
        "the run exists but no generation has been dispatched, and every "
        "command is submitted under a generation; recording here would need "
        "a dispatch invented solely to report that nothing ran",
}


def _scorecard_sites():
    lines = _run_item().splitlines()
    return [(i, l) for i, l in enumerate(lines)
            if "json_row" in l and "SCORECARD" in l]


def test_the_scorecard_sites_are_found():
    """A parser that finds nothing reports total compliance."""
    assert len(_scorecard_sites()) >= 6


def test_every_terminal_scorecard_row_records_a_kernel_outcome():
    lines = _run_item().splitlines()
    missing = []
    for i, line in _scorecard_sites():
        if any(k in line for k in _NO_KERNEL_OUTCOME):
            continue
        window = "\n".join(lines[max(0, i - 6):i])
        if "_kernel_record_run_outcome" not in window:
            missing.append(line.strip()[:90])
    assert not missing, (
        "these scorecard rows end a run without telling the kernel, so the "
        "ledger keeps them in flight forever:\n  " + "\n  ".join(missing))


def test_every_exemption_still_matches_a_real_site():
    """A stale exemption is worse than none: it silently excuses whatever
    site later happens to contain its text."""
    sites = [l for _, l in _scorecard_sites()]
    for key in _NO_KERNEL_OUTCOME:
        assert sum(key in l for l in sites) == 1, (
            f"exemption {key!r} matches {sum(key in l for l in sites)} sites, "
            "expected exactly 1")


def test_the_exempt_sites_really_are_before_a_generation_exists():
    """The REASON the exemptions are legitimate, checked rather than asserted.

    Every command is submitted under a dispatched generation. If one of these
    sites drifted BELOW the dispatch, its exemption would no longer describe
    anything true -- it would just be a site quietly opting out.
    """
    lines = _run_item().splitlines()
    dispatch_at = next(i for i, l in enumerate(lines)
                       if "_kernel_dispatch" in l and "BIRCHER_GENERATION=" in l)
    for i, line in _scorecard_sites():
        if any(k in line for k in _NO_KERNEL_OUTCOME):
            assert i < dispatch_at, (
                f"exempt site at run_item line {i} is BELOW the dispatch at "
                f"{dispatch_at}; a generation exists, so it can and must "
                f"record an outcome: {line.strip()[:80]}")
