"""What to do with a run that was interrupted, decided from HISTORY.

The repair loop holds no state of its own, and the state NAME is not enough to
recover from. `reviewing` is reached from `record_review(accept)`, from
`record_review(reject)` AND from `record_merge_outcome(failed)` -- "proceed to
merge or revise" cannot be derived from it. Worse, a crash after an external
review returned FAIL but BEFORE `request_revision` was recorded leaves no
journal evidence that a revision is owed, while an older accepted binding may
still be the latest verdict, so a naive resume would merge on it.

So this reads the journal. `decide()` is pure -- facts in, an action out -- and
every row of the design's recovery table is one branch with one test.

ORDER IS THE DESIGN. Evidence is examined most-advanced first, because the
dangerous mistakes all run the other way: re-issuing a merge that already
happened, or re-reviewing past a merge that is already recorded. An earlier
draft checked the review rows first and would have re-reviewed a merged run.

Design: docs/superpowers/specs/2026-08-31-repair-loop-design.md
"""

from __future__ import annotations

from dataclasses import dataclass

MERGE = "merge"


@dataclass(frozen=True)
class Action:
    """*do* is the verb the caller dispatches on; *why* is for the log."""

    do: str
    why: str


def _kind(f) -> str:
    k = getattr(f, "kind", None)
    return getattr(k, "value", k) or ""


def _payload(f) -> dict:
    return getattr(f, "payload", None) or {}


def _of(facts, *kinds):
    return [f for f in facts or () if _kind(f) in kinds]


def _last(facts, *kinds):
    got = _of(facts, *kinds)
    return got[-1] if got else None


def merge_effect_state(facts) -> str | None:
    """The state of this run's MERGE effect, as the FACTS record it.

    JOINED THROUGH effect_id ON PURPOSE. `effect_uncertain` carries only the id
    and the error -- not the class -- so filtering uncertainty by class is only
    possible via the intent that opened it. Filtering on the confirmations
    alone would make every uncertain merge invisible, which is precisely the
    row that must never be re-executed.

    A FALLBACK, not the authority. `store.journal_intent` writes to the effects
    TABLE and appends no fact at all, so a caller holding the store should pass
    `merge_effect=` from `store.effect_state` instead. That is the same source
    `kernel.cli pending` reads and the one `is_halted` trusts. This exists for
    callers that have only a fact stream, and it is correct for the case that
    actually arises in production -- a crash INSIDE `perform`, which appends
    `effect_intended` before it executes anything.
    """
    ids = set()
    for f in _of(facts, "effect_intended", "effect_confirmed"):
        p = _payload(f)
        if p.get("effect_class") == MERGE and p.get("effect_id"):
            ids.add(p["effect_id"])
    if not ids:
        return None
    by_state = {}
    for state in ("effect_intended", "effect_uncertain", "effect_confirmed",
                  "effect_reconciled"):
        for f in _of(facts, state):
            eid = _payload(f).get("effect_id")
            if eid in ids:
                by_state[eid] = state.removeprefix("effect_")
    # The WORST unresolved state wins: one uncertain merge among several
    # confirmed ones still means the forge may hold a merge nobody recorded.
    for worst in ("uncertain", "intended"):
        if worst in by_state.values():
            return worst
    return "reconciled" if "reconciled" in by_state.values() else "confirmed"


