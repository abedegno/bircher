"""Task 11: the four slice assertions and the amended existing checks
(shaping spec §6). Task 10 restates assertion 4 in three clauses over
visits, amends the phase-set and approval checks, and adds the fifth line,
`assert_dispute` -- all of it revision 16 (the hand-back)."""
import json

import pytest

from coordinator.sweep import ReadFailed
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.policy import policy_of
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front
from tests.kernel.test_prove_front_half import _accept, _store_with_confirmed_stops
from tools import prove_front_half as prove

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}
REPO = "o/r"


def _no_network(monkeypatch):
    """`main()` calls `assert_sessions` with the real `_fetch` (a `curl`
    subprocess) whenever a test doesn't override it -- and there is no seam
    to inject a fake fetch through `main`'s argv. These tests exercise
    `--repo`/`--children`, not the session-fetch path, so make every
    subprocess call fail fast and deterministically instead of touching the
    network; `assert_sessions` already turns that into an ordinary failure
    line (`except Exception`), which is harmless noise for what these tests
    check."""
    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "network disabled in tests"
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Failed())


class GH:
    """What GitHub holds after a correct filing of *f*: built from the
    journal, then edited by a test to plant a defect."""
    def __init__(self, s, f):
        n = f.epoch()
        plan = front.accepted_plan(s, f.run_id, n)
        h = front.accepted_slices_hash(s, f.run_id, n)
        numbers = {k: x.payload["issue"] for k, x in front.slices_filed(s, f.run_id, n).items()}
        self.issues = {}
        for sl in plan.slices:
            t, b = slices.render_child(sl, 12, h[:8], {d: numbers[d] for d in sl.depends_on})
            self.issues[numbers[sl.number]] = {"title": t, "body": b, "blocked_by": [{"number": numbers[d]} for d in sl.depends_on]}
        self.issues[12] = {"comments": [{"body": slices.umbrella_body(h[:8], plan, numbers)}]}

    def __call__(self, argv):
        # Index 4, not 3, on the `gh api` path: `repos/<owner>/<repo>/issues/<n>`
        # splits to ["repos", owner, repo, "issues", n] -- index 3 is the
        # literal "issues" (controller ruling; mirrors
        # tests/coordinator/fake_omnigent.py's `_gh`).
        n = int(argv[3]) if argv[1] == "issue" else int(argv[2].split("/")[4])
        if argv[1] == "api":
            return self.issues[n]["blocked_by"]
        fields = argv[argv.index("--json") + 1].split(",")
        return {k: self.issues[n].get(k) for k in fields}


def _to_sliced(f: Front) -> Front:
    """`Front.to_sliced`, except the review round's findings actually name
    the brief's hash8 (`_accept`'s reason to exist, test_prove_front_half.py
    docstring) -- `to_sliced` has no hook to pass that through, and the
    driver's placeholder findings (`b"ok"`) trip the verdict-line check
    that a fully clean `assert_journal` result needs."""
    from kernel.policy import policy_of
    assert f.state() == "shaping", f.state()
    f.shape_round(SLICES_BYTES)
    _accept(f)
    if "slices" in policy_of(f.store, f.run_id).gates:
        f.approve()
    else:
        f.advance()
    assert f.state() == "sliced", f.state()
    return f


def _parent(tmp_path, name="k.db"):
    # `_store_with_confirmed_stops`, not a plain `Store.open`: the first
    # test below asks `assert_journal` for a fully empty result, which
    # needs the `effect_confirmed` fact a real stop carries -- see that
    # helper's docstring in test_prove_front_half.py. `Front._journal`
    # never appends it on its own.
    s = _store_with_confirmed_stops(tmp_path / name)
    f = _to_sliced(Front(s, "i12-epic-1", shape=False, issue=ISSUE))
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f.file_all(g)
    return s, f


def test_a_sliced_parent_passes_the_journal_checks_and_the_four_assertions(tmp_path):
    s, f = _parent(tmp_path)
    assert prove.assert_journal(s, f.run_id, mode="zero") == []
    assert prove.assert_slices(s, f.run_id, repo="o/r", gh_json=GH(s, f)) == []


def test_assertion_1_reds_on_a_wrong_body_a_duplicate_create_and_an_unrecorded_create(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[41]["body"] = gh.issues[40]["body"]                               # every child the first slice's scope
    assert any("body" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    # fix round 1, IMPORTANT 1: the case above binds only through the marker
    # line -- swapping slice 1's whole body into slice 2 also swaps the
    # numbered "Slice N of #parent" line, so a marker-only comparison would
    # still catch it (see the mutation below). A body that keeps the real
    # marker and title but has one line appended isolates the full-content
    # comparison.
    gh2 = GH(s, f)
    gh2.issues[41]["body"] = gh2.issues[41]["body"] + "an extra line, appended\n"
    assert any("body" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh2))
    s2, f2 = _parent(tmp_path, "dup.db")
    g = f2._dispatch(Role.OPERATOR, "coordinator")
    h = front.accepted_slices_hash(s2, f2.run_id, 0)
    f2._journal(g, "slice:dup", {"argv": ["gh", "issue", "create"], "obligation": {"kind": "slice_issue", "run": f2.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": h}},
                json.dumps({"url": "u", "number": 77, "id": 1077}), cls="issue_create")   # a second delivered create for slice 1
    assert any("delivered creates" in x for x in prove.assert_slices(s2, f2.run_id, repo="o/r", gh_json=GH(s2, f2)))
    # fix round 1, MINOR 1: the "unrecorded create" branch (`for k in per: if
    # k not in filed`) was untested -- a delivered create for a slice number
    # that was never filed at all (recorded the way the fixture records a
    # delivered create, without the paired `record_slice_filed`).
    s3, f3 = _parent(tmp_path, "unrec.db")
    g3 = f3._dispatch(Role.OPERATOR, "coordinator")
    h3 = front.accepted_slices_hash(s3, f3.run_id, 0)
    f3._journal(g3, "slice:phantom", {"argv": ["gh", "issue", "create"], "obligation": {"kind": "slice_issue", "run": f3.run_id, "epoch": 0, "slice": 99, "parent": 12, "plan_hash": h3}},
                json.dumps({"url": "u", "number": 88, "id": 1088}), cls="issue_create")   # never filed
    assert any("has no slice_filed" in x for x in prove.assert_slices(s3, f3.run_id, repo="o/r", gh_json=GH(s3, f3)))


def test_assertion_2_reds_on_a_missing_and_an_extra_link(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[41]["blocked_by"] = []
    assert any("link" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    gh = GH(s, f)
    gh.issues[40]["blocked_by"] = [{"number": 41}]
    assert any("link" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))


def test_assertion_3_reds_on_two_umbrellas_and_on_a_wrong_list(tmp_path):
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[12]["comments"].append(dict(gh.issues[12]["comments"][0]))
    assert any("umbrella" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))
    gh = GH(s, f)
    gh.issues[12]["comments"][0]["body"] = gh.issues[12]["comments"][0]["body"].replace("#41", "#99")
    assert any("umbrella" in x for x in prove.assert_slices(s, f.run_id, repo="o/r", gh_json=gh))


def test_assertion_4_passes_a_ruling_after_a_rejected_plan_and_a_superseded_epoch_and_reds_on_a_planted_ruling(tmp_path):
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES); f.review_round("request_revision", findings=b"one piece"); f.shape_round()
    f.author_round(SPEC_BYTES); f.review_round("accept"); f.author_round(PLAN_BYTES); f.review_round("accept")
    assert not [x for x in prove.assert_slices(s, "r-1", repo="o/r", gh_json=None) if "epoch" in x or "decision" in x]
    s2 = Store.open(tmp_path / "b.db")
    f2 = Front(s2, "r-1", shape=False, issue=ISSUE)
    f2.shape_round(SLICES_BYTES)                                                # a plan waits ...
    f2.revise(dict(ISSUE, body="changed"), reshape=False)                       # ... and the issue changes: epoch 1
    f2.to_sliced()
    f2.file_all(f2._dispatch(Role.OPERATOR, "coordinator"))
    assert prove.assert_slices(s2, "r-1", repo="o/r", gh_json=GH(s2, f2)) == []
    # A shape ruling planted beside the filing (the kernel refuses this path; the proof must still see it).
    s2.append_fact(run_id="r-1", kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                   payload={"epoch": 1, "question_id": "shape", "ruling": "one piece", "reasoning": "r", "cost_if_wrong": "c"})
    assert any("decision" in x for x in prove.assert_slices(s2, "r-1", repo="o/r", gh_json=GH(s2, f2)))


def test_the_amended_journal_checks(tmp_path):
    # A one-piece run whose only ruling is the shape one fails the ruling check, as a run with none does.
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE); f.author_round(SPEC_BYTES); f.review_round("accept"); f.author_round(PLAN_BYTES); f.review_round("accept")
    assert any("model_ruling" in x for x in prove.assert_journal(s, "r-1", mode="zero"))
    # {slices, spec, plan} in the final epoch passes the phase-set check; {spec} alone fails it.
    s2 = Store.open(tmp_path / "b.db")
    f2 = Front(s2, "r-1", shape=False, issue=ISSUE)
    f2.shape_round(SLICES_BYTES); f2.review_round("request_revision", findings=b"no"); f2.shape_round()
    f2.ask_round([("Q1", "?")], {"Q1": "yes"}); f2.author_round(SPEC_BYTES); f2.review_round("accept")
    f2.author_round(PLAN_BYTES); f2.review_round("accept")
    assert not [x for x in prove.assert_journal(s2, "r-1", mode="zero") if "artifact_submitted" in x]
    s3 = Store.open(tmp_path / "c.db")
    f3 = Front(s3, "r-1", issue=ISSUE); f3.ask_round([("Q1", "?")], {"Q1": "yes"}); f3.author_round(SPEC_BYTES)
    assert any("artifact_submitted" in x for x in prove.assert_journal(s3, "r-1", mode="zero"))
    # A spec in epoch 1, then sliced in epoch 2, passes; a final epoch holding {slices, spec} fails.
    s4 = Store.open(tmp_path / "d.db")
    f4 = Front(s4, "r-1", issue=ISSUE); f4.author_round(SPEC_BYTES)
    f4.revise(dict(ISSUE, body="changed"), reshape=False); f4.to_sliced(); f4.file_all(f4._dispatch(Role.OPERATOR, "coordinator"))
    assert not [x for x in prove.assert_journal(s4, "r-1", mode="zero") if "artifact_submitted" in x]
    s4.append_fact(run_id="r-1", kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                   payload={"phase": "spec", "epoch": 1, "hash": "x", "author": "claude", "round": 1})
    assert any("artifact_submitted" in x for x in prove.assert_journal(s4, "r-1", mode="zero"))


def test_the_verdict_binding_block_re_renders_a_slices_brief(tmp_path):
    from tests.kernel.test_prove_front_half import _review_round_with_real_brief
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES)
    _review_round_with_real_brief(f)
    f.advance(); f.file_all(f._dispatch(Role.OPERATOR, "coordinator"))
    assert not [x for x in prove.assert_journal(s, "r-1", mode="zero") if "brief" in x]
    b = s.newest_fact("r-1", EventKind.REVIEW_BRIEF_ISSUED)
    # `Store.put_blob` is `INSERT OR IGNORE` keyed on the content hash
    # (kernel/store.py, kernel/schema.sql -- `hash TEXT PRIMARY KEY`): the
    # brief_hash slot is already occupied by the real rendered bytes, so
    # writing different bytes under that same key is silently a no-op and
    # the bytes never move. Plant the defect the way
    # `test_each_assertion_fails_on_its_defect` plants its forged facts
    # instead: a second review_brief_issued naming a genuinely different
    # blob under its own generation, paired with a review_verdict of that
    # generation so the verdict-binding loop actually reaches it.
    bad_hash = put_artifact(s, b"not the brief")
    fake_gen = 999
    s.append_fact(run_id="r-1", kind=EventKind.REVIEW_BRIEF_ISSUED, actor="claude", causal_command_id=None,
                  payload=dict(b.payload, generation=fake_gen, brief_hash=bad_hash))
    s.append_fact(run_id="r-1", kind=EventKind.REVIEW_VERDICT, actor="codex", causal_command_id=None,
                  payload={"verdict": "accept", "phase": b.payload["phase"], "epoch": b.payload["epoch"],
                           "artifact_hash": b.payload["artifact_hash"], "findings_hash": None,
                           "ruling": "review_ruling", "binding_hash": None, "reviewer_identity": "codex",
                           "generation": fake_gen})
    assert any("render()" in x for x in prove.assert_journal(s, "r-1", mode="zero"))


