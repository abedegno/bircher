import pytest

from kernel import bundle, front
from kernel.authz import NotAuthorized
from kernel.canon import canonical_bytes, content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front

ISSUE = {"number": 1, "title": "T", "body": "B", "labels": ["bircher:queued"], "comments": []}


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_a_relevant_change_resets_to_queued_and_bumps_the_epoch(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=dict(ISSUE, labels=["bircher:queued", "bircher:autonomous"]))
    f.author_round(SPEC_BYTES); f.review_round("accept")
    f.author_round(PLAN_BYTES)
    assert s.run_state("r-1") == "plan_submitted"
    old_hash = front.bundle_hash(s, "r-1")
    new_issue = dict(ISSUE, body="B\n\nAlso handle the dark theme.",
                     labels=["bircher:running", "bircher:autonomous"])
    f.revise(new_issue)
    assert s.run_state("r-1") == "queued"
    assert front.epoch(s, "r-1") == 1
    fact = s.newest_fact("r-1", EventKind.BUNDLE_REVISED)
    new_hash = bundle.bundle_hash(bundle.snapshot(new_issue))
    assert fact.payload["bundle_hash"] == new_hash
    assert fact.payload["prior_bundle_hash"] == old_hash
    assert fact.payload["reason"] == "issue changed"
    assert fact.payload["diff"]["changed"] == ["body"]
    assert "dark theme" in fact.payload["diff"]["unified"]
    assert s.read_blob(new_hash) == canonical_bytes(bundle.snapshot(new_issue))
    assert front.bundle_hash(s, "r-1") == new_hash
    # The phase artefacts of epoch 0 are still readable, but a fresh epoch submits afresh.
    assert s.phase_artifact("r-1", "spec") is not None
    f.author_round(SPEC_BYTES)                       # same bytes, new epoch: not identical
    assert s.run_state("r-1") == "spec_submitted"
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED).payload["epoch"] == 1


def test_a_bircher_only_change_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=ISSUE)
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="relevant"):
        f._cmd(g, "revise_bundle", {"issue": dict(ISSUE, labels=["bircher:running"],
                                                  comments=[{"id": 1, "author": "bircher",
                                                             "body": "bircher: outcome=escalated"}])})
    assert s.run_state("r-1") == "spec_submitted" and front.epoch(s, "r-1") == 0


def test_revision_consumes_a_park_and_is_not_a_round(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", issue=ISSUE)
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.revise(dict(ISSUE, title="T2"))
    assert front.current_park(s, "r-1") is None            # the transition consumed it
    assert s.run_state("r-1") == "queued"


def test_snapshot_diff_shape_and_cap():
    old = bundle.snapshot(ISSUE)
    new = bundle.snapshot(dict(ISSUE, title="T2", body="x" * 200_000))
    d = bundle.snapshot_diff(old, new)
    assert d["changed"] == ["body", "title"]
    assert len(d["unified"]) <= 65536 + len("\n... truncated")
    assert d["unified"].endswith("... truncated")
    assert bundle.snapshot_diff(old, old) == {"changed": [], "unified": ""}


def test_v1_revise_bundle_function_now_puts_the_bytes(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1", issue=ISSUE)
    snap = bundle.snapshot(dict(ISSUE, title="T3"))
    h = bundle.revise_bundle(s, "r-1", new_snapshot=snap, reason="operator")
    assert s.read_blob(h) == canonical_bytes(snap)
