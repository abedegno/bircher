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
