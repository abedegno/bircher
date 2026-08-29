"""Which pull request belongs to this item.

Discovery is mechanism: it decides what the rest of the derivation observes, so
picking the wrong PR misattributes a merge. Pure -- every function takes what
`gh` returned and decides; the calls stay with their callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def is_abandoned(state: str, merged_at: str | None) -> bool:
    """A PR CLOSED without merging can never satisfy its item.

    `gh` reports an unmerged PR's `mergedAt` as empty or the string "null"
    depending on how it is queried, and both mean the same thing. Treating
    "null" as a timestamp would read every closed-unmerged PR as merged --
    which is the i506 scratch-PR case this exists for.
    """
    if state != "CLOSED":
        return False
    return not merged_at or merged_at == "null"


def matches_code(branch: str, code: str) -> bool:
    """Does this branch name carry the item code on a TOKEN BOUNDARY?

    The boundaries are load-bearing. A bare substring test makes `i23` match
    `i230-...`, so an item adopts its neighbour's PR and reports a merge it
    never made. Issue #22.
    """
    if not code:
        return False
    pat = re.compile(rf"(^|[^a-z0-9]){re.escape(code.lower())}([^a-z0-9]|$)")
    return bool(pat.search(branch.lower()))


@dataclass(frozen=True)
class Choice:
    decision: str          # use-signal | use-the-one-match | no-match | ambiguous/escalate
    value: str = ""


def select(signal: str, matches) -> Choice:
    """An explicit signal wins; otherwise exactly one match is required.

    TWO OR MORE MATCHES ESCALATE rather than picking. Choosing between them
    would be a guess about which PR an item produced, and a wrong guess merges
    someone else's work under this item's name.
    """
    if signal:
        return Choice("use-signal", signal)
    if isinstance(matches, str):
        matches = matches.split()
    matches = [m for m in matches if m]
    if not matches:
        return Choice("no-match")
    if len(matches) == 1:
        return Choice("use-the-one-match", matches[0])
    return Choice("ambiguous/escalate", " ".join(matches))
