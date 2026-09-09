"""Task 3: the shaping states and the three commands that move between them
(shaping spec §2 *States*, *Existing commands whose from-sets gain states*,
*Which state set each site reads*, `record_one_piece`, `advance_ungated`)."""
import pytest

from kernel import authz, front, projection
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized, phase_of
from kernel.brief import render
from kernel.dispatch import Role
from kernel.enqueue import create_run
from kernel.events import EventKind
from kernel.policy import Policy
from kernel.slices import COARSE
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


# -- the sets ------------------------------------------------------------------

def test_the_sets_and_the_phase_map():
    assert authz.SHAPING_STATES == frozenset({"shaping", "slices_submitted", "slices_accepted"})
    assert not (authz.SHAPING_STATES & authz.FRONT_HALF_STATES)
    for st in ("shaping", "slices_submitted", "slices_accepted", "sliced"):
        assert phase_of(st) == "slices"
        assert st in authz._ALL_ACTIVE
    assert front.FRONT_PHASES == ("slices", "spec", "plan") == projection.FRONT_PHASES
    assert projection.is_front_verdict({"phase": "slices"})


def test_membership_per_command():
    both = authz.FRONT_HALF_STATES | authz.SHAPING_STATES
    legal = authz.legal_states_for
    for name in ("park", "record_human_answer", "record_prompt_item", "dismiss_human_item"):
        assert legal(name) == both, name
    assert legal("record_human_direction") == frozenset({"queued", "specified", "shaping"})
    assert legal("record_model_question") == authz.FRONT_HALF_STATES
    assert legal("record_model_ruling") == authz.FRONT_HALF_STATES
    assert legal("revise_bundle") == both and authz.next_state_for("revise_bundle") == "shaping"
    assert legal("record_turn_ended") >= {"shaping", "slices_submitted"}
    assert legal("record_author_empty") >= {"shaping"}
    assert legal("grant_round") >= {"shaping", "slices_submitted"}
    assert legal("issue_review_brief") >= {"slices_submitted"}
    assert legal("record_review") >= {"slices_submitted", "slices_accepted"}
    assert legal("approve_artifact") >= {"slices_accepted"}
    assert legal("record_one_piece") == frozenset({"shaping"})
    assert legal("submit_slices") == frozenset({"shaping"})
    assert legal("advance_ungated") == frozenset({"slices_accepted"})
    assert "sliced" not in legal("revise_bundle")
    assert "sliced" in legal("cancel_run") and "sliced" in legal("record_run_outcome")


def test_create_run_is_born_in_shaping(tmp_path):
    s = _store(tmp_path)
    create_run(s, run_id="r-1", base_repo="o/r", base_sha="0" * 40, issue=ISSUE, project_config={})
    assert s.run_state("r-1") == "shaping"
    assert projection.project(s.facts_for("r-1")).state == "shaping"


# -- record_one_piece ----------------------------------------------------------

def test_one_piece_moves_to_queued_and_records_the_shape_ruling(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")                      # the driver shapes by default (planning ruling 3)
    assert s.run_state("r-1") == "queued"
    r = front.shape_ruling(s, "r-1", 0)
    assert r is not None and r.payload["ruling"] == "one piece" and r.payload["question_id"] == "shape"
    assert r.payload["reasoning"] and r.payload["cost_if_wrong"]
    f.author_round(SPEC_BYTES)               # the run proceeds as today
    assert s.run_state("r-1") == "spec_submitted"


def test_one_piece_refusals(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    with pytest.raises(NotAuthorized, match="turn_ended"):          # the turn's end is a fact
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})
    f._end_turn(g, sid)
    for bad in ({"reasoning": "", "cost_if_wrong": "c"}, {"reasoning": "r", "cost_if_wrong": "  "}, {"reasoning": "r"}):
        with pytest.raises(NotAuthorized, match="non-empty"):
            f._cmd(g, "record_one_piece", bad)
    # A direction newer than the dispatch: the interrupted turn's ruling is not recorded.
    f.direct("slice it along the store/API line")
    with pytest.raises(NotAuthorized, match="human_direction"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})
    assert s.run_state("r-1") == "shaping"


