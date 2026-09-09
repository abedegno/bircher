"""§8 proof-script tests: the journal assertions over a run the driver
produced, and a fake server for the session assertions.

`_review_round_with_real_brief` and `_accept` exist because
`tests.kernel.front.Front.review_round` -- built to drive the kernel-guard
suite, where neither a session's content nor its findings text matters --
prompts with placeholder text and records `findings=b"ok"`. Two of the §8
checks read exactly those bytes: `assert_sessions` hashes a reviewer
session's first item against the issued brief, and `assert_journal` searches
the findings blob for the brief's hash8. A placeholder cannot satisfy
either, so the clean run needs at least one round with the session and
findings a real reviewer would actually receive and write.
`_accept` only fixes the findings -- the tests that use it never check
`assert_sessions`, so their sessions can stay the driver's placeholder.

`_store_with_confirmed_stops` exists for the same reason, for a third §8
check: the turn-ordering assertion reads the `effect_confirmed` fact a real
stop carries (`kernel/effects.py` `perform`), but `Front._journal` marks a
session-control effect 'confirmed' by writing straight to the effects table
(`store.mark_effect`), never appending that fact at all. Backfilling it after
the fact would place it after every real output fact and fail a clean run on
the very check meant to catch a genuine ordering defect, so this appends it
at the moment a stop is actually marked confirmed -- the same moment
`perform()` would have.
"""
import json

from kernel import brief, front
from kernel.artifacts import put_artifact
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import PLAN_BYTES, SPEC_BYTES, Front
from tools import prove_front_half as prove


def _prompt_with_artifact(f: Front, generation: int, sid: str, cause: str, artifact_hash: str) -> None:
    """Like `Front._prompt`, but the body names *artifact_hash* rather than
    the driver's own placeholder text -- the one line
    `_review_round_with_real_brief` needs to differ from `_prompt`."""
    phase, epoch = f.phase(), f.epoch()
    pkey = f"sess-prompt:{sid}:{generation}:{cause}"
    f._journal(generation, pkey, {
        "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid}/events",
                 "-H", "content-type: application/json"],
        "obligation": {"kind": "sess-prompt", "session": sid, "phase": phase, "epoch": epoch, "cause": cause},
        "body": {"artifact": artifact_hash},
    }, f"item-{sid}-{generation}")


def _review_round_with_real_brief(f: Front, verdict: str = "accept") -> None:
    """`Front.review_round`, except the session is actually prompted with
    the rendered brief and the findings actually name its hash8 -- what §8
    checks and the driver's placeholder round never sends."""
    cause = f._newest_id()
    g = f._dispatch(Role.REVIEWER, f.reviewer)
    f._cmd(g, "issue_review_brief", {"phase": f.phase()})
    issued = front.brief_for(f.store, f.run_id, g).payload
    sid = f._create(g, cause)
    _prompt_with_artifact(f, g, sid, cause, issued["brief_hash"])
    f._end_turn(g, sid)
    findings = f"findings\n\nVERDICT: PASS {brief.hash8(issued['artifact_hash'])}\n".encode()
    payload = {
        "phase": f.phase(), "verdict": verdict,
        "artifact_hash": issued["artifact_hash"], "base_sha": issued["base_sha"],
        "context_bundle_hash": issued["context_bundle_hash"],
        "policy_version": issued["policy_version"],
        "findings_hash": put_artifact(f.store, findings),
    }
    f._cmd(g, "record_review", payload)


def _accept(f: Front, verdict: str = "accept") -> None:
    """`review_round`, with findings that actually name the brief's hash8 --
    the placeholder `b"ok"` trips the verdict-line check for a reason
    unrelated to whatever a test built this run to exercise."""
    h = f.store.phase_artifact(f.run_id, f.phase())
    f.review_round(verdict, findings=f"findings\n\nVERDICT: PASS {brief.hash8(h)}\n".encode())


def _fake_fetch_for(s, run_id: str = "r-1"):
    """Lists every session the journal created, with its snapshot's agent_id
    and the prompted items as user-role items, in the `PaginatedList` shape
    the real server sends (`{"object": "list", "data": [...]}`) -- so this
    exercises `list_items`/`list_sessions`' primary branch, not their
    fallback. `listed` comes back separately from `sessions` so a test can
    drop a session from the listing without also breaking its individual
    lookup."""
    sessions = {}
    listed = set()
    for row in front.satisfied_effects(s, run_id, "sess-create"):
        snap = json.loads(row["external_object_id"])
        sessions[snap["id"]] = dict(snap, items=[])
        listed.add(snap["id"])
    for row in front.satisfied_effects(s, run_id, "sess-prompt"):
        ob = row["intent"]["obligation"]
        text = s.read_blob(row["intent"]["body"]["artifact"]).decode()
        sessions[ob["session"]]["items"].append({"id": row["external_object_id"], "role": "user",
                                                 "content": [{"type": "input_text", "text": text}]})

    def fetch(url):
        # The query is stripped: the readers page, so the URL carries an
        # `order`/`limit` and an `after` on every page after the first.
        tail = url.partition("/v1/")[2].partition("?")[0]  # "sessions", "sessions/<sid>", ".../items"
        parts = tail.split("/")
        if parts == ["sessions"]:
            return json.dumps({"object": "list", "data": [{"id": k} for k in sessions if k in listed]})
        sid = parts[1]
        if parts[-1] == "items":
            return json.dumps({"object": "list", "data": sessions[sid]["items"]})
        return json.dumps({k: v for k, v in sessions[sid].items() if k != "items"})
    return fetch, sessions, listed