# -- fix round 1 -------------------------------------------------------------


def test_a_failed_read_becomes_a_failure_line_and_the_other_assertions_still_run(tmp_path):
    """CRITICAL: a `ReadFailed` from one child's read must not crash
    `assert_slices` -- it becomes a failure line naming the child and what
    could not be read, and every other check still runs (no spurious
    mismatch is ALSO reported for the same read)."""
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    real_call = gh.__call__

    def flaky(argv):
        if argv[1] == "issue" and argv[2] == "view" and argv[3] == "41" and "title,body" in argv:
            raise ReadFailed("boom")
        return real_call(argv)

    fails = prove.assert_slices(s, f.run_id, repo="o/r", gh_json=flaky)
    assert fails == ["slice 2 (#41): could not read title,body: boom"]


def test_main_children_without_repo_refuses_with_a_usage_message(tmp_path, monkeypatch, capsys):
    """IMPORTANT 2(a)."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x", "--children"])
    assert rc == 2
    assert "--children needs --repo" in capsys.readouterr().err


def test_main_children_reports_on_the_newest_run_for_a_child(tmp_path, monkeypatch, capsys):
    """IMPORTANT 2(b): a child issue with two runs -- the older carries a
    human fact (fails zero-touch), the newer carries none. `main --children`
    must report on the newest (`kids[-1]`, `store.all_run_ids()` being
    stable oldest-first) and never mention the older run at all."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    monkeypatch.setattr("coordinator.sweep.gh_json", GH(s, f))
    child_issue = {"number": 41, "title": "The API", "body": "b", "labels": ["bircher:autonomous"], "comments": []}
    Front(s, "i41-x-1", issue=child_issue).direct("noted")   # older, and touched
    Front(s, "i41-x-2", issue=child_issue)                    # newer, untouched
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", "o/r", "--children"])
    out = capsys.readouterr()
    assert "i41-x-1" not in out.err
    lines41 = [ln for ln in out.err.splitlines() if ln.startswith("child #41")]
    assert lines41 and all("i41-x-2" in ln for ln in lines41)


def test_main_children_continues_past_a_failed_read(tmp_path, monkeypatch, capsys):
    """IMPORTANT 2(c): one child's read raises `ReadFailed` -- the exit is
    non-zero, that child's failure line is in the output, and a DIFFERENT
    child's own (unrelated) planted defect still shows up too, proving the
    loop and the remaining assertions kept running."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    gh = GH(s, f)
    gh.issues[41]["blocked_by"] = []          # slice 2's own, separate defect: a missing link
    real_call = gh.__call__

    def flaky(argv):
        if argv[1] == "issue" and argv[2] == "view" and argv[3] == "40" and "title,body" in argv:
            raise ReadFailed("boom")
        return real_call(argv)

    monkeypatch.setattr("coordinator.sweep.gh_json", flaky)
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", "o/r", "--children"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("slice 1 (#40): could not read title,body: boom" in ln for ln in out.err.splitlines())
    assert any("link" in ln for ln in out.err.splitlines())


def test_assertion_4_the_final_epoch_check_reds_alone_on_an_undecided_run(tmp_path):
    """MINOR 2: isolate the final "last visit's decision" check (clause (c),
    restated over visits by revision 16) from the per-visit "one decision
    per visit" guard (clause (b)), which structurally cannot fire here -- it
    only ever activates once a ruling exists in some epoch, and this run has
    ruled on nothing.

    The two shapes fix round 1 suggested for this -- a run that WAS sliced
    and filed, then revised past that decision, still undecided at the new
    final epoch; a shape ruling at the final epoch with the filing in an
    earlier one -- are NOT constructible through the driver: `revise_bundle`
    is authorized only from {queued, spec_submitted, spec_accepted,
    specified, plan_submitted, plan_accepted, shaping, slices_submitted,
    slices_accepted} (kernel/authz.py), and `sliced` -- the state a run is
    in once it has actually filed children -- is not in that set. Confirmed
    empirically: calling `Front.revise` on a `_parent(tmp_path)` fixture
    raises `NotAuthorized: revise_bundle is not legal from state 'sliced'`.
    Once a parent has filed children, the kernel gives it no legal way back
    to an undecided epoch, so a "was sliced, now undecided" or "ruled now,
    filed earlier" journal cannot be built without forging facts the kernel
    would refuse -- which the existing planted-ruling test already does for
    the "both present" shape, and doing so again here would not prove
    anything the "one decision" table doesn't already cover:
      - ruling only  (no filing)   -- the first half of
        test_assertion_4_..._reds_on_a_planted_ruling (a one-piece run)
      - filing only  (no ruling)   -- test_a_sliced_parent_passes_...
      - both         (kernel-refused) -- the same test's forged-ruling tail
      - neither      (this test)   -- reachable legitimately: a run that
        has simply never decided anything yet."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    assert f.state() == "shaping"
    fails = prove.assert_slices(s, "r-1", repo="o/r", gh_json=None)
    assert any("the last visit's decision" in x for x in fails)
    assert not any("one decision per visit" in x for x in fails)


# -- Task 10: assertion 4 over visits, the phase-set and approval amendments,
#    and the fifth line, `assert_dispute` (shaping spec §6, revision 16) ----


