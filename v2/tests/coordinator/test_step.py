"""The one next action for a run with a pull request."""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit, HUMAN_GENERATION, execute_as_human
from kernel.dispatch import Role, dispatch
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.store import Store
from tests.kernel.front import Front
from kernel import back, front

from coordinator.cli import main
from coordinator.step import Ground, Step, next_step

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64


def _store():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    Front(s, "r", base_sha=BASE)
    return s


def _sub(s, name, key, actor=None, **payload):
    if name == "request_merge":
        payload.setdefault("pr", 42)
        payload.setdefault("repo", "abedegno/muesli")
    if name == "record_review":
        payload.setdefault("phase", "implementation")
    role = Role.REVIEWER if name == "record_review" else Role.IMPLEMENTER
    if actor is None:
        actor = "codex" if role == Role.REVIEWER else "claude"
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=payload,
    ))


def _to_implementing(s):
    Front(s, "r", base_sha=BASE, existing=True).to_planned()
    spec = put_artifact(s, b"# spec")
    _sub(s, "start_implementation", "k3", actor="claude")
    _sub(s, "record_implementation_output", "k3o", actor="claude", artifact_hash=spec)
    return s, spec


def _repair(s, key, **over):
    payload = {"cause": "ci_red", "head_sha": HEAD, "evidence": ["server (go)"]}
    payload.update(over)
    return _sub(s, "request_repair", key, actor="claude", **payload)


def _ci(s, key, status, head=HEAD, pr_state="open", jobs=None, pr="42"):
    return _sub(s, "record_ci_observation", key, actor="claude", status=status,
                head_git_sha=head, pr_state=pr_state, failing_jobs=list(jobs or []), pr=pr)


def _park(s, key, *, cause, evidence):
    return _sub(s, "park", key, actor="claude", reason="no_progress", cause=cause,
                evidence=list(evidence), session_id=None, cursor_item_id=None)


def _grant(s, key):
    return execute_as_human(s, Command(
        name="grant_round", run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key, generation=HUMAN_GENERATION, payload={"cursor_item_id": None}))


GREEN = dict(pr_state="open", head=HEAD, ci="green", failing_jobs=(), verdict=None, fingerprints=())


def g(**over):
    return Ground(**dict(GREEN, **over))


def test_a_pending_ci_waits():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(ci="pending")).kind == "wait"


def test_no_head_waits():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(head="")).kind == "wait"


def test_an_unknown_pr_state_waits():
    # Silence must not move a run: "" means the derivation could not ask.
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(pr_state="")).kind == "wait"


def test_red_ci_is_a_repair_not_an_ending():
    # #764's shape: the implementer's session died with CI red on its head.
    s, _ = _to_implementing(_store())
    st = next_step(s, "r", g(ci="red", failing_jobs=("server (go)", "client (node)")))
    assert st == Step("repair", "ci_red", ("client (node)", "server (go)"))


def test_green_without_a_verdict_reviews():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g()).kind == "review"


def test_a_pass_merges():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict="accept", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD)
    assert next_step(s, "r", g(verdict="PASS")).kind == "merge"


def test_a_fail_is_a_review_fail_repair_with_the_fingerprints():
    s, spec = _to_implementing(_store())
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD, fingerprints=["aa" * 20])
    st = next_step(s, "r", g(verdict="FAIL", fingerprints=("aa" * 20,)))
    assert st == Step("repair", "review_fail", ("aa" * 20,))


def test_a_repair_already_requested_on_this_head_is_redispatched_from_planned():
    s, _ = _to_implementing(_store())
    _repair(s, "r1", evidence=["server (go)"])
    assert s.run_state("r") == "planned"
    st = next_step(s, "r", g(ci="red", failing_jobs=("server (go)",)))
    assert st.kind == "repair" and st.redispatch is True and st.cause == "ci_red"


def test_the_769_sequence_repairs_five_times_and_merges():
    # Six verdicts on six heads: FAIL x5 with different findings, then PASS.
    s, spec = _to_implementing(_store())
    heads = [f"{i + 1:02d}" * 20 for i in range(6)]
    # Until Task 9 freezes _BACK_HALF_DESTINATIONS a request_revision lands at planned, so each round needs a start_implementation before its request_repair; next_step reads facts by kind, so the extra transition changes no answer.
    for i, h in enumerate(heads[:5]):
        _sub(s, "record_review", f"v{i}", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[f"{i:02x}" * 20])
        st = next_step(s, "r", g(head=h, verdict="FAIL", fingerprints=(f"{i:02x}" * 20,)))
        assert st.kind == "repair", (i, st)
        _sub(s, "start_implementation", f"si{i}a", actor="claude")
        _sub(s, f"request_repair", f"rr{i}", actor="claude", cause="review_fail", head_sha=h, evidence=list(st.evidence))
        _sub(s, "start_implementation", f"si{i}b", actor="claude")
    _sub(s, "record_review", "v5", verdict="accept", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=heads[5])
    assert next_step(s, "r", g(head=heads[5], verdict="PASS")).kind == "merge"


