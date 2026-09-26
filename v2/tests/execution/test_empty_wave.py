"""An empty wave spends nothing on the providers (2026-09-26).

The wave timer fires every 30 minutes. Each empty wave used to probe both
providers with a model call, open two dispatch-probe sessions and upload
three bundles before it learned the queue held nothing. This runs the real
`run-queue.sh` on an empty queue with every provider-facing command stubbed
to log its call.
"""
import os
import subprocess
from pathlib import Path

RQ = Path(__file__).resolve().parents[3] / "batch" / "run-queue.sh"
_LOGGING_STUB = '#!/bin/sh\necho "{name} $*" >> "{log}"\necho READY\n'
# `timeout` runs its command, so the old order reaches the stubs above
# instead of failing on a host without coreutils.
_TIMEOUT_STUB = '#!/bin/sh\nshift\nexec "$@"\n'


def test_an_empty_queue_exits_before_any_provider_call(tmp_path):
    bin_dir, queue, log = tmp_path / "bin", tmp_path / "queue", tmp_path / "called"
    bin_dir.mkdir()
    queue.mkdir()
    for name in ("claude", "codex", "curl"):
        stub = bin_dir / name
        stub.write_text(_LOGGING_STUB.format(name=name, log=log))
        stub.chmod(0o755)
    (bin_dir / "timeout").write_text(_TIMEOUT_STUB)
    (bin_dir / "timeout").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", QUEUE=str(queue),
               BIRCHER_SOURCE="queue", BIRCHER_EFFECT_MODE="legacy",
               BIRCHER_BATCH_LOCK=str(tmp_path / "lock"), SERVER="http://srv")
    r = subprocess.run(["bash", str(RQ)], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "queue empty" in r.stdout, (r.stdout, r.stderr)
    assert not log.exists(), log.read_text()
