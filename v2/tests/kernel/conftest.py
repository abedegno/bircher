"""Shared test support for the kernel suite.

`valid_argv` exists because `perform()` now REQUIRES a command for any effect
class that declares an argv contract. It used to accept an empty intent and
skip the contract check and the merge-target check together -- both were
guarded by `if argv:`, so `perform(MERGE, intent={})` executed and neither
ran. That is a fail-open default, and "no production caller does that" is the
same reasoning that nearly dismissed the PATH-resolution finding.

Tests that are about the journal, fencing or idempotency rather than about the
command still need a command, so they use this.
"""

from kernel.effects import EffectClass

_ARGV = {
    EffectClass.MERGE: ["gh", "pr", "merge", "42", "--repo", "abedegno/muesli"],
    EffectClass.PULL_REQUEST: ["gh", "pr", "close", "7", "--repo", "o/r"],
    EffectClass.COMMENT: ["gh", "pr", "comment", "7", "--repo", "o/r", "--body", "x"],
    EffectClass.REF_UPDATE: ["git", "push", "origin", "HEAD:main"],
    EffectClass.STATUS_CHECK: ["gh", "api", "repos/o/r/statuses/abc", "-X", "POST",
                               "-f", "state=success"],
    EffectClass.ISSUE_OR_LABEL: ["gh", "issue", "close", "7", "--repo", "o/r"],
    EffectClass.REVERT_OR_RECOVERY: ["git", "revert", "-n", "abc"],
    EffectClass.SESSION_CONTROL: ["curl", "-sf", "-X", "DELETE",
                                  "http://srv/v1/sessions/1"],
}


def valid_argv(effect_class: str) -> dict:
    """An intent whose command satisfies *effect_class*'s contract."""
    return {"argv": list(_ARGV[effect_class])}