class _Planted:
    """The shared history builders §8 asks for: each returns `(store,
    run_id)` after driving the hand-back mechanics real commands construct,
    or -- where the kernel's own guards make a history unreachable any other
    way, exactly as `test_assertion_4_..._reds_on_a_planted_ruling` above
    already does for the pre-visit code -- forging the one fact the kernel
    would have refused, directly into the store.

    `self.gh` is the sweep's fake `gh_json` reader (this file's `GH`) for
    whichever run last filed anything; a builder that never slices leaves it
    at `None`, which is fine -- assertion 4 alone runs without it for a run
    that never filed (`assert_slices`'s own `if plan is not None and
    filed:` guard), and none of these builders that skip filing are ever
    checked through anything else."""

    def __init__(self, tmp_path):
        self._tmp_path = tmp_path
        self._n = 0
        self.gh = None

    def _db(self) -> Store:
        self._n += 1
        return _store_with_confirmed_stops(self._tmp_path / f"planted-{self._n}.db")

    def _advance_or_approve(self, s: Store, f: Front) -> None:
        if "slices" in policy_of(s, f.run_id).gates:
            f.approve()
        else:
            f.advance()

    def _file(self, s: Store, f: Front) -> None:
        g = f._dispatch(Role.OPERATOR, "coordinator")
        f.file_all(g)
        self.gh = GH(s, f)

    # -- assertion 4 ----------------------------------------------------

    def hand_back_then_sliced(self):
        """Visit 1: the one-piece ruling every hand-back needs to exist
        (`request_reshape`'s own guard). Visit 2, the hand-back's own: no
        ruling at all -- the shaper reconsiders and slices directly. The
        plain answered-hand-back history: clause (c) and the fifth line
        both pass it."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("the issue names a store, an API and a page")
        f.shape_round(SLICES_BYTES)
        _accept(f)
        self._advance_or_approve(s, f)
        self._file(s, f)
        return s, f.run_id

    def rejected_then_ruled(self):
        """A plan rejected, then ruled, in the SAME visit -- no hand-back
        needed at all for clause (b)'s "rejected submissions before a
        ruling are admitted": visit 1 holds both, in order."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE, shape=False)
        f.shape_round(SLICES_BYTES)
        f.review_round("request_revision", findings=b"one piece")
        f.shape_round()
        return s, f.run_id

    def rejected_then_resubmitted_same_hash_and_accepted(self):
        """The exact case clause (b) exists to get right and hash equality
        alone gets wrong: visit 1 submits a plan, it is REJECTED, and the
        shaper rules one piece over it (so a hand-back has something to
        answer). Visit 2 (the hand-back's own) resubmits the IDENTICAL
        bytes -- legal, since `_check_submit`'s dedupe is per visit, not per
        epoch -- and it is accepted and filed. Both visits now hold a
        submission of the SAME hash; only visit 2's carries an `accept`
        verdict in ITS OWN visit. A hash-only reader would find the hash in
        visit 1 too, beside a ruling, and misreport a collision that never
        happened."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE, shape=False)
        f.shape_round(SLICES_BYTES)
        f.review_round("request_revision", findings=b"reconsider the split")
        f.shape_round()                      # visit 1's ruling, over the rejected plan
        f.request_reshape("the split still looks right; asking again")
        f.shape_round(SLICES_BYTES)           # visit 2: the SAME bytes, a different visit
        _accept(f)
        self._advance_or_approve(s, f)
        self._file(s, f)
        return s, f.run_id

    def submission_after_ruling_same_visit(self):
        """The kernel refuses this at the door: `submit_slices` at `shaping`
        is refused while `front.unresolved_disagreement` holds
        (kernel/authz.py's `authorize`, "a third seat cannot slice it
        away"), and a NON-disputed ruling leaves `shaping` outright -- so
        there is no state a real command could record a slice plan from,
        after a ruling in its own visit. Forged directly, as the existing
        per-epoch test above already forges a beside-the-filing ruling."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")  # visit 2, disputed
        n = f.epoch()
        v = front.shaping_visit(s, f.run_id, n)
        h = put_artifact(s, SLICES_BYTES)
        s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                      payload={"phase": "slices", "epoch": n, "hash": h, "author": "claude", "round": 1, "visit": v})
        return s, f.run_id

    def ruled_then_directed_then_sliced(self):
        """Visit 1: the initial ruling. Visit 2 (the hand-back's own): a
        second ruling -- disputed. A direction resolves it and opens visit
        3, whose shaper slices instead of ruling again. The ruling in an
        earlier visit, the filing in the last: clause (c) says this is
        legal, and it is the specific case `visit=last` exists to get
        right (an epoch-wide read would find visit 2's ruling and call it
        the decision, though visit 3 -- the last -- decided by filing)."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.direct("slice it along the three parts")
        f.shape_round(SLICES_BYTES)
        _accept(f)
        self._advance_or_approve(s, f)
        self._file(s, f)
        return s, f.run_id

    def filed_then_ruled(self):
        """The reverse of the history above, and NOT reachable any other
        way: once a parent has filed children the kernel has no legal path
        back to an undecided epoch (see
        `test_assertion_4_the_final_epoch_check_reds_alone_on_an_undecided_run`'s
        docstring above, which hit the same wall). A shape ruling forged
        after the filing, in the filing's own (and only) visit, is a
        journal no real command sequence can write -- the last visit's
        decision cannot be both a filing and a ruling -- and clause (c) is
        exactly what notices."""
        s, run_id = self.hand_back_then_sliced()
        f = Front(s, run_id, existing=True)
        n = f.epoch()
        v = front.shaping_visit(s, run_id, n)
        s.append_fact(run_id=run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                      payload={"epoch": n, "question_id": "shape", "ruling": "one piece",
                               "reasoning": "forged after the filing", "cost_if_wrong": "n/a", "visit": v})
        return s, run_id

    # -- the phase-set amendment -----------------------------------------

    def revision_turn_hand_back_then_sliced(self):
        """A hand-back from a REVISION turn: the epoch's spec was already
        submitted once, rejected, and the spec author's SECOND turn hands
        back instead of resubmitting. The phase-set amendment admits the
        spec submission that precedes the hand-back."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.author_round(SPEC_BYTES)
        _accept(f, "request_revision")
        f.request_reshape("the issue names a store, an API and a page")
        f.shape_round(SLICES_BYTES)
        _accept(f)
        self._advance_or_approve(s, f)
        self._file(s, f)
        return s, f.run_id

    def spec_after_the_hand_back_then_sliced(self):
        """The mirror case the amendment must still refuse: a `spec`
        submission forged AFTER the hand-back. The kernel never reaches
        `shaping` (where the forged fact's epoch and phase would need to
        come from a submit_spec) with a hand-back already standing and a
        spec still to submit, so this is forged directly onto an otherwise
        clean answered hand-back."""
        s, run_id = self.hand_back_then_sliced()
        hb = s.facts_of_kind(run_id, EventKind.RESHAPE_REQUESTED)[-1]
        h = put_artifact(s, SPEC_BYTES)
        s.append_fact(run_id=run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                      payload={"phase": "spec", "epoch": hb.payload["epoch"], "hash": h,
                               "author": "claude", "round": 1})
        return s, run_id

    # -- the approval-filter amendment ------------------------------------

    def disagreed_then_approved_then_merged(self):
        """A hand-back, a disputed ruling, the coordinator's own park
        (`reason: disagreement`), and the person's `approve_one_piece` --
        then an ordinary run to completion. Approval mode's two new
        admissions together, with nothing else to trip on."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.park(reason="disagreement")
        f.approve_one_piece()
        f.ask_round([("q1", "?")], rulings={"q1": "yes"})   # the model_ruling assert_journal needs
        f.author_round(SPEC_BYTES)
        _accept(f)
        if f.state() == "spec_accepted":
            f.approve()
        f.author_round(PLAN_BYTES)
        _accept(f)
        if f.state() == "plan_accepted":
            f.approve()
        return s, f.run_id

    def disagreed_then_directed_then_merged(self):
        """The other resolution: a direction, not an approval. Still a
        human fact approval mode must refuse -- it is legal at `shaping` as
        the OTHER way to resolve a disagreement, but a directed run proves
        only under `--expect-human`, like every other directed run."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.park(reason="disagreement")
        f.direct("slice it along the three parts")
        f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
        f.ask_round([("q1", "?")], rulings={"q1": "yes"})
        f.author_round(SPEC_BYTES)
        _accept(f)
        if f.state() == "spec_accepted":
            f.approve()
        f.author_round(PLAN_BYTES)
        _accept(f)
        if f.state() == "plan_accepted":
            f.approve()
        return s, f.run_id

    # -- the fifth line, assert_dispute ------------------------------------

    def hand_back_then_revise_bundle(self):
        """The hand-back stands unresolved when the issue changes: a new
        epoch supersedes it (`revise_bundle` is legal from `shaping`,
        hand-back or none). The fifth line owes the abandoned epoch
        nothing -- a person's issue edit IS the resolution the old epoch
        never got a chance to reach."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.revise(dict(ISSUE, body="changed"), reshape=False)
        return s, f.run_id

    def hand_back_then_nothing(self):
        """A hand-back the CURRENT epoch still holds, standing over neither
        a ruling nor a filing -- unlike `hand_back_then_revise_bundle`,
        nothing supersedes it, so the fifth line does not skip it: this is
        the plain "still waiting" case, not the superseded one."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        return s, f.run_id

    def two_hand_backs_in_one_epoch(self):
        """One hand-back per bundle is the kernel's own guard
        (`request_reshape`'s "already holds a hand-back" refusal,
        kernel/authz.py) -- a second is forged directly, the same way the
        existing per-epoch test above forges a beside-the-filing ruling."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        n = f.epoch()
        v = front.shaping_visit(s, f.run_id, n)
        s.append_fact(run_id=f.run_id, kind=EventKind.RESHAPE_REQUESTED, actor="claude", causal_command_id=None,
                      payload={"epoch": n, "visit": v + 1, "reasoning": "a second, forged hand-back"})
        return s, f.run_id

    def spec_between_the_disputed_ruling_and_nothing(self):
        """A `spec` forged into the window revision 16 protects: after the
        disputed ruling, before its resolution. The kernel never reaches
        `shaping` -- where the forged fact's phase would have to come from
        a `submit_spec` -- with a disputed ruling standing over it, so this
        is forged too; the direction that follows is real, and is what
        gives the forged fact a resolution to fall BEFORE."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.shape_round(reasoning="still one", cost="the spec would find one surface")   # the disputed ruling
        h = put_artifact(s, SPEC_BYTES)
        n = f.epoch()
        s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                      payload={"phase": "spec", "epoch": n, "hash": h, "author": "claude", "round": 1})
        f.direct("slice it")   # the resolution, recorded after the forged spec
        return s, f.run_id

    def direction_before_the_disputed_ruling(self):
        """A direction typed BEFORE any ruling answers the hand-back
        resolves nothing and opens no visit
        (test_reshape.py's `test_a_direction_before_the_disputed_ruling_opens_no_visit`
        pins this at the kernel level); the ruling that follows is left
        disputed with no resolution after it -- `front.dispute`'s own
        `f.seq > disputed.seq` ordering is what keeps the earlier direction
        from being mistaken for the later ruling's answer."""
        s = self._db()
        f = Front(s, "i12-epic-1", issue=ISSUE)
        f.request_reshape("three parts")
        f.direct("consider the migration too")
        f.shape_round(reasoning="still one", cost="the spec would find one surface")
        return s, f.run_id


