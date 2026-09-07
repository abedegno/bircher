"""Session effects (spec §3 *Sessions are effects*): every create, prompt and
stop is a SESSION_CONTROL effect with an obligation, performed through
perform_effect. The read side (state, items) is session.py."""
from __future__ import annotations

import json
import os
import subprocess

from coordinator.effects import perform_effect
from coordinator.session import LookupFailed, _fetch
from kernel.artifacts import put_artifact

SESSION_CONTROL = "session_control"
_JSON = ["-H", "content-type: application/json"]


class WorktreeExists(Exception):
    pass


def worktree_path(workspaces_root: str, run_id: str, generation: int) -> str:
    return os.path.join(workspaces_root, run_id, str(generation))


def add_worktree(repo_dir: str, base_sha: str, path: str) -> str:
    """One worktree per session at the run's base sha. Refuses an existing
    path before anything is added; returns the realpath the create names."""
    if os.path.lexists(path):
        raise WorktreeExists(f"worktree path already exists: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(["git", "-C", repo_dir, "worktree", "add", "--detach", path, base_sha],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    return os.path.realpath(path)


def _events_argv(server: str, session_id: str, max_time: str) -> list[str]:
    # No -d: the kernel composes the event from the obligation and appends
    # --data-binary @<file> after the contract check (Task 3; ruling 1).
    return ["curl", "-sSf", "--max-time", max_time, "-X", "POST",
            f"{server}/v1/sessions/{session_id}/events", *_JSON]


def create_session(store, *, run_id: str, generation: int, server: str, agent_id: str,
                   host_id: str, workspace: str, phase: str, epoch: int, cause: str, env) -> dict:
    key = f"sess-create:{run_id}:{generation}"
    body = {"agent_id": agent_id, "host_id": host_id, "workspace": workspace, "title": key}
    argv = ["curl", "-sSf", "--max-time", "30", "-X", "POST", f"{server}/v1/sessions",
            *_JSON, "-d", json.dumps(body, sort_keys=True)]
    obligation = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": epoch, "cause": cause}
    out = perform_effect(SESSION_CONTROL, key, argv, timeout=40, env=env, obligation=obligation)
    return json.loads(out)


def prompt_session(store, *, run_id: str, generation: int, server: str, session_id: str,
                   phase: str, epoch: int, cause: str, text: bytes, env) -> str:
    h = put_artifact(store, text)
    key = f"sess-prompt:{session_id}:{generation}:{cause}"
    obligation = {"kind": "sess-prompt", "session": session_id, "phase": phase, "epoch": epoch, "cause": cause}
    return perform_effect(SESSION_CONTROL, key, _events_argv(server, session_id, "120"),
                          timeout=130, env=env, obligation=obligation, body={"artifact": h})


def stop_session(store, *, run_id: str, generation: int, server: str, session_id: str,
                 cause: str, env) -> str:
    key = f"sess-stop:{session_id}:{generation}"
    obligation = {"kind": "sess-stop", "session": session_id, "cause": cause}
    return perform_effect(SESSION_CONTROL, key, _events_argv(server, session_id, "30"),
                          timeout=40, env=env, obligation=obligation, body={"event": "stop_session"})


def list_sessions(server: str, *, fetch=_fetch) -> set[str]:
    d = json.loads(fetch(f"{server}/v1/sessions"))
    raw = d.get("sessions", d.get("items", d)) if isinstance(d, dict) else d
    if not isinstance(raw, list):
        raise LookupFailed("sessions: not a list")
    return {str(x.get("id")) for x in raw if isinstance(x, dict)}


def satisfied(store, run_id: str, obligation: dict) -> dict | None:
    """The satisfied effect row carrying exactly *obligation*, else None:
    the 'is it owed?' read before every send (spec §3 *Obligations*)."""
    from kernel.front import satisfied_effects
    for row in satisfied_effects(store, run_id, obligation["kind"]):
        if row["intent"].get("obligation") == obligation:
            return row
    return None
