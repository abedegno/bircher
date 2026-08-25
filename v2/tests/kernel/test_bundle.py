"""The frozen input snapshot: decisions 1 and 2, made executable."""

import pytest

from kernel.bundle import (
    BUNDLE_CANON_VERSION, FROZEN_FIELDS, bundle_hash, snapshot,
)


def _issue(**over):
    i = {
        "number": 711, "title": "live transcription loses speech",
        "body": "sometimes words go missing",
        "labels": ["bircher:queued", "bug"],
        "comments": [
            {"id": 1, "author": "jon", "body": "happens on reconnect"},
            {"id": 2, "author": "bircher-bot", "body": "bircher-status: running"},
        ],
        "updated_at": "2026-08-24T10:00:00Z",
        "reactions": 3, "view_count": 91,
    }
    i.update(over)
    return i


def test_the_frozen_fields_are_fixed_and_documented():
    """Decision 1. If this set changes, every previously frozen bundle hashes
    differently -- so it is pinned by a test, not by convention."""
    assert FROZEN_FIELDS == ("number", "title", "body", "labels", "comments")


def test_the_snapshot_contains_exactly_the_frozen_fields_and_the_version():
    """Both directions. Asserting only that volatile fields are absent would
    pass for a snapshot that had silently dropped `body`."""
    assert set(snapshot(_issue())) == set(FROZEN_FIELDS) | {"canon_version"}


@pytest.mark.parametrize("field", ["updated_at", "reactions", "view_count"])
def test_volatile_fields_are_excluded(field):
    """They change without the input changing. Including them would
    invalidate approvals for no reason."""
    assert field not in snapshot(_issue())


def test_snapshot_is_stable_across_irrelevant_change():
    a = bundle_hash(snapshot(_issue()))
    b = bundle_hash(snapshot(_issue(reactions=99, view_count=5)))
    assert a == b


def test_label_order_does_not_change_the_hash():
    a = bundle_hash(snapshot(_issue(labels=["bug", "bircher:queued"])))
    b = bundle_hash(snapshot(_issue(labels=["bircher:queued", "bug"])))
    assert a == b


def test_comment_order_is_normalized_by_id():
    i = _issue()
    j = _issue(comments=list(reversed(i["comments"])))
    assert bundle_hash(snapshot(i)) == bundle_hash(snapshot(j))


def test_a_duplicate_label_still_changes_the_hash():
    """Sorting must not collapse duplicates: `sorted` is the normalization,
    not `set`, and a set would make a genuine change invisible."""
    a = bundle_hash(snapshot(_issue(labels=["bug"])))
    b = bundle_hash(snapshot(_issue(labels=["bug", "bug"])))
    assert a != b


@pytest.mark.parametrize("mutate,label", [
    (lambda i: i.update({"title": "different"}), "title"),
    (lambda i: i.update({"body": "different"}), "body"),
    (lambda i: i.update({"number": 712}), "number"),
    (lambda i: i["labels"].append("bircher:blocked"), "labels"),
    (lambda i: i["comments"].append({"id": 3, "author": "x", "body": "new"}), "comments"),
    (lambda i: i["comments"][0].update({"body": "edited"}), "comment body"),
    (lambda i: i["comments"][0].update({"author": "someone-else"}), "comment author"),
])
def test_any_frozen_field_changing_changes_the_hash(mutate, label):
    """Each frozen field checked separately: one combined assertion would pass
    while the others went unverified."""
    i = _issue()
    before = bundle_hash(snapshot(i))
    mutate(i)
    assert bundle_hash(snapshot(i)) != before, f"{label} did not affect the hash"


def test_canon_version_is_recorded_in_the_snapshot():
    """A hash whose canonical form can change without a version is a hash that
    can drift silently."""
    assert snapshot(_issue())["canon_version"] == BUNDLE_CANON_VERSION


def test_the_canon_version_participates_in_the_hash():
    """Recording the version and then not hashing it would let two canonical
    forms collide -- the exact drift the version exists to prevent."""
    s = snapshot(_issue())
    bumped = dict(s, canon_version=s["canon_version"] + 1)
    assert bundle_hash(s) != bundle_hash(bumped)


def test_the_number_is_an_int_not_whatever_the_provider_sent():
    """`"711"` and `711` must not be two different bundles."""
    assert bundle_hash(snapshot(_issue(number="711"))) == \
           bundle_hash(snapshot(_issue(number=711)))


# --- decisions 3, 4, 5 --------------------------------------------------------

import sqlite3  # noqa: E402

import pytest as _pytest  # noqa: E402

