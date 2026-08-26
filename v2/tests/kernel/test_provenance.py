"""The provenance table is checked, not read.

Round 5's audit found five of the six links in the merge chain were caller
assertions. Every comparison was correct; the chain proved nothing. What was
missing was not a check -- it was a record of which inputs the mechanism had
actually observed. A table nobody verifies is prose, and prose asserting a
property is the same defect in a different medium.

The input list is derived from `kernel/authz.py` by AST walk, NOT from a
hand-written list.

The walk matches `store.X` syntactically, so it cannot see a read through an
alias -- `s = store; s.run_owner(...)` is invisible, verified by executing it
against the real extractor. Rather than leave a guarantee that holds only for
one spelling, `test_the_store_is_never_aliased` enforces the assumption the
walk rests on. A hand-written list checked against a hand-written table is
prose checking prose: adding an unclassified input would satisfy both.
"""

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
AUTHZ = pathlib.Path(__file__).resolve().parents[2] / "kernel" / "authz.py"
TABLE = (pathlib.Path(__file__).resolve().parents[3]
         / "docs" / "design" / "provenance-table.md")

#: Identity readers. They take `store` as an argument rather than being
#: attributes of it, so the `store.X` walk cannot see them.
_IDENTITY_READERS = {"role_for", "actor_for", "_implementer_of", "_reviewer_of"}


def authorization_inputs() -> set[str]:
    """Every input the authorization path reads, from the source itself."""
    tree = ast.parse(AUTHZ.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "store"):
            found.add(f"store.{node.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _IDENTITY_READERS:
                found.add(node.func.id)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            recv, key = node.func.value, node.args[0].value
            base = None
            if isinstance(recv, ast.Attribute) and recv.attr == "payload":
                base = ("cmd.payload"
                        if isinstance(recv.value, ast.Name) and recv.value.id == "cmd"
                        else "fact.payload")
            elif isinstance(recv, ast.Name):
                base = recv.id
            if base:
                found.add(f"{base}[{key!r}]")
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "payload"
                and isinstance(node.slice, ast.Constant)):
            found.add(f"cmd.payload[{node.slice.value!r}]")
    return found


def table_rows() -> list[dict]:
    rows = []
    for line in TABLE.read_text().splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "Input":
            continue
        rows.append({"input": cells[0].strip("`"), "enters": cells[1],
                     "provenance": cells[2], "reason": cells[3]})
    return rows


# --- the detector must be able to fail ---------------------------------------

def test_the_extractor_finds_known_inputs():
    """A detector that returns nothing reports total coverage.

    Two known-positives of different SHAPES: an attribute read off `store`, and
    a key read out of a caller's payload. A walk that handled only one would
    still pass a single-shape check.
    """
    found = authorization_inputs()
    assert "store.current_artifact" in found
    assert "cmd.payload['verdict']" in found
    assert "role_for" in found
    assert len(found) > 10, f"suspiciously few inputs extracted: {sorted(found)}"


def test_the_table_parses():
    rows = table_rows()
    assert len(rows) > 15, f"only {len(rows)} rows parsed; the table is not being read"
    assert {r["provenance"] for r in rows} <= {"observed", "asserted"}


# --- the properties -----------------------------------------------------------

def test_every_authorization_input_appears_in_the_table():
    """A missing row is an input nobody classified, which is how five of six
    links stayed asserted through three rounds of repair."""
    classified = {r["input"] for r in table_rows()}
    missing = sorted(i for i in authorization_inputs() if i not in classified)
    assert not missing, (
        "authorization reads these and the provenance table does not classify "
        f"them: {missing}"
    )


def test_no_asserted_input_is_left_without_a_reason():
    """An `asserted` row is a defect or a declared residual. There is no third
    case, and 'asserted' with an empty reason is the third case."""
    for row in table_rows():
        if row["provenance"] == "asserted":
            assert len(row["reason"]) > 40, (
                f"{row['input']}: asserted with no stated reason"
            )
            assert re.search(r"\*\*(Residual|Intentional)", row["reason"]), (
                f"{row['input']}: asserted rows must say whether this is a "
                "declared residual or intentional, in bold, so it cannot be "
                "skimmed past"
            )


def test_the_residuals_are_the_ones_we_know_about():
    """Naming them here means a NEW asserted input fails this test rather than
    joining a list nobody re-reads."""
    asserted = {r["input"] for r in table_rows() if r["provenance"] == "asserted"}
    assert asserted == {
        "cmd.payload['verdict']",
        "latest['status']",
        "latest['head_git_sha']",
        "cmd.payload['head_git_sha']",
        "cmd.payload['context_bundle_hash']",
        "payload['policy_version']",
        # Round 6, C2: binding the merge effect to the authorization made the
        # authorization's own target an authorization input. It is asserted,
        # and closing it is blocked on the kernel creating the PR (C8).
        "cmd.payload['pr']",
        "cmd.payload['repo']",
    }



def test_the_store_is_never_aliased_in_authz():
    """The assumption the AST walk rests on, made enforceable.

    `authorization_inputs` finds kernel-state reads by matching an attribute
    whose receiver is the NAME `store`. An alias defeats it:

        s = store
        owner = s.run_owner(cmd.run_id)   # not extracted

    Executed against the real extractor, that read is invisible while a new
    payload key is caught. So the guarantee -- "authorization reads these and
    the table does not classify them" -- holds only for reads written one way.

    Widening the walk properly needs dataflow analysis. Forbidding the alias
    is cheaper and exact, and it fails loudly if anyone introduces one.
    """
    tree = ast.parse(AUTHZ.read_text())
    aliases = [
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Name)
        and node.value.id == "store"
        for t in node.targets
        if isinstance(t, ast.Name)
    ]
    assert not aliases, (
        f"authz.py aliases `store` as {aliases}; reads through the alias are "
        "invisible to the provenance walk, so the table's completeness "
        "guarantee would silently stop holding"
    )


def test_the_spec_and_the_table_agree_on_the_count():
    """The spec said FOUR, the table had EIGHT, and the test fixed EIGHT.

    An edit meant for the spec landed in the table instead, so the number in
    the design document drifted from the artifact it describes and nothing
    noticed -- found by a reviewer counting the rows. A count asserted in
    prose and checked nowhere is the defect this whole table exists to catch,
    committed in the document that defines it.
    """
    spec = (REPO_ROOT / "docs" / "design"
            / "2026-08-23-v2-kernel-design.md").read_text()
    m = re.search(r"\*\*(\w+) rows are asserted after Milestone 1\*\*", spec)
    assert m, "the spec no longer states the asserted-row count"
    words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
    claimed = words.get(m.group(1).lower())
    assert claimed is not None, f"unrecognised count word: {m.group(1)!r}"
    actual = sum(1 for r in table_rows() if r["provenance"] == "asserted")
    assert claimed == actual, (
        f"the spec says {claimed} asserted rows; the table has {actual}"
    )
