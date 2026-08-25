"""Failing tests for the authz review. The first one is the attack the
previous 'authorized' happy-path test demonstrated rather than refuted."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store

BASE, BUNDLE, HEAD = "c" * 40, "e" * 64, "d" * 40
SPEC = None  # set per-store: reviews may only bind artifacts the kernel holds


def _store():
    global SPEC
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    SPEC = put_artifact(s, b"# spec")
    return s


def _sub(s, name, key, owner="implementer", **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=acquire(s, "r", owner), payload=payload,
    ))


def _to_implementing(s):
    _sub(s, "submit_spec", "k1", spec_sha256=SPEC)
    _sub(s, "submit_plan", "k2", plan_sha256=put_artifact(s, b"# plan"))
    _sub(s, "start_implementation", "k3", implementer_identity="claude")
    return s


def test_a_self_asserted_review_does_not_authorize_a_merge():
    """THE attack. One caller records its own approval, naming any reviewer
    and any hashes, then presents the same tuple for merge. The binding
    comparison is real; what it compares is two things the same actor said."""
    s = _to_implementing(_store())
    with pytest.raises(NotAuthorized, match="independen"):
        _sub(s, "record_review", "k4", owner="claude", verdict="accept",
             artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
             reviewer_identity="claude", policy_version=1)


def test_a_review_naming_a_base_the_kernel_never_observed_is_refused():
    """base_sha is observed and stored by the kernel at create_run. A review
    binding some other base is binding to something that did not happen."""
    s = _to_implementing(_store())
    with pytest.raises(NotAuthorized, match="base"):
        _sub(s, "record_review", "k4", owner="codex", verdict="accept",
             artifact_hash=SPEC, base_sha="9" * 40, context_bundle_hash=BUNDLE,
             reviewer_identity="codex", policy_version=1)


def test_malformed_request_merge_is_rejected_not_crashed():
    """A missing key raised bare KeyError out of submit(): no rejection fact,
    unhandled exception on a validation path. Fail-closed by accident is
    still a crash."""
    s = _to_implementing(_store())
    _sub(s, "record_review", "k4", owner="codex", verdict="accept",
         artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity="codex", policy_version=1)
    with pytest.raises(NotAuthorized):
        _sub(s, "request_merge", "k5", owner="impl", artifact_hash=SPEC)


def test_policy_version_is_not_coerced_across_types():
    """int(1.9) == 1, so a float silently bound to a review recorded with 1 --
    in a system whose canonical form refuses floats outright."""
    s = _to_implementing(_store())
    _sub(s, "record_review", "k4", owner="codex", verdict="accept",
         artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity="codex", policy_version=1)
    with pytest.raises(NotAuthorized):
        _sub(s, "request_merge", "k5", owner="impl", artifact_hash=SPEC,
             base_sha=BASE, context_bundle_hash=BUNDLE,
             reviewer_identity="codex", policy_version=1.9)


@pytest.mark.parametrize("outcome,expected", [("merged", "merged"),
                                              ("failed", "reviewing")])
def test_merge_outcomes_move_the_run_where_they_should(outcome, expected):
    """The old version only checked that a command name appeared in the table,
    so mutating 'merged' to land back in 'reviewing' left it green. It
    asserted a table entry, not a behaviour."""
    s = _to_implementing(_store())
    _sub(s, "record_review", "rv", owner="codex", verdict="accept",
         artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity="codex", policy_version=1)
    _sub(s, "record_ci_observation", "ci", owner="impl", status="success",
         head_git_sha=HEAD)
    _sub(s, "request_merge", "rm", owner="impl", artifact_hash=SPEC,
         base_sha=BASE, context_bundle_hash=BUNDLE, reviewer_identity="codex",
         policy_version=1)
    _sub(s, "record_merge_outcome", "mo", owner="impl", outcome=outcome)
    assert s.run_state("r") == expected


def test_the_recorded_verdict_carries_the_binding_that_authorizes_merge():
    """The old version asserted only that an event of the right KIND existed,
    so replacing its binding hash with 'broken' left it green. What matters is
    that the recorded hash is the one merge authorization compares against."""
    from kernel.artifacts import VerdictBinding, binding_hash

    s = _to_implementing(_store())
    _sub(s, "record_review", "k4", owner="codex", verdict="accept",
         artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity="codex", policy_version=1)
    verdicts = [f for f in s.facts_for("r") if f.kind == "review_verdict"]
    assert len(verdicts) == 1
    expected = binding_hash(VerdictBinding(
        artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
        reviewer_identity="codex", policy_version=1,
    ))
    assert verdicts[0].payload["binding_hash"] == expected
    assert verdicts[0].payload["verdict"] == "accept"
