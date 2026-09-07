"""Human interaction (spec §4): the cursor, the discriminator, the batch
rules, dismissals and their replies, and the human pass."""
from __future__ import annotations

import json

from coordinator import seat, sessions
from coordinator.session import LookupFailed, list_items
from kernel import front
from kernel.authz import NotAuthorized
from kernel.canon import content_hash
from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
from kernel.events import EventKind

APPROVE, RETRY = "approve", "retry"


def cursor(store, run_id: str, session_id: str, listing: list) -> str | None:
    """The newest item the coordinator has listed (spec §4).

    The RUN's, not the session's: every fact that moves the cursor carries
    the id of the last item of the listing it was derived from, so the newest
    such fact is where reading resumes. A run that has recorded nothing yet
    starts at the session's first item, and an empty listing has no cursor at
    all.
    """
    for f in reversed(store.facts_for(run_id)):
        c = f.payload.get("cursor_item_id") if isinstance(f.payload, dict) else None
        if c:
            return c
    return listing[0]["id"] if listing else None


def _own_prompts(ctx, session_id: str) -> tuple[set, set]:
    """The ids and the content hashes of the prompts the coordinator itself
    sent to *session_id*.

    Per session, as the kernel's own duplicate rule is (Task 17): omnigent's
    item ids are not shown to be unique across sessions, so a bare id set
    could read a human's message here as another session's recorded prompt
    and drop it unread. The hashes are per session already.
    """
    ids = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, EventKind.PROMPT_ITEM)
           if p.payload["session_id"] == session_id}
    hashes = front.prompt_hashes_of(ctx.store, ctx.run_id, session_id)
    return ids, hashes


def unread_human_items(ctx, session_id: str, listing: list) -> list:
    """Every user-role item after the cursor that the coordinator did not send.

    A prompt is excluded by its `prompt_item` id, or -- in the crash window
    between a confirmed prompt and its `record_prompt_item` -- by its content
    hash matching a satisfied prompt effect's `body.artifact`. A cursor this
    listing does not contain belongs to another session's listing, and leaves
    the whole of this one to be read; both exclusions still apply.
    """
    ids, hashes = _own_prompts(ctx, session_id)
    cur = cursor(ctx.store, ctx.run_id, session_id, listing)
    after = []
    seen = cur is None
    for it in listing:
        if seen:
            after.append(it)
        if it["id"] == cur:
            seen = True
    if not seen:
        after = listing
    return [it for it in after if it["role"] == "user" and it["id"] not in ids
            and content_hash(it["text"].encode()) not in hashes]


def classify_batch(items: list, *, state: str, grill_open: bool) -> tuple[str, str]:
    """What one batch of human messages IS (spec §4), given the run's state.

    A batch is ONE fact, never several: an answer while questions are open;
    at a gate or a stall the single bare token `approve` or `retry`, and
    corrections otherwise; at `queued`/`specified` a direction, unless it is
    the bare `retry` that grants a round. The token has to be the WHOLE
    message, trimmed and case-folded -- `approve?` is a question and
    `approve.` is prose, and neither of them is a ruling.
    """
    texts = [it["text"].strip() for it in items]
    joined = "\n\n".join(texts)
    if grill_open:
        return "answer", joined
    single = texts[0].casefold() if len(texts) == 1 else None
    if single == RETRY:
        return "retry", ""
    if state in ("spec_submitted", "spec_accepted", "plan_submitted", "plan_accepted"):
        if single == APPROVE:
            return "approve", ""
        return "revision", joined
    return "direction", joined


def _human(ctx, name: str, payload: dict, key: str):
    return execute_as_human(ctx.store, Command(
        name=name, run_id=ctx.run_id, expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=key, generation=HUMAN_GENERATION, payload=payload))


def take_listing(ctx, session_id: str, listing: list) -> str | None:
    """Record what the human said in *listing*, or None if they said nothing.

    Every listing is discriminated (spec §4), and the batch becomes one fact.
    The two cursor-bearing shapes carry `cursor_item_id = listing[-1]["id"]`
    -- the last item of the listing that was READ, not of the batch, so an
    assistant item written after the human's message is behind the cursor
    too. The three ruling shapes carry no cursor (Task 8): each transitions
    the run or grants a round, so the next listing belongs to a new park or a
    new turn and takes its cursor from there.

    A token the kernel refuses is DISMISSED rather than retried: the
    dismissal moves the cursor past it and names the refusal, and the reply
    it owes is sent by `reply_refusals`.
    """
    store, run_id = ctx.store, ctx.run_id
    items = unread_human_items(ctx, session_id, listing)
    if not items:
        return None
    state, n = store.run_state(run_id), front.epoch(store, run_id)
    questions = front.epoch_facts(store, run_id, EventKind.MODEL_QUESTION, n)
    answers = front.epoch_facts(store, run_id, EventKind.HUMAN_ANSWER, n)
    newer_q = [q for q in questions if not answers or q.seq > answers[-1].seq]
    kind, text = classify_batch(items, state=state, grill_open=bool(newer_q))
    cur = listing[-1]["id"]
    key = f"human:{run_id}:{items[-1]['id']}"
    phase = front.FRONT_PHASES[0] if state in ("queued", "spec_submitted", "spec_accepted") else "plan"
    try:
        if kind == "answer":
            _human(ctx, "record_human_answer", {"question_ids": [q.payload["question_id"] for q in newer_q],
                                                "answer": text, "cursor_item_id": cur}, key)
        elif kind == "approve":
            _human(ctx, "approve_artifact", {"artifact_hash": store.phase_artifact(run_id, phase)}, key)
        elif kind == "retry":
            _human(ctx, "grant_round", {}, key)
        elif kind == "revision":
            _human(ctx, "record_review", {"phase": phase, "artifact_hash": store.phase_artifact(run_id, phase),
                                          "verdict": "request_revision", "findings": text}, key)
        else:
            _human(ctx, "record_human_direction", {"text": text, "cursor_item_id": cur}, key)
    except NotAuthorized:
        rej = store.newest_fact(run_id, EventKind.COMMAND_REJECTED)
        seat.command(ctx, "dismiss_human_item", {"cursor_item_id": cur, "rejection": rej.id})
        return "dismissed"
    return kind