def decide(facts, *, current_binding_hash=None, merge_effect=None) -> Action:
    """One row of the recovery table, for a run's whole journal.

    *current_binding_hash* is `artifacts.binding_hash` of the binding the run
    would present NOW. The acceptance is compared against it because a
    REVIEW_VERDICT fact records `binding_hash` and NOT `artifact_hash` -- I
    wrote this comparing artifact hashes, and it could never have matched. A
    hand-built fact list would have agreed with the mistake.

    *merge_effect* is the effect table's own answer for this run's merge, when
    the caller has the store. Omitted, the facts are used instead.
    """
    facts = list(facts or ())

    # --- the merge is already under way, or over -----------------------------
    for f in reversed(_of(facts, "transition_performed")):
        p = _payload(f)
        if p.get("via") != "record_merge_outcome":
            continue
        if p.get("to") == "merged":
            return Action("done", "the merge outcome is recorded; close out")
        # `merged` is the only terminal destination; anything else is the
        # failure arm. Retrying the MERGE is right and it must NOT consume a
        # revision: the merge failed, not the review, and spending a repair
        # round on it would burn the allowance on work no reviewer asked for.
        return Action("retry_merge",
                      "the merge failed, not the review; retry the merge and "
                      "do not consume a revision")

    state = merge_effect if merge_effect is not None else merge_effect_state(facts)
    if state:
        if state in ("intended", "uncertain"):
            # THE MERGE MAY ALREADY HAVE HAPPENED AT GITHUB. An intent was
            # journalled and no confirmation followed, so the kernel does not
            # know whether the command reached the forge. Re-executing risks a
            # second merge; assuming failure risks reporting an open PR as
            # merged. Only an observation can settle it. This is the halt
            # muesli #726 took on the first live merge.
            return Action("halt_and_reconcile",
                          "a merge effect is intended or uncertain; observe "
                          "the PR and reconcile, never re-execute")
        return Action("record_merge_outcome",
                      "the merge HAPPENED and its outcome was never recorded; "
                      "record it from the observed merge commit")

    if _of(facts, "merge_authorized"):
        # Authorised and never attempted. Perform the merge -- do NOT re-issue
        # request_merge, which is illegal from `merge_requested` and would be
        # refused, leaving the run stuck with a valid authorization it cannot
        # use.
        return Action("perform_merge",
                      "authorised and never attempted; perform the merge "
                      "without re-issuing request_merge")

    # --- the review rows -----------------------------------------------------
    verdicts = _of(facts, "review_verdict")
    if not verdicts:
        # A record_review that VALIDATED and then lost the CAS leaves a
        # rejection and no verdict fact. The revision did not happen, whatever
        # the adapter returned.
        if _rejected_review(facts):
            return Action("re_derive",
                          "a record_review was rejected and no verdict fact "
                          "exists; the review did not land")
        return Action("derive", "no review verdict at all; derive from scratch")

    latest = verdicts[-1]
    verdict = _payload(latest).get("verdict")

    if _rejected_review(facts, after=getattr(latest, "seq", None)):
        # A verdict exists, but a LATER record_review was refused. Treating the
        # older verdict as current is how an approval outlives the review that
        # was meant to replace it.
        return Action("re_derive",
                      "a record_review after the latest verdict was rejected; "
                      "do not treat the older verdict as current")

    if verdict == "request_revision":
        started = _of(facts, "attempt_dispatched")
        in_flight = [f for f in started
                     if (getattr(f, "seq", 0) or 0) > (getattr(latest, "seq", 0) or 0)
                     and _payload(f).get("role") == "implementer"]
        if in_flight:
            return Action("settle_implementer",
                          "a revision was recorded and an implementer is "
                          "already in flight; settle it rather than dispatching "
                          "a second")
        return Action("dispatch_implementer",
                      "a revision was recorded and nothing has started; "
                      "dispatch the repair")

    if verdict == "reject":
        return Action("terminal", "the review rejected the work; terminal")

    if verdict == "accept":
        bound = _payload(latest).get("binding_hash")
        if current_binding_hash is not None and bound != current_binding_hash:
            # `validate_review` already refuses this binding, so proceeding
            # would fail at the merge gate anyway -- but it would fail LATE,
            # after the effect path had been entered.
            return Action("re_review",
                          "the latest acceptance binds a superseded output; "
                          "the approval is stale")
        return Action("merge", "an acceptance binds the current output")

    return Action("derive", f"unrecognised verdict {verdict!r}; derive again")


def _rejected_review(facts, after=None) -> bool:
    """Was a `record_review` command refused, with no verdict fact behind it?

    `commands.py` validates, THEN bumps the version under CAS, THEN appends
    REVIEW_VERDICT. A command that loses the CAS is refused after validation
    and writes no verdict -- and the shell adapter is advisory, so its caller
    saw success.
    """
    for f in _of(facts, "command_rejected"):
        # `command_name`, not `command`. I guessed `command` writing this and
        # checked before testing: a hand-built fact would have agreed with the
        # guess and the row would simply never have fired in production.
        if _payload(f).get("command_name") != "record_review":
            continue
        if after is not None and (getattr(f, "seq", 0) or 0) <= after:
            continue
        return True
    return False
