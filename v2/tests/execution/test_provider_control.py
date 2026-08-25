"""Criterion 4: provider-control effects are PERMITTED kernel effects, tested
separately from forbidden GitHub and repository mutations.

Session creation, prompting, polling and stopping are themselves mutating
POSTs. A boundary that denied them would break the retained path outright --
which is exactly why trapping `curl` cannot prove anything, and why "every
mutation-capable command" is not an enumerable set.
"""

import pathlib
import re

from kernel.effects import EffectClass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
INVENTORY = REPO_ROOT / "docs" / "design" / "effect-site-inventory.md"

#: Effects the kernel performs on the model provider. Permitted, and still
#: journalled: ownership ambiguity from a session stop is precisely what the
#: journal exists to record.
PERMITTED = {EffectClass.SESSION_CONTROL}

#: Effects no model process may perform. The kernel performs them from its own
#: credential domain, which is what the M1-1 boundary enforces.
FORBIDDEN_TO_MODELS = {
    EffectClass.REF_UPDATE, EffectClass.PULL_REQUEST, EffectClass.MERGE,
    EffectClass.STATUS_CHECK, EffectClass.COMMENT, EffectClass.ISSUE_OR_LABEL,
    EffectClass.REVERT_OR_RECOVERY, EffectClass.CREDENTIAL_LIFECYCLE,
}


def test_the_two_sets_partition_every_class():
    """No class may be silently unclassified -- that is how PR creation fell
    out of an earlier journal. Both directions are asserted: a missing class
    is an unclassified effect, and an overlapping one is a class that is both
    permitted and forbidden."""
    assert PERMITTED | FORBIDDEN_TO_MODELS == EffectClass.ALL, (
        f"unclassified: {sorted(EffectClass.ALL - PERMITTED - FORBIDDEN_TO_MODELS)}"
    )
    assert not (PERMITTED & FORBIDDEN_TO_MODELS)


def test_session_control_is_permitted_but_still_journalled():
    """Permitted does not mean unjournalled. A stop whose outcome is unknown
    leaves ownership ambiguous, and that ambiguity is a durable fact."""
    assert EffectClass.SESSION_CONTROL in PERMITTED
    assert EffectClass.SESSION_CONTROL in EffectClass.ALL


def test_merge_is_its_own_class_and_is_forbidden_to_models():
    """Folding merge into pull_request would let the authority-bearing
    operation share a gate with the routine one -- and merge is the only class
    perform() revalidates."""
    assert EffectClass.MERGE != EffectClass.PULL_REQUEST
    assert EffectClass.MERGE in FORBIDDEN_TO_MODELS


def test_every_class_the_coordinator_routes_is_classified():
    """The inventory drives what bash can ask for. A class appearing there and
    nowhere here is an effect nobody decided the policy for."""
    classes = set(re.findall(r"\| `(\w+)` \|", INVENTORY.read_text()))
    routed = {c for c in classes if c in EffectClass.ALL}
    assert routed, "no effect classes parsed out of the inventory"
    assert routed <= FORBIDDEN_TO_MODELS, (
        f"the coordinator routes classes that are not forbidden to models: "
        f"{sorted(routed - FORBIDDEN_TO_MODELS)}"
    )


def test_the_coordinator_never_routes_a_permitted_class():
    """Session control is the kernel's own business. If it ever appears in the
    coordinator's inventory, the boundary has moved and this test should be
    the thing that says so."""
    classes = set(re.findall(r"\| `(\w+)` \|", INVENTORY.read_text()))
    assert not (classes & PERMITTED)
