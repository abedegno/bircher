"""Shadow mode records what enforcement would have refused.

Turning enforcement on because the tests pass would be a claim outrunning its
evidence. Shadow produces the evidence: run real traffic, then read what would
have been refused and why.
"""
import pytest

from kernel.artifacts import put_artifact
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.mode import ENFORCE, SHADOW
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_the_default_is_shadow(monkeypatch):
    monkeypatch.delenv("BIRCHER_KERNEL_MODE", raising=False)
    from kernel.mode import kernel_mode
    assert kernel_mode() == SHADOW


def test_an_unknown_mode_is_refused(monkeypatch):
    """A typo must not silently mean shadow -- that is the direction that
    disables every guard without saying so."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", "yolo")
    from kernel.mode import kernel_mode
    with pytest.raises(ValueError, match="BIRCHER_KERNEL_MODE"):
        kernel_mode()


def test_shadow_accepts_a_command_enforcement_would_refuse(store, monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    r = submit(store, Command(name="request_merge", run_id="r",
                              expected_version=store.run_version("r"),
                              idempotency_key="k", generation=g, payload={}))
    assert r.accepted


def test_shadow_records_what_would_have_been_refused(store, monkeypatch):
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="request_merge", run_id="r",
                          expected_version=store.run_version("r"),
                          idempotency_key="k", generation=g, payload={}))
    shadow = [f for f in store.facts_for("r") if f.kind == "shadow_rejected"]
    assert len(shadow) == 1
    assert shadow[0].payload["command_name"] == "request_merge"
    assert "queued" in shadow[0].payload["reason"]


def test_enforce_still_refuses(store, monkeypatch):
    """The control. If shadow were the only behaviour, every test above would
    pass with the guards deleted."""
    from kernel.authz import NotAuthorized
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", ENFORCE)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        submit(store, Command(name="request_merge", run_id="r",
                              expected_version=store.run_version("r"),
                              idempotency_key="k", generation=g, payload={}))


def test_shadow_covers_the_argv_contract_too(store, monkeypatch):
    """One switch, not two: commands shadowed while effects enforce is a state
    nobody reasoned about."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    ran = []
    perform(store, "r", g, EffectClass.COMMENT, "k",
            {"argv": ["git", "push", "origin", ":main"]},
            lambda c, i, kk: ran.append(i) or "done")
    assert ran, "shadow did not let the effect through"
    assert [f for f in store.facts_for("r") if f.kind == "shadow_rejected"]


def test_enforce_still_refuses_a_contract_violation(store, monkeypatch):
    from kernel.authz import NotAuthorized
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", ENFORCE)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        perform(store, "r", g, EffectClass.COMMENT, "k",
                {"argv": ["git", "push", "origin", ":main"]},
                lambda *a: "done")


def test_a_shadow_rejection_is_recorded_before_the_command_proceeds(store, monkeypatch):
    """Order matters: a crash mid-command must leave the refusal recorded, or
    the evidence is lost in exactly the runs worth studying."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    submit(store, Command(name="request_merge", run_id="r",
                          expected_version=store.run_version("r"),
                          idempotency_key="k", generation=g, payload={}))
    kinds = [f.kind for f in store.facts_for("r")]
    assert kinds.index("shadow_rejected") < kinds.index("command_accepted")
