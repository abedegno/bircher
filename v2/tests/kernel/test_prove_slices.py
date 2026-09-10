"""Task 11: the four slice assertions and the amended existing checks
(shaping spec §6)."""
import json

from coordinator.sweep import ReadFailed
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front
from tests.kernel.test_prove_front_half import _accept, _store_with_confirmed_stops
from tools import prove_front_half as prove

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous"], "comments": []}


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
    """MINOR 2: isolate the final "exactly one decision" check from the
    per-epoch "shape ruling beside a filing" guard, which structurally
    cannot fire here -- it only ever activates once a ruling exists in some
    epoch, and this run has ruled on nothing.

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
    assert any("exactly one decision" in x for x in fails)
    assert not any("beside a filing" in x for x in fails)
