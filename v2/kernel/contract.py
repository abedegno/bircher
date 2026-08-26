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

from dataclasses import dataclass, field

from kernel.effects import EffectClass

_METHOD_FLAGS = ("-X", "--method")


@dataclass(frozen=True)
class Rule:
    """One permitted command shape for a class."""

    sig: str
    #: For `gh api`: a fragment that must appear in the URL OPERAND itself --
    #: not anywhere in the argv. Searching the joined argv let the marker be
    #: smuggled into an unrelated field.
    url: str | None = None
    flags: frozenset = frozenset()
    #: Flags whose NEXT token is a value, not an operand. Without this,
    #: `--max-time 120` made `120` the URL operand and every session_control
    #: call failed its own contract -- and, worse, a real operand could hide
    #: behind a flag that takes one.
    valued: frozenset = frozenset()
    methods: frozenset = frozenset()
    #: Operand prefixes that are refused outright. `:` on a git refspec is a
    #: DELETION -- `git push origin :main` removes the branch.
    forbid_operand_prefix: tuple = ()


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
             forbid_operand_prefix=(":",)),
        Rule("gh api", url="update-branch", flags=frozenset({"-X", "-f", "-q"}),
             valued=frozenset({"-X", "-f", "-q"}), methods=frozenset({"PUT"})),
    ],
    EffectClass.STATUS_CHECK: [
        Rule("gh api", url="/statuses/", flags=frozenset({"-X", "-f"}),
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
             forbid_operand_prefix=(":",)),
    ],
    EffectClass.SESSION_CONTROL: [
        Rule("curl", url="/v1/sessions",
             flags=frozenset({"-s", "-sf", "-f", "-X", "-H", "-d", "-F", "-w",
                              "--max-time"}),
             valued=frozenset({"-X", "-H", "-d", "-F", "-w", "--max-time"}),
             methods=frozenset({"POST", "DELETE"})),
    ],
    EffectClass.CREDENTIAL_LIFECYCLE: [
        Rule("gh auth", flags=frozenset({"--hostname"})),
    ],
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


def _url_operand(argv: list[str], after: str, valued: frozenset) -> str | None:
    """The first operand following *after* -- `gh api <URL>`, `curl <URL>`."""
    _f, _m, operands = _flags_and_operands(argv, valued)
    if after in operands:
        i = operands.index(after)
        return operands[i + 1] if i + 1 < len(operands) else None
    return operands[0] if operands else None


def check(effect_class: str, argv: list[str]) -> None:
    """Refuse an argv inconsistent with its declared class."""
    allowed = CONTRACTS.get(effect_class)
    if not allowed:
        raise ContractViolation(
            f"effect class {effect_class!r} declares no argv contract, so "
            "nothing constrains what it may run"
        )
    sig = signature(argv)
    reasons = []
    for rule in allowed:
        if not (sig == rule.sig or sig.startswith(rule.sig + " ")):
            continue
        flags, methods, operands = _flags_and_operands(argv, rule.valued)
        if rule.url is not None:
            target = _url_operand(argv, rule.sig.split()[-1], rule.valued)
            if not target or rule.url not in target:
                reasons.append(f"{rule.sig}: url operand {target!r} lacks {rule.url!r}")
                continue
        bad = [f for f in flags if f not in rule.flags]
        if bad:
            reasons.append(f"{rule.sig}: flags not permitted: {bad}")
            continue
        bad_m = [m for m in methods if m.upper() not in rule.methods]
        if bad_m:
            reasons.append(f"{rule.sig}: method not permitted: {bad_m}")
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


def merge_target(argv: list[str]) -> tuple[str | None, str | None]:
    """The (pr, repo) a merge command would act on."""
    pr = repo = None
    for i, tok in enumerate(argv):
        if tok == "merge" and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            pr = argv[i + 1]
        if tok == "--repo" and i + 1 < len(argv):
            repo = argv[i + 1]
        elif tok.startswith("--repo="):
            repo = tok.split("=", 1)[1]
    return pr, repo
