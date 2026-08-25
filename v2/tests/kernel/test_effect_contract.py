"""An effect must do what its class says, on the object that was authorized.

Round 6, findings C1 and C2 — found independently by the author and by codex.

C1: nothing compared the effect CLASS to the argv, so `_effect comment k -
    gh pr merge 123` journalled a comment and performed a merge. Because the
    class was not `merge`, `revalidate_merge` never ran at all: the
    authority-bearing gate was opt-in by the caller.

C2: even with the class declared honestly, the argv was caller-supplied and
    unchecked. A run legitimately authorized to merge its own artifact
    executed `gh pr merge 9999 --repo attacker/other --admin`.

Both are the same shape as every other finding in this programme: a real,
correctly implemented check that constrains something the caller chooses.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64
REPO, PR = "abedegno/muesli", 42


def _sub(s, name, key, actor, role, **p):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation, payload=p))


def _authorized():
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
         policy_version=1, pr=PR, repo=REPO)
    return s


def _perform(s, cls, key, argv, run="r"):
    gen = dispatch(s, run, actor="claude", role=Role.IMPLEMENTER).generation
    ran = []
    out = perform(s, run, gen, cls, key, {"argv": argv},
                  lambda c, i, k: ran.append(i["argv"]) or "done")
    return out, ran


# --- C1: the class must match the argv ---------------------------------------

def test_a_merge_labelled_as_a_comment_is_refused():
    """THE BYPASS. Mislabelling skipped revalidate_merge entirely."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "sneaky",
                 ["gh", "pr", "merge", "123"])


def test_a_push_labelled_as_a_comment_is_refused():
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "sneaky2",
                 ["git", "push", "origin", "HEAD:main"])


def test_an_honest_comment_is_allowed():
    """The control. Refusing everything would pass both tests above."""
    s = _authorized()
    out, ran = _perform(s, EffectClass.COMMENT, "honest",
                        ["gh", "pr", "comment", "7", "--body", "hi"])
    assert out == "done" and ran


def test_an_argv_matching_no_contract_is_refused():
    """Fail closed on a shape the kernel does not recognise, rather than
    passing it through because no rule said no."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "weird", ["curl", "https://evil/"])


# --- C2: the merge must target what was authorized ---------------------------

def test_a_merge_of_a_different_pr_is_refused():
    """THE OTHER HALF. The run is legitimately authorized; the argv is not."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="authorized"):
        _perform(s, EffectClass.MERGE, "wrong-pr",
                 ["gh", "pr", "merge", "9999", "--repo", REPO])


def test_a_merge_in_a_different_repo_is_refused():
    s = _authorized()
    with pytest.raises(NotAuthorized, match="authorized"):
        _perform(s, EffectClass.MERGE, "wrong-repo",
                 ["gh", "pr", "merge", str(PR), "--repo", "attacker/other"])


def test_the_authorized_merge_executes():
    """The control."""
    s = _authorized()
    out, ran = _perform(s, EffectClass.MERGE, "right",
                        ["gh", "pr", "merge", str(PR), "--repo", REPO])
    assert out == "done"
    assert ran[0][:4] == ["gh", "pr", "merge", str(PR)]


def test_request_merge_must_name_its_pr_and_repo():
    """The authorization has to record a target, or there is nothing for the
    effect to be bound to."""
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    with pytest.raises(NotAuthorized, match="pr"):
        _sub(s, "request_merge", "k7", "claude", Role.IMPLEMENTER,
             head_git_sha=HEAD, artifact_hash=out, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)


def test_the_authorization_fact_records_the_target():
    s = _authorized()
    from kernel.authz import latest_merge_authorization
    a = latest_merge_authorization(s, "r")
    assert a["pr"] == PR and a["repo"] == REPO
