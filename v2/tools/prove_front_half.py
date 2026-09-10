"""The §8 proof assertions over a run's journal and its sessions.

A read-only reporter: it opens the store, reads facts and satisfied effects,
fetches sessions from the server, and prints what does not hold. It writes
nothing and performs no effect of its own -- the run under proof is already
merged by the time this runs.

Two halves, because the claim has two kinds of evidence. `assert_journal`
answers everything the kernel's own facts and effects settle: no human fact
outside what the run's mode admits, at least one `model_ruling`, the two
front-half artefacts submitted with different hashes, every seat's snapshot
naming its dispatched vendor, every `review_ruling` tracing to a brief that
still re-renders byte for byte, no duplicate session obligation, every
session either prompted through to a stopped turn or stopped as an orphan,
and no output fact of a turn recorded before that turn's stop was actually
confirmed. `assert_sessions` answers what only the server can: that a session
the journal says it created still exists there under the agent the create's
snapshot named, that nothing but a recorded prompt or brief ever reached it
as a user-role item -- the check that a human did not type into the run
without parking it first -- and that an unprompted (orphan) reviewer session
was stopped exactly once, not left open or stopped twice over.
"""

from __future__ import annotations

import argparse
import json
import sys

from coordinator.human import _is_cancellation_notice
from coordinator.session import _fetch, list_items
from coordinator.sessions import list_sessions
from kernel import brief, front
from kernel.canon import content_hash
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.policy import from_payload
from kernel.store import Store

#: The three modes trade on which of these a merged run may hold (spec §8):
#: `zero` admits none, `approval` admits exactly the human's own approval and
#: the gate it answers, `human` admits any of them and checks everything else
#: unchanged.
#: HUMAN_DIRECTION is IN this set. A direction typed into a live author
#: session at `queued` writes no park and no ruling -- it is the one human
#: fact that leaves no other trace -- so leaving it out made the journal
#: half of the zero-touch proof blind to exactly the touch that is hardest
#: to see any other way.
HUMAN = {EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING, EventKind.PARKED,
         EventKind.HUMAN_DIRECTION}
MODES = ("zero", "approval", "human")