@pytest.fixture
def planted(tmp_path):
    return _Planted(tmp_path)


def test_assertion_4_passes_the_hand_back_then_slice_history(planted):
    s, run = planted.hand_back_then_sliced()
    assert prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_passes_a_rejected_plan_then_a_ruling_in_one_visit(planted):
    s, run = planted.rejected_then_ruled()
    assert prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_identifies_the_acceptance_by_the_verdict_it_followed_not_hash_alone(planted):
    """The plan's own scenario (task-10-brief.md step 4): a plan rejected in
    visit 1 and resubmitted, byte for byte, and accepted in visit 2. Both
    visits hold the accepted hash; only visit 2's is the acceptance. A
    hash-only reader would find visit 1's rejected copy too, beside its
    ruling, and misreport "a shape ruling beside the accepted slice plan"
    for a visit that never accepted anything."""
    s, run = planted.rejected_then_resubmitted_same_hash_and_accepted()
    assert prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_reds_on_a_submission_after_a_ruling_in_its_visit(planted):
    s, run = planted.submission_after_ruling_same_visit()
    assert any("one decision per visit" in x for x in prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh))


def test_assertion_4_passes_a_ruling_in_an_earlier_visit_beside_a_filing(planted):
    """The hand-back resolved by slicing: a ruling in an earlier visit, the
    filing in the last."""
    s, run = planted.ruled_then_directed_then_sliced()
    assert prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh) == []


def test_assertion_4_reds_on_a_filing_beside_a_newer_ruling(planted):
    s, run = planted.filed_then_ruled()
    fails = prove.assert_slices(s, run, repo=REPO, gh_json=planted.gh)
    assert any("the last visit's decision" in x for x in fails)


def test_the_phase_set_admits_a_spec_before_the_hand_back(planted):
    s, run = planted.revision_turn_hand_back_then_sliced()
    assert prove.assert_journal(s, run, mode="human") == []


def test_the_phase_set_reds_on_a_spec_after_the_hand_back(planted):
    s, run = planted.spec_after_the_hand_back_then_sliced()
    assert any("artifact_submitted" in x for x in prove.assert_journal(s, run, mode="human"))


def test_approval_mode_admits_the_approval_and_its_park(planted):
    s, run = planted.disagreed_then_approved_then_merged()
    assert prove.assert_journal(s, run, mode="approval") == []
    assert any("human facts present" in x for x in prove.assert_journal(s, run, mode="zero"))


def test_approval_mode_still_refuses_a_direction(planted):
    s, run = planted.disagreed_then_directed_then_merged()
    assert any("beyond the approval" in x for x in prove.assert_journal(s, run, mode="approval"))


def test_the_fifth_line_passes_an_answered_hand_back(planted):
    s, run = planted.hand_back_then_sliced()
    assert prove.assert_dispute(s, run) == []


def test_the_fifth_line_skips_a_superseded_hand_back(planted):
    s, run = planted.hand_back_then_revise_bundle()
    assert prove.assert_dispute(s, run) == []


def test_the_fifth_line_passes_a_disputed_ruling_resolved_by_approval(planted):
    """The other resolution path assert_dispute's docstring names -- "a
    disputed ruling followed by its resolution" -- checked on its own:
    `disagreed_then_approved_then_merged` (built for the approval-mode
    tests above) also answers its hand-back this way, and nothing here has
    asserted that of it directly yet."""
    s, run = planted.disagreed_then_approved_then_merged()
    assert prove.assert_dispute(s, run) == []


def test_the_fifth_line_reds_on_a_hand_back_with_neither_a_ruling_nor_a_filing(planted):
    """The plain unanswered case: a hand-back the CURRENT epoch still holds,
    with nothing after it at all -- not superseded, so the fifth line does
    not skip it."""
    s, run = planted.hand_back_then_nothing()
    assert any("no disputed ruling and no filing after it" in x for x in prove.assert_dispute(s, run))


def test_the_fifth_line_reds_on_a_second_hand_back_in_one_epoch(planted):
    s, run = planted.two_hand_backs_in_one_epoch()
    assert any("a second reshape_requested" in x for x in prove.assert_dispute(s, run))


def test_the_fifth_line_reds_on_a_spec_after_an_unresolved_ruling(planted):
    s, run = planted.spec_between_the_disputed_ruling_and_nothing()
    fails = prove.assert_dispute(s, run)
    assert any("between the disputed ruling and the resolution" in x for x in fails)


def test_the_fifth_line_reds_on_a_direction_before_the_disputed_ruling(planted):
    s, run = planted.direction_before_the_disputed_ruling()
    assert any("unanswered" in x for x in prove.assert_dispute(s, run))


# -- Fix round 1: B1 (out-of-range visit stamps), M1 (clause (c) is an
#    iff, not an XOR), B2 (main's assert_dispute wiring), L1
#    (reshape_requested with no epoch), L4 (exactly one approval per gate) -


def test_assertion_4_reds_on_a_ruling_stamped_outside_the_epochs_visits(tmp_path):
    """Round 1, B1 (generalized in round 2 to a stamp-vs-prefix-walk
    comparison, B3/B4/B5): a fact's own `visit` stamp was invisible to every
    per-visit read once it disagreed with `front.visit_of`'s prefix walk --
    round 1 only checked it fell in `range(1, last+1)`, which an in-range
    but WRONG stamp still passed. Built on the plain sliced-parent fixture
    (no hand-back at all, last visit 1): a shape ruling forged beside the
    accepted, filed plan, stamped one visit PAST the epoch's last -- the
    walk attributes it to visit 1, same as everything else in this epoch.
    Before round 1's fix, `assert_slices`, `assert_dispute` and
    `assert_journal` all read this run as clean (the reviewer's probe G4)."""
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged one past the last visit", "cost_if_wrong": "n/a", "visit": 2})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("prefix walk attributes it to visit" in x for x in fails), fails


def test_assertion_4_the_out_of_range_control_at_the_real_visit(tmp_path):
    """The control for the test above: the SAME forged ruling, stamped at
    the epoch's real (only, and correctly-walked) visit, is caught by the
    pre-existing clause (b) message instead -- proof the new guard is doing
    new work, not the existing check's job."""
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged at the real visit", "cost_if_wrong": "n/a", "visit": 1})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("beside the accepted slice plan" in x for x in fails), fails
    assert not any("prefix walk attributes it to visit" in x for x in fails), fails


def test_assertion_4_reds_on_a_submission_stamped_outside_the_epochs_visits(tmp_path):
    """Round 1, B1's second instance (clause (b), generalized in round 2): a
    one-piece run (last visit 1) with a `slices` submission forged AFTER the
    ruling, stamped a visit far past the last -- the walk attributes it to
    visit 1 like everything else here (the reviewer's probe G2)."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)     # birth: one-piece ruling, visit 1
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 1, "visit": 7})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("prefix walk attributes it to visit" in x for x in fails), fails


def test_assertion_4_the_out_of_range_control_for_a_submission(tmp_path):
    """The control: the identical forged submission at the real (and
    correctly-walked) visit gets the pre-existing "submitted after the shape
    ruling" message (the reviewer's probe G3)."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 1, "visit": 1})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("submitted after the shape ruling" in x for x in fails), fails
    assert not any("prefix walk attributes it to visit" in x for x in fails), fails


def test_assertion_4_clause_c_reds_when_the_last_visit_decided_nothing_but_the_epoch_filed(tmp_path):
    """Round 1, M1: clause (c) implemented "a ruling is absent" (an XOR)
    where §6 asks "that visit's decision IS the accepted submission" (an
    iff). A last visit that decided NOTHING -- opened by a second, forged
    hand-back recorded after the real filing -- with a filing left over
    from an earlier visit must fail; the old code, asking only "is there a
    ruling in the last visit", passed it (the reviewer's probe E16)."""
    s, run = _Planted(tmp_path).hand_back_then_sliced()
    f = Front(s, run, existing=True)
    n = f.epoch()
    v = front.shaping_visit(s, run, n)
    # One hand-back per bundle is the kernel's own guard; a second is
    # forged, exactly as the fifth-line tests above forge one to open a
    # boundary no real command could open here.
    s.append_fact(run_id=run, kind=EventKind.RESHAPE_REQUESTED, actor="claude", causal_command_id=None,
                  payload={"epoch": n, "visit": v + 1, "reasoning": "forged: a third visit that decides nothing"})
    fails = prove.assert_slices(s, run, repo=REPO, gh_json=GH(s, f))
    assert any("the last visit's decision must be the accepted submission" in x for x in fails), fails


def test_the_fifth_line_reds_on_a_reshape_requested_missing_its_epoch(tmp_path):
    """Round 1, L1 (generalized in round 2, B4): `epoch_facts` (and
    `front.dispute`) key on `payload["epoch"]`; a reshape_requested that
    never carries one matches no epoch in the per-epoch loop and was
    invisible to every check there -- caught directly instead."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE)
    s.append_fact(run_id=f.run_id, kind=EventKind.RESHAPE_REQUESTED, actor="claude", causal_command_id=None,
                  payload={"visit": 2, "reasoning": "forged with no epoch key"})
    assert any("the journal's own count of bundle_revised facts before it gives epoch" in x for x in prove.assert_dispute(s, f.run_id))


def test_main_proves_the_parent_hand_back_through_assert_dispute(tmp_path, monkeypatch, capsys):
    """Round 1, B2: `assert_dispute`'s TOP-level wiring into `main` --
    deleting either call site left the whole suite green. A run with an
    unanswered hand-back must fail through `main` with no `--children` at
    all."""
    _no_network(monkeypatch)
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.request_reshape("three parts")
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("unanswered" in x for x in out.err.splitlines())


def test_main_children_proves_each_childs_hand_back_through_assert_dispute(tmp_path, monkeypatch, capsys):
    """Round 1, B2's other call site: a child run with an unanswered
    hand-back of its own must fail `main --children`, even though the
    PARENT's own history is entirely clean -- the reviewer's own live drive
    showed a child can carry a full unresolved dispute (refused
    `submit_slices` by the slice-not-sliceable guard, ruled one piece,
    `request_reshape` accepted from `queued`)."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    monkeypatch.setattr("coordinator.sweep.gh_json", GH(s, f))
    child_issue = {"number": 40, "title": "The store", "body": "b", "labels": ["bircher:autonomous"], "comments": []}
    Front(s, "i40-x-1", issue=child_issue).request_reshape("reconsider the split")
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", "o/r", "--children"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("child #40" in x and "unanswered" in x for x in out.err.splitlines())


