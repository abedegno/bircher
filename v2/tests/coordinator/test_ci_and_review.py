"""The CI classifiers and verdict extraction, plus a DIFFERENTIAL check.

A port's risk is not that the new code is wrong in the abstract -- it is that
it disagrees with the code it replaces on an input nobody thought of. So the
property tests below are paired with tests that run the ORIGINAL bash and the
new Python on the same inputs and require identical answers.
"""
import json
import pathlib
import subprocess

import pytest

from coordinator.ci import (GhError, classify_failure, drop_ignored,
                            keep_blocking, normalize)
from coordinator.review import extract_verdict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


#: The bash these replaced, CAPTURED AS IT SHIPPED.
#:
#: Frozen rather than read live, because run-queue.sh now DELEGATES to this
#: Python -- reading it back would compare the port against itself and prove
#: nothing. Frozen text keeps the differential honest: change the Python and it
#: must still answer exactly as the shell did on the day it was replaced.
_ORIGINAL_BASH = {
    '_normalize_ci': """_normalize_ci() {
  local buckets="$1"
  # `case`, not `${buckets//[[:space:]]/}`: that global substitution walks and rebuilds
  # the whole string and is pathologically slow on a large one -- a 140KB input took
  # minutes, which is how it was found. A single pattern match answers the same question
  # ("is there any non-whitespace character?") in one pass. Buckets are normally tiny, so
  # this never bit in production, but the SIGPIPE fixture below has to be big.
  case "$buckets" in *[![:space:]]*) : ;; *) echo pending; return ;; esac
  # PIPE-FREE, and this one is not cosmetic. Under `set -o pipefail`, `grep -q` exits
  # the instant it matches and the producing `printf` takes SIGPIPE; the pipeline then
  # reports FAILURE despite the match. On this function that inverts a merge-safety
  # verdict: a bucket list beginning with `fail` matches, grep exits, printf dies at 141,
  # the `if` is skipped -- and so is the pending check, for the same reason -- and red CI
  # is reported GREEN. Whether it fires depends on buffer size and scheduling, so it
  # would present as a rare, unreproducible bad merge.
  local line red=0 pend=0
  while IFS= read -r line; do
    case "$line" in
      fail|cancel) red=1 ;;
      pending)     pend=1 ;;
    esac
  done <<EOF
$buckets
EOF
  [ "$red" = 1 ]  && { echo red; return; }
  [ "$pend" = 1 ] && { echo pending; return; }
  echo green
}""",
    '_extract_verdict': """_extract_verdict() {
  local last
  last=$(printf '%s\\n' "$1" | sed 's/[[:space:]]*$//' | grep -v '^$' | tail -n1)
  # Normalise the decoration real agents emit around a final line. Byte-exact
  # matching escalates on `**VERDICT: PASS**`, which is benign output rather than
  # a malformed review -- but the grammar accepted here must stay NARROW, because
  # this string authorises an automatic merge.
  #
  # Accepted: balanced markdown/code ornament in any order, plus AT MOST ONE
  # terminal period or exclamation. Rejected (fails closed): `VERDICT: PASS!!!`
  # and `VERDICT: PASS...`, which are not ornament but non-contractual output.
  #
  # Stripping is order-INDEPENDENT and bounded. A first attempt stripped all
  # decoration then punctuation, which made `` `VERDICT: FAIL`. `` fail while
  # `` `VERDICT: FAIL.` `` passed -- the trailing tick was never reconsidered.
  # The loop removes one character per side per pass instead, so interleaving
  # does not matter, and the bound stops a line of pure decoration ever
  # normalising into a verdict.
  local _punct=0 _i=0 _before
  while [ "$_i" -lt 8 ]; do
    _before="$last"
    last="${last#"${last%%[![:space:]]*}"}"
    last="${last%"${last##*[![:space:]]}"}"
    case "$last" in [*\\`_]*) last="${last#?}" ;; esac
    case "$last" in
      *[*\\`_]) last="${last%?}" ;;
      *[.!])    [ "$_punct" -eq 0 ] && { last="${last%?}"; _punct=1; } ;;
    esac
    [ "$last" = "$_before" ] && break
    _i=$((_i + 1))
  done
  case "$last" in
    "VERDICT: PASS") printf 'PASS' ;;
    "VERDICT: FAIL") printf 'FAIL' ;;
    *) [ -n "$last" ] && echo "[batch] WARN: review's final line is not a bare verdict -> treating as no verdict" >&2 ;;
  esac
}""",
    '_drop_ignored': """_drop_ignored() {
  printf '%s\\n' "$1" | grep -vE "^(${BIRCHER_CI_IGNORE_CHECKS:-Dependabot|review-gate})\\|"
}""",
    '_keep_blocking_checks': """_keep_blocking_checks() {
  local lines="$1" required="$2" filtered name _r
  filtered=$(_drop_ignored "$lines")
  if [ -z "$required" ]; then
    printf '%s\\n' "$filtered" | cut -d'|' -f2,3
    return
  fi
  local kept
  kept=$(printf '%s\\n' "$filtered" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    name="${line%%|*}"
    if printf '%s\\n' "$required" | grep -Fxq "$name"; then
      # PROJECT to exactly status|conclusion. #73 added a fourth field (the producing
      # app), and `_checkrun_state`'s allowlist is strict by design since #67 -- handing
      # it `status|conclusion|app` would match nothing and read RED for every check.
      _r=${line#*|}; printf '%s\\n' "${_r%|*}"
    fi
  done)
  # A required-set that matches NOTHING is a misconfiguration or a naming mismatch
  # (contexts that never run on this event, a renamed job), not a genuine "no checks".
  # Returning empty there reads as "CI has not registered yet" -> pending forever, or
  # worse, hides a red. Fall back to ignore-list-only, which errs toward red.
  #
  # REJECTED (2026-08-16, raised by review): "require EVERY required context to be
  # present, else pending". That is right for a PR head and wrong here. Required
  # contexts are a branch-protection property of the PR, and the post-merge watcher
  # reads a MERGE COMMIT, where most of them legitimately never report -- muesli's
  # merge commits carry 0 statuses and a combined `pending`, and `review-gate` /
  # `bircher/cross-review` only ever appear on PR heads. Demanding completeness there
  # would make every post-merge watch poll to timeout, i.e. halt the pipeline. The
  # completeness this function CANNOT provide is instead enforced upstream, by
  # _commit_ci_lines failing closed on any response that is not whole.
  if [ -z "${kept//[[:space:]]/}" ] && [ -n "${filtered//[[:space:]]/}" ]; then
    printf '%s\\n' "$filtered" | cut -d'|' -f2,3
    return
  fi
  printf '%s\\n' "$kept"
}"""
}