def test_one_piece_is_refused_by_a_reviewer_and_by_the_wrong_vendor(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause, actor="codex")            # the seat's bundle names codex
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="vendor"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})


def test_second_ruling_in_an_epoch_is_refused_and_a_new_epoch_admits_one(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    # Back to shaping only through a revision, which opens a new epoch.
    f.revise(dict(ISSUE, body="changed", labels=["bircher:autonomous"]), reshape=False)
    assert s.run_state("r-1") == "shaping" and f.epoch() == 1
    f.shape_round()
    assert s.run_state("r-1") == "queued"
    assert front.shape_ruling(s, "r-1", 1) is not None


def test_a_rejected_slice_plan_then_a_one_piece_ruling_is_legal(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES)
    assert s.run_state("r-1") == "slices_submitted"
    f.review_round("request_revision", findings=b"one piece really")
    assert s.run_state("r-1") == "shaping"
    f.shape_round()                                     # legal: the epoch's newer decision
    assert s.run_state("r-1") == "queued"
    sub = front.newest_submission(s, "r-1", "slices", 0)
    assert front.shape_ruling(s, "r-1", 0).seq > sub.seq


def test_the_shape_id_is_reserved(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="reserved"):
        f._cmd(g, "record_model_question", {"question_id": "shape", "question": "?"})
    with pytest.raises(NotAuthorized, match="reserved"):
        f._cmd(g, "record_model_ruling", {"question_id": "shape", "ruling": "one piece",
                                          "reasoning": "r", "cost_if_wrong": "c"})


def test_model_question_and_ruling_are_refused_from_the_shaping_states(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    cause = f._newest_id()
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, cause)
    f._end_turn(g, sid)
    with pytest.raises(NotAuthorized, match="not legal from state 'shaping'"):
        f._cmd(g, "record_model_question", {"question_id": "Q1", "question": "?"})


# -- submit_slices -------------------------------------------------------------

def test_submit_slices_lands_in_slices_submitted_and_holds_the_artefact(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    h = f.shape_round(SLICES_BYTES)
    assert s.run_state("r-1") == "slices_submitted"
    assert s.phase_artifact("r-1", "slices") == h
    sub = front.newest_submission(s, "r-1", "slices", 0)
    assert sub.payload["phase"] == "slices" and sub.payload["round"] == 1


@pytest.mark.parametrize("bad, why", [
    (SLICES_BYTES.replace(b"Depends on: 1", b"Depends on: 9"), "not in the plan"),
    (SLICES_BYTES.replace(b"Add the table. Add the migration. Add the reads.", b"Add it. Done."), "sentence"),
    (b"# T\n\n## Slice 1: only\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n", "1 slices"),
])
def test_submit_slices_grammar_refusals_name_why(tmp_path, bad, why):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    with pytest.raises(NotAuthorized, match=why):
        f.shape_round(bad)
    assert s.run_state("r-1") == "shaping"


def test_a_slice_is_not_sliced_and_the_observable_is_policy_frozen(tmp_path):
    s = _store(tmp_path)
    child = dict(ISSUE, number=40, labels=["bircher:autonomous", "bircher:slice", "bircher:queued"])
    f = Front(s, "r-1", shape=False, issue=child)
    # The canon strips every bircher: label from the bundle (bundle.py), so
    # the bundle is NOT where the refusal reads.
    import json
    assert "bircher:slice" not in json.loads(s.read_blob(front.bundle_hash(s, "r-1")))["labels"]
    assert "bircher:slice" in front.frozen_labels(s, "r-1")
    with pytest.raises(NotAuthorized, match="a slice is not sliced"):
        f.shape_round(SLICES_BYTES)
    f.shape_round()                                      # it proceeds as one piece
    assert s.run_state("r-1") == "queued"


def test_identical_resubmission_is_refused(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"again")
    with pytest.raises(NotAuthorized, match="identical"):
        f.shape_round(SLICES_BYTES)


# -- the review, the gate, the advance -------------------------------------------

def test_reviewer_accept_lands_in_slices_accepted_whatever_the_gates(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)                     # autonomous: no gates
    f.shape_round(SLICES_BYTES)
    f.review_round("accept")
    assert s.run_state("r-1") == "slices_accepted"       # not planned, not sliced


def test_advance_ungated_moves_on_and_is_refused_under_a_gate(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(g, "advance_ungated", {})
    f.advance()
    assert s.run_state("r-1") == "sliced"
    adv = s.newest_fact("r-1", EventKind.ARTIFACT_ADVANCED)
    assert adv.payload == {"phase": "slices", "epoch": 0, "hash": s.phase_artifact("r-1", "slices")}
    s2 = Store.open(tmp_path / "gated.db")
    f2 = Front(s2, "r-1", shape=False, labels=())        # default policy: slices gated
    f2.shape_round(SLICES_BYTES); f2.review_round("accept")
    with pytest.raises(NotAuthorized, match="gates slices"):
        f2.advance()
    f2.approve()
    assert s2.run_state("r-1") == "sliced"


def test_human_correction_at_the_slice_gate_lands_in_shaping(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False, labels=())
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    assert s.run_state("r-1") == "slices_accepted"
    f.human("record_review", {"phase": "slices", "artifact_hash": s.phase_artifact("r-1", "slices"),
                              "verdict": "request_revision", "findings": "merge slices 1 and 2"})
    assert s.run_state("r-1") == "shaping"


def test_one_piece_is_refused_once_a_plan_is_accepted(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.shape_round(SLICES_BYTES); f.review_round("accept")
    g = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="not legal from state 'slices_accepted'"):
        f._cmd(g, "record_one_piece", {"reasoning": "r", "cost_if_wrong": "c"})


def test_revise_bundle_lands_in_shaping_from_a_spec_state(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    assert s.run_state("r-1") == "spec_submitted"
    f.revise(dict(ISSUE, body="changed"), reshape=False)
    assert s.run_state("r-1") == "shaping" and f.epoch() == 1


def test_direction_at_shaping_is_legal_and_at_slices_submitted_is_not(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False)
    f.direct("one piece, do not slice it")
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["phase"] == "slices"
    f.shape_round(SLICES_BYTES)
    with pytest.raises(NotAuthorized, match="not legal from state 'slices_submitted'"):
        f.direct("x")


def test_grant_round_is_legal_at_slices_submitted_with_a_park(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1", shape=False, project_config={"max_rounds": 1})
    f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"no")
    f.shape_round(SLICES_BYTES.replace(b"the API.", b"the API, the client."))
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "park", {"reason": "bound_exhausted", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    f.grant()
    assert front.round_bound(s, "r-1", "slices", 0) == 2


def test_the_slices_brief_carries_the_coarse_paragraph_and_spec_briefs_are_unchanged(tmp_path):
    bundle = b'{"number": 1}'
    b = render(phase="slices", artefact=SLICES_BYTES, bundle=bundle, spec=None,
               policy=Policy(), base_sha="0" * 40, template=2)
    assert COARSE.encode() in b and b"then the slice plan" in b and b"## What you are grading" in b
    for phase, spec in (("spec", None), ("plan", SPEC_BYTES)):
        art = SPEC_BYTES if phase == "spec" else PLAN_BYTES
        out = render(phase=phase, artefact=art, bundle=bundle, spec=spec, policy=Policy(), base_sha="0" * 40, template=2)
        assert b"## What you are grading" not in out and COARSE.encode() not in out
    with pytest.raises(ValueError, match="carries no spec"):
        render(phase="slices", artefact=SLICES_BYTES, bundle=bundle, spec=SPEC_BYTES,
               policy=Policy(), base_sha="0" * 40, template=2)


def test_to_sliced_drives_the_whole_phase(tmp_path):
    s = _store(tmp_path)
    Front(s, "r-1", shape=False).to_sliced()
    assert s.run_state("r-1") == "sliced"