def test_approval_mode_reds_on_a_second_approval_at_the_same_gate(tmp_path):
    """Round 1, L4: §6 says "exactly one human_ruling {approve} at it ...
    beside the spec gate's" -- one approval per gate. Revision 16 widened
    the approval filter's admitted set without adding a count. A second
    `approve` at the SAME (phase, epoch) is kernel-refused once the phase
    has already advanced past it, so forged directly."""
    s = _store_with_confirmed_stops(tmp_path / "b.db")
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES)
    _accept(f)
    f.approve()
    s.append_fact(run_id="r-1", kind=EventKind.HUMAN_RULING, actor="human", causal_command_id=None,
                  payload={"ruling": "approve", "phase": "spec", "epoch": 0,
                           "artifact_hash": s.phase_artifact("r-1", "spec"), "cursor_item_id": "i-h2"})
    assert any("more than one approval" in x for x in prove.assert_journal(s, "r-1", mode="approval"))


# -- Fix round 2: B3 (a wrong-but-in-range visit stamp), B4 (a wrong or
#    absent epoch stamp -- B1/L1's defect, one key over), B5 (a `visit:
#    None` stamp read two ways by two readers), the crash a string `visit`
#    caused, and four inert assertions round 1 left behind --------------


def test_assertion_4_reds_on_a_ruling_stamped_an_in_range_but_wrong_visit(tmp_path):
    """Round 2, B3: an IN-RANGE `visit` stamp that disagrees with the
    journal's own order was invisible to round 1's out-of-range check,
    which only asked whether the stamp fell in `[1, last]` --
    `_Planted.ruled_then_directed_then_sliced`'s own docstring calls it "the
    specific case visit=last exists to get right": visit 1 rules, visit 2
    rules again (disputed), a direction resolves it and opens visit 3,
    which files. A shape ruling forged AFTER the filing, stamped visit 2 --
    an earlier, already-decided, real visit -- must fail: the walk
    attributes it to visit 3, the one that actually filed. No backstop
    exists for this shape: the dispute was already resolved by the
    direction, so the fifth line has nothing to say."""
    s, run = _Planted(tmp_path).ruled_then_directed_then_sliced()
    f = Front(s, run, existing=True)
    s.append_fact(run_id=run, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged after the filing, misattributed to visit 2",
                           "cost_if_wrong": "n/a", "visit": 2})
    fails = prove.assert_slices(s, run, repo=REPO, gh_json=GH(s, f))
    assert any("prefix walk attributes it to visit 3" in x for x in fails), fails
    assert prove.assert_dispute(s, run) == []      # confirmed: no backstop here


def test_assertion_4_reds_on_the_same_ruling_misattributed_to_visit_1(tmp_path):
    """The other wrong stamp from the same probe: visit 1 already held its
    own real ruling, and the forged one hiding behind it must still fail."""
    s, run = _Planted(tmp_path).ruled_then_directed_then_sliced()
    f = Front(s, run, existing=True)
    s.append_fact(run_id=run, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged after the filing, misattributed to visit 1",
                           "cost_if_wrong": "n/a", "visit": 1})
    fails = prove.assert_slices(s, run, repo=REPO, gh_json=GH(s, f))
    assert any("prefix walk attributes it to visit 3" in x for x in fails), fails


def test_assertion_4_a_ruling_stamped_its_true_visit_is_caught_by_clause_b(tmp_path):
    """The control: the SAME forged ruling, stamped correctly (visit 3, its
    real walked visit), passes the new stamp check -- and is caught instead
    by the pre-existing clause (b), which sees it beside that visit's own
    accepted, filed submission."""
    s, run = _Planted(tmp_path).ruled_then_directed_then_sliced()
    f = Front(s, run, existing=True)
    s.append_fact(run_id=run, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged after the filing, correctly stamped",
                           "cost_if_wrong": "n/a", "visit": 3})
    fails = prove.assert_slices(s, run, repo=REPO, gh_json=GH(s, f))
    assert not any("prefix walk attributes it to visit" in x for x in fails), fails
    assert any("beside the accepted slice plan" in x for x in fails), fails


@pytest.mark.parametrize("bad_epoch,has_key", [
    (None, False),   # no epoch key at all
    (99, True),
    ("0", True),
    (-1, True),
])
def test_assertion_4_reds_on_a_ruling_stamped_a_wrong_epoch(tmp_path, bad_epoch, has_key):
    """Round 2, B4: B1's defect one key over. Every reader in assertion 4
    filters on `payload["epoch"] == e`, so a fact stamped an epoch this run
    never reached -- absent, out of range, or merely a value that LOOKS
    like the real one without ever comparing equal to it (a string "0" is
    never `== 0`) -- sits in no epoch's list and is read by nothing."""
    s, f = _parent(tmp_path)
    payload = {"question_id": "shape", "ruling": "one piece",
               "reasoning": "forged with a bad epoch", "cost_if_wrong": "n/a"}
    if has_key:
        payload["epoch"] = bad_epoch
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload=payload)
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("the journal's own count of bundle_revised facts before it gives epoch" in x for x in fails), (bad_epoch, has_key, fails)


@pytest.mark.parametrize("bad_epoch,has_key", [
    (None, False),
    (99, True),
    ("0", True),
])
def test_assertion_4_reds_on_a_submission_stamped_a_wrong_epoch(tmp_path, bad_epoch, has_key):
    """B4's clause-(b) instance: a `slices` submission stamped an epoch
    this run never reached."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    h = put_artifact(s, SLICES_BYTES)
    payload = {"phase": "slices", "hash": h, "author": "claude", "round": 1, "visit": 1}
    if has_key:
        payload["epoch"] = bad_epoch
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload=payload)
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("the journal's own count of bundle_revised facts before it gives epoch" in x for x in fails), (bad_epoch, has_key, fails)


@pytest.mark.parametrize("bad_epoch,has_key", [
    (None, False),
    (99, True),
    ("0", True),
    (-1, True),
])
def test_the_fifth_line_reds_on_a_reshape_requested_stamped_a_wrong_epoch(tmp_path, bad_epoch, has_key):
    """Round 2, B4's instance inside the fifth line: round 1's L1 fix caught
    only the missing-key spelling of this; a wrong-but-present epoch value
    passed the same way, while the identical hand-back at the real epoch
    was already rejected."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE)
    payload = {"visit": 2, "reasoning": "forged with a bad epoch"}
    if has_key:
        payload["epoch"] = bad_epoch
    s.append_fact(run_id=f.run_id, kind=EventKind.RESHAPE_REQUESTED, actor="claude", causal_command_id=None,
                  payload=payload)
    fails = prove.assert_dispute(s, f.run_id)
    assert any("the journal's own count of bundle_revised facts before it gives epoch" in x for x in fails), (bad_epoch, has_key, fails)


def test_assertion_4_reds_on_a_submission_stamped_visit_none(tmp_path):
    """Round 2, B5: `front.submissions`'s per-visit filter used to read
    `payload.get("visit", visit_of(...))` -- a default that fires only when
    the KEY is absent, so a payload carrying an EXPLICIT `visit: None`
    (present, valued `None`) matched no visit at all and was invisible to
    clause (b). `front.shape_ruling` spelled the same fallback with `or`,
    which treats a `None` VALUE the same as an absent key -- two readers of
    one stamp, two answers. `front.visit_stamp_of` is the one definition
    now, and this proof's own stamp-vs-walk check catches the submission
    side directly: a stamp of `None` never equals the walk's real (>= 1)
    answer."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 1, "visit": None})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("prefix walk attributes it to visit" in x for x in fails), fails


def test_assertion_4_does_not_crash_on_a_string_visit_stamp(tmp_path):
    """Medium, round 2: a `visit` stamp of the wrong TYPE used to raise out
    of round 1's range check (`1 <= v <= last` on a string), killing the
    tool mid-proof against `assert_slices`'s own docstring promise that a
    failed read is a failure line, not a crash. The stamp-vs-walk `!=`
    comparison never raises regardless of type."""
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged with a string visit", "cost_if_wrong": "n/a", "visit": "1"})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))   # must not raise
    assert any("prefix walk attributes it to visit" in x for x in fails), fails


def test_the_final_epoch_conjunct_ignores_a_matching_hash_in_a_superseded_epoch(tmp_path):
    """Round 2, inert #1: `_decision_at`'s `accepted` filter requires
    `e == n`. Without it, a superseded epoch's own submission -- reviewed
    and accepted before the issue changed out from under it -- whose bytes
    happen to match the FINAL epoch's later, genuinely accepted plan (the
    same content submitted twice, once before the revision and once after)
    would itself read as "accepted", and a ruling forged beside it in the
    OLD epoch would wrongly fire "beside the accepted slice plan". `e == n`
    keeps "accepted" scoped to the epoch that actually reached `sliced`.

    Round 3, C1 correction: the ruling is forged at a seq genuinely INSIDE
    epoch 0's own range (before the `revise_bundle` that supersedes it), not
    after `file_all` -- forging it afterward stamps an epoch that no longer
    matches the journal's own count of `bundle_revised` facts before that
    seq, which is C1's OWN defect, not this test's. Forged here, the ruling
    is genuinely a real (if kernel-refused) member of epoch 0, and its
    stamped epoch and the walk agree."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES)                              # epoch 0, visit 1
    f.review_round("accept")                                 # -> slices_accepted
    # A ruling forged into epoch 0's OWN seq range, before the epoch is
    # superseded -- not after everything, which would itself be C1.
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged into the superseded epoch, before it is superseded"})
    f.revise(dict(ISSUE, body="changed"), reshape=False)      # epoch 1 opens; epoch 0 abandoned mid-accept
    f.to_sliced(SLICES_BYTES)                                 # epoch 1: the SAME bytes, genuinely accepted
    f.file_all(f._dispatch(Role.OPERATOR, "coordinator"))
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f)) == []


