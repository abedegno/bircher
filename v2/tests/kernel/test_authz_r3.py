"""Failing tests for the authz review. The first one is the attack the
previous 'authorized' happy-path test demonstrated rather than refuted."""

import pytest

from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.ids import Clock
from kernel.ownership import acquire
from kernel.store import Store

SPEC, BASE, BUNDLE = "a" * 64, "c" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return s


def _sub(s, name, key, owner="implementer", **payload):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=acquire(s, "r", owner), payload=payload,
    ))


def _to_implementing(s):
    _sub(s, "submit_spec", "k1", spec_sha256=SPEC)
    _sub(s, "submit_plan", "k2", plan_sha256="b" * 64)
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


def test_merge_requested_is_not_a_dead_end():
    """No path to merged and none back to reviewing wedges every run whose
    merge does not go cleanly -- and cancel_run then records 'cancelled' for
    a run that actually merged."""
    from kernel.authz import legal_states_for

    outbound = [
        name for name in ("record_merge_outcome", "cancel_run")
        if "merge_requested" in legal_states_for(name)
    ]
    assert "record_merge_outcome" in outbound, (
        "merge_requested has no way to record what actually happened"
    )


def test_a_verdict_is_recorded_as_a_verdict():
    """REVIEW_VERDICT exists as an event kind and was never emitted; the
    verdict lived only as a verbatim copy of the caller's payload nested in a
    COMMAND_ACCEPTED fact."""
    s = _to_implementing(_store())
    _sub(s, "record_review", "k4", owner="codex", verdict="accept",
         artifact_hash=SPEC, base_sha=BASE, context_bundle_hash=BUNDLE,
         reviewer_identity="codex", policy_version=1)
    assert "review_verdict" in [f.kind for f in s.facts_for("r")]
