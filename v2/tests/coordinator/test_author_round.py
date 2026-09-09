import os
import time

import pytest

from coordinator import author, phases, seat
from coordinator.effects import KERNEL
from kernel import front
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import SPEC_BYTES, Front


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(labels=("bircher:autonomous",), cfg=None):
        db = tmp_path / "k.db"
        s = Store.open(db)
        f = Front(s, "r-1", labels=labels, project_config=cfg)
        fake = FakeOmnigent()
        monkeypatch.setattr("kernel.cli.subprocess.run", fake.run)
        monkeypatch.setattr("coordinator.sessions.add_worktree",
                            lambda repo, sha, path: (os.makedirs(path), os.path.realpath(path))[1])
        env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
               "PATH": os.environ["PATH"]}
        clock = {"t": time.time()}           # offset from real time: effect rows carry the kernel's clock
        ctx = phases.Ctx(store=s, run_id="r-1", server="http://srv", repo="o/r", issue_number=1,
                         repo_dir=str(tmp_path / "repo"), workspaces_root=str(tmp_path / "ws"),
                         bundle_dir=str(tmp_path / "bundle"), agent_ids={"claude": "ag_claude", "codex": "ag_codex"},
                         host_id="host_h", turn_timeout_s=100, default_author="claude", env=env,
                         fetch=fake.fetch, clock=lambda: clock["t"],
                         sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None)
        return s, f, fake, ctx
    return make


def _writes(fake, name, data):
    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, name), "wb").write(data)
    fake.on_prompt = on_prompt


def test_parse_questions():
    text = "### Q1: sqlite or postgres?\nRecommended: sqlite\n\n### Q2: port?\nRecommended: 8080\nRuling: 8080 — default — low\n"
    qs = author.parse_questions(text)
    assert qs == [{"id": "Q1", "question": "sqlite or postgres?", "recommended": "sqlite", "ruling": None},
                  {"id": "Q2", "question": "port?", "recommended": "8080", "ruling": "8080 — default — low"}]
    assert author.parse_questions("no questions here") == []


def test_an_artefact_turn_submits_and_writes_the_copy(world, tmp_path):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert s.run_state("r-1") == "spec_submitted"
    sub = s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED)
    assert sub.payload["author"] == "claude" and sub.payload["round"] == 1
    copy = tmp_path / "bundle" / "r-1" / "spec-r1.md"
    assert copy.read_bytes() == SPEC_BYTES
    sid = list(fake.sessions)[0]
    prompt_text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "spec-author" in prompt_text and "bircher/artifact.md" in prompt_text
    assert '"body": "B"' in prompt_text or '"body":"B"' in prompt_text


def test_the_brief_carries_findings_on_a_revision_and_the_spec_for_a_plan(world):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    f.review_round("request_revision", findings=b"needs a threat model")
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES + b"\n## Threats\n")
    assert author.author_round(ctx) == "submitted"
    sid = list(fake.sessions)[-1]
    text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "needs a threat model" in text and SPEC_BYTES.decode() in text
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED).payload["author"] == "codex"   # rotation
    f.reviewer = "claude"                                                # the other vendor reviews
    f.review_round("accept")
    _writes(fake, seat.ARTIFACT_OUT, b"# Plan\n\n### Task 1: x\n")
    assert author.author_round(ctx) == "submitted"
    sid = list(fake.sessions)[-1]
    text = fake.sessions[sid]["items"][0]["content"][0]["text"]
    assert "plan-author" in text and (SPEC_BYTES + b"\n## Threats\n").decode() in text


def test_questions_under_grill_human_are_recorded_and_the_session_resumes(world):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    _writes(fake, seat.QUESTIONS_OUT, b"### Q1: db?\nRecommended: sqlite\n")
    assert author.author_round(ctx) == "questions"
    q = s.newest_fact("r-1", EventKind.MODEL_QUESTION)
    assert q.payload["question_id"] == "Q1" and q.payload["question"] == "db?"
    sid = list(fake.sessions)[0]
    f.answer("sqlite", question_ids=("Q1",), cursor=fake.sessions[sid]["items"][-1]["id"])
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert list(fake.sessions) == [sid]                                  # the same session
    assert fake.sessions[sid]["items"][-1]["content"][0]["text"].startswith("Answered; continue.")


