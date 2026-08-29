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