def test_the_shape_question_filter_excludes_a_grill_rulings_bad_stamp(tmp_path):
    """Round 2, inert #3: the stamp-vs-walk scan filters to
    `question_id == SHAPE_QUESTION` -- without it, an ordinary grill ruling
    (no part of the shaping visit system at all) carrying a wildly wrong
    `visit` stamp would be swept into the same check assertion 4 runs over
    shape rulings, and it must not be: a grill ruling's `visit` means
    nothing to §6."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)           # birth: shape ruling, visit 1
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    grill_ruling = s.facts_of_kind(f.run_id, EventKind.MODEL_RULING)[-1]
    assert grill_ruling.payload["question_id"] != "shape"
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={**grill_ruling.payload, "visit": 999})
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None) == []


def test_the_shape_question_filter_excludes_a_grill_rulings_bad_epoch(tmp_path):
    """Round 2's own B4 epoch scan (`all_shape_rulings`) filters to
    `question_id == SHAPE_QUESTION` too, independently of the stamp-vs-walk
    scan above -- without it, a grill ruling stamped a bad EPOCH would be
    swept into the epoch-validity check assertion 4 runs over shape
    rulings, and it must not be: a grill ruling's epoch is checked by
    `assert_journal`'s own model_ruling machinery, not by §6."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    grill_ruling = s.facts_of_kind(f.run_id, EventKind.MODEL_RULING)[-1]
    assert grill_ruling.payload["question_id"] != "shape"
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={**grill_ruling.payload, "epoch": 99})
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None) == []


def test_the_slices_phase_filter_excludes_a_spec_submissions_bad_epoch(tmp_path):
    """The mirror of the two tests above, on the submission side:
    `all_slices_subs` filters to `phase == "slices"` -- without it, a `spec`
    submission stamped a bad epoch would be swept into assertion 4's own
    epoch-validity scan, and it must not be: a spec submission's epoch is
    `assert_journal`'s phase-set check's concern, not §6's."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    h = put_artifact(s, SPEC_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "spec", "epoch": 99, "hash": h, "author": "claude", "round": 1})
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None) == []


def test_the_duplicate_approval_key_distinguishes_epochs_not_just_phases(tmp_path):
    """Round 2, inert #4: the gate key is `(phase, epoch)` -- without the
    epoch half, two LEGITIMATE approvals of the SAME phase in two DIFFERENT
    epochs (a spec approved, the issue revised, a fresh spec approved
    again) would be wrongly grouped as "the same gate twice"."""
    s = _store_with_confirmed_stops(tmp_path / "b.db")
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES)
    _accept(f)
    f.approve()
    f.revise(dict(ISSUE, body="changed"))
    f.author_round(SPEC_BYTES + b"\nrevised\n")
    _accept(f)
    f.approve()
    fails = prove.assert_journal(s, f.run_id, mode="approval")
    assert not any("more than one approval" in x for x in fails), fails


def test_main_proves_the_parent_through_assert_journal(tmp_path, monkeypatch, capsys):
    """Round 2: main's parent `assert_journal` call was exercised by
    nothing (deleting it left the whole suite green) -- a run with a human
    fact under zero mode must fail `main` with that call's own message, not
    merely via `assert_sessions` (which `_no_network` fails regardless)."""
    _no_network(monkeypatch)
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.direct("use postgres")
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("human facts present" in x for x in out.err.splitlines())


def test_main_children_proves_each_child_through_assert_journal(tmp_path, monkeypatch, capsys):
    """The child-loop half of the same wiring."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    monkeypatch.setattr("coordinator.sweep.gh_json", GH(s, f))
    child_issue = {"number": 40, "title": "The store", "body": "b", "labels": ["bircher:autonomous"], "comments": []}
    Front(s, "i40-x-1", issue=child_issue).direct("noted")
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", "o/r", "--children"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("child #40" in x and "human_direction" in x for x in out.err.splitlines())


def test_main_proves_the_parent_through_assert_sessions(tmp_path, monkeypatch, capsys):
    """main's `assert_sessions` call -- a run whose journal and dispute are
    both clean must still fail `main` when the server disagrees; with
    `_no_network` stubbed in, the listing simply cannot be read at all."""
    _no_network(monkeypatch)
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session())
    from tests.kernel.test_prove_front_half import _review_round_with_real_brief
    _review_round_with_real_brief(f)
    f.author_round(PLAN_BYTES)
    _review_round_with_real_brief(f)
    assert prove.assert_journal(s, f.run_id, mode="zero") == []   # isolates assert_sessions as the source
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("could not be read" in x for x in out.err.splitlines())


def test_main_children_proves_each_child_through_assert_sessions(tmp_path, monkeypatch, capsys):
    """The child-loop half of `assert_sessions`'s wiring."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    monkeypatch.setattr("coordinator.sweep.gh_json", GH(s, f))
    child_issue = {"number": 40, "title": "The store", "body": "b", "labels": ["bircher:autonomous"], "comments": []}
    Front(s, "i40-x-1", issue=child_issue)
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", "o/r", "--children"])
    out = capsys.readouterr()
    assert rc != 0
    assert any("child #40" in x and "could not be read" in x for x in out.err.splitlines())


# -- Fix round 3: C1/C2 (the `epoch` stamp is a claim, not a range, exactly
#    like `visit`), C3 (`shape_ruling`'s newest-wins collapse hides more
#    than one ruling per visit), C4 (approval mode never checks the gate
#    it admits exists), the KeyError crash class, and four more inert
#    assertions -------------------------------------------------------


def _two_epoch_sliced_parent(tmp_path, name="a.db"):
    """Epoch 0 submits a slice plan and is superseded by a `revise_bundle`
    before it decides anything; epoch 1 slices, is accepted, and files --
    the reviewer's own C1/C2 construction."""
    s = Store.open(tmp_path / name)
    f = Front(s, "r-1", shape=False, issue=ISSUE)
    f.shape_round(SLICES_BYTES)                             # epoch 0: a plan waits, undecided
    f.revise(dict(ISSUE, body="changed"), reshape=False)     # epoch 1 opens; epoch 0 abandoned
    f.to_sliced(SLICES_BYTES)
    f.file_all(f._dispatch(Role.OPERATOR, "coordinator"))
    return s, f


def test_assertion_4_reds_on_a_ruling_stamped_the_superseded_epoch(tmp_path):
    """Round 3, C1: the `epoch` stamp had B3's exact defect one key over --
    round 2 only bounded it to `[0, n]`, never derived it from the journal.
    A shape ruling forged AFTER the filing, stamped the SUPERSEDED epoch
    (0, when the real epoch is 1), was accepted; the journal's own count of
    `bundle_revised` facts before its seq says otherwise."""
    s, f = _two_epoch_sliced_parent(tmp_path)
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f)) == []
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged after the filing, stamped the superseded epoch",
                           "cost_if_wrong": "n/a"})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("bundle_revised facts before it gives epoch 1" in x for x in fails), fails


def test_assertion_4_the_epoch_control_stamped_the_real_epoch(tmp_path):
    """The control: the SAME forged ruling, stamped the real epoch (1), is
    caught by the pre-existing clause (b)/(c) messages instead -- proof the
    epoch-derivation check is doing new work, not the existing clauses'."""
    s, f = _two_epoch_sliced_parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 1, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "forged after the filing, stamped the real epoch",
                           "cost_if_wrong": "n/a"})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("beside the accepted slice plan" in x for x in fails), fails
    assert not any("bundle_revised facts before it" in x for x in fails), fails


def test_assertion_4_reds_on_a_submission_stamped_the_superseded_epoch(tmp_path):
    """C1's other instance: a `slices` submission forged after the filing,
    stamped the superseded epoch."""
    s, f = _two_epoch_sliced_parent(tmp_path)
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 9, "visit": 1})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))
    assert any("bundle_revised facts before it gives epoch 1" in x for x in fails), fails


def test_the_fifth_line_reds_on_a_hand_back_stamped_the_superseded_epoch(tmp_path):
    """Round 3, C2: the most damaging consequence of C1 -- `assert_dispute`
    skips a superseded epoch BY DESIGN (§7), so an unanswered hand-back
    stamped the epoch that has already been superseded switches the fifth
    line off entirely, on the one epoch it actually needs to answer for."""
    s, f = _two_epoch_sliced_parent(tmp_path)
    assert prove.assert_dispute(s, f.run_id) == []
    s.append_fact(run_id=f.run_id, kind=EventKind.RESHAPE_REQUESTED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "visit": 2,
                           "reasoning": "forged after the filing, stamped the superseded epoch"})
    fails = prove.assert_dispute(s, f.run_id)
    assert any("bundle_revised facts before it gives epoch 1" in x for x in fails), fails


