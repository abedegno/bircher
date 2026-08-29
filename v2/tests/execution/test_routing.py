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
    assert re.search(r"## Mutations — \d+ routed sites", text), (
        "the inventory's own count heading is gone")
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


# --- round 6: shapes that defeated a NAMED exclusion --------------------------

def test_a_mutation_after_a_routed_call_on_the_same_line_is_found(tmp_path):
    """CODEX, round 6. EXCLUSION 3 skipped the whole line once `_effect`
    matched, so anything after it was exempt. The comment there claimed a
    planted positive covered this shape; the positive covered `_effect`
    mentioned in a COMMENT, which is a different shape."""
    assert _scan(tmp_path, '_effect comment k - true; gh pr merge 301\n')


def test_a_mutation_in_a_command_substitution_is_found(tmp_path):
    """CODEX, round 6. `$(...)` inside double quotes is executed, so its
    contents are code -- the quote test suppressed a real merge as data."""
    assert _scan(tmp_path, 'captured="$(gh pr merge 401)"\n')


def test_a_substitution_inside_single_quotes_stays_data(tmp_path):
    """The other direction: single quotes do not interpolate."""
    assert not _scan(tmp_path, """echo 'run $(gh pr merge 401) yourself'\n""")


def test_a_routed_call_whose_key_contains_a_pipeline_is_not_flagged(tmp_path):
    """The regression the segment fix introduced and then closed: a separator
    inside `$(...)` must not split the line and strip the `_effect` prefix."""
    assert not _scan(tmp_path,
        '_effect comment "k:$(printf %s "$b" | shasum -a 256)" - '
        'gh pr comment 7 --body x\n')


def test_a_routed_call_still_exempts_itself(tmp_path):
    assert not _scan(tmp_path, '_effect merge "m:1" - gh pr merge 1 --repo o/r\n')


# --- the class, not just the instance ----------------------------------------

_VARIABLE_METHOD_CURL = re.compile(r'curl\b[^|\n]*-X\s+["\']?\$')

#: Shell helpers permitted to issue a curl whose METHOD is a variable. Each one
#: is a hole in `MUTATION`, which can only match a literal verb -- so each needs
#: its own alternative in the pattern, naming the helper and its method
#: argument.
#:
#: `_http_json` was the first, and it hid two live `session_control` mutations
#: from the initial public release until 2026-08-29.
_VARIABLE_METHOD_HELPERS = {"_http_json"}


def _shell_functions(path):
    """Name and CODE body of each function, comments stripped.

    Two things this got wrong on the first attempt, both caught by it producing
    false positives against the very change it was written for:

    - A ONE-LINE function (`f() { cmd; }`) has no line equal to `}`, so scanning
      forward swallowed the NEXT function's body and attributed its code to the
      wrong name.
    - Comments were included, so a comment DESCRIBING the pattern matched it.
      That is the second time today a substring scan has been tripped by prose
      explaining the thing it looks for.
    """
    lines = path.read_text().splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"^([a-z_][\w]*)\(\) \{", l)
        if not m:
            continue
        if l.rstrip().endswith("}"):
            body = [l]
        else:
            end = next((j for j in range(i + 1, len(lines)) if lines[j] == "}"), None)
            if end is None:
                continue
            body = lines[i:end + 1]
        code = [b for b in body if not b.strip().startswith("#")]
        yield m.group(1), "\n".join(code)


def test_no_other_helper_hides_a_variable_method_curl():
    """CLOSES THE CLASS that `_http_json` was one instance of.

    A helper doing `curl -X "$method"` makes every mutation through it
    invisible to MUTATION, which can only match a literal verb. Fixing the
    instance without this test would leave the next such wrapper free to hide
    the same way -- and the criterion-1 test cannot notice, because it only
    checks findings the detector actually produced.
    """
    found = {name for name, body in _shell_functions(RUN_QUEUE)
             if _VARIABLE_METHOD_CURL.search(body)}
    unexpected = found - _VARIABLE_METHOD_HELPERS
    assert not unexpected, (
        "these helpers issue a curl with a variable method, so mutations "
        "through them are invisible to the detector. Add an alternative to "
        f"MUTATION naming each, then list it here: {sorted(unexpected)}")

    stale = _VARIABLE_METHOD_HELPERS - found
    assert not stale, (
        f"no longer present and should be dropped from the list: {sorted(stale)}")


def test_the_detector_sees_an_indirect_mutation(tmp_path):
    """The known-positive the detector lacked. Its existing self-check plants a
    LITERAL `curl -X POST`, which is exactly the shape that already worked."""
    assert _scan(tmp_path, '  _http_json POST "/v1/sessions" "$body"\n'), (
        "an _http_json POST is a mutation and must be detected")


def test_an_indirect_GET_is_not_a_mutation(tmp_path):
    """The counterpart: flagging reads would make the inventory meaningless."""
    assert not _scan(tmp_path, '  _http_json GET "/v1/sessions/$1"\n')
