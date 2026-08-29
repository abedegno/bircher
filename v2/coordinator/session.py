"""Talking to omnigent.

The second thing moved out of `batch/run-queue.sh`. These were already Python:
`_json_get`, `_session_state` and `_last_assistant_text` each shelled out to
`python3 -c` from inside a bash function, which is the clearest possible sign
of which side of the boundary they belong on.

Read-only, deliberately. `_create_session`, `_send_prompt` and `_stop_session`
MUTATE and stay in bash for now: two of them are unrouted mutations the effect
inventory already dispositions, and routing them properly needs the coordinator
to own effect journalling rather than reach it through a CLI. That is the next
slice, not this one.

The transport is injectable so tests need no network.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

#: Matches the bash these replace. The session-state poll ran with a 10s cap;
#: keeping it means a hung server degrades a poll rather than the run.
_TIMEOUT = 10


class LookupFailed(Exception):
    """The server could not be read, or answered with something unusable.

    ONE exception for both, on purpose: a 200 carrying malformed JSON is as
    failed a lookup as a connection error, and an earlier version of the shell
    that distinguished them reproduced the ambiguity it was written to remove.
    """


def _fetch(url: str) -> str:
    """Via `curl`, not urllib, and the reason is a scar.

    `--self-test` fakes the omnigent server by putting a stub `curl` on PATH.
    That seam exists because a real endpoint removal in omnigent v0.9.0 went
    unnoticed (#61), and switching this to urllib would have deleted the test
    that catches that class while leaving it looking present.

    It is also the same choice `observe.py` makes with `gh`: reuse the tool
    that already handles the transport rather than reimplement it.
    """
    r = subprocess.run(
        ["curl", "-sf", "--max-time", str(_TIMEOUT), url],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise LookupFailed(f"curl rc={r.returncode}: {r.stderr.strip()[:120]}")
    return r.stdout


@dataclass(frozen=True)
class State:
    status: str = "unknown"
    error_code: str = ""


def state(server: str, conv_id: str, *, fetch=_fetch) -> State:
    """The session's status and last task error code.

    An unreachable or unparseable server is `unknown`, never a guess. The
    caller counts consecutive unknowns and keeps waiting rather than recovering
    while blind -- see run_item's teardown.
    """
    try:
        d = json.loads(fetch(f"{server}/v1/sessions/{conv_id}"))
    except (LookupFailed, ValueError):
        return State()
    if not isinstance(d, dict):
        return State()
    labels = d.get("labels")
    return State(
        status=d.get("status") or "",
        error_code=(labels or {}).get("omnigent.last_task_error_code") or "",
    )


def died(status: str, error_code: str) -> bool:
    """Whether a session is DEAD, not whether it is busy.

    NOT called from bash, deliberately. This is pure logic, and routing the
    shell's caller through a subprocess spawned an interpreter per poll for a
    four-line predicate -- and when the spawn could not run, the loop never saw
    death and polled to its cap. `run-queue.sh` keeps its own copy until the
    poll loop itself is Python; this is the version that survives.

    `idle` is not death -- a coordinator awaiting a sub-agent is idle -- so
    only an explicit failure state or a task error code counts. Reading idle as
    death would start recovery against a session still working the PR.
    """
    if error_code.strip():
        return True
    return status in ("failed", "error", "cancelled")


def last_assistant_text(server: str, conv_id: str, n: int = 3, *, fetch=_fetch) -> str:
    """The most recent assistant text, newest first, up to `n` items.

    Raises LookupFailed rather than returning "" on a bad lookup: the caller
    uses this to detect a provider limit message, and "no text" and "could not
    read" must not be the same value. They were, once.
    """
    try:
        payload = json.loads(fetch(
            f"{server}/v1/sessions/{conv_id}/items?order=desc&limit=20"))
    except ValueError as exc:
        raise LookupFailed("unparseable session-items response") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise LookupFailed("session-items response has no data list")

    out: list[str] = []
    for item in data:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        for c in item.get("content") or []:
            text = c.get("text") if isinstance(c, dict) else None
            if text:
                out.append(text)
        if len(out) >= n:
            break
    return "\n".join(out)


@dataclass(frozen=True)
class Settled:
    """Whether a session has gone quiet, and the count to carry to the next poll."""

    count: int | None
    stable_polls: int
    settled: bool


def settle(status: str, count: int | None, prev_count: int | None,
           stable_polls: int, *, needed: int) -> Settled:
    """Has the coordinator finished, judged by OBSERVATION rather than report?

    The marker used to answer this: the coordinator posted one when it decided
    it had converged. Removing it cost an item ~23 minutes of waiting for a cap
    (measured: 136s -> 1536s), because nothing else said "done".

    The observable stand-in is a session that is IDLE and has stopped producing
    items. Neither alone is enough:

    - `idle` is not done. A coordinator awaiting a sub-agent is idle, which is
      why `died()` refuses to treat it as death.
    - a stable item count is not done either, because a session mid-tool-call
      produces no items while it works.

    Together, held for `needed` consecutive polls, they say the session is not
    doing anything and has not been for a while. The caller adds the condition
    that carries the real weight: a PR must already exist. Quiet with no PR is
    a session that failed to deliver, and belongs to the cap.

    A failed lookup (`count is None`) RESETS the streak rather than extending
    it: an unreadable session is not an idle one, and treating "I cannot see"
    as "nothing is happening" is how a run derives an outcome while the
    coordinator is still writing.
    """
    if count is None or status != "idle" or count != prev_count:
        return Settled(count=count if count is not None else prev_count,
                       stable_polls=0, settled=False)
    n = stable_polls + 1
    return Settled(count=count, stable_polls=n, settled=n >= needed)


def item_count(server: str, conv_id: str, *, fetch=_fetch) -> int | None:
    """How many items the session has produced. None if it cannot be read."""
    try:
        payload = json.loads(fetch(f"{server}/v1/sessions/{conv_id}"))
    except (LookupFailed, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    return len(items) if isinstance(items, list) else None
