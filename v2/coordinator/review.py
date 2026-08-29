"""Reading a reviewer's verdict.

`extract_verdict` is ported from `_extract_verdict`, which had 45 call sites
and a trimming loop carrying its own scars. The logic is preserved exactly; it
is merely legible now.
"""

from __future__ import annotations

#: How many trim passes before giving up. BOUNDED on purpose: without it a line
#: of pure decoration could normalise into a verdict one character at a time.
_MAX_TRIM_PASSES = 8

_EDGE = "*`_"


def extract_verdict(text: str) -> str | None:
    """`PASS`, `FAIL`, or None from a reviewer's output.

    Reads the LAST non-blank line and requires it to be a BARE verdict. A
    verdict mentioned mid-report is not a verdict -- a reviewer writing "if the
    tests passed I would say VERDICT: PASS" must not merge anything.

    Decoration is tolerated because reviewers emit markdown: `**VERDICT:
    PASS**`, `` `VERDICT: PASS` ``, `VERDICT: PASS.` all count. ONE trailing
    sentence-ending mark is allowed, once -- a line ending `...` is prose.
    """
    lines = [l.rstrip() for l in (text or "").splitlines()]
    non_blank = [l for l in lines if l.strip()]
    if not non_blank:
        return None

    last = non_blank[-1]
    punct_stripped = False
    for _ in range(_MAX_TRIM_PASSES):
        before = last
        last = last.strip()
        if last[:1] in _EDGE:
            last = last[1:]
        if last[-1:] in _EDGE:
            last = last[:-1]
        elif last[-1:] in ".!" and not punct_stripped:
            last = last[:-1]
            punct_stripped = True
        if last == before:
            break

    if last == "VERDICT: PASS":
        return "PASS"
    if last == "VERDICT: FAIL":
        return "FAIL"
    return None
