"""Task 4: a reconciliation names what the human saw, per key, typed by obligation."""
import json
import os

import pytest

from kernel.cli import RC_REFUSED, main
from kernel.dispatch import Role, dispatch
from kernel.effects import (EffectClass, Resolution, _perform_unhalted,
                            is_halted, reconcile_many, reconcile_typed)
from kernel.events import EventKind
from kernel.store import Store

EVENTS = ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/events"]


def _body(tmp_path, title="sess-create:r-1:1", workspace=None):
    ws = workspace or os.path.realpath(str(tmp_path / "ws"))
    return {"agent_id": "ag_1", "host_id": "host_h1", "workspace": ws, "title": title}


def _create_argv(body):
    return ["curl", "-sSf", "-X", "POST", "-d", json.dumps(body), "http://srv/v1/sessions"]


def _snap(body, **over):
    return {"id": "s1", "agent_id": body["agent_id"], "agent_name": "v2_author_claude",
            "host_id": "h1", "workspace": body["workspace"], "title": body["title"], **over}


def _boom(*a):
    raise RuntimeError("curl: (7) connection refused")


def _uncertain(s, gen, key, intent, effect_class=EffectClass.SESSION_CONTROL):
    # `_perform_unhalted`, not `perform`: several tests below journal a SECOND
    # (or third) uncertain effect on a run a prior call already halted, and
    # `perform`'s run-level halt gate refuses ANY further effect once halted
    # -- by design (kernel/effects.py's `perform` docstring) -- so it would
    # never reach the executor and never journal the new key at all. The same
    # bypass is how tests/kernel/test_cli_reconcile.py sets up a second
    # uncertain effect on an already-halted run.
    with pytest.raises(Exception):
        _perform_unhalted(s, "r-1", gen, effect_class, key, intent, _boom)
    assert s.effect_by_key(key, run_id="r-1")["state"] == "uncertain"


@pytest.fixture
def run(tmp_path):
    os.makedirs(tmp_path / "ws")
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    # Role.AUTHOR does not exist until Task 6 (see task-3-brief.md's identical
    # note); Role.IMPLEMENTER is the placeholder until then.
    gen = dispatch(s, "r-1", actor="claude", role=Role.IMPLEMENTER).generation
    return s, gen


def _create_intent(body):
    return {"argv": _create_argv(body),
            "obligation": {"kind": "sess-create", "run": "r-1", "phase": "spec",
                           "epoch": 0, "cause": "f1"}}


def test_reconcile_title_distinguishes_same_tuple(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    other = json.dumps(_snap(body, title="sess-create:r-1:0"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, other)], s.run_version("r-1"))
    n = reconcile_typed(s, "r-1", [Resolution(body["title"], True, json.dumps(_snap(body)))],
                        s.run_version("r-1"))
    assert n == 1
    row = s.effect_by_key(body["title"], run_id="r-1")
    assert row["state"] == "reconciled"
    assert json.loads(row["external_object_id"])["id"] == "s1"
    assert "items" not in json.loads(row["external_object_id"])
    fact = s.newest_fact("r-1", EventKind.EFFECT_RECONCILED)
    assert fact.actor == "human"
    assert fact.payload["resolution"] == "delivered"
    assert json.loads(fact.payload["external_object_id"])["title"] == body["title"]
    assert not is_halted(s, "r-1")


def test_reconcile_title_must_equal_the_key_not_only_the_body(run, tmp_path):
    """The coordinator bug the three-way check exists for: a create's own -d
    body disagrees with the key it was journalled under. A snapshot that only
    agrees with the (wrong) body -- title == body.title -- must still be
    refused, because it does not name the key being reconciled."""
    s, gen = run
    body = _body(tmp_path, title="sess-create:r-1:999")
    key = f"sess-create:r-1:{gen}"
    assert body["title"] != key
    _uncertain(s, gen, key, _create_intent(body))
    snap = _snap(body)
    assert snap["title"] == body["title"] != key
    with pytest.raises(ValueError, match="title"):
        reconcile_typed(s, "r-1", [Resolution(key, True, json.dumps(snap))], s.run_version("r-1"))
    assert s.effect_by_key(key, run_id="r-1")["state"] == "uncertain"
    assert is_halted(s, "r-1")


