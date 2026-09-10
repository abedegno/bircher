"""Filing the children (shaping spec §3 *Filing the children*): four passes
over the accepted plan, each complete before the next, idempotent per
obligation. Every step checks its own fact or effect and performs only what
is missing, so a pass that resumes after any crash repairs exactly the
remainder; the kernel refuses anything performed out of order
(kernel/filing.py), so the order here is the behaviour and the kernel's
refusal is the guard."""
from __future__ import annotations

from coordinator import seat
from coordinator.effects import perform_effect
from kernel import filing as kfiling
from kernel import front, slices
from kernel.artifacts import put_artifact
from kernel.effect_class import EffectClass


def file_owed(ctx) -> list[str]:
    store, run_id = ctx.store, ctx.run_id
    if ctx.state() != "sliced":
        return []
    n = ctx.epoch()
    if front.filing_complete(store, run_id, n) is not None:
        return []
    plan = front.accepted_plan(store, run_id, n)
    h = front.accepted_slices_hash(store, run_id, n)
    parent, repo, gen = front.issue_number(store, run_id), ctx.repo, ctx.generation
    labels = kfiling.expected_labels(store, run_id)
    base = {"run": run_id, "epoch": n}
    done: list[str] = []

    def owed(ob):
        return front.satisfied_obligation(store, run_id, ob) is None

    # Pass 1: the creates and their facts, in dependency order.
    for k in slices.topo_order(plan):
        ob = dict(base, kind="slice_issue", slice=k, parent=parent, plan_hash=h)
        if owed(ob):
            title, body = slices.render_child(plan.by_number()[k], parent, h[:8], kfiling.filed_numbers(store, run_id, n))
            key = f"slice:{run_id}:{k}:{gen}"
            perform_effect(EffectClass.ISSUE_CREATE, key, kfiling.create_argv(repo, title, labels), timeout=60,
                           env=ctx.effect_env(), obligation=ob, body={"artifact": put_artifact(store, body.encode("utf-8"))})
            done.append(key)
        if k not in front.slices_filed(store, run_id, n):
            # The key of the satisfied create -- whichever generation minted it.
            row = front.satisfied_obligation(store, run_id, ob)
            seat.command(ctx, "record_slice_filed", {"slice": k, "effect_key": row["idempotency_key"]})
    numbers, ids = kfiling.filed_numbers(store, run_id, n), kfiling.filed_ids(store, run_id, n)
    # Pass 2: the links.
    for k, b in plan.edges():
        ob = dict(base, kind="slice_dependency", plan_hash=h, slice=k, blocker=b)
        if owed(ob):
            key = f"link:{run_id}:{k}:{b}:{gen}"
            perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.link_argv(repo, numbers[k], ids[b]), timeout=60,
                           env=ctx.effect_env(), obligation=ob)
            done.append(key)
    # Pass 3: the queue labels -- only now, so a queued child has every link.
    for s in plan.slices:
        ob = dict(base, kind="slice_queue", plan_hash=h, slice=s.number)
        if owed(ob):
            key = f"queue:{run_id}:{s.number}:{gen}"
            perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.queue_argv(repo, numbers[s.number]), timeout=60,
                           env=ctx.effect_env(), obligation=ob)
            done.append(key)
    # Pass 4: the umbrella and its label.
    ob = dict(base, kind="umbrella", plan_hash=h)
    if owed(ob):
        key = f"umbrella:{run_id}:{gen}"
        perform_effect(EffectClass.COMMENT, key, kfiling.comment_argv(repo, parent, slices.umbrella_body(h[:8], plan, numbers)),
                       timeout=60, env=ctx.effect_env(), obligation=ob)
        done.append(key)
    ob = dict(base, kind="umbrella_label", plan_hash=h)
    if owed(ob):
        key = f"umbrella-label:{run_id}:{gen}"
        perform_effect(EffectClass.ISSUE_OR_LABEL, key, kfiling.umbrella_label_argv(repo, parent), timeout=60,
                       env=ctx.effect_env(), obligation=ob)
        done.append(key)
    # Last, once: the kernel's statement that nothing about filing is owed.
    if front.filing_complete(store, run_id, n) is None:
        seat.command(ctx, "record_filing_complete", {})
    return done
