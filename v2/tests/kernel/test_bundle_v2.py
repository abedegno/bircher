import pathlib

from kernel import bundle

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "bircher_status_comments.tsv"


def _issue(**over):
    base = {
        "number": 7, "title": "T", "body": "B",
        "labels": ["bug", "bircher:queued"],
        "comments": [{"id": 1, "author": "alice", "body": "please fix"}],
        "updated_at": "2026-09-06T00:00:00Z",
    }
    base.update(over)
    return base


def test_canon_is_version_2_and_freezes_the_same_fields():
    assert bundle.BUNDLE_CANON_VERSION == 2
    assert bundle.FROZEN_FIELDS == ("number", "title", "body", "labels", "comments")
    assert bundle.snapshot(_issue())["canon_version"] == 2


def test_fixture_drives_is_bircher_status():
    rows = [line.split("\t", 1) for line in FIXTURE.read_text().splitlines()]
    assert len(rows) >= 10
    for verdict, body in rows:
        assert bundle.is_bircher_status(body) is (verdict == "drop"), body


def test_prefixes_are_exactly_five_and_not_the_generic_one():
    assert bundle.BIRCHER_STATUS_PREFIXES == (
        "bircher: outcome=", "bircher-status:",
        "Outcome derived from the repository",
        "Cross-vendor review (outcome derived",
        "bircher: published ",
    )
    assert "bircher: " not in bundle.BIRCHER_STATUS_PREFIXES


def test_bircher_labels_and_status_comments_are_not_frozen():
    snap = bundle.snapshot(_issue())
    assert snap["labels"] == ["bug"]
    with_status = _issue(comments=[
        {"id": 1, "author": "alice", "body": "please fix"},
        {"id": 2, "author": "bircher", "body": "bircher: outcome=merged"},
        {"id": 3, "author": "bircher", "body": "bircher: published plan abc12345\n\n### Task 1"},
    ])
    assert [c["id"] for c in bundle.snapshot(with_status)["comments"]] == [1]


def test_every_bircher_mutation_is_not_a_relevant_change():
    fetched = _issue()
    flipped = _issue(labels=["bug", "bircher:running"])
    assert bundle.is_relevant_change(fetched, flipped) is False
    outcome = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: outcome=escalated"}])
    assert bundle.is_relevant_change(fetched, outcome) is False
    published = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: published spec 3f9a1c2e"}])
    assert bundle.is_relevant_change(fetched, published) is False
    all_three = _issue(labels=["bug", "bircher:parked"], comments=fetched["comments"] + [
        {"id": 9, "author": "bircher", "body": "bircher: outcome=escalated"},
        {"id": 10, "author": "bircher", "body": "bircher: published spec 3f9a1c2e"}])
    assert bundle.is_relevant_change(fetched, all_three) is False


def test_a_human_comment_is_a_relevant_change_even_when_it_quotes_a_marker():
    fetched = _issue()
    human = _issue(comments=fetched["comments"] + [
        {"id": 9, "author": "alice", "body": "the bircher: outcome=merged above is wrong"}])
    assert bundle.is_relevant_change(fetched, human) is True
    label = _issue(labels=["bug", "bircher:queued", "priority"])
    assert bundle.is_relevant_change(fetched, label) is True
