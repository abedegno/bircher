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
import collections
import functools
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


def _parse_human_ruling_payload_keys(source: str) -> tuple[dict[str, set[str]], list[int]]:
    """The AST walk, pure over a source string: every `human_ruling` fact's
    ruling word mapped to the literal keys ITS payload dict declares, and
    the line number of every `human_ruling`-kind `append_fact` call whose
    `payload` this walk could NOT read at all -- a variable, a `**spread`,
    or a dict built above the call, rather than a literal (round 1, L3). The
    first version of this walk skipped such a call in total silence, which
    is the same shape of miss the controller amendment exists to close: the
    one branch built to catch an undocumented ruling word never fires for a
    payload it cannot see, so a fifth word written this way would still slip
    through, just one step further back than `approve_one_piece` did."""
    import ast
    out: dict[str, set[str]] = {}
    opaque: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append_fact"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        kind = kw.get("kind")
        if not (isinstance(kind, ast.Attribute) and kind.attr == "HUMAN_RULING"):
            continue
        payload = kw.get("payload")
        if not isinstance(payload, ast.Dict):
            opaque.append(getattr(node, "lineno", -1))
            continue
        keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
        word = next((v.value for k, v in zip(payload.keys, payload.values)
                     if isinstance(k, ast.Constant) and k.value == "ruling" and isinstance(v, ast.Constant)),
                    None)
        if word is not None:
            out[word] = keys
    return out, opaque


@functools.lru_cache(maxsize=1)
def _real_human_ruling_payload_keys() -> tuple[dict[str, set[str]], list[int]]:
    """The real module's read, cached (round 1, nit): `assert_journal` calls
    this once per run and once per child under `--children`, and
    `kernel/commands.py`'s source does not change within one process."""
    import inspect
    from kernel import commands as _commands
    return _parse_human_ruling_payload_keys(inspect.getsource(_commands))


def _human_ruling_payload_keys(source: str | None = None) -> tuple[dict[str, set[str]], list[int]]:
    """Every `human_ruling` fact `kernel/commands.py` can write, as the
    ruling word mapped to the literal keys ITS payload dict declares, paired
    with the line numbers of any `human_ruling` write this walk could not
    read at all -- walked from the SOURCE (an AST, not a run), so a fifth
    ruling word is discovered the day it is added to the kernel, not the day
    someone remembers to update a list here (Task 10, the controller
    amendment).

    *source* is for a test to substitute a snippet standing in for a
    regression; `assert_journal` always calls this with none, reading the
    real module (cached)."""
    if source is not None:
        return _parse_human_ruling_payload_keys(source)
    return _real_human_ruling_payload_keys()


#: Task 10 controller amendment. Task 4 shipped `approve_one_piece`'s
#: `human_ruling` fact silently missing `cursor_item_id` -- the key
#: `assert_sessions` reads from EVERY human_ruling fact, generically, as its
#: read-watermark (this file, `assert_sessions`'s `cursors` set). Nothing
#: went red; the proof would simply have read a shorter watermark on the
#: first live run that recorded one. The fix is in `commands.py`; this is
#: the regression guard for the next one -- the keys the shaping spec's §2
#: Facts table lists for `approve_one_piece`, new in revision 16. The other
#: three words are not new, and the table only says `approve` is "as today"
#: without enumerating it, and does not carry `grant_round` or
#: `request_revision` at all -- but `docs/design/provenance-table.md`'s
#: "Caller-presented" and "Asserted" tables document `cursor_item_id` for
#: `approve_artifact`, `grant_round` and the human's own `record_review`,
#: and `artifact_hash` for `record_review` (round 1, L2), so those come from
#: there rather than from `commands.py` itself; only `grant_round`'s
#: `park_seq` has no design source anywhere and is read from the code. The
#: WORDS this is checked against are never typed out a second time:
#: `_human_ruling_payload_keys` walks them from `commands.py` itself, so a
#: fifth word with no entry below fails on its own account rather than
#: silently never being checked.
HUMAN_RULING_KEYS = {
    "approve": {"ruling", "phase", "epoch", "artifact_hash", "cursor_item_id"},
    "grant_round": {"ruling", "phase", "epoch", "park_seq", "cursor_item_id"},
    "request_revision": {"ruling", "phase", "epoch", "artifact_hash", "cursor_item_id"},
    "approve_one_piece": {"ruling", "phase", "epoch", "visit", "cursor_item_id"},
}


