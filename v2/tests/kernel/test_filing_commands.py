"""Task 4: the filing and closing facts, the sliced outcome and the
sliced-once refusal (shaping spec §2)."""
import json

import pytest

from kernel import front
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.enqueue import IssueAlreadySliced, create_run
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SLICES_BYTES, Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


def _sliced(tmp_path, name="k.db"):
    s = Store.open(tmp_path / name)
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    return s, f, g


def _ob(f, kind, **more):
    return {"kind": kind, "run": f.run_id, "epoch": f.epoch(), **more}


# -- record_slice_filed --------------------------------------------------------

def test_slice_filed_reads_the_confirmed_row(tmp_path):
    s, f, g = _sliced(tmp_path)
    key = f.file_slice(g, 1, 40, 1040)
    fact = s.newest_fact(f.run_id, EventKind.SLICE_FILED)
    assert fact.payload == {"epoch": 0, "slice": 1, "issue": 40, "issue_id": 1040, "title": "The store",
                            "parent": 12, "plan_hash": s.phase_artifact(f.run_id, "slices")}
    assert front.slices_filed(s, f.run_id, 0)[1].id == fact.id
    with pytest.raises(NotAuthorized, match="already has a slice_filed"):
        f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": key})


def test_slice_filed_refusals(tmp_path):
    s, f, g = _sliced(tmp_path)
    h, parent = s.phase_artifact(f.run_id, "slices"), 12
    with pytest.raises(NotAuthorized, match="not in the accepted plan"):
        f._cmd(g, "record_slice_filed", {"slice": 9, "effect_key": "x"})
    # A confirmed create whose obligation names another slice.
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": parent, "plan_hash": h}
    f._journal(g, "slice:wrong", {"argv": ["gh", "issue", "create"], "obligation": dict(ob, slice=2)},
               json.dumps({"url": "u", "number": 41, "id": 1041}), cls="issue_create")
    with pytest.raises(NotAuthorized, match="obligation"):
        f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": "slice:wrong"})
    # The wrong role.
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_slice_filed", {"slice": 1, "effect_key": "slice:wrong"})
    # An intended (unconfirmed) create -- LAST, and under a dispatch taken
    # BEFORE it is journalled: an intended row is a pending effect, and
    # `dispatch` halts the run and refuses over one (PendingEffects,
    # dispatch.py:105). Every dispatch this test makes therefore precedes it.
    g2 = f._dispatch(Role.OPERATOR, "coordinator")
    f._journal(g2, "slice:intended", {"argv": ["gh", "issue", "create"], "obligation": ob}, None,
               cls="issue_create", state="intended")
    with pytest.raises(NotAuthorized, match="not a satisfied issue_create"):
        f._cmd(g2, "record_slice_filed", {"slice": 1, "effect_key": "slice:intended"})


# -- record_filing_complete ----------------------------------------------------

def test_filing_complete_needs_every_obligation(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_slice(g, 1, 40, 1040); f.file_slice(g, 2, 41, 1041)
    h = s.phase_artifact(f.run_id, "slices")
    with pytest.raises(NotAuthorized, match="slice_dependency"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "slice_dependency", plan_hash=h, slice=2, blocker=1))
    with pytest.raises(NotAuthorized, match="slice_queue"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "slice_queue", plan_hash=h, slice=1)); f.satisfy(g, _ob(f, "slice_queue", plan_hash=h, slice=2))
    f.satisfy(g, _ob(f, "umbrella", plan_hash=h), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-1")
    with pytest.raises(NotAuthorized, match="umbrella_label"):
        f._cmd(g, "record_filing_complete", {})
    f.satisfy(g, _ob(f, "umbrella_label", plan_hash=h))
    f._cmd(g, "record_filing_complete", {})
    fc = front.filing_complete(s, f.run_id, 0)
    assert fc.payload == {"epoch": 0, "plan_hash": h, "children": [40, 41]}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_filing_complete", {})
    assert s.run_state(f.run_id) == "sliced"


def test_filing_complete_needs_every_slice_filed(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_slice(g, 1, 40, 1040)
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_filing_complete", {})


# -- closures ------------------------------------------------------------------

def test_closures_are_observations_with_an_opposite(tmp_path):
    s, f, g = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="filing_complete"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "2026-09-10T00:00:00Z", "state": "merged"})
    f.file_all(g)
    with pytest.raises(NotAuthorized, match="no slice_filed"):
        f._cmd(g, "record_slice_closed", {"slice": 9, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="merged or closed"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "done"})
    with pytest.raises(NotAuthorized, match="never closed"):
        f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t"})
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t1", "state": "merged"})
    c = front.current_closure(s, f.run_id, 0, 1)
    assert c.kind == EventKind.SLICE_CLOSED and c.payload == {"epoch": 0, "slice": 1, "issue": 40, "closed_at": "t1", "state": "merged"}
    with pytest.raises(NotAuthorized, match="currently closed"):
        f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t2", "state": "merged"})
    f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t3"})
    assert front.current_closure(s, f.run_id, 0, 1).kind == EventKind.SLICE_REOPENED
    with pytest.raises(NotAuthorized, match="never closed|not currently closed"):
        f._cmd(g, "record_slice_reopened", {"slice": 1, "observed_at": "t4"})
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t5", "state": "closed"})   # accepted again
    assert front.current_closure(s, f.run_id, 0, 1).payload["state"] == "closed"


