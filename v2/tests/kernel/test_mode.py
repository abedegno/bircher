"""Shadow mode records what enforcement would have refused.

Turning enforcement on because the tests pass would be a claim outrunning its
evidence. Shadow produces the evidence: run real traffic, then read what would
have been refused and why.

Shadow means EVALUATE and RECORD. It does not mean act: a command whose
authorization was refused must leave no trace beyond its own refusal --
`submit()` must not write COMMAND_ACCEPTED, bump the version, or run any of
the command-specific side effects (MERGE_AUTHORIZED, set_current_artifact,
a transition, a REVIEW_VERDICT) that were previously gated only on
`cmd.name`, never on `authorize()` having actually succeeded. The round-1
review found the gap those side effects left: a run already at
`merge_requested` could receive a second, illegal `request_merge` naming an
attacker's pr/repo, and under the old (wrong) "shadow lets the command
proceed" model, that illegal command still reached the `MERGE_AUTHORIZED`
write -- poisoning the record `revalidate_merge` trusts.
"""
import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized, latest_merge_authorization
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.mode import ENFORCE, SHADOW
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def _sub(s, name, key, actor, role, **payload):
    """Submit one command, dispatching a fresh generation for it.

    `request_merge` defaults a legal-shaped target: these helpers exist to
    build sequences about OTHER properties, and the target binding itself is
    asserted directly in test_effect_contract.py.
    """
    if name == "request_merge":
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _authorized_merge():
    """A run legitimately at merge_requested. Returns (store, artifact)."""
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    _sub(s, "request_merge", "k7", "claude", Role.IMPLEMENTER, head_git_sha=HEAD,
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    assert s.run_state("r") == "merge_requested"
    return s, out


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


def test_shadow_does_not_apply_a_refused_command(store, monkeypatch):
    """Shadow evaluates and records; it does not act. `Result.accepted` is
    False, no COMMAND_ACCEPTED fact is written, and the version does not
    move -- there is nothing here for a later check to mistake for a real
    acceptance."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    before = store.run_version("r")
    r = submit(store, Command(name="request_merge", run_id="r",
                              expected_version=store.run_version("r"),
                              idempotency_key="k", generation=g, payload={}))
    assert not r.accepted
    assert store.run_version("r") == before
    kinds = [f.kind for f in store.facts_for("r")]
    assert "command_rejected" in kinds
    assert "shadow_rejected" in kinds
    assert "command_accepted" not in kinds


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
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", ENFORCE)
    g = dispatch(store, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        perform(store, "r", g, EffectClass.COMMENT, "k",
                {"argv": ["git", "push", "origin", ":main"]},
                lambda *a: "done")


def test_shadow_cannot_poison_merge_authorization_with_a_second_request(monkeypatch):
    """The round-1 review's repro, verbatim: a run legitimately at
    merge_requested receives a second, illegal request_merge naming an
    attacker's pr/repo. `authorize()` refuses it (illegal from this state);
    under shadow that refusal must not reach the MERGE_AUTHORIZED write, or
    `revalidate_merge` trusts the attacker's target and the merge effect
    executes it.
    """
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    s, out = _authorized_merge()

    attack = _sub(s, "request_merge", "attack", "claude", Role.IMPLEMENTER,
                  pr=9999, repo="attacker/evil", head_git_sha=HEAD,
                  artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
                  policy_version=1)
    assert not attack.accepted
    assert s.run_state("r") == "merge_requested", "the poisoned attempt must not transition the run"

    authorized = latest_merge_authorization(s, "r")
    assert authorized["pr"] == 42
    assert authorized["repo"] == "abedegno/muesli"

    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(NotAuthorized):
        perform(s, "r", gen, EffectClass.MERGE, "m",
                {"argv": ["gh", "pr", "merge", "9999", "--repo", "attacker/evil"]},
                lambda *a: "merged!")


def test_shadow_does_not_apply_a_phantom_artifact(monkeypatch):
    """record_implementation_output naming a hash the store never held is
    refused by authorize(); under shadow that refusal must not reach
    set_current_artifact, or the run's recorded output points at an object
    has_artifact() reports as absent."""
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    assert s.current_artifact("r") is None

    phantom = "f" * 64
    assert not s.has_artifact(phantom), "the phantom hash must actually be absent"
    r = _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
             artifact_hash=phantom)
    assert not r.accepted
    assert s.current_artifact("r") is None


def test_a_shadow_rejection_mid_sequence_does_not_derail_later_legal_commands(monkeypatch):
    """Nothing in the suite previously chained two commands under shadow, so
    this survived: an illegal attempt in the middle of an otherwise-legal
    sequence must neither advance the run nor block a LATER command that does
    not depend on it. Uses the real, unset default -- not an explicit
    monkeypatch to SHADOW -- because this is the behaviour a run gets when
    nobody has configured anything. tests/kernel/conftest.py defaults this
    whole suite to `enforce` (the rest of the suite predates the mode switch);
    `delenv` here removes that override to reach the code's actual default.
    """
    monkeypatch.delenv("BIRCHER_KERNEL_MODE", raising=False)
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")

    r1 = _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    r2 = _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    assert r1.accepted and r2.accepted
    assert s.run_state("r") == "planned"
    version_before_attack = s.run_version("r")

    # Illegal here: request_merge requires `reviewing`.
    r3 = _sub(s, "request_merge", "k3", "claude", Role.IMPLEMENTER)
    assert not r3.accepted
    assert s.run_state("r") == "planned"
    assert s.run_version("r") == version_before_attack

    # A command that does not depend on the rejected one still works.
    r4 = _sub(s, "start_implementation", "k4", "claude", Role.IMPLEMENTER)
    assert r4.accepted
    assert s.run_state("r") == "implementing"
    assert s.run_version("r") == version_before_attack + 1

    kinds = [f.kind for f in s.facts_for("r")]
    assert kinds.count("command_accepted") == 3
    assert kinds.count("command_rejected") == 1
    assert kinds.count("shadow_rejected") == 1
    assert not [f for f in s.facts_for("r") if f.kind == EventKind.MERGE_AUTHORIZED]


def test_shadow_does_not_apply_a_conflicted_review(monkeypatch):
    """The sibling of the merge-authorization defect fix round 1 closed:
    authorize() checks only state-legality and verdict well-formedness for
    record_review. Every substantive check -- artifact-hash binding,
    base-sha match, role, reviewer independence -- lives entirely in
    validate_review(). A record_review that PASSES authorize() and FAILS
    validate_review() (a conflicted reviewer, here: the actor who
    implemented and produced the artifact under review) must not advance the
    run under shadow, or the except-block sibling to the one round 1 fixed
    would have zero coverage -- exactly what the round-2 re-review found.
    """
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", SHADOW)
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    assert s.run_state("r") == "implementing"
    state_before = s.run_state("r")
    version_before = s.run_version("r")
    # The prior legitimate commands already wrote their own
    # transition_performed/command_accepted facts -- what matters is whether
    # THIS refused review adds any more, not whether the run has ever seen
    # one.
    kinds_before = [f.kind for f in s.facts_for("r")]

    # claude implemented this run and produced the artifact under review;
    # dispatched as reviewer here so it clears authorize()'s role/state
    # checks and reaches validate_review()'s independence check, which must
    # refuse it.
    r = _sub(s, "record_review", "k5", "claude", Role.REVIEWER,
             verdict="accept", artifact_hash=out, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)
    assert not r.accepted
    assert s.run_state("r") == state_before, "a refused review must not transition the run"
    assert s.run_version("r") == version_before

    new_kinds = [f.kind for f in s.facts_for("r")][len(kinds_before):]
    assert EventKind.TRANSITION not in new_kinds
    assert EventKind.REVIEW_VERDICT not in new_kinds
    assert EventKind.COMMAND_ACCEPTED not in new_kinds
    assert "command_rejected" in new_kinds
    assert "shadow_rejected" in new_kinds