def test_rulings_under_grill_model_are_recorded_before_the_submit(world):
    s, f, fake, ctx = world()

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.QUESTIONS_OUT), "wb").write(b"### Q1: db?\nRecommended: sqlite\nRuling: sqlite - simplest - low\n")
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC_BYTES)
    fake.on_prompt = on_prompt
    assert author.author_round(ctx) == "submitted"
    kinds = [x.kind for x in s.facts_for("r-1")]
    assert kinds.index("model_ruling") < kinds.index("artifact_submitted")


def test_an_empty_turn_retries_once_then_fails(world):
    s, f, fake, ctx = world()
    ctx.turn_timeout_s = 10
    fake.on_prompt = lambda sid, text: None
    assert author.author_round(ctx) == "empty_retry"
    empty = s.newest_fact("r-1", EventKind.AUTHOR_EMPTY)
    assert author.author_round(ctx) == "failed"
    create = front.newest_seat(s, "r-1", Role.AUTHOR, "spec", 0)
    assert create["cause"] == empty.id
    assert len(s.facts_of_kind("r-1", EventKind.AUTHOR_EMPTY)) == 1


def test_a_questions_file_that_asks_nothing_is_an_empty_turn(world):
    """A questions file no `### Q<n>:` heading matches asks the run NOTHING.
    Returning "questions" for it parks the loop on a grill with no
    model_question to answer, and every pass after it spends another seat on
    the same unreadable file."""
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    logged = []
    ctx.log = logged.append
    _writes(fake, seat.QUESTIONS_OUT, b"### Question 1: db?\nRecommended: sqlite\n")
    assert author.author_round(ctx) == "empty_retry"
    assert s.newest_fact("r-1", EventKind.AUTHOR_EMPTY) is not None
    assert s.facts_of_kind("r-1", EventKind.MODEL_QUESTION) == []
    assert any("matches no" in m for m in logged)


def test_a_direction_read_at_the_end_of_the_turn_is_not_submitted(world):
    s, f, fake, ctx = world()

    def on_prompt(sid, text):
        ws = fake.sessions[sid]["workspace"]
        os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
        open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC_BYTES)
        fake.add_user_message(sid, "actually, use postgres")
    fake.on_prompt = on_prompt
    assert author.author_round(ctx) == "direction"
    assert s.run_state("r-1") == "queued"
    assert s.newest_fact("r-1", EventKind.ARTIFACT_SUBMITTED) is None
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["text"] == "actually, use postgres"
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    assert len(fake.sessions) == 2                                        # a fresh session


def test_identical_resubmission_reauthors_once_then_stalls(world):
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    f.review_round("request_revision")
    assert author.author_round(ctx) == "reauthor"
    rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rej.payload["command_name"] == "submit_spec"
    assert author.author_round(ctx) == "stall"
    assert front.newest_seat(s, "r-1", Role.AUTHOR, "spec", 0)["cause"] == rej.id


def test_seats_exhausted_is_budget(world):
    # 5, not 4: the driver's birth shape_round has spent one author seat
    # before this test begins (Task 3 rule (b)). The four below still exhaust
    # the bound, and the round under test still has none left.
    s, f, fake, ctx = world(cfg={"max_seats": 5})
    for _ in range(2):
        f._dispatch(Role.AUTHOR, "claude"); f._dispatch(Role.REVIEWER, "codex")
    assert author.author_round(ctx) == "budget"
    assert s.newest_fact("r-1", EventKind.ATTEMPT_DISPATCHED).payload["role"] == "reviewer"


def test_an_artefact_before_the_grill_is_answered_reauthors_then_stalls(world):
    """Live on 2026-09-08: under grill=human the author wrote the spec instead
    of questions, the kernel refused the submit (the author MUST ask at least
    once), and the loop discarded the run as an unexpected refusal. The
    refusal is expected: it becomes the next round's findings and the author
    asks. A second one is the author ignoring that, which is a park."""
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "reauthor"
    rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    assert rej.payload["command_name"] == "submit_spec"
    assert "the grill is open" in rej.payload["detail"]
    # Nothing was submitted, so the phase has no artefact to review.
    assert front.newest_submission(s, "r-1", "spec", 0) is None
    assert author.author_round(ctx) == "stall"
    assert front.newest_seat(s, "r-1", Role.AUTHOR, "spec", 0)["cause"] == rej.id


