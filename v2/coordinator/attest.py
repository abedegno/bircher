"""What a cross-review status attests to (spec §4, gate integrity).

Pure. The description names the vendor, the round, the verdict and the
merge-base..head range, so a person reading the PR's checks can see exactly
what was reviewed; the state follows the verdict, and silence is an error,
never a pass.
"""

from __future__ import annotations

import re

#: The context muesli's `review-gate` workflow requires `success` on.
CONTEXT = "bircher/cross-review"

#: GitHub truncates longer descriptions; truncate deliberately instead.
_MAX_DESCRIPTION = 140

_BLOCKING = re.compile(r"(?<![\w-])blocking findings\b\W*(.*)$", re.I)
_OTHER_SECTION = re.compile(r"(?<![\w-])(non-blocking findings|suggestions|verification|verdict)\b", re.I)
#: A list item at the section's own indentation: `- `, `* `, `• `, `1. `, `1) `.
_ITEM = re.compile(r"^ {0,2}(?:[-*•]|\d+[.)])\s+\S")
_NONE = re.compile(r"^(none|n/a|no blocking findings)\.?$", re.I)


def blocking_count(text: str) -> int:
    """How many items the reviewer listed under its blocking-findings heading.

    Counts list items between the heading and the next section heading, at
    the section's own indentation (a nested sub-bullet elaborates a finding,
    it is not another one). A heading followed on the same line by `None`
    counts nothing; a heading followed by a finding on the same line counts
    one. Text with no heading counts nothing, so an unstructured FAIL posts
    `0 blocking` rather than a guess.
    """
    count, inside = 0, False
    for line in (text or "").splitlines():
        m = _BLOCKING.search(line)
        if m:
            inside = True
            rest = m.group(1).strip()
            if rest and not _NONE.match(rest):
                count += 1
            continue
        if inside and _OTHER_SECTION.search(line):
            break
        if inside and _ITEM.match(line):
            count += 1
    return count


def state_for(verdict: str | None) -> str:
    """`success` for PASS, `failure` for FAIL, `error` for anything else.

    BLOCKED, an empty verdict and a reviewer that never produced one all
    land on `error`: none of them is an approval, and the gate requires
    `success` by name.
    """
    return {"PASS": "success", "FAIL": "failure"}.get(verdict or "", "error")


def describe(vendor: str, round_n: int, verdict: str | None, blocking: int,
             merge_base: str, head: str) -> str:
    """`<vendor> round <n> <word> on <merge-base7>..<head7>`."""
    if verdict == "PASS":
        word = "PASS"
    elif verdict == "FAIL":
        word = f"FAIL ({blocking} blocking)"
    else:
        word = "no verdict"
    rng = f"{(merge_base or '')[:7] or '?'}..{(head or '')[:7] or '?'}"
    return f"{vendor} round {round_n} {word} on {rng}"[:_MAX_DESCRIPTION]
