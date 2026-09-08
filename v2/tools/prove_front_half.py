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

    if EventKind.MODEL_RULING not in kinds:
        fails.append("no model_ruling: the author did not rule on a question")

    subs = {f.payload["phase"]: f.payload["hash"]
            for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)}
    if set(subs) != {"spec", "plan"} or subs["spec"] == subs["plan"]:
        fails.append(f"artifact_submitted for spec and plan with different hashes: {subs}")

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

    for row in prompts:
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
        users = [it for it in items if it["role"] == "user"]
        for it in users:
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
    a = ap.parse_args(argv)

    store = Store.open(a.db)
    mode = "human" if a.expect_human else "approval" if a.expect_approval else "zero"
    fails = assert_journal(store, a.run_id, mode=mode) + assert_sessions(store, a.run_id, server=a.server)

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
