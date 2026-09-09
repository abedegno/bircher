"""What each effect class may run, and to what.

Round 6 found that the kernel accepted a class and an argv from the same
caller and never compared them. Round 7 found the first repair constrained
only the VERB: the signature stops at the first flag, so everything after it
was unexamined. All of these passed:

    ref_update      git push origin :main                 (deletes the branch)
    ref_update      git push --force origin HEAD:main
    ref_update      gh api repos/o/r/git/refs/heads/main -X DELETE \\
                        -f note=update-branch             (the marker smuggled
                                                           into a field, because
                                                           the URL test searched
                                                           the whole argv)
    status_check    gh api repos/o/r/statuses/abc -X DELETE
    comment         gh pr comment 7 --body-file /etc/passwd

Same shape as everything else in this programme: a real check, correctly
implemented, constraining the part the caller does not care about.

So a rule now constrains four things -- the verb, the flags, the HTTP method,
and the operands -- and all four are ALLOWLISTS. The flag sets were taken from
what the 15 routed call sites actually use, so widening one is a deliberate,
reviewable act rather than a consequence of nobody having thought of a flag.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from kernel.effect_class import EffectClass

_METHOD_FLAGS = ("-X", "--method")


@dataclass(frozen=True)
class Parsed:
    """One reading of an argv. Every consumer of a flag's value reads it here."""
    flags: frozenset[str]
    methods: frozenset[str]
    operands: tuple[str, ...]
    values: dict[str, list[str]]
    joined: frozenset[str]


def parse(argv: list[str], valued: frozenset[str]) -> Parsed:
    flags: set[str] = set()
    methods: set[str] = set()
    operands: list[str] = []
    values: dict[str, list[str]] = {}
    joined: set[str] = set()
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            operands.append(tok)
            i += 1
            continue
        flag, sep, inline = tok.partition("=")
        flags.add(flag)
        if sep:
            joined.add(flag)
            if flag in valued:
                values.setdefault(flag, []).append(inline)
            if flag in _METHOD_FLAGS:
                methods.add(inline)
            i += 1
            continue
        # A method flag ALWAYS takes the next token as its value, whether or
        # not the rule also lists it in `valued`: `-X`/`--method` name the
        # verb, and a rule that named `--method` in `methods` but forgot it
        # in `valued` must not leak the verb into `operands` -- the same
        # defect the `--max-time 120` finding (module docstring) already
        # fixed for ordinary valued flags.
        if (flag in valued or flag in _METHOD_FLAGS) and i + 1 < len(argv):
            if flag in valued:
                values.setdefault(flag, []).append(argv[i + 1])
            if flag in _METHOD_FLAGS:
                methods.add(argv[i + 1])
            i += 2
            continue
        i += 1
    return Parsed(frozenset(flags), frozenset(methods), tuple(operands), values,
                  frozenset(joined))


def _flags_and_operands(argv: list[str], valued: frozenset = frozenset()):
    """Split argv into (flags, method-values, operands).

    `--flag=value` is normalised to its flag, or an attacker writes
    `--repo=x` and the flag never appears in the flag list at all.
    """
    p = parse(argv, valued)
    return p.flags, p.methods, list(p.operands)


_flags_and_operands.__wrapped_by__ = "parse"


