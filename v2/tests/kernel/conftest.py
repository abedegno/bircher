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

import pytest

from kernel.effects import EffectClass


@pytest.fixture(autouse=True)
def _kernel_mode_defaults_to_enforce_in_tests(monkeypatch):
    """This suite predates `kernel.mode` and tests whether a guard fires at
    all -- "the exploit is reproduced below as a test; it must not be
    expressible." Production defaults `BIRCHER_KERNEL_MODE` to `shadow`
    (`kernel.mode.kernel_mode`, exercised directly by
    `tests/kernel/test_mode.py`, which sets or clears the variable itself and
    so overrides this default); every other kernel test runs under `enforce`
    unless it opts out, so a refusal it asserts on still refuses.
    """
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", "enforce")

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
