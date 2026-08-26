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

import re
from dataclasses import dataclass

from kernel.effects import EffectClass

_METHOD_FLAGS = ("-X", "--method")


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


_GH_COMMON = frozenset({"--repo"})

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
    ],
    EffectClass.REVERT_OR_RECOVERY: [
        Rule("git revert", flags=frozenset({"-n", "--no-edit", "-m"}),
             valued=frozenset({"-m"})),
        Rule("git push", flags=frozenset({"-q", "--quiet"}),
             forbid_operand_prefix=(":", "+"), operand_count=2),
    ],
    EffectClass.SESSION_CONTROL: [
        # `-w` is GONE. Its value supports `%output{path}`, which redirects
        # write-out to a local file -- an arbitrary filesystem write from a
        # class that exists to control a session. No routed site uses it.
        Rule("curl", url_path=r"/v1/sessions", operand_count=1,
             flags=frozenset({"-s", "-sf", "-f", "-X", "-H", "-d", "-F",
                              "--max-time"}),
             valued=frozenset({"-X", "-H", "-d", "-F", "--max-time"}),
             methods=frozenset({"POST", "DELETE"})),
    ],
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


def _flags_and_operands(argv: list[str], valued: frozenset = frozenset()):
    """Split argv into (flags, method-values, operands).

    `--flag=value` is normalised to its flag, or an attacker writes
    `--repo=x` and the flag never appears in the flag list at all.
    """
    flags, methods, operands = [], [], []
    pending = None
    for tok in argv:
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            flags.append(name)
            if "=" in tok:
                if name in _METHOD_FLAGS:
                    methods.append(tok.split("=", 1)[1])
            elif name in _METHOD_FLAGS or name in valued:
                pending = name
            continue
        if pending is not None:
            if pending in _METHOD_FLAGS:
                methods.append(tok)
            pending = None
            continue
        operands.append(tok)
    return flags, methods, operands


def _rule_parts(argv: list[str], rule) -> tuple[list[str], list[str], list[str]]:
    """Flags, method values, and operands, with the rule's own command words
    removed.

    The command words are not operands: leaving `curl` and `git push` in the
    list made every `operand_count` off by the length of the signature.
    """
    lead = len(rule.sig.split())
    return _flags_and_operands(argv[lead:], rule.valued)


def check(effect_class: str, argv: list[str]) -> None:
    """Refuse an argv inconsistent with its declared class."""
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
        flags, methods, operands = _rule_parts(argv, rule)
        if rule.url_path is not None:
            target = operands[0] if operands else None
            if not target:
                reasons.append(f"{rule.sig}: no url operand")
                continue
            scheme = target.split("://", 1)[0].lower() if "://" in target else None
            if scheme is not None and scheme not in rule.schemes:
                reasons.append(f"{rule.sig}: scheme {scheme!r} not permitted")
                continue
            # Query and fragment are DATA. The path is the endpoint.
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not re.search(rule.url_path, path):
                reasons.append(
                    f"{rule.sig}: url path {path!r} does not match {rule.url_path!r}")
                continue
        bad = [f for f in flags if f not in rule.flags]
        if bad:
            reasons.append(f"{rule.sig}: flags not permitted: {bad}")
            continue
        # A rule that names methods REQUIRES one. Checking only the values it
        # happened to collect made the constraint vacuous when no method flag
        # was given at all -- and `gh api` switches from GET to POST on its own
        # when a field is present, so an omitted `-X` reached a PUT-only rule
        # as a POST.
        if rule.methods:
            if len(methods) != 1:
                reasons.append(
                    f"{rule.sig}: exactly one explicit method required, got {methods}")
                continue
            if methods[0].upper() not in rule.methods:
                reasons.append(f"{rule.sig}: method not permitted: {methods}")
                continue
        if rule.operand_count is not None and len(operands) != rule.operand_count:
            reasons.append(
                f"{rule.sig}: expected {rule.operand_count} operand(s), got {operands}")
            continue
        bad_o = [o for o in operands
                 if o.startswith(rule.forbid_operand_prefix)] \
            if rule.forbid_operand_prefix else []
        if bad_o:
            reasons.append(f"{rule.sig}: refused operand: {bad_o}")
            continue
        return
    raise ContractViolation(
        f"argv does not match the contract for {effect_class!r}: signature "
        f"{sig!r}" + (f"; {'; '.join(reasons)}" if reasons else "")
    )


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
