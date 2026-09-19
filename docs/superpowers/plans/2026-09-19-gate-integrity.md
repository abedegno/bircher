# Gate Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `bircher/cross-review` status is posted for every review verdict, names the reviewed range, and is backed by exactly one reviewer prompt that tells the reviewer the range; the verdict fact records what was reviewed.

**Architecture:** The reviewer prompt lives only in Python (`v2/coordinator/review.py`) and gains one sentence naming `origin/<base>...<head>`. The derivation (`v2/coordinator/outcome.py`) posts the status itself, through the same journalled effect path it already uses for the PR comment, and carries the merge-base sha and the three-dot delta digest out to the runner, which records them on the `review_verdict` fact. The bash copies of the prompt and of the delta digest go; the bash status post becomes an idempotent fallback.

**Tech Stack:** Python 3 (no third-party packages; `pytest` 8.3.4 run as `cd v2 && python -m pytest -q`), bash (`bash batch/run-queue.sh --self-test`, hermetic, fake `gh` on PATH), GitHub REST via `gh api`, SQLite kernel journal.

**Spec:** `docs/superpowers/specs/2026-09-19-back-half-closed-loop-design.md`, section 4 "Gate integrity" (the first of that spec's five PRs).

## Global Constraints

- No AI attribution anywhere: no `Co-Authored-By`, no `Claude-Session`, no "Generated with" in any commit message or PR body. Commit as the repository's existing author identity (`git log -1 --format='%an <%ae>'` on `main`).
- The muesli `review-gate` workflow is out of scope and unchanged; it keeps requiring a `bircher/cross-review` status with state `success` on the head.
- The status description format is exactly `<vendor> round <n> <PASS|FAIL (<k> blocking)|no verdict> on <merge-base7>..<head7>`, truncated to 140 characters (GitHub's limit).
- The range sentence in the prompt names `git diff origin/<base>...<head>` where `<base>` is the PR's base branch, and says a head commit touching one file does not narrow the review.
- Every GitHub mutation goes through the effect lifecycle (`perform_effect` in Python, `_effect` in bash); nothing posts a status unjournalled in kernel mode.
- Nothing in the front half, the shaping phase, or the kernel's transition table changes in this PR.
- Work on branch `spec/back-half-closed-loop` in worktree `/Users/jonw/bircher/.worktrees/back-half-closed-loop` (it holds the spec); the PR targets `main`. Bircher PR #101 (`fix/recovery-review-range`) is superseded by Task 1 and is closed, not merged, in Task 9.

---

## File structure

| file | responsibility after this plan |
|---|---|
| `v2/coordinator/review.py` | the only reviewer prompt; `review_prompt` and `dispatch` take the PR's base branch |
| `v2/coordinator/attest.py` (new) | pure helpers: count blocking findings, map a verdict to a status state, render the description |
| `v2/coordinator/delta.py` (new) | the PR's three-dot delta digest, ported from bash; the only implementation |
| `v2/coordinator/outcome.py` | `Deps` gains base/merge-base/digest/round deps; `Derived` carries merge-base and digest (ten fields); `derive` posts the status |
| `v2/coordinator/wiring.py` | wires the three new deps to `gh` |
| `v2/coordinator/cli.py` | `derive` computes the round from the journal; new `delta` subcommand for bash |
| `v2/kernel/commands.py`, `v2/kernel/events.py` | `review_verdict` stores `head_sha`, `merge_base_sha`, `delta_digest`; schema version 2 |
| `batch/lib/kernel-client.sh` | `_kernel_record_review` accepts the three range fields |
| `batch/run-queue.sh` | ten-field tuple; `_recovery_review_prompt` and `_pr_delta_digest` deleted; `_post_cross_review_status` idempotent with optional state/description; restamp uses the CLI digest |
| tests | `v2/tests/coordinator/test_attest.py`, `test_delta.py` (new); `test_ci_and_review.py`, `test_outcome.py`, `test_repair_loop.py`, `v2/tests/kernel/test_review_r3b.py`, `v2/tests/execution/test_settled_pr_reaches_the_caller.py` (modified); the bash self-test |

---

### Task 1: One reviewer prompt, and it names the range

**Files:**
- Modify: `v2/coordinator/review.py:84-129` (`_PROMPT`, `review_prompt`) and `:132-181` (`dispatch`)
- Modify: `v2/coordinator/wiring.py:107-111` (`do_review`)
- Modify: `batch/run-queue.sh` — delete `_recovery_review_prompt` (comment block from ~2573 through the function's closing `}` at ~2615) and its self-test assertions (~7879-7912)
- Modify: `v2/tests/coordinator/test_ci_and_review.py:456-480`
- Modify: `v2/tests/execution/test_observe_execution.py:10` (docstring only)

**Interfaces:**
- Produces: `review_prompt(pr: str, repo: str, sha: str = "", nonce: str = "", base: str = "main") -> str`; `dispatch(pr, repo, sha, *, reviewer, bundle_dir, server, log_path, run=None, base: str = "main") -> tuple[str | None, str]`.
- Consumed by Task 5, which wires `base` from `gh`.

- [ ] **Step 1: Write the failing tests** — replace `test_the_rendered_prompt_is_byte_identical_to_the_bash` (line 456) with these two, keeping `test_an_absent_sha_falls_back_to_FETCH_HEAD`:

```python
def test_the_prompt_names_the_range_against_the_prs_base():
    p = review_prompt("7", "o/r", "abc123", base="develop")
    assert "git diff origin/develop...abc123" in p
    assert "NOT only the head commit's own diff" in p
    # The pinned checkout is unchanged by naming the range.
    assert "You are reviewing EXACTLY commit abc123." in p


def test_the_base_defaults_to_main():
    assert "git diff origin/main...abc123" in review_prompt("7", "o/r", "abc123")


def test_the_range_reaches_the_runner(tmp_path):
    seen = {}

    def run(argv, cwd):
        seen["argv"] = argv
        return _R(0, "VERDICT: PASS")

    dispatch("7", "o/r", "dead", reviewer="claude_code", bundle_dir="/b",
             server="http://s", log_path=str(tmp_path / "l"), run=run, base="release")
    assert any("origin/release...dead" in a for a in seen["argv"]), "the base must reach the prompt"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_ci_and_review.py -k "range or defaults_to_main" -v`
Expected: FAIL — `review_prompt() got an unexpected keyword argument 'base'` and `dispatch() got an unexpected keyword argument 'base'`.

- [ ] **Step 3: Add the sentence and the parameter.** In `_PROMPT`, insert this line directly after the `You are reviewing EXACTLY commit {co}. ...` line:

```
The change under review is the WHOLE pull request as it stands at that commit: every file in `git diff origin/{base}...{co}` (from the PR's merge-base with {base} up to the pinned head), NOT only the head commit's own diff. A head commit that touches one file does not narrow the review to one file.
```

Change the signature and the format call:

```python
def review_prompt(pr: str, repo: str, sha: str = "", nonce: str = "", base: str = "main") -> str:
    ...
    return _PROMPT.format(pr=pr, repo=repo, co=(sha or "FETCH_HEAD"),
                          nonce=(nonce or (sha or "head")[:8]), base=(base or "main"))
```

In `dispatch`, add `base: str = "main"` as the last keyword parameter and pass `base=base` where it calls `review_prompt(...)`. Replace the docstring sentence above `_PROMPT` that mentions `test_the_rendered_prompt_is_byte_identical_to_the_bash` with: `#: The only copy: the bash `_recovery_review_prompt` it was ported from is gone (gate integrity, spec §4), so nothing keeps two prompts in step because there is one.`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_ci_and_review.py -v`
Expected: all PASS (the byte-identity test no longer exists).

- [ ] **Step 5: Wire the base in `do_review`** (`v2/coordinator/wiring.py`):

```python
    def base_of(pr):
        try:
            return _gh(["api", f"repos/{repo}/pulls/{pr}", "--jq", ".base.ref"]).strip() or "main"
        except GhError:
            return "main"

    def do_review(pr, sha):
        return review.dispatch(
            str(pr), repo, sha, reviewer=reviewer, bundle_dir=bundle_dir,
            server=server,
            log_path=os.environ.get("BIRCHER_REVIEW_LOG") or f"/tmp/review-{item}.log",
            base=base_of(pr))
```

(`base_of` is also added to `Deps` in Task 4; define it here once and pass it there in Task 5.)

- [ ] **Step 6: Delete the bash copy.** In `batch/run-queue.sh` remove the whole `_recovery_review_prompt` function together with its leading comment block (`# _recovery_review_prompt <pr> -> the read-only reviewer sub-agent input.` through the closing `}` after the heredoc's `EOF`). In the self-test, remove from the comment `# #66: the prompt must pin the checkout to the CAPTURED sha.` through the line `echo "_recovery_review_prompt pins the reviewed commit OK (#66)"` inclusive. Confirm nothing else references it:

Run: `grep -n "_recovery_review_prompt" batch/run-queue.sh batch/lib/*.sh v2 -r`
Expected: no output.

Edit the docstring at `v2/tests/execution/test_observe_execution.py:10` so it no longer claims the prompt "renders byte-identically to the bash it came from"; say instead that the prompt is the coordinator's own.

- [ ] **Step 7: Run the bash self-test and the whole pytest suite**

Run: `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; echo rc=$?; tail -3 /tmp/selftest.log` and `cd v2 && python -m pytest -q`
Expected: self-test rc=0 with no `FAIL` line mentioning `_recovery_review_prompt`; pytest all green. (The runner's self-test fails on a root-only test unrelated to this work; run the self-test locally as a non-root user.)

- [ ] **Step 8: Commit**

```bash
git add v2/coordinator/review.py v2/coordinator/wiring.py batch/run-queue.sh v2/tests/coordinator/test_ci_and_review.py v2/tests/execution/test_observe_execution.py
git commit -m "One reviewer prompt, and it names the range"
```

---

### Task 2: What a status attests to (pure helpers)

**Files:**
- Create: `v2/coordinator/attest.py`
- Test: `v2/tests/coordinator/test_attest.py`

**Interfaces:**
- Produces: `CONTEXT = "bircher/cross-review"`; `blocking_count(text: str) -> int`; `state_for(verdict: str | None) -> str` (`success`/`failure`/`error`); `describe(vendor: str, round_n: int, verdict: str | None, blocking: int, merge_base: str, head: str) -> str`.
- Consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from coordinator.attest import CONTEXT, blocking_count, describe, state_for

CODEX_FAIL = """I'll check out the commit.
Blocking findings

- `src/main/livePromptRelay.ts:206-209` provides `stopAll()` but it is never called.
- The eight-template cap is vulnerable to concurrent creates.

Non-blocking findings

- None.

Suggestions

- Make snapshot/reread state consistent.

Verification

- `go build ./...`: passed
- `go vet ./...`: passed

VERDICT: FAIL
"""

CODEX_NUMBERED = """Blocking findings

1. Hosted deployments have no live-prompts transport.
2. Trashing an active note does not end its live work.
3. Required feature-level E2E coverage is absent.

Non-blocking findings

- None.

VERDICT: FAIL
"""

CODEX_PASS = """The commit only corrects a regression test.Blocking findings: None.

Non-blocking findings: None.

Suggestions: None.

VERDICT: PASS
"""


@pytest.mark.parametrize("text, expected", [
    (CODEX_FAIL, 2),
    (CODEX_NUMBERED, 3),
    (CODEX_PASS, 0),
    ("", 0),
    ("no headings at all\nVERDICT: PASS", 0),
])
def test_blocking_findings_are_counted_from_the_blocking_section_only(text, expected):
    assert blocking_count(text) == expected


def test_context_is_the_gate_the_repository_requires():
    assert CONTEXT == "bircher/cross-review"


@pytest.mark.parametrize("verdict, state", [
    ("PASS", "success"), ("FAIL", "failure"), (None, "error"), ("", "error"), ("BLOCKED", "error"),
])
def test_state_follows_the_verdict_and_silence_is_an_error(verdict, state):
    assert state_for(verdict) == state


def test_the_description_names_vendor_round_verdict_and_range():
    d = describe("codex", 3, "PASS", 0, "2742ae4f" + "0" * 32, "e4ea3eb7" + "1" * 32)
    assert d == "codex round 3 PASS on 2742ae4..e4ea3eb"


def test_a_fail_carries_its_blocking_count():
    assert describe("codex", 4, "FAIL", 3, "a" * 40, "b" * 40) == "codex round 4 FAIL (3 blocking) on aaaaaaa..bbbbbbb"


def test_no_verdict_says_so_and_an_unknown_base_is_a_question_mark():
    assert describe("claude_code", 1, None, 0, "", "b" * 40) == "claude_code round 1 no verdict on ?..bbbbbbb"


def test_the_description_never_exceeds_githubs_limit():
    assert len(describe("x" * 200, 1, "PASS", 0, "a" * 40, "b" * 40)) <= 140
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_attest.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'coordinator.attest'`.

- [ ] **Step 3: Write the module**

```python
"""What a cross-review status attests to (spec §4, gate integrity).

Pure. The description names the vendor, the round, the verdict and the
merge-base..head range, so a person reading the PR's checks can see exactly
what was reviewed; the state follows the verdict, and silence is an error,
never a pass.
"""

from __future__ import annotations

import re

#: The context muesli's `review-gate` workflow requires `success` on.
CONTEXT = "bircher/cross-review"

#: GitHub truncates longer descriptions; truncate deliberately instead.
_MAX_DESCRIPTION = 140

_BLOCKING = re.compile(r"^\W*blocking findings\W*(.*)$", re.I)
_OTHER_SECTION = re.compile(r"^\W*(non-blocking findings|suggestions|verification|verdict)\b", re.I)
#: A list item at the section's own indentation: `- `, `* `, `• `, `1. `, `1) `.
_ITEM = re.compile(r"^ {0,2}(?:[-*•]|\d+[.)])\s+\S")
_NONE = re.compile(r"^(none|n/a|no blocking findings)\.?$", re.I)


def blocking_count(text: str) -> int:
    """How many items the reviewer listed under its blocking-findings heading.

    Counts list items between the heading and the next section heading, at
    the section's own indentation (a nested sub-bullet elaborates a finding,
    it is not another one). A heading followed on the same line by `None`
    counts nothing; a heading followed by a finding on the same line counts
    one. Text with no heading counts nothing, so an unstructured FAIL posts
    `0 blocking` rather than a guess.
    """
    count, inside = 0, False
    for line in (text or "").splitlines():
        m = _BLOCKING.match(line)
        if m:
            inside = True
            rest = m.group(1).strip()
            if rest and not _NONE.match(rest):
                count += 1
            continue
        if inside and _OTHER_SECTION.match(line):
            break
        if inside and _ITEM.match(line):
            count += 1
    return count


def state_for(verdict: str | None) -> str:
    """`success` for PASS, `failure` for FAIL, `error` for anything else.

    BLOCKED, an empty verdict and a reviewer that never produced one all
    land on `error`: none of them is an approval, and the gate requires
    `success` by name.
    """
    return {"PASS": "success", "FAIL": "failure"}.get(verdict or "", "error")


def describe(vendor: str, round_n: int, verdict: str | None, blocking: int,
             merge_base: str, head: str) -> str:
    """`<vendor> round <n> <word> on <merge-base7>..<head7>`."""
    if verdict == "PASS":
        word = "PASS"
    elif verdict == "FAIL":
        word = f"FAIL ({blocking} blocking)"
    else:
        word = "no verdict"
    rng = f"{(merge_base or '')[:7] or '?'}..{(head or '')[:7] or '?'}"
    return f"{vendor} round {round_n} {word} on {rng}"[:_MAX_DESCRIPTION]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_attest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/coordinator/attest.py v2/tests/coordinator/test_attest.py
git commit -m "Name what a cross-review status attests to"
```

---

### Task 3: The delta digest in Python, and only there

**Files:**
- Create: `v2/coordinator/delta.py`
- Modify: `v2/coordinator/cli.py` (new `delta` subcommand beside `derive`)
- Modify: `batch/run-queue.sh` — `_restamp_if_delta_unchanged` (~1298-1350) calls the CLI; `_pr_delta_digest` and its comment block deleted
- Test: `v2/tests/coordinator/test_delta.py`

**Interfaces:**
- Produces: `canonical_delta(compare: dict, tree: dict) -> str | None` (the canonical JSON text, or None when unprovable); `delta_digest(repo: str, base: str, ref: str, gh=_gh) -> str` (sha256 hex, or `""` when unprovable); CLI `python3 -m coordinator.cli delta --repo <owner/name> --base <ref> --ref <ref>` printing the hex digest, rc 0, or nothing with rc 1.
- Consumed by Task 4 (`Deps.delta_digest`) and by the bash restamp.

- [ ] **Step 1: Write the failing tests.** The fixtures are the self-test's own (`batch/run-queue.sh` ~7662 and ~7673), so the Python answer is pinned to what the bash answered for the same input before the bash was deleted:

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from coordinator.delta import canonical_delta, delta_digest

COMPARE_B1 = {"files": [{"filename": "a.txt", "status": "modified",
                         "sha": "blob0000000000000000000000000000000000b1",
                         "patch": "@@ -1 +1 @@\n-old\n+new"}]}
COMPARE_B2 = {"files": [{"filename": "a.txt", "status": "modified",
                         "sha": "blob0000000000000000000000000000000000b2",
                         "patch": "@@ -1 +1 @@\n-old\n+SOMETHING ELSE"}]}
TREE = {"truncated": False, "tree": [{"path": "a.txt", "mode": "100644", "type": "blob"}]}


def _gh_for(compare, tree):
    def gh(args):
        joined = " ".join(args)
        if "/compare/" in joined:
            return json.dumps(compare)
        if "/git/trees/" in joined:
            return json.dumps(tree)
        raise AssertionError(f"unexpected gh call {args}")
    return gh


def test_the_canonical_form_is_sorted_compact_and_carries_mode_and_type():
    text = canonical_delta(COMPARE_B1, TREE)
    assert text == ('[{"entry":"100644|blob","filename":"a.txt","patch":"@@ -1 +1 @@\\n-old\\n+new",'
                    '"previous_filename":null,"sha":"blob0000000000000000000000000000000000b1",'
                    '"status":"modified"}]')


def test_the_digest_is_the_sha256_of_the_canonical_form():
    text = canonical_delta(COMPARE_B1, TREE)
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE)) == hashlib.sha256(text.encode()).hexdigest()


def test_a_changed_patch_changes_the_digest():
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE)) != \
        delta_digest("o/r", "main", "b2", gh=_gh_for(COMPARE_B2, TREE))


def test_a_path_absent_from_the_tree_is_marked_absent():
    assert '"entry":"ABSENT"' in canonical_delta(COMPARE_B1, {"truncated": False, "tree": []})


@pytest.mark.parametrize("compare, tree", [
    ({"files": []}, TREE),                                                   # 0 files
    ({"files": [dict(COMPARE_B1["files"][0]) for _ in range(300)]}, TREE),   # GitHub's cap
    ({"files": [{"filename": "a.txt", "status": "modified", "sha": "x"}]}, TREE),  # patch withheld
    (COMPARE_B1, {"truncated": True, "tree": []}),                           # truncated tree
    (COMPARE_B1, {"tree": []}),                                              # truncated absent, not false
])
def test_every_unprovable_case_yields_no_digest(compare, tree):
    assert canonical_delta(compare, tree) is None
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(compare, tree)) == ""


def test_a_failed_lookup_is_unprovable_not_a_crash():
    def gh(args):
        raise RuntimeError("boom")
    assert delta_digest("o/r", "main", "b1", gh=gh) == ""


def test_the_cli_prints_the_digest_or_nothing(tmp_path, monkeypatch):
    # A fake gh on PATH, answering by URL shape as the self-test's does.
    fake = tmp_path / "gh"
    fake.write_text('#!/bin/bash\ncase "$*" in *"/compare/"*) printf %s \'' + json.dumps(COMPARE_B1) +
                    '\' ;; *"/git/trees/"*) printf %s \'' + json.dumps(TREE) + '\' ;; *) exit 1 ;; esac\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "BIRCHER_GH_REPO": "o/r"}
    r = subprocess.run(["python3", "-m", "coordinator.cli", "delta", "--repo", "o/r", "--base", "main", "--ref", "b1"],
                       capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parents[2]))
    assert r.returncode == 0
    assert r.stdout.strip() == delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_delta.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'coordinator.delta'`.

- [ ] **Step 3: Before writing the port, freeze the bash answer.** With the bash function still present, render its digest for the fixture so the port is checked against it once:

```bash
cd /Users/jonw/bircher/.worktrees/back-half-closed-loop
tmp=$(mktemp -d); cat > "$tmp/gh" <<'SH'
#!/bin/bash
case "$*" in
  *"/compare/"*) printf '%s' '{"files":[{"filename":"a.txt","status":"modified","sha":"blob0000000000000000000000000000000000b1","patch":"@@ -1 +1 @@\n-old\n+new"}]}' ;;
  *"/git/trees/"*) printf '%s' '{"truncated":false,"tree":[{"path":"a.txt","mode":"100644","type":"blob"}]}' ;;
  *) exit 1 ;;
esac
SH
chmod +x "$tmp/gh"
PATH="$tmp:$PATH" REPO=o/r bash -c 'source <(sed -n "/^_sha256()/,/^}/p; /^_pr_delta_digest()/,/^}/p" batch/run-queue.sh); _pr_delta_digest main b1'
```

Record the printed hex. Step 4's port must reproduce it: add `test_the_port_reproduces_the_bash_digest_for_the_fixture` asserting `delta_digest("o/r","main","b1", gh=_gh_for(COMPARE_B1, TREE)) == "<that hex>"`. If it does not match, the canonical JSON differs (key order, spacing, unicode or null rendering); fix the port, never the expected value.

- [ ] **Step 4: Write the module**

```python
"""The PR's own three-dot delta, digested (gate integrity, spec §4).

Ported from `_pr_delta_digest` in `batch/run-queue.sh`, which is gone: one
implementation, used by the derivation (to record what was reviewed on the
verdict fact) and by the runner's re-stamp after an update-branch (through
`coordinator.cli delta`).

GitHub's compare is three-dot, so `base...ref` is the change the PR
contributes and never the base branch's own commits. That property is what
makes the digest comparable across an update-branch: that operation MERGES
the base into the head (it does not rebase), so the new head CONTAINS the new
base, the merge-base IS the new base, and the compare still yields only the
PR's work.

An unprovable delta is `None`, never "assume equal": GitHub caps the
changed-file list at 300, omits `patch` for binaries, pure renames and
anything over its size limit, and truncates a large tree. Digesting a partial
answer would produce a confident wrong one, which here means merging code no
reviewer read.
"""

from __future__ import annotations

import hashlib
import json

from coordinator.ci import GhError, _gh

#: GitHub's documented cap on a compare's file list; at exactly 300 it may be truncated.
_FILE_CAP = 300


def canonical_delta(compare: dict, tree: dict) -> str | None:
    """The canonical text of the delta, or None when it cannot be established."""
    files = (compare or {}).get("files")
    if not isinstance(files, list):
        return None
    if not (1 <= len(files) < _FILE_CAP):
        return None
    if any("patch" not in f for f in files):
        return None
    # Require an explicit false: an absent or null `truncated` is an answer we
    # did not get, not a reassuring one.
    if (tree or {}).get("truncated") is not False:
        return None
    entries = {e.get("path"): f"{e.get('mode')}|{e.get('type')}" for e in (tree.get("tree") or [])}
    rows = []
    for f in sorted(files, key=lambda f: f["filename"]):
        rows.append({
            "filename": f["filename"],
            "previous_filename": f.get("previous_filename"),
            "status": f.get("status"),
            "sha": f.get("sha"),
            "patch": f.get("patch"),
            "entry": entries.get(f["filename"], "ABSENT"),
        })
    # Sorted keys, no whitespace, unicode kept raw: the same bytes `jq -cS` produced.
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def delta_digest(repo: str, base: str, ref: str, gh=_gh) -> str:
    """sha256 of the canonical delta of `base...ref`, or "" when unprovable."""
    try:
        compare = json.loads(gh(["api", f"repos/{repo}/compare/{base}...{ref}"]) or "null")
        tree = json.loads(gh(["api", f"repos/{repo}/git/trees/{ref}?recursive=1"]) or "null")
    except (GhError, ValueError, TypeError, RuntimeError):
        return ""
    text = canonical_delta(compare, tree)
    if text is None:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Add the CLI subcommand** in `v2/coordinator/cli.py`, next to the `derive` parser:

```python
    dl = subs.add_parser("delta")
    dl.add_argument("--repo", required=True)
    dl.add_argument("--base", required=True)
    dl.add_argument("--ref", required=True)
```

and in the dispatch section, before `if a.mode == "derive":`:

```python
    if a.mode == "delta":
        from coordinator.delta import delta_digest
        os.environ["BIRCHER_GH_REPO"] = a.repo
        digest = delta_digest(a.repo, a.base, a.ref)
        if not digest:
            return 1
        print(digest, end="")
        return RC_OK
```

- [ ] **Step 6: Run the tests to verify they pass** (including the frozen-digest test from Step 3)

Run: `cd v2 && python -m pytest -q tests/coordinator/test_delta.py -v`
Expected: all PASS.

- [ ] **Step 7: Point the restamp at the CLI and delete the bash function.** In `_restamp_if_delta_unchanged` replace

```bash
  old_digest=$(_pr_delta_digest "$base" "$reviewed") || old_digest=""
  new_digest=$(_pr_delta_digest "$base" "$new")      || new_digest=""
```

with

```bash
  old_digest=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
                 python3 -m coordinator.cli delta --repo "$REPO" --base "$base" --ref "$reviewed" 2>/dev/null) || old_digest=""
  new_digest=$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
                 python3 -m coordinator.cli delta --repo "$REPO" --base "$base" --ref "$new" 2>/dev/null) || new_digest=""
```

(`python3 -m coordinator.cli` is how `observe_outcome` already invokes the coordinator; copy its working-directory handling from that call site, ~2995-3036, so the module resolves.) Delete `_pr_delta_digest` and its comment block (from `# _pr_delta_digest <base_ref> <head_ref> -> ...` through the function's `}`); keep `_sha256`, which other code uses.

Run: `grep -n "_pr_delta_digest" batch/run-queue.sh batch/lib/*.sh`
Expected: no output.

- [ ] **Step 8: Run the self-test; the two delta fixtures are the oracle**

Run: `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; echo rc=$?; grep -n "sweep-behind" /tmp/selftest.log`
Expected: rc=0; both `sweep-behind-changed` (delta CHANGED → no re-stamp) and `sweep-behind-same` (delta identical → re-stamped) still pass. If the Python CLI cannot find `coordinator` from the self-test's working directory, mirror `observe_outcome`'s `cd`/`PYTHONPATH` handling exactly.

- [ ] **Step 9: Commit**

```bash
git add v2/coordinator/delta.py v2/coordinator/cli.py batch/run-queue.sh v2/tests/coordinator/test_delta.py
git commit -m "One delta digest, in the coordinator"
```

---

### Task 4: The derivation records the range and posts a status for every verdict

**Files:**
- Modify: `v2/coordinator/outcome.py:27-115` (`Deps`, `Derived`) and `:169-266` (`derive`)
- Modify: `v2/tests/coordinator/test_outcome.py`, `v2/tests/coordinator/test_repair_loop.py:109-113`
- Test: additions to `v2/tests/coordinator/test_outcome.py`

**Interfaces:**
- Consumes: Task 2's `attest` helpers.
- Produces: `Deps.base_of: callable = lambda pr: "main"`, `Deps.merge_base: callable = lambda base, head: ""`, `Deps.delta_digest: callable = lambda base, head: ""`, `Deps.round_number: int = 1`; `Derived.merge_base: str = ""`, `Derived.delta_digest: str = ""`; `Derived.FIELDS == 10`; `as_line()` = `outcome|review|note|sha|ci|ci_first|resubmissions|pr|merge_base|delta_digest`.
- Consumed by Tasks 5 and 7.

- [ ] **Step 1: Write the failing tests.** Find how `test_outcome.py` builds a `Deps` for a green PR with a PASS review (it has one near line 33; copy its construction into a helper if none exists) and add:

```python
def _green_pass_deps(effects, verdict="PASS", out="VERDICT: PASS"):
    return Deps(
        checks=lambda pr: "ci|pass",
        head_of=lambda pr: "e4ea3eb7" + "1" * 32,
        review=lambda pr, sha: (verdict, out),
        effect=lambda cls, key, argv: effects.append((cls, key, argv)) or "ok",
        base_of=lambda pr: "develop",
        merge_base=lambda base, head: "2742ae4f" + "0" * 32,
        delta_digest=lambda base, head: "d" * 64,
        round_number=3,
        reviewer="codex",
        repo="o/r",
    )


def test_the_line_has_ten_fields_and_carries_the_range():
    effects = []
    r = derive("i", "c", "7", "", deps=_green_pass_deps(effects))
    assert r.FIELDS == 10
    fields = r.as_line().split("|")
    assert len(fields) == 10
    assert fields[8] == "2742ae4f" + "0" * 32
    assert fields[9] == "d" * 64


def test_a_pass_posts_a_success_status_naming_the_range():
    effects = []
    derive("i", "c", "7", "", deps=_green_pass_deps(effects))
    posts = [e for e in effects if e[0] == "status_check"]
    assert len(posts) == 1
    cls, key, argv = posts[0]
    assert key == "status:" + "e4ea3eb7" + "1" * 32 + ":3"
    assert "repos/o/r/statuses/e4ea3eb7" + "1" * 32 in argv
    assert "state=success" in argv
    assert "context=bircher/cross-review" in argv
    assert "description=codex round 3 PASS on 2742ae4..e4ea3eb" in argv


def test_a_fail_posts_failure_with_its_blocking_count():
    effects = []
    out = "Blocking findings\n\n- one\n- two\n\nNon-blocking findings\n\n- None.\n\nVERDICT: FAIL\n"
    derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="FAIL", out=out))
    argv = [e for e in effects if e[0] == "status_check"][0][2]
    assert "state=failure" in argv
    assert "description=codex round 3 FAIL (2 blocking) on 2742ae4..e4ea3eb" in argv


def test_no_verdict_posts_error():
    effects = []
    derive("i", "c", "7", "", deps=_green_pass_deps(effects, verdict="NONE", out="rambling"))
    argv = [e for e in effects if e[0] == "status_check"][0][2]
    assert "state=error" in argv
    assert "no verdict on 2742ae4..e4ea3eb" in " ".join(argv)


def test_a_failed_status_post_does_not_change_the_outcome():
    def effect(cls, key, argv):
        if cls == "status_check":
            raise RuntimeError("github is down")
        return "ok"
    d = _green_pass_deps([])
    d.effect = effect
    r = derive("i", "c", "7", "", deps=d)
    assert r.outcome == "ready"


def test_no_status_is_posted_without_a_pinned_head():
    effects = []
    d = _green_pass_deps(effects)
    d.head_of = lambda pr: "not-a-sha"
    derive("i", "c", "7", "", deps=d)
    assert not [e for e in effects if e[0] == "status_check"]


def test_red_ci_posts_nothing_and_carries_no_range():
    effects = []
    d = _green_pass_deps(effects)
    d.checks = lambda pr: "ci|fail"
    r = derive("i", "c", "7", "", deps=d)
    assert not [e for e in effects if e[0] == "status_check"]
    assert r.merge_base == "" and r.delta_digest == ""
```

Also change `test_repair_loop.py:111` to `assert len(d.as_line().split("|")) == Derived.FIELDS == 10` and give its `Derived(...)` construction two trailing keyword arguments `merge_base="", delta_digest=""` only if it uses positional `findings` (it uses `findings=` by keyword, so no change there).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd v2 && python -m pytest -q tests/coordinator/test_outcome.py -k "ten_fields or posts or status or pinned_head or carries_no_range" -v`
Expected: FAIL — `Deps.__init__() got an unexpected keyword argument 'base_of'`.

- [ ] **Step 3: Widen `Deps` and `Derived`.** In `Deps`, after `repo: str = ""`:

```python
    #: The PR's base branch, for the reviewer's range and the merge-base.
    base_of: callable = lambda pr: "main"
    #: (base, head) -> merge-base sha, or "" when unknown.
    merge_base: callable = lambda base, head: ""
    #: (base, head) -> the three-dot delta digest, or "" when unprovable.
    delta_digest: callable = lambda base, head: ""
    #: Which review round this is, from the journal (revisions used + 1).
    round_number: int = 1
```

In `Derived`, after `findings: str = ""`:

```python
    #: What the review covered: the merge-base with the PR's base, and the
    #: digest of the PR's own delta at the reviewed head. Both ride out on
    #: every derivation that pinned a head, so the runner can record them on
    #: the verdict fact (spec §4).
    merge_base: str = ""
    delta_digest: str = ""
```

Change `FIELDS = 8` to `FIELDS = 10`, and `as_tuple`/`as_line` to append the two:

```python
    def as_tuple(self):
        return (self.outcome, self.review, self.note, self.sha, self.ci,
                self.ci_first, self.resubmissions, self.pr, self.merge_base,
                self.delta_digest)

    def as_line(self) -> str:
        """The TEN-field form the shell parses. ..."""  # keep the existing warning text
        r = "" if self.resubmissions is None else self.resubmissions
        return (f"{self.outcome}|{self.review}|{self.note}|{self.sha}|"
                f"{self.ci}|{self.ci_first}|{r}|{self.pr}|{self.merge_base}|{self.delta_digest}")
```

- [ ] **Step 4: Post the status and carry the range in `derive`.** Add `from coordinator import attest` at the top. Inside `derive`, replace the block from `ci, verdict, reviewed_sha, reviewer_out = "na", None, "", ""` through the review call with:

```python
    ci, verdict, reviewed_sha, reviewer_out = "na", None, "", ""
    merge_base, digest = "", ""
    if pr:
        ci = _settle_ci(item, pr, d, rerun_max)

        if ci == "green":
            head = (d.head_of(pr) or "").strip()
            if _FULL_SHA.match(head):
                reviewed_sha = head
            else:
                d.log(f"{item}: could not capture a full 40-hex head for PR #{pr} "
                      f"(got {head or '<empty>'!r}) -> cannot pin a merge")
            d.log(f"{item}: PR #{pr} CI green -> {d.reviewer} review at {reviewed_sha[:7]}")
            verdict, reviewer_out = d.review(pr, reviewed_sha)
            if verdict == "NONE":
                verdict = None
            if reviewed_sha:
                # WHAT WAS REVIEWED, captured against the same pinned head the
                # reviewer was told to check out (spec §4): the merge-base with
                # the PR's base and the digest of the PR's own delta.
                base = d.base_of(pr) or "main"
                merge_base = (d.merge_base(base, reviewed_sha) or "").strip()
                digest = d.delta_digest(base, reviewed_sha) or ""
                _post_status(item, pr, reviewed_sha, verdict, reviewer_out,
                             merge_base, d)
```

and add, at module level below `_settle_ci`:

```python
def _post_status(item, pr, head, verdict, reviewer_out, merge_base, d: Deps) -> None:
    """Every verdict posts `bircher/cross-review` on the reviewed head: success,
    failure, or error, with the range in the description (spec §4). A FAIL
    can no longer leave an older success standing.

    BEST EFFORT, as the PR comment is: the outcome is derived from the
    repository and is already correct; a transient GitHub failure here must
    not turn a derivation into an escalation. The runner's own idempotent
    post before the merge is the fallback for a missing success.
    """
    blocking = attest.blocking_count(reviewer_out) if verdict == "FAIL" else 0
    state = attest.state_for(verdict)
    desc = attest.describe(d.reviewer, d.round_number, verdict, blocking, merge_base, head)
    key = f"status:{head}:{d.round_number}"
    try:
        d.effect("status_check", key,
                 ["gh", "api", f"repos/{d.repo}/statuses/{head}", "-X", "POST",
                  "-f", f"state={state}", "-f", f"context={attest.CONTEXT}",
                  "-f", f"description={desc}"])
        d.log(f"{item}: posted {attest.CONTEXT}={state} on {head[:7]} ({desc})")
    except Exception as exc:                       # noqa: BLE001
        d.log(f"{item}: failed to post {attest.CONTEXT}={state} on PR #{pr} "
              f"({type(exc).__name__}: {exc}) -> continuing; the outcome is "
              f"derived from the repository and is unaffected")
```

Finally, the return: `merge_base` and `digest` ride out whenever a head was pinned, so change the final `return Derived(...)` to pass `merge_base=merge_base if reviewed_sha else "", delta_digest=digest if reviewed_sha else ""` as keywords after `findings=...`.

- [ ] **Step 5: Run the coordinator tests**

Run: `cd v2 && python -m pytest -q tests/coordinator -v`
Expected: all PASS, including every pre-existing test in `test_outcome.py` and `test_repair_loop.py` (their `Derived(...)` constructions use keyword `findings`, so the two new trailing fields default to `""`).

- [ ] **Step 6: Commit**

```bash
git add v2/coordinator/outcome.py v2/tests/coordinator/test_outcome.py v2/tests/coordinator/test_repair_loop.py
git commit -m "Post a cross-review status for every verdict, naming the range"
```

---

### Task 5: Wire the range to `gh`, and the round to the journal

**Files:**
- Modify: `v2/coordinator/wiring.py:28-150` (`live_deps`)
- Modify: `v2/coordinator/cli.py:475-500` (`derive`)
- Test: `v2/tests/coordinator/test_wiring.py` if it exists, else add to `v2/tests/coordinator/test_outcome.py`

**Interfaces:**
- Consumes: Task 1's `base_of`, Task 3's `delta_digest`, Task 4's `Deps` fields.
- Produces: `live_deps(..., round_number: int = 1)`.

- [ ] **Step 1: Write the failing test.** `live_deps` is built from `gh`; test the round computation, which is pure, by extracting it:

```python
from coordinator.cli import _round_number


def test_the_round_is_revisions_used_plus_one(tmp_path):
    from kernel.store import Store
    db = tmp_path / "k.db"
    Store.open(str(db))  # an empty journal
    assert _round_number(str(db), "run-1") == 1


def test_no_journal_means_round_one():
    assert _round_number("", "") == 1
    assert _round_number("/nonexistent/path.db", "run-1") == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd v2 && python -m pytest -q tests/coordinator -k round_number -v`
Expected: FAIL with `ImportError: cannot import name '_round_number'`.

- [ ] **Step 3: Implement.** In `cli.py`, above the `main`/dispatch function:

```python
def _round_number(db: str, run_id: str) -> int:
    """Which review round the derivation is about to record: revisions used
    so far, from the journal, plus one. No journal, no run, or an unreadable
    database means round one -- the description must never block a status.
    """
    if not db or not run_id or not os.path.exists(db):
        return 1
    try:
        from kernel.store import Store
        from coordinator.observe import revisions_used
        return revisions_used(Store.open(db).facts_for(run_id)) + 1
    except Exception:                                  # noqa: BLE001
        return 1
```

and in the `derive` branch pass it: `round_number=_round_number(os.environ.get("BIRCHER_KERNEL_DB", ""), os.environ.get("BIRCHER_RUN_ID", ""))` into `live_deps(...)`.

In `wiring.py`, add the parameter `round_number: int = 1` to `live_deps`, and wire the three deps in the `Deps(...)` construction (import `delta` from `coordinator` beside `review`):

```python
    def merge_base(base, head):
        try:
            return _gh(["api", f"repos/{repo}/compare/{base}...{head}",
                        "--jq", ".merge_base_commit.sha"]).strip()
        except GhError:
            return ""
```

and in `return Deps(...)`:

```python
        base_of=base_of,
        merge_base=merge_base,
        delta_digest=lambda base, head: delta.delta_digest(repo, base, head),
        round_number=round_number,
```

- [ ] **Step 4: Run the tests**

Run: `cd v2 && python -m pytest -q tests/coordinator tests/execution -x`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/coordinator/wiring.py v2/coordinator/cli.py v2/tests/coordinator
git commit -m "Wire the reviewed range and the round into the derivation"
```

---

### Task 6: The verdict fact records the range

**Files:**
- Modify: `v2/kernel/commands.py:646-668` (the `record_review` fact payload)
- Modify: `v2/kernel/events.py` (`SCHEMA_VERSIONS[EventKind.REVIEW_VERDICT]`)
- Test: `v2/tests/kernel/test_review_r3b.py`

**Interfaces:**
- Produces: `review_verdict` payload keys `head_sha`, `merge_base_sha`, `delta_digest` (strings or `None`); schema version 2.
- Consumed by Task 7's runner call and by the next PR's step function.

- [ ] **Step 1: Write the failing test.** Copy the setup of `test_request_merge_with_an_accepted_review_is_authorized` in `v2/tests/kernel/test_authorization.py` (a run with an implementation output, then `record_review` with `accept`) into `test_review_r3b.py` as a helper, and add:

```python
def test_the_verdict_records_what_it_reviewed(store_and_run):
    store, run_id = store_and_run
    payload = _accept_payload(store, run_id)   # the helper's artifact/base/context binding
    payload.update({"head_sha": "e" * 40, "merge_base_sha": "2" * 40, "delta_digest": "d" * 64})
    submit(store, command("record_review", run_id, payload))
    fact = [f for f in store.facts_for(run_id) if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["head_sha"] == "e" * 40
    assert fact.payload["merge_base_sha"] == "2" * 40
    assert fact.payload["delta_digest"] == "d" * 64
    assert fact.schema_version == 2


def test_a_verdict_without_a_range_records_none_not_garbage(store_and_run):
    store, run_id = store_and_run
    submit(store, command("record_review", run_id, _accept_payload(store, run_id)))
    fact = [f for f in store.facts_for(run_id) if f.kind == EventKind.REVIEW_VERDICT][-1]
    assert fact.payload["head_sha"] is None
    assert fact.payload["merge_base_sha"] is None
    assert fact.payload["delta_digest"] is None
```

(Use the file's existing fixture and `submit`/`command` helpers by their real names; `_accept_payload` is the helper you write from the copied setup.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd v2 && python -m pytest -q tests/kernel/test_review_r3b.py -k "records_what_it_reviewed or without_a_range" -v`
Expected: FAIL with `KeyError: 'head_sha'`.

- [ ] **Step 3: Store the fields.** In the `record_review` payload dict in `commands.py`, after `"generation": cmd.generation,` add:

```python
                        # What was reviewed (spec §4, gate integrity): the pinned
                        # head, its merge-base with the PR's base, and the digest
                        # of the PR's own delta. None when the caller had none
                        # (a human ruling, or a derivation that pinned no head).
                        "head_sha": cmd.payload.get("head_sha"),
                        "merge_base_sha": cmd.payload.get("merge_base_sha"),
                        "delta_digest": cmd.payload.get("delta_digest"),
```

In `events.py` change `EventKind.REVIEW_VERDICT: 1,` to `EventKind.REVIEW_VERDICT: 2,` with the comment `# 2: head_sha, merge_base_sha, delta_digest (gate integrity)`.

- [ ] **Step 4: Run the kernel tests**

Run: `cd v2 && python -m pytest -q tests/kernel -x`
Expected: all PASS (the schema-version tests assert the map's values are `>= 1` and match, so 2 is accepted).

- [ ] **Step 5: Commit**

```bash
git add v2/kernel/commands.py v2/kernel/events.py v2/tests/kernel/test_review_r3b.py
git commit -m "The verdict fact records what it reviewed"
```

---

### Task 7: The runner reads ten fields and records the range

**Files:**
- Modify: `batch/lib/kernel-client.sh` (`_kernel_record_review`, ~855-880)
- Modify: `batch/run-queue.sh` — `_derived_width_ok` (~1010), the `IFS='|' read -r ...` in `run_item` (~5019), the `_kernel_record_review` call (~5062), and the two `obs="escalated|..."` fallbacks (~5003 and ~5013) which must now carry ten fields
- Modify: `v2/tests/execution/test_settled_pr_reaches_the_caller.py`, `v2/tests/execution/test_run_item_argument_wiring.py` (lines that assert `_kernel_record_review`'s argument list)

**Interfaces:**
- Consumes: Task 4's ten-field line; Task 6's payload keys.
- Produces: `_kernel_record_review <run_id> <generation> <verdict> <artifact> <base> <context> [key] [terminal] [head_sha] [merge_base_sha] [delta_digest]`.

- [ ] **Step 1: Write the failing tests.** In `test_settled_pr_reaches_the_caller.py`, `test_the_shell_reads_as_many_fields_as_the_line_emits` already asserts the `read -r` name count equals `Derived.FIELDS`; it fails by itself once Task 4 landed. Add, in `test_run_item_argument_wiring.py` beside the existing `_kernel_record_review` assertions:

```python
def test_the_review_record_carries_the_range():
    src = _run_item_source()   # the file's existing helper that slices run_item's text
    call = src[src.index("_kernel_record_review \"$BIRCHER_RUN_ID\""):]
    call = call[:call.index("\n")]
    for name in ('"$observed_head"', '"$_merge_base"', '"$_delta_digest"'):
        assert name in call, f"{name} must reach the review record"
```

Run: `cd v2 && python -m pytest -q tests/execution/test_settled_pr_reaches_the_caller.py tests/execution/test_run_item_argument_wiring.py -v`
Expected: FAIL — the name count is 8 against `FIELDS == 10`, and the call lacks the range.

- [ ] **Step 2: Widen the client.** In `_kernel_record_review`, extend the signature comment and locals:

```bash
_kernel_record_review() {  # <run_id> <generation> <verdict> <artifact> <base> <context> [key] [terminal] [head_sha] [merge_base_sha] [delta_digest]
  local run_id="$1" generation="$2" raw="$3" artifact="$4" base="$5" context="$6"
  local key="${7:-}" terminal="${8:-}" head_sha="${9:-}" merge_base="${10:-}" digest="${11:-}"
```

Build the range fragment once, before the two `_kernel command` calls, and splice it into both payloads immediately after `\"context_bundle_hash\":\"$context\",`:

```bash
  # What was reviewed (spec §4): included only when the derivation pinned a
  # head. Values are shas and a hex digest, so they need no JSON escaping.
  local range=""
  [ -n "$head_sha" ] && range="\"head_sha\":\"$head_sha\",\"merge_base_sha\":\"$merge_base\",\"delta_digest\":\"$digest\","
```

so each payload reads `...\"context_bundle_hash\":\"$context\",${range}\"policy_version\":$_KERNEL_POLICY_VERSION}`.

- [ ] **Step 3: Widen the runner.** In `_derived_width_ok` change `[ "$n" = 8 ]` to `[ "$n" = 10 ]` and its comment from eight to ten. In `run_item` change the read to:

```bash
    IFS='|' read -r outcome review note observed_head _obs_ci ci_first resubmissions _settled_pr _merge_base _delta_digest <<EOF
$obs
EOF
```

declare `_merge_base` and `_delta_digest` with the function's other `local` names (one `local` per name, as the file's own test requires), reset them at the top of each loop round where the others are reset, and change both fallback tuples to ten fields:

```bash
      obs="escalated|na|outcome derivation failed (no tuple); needs a human||na|unknown||||"
      obs="escalated|na|derivation returned a malformed tuple; needs a human||na|unknown||||"
```

Change the review record call to:

```bash
      _kernel_record_review "$BIRCHER_RUN_ID" "$BIRCHER_GENERATION" "$review" \
        "$_out_hash" "$_base_sha" "$_ctx_hash" "$_rev_key" "$_rev_terminal" \
        "$observed_head" "$_merge_base" "$_delta_digest"
```

Search the file for every other `_derived_width_ok` caller and every other parser of the derive tuple (`recover_pr_cmd` parses it too, ~2416-2440): widen each `read -r` to the same ten names and each fallback to ten fields.

Run: `grep -n "IFS='|' read -r outcome" batch/run-queue.sh` and confirm every hit lists ten names.

- [ ] **Step 4: Run the tests and the self-test**

Run: `cd v2 && python -m pytest -q tests/execution -x` and `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; echo rc=$?; grep -c FAIL /tmp/selftest.log`
Expected: pytest all PASS; self-test rc=0 and `0` FAIL lines. The self-test's fake derivations emit tuples in its fixtures (search the self-test for `|green|` and `|unknown||`); any fixture tuple that is still eight fields must be widened to ten by appending `||`.

- [ ] **Step 5: Commit**

```bash
git add batch/lib/kernel-client.sh batch/run-queue.sh v2/tests/execution
git commit -m "The runner records the reviewed range on the verdict"
```

---

### Task 8: The bash status post becomes an idempotent fallback that keeps the description

**Files:**
- Modify: `batch/run-queue.sh` — `_post_cross_review_status` (~1173-1227), `_restamp_if_delta_unchanged` (~1339)
- Modify: the self-test's fake `gh` shims that answer `commits/*/status` (~7448, ~7595, ~7830) if they do not already return the stored state
- Modify: `docs/design/ARCHITECTURE.md` — the paragraph describing the cross-review status

**Interfaces:**
- Produces: `_post_cross_review_status <item> <pr> [sha] [state] [description]`; with `state` defaulting to `success` and `description` to the legacy text. A `success` already present on the sha returns 0 without posting.

- [ ] **Step 1: Make the self-test fail first.** Add to the self-test, next to the `merge_ready_pr OK (incl. #10 cross-review status)` block, a case where the fake `gh` answers `commits/<sha>/status` with a `success` for the context before any POST, and assert no POST happens:

```bash
  # Gate integrity: a success already on the head is not re-posted. The
  # derivation posts on every verdict; the merge path's own post is a fallback.
  : > "$slog"; STATUS_PRESENT=1 _post_cross_review_status demo 7 headsha >/dev/null 2>&1
  grep -q 'statuses/headsha' "$slog" \
    && { echo "FAIL _post_cross_review_status: re-posted a success that was already on the head"; exit 1; }
  echo "_post_cross_review_status skips a present success OK"
```

and teach that block's fake `gh` to answer the read with `success` when `STATUS_PRESENT=1` (mirror the shape of the existing shim at ~7448: `printf '%s\n' "$@" | grep -q 'commits/.*/status' && [ "${STATUS_PRESENT:-0}" = 1 ] && { printf 'success\n'; exit 0; }`).

Run: `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; grep -n "re-posted" /tmp/selftest.log`
Expected: the new FAIL line appears.

- [ ] **Step 2: Rewrite the function's head.** Replace the signature line and add the short-circuit and the two optional parameters:

```bash
_post_cross_review_status() {  # <item> <pr> [sha] [state] [description]
  local item="$1" pr="$2" sha="${3:-}" state="${4:-success}" description="${5:-cross-vendor review PASS (Bircher)}" attempt err
```

After the `[ -n "$sha" ] || { ... return 1; }` guard, insert:

```bash
  # ALREADY THERE -> nothing to do. The derivation posts a status for every
  # verdict (spec §4), so by the time the merge path gets here a success
  # usually exists with a description naming the reviewed range; re-posting
  # would overwrite that description with the legacy text.
  if [ "$state" = success ] && [ "$(_net_run "$(_cap_to "$BIRCHER_PREMERGE_TIMEOUT" "${PREMERGE_DEADLINE_AT:-}")" \
       gh api "repos/$REPO/commits/$sha/status" \
       -q '.statuses[] | select(.context=="bircher/cross-review") | .state' 2>/dev/null \
       | grep -cx 'success')" != 0 ]; then
    echo "[batch:merge] $item: bircher/cross-review=success already on ${sha:0:7} -> not re-posting" >&2
    return 0
  fi
```

In the POST, replace `-f state=success` with `-f state=$state` and `-f description="cross-vendor review PASS (Bircher)"` with `-f description="$description"`; in the verification `grep -cx 'success'` becomes `grep -cx "$state"`; in the two log lines replace the literal `success` with `$state`.

- [ ] **Step 3: The restamp keeps the reviewed description.** In `_restamp_if_delta_unchanged`, before `_post_cross_review_status "$item" "$pr" "$new" || return 1`, read the description off the reviewed head and carry it:

```bash
  local prior_desc
  prior_desc=$(gh api "repos/$REPO/commits/$reviewed/status" \
      -q '.statuses[] | select(.context=="bircher/cross-review") | .description' 2>/dev/null | head -1)
  _post_cross_review_status "$item" "$pr" "$new" success \
      "${prior_desc:-cross-vendor review PASS (Bircher)} (re-stamped on ${new:0:7}, delta unchanged)" || return 1
```

- [ ] **Step 4: Run the self-test**

Run: `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; echo rc=$?; grep -n "FAIL\|skips a present success" /tmp/selftest.log`
Expected: rc=0, the new OK line, no FAIL. The `sweep-behind-same` fixture must still see a post on the new head (its fake `gh` stores nothing for the new sha, so the short-circuit does not fire).

- [ ] **Step 5: Document.** In `docs/design/ARCHITECTURE.md`, find the sentence describing `bircher/cross-review` and replace it with: "`bircher/cross-review` is posted by the derivation for every verdict on the reviewed head: `success` for PASS, `failure` for FAIL with the blocking count, `error` when no verdict was produced, with `<vendor> round <n> ... on <merge-base7>..<head7>` as the description; the merge path re-posts only when no success is present, and a re-stamp after a content-identical update-branch keeps the reviewed description."

- [ ] **Step 6: Commit**

```bash
git add batch/run-queue.sh docs/design/ARCHITECTURE.md
git commit -m "The runner's status post is a fallback that keeps the reviewed description"
```

---

### Task 9: Whole-suite gates, the PR, and #101's retirement

**Files:**
- No code. Verification, the PR body file, and closing PR #101.

- [ ] **Step 1: Run everything**

Run: `cd v2 && python -m pytest -q` then `bash batch/run-queue.sh --self-test > /tmp/selftest.log 2>&1; echo rc=$?; tail -2 /tmp/selftest.log`
Expected: pytest all PASS; self-test rc=0.

- [ ] **Step 2: Prove the two copies are gone**

Run: `grep -rn "_recovery_review_prompt\|_pr_delta_digest\|byte_identical_to_the_bash" batch v2 docs/design | grep -v "docs/superpowers"`
Expected: no output.

- [ ] **Step 3: Write the PR body to a file** at `$TMPDIR/gate-integrity-pr.md` (never interpolate it on the command line), no attribution lines:

```markdown
## What

Gate integrity, the first of the five PRs in `docs/superpowers/specs/2026-09-19-back-half-closed-loop-design.md` (§4).

- One reviewer prompt, in the coordinator, naming the range `origin/<base>...<head>`; the bash copy and the byte-identity test are gone.
- The derivation posts `bircher/cross-review` for every verdict: `success` / `failure` (with the blocking count) / `error`, described as `<vendor> round <n> <word> on <merge-base7>..<head7>`, through the journalled effect path.
- The verdict fact records `head_sha`, `merge_base_sha` and `delta_digest` (schema version 2); the runner passes them through a ten-field derivation tuple.
- The delta digest has one implementation, in Python, used by the derivation and by the runner's re-stamp.
- The runner's own status post is now an idempotent fallback that keeps the reviewed description.

## Why

On muesli PR #769 a cross-review of one commit posted a green gate on a 38-file PR, and a later FAIL posted nothing, leaving that green standing. The record is `docs/design/front-half-live-log.md`. Supersedes #101.

## Verification

- `cd v2 && python -m pytest -q`: all green.
- `bash batch/run-queue.sh --self-test`: rc 0 as a non-root user (the runner's root-only self-test failure is pre-existing and unrelated).
- Acceptance lines from the spec satisfied here: every recorded verdict has a status whose state matches the verdict and whose description names the range; exactly one reviewer prompt exists and it names the range.
```

- [ ] **Step 4: Push and open the PR; close #101 as superseded**

```bash
git push -u origin spec/back-half-closed-loop
gh pr create --repo abedegno/bircher --base main --head spec/back-half-closed-loop \
  --title "Gate integrity: one prompt, a status for every verdict, the range on the fact" \
  --body-file "$TMPDIR/gate-integrity-pr.md"
gh pr close 101 --repo abedegno/bircher --comment "Superseded by the gate-integrity PR, which carries the range sentence in the coordinator's prompt (the only copy now)."
```

- [ ] **Step 5: Cross-review.** Run the codex plugin review over the branch diff before asking for a merge; fix blocking findings on the branch and re-run until clear.

---

## Self-review against the spec (§4)

- *One reviewer prompt naming the range, base from the PR:* Task 1 (prompt, `base` parameter, `base_of` from `gh`), Task 5 (wired).
- *Every verdict posts a status; FAIL cannot leave an older success standing:* Task 4 (`_post_status` on PASS/FAIL/none), Task 8 (fallback does not overwrite).
- *Description names vendor, round, verdict, range:* Task 2 (`describe`), Task 5 (`round_number` from the journal).
- *The verdict fact records merge-base, head, digest; re-stamp stays provable by digest:* Tasks 3, 6, 7, 8.
- *Posting is a journalled effect:* Task 4 uses `d.effect("status_check", ...)`, which is `perform_effect` in live wiring; Task 8's bash path uses `_effect`.
- *#101 reworked, not merged as it stands:* Task 9.
- *Muesli `review-gate` unchanged:* no task touches it.
- Not in this PR by design: the container toolchain (spec's PR 5).

Placeholder scan: every code step shows its code; the two "copy the existing helper" instructions in Tasks 6 and 7 name the exact test and helper to copy from. Type consistency: `Deps.base_of/merge_base/delta_digest/round_number` (Task 4) match `live_deps` (Task 5); `Derived.merge_base/delta_digest` and `FIELDS == 10` (Task 4) match the shell's ten names (Task 7); `_kernel_record_review`'s positional order (Task 7) matches its call site; `attest.CONTEXT/state_for/describe/blocking_count` (Task 2) are the names Task 4 imports.
