"""The front-half driver every migrated test uses.

It performs the ceremony a coordinator performs -- dispatch, a satisfied
sess-create whose snapshot names the actor, a satisfied sess-prompt, the
turn's end as a fact, a satisfied sess-stop, then the command -- against
the kernel's Python API. A test that needs a run at `planned` gets one by
the only path the kernel admits. Tasks 8-12 add the guards that make each
step load-bearing; each of those tasks mutates this driver to prove it.
"""
from __future__ import annotations

import argparse
import json
import sys

from kernel import front as fq
from kernel.artifacts import put_artifact
from kernel.authz import phase_of
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.enqueue import _run_exists, create_run
from kernel.events import EventKind
from kernel.store import Store

SPEC_BYTES = b"# Spec\n\nThe thing, specified.\n"
PLAN_BYTES = b"# Plan\n\n### Task 1: do the thing\n\n- [ ] Step 1\n"
SLICES_BYTES = b"""# Slicing T

Why: two pieces.

## Slice 1: The store
Scope: Add the table. Add the migration. Add the reads.
Non-goals: the API.
Depends on: none

## Slice 2: The API
Scope: Add the endpoint. Wire it to the store. Cover it with a test.
Non-goals: the client.
Depends on: 1
"""


class Front:
    def __init__(self, store, run_id: str, *, author: str = "claude",
                 reviewer: str = "codex", labels=("bircher:autonomous",),
                 base_sha: str = "0" * 40, base_repo: str = "o/r",
                 issue: dict | None = None, project_config: dict | None = None,
                 existing: bool = False, shape: bool = True) -> None:
        self.store, self.run_id = store, run_id
        self.author, self.reviewer, self.base_sha = author, reviewer, base_sha
        self._n = 0
        born = _run_exists(store, run_id)
        if existing:
            assert born, f"run {run_id} does not exist"
        if born:
            # A second driver over a run that was already born. An idempotency
            # key is unique within the RUN, not within this driver: the birth
            # shape_round has already spent `record_turn_ended-1`, so a driver
            # numbering from 1 again would be refused for reusing a key on a
            # different request. Seed from what the run has already been
            # asked, and shape nothing -- it is born, and `create_run` would
            # only replay. A fresh run has been asked nothing and numbers
            # from 1 as before.
            self._n = len(store.facts_of_kind(run_id, EventKind.COMMAND_REQUESTED))
            return
        issue = issue or {"number": 1, "title": "T", "body": "B",
                          "labels": list(labels), "comments": []}
        create_run(store, run_id=run_id, base_repo=base_repo, base_sha=base_sha,
                   issue=issue, project_config=project_config or {})
        # Every run is born in `shaping` (shaping spec §2). The driver rules
        # it one piece by default, so a test of the spec or plan phase gets
        # its run at `queued` by the only path the kernel admits (planning
        # ruling 3); a test of the shaping phase passes shape=False.
        if shape:
            self.shape_round()

    # -- primitives ----------------------------------------------------------

    def _key(self, tag: str) -> str:
        self._n += 1
        return f"{tag}-{self._n}"

    def state(self) -> str:
        return self.store.run_state(self.run_id)

    def phase(self) -> str:
        return phase_of(self.state())

    def epoch(self) -> int:
        return fq.epoch(self.store, self.run_id)

    def _newest_id(self) -> str:
        return self.store.facts_for(self.run_id)[-1].id

    def _cmd(self, generation: int, name: str, payload: dict):
        return submit(self.store, Command(
            name=name, run_id=self.run_id,
            expected_version=self.store.run_version(self.run_id),
            idempotency_key=self._key(name), generation=generation, payload=payload,
        ))

    def _dispatch(self, role: str, actor: str) -> int:
        return dispatch(self.store, self.run_id, actor=actor, role=role).generation

    def _journal(self, generation: int, key: str, intent: dict, external: str | None) -> None:
        # The effect ROW id is a global primary key while the idempotency key
        # is unique only within a run, so the run id goes in the row id. Two
        # runs in one store both open session `s-1` under generation 1, and
        # without this the second one collides on the id rather than on
        # anything the kernel would refuse.
        self.store.journal_intent(f"e-{self.run_id}-{key}", self.run_id, generation,
                                  "session_control", key, intent)
        self.store.mark_effect(key, "confirmed", external, run_id=self.run_id)

    def _create(self, generation: int, cause: str, *, actor: str | None = None) -> str:
        """A satisfied sess-create under *generation* -- and nothing else. The
        snapshot names the BUNDLE (`v2_author_<vendor>`), as the server does;
        the dispatch names the vendor (ruling 14)."""
        actor = actor or self.store.dispatches_for(self.run_id)[-1]["actor"]
        sid = f"s-{generation}"
        phase, epoch = self.phase(), self.epoch()
        ckey = f"sess-create:{self.run_id}:{generation}"
        body = {"agent_id": f"agent-{actor}", "host_id": "host_h1",
                "workspace": f"/w/{self.run_id}/{sid}", "title": ckey}
        self._journal(generation, ckey, {
            "argv": ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions",
                     "-H", "content-type: application/json", "-d", json.dumps(body)],
            "obligation": {"kind": "sess-create", "run": self.run_id,
                           "phase": phase, "epoch": epoch, "cause": cause},
        }, json.dumps({"id": sid, "agent_id": body["agent_id"], "agent_name": f"v2_author_{actor}",
                       "host_id": "h1", "workspace": body["workspace"], "title": ckey}))
        return sid

    def _newest_author_session(self) -> str | None:
        seat = fq.newest_seat(self.store, self.run_id, Role.AUTHOR, self.phase(), self.epoch())
        return None if seat is None else seat["session"]["id"]

    def _prompt(self, generation: int, sid: str, cause: str) -> None:
        phase, epoch = self.phase(), self.epoch()
        prompt_hash = put_artifact(self.store, f"prompt for {sid} caused by {cause}".encode())
        pkey = f"sess-prompt:{sid}:{generation}:{cause}"
        self._journal(generation, pkey, {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-prompt", "session": sid,
                           "phase": phase, "epoch": epoch, "cause": cause},
            "body": {"artifact": prompt_hash},
        }, f"item-{sid}-{generation}")

    def _session(self, generation: int, cause: str, *, actor: str | None = None) -> str:
        """A satisfied sess-create and sess-prompt under *generation*."""
        sid = self._create(generation, cause, actor=actor)
        self._prompt(generation, sid, cause)
        return sid

    def _end_turn(self, generation: int, sid: str, ended: str = "file") -> None:
        self._cmd(generation, "record_turn_ended", {"session": sid, "ended": ended})
        te = self.store.newest_fact(self.run_id, EventKind.TURN_ENDED)
        skey = f"sess-stop:{sid}:{generation}"
        self._journal(generation, skey, {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-stop", "session": sid, "cause": te.id},
            "body": {"event": "stop_session"},
        }, "")

    # -- rounds ----------------------------------------------------------------

    def shape_round(self, plan: bytes | None = None, *, ended: str = "file",
                    reasoning: str = "one coherent change", cost: str = "a spec round") -> str | None:
        """One shaping turn (shaping spec §3): a one-piece ruling, or -- with
        *plan* -- a slice plan submitted. Returns the plan's hash, or None."""
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        sid = self._session(g, cause)
        self._end_turn(g, sid, ended)
        if plan is None:
            self._cmd(g, "record_one_piece", {"reasoning": reasoning, "cost_if_wrong": cost})
            return None
        h = put_artifact(self.store, plan)
        self._cmd(g, "submit_slices", {"artifact_hash": h})
        return h

    def advance(self) -> None:
        """The coordinator's `advance_ungated` at slices_accepted."""
        g = self._dispatch(Role.OPERATOR, "coordinator")
        self._cmd(g, "advance_ungated", {})

    def to_sliced(self, plan: bytes = SLICES_BYTES) -> "Front":
        from kernel.policy import policy_of
        assert self.state() == "shaping", self.state()
        self.shape_round(plan)
        self.review_round("accept")
        if "slices" in policy_of(self.store, self.run_id).gates:
            self.approve()
        else:
            self.advance()
        assert self.state() == "sliced", self.state()
        return self

    def author_round(self, artefact: bytes, *, ended: str = "file",
                     resume: str | None = None) -> str:
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        if resume is None:
            sid = self._session(g, cause)
        else:
            sid = resume
            self._prompt(g, sid, cause)
        self._end_turn(g, sid, ended)
        h = put_artifact(self.store, artefact)
        name = "submit_spec" if self.phase() == "spec" else "submit_plan"
        self._cmd(g, name, {"artifact_hash": h})
        return h

    def ask_round(self, questions, rulings=None) -> str:
        """An author turn that ends with questions rather than an artefact."""
        cause = self._newest_id()
        g = self._dispatch(Role.AUTHOR, self.author)
        sid = self._session(g, cause)
        self._end_turn(g, sid)
        for qid, text in questions:
            self._cmd(g, "record_model_question", {"question_id": qid, "question": text})
            if rulings and qid in rulings:
                self._cmd(g, "record_model_ruling",
                          {"question_id": qid, "ruling": rulings[qid],
                           "reasoning": "reasoned", "cost_if_wrong": "low"})
        return sid

    def revise(self, issue: dict, *, reshape: bool = True) -> None:
        """A relevant issue change; lands in `shaping` (ruling 11). By default
        the driver rules the new epoch one piece so the run is back at
        `queued`; a test that asserts the landing state passes reshape=False."""
        g = self._dispatch(Role.OPERATOR, "runner")
        self._cmd(g, "revise_bundle", {"issue": issue})
        if reshape:
            self.shape_round()

    def review_round(self, verdict: str = "accept", findings: bytes = b"ok", **override) -> None:
        cause = self._newest_id()
        g = self._dispatch(Role.REVIEWER, self.reviewer)
        self._cmd(g, "issue_review_brief", {"phase": self.phase()})
        sid = self._session(g, cause)
        self._end_turn(g, sid)
        phase = self.phase()
        issued = fq.brief_for(self.store, self.run_id, g).payload
        payload = {
            "phase": phase, "verdict": verdict,
            "artifact_hash": issued["artifact_hash"], "base_sha": issued["base_sha"],
            "context_bundle_hash": issued["context_bundle_hash"],
            "policy_version": issued["policy_version"],
            "findings_hash": put_artifact(self.store, findings),
        }
        payload.update(override)
        self._cmd(g, "record_review", payload)

    # -- the human's commands ---------------------------------------------------

    def human(self, name: str, payload: dict):
        from kernel.commands import HUMAN_GENERATION, execute_as_human
        return execute_as_human(self.store, Command(
            name=name, run_id=self.run_id,
            expected_version=self.store.run_version(self.run_id),
            idempotency_key=self._key(f"human-{name}"), generation=HUMAN_GENERATION,
            payload=payload,
        ))

    def approve(self) -> None:
        self.human("approve_artifact",
                   {"artifact_hash": self.store.phase_artifact(self.run_id, self.phase())})

    def grant(self) -> None:
        self.human("grant_round", {})

    def answer(self, answer: str, question_ids=(), cursor: str | None = "i-h") -> None:
        self.human("record_human_answer",
                   {"question_ids": list(question_ids), "answer": answer, "cursor_item_id": cursor})

    def direct(self, text: str, cursor: str | None = "i-h") -> None:
        self.human("record_human_direction", {"text": text, "cursor_item_id": cursor})

    def to_specified(self) -> "Front":
        self.author_round(SPEC_BYTES)
        self.review_round("accept")
        if self.state() == "spec_accepted":
            self.approve()
        assert self.state() == "specified", self.state()
        return self

    def to_planned(self) -> "Front":
        self.to_specified()
        self.author_round(PLAN_BYTES)
        self.review_round("accept")
        if self.state() == "plan_accepted":
            self.approve()
        assert self.state() == "planned", self.state()
        return self

    def to_implementing(self, *, implementer: str = "claude", output: bytes = b"impl") -> int:
        self.to_planned()
        g = self._dispatch(Role.IMPLEMENTER, implementer)
        self._cmd(g, "start_implementation", {})
        self._cmd(g, "record_implementation_output",
                  {"artifact_hash": put_artifact(self.store, output)})
        return g


def reach_specified(store, run_id: str, **kw) -> Front:
    return Front(store, run_id, **kw).to_specified()


def reach_planned(store, run_id: str, **kw) -> Front:
    return Front(store, run_id, **kw).to_planned()


def reach_implementing(store, run_id: str, **kw) -> Front:
    f = Front(store, run_id, **kw)
    f.to_implementing()
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tests.kernel.front")
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--to", required=True, choices=["specified", "planned", "implementing"])
    ap.add_argument("--existing", action="store_true",
                    help="drive a run the caller already created")
    ap.add_argument("--author", default="claude")
    ap.add_argument("--reviewer", default="codex")
    a = ap.parse_args(argv)
    store = Store.open(a.db)
    f = Front(store, a.run_id, author=a.author, reviewer=a.reviewer, existing=a.existing)
    getattr(f, f"to_{a.to}")()
    print(json.dumps({"run_id": a.run_id, "state": store.run_state(a.run_id)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
