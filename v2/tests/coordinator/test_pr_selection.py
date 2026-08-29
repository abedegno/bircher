"""Choosing an item's pull request."""
import subprocess

import pytest

from coordinator.pr_selection import Choice, is_abandoned, matches_code, select


@pytest.mark.parametrize("state,merged,expected", [
    ("CLOSED", "", True),
    ("CLOSED", "null", True),
    ("CLOSED", None, True),
    ("CLOSED", "2026-08-01T10:00:00Z", False),
    ("OPEN", "", False),
    ("MERGED", "2026-08-01T10:00:00Z", False),
])
def test_only_a_closed_unmerged_pr_is_abandoned(state, merged, expected):
    assert is_abandoned(state, merged) is expected


def test_the_string_null_is_not_a_timestamp():
    """`gh` reports an unmerged PR's mergedAt as "null" in some queries.
    Reading it as a merge time would mark every closed-unmerged PR merged."""
    assert is_abandoned("CLOSED", "null") is True


@pytest.mark.parametrize("branch,code,expected", [
    ("i23-fix-thing", "i23", True),
    ("feat/i23", "i23", True),
    ("wip-i23", "i23", True),
    ("i230-other", "i23", False),
    ("xi23", "i23", False),
    ("i23x", "i23", False),
    ("I23-upper", "i23", True),
    ("i23", "i23", True),
    ("anything", "", False),
])
def test_the_code_must_sit_on_a_token_boundary(branch, code, expected):
    """#22: a bare substring test makes `i23` match `i230-...`, so an item
    adopts its neighbour's PR and reports a merge it never made."""
    assert matches_code(branch, code) is expected


def test_a_regex_metacharacter_in_a_code_is_not_a_pattern():
    """An item code is data. Unescaped, `i.3` would match `i23`."""
    assert matches_code("i23-x", "i.3") is False
    assert matches_code("i.3-x", "i.3") is True


def test_an_explicit_signal_wins_over_discovery():
    assert select("279", ["1", "2"]) == Choice("use-signal", "279")


def test_exactly_one_match_is_used():
    assert select("", ["297"]) == Choice("use-the-one-match", "297")


def test_no_matches_is_no_match():
    assert select("", []) == Choice("no-match", "")
    assert select("", "") == Choice("no-match", "")


def test_two_matches_ESCALATE_rather_than_choosing():
    """Choosing would be a guess about which PR an item produced, and a wrong
    guess merges someone else's work under this item's name."""
    c = select("", ["297", "298"])
    assert c.decision == "ambiguous/escalate"
    assert "297" in c.value and "298" in c.value


def test_whitespace_separated_matches_are_accepted_like_the_shell_passed_them():
    assert select("", "297 298").decision == "ambiguous/escalate"
    assert select("", "  297  ") == Choice("use-the-one-match", "297")


# --- differential against the bash these replaced ----------------------------

#: Frozen, because run-queue.sh now delegates: reading it live would compare
#: the port against itself and prove nothing.
_ORIGINAL_BASH = {
    '_pr_is_abandoned': """_pr_is_abandoned() {
  local state="$1" merged="${2:-}"
  [ "$state" = "CLOSED" ] || return 1
  [ -z "$merged" ] || [ "$merged" = "null" ]
}""",
    '_select_pr_candidate': """_select_pr_candidate() {
  local signal="$1" matches="$2"
  if [ -n "$signal" ]; then
    printf 'use-signal|%s\\n' "$signal"
    return 0
  fi
  set -- $matches
  case "$#" in
    0) printf 'no-match|\\n' ;;
    1) printf 'use-the-one-match|%s\\n' "$1" ;;
    *) printf 'ambiguous/escalate|%s\\n' "$*" ;;
  esac
}"""
}


def _bash(fn, call, **env):
    script = "set -uo pipefail\n" + _ORIGINAL_BASH[fn] + "\n" + call
    e = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=e)


@pytest.mark.parametrize("state,merged", [
    ("CLOSED", ""), ("CLOSED", "null"), ("CLOSED", "2026-08-01T10:00:00Z"),
    ("OPEN", ""), ("MERGED", "2026-08-01T10:00:00Z"), ("", ""),
])
def test_is_abandoned_agrees_with_the_bash(state, merged):
    r = _bash("_pr_is_abandoned", '_pr_is_abandoned "$S" "$M"', S=state, M=merged)
    assert is_abandoned(state, merged) is (r.returncode == 0), (state, merged)


@pytest.mark.parametrize("signal,matches", [
    ("", ""), ("", "297"), ("", "297 298"), ("279", "297 298"), ("279", ""),
    ("", "  297  "), ("", "1 2 3"),
])
def test_select_agrees_with_the_bash(signal, matches):
    r = _bash("_select_pr_candidate", '_select_pr_candidate "$SIG" "$M"',
              SIG=signal, M=matches)
    out = r.stdout.strip()
    c = select(signal, matches)
    mine = f"{c.decision}|{c.value}"
    assert mine == out, f"python={mine!r} bash={out!r}"
