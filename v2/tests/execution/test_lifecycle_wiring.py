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
    # Only caller is merge_ready_pr's revert path, which is itself REACHED.
    "_reopen_reverted_issues": "REACHED",
    # ADOPTS = mints or re-adopts the item's run and re-fences a generation
    # before its first effect, so it runs under a valid one of its own.
    "recover_pr_cmd": "ADOPTS",
    "reconcile_deferred_ready": "ADOPTS",
}

#: WHAT THESE TESTS CAN AND CANNOT SHOW. The table is a REVIEWED CLAIM about
#: runtime reachability, and "REACHED" is asserted by reading the call graph,
#: not proven here -- a static test cannot know which functions run under
#: run_item. What is enforced is narrower and still worth having: that every
#: _effect site is classified (so a new one cannot join silently), that the
#: functions claiming to adopt do so BEFORE their first effect, and that
#: BIRCHER_RUN_ID is still assigned in exactly one place, which is the
#: assumption the whole analysis rests on.
#:
#: An earlier version of this table listed `_pr` as a gap. It was a parser
#: artefact: `_pr` is a ONE-LINE function whose `}` sits on the same line, so
#: the span never closed and it "owned" every later line -- including an
#: `_effect` occurrence inside a quoted self-test assertion, which is not a
#: call site at all. It also listed `_reopen_reverted_issues` as a gap when its
#: only caller is REACHED. Both survived because the gap test compared the
#: table against ITSELF.


#: Quoted spans, stripped before looking for a call. `_effect` inside a string
#: is a self-test ASSERTION about a call site, not one.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _effect_sites_by_function():
    src = RUN_QUEUE.read_text().splitlines()
    spans, fn, start = {}, None, None
    for i, l in enumerate(src):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", l)
        if m:
            # A one-liner opens and closes on the same line. Without this its
            # span never closes and it silently owns the rest of the file.
            if l.rstrip().endswith("}"):
                spans[m.group(1)] = (i, i)
                continue
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
        if re.search(r"(?<!_)\b_effect\s+\w", _QUOTED.sub("", l)):
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


def test_the_run_id_is_still_minted_in_exactly_one_place():
    """The assumption the whole classification rests on.

    An earlier version of this test asserted the GAP LIST instead -- a set
    compared against the table it was derived from, which is a tautology and
    passed while two of its four entries were wrong. This asserts something the
    table cannot fake: BIRCHER_RUN_ID is assigned by run_item and nowhere else,
    so every other _effect site either inherits it or adopts one.
    """
    src = RUN_QUEUE.read_text()
    assigns = [l.strip() for l in src.splitlines()
               if "BIRCHER_RUN_ID=" in l and not l.strip().startswith("#")]
    # TWO now, and the second is deliberate: the sweep restores the run id
    # RECORDED with a deferred row rather than guessing by item code. It mints
    # nothing -- it reuses an id the kernel already holds -- so the
    # "run_item is the only place a run is CREATED" premise still stands.
    assert len(assigns) == 2, (
        "BIRCHER_RUN_ID is assigned somewhere new; every REACHED/ADOPTS "
        f"classification above assumed the places it is set are known: {assigns}")
    assert any('="${item}-$(date +%s)"' in a for a in assigns), (
        "the minting assignment in run_item is gone or changed shape")
    assert any('="$deferred_run"' in a for a in assigns), (
        "the sweep no longer restores the recorded run id, so it is back to "
        "guessing by item code")


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


# --- binding variables must outlive the branch that fills them ----------------

def test_binding_variables_are_declared_at_run_item_scope():
    """`local` inside a branch, read outside it, is a crash under `set -u`.

    run-queue.sh runs with `set -uo pipefail`. `_out_hash` was declared
    `local` inside `if [ -n "$marker" ]` and read at the merge gate, which
    EVERY path reaches. The marker path was fine; the no-marker recovery path
    -- which fires automatically whenever an implementer session dies -- died
    with `_out_hash: unbound variable`, on a real run, after the PR had already
    been opened.

    The suite could not have caught it: the argument-wiring harness drives the
    MARKER path only, so the branch that declares the variable always ran. This
    is structural because the behavioural version would have to drive
    `recover_from_ground_truth` for real.
    """
    body = _run_item().splitlines()
    base = "  "                      # run_item's own body indent

    used_in_bindings = set()
    for _, logical in _logical_lines():
        if "_kernel_record_review" in logical or "_kernel_request_merge" in logical:
            used_in_bindings.update(re.findall(r'"\$(_[a-z_]+)"', logical))
    assert used_in_bindings, "found no binding variables; the parser is wrong"

    for var in sorted(used_in_bindings):
        decls = [l for l in body if re.match(rf"\s*local\s+{var}\b", l)]
        assert decls, f"${var} is used in a binding but never declared local in run_item"
        for d in decls:
            indent = d[:len(d) - len(d.lstrip())]
            assert indent == base, (
                f"${var} is declared at indent {len(indent)} (inside a branch) but read "
                f"at the merge gate, which every path reaches: under `set -u` the "
                f"path that skips that branch dies with 'unbound variable'.\n  {d.strip()}")