def _store_with_confirmed_stops(path) -> Store:
    """A Store whose stop confirmations also append the `effect_confirmed`
    fact a real stop carries -- see the module docstring. Wraps the instance's
    `mark_effect`, not the class's: every `Front` method reaches the store
    through `self.store`, which is this same wrapped object."""
    store = Store.open(path)
    real_mark_effect = store.mark_effect

    def mark_effect(idempotency_key, state, external_object_id, *, run_id):
        real_mark_effect(idempotency_key, state, external_object_id, run_id=run_id)
        if state == "confirmed" and idempotency_key.startswith("sess-stop:"):
            store.append_fact(
                run_id=run_id, kind=EventKind.EFFECT_CONFIRMED, actor="kernel",
                causal_command_id=idempotency_key,
                payload={"effect_id": f"eff-{idempotency_key}",
                         "external_object_id": external_object_id,
                         "effect_class": "session_control"},
            )

    store.mark_effect = mark_effect
    return store


def test_a_clean_autonomous_run_passes(tmp_path):
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session())
    _review_round_with_real_brief(f)
    f.author_round(PLAN_BYTES)
    _review_round_with_real_brief(f)
    assert prove.assert_journal(s, "r-1", mode="zero") == []
    fetch, sessions, listed = _fake_fetch_for(s)
    assert prove.assert_sessions(s, "r-1", fetch=fetch) == []


