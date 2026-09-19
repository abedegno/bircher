"""The back half, read from the journal (closed-loop spec §1-§3).

Everything a step decision needs about a run with a pull request: whether an
implementation output exists, what CI and the PR last looked like, which
verdict sits on a head, and whether the repair about to be requested would
be the third identical one in a row. Pure reads; nothing here writes.
"""

from __future__ import annotations

from kernel.events import EventKind


def _accepted(store, run_id: str, name: str) -> list:
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.COMMAND_ACCEPTED and f.payload.get("command_name") == name]


def implementation_output_recorded(store, run_id: str) -> bool:
    """True once the run has produced an implementation (a pull request)."""
    return bool(_accepted(store, run_id, "record_implementation_output"))


def latest_ci(store, run_id: str) -> dict | None:
    """The newest CI observation's payload, or None before any."""
    obs = _accepted(store, run_id, "record_ci_observation")
    return (obs[-1].payload.get("payload") or {}) if obs else None


def latest_pr_state(store, run_id: str) -> str | None:
    ci = latest_ci(store, run_id)
    return None if ci is None else ci.get("pr_state")


def back_verdicts(store, run_id: str) -> list:
    """Implementation-phase verdicts, oldest first."""
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.REVIEW_VERDICT and f.payload.get("phase") == "implementation"]


def verdict_for_head(store, run_id: str, head: str):
    """The newest verdict recorded against exactly this head, or None."""
    for f in reversed(back_verdicts(store, run_id)):
        if f.payload.get("head_sha") == head:
            return f
    return None


def _newest_grant_seq(store, run_id: str) -> int:
    seq = 0
    for f in store.facts_for(run_id):
        if f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "grant_round":
            seq = f.seq
    return seq


def repairs_since_grant(store, run_id: str) -> list:
    """Repair requests after the newest round grant, oldest first. A grant
    resets the no-progress window (spec §3): the next round is judged
    against itself."""
    since = _newest_grant_seq(store, run_id)
    return [f for f in store.facts_for(run_id)
            if f.kind == EventKind.REPAIR_REQUESTED and f.seq > since]


def repair_for_head(store, run_id: str, head: str):
    """The newest repair requested against this head, or None."""
    for f in reversed(list(store.facts_of_kind(run_id, EventKind.REPAIR_REQUESTED))):
        if f.payload.get("head_sha") == head:
            return f
    return None


def would_be_third_identical(store, run_id: str, cause: str, evidence) -> bool:
    """spec §3: no progress is the same cause with the same evidence three
    rounds running. True when the two newest repairs since the grant already
    carry this cause and this evidence set, so requesting it again would be
    the third."""
    recent = repairs_since_grant(store, run_id)[-2:]
    if len(recent) < 2:
        return False
    want = (cause, frozenset(evidence or ()))
    return all((f.payload.get("cause"), frozenset(f.payload.get("evidence") or ())) == want for f in recent)
