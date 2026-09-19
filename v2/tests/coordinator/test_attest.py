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

CODEX_GLUED = """Local build, vet, and typecheck are green. I found a lifecycle defect that needs a close read.Blocking findings

1. Hosted deployments have no live-prompts transport.
2. Trashing an active note does not end its live work.
3. Required feature-level E2E coverage is absent.

Non-blocking findings

- None.

VERDICT: FAIL
"""

CODEX_WORDS_IN_BULLETS = """Blocking findings

- The final verdict logic is unreachable in prod.
- Auth check is skipped on the admin route; see the verification notes.
- Timer leak on unmount.

Non-blocking findings

- None.

VERDICT: FAIL
"""

CODEX_PHRASE_IN_PROSE = """We reviewed things and found no blocking findings issues really but lets see.

Blocking findings

- Real finding one.
- Real finding two.

Non-blocking findings

- None.

VERDICT: FAIL
"""

CODEX_NESTED = """Blocking findings

- Real finding one.
  - elaboration of one, not a second finding
- Real finding two.

Non-blocking findings: None.

VERDICT: FAIL
"""

CODEX_BOLD = """**Blocking findings**

- a
- b

**Non-blocking findings**

- None.

**VERDICT: FAIL**
"""


@pytest.mark.parametrize("text, expected", [
    (CODEX_FAIL, 2),
    (CODEX_NUMBERED, 3),
    (CODEX_PASS, 0),
    (CODEX_GLUED, 3),
    (CODEX_WORDS_IN_BULLETS, 3),
    (CODEX_PHRASE_IN_PROSE, 2),
    (CODEX_NESTED, 2),
    (CODEX_BOLD, 2),
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
