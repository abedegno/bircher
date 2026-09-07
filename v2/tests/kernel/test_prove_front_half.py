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
        tail = url.partition("/v1/")[2]  # "sessions", "sessions/<sid>", "sessions/<sid>/items"
        parts = tail.split("/")
        if parts == ["sessions"]:
            return json.dumps({"object": "list", "data": [{"id": k} for k in sessions if k in listed]})
        sid = parts[1]
        if parts[-1] == "items":
            return json.dumps({"object": "list", "data": sessions[sid]["items"]})
        return json.dumps({k: v for k, v in sessions[sid].items() if k != "items"})
    return fetch, sessions, listed


def test_a_clean_autonomous_run_passes(tmp_path):
    s = Store.open(tmp_path / "k.db")
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
    s = Store.open(tmp_path / "k.db")
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
    s2 = Store.open(tmp_path / "k2.db")
    f2 = Front(s2, "r-2", labels=())
    f2.author_round(SPEC_BYTES); _accept(f2); f2.approve()
    f2.answer("noted")
    assert any("beyond the approval" in x for x in prove.assert_journal(s2, "r-2", mode="approval"))

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