def test_reconcile_host_id_prefix_both_ways(run, tmp_path):
    s, gen = run
    for i, (req, got) in enumerate([("host_h1", "h1"), ("h1", "host_h1"),
                                    ("host_h1", "host_h1"), ("h1", "h1")]):
        body = _body(tmp_path, title=f"sess-create:r-1:{i}")
        body["host_id"] = req
        _uncertain(s, gen, body["title"], _create_intent(body))
        assert reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                                     json.dumps(_snap(body, host_id=got)))],
                               s.run_version("r-1")) == 1
    body = _body(tmp_path, title="sess-create:r-1:9")
    body["host_id"] = "host_"
    _uncertain(s, gen, body["title"], _create_intent(body))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                              json.dumps(_snap(body, host_id="host_")))],
                        s.run_version("r-1"))


def test_reconcile_workspace_realpath(run, tmp_path):
    s, gen = run
    os.symlink(tmp_path / "ws", tmp_path / "link")
    body = _body(tmp_path, workspace=str(tmp_path / "link"))
    _uncertain(s, gen, body["title"], _create_intent(body))
    real = os.path.realpath(str(tmp_path / "ws"))
    assert reconcile_typed(s, "r-1", [Resolution(body["title"], True,
                                                 json.dumps(_snap(body, workspace=real)))],
                           s.run_version("r-1")) == 1


def test_reconcile_delivered_mismatched_agent_id_refused(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    for bad in [_snap(body, agent_id="ag_2"), _snap(body, agent_name=None),
                _snap(body, id=""), _snap(body, workspace="/elsewhere")]:
        with pytest.raises(ValueError):
            reconcile_typed(s, "r-1", [Resolution(body["title"], True, json.dumps(bad))],
                            s.run_version("r-1"))
    assert s.effect_by_key(body["title"], run_id="r-1")["state"] == "uncertain"
    assert is_halted(s, "r-1")


def test_reconcile_create_without_id_refused(run, tmp_path):
    s, gen = run
    body = _body(tmp_path)
    _uncertain(s, gen, body["title"], _create_intent(body))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, None)], s.run_version("r-1"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(body["title"], True, "s1")], s.run_version("r-1"))


def test_reconcile_create_malformed_journaled_body_refused(run):
    """`check()` validates the ARGV shape at journal time -- one `-d`, as its
    own token -- but never parses what's inside it, so a malformed-but-present
    `-d` journals fine and is only discovered here. `create_body` raises
    `ContractViolation`, a bare `Exception`, not a `ValueError`; uncaught, that
    would escape `reconcile_typed` and `_do_reconcile`'s `except ValueError`
    would miss it entirely."""
    s, gen = run
    key = "sess-create:r-1:bad"
    argv = ["curl", "-sSf", "-X", "POST", "-d", "{not valid json", "http://srv/v1/sessions"]
    intent = {"argv": argv, "obligation": {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                           "epoch": 0, "cause": "f1"}}
    _uncertain(s, gen, key, intent)
    snap = json.dumps({"id": "s1", "agent_id": "ag_1", "agent_name": "v2_author_claude",
                       "host_id": "h1", "workspace": "/tmp/whatever", "title": key})
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(key, True, snap)], s.run_version("r-1"))
    assert s.effect_by_key(key, run_id="r-1")["state"] == "uncertain"


def test_reconcile_stop_without_id(run):
    s, gen = run
    intent = {"argv": EVENTS, "body": {"event": "stop_session"},
              "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}}
    _uncertain(s, gen, "sess-stop:s1:1", intent)
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True, "x")], s.run_version("r-1"))
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True, None)],
                           s.run_version("r-1")) == 1
    assert s.effect_by_key("sess-stop:s1:1", run_id="r-1")["external_object_id"] == "ok"


def test_reconcile_prompt_and_publish_take_a_string(run):
    s, gen = run
    _uncertain(s, gen, "sess-prompt:s1:1:f1",
               {"argv": EVENTS, "obligation": {"kind": "sess-prompt", "session": "s1",
                                               "phase": "spec", "epoch": 0, "cause": "f1"}})
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-prompt:s1:1:f1", True, None)],
                        s.run_version("r-1"))
    assert reconcile_typed(s, "r-1", [Resolution("sess-prompt:s1:1:f1", True, "item_9")],
                           s.run_version("r-1")) == 1
    assert s.effect_by_key("sess-prompt:s1:1:f1", run_id="r-1")["external_object_id"] == "item_9"


