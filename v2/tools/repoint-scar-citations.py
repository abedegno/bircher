#!/usr/bin/env python3
"""Re-point `file:line` citations in the scar matrix after code moves.

Every citation is `file:line` + scope + ANCHOR, and test_scar_equivalence
checks the cited line still contains its anchor. Editing run-queue.sh shifts
those lines, so the test reds on drift -- correctly, but the repair was a
manual hunt each time, and doing it by hand three times in one branch is how a
citation silently gets repointed at the wrong line.

This searches for each anchor and rewrites the line number ONLY when the answer
is unambiguous. Two disambiguators, in order:

1. the anchor matches exactly one line;
2. the anchor matches several, but exactly one of them is inside the SCOPE the
   citation already names -- which is a fact the row states, not a guess.

Anything still ambiguous is reported and left alone. Guessing is what the
citation test exists to prevent, and the second rule is not a guess: a row
saying `merge_ready_pr` is a claim the test independently checks.

Rule 2 was added after resolving the same two rows by hand four times, each
time by asking "which hit is inside the named function?" -- a question the tool
had every input needed to answer.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "design" / "scar-effect-matrix.md"
CITE = re.compile(r"`([\w.-]+):(\d+)`(\s*`[^`]+`\s*)`([^`]+)`")


def _scope_range(lines, scope):
    """The line range of a named shell function, or (None, None).

    `«top-level»` is a real scope in the matrix and deliberately has no range:
    a row claiming top-level must not be resolved by looking inside functions.
    """
    if not scope or scope.startswith("«"):
        return None, None
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(f"{scope}() {{"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    except StopIteration:
        return None, None
    return start + 1, end + 1


def main() -> int:
    text = MATRIX.read_text()
    changed, problems = 0, []

    def fix(m):
        nonlocal changed
        fname, line, mid, anchor = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        path = ROOT / ("batch/" + fname if fname.endswith("run-queue.sh")
                       else "batch/lib/" + fname)
        lines = path.read_text().splitlines()
        if 0 < line <= len(lines) and anchor in lines[line - 1]:
            return m.group(0)                      # still correct
        hits = [i + 1 for i, l in enumerate(lines) if anchor in l]
        if len(hits) > 1:
            scope = mid.strip().strip("`")
            lo, hi = _scope_range(lines, scope)
            if lo is not None:
                inside = [h for h in hits if lo <= h <= hi]
                if len(inside) == 1:
                    hits = inside
        if len(hits) != 1:
            problems.append(
                f"{fname}:{line} anchor {anchor!r} matches {len(hits)} lines"
                + ("" if len(hits) else " (deleted code? the row may need retiring)"))
            return m.group(0)
        changed += 1
        print(f"  {fname}:{line} -> {hits[0]}   ({anchor})")
        return f"`{fname}:{hits[0]}`{mid}`{anchor}`"

    out = CITE.sub(fix, text)
    if changed:
        MATRIX.write_text(out)
    print(f"repointed {changed} citation(s)")
    for p in problems:
        print(f"  UNRESOLVED: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
