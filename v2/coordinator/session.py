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
import pathlib
import subprocess
import time
from dataclasses import dataclass

#: Matches the bash these replace. The session-state poll ran with a 10s cap;
#: keeping it means a hung server degrades a poll rather than the run.
_TIMEOUT = 10

#: Both omnigent list routes are `limit`-capped at 1000 and cursor-paginated
#: (`after`), so the readers below ask for the largest page the server will
#: give and follow `has_more`.
PAGE_LIMIT = 1000
#: The bound on the paging loop. 200 pages of 1000 is 200_000 rows -- orders of
#: magnitude past any real session or server -- so reaching it means the server
#: is answering `has_more: true` forever, and a lookup that cannot terminate is
#: a failed lookup, not a hung coordinator.
MAX_PAGES = 200


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
    agent_id: str = ""


def state(server: str, conv_id: str, *, fetch=_fetch) -> State:
    """The session's status, last task error code, and agent_id.

    An unreachable or unparseable server is `unknown`, never a guess. The
    caller counts consecutive unknowns and keeps waiting rather than recovering
    while blind -- see run_item's teardown. `agent_id` is `""` there too, so an
    unknown read compares as nothing rather than a mismatch (wait_turn).
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
        agent_id=str(d.get("agent_id") or ""),
    )


def pages(url: str, what: str, keys: tuple[str, ...], *, fetch=_fetch):
    """Each page's raw row list from a cursor-paginated omnigent list route.

    *url* already carries the query's `order` and `limit`; this follows
    `has_more` by appending `after=<the page's last id>` until the server says
    there is no more. `first_id`/`last_id`/`has_more` are the `PaginatedList`
    fields both routes answer with (omnigent/server/schemas.py).

    *keys* are the body keys tried in order for the rows, and the last resort
    is the body itself: `data` is what the real routes send, and the others
    stay as fallbacks for a stub shaped some other way. A body with no rows
    list at all is `LookupFailed`, as is one that claims `has_more` while
    naming no cursor to follow -- both are unusable, and a reader that
    returned what it did have would report a truncation as a complete answer.

    A page with no `has_more` at all ends the loop: the fallback shapes carry
    no pagination, so a stub that answers one flat body is read exactly once,
    as it was before this paged.
    """
    after = None
    for _ in range(MAX_PAGES):
        try:
            d = json.loads(fetch(url if after is None else f"{url}&after={after}"))
        except ValueError as exc:
            raise LookupFailed(f"{what}: {exc}") from exc
        raw = d
        if isinstance(d, dict):
            for k in keys:
                if k in d:
                    raw = d[k]
                    break
        if not isinstance(raw, list):
            raise LookupFailed(f"{what}: not a list")
        yield raw
        if not (isinstance(d, dict) and d.get("has_more")):
            return
        last = d.get("last_id") or (raw[-1].get("id") if raw and isinstance(raw[-1], dict) else None)
        if not last:
            raise LookupFailed(f"{what}: has_more is true but the page names no cursor to follow")
        after = str(last)
    raise LookupFailed(f"{what}: the server is still paginating after {MAX_PAGES} pages")


def list_items(server: str, conv_id: str, *, fetch=_fetch) -> list[dict]:
    """EVERY item of the session, oldest first, as `{id, role, text}`.

    `text` is the concatenation of that item's `content[*].text` for
    `input_text`/`output_text` parts -- a tool call carries no text and
    contributes none. Unlike `state`, a bad lookup here is not a hidden
    `unknown`: the caller would otherwise be unable to tell "no items" from
    "could not read".

    EVERY item, and it PAGES to get them. The route defaults to the OLDEST 100
    (`limit=100, order=asc`, routes_items.py), and a session that authors a
    spec passes 100 items in the ordinary case -- two per tool call. A reader
    that took one page and ignored `has_more` therefore never saw the newest
    item of a working session, and the newest item is exactly the human's:
    `unread_human_items` read every user item after the cursor out of a page
    that stopped before the person's `approve`, so `human_pass` parked forever
    while the approval sat in the UI.

    `order=asc` is asked for rather than assumed, because everything
    downstream -- the cursor, the discriminator, the reviewer's first user
    item -- reads this listing as oldest-first.
    """
    out = []
    url = f"{server}/v1/sessions/{conv_id}/items?order=asc&limit={PAGE_LIMIT}"
    for raw in pages(url, "items", ("data", "items"), fetch=fetch):
        for it in raw:
            parts = it.get("content") or []
            text = "".join(p.get("text", "") for p in parts
                           if isinstance(p, dict) and p.get("type") in ("input_text", "output_text"))
            # `response_id` rides along: it is what tells a person's message
            # from one the SERVER wrote in their voice. A cancelled turn adds a
            # user-role item beginning "[System: interrupted]" whose
            # response_id is `cancel_...`, where every real message -- the
            # coordinator's prompts and the human's replies alike -- is
            # `turn_...`. Without it the coordinator reads its own stop as the
            # human speaking (see `human.unread_human_items`).
            out.append({"id": str(it.get("id")), "role": str(it.get("role") or ""), "text": text,
                        "response_id": str(it.get("response_id") or "")})
    return out


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


@dataclass(frozen=True)
class TurnEnd:
    """How a waited turn ended, and whether its watched output exists.

    `ended` is one of `file`, `dead`, `cap`, `displaced` -- never `unknown`:
    `wait_turn` does not return until one of the five checks fires.
    """
    ended: str
    file_present: bool


class AgentMismatch(Exception):
    """The session answered with an `agent_id` other than the create's
    snapshot -- spec §11: an attacker or a redeployed server could otherwise
    have `wait_turn` read (and a caller act on) another session's output
    under this run's name. Nothing past `state()` is read once this fires."""

    def __init__(self, session_id: str, expected: str, observed: str) -> None:
        super().__init__(f"session {session_id} reports agent_id {observed!r}, "
                         f"its create's snapshot said {expected!r}")
        self.session_id, self.expected, self.observed = session_id, expected, observed