def test_assertion_4_reds_on_a_submission_sandwiched_between_two_rulings(tmp_path):
    """Round 3, C3: `front.shape_ruling` returns only the NEWEST ruling of a
    visit, so clause (b)'s "no submission follows A ruling" used to compare
    against that one ruling alone -- a submission that follows the FIRST of
    two rulings in a visit, with a SECOND ruling forged after it, went
    invisible. Every stamp equals the walk and every epoch is real; only
    `shape_ruling`'s newest-wins collapse hid it. `_decision_at` now reads
    every ruling attributed to the visit, not the newest."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)      # birth: the first ruling, visit 1
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 1})
    s.append_fact(run_id=f.run_id, kind=EventKind.MODEL_RULING, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "question_id": "shape", "ruling": "one piece",
                           "reasoning": "a second ruling, forged after the submission",
                           "cost_if_wrong": "n/a"})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("submitted after the shape ruling" in x for x in fails), fails


def test_assertion_4_the_c3_control_without_the_second_ruling_still_reds(tmp_path):
    """The control: without the second ruling, the identical submission is
    already rejected -- the second ruling is what used to hide it, not some
    other difference between this history and the committed ones."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "r-1", issue=ISSUE)
    h = put_artifact(s, SLICES_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": h, "author": "claude", "round": 1})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("submitted after the shape ruling" in x for x in fails), fails


def test_approval_mode_reds_on_an_approve_at_a_gate_never_reached(tmp_path):
    """Round 3, C4: admission by ruling word alone says nothing about
    whether the gate it names ever existed. §6 admits "every gate's
    approve" -- a gate, not any fact spelling the word."""
    s = _store_with_confirmed_stops(tmp_path / "a.db")
    f = Front(s, "r-1")   # a one-piece run: never submits a slices plan at all
    s.append_fact(run_id=f.run_id, kind=EventKind.HUMAN_RULING, actor="human", causal_command_id=None,
                  payload={"ruling": "approve", "phase": "slices", "epoch": 0,
                           "artifact_hash": "deadbeef", "cursor_item_id": "i-h"})
    fails = prove.assert_journal(s, f.run_id, mode="approval")
    assert any("admitted by ruling word alone" in x for x in fails), fails


def test_approval_mode_reds_on_an_approve_naming_an_epoch_the_run_never_had(tmp_path):
    s = _store_with_confirmed_stops(tmp_path / "b.db")
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.HUMAN_RULING, actor="human", causal_command_id=None,
                  payload={"ruling": "approve", "phase": "spec", "epoch": 7,
                           "artifact_hash": s.phase_artifact(f.run_id, "spec"), "cursor_item_id": "i-h"})
    fails = prove.assert_journal(s, f.run_id, mode="approval")
    assert any("admitted by ruling word alone" in x for x in fails), fails


def test_approval_mode_reds_on_an_approve_one_piece_with_no_dispute(tmp_path):
    s = _store_with_confirmed_stops(tmp_path / "c.db")
    f = Front(s, "r-1")   # a plain one-piece run: never handed back
    s.append_fact(run_id=f.run_id, kind=EventKind.HUMAN_RULING, actor="human", causal_command_id=None,
                  payload={"ruling": "approve_one_piece", "phase": "slices", "epoch": 0,
                           "visit": 1, "cursor_item_id": "i-h"})
    fails = prove.assert_journal(s, f.run_id, mode="approval")
    assert any("admitted by ruling word alone" in x for x in fails), fails


def test_approval_mode_reds_on_a_disagreement_park_with_no_dispute(tmp_path):
    s = _store_with_confirmed_stops(tmp_path / "d.db")
    f = Front(s, "r-1")
    s.append_fact(run_id=f.run_id, kind=EventKind.PARKED, actor="coordinator", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "reason": "disagreement", "session_id": None,
                           "cursor_item_id": None, "findings_hash": None, "verdict": None, "reviewer": None,
                           "generation": 1})
    fails = prove.assert_journal(s, f.run_id, mode="approval")
    assert any("admitted by ruling word alone" in x for x in fails), fails


def test_assert_journal_does_not_crash_on_a_submission_missing_its_phase(tmp_path):
    """Medium, round 3: an artifact_submitted with no `phase` key used to
    raise `KeyError` out of `subs`'s dict comprehension, and `main` calls
    `assert_journal` first -- the tool died before anything else ran. The
    same class round 2 closed for a string `visit` stamp, one key over."""
    s = _store_with_confirmed_stops(tmp_path / "a.db")
    f = Front(s, "r-1")
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "hash": "deadbeef", "author": "claude", "round": 1})  # no "phase"
    fails = prove.assert_journal(s, f.run_id, mode="zero")   # must not raise
    assert isinstance(fails, list)


def test_assert_journal_does_not_crash_on_a_sliced_parents_submission_missing_phase(tmp_path):
    """The `late_spec` instance of the same crash -- reachable only through
    a sliced parent that ALSO holds a hand-back (`late_spec`'s list
    comprehension is never even evaluated when `hb is None`, so a plain
    `_parent` fixture never reaches this line at all)."""
    s, run = _Planted(tmp_path).hand_back_then_sliced()
    s.append_fact(run_id=run, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0})   # no "phase", no "hash" at all
    fails = prove.assert_journal(s, run, mode="zero")    # must not raise
    assert isinstance(fails, list)


def test_assert_slices_does_not_crash_on_a_submission_missing_its_hash(tmp_path):
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "author": "claude", "round": 9, "visit": 1})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f))   # must not raise
    assert isinstance(fails, list)


def test_assert_dispute_does_not_crash_on_a_between_fact_missing_its_phase(tmp_path):
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE)
    f.request_reshape("three parts")
    f.shape_round(reasoning="still one", cost="the spec would find one surface")   # the disputed ruling
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0})   # no "phase" at all
    f.direct("slice it")   # the resolution
    fails = prove.assert_dispute(s, f.run_id)   # must not raise
    assert isinstance(fails, list)


def test_clause_a_reds_on_a_slice_filed_outside_the_final_epoch(tmp_path):
    """Round 3, inert #1: clause (a)'s own filter -- no history plants a
    slice_filed outside the final epoch at all. Forged: a `bundle_revised`
    fact appended after a clean parent has already filed (the kernel
    refuses this in reality -- `revise_bundle` is refused from `sliced` --
    so this is forged directly), opening a new epoch the OLD filing is now
    outside of."""
    s, f = _parent(tmp_path)
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f)) == []
    # `assert_slices` reads `front.issue_number`, which reads the newest
    # bundle_hash's own blob -- a real one, not a placeholder string, or the
    # forgery crashes somewhere this test does not mean to probe.
    new_bundle = put_artifact(s, json.dumps({**ISSUE, "body": "changed"}).encode())
    s.append_fact(run_id=f.run_id, kind=EventKind.BUNDLE_REVISED, actor="runner", causal_command_id=None,
                  payload={"bundle_hash": new_bundle})
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=None)
    assert any("outside the final epoch" in x for x in fails), fails


def test_the_hash_conjunct_ignores_a_decoy_accept_verdict_in_an_earlier_visit(tmp_path):
    """Round 3, inert #2: `_decision_at`'s accepted-submission filter also
    requires the hash to match `accepted_h` -- without it, a DECOY
    submission sharing a visit with a real ruling, and its own (forged)
    accept verdict for a plan that was never the one actually advanced,
    would be misread as itself the accepted plan, firing "beside the
    accepted slice plan" for a visit that never accepted anything."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE, shape=False)   # state=shaping, nothing ruled yet
    decoy_h = put_artifact(s, b"decoy plan bytes, never actually accepted")
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "slices", "epoch": 0, "hash": decoy_h, "author": "claude",
                           "round": 1, "visit": 1})
    s.append_fact(run_id=f.run_id, kind=EventKind.REVIEW_VERDICT, actor="codex", causal_command_id=None,
                  payload={"verdict": "accept", "phase": "slices", "epoch": 0, "artifact_hash": decoy_h,
                           "findings_hash": None, "ruling": "review_ruling", "binding_hash": None,
                           "reviewer_identity": "codex", "generation": 999})
    f.shape_round(reasoning="one piece despite the decoy", cost="n/a")   # the REAL ruling, AFTER the decoy
    f.request_reshape("the issue names a store, an API and a page")     # visit 2
    f.shape_round(SLICES_BYTES)
    _accept(f)
    if "slices" in policy_of(s, f.run_id).gates:
        f.approve()
    else:
        f.advance()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    f.file_all(g)
    assert prove.assert_slices(s, f.run_id, repo=REPO, gh_json=GH(s, f)) == []


def test_filed_after_ignores_a_filing_recorded_before_the_hand_back(tmp_path):
    """Round 3, inert #3: `assert_dispute`'s `filed_after` bound
    (`f.seq > d.hand_back.seq`) -- no history plants a filing before its own
    hand-back. Unreachable through any real command (clause (a) already
    refuses a filing outside the final epoch, and filing only happens from
    `sliced`, which `request_reshape` cannot reach), so forged directly."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE)
    s.append_fact(run_id=f.run_id, kind=EventKind.SLICE_FILED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "slice": 1, "issue": 40, "issue_id": 1040, "title": "t",
                           "parent": 12, "plan_hash": "deadbeef"})
    f.request_reshape("three parts")
    assert any("unanswered" in x for x in prove.assert_dispute(s, f.run_id))


def test_between_ignores_a_spec_forged_before_the_disputed_ruling(tmp_path):
    """Round 3, inert #4: `between`'s lower bound (`d.disputed.seq < f.seq`)
    -- no history plants a spec submission BEFORE the disputed ruling that
    is also before the resolution, so nothing pins that the window starts
    at the disputed ruling and not merely ends at the resolution."""
    s = Store.open(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", issue=ISSUE)
    f.request_reshape("three parts")
    h = put_artifact(s, SPEC_BYTES)
    s.append_fact(run_id=f.run_id, kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "spec", "epoch": 0, "hash": h, "author": "claude", "round": 1})
    f.shape_round(reasoning="still one", cost="n/a")   # the disputed ruling, AFTER the forged spec
    f.direct("slice it")                                # the resolution
    assert prove.assert_dispute(s, f.run_id) == []


