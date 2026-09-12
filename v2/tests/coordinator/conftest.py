"""Shared history-builders for the coordinator's brief tests (shaping spec
§3, revision 16 -- *the spec round's question*).

Every `fake.<name>()` below builds a small, REAL journal through
`tests.kernel.front.Front`, the same driver every kernel test in this suite
uses to reach a state -- never a hand-built bundle or a mock standing in for
one. A fixture that cannot produce the wrong history proves nothing about
what `author_brief` reads from it: the whole point of `_question_section`
and `_findings_for` reading the round's CAUSE rather than a predicate on the
epoch is that the two can come apart, and only a fixture built fact-by-fact
can put them in the states where they do.
"""
from __future__ import annotations

import time

import pytest

from coordinator import phases
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.slices import RESHAPE_LINE
from kernel.store import Fact, Store
from tests.kernel.front import ISSUE, SPEC_BYTES, Front


def _ctx(store, run_id: str) -> phases.Ctx:
    """A `Ctx` for `author_brief`/`phases.round_cause`, which between them
    read only `store`, `run_id` and `epoch()` off it. Every other field is a
    placeholder: nothing built here dispatches a real turn against it."""
    return phases.Ctx(
        store=store, run_id=run_id, server="http://srv", repo="o/r", issue_number=1,
        repo_dir="/repo", workspaces_root="/ws", bundle_dir="/bundle",
        agent_ids={"claude": "ag_claude", "codex": "ag_codex"}, host_id="host_h",
        turn_timeout_s=100, default_author="claude", env={},
        fetch=None, clock=time.time, sleep=lambda s: None, log=lambda m: None,
    )


def _fact(kind: str, payload: dict, *, actor: str = "claude") -> Fact:
    """A bare fact for `_is_round_cause`, which is a pure predicate over
    `kind`/`payload`/`actor` -- no journal needed to exercise it at all."""
    return Fact(seq=1, id="f-1", run_id="r-1", kind=kind, schema_version=1, mechanism_version=1,
               causal_command_id=None, actor=actor, observed_at_us=0, payload=payload)


class Scenario:
    """`f.ctx` for `author_brief`, plus the `Front` primitives a test needs
    to carry the history one step further (`direct`, and whatever else a
    later task adds)."""

    def __init__(self, front: Front):
        self.front = front
        self.store, self.run_id = front.store, front.run_id
        self.ctx = _ctx(front.store, front.run_id)
        #: Set by `answered_then_handed_back_from_the_resume_turn`, for the
        #: test that checks its own text reaches the brief.
        self.answer_text: str | None = None

    def direct(self, text: str) -> None:
        self.front.direct(text)


