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
