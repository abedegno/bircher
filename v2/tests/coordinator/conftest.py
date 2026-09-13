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

Fix round 1: several fixtures below exist only because a review found the
first pass's assertions could not fail against the history they were given
-- `ruled_one_piece`'s ruling now carries distinctive text a leak could
actually contain; `handed_back_after_a_rejected_visit_one_plan` gives the
visit-scoping something to leak; the `direction_resolved_*` pair builds the
OTHER resolution path the spec names, which fix round 1 found unreachable
by any fixture at all.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from coordinator import human, phases, seat
from coordinator.effects import KERNEL
from coordinator.session import list_items
from kernel import front as fq
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.slices import RESHAPE_LINE, SHAPE_QUESTION
from kernel.store import Fact, Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import ISSUE, PLAN_BYTES, SLICES_BYTES, SPEC_BYTES, Front

#: The two configured vendors, everywhere a Scenario builds a `Ctx` (Task 8):
#: matches every OTHER coordinator test's `world` fixture, so a scenario that
#: goes on to drive a real turn agrees with the rest of the suite about what
#: exists.
AGENT_IDS = {"claude": "ag_claude", "codex": "ag_codex"}


def _ctx(store, run_id: str, *, omni: FakeOmnigent, base: pathlib.Path) -> phases.Ctx:
    """A `Ctx` for `author_brief`/`phases.round_cause`, AND -- since Task 8's
    tests drive `run_loop`, `stall` and real turns, not only pure reads -- a
    working `fetch`/`server`/`env` wired to *omni*, real directories under
    *base* rather than the placeholder paths a purely-read scenario never
    touched. A real, file-backed `BIRCHER_KERNEL_DB` is required: `stall`'s
    park, `notify_owed`'s comment and every seat's turn perform effects
    through `coordinator.effects.perform_effect`, which reopens the store
    from that path."""
    env = {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": store.path,
           "BIRCHER_RUN_ID": run_id, "PATH": os.environ["PATH"]}
    # A FAKE clock, `sleep` advancing it rather than blocking (the same
    # pattern `test_author_round.py`'s and `test_phase_loop.py`'s `world`
    # fixtures use): `seat.run_turn`'s poll loop waits on `ctx.clock()`
    # against `turn_timeout_s`, and a real `time.sleep` there would make an
    # empty-turn test spend its `turn_timeout_s` in real wall-clock seconds.
    clock = {"t": time.time()}
    return phases.Ctx(
        store=store, run_id=run_id, server="http://srv", repo="o/r", issue_number=1,
        repo_dir=str(base / "repo"), workspaces_root=str(base / "ws"),
        bundle_dir=str(base / "bundle"), agent_ids=AGENT_IDS, host_id="host_h",
        turn_timeout_s=100, default_author="claude", env=env,
        fetch=omni.fetch, clock=lambda: clock["t"],
        sleep=lambda x: clock.__setitem__("t", clock["t"] + x), log=lambda m: None,
    )


def _fact(kind: str, payload: dict, *, actor: str = "claude") -> Fact:
    """A bare fact for `_is_round_cause`, which is a pure predicate over
    `kind`/`payload`/`actor` -- no journal needed to exercise it at all."""
    return Fact(seq=1, id="f-1", run_id="r-1", kind=kind, schema_version=1, mechanism_version=1,
               causal_command_id=None, actor=actor, observed_at_us=0, payload=payload)