class Fake:
    """The `fake` fixture: one constructor per scenario the author-brief
    tests need, each on its own store so the scenarios never share state."""

    def __init__(self, tmp_path):
        self._tmp_path = tmp_path
        self._n = 0
        # `_is_round_cause`'s own table (spec §2 `request_reshape` is a round
        # cause with no findings; spec §3 admits a `model_ruling` only for
        # the shape question, never a grill ruling).
        self.fact_shape_ruling = _fact(EventKind.MODEL_RULING, {"question_id": "shape"})
        self.fact_grill_ruling = _fact(EventKind.MODEL_RULING, {"question_id": "Q1"})
        self.fact_reshape_requested = _fact(EventKind.RESHAPE_REQUESTED, {"reasoning": "x"})
        self.fact_refused_request_reshape = _fact(
            EventKind.COMMAND_REJECTED, {"command_name": "request_reshape"}, actor="claude")

    def _store(self, name: str) -> Store:
        self._n += 1
        return Store.open(self._tmp_path / f"{name}-{self._n}.db")

    # -- the question's cause, and the four it must not fire on --------------

    def ruled_one_piece(self, comments: list[str] | None = None) -> Scenario:
        """A run ruled one piece at birth (`Front`'s default): `queued`, the
        cause is the shape `model_ruling`, no hand-back in the epoch -- the
        question's own cause. `comments` builds the issue with them, so a
        test of the frozen snapshot's filter goes through the real
        `kernel.bundle.snapshot` path `create_run` calls, not a hand-built
        bundle that cannot exercise that filter at all."""
        issue = ISSUE if comments is None else {
            **ISSUE,
            "comments": [{"id": i + 1, "author": "someone", "body": body}
                        for i, body in enumerate(comments)],
        }
        f = Front(self._store("ruled"), "r-1", issue=issue)
        return Scenario(f)

    def refused_submission_retry(self) -> Scenario:
        """Live on 2026-09-08: under `grill: human`, with no answer yet, the
        author wrote the spec anyway and the kernel refused `submit_spec`.
        The refusal is a `command_rejected`, one of the four causes the
        question must never fire on."""
        f = Front(self._store("refused"), "r-1",
                  issue={**ISSUE, "labels": ["bircher:grill", "bircher:autonomous"]})
        cause = f._newest_id()
        g = f._dispatch(Role.AUTHOR, f.author)
        sid = f._session(g, cause)
        f._end_turn(g, sid)
        h = put_artifact(f.store, SPEC_BYTES)
        try:
            f._cmd(g, "submit_spec", {"artifact_hash": h})
        except NotAuthorized:
            pass                                          # the rejection is the point
        return Scenario(f)

    def empty_turn_retry(self) -> Scenario:
        """A turn that ended with neither an artefact nor a hand-back:
        `author_empty` with no `detail` -- the plain empty turn, not
        `malformed_reshape`'s."""
        f = Front(self._store("empty"), "r-1")
        cause = f._newest_id()
        g = f._dispatch(Role.AUTHOR, f.author)
        sid = f._session(g, cause)
        f._end_turn(g, sid)
        f._cmd(g, "record_author_empty", {"session": sid})
        return Scenario(f)

    def revision_turn(self) -> Scenario:
        """A spec drafted, reviewed, and sent back: the cause is the
        reviewer's `review_verdict`, in an epoch that holds a shape ruling
        and no hand-back -- the composed turn the availability instruction
        exists for."""
        f = Front(self._store("revision"), "r-1")
        f.author_round(SPEC_BYTES)
        f.review_round("request_revision", findings=b"split the store from the API")
        return Scenario(f)

    def after_approve_one_piece(self) -> Scenario:
        """Spec §2: 'After approve_one_piece no turn of the epoch asks.' A
        round after the resolution's OWN turn, caused by a `review_verdict`
        rather than by the resolution itself -- so this is not merely
        `approved_one_piece` again under another name."""
        f = Front(self._store("after_approve"), "r-1")
        f.request_reshape("the issue names a store, an API and a page", vendor="codex")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.approve_one_piece()
        f.author_round(SPEC_BYTES)
        f.review_round("request_revision", findings=b"needs a migration plan")
        return Scenario(f)

    # -- the resolution ------------------------------------------------------

    def approved_one_piece(self) -> Scenario:
        """The dispute resolved by the person's approval, with no spec ever
        drafted before the hand-back: the plainest resolution, nothing to
        disposition."""
        f = Front(self._store("approved"), "r-1")
        f.request_reshape("the issue names a store, an API and a page", vendor="codex")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.approve_one_piece()
        return Scenario(f)

    def spec_reviewed_then_handed_back_then_approved(self, findings: bytes) -> Scenario:
        """A spec drafted and sent to revision BEFORE the hand-back -- the
        revision turn that disputes the shape -- then the disputed ruling,
        then the person's approval: the reviewer's outstanding findings must
        reach the author beneath the resolution, not be dropped by it."""
        f = Front(self._store("reviewed_handed_back"), "r-1")
        f.author_round(SPEC_BYTES)
        f.review_round("request_revision", findings=findings)
        f.request_reshape("the issue names a store, an API and a page")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.approve_one_piece()
        return Scenario(f)

    # -- the hand-back and its standing sections -----------------------------

    def handed_back(self, reasoning: str | None = None) -> Scenario:
        """The bare hand-back: a ruling, then the spec author's
        `request_reshape`. The run sits at `shaping`; no disputed ruling has
        been recorded yet."""
        f = Front(self._store("handed_back"), "r-1")
        f.request_reshape(reasoning or "the issue names a store, an API and a page")
        return Scenario(f)

    def answered_then_handed_back_from_the_resume_turn(self) -> Scenario:
        """`grill: human`: a question, the person's answer, then a hand-back
        -- legal from the resume turn (shaping spec §3). The epoch's
        `human_answer` is a standing section of every later composed spec
        brief, so a seat of another vendor still has it in front of it."""
        f = Front(self._store("answered_handed_back"), "r-1",
                  issue={**ISSUE, "labels": ["bircher:grill", "bircher:autonomous"]})
        cause = f._newest_id()
        g = f._dispatch(Role.AUTHOR, f.author)
        sid = f._session(g, cause)
        f._end_turn(g, sid)
        f._cmd(g, "record_model_question", {"question_id": "Q1", "question": "which auth?"})
        answer_text = "use OAuth end to end, session cookies, no local passwords"
        f.answer(answer_text, question_ids=("Q1",), cursor=sid)
        f.request_reshape("the issue names a store, an API and a page")
        scenario = Scenario(f)
        scenario.answer_text = answer_text
        return scenario

    def malformed_reshape(self) -> Scenario:
        """A `reshape.md` that does not parse: the empty turn WITH a reason
        (shaping spec §3) -- the exact `detail` `author.author_round`
        supplies for a hand-back missing its `Reasoning:` block."""
        f = Front(self._store("malformed"), "r-1")
        cause = f._newest_id()
        g = f._dispatch(Role.AUTHOR, f.author)
        sid = f._session(g, cause)
        f._end_turn(g, sid)
        detail = (f"reshape.md did not parse: the first non-blank line must be "
                  f"exactly `{RESHAPE_LINE}` and a non-empty `Reasoning:` "
                  f"block must follow")
        f._cmd(g, "record_author_empty", {"session": sid, "detail": detail})
        return Scenario(f)


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)
