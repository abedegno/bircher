from kernel import bundle

RAW = {
    "number": 7, "title": "T", "body": "B",
    "labels": [{"name": "bug", "color": "x"}, {"name": "bircher:queued", "color": "y"}],
    "comments": [
        {"id": "IC_kwDOA", "author": {"login": "alice"}, "body": "second",
         "url": "https://github.com/o/r/issues/7#issuecomment-222"},
        {"id": "IC_kwDOB", "author": {"login": "bob"}, "body": "first",
         "url": "https://github.com/o/r/issues/7#issuecomment-111"},
        {"id": "IC_kwDOC", "author": None, "body": "ghost",
         "url": "https://github.com/o/r/issues/7#issuecomment-333"},
    ],
}


def test_from_gh_reduces_to_the_snapshot_shape():
    issue = bundle.from_gh(RAW)
    assert issue["labels"] == ["bug", "bircher:queued"]
    assert issue["comments"] == [
        {"id": 222, "author": "alice", "body": "second"},
        {"id": 111, "author": "bob", "body": "first"},
        {"id": 333, "author": "unknown", "body": "ghost"},
    ]
    snap = bundle.snapshot(issue)
    assert [c["id"] for c in snap["comments"]] == [111, 222, 333]
    assert snap["labels"] == ["bug"]


def test_from_gh_tolerates_missing_fields():
    assert bundle.from_gh({"number": "3", "title": "t", "body": None}) == \
        {"number": 3, "title": "t", "body": "", "labels": [], "comments": []}