@pytest.mark.parametrize("stage", [
    "_kernel_record_output", "_kernel_record_ci",
    "_kernel_record_review", "_kernel_request_merge",
])
def test_recover_pr_cmd_drives_the_lifecycle_too(stage):
    """`--recover-pr` adopts a run, which gives its effects a generation and
    gives the kernel no EVIDENCE. A live probe left the run at `queued` with
    only a comment and a status_check journalled, and the merge refused --
    correctly, since nothing had recorded an output, a CI observation or a
    verdict for it to authorize against.

    Adoption was necessary and not sufficient; this is the sufficient half.
    """
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = "\n".join(l for l in src[start:end] if not l.strip().startswith("#"))
    assert stage in body, f"recover_pr_cmd never calls {stage}"


def test_recover_pr_cmd_resolves_a_halt_before_it_acts():
    """Order again, and again it is the whole content.

    `perform` refuses every effect on a halted run, so a halted run adopted
    here would have each subsequent effect declined -- correctly, and with no
    way forward. Reconciling after the first effect would be reconciling after
    the thing it exists to unblock has already failed.
    """
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = [l for l in src[start:end] if not l.strip().startswith("#")]

    import re
    recon = next((i for i, l in enumerate(body) if "_kernel_pending" in l), None)
    effect = next((i for i, l in enumerate(body)
                   if re.search(r"(?<!_)\b_effect\s+\w", l)), None)
    assert recon is not None, "recover_pr_cmd never checks for a halt"
    # Checking for a halt and never resolving it is a check with no consequence.
    # Deleting the _kernel_reconcile call left every test passing.
    assert any("_kernel_reconcile" in l for l in body), (
        "recover_pr_cmd detects a halt and never calls _kernel_reconcile, so "
        "the halt it found is never resolved")
    assert effect is not None, "recover_pr_cmd has no _effect call; update this test"
    assert recon < effect, (
        f"the halt check is at body line {recon} but an effect runs at {effect}: "
        "on a halted run that effect is refused and the halt is never resolved")


def test_the_merge_key_distinguishes_attempts():
    """`merge:<pr>:<head>` was stable across reconciliations, so a retry after
    one replayed a spent key: the kernel returned the resolved attempt's null
    external id without executing, and merge_ready_pr read that as "merged,
    sha unknown" for a PR that was still open.

    The generation is the kernel's own notion of a distinct attempt, so retries
    within one attempt still collapse to a single effect while a new attempt
    gets a new key.
    """
    src = RUN_QUEUE.read_text()
    key = next(l for l in src.splitlines()
               if "_effect merge " in l and not l.strip().startswith("#"))
    assert "BIRCHER_GENERATION" in key, (
        f"the merge key does not distinguish attempts, so a retry after a "
        f"reconciliation replays a spent key:\n  {key.strip()}")
    assert "$expected_sha" in key, (
        "the merge key must still pin the head it was reviewed against")


def test_recover_pr_cmd_writes_back_to_the_issue():
    """This path had no write-back, so a recovered item left its issue carrying
    `bircher:running` after the PR had merged -- a label meaning "being worked"
    saying so about finished work. It needed clearing by hand after the muesli
    merge, and residue like that is how a label stops being trusted."""
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = "\n".join(l for l in src[start:end] if not l.strip().startswith("#"))
    assert "_issue_writeback" in body, "recover_pr_cmd never writes back to the issue"
    assert "closingIssuesReferences" in body, (
        "the issue must come from the PR's own closing references: this path "
        "adopts a PR and may never have seen the queue item that created it")


