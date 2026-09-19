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


def _heading(phrase: str) -> str:
    """`Phrase` or `PHRASE`: a heading is capitalised, prose is not."""
    return f"(?:{phrase}|{phrase.upper()})"


#: A heading starts its line, or is glued to the end of the sentence before
#: it (`...conclusions.Blocking findings`, as the live reviewers write), and
#: may carry Markdown heading and bold markers (`## `, `### **`) in front.
#: Case-sensitive on purpose: "found no blocking findings issues" is prose
#: and opens no section; "the final verdict logic" is prose and closes none.
_LEAD = r"(?:^|[.!?:]\s*)[#*\s]*"
#: After the phrase: a word boundary, then the heading's own punctuation --
#: bold markers, a parenthetical count (`Blocking findings (2)`, which is a
#: COUNT and not a finding), a colon. Group 1 is that colon when there was
#: one; group 2 is the rest of the line.
_BLOCKING = re.compile(
    _LEAD + _heading("Blocking findings") + r"\b\**\s*(?:\(\d+\))?\s*(:)?\s*\**\s*(.*)$")
#: A closing heading STARTS its line (after heading and bold markers) and may
#: carry any trailer at all: `Non-blocking findings -- none worth naming` and
#: `## Suggestions` both close the blocking section. The sentence-punctuation
#: lead is deliberately NOT used here -- a bullet reading
#: `- : Verification of the lease is missing` is a finding, not a heading, and
#: closing on it would silently drop every finding under it.
_OTHER_SECTION = re.compile(
    r"^[#*\s]*(?:" + "|".join(_heading(p) for p in
                               ("Non-blocking findings", "Suggestions", "Verification", "Verdict"))
    + r")\b")
#: A list item at column zero: `- `, `* `, `• `, `1. `, `1) `. An indented
#: item elaborates the finding above it and is not another finding.
_ITEM = re.compile(r"^(?:[-*•]|\d+[.)])\s+\S")
_NONE = re.compile(r"^(none|n/a|no blocking findings)\.?\**$", re.I)


def blocking_count(text: str) -> int:
    """How many items the reviewer listed under its blocking-findings heading.

    Counts list items between the heading and the next section heading, at
    column zero (a nested sub-bullet elaborates a finding, it is not another
    one). A heading followed on the same line by `None` counts nothing; a
    heading followed by a COLON and a finding on the same line counts one; a
    parenthetical count (`Blocking findings (2)`) is the section's own count
    and is never a finding. Text with no heading counts nothing, so an
    unstructured FAIL posts `0 blocking` rather than a guess.

    A heading is recognised at the start of a line -- Markdown heading and
    bold markers ahead of it are allowed (`## Blocking findings`,
    `### **Blocking findings**`) -- or glued to the end of the sentence before
    it, in heading case only; a prose mention of the phrase mid-sentence opens
    nothing and closes nothing. The phrase CONTINUING as prose with no colon
    (`Blocking findings were all resolved.`) is such a mention: it opens no
    section, because treating it as a heading both counts the sentence as a
    finding and makes every list below it blocking. Items count at column
    zero; an indented item elaborates the finding above it.
    """
    count, inside = 0, False
    for line in (text or "").splitlines():
        m = _BLOCKING.search(line)
        if m:
            colon, rest = m.group(1), m.group(2).strip()
            # Prose continuing off the phrase with no colon is a mention of
            # the section, not the section (`Blocking findings were all
            # resolved.`). A heading ends the line, or introduces its findings
            # with a colon.
            if rest and not colon:
                continue
            inside = True
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
