"""Event kinds and their schema versions.

A stored event must never acquire a new meaning when code changes, so every
kind declares its version here and the store refuses any kind absent from this
table.
"""

from __future__ import annotations


class EventKind:
    RUN_STARTED = "run_started"
    COMMAND_REQUESTED = "command_requested"
    COMMAND_ACCEPTED = "command_accepted"
    COMMAND_REJECTED = "command_rejected"
    ARTIFACT_CREATED = "artifact_created"
    REVIEW_VERDICT = "review_verdict"
    TRANSITION = "transition_performed"
    OBSERVATION = "external_observation"
    HUMAN_RULING = "human_ruling"
    OWNERSHIP_ACQUIRED = "ownership_acquired"
    EFFECT_INTENDED = "effect_intended"
    EFFECT_CONFIRMED = "effect_confirmed"
    EFFECT_UNCERTAIN = "effect_uncertain"
    EFFECT_RECONCILED = "effect_reconciled"
    ATTEMPT_DISPATCHED = "attempt_dispatched"
    MERGE_AUTHORIZED = "merge_authorized"


SCHEMA_VERSIONS = {
    EventKind.RUN_STARTED: 1,
    EventKind.COMMAND_REQUESTED: 1,
    EventKind.COMMAND_ACCEPTED: 1,
    EventKind.COMMAND_REJECTED: 1,
    EventKind.ARTIFACT_CREATED: 1,
    EventKind.REVIEW_VERDICT: 1,
    EventKind.TRANSITION: 1,
    EventKind.OBSERVATION: 1,
    EventKind.HUMAN_RULING: 1,
    EventKind.OWNERSHIP_ACQUIRED: 1,
    EventKind.EFFECT_INTENDED: 1,
    EventKind.EFFECT_CONFIRMED: 1,
    EventKind.EFFECT_UNCERTAIN: 1,
    EventKind.EFFECT_RECONCILED: 1,
    EventKind.ATTEMPT_DISPATCHED: 1,
    EventKind.MERGE_AUTHORIZED: 1,
}

MECHANISM_VERSION = 1