@dataclass(frozen=True)
class Rule:
    """One permitted command shape for a class."""

    sig: str
    #: For `gh api` / `curl`: a regex the URL's PATH must match, after the
    #: query and fragment are stripped.
    #:
    #: This was a substring test against the URL operand, and round 7 showed a
    #: substring is not an endpoint: `repos/o/r/issues/1/comments?marker=/statuses/`
    #: satisfied the status rule and posted an issue comment, and
    #: `repos/o/r/contents/x.txt?marker=update-branch` satisfied the ref rule
    #: and wrote a repository file. The marker lived in the QUERY. The path is
    #: the endpoint; everything after `?` is data.
    url_path: str | None = None
    flags: frozenset = frozenset()
    #: Flags whose NEXT token is a value, not an operand. Without this,
    #: `--max-time 120` made `120` the URL operand and every session_control
    #: call failed its own contract -- and, worse, a real operand could hide
    #: behind a flag that takes one.
    valued: frozenset = frozenset()
    methods: frozenset = frozenset()
    #: Operand prefixes refused outright. On a git refspec `:` is a DELETION
    #: and `+` is a FORCED update -- git's second spelling for --force, which
    #: is an operand, so no flag allowlist can see it. Demonstrated in round 7
    #: against a real repository: `git push origin +HEAD:main` rewrote main
    #: while the plain push was rejected as non-fast-forward.
    forbid_operand_prefix: tuple = ()
    #: Exact number of operands permitted, when the shape is fixed. `git push`
    #: takes a remote and ONE refspec: `origin HEAD:main HEAD:other` updated
    #: two branches under a single journalled effect.
    operand_count: int | None = None
    #: URL schemes permitted when the operand carries one. `file://` is not a
    #: session-control transfer.
    schemes: frozenset = frozenset({"http", "https"})
    #: A rule's own name, so `check` can return WHICH shape matched rather
    #: than only that one did. SESSION_CONTROL has three; nothing else needs
    #: to distinguish among a class's rules yet, so this defaults to empty.
    name: str = ""
    #: At least one of these flags must appear. `_CURL_FAIL` uses this: some
    #: failing-curl mode is required so a routed call cannot silently exit 0
    #: on a 4xx/5xx and be journalled as confirmed.
    required_any: frozenset[str] = frozenset()
    #: Every one of these flags must appear.
    required: frozenset[str] = frozenset()
    #: Each of these flags, if present, must appear EXACTLY once, as its own
    #: token -- never `flag=value`, never given twice. `-d` on sess-create and
    #: sess-events is the case: two bodies or a joined form both read as an
    #: attempt to smuggle a second reading of the request past the rule that
    #: names one.
    once: frozenset[str] = frozenset()
    #: (flag, prefix) pairs whose value may not start with that prefix. `-d`
    #: may not start with `@`: curl reads `@path` as a FILE, which is an
    #: arbitrary local read baked into a request body, not a body.
    forbid_value_prefix: tuple[tuple[str, str], ...] = ()
    #: (flag, values) pairs: the flag may not carry any of those values. The
    #: filing step creates children with `bircher:slice` and the inherited
    #: policy labels; `bircher:queued`, `bircher:running` and `bircher:sliced`
    #: are the runner's and the filing step's to set LATER (shaping spec §2),
    #: and a created issue carrying `queued` would be runnable before its
    #: links exist.
    forbid_value: tuple[tuple[str, frozenset], ...] = ()


_GH_COMMON = frozenset({"--repo"})

# `-w` is GONE from every session_control rule below. Its value supports
# `%output{path}`, which redirects write-out to a local file -- an arbitrary
# filesystem write from a class that exists to control a session. No routed
# site uses it.
#
# `_CURL_FAIL` is what a "some failing-curl mode" requirement checks: `-s`
# alone makes curl print the error body and still exit 0, so a routed effect
# that gets a 4xx/5xx back is journalled `confirmed` on the strength of an
# error page. `-f`/`-sf`/`-sSf` all turn that into a real nonzero exit.
_CURL_FAIL = frozenset({"-f", "-sf", "-sSf"})
# -q is permitted but not required: the LAUNCHER injects it as curl's first
# argument, which is the only position that suppresses .curlrc. Allowed so an
# explicit one is not refused.
_CURL_FLAGS = frozenset({"-s", "-S", "-sf", "-sSf", "-f", "-q", "-X", "-H", "--max-time"})
_CURL_VALUED = frozenset({"-X", "-H", "--max-time"})

