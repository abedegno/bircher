"""The six decisions are recorded, and each still has an implementation.

The spec requires Milestone 1 to FIX six things about the frozen bundle
rather than gesture at them. A document that names a symbol which no longer
exists is a claim about the source that is false -- the defect class this
programme is organised around -- and a decision table nothing checks is prose.
"""

import importlib
import pathlib
import re

import pytest

DOC = (pathlib.Path(__file__).resolve().parents[3]
       / "docs" / "design" / "frozen-bundle.md")

EXPECTED = 6


def rows():
    out = []
    for line in DOC.read_text().splitlines():
        m = re.match(r"\|\s*(\d)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|", line)
        if m:
            out.append({
                "n": int(m.group(1)),
                "decision": m.group(2).strip(),
                "symbols": [s.strip(" `") for s in m.group(3).split(",")],
                "module": m.group(4).strip(" `"),
            })
    return out


def test_the_table_parses_and_has_all_six():
    """A parser that finds nothing reports total compliance."""
    r = rows()
    assert len(r) == EXPECTED, f"parsed {len(r)} decisions, expected {EXPECTED}"
    assert [x["n"] for x in r] == list(range(1, EXPECTED + 1))


def test_every_decision_names_a_symbol_that_exists():
    for row in rows():
        mod = importlib.import_module(row["module"].replace("/", ".").removesuffix(".py"))
        for sym in row["symbols"]:
            assert hasattr(mod, sym), (
                f"decision {row['n']} names {sym!r}, which {row['module']} "
                "does not define"
            )


def test_every_decision_has_a_section_with_reasoning():
    """A row in a table is a pointer, not a decision. The section is where the
    reasoning lives, and a reader who cannot find the why cannot tell a
    decision from a default."""
    text = DOC.read_text()
    for row in rows():
        heading = f"## {row['n']}."
        assert heading in text, f"decision {row['n']} has no section"
        body = text.split(heading, 1)[1].split("\n## ")[0]
        assert len(body) > 300, (
            f"decision {row['n']}'s section is {len(body)} chars -- too thin "
            "to carry a reason"
        )


def test_decision_4_records_that_it_is_enforced_not_asserted():
    """The one most likely to decay back into a constant."""
    body = DOC.read_text().split("## 4.", 1)[1].split("\n## ")[0]
    assert "two entry points" in body.lower()
    assert "propose_revision" in body and "revise_bundle" in body


def test_the_invalidation_table_matches_the_code():
    """The doc's table and `_BINDS` are two statements of one rule, and two
    statements of one rule drift."""
    from kernel.bundle import _BINDS

    body = DOC.read_text().split("## 5.", 1)[1].split("\n## ")[0]
    for kind, binds in _BINDS.items():
        row = [l for l in body.splitlines() if f"`{kind}`" in l]
        assert row, f"{kind} is not in the doc's invalidation table"
        cell = row[0]
        assert ("head" in cell) == ("head_git_sha" in binds), kind
        assert ("context bundle" in cell) == ("context_bundle_hash" in binds), kind
        assert ("base" in cell) == ("base_git_sha" in binds), kind
