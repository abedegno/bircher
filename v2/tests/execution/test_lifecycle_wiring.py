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
import subprocess

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
# Wiring the reachable sites fixes those instances. The tests below close the
# class WITHIN run_item: a new exit added there either records an outcome or
# names itself in _NO_KERNEL_OUTCOME with a reason.
#
# SCOPE, stated because the earlier version of this comment did not and the
# claim was therefore larger than the parser. `_run_item()` reads run_item and
# nothing else. `reconcile_deferred_ready` appends EIGHT further terminal
# scorecard rows for the same items after run_item has returned, and can
# record `escalated` where run_item already recorded `ready`. None of those
# are in scope here, none are exempted here, and because a second
# record_run_outcome is refused by design, the kernel's terminal fact can then
# disagree with the scorecard's last word and never be corrected. That is a
# real gap; this test does not cover it and does not pretend to.

#: Scorecard writes that deliberately record NO kernel outcome, and why. Each
#: key is a distinguishing substring of the site's own line.
_NO_KERNEL_OUTCOME = {
    "queue file missing at read time":
        "the item never launched; BIRCHER_RUN_ID is not assigned yet, so "
        "there is no run in the kernel to end",
    "empty/blank prompt":
        "same -- refused before the run id is minted",
}


def _logical_lines():
    """run_item as LOGICAL lines: continuations joined, comments stripped.

    Both transformations are load-bearing, and each was a way past the naive
    parser that stayed green:

    - A new exit whose `>> "$SCORECARD"` sat on a CONTINUATION line was
      invisible, because no single physical line held both `json_row` and
      `SCORECARD`. The only difference from a caught exit was a backslash,
      and run-queue.sh already writes `_effect` calls that way in seven
      places.
    - Replacing a real recorder with `# TODO: call _kernel_record_run_outcome
      here` also stayed green, because the window check was a raw substring
      search: a comment NAMING the call satisfied it. This file already knew
      that shape -- test_no_kernel_call_is_tested_for_success filters
      `startswith("#")` -- and the filter was not carried across.

    Returns (first_physical_index, logical_text).
    """
    out, buf, start = [], "", None
    for i, raw in enumerate(_run_item().splitlines()):
        code = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else raw
        if start is None:
            start = i
        buf += code.rstrip("\\") if code.rstrip().endswith("\\") else code
        if code.rstrip().endswith("\\"):
            continue
        out.append((start, buf))
        buf, start = "", None
    if start is not None:
        out.append((start, buf))
    return out


def _strip_comment(text):
    """Drop a trailing comment, so a comment naming a call is not the call."""
    stripped = text.lstrip()
    return "" if stripped.startswith("#") else text


def _scorecard_sites():
    return [(i, l) for i, l in _logical_lines()
            if "json_row" in l and "SCORECARD" in l]


def test_the_scorecard_sites_are_found():
    """A parser that finds nothing reports total compliance."""
    assert len(_scorecard_sites()) >= 6


def test_every_terminal_scorecard_row_records_a_kernel_outcome():
    lines = [_strip_comment(l) for l in _run_item().splitlines()]
    missing = []
    for i, line in _scorecard_sites():
        if any(k in line for k in _NO_KERNEL_OUTCOME):
            continue
        window = "\n".join(lines[max(0, i - 8):i])
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


# --- no routed effect may precede the generation it is recorded under ---------

def test_no_effect_in_run_item_runs_before_a_generation_exists():
    """The CRITICAL finding, closed as a class rather than as one line.

    `_effect issue_or_label "running:..."` sat above the dispatch, so in kernel
    mode `${BIRCHER_GENERATION:?}` aborted the call and its trailing `|| true`
    swallowed the failure: the `bircher:running` label was SILENTLY dropped
    where legacy mode applied it. On the second item of a run it was worse than
    dropped -- BIRCHER_GENERATION is exported, so the stale value from the
    previous item would have attributed this item's effect to another run.

    Fixing the one line would leave the next `_effect` added above the dispatch
    to fail exactly the same way, just as quietly. Every routed effect in
    run_item must sit below the dispatch, and that is what this asserts.
    """
    logical = _logical_lines()
    dispatch_at = next(i for i, l in logical
                       if "_kernel_dispatch" in l and "BIRCHER_GENERATION=" in l)
    early = [(i, l.strip()[:80]) for i, l in logical
             if "_effect " in l and i < dispatch_at]
    assert not early, (
        "these routed effects run before any generation exists, so in kernel "
        "mode they abort on ${BIRCHER_GENERATION:?} and are silently skipped:\n"
        + "\n".join(f"  run_item line {i}: {t}" for i, t in early))