# ---------------------------------------------------------------------------
# Fix round 4: the crash class, closed by one helper (`prove._need`) rather
# than patched key by key. The tests below are the class-level proof the
# coordinator asked for: not one instance reproduced and fixed, but a walk
# over the fact kinds the proof reads and the keys it reads off them, each
# stripped from a REAL, otherwise-passing fact and checked to fail cleanly
# rather than crash.
# ---------------------------------------------------------------------------

def _forged(s, run_id, kind, template, *, drop=None, **override):
    """Append a fact of *kind* to *run_id*, its payload CLONED from
    *template* (a real fact's payload, borrowed from a working fixture that
    shares this same store -- so every hash it carries still names a real
    blob) with *drop* removed and *override* applied on top. Facts are
    append-only (the store enforces it, `facts_no_update`/`facts_no_delete`
    in schema.sql) -- this is how a test gets a fact that is otherwise real
    but missing exactly one key, without touching the fact a passing
    fixture actually wrote."""
    p = dict(template)
    p.update(override)
    if drop is not None:
        del p[drop]
    s.append_fact(run_id=run_id, kind=kind, actor="claude", causal_command_id=None, payload=p)


def _no_fetch(*_a, **_k):
    raise RuntimeError("no network in this test")


def _no_gh(_argv):
    raise ReadFailed("no network in this test")


def test_a_missing_payload_key_reds_not_crashes(tmp_path):
    """Round 4: the crash class. The round-4 review found roughly twenty
    payload keys that still crashed the tool outright -- eight on
    `review_brief_issued` alone -- after three rounds of fixing them one
    instance at a time. The fix is `prove._need`, one helper applied
    everywhere a fact's payload crosses from a claim to a read; this is the
    test that proves the CLASS is closed, not just the member the last
    review happened to name: every case below builds a bare run and forges
    one fact CLONED from a real, working fixture's (`_parent`'s) own facts,
    with exactly one key dropped, then asserts the proof reports a failure
    line NAMING that key rather than raising.

    One shared store (`_parent`'s), not one per case: `review_round`'s
    facts point at real content-addressed blobs, and a forged fact that
    borrows their hashes needs those same blobs still in the artifact
    table it reads from. Each case gets its OWN run id within that store,
    so one case's forged fact cannot answer another's read."""
    s, tmpl = _parent(tmp_path)
    verdict_tmpl = s.facts_of_kind(tmpl.run_id, EventKind.REVIEW_VERDICT)[-1].payload
    brief_tmpl = s.facts_of_kind(tmpl.run_id, EventKind.REVIEW_BRIEF_ISSUED)[-1].payload
    policy_tmpl = s.newest_fact(tmpl.run_id, EventKind.POLICY_FROZEN).payload

    cases = []  # (label, key, build) -- build(run_id, issue_no) -> fails

    def review_case(key, *, on):
        def build(run_id, issue_no):
            Front(s, run_id, issue={**ISSUE, "number": issue_no}, shape=False)
            _forged(s, run_id, EventKind.REVIEW_BRIEF_ISSUED, brief_tmpl, drop=key if on == "brief" else None)
            _forged(s, run_id, EventKind.REVIEW_VERDICT, verdict_tmpl, drop=key if on == "verdict" else None)
            return prove.assert_journal(s, run_id, mode="zero")
        return key, build

    for label, args in [
        ("review_verdict.generation", dict(key="generation", on="verdict")),
        ("review_verdict.epoch", dict(key="epoch", on="verdict")),
        ("review_verdict.artifact_hash", dict(key="artifact_hash", on="verdict")),
        ("review_brief_issued.epoch", dict(key="epoch", on="brief")),
        ("review_brief_issued.brief_hash", dict(key="brief_hash", on="brief")),
        ("review_brief_issued.phase", dict(key="phase", on="brief")),
        ("review_brief_issued.artifact_hash", dict(key="artifact_hash", on="brief")),
        ("review_brief_issued.context_bundle_hash", dict(key="context_bundle_hash", on="brief")),
        ("review_brief_issued.base_sha", dict(key="base_sha", on="brief")),
        ("review_brief_issued.brief_template", dict(key="brief_template", on="brief")),
        ("review_brief_issued.spec_hash", dict(key="spec_hash", on="brief")),
    ]:
        key, build = review_case(**args)
        cases.append((label, key, build))

    def policy_case(run_id, issue_no):
        Front(s, run_id, issue={**ISSUE, "number": issue_no}, shape=False)
        _forged(s, run_id, EventKind.REVIEW_BRIEF_ISSUED, brief_tmpl)
        _forged(s, run_id, EventKind.REVIEW_VERDICT, verdict_tmpl)
        _forged(s, run_id, EventKind.POLICY_FROZEN, policy_tmpl, drop="policy")
        return prove.assert_journal(s, run_id, mode="zero")
    cases.append(("policy_frozen.policy", "policy", policy_case))

    def prompt_item_case(run_id, issue_no):
        Front(s, run_id, issue={**ISSUE, "number": issue_no}, shape=False)
        s.append_fact(run_id=run_id, kind=EventKind.PROMPT_ITEM, actor="coordinator", causal_command_id=None,
                      payload={"session_id": "sess-1", "sha256": "deadbeef"})   # no "item_id"
        return prove.assert_sessions(s, run_id, fetch=_no_fetch)
    cases.append(("prompt_item.item_id", "item_id", prompt_item_case))

    def brief_sessions_case(key):
        def build(run_id, issue_no):
            Front(s, run_id, issue={**ISSUE, "number": issue_no}, shape=False)
            _forged(s, run_id, EventKind.REVIEW_BRIEF_ISSUED, brief_tmpl, drop=key)
            return prove.assert_sessions(s, run_id, fetch=_no_fetch)
        return build
    cases.append(("review_brief_issued.generation (assert_sessions)", "generation", brief_sessions_case("generation")))
    cases.append(("review_brief_issued.brief_hash (assert_sessions)", "brief_hash", brief_sessions_case("brief_hash")))

    def slice_filed_case(run_id, issue_no):
        Front(s, run_id, issue={**ISSUE, "number": issue_no}, shape=False)
        s.append_fact(run_id=run_id, kind=EventKind.SLICE_FILED, actor="claude", causal_command_id=None,
                      payload={"epoch": 0, "slice": 1, "issue_id": 1040, "title": "t",
                               "parent": issue_no, "plan_hash": "deadbeef"})   # no "issue"
        return prove.assert_slices(s, run_id, repo=REPO, gh_json=_no_gh)
    cases.append(("slice_filed.issue (assert_slices)", "issue", slice_filed_case))

    for i, (label, key, build) in enumerate(cases):
        fails = build(f"case-{i}", 1000 + i)
        assert isinstance(fails, list), label
        assert any(repr(key) in x for x in fails), f"{label}: no failure line named {key!r}; got {fails}"


def test_main_does_not_crash_on_a_slice_filed_missing_its_issue(tmp_path, monkeypatch):
    """The same key (`slice_filed.issue`), the other call site: `main`'s own
    `--children` loop reads it three times over before `assert_slices` ever
    gets a look in. Both are `prove._need` now; this is the second site's
    own regression guard, not a re-test of the first. A slice number
    outside the plan (99), so it does not collide with `_parent`'s own real
    filings -- `main`'s loop does not care whether a number is in the plan,
    only whether it can find a child run for it."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.SLICE_FILED, actor="claude", causal_command_id=None,
                  payload={"epoch": 0, "slice": 99, "issue_id": 9999, "title": "t",
                           "parent": 12, "plan_hash": "deadbeef"})   # no "issue"
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--repo", REPO, "--children"])
    assert rc == 1   # a real defect: a slice_filed the tool cannot even name


def test_main_does_not_crash_on_a_run_enqueued_missing_its_bundle_hash(tmp_path, monkeypatch):
    """`run_enqueued`'s own `bundle_hash`, read only by `main`'s `--issues`
    block -- a second, forged `run_enqueued` (facts are append-only; a real
    run never gets a second one, but `store.newest_fact` does not know
    that, so the forged one shadows the real one for this read exactly as a
    live corruption would)."""
    _no_network(monkeypatch)
    s, f = _parent(tmp_path)
    s.append_fact(run_id=f.run_id, kind=EventKind.RUN_ENQUEUED, actor="human", causal_command_id=None,
                  payload={"packet_hash": None})   # no "bundle_hash"
    issues = tmp_path / "issues.txt"
    issues.write_text("12\n")
    rc = prove.main(["--db", str(tmp_path / "k.db"), "--run-id", f.run_id, "--server", "http://x",
                      "--issues", str(issues)])
    assert rc == 1


def test_assert_slices_does_not_crash_on_a_bundle_revised_missing_its_hash(tmp_path):
    """`front.bundle_hash`'s other source fact -- `bundle_revised`, read
    through `front.issue_number`'s caller (`assert_slices`), which used to
    let a bad hash crash the WHOLE function before assertion 4 (unrelated to
    any parent number) ever ran."""
    s = _store_with_confirmed_stops(tmp_path / "a.db")
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE)
    s.append_fact(run_id=f.run_id, kind=EventKind.BUNDLE_REVISED, actor="runner", causal_command_id=None,
                  payload={})   # no "bundle_hash" at all
    fails = prove.assert_slices(s, f.run_id, repo=REPO, gh_json=_no_gh)
    assert isinstance(fails, list)
    assert any("issue number" in x for x in fails)