from kernel.bundle import (  # noqa: E402
    REVISION_AUTHORITY, VERDICT_KINDS, invalidates, is_relevant_change,
    propose_revision, revise_bundle,
)
from kernel.ids import Clock  # noqa: E402
from kernel.store import Store  # noqa: E402


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_decision_3_volatile_change_is_not_relevant():
    assert not is_relevant_change(_issue(), _issue(reactions=1, view_count=9))


def test_decision_3_a_frozen_field_change_is_relevant():
    assert is_relevant_change(_issue(), _issue(title="new"))


def test_decision_3_is_defined_by_the_frozen_set_not_a_parallel_list():
    """Every frozen field must drive relevance. A separate list of
    'interesting' fields drifts away from the set it mirrors."""
    for field, value in [("title", "x"), ("body", "y"), ("number", 999)]:
        assert is_relevant_change(_issue(), _issue(**{field: value})), field
    assert is_relevant_change(_issue(), _issue(labels=["only-one"]))
    assert is_relevant_change(
        _issue(), _issue(comments=[{"id": 9, "author": "z", "body": "new"}]))


# --- decision 4: authority is the entry point, not a string -------------------

def test_a_model_proposing_a_revision_does_not_revise():
    """THE property. A model may propose; the proposal changes nothing that
    re-authorizes work."""
    s = _store()
    propose_revision(s, "r", reason="the issue moved")
    kinds = [f.kind for f in s.facts_for("r")]
    assert "revision_proposed" in kinds
    assert "bundle_revised" not in kinds


def test_a_proposal_is_attributed_to_the_model_not_to_a_human():
    s = _store()
    propose_revision(s, "r", reason="x")
    fact = [f for f in s.facts_for("r") if f.kind == "revision_proposed"][0]
    assert fact.actor == "model"


def test_the_model_path_has_no_argument_that_reaches_the_human_path():
    """`REVISION_AUTHORITY == "human"` compares a constant to a constant: it
    states the decision without enforcing it. What enforces it is that
    `propose_revision` takes no parameter -- actor, authority, or otherwise --
    that could make it revise."""
    import inspect

    params = set(inspect.signature(propose_revision).parameters) - {"store", "run_id"}
    assert params == {"reason"}, f"a proposal can carry {sorted(params)}"


def test_the_operator_path_records_a_human_revision():
    """The control. Without it, a revise function that did nothing would pass
    every test above."""
    s = _store()
    h = revise_bundle(s, "r", new_snapshot=snapshot(_issue(title="new")),
                      reason="issue edited")
    fact = [f for f in s.facts_for("r") if f.kind == "bundle_revised"][0]
    assert fact.actor == "human"
    assert fact.payload["bundle_hash"] == h


def test_revision_authority_is_still_recorded_for_readers():
    assert REVISION_AUTHORITY == "human"


# --- decision 5 ---------------------------------------------------------------

def test_decision_5_implementation_output_does_not_invalidate_spec_or_plan():
    """A spec verdict binds the spec artifact and the base. Implementation
    changes the head, which the spec verdict never bound -- invalidating it
    would discard sound approvals and force re-review churn."""
    assert not invalidates("spec_review", {"head_git_sha"})
    assert not invalidates("plan_review", {"head_git_sha"})


def test_decision_5_implementation_review_does_bind_the_head():
    assert invalidates("implementation_review", {"head_git_sha"})


def test_decision_5_a_base_change_invalidates_every_kind():
    """Every verdict binds base_sha. Rebasing the world changes what any
    approval was about."""
    for kind in VERDICT_KINDS:
        assert invalidates(kind, {"base_git_sha"}), kind


def test_decision_5_a_bundle_change_invalidates_spec_and_plan():
    for kind in ("spec_review", "plan_review"):
        assert invalidates(kind, {"context_bundle_hash"}), kind


def test_decision_5_a_bundle_change_does_not_invalidate_implementation_review():
    """Stated explicitly rather than left as the absence of a test: an
    implementation verdict binds the head and the artifact, not the bundle."""
    assert not invalidates("implementation_review", {"context_bundle_hash"})


def test_decision_5_an_unrelated_change_invalidates_nothing():
    for kind in VERDICT_KINDS:
        assert not invalidates(kind, {"labels_on_some_other_issue"}), kind


def test_an_unknown_verdict_kind_raises_rather_than_returning_false():
    """A default of False makes a typo silently mean 'nothing invalidates
    this' -- the fail-open direction."""
    with _pytest.raises(ValueError, match="unknown verdict kind"):
        invalidates("speck_review", {"base_git_sha"})