def _bash(fn_names, call, arg=""):
    """Run the ORIGINAL bash implementation against `$ARG`.

    The argument travels in the ENVIRONMENT, never interpolated into the
    script. Embedding it via Python's repr silently broke every multi-line
    case: `\n` inside bash single quotes is a literal backslash-n, so the
    function received one line where the test meant two, and the differential
    "failures" were the harness disagreeing with itself.
    """
    names = [fn_names] if isinstance(fn_names, str) else fn_names
    script = ("set -uo pipefail\n"
              + "\n".join(_ORIGINAL_BASH[n] for n in names) + "\n" + call)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin", "ARG": arg,
                            "REQ": ""})
    return r.stdout


# --- normalize ---------------------------------------------------------------

@pytest.mark.parametrize("buckets,expected", [
    ("pass\npass", "green"),
    ("pass\nfail", "red"),
    ("pass\npending", "pending"),
    ("cancel", "red"),
    ("fail\npending", "red"),
    ("", "pending"),
    ("   \n  ", "pending"),
])
def test_normalize_classifies_buckets(buckets, expected):
    assert normalize(buckets) == expected


def test_empty_is_PENDING_not_green():
    """No checks reported yet is the absence of a verdict. Reading it as
    success would merge a PR whose CI had not started."""
    assert normalize("") == "pending"


def test_red_beats_pending():
    """A failure among still-running checks is already a failure."""
    assert normalize("pending\nfail") == "red"


@pytest.mark.parametrize("buckets", [
    "pass\npass", "pass\nfail", "pass\npending", "", "cancel", "fail\npending",
])
def test_normalize_agrees_with_the_bash_it_replaced(buckets):
    out = _bash("_normalize_ci", '_normalize_ci "$ARG"', arg=buckets).strip()
    assert normalize(buckets) == out, f"python={normalize(buckets)!r} bash={out!r}"


# --- drop_ignored / keep_blocking --------------------------------------------

def test_review_gate_is_dropped_or_the_derivation_deadlocks():
    """`review-gate` stays pending until a cross-vendor review is posted, and
    the caller is the thing about to post one. Each waits for the other."""
    lines = "review-gate|pending|x\nbuild|pass|y"
    assert "review-gate" not in drop_ignored(lines)
    assert "build" in drop_ignored(lines)


def test_with_no_required_contexts_every_non_ignored_check_blocks():
    assert keep_blocking("build|pass|x\ntest|fail|y", "") == "pass|x\nfail|y"


def test_only_required_contexts_block_when_they_are_declared():
    assert keep_blocking("build|pass|x\nflaky|fail|y", "build") == "pass|x"


