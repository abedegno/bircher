"""The PR the derivation settles on must reach the caller that merges.

muesli #723, 2026-08-30. The implementer closed PR #737 and opened #738. The
derivation did everything right -- `adopted CI-green sibling PR #738 (was
#737)`, reviewed #738's head, posted `bircher/cross-review` there -- and then
returned a tuple with no PR field in it. `run_item` kept the number it started
with, authorized a merge for #737 and tried to merge a closed pull request.

The safety nets held: `--match-head-commit` and the moved-head rule both refuse
a merge whose head is not the reviewed one, so nothing wrong could land. But
the item stranded, and #738 -- finished, reviewed and green -- sat unmerged.

This is the bound-at-the-producer-not-the-consumer shape. `_settle_pr` produced
the right answer and three internal callers used it; the one caller outside the
function could not see it.
"""
import pathlib
import re

from coordinator.outcome import Derived

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def test_the_derived_line_carries_the_settled_pr():
    d = Derived("ready", "claude_code:pass", "n", "a" * 40, "green", "true", 0, "738")
    assert d.as_line().split("|")[-1] == "738"
    assert d.as_tuple()[-1] == "738"


def test_the_shell_reads_as_many_fields_as_the_line_emits():
    """THE CLASS, not the instance.

    `read -r a b c` does not error on a short variable list -- it concatenates
    every remaining field into the LAST name. So a tuple that grows silently
    corrupts one value instead of failing, which is exactly how a missing `pr`
    survived a live run that looked like it worked.

    Asserted against `Derived.FIELDS` so adding a field without widening the
    reader fails here rather than in production.
    """
    src = RUN_QUEUE.read_text()
    m = re.search(r"IFS='\|' read -r (outcome[^<]*?)<<EOF", src, re.S)
    assert m, "run_item's tuple read is not where this test expects it"
    names = m.group(1).split()
    assert len(names) == Derived.FIELDS, (
        f"run_item reads {len(names)} fields but the derivation emits "
        f"{Derived.FIELDS}; the surplus is silently absorbed into "
        f"{names[-1]!r}")


def test_run_item_adopts_the_settled_pr_before_it_authorizes_a_merge():
    """Order matters, not just presence.

    `$pr` feeds `_kernel_request_merge`, `merge_ready_pr` and the scorecard
    line. Adopting it after the authorization would authorize one PR and merge
    another -- a subtler version of the same defect.
    """
    src = RUN_QUEUE.read_text()
    adopt = src.index('pr="$_settled_pr"')
    request = src.index('_kernel_request_merge "$BIRCHER_RUN_ID"', adopt - 20000)
    merge = src.index('merge_ready_pr "$item" "$pr"', adopt - 20000)
    assert adopt < request, "the settled PR must be adopted BEFORE request_merge"
    assert adopt < merge, "the settled PR must be adopted BEFORE the merge"


def test_an_unchanged_pr_is_not_announced():
    """The common case must stay quiet, or every item logs a non-event."""
    src = RUN_QUEUE.read_text()
    i = src.index('pr="$_settled_pr"')
    guard = src[i - 400:i]
    assert '!= "${pr:-}"' in guard, (
        "adoption must be conditional on the PR actually differing")


# --- the boundary must fail closed, not fall back to the old behaviour -------

def _width_ok(line: str) -> bool:
    import subprocess
    src = RUN_QUEUE.read_text()
    m = re.search(r"^_derived_width_ok\(\) \{.*?^\}", src, re.S | re.M)
    assert m, "_derived_width_ok not found"
    r = subprocess.run(["bash", "-c", m.group(0) + '\n_derived_width_ok "$1"\n', "_", line],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    return r.returncode == 0


def test_a_short_tuple_is_rejected_rather_than_silently_accepted():
    """Found by cross-review, and it would have undone the whole fix.

    A seven-field result leaves `_settled_pr` EMPTY, which the adoption guard
    reads as "nothing to adopt" -- indistinguishable from a derivation that
    legitimately settled on no PR. So version skew against an older
    coordinator, or a truncated write, would silently restore the stale-PR
    behaviour this change exists to remove.
    """
    assert _width_ok("a|b|c|d|e|f|g|h")
    assert not _width_ok("a|b|c|d|e|f|g")
    assert not _width_ok("a|b|c|d|e|f|g|h|i")
    assert not _width_ok("")


def test_an_embedded_newline_is_rejected():
    """`read` consumes only the FIRST line, so a multi-line result would be
    parsed as its first line with the rest discarded silently."""
    assert not _width_ok("a|b|c|d|e|f|g|h\nx|y")


def test_a_real_tuple_is_accepted():
    """The guard must not reject what production emits -- an earlier cut used
    `"$(printf '\\n')"` for the newline test, which command substitution
    reduces to the EMPTY STRING, so the pattern matched everything and the
    check rejected every valid tuple."""
    assert _width_ok("ready|claude_code:pass|note|" + "a" * 40 + "|green|true|0|738")
    assert _width_ok("escalated|na|no PR||na|unknown||")


def test_recovery_uses_the_settled_pr_rather_than_discarding_it():
    """recover_pr_cmd runs the SAME `_settle_pr`, so discarding the eighth
    field left it able to reproduce the exact defect: review and comment a
    sibling, then authorize and merge the stale PR it was invoked with."""
    src = RUN_QUEUE.read_text()
    assert "r_settled_pr <<EOF" in src, "recovery must parse the settled PR"
    i = src.index('pr="$r_settled_pr"')
    j = src.index("merge_ready_pr \"$item\" \"$pr\"", i)
    assert i < j, "recovery must adopt the settled PR before it merges"