class _BadPayload(Exception):
    """Round 4: the crash class. Twenty payload keys deep and three rounds
    running, the same shape kept recurring -- a `.payload[key]` subscript on
    a fact the mutation forged without that key, or with the wrong type for
    it, killed the whole tool before it printed a single failure line
    (`assert_journal` runs first in `main`, uncaught). Round 3 closed a few
    by instance (round 3, "one medium crash... do it by sweep, not by
    instance" -- and the sweep still missed a dozen more the very next
    review). This is the class fix: ONE exception, raised by `_need` and
    nothing else, so a missing or wrong-typed key becomes a failure line
    naming the fact and the key -- never an exception escaping a check."""

    def __init__(self, fact, key: str, detail: str):
        self.line = f"{fact.kind} (seq {fact.seq}): payload {detail}"
        super().__init__(self.line)


def _need(fact, key: str, *, type=None):
    """The one safe read. *fact*.payload[*key*], or a raised `_BadPayload`
    naming the fact and the key: absent, or (when *type* is given) not an
    instance of it. Every direct payload subscript below a fact's kind is
    established as read-worthy goes through this -- not `.get(key, default)`,
    which would quietly substitute a value the check never asked for and
    make a missing key look like a legitimate one (the same silent-admission
    shape the controller amendment and the grounding checks exist to close,
    one level down at the key itself)."""
    p = fact.payload
    if key not in p:
        raise _BadPayload(fact, key, f"has no {key!r} key")
    v = p[key]
    if type is not None and not isinstance(v, type):
        raise _BadPayload(fact, key, f"{key!r} is {v!r}, not {type}")
    return v