def test_a_required_list_matching_nothing_falls_back_to_all_checks():
    """Otherwise the filtered set is empty, which `normalize` reads as pending
    for ever -- a PR that can never merge and never says why."""
    assert keep_blocking("build|pass|x", "some-other-context") == "pass|x"


def test_the_fallback_does_not_fire_when_there_are_genuinely_no_checks():
    assert keep_blocking("", "build") == ""


# --- classify_failure --------------------------------------------------------

@pytest.mark.parametrize("count,expected", [
    (0, "infra"), (3, "genuine"), ("0", "infra"), ("2", "genuine"),
    ("", "infra"), (None, "infra"),
])
def test_a_red_run_with_no_failed_step_is_infrastructure(count, expected):
    assert classify_failure(count) == expected


# --- extract_verdict ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("VERDICT: PASS", "PASS"),
    ("VERDICT: FAIL", "FAIL"),
    ("findings\n\nVERDICT: PASS", "PASS"),
    ("**VERDICT: PASS**", "PASS"),
    ("`VERDICT: PASS`", "PASS"),
    ("VERDICT: PASS.", "PASS"),
    ("VERDICT: PASS!", "PASS"),
    ("  VERDICT: PASS  ", "PASS"),
    ("VERDICT: PASS\n\n\n", "PASS"),
])
def test_a_bare_final_verdict_is_read(text, expected):
    assert extract_verdict(text) == expected


@pytest.mark.parametrize("text", [
    "if the tests passed I would say VERDICT: PASS but they did not",
    "VERDICT: PASS\nactually, one more thing",
    "VERDICT: MAYBE",
    "",
    "\n\n",
    "no verdict at all",
    "VERDICT: PASS...",
])
def test_anything_that_is_not_a_bare_final_verdict_is_no_verdict(text):
    """A verdict mid-report is not a verdict, and `...` is prose. Reading
    either as approval would merge on a sentence."""
    assert extract_verdict(text) is None


@pytest.mark.parametrize("text", [
    "VERDICT: PASS", "VERDICT: FAIL", "**VERDICT: PASS**", "`VERDICT: FAIL`",
    "VERDICT: PASS.", "prose\nVERDICT: PASS", "VERDICT: MAYBE", "",
    "VERDICT: PASS...", "  VERDICT: PASS  ", "***VERDICT: PASS***",
])
def test_extract_verdict_agrees_with_the_bash_it_replaced(text):
    out = _bash("_extract_verdict", '_extract_verdict "$ARG"', arg=text).strip()
    mine = extract_verdict(text) or ""
    assert mine == out, f"python={mine!r} bash={out!r} for {text!r}"


# --- the shape that hung the self-test ---------------------------------------

def test_a_row_with_no_delimiter_passes_through_whole():
    """`cut -d'|' -f2,3` prints a delimiter-less line UNCHANGED, and real
    `gh pr checks` output arrives that way on at least one path. Returning
    empty instead made every check `pending`, which `_wait_ci` reads as "keep
    waiting" -- so the run hung rather than failing. The Python suite missed
    it; `--self-test` did not."""
    assert keep_blocking("pass\npass", "") == "pass\npass"
    assert normalize(keep_blocking("pass\npass", "")) == "green"


def test_a_delimiter_less_RED_row_is_still_red():
    assert normalize(keep_blocking("fail\npass", "")) == "red"


def test_mixed_delimited_and_bare_rows_both_survive():
    assert keep_blocking("build|pass\npending", "") == "pass\npending"


@pytest.mark.parametrize("lines", [
    "build|pass\ntest|pass",
    "pass\npass",                       # delimiter-less: the shape that hung
    "build|pass\nreview-gate|pending",  # the ignored check
    "Dependabot|fail\nbuild|pass",
    "",
    "build|pass|SUCCESS\ntest|fail|FAILURE",
])
def test_keep_blocking_agrees_with_the_bash_it_duplicates(lines):
    """The pair that diverged once already. Bash `cut` passes a delimiter-less
    line through whole; the first Python returned empty, which read as
    `pending` and hung the run."""
    out = _bash(["_drop_ignored", "_keep_blocking_checks"],
                '_keep_blocking_checks "$ARG" "$REQ"', arg=lines)
    assert keep_blocking(lines, "") == out.rstrip("\n"), (
        f"python={keep_blocking(lines, '')!r} bash={out.rstrip(chr(10))!r}")


# --- run id extraction -------------------------------------------------------

from coordinator.ci import poll, run_ids_from_links


