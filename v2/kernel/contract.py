"""What each effect class is allowed to do, and to what.

Round 6 found two holes that are really one: the kernel accepted a class and
an argv from the same caller and never compared them.

  _effect comment k - gh pr merge 123     -> journalled a comment, merged a PR
                                            AND skipped revalidate_merge, because
                                            the class was not `merge`

  perform(..., MERGE, {"argv": [... 9999 --repo attacker/other]})
                                         -> a run authorized to merge its own
                                            artifact merged someone else's PR

So a class must (a) constrain the SHAPE of the command, and (b) for the
authority-bearing class, constrain its TARGET to what was authorized.

This is an allowlist. An argv matching no contract is refused: a denylist
would pass every shape nobody thought to forbid, which is the direction that
fails open.
"""

from __future__ import annotations

from kernel.effects import EffectClass


def signature(argv: list[str]) -> str:
    """The command's shape: leading non-flag tokens, flags and values dropped.

    `["gh","pr","merge","42","--repo","X"]` -> `"gh pr merge"`. Numeric and
    URL-ish operands are excluded so a target cannot change the shape -- the
    target is checked separately, against the authorization, not against a
    pattern.
    """
    out = []
    for tok in argv[:3]:
        # Flags end the signature; so does the first OPERAND. An operand is a
        # shell expansion, a number, or anything path- or ref-shaped. Without
        # this the target becomes part of the shape, and `gh issue reopen $n`
        # would be a different shape from `gh issue reopen 7`.
        if tok.startswith(("-", "$")) or not tok:
            break
        if out and (tok[:1].isdigit() or "/" in tok or ":" in tok):
            break
        out.append(tok)
    return " ".join(out)


#: class -> the signatures it may carry, and for `gh api`, a URL fragment that
#: must appear somewhere in the argv. Derived from the 13 routed call sites in
#: docs/design/effect-site-inventory.md plus the three session_control calls.
CONTRACTS: dict[str, list[tuple[str, str | None]]] = {
    EffectClass.MERGE:            [("gh pr merge", None)],
    EffectClass.PULL_REQUEST:     [("gh pr close", None), ("gh pr create", None),
                                   ("gh pr reopen", None)],
    EffectClass.REF_UPDATE:       [("git push", None), ("gh api", "update-branch")],
    EffectClass.STATUS_CHECK:     [("gh api", "/statuses/")],
    EffectClass.COMMENT:          [("gh pr comment", None), ("gh issue comment", None)],
    EffectClass.ISSUE_OR_LABEL:   [("gh issue edit", None), ("gh issue close", None),
                                   ("gh issue reopen", None)],
    EffectClass.REVERT_OR_RECOVERY: [("git revert", None), ("git push", None)],
    EffectClass.SESSION_CONTROL:  [("curl", "/v1/sessions")],
    EffectClass.CREDENTIAL_LIFECYCLE: [("gh auth", None)],
}


class ContractViolation(Exception):
    """The argv does not do what its class says."""


def check(effect_class: str, argv: list[str]) -> None:
    """Refuse an argv inconsistent with its declared class."""
    allowed = CONTRACTS.get(effect_class)
    if not allowed:
        raise ContractViolation(
            f"effect class {effect_class!r} declares no argv contract, so "
            "nothing constrains what it may run"
        )
    sig = signature(argv)
    joined = " ".join(argv)
    for want_sig, want_url in allowed:
        # PREFIX match on whole tokens: `git push origin` satisfies `git push`,
        # while `git pushx` does not. Equality would force the contract to
        # enumerate every operand arity a command can take.
        if (sig == want_sig or sig.startswith(want_sig + " ")) and (
            want_url is None or want_url in joined
        ):
            return
    raise ContractViolation(
        f"argv does not match the contract for {effect_class!r}: signature "
        f"{sig!r} is not one of {[a for a, _ in allowed]}"
    )


def merge_target(argv: list[str]) -> tuple[str | None, str | None]:
    """The (pr, repo) a merge command would act on."""
    pr = repo = None
    for i, tok in enumerate(argv):
        if tok == "merge" and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            pr = argv[i + 1]
        if tok == "--repo" and i + 1 < len(argv):
            repo = argv[i + 1]
    return pr, repo