def wait_turn(store, run_id: str, generation: int, server: str, session_id: str,
              watched: list[pathlib.Path], deadline_us: int, *, agent_id_expected: str,
              fetch=_fetch, clock=time.time, sleep=time.sleep, poll_s: float = 15.0) -> TurnEnd:
    """spec §3 *The turn's end is a fact*: the five checks, each poll.

    (1) a human_direction newer than this generation's dispatch ends the turn
    `displaced` before anything is fetched; (2) the session's `state()`, whose
    `agent_id` must match `agent_id_expected` (else `AgentMismatch` -- an
    `unknown` read has `agent_id == ""` and compares nothing); (3) a watched
    path present ends it `file`, whatever the status; (4) `died()` ends it
    `dead`; (5) the deadline passed ends it `cap`; else sleep and poll again.

    A dead session read past its own deadline is still `dead`, not `cap`:
    both are stopped and read the same way once the turn is over, and the
    fact records what this poll actually observed first, not which cause
    would have been reached had the session kept quiet a little longer.

    Reads nothing the turn wrote and records nothing: the caller records the
    end and stops the session.

    A generation with no `attempt_dispatched` fact makes `dispatch_seq` return
    `None`; there is then no seq to order a human_direction against, so the
    displaced check is skipped for the life of this call rather than raised --
    `wait_turn` only reads, and a read that cannot decide "newer than what"
    should not fail the turn it is watching.
    """
    from kernel import front
    watched = [pathlib.Path(p) for p in watched]
    d_seq = front.dispatch_seq(store, run_id, generation)
    while True:
        present = any(p.exists() for p in watched)
        if d_seq is not None and front.direction_after(store, run_id, d_seq) is not None:
            return TurnEnd("displaced", present)
        st = state(server, session_id, fetch=fetch)
        if st.agent_id and st.agent_id != agent_id_expected:
            raise AgentMismatch(session_id, agent_id_expected, st.agent_id)
        present = any(p.exists() for p in watched)
        if present:
            return TurnEnd("file", True)
        if died(st.status, st.error_code):
            return TurnEnd("dead", False)
        if int(clock() * 1_000_000) >= deadline_us:
            return TurnEnd("cap", any(p.exists() for p in watched))
        sleep(poll_s)


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