@pytest.mark.parametrize("fn", ["recover_pr_cmd", "run_item"])
def test_an_empty_recovery_tuple_is_treated_as_a_failure(fn):
    """recover_from_ground_truth has ONE exit and always emits five fields, so
    no output means it died before reaching that line -- and `rec=$(...)`
    swallows the death into an empty string. Parsed straight it reads as
    outcome="" and the caller reports "NOT ready", which is a benign-looking
    sentence for "the recovery crashed". Seen once on the smoke repo and not
    reproducible since; the misreading is the part worth making impossible."""
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith(f"{fn}()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = [l for l in src[start:end] if not l.strip().startswith("#")]

    call = next(i for i, l in enumerate(body) if "rec=$(recover_from_ground_truth" in l)
    parse = next(i for i, l in enumerate(body)
                 if "read -r" in l and ("r_outcome" in l or "outcome review note" in l))
    window = "\n".join(body[call:parse])
    assert "${rec//[[:space:]]/}" in window, (
        f"{fn} parses the recovery tuple without checking it is non-empty, so a "
        "crashed recovery reads as a verdict of ''")


def test_reconciliation_is_scoped_to_the_class_the_observation_speaks_to():
    """Cross-vendor HIGH. The first version applied one PR-merge observation to
    EVERY pending key regardless of class, so an uncertain comment,
    status_check or ref_update was 'resolved' by evidence that said nothing
    about it -- an observation about one PR presented as an observation about
    each effect."""
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = "\n".join(l for l in src[start:end] if not l.strip().startswith("#"))

    assert 'effect_class") == "merge"' in body, (
        "reconciliation does not filter by effect class, so a PR-merge "
        "observation resolves effects it says nothing about")
    assert "_unspoken" in body, (
        "effects the observation cannot speak to must be reported, not "
        "silently left out of the log")


def test_the_reconciliation_version_is_re_read_per_key():
    """Each successful reconciliation bumps the run version under CAS, so a
    version captured once and reused made every reconciliation after the first
    stale -- and the advisory wrapper swallowed the failure while the script
    printed 'reconciled' anyway."""
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = src[start:end]

    loop = next(i for i, l in enumerate(body) if "while IFS= read -r _k" in l)
    done = next(i for i in range(loop, len(body)) if body[i].strip() == "EOF")
    inside = "\n".join(body[loop:done])
    assert "_pver=$(_kernel_pending" in inside, (
        "the CAS version is read outside the reconciliation loop, so every key "
        "after the first reconciles against a version the run has left behind")


def test_the_sweep_prefers_the_run_that_opened_the_PR():
    """Cross-vendor MEDIUM. Adoption chose the newest run sharing an item code,
    which is not necessarily the run that opened the PR being swept: a re-queued
    item creates a newer run, and the sweep would attribute the older run's PR
    to it and ask the kernel to revalidate the merge against the wrong attempt's
    authorization. Adopting by code was a guess that looked like a lookup.

    The deferred row now carries the run id, and an OLD row without one still
    parses -- falling back to adoption by code, exactly as before, so an
    existing queue does not become unreadable because a field was added.
    """
    src = RUN_QUEUE.read_text()
    assert '"$item" "$pr" "$issue" "$sha" "${BIRCHER_RUN_ID:-}"' in src, (
        "_record_deferred_ready no longer records the run id, so the sweep is "
        "back to guessing by item code")

    lines = src.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("reconcile_deferred_ready()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    body = "\n".join(l for l in lines[start:end] if not l.strip().startswith("#"))
    assert 'deferred_run=' in body, "the sweep never parses the recorded run id"
    assert 'if [ -n "$deferred_run" ]' in body, (
        "the sweep parses the run id and does not prefer it")
    assert "_kernel_adopt_run" in body, (
        "the by-code fallback is gone, so a row written before the run id "
        "existed can no longer be swept")


def test_the_state_check_distinguishes_its_three_causes():
    """It collapsed "past these stages", "no such run" and "kernel
    unreachable" into one branch that printed the first -- so an unreachable
    kernel was reported as a run that had progressed. A false claim about the
    source, in code added to fix a different instance of exactly that."""
    src = RUN_QUEUE.read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("recover_pr_cmd()"))
    end = next(i for i in range(start + 1, len(src)) if src[i] == "}")
    body = "\n".join(l for l in src[start:end] if not l.strip().startswith("#"))

    assert "kernel unreachable" in body, "an unreachable kernel is not distinguished"
    assert "knows no run" in body, "a missing run is not distinguished"
    assert "past the lifecycle stages" in body, "the benign case lost its message"
