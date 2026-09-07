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

import difflib
import json

from kernel.canon import canonical_bytes, content_hash

#: Version 2 drops what bircher itself writes to an issue. Version 1 froze
#: every label and every comment, so the runner's own `bircher:running` flip
#: and the coordinator's publication comment read as a relevant change and
#: reset the run to `queued` on every pass (spec §2 Bundle revision).
BUNDLE_CANON_VERSION = 2

#: Pinned by a test, not by convention: changing this set rehashes every
#: bundle ever frozen, so it is a deliberate, versioned change.
FROZEN_FIELDS = ("number", "title", "body", "labels", "comments")

#: Labels bircher writes. Never frozen; they carry state, not requirement.
BIRCHER_LABEL_PREFIX = "bircher:"

#: The single definition of the predicate `run-queue.sh`'s digest also
#: applies. Five EXACT prefixes, matched with startswith on the stripped body
#: -- not the generic "bircher: ", which would silence a human discussing a
#: marker. Both copies are tested against tests/fixtures/bircher_status_comments.tsv.
BIRCHER_STATUS_PREFIXES: tuple[str, ...] = (
    "bircher: outcome=",
    "bircher-status:",
    "Outcome derived from the repository",
    "Cross-vendor review (outcome derived",
    "bircher: published ",
)


def is_bircher_status(body: str) -> bool:
    head = (body or "").lstrip()
    return any(head.startswith(p) for p in BIRCHER_STATUS_PREFIXES)


def snapshot(issue: dict) -> dict:
    """Reduce a provider issue to the frozen input, in canonical form."""
    return {
        "canon_version": BUNDLE_CANON_VERSION,
        "number": int(issue["number"]),
        "title": issue["title"],
        "body": issue["body"],
        # Sorted: label order is not stable and carries no meaning. Bircher's
        # own labels are state, not input.
        "labels": sorted(
            l for l in issue.get("labels", []) if not l.startswith(BIRCHER_LABEL_PREFIX)
        ),
        # Ordered by id: creation order is the meaningful one, and stable.
        # Bircher's own status and publication comments are not input.
        "comments": [
            {"id": int(c["id"]), "author": c["author"], "body": c["body"]}
            for c in sorted(issue.get("comments", []), key=lambda c: int(c["id"]))
            if not is_bircher_status(c.get("body") or "")
        ],
    }


def bundle_hash(snap: dict) -> str:
    return content_hash(canonical_bytes(snap))


DIFF_CAP = 65536


def snapshot_diff(old: dict, new: dict) -> dict:
    """What changed between two snapshots, for the next author's findings."""
    changed = sorted(k for k in FROZEN_FIELDS if old.get(k) != new.get(k))
    if not changed:
        return {"changed": [], "unified": ""}
    a = json.dumps(old, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    b = json.dumps(new, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    text = "\n".join(difflib.unified_diff(a, b, "before", "after", lineterm=""))
    if len(text) > DIFF_CAP:
        text = text[:DIFF_CAP] + "\n... truncated"
    return {"changed": changed, "unified": text}


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
    from kernel.artifacts import put_artifact
    from kernel.canon import canonical_bytes
    from kernel.events import EventKind

    h = put_artifact(store, canonical_bytes(new_snapshot))
    assert h == bundle_hash(new_snapshot)
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
