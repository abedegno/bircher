"""Human interaction (spec §4). Task 19 fills this in."""


def take_listing(ctx, session_id: str, listing: list) -> str | None:
    """Discriminate a listing: every unread user-role item that is not one of
    the coordinator's own prompts is recorded under the batch rules. Returns
    'direction', 'ruling', 'answer' or None. Task 19 implements the rules;
    until then nothing human is taken."""
    from kernel import front
    from kernel.canon import content_hash
    # Per session, as the kernel's own duplicate rule is: omnigent's item ids
    # are not shown to be unique across sessions, and a bare id set could read
    # a human's message here as another session's recorded prompt.
    known = {p.payload["item_id"] for p in ctx.store.facts_of_kind(ctx.run_id, "prompt_item")
             if p.payload["session_id"] == session_id}
    hashes = front.prompt_hashes_of(ctx.store, ctx.run_id, session_id)
    human = [it for it in listing if it["role"] == "user" and it["id"] not in known
             and content_hash(it["text"].encode()) not in hashes]
    if not human:
        return None
    text = "\n\n".join(it["text"].strip() for it in human)
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    execute_as_human(ctx.store, Command(
        name="record_human_direction", run_id=ctx.run_id,
        expected_version=ctx.store.run_version(ctx.run_id),
        idempotency_key=f"direction:{ctx.run_id}:{human[-1]['id']}", generation=HUMAN_GENERATION,
        payload={"text": text, "cursor_item_id": listing[-1]["id"]}))
    return "direction"
