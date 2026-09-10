"""Task 3: bodies are composed by the kernel from the journal, never from argv."""
import json
import os
import subprocess

import pytest

import kernel.cli
from kernel.artifacts import put_artifact
from kernel.cli import make_executor, session_projection, validate_create_response
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, NotReplayable, _check_body, perform
from kernel.store import Store

EVENTS = ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
          "http://srv/v1/sessions/s1/events"]
CREATE_BODY = {"agent_id": "ag_1", "host_id": "host_h1", "workspace": "/w",
               "title": "sess-create:r-1:1"}
CREATE = ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
          "-d", json.dumps(CREATE_BODY), "http://srv/v1/sessions"]
SNAP = {"id": "s1", "agent_id": "ag_1", "agent_name": "v2_author_claude",
        "host_id": "h1", "workspace": "/w", "title": "sess-create:r-1:1",
        "items": [{"role": "user"}]}


def _store(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40)
    return s


def _fake_run(seen, stdout="ok"):
    def run(argv, capture_output, text):
        assert capture_output and text
        seen["argv"] = list(argv)
        if argv[-2:-1] == ["--data-binary"]:
            path = argv[-1][1:]
            seen["path"] = path
            with open(path, "rb") as fh:
                seen["bytes"] = fh.read()
            seen["mode"] = oct(os.stat(path).st_mode & 0o777)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return run


def test_executor_builds_stop_body(tmp_path, monkeypatch):
    s = _store(tmp_path)
    seen = {}
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run(seen, '{"queued": false}'))
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    ex = make_executor(s)
    out = ex(EffectClass.SESSION_CONTROL,
             {"argv": EVENTS, "body": {"event": "stop_session"}}, "sess-stop:s1:1")
    assert json.loads(seen["bytes"]) == {"type": "stop_session"}
    assert seen["mode"] == "0o600"
    assert os.path.dirname(seen["path"]) == os.path.dirname(s.path)
    assert not os.path.exists(seen["path"])            # unlinked after the call
    assert seen["argv"][-2] == "--data-binary"
    assert all("stop_session" not in a for a in seen["argv"][:-1])
    assert out == '{"queued": false}'


def test_executor_builds_prompt_body_from_artifact(tmp_path, monkeypatch):
    s = _store(tmp_path)
    text = "brief " * 40_000                           # ~240 KB
    h = put_artifact(s, text.encode())
    seen = {}
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run(seen))
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    make_executor(s)(EffectClass.SESSION_CONTROL,
                     {"argv": EVENTS, "body": {"artifact": h}}, "sess-prompt:s1:1:f1")
    ev = json.loads(seen["bytes"])
    assert ev["type"] == "message"
    assert ev["data"]["role"] == "user"
    assert ev["data"]["content"] == [{"type": "input_text", "text": text}]
    assert max(len(a) for a in seen["argv"]) < 128 * 1024


def test_executor_body_file_removed_on_failure(tmp_path, monkeypatch):
    s = _store(tmp_path)
    seen = {}

    def failing(argv, capture_output, text):
        seen["path"] = argv[-1][1:]
        return subprocess.CompletedProcess(argv, 22, stdout="", stderr="404")
    monkeypatch.setattr(kernel.cli.subprocess, "run", failing)
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    with pytest.raises(RuntimeError):
        make_executor(s)(EffectClass.SESSION_CONTROL,
                         {"argv": EVENTS, "body": {"event": "stop_session"}}, "k")
    assert not os.path.exists(seen["path"])


def test_executor_without_store_refuses_body():
    with pytest.raises(RuntimeError):
        kernel.cli._executor(EffectClass.SESSION_CONTROL,
                             {"argv": EVENTS, "body": {"event": "stop_session"}}, "k")


