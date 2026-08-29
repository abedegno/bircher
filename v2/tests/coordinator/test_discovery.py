"""Finding an item's PR when branch-code discovery failed."""
import json

import pytest

from coordinator.ci import GhError
from coordinator.discovery import by_code, by_issue, closes_issue


@pytest.mark.parametrize("body,expected", [
    ("Closes #711", True), ("closes #711", True), ("Closed #711", True),
    ("Fixes #711", True), ("fixed #711", True), ("Fix #711", True),
    ("Resolves #711", True), ("resolve #711", True), ("resolved #711", True),
    ("Closes: #711", True), ("Closes:#711", True), ("Closes  #711", True),
    ("Closes #7110", False),
    ("Closes #71", False),
    ("mentions #711 in passing", False),
    ("Related to #711", False),
    ("See #711", False),
    ("", False),
])
def test_only_a_closing_keyword_links_an_issue(body, expected):
    """"Related to #711" is not a link. Adopting on a mention would take any PR
    that referenced the issue in passing."""
    assert closes_issue(body, "711") is expected


def test_the_issue_number_is_not_a_pattern():
    """An issue number is data. Unescaped, `7.1` would match `7x1`."""
    assert closes_issue("Closes #7x1", "7.1") is False
    assert closes_issue("Closes #7.1", "7.1") is True


def test_no_issue_links_nothing():
    assert closes_issue("Closes #711", "") is False


def test_by_issue_returns_only_prs_that_CLOSE_it():
    payload = [{"number": 1, "body": "Closes #711"},
               {"number": 2, "body": "Related to #711"},
               {"number": 3, "body": "fixes #711"}]
    assert by_issue("o/r", "711", gh=lambda a: json.dumps(payload)) == ["1", "3"]


def test_by_issue_is_empty_when_the_lookup_fails():
    def boom(args):
        raise GhError("rate limited")
    assert by_issue("o/r", "711", gh=boom) == []


def test_by_issue_survives_a_malformed_response():
    assert by_issue("o/r", "711", gh=lambda a: "not json") == []
    assert by_issue("o/r", "711", gh=lambda a: json.dumps({"x": 1})) == []


def test_by_code_matches_on_a_token_boundary():
    payload = [{"number": 1, "headRefName": "i23-fix"},
               {"number": 2, "headRefName": "i230-other"},
               {"number": 3, "headRefName": "feat/i23"}]
    assert by_code("o/r", "i23", gh=lambda a: json.dumps(payload)) == ["1", "3"]


def test_by_code_is_empty_when_the_lookup_fails():
    def boom(args):
        raise GhError("boom")
    assert by_code("o/r", "i23", gh=boom) == []


# --- sibling reconciliation (plan task 3) ------------------------------------

from coordinator.discovery import reconcile


def _listing(numbers):
    return lambda args: json.dumps(
        [{"number": int(n), "headRefName": f"i23-{n}"} for n in numbers])


def test_a_single_match_is_returned_untouched():
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5"]),
                    ci_of=lambda n: "green",
                    close=lambda m, g: closed.append(m))
    assert got == "5" and closed == []


def test_a_ci_GREEN_sibling_is_adopted_and_the_others_closed():
    """Run #20 #141: a CI-red retry opened a second PR before the coordinator
    died. Adopting the green one is the recovery."""
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
                    ci_of=lambda n: "green" if n == "6" else "red",
                    close=lambda m, g: closed.append(m))
    assert got == "6" and closed == ["5"]


def test_with_no_green_sibling_the_tracked_pr_is_kept_and_nothing_is_closed():
    """Closing on no evidence would destroy the only candidate."""
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
                    ci_of=lambda n: "red", close=lambda m, g: closed.append(m))
    assert got == "5" and closed == []


def test_the_adopted_pr_is_never_closed():
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5", "6", "7"]),
                    ci_of=lambda n: "green" if n == "6" else "red",
                    close=lambda m, g: closed.append(m))
    assert got == "6"
    assert got not in closed
    assert sorted(closed) == ["5", "7"]


def test_the_superseding_pr_is_named_to_the_closer():
    """The close comment says which PR superseded it. Without the second
    argument it would say nothing useful to whoever reads the closed PR."""
    seen = []
    reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
              ci_of=lambda n: "green" if n == "6" else "red",
              close=lambda m, g: seen.append((m, g)))
    assert seen == [("5", "6")]


def test_no_code_means_no_reconciliation():
    closed = []
    assert reconcile("o/r", "", "5", gh=_listing(["5", "6"]),
                     ci_of=lambda n: "green",
                     close=lambda m, g: closed.append(m)) == "5"
    assert closed == []
