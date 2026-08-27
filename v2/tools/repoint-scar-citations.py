#!/usr/bin/env python3
"""Re-point `file:line` citations in the scar matrix after code moves.

Every citation is `file:line` + scope + ANCHOR, and test_scar_equivalence
checks the cited line still contains its anchor. Editing run-queue.sh shifts
those lines, so the test reds on drift -- correctly, but the repair was a
manual hunt each time, and doing it by hand three times in one branch is how a
citation silently gets repointed at the wrong line.

This searches for each anchor and rewrites the line number ONLY when the anchor
is unambiguous. An anchor matching zero or several lines is reported and left
alone: guessing is exactly what the citation test exists to prevent.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "design" / "scar-effect-matrix.md"
CITE = re.compile(r"`([\w.-]+):(\d+)`(\s*`[^`]+`\s*)`([^`]+)`")


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
        if len(hits) != 1:
            problems.append(f"{fname}:{line} anchor {anchor!r} matches {len(hits)} lines")
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
