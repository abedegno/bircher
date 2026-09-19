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

# --- the shapes a live reviewer writes that the first cut counted wrong -----
# Every fixture below was MEASURED against the previous regexes and came back
# with the wrong number; the count beside each one in the parametrize list is
# what the text plainly says.

#: A Markdown heading. `#` ahead of the phrase was not allowed, so this
#: counted 0 -- a two-finding FAIL posted as `FAIL (0 blocking)`.
CODEX_HASH_HEADING = """## Blocking findings

- The relay never releases its socket on an early return.
- The cap is racy under concurrent creates.
"""

#: Heading markers AND bold together, as GPT-5 writes them. Counted 0.
CODEX_HASH_BOLD_HEADING = """### **Blocking findings**

- The lease is renewed after it has already expired.
"""

#: A closing heading that itself carries `#` markers. The closer was anchored
#: with the same lead as the opener but required a SHORT trailer, so `##
#: Non-blocking findings` did not close and its bullets were counted as
#: blocking -- 1 became 4.
CODEX_HASH_CLOSERS = """## Blocking findings

- The only real one.

## Non-blocking findings

- A naming nit.
- Another nit.

## Suggestions

- Consider a helper.
"""

#: A closing heading with a prose trailer. `Non-blocking findings -- none
#: worth naming` did not close either, so the bullet after it was blocking.
CODEX_PROSE_TRAILER_CLOSER = """Blocking findings

- The only real one.

Non-blocking findings -- none worth naming

- A nit that is not blocking.
"""

#: The section's own count. `(2)` was captured as the rest of the heading
#: line and counted as a third finding.
CODEX_PARENTHETICAL_COUNT = """Blocking findings (2)

- The first.
- The second.
"""

#: The phrase continuing as PROSE. With no boundary after it this opened a
#: section and counted the sentence itself, so a clean PASS reported
#: `FAIL (1 blocking)` when the verdict went the other way.
CODEX_PHRASE_CONTINUES = """Blocking findings were all resolved.
"""

#: A bullet that happens to contain a closing heading's word after a colon.
#: The closer must be anchored at the START of the line, or this finding
#: closes the section it belongs to and the one below it is lost.
CODEX_HEADING_WORD_IN_BULLET = """Blocking findings

- The lease is never released: Verification of the lease is missing too.
- A second, real finding.

Non-blocking findings: None.
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
    (CODEX_HASH_HEADING, 2),
    (CODEX_HASH_BOLD_HEADING, 1),
    (CODEX_HASH_CLOSERS, 1),
    (CODEX_PROSE_TRAILER_CLOSER, 1),
    (CODEX_PARENTHETICAL_COUNT, 2),
    (CODEX_PHRASE_CONTINUES, 0),
    (CODEX_HEADING_WORD_IN_BULLET, 2),
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
