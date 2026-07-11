#!/usr/bin/env python3
"""PTY-probe Claude Code's live subscription usage (ToS-clean).

Runs the genuine ``claude`` binary in an interactive PTY with a one-shot
``--settings`` override that points ``statusLine`` at a capture script, sends a
single trivial prompt to force one API turn (the ``rate_limits`` object is
absent until a request is made), then reads the authoritative account-wide
``rate_limits.five_hour/seven_day {used_percentage, resets_at}`` that Claude
Code feeds its own statusLine hook. Nothing reuses the OAuth token or calls a
scope-gated endpoint -- this is just Claude Code running and reporting its own
usage, the same data ``/usage`` shows. Validated live on macOS (full login) and
the NAS runner (inference-only setup-token) 2026-07-08.

Prints ``5h_pct|5h_reset_epoch|7d_pct|7d_reset_epoch`` to stdout (percent 0-100,
resets as epoch seconds; "-" for any absent field) and exits 0 on success.
Prints nothing and exits non-zero when claude is missing, the PTY drive fails,
or no ``rate_limits`` render was captured -- callers degrade to the default
vendor. Kept standalone (not sourced into run-queue.sh) so it is independently
runnable/testable and so the bash side stays free of PTY handling.
"""

from __future__ import annotations

import datetime
import json
import os
import pty
import select
import shutil
import signal
import sys
import tempfile
import time


def _epoch(value: object) -> str:
    """ISO-8601 (or epoch-ish) reset time -> epoch-seconds string, else '-'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, str) and value.strip():
        try:
            return str(
                int(
                    datetime.datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).timestamp()
                )
            )
        except ValueError:
            return "-"
    return "-"


def _tuple_from_rate_limits(rl: dict) -> str:
    """Build the pipe tuple from a statusLine ``rate_limits`` object."""
    def pct(v: object) -> str:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(round(float(v), 1))  # tidy float artifacts (14.0000002 -> 14.0)
        return "-"

    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    return "%s|%s|%s|%s" % (
        pct(five.get("used_percentage")),
        _epoch(five.get("resets_at")),
        pct(seven.get("used_percentage")),
        _epoch(seven.get("resets_at")),
    )


def probe(timeout: float = 55.0) -> str | None:
    """Drive claude once in a PTY and return the usage tuple, or None."""
    claude = shutil.which("claude")
    if not claude:
        return None
    work = tempfile.mkdtemp(prefix="bircher-usage-")
    cap = os.path.join(work, "capture.jsonl")
    capsh = os.path.join(work, "capture.sh")
    settings = os.path.join(work, "settings.json")
    open(cap, "w").close()
    with open(capsh, "w") as fh:
        # Append each statusLine render (its stdin JSON) + a delimiter.
        fh.write('#!/bin/bash\ncat >> %r\nprintf "\\n===SL===\\n" >> %r\n' % (cap, cap))
    os.chmod(capsh, 0o755)
    json.dump({"statusLine": {"type": "command", "command": capsh}}, open(settings, "w"))

    deadline = time.time() + timeout
    pid, fd = pty.fork()
    if pid == 0:  # child: the interactive claude TUI
        os.chdir(work)
        os.environ["TERM"] = "xterm-256color"
        os.execv(claude, [claude, "--settings", settings])
        os._exit(127)  # unreachable on success

    def _latest_rate_limits() -> dict | None:
        try:
            blob = open(cap, encoding="utf-8", errors="replace").read()
        except OSError:
            return None
        found = None
        for chunk in blob.split("===SL==="):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                payload = json.loads(chunk)
            except ValueError:
                continue
            rl = payload.get("rate_limits")
            if isinstance(rl, dict) and rl:
                found = rl
        return found

    def drain(seconds: float, until_rate_limits: bool = False) -> dict | None:
        """Pump the PTY for up to `seconds`; if until_rate_limits, return the
        captured rate_limits as soon as one appears (early-exit)."""
        end = min(time.time() + seconds, deadline)
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.3)
            if fd in ready:
                try:
                    if not os.read(fd, 4096):
                        break
                except OSError:
                    break
            if until_rate_limits:
                rl = _latest_rate_limits()
                if rl is not None:
                    return rl
        return _latest_rate_limits() if until_rate_limits else None

    def send(text: str) -> None:
        try:
            os.write(fd, text.encode())
        except OSError:
            pass

    try:
        drain(7)  # boot + any first-run screen
        send("\r")  # accept a possible trust prompt
        drain(2)
        send("reply with the single word ok\r")  # one turn -> populates rate_limits
        drain(max(0.0, deadline - time.time()), until_rate_limits=True)  # exit early once captured
        send("\x03")
        time.sleep(0.3)
        send("\x03")  # Ctrl-C out of the TUI
        time.sleep(0.3)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    result = None
    try:
        blob = open(cap, encoding="utf-8", errors="replace").read()
    except OSError:
        blob = ""
    for chunk in blob.split("===SL==="):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            payload = json.loads(chunk)
        except ValueError:
            continue
        rl = payload.get("rate_limits")
        if isinstance(rl, dict) and rl:
            result = _tuple_from_rate_limits(rl)  # keep the latest render
    shutil.rmtree(work, ignore_errors=True)
    return result


def main() -> int:
    timeout = 55.0
    if len(sys.argv) > 1:
        try:
            timeout = float(sys.argv[1])
        except ValueError:
            pass
    tup = probe(timeout)
    if not tup:
        return 1
    print(tup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
