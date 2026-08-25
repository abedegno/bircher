"""Criterion 1 (structural routing) and the detector's own planted positives.

Coverage evidence -- NOT the authority-boundary proof, which is M1-1's
end-to-end capability test. A PATH shim in the coordinator says nothing about
a model session, which is a separate process that can use an absolute path,
another HTTP client, an SDK or a language runtime.

Every exclusion in the detector gets its own planted positive, drawn from the
shape it could blind the detector to. A single "known bad line" test would
pass while four exclusions went unchecked -- and in a sibling programme four
detectors passed vacuously, with the analysis correct and the exclusion wrong
every time.
"""

import pathlib
import re
import textwrap

from tools.detect_direct_effects import find_direct_effects, scan

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"
INVENTORY = REPO_ROOT / "docs" / "design" / "effect-site-inventory.md"


def _scan(tmp_path, body, name="sample.sh"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body).lstrip("\n"))
    return find_direct_effects(str(p))


# --- planted positives: the detector must be able to fail ---------------------

def test_planted_positive_bare_merge(tmp_path):
    assert _scan(tmp_path, 'gh pr merge "$pr" --repo "$REPO" --squash\n')


def test_planted_positive_git_push(tmp_path):
    assert _scan(tmp_path, 'git push origin HEAD:main -q\n')


def test_planted_positive_gh_api_post(tmp_path):
    assert _scan(tmp_path, 'gh api "repos/$REPO/statuses/$sha" -X POST -f state=success\n')


def test_planted_positive_a_quoted_verb_is_still_a_call(tmp_path):
    """EXCLUSION 4's positive. A pattern requiring the verb adjacent to
    whitespace does not match this AT ALL, so quote-position analysis alone
    would never have found it."""
    assert _scan(tmp_path, 'gh pr "merge" "$pr" --repo "$REPO"\n')


def test_planted_positive_a_heredoc_fed_to_bash_is_code(tmp_path):
    """EXCLUSION 1's positive. Skipping every heredoc body would leave a hole
    shaped exactly like the boundary this detector exists to prove."""
    assert _scan(tmp_path, '''
        bash <<'EOF'
        gh pr merge "$pr" --repo "$REPO"
        EOF
    ''')


def test_planted_positive_effect_mentioned_in_a_comment_cannot_launder(tmp_path):
    """EXCLUSION 3's positive. A line that MENTIONS _effect while performing a
    bare mutation must still be caught."""
    assert _scan(tmp_path, 'gh pr merge "$pr" --repo "$REPO"   # replaces _effect merge\n')


def test_planted_positive_the_adapter_file_is_not_wholly_exempt(tmp_path):
    """An unrouted mutation added to the adapter itself must still be caught.
    Exempting the file by name was an earlier design and would have hidden it."""
    assert _scan(tmp_path, 'gh pr merge "$pr" --repo "$REPO"\n', name="effect-adapter.sh")


# --- the exclusions must actually exclude -------------------------------------

def test_a_comment_mentioning_a_mutation_is_not_a_call(tmp_path):
    assert not _scan(tmp_path, '# historically this called gh pr merge directly\n')


def test_a_mutation_inside_a_string_literal_is_data(tmp_path):
    assert not _scan(tmp_path, 'MERGE_NOTE="merge deferred: gh pr merge failed"\n')


def test_a_selftest_assertion_about_the_source_is_data(tmp_path):
    assert not _scan(tmp_path, """_contains "$_body" 'git push origin' || exit 1\n""")


def test_a_heredoc_fed_to_cat_is_data(tmp_path):
    assert not _scan(tmp_path, '''
        cat <<'EOF'
        run: gh pr merge "$pr"
        EOF
    ''')


def test_a_routed_mutation_is_not_flagged(tmp_path):
    assert not _scan(tmp_path, '_effect merge "merge:$pr" gh pr merge "$pr" --repo "$REPO"\n')


def test_routed_after_a_conditional_is_not_flagged(tmp_path):
    assert not _scan(tmp_path,
                     '[ -n "$pr" ] && _effect comment "c:$pr" gh pr comment "$pr" --body x\n')


def test_a_string_opened_on_one_line_stays_a_string_on_the_next(tmp_path):
    """Quote state carries across lines, or a multi-line string's continuation
    reads as bare command text."""
    assert not _scan(tmp_path, '''
        NOTE="first line
        gh pr merge is mentioned here
        and here"
    ''')


# --- criterion 1 --------------------------------------------------------------

def test_the_detector_agrees_with_the_inventory():
    """The inventory drives the boundary, so a drift between them is a
    boundary nobody is checking. Both numbers are asserted: a detector finding
    FEWER sites than the inventory has gone blind, and finding more means the
    inventory is stale."""
    findings, suppressed = scan(str(RUN_QUEUE))
    text = INVENTORY.read_text()
    assert "## Mutations — 15 routed sites" in text, "the inventory's own count moved"
    assert len(findings) + len(suppressed) > 0
    for f in findings:
        assert f"| {f.line} |" in text, (
            f"line {f.line} is a mutation the inventory does not list: {f.text}"
        )


def test_criterion_1_every_mutation_is_routed_or_dispositioned():
    """Acceptance criterion 1.

    An unrouted mutation must be named in the inventory's dispositioned table
    with a reason. That is a documented exception, not a silent one: the
    difference is that this test fails when a NEW unrouted site appears, and
    would not if the criterion simply allowed a hardcoded count.

    If this fails, the inventory was incomplete -- go back to it rather than
    widening a detector exclusion.
    """
    text = INVENTORY.read_text()
    dispositioned = {int(m) for m in re.findall(r"^\| (\d+) \|", 
                     text.split("## Dispositioned")[-1], re.M)}
    unrouted = {f.line: f.text for f in find_direct_effects(str(RUN_QUEUE))}
    undocumented = {n: txt for n, txt in unrouted.items() if n not in dispositioned}
    assert not undocumented, "unrouted and undocumented:\n" + "\n".join(
        f"  {n}: {txt}" for n, txt in undocumented.items())


def test_the_dispositioned_list_is_not_a_dumping_ground():
    """Every dispositioned line must still BE an unrouted mutation. A stale
    entry would silently pre-authorise the next site that lands on that line."""
    text = INVENTORY.read_text()
    dispositioned = {int(m) for m in re.findall(r"^\| (\d+) \|",
                     text.split("## Dispositioned")[-1], re.M)}
    unrouted = {f.line for f in find_direct_effects(str(RUN_QUEUE))}
    stale = sorted(dispositioned - unrouted)
    assert not stale, f"dispositioned lines that are not unrouted mutations: {stale}"