def assert_journal(store, run_id: str, *, mode: str = "zero") -> list[str]:
    """The journal-only half of the §8 proof. Every check reads facts and
    satisfied effects already in the store; nothing here fetches a session."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    fails: list[str] = []
    facts = store.facts_for(run_id)
    kinds = [f.kind for f in facts]
    human = [f for f in facts if f.kind in HUMAN]

    if mode == "zero" and human:
        fails.append(
            f"human facts present: "
            f"{[(f.kind, f.payload.get('ruling') or f.payload.get('reason')) for f in human]}"
        )
    if mode == "approval":
        extra = [f for f in human if not (
            (f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") == "approve")
            or (f.kind == EventKind.PARKED and f.payload.get("reason") == "gate")
        )]
        if extra:
            fails.append(f"human facts beyond the approval: {[f.kind for f in extra]}")
    # mode == "human" admits every human fact; everything below still runs.

    if EventKind.EFFECT_RECONCILED in kinds or store.reconciliation_evidence(run_id) is not None:
        fails.append("the run was reconciled or halted")

    from kernel.slices import SHAPE_QUESTION
    n = front.epoch(store, run_id)
    sliced_parent = bool(front.slices_filed(store, run_id, n))
    # A ruling on a question the AUTHOR raised: the shape ruling every
    # one-piece run carries would satisfy this vacuously (shaping spec §6).
    # A sliced parent is exempt -- its decision is slice_filed.
    if not sliced_parent and not any(
            f.kind == EventKind.MODEL_RULING and f.payload.get("question_id") != SHAPE_QUESTION for f in facts):
        fails.append("no model_ruling: the author did not rule on a question")

    # The FINAL epoch only (shaping spec §6, round 12): a run that submitted
    # a spec before its issue changed and was then sliced is a valid history.
    subs = {f.payload["phase"]: f.payload["hash"]
            for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED) if f.payload.get("epoch") == n}
    if sliced_parent:
        if set(subs) != {"slices"} or subs["slices"] != front.accepted_slices_hash(store, run_id, n):
            fails.append(f"artifact_submitted: a sliced parent's final epoch submits exactly the accepted slice plan: {subs}")
    elif (not {"spec", "plan"} <= set(subs) or not set(subs) <= {"slices", "spec", "plan"}
          or subs["spec"] == subs["plan"]):
        fails.append(f"artifact_submitted for spec and plan with different hashes (and at most a slice plan): {subs}")

    roles = {d["generation"]: d for d in store.dispatches_for(run_id)}
    creates = front.satisfied_effects(store, run_id, "sess-create")
    seen_obs = []
    for row in creates:
        snap = json.loads(row["external_object_id"])
        d = roles.get(row["generation"])
        # Ruling 14: the snapshot names the BUNDLE (`v2_author_<vendor>`), the
        # dispatch names the vendor; comparing the raw strings would refuse
        # every legitimate seat.
        if d and d["role"] in Role.SEATS and front.vendor_of(snap.get("agent_name")) != d["actor"]:
            fails.append(
                f"{row['idempotency_key']}: snapshot names {snap.get('agent_name')!r}, "
                f"dispatch actor {d['actor']!r}"
            )
        ob = row["intent"]["obligation"]
        if ob in seen_obs:
            fails.append(f"two sess-creates share an obligation: {ob}")
        seen_obs.append(ob)

    prompts = front.satisfied_effects(store, run_id, "sess-prompt")
    stops = front.satisfied_effects(store, run_id, "sess-stop")
    prompted = {r["intent"]["obligation"]["session"] for r in prompts}
    stopped = {r["intent"]["obligation"]["session"] for r in stops}
    for row in creates:
        sid = json.loads(row["external_object_id"])["id"]
        if sid not in prompted and sid not in stopped:
            fails.append(f"session {sid} was neither prompted nor stopped")

    # The generation a fact was recorded under, read from the paired
    # command_accepted fact (same causal_command_id): artifact_submitted,
    # model_question and model_ruling carry no `generation` of their own in
    # their payload, but every command_accepted fact does (kernel/commands.py
    # `_submit`), and it shares the output fact's causal_command_id because
    # both are written under the same command.
    accepted_generation = {
        c.causal_command_id: c.payload.get("generation")
        for c in store.facts_of_kind(run_id, EventKind.COMMAND_ACCEPTED)
    }
    output_facts = [
        f for f in store.facts_of_kind(
            run_id, EventKind.ARTIFACT_SUBMITTED, EventKind.AUTHOR_EMPTY,
            EventKind.MODEL_QUESTION, EventKind.MODEL_RULING, EventKind.REVIEW_VERDICT,
        )
        if f.kind != EventKind.REVIEW_VERDICT or f.payload.get("ruling") == "review_ruling"
    ]

    # Not every prompt is an awaited turn. The park prompt (cause: a `parked`
    # fact) tells the person what the run is waiting for, and the refusal
    # reply (cause: a `command_rejected` fact) answers a token the kernel
    # refused; neither is polled for a file and neither earns a `turn_ended`
    # (spec section 4). Holding them to the seat's rule failed the first
    # parked run that otherwise passed, on 2026-09-09.
    kind_of = {f.id: f.kind for f in store.facts_for(run_id)}
    unawaited = {EventKind.PARKED, EventKind.COMMAND_REJECTED}
    for row in prompts:
        if kind_of.get(row["intent"]["obligation"].get("cause")) in unawaited:
            continue
        te = front.turn_ended_for(store, run_id, row["idempotency_key"])
        if te is None:
            fails.append(f"prompt {row['idempotency_key']} has no turn_ended")
            continue
        if not front.stop_satisfied_for(store, run_id, te.id):
            fails.append(f"turn_ended {te.id} has no satisfied stop")
            continue
        # The stop's own satisfaction is a fact with a seq of its own:
        # performing an effect appends `effect_confirmed` (or, on a delayed
        # reconciliation, `effect_reconciled`), bound to the effect by
        # `causal_command_id == the effect's idempotency_key` -- neither
        # payload carries the key itself (kernel/effects.py `perform`).
        # Comparing seqs, not `at_us`: two facts' ORDER in the journal is
        # what the guard they came from actually enforced; wall-clock time
        # is not.
        stop_row = next(
            r for r in stops if r["intent"]["obligation"].get("cause") == te.id
        )
        stop_key = stop_row["idempotency_key"]
        confirmation = next(
            (f for f in store.facts_of_kind(run_id, EventKind.EFFECT_CONFIRMED, EventKind.EFFECT_RECONCILED)
             if f.causal_command_id == stop_key),
            None,
        )
        if confirmation is None:
            fails.append(
                f"stop {stop_key} has no effect_confirmed/effect_reconciled fact to "
                "bind the turn's output ordering to"
            )
            continue
        gen = row["generation"]
        for f in output_facts:
            if accepted_generation.get(f.causal_command_id) == gen and f.seq <= confirmation.seq:
                fails.append(
                    f"{f.kind} (seq {f.seq}) of generation {gen} is not newer than its "
                    f"turn's stop confirmation {stop_key} (seq {confirmation.seq})"
                )

    for v in store.facts_of_kind(run_id, EventKind.REVIEW_VERDICT):
        if v.payload.get("ruling") != "review_ruling" or v.payload.get("phase") not in front.FRONT_PHASES:
            continue
        b = front.brief_for(store, run_id, v.payload["generation"])
        if b is None or b.payload["epoch"] != v.payload["epoch"]:
            fails.append(f"review_ruling gen {v.payload['generation']} has no brief in its epoch")
            continue
        bp = b.payload
        data = store.read_blob(bp["brief_hash"])
        pol = from_payload(store.newest_fact(run_id, EventKind.POLICY_FROZEN).payload["policy"])
        rendered = brief.render(
            phase=bp["phase"], artefact=store.read_blob(bp["artifact_hash"]),
            bundle=store.read_blob(bp["context_bundle_hash"]),
            spec=None if bp["spec_hash"] is None else store.read_blob(bp["spec_hash"]),
            policy=pol, base_sha=bp["base_sha"], template=bp["brief_template"],
            prior_findings=(None if bp.get("prior_findings_hash") is None
                            else store.read_blob(bp["prior_findings_hash"])),
        )
        if data != rendered:
            fails.append(f"brief {bp['brief_hash'][:12]} is not render() over its named objects")
        if v.payload["artifact_hash"] != bp["artifact_hash"]:
            fails.append("review_ruling artifact_hash differs from its brief's")
        fh = v.payload.get("findings_hash")
        if fh and brief.hash8(bp["artifact_hash"]) not in store.read_blob(fh).decode("utf-8", "replace"):
            fails.append("the reviewer's verdict line does not name the brief's hash8")

    return fails


def assert_sessions(store, run_id: str, *, server: str = "", fetch=_fetch) -> list[str]:
    """The server-side half of the §8 proof. Every satisfied `sess-create`
    of the run is checked against what the server holds now, not against
    anything the journal alone could tell."""
    fails: list[str] = []
    try:
        listed = list_sessions(server, fetch=fetch)
    except Exception as exc:  # noqa: BLE001 -- a bad listing is a finding, not a crash
        fails.append(f"the {server}/v1/sessions listing could not be read: {exc}")
        listed = set()

    prompt_items = {p.payload["item_id"] for p in store.facts_of_kind(run_id, EventKind.PROMPT_ITEM)}
    cursors = {f.payload.get("cursor_item_id")
               for f in store.facts_of_kind(run_id, EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING,
                                            EventKind.HUMAN_DIRECTION, EventKind.PARKED,
                                            EventKind.HUMAN_ITEM_DISMISSED)} - {None}
    prompt_hashes = {r["intent"]["body"]["artifact"]
                     for r in front.satisfied_effects(store, run_id, "sess-prompt")}
    briefs = {b.payload["generation"]: b.payload["brief_hash"]
              for b in store.facts_of_kind(run_id, EventKind.REVIEW_BRIEF_ISSUED)}
    roles = {d["generation"]: d["role"] for d in store.dispatches_for(run_id)}
    prompts_by_session: dict[str, list[dict]] = {}
    for r in front.satisfied_effects(store, run_id, "sess-prompt"):
        prompts_by_session.setdefault(r["intent"]["obligation"]["session"], []).append(r)
    stop_counts: dict[str, int] = {}
    for r in front.satisfied_effects(store, run_id, "sess-stop"):
        stop_sid = r["intent"]["obligation"].get("session")
        stop_counts[stop_sid] = stop_counts.get(stop_sid, 0) + 1

    for row in front.satisfied_effects(store, run_id, "sess-create"):
        snap = json.loads(row["external_object_id"])
        sid = snap["id"]

        if sid not in listed:
            fails.append(f"session {sid} is not in the {server}/v1/sessions listing")

        try:
            live = json.loads(fetch(f"{server}/v1/sessions/{sid}"))
        except Exception as exc:  # noqa: BLE001
            fails.append(f"session {sid} could not be read: {exc}")
            continue
        # A switch-agent (§3 *Obligations*) deletes the agent row and mints a
        # fresh id, so this equality is the observation that none happened.
        if live.get("agent_id") != snap["agent_id"]:
            fails.append(
                f"session {sid}: agent_id {live.get('agent_id')!r} != "
                f"its create's snapshot {snap['agent_id']!r}"
            )

        try:
            items = list_items(server, sid, fetch=fetch)
        except Exception as exc:  # noqa: BLE001
            fails.append(f"session {sid}: items could not be read: {exc}")
            continue
        # The server writes one user-role item itself: the "[System:
        # interrupted]" notice a cancelled turn leaves, response_id `cancel_...`
        # where every real message is `turn_...`. It is not a touch by anyone,
        # and the coordinator already ignores it (human._is_cancellation_notice);
        # the proof must use the SAME rule, or every session the coordinator
        # stopped mid-work reads as an unrecorded prompt -- four of them on the
        # first run that converged unattended (2026-09-08).
        # A person's message the coordinator READ is a touch the run's mode
        # already judged (assert_journal), not an unrecorded prompt. Every
        # human fact names the cursor it was read to; everything in this
        # listing at or before such a cursor was read. Under mode "zero" no
        # human fact exists, so nothing is admitted and the check is as
        # strict as before.
        ids = [it["id"] for it in items]
        read_to = max((ids.index(c) for c in cursors if c in ids), default=-1)
        users = [it for it in items if it["role"] == "user" and not _is_cancellation_notice(it)]
        for it in users:
            if ids.index(it["id"]) <= read_to:
                continue
            if it["id"] not in prompt_items and content_hash(it["text"].encode()) not in prompt_hashes:
                fails.append(
                    f"session {sid}: user item {it['id']} is no prompt_item "
                    "and matches no prompt effect"
                )

        if roles.get(row["generation"]) == Role.REVIEWER:
            ps = prompts_by_session.get(sid, [])
            if ps:
                gen = ps[0]["generation"]
                if not users or content_hash(users[0]["text"].encode()) != briefs.get(gen):
                    fails.append(
                        f"reviewer session {sid}: first user item is not the brief of generation {gen}"
                    )
            else:
                stop_count = stop_counts.get(sid, 0)
                if users or stop_count != 1:
                    fails.append(
                        f"reviewer session {sid}: unprompted, yet has items or "
                        f"{stop_count} satisfied stops (want exactly 1)"
                    )

    return fails


def assert_slices(store, run_id: str, *, repo: str, gh_json) -> list[str]:
    """The four assertions of shaping spec §6 over a parent. `gh_json` is
    the sweep's reader (coordinator.sweep.gh_json) or a fake; it is not
    called for a run that filed nothing (assertion 4 still runs).

    A failed read is a proof failure, not a crash: each `gh_json` call is
    wrapped so a `ReadFailed` appends a failure line naming the child (or
    the parent) and what could not be read, and the function carries on
    with the remaining children and assertions. A read that fails cannot
    satisfy its own assertion, so the failure line stands in for it -- no
    further check is attempted for that same read."""
    import collections
    from coordinator.sweep import ReadFailed
    from kernel import slices
    fails: list[str] = []
    n = front.epoch(store, run_id)
    plan = front.accepted_plan(store, run_id, n)
    h = front.accepted_slices_hash(store, run_id, n)
    filed = front.slices_filed(store, run_id, n)
    parent = front.issue_number(store, run_id)
    numbers = {k: f.payload["issue"] for k, f in filed.items()}
    if plan is not None and filed:
        # 1. The children are the plan, body and all -- and exactly one delivered create per slice.
        if sorted(filed) != [s.number for s in plan.slices]:
            fails.append(f"slice_filed {sorted(filed)} does not match the plan's slices, by number")
        creates = front.satisfied_effects(store, run_id, "slice_issue")
        per = collections.Counter(r["intent"]["obligation"].get("slice") for r in creates)
        for s in plan.slices:
            if per.get(s.number, 0) != 1:
                fails.append(f"slice {s.number}: {per.get(s.number, 0)} delivered creates, want exactly one")
        for k in per:
            if k not in filed:
                fails.append(f"a delivered create for slice {k} has no slice_filed")
        for s in plan.slices:
            if s.number not in filed:
                continue
            try:
                live = gh_json(["gh", "issue", "view", str(numbers[s.number]), "--repo", repo, "--json", "title,body"])
            except ReadFailed as exc:
                fails.append(f"slice {s.number} (#{numbers[s.number]}): could not read title,body: {exc}")
                continue
            title, body = slices.render_child(s, parent, h[:8], {d: numbers[d] for d in s.depends_on})
            if live.get("title") != title or (live.get("body") or "").rstrip("\n") != body.rstrip("\n"):
                fails.append(f"child #{numbers[s.number]}: title or body is not render_child over slice {s.number}")
        # 2. The dependencies are the links.
        for s in plan.slices:
            if s.number not in filed:
                continue
            try:
                links = gh_json(["gh", "api", f"repos/{repo}/issues/{numbers[s.number]}/dependencies/blocked_by"])
            except ReadFailed as exc:
                fails.append(f"slice {s.number} (#{numbers[s.number]}): could not read blocked_by: {exc}")
                continue
            got = sorted(int(x["number"]) for x in links)
            want = sorted(numbers[d] for d in s.depends_on)
            if got != want:
                fails.append(f"child #{numbers[s.number]}: blocked-by links {got} are not the plan's {want} (a missing or extra link)")
        # 3. The umbrella names the children.
        try:
            comments = gh_json(["gh", "issue", "view", str(parent), "--repo", repo, "--json", "comments"]).get("comments") or []
        except ReadFailed as exc:
            fails.append(f"parent #{parent}: could not read comments: {exc}")
            comments = None
        if comments is not None:
            ums = [c for c in comments if (c.get("body") or "").startswith(f"bircher: sliced {h[:8]}")]
            if len(ums) != 1:
                fails.append(f"{len(ums)} umbrella comments for plan {h[:8]}, want exactly one")
            elif ums[0]["body"].rstrip("\n") != slices.umbrella_body(h[:8], plan, numbers).rstrip("\n"):
                fails.append("the umbrella comment does not list exactly the filed children")
    # 4. One decision per epoch, and every filing in the last.
    for f in store.facts_of_kind(run_id, EventKind.SLICE_FILED):
        if f.payload.get("epoch") != n:
            fails.append(f"slice_filed in epoch {f.payload.get('epoch')}, outside the final epoch {n}")
    for e in range(n + 1):
        ruling = front.shape_ruling(store, run_id, e)
        if ruling is None:
            continue
        subs = front.submissions(store, run_id, "slices", e)
        if subs and ruling.seq < subs[-1].seq:
            fails.append(f"epoch {e}: a slice plan was submitted after the shape ruling (one decision per epoch)")
        if front.slices_filed(store, run_id, e):
            fails.append(f"epoch {e}: a shape ruling beside a filing (one decision per epoch)")
    if bool(front.shape_ruling(store, run_id, n)) == bool(filed):
        fails.append(f"final epoch {n}: exactly one decision, a slice_filed or a shape ruling")
    return fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Prove the §8 done criterion over a merged front-half run.",
    )
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--expect-human", action="store_true",
                    help="admit any human fact (E2); default admits none (E4)")
    ap.add_argument("--expect-approval", action="store_true",
                    help="admit exactly the gate's approval (E3); default admits none (E4)")
    ap.add_argument("--issues", default=None,
                    help="the pre-registered issue list; the run's issue must be in it")
    ap.add_argument("--repo", default="")
    ap.add_argument("--children", action="store_true",
                    help="prove the parent's slicing and each child's run")
    a = ap.parse_args(argv)

    store = Store.open(a.db)
    mode = "human" if a.expect_human else "approval" if a.expect_approval else "zero"
    fails = assert_journal(store, a.run_id, mode=mode) + assert_sessions(store, a.run_id, server=a.server)

    if a.children:
        from coordinator.sweep import ReadFailed, gh_json
        if not a.repo:
            print("--children needs --repo", file=sys.stderr)
            return 2
        fails += assert_slices(store, a.run_id, repo=a.repo, gh_json=gh_json)
        for f in store.facts_of_kind(a.run_id, EventKind.SLICE_FILED):
            kids = [r for r in store.all_run_ids() if r.startswith(f"i{f.payload['issue']}-")]
            if not kids:
                fails.append(f"child #{f.payload['issue']} has no run yet")
                continue
            # Nothing here reads gh today, but a read failure while proving a
            # child must become a failure line, not an exception escaping
            # main -- the loop continues over the remaining children either way.
            try:
                child_fails = assert_journal(store, kids[-1], mode=mode) + assert_sessions(store, kids[-1], server=a.server)
            except ReadFailed as exc:
                fails.append(f"child #{f.payload['issue']} ({kids[-1]}): could not read: {exc}")
                continue
            fails += [f"child #{f.payload['issue']} ({kids[-1]}): {x}" for x in child_fails]

    if a.issues:
        # The body-content half of this rule (no file, function or acceptance
        # test named) is the pre-registration's own discipline, applied when
        # the list is written (Task 24) -- there is no body left to read here.
        enq = store.newest_fact(a.run_id, EventKind.RUN_ENQUEUED)
        snap = json.loads(store.read_blob(enq.payload["bundle_hash"]))
        allowed = {int(x) for x in open(a.issues).read().split()}
        if snap["number"] not in allowed:
            fails.append(f"issue #{snap['number']} is not in the pre-registered list")

    for f in fails:
        print(f, file=sys.stderr)
    print("PROOF: " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
