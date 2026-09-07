"""The bash copy of `is_bircher_status` and the kernel's must agree.

There are two implementations of one predicate: `kernel/bundle.py`'s, which
decides what the frozen input contains, and the one embedded in
`_format_issue_comments` in `batch/run-queue.sh`, which decides what a session
is shown. They are separate because the shell one runs inside a `python3 -c`
program in a bash single-quoted string and cannot import the kernel from where
it runs; that is a reason for two copies, not a reason for two ANSWERS.

So the bash copy is EXTRACTED from the runner and EXECUTED here, against the
same fixture the kernel's copy is driven against. A drift shows up as a fixture
row that one accepts and the other drops -- which is what would happen if a
prefix were added to one file and not the other, exactly as
`bircher: published ` was.
"""
import pathlib
import re

RUNNER = pathlib.Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "bircher_status_comments.tsv"


def _bash_predicate_source() -> str:
    src = RUNNER.read_text()
    m = re.search(r"def is_bircher_status\(body\):.*?return \(.*?\)\n", src, re.S)
    assert m, "the embedded is_bircher_status is gone or moved"
    return m.group(0)


def test_the_extractor_takes_the_WHOLE_predicate():
    """A parser that finds nothing reports total compliance -- and one that
    finds a PREFIX of the predicate reports compliance for the clauses it
    happened to reach. Asserted against the RETURN EXPRESSION, not the whole
    match: the match starts at `def`, so the comment block above the code
    would satisfy a naive substring check while the predicate below it
    tested one clause fewer. The `or` at the end of each line is what keeps
    every clause inside one match."""
    src = _bash_predicate_source()
    expr = src[src.index("return ("):]
    from kernel import bundle
    for prefix in bundle.BIRCHER_STATUS_PREFIXES:
        assert prefix in expr, (prefix, expr)


def test_the_bash_copy_agrees_with_the_fixture_and_the_kernel():
    from kernel import bundle
    ns = {}
    exec(_bash_predicate_source(), ns)
    rows = 0
    for line in FIXTURE.read_text().splitlines():
        verdict, body = line.split("\t", 1)
        assert ns["is_bircher_status"](body) is (verdict == "drop"), body
        assert ns["is_bircher_status"](body) == bundle.is_bircher_status(body), body
        rows += 1
    assert rows >= 10, "the fixture shrank; both copies would be tested by less"
