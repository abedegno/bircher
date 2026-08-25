"""Decision-as-data validation.

A decision arrives as data, not as an action. The kernel validates its schema,
confirms every referenced hash STILL matches what it observes, confirms the
decision type is legal, and checks reviewer independence -- before any
transition is computed.
"""

from __future__ import annotations


class DecisionRejected(Exception):
    pass


BOUND_INPUTS = (
    "state_version", "spec_sha256", "plan_sha256",
    "base_git_sha", "head_git_sha", "review_bundle_sha256",
)

LEGAL_TYPES = frozenset({"review_ruling", "ci_ruling", "human_ruling"})


def validate_decision(store, decision: dict, observed: dict) -> None:
    if decision.get("decision_type") not in LEGAL_TYPES:
        raise DecisionRejected(
            f"decision_type {decision.get('decision_type')!r} is not legal"
        )

    based_on = decision.get("based_on") or {}
    for name in BOUND_INPUTS:
        if name not in based_on:
            raise DecisionRejected(f"based_on is missing {name}")
        if based_on[name] != observed.get(name):
            raise DecisionRejected(
                f"{name} drifted: decision saw {based_on[name]!r}, "
                f"kernel observes {observed.get(name)!r}"
            )

    if observed.get("reviewer_identity") == observed.get("implementer_identity"):
        raise DecisionRejected(
            "reviewer independence violated: reviewer and implementer are the same actor"
        )