def test_not_delivered_stores_nothing(run):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", False, None)],
                           s.run_version("r-1")) == 1
    row = s.effect_by_key("sess-stop:s1:1", run_id="r-1")
    assert row["external_object_id"] is None
    assert s.newest_fact("r-1", EventKind.EFFECT_RECONCILED).payload == {
        "resolution": "not_delivered", "external_object_id": None}
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", False, "x")], s.run_version("r-1"))


def test_forms_are_by_intent_not_class(run):
    s, gen = run
    # A runner effect: {argv} alone. Typed form refused; legacy form accepted.
    _uncertain(s, gen, "comment:1", {"argv": ["gh", "issue", "comment", "1", "--body", "x"]},
               effect_class=EffectClass.COMMENT)
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("comment:1", False)], s.run_version("r-1"))
    # A coordinator effect: obligation present. Legacy form refused.
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    with pytest.raises(ValueError):
        reconcile_many(s, "r-1", ["sess-stop:s1:1"], "seen", s.run_version("r-1"))
    assert reconcile_many(s, "r-1", ["comment:1"], "seen", s.run_version("r-1")) == 1


def test_batch_is_all_or_nothing_and_keys_once(run):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    _uncertain(s, gen, "sess-stop:s2:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s2", "cause": "f2"}})
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True),
                                   Resolution("sess-stop:s1:1", False)], s.run_version("r-1"))
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True),
                                   Resolution("sess-stop:s2:1", True, "bad")], s.run_version("r-1"))
    assert s.effect_by_key("sess-stop:s1:1", run_id="r-1")["state"] == "uncertain"
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s1:1", True)],
                           s.run_version("r-1")) == 1
    assert is_halted(s, "r-1")                          # s2 still pending
    assert reconcile_typed(s, "r-1", [Resolution("sess-stop:s2:1", False)],
                           s.run_version("r-1")) == 1
    assert not is_halted(s, "r-1")


def test_cli_typed_form(run, tmp_path, capsys):
    s, gen = run
    _uncertain(s, gen, "sess-stop:s1:1",
               {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}})
    _uncertain(s, gen, "sess-prompt:s1:1:f1",
               {"argv": EVENTS, "obligation": {"kind": "sess-prompt", "session": "s1",
                                               "phase": "spec", "epoch": 0, "cause": "f1"}})
    db = str(tmp_path / "k.db")
    rc = main(["reconcile", "--db", db, "--run-id", "r-1",
               "--expected-version", str(s.run_version("r-1")),
               "--delivered", "sess-stop:s1:1", "--not-delivered", "sess-prompt:s1:1:f1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is False and out["pending"] == []
    assert main(["reconcile", "--db", db, "--run-id", "r-1", "--expected-version", "1",
                 "--delivered", "k", "--idempotency-key", "k", "--resolution", "x"]) == 2


def test_cli_delivered_at_file_missing_is_refused(run, tmp_path, capsys):
    """`open()` on a missing/unreadable `@file` raises OSError, not ValueError
    -- uncaught, that would crash main() with a traceback instead of the
    RC_REFUSED every other reconcile refusal returns."""
    s, gen = run
    body = _body(tmp_path)
    key = body["title"]
    _uncertain(s, gen, key, _create_intent(body))
    db = str(tmp_path / "k.db")
    missing = str(tmp_path / "nonexistent-snapshot.json")
    rc = main(["reconcile", "--db", db, "--run-id", "r-1",
               "--expected-version", str(s.run_version("r-1")),
               "--delivered", f"{key}=@{missing}"])
    assert rc == RC_REFUSED
    err = capsys.readouterr().err
    assert missing in err
    assert s.effect_by_key(key, run_id="r-1")["state"] == "uncertain"


def test_cli_delivered_at_file_snapshot_is_accepted(run, tmp_path, capsys):
    """The plumbing `@file` exists for (Task 20's runner hands a snapshot this
    way rather than on the command line): a real file holding the session
    snapshot JSON is read and its content reconciles the create."""
    s, gen = run
    body = _body(tmp_path)
    key = body["title"]
    _uncertain(s, gen, key, _create_intent(body))
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps(_snap(body)))
    db = str(tmp_path / "k.db")
    rc = main(["reconcile", "--db", db, "--run-id", "r-1",
               "--expected-version", str(s.run_version("r-1")),
               "--delivered", f"{key}=@{snap_path}"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is False and out["pending"] == []
    row = s.effect_by_key(key, run_id="r-1")
    assert row["state"] == "reconciled"
    assert json.loads(row["external_object_id"])["id"] == "s1"
