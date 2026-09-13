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