def test_children_observed_closed(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_all(g)
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="open or closed"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "shut", "observed_at": "t"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    obs = front.observed_closed_under(s, f.run_id, 0, g)
    assert obs.payload == {"epoch": 0, "generation": g, "slices": [1, 2], "parent": "open", "observed_at": "t"}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})


# -- the outcome ---------------------------------------------------------------

def _close_all(f, g):
    for n in (1, 2):
        f._cmd(g, "record_slice_closed", {"slice": n, "closed_at": "t", "state": "merged"})


def test_only_sliced_leaves_sliced(tmp_path):
    s, f, g = _sliced(tmp_path)
    for outcome in ("failed", "merged", "escalated"):
        with pytest.raises(NotAuthorized, match="ends only as sliced"):
            f._cmd(g, "record_run_outcome", {"outcome": outcome})
    with pytest.raises(NotAuthorized, match="filing_complete"):
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    assert s.run_state(f.run_id) == "sliced"


def test_failed_is_still_legal_from_a_shaping_state(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1", shape=False)
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "record_run_outcome", {"outcome": "failed"})
    assert s.run_state("r-1") == "ended"


def test_sliced_outcome_guard(tmp_path):
    s, f, g = _sliced(tmp_path)
    f.file_all(g)
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    with pytest.raises(NotAuthorized, match="slice 2"):
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "merged"})
    f._cmd(g, "record_slice_reopened", {"slice": 2, "observed_at": "t"})
    with pytest.raises(NotAuthorized, match="slice 2"):                         # reopened after closing
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "closed"})
    with pytest.raises(NotAuthorized, match="children_observed_closed"):        # no observation
        f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    g2 = f._dispatch(Role.OPERATOR, "coordinator")                            # a later firing
    with pytest.raises(NotAuthorized, match="under this generation"):          # the earlier one's does not count
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f._cmd(g2, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    with pytest.raises(NotAuthorized, match="umbrella_close"):
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f.satisfy(g2, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    with pytest.raises(NotAuthorized, match="parent_close"):                  # open parent, no close
        f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    f.satisfy(g2, _ob(f, "parent_close"), value="closed")
    f._cmd(g2, "record_run_outcome", {"outcome": "sliced"})
    assert s.run_state(f.run_id) == "ended"
    assert s.newest_fact(f.run_id, EventKind.TRANSITION).payload == {"to": "ended", "via": "record_run_outcome"}


def test_a_hand_closed_parent_ends_without_a_close_and_a_reopened_one_with_its_old_close(tmp_path):
    s, f, g = _sliced(tmp_path, "a.db")
    f.file_all(g); _close_all(f, g)
    f._cmd(g, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    f.satisfy(g, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    f._cmd(g, "record_run_outcome", {"outcome": "sliced"})                    # no parent_close row
    assert s.run_state(f.run_id) == "ended"
    s2, f2, g2 = _sliced(tmp_path, "b.db")
    f2.file_all(g2); _close_all(f2, g2)
    f2.satisfy(g2, _ob(f2, "parent_close"), value="closed")                   # the pipeline closed it ...
    f2.satisfy(g2, _ob(f2, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    g3 = f2._dispatch(Role.OPERATOR, "coordinator")
    f2._cmd(g3, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})   # ... and a person reopened it
    f2._cmd(g3, "record_run_outcome", {"outcome": "sliced"})                  # ruling 22: the person's
    assert s2.run_state(f2.run_id) == "ended"


# -- the operator role ---------------------------------------------------------

def test_every_filing_command_needs_the_operator_role(tmp_path):
    """Each of the five checks its OWN role (shaping spec §2: every one of them
    is legal `under the coordinator's operator dispatch`).

    At each point below the command would otherwise be accepted, so deleting
    that branch's `_check_operator` turns a refusal into an accepted fact --
    the exception is `record_run_outcome {sliced}`, whose guard also wants a
    `children_observed_closed` under the CALLING generation and an author's
    generation can never hold one, recording it being operator-only itself.
    """
    s, f, g = _sliced(tmp_path)
    # record_filing_complete: everything filed and satisfied, nothing recorded.
    for n, issue in ((1, 40), (2, 41)):
        f.file_slice(g, n, issue, 1000 + issue)
    for ob in front.filing_obligations(s, f.run_id, 0):
        if ob["kind"] != "slice_issue":
            f.satisfy(g, ob, cls="comment" if ob["kind"] == "umbrella" else "issue_or_label",
                      value="https://github.com/o/r/issues/1#issuecomment-1" if ob["kind"] == "umbrella" else "ok")
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_filing_complete", {})
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "record_filing_complete", {})
    # record_slice_closed: slice 1 filed, no closure yet.
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "record_slice_closed", {"slice": 1, "closed_at": "t", "state": "merged"})
    # record_slice_reopened: slice 1's current closure is slice_closed.
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_slice_reopened", {"slice": 1, "observed_at": "t"})
    # record_children_observed_closed: both children closed, none observed
    # under the author's own generation.
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "record_slice_closed", {"slice": 2, "closed_at": "t", "state": "merged"})
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    # record_run_outcome {sliced}: everything else the guard asks for is here.
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f._cmd(g, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    f.satisfy(g, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    a = f._dispatch(Role.AUTHOR, "claude")
    with pytest.raises(NotAuthorized, match="operator role"):
        f._cmd(a, "record_run_outcome", {"outcome": "sliced"})
    assert s.run_state(f.run_id) == "sliced"


# -- sliced once ---------------------------------------------------------------

def _mint_again(s, run_id="i12-epic-2"):
    create_run(s, run_id=run_id, base_repo="o/r", base_sha="0" * 40, issue=ISSUE, project_config={})


def test_create_run_refuses_after_a_create_attempt_whatever_the_state(tmp_path):
    # Ended sliced.
    s, f, g = _sliced(tmp_path, "ended.db")
    f.file_all(g); _close_all(f, g)
    f._cmd(g, "record_children_observed_closed", {"parent_state": "closed", "observed_at": "t"})
    f.satisfy(g, _ob(f, "umbrella_close"), cls="comment", value="https://github.com/o/r/issues/12#issuecomment-9")
    f._cmd(g, "record_run_outcome", {"outcome": "sliced"})
    # The run AND ITS ROWS, each with the state it is in (spec §2 *An issue is
    # sliced once*): a person told only that some earlier run attempted a
    # create still has to go and find which children exist, which is the very
    # question the refusal asks them to answer.
    with pytest.raises(IssueAlreadySliced,
                       match=r"run i12-epic-1 .* rows slice:i12-epic-1:1:\d+ \[confirmed\], "
                             r"slice:i12-epic-1:2:\d+ \[confirmed\]\."):
        _mint_again(s)
    # Cancelled after a confirmed create.
    s, f, g = _sliced(tmp_path, "cancelled.db")
    f.file_slice(g, 1, 40, 1040)
    f.human("cancel_run", {})
    assert s.run_state(f.run_id) == "cancelled"
    with pytest.raises(IssueAlreadySliced, match=r"rows slice:i12-epic-1:1:\d+ \[confirmed\]\."):
        _mint_again(s)
    # Halted on an uncertain create.
    s, f, g = _sliced(tmp_path, "halted.db")
    f._journal(g, "slice:u", {"argv": ["gh", "issue", "create"], "obligation": _ob(f, "slice_issue", slice=1, parent=12,
                                                                                   plan_hash=s.phase_artifact(f.run_id, "slices"))},
               None, cls="issue_create", state="uncertain")
    with pytest.raises(IssueAlreadySliced, match=r"rows slice:u \[uncertain\]\."):
        _mint_again(s)


def test_create_run_admits_a_run_cancelled_before_any_create_and_other_outcomes(tmp_path):
    s, f, g = _sliced(tmp_path, "cancelled-early.db")
    f.human("cancel_run", {})
    _mint_again(s)
    assert s.run_state("i12-epic-2") == "shaping"
    s2 = Store.open(tmp_path / "reconciled.db")
    f2 = Front(s2, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g2 = f2._dispatch(Role.OPERATOR, "coordinator")
    f2._journal(g2, "slice:nd", {"argv": ["gh", "issue", "create"], "obligation": {}}, None,
                cls="issue_create", state="reconciled")           # not delivered: no child exists
    f2.human("cancel_run", {})
    _mint_again(s2)
    s3 = Store.open(tmp_path / "failed.db")
    f3 = Front(s3, "i12-epic-1", shape=False, issue=ISSUE)
    f3._cmd(f3._dispatch(Role.OPERATOR, "runner"), "record_run_outcome", {"outcome": "failed"})
    _mint_again(s3)


def test_revise_bundle_is_refused_from_sliced(tmp_path):
    s, f, g = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="not legal from state 'sliced'"):
        f.revise(dict(ISSUE, body="changed"), reshape=False)
