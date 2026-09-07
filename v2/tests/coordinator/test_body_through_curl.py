"""The brief goes through the real path: perform_effect -> kernel executor ->
curl -> a loopback HTTP stub. A fake executor would pass an argv body."""
import http.server
import json
import os
import platform
import shutil
import subprocess
import threading

import pytest

from coordinator import sessions
from coordinator.effects import KERNEL
from kernel.dispatch import Role
from kernel.effects import UncertainEffect, is_halted, pending_reconciliation
from kernel.store import Store
from tests.kernel.front import Front

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


class Recorder(http.server.BaseHTTPRequestHandler):
    received = []
    stop_status = 200
    stop_body = b'{"queued": false}'

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        Recorder.received.append((self.path, body))
        if self.path.endswith("/events") and json.loads(body).get("type") == "stop_session":
            self.send_response(Recorder.stop_status)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(Recorder.stop_body)
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"item_id": "it-1"}')

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    Recorder.received = []
    Recorder.stop_status, Recorder.stop_body = 200, b'{"queued": false}'
    srv = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _env(db, gen):
    return {"BIRCHER_EFFECT_MODE": KERNEL, "BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r-1",
            "BIRCHER_GENERATION": str(gen), "PATH": os.environ["PATH"]}


@pytest.fixture
def run(tmp_path):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1")
    g = f._dispatch(Role.REVIEWER, "codex")
    return s, db, g


def test_a_200kb_brief_arrives_byte_identical_and_never_rides_argv(run, server, monkeypatch):
    s, db, g = run
    brief = ("# brief\n" + "x" * 1000 + "\n") * 200        # ~200 KB
    seen = []
    real_run = subprocess.run

    def spy(argv, **kw):
        seen.append(list(argv))
        return real_run(argv, **kw)
    monkeypatch.setattr("kernel.cli.subprocess.run", spy)
    sessions.prompt_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                            phase="spec", epoch=0, cause="f-1", text=brief.encode(), env=_env(db, g))
    path, body = Recorder.received[-1]
    assert path == "/v1/sessions/s-1/events"
    assert json.loads(body)["data"]["content"][0]["text"] == brief
    argv = seen[-1]
    assert max(len(a) for a in argv) < 128 * 1024
    assert all(brief[:64] not in a for a in argv)
    row = s.effect_by_key(f"sess-prompt:s-1:{g}:f-1", run_id="r-1")
    assert brief[:64] not in json.dumps(row["intent"])
    assert "--data-binary" not in row["intent"]["argv"]


@pytest.mark.skipif(platform.system() != "Linux", reason="MAX_ARG_STRLEN is Linux's")
def test_the_same_brief_as_an_argv_argument_fails_with_e2big(server):
    brief = ("x" * 1000 + "\n") * 200
    with pytest.raises(OSError) as exc:
        subprocess.run(["curl", "-sSf", "-X", "POST", f"{server}/v1/sessions/s-1/events",
                        "-d", brief], capture_output=True)
    assert exc.value.errno == 7


def test_stop_no_runner_confirms(run, server):
    """§11: a stop the server answers 2xx `{"queued": false}` (no runner bound)
    is confirmed -- a session running nowhere is what the stop wanted."""
    s, db, g = run
    sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                          cause="te-1", env=_env(db, g))
    assert s.effect_by_key(f"sess-stop:s-1:{g}", run_id="r-1")["state"] == "confirmed"
    assert json.loads(Recorder.received[-1][1]) == {"type": "stop_session"}
    assert not is_halted(s, "r-1")


def test_stop_503_halts(run, server):
    """§11: a stop the server answers 503 (runner unreachable) is uncertain,
    the run halted, the key pending -- never confirmed, never pretended."""
    s, db, g = run
    Recorder.stop_status, Recorder.stop_body = 503, b'{"detail": "runner unreachable"}'
    with pytest.raises(UncertainEffect):
        sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                              cause="te-1", env=_env(db, g))
    assert is_halted(s, "r-1")
    assert [p["idempotency_key"] for p in pending_reconciliation(s, "r-1")] == [f"sess-stop:s-1:{g}"]


def test_stop_404_halts_and_bare_delivered_reconciles(run, server):
    from kernel.effects import Resolution, reconcile_typed
    s, db, g = run
    Recorder.stop_status, Recorder.stop_body = 404, b'{"detail": "no such session"}'
    with pytest.raises(UncertainEffect):
        sessions.stop_session(s, run_id="r-1", generation=g, server=server, session_id="s-1",
                              cause="te-1", env=_env(db, g))
    key = f"sess-stop:s-1:{g}"
    with pytest.raises(ValueError):
        reconcile_typed(s, "r-1", [Resolution(key, True, "some-id")], s.run_version("r-1"))
    reconcile_typed(s, "r-1", [Resolution(key, True, None)], s.run_version("r-1"))
    assert not is_halted(s, "r-1")