def test_run_ids_are_pulled_from_check_links():
    lines = ("build|https://github.com/o/r/actions/runs/123/job/9\n"
             "test|https://github.com/o/r/actions/runs/456")
    assert run_ids_from_links(lines) == ["123", "456"]


def test_several_checks_in_one_run_yield_ONE_id():
    """`sort -u`. Re-running the same id once per check would multiply the CI
    cost of a single infrastructure failure by the number of jobs in it."""
    lines = ("a|https://github.com/o/r/actions/runs/123/job/1\n"
             "b|https://github.com/o/r/actions/runs/123/job/2")
    assert run_ids_from_links(lines) == ["123"]


def test_ignored_checks_contribute_no_run_ids():
    lines = ("review-gate|https://github.com/o/r/actions/runs/999\n"
             "build|https://github.com/o/r/actions/runs/123")
    assert run_ids_from_links(lines) == ["123"]


def test_a_link_that_is_not_a_workflow_run_is_skipped():
    assert run_ids_from_links("ext|https://example.com/status/7") == []


# --- the poll loop -----------------------------------------------------------

def _gh_returning(*sequence):
    calls = {"n": 0}

    def gh(args):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]
    gh.calls = calls
    return gh


def test_poll_returns_as_soon_as_ci_settles():
    slept = []
    out = poll("1", "", gh=_gh_returning("build|pass"), sleep=slept.append)
    assert out == "green"
    assert slept == [], "it must not sleep after a settled answer"


def test_poll_waits_while_pending_then_reports_the_settled_answer():
    gh = _gh_returning("build|pending", "build|pending", "build|fail")
    slept = []
    assert poll("1", "", gh=gh, sleep=slept.append, interval=5) == "red"
    assert slept == [5, 5], "one sleep per pending poll, no more"


def test_poll_gives_up_as_PENDING_not_red():
    """"I stopped looking" is not "the checks failed". Reporting red would fail
    a PR whose CI was merely slow; reporting green would merge one nobody
    watched."""
    slept = []
    out = poll("1", "", gh=_gh_returning("build|pending"), sleep=slept.append,
               timeout=10, interval=5)
    assert out == "pending"
    assert slept == [5, 5], "it must honour its own bound"


def test_poll_applies_the_required_contexts_filter():
    """Otherwise a non-required flaky check would hold every PR pending."""
    gh = _gh_returning("build|pass\nflaky|pending")
    assert poll("1", "build", gh=gh, sleep=lambda _s: None) == "green"


# --- required contexts and failure kind (plan task 1) ------------------------

from coordinator.ci import failure_kind, required_contexts


def test_required_contexts_unions_both_shapes():
    """Branch protection reports required checks under TWO keys, and a repo can
    use either. Reading one would leave the other's checks non-blocking."""
    payload = {"required_status_checks": {
        "contexts": ["build"],
        "checks": [{"context": "test"}, {"context": "build"}]}}
    gh = lambda args: json.dumps(payload)
    assert sorted(required_contexts("o/r", gh=gh).split()) == ["build", "test"]


def test_required_contexts_is_EMPTY_when_the_lookup_fails():
    """Empty means "everything blocks" downstream, which is conservative.
    Inventing a context list would make real checks non-blocking."""
    def boom(args):
        raise GhError("404")
    assert required_contexts("o/r", gh=boom) == ""


def test_required_contexts_is_fetched_once():
    calls = []

    def gh(args):
        calls.append(args)
        return json.dumps({"required_status_checks": {"contexts": ["build"]}})

    cache = {}
    required_contexts("o/r", gh=gh, cache=cache)
    required_contexts("o/r", gh=gh, cache=cache)
    assert len(calls) == 1, "branch protection is fetched once per run"


def test_a_red_run_with_failed_steps_is_genuine():
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        return json.dumps({"jobs": [{"conclusion": "failure",
                                     "steps": [{"conclusion": "failure"}]}]})
    assert failure_kind("7", gh=gh) == "genuine"


def test_a_red_run_with_NO_failed_step_is_infrastructure():
    """B-5: a runner never acquired, or a cancelled job, fails with zero failed
    steps. Burying those as `failed` buried three green PRs in one incident."""
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        return json.dumps({"jobs": [{"conclusion": "cancelled", "steps": []}]})
    assert failure_kind("7", gh=gh) == "infra"


def test_an_unreadable_run_is_GENUINE_not_infra():
    """Fails toward NOT re-running. `infra` triggers a re-run that costs CI
    minutes; if we cannot see why a run failed, spending them is a guess."""
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        raise GhError("boom")
    assert failure_kind("7", gh=gh) == "genuine"


def test_a_pr_with_no_workflow_runs_is_genuine():
    assert failure_kind("7", gh=lambda a: "external|https://example.com/x") == "genuine"