def test_each_assertion_fails_on_its_defect(tmp_path):
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1", labels=())
    spec_hash = f.author_round(SPEC_BYTES); _accept(f); f.approve()
    f.author_round(PLAN_BYTES); _accept(f)
    fails = prove.assert_journal(s, "r-1", mode="zero")
    assert any("human_ruling" in x for x in fails) and any("model_ruling" in x for x in fails)
    assert prove.assert_journal(s, "r-1", mode="approval") == [x for x in
                                                              prove.assert_journal(s, "r-1", mode="approval")
                                                              if "model_ruling" in x]

    # A run that was reconciled or halted fails in every mode.
    s.set_reconciliation("r-1", {"run_id": "r-1", "reason": "test halt"})
    assert any("reconciled or halted" in x for x in prove.assert_journal(s, "r-1", mode="zero"))
    s.clear_reconciliation("r-1")

    # A human fact outside {approve, gate} fails approval mode specifically --
    # a separate run, since "r-1" is already past the states
    # record_human_answer allows by this point.
    s2 = _store_with_confirmed_stops(tmp_path / "k2.db")
    f2 = Front(s2, "r-2", labels=())
    f2.author_round(SPEC_BYTES); _accept(f2); f2.approve()
    f2.answer("noted")
    assert any("beyond the approval" in x for x in prove.assert_journal(s2, "r-2", mode="approval"))

    # A DIRECTION typed into a live author session at `queued`. It writes no
    # park and no ruling -- the one human fact that leaves no other trace --
    # so the journal half has to count it as a touch, or the zero-touch proof
    # is blind to exactly the touch that is hardest to see any other way.
    s3 = _store_with_confirmed_stops(tmp_path / "k3.db")
    Front(s3, "r-3").direct("use postgres")
    assert any("human_direction" in x for x in prove.assert_journal(s3, "r-3", mode="zero"))
    assert any("human_direction" in x for x in prove.assert_journal(s3, "r-3", mode="approval"))

    # Two sess-creates sharing an obligation (same cause, same phase and
    # epoch): the first names a vendor its dispatch never claimed, and
    # neither is ever prompted or stopped. Checked under mode="human" to
    # isolate them from the human-fact noise above.
    cause = f._newest_id()
    g1 = f._dispatch(Role.AUTHOR, f.author)
    f._create(g1, cause, actor="mallory")
    g2 = f._dispatch(Role.AUTHOR, f.author)
    f._create(g2, cause)
    human_fails = prove.assert_journal(s, "r-1", mode="human")
    assert any("dispatch actor" in x for x in human_fails)
    assert any("share an obligation" in x for x in human_fails)
    assert any("neither prompted nor stopped" in x for x in human_fails)

    # A satisfied prompt with no turn_ended at all.
    g3 = f._dispatch(Role.AUTHOR, f.author)
    f._session(g3, f._newest_id())
    assert any("has no turn_ended" in x for x in prove.assert_journal(s, "r-1", mode="human"))

    fetch, sessions, listed = _fake_fetch_for(s)
    sid = next(iter(sessions))
    sessions[sid]["agent_id"] = "ag_switched"
    assert any("agent_id" in x for x in prove.assert_sessions(s, "r-1", fetch=fetch))
    sessions[sid]["agent_id"] = json.loads(front.satisfied_effects(s, "r-1", "sess-create")[0]["external_object_id"])["agent_id"]
    sessions[sid]["items"].append({"id": "typed", "role": "user", "content": [{"type": "input_text", "text": "hi"}]})
    assert any("prompt_item" in x for x in prove.assert_sessions(s, "r-1", fetch=fetch))

    # A session the journal created but the /v1/sessions listing omits.
    listed.discard(sid)
    assert any("is not in the" in x and sid in x for x in prove.assert_sessions(s, "r-1", fetch=fetch))

    # Two more defects reached only by a fact no command would ever accept as
    # written -- the kernel refuses both at the door, so the journal can only
    # hold them if something upstream of the kernel is wrong. Forged directly
    # (`store.append_fact`), not through `Front`, for exactly that reason.
    n = front.epoch(s, "r-1")
    s.append_fact(run_id="r-1", kind=EventKind.ARTIFACT_SUBMITTED, actor="claude", causal_command_id=None,
                  payload={"phase": "plan", "epoch": n, "hash": spec_hash, "author": "claude", "round": 99})
    assert any("artifact_submitted" in x for x in prove.assert_journal(s, "r-1", mode="human"))

    s.append_fact(run_id="r-1", kind=EventKind.REVIEW_VERDICT, actor="mallory", causal_command_id=None,
                  payload={"verdict": "accept", "phase": "plan", "epoch": n, "artifact_hash": spec_hash,
                           "findings_hash": None, "ruling": "review_ruling", "binding_hash": None,
                           "reviewer_identity": "mallory", "generation": g1})
    assert any("has no brief in its epoch" in x for x in prove.assert_journal(s, "r-1", mode="human"))

    # An output fact recorded before its own turn's stop was confirmed -- a
    # fresh run, since a real command would refuse to record output before
    # the round's stop is satisfied (the OUTPUT_COMMANDS guard,
    # kernel/authz.py); only a fact forged straight into the store, ahead of
    # the turn actually ending, can misorder them. `causal_command_id` binds
    # the forged output fact to the forged command_accepted fact that names
    # its generation, exactly as a real command's would.
    s3 = _store_with_confirmed_stops(tmp_path / "k3.db")
    f3 = Front(s3, "r-3")
    g7 = f3._dispatch(Role.AUTHOR, f3.author)
    sid7 = f3._session(g7, f3._newest_id())
    forged_key = f"forged-output-{g7}"
    s3.append_fact(run_id="r-3", kind=EventKind.COMMAND_ACCEPTED, actor=f3.author, causal_command_id=forged_key,
                   payload={"command_name": "record_author_empty", "generation": g7, "payload": {}})
    s3.append_fact(run_id="r-3", kind=EventKind.AUTHOR_EMPTY, actor=f3.author, causal_command_id=forged_key,
                   payload={"session": sid7, "phase": f3.phase(), "epoch": f3.epoch(), "generation": g7})
    f3._end_turn(g7, sid7)  # the stop's effect_confirmed fact lands AFTER the forged output above
    assert any("is not newer than its turn's stop confirmation" in x
              for x in prove.assert_journal(s3, "r-3", mode="human"))

    # A reviewer orphan (no satisfied prompt) stopped twice over.
    g8 = f._dispatch(Role.REVIEWER, f.reviewer)
    sid8 = f._create(g8, f._newest_id())
    for i in range(2):
        f._journal(g8, f"sess-stop:{sid8}:{g8}:{i}", {
            "argv": ["curl", "-sSf", "-X", "POST", f"http://srv/v1/sessions/{sid8}/events",
                     "-H", "content-type: application/json"],
            "obligation": {"kind": "sess-stop", "session": sid8, "cause": f"displaced-{i}"},
            "body": {"event": "stop_session"},
        }, "")
    fetch4, sessions4, listed4 = _fake_fetch_for(s)
    assert any(sid8 in x and "satisfied stops" in x for x in prove.assert_sessions(s, "r-1", fetch=fetch4))


