# v2/coordinator/sweep.py
"""The sweep (shaping spec §3 *The sweep*): each firing of the scheduled
wave, before it generates the queue, reads every child of every filed epic,
records what it saw, and ends the epic whose children all closed.

Reads are not effects (front-half §3); they are injected as `gh_json` so
tests answer them from a dict and the CLI answers them with `gh`. A read
that fails ends this run's firing there: the facts recorded before it stand,
nothing after it is read or recorded, no completion is attempted (§3).

Every run is isolated from every other (fix round 1): `store.all_run_ids()`
is stable oldest-first, so an unhandled exception from one run's processing
must not abort the firing for every run listed after it -- and must not wedge
every later firing the same way, since the loop is the only place that ever
retries. A `ReadFailed` is the sweep's own, expected way for a run's firing to
end quietly; anything else is a bug or a race (a guard refusing under one, a
`PendingEffects` from a dispatch collision) and is caught, logged by run, and
left for the next firing to retry -- neither ended nor recorded as touched
this time.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess

from coordinator import phases, seat
from coordinator.effects import perform_effect
from kernel import filing as kfiling
from kernel import front, slices
from kernel.dispatch import Role, dispatch
from kernel.effect_class import EffectClass
from kernel.effects import is_halted
from kernel.events import EventKind


class ReadFailed(Exception):
    """A `gh` read did not answer."""


def gh_json(argv: list[str]) -> dict:
    r = subprocess.run(list(argv), capture_output=True, text=True)
    if r.returncode != 0:
        raise ReadFailed(f"{' '.join(argv[:4])} rc={r.returncode}: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except ValueError as exc:
        raise ReadFailed(f"{' '.join(argv[:4])}: not JSON: {r.stdout[:200]!r}") from exc


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _closure(gh, repo: str, issue: dict) -> tuple[str, str] | None:
    """`(state, closed_at)` for a closed child -- `merged` when a pull request
    that closed it is MERGED, `closed` otherwise -- or None when open."""
    if issue.get("state") != "CLOSED":
        return None
    state = "closed"
    for ref in issue.get("closedByPullRequestsReferences") or []:
        pr = gh(["gh", "pr", "view", str(ref["number"]), "--repo", repo, "--json", "state"])
        if pr.get("state") == "MERGED":
            state = "merged"
            break
    return state, str(issue.get("closedAt") or "")


def _sweep_run(store, run_id: str, n: int, *, server: str, repo: str, env: dict, gh_json, now, log) -> bool:
    """One run's firing: every child read, closures and reopenings recorded,
    and -- if every read succeeded and every child was observed closed -- the
    completion effects and the outcome, all under one operator generation.
    Returns whether this run ended. Raises `ReadFailed` on a failed read;
    anything else it raises is the caller's to isolate per run."""
    parent = front.issue_number(store, run_id)
    ctx = phases.Ctx(store=store, run_id=run_id, server=server, repo=repo, issue_number=parent, repo_dir="",
                     workspaces_root="", bundle_dir="", agent_ids={}, host_id="", turn_timeout_s=1,
                     default_author="", env=dict(env, BIRCHER_RUN_ID=run_id), fetch=None, log=log)
    ctx.generation = dispatch(store, run_id, actor="coordinator", role=Role.OPERATOR).generation
    plan = front.accepted_plan(store, run_id, n)
    numbers = kfiling.filed_numbers(store, run_id, n)
    for s in plan.slices:
        issue = gh_json(["gh", "issue", "view", str(numbers[s.number]), "--repo", repo,
                         "--json", "state,closedAt,closedByPullRequestsReferences"])
        seen = _closure(gh_json, repo, issue)
        cur = front.current_closure(store, run_id, n, s.number)
        if seen is not None and (cur is None or cur.kind != EventKind.SLICE_CLOSED):
            seat.command(ctx, "record_slice_closed", {"slice": s.number, "closed_at": seen[1] or now(), "state": seen[0]})
        elif seen is None and cur is not None and cur.kind == EventKind.SLICE_CLOSED:
            seat.command(ctx, "record_slice_reopened", {"slice": s.number, "observed_at": now()})
    all_closed = all((c := front.current_closure(store, run_id, n, s.number)) is not None
                     and c.kind == EventKind.SLICE_CLOSED for s in plan.slices)
    if not all_closed:
        return False
    parent_view = gh_json(["gh", "issue", "view", str(parent), "--repo", repo, "--json", "state"])
    parent_state = "closed" if parent_view.get("state") == "CLOSED" else "open"
    seat.command(ctx, "record_children_observed_closed", {"parent_state": parent_state, "observed_at": now()})
    base = {"run": run_id, "epoch": n}
    states = {s.number: front.current_closure(store, run_id, n, s.number).payload["state"] for s in plan.slices}
    ob = dict(base, kind="umbrella_close")
    if front.satisfied_obligation(store, run_id, ob) is None:
        perform_effect(EffectClass.COMMENT, f"umbrella-close:{run_id}:{ctx.generation}",
                       kfiling.comment_argv(repo, parent, slices.completion_body(plan, numbers, states)),
                       timeout=60, env=ctx.effect_env(), obligation=ob)
    ob = dict(base, kind="parent_close")
    if parent_state == "open" and front.satisfied_obligation(store, run_id, ob) is None:
        perform_effect(EffectClass.ISSUE_OR_LABEL, f"parent-close:{run_id}:{ctx.generation}",
                       kfiling.close_argv(repo, parent), timeout=60, env=ctx.effect_env(), obligation=ob)
    seat.command(ctx, "record_run_outcome", {"outcome": "sliced"})
    return True


def sweep_sliced(store, *, server: str, repo: str, env: dict, gh_json=gh_json, now=None, log=print) -> list[str]:
    now = now or _now
    ended = []
    for run_id in store.all_run_ids():
        if store.run_state(run_id) != "sliced":
            continue
        n = front.epoch(store, run_id)
        if is_halted(store, run_id) or store.uncertain_effects(run_id):
            log(f"sweep: {run_id} is halted or holds pending effects; skipped")
            continue
        if front.filing_complete(store, run_id, n) is None:
            log(f"sweep: {run_id} has no filing_complete; the loop's, not the sweep's")
            continue
        try:
            if _sweep_run(store, run_id, n, server=server, repo=repo, env=env, gh_json=gh_json, now=now, log=log):
                ended.append(run_id)
        except ReadFailed as exc:
            log(f"sweep: {run_id}: a read failed, the firing ends here for this run: {exc}")
        except Exception as exc:
            # Any other failure -- a guard refusing under a race, a
            # PendingEffects from a dispatch collision, a bug -- is this run's
            # alone: the facts already recorded for it stand, nothing further
            # is attempted this firing, and every OTHER run on the list still
            # gets its turn. Not retried within this call; the next firing
            # tries again from the journal, as any other unfinished run does.
            log(f"sweep: {run_id}: {type(exc).__name__}: {exc}; the firing continues with the next run")
    return ended