class Scenario:
    """`f.ctx` for `author_brief`, plus the `Front` primitives a test needs
    to carry the history one step further.

    Task 8 adds `.omni`, a `FakeOmnigent` every satisfied `sess-create`
    *front* journals is mirrored into (`_sync_sessions`): `Front`'s own
    ceremony writes the kernel's effect rows directly and never touches a
    fake server at all, which is fine for the pure `author_brief`/
    `choose_author_vendor` reads every earlier task's fixtures were built
    for, but a test that goes on to drive `run_loop`, `stall` or `.reply()`
    needs the two to agree on what sessions exist -- and `.reply()`, plus
    every method below that mutates the journal further, keeps them synced.
    """

    def __init__(self, front: Front, *, base: pathlib.Path, monkeypatch=None):
        self.front = front
        self.store, self.run_id = front.store, front.run_id
        self.omni = FakeOmnigent()
        if monkeypatch is not None:
            # Global for the test, not per-scenario: a test that builds more
            # than one Scenario only ever drives ONE of them through a real
            # turn or effect, and by the time it does, this points at that
            # Scenario's own fake.
            monkeypatch.setattr("kernel.cli.subprocess.run", self.omni.run)
            monkeypatch.setattr("coordinator.sessions.add_worktree",
                                lambda repo, sha, path: (os.makedirs(path, exist_ok=True),
                                                         os.path.realpath(path))[1])
        self.ctx = _ctx(front.store, front.run_id, omni=self.omni, base=base)
        self.bundle_dir = self.ctx.bundle_dir
        #: Set by `answered_then_handed_back_from_the_resume_turn`, for the
        #: test that checks its own text reaches the brief.
        self.answer_text: str | None = None
        #: The disagreement park, and a bare non-disagreement one for the
        #: notice test that checks the two never share text -- both are
        #: `fake.disagreed`'s to fill in (`Fake._fact` needs no journal).
        self.park = None
        self.gate_park = None
        #: The text of every message the coordinator sent INTO a session
        #: during `.reply()` -- a dismissal's refusal, most often.
        self.session_replies: list[str] = []
        self._dispatch_baseline = 0
        self._sync_sessions()

    @property
    def spec_ctx(self):
        """An alias for `.ctx`, for a test that builds a scenario at
        `shaping` and reads it again once the run has moved on to the spec
        phase: `Ctx.phase()` reads the store fresh every call, so there is
        only ever the one `Ctx` to have."""
        return self.ctx

    @property
    def seats_dispatched(self) -> int:
        """AUTHOR or REVIEWER dispatches since `._checkpoint()` was last
        called -- the operator dispatch every `run_loop` iteration makes
        does not count, and neither does any seat the fixture itself
        dispatched to build the history under test."""
        rows = self.store.dispatches_for(self.run_id)
        return sum(1 for d in rows[self._dispatch_baseline:] if d["role"] in (Role.AUTHOR, Role.REVIEWER))

    def _checkpoint(self) -> None:
        self._dispatch_baseline = len(self.store.dispatches_for(self.run_id))

    def _sync_sessions(self) -> None:
        """Mirror every satisfied `sess-create` *front* has journalled
        directly into `.omni`, so a listing sees what the journal already
        says exists (see the class docstring)."""
        for row in fq.satisfied_effects(self.store, self.run_id, "sess-create"):
            body = json.loads(row["external_object_id"])
            sid = body["id"]
            if sid in self.omni.sessions:
                continue
            self.omni.sessions[sid] = {**body, "status": "idle", "items": [], "labels": {}}
            self.omni.listed.add(sid)

    def direct(self, text: str) -> None:
        self.front.direct(text)
        self._sync_sessions()

    def empty_turn(self, *, detail: str = "") -> None:
        """A turn that ends with nothing (spec §3): a fresh round cause of
        its own, in whatever phase the run currently sits in -- so a test
        can render the SAME standing facts against a DIFFERENT cause,
        rather than calling `author_brief` twice with nothing between,
        which proves the function writes nothing and nothing about a
        retry."""
        f = self.front
        cause = f._newest_id()
        g = f._dispatch(Role.AUTHOR, f.author)
        sid = f._session(g, cause)
        f._end_turn(g, sid)
        payload = {"session": sid}
        if detail:
            payload["detail"] = detail
        f._cmd(g, "record_author_empty", payload)
        self._sync_sessions()

    def crash_resume(self) -> None:
        """A SECOND dispatch and session for the round's CURRENT cause (spec
        §3: "a crash-resume of that turn, whose cause is unchanged") --
        what the coordinator's own resume path builds, and not the same
        pure call made twice with nothing between it."""
        f = self.front
        cause_id = phases.round_cause(self.ctx).id
        g = f._dispatch(Role.AUTHOR, f.author)
        f._session(g, cause_id)
        self._sync_sessions()

    def reject_plan(self, findings: bytes = b"needs another slice") -> None:
        """A slice plan submitted in the CURRENT visit, then sent back to
        the shaping round with a reviewer's verdict on the record -- the
        history Task 8's vendor tests need to show the `slices` clauses
        hold for the WHOLE visit, verdicts or none (shaping spec §2 *The
        vendors*), not only the easy case of an empty-turn retry."""
        self.front.shape_round(SLICES_BYTES)
        self.front.review_round("request_revision", findings=findings)
        self._sync_sessions()

    def review_spec(self, *, verdict: str, reviewer: str, author: str | None = None,
                    findings: bytes = b"needs work") -> None:
        """A spec submitted (by *author*, defaulting to the fixture's own
        `Front.author`) and reviewed with *verdict* by *reviewer* -- for a
        test that wants a spec `review_verdict` on the record without
        driving the seat through `choose_author_vendor` itself, only
        caring who judged it (Task 8's spec-clause tests: it is the
        REVIEWER's identity the same-phase rotation reads)."""
        if author is not None:
            self.front.author = author
        self.front.author_round(SPEC_BYTES)
        self.front.reviewer = reviewer
        self.front.review_round(verdict, findings=findings)
        self._sync_sessions()

    def approve_one_piece(self, **kw) -> None:
        """The person's resolution, tolerant of being sent where there is no
        dispute to resolve: a stray `approve_one_piece` outside a dispute is
        refused by the kernel (`front.py`'s own `dismiss_human_item` catches
        the same class of refusal the same way) and changes nothing
        `choose_author_vendor` reads, which is the point of sending it."""
        try:
            self.front.approve_one_piece(**kw)
        except NotAuthorized:
            pass
        self._sync_sessions()

    def submit_plan(self, plan: bytes = SLICES_BYTES) -> None:
        """A REAL shaping round for the CURRENT visit, through
        `coordinator.shape.shape_round` -- not `Front.shape_round`'s
        journal shortcut, which never calls `author._copy` at all, so it
        cannot exercise the visit-scoped naming Task 8's Step 7 changes.

        A placeholder is written first at the LAST visit's own name: the
        defect the naming change guards against is the round counter
        moving to the visit while the copy stayed epoch-numbered, which
        would silently overwrite whatever an earlier visit wrote under the
        same round number -- proving it takes a file already there to prove
        it survives."""
        from coordinator import shape
        d = pathlib.Path(self.ctx.bundle_dir) / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "slices-v1-r1.md").write_bytes(b"visit one's own copy, not to be overwritten")

        def on_prompt(sid, text):
            ws = self.omni.sessions[sid]["workspace"]
            os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
            open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(plan)
        self.omni.on_prompt = on_prompt
        shape.shape_round(self.ctx)
        self._sync_sessions()

    def _reply_session(self) -> str:
        if self.park is not None and self.park.payload.get("session_id"):
            return self.park.payload["session_id"]
        return phases._newest_author_session(self.ctx)

    def seed_reply(self, sid: str, text: str) -> None:
        """A person's message added to *sid*, safe against `human.cursor`'s
        empty-listing fallback: with nothing yet carrying a recorded
        cursor, `cursor()` starts at `listing[0]` -- in production always
        the coordinator's own prompt, landed before a person ever replies.
        `Front`'s ceremony never sends one into the fake at all (it
        journals the kernel's effect rows directly), so a bare message here
        would be its own listing's first item and read as the starting
        point rather than as something unread. An assistant-role
        placeholder gives the listing that first item without being
        mistaken for a human message itself. Shared by `.reply()` and
        `fake.disagreed`'s `pending_reply`, which seeds a message the SAME
        way before any park exists to carry a `.reply()` call."""
        if not self.omni.sessions[sid]["items"]:
            self.omni.add_assistant_text(sid, "(the model's turn)")
        self.omni.add_user_message(sid, text)

    def reply(self, text: str) -> str | None:
        """One person's message, read exactly as `human.human_pass` reads
        the first listing of a park: added to the session, discriminated,
        and -- when refused -- answered (`human.reply_refusals`). Returns
        `take_listing`'s own vocabulary, so a test can check what the
        coordinator did with what was typed without driving the whole loop
        for it."""
        from kernel.dispatch import dispatch
        # The operator fence `run_loop` takes every iteration: `take_listing`
        # and `reply_refusals` both perform effects under `ctx.generation`,
        # which is otherwise `None` for a Scenario nothing has dispatched
        # yet -- a test that calls `.reply()` without first driving the loop.
        self.ctx.generation = dispatch(self.store, self.run_id, actor="coordinator", role=Role.OPERATOR).generation
        sid = self._reply_session()
        self.seed_reply(sid, text)
        listing = list_items(self.ctx.server, sid, fetch=self.ctx.fetch)
        kind = human.take_listing(self.ctx, sid, listing)
        before = len(self.omni.posts)
        human.reply_refusals(self.ctx)
        self.session_replies += [body["data"]["content"][0]["text"] for _, body in self.omni.posts[before:]]
        self._sync_sessions()
        return kind