def test_the_proof_re_renders_a_template_2_brief_over_its_prior_findings(tmp_path, monkeypatch):
    """The disposition experiment issues template 2 briefs that carry the
    previous round's findings. The REVIEW_BRIEF_ISSUED fact names that prior
    by hash, and the proof must re-render with it -- otherwise every brief of
    an experiment run reads as "not render() over its named objects"."""
    monkeypatch.setenv("BIRCHER_REVIEW_DISPOSITIONS", "on")
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session())
    _review_round_with_real_brief(f, verdict="request_revision")   # round 1: findings exist now
    f.author_round(SPEC_BYTES + b"\n## Dispositions\n\n1. accepted\n")
    _review_round_with_real_brief(f)                               # round 2's brief carries round 1's findings
    issued = [x for x in s.facts_for("r-1") if x.kind == EventKind.REVIEW_BRIEF_ISSUED]
    assert issued[-1].payload["brief_template"] == 2
    assert issued[-1].payload["prior_findings_hash"] is not None
    f.author_round(PLAN_BYTES)
    _review_round_with_real_brief(f)
    assert prove.assert_journal(s, "r-1", mode="zero") == []


def test_the_servers_cancellation_notice_is_not_an_unrecorded_prompt(tmp_path):
    """A cancelled turn leaves a user-role item the server wrote, response_id
    `cancel_...`. The coordinator ignores it; the proof must too, or every
    session stopped mid-work fails the zero-touch assertion. A real message
    that is no prompt still fails."""
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session())
    _review_round_with_real_brief(f)
    f.author_round(PLAN_BYTES)
    _review_round_with_real_brief(f)
    fetch, sessions, listed = _fake_fetch_for(s)
    assert prove.assert_sessions(s, "r-1", fetch=fetch) == []
    sid = next(iter(sessions))
    sessions[sid]["items"].append({"id": "it-cancel", "role": "user", "response_id": "cancel_9f1",
                                   "content": [{"type": "input_text", "text": "[System: interrupted]\nabandoned"}]})
    assert prove.assert_sessions(s, "r-1", fetch=fetch) == [], "the server's own item is not a touch"
    sessions[sid]["items"].append({"id": "it-human", "role": "user", "response_id": "turn_abc",
                                   "content": [{"type": "input_text", "text": "please use postgres"}]})
    fails = prove.assert_sessions(s, "r-1", fetch=fetch)
    assert any("it-human" in x and "no prompt_item" in x for x in fails), fails


def _parked_world(tmp_path):
    """A run that parked once, was told so in its author session, and was
    granted a round by a person -- the shape of the first unattended run that
    reached a merged PR (2026-09-09), whose one human fact was that retry."""
    s = _store_with_confirmed_stops(tmp_path / "k.db")
    f = Front(s, "r-1")
    f.ask_round([("q1", "?")], rulings={"q1": "yes"})
    f.author_round(SPEC_BYTES, resume=f._newest_author_session())
    # A rejection keeps the run in the spec phase, where the author seat the
    # driver's phase-scoped helper finds still exists; an accept would move
    # the phase to `plan`, where there is no author seat yet and the helper
    # answers None -- which parked a session called None the first time.
    _review_round_with_real_brief(f, verdict="request_revision")
    sid = f._newest_author_session()
    assert sid, "the fixture needs a real carrier session"
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": sid, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    park = front.current_park(s, "r-1")
    _prompt_with_artifact(f, g, sid, park.id, put_artifact(s, b"The run is stalled. Reply retry."))
    return s, f, sid, park


def test_a_park_prompt_is_not_an_awaited_turn(tmp_path):
    s, f, sid, park = _parked_world(tmp_path)
    f.grant()
    fails = prove.assert_journal(s, "r-1", mode="human")
    assert not [x for x in fails if "has no turn_ended" in x], fails


def test_a_message_the_coordinator_read_past_is_not_an_unrecorded_prompt(tmp_path):
    s, f, sid, park = _parked_world(tmp_path)
    fetch, sessions, listed = _fake_fetch_for(s)
    sessions[sid]["items"].append({"id": "it-retry", "role": "user", "response_id": "turn_r",
                                   "content": [{"type": "input_text", "text": "retry"}]})
    sessions[sid]["items"].append({"id": "it-echo", "role": "assistant", "response_id": "resp_e",
                                   "content": [{"type": "output_text", "text": "retry"}]})
    f.human("grant_round", {"cursor_item_id": "it-echo"})     # read to the echo, past the retry
    assert prove.assert_sessions(s, "r-1", fetch=fetch) == []
    # A message AFTER the newest cursor was never read: still an unrecorded prompt.
    sessions[sid]["items"].append({"id": "it-later", "role": "user", "response_id": "turn_l",
                                   "content": [{"type": "input_text", "text": "and another thing"}]})
    fails = prove.assert_sessions(s, "r-1", fetch=fetch)
    assert any("it-later" in x for x in fails) and not any("it-retry" in x for x in fails), fails
