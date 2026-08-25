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
    leaves ownership ambiguous, and that ambiguity is a durable fact.

    The previous version of this test asserted membership in two Python sets.
    It journalled nothing, and passed for the whole period during which
    session control was performed by unrouted `curl` calls. This one performs
    the effect and reads the journal.
    """
    from kernel.dispatch import Role, dispatch
    from kernel.effects import perform
    from kernel.ids import Clock
    from kernel.store import Store

    assert EffectClass.SESSION_CONTROL in PERMITTED
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    gen = dispatch(s, "r", actor="coordinator", role=Role.OPERATOR).generation
    perform(s, "r", gen, EffectClass.SESSION_CONTROL, "stop:1",
            {"argv": ["curl", "-X", "DELETE", "http://srv/v1/sessions/1"]},
            lambda *a: "stopped")
    kinds = [f.kind for f in s.facts_for("r") if f.kind.startswith("effect_")]
    assert "effect_intended" in kinds and "effect_confirmed" in kinds


def test_an_uncertain_session_stop_halts_the_run():
    """The hazard the spec names: a stop that may or may not have happened."""
    import pytest as _pt

    from kernel.dispatch import Role, dispatch
    from kernel.effects import UncertainEffect, is_halted, perform
    from kernel.ids import Clock
    from kernel.store import Store

    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    gen = dispatch(s, "r", actor="coordinator", role=Role.OPERATOR).generation
    with _pt.raises(UncertainEffect):
        perform(s, "r", gen, EffectClass.SESSION_CONTROL, "stop:1",
                {"argv": ["curl", "-X", "DELETE", "http://srv/v1/sessions/1"]},
                lambda *a: (_ for _ in ()).throw(TimeoutError("no response")))
    assert is_halted(s, "r")


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
    unclassified = routed - PERMITTED - FORBIDDEN_TO_MODELS
    assert not unclassified, f"routed but unclassified: {sorted(unclassified)}"


def test_the_coordinator_routes_session_control():
    """This test used to assert the OPPOSITE -- that no permitted class
    appears in the inventory -- with a comment reading "session control is the
    kernel's own business". It did not appear because it was never routed:
    three live `curl` calls to $SERVER/v1/sessions were performing session
    create, prompt and stop with no journal at all. The test compared two
    hand-written sets and passed, reflecting the omission rather than
    detecting it.

    Permitted means the kernel MAY perform it, not that it happens outside the
    journal. Spec section 5 names "session dispatch, stop, and reconciliation
    wherever ambiguity affects ownership" as a journalled class, and section
    on uncertainty names the exact hazard: a session stop may be unconfirmed.
    """
    classes = set(re.findall(r"\| `(\w+)` \|", INVENTORY.read_text()))
    assert EffectClass.SESSION_CONTROL in classes, (
        "session control is not routed, so a stop whose outcome is unknown "
        "leaves ownership ambiguous with nothing recording it"
    )