def test_the_refusal_is_the_next_briefs_findings(world):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    brief = author.author_brief(ctx, phase="spec").decode()
    assert "the grill is open" in brief, "the kernel's reason must reach the author"


def test_the_brief_tells_a_grill_human_author_to_ask_before_it_writes(world):
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    brief = author.author_brief(ctx, phase="spec").decode()
    assert "ask, do not write the spec" in brief
    assert seat.QUESTIONS_OUT in brief

    # Once the human has answered, the instruction is gone: the same text on
    # the resumed turn would tell the author not to write the spec it is now
    # being asked for.
    _writes(fake, seat.QUESTIONS_OUT, b"### Q1: db?\nRecommended: sqlite\n")
    assert author.author_round(ctx) == "questions"
    sid = list(fake.sessions)[0]
    f.answer("sqlite", question_ids=("Q1",), cursor=fake.sessions[sid]["items"][-1]["id"])
    assert "ask, do not write the spec" not in author.author_brief(ctx, phase="spec").decode()


def test_a_grill_model_author_is_not_told_to_ask_first(world):
    s, f, fake, ctx = world()
    assert "ask, do not write the spec" not in author.author_brief(ctx, phase="spec").decode()


def test_the_resume_prompt_says_the_previous_draft_is_gone(world, tmp_path):
    """Live on 2026-09-08. Under grill=human the author wrote questions AND a
    spec on the same turn, against its instruction. The coordinator moved both
    aside before re-prompting with the answers -- it must, or a draft written
    before the human spoke would be submitted as though it answered them --
    and the author, whose own context said it had written the spec, replied
    "the spec is already complete" and ended the turn. No file appeared and
    the coordinator polled the empty path for the whole cap.
    """
    s, f, fake, ctx = world(labels=("bircher:grill", "bircher:autonomous"))
    _writes(fake, seat.QUESTIONS_OUT, b"### Q1: db?\nRecommended: sqlite\n")
    assert author.author_round(ctx) == "questions"
    sid = list(fake.sessions)[0]
    f.answer("sqlite", question_ids=("Q1",), cursor=fake.sessions[sid]["items"][-1]["id"])

    answer = s.newest_fact("r-1", EventKind.HUMAN_ANSWER)
    brief = author.author_brief(ctx, phase="spec", resume_answer=answer).decode()
    assert "moved aside" in brief and seat.ARTIFACT_OUT in brief
    assert "again from scratch" in brief
    assert "sqlite" in brief, "the answers still have to reach the author"


def test_the_revision_author_rotates_by_default(world):
    """The spec's Rotation rule: the vendor that reviewed round r authors the
    revision in round r+1. A reviewer never grades its own prescriptions."""
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    assert author.author_round(ctx) == "submitted"
    first = front.submissions(s, "r-1", "spec", 0)[0].payload["author"]
    f.review_round("request_revision")
    assert author.choose_author_vendor(ctx) != first, "the reviewer authors the revision"


def test_a_revision_brief_requires_dispositions(world):
    """Spec section 3, Author round: on a revision the author must answer
    each finding by number, accepted or rejected with a reason, inside the
    artefact -- so a wrong finding can be refused on the record and the
    document stops growing."""
    s, f, fake, ctx = world()
    _writes(fake, seat.ARTIFACT_OUT, SPEC_BYTES)
    author.author_round(ctx)
    f.review_round("request_revision")
    brief = author.author_brief(ctx, phase="spec").decode()
    assert "## Findings to address" in brief
    assert "## Dispositions are required" in brief
    assert "rejected —" in brief and "accepted —" in brief


def test_a_first_draft_asks_for_no_dispositions(world):
    """There is nothing to disposition before a review has happened. The
    skill text the brief embeds mentions dispositions in its own revision
    section, so the assertion is on the brief's requirement heading, not on
    the word."""
    s, f, fake, ctx = world()
    assert "## Dispositions are required" not in author.author_brief(ctx, phase="spec").decode()
