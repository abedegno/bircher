"""The frozen input snapshot.

**Decision 1 — which fields.** Exactly five form the frozen input. Volatile
metadata (updated_at, reactions, view counts) is excluded: it changes without
the input changing, and including it would invalidate approvals for no reason.

**Decision 2 — canonicalization.** The snapshot is canonicalized before
hashing -- labels sorted, comments ordered by id -- so a re-read of the same
issue produces the same hash regardless of the order the provider returns.

The canonical form is versioned and the version is IN the snapshot. A hash
whose canonical form can change without a version is a hash that drifts
silently, and every approval bound to it drifts with it.
"""

from __future__ import annotations

from kernel.canon import canonical_bytes, content_hash

BUNDLE_CANON_VERSION = 1

#: Pinned by a test, not by convention: changing this set rehashes every
#: bundle ever frozen, so it is a deliberate, versioned change.
FROZEN_FIELDS = ("number", "title", "body", "labels", "comments")


def snapshot(issue: dict) -> dict:
    """Reduce a provider issue to the frozen input, in canonical form."""
    return {
        "canon_version": BUNDLE_CANON_VERSION,
        "number": int(issue["number"]),
        "title": issue["title"],
        "body": issue["body"],
        # Sorted: label order is not stable and carries no meaning.
        "labels": sorted(issue.get("labels", [])),
        # Ordered by id: creation order is the meaningful one, and stable.
        "comments": [
            {"id": int(c["id"]), "author": c["author"], "body": c["body"]}
            for c in sorted(issue.get("comments", []), key=lambda c: int(c["id"]))
        ],
    }


def bundle_hash(snap: dict) -> str:
    return content_hash(canonical_bytes(snap))


# --- Decision 3: what counts as a relevant change ----------------------------

def is_relevant_change(old_issue: dict, new_issue: dict) -> bool:
    """True when the FROZEN input changed.

    Takes raw provider issues, not snapshots, deliberately. Given two
    snapshots the answer is `old != new` and the function proves nothing --
    the whole content of decision 3 is that volatile metadata does not count,
    and that only shows when something volatile is present to be ignored.

    Defined by the frozen set rather than a separate list of "interesting"
    fields, which is a list that drifts away from the set it mirrors.
    """
    return bundle_hash(snapshot(old_issue)) != bundle_hash(snapshot(new_issue))


# --- Decision 4: who creates a revision ---------------------------------------
#
# Only a human. The front end grills and proposes; re-authorizing work is not
# a model's to do.
#
# This is TWO ENTRY POINTS, not a constant. `REVISION_AUTHORITY == "human"`
# compares a constant to a constant: it states the decision without enforcing
# it, and a model calling a revise function would still revise. The function
# a caller can reach is what decides, so a model may reach `propose_revision`
# and there is no argument it can pass to reach the other one.

REVISION_AUTHORITY = "human"


def propose_revision(store, run_id: str, *, reason: str) -> None:
    """The model path. Records a proposal and changes nothing else."""
    from kernel.events import EventKind

    store.append_fact(
        run_id=run_id, kind=EventKind.REVISION_PROPOSED, actor="model",
        causal_command_id=None, payload={"reason": reason},
    )


def revise_bundle(store, run_id: str, *, new_snapshot: dict, reason: str) -> str:
    """The operator path. Re-freezes the input under a human's authority.

    Reachable only from the operator's own path -- enforced by the
    filesystem boundary, not the network one; see dispatch.py. On the other side of the
    M1-1 boundary from any model session -- the same enforcement `reconcile`
    already relies on. That is why `actor="human"` here is a record rather
    than a claim: there is no code path a model can call that reaches it.
    """
    from kernel.events import EventKind

    h = bundle_hash(new_snapshot)
    store.append_fact(
        run_id=run_id, kind=EventKind.BUNDLE_REVISED, actor="human",
        causal_command_id=None, payload={"bundle_hash": h, "reason": reason},
    )
    return h


# --- Decision 5: which changes invalidate which verdict ----------------------
#
# A spec or plan verdict binds the artifact, the base and the context bundle.
# It never bound the implementation head, so implementation output does not
# invalidate it: invalidating on head would discard sound approvals and put
# every run into re-review churn over a change the reviewer never considered.
# An implementation verdict does bind the head. EVERY verdict binds the base --
# rebasing the world changes what any approval was about.

_BINDS = {
    "spec_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash"},
    "plan_review":           {"artifact_hash", "base_git_sha", "context_bundle_hash"},
    "implementation_review": {"artifact_hash", "base_git_sha", "head_git_sha"},
}

VERDICT_KINDS = frozenset(_BINDS)


def invalidates(verdict_kind: str, changed: set[str]) -> bool:
    """True when *changed* touches something *verdict_kind* bound.

    Unknown kinds raise. A default of False would make a typo silently mean
    "nothing invalidates this", which is the fail-open direction.
    """
    if verdict_kind not in _BINDS:
        raise ValueError(
            f"unknown verdict kind {verdict_kind!r}; expected one of "
            f"{sorted(_BINDS)}"
        )
    return bool(_BINDS[verdict_kind] & changed)