def test_create_response_validated(tmp_path, monkeypatch):
    s = _store(tmp_path)
    monkeypatch.setattr(kernel.cli, "resolve_command", lambda a: list(a))
    monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run({}, json.dumps(SNAP)))
    out = make_executor(s)(EffectClass.SESSION_CONTROL, {"argv": CREATE}, "sess-create:r-1:1")
    assert json.loads(out) == session_projection(SNAP)
    assert "items" not in json.loads(out)
    for bad in ('{"error": "not found"}', json.dumps({**SNAP, "agent_id": "ag_2"}),
                json.dumps({**SNAP, "title": "other"}), "[]", "not json", ""):
        monkeypatch.setattr(kernel.cli.subprocess, "run", _fake_run({}, bad))
        with pytest.raises(RuntimeError):
            make_executor(s)(EffectClass.SESSION_CONTROL, {"argv": CREATE}, "sess-create:r-1:2")


def test_validate_create_response_requires_agent_name():
    with pytest.raises(RuntimeError):
        validate_create_response(json.dumps({**SNAP, "agent_name": None}), CREATE_BODY)


def test_body_artifact_missing_refused_before_journal(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"artifact": "f" * 64}})
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"event": "switch_agent"}})
    with pytest.raises(ValueError):
        _check_body(s, {"argv": EVENTS, "body": {"artifact": "f" * 64, "event": "stop_session"}})
    _check_body(s, {"argv": EVENTS})                   # no body: nothing to check
    gen = dispatch(s, "r-1", actor="claude", role=Role.AUTHOR).generation
    with pytest.raises(ValueError):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-prompt:s1:1:f1",
                {"argv": EVENTS, "body": {"artifact": "f" * 64}}, lambda *a: "never")
    assert s.effects_for("r-1") == []


def test_obligation_mismatch_is_not_replayable(tmp_path):
    s = _store(tmp_path)
    gen = dispatch(s, "r-1", actor="claude", role=Role.AUTHOR).generation
    intent = {"argv": EVENTS, "obligation": {"kind": "sess-stop", "session": "s1", "cause": "f1"}}
    assert perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", intent,
                   lambda *a: "ok") == "ok"
    same = perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", intent,
                   lambda *a: "never")
    assert same == "ok"
    other = {**intent, "obligation": {**intent["obligation"], "cause": "f2"}}
    with pytest.raises(NotReplayable):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1", other,
                lambda *a: "never")
    with pytest.raises(NotReplayable):
        perform(s, "r-1", gen, EffectClass.SESSION_CONTROL, "sess-stop:s1:1",
                {"argv": EVENTS}, lambda *a: "never")


def test_issue_create_writes_the_body_file_after_the_check_and_reads_the_id_back(tmp_path, monkeypatch):
    """Planning ruling 1 and 6: the body is the artefact the intent names,
    passed as --body-file after the contract check; the confirmation reads
    the created issue's database id back and returns {url, number, id}."""
    import json
    from kernel import cli
    from kernel.artifacts import put_artifact
    from kernel.store import Store
    s = Store.open(tmp_path / "k.db")
    h = put_artifact(s, b"Slice 1 of #12 (bircher shaping abcd1234)\n\nbody\n")
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        class R: returncode, stderr = 0, ""
        r = R()
        if "create" in argv:
            path = argv[argv.index("--body-file") + 1]
            assert open(path, "rb").read() == s.read_blob(h)
            r.stdout = "https://github.com/o/r/issues/40\n"
        else:
            r.stdout = "1040\n"
        return r
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "resolve_command", lambda argv: argv)
    ex = cli.make_executor(s)
    out = ex("issue_create", {"argv": ["gh", "issue", "create", "--repo", "o/r", "--title", "T", "--label", "bircher:slice"],
                              "body": {"artifact": h}}, "k")
    assert json.loads(out) == {"id": 1040, "number": 40, "url": "https://github.com/o/r/issues/40"}
    assert seen[0][:3] == ["gh", "issue", "create"] and "--body-file" in seen[0]
    assert seen[1][:3] == ["gh", "api", "repos/o/r/issues/40"] and seen[1][3:] == ["--jq", ".id"]
    assert not __import__("os").path.exists(seen[0][seen[0].index("--body-file") + 1])