# --- every _effect site in the file, classified ------------------------------
#
# The run_item-scoped test above closes its class WITHIN run_item, and a live
# smoke run showed how much that leaves out. `--recover-pr` -- the documented
# path for landing a human PR -- performs `_effect ref_update`, then reaches
# `_post_cross_review_status` and the merge through `merge_ready_pr`, and NEVER
# calls run_item. BIRCHER_RUN_ID is assigned in exactly one place in this file,
# inside run_item, so in kernel mode every one of those effects aborts on
# `${BIRCHER_RUN_ID:?}` and is swallowed by the redirects around it. Observed,
# not reasoned: a real --recover-pr against a throwaway repo in
# BIRCHER_EFFECT_MODE=kernel produced no kernel run, no PR comment and no
# bircher/cross-review status.
#
# There are two distinct failure modes and they need separating:
#
#   NO GENERATION   -- entered without run_item ever having run. Effects abort.
#   STALE GENERATION-- runs AFTER run_item returned, and nothing unsets
#                      BIRCHER_RUN_ID/BIRCHER_GENERATION, so effects are
#                      attributed to whichever item happened to run last.
#
# This test does not fix either. It makes them enumerable, so a new _effect
# site cannot join them silently.

#: Functions containing an `_effect` call, and the generation context they run
#: in. REACHED = called from run_item, so the exported generation is this run's.
_EFFECT_SITE_CONTEXT = {
    "run_item": "REACHED",
    "_send_prompt": "REACHED",
    "_prune_session": "REACHED",
    "_post_cross_review_status": "REACHED",
    "merge_ready_pr": "REACHED",
    "_issue_writeback": "REACHED",
    "_ensure_issue_closed": "REACHED",
    "recover_from_ground_truth": "REACHED",
    "_reconcile_item_pr": "REACHED",
    # ADOPTS = mints or re-adopts the item's run and re-fences a generation
    # before its first effect, so it runs under a valid one of its own.
    "recover_pr_cmd": "ADOPTS",
    "reconcile_deferred_ready": "ADOPTS",
    # --- known gaps, evidenced ---
    "_reopen_reverted_issues": "STALE GENERATION",
    "_pr": "STALE GENERATION",
}


def _effect_sites_by_function():
    import re
    src = RUN_QUEUE.read_text().splitlines()
    spans, fn, start = {}, None, None
    for i, l in enumerate(src):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", l)
        if m:
            fn, start = m.group(1), i
        elif l == "}" and fn is not None:
            spans[fn] = (start, i)
            fn = None

    def owner(idx):
        best = None
        for name, (a, b) in spans.items():
            if a < idx < b and (best is None or a > best[1]):
                best = (name, a)
        return best[0] if best else "«top-level»"

    out = {}
    for i, l in enumerate(src):
        if l.strip().startswith("#"):
            continue
        if re.search(r"(?<!_)\b_effect\s+\w", l):
            out.setdefault(owner(i), []).append(i + 1)
    return out


def test_the_effect_site_parser_finds_them():
    """A parser that finds nothing reports total compliance."""
    assert len(_effect_sites_by_function()) >= 10


def test_every_effect_site_is_classified():
    """A new `_effect` call must declare which generation context it runs in.

    Without this the two gaps above are invisible: an effect added to a
    function that never runs under run_item fails silently in kernel mode, and
    the failure looks exactly like nothing happening.
    """
    found = set(_effect_sites_by_function())
    known = set(_EFFECT_SITE_CONTEXT)
    assert found <= known, (
        "unclassified _effect sites -- say which generation context each runs "
        f"in: {sorted(found - known)}")
    assert known <= found, (
        f"_EFFECT_SITE_CONTEXT names functions that no longer contain an "
        f"_effect call: {sorted(known - found)}")