def _send(ctx, session_id: str, cause: str, text: bytes) -> None:
    """One prompt, by obligation: sent only where the journal owes it, and its
    `prompt_item` recorded either way -- the crash window between a confirmed
    prompt and its record is closed by finding the item by hash."""
    phase, n = ctx.phase(), ctx.epoch()
    ob = {"kind": "sess-prompt", "session": session_id, "phase": phase, "epoch": n, "cause": cause}
    if sessions.satisfied(ctx.store, ctx.run_id, ob) is None:
        sessions.prompt_session(ctx.store, run_id=ctx.run_id, generation=ctx.generation, server=ctx.server,
                                session_id=session_id, phase=phase, epoch=n, cause=cause, text=text,
                                env=ctx.effect_env())
    seat._record_prompt_item(ctx, session_id, content_hash(text))


def reply_refusals(ctx) -> list[str]:
    """One reply per dismissal, once (spec §4: the refusal is answered).

    The obligation is derived from the journal, not from when the dismissal
    happened: a dismissal whose reply never went out is still owed on every
    later pass, however many human facts have landed since. A server that
    cannot be read, or that no longer lists the session, is skipped rather
    than raised -- a reply that cannot be delivered is not a reason to fail
    the pass, and the obligation survives to the next one.
    """
    store, run_id = ctx.store, ctx.run_id
    try:
        listed = sessions.list_sessions(ctx.server, fetch=ctx.fetch)
    except (LookupFailed, ValueError):
        return []
    sent = []
    rejections = {f.id: f for f in front.human_rejections(store, run_id)}
    for d in store.facts_of_kind(run_id, EventKind.HUMAN_ITEM_DISMISSED):
        rej = rejections.get(d.payload["rejection"])
        sid = _session_for_reply(ctx, d)
        if rej is None or sid is None or sid not in listed:
            continue
        ob = {"kind": "sess-prompt", "session": sid, "phase": ctx.phase(), "epoch": ctx.epoch(), "cause": rej.id}
        if sessions.satisfied(store, run_id, ob) is not None:
            continue
        text = f"Bircher refused that: {rej.payload.get('detail', '')}".encode()
        _send(ctx, sid, rej.id, text)
        sent.append(rej.id)
    return sent


def _session_for_reply(ctx, dismissal) -> str | None:
    """The session the dismissed item was read from: the newest session the
    run prompted before the dismissal."""
    rows = front.satisfied_effects(ctx.store, ctx.run_id, "sess-prompt")
    return rows[-1]["intent"]["obligation"]["session"] if rows else None


def _park_session(ctx, park) -> str | None:
    """The session that carries the park's prompt: the confirmed `sess-create`
    whose cause is the park itself, else the session the park named."""
    for row in front.satisfied_effects(ctx.store, ctx.run_id, "sess-create"):
        if row["intent"]["obligation"].get("cause") == park.id:
            return json.loads(row["external_object_id"])["id"]
    return park.payload.get("session_id")


def park_prompt(ctx, park) -> bytes:
    """What the park asks the human for, by reason.

    A grill asks nothing: the author's own questions are already the last
    thing in the session, and re-listing them would be the coordinator
    answering for the model.
    """
    store, run_id = ctx.store, ctx.run_id
    reason, phase = park.payload["reason"], park.payload["phase"]
    if reason == "grill":
        return b""                                       # the author's own questions are the prompt
    h = store.phase_artifact(run_id, phase)
    art = store.read_blob(h).decode("utf-8", "replace") if h else "(no artefact yet)"
    if reason == "gate":
        return (f"The {phase} below was accepted by the reviewer and waits for your approval.\n\n"
                f"Reply with the single word `approve`, or give corrections.\n\n---\n\n{art}").encode()
    findings = ""
    if park.payload.get("findings_hash"):
        findings = ("\n\n## Final findings\n\n"
                    + store.read_blob(park.payload["findings_hash"]).decode("utf-8", "replace"))
    return (f"The run is stalled ({reason}) at {phase}.\n\n"
            f"Reply with the single word `retry`, or give corrections.\n\n---\n\n{art}{findings}").encode()


def human_pass(ctx, park) -> str:
    """One pass over the parked session: `"taken"` when a human fact landed,
    else `"parked"` (spec §3 loop, §4).

    The human comes FIRST, before anything is sent: a message typed while the
    coordinator was elsewhere is the answer to this park, and prompting over
    it would ask a question already answered. The CONFIRM listing is
    discriminated too, so a message typed between the list and the prompt
    consumes the park in the same pass rather than waiting for the next one.
    """
    sid = _park_session(ctx, park)
    if sid is None:
        return "parked"
    try:
        listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    except LookupFailed:
        return "parked"
    if take_listing(ctx, sid, listing) not in (None, "dismissed"):
        return "taken"
    text = park_prompt(ctx, park)
    if text:
        _send(ctx, sid, park.id, text)
    reply_refusals(ctx)
    listing = list_items(ctx.server, sid, fetch=ctx.fetch)
    if take_listing(ctx, sid, listing) not in (None, "dismissed"):
        return "taken"
    return "parked"
