import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_the_max_rounds_plus_oneth_revision_is_refused_until_granted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", project_config={"max_rounds": 2})
    for i in range(2):
        f.author_round(SPEC_BYTES + bytes([48 + i]))
        f.review_round("request_revision")
    assert front.rounds_used(s, "r-1", "spec", 0) == 2
    f.author_round(SPEC_BYTES + b"3")
    with pytest.raises(NotAuthorized, match="max_rounds"):
        f.review_round("request_revision")
    assert s.run_state("r-1") == "spec_submitted"
    # accept is not bounded
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": "request_revision", "reviewer": "codex"})
    f.grant()
    assert front.round_bound(s, "r-1", "spec", 0) == 3
    f.review_round("request_revision")
    assert s.run_state("r-1") == "queued"
    assert front.rounds_used(s, "r-1", "spec", 0) == 3


def test_rounds_are_per_phase_and_epoch_and_human_revisions_do_not_count(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", project_config={"max_rounds": 1})
    f.author_round(SPEC_BYTES); f.review_round("request_revision")
    h = f.author_round(SPEC_BYTES + b"2")
    f.human("record_review", {"phase": "spec", "artifact_hash": h, "verdict": "request_revision",
                              "findings": "human says no"})
    assert front.rounds_used(s, "r-1", "spec", 0) == 1
    f.author_round(SPEC_BYTES + b"3")
    with pytest.raises(NotAuthorized, match="max_rounds"):
        f.review_round("request_revision")
    f.review_round("accept")
    # The plan phase has its own allowance.
    f.author_round(b"# Plan\n\n### Task 1: x\n")
    f.review_round("request_revision")
    assert front.rounds_used(s, "r-1", "plan", 0) == 1
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    assert front.rounds_used(s, "r-1", "spec", 1) == 0


def test_submit_actor_must_be_the_sessions_vendor(tmp_path):
    """The snapshot names the BUNDLE (v2_author_claude); the dispatch the
    vendor (codex). vendor_of maps one to the other (ruling 14)."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "codex")
    sid = f._session(g, f._newest_id(), actor="claude")     # snapshot agent_name == "v2_author_claude"
    f._end_turn(g, sid)
    assert __import__("json").loads(s.effect_by_key(f"sess-create:r-1:{g}", run_id="r-1")["external_object_id"])["agent_name"] == "v2_author_claude"
    with pytest.raises(NotAuthorized, match="agent_name"):
        f._cmd(g, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    rejected = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rejected.actor == "codex"
    # The spec's own §8 case: snapshot v2_author_claude, a claude generation -> accepted.
    g2 = f._dispatch(Role.AUTHOR, "claude")
    sid2 = f._session(g2, f._newest_id())
    f._end_turn(g2, sid2)
    f._cmd(g2, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    assert s.run_state("r-1") == "spec_submitted"
    assert front.vendor_of("v2_author_claude") == "claude" and front.vendor_of("codex") is None
    assert front.vendor_of("v2_implementer") is None and front.vendor_of("v2_author_") is None


def test_reviewer_actor_must_be_the_seats_vendor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._session(g, f._newest_id(), actor="gemini")
    f._end_turn(g, sid)
    issued = front.brief_for(s, "r-1", g).payload
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": issued["artifact_hash"],
               "base_sha": issued["base_sha"], "context_bundle_hash": issued["context_bundle_hash"],
               "policy_version": issued["policy_version"], "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="agent_name"):
        f._cmd(g, "record_review", payload)


def test_identity_laundering_is_refused(tmp_path):
    """A Claude session's artefact submitted under a Codex generation."""
    s = _store(tmp_path)
    f = Front(s, "r-1", author="claude", reviewer="codex")
    g1 = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g1, f._newest_id())
    # The pass dies; a resumed pass dispatches as codex and adopts the session.
    g2 = f._dispatch(Role.AUTHOR, "codex")
    f._prompt(g2, sid, f._newest_id())
    f._end_turn(g2, sid)
    with pytest.raises(NotAuthorized, match=r"codex.*v2_author_claude"):
        f._cmd(g2, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES)})
    # Positive control: a fresh claude generation with a matching claude
    # session is accepted -- the refusal above is the vendor mismatch, not
    # some blanket rejection of this run or this phase.
    g3 = f._dispatch(Role.AUTHOR, "claude")
    sid3 = f._session(g3, f._newest_id())
    f._end_turn(g3, sid3)
    f._cmd(g3, "submit_spec", {"artifact_hash": put_artifact(s, SPEC_BYTES + b"-ctrl")})
    assert s.run_state("r-1") == "spec_submitted"
