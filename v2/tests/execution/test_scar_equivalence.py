"""The scar/effect matrix must describe the source, not resemble it.

The port is complete only when a mutation of the v1 scar still fails an
equivalent v2 test. That claim needs an artifact or it cannot be checked, and
an artifact nothing checks is prose -- which is the same defect as a comment
asserting a property nothing verifies.

Every check here binds a matrix cell to something in the repository. A row
citing a line that does not exist is a claim about source that is false, which
is the defect class this whole programme exists to catch.
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MATRIX = REPO_ROOT / "docs" / "design" / "scar-effect-matrix.md"
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"

EXPECTED_COLUMNS = ["v1 behaviour", "source", "mutation that breaks it",
                    "v2 owner", "test fixture", "fault injected",
                    "expected durable events", "effects"]


def _tables():
    """Split the file into markdown tables, each a list of cell-lists."""
    tables, current = [], None
    for line in MATRIX.read_text().splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if current is None:
                current = []
                tables.append(current)
            current.append(cells)
        else:
            current = None
    return tables


def retained():
    t = _tables()[0]
    return t[0], t[1:]


def excluded():
    t = _tables()[1]
    return t[0], t[1:]


def _function_ranges():
    """name -> (first line, last line) for every top-level bash function."""
    ranges, name, start = {}, None, None
    for n, raw in enumerate(RUN_QUEUE.read_text().splitlines(), 1):
        if name is None:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", raw)
            if m:
                # A one-liner closes on its own line. Running past it made
                # `_derive_bundle_dir` look 121 lines long, and a citation
                # 5 lines outside it passed the range check.
                if raw.rstrip().endswith("}"):
                    ranges[m.group(1)] = (n, n)
                    continue
                name, start = m.group(1), n
        elif raw == "}":
            ranges[name] = (start, n)
            name = None
    return ranges


# --- the parser must be able to fail ------------------------------------------

def test_the_matrix_parses_into_two_tables():
    """A parser that finds nothing reports total compliance."""
    tables = _tables()
    assert len(tables) == 2, f"expected retained + excluded, got {len(tables)}"
    assert len(retained()[1]) >= 8
    assert len(excluded()[1]) >= 2


def test_the_function_scanner_finds_known_functions():
    """The known-positive for the range scanner."""
    ranges = _function_ranges()
    for fn in ("_classify_ci_failure", "merge_ready_pr", "parse_marker",
               "_merge_gate", "_reopen_reverted_issues"):
        assert fn in ranges, f"{fn} not found by the scanner"
    lo, hi = ranges["_classify_ci_failure"]
    assert hi > lo


# --- the properties -----------------------------------------------------------

def test_column_order_is_what_the_index_assertions_assume():
    """Without this, reordering the table would silently make every check
    below inspect the wrong column and still pass."""
    header = [c.strip().lower() for c in retained()[0]]
    assert header == EXPECTED_COLUMNS


SOURCE = re.compile(r"`([\w.-]+):(\d+)`\s*`([^`]+)`\s*`([^`]+)`")


def _cited(row):
    m = SOURCE.search(row[1])
    assert m, f"{row[0]}: source cell must be `file:line` `scope` `anchor`"
    return m.group(1), int(m.group(2)), m.group(3), m.group(4)


def test_every_cited_line_contains_its_anchor():
    """THE citation check. A line number alone proves nothing -- any number
    under 6900 falls inside some function, and two citations here were wrong
    while a range check passed them. The anchor is content."""
    for row in retained()[1] + excluded()[1]:
        fname, line, _scope, anchor = _cited(row)
        path = REPO_ROOT / ("batch/" + fname if fname.endswith("run-queue.sh")
                            else "batch/lib/" + fname)
        lines = path.read_text().splitlines()
        assert 0 < line <= len(lines), f"{row[0]}: {fname}:{line} is out of range"
        assert anchor in lines[line - 1], (
            f"{row[0]}: {fname}:{line} does not contain {anchor!r}\n"
            f"  actual: {lines[line - 1].strip()[:100]}"
        )


def test_every_cited_line_is_in_the_scope_it_names():
    """The anchor proves the line; this proves the row is describing the right
    piece of the program. A `«top-level»` row must NOT be inside a function --
    that is a claim too, and it was false for the adapter-wiring row."""
    ranges = _function_ranges()
    for row in retained()[1] + excluded()[1]:
        fname, line, scope, _anchor = _cited(row)
        if not fname.endswith("run-queue.sh"):
            continue
        inside = [f for f, (lo, hi) in ranges.items() if lo <= line <= hi]
        if scope == "«top-level»":
            assert not inside, f"{row[0]}: line {line} is inside {inside}"
        else:
            assert scope in ranges, f"{row[0]}: no function {scope!r}"
            lo, hi = ranges[scope]
            assert lo <= line <= hi, (
                f"{row[0]}: line {line} is not inside {scope} ({lo}-{hi})"
            )


def test_every_retained_row_names_who_binds_the_mutation():
    """A mutation with no recorded result is a mutation nobody ran. `v1 binds`
    and `v2 binds` are different claims and the matrix must not blur them."""
    for row in retained()[1]:
        assert re.search(r"\*\*v[12] binds", row[2]), (
            f"{row[0]}: the mutation cell does not say who binds it"
        )


def test_every_retained_row_has_a_fixture_and_an_injected_fault():
    """A row with no fault is a happy path wearing a matrix row's costume."""
    for row in retained()[1]:
        assert row[4], f"{row[0]}: no test fixture"
        assert row[5], f"{row[0]}: no injected fault"


def test_every_retained_row_has_an_owner():
    for row in retained()[1]:
        assert row[3], f"{row[0]}: no v2 owner"


def test_every_excluded_row_carries_a_real_disposition():
    """'Excluded' with an empty reason is how a gap becomes a decision nobody
    made."""
    for row in excluded()[1]:
        assert len(row[2]) > 80, f"{row[0]}: disposition is too thin to be one"


def test_effects_cells_name_only_real_effect_classes():
    from kernel.effects import EffectClass

    for row in retained()[1]:
        for token in re.findall(r"`?\b([a-z_]{4,})\b`?", row[7]):
            if token == "none":
                continue
            assert token in EffectClass.ALL, (
                f"{row[0]}: {token!r} is not an effect class"
            )
