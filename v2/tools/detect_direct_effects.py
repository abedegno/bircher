"""Find externally visible mutations that bypass the `_effect` adapter.

Read what this throws away before you read what it does: every exclusion is a
place the detector can go silently blind, and a detector that certifies
everything prints exactly what a working one prints.

The design decision that matters: **a mutation-shaped string inside a quoted
literal is data, not a call.** `run-queue.sh` holds six of them -- an
assignment value, two string comparisons and three selftest messages -- and a
line-oriented regex flags all six. The fix is not to widen an exclusion until
they disappear; it is to parse enough shell to tell a command from a string,
and then to REPORT what was suppressed rather than drop it. A suppression
nobody wrote down is a suppression nobody re-reads.

The quote test is on **where the match STARTS**, not on the stripped text.
Blanking quoted regions and then matching would miss `gh pr "merge" "$pr"` --
a real call whose verb happens to be quoted -- because the verb would be gone
before the pattern ran. Asking whether the match begins outside a quote keeps
that call visible while still suppressing a whole mutation embedded in a
string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MUTATION = re.compile(r"""
    gh\s+["']?(pr|issue)["']?\s+["']?(merge|close|reopen|comment|edit|create|review)
  | gh\s+["']?api["']?\b[^|\n]*-X\s+["']?(POST|PUT|PATCH|DELETE)
  | gh\s+["']?api["']?\b[^|\n]*\bstatuses/
  | git\s+(-C\s+\S+\s+)?["']?push
  | curl\b[^|\n]*-X\s+["']?(POST|PUT|PATCH|DELETE)
  | _http_json\s+["']?(POST|PUT|PATCH|DELETE)
""", re.VERBOSE)
#: `_http_json <METHOD> <path>` -- an INDIRECT mutation, and the whole class of
#: them was invisible until 2026-08-29. The helper does
#: `curl -sf -X "$method"`, and every pattern above requires a LITERAL method
#: after `-X`, so a mutation went undetected the moment it was routed through a
#: wrapper. Two live ones were hiding there since the initial public release:
#: creating a model session and stopping one -- both `session_control`, neither
#: journalled, neither generation-fenced.
#:
#: The suite was green throughout, and could not have been otherwise:
#: `test_criterion_1_every_mutation_is_routed_or_dispositioned` asserts every
#: FINDING is accounted for, so a detector that finds nothing satisfies it
#: perfectly. A blind spot is invisible to a test that can only see through the
#: thing that is blind.
#:
#: Naming `_http_json` fixes the instance. What closes the CLASS is
#: `test_no_other_helper_hides_a_variable_method_curl`, which fails if a second
#: such wrapper is ever added.
#: `curl` with a mutating method. Added in round 6: the pattern knew only `gh`
#: and `git push`, and the inventory claimed to union "five independent grep
#: patterns" -- all five of which looked for those same two commands, so the
#: union of five patterns was one pattern. Three live session_control
#: mutations were invisible to every one of them.
#: `statuses/` with the slash: the bare word also occurs in the jq filter
#: `.statuses[] | select(...)` used to VERIFY a status, which is a GET. That
#: match was being suppressed only because it happened to fall inside quotes --
#: correct by accident, and it would have flagged the moment the filter was
#: rewritten without them. The endpoint always has a SHA after it.
#:
#: Quotes are optional around every word. `gh pr "merge" "$pr"` is a real call
#: whose verb happens to be quoted; a pattern requiring the verb adjacent to
#: whitespace does not match it AT ALL, so no amount of quote-position analysis
#: would have found it. The quote-START test below is what keeps this from
#: re-flagging whole mutations embedded in strings.

#: A call already routed through the adapter. Anchored to the call POSITION --
#: line start, a pipeline/list operator, a compound-command keyword, or `$(` --
#: so a line that merely MENTIONS `_effect` cannot launder a real mutation.
#: `if`/`while`/`until`/`!` are command positions too: `if _effect ... ; then`
#: is how a routed call gets its exit status tested.
ROUTED = re.compile(
    r"(^|\|\||&&|;|\{|\(|!|\b(then|else|do|if|elif|while|until)\b)\s*_effect\s"
)

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

#: A heredoc fed to one of these is EXECUTED, so its body is code.
_INTERPRETER = re.compile(r"\b(bash|sh|zsh|ssh|python3?|perl|ruby|node)\b")


@dataclass(frozen=True)
class Finding:
    line: int
    text: str


@dataclass(frozen=True)
class Suppressed:
    line: int
    text: str
    reason: str


def quote_mask(line: str, in_quote: str | None) -> tuple[list[bool], str | None]:
    """Per-character "is inside a quoted literal", plus the trailing state.

    Quote state carries ACROSS lines: a string opened on one line and closed on
    the next would otherwise leave its continuation looking like bare command
    text. Backslash escapes are honoured inside double quotes only, matching
    the shell -- inside single quotes a backslash is literal.
    """
    mask, i, q = [], 0, in_quote
    while i < len(line):
        c = line[i]
        if q is None:
            if c in "'\"":
                q = c
                mask.append(True)          # the opening quote itself
            else:
                mask.append(False)
        else:
            if q == '"' and c == "\\" and i + 1 < len(line):
                mask.extend([True, True])
                i += 2
                continue
            # A command substitution inside DOUBLE quotes is executed, so its
            # contents are code. `captured="$(gh pr merge 401)"` is a real
            # merge that the quote test otherwise suppressed as data. Single
            # quotes do not interpolate, so `'$(...)'` stays data.
            if q == '"' and c == "$" and line[i + 1:i + 2] == "(":
                depth, j = 1, i + 2
                mask.extend([True, True])
                while j < len(line) and depth:
                    if line[j] == "(":
                        depth += 1
                    elif line[j] == ")":
                        depth -= 1
                        if depth == 0:
                            mask.append(True)
                            j += 1
                            break
                    mask.append(False)
                    j += 1
                i = j
                continue
            mask.append(True)
            if c == q:
                q = None
        i += 1
    return mask, q


def logical_lines(path: str):
    """Yield (first_physical_line, joined_text), joining `\\` continuations.

    A line-oriented scan reads a continued command as several unrelated lines,
    so `_effect ... \\` on one line and `gh pr merge ...` on the next looks
    like an unrouted mutation -- and, worse, the reverse hides a real one
    behind an unrelated routed call above it. Both of the coordinator's
    authority-bearing sites are written across continuations, so this is not a
    hypothetical shape.
    """
    buf, start = None, None
    for n, raw in enumerate(open(path), 1):
        line = raw.rstrip("\n")
        if buf is None:
            start = n
            buf = line
        else:
            buf = buf[:-1] + " " + line.lstrip()
        if buf.endswith("\\"):
            continue
        yield start, buf
        buf = None
    if buf is not None:
        yield start, buf


def code_lines(path: str):
    """Yield (line_no, logical_line, quote_mask, is_comment) for real code.

    Heredoc bodies not fed to an interpreter are dropped; comments are yielded
    flagged but do NOT feed the quote state, because an apostrophe in prose
    ("don't") opens a string that never closes and every later line then reads
    as quoted.

    Extracted so the detector and anything else that has to answer "is this
    line code?" share one implementation. A second, subtly different copy in a
    test silently cut its own extraction from 13 call sites to 6.
    """
    quote: str | None = None
    heredoc: str | None = None
    for n, line in logical_lines(path):
        stripped = line.lstrip()
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            continue
        if quote is None and (m := _HEREDOC.search(line)):
            if not _INTERPRETER.search(line[:m.start()]):
                heredoc = m.group(2)
        if quote is None and stripped.startswith("#"):
            yield n, line, None, True
            continue
        mask, quote = quote_mask(line, quote)
        yield n, line, mask, False


_SEPARATOR = re.compile(r"(?:\|\||&&|[;|])")


def _segments(line: str, mask: list[bool]) -> list[tuple[str, int]]:
    """Split a logical line at UNQUOTED shell separators.

    Each segment is judged on its own, so a routed call exempts itself and not
    whatever follows it on the same line. Offsets are returned so the quote
    mask still applies to the original positions.
    """
    out, start, depth = [], 0, 0
    i = 0
    while i < len(line):
        # Separators INSIDE a command substitution belong to its own pipeline
        # and must not split the outer line -- otherwise
        # `_effect comment "k:$(printf %s "$b" | shasum)" gh pr comment ...`
        # splits at that pipe and the remainder loses its `_effect` prefix.
        if line.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if line[i] == ")" and depth:
            depth -= 1
            i += 1
            continue
        if depth == 0 and not mask[i]:
            m = _SEPARATOR.match(line, i)
            if m:
                out.append((line[start:i], start))
                start = m.end()
                i = m.end()
                continue
        i += 1
    out.append((line[start:], start))
    return out


def scan(path: str) -> tuple[list[Finding], list[Suppressed]]:
    """Return (unrouted mutations, matches suppressed with the reason)."""
    findings: list[Finding] = []
    suppressed: list[Suppressed] = []
    quote: str | None = None
    heredoc: str | None = None

    for n, line in logical_lines(path):
        stripped = line.lstrip()

        # EXCLUSION 1: heredoc bodies. Prompt text and usage messages are data.
        # NOT skipped when the heredoc feeds an interpreter -- `bash <<EOF`,
        # `sh`, `ssh`, `python` -- because that body IS executed, and skipping
        # it would be a hole shaped exactly like the boundary this detector
        # exists to prove. Planted positive covers it.
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            continue
        if quote is None and (m := _HEREDOC.search(line)):
            if not _INTERPRETER.search(line[:m.start()]):
                heredoc = m.group(2)

        # EXCLUSION 2: comments. Prose about `gh pr merge` is not a call.
        # Blinds the detector to: nothing -- a `#` at the start of a line is a
        # comment in every shell context outside a heredoc, handled above.
        if quote is None and stripped.startswith("#"):
            if MUTATION.search(line):
                suppressed.append(Suppressed(n, stripped[:110], "comment"))
            continue

        mask, quote = quote_mask(line, quote)

        # EXCLUSION 3: already routed. Anchored to the call position.
        #
        # It used to skip the WHOLE line, which exempted anything after the
        # routed call: `_effect comment k - true; gh pr merge 301` was
        # invisible. The comment here claimed a planted positive covered that
        # shape -- it did not; the positive covered `_effect` mentioned in a
        # comment, which is a different shape. Found by codex in round 6.
        #
        # Now the line is split at UNQUOTED separators and each segment judged
        # on its own, so a routed segment exempts only itself.
        segments = _segments(line, mask)

        # EXCLUSION 4: quoted string literals. THE substantive one.
        # Tested on where the match STARTS, so a call whose verb is quoted --
        # `gh pr "merge" "$pr"` -- is still found. Suppressions are reported
        # rather than dropped, so a NEW one fails the test rather than joining
        # a list nobody re-reads.
        unrouted_hit = False
        for seg, off in segments:
            if ROUTED.search(seg):
                continue
            if any(not mask[off + m.start()] for m in MUTATION.finditer(seg)):
                unrouted_hit = True
                break
        if unrouted_hit:
            findings.append(Finding(n, stripped[:110]))
        elif MUTATION.search(line) and not ROUTED.search(line):
            suppressed.append(Suppressed(n, stripped[:110], "inside a quoted string"))

    return findings, suppressed


def find_direct_effects(path: str) -> list[Finding]:
    return scan(path)[0]
