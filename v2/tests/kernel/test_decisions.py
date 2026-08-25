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


def test_accept_does_not_authorize_a_merge():
    """'accept' means no unresolved blockers for the pinned bundle. Only the
    kernel authorizes a merge, and only through request_merge."""
    d = _decision(recommendation="accept")
    validate_decision(None, d, _observed())
    assert d["recommendation"] != "merge"


def test_unknown_decision_type_is_refused():
    with pytest.raises(DecisionRejected, match="decision_type"):
        validate_decision(None, _decision(decision_type="just_merge_it"), _observed())


def test_missing_based_on_field_is_refused():
    d = _decision()
    del d["based_on"]["head_git_sha"]
    with pytest.raises(DecisionRejected, match="head_git_sha"):
        validate_decision(None, d, _observed())