def test_three_identical_fails_park_for_no_progress():
    s, spec = _to_implementing(_store())
    fp = "ab" * 20
    # Until Task 9 freezes _BACK_HALF_DESTINATIONS a request_revision lands at planned, so each round needs a start_implementation before its request_repair; next_step reads facts by kind, so the extra transition changes no answer.
    for i in range(2):
        h = f"{i + 1:02d}" * 20
        _sub(s, "record_review", f"v{i}", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[fp])
        _sub(s, "start_implementation", f"si{i}a", actor="claude")
        _sub(s, "request_repair", f"rr{i}", actor="claude", cause="review_fail", head_sha=h, evidence=[fp])
        _sub(s, "start_implementation", f"si{i}b", actor="claude")
    h = "9" * 40
    _sub(s, "record_review", "v2", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=h, fingerprints=[fp])
    st = next_step(s, "r", g(head=h, verdict="FAIL", fingerprints=(fp,)))
    assert st == Step("park", "review_fail", (fp,), reason="no_progress")


def test_a_merged_or_cancelled_run_is_done():
    s, _ = _to_implementing(_store())
    _sub(s, "cancel_run", "x1", actor="claude")
    assert next_step(s, "r", g()).kind == "done"


def test_a_closed_pr_is_done_too():
    s, _ = _to_implementing(_store())
    assert next_step(s, "r", g(pr_state="closed")).kind == "done"


def _file_store(tmp_path):
    db = str(tmp_path / "k.db")
    s = Store.open(db, clock=Clock(start_us=1))
    Front(s, "r", base_sha=BASE)
    return s, db


def test_the_step_cli_prints_the_step_line(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD,
                 "--ci", "red", "--failing-jobs", "server (go),client (node)"]) == 0
    assert capsys.readouterr().out == "repair|ci_red|client (node),server (go)||no"


def test_the_step_cli_reads_the_verdict_and_fingerprints_from_the_journal(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    _sub(s, "record_review", "v1", verdict="request_revision", artifact_hash=spec, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1, head_sha=HEAD, fingerprints=["ab" * 20])
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD, "--ci", "green"]) == 0
    assert capsys.readouterr().out == f"repair|review_fail|{'ab' * 20}||no"


def test_the_step_cli_says_redispatch_for_a_repair_already_requested(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    _repair(s, "r1", evidence=["server (go)"])
    assert main(["step", "--db", db, "--run-id", "r", "--pr-state", "open", "--head", HEAD,
                 "--ci", "red", "--failing-jobs", "server (go)"]) == 0
    assert capsys.readouterr().out == "repair|ci_red|server (go)||yes"


def test_the_step_cli_refuses_a_run_the_kernel_does_not_hold(tmp_path):
    _, db = _file_store(tmp_path)
    with pytest.raises(Exception):
        main(["step", "--db", db, "--run-id", "nope", "--pr-state", "open", "--head", HEAD, "--ci", "green"])


def test_back_state_prints_the_newest_observation_and_the_artifact(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    _ci(s, "c1", "failure", jobs=["x"])
    assert main(["back-state", "--db", db, "--run-id", "r"]) == 0
    assert capsys.readouterr().out == f"42|open|{HEAD}|{spec}"


def test_back_state_before_any_observation_is_blank_but_for_the_artifact(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _, spec = _to_implementing(s)
    assert main(["back-state", "--db", db, "--run-id", "r"]) == 0
    assert capsys.readouterr().out == f"|||{spec}"


def test_session_last_item_prints_the_last_id(monkeypatch, capsys):
    monkeypatch.setattr("coordinator.session.list_items",
                        lambda server, sid, **kw: [{"id": "a", "role": "user", "text": "x"},
                                                   {"id": "b", "role": "assistant", "text": "y"}])
    assert main(["session-last-item", "--server", "http://x", "--id", "s1"]) == 0
    assert capsys.readouterr().out == "b"


def test_the_no_progress_notice_names_the_cause_and_the_two_words(tmp_path, capsys):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    for k in ("r1", "r2"):
        _repair(s, k, evidence=["server (go)"])
        _sub(s, "start_implementation", "k" + k, actor="claude")
    _park(s, "p1", cause="ci_red", evidence=["server (go)"])
    assert main(["park-notice", "--db", db, "--run-id", "r"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("bircher: parked no_progress")
    assert "ci_red" in out and "server (go)" in out and "`retry`" in out and "`stop`" in out


def test_park_notice_without_a_park_is_rc_1(tmp_path):
    s, db = _file_store(tmp_path)
    _to_implementing(s)
    assert main(["park-notice", "--db", db, "--run-id", "r"]) == 1
