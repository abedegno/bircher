import json
import os
import subprocess

import pytest

from coordinator import sessions
from coordinator.effects import KERNEL
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import Front


def _env(db, run_id, gen):
    return {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db),
            "BIRCHER_RUN_ID": run_id, "BIRCHER_GENERATION": str(gen), "PATH": os.environ["PATH"]}


@pytest.fixture
def run(tmp_path):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    return s, db, g


def test_add_worktree_refuses_an_existing_path_and_returns_realpath(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    link = tmp_path / "link"
    (tmp_path / "real").mkdir()
    link.symlink_to(tmp_path / "real")
    wt = link / "r-1" / "3"
    got = sessions.add_worktree(str(repo), sha, str(wt))
    assert got == os.path.realpath(str(wt)) and "link" not in got
    assert (wt / ".git").exists()
    with pytest.raises(sessions.WorktreeExists, match=str(wt)):
        sessions.add_worktree(str(repo), sha, str(wt))


def test_worktree_path_exists_rc_failed(tmp_path):
    """§11: a path that exists before the create is refused before anything
    is added; the loop maps WorktreeExists to RC_FAILED (Task 19)."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    existing = tmp_path / "ws" / "r-1" / "7"
    existing.mkdir(parents=True)
    with pytest.raises(sessions.WorktreeExists):
        sessions.add_worktree(str(repo), "0" * 40, str(existing))
    assert not (existing / ".git").exists()


def test_create_session_journals_the_obligation_and_records_the_snapshot(run, tmp_path, monkeypatch):
    s, db, g = run
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        body = json.loads(argv[argv.index("-d") + 1])
        class R: returncode, stdout, stderr = 0, json.dumps({"id": "s-9", "agent_id": body["agent_id"],
                                                             "agent_name": "claude", "host_id": "h",
                                                             "workspace": body["workspace"],
                                                             "title": body["title"]}), ""
        return R()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake_run)
    # As tests/kernel/test_executor_body.py does for the same reason: without
    # this, resolve_command's tool-path resolution and curlrc `-q` suppression
    # (kernel/cli.py, covered by tests/kernel/test_launcher.py) rewrite argv[0]
    # to an absolute path and prepend `-q`, which this test is not about.
    monkeypatch.setattr("kernel.cli.resolve_command", lambda a: list(a))
    snap = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv",
                                   agent_id="ag_1", host_id="host_h", workspace="/w/r-1/%d" % g,
                                   phase="spec", epoch=0, cause="f-1", env=_env(db, "r-1", g))
    assert snap["id"] == "s-9" and snap["agent_name"] == "claude"
    row = s.effect_by_key(f"sess-create:r-1:{g}", run_id="r-1")
    assert row["state"] == "confirmed"
    assert row["intent"]["obligation"] == {"kind": "sess-create", "run": "r-1", "phase": "spec",
                                           "epoch": 0, "cause": "f-1"}
    assert json.loads(row["external_object_id"])["title"] == f"sess-create:r-1:{g}"
    argv = calls[0]
    assert argv[:2] == ["curl", "-sSf"] and "POST" in argv and argv[-2] == "-d"
    assert json.loads(argv[-1]) == {"agent_id": "ag_1", "host_id": "host_h",
                                    "workspace": "/w/r-1/%d" % g, "title": f"sess-create:r-1:{g}"}
    # Replay: same obligation, same key -> the recorded snapshot, no second POST.
    again = sessions.create_session(s, run_id="r-1", generation=g, server="http://srv",
                                    agent_id="ag_1", host_id="host_h", workspace="/w/r-1/%d" % g,
                                    phase="spec", epoch=0, cause="f-1", env=_env(db, "r-1", g))
    assert again == snap and len(calls) == 1


def test_prompt_and_stop_carry_no_d_and_compose_from_the_journal(run, tmp_path, monkeypatch):
    s, db, g = run
    seen = []

    def fake_run(argv, **kw):
        path = argv[argv.index("--data-binary") + 1][1:]
        seen.append((list(argv), open(path, "rb").read()))
        class R: returncode, stdout, stderr = 0, json.dumps({"item_id": "it-1"}), ""
        return R()
    monkeypatch.setattr("kernel.cli.subprocess.run", fake_run)
    out = sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                                  phase="spec", epoch=0, cause="f-1", text=b"hello author",
                                  env=_env(db, "r-1", g))
    assert json.loads(out)["item_id"] == "it-1"
    argv, sent = seen[-1]
    assert "-d" not in argv and "--data-binary" in argv
    assert json.loads(sent) == {"type": "message", "data": {"role": "user",
                                "content": [{"type": "input_text", "text": "hello author"}]}}
    row = s.effect_by_key(f"sess-prompt:s-9:{g}:f-1", run_id="r-1")
    assert "-d" not in row["intent"]["argv"] and "--data-binary" not in row["intent"]["argv"]
    assert s.has_artifact(row["intent"]["body"]["artifact"])
    sessions.stop_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                          cause="te-1", env=_env(db, "r-1", g))
    argv, sent = seen[-1]
    assert json.loads(sent) == {"type": "stop_session"}
    row = s.effect_by_key(f"sess-stop:s-9:{g}", run_id="r-1")
    assert row["state"] == "confirmed" and row["intent"]["obligation"] == {"kind": "sess-stop", "session": "s-9",
                                                                             "cause": "te-1"}


def test_satisfied_matches_on_the_obligation_alone(run, tmp_path, monkeypatch):
    s, db, g = run
    monkeypatch.setattr("kernel.cli.subprocess.run", lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": json.dumps({"item_id": "x"}), "stderr": ""})())
    ob = {"kind": "sess-prompt", "session": "s-9", "phase": "spec", "epoch": 0, "cause": "f-1"}
    assert sessions.satisfied(s, "r-1", ob) is None
    sessions.prompt_session(s, run_id="r-1", generation=g, server="http://srv", session_id="s-9",
                            phase="spec", epoch=0, cause="f-1", text=b"v1", env=_env(db, "r-1", g))
    assert sessions.satisfied(s, "r-1", ob)["idempotency_key"] == f"sess-prompt:s-9:{g}:f-1"
    # A different body under the same obligation is the same obligation.
    assert sessions.satisfied(s, "r-1", dict(ob)) is not None
    assert sessions.satisfied(s, "r-1", dict(ob, cause="f-2")) is None