_SESSION_RULES = [
    # Create: one -d body, JSON object, never a file. The executor validates
    # the response against this body (kernel/cli.py::validate_create_response).
    Rule("curl", name="sess-create", url_path=r"^/v1/sessions$", operand_count=1,
         flags=_CURL_FLAGS | {"-d"}, valued=_CURL_VALUED | {"-d"}, methods=frozenset({"POST"}),
         required_any=_CURL_FAIL, required=frozenset({"-d"}), once=frozenset({"-d"}),
         forbid_value_prefix=(("-d", "@"),)),
    # Events: the coordinator sends no -d (the kernel composes the body and
    # appends --data-binary after this check); the runner's template sites
    # (_send_prompt, _stop_session) keep their single inline -d. Ruling 1.
    Rule("curl", name="sess-events", url_path=r"^/v1/sessions/[^/]+/events$", operand_count=1,
         flags=_CURL_FLAGS | {"-d"}, valued=_CURL_VALUED | {"-d"}, methods=frozenset({"POST"}),
         required_any=_CURL_FAIL, once=frozenset({"-d"}),
         forbid_value_prefix=(("-d", "@"),)),
    Rule("curl", name="sess-delete", url_path=r"^/v1/sessions/[^/]+$", operand_count=1,
         flags=_CURL_FLAGS, valued=_CURL_VALUED, methods=frozenset({"DELETE"}),
         required_any=_CURL_FAIL),
]