def assert_journal(store, run_id: str, *, mode: str = "zero") -> list[str]:
    """The journal-only half of the §8 proof. Every check reads facts and
    satisfied effects already in the store; nothing here fetches a session."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    fails: list[str] = []
    facts = store.facts_for(run_id)
    kinds = [f.kind for f in facts]
    human = [f for f in facts if f.kind in HUMAN]
    n = front.epoch(store, run_id)

    if mode == "zero" and human:
        fails.append(
            f"human facts present: "
            f"{[(f.kind, f.payload.get('ruling') or f.payload.get('reason')) for f in human]}"
        )
    if mode == "approval":
        # Revision 16: `approve_one_piece` is the person's resolution of a
        # shape disagreement -- admitted beside the ordinary `approve` -- and
        # its own park, `reason: disagreement`, beside the gate's. A
        # DIRECTION stays OUT of this set even so: it is legal at `shaping`
        # as the other way to resolve a disagreement, but a run resolved by
        # a direction proves only under `--expect-human`, as every directed
        # run does (shaping spec §6).
        extra = [f for f in human if not (
            (f.kind == EventKind.HUMAN_RULING and f.payload.get("ruling") in ("approve", "approve_one_piece"))
            or (f.kind == EventKind.PARKED and f.payload.get("reason") in ("gate", "disagreement"))
        )]
        if extra:
            fails.append(f"human facts beyond the approval: {[f.kind for f in extra]}")
        # §6: "exactly one human_ruling {approve} at it ... beside the spec
        # gate's" -- one approval per GATE reached, not a bound on the run
        # (a fully gated one-piece run legitimately carries one at the spec
        # gate and another at the plan gate). Revision 16 widened the
        # admitted ruling words without adding a count (round 1, L4);
        # grouped by the (phase, epoch) the ruling names, since that is what
        # "a gate" means to a fact that carries no gate id of its own.
        approvals = [f for f in human if f.kind == EventKind.HUMAN_RULING
                     and f.payload.get("ruling") in ("approve", "approve_one_piece")]
        # Round 4, D2: grouped by the STAMPED epoch, a duplicate approval
        # hides behind a second copy stamped a wrong-but-in-range epoch --
        # exactly the defect `_gate_exists` below now refuses to admit one
        # key over. `front.epoch_of` is the same derivation used everywhere
        # else in this file; two approvals of the same real gate group into
        # the same bucket regardless of what either one's stamp claims.
        by_gate = collections.Counter((f.payload.get("phase"), front.epoch_of(store, run_id, f.seq))
                                       for f in approvals)
        dup_gates = {k: v for k, v in by_gate.items() if v > 1}
        if dup_gates:
            fails.append(f"more than one approval at the same gate: {dup_gates}")

        # Round 3, C4: admission by RULING WORD alone says nothing about
        # whether the gate it names ever existed. §6 admits "every gate's
        # approve" -- a gate, not any fact spelling the word. `approve`
        # needs a real submission at the (phase, epoch) it names; a real
        # gate can only be approved after something was actually submitted
        # to it. `approve_one_piece` and a disagreement park need a real
        # dispute at their epoch, WITH a disputed ruling for it to resolve
        # -- a hand-back with no ruling after it yet is not a disagreement
        # anyone could have approved past. A "gate" park needs the same
        # grounding as `approve`: a real submission is what it is waiting
        # on. `front.dispute` is the one definition, as everywhere else in
        # this file.
        #
        # Round 4, D2: the epoch this checks against is `front.epoch_of`'s
        # derivation, not the stamp bounded to a range -- the same fix
        # `assert_slices` and `assert_dispute` already apply to every other
        # epoch-bearing fact in this file (round 3, C1/C2). A stamp that
        # disagrees with the journal's own count no longer names a real
        # gate, in range or not.
        def _gate_exists(f) -> bool:
            p = f.payload
            ep = front.epoch_of(store, run_id, f.seq)
            if p.get("epoch") != ep:
                return False
            if f.kind == EventKind.HUMAN_RULING and p.get("ruling") == "approve":
                phase = p.get("phase")
                return phase in front.FRONT_PHASES and bool(front.submissions(store, run_id, phase, ep))
            if f.kind == EventKind.HUMAN_RULING and p.get("ruling") == "approve_one_piece":
                d = front.dispute(store, run_id, ep)
                return d is not None and d.disputed is not None
            if f.kind == EventKind.PARKED and p.get("reason") == "disagreement":
                d = front.dispute(store, run_id, ep)
                return d is not None
            if f.kind == EventKind.PARKED and p.get("reason") == "gate":
                phase = p.get("phase")
                return phase in front.FRONT_PHASES and bool(front.submissions(store, run_id, phase, ep))
            # Round 4: every combination `extra` admits reaches one of the
            # branches above (that is what "admitted" means); the four
            # branches now cover every fact `human` can hold that is not in
            # `extra`. Denying by default, not admitting -- the previous
            # `return True` here was never exercised by any test in three
            # rounds of adversarial review, because nothing in `human`
            # could reach it without also being in `extra`. A future ruling
            # word or park reason added to the admitted set without a
            # branch here now fails closed instead of being trusted by
            # default.
            return False

        # Round 4, inert assertion #4: with `_gate_exists` now denying by
        # default, an `extra` fact (already reported by "human facts beyond
        # the approval" above) would ALSO fail `_gate_exists` -- none of its
        # four branches match a fact `extra` admitted -- and be reported a
        # second time here without this filter. Kept, not deleted: it is
        # what keeps the two checks from duplicating the same fact's report.
        ungrounded = [f for f in human if f not in extra and not _gate_exists(f)]
        if ungrounded:
            fails.append(
                f"human facts admitted by ruling word alone, at a gate this run never reached: "
                f"{[(f.kind, f.payload) for f in ungrounded]}"
            )
    # mode == "human" admits every human fact; everything below still runs.

    # Controller amendment (Task 10): commands.py's OWN human_ruling payload
    # literals, checked against the spec's Facts table -- not this run's
    # journal, which can only ever hold whatever the code just wrote, bug or
    # no bug (see HUMAN_RULING_KEYS's comment for why a per-fact check would
    # never have caught the bug it exists for). Runs for every proof, not
    # just a run that happens to hold a human_ruling fact: a fifth ruling
    # word with a broken payload is a defect whether or not this particular
    # run ever recorded it.
    ruling_keys, opaque_lines = _human_ruling_payload_keys()
    if opaque_lines:
        fails.append(
            f"commands.py writes a human_ruling fact whose payload this proof cannot read "
            f"statically (line(s) {opaque_lines}): the controller amendment's check does not cover it"
        )
    for word, keys in ruling_keys.items():
        want = HUMAN_RULING_KEYS.get(word)
        if want is None:
            fails.append(
                f"human_ruling {word!r}: commands.py writes it, but this proof carries no "
                "required-key entry for it (Task 10 controller amendment)"
            )
        elif not want <= keys:
            fails.append(
                f"human_ruling {word!r}: commands.py's payload carries {sorted(keys)}, missing "
                f"{sorted(want - keys)} the spec's Facts table lists for it"
            )

    if EventKind.EFFECT_RECONCILED in kinds or store.reconciliation_evidence(run_id) is not None:
        fails.append("the run was reconciled or halted")

    from kernel.slices import SHAPE_QUESTION
    sliced_parent = bool(front.slices_filed(store, run_id, n))
    # A ruling on a question the AUTHOR raised: the shape ruling every
    # one-piece run carries would satisfy this vacuously (shaping spec §6).
    # A sliced parent is exempt -- its decision is slice_filed.
    #
    # Round 4, D3: a ruling naming any non-shape `question_id` used to admit
    # this on the ruling WORD alone -- the same admission-by-word-alone
    # shape `_gate_exists` (round 3, C4) and the controller amendment
    # (Task 10) both close elsewhere in this file, left open here. A
    # `model_ruling` whose `question_id` names no `model_question` this run
    # ever asked answers nothing; grounded against the real questions, the
    # same way `_gate_exists` grounds a ruling word against a real gate.
    asked = {q.payload.get("question_id") for q in store.facts_of_kind(run_id, EventKind.MODEL_QUESTION)}
    if not sliced_parent and not any(
            f.kind == EventKind.MODEL_RULING and f.payload.get("question_id") != SHAPE_QUESTION
            and f.payload.get("question_id") in asked
            for f in facts):
        fails.append("no model_ruling: the author did not rule on a question")

    # The FINAL epoch only (shaping spec §6, round 12): a run that submitted
    # a spec before its issue changed and was then sliced is a valid history.
    subs = {f.payload.get("phase"): f.payload.get("hash")
            for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED) if f.payload.get("epoch") == n}
    if sliced_parent:
        hb = next((f for f in front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, n)), None)
        # Revision 16: a hand-back from a REVISION turn means the epoch
        # legitimately holds a spec submission -- before the hand-back,
        # never after it (the spec author's second-turn choice, not a
        # resubmission race). A one-piece run resolved by approve_one_piece
        # keeps the one-piece rule below, because it is not a sliced parent.
        allowed = {"slices"} if hb is None else {"slices", "spec"}
        # No hb: "late" is undefined and moot, since `allowed` already
        # excludes "spec" outright and any spec submission trips
        # `set(subs) - allowed` on its own below (round 1's `and hb is not
        # None` conjunct, and round 2's `hb_seq` sentinel that replaced it,
        # both read as load-bearing when neither ever changed the outcome --
        # this `[]` says the same thing without a value to argue is dead).
        late_spec = [] if hb is None else [
            f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
            if f.payload.get("epoch") == n and f.payload.get("phase") == "spec" and f.seq > hb.seq
        ]
        if set(subs) - allowed or "slices" not in subs or late_spec \
                or subs["slices"] != front.accepted_slices_hash(store, run_id, n):
            fails.append(f"artifact_submitted: a sliced parent's final epoch submits the accepted slice plan "
                         f"and at most a spec preceding its hand-back: {subs}")
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
            EventKind.RESHAPE_REQUESTED,  # revision 16: the hand-back is a turn's output too
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
        # Round 4: every read below goes through `_need` and is caught here,
        # once, per verdict -- a malformed brief or verdict fails this one
        # verdict's checks with a named line and the loop moves on to the
        # next, rather than the whole tool dying before `assert_sessions`
        # and `assert_dispute` ever run (the crash class; this loop alone
        # held eight of the payload keys the round-4 review named).
        try:
            gen = _need(v, "generation")
            b = front.brief_for(store, run_id, gen)
            if b is None or _need(b, "epoch") != _need(v, "epoch"):
                fails.append(f"review_ruling gen {gen} has no brief in its epoch")
                continue
            bp = b.payload
            brief_hash = _need(b, "brief_hash")
            data = store.read_blob(brief_hash)
            policy_fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
            if policy_fact is None:
                fails.append(f"review_ruling gen {gen}: no policy_frozen fact to render its brief against")
                continue
            spec_hash = _need(b, "spec_hash")
            artifact_hash = _need(b, "artifact_hash")
            pol = from_payload(_need(policy_fact, "policy"))
            rendered = brief.render(
                phase=_need(b, "phase"), artefact=store.read_blob(artifact_hash),
                bundle=store.read_blob(_need(b, "context_bundle_hash")),
                spec=None if spec_hash is None else store.read_blob(spec_hash),
                policy=pol, base_sha=_need(b, "base_sha"), template=_need(b, "brief_template"),
                prior_findings=(None if bp.get("prior_findings_hash") is None
                                else store.read_blob(bp["prior_findings_hash"])),
            )
            if data != rendered:
                fails.append(f"brief {brief_hash[:12]} is not render() over its named objects")
            if _need(v, "artifact_hash") != artifact_hash:
                fails.append("review_ruling artifact_hash differs from its brief's")
            fh = v.payload.get("findings_hash")
            if fh and brief.hash8(artifact_hash) not in store.read_blob(fh).decode("utf-8", "replace"):
                fails.append("the reviewer's verdict line does not name the brief's hash8")
        except _BadPayload as exc:
            fails.append(exc.line)
            continue

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

    # Round 4: both comprehensions below used to subscript the payload
    # directly (`prompt_item`'s `item_id`, `review_brief_issued`'s
    # `generation`/`brief_hash`) -- a crash before a single failure line
    # printed. Built as loops instead, each malformed fact caught and named
    # by `_need`/`_BadPayload` rather than dropped by a silent `.get()`
    # default, which would have made a missing key look like a legitimate
    # unset one.
    prompt_items = set()
    for p in store.facts_of_kind(run_id, EventKind.PROMPT_ITEM):
        try:
            prompt_items.add(_need(p, "item_id"))
        except _BadPayload as exc:
            fails.append(exc.line)
    cursors = {f.payload.get("cursor_item_id")
               for f in store.facts_of_kind(run_id, EventKind.HUMAN_ANSWER, EventKind.HUMAN_RULING,
                                            EventKind.HUMAN_DIRECTION, EventKind.PARKED,
                                            EventKind.HUMAN_ITEM_DISMISSED)} - {None}
    prompt_hashes = {r["intent"]["body"]["artifact"]
                     for r in front.satisfied_effects(store, run_id, "sess-prompt")}
    briefs = {}
    for b in store.facts_of_kind(run_id, EventKind.REVIEW_BRIEF_ISSUED):
        try:
            briefs[_need(b, "generation")] = _need(b, "brief_hash")
        except _BadPayload as exc:
            fails.append(exc.line)
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
    from coordinator.sweep import ReadFailed
    from kernel import slices
    fails: list[str] = []
    n = front.epoch(store, run_id)
    plan = front.accepted_plan(store, run_id, n)
    h = front.accepted_slices_hash(store, run_id, n)
    filed = front.slices_filed(store, run_id, n)
    # Round 4: a bad bundle_hash (missing, or pointing nowhere real) used to
    # crash the whole function before assertion 4 -- which needs no parent
    # number at all -- ever got to run.
    try:
        parent = front.issue_number(store, run_id)
    except Exception as exc:  # noqa: BLE001 -- a bad bundle is a finding, not a crash
        fails.append(f"the run's own issue number could not be read: {exc}")
        parent = None
    # Round 4: built as a loop, not a comprehension over `f.payload["issue"]`
    # -- a slice_filed fact missing it names itself and is left out, rather
    # than crashing before assertions 1-3 (or clause (a) below) ever run.
    numbers = {}
    for k, f in filed.items():
        try:
            numbers[k] = _need(f, "issue")
        except _BadPayload as exc:
            fails.append(exc.line)
    if plan is not None and filed:
        # Round 4, D1: `front.slices_filed` collapses every filing of a
        # slice to its LAST one -- a dict comprehension keyed on the slice
        # number. A phantom filing sandwiched between a real one and its own
        # restatement is invisible to that collapse, and so to every check
        # below that reads `filed`. `front.slice_filings` is the same facts,
        # uncollapsed; this is the one place §6's "exactly one" gets to see
        # all of them, not just the newest.
        #
        # Round 5, RC-1: this check, the plan-to-number match right below,
        # and the delivered-create counts need no parent issue number at
        # all -- only the two checks that actually call `gh_json` on the
        # PARENT issue (the title/body render, which names it, and
        # assertion 3's umbrella) do. The round-4 guard widened `and parent
        # is not None` over this whole block, so a bundle_hash the proof
        # could not read silently hid D1's own new check along with the
        # other two -- one unreadable parent number suppressing three
        # checks that never asked for one.
        dup_filed = {k: len(fs) for k, fs in front.slice_filings(store, run_id, n).items() if len(fs) > 1}
        if dup_filed:
            fails.append(f"slice_filed: more than one filing for slice(s) {dup_filed} (one filing per slice)")
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
            # Round 4: also skip a slice (or a dependency of one) whose
            # slice_filed fact was missing "issue" -- already named above by
            # `_need`, and a `numbers[...]` subscript below would otherwise
            # crash on exactly the fact that just failed to build it.
            # Round 5, RC-1: and skip when the parent number itself could
            # not be read -- `render_child` needs it, and that failure is
            # already named above; this check alone, of the three in this
            # block, actually needs it.
            if (s.number not in filed or s.number not in numbers
                    or any(d not in numbers for d in s.depends_on) or parent is None):
                continue
            try:
                live = gh_json(["gh", "issue", "view", str(numbers[s.number]), "--repo", repo, "--json", "title,body"])
            except ReadFailed as exc:
                fails.append(f"slice {s.number} (#{numbers[s.number]}): could not read title,body: {exc}")
                continue
            title, body = slices.render_child(s, parent, h[:8], {d: numbers[d] for d in s.depends_on})
            if live.get("title") != title or (live.get("body") or "").rstrip("\n") != body.rstrip("\n"):
                fails.append(f"child #{numbers[s.number]}: title or body is not render_child over slice {s.number}")
        # 2. The dependencies are the links. No parent number needed.
        for s in plan.slices:
            if s.number not in filed or s.number not in numbers or any(d not in numbers for d in s.depends_on):
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
        # 3. The umbrella names the children -- on the PARENT issue, so this
        #    one genuinely needs the parent number (round 5, RC-1).
        if parent is not None:
            try:
                comments = gh_json(["gh", "issue", "view", str(parent), "--repo", repo, "--json", "comments"]).get("comments") or []
            except ReadFailed as exc:
                fails.append(f"parent #{parent}: could not read comments: {exc}")
                comments = None
            if comments is not None:
                ums = [c for c in comments if (c.get("body") or "").startswith(f"bircher: sliced {h[:8]}")]
                if len(ums) != 1:
                    fails.append(f"{len(ums)} umbrella comments for plan {h[:8]}, want exactly one")
                elif any(s.number not in numbers for s in plan.slices):
                    # Round 4: `umbrella_body` subscripts every plan slice's
                    # filed number unconditionally; a slice already named
                    # above (missing "issue") would otherwise crash it here
                    # too.
                    fails.append("the umbrella comment could not be checked: a filed slice has no issue number")
                elif ums[0]["body"].rstrip("\n") != slices.umbrella_body(h[:8], plan, numbers).rstrip("\n"):
                    fails.append("the umbrella comment does not list exactly the filed children")
    # 4. One decision per VISIT, and every filing in the last epoch
    #    (shaping spec §6, revision 16). Three clauses.
    # (a) No filing outside the final epoch: a superseded epoch never filed,
    #     since revise_bundle is refused from `sliced`.
    for f in store.facts_of_kind(run_id, EventKind.SLICE_FILED):
        if f.payload.get("epoch") != n:
            fails.append(f"slice_filed in epoch {f.payload.get('epoch')}, outside the final epoch {n}")

    def _decision_at(e: int, v: int):
        """The visit's own decision, per clause (b): EVERY shape ruling
        attributed to it (round 3, C3 -- not `front.shape_ruling`'s newest
        alone: a visit holding two rulings, forged or otherwise, has a
        decision each, and "at most one" cannot be checked by a reader that
        already collapsed them to one), and the submission the acceptance
        actually followed (empty if none). Shared by clauses (b) and (c) so
        "the last visit's decision" means exactly the same thing in both
        places (round 1, M1).

        Round 4, D4: the accepted hash is EPOCH e's own
        (`front.accepted_slices_hash(store, run_id, e)`), not the final
        epoch's read once outside this function and reused for every `e`.
        The old `e == n` conjunct collapsed "within EACH epoch" (spec §6(b))
        to just the final one -- clause (b)'s "ruling beside the accepted
        plan" check was dead in every superseded epoch, the very epochs a
        hand-back and a re-shape make routine."""
        rulings = [f for f in front.epoch_facts(store, run_id, EventKind.MODEL_RULING, e)
                   if f.payload.get("question_id") == slices.SHAPE_QUESTION
                   and front.visit_stamp_of(store, run_id, f) == v]
        subs_v = front.submissions(store, run_id, "slices", e, visit=v)
        acc_v = front.newest_review_verdict(store, run_id, "slices", e, visit=v)
        accepted_h_e = front.accepted_slices_hash(store, run_id, e)
        accepted = [x for x in subs_v
                    if x.payload.get("hash") == accepted_h_e
                    and acc_v is not None and acc_v.payload.get("verdict") == "accept"]
        return rulings, subs_v, accepted

    # Round 2, B4 / round 3, C1 and C2: every reader in this function (and
    # in assert_dispute) keys on `payload["epoch"]`, so a fact stamped an
    # epoch this run never reached is read by nothing -- the same defect
    # `visit` had, one key over, and round 2 only bounded `epoch` to a
    # range rather than deriving it. The journal gives the true epoch as
    # readily as the true visit: count the `bundle_revised` facts that
    # precede the fact (`front.epoch_of`, `visit_of`'s own sibling). Checked
    # over every shape ruling and slices submission in the WHOLE run, not
    # scoped to any single `e` the rest of this function already trusts.
    all_shape_rulings = [f for f in store.facts_of_kind(run_id, EventKind.MODEL_RULING)
                         if f.payload.get("question_id") == slices.SHAPE_QUESTION]
    all_slices_subs = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
                       if f.payload.get("phase") == "slices"]
    for f in all_shape_rulings + all_slices_subs:
        ep = f.payload.get("epoch")
        derived = front.epoch_of(store, run_id, f.seq)
        if ep != derived:
            fails.append(f"{f.kind} (seq {f.seq}) stamped epoch {ep!r}, but the journal's own count of "
                         f"bundle_revised facts before it gives epoch {derived}")

    for e in range(n + 1):
        last = front.shaping_visit(store, run_id, e)
        # Round 1, B1 / round 2, B3 and B5: the proof used to trust a
        # fact's `visit` stamp on its own -- first only checking it fell in
        # [1, last] (round 1), which an IN-RANGE but WRONG stamp still
        # passed (a ruling truly written in visit 3, forged to visit 2,
        # where visit 2 already holds a decision of its own to hide
        # behind), and which a stamp of `None` skipped by construction (the
        # `is not None` guard). The one ground truth §6 names is
        # `front.visit_of`'s prefix walk ("by the visit it carries, or ...
        # by front.visit_of"); a stamp that disagrees with it is a defect in
        # its own right, compared by `!=` so a stamp of the wrong TYPE
        # (a string) fails the comparison instead of crashing a `<=` the
        # way the round-1 range check did.
        # Round 4: `reshape_requested` and `human_ruling {approve_one_piece}`
        # both carry a `visit` stamp too (the hand-back opens the next
        # visit; the resolution names the disputed ruling's own, per
        # commands.py's `approve_one_piece` comment) and neither was ever
        # compared against the walk -- no exploit was found for either, but
        # leaving a written, unread stamp unchecked is the same gap this
        # very loop exists to close for the other two kinds. Cheap to add,
        # so added rather than deleted.
        stamped = ([f for f in front.epoch_facts(store, run_id, EventKind.MODEL_RULING, e)
                    if f.payload.get("question_id") == slices.SHAPE_QUESTION]
                   + front.submissions(store, run_id, "slices", e)
                   + front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, e)
                   + [f for f in front.epoch_facts(store, run_id, EventKind.HUMAN_RULING, e)
                      if f.payload.get("ruling") == "approve_one_piece"])
        for f in stamped:
            if "visit" not in f.payload:
                continue  # no stamp at all: the prefix walk is simply the answer, not a claim to check
            walked = front.visit_of(store, run_id, f.seq)
            if f.payload["visit"] != walked:
                fails.append(f"epoch {e}: {f.kind} (seq {f.seq}) stamped visit {f.payload['visit']!r}, but "
                             f"the journal's own prefix walk attributes it to visit {walked}")
        for v in range(1, last + 1):
            rulings, subs_v, accepted = _decision_at(e, v)
            # (b) At most one decision per visit: the ruling, or the
            #     submission the acceptance actually followed. Rejected
            #     submissions BEFORE a ruling in the same visit are
            #     admitted -- an author may write a plan, have it rejected,
            #     and then rule. A submission AFTER *A* ruling is not
            #     (round 3, C3: not merely after the newest ruling in the
            #     visit -- a submission sandwiched between two forged
            #     rulings still follows the first one).
            #     Identified by the review_verdict {accept} its visit holds,
            #     not by hash equality alone: a plan rejected in visit 1 and
            #     resubmitted in visit 2 leaves the accepted hash sitting in
            #     BOTH visits, and hash equality alone would double-count it.
            if rulings and accepted:
                fails.append(f"epoch {e} visit {v}: a shape ruling beside the accepted slice plan "
                             "(one decision per visit)")
            if any(x.seq > r.seq for r in rulings for x in subs_v):
                fails.append(f"epoch {e} visit {v}: a slice plan was submitted after the shape ruling "
                             "(one decision per visit)")
    # (c) In the final epoch the LAST visit's decision matches the outcome:
    #     a slice_filed exists iff that visit's decision is the ACCEPTED
    #     SUBMISSION, and none exists iff it is a ruling (round 1, M1: not
    #     merely "no ruling is present", which a filing left over from an
    #     EARLIER visit already satisfies whether or not the last visit
    #     decided anything at all -- clause (b)'s own `accepted` is what
    #     "the accepted submission" means, reused here at v=last). A ruling
    #     in an earlier visit beside a filing in the last is legal -- it is
    #     the hand-back resolved by slicing, not a second decision in one
    #     visit.
    last = front.shaping_visit(store, run_id, n)
    rulings, _subs_last, accepted = _decision_at(n, last)
    decided_by_ruling = bool(rulings)
    decided_by_submission = bool(accepted)
    if bool(filed) != decided_by_submission:
        fails.append(f"final epoch {n} visit {last}: the last visit's decision must be the accepted "
                     f"submission whenever a slice_filed exists (filed={bool(filed)}, "
                     f"decided_by_submission={decided_by_submission})")
    if (not filed) != decided_by_ruling:
        fails.append(f"final epoch {n} visit {last}: the last visit's decision must be a shape ruling "
                     f"whenever no slice_filed exists (filed={bool(filed)}, decided_by_ruling={decided_by_ruling})")
    return fails


def assert_dispute(store, run_id: str) -> list[str]:
    """The fifth §6 line (revision 16): the hand-back was answered.

    Over each epoch that holds a `reshape_requested` and is not superseded
    by a later `bundle_revised` -- a superseded bundle owes nothing, since
    the person's issue edit is the resolution the old epoch never got a
    chance to reach. The hand-back must be answered: by a slice plan
    accepted and filed after it, or by a disputed ruling followed by its
    resolution (`front.dispute` is the one definition of both -- the
    destination, the refusals, the coordinator's notices and this line all
    read it, so a second derivation cannot disagree with it the way three
    incompatible ones once did). And no `spec` submission may fall between
    the disputed ruling and the resolution -- the window revision 16
    protects, where a crash loses nothing because no decision yet exists to
    lose."""
    fails: list[str] = []
    n = front.epoch(store, run_id)
    # Round 1, L1 / round 2, B4 / round 3, C1 and C2: `epoch_facts` (and
    # `front.dispute`) key on `payload["epoch"]` -- a reshape_requested
    # stamped an epoch that disagrees with the journal's own count of
    # `bundle_revised` facts before it (missing, out of range, or merely a
    # value that LOOKS like a real one) matches no `e` in the loop below and
    # is invisible to every hand-back check -- including "one hand-back per
    # bundle", and including the fifth line's own superseded-epoch skip,
    # which is exactly the epoch a hand-back stamped one back would be
    # misread as belonging to. `front.epoch_of` is the same derivation
    # assertion 4 now uses for its own facts.
    for f in store.facts_of_kind(run_id, EventKind.RESHAPE_REQUESTED):
        ep = f.payload.get("epoch")
        derived = front.epoch_of(store, run_id, f.seq)
        if ep != derived:
            fails.append(f"reshape_requested {f.id} stamped epoch {ep!r}, but the journal's own count of "
                         f"bundle_revised facts before it gives epoch {derived}: invisible to every hand-back check")
    for e in range(n + 1):
        hbs = front.epoch_facts(store, run_id, EventKind.RESHAPE_REQUESTED, e)
        if not hbs:
            continue
        if len(hbs) > 1:
            fails.append(f"epoch {e}: a second reshape_requested; one hand-back per bundle is the kernel's guard")
        if e != n:
            continue                      # superseded: the epoch owes nothing
        d = front.dispute(store, run_id, e)
        filed_after = [f for f in front.epoch_facts(store, run_id, EventKind.SLICE_FILED, e)
                       if f.seq > d.hand_back.seq]
        if d.disputed is None:
            if not filed_after:
                fails.append(f"epoch {e}: the hand-back is unanswered -- no disputed ruling and no filing after it")
            continue
        if d.resolution is None:
            fails.append(f"epoch {e}: the hand-back is unanswered -- a disputed ruling with no resolution")
            continue
        between = [f for f in store.facts_of_kind(run_id, EventKind.ARTIFACT_SUBMITTED)
                   if f.payload.get("epoch") == e and f.payload.get("phase") == "spec"
                   and d.disputed.seq < f.seq < d.resolution.seq]
        if between:
            fails.append(f"epoch {e}: a spec was submitted between the disputed ruling and the resolution")
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
    fails = (assert_journal(store, a.run_id, mode=mode) + assert_sessions(store, a.run_id, server=a.server)
             + assert_dispute(store, a.run_id))

    if a.children:
        from coordinator.sweep import ReadFailed, gh_json
        if not a.repo:
            print("--children needs --repo", file=sys.stderr)
            return 2
        fails += assert_slices(store, a.run_id, repo=a.repo, gh_json=gh_json)
        for f in store.facts_of_kind(a.run_id, EventKind.SLICE_FILED):
            # Round 4: `f.payload["issue"]`, read three times over this
            # fact, used to crash `main` outright on a slice_filed missing
            # it.
            try:
                issue = _need(f, "issue")
            except _BadPayload as exc:
                fails.append(exc.line)
                continue
            kids = [r for r in store.all_run_ids() if r.startswith(f"i{issue}-")]
            if not kids:
                fails.append(f"child #{issue} has no run yet")
                continue
            # Nothing here reads gh today, but a read failure while proving a
            # child must become a failure line, not an exception escaping
            # main -- the loop continues over the remaining children either way.
            try:
                child_fails = (assert_journal(store, kids[-1], mode=mode) + assert_sessions(store, kids[-1], server=a.server)
                               + assert_dispute(store, kids[-1]))
            except ReadFailed as exc:
                fails.append(f"child #{issue} ({kids[-1]}): could not read: {exc}")
                continue
            fails += [f"child #{issue} ({kids[-1]}): {x}" for x in child_fails]

    if a.issues:
        # The body-content half of this rule (no file, function or acceptance
        # test named) is the pre-registration's own discipline, applied when
        # the list is written (Task 24) -- there is no body left to read here.
        enq = store.newest_fact(a.run_id, EventKind.RUN_ENQUEUED)
        if enq is None:
            fails.append("no run_enqueued fact: cannot check the pre-registered issue list")
        else:
            try:
                # Round 4: `run_enqueued`'s `bundle_hash` -- the crash class.
                bh = _need(enq, "bundle_hash")
                snap = json.loads(store.read_blob(bh))
                allowed = {int(x) for x in open(a.issues).read().split()}
                if snap["number"] not in allowed:
                    fails.append(f"issue #{snap['number']} is not in the pre-registered list")
            except _BadPayload as exc:
                fails.append(exc.line)

    for f in fails:
        print(f, file=sys.stderr)
    print("PROOF: " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
