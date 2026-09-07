import pytest

from kernel import brief, front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.policy import Policy
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def test_render_is_pure_and_names_its_inputs():
    a = brief.render(phase="spec", artefact=b"# S", bundle=b'{"body":"B"}', spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    b = brief.render(phase="spec", artefact=b"# S", bundle=b'{"body":"B"}', spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    assert a == b and isinstance(a, bytes)
    text = a.decode()
    assert brief.REVIEW_OUT in text
    assert content_hash(b"# S")[:8] in text          # the hash8 the verdict must name
    assert "VERDICT: PASS|FAIL" in text
    assert "0" * 40 in text
    assert "# S" in text and '"body":"B"' in text
    assert "grill: model" in text and "gates: spec" in text
    other = brief.render(phase="spec", artefact=b"# S2", bundle=b'{"body":"B"}', spec=None,
                         policy=Policy(), base_sha="0" * 40, template=1)
    assert other != a


def test_render_requires_the_spec_for_a_plan_and_refuses_it_for_a_spec():
    with pytest.raises(ValueError, match="spec"):
        brief.render(phase="plan", artefact=b"# P", bundle=b"{}", spec=None,
                     policy=Policy(), base_sha="0" * 40, template=1)
    with pytest.raises(ValueError, match="spec"):
        brief.render(phase="spec", artefact=b"# S", bundle=b"{}", spec=b"# S",
                     policy=Policy(), base_sha="0" * 40, template=1)
    with pytest.raises(ValueError, match="template"):
        brief.render(phase="spec", artefact=b"# S", bundle=b"{}", spec=None,
                     policy=Policy(), base_sha="0" * 40, template=2)
    out = brief.render(phase="plan", artefact=b"# P", bundle=b"{}", spec=b"# THE SPEC",
                       policy=Policy(), base_sha="0" * 40, template=1)
    assert b"# THE SPEC" in out


def test_issue_review_brief_renders_from_the_journal_and_puts_the_bytes(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    h = f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    fact = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    p = fact.payload
    assert p["phase"] == "spec" and p["epoch"] == 0 and p["artifact_hash"] == h
    assert p["context_bundle_hash"] == front.bundle_hash(s, "r-1")
    assert p["policy_version"] == front.policy_version(s, "r-1")
    assert p["base_sha"] == "0" * 40 and p["spec_hash"] is None
    assert p["brief_template"] == brief.TEMPLATE_VERSION
    stored = s.read_blob(p["brief_hash"])
    assert content_hash(stored) == p["brief_hash"]
    expected = brief.render(phase="spec", artefact=SPEC_BYTES,
                            bundle=s.read_blob(front.bundle_hash(s, "r-1")), spec=None,
                            policy=Policy(gates=frozenset()), base_sha="0" * 40,
                            template=brief.TEMPLATE_VERSION)
    assert stored == expected
    assert front.brief_for(s, "r-1", g).id == fact.id
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "issue_review_brief", {"phase": "spec"})


def test_a_plan_brief_carries_the_current_spec(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.to_specified()
    spec_hash = s.phase_artifact("r-1", "spec")
    f.author_round(PLAN_BYTES)
    g = f._dispatch(Role.REVIEWER, "claude")
    f._cmd(g, "issue_review_brief", {"phase": "plan"})
    p = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED).payload
    assert p["spec_hash"] == spec_hash
    assert SPEC_BYTES in s.read_blob(p["brief_hash"])


def test_issue_review_brief_needs_the_reviewer_role_and_the_states_phase(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="reviewer"):
        f._cmd(g, "issue_review_brief", {"phase": "spec"})
    g = f._dispatch(Role.REVIEWER, "codex")
    with pytest.raises(NotAuthorized, match="phase"):
        f._cmd(g, "issue_review_brief", {"phase": "plan"})


def test_review_ruling_binds_to_its_generations_brief(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    # The driver issues the brief; a seat without one is refused.
    g = f._dispatch(Role.REVIEWER, "codex")
    sid = f._session(g, f._newest_id()); f._end_turn(g, sid)
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": f.base_sha, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"ok")}
    with pytest.raises(NotAuthorized, match="review_brief_issued"):
        f._cmd(g, "record_review", payload)
    f.review_round("accept")
    assert s.run_state("r-1") == "specified"
    v = s.newest_fact("r-1", EventKind.REVIEW_VERDICT)
    b = front.brief_for(s, "r-1", v.payload["generation"])
    assert b is not None and b.payload["artifact_hash"] == v.payload["artifact_hash"]


def test_a_brief_from_a_previous_epoch_does_not_bind(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "issue_review_brief", {"phase": "spec"})
    sid = f._session(g, f._newest_id())
    f.revise({"number": 1, "title": "T", "body": "B2", "labels": ["bircher:autonomous"], "comments": []})
    f.author_round(SPEC_BYTES + b"\nB2\n")
    # The old generation is superseded; a fresh seat renders a brief over the new bundle.
    f.review_round("accept")
    p = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED).payload
    assert p["epoch"] == 1 and p["context_bundle_hash"] == front.bundle_hash(s, "r-1")
    payload = {"phase": "spec", "verdict": "accept", "artifact_hash": s.phase_artifact("r-1", "spec"),
               "base_sha": "0" * 40, "context_bundle_hash": front.bundle_hash(s, "r-1"),
               "policy_version": front.policy_version(s, "r-1"), "findings_hash": put_artifact(s, b"x")}
    with pytest.raises(Exception):          # OwnershipLost: g is superseded
        f._cmd(g, "record_review", payload)
