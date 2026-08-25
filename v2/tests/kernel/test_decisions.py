import pytest

from kernel.decisions import DecisionRejected, validate_decision


def _decision(**over):
    d = {
        "decision_id": "dec_1", "run_id": "r", "decision_type": "review_ruling",
        "based_on": {
            "state_version": 47, "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "base_git_sha": "c" * 40, "head_git_sha": "d" * 40,
            "review_bundle_sha256": "e" * 64,
        },
        "finding_rulings": [
            {"finding_id": "f1", "disposition": "blocking", "rationale": "why"}
        ],
        "recommendation": "request_revision",
    }
    d.update(over)
    return d


def _observed(**over):
    o = {
        "state_version": 47, "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
        "base_git_sha": "c" * 40, "head_git_sha": "d" * 40,
        "review_bundle_sha256": "e" * 64,
        "reviewer_identity": "codex", "implementer_identity": "claude",
    }
    o.update(over)
    return o


def test_matching_decision_is_accepted():
    validate_decision(None, _decision(), _observed())


@pytest.mark.parametrize("field", [
    "spec_sha256", "plan_sha256", "base_git_sha", "head_git_sha",
    "review_bundle_sha256", "state_version",
])
def test_any_drifted_input_rejects_the_decision(field):
    """Each referenced input checked separately: a single combined comparison
    would pass while five of six went unverified."""
    changed = 48 if field == "state_version" else "9" * (40 if field.endswith("git_sha") else 64)
    with pytest.raises(DecisionRejected, match=field):
        validate_decision(None, _decision(), _observed(**{field: changed}))


def test_reviewer_must_be_independent_of_the_implementer():
    with pytest.raises(DecisionRejected, match="independence"):
        validate_decision(None, _decision(), _observed(reviewer_identity="claude"))


def test_accept_is_a_legal_recommendation():
    """'accept' means no unresolved blockers for the pinned bundle.

    This asserts only that accept validates. The property that matters --
    that no decision can recommend a merge at all -- is enforced by the
    closed recommendation set and tested in
    test_review_fixes.test_recommendation_is_constrained_to_a_closed_set.
    An earlier version of this test built a fixture with
    recommendation='accept' and asserted it was not 'merge', which is a
    property of the fixture and not of the code.
    """
    validate_decision(None, _decision(recommendation="accept"), _observed())


def test_unknown_decision_type_is_refused():
    with pytest.raises(DecisionRejected, match="decision_type"):
        validate_decision(None, _decision(decision_type="just_merge_it"), _observed())


def test_missing_based_on_field_is_refused():
    d = _decision()
    del d["based_on"]["head_git_sha"]
    with pytest.raises(DecisionRejected, match="head_git_sha"):
        validate_decision(None, d, _observed())
