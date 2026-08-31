"""The marker is retired, and this is what keeps it retired.

An enumerating test, not N per-site tests: it fails when someone adds site
N+1. The predecessor of this shape caught two new `_effect` call sites that
would otherwise have joined silently.
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Prose may still SAY "bircher-status" -- records, specs and plans describe
#: the history, and erasing the name from them would make the scar record
#: unreadable. Code and tests may not.
_PROSE_ROOTS = {"docs", "README.md", ".superpowers"}

_CODE_SUFFIXES = {".sh", ".py", ".yaml", ".yml"}

#: INSTRUCTIONS TO A MODEL ARE EXECUTABLE, so they are scanned like code.
#:
#: The hole this closes: `.md` was not a scanned suffix, so while no shipped
#: code wrote or read the marker, `skills/muesli-loop/SKILL.md` still TOLD the
#: lead session to post one -- and it did, on muesli #735. The channel was
#: retired everywhere except the one place that caused it to be written.
#:
#: Only `skills/` and `agents/`. `docs/` stays prose: records and specs describe
#: the history, and erasing the name from them would make the scar record
#: unreadable.
_INSTRUCTION_ROOTS = {"skills", "agents"}


def _shipped_files():
    for p in sorted(REPO_ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO_ROOT)
        instruction = (rel.parts and rel.parts[0] in _INSTRUCTION_ROOTS
                       and p.suffix == ".md")
        if p.suffix not in _CODE_SUFFIXES and not instruction:
            continue
        if rel.parts[0] == ".git" or rel.parts[0] in _PROSE_ROOTS:
            continue
        # This file exists to NAME the marker -- its allowlist, its scanning
        # line and its own assertions all mention it. Scanning itself would
        # make every entry an offender and the exemption list a fixed point.
        if rel.name == "test_marker_is_gone.py":
            continue
        yield rel, p


def test_the_guard_can_actually_see_the_files_it_claims_to_check():
    """A scan that matches nothing reports total compliance. The sibling
    detector in this repo returned 'none flagged' for every parameter because
    it was reading strings instead of types -- a green result from a test that
    could not fail."""
    files = {str(rel) for rel, _ in _shipped_files()}
    assert "batch/run-queue.sh" in files, files
    assert "batch/lib/observe.sh" in files, files
    # The instruction files, which were invisible until 2026-08-31: `.md` was
    # not a scanned suffix, so the marker survived in the one place that caused
    # it to be written.
    assert "skills/muesli-loop/SKILL.md" in files, files
    assert len(files) > 20, files


#: The only lines of code permitted to name the marker, and why.
#:
#: Matched EXACTLY and required to be present: an unlisted site fails the test,
#: and a listed site that disappears fails it too. A one-way allowlist rots
#: into a dumping ground -- this one cannot, because a stale entry is an error.
_ALLOWED_LINES = {
    'or head.startswith("bircher-status:")':
        "READS the archive. Real PRs carry thousands of legacy markers, and a "
        "digest filter that stopped recognising them would start feeding a "
        "session the status lines its predecessor wrote. Retiring a channel "
        "means never writing one again, not forgetting how to read history.",
    "grep -q 'bircher-status:' \"$shimdir/comment.txt\" \\":
        "ASSERTS ABSENCE. The self-test that fails if a future edit restores "
        "the channel by restoring its prefix.",
    'assert "bircher-status" not in " ".join(str(a) for _c, _k, a in d.posted)':
        "ASSERTS ABSENCE. The check that the DERIVED comment carries no "
        "channel -- the Python half of the same property the shell guard "
        "above covers.",
    '{"id": 2, "author": "bircher-bot", "body": "bircher-status: running"},':
        "ARCHIVE DATA. Real issues carry these comments and always will; the "
        "bundle freezer has to hash an issue as it actually exists, and a "
        "fixture that pretended otherwise would test a shape that does not "
        "occur.",
    'This block used to specify a `bircher-status:` line and require you to fill in':
        "EXPLAINS THE RETIREMENT, in the same instruction file. Kept because a block that simply vanished would leave a session wondering what to report instead; this says nothing, and why.",
    'So: do not write a `bircher-status:` line, in a PR comment, a PR body, an':
        "FORBIDS IT BY NAME, in the instruction file that used to mandate it. The prohibition has to name the thing to be effective for a model reader -- the smoke items that avoided the marker did so only because their text forbade it by name, while issue-derived text did not and #735 carried one. Prompt wording is not the mechanism (this scan is); it is what stops the model writing one before the scan ever runs.",
}


def test_no_shipped_file_writes_or_reads_the_marker():
    offenders, seen = [], set()
    for rel, path in _shipped_files():
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if "bircher-status" not in line:
                continue
            stripped = line.strip()
            # A comment explaining the retired marker is legitimate history.
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if stripped in _ALLOWED_LINES:
                seen.add(stripped)
                continue
            offenders.append(f"{rel}:{n}: {stripped[:70]}")
    assert not offenders, (
        "the marker is retired; these still write or read one:\n  "
        + "\n  ".join(offenders))

    missing = set(_ALLOWED_LINES) - seen
    assert not missing, (
        "these exemptions no longer match anything and should be deleted -- "
        "an allowlist that outlives its sites stops being a record of what was "
        f"decided: {sorted(missing)}")


def test_parse_marker_is_gone():
    src = (REPO_ROOT / "batch" / "run-queue.sh").read_text()
    assert "parse_marker" not in src, "parse_marker still exists"
    assert "_marker_bodies_since" not in src, "_marker_bodies_since still exists"