def test_the_known_gaps_are_still_gaps_and_not_quietly_more():
    """If one of these is fixed, this test fails and the entry is deleted --
    which is the point: the gap list shrinks deliberately, never by drift."""
    gaps = {k for k, v in _EFFECT_SITE_CONTEXT.items()
            if v not in ("REACHED", "ADOPTS")}
    assert gaps == {"_reopen_reverted_issues", "_pr"}, sorted(gaps)
    src = RUN_QUEUE.read_text()
    assert src.count("BIRCHER_RUN_ID=") == 1, (
        "BIRCHER_RUN_ID is assigned somewhere new; the gap analysis above "
        "assumed run_item is the only place a run id is minted")


# --- the work-repo directive, RENDERED rather than grepped --------------------

def _rendered_prompt(workdir, repo, vendor="codex", reviewer="claude_code"):
    """Evaluate run_item's actual prompt assignment in bash.

    A source-text assertion here would only prove the directive is present.
    What matters is what it RENDERS to -- a directive that names the wrong
    variable, or interpolates nothing, reads fine and instructs nothing.
    """
    body = _run_item()
    start = body.index('prompt="IMPLEMENTER VENDOR DIRECTIVE')
    end = body.index('${prompt}"', start) + len('${prompt}"')
    script = (f'WORKDIR={workdir}\nREPO={repo}\nvendor={vendor}\n'
              f'RECOVERY_REVIEWER={reviewer}\nprompt="ORIGINAL ITEM TEXT"\n'
              f'{body[start:end]}\nprintf %s "$prompt"')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_implementer_is_told_which_repo_to_work_in():
    """The agent bundles hardcode `git -C /workspaces/muesli worktree add`, so
    without this an implementer on ANY other target branches from -- and pushes
    to -- muesli whatever WORKDIR says. That is why a throwaway-repo end-to-end
    run was unsafe until now.
    """
    out = _rendered_prompt("/workspaces/smoke", "abedegno/bircher-smoke")

    assert "git -C /workspaces/smoke worktree add" in out, (
        "the directive does not spell the worktree command with WORKDIR, so it "
        "does not actually override the bundle's literal path")
    assert "git -C /workspaces/smoke fetch origin main" in out
    assert "abedegno/bircher-smoke" in out, "the target repo is never named"
    assert "ORIGINAL ITEM TEXT" in out, "the item's own prompt was dropped"


def test_the_directive_is_present_for_the_default_target_too():
    """Stated unconditionally. A directive that appears only in the unusual
    case is one nobody has read when the unusual case arrives."""
    out = _rendered_prompt("/workspaces/muesli", "abedegno/muesli")
    assert "git -C /workspaces/muesli worktree add" in out
    assert "WORK REPO DIRECTIVE" in out


def test_every_adopting_function_adopts_before_its_first_effect():
    """ADOPTS is a claim about ORDER, and order is the whole content of it.

    `_kernel_adopt_run` sets BIRCHER_RUN_ID and re-fences a generation. An
    effect above that call runs with whatever the previous item left exported
    -- or with nothing, aborting on `${VAR:?}` and being swallowed. Both are
    silent. Asserting the call merely EXISTS in the function would pass in
    either case, which is why this asserts it comes first.
    """
    import re
    src = RUN_QUEUE.read_text().splitlines()
    spans, fn, start = {}, None, None
    for i, l in enumerate(src):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", l)
        if m:
            fn, start = m.group(1), i
        elif l == "}" and fn is not None:
            spans[fn] = (start, i)
            fn = None

    adopting = [k for k, v in _EFFECT_SITE_CONTEXT.items() if v == "ADOPTS"]
    assert adopting, "no function claims to adopt; this test would prove nothing"

    for name in adopting:
        a, b = spans[name]
        body = src[a:b]
        adopt_at = next((i for i, l in enumerate(body)
                         if "_kernel_adopt_run" in l and not l.strip().startswith("#")), None)
        effect_at = next((i for i, l in enumerate(body)
                          if re.search(r"(?<!_)\b_effect\s+\w", l)
                          and not l.strip().startswith("#")), None)
        assert adopt_at is not None, f"{name} is marked ADOPTS but never calls _kernel_adopt_run"
        assert effect_at is not None, f"{name} has no _effect call; drop it from the table"
        assert adopt_at < effect_at, (
            f"{name} performs an effect at body line {effect_at} before adopting "
            f"a run at {adopt_at}: that effect runs under a stale generation or "
            f"none at all, and fails silently either way")
