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
    # `pr` is field 8 of 10 -- the reviewed range rides out after it.
    assert d.as_line().split("|")[7] == "738"
    assert d.as_tuple()[7] == "738"


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


def test_the_recovery_reads_as_many_fields_as_the_line_emits():
    """THE SAME CLASS, at the OTHER reader.

    `recover_pr_cmd` parses the same ten-field tuple `run_item` does, and it
    is a separate `read` with its own name list -- so widening one and not the
    other leaves the recovery silently absorbing the surplus into its last
    name. Asserted against `Derived.FIELDS`, as `run_item`'s is.
    """
    src = RUN_QUEUE.read_text()
    m = re.search(r"IFS='\|' read -r (r_outcome[^<]*?)<<EOF", src, re.S)
    assert m, "recover_pr_cmd's tuple read is not where this test expects it"
    names = m.group(1).split()
    assert len(names) == Derived.FIELDS, (
        f"recover_pr_cmd reads {len(names)} fields but the derivation emits "
        f"{Derived.FIELDS}; the surplus is silently absorbed into "
        f"{names[-1]!r}")


def test_the_recovery_records_the_range_on_its_verdict():
    """`run_item` records what the reviewer read on the verdict fact (spec
    §4). The recovery drives the same derivation and the same kernel command,
    and parsed those two fields only to throw them away -- so a recovered
    item's verdict fact recorded no range at all."""
    src = RUN_QUEUE.read_text()
    m = re.search(r'_kernel_record_review "\$BIRCHER_RUN_ID" "\$BIRCHER_GENERATION" "\$r_review"'
                  r'(.*?)\n\n', src, re.S)
    assert m, "the recovery's record_review call is not where this test expects it"
    call = m.group(1)
    for arg in ('"$r_sha"', '"$r_merge_base"', '"$r_delta_digest"'):
        assert arg in call, f"the recovery's verdict does not carry {arg}"


def test_run_item_threads_the_reviewed_range_into_the_fallback_status():
    """The runner's status post is a FALLBACK, and it fires exactly when the
    derivation's own post did not land. Its default description names no
    range, so without a description threaded from the call site the fallback
    replaces the merge head's range-naming description with legacy text."""
    src = RUN_QUEUE.read_text()
    m = re.search(r'\n\s*_status_desc="([^"\n]*)"', src)
    assert m, "run_item composes no fallback status description"
    desc = m.group(1)
    assert "${_merge_base:0:7}" in desc and "${observed_head:0:7}" in desc, (
        f"the fallback description names no range: {desc!r}")
    call = re.search(r'merge_ready_pr "\$item" "\$pr" "\$reviewed_sha"([^\n]*)', src)
    assert call, "run_item's merge_ready_pr call is not where this test expects it"
    assert '"$_status_desc"' in call.group(1), (
        "run_item composes a range description and does not pass it to "
        "merge_ready_pr")


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
    assert _width_ok("a|b|c|d|e|f|g|h|i|j")
    assert not _width_ok("a|b|c|d|e|f|g|h|i")
    assert not _width_ok("a|b|c|d|e|f|g|h|i|j|k")
    assert not _width_ok("")


def test_an_embedded_newline_is_rejected():
    """`read` consumes only the FIRST line, so a multi-line result would be
    parsed as its first line with the rest discarded silently."""
    assert not _width_ok("a|b|c|d|e|f|g|h|i|j\nx|y")


def test_a_real_tuple_is_accepted():
    """The guard must not reject what production emits -- an earlier cut used
    `"$(printf '\\n')"` for the newline test, which command substitution
    reduces to the EMPTY STRING, so the pattern matched everything and the
    check rejected every valid tuple."""
    assert _width_ok("ready|claude_code:pass|note|" + "a" * 40 + "|green|true|0|738||")
    assert _width_ok("escalated|na|no PR||na|unknown||||")


def test_recovery_uses_the_settled_pr_rather_than_discarding_it():
    """recover_pr_cmd runs the SAME `_settle_pr`, so discarding the eighth
    field left it able to reproduce the exact defect: review and comment a
    sibling, then authorize and merge the stale PR it was invoked with."""
    src = RUN_QUEUE.read_text()
    # The settled PR is field 8 of 10; the reviewed range (merge_base, digest)
    # rides out after it, into the recovery's own review record.
    assert "r_settled_pr r_merge_base r_delta_digest <<EOF" in src, (
        "recovery must parse the settled PR and the range after it")
    i = src.index('pr="$r_settled_pr"')
    j = src.index("merge_ready_pr \"$item\" \"$pr\"", i)
    assert i < j, "recovery must adopt the settled PR before it merges"
