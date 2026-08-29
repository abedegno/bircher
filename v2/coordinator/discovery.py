"""Finding an item's pull request when discovery by branch code failed.

The closing-keyword rule is a scar: run #24's `a06-vs-i230` class had the
branch AND the signal on the wrong code, and only the PR body's write-back was
right. Without this the item reported no PR and re-ballooned.
"""

from __future__ import annotations

import json
import re

from coordinator.ci import GhError, _gh
from coordinator.pr_selection import matches_code

#: `Closes #N`, `Fixes: #N`, `resolved #N`. NOT `Related to #N` -- a mention is
#: not a link, and adopting on one would take any PR that referenced the issue
#: in passing.
_CLOSES = r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*:?\s*#"


def closes_issue(body: str, issue: str) -> bool:
    """Does this PR body CLOSE the issue, not merely mention it?"""
    if not issue:
        return False
    return bool(re.search(_CLOSES + re.escape(issue) + r"\b", body or ""))


def by_issue(repo: str, issue: str, *, gh=_gh) -> list[str]:
    """Open PRs whose body closes `issue`, newest first as gh returns them."""
    if not issue:
        return []
    try:
        prs = json.loads(gh(["pr", "list", "--repo", repo, "--state", "open",
                             "--search", f"{issue} in:body",
                             "--json", "number,body"]))
    except (GhError, ValueError):
        return []
    if not isinstance(prs, list):
        return []
    return [str(p["number"]) for p in prs
            if isinstance(p, dict) and p.get("number") is not None
            and closes_issue(p.get("body") or "", issue)]


def by_code(repo: str, code: str, *, gh=_gh) -> list[str]:
    """Open PRs whose head branch carries the item code on a token boundary."""
    if not code:
        return []
    try:
        prs = json.loads(gh(["pr", "list", "--repo", repo, "--state", "open",
                             "--json", "number,headRefName"]))
    except (GhError, ValueError):
        return []
    if not isinstance(prs, list):
        return []
    return [str(p["number"]) for p in prs
            if isinstance(p, dict) and matches_code(p.get("headRefName") or "", code)]