class Fake:
    """The `fake` fixture: one constructor per scenario the author-brief
    tests need, each on its own store so the scenarios never share state."""

    def __init__(self, tmp_path, monkeypatch=None):
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch
        self._n = 0
        # `_is_round_cause`'s own table (spec §2 `request_reshape` is a round
        # cause with no findings; spec §3 admits a `model_ruling` only for
        # the shape question, never a grill ruling).
        self.fact_shape_ruling = _fact(EventKind.MODEL_RULING, {"question_id": SHAPE_QUESTION})
        self.fact_grill_ruling = _fact(EventKind.MODEL_RULING, {"question_id": "Q1"})
        self.fact_reshape_requested = _fact(EventKind.RESHAPE_REQUESTED, {"reasoning": "x"})
        self.fact_refused_request_reshape = _fact(
            EventKind.COMMAND_REJECTED, {"command_name": "request_reshape"}, actor="claude")

    def _store(self, name: str) -> Store:
        self._n += 1
        return Store.open(self._tmp_path / f"{name}-{self._n}.db")

    def _scenario(self, front: Front) -> Scenario:
        """Every `fake.<name>()` builds its `Scenario` through here (Task
        8): one base directory per scenario, keyed on the same counter
        `._store` just advanced, so two scenarios in one test never share a
        `bundle_dir`/`workspaces_root` even though every `Front` uses the
        same run id."""
        base = self._tmp_path / f"base-{self._n}"
        return Scenario(front, base=base, monkeypatch=self._monkeypatch)

    # -- the question's cause, and the causes it must not fire on ------------

    def ruled_one_piece(self, comments: list[str] | None = None, *, ruling_vendor: str = "claude") -> Scenario:
        """A run ruled one piece at birth: `queued`, the cause is the shape
        `model_ruling`, no hand-back in the epoch -- the question's own
        cause. `shape=False` plus an explicit `shape_round` (fix round 1,
        Q3): `Front`'s own defaults ("one coherent change" / "a spec
        round") never appear in any assertion, so a mutation that leaked
        the ruling's own words into the question left every check green;
        this ruling's reasoning and cost are distinctive enough to leak
        somewhere a test would notice. `comments` builds the issue with
        them, so a test of the frozen snapshot's filter goes through the
        real `kernel.bundle.snapshot` path `create_run` calls, not a
        hand-built bundle that cannot exercise that filter at all.
        `ruling_vendor` (Task 8): the shaper's own vendor, so
        `choose_author_vendor`'s spec clause has something to pick the
        OTHER of."""
        issue = ISSUE if comments is None else {
            **ISSUE,
            "comments": [{"id": i + 1, "author": "someone", "body": body}
                        for i, body in enumerate(comments)],
        }
        f = Front(self._store("ruled"), "r-1", issue=issue, shape=False, author=ruling_vendor)
        f.shape_round(reasoning="the export flow and the import flow are one thing",
                     cost="the spec would find one surface")
        return self._scenario(f)

    def ruled_one_piece_after_an_earlier_epochs_accepted_spec(self) -> Scenario:
        """Fix round 1, Q4 line 22: `ruled_one_piece` alone has no
        submission anywhere in its journal, so "no previous draft" on the
        question turn could not fail no matter how `prior` was scoped. A
        second epoch's question turn, after the first epoch's spec was
        drafted, reviewed and approved end to end -- something real for
        `ctx.epoch()`'s scoping (`front.newest_submission`'s `epoch_n`) to
        leak if it did not hold."""
        f = Front(self._store("ruled_new_epoch"), "r-1")
        f.to_specified()
        f.revise({"number": 1, "title": "T", "body": "a materially different issue body, revised",
                 "labels": ["bircher:autonomous"], "comments": []})
        return self._scenario(f)

    def refused_submission_retry(self) -> Scenario:
        """Live on 2026-09-08: under `grill: human`, with no answer yet, the
        author wrote the spec anyway and the kernel refused `submit_spec`.
        The refusal is a `command_rejected`, one of the causes the question
        must never fire on."""
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
        return self._scenario(f)

    def empty_turn_retry(self) -> Scenario:
        """A turn that ended with neither an artefact nor a hand-back:
        `author_empty` with no `detail` -- the plain empty turn, not
        `malformed_reshape`'s."""
        f = Front(self._store("empty"), "r-1")
        s = self._scenario(f)
        s.empty_turn()
        return s

    def revision_turn(self) -> Scenario:
        """A spec drafted, reviewed, and sent back: the cause is the
        reviewer's `review_verdict`, in an epoch that holds a shape ruling
        and no hand-back -- the composed turn the availability instruction
        exists for."""
        f = Front(self._store("revision"), "r-1")
        f.author_round(SPEC_BYTES)
        f.review_round("request_revision", findings=b"split the store from the API")
        return self._scenario(f)

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
        return self._scenario(f)

    def direction_resolved_one_piece(self, *, direction_text: str = "slice it into pieces",
                                     ruling_reasoning: str = "on reflection, only the export flow is its own piece"
                                     ) -> Scenario:
        """Fix round 1, B1/B3: the OTHER resolution the spec names -- "the
        `shape` ruling recorded after their direction" -- built for real: a
        hand-back, the disputed ruling, a direction, and the shaper's
        ruling in the visit the direction opens. `round_cause` is THAT
        ruling: not the direction (`dispute`'s own `resolution` field, on
        this path) and not the disputed ruling it overruled."""
        f = Front(self._store("direction_resolved"), "r-1")
        f.request_reshape("the issue names a store, an API and a page", vendor="codex")
        f.shape_round(reasoning="the login and export flows are one thing",
                     cost="the spec would find one surface")           # the disputed ruling
        f.direct(direction_text)                                       # resolves; opens the next visit
        f.shape_round(reasoning=ruling_reasoning, cost="the spec would find one surface")   # the ruling that followed
        return self._scenario(f)

    # -- the resolution --------------------------------------------------------

    def approved_one_piece(self) -> Scenario:
        """The dispute resolved by the person's approval, with no spec ever
        drafted before the hand-back: the plainest resolution, nothing to
        disposition."""
        f = Front(self._store("approved"), "r-1")
        f.request_reshape("the issue names a store, an API and a page", vendor="codex")
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.approve_one_piece()
        return self._scenario(f)

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
        return self._scenario(f)

    def direction_resolved_with_prior_review(self, findings: bytes, *,
                                             direction_text: str = "slice it into pieces") -> Scenario:
        """B2 on the direction path: a spec drafted and sent to revision
        BEFORE the hand-back, then a DIRECTION resolves the dispute instead
        of an approval -- the reviewer's outstanding findings must reach
        the author on this path too, not only the approval's."""
        f = Front(self._store("direction_reopened"), "r-1")
        f.author_round(SPEC_BYTES)
        f.review_round("request_revision", findings=findings)
        f.request_reshape("the issue names a store, an API and a page")
        f.shape_round(reasoning="still one piece, on the shaper's first look",
                     cost="the spec would find one surface")
        f.direct(direction_text)
        f.shape_round(reasoning="agreed, one piece after the direction",
                     cost="the spec would find one surface")
        return self._scenario(f)

    # -- the hand-back and its standing sections --------------------------------

    def handed_back(self, reasoning: str | None = None, *,
                    ruling_vendor: str = "claude", hand_back_vendor: str = "codex") -> Scenario:
        """The bare hand-back: a ruling, then the spec author's
        `request_reshape`. The run sits at `shaping`; no disputed ruling has
        been recorded yet. `ruling_vendor`/`hand_back_vendor` (Task 8): the
        two seats' vendors, so `choose_author_vendor`'s hand-back-visit
        clause has both to compare -- defaulting to two DIFFERENT vendors,
        since `test_a_hand_back_from_the_rulings_own_vendor_goes_to_the_other`
        is the one that wants them the same."""
        f = Front(self._store("handed_back"), "r-1", author=ruling_vendor)
        f.request_reshape(reasoning or "the issue names a store, an API and a page", vendor=hand_back_vendor)
        return self._scenario(f)

    def handed_back_and_disputed(self, reasoning: str | None = None,
                                 disputed_reasoning: str = "still one piece") -> Scenario:
        """`handed_back`, plus the shaper's reaffirming ruling in the
        hand-back's own visit -- the disputed ruling a direction needs in
        order to resolve anything and open the visit after it
        (`front._visit_boundaries`'s direction branch requires `disputed`;
        `handed_back` alone never gives a direction one to resolve, which
        is fix round 1's Q5)."""
        f = Front(self._store("disputed"), "r-1")
        f.request_reshape(reasoning or "the issue names a store, an API and a page", vendor="codex")
        f.shape_round(reasoning=disputed_reasoning, cost="the spec would find one surface")
        return self._scenario(f)

    def disagreed(self, *, ruling_vendor: str = "claude", hand_back_vendor: str = "codex",
                 ruling_reasoning: str = "still one piece, the export and import flows are one thing",
                 hand_back_reasoning: str = "the issue names a store, an API and a page",
                 park: bool = True, pending_reply: str | None = None,
                 resolve_in_the_gap: bool = False, policy: str | None = None,
                 unanswered_question: bool = False) -> Scenario:
        """`handed_back_and_disputed`'s history -- an unresolved dispute,
        both rulings and the hand-back on the record -- with the Task 8
        surface those tests need beyond `choose_author_vendor`: the park
        (`.park`, `.gate_park`), the reasoning text quoted in the notice and
        the prompt (`.ruling_reasoning`, `.hand_back_reasoning`), and a
        knob per crash/reply scenario.

        `park=True` (the default) records the `disagreement` park through
        the kernel, at the session `run_loop`'s own `stall` call would name
        (`phases._newest_author_session`) -- the disputed ruling's session.
        `park=False` leaves the dispute unresolved with NO park recorded,
        the history `run_loop`'s new branch exists for.

        `pending_reply` seeds a message in that same session BEFORE any
        park exists, for the "taken without parking" path (spec §2 *The
        park is the disagreement's notice, not its definition*).

        `resolve_in_the_gap` monkeypatches `coordinator.session.list_items`
        so the FIRST call `stall` makes -- its own pre-park listing --
        resolves the dispute as a side effect, landing exactly in the gap
        between that listing and the `park` command the spec names: a
        person's command arriving between the two. A competent script is
        wired to the fake so the run, once resolved, can actually finish
        the phases it now has to author for real rather than dead-ending on
        an unscripted empty turn.

        `policy`/`unanswered_question` build an epoch-scoped grill question
        asked and left unanswered BEFORE the hand-back, still standing once
        the run is back at `shaping`: the history
        `test_approve_is_the_approval_even_in_a_grill_epoch` and
        `test_the_grill_branch_is_skipped_at_every_shaping_state` need."""
        labels = ["bircher:autonomous"] if policy is None else [policy, "bircher:autonomous"]
        front = Front(self._store("disagreed"), "r-1", author=ruling_vendor, labels=labels)
        if unanswered_question:
            front.ask_round([("Q1", "which auth flow matters here?")])
        front.request_reshape(hand_back_reasoning, vendor=hand_back_vendor)
        front.shape_round(reasoning=ruling_reasoning, cost="the spec would find one surface")
        scenario = self._scenario(front)
        scenario.ruling_reasoning = ruling_reasoning
        scenario.hand_back_reasoning = hand_back_reasoning
        # A bare, non-disagreement park for the notice test that checks the
        # two never share text: no journal needed, `park_notice_body` reads
        # only the fact's own payload.
        scenario.gate_park = _fact(EventKind.PARKED, {"reason": "gate", "phase": "spec", "session_id": None})
        if resolve_in_the_gap:
            import coordinator.session as _session_mod
            original_list_items = _session_mod.list_items

            def _resolve_then_list(server, conv_id, **kw):
                # The gap `_check_park`'s own guard exists for: a person's
                # command lands between `stall`'s listing and its park.
                result = original_list_items(server, conv_id, **kw)
                front.approve_one_piece()
                return result
            self._monkeypatch.setattr("coordinator.session.list_items", _resolve_then_list)

            def on_prompt(sid, text):
                ws = scenario.omni.sessions[sid]["workspace"]
                os.makedirs(os.path.join(ws, "bircher"), exist_ok=True)
                if text.startswith("# Bircher spec author brief"):
                    open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(SPEC_BYTES)
                elif text.startswith("# Bircher plan author brief"):
                    open(os.path.join(ws, seat.ARTIFACT_OUT), "wb").write(PLAN_BYTES)
                elif text.startswith("# Bircher review brief"):
                    phase = "spec" if "of a spec" in text else "plan"
                    h = scenario.store.phase_artifact(scenario.run_id, phase)
                    if h:
                        from kernel.brief import REVIEW_OUT
                        open(os.path.join(ws, REVIEW_OUT), "w").write(f"fine\n\nVERDICT: PASS {h[:8]}\n")
            scenario.omni.on_prompt = on_prompt
        if pending_reply:
            sid = phases._newest_author_session(scenario.ctx)
            scenario.seed_reply(sid, pending_reply)
        if park:
            sid = phases._newest_author_session(scenario.ctx)
            front.park(reason="disagreement", phase="slices", session_id=sid, cursor_item_id=None)
            scenario._sync_sessions()
            scenario.park = scenario.store.newest_fact(scenario.run_id, EventKind.PARKED)
        scenario._checkpoint()
        return scenario

    def grill_open_at_shaping(self) -> Scenario:
        """A `bircher:grill` epoch's question standing unanswered while the
        run sits at `shaping` after a hand-back (revision 16, shaping spec
        §2): the exact history `record_human_answer`'s refusal from every
        shaping state guards against, so the coordinator's own reading has
        to skip the grill branch there rather than lean on the kernel's
        refusal."""
        return self.disagreed(policy="bircher:grill", unanswered_question=True, park=False)

    def handed_back_after_a_rejected_visit_one_plan(self, reasoning: str | None = None) -> Scenario:
        """Fix round 1, Q4 line 89: `handed_back` alone has no submission in
        ANY visit, and a bare hand-back's own cause carries no findings
        either way (`_findings_for`'s `RESHAPE_REQUESTED` branch), so "not
        in brief" could not fail no matter what `prior`'s visit scoping did
        -- the previous-draft block needs `findings` truthy too, and giving
        visit 1 alone a plan changes nothing there either.

        Visit 1 holds a rejected plan; visit 2 opens and gets a DIRECTION
        that resolves nothing (no disputed ruling exists yet in visit 2, so
        `front._visit_boundaries`'s direction branch does not fire) and so
        stays IN visit 2 as its own round cause -- a `human_direction`,
        which `_findings_for` DOES render as findings. Now `findings` is
        truthy with NO submission yet in visit 2: unscoped, `prior` would be
        visit 1's rejected plan; scoped, `None`. This is the history where
        the two answers actually differ."""
        f = Front(self._store("handed_back_v1_plan"), "r-1", shape=False)
        f.shape_round(SLICES_BYTES)                                              # visit 1's own plan
        f.review_round("request_revision", findings=b"visit one's own findings, not a hand-back's")
        f.shape_round(reasoning="one piece after all", cost="the spec would find one surface")
        f.request_reshape(reasoning or "the issue names a store, an API and a page")   # opens visit 2
        f.direct("consider the auth flow too")   # into visit 2; resolves nothing, opens nothing
        return self._scenario(f)

    def answered_then_handed_back_from_the_resume_turn(self) -> Scenario:
        """`grill: human`: a question, the person's answer, a hand-back --
        legal from the resume turn (shaping spec §3) -- then the shaper's
        reaffirming ruling and the person's approval, reaching a REAL spec
        round at `queued` (fix round 1, Q7: composing a `phase="spec"`
        brief while the run actually sat at `shaping` proved nothing about
        the seat the spec's clause is about). The epoch's `human_answer` is
        a standing section of this later, genuinely-spec brief, so a seat
        of another vendor still has it in front of it."""
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
        f.shape_round(reasoning="still one piece", cost="the spec would find one surface")
        f.approve_one_piece()
        scenario = self._scenario(f)
        scenario.answer_text = answer_text
        return scenario

    def malformed_reshape(self) -> Scenario:
        """A `reshape.md` that does not parse: the empty turn WITH a reason
        (shaping spec §3) -- the exact `detail` `author.author_round`
        supplies for a hand-back missing its `Reasoning:` block."""
        f = Front(self._store("malformed"), "r-1")
        s = self._scenario(f)
        detail = (f"reshape.md did not parse: the first non-blank line must be "
                  f"exactly `{RESHAPE_LINE}` and a non-empty `Reasoning:` "
                  f"block must follow")
        s.empty_turn(detail=detail)
        return s

    def malformed_reshape_with_a_prior_submission(self) -> Scenario:
        """Fix round 2, item 1: `malformed_reshape` alone never submits
        anything, so the previous-draft guard's `and findings` half (drop
        it and the whole suite stays green) has nothing to leak -- `prior`
        is `None` regardless of the guard. `author_brief`'s parse-failure
        branch is phase-agnostic (only the spec round supplies a `detail`
        today; minor finding 3), so this builds the same shape in the
        SHAPING round instead: a plan submitted and rejected, then a
        malformed retry in the SAME visit, where `prior` is genuinely
        non-`None`."""
        f = Front(self._store("malformed_with_prior"), "r-1", shape=False)
        f.shape_round(SLICES_BYTES)
        f.review_round("request_revision", findings=b"visit one's own findings")
        s = self._scenario(f)
        detail = (f"reshape.md did not parse: the first non-blank line must be "
                  f"exactly `{RESHAPE_LINE}` and a non-empty `Reasoning:` "
                  f"block must follow")
        s.empty_turn(detail=detail)
        return s


@pytest.fixture
def fake(tmp_path, monkeypatch):
    return Fake(tmp_path, monkeypatch)