CONTRACTS: dict[str, list[Rule]] = {
    EffectClass.MERGE: [
        Rule("gh pr merge",
             flags=_GH_COMMON | {"--squash", "--delete-branch",
                                 "--match-head-commit"},
             valued=frozenset({"--repo", "--match-head-commit"})),
    ],
    EffectClass.PULL_REQUEST: [
        Rule("gh pr close", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
        Rule("gh pr create", flags=_GH_COMMON | {"--title", "--body", "--base",
                                                 "--head"},
             valued=frozenset({"--repo", "--title", "--body", "--base", "--head"})),
        Rule("gh pr reopen", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
    ],
    EffectClass.REF_UPDATE: [
        # No --force, no --delete, no deletion refspec. A ref update advances a
        # branch; it does not remove or rewrite one.
        Rule("git push", flags=frozenset({"-q", "--quiet"}),
             forbid_operand_prefix=(":", "+"), operand_count=2),
        Rule("gh api", url_path=r"/update-branch$", flags=frozenset({"-X", "-f", "-q"}),
             valued=frozenset({"-X", "-f", "-q"}), methods=frozenset({"PUT"})),
    ],
    EffectClass.STATUS_CHECK: [
        Rule("gh api", url_path=r"/statuses/", flags=frozenset({"-X", "-f"}),
             valued=frozenset({"-X", "-f"}), methods=frozenset({"POST"})),
    ],
    EffectClass.COMMENT: [
        Rule("gh pr comment", flags=_GH_COMMON | {"--body"},
             valued=frozenset({"--repo", "--body"})),
        Rule("gh issue comment", flags=_GH_COMMON | {"--body"},
             valued=frozenset({"--repo", "--body"})),
    ],
    EffectClass.ISSUE_OR_LABEL: [
        Rule("gh issue edit",
             flags=_GH_COMMON | {"--add-label", "--remove-label"},
             valued=frozenset({"--repo", "--add-label", "--remove-label"})),
        Rule("gh issue close", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
        Rule("gh issue reopen", flags=_GH_COMMON | {"--comment"},
             valued=frozenset({"--repo", "--comment"})),
        # The write half of the endpoint the queue generator reads
        # (`is_unblocked`, issues-to-queue.sh): a blocked-by link between two
        # issues (shaping spec §2).
        Rule("gh api", url_path=r"/issues/\d+/dependencies/blocked_by$", flags=frozenset({"-X", "-f"}),
             valued=frozenset({"-X", "-f"}), methods=frozenset({"POST"})),
    ],
    # The child issue (shaping spec §2 *The effect class*). No --body-file:
    # the executor appends it after this check, from the artefact the intent's
    # body names (planning ruling 1). The three runner labels are refused as
    # values, so no create can queue a child before its links exist.
    EffectClass.ISSUE_CREATE: [
        Rule("gh issue create", flags=_GH_COMMON | {"--title", "--label"},
             valued=frozenset({"--repo", "--title", "--label"}),
             required=frozenset({"--repo", "--title"}),
             forbid_value=(("--label", frozenset({"bircher:queued", "bircher:running", "bircher:sliced"})),)),
    ],
    EffectClass.REVERT_OR_RECOVERY: [
        Rule("git revert", flags=frozenset({"-n", "--no-edit", "-m"}),
             valued=frozenset({"-m"})),
        Rule("git push", flags=frozenset({"-q", "--quiet"}),
             forbid_operand_prefix=(":", "+"), operand_count=2),
    ],
    EffectClass.SESSION_CONTROL: _SESSION_RULES,
    # DELIBERATELY EMPTY. `Rule("gh auth", ...)` admitted every unflagged
    # `gh auth` subcommand, including `gh auth token` -- which PRINTS the
    # token. `cli.py`'s executor captures stdout as the external object id and
    # main() prints it back to the caller, so an authorized
    # credential_lifecycle request could extract the kernel's GitHub
    # credential through the very adapter built to keep it away from models.
    #
    # No routed call site performs a credential effect. The class keeps its
    # entry so `check` can say this was decided rather than forgotten, and so
    # adding a shape here is a deliberate act.
    EffectClass.CREDENTIAL_LIFECYCLE: [],
}


class ContractViolation(Exception):
    """The argv does not do what its class says."""


def signature(argv: list[str]) -> str:
    """The command's shape: leading non-flag tokens, operands excluded."""
    out = []
    for tok in argv[:3]:
        if tok.startswith("-") or not tok:
            break
        if out and (tok[:1].isdigit() or "/" in tok or ":" in tok):
            break
        out.append(tok)
    return " ".join(out)


def _rule_parts(argv: list[str], rule) -> list[str]:
    """argv with the rule's own command words removed, ready for `parse`.

    The command words are not operands: leaving `curl` and `git push` in the
    list made every `operand_count` off by the length of the signature.
    """
    lead = len(rule.sig.split())
    return argv[lead:]


def check(effect_class: str, argv: list[str]) -> Rule:
    """Refuse an argv inconsistent with its declared class; return the rule
    that matched, so a caller can act on WHICH shape was authorized."""
    allowed = CONTRACTS.get(effect_class)
    if allowed is None:
        raise ContractViolation(
            f"effect class {effect_class!r} declares no argv contract, so "
            "nothing constrains what it may run"
        )
    if not allowed:
        raise ContractViolation(
            f"effect class {effect_class!r} has an empty contract: no command "
            "shape is permitted for it"
        )
    sig = signature(argv)
    reasons = []
    for rule in allowed:
        if not (sig == rule.sig or sig.startswith(rule.sig + " ")):
            continue
        parts = _rule_parts(argv, rule)
        p = parse(parts, rule.valued)
        if rule.url_path is not None:
            target = p.operands[0] if p.operands else None
            if not target:
                reasons.append(f"{rule.sig}: no url operand")
                continue
            scheme = target.split("://", 1)[0].lower() if "://" in target else None
            if scheme is not None and scheme not in rule.schemes:
                reasons.append(f"{rule.sig}: scheme {scheme!r} not permitted")
                continue
            # The authority (host[:port]) is transport, not endpoint. A `gh
            # api` operand carries no scheme at all (`repos/o/r/...`), so the
            # whole operand already IS the path; a `curl` operand is a full
            # URL, and ANCHORING url_path (this task) only means something
            # once the host it was never anchored against is gone -- the old
            # unanchored `/v1/sessions` matched `http://srv/v1/sessions` as a
            # substring, which a `^...$` pattern cannot.
            if scheme is not None:
                after_scheme = target.split("://", 1)[1]
                _, _, after_authority = after_scheme.partition("/")
                target = "/" + after_authority
            # Query and fragment are DATA. The path is the endpoint.
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not re.search(rule.url_path, path):
                reasons.append(
                    f"{rule.sig}: url path {path!r} does not match {rule.url_path!r}")
                continue
        bad = [f for f in p.flags if f not in rule.flags]
        if bad:
            reasons.append(f"{rule.sig}: flags not permitted: {bad}")
            continue
        # A rule that names methods REQUIRES one. Checking only the values it
        # happened to collect made the constraint vacuous when no method flag
        # was given at all -- and `gh api` switches from GET to POST on its own
        # when a field is present, so an omitted `-X` reached a PUT-only rule
        # as a POST.
        if rule.methods:
            if len(p.methods) != 1:
                reasons.append(
                    f"{rule.sig}: exactly one explicit method required, got "
                    f"{sorted(p.methods)}")
                continue
            method = next(iter(p.methods))
            if method.upper() not in rule.methods:
                reasons.append(f"{rule.sig}: method not permitted: {method}")
                continue
        if rule.operand_count is not None and len(p.operands) != rule.operand_count:
            reasons.append(
                f"{rule.sig}: expected {rule.operand_count} operand(s), got "
                f"{list(p.operands)}")
            continue
        bad_o = [o for o in p.operands
                 if o.startswith(rule.forbid_operand_prefix)] \
            if rule.forbid_operand_prefix else []
        if bad_o:
            reasons.append(f"{rule.sig}: refused operand: {bad_o}")
            continue
        if rule.required_any and not (p.flags & rule.required_any):
            reasons.append(f"{rule.name}: needs one of {sorted(rule.required_any)}")
            continue
        missing = rule.required - p.flags
        if missing:
            reasons.append(f"{rule.name}: missing {sorted(missing)}")
            continue
        bad_once = [f for f in rule.once
                    if f in p.flags and (len(p.values.get(f, [])) != 1 or f in p.joined)]
        if bad_once:
            reasons.append(f"{rule.name}: {bad_once} must appear exactly once as its own token")
            continue
        bad_prefix = [f for f, pre in rule.forbid_value_prefix
                      if any(v.startswith(pre) for v in p.values.get(f, []))]
        if bad_prefix:
            reasons.append(f"{rule.name}: {bad_prefix} value may not start with a file/prefix")
            continue
        bad_value = [f for f, vals in rule.forbid_value
                     if set(p.values.get(f, [])) & set(vals)]
        if bad_value:
            reasons.append(f"{rule.sig}: {bad_value} carry a value the rule refuses")
            continue
        return rule
    raise ContractViolation(
        f"argv does not match the contract for {effect_class!r}: signature "
        f"{sig!r}" + (f"; {'; '.join(reasons)}" if reasons else "")
    )


def create_body(argv: list[str]) -> dict:
    """The JSON body of a sess-create argv, from the single -d the rule admits."""
    p = parse(argv, _CURL_VALUED | {"-d"})
    vals = p.values.get("-d", [])
    if len(vals) != 1:
        raise ContractViolation(f"sess-create needs exactly one -d, got {len(vals)}")
    try:
        body = json.loads(vals[0])
    except ValueError as exc:
        raise ContractViolation(f"sess-create body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ContractViolation("sess-create body must be a JSON object")
    return body


_MERGE_VALUED = frozenset({"--repo", "--match-head-commit"})


def merge_target(argv: list[str]) -> tuple[str | None, str | None]:
    """The (pr, repo) a merge command would act on.

    Uses the operand parser rather than "the token after `merge`", because
    `gh` accepts flags before the positional: `gh pr merge --repo o/r 42` is
    valid and the old reading returned pr=None, refusing a legitimate merge.
    That failed closed, but the docstring claimed to model what the command
    would act on, and it did not.
    """
    _f, _m, operands = _flags_and_operands(argv[3:], _MERGE_VALUED)
    pr = operands[0] if operands else None
    repo = None
    for i, tok in enumerate(argv):
        if tok == "--repo" and i + 1 < len(argv):
            repo = argv[i + 1]
        elif tok.startswith("--repo="):
            repo = tok.split("=", 1)[1]
    return pr, repo
