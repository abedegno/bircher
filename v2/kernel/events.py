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
    MODEL_QUESTION = "model_question"
    ENQUEUE_PROPOSED = "enqueue_proposed"
    RUN_ENQUEUED = "run_enqueued"
    REVISION_PROPOSED = "revision_proposed"
    BUNDLE_REVISED = "bundle_revised"
    SHADOW_REJECTED = "shadow_rejected"

    # Front half (spec §2). Each is written by exactly one command; see
    # kernel/commands.py::_side_fact.
    POLICY_FROZEN = "policy_frozen"
    ARTIFACT_SUBMITTED = "artifact_submitted"
    REVIEW_BRIEF_ISSUED = "review_brief_issued"
    PARKED = "parked"
    TURN_ENDED = "turn_ended"
    AUTHOR_EMPTY = "author_empty"
    HUMAN_ANSWER = "human_answer"
    MODEL_RULING = "model_ruling"
    HUMAN_DIRECTION = "human_direction"
    HUMAN_ITEM_DISMISSED = "human_item_dismissed"
    PROMPT_ITEM = "prompt_item"

    # The shaping phase (shaping spec §2 *Facts*). Each is written by exactly
    # one command; see kernel/commands.py::_side_fact.
    ARTIFACT_ADVANCED = "artifact_advanced"
    SLICE_FILED = "slice_filed"
    FILING_COMPLETE = "filing_complete"
    SLICE_CLOSED = "slice_closed"
    SLICE_REOPENED = "slice_reopened"
    CHILDREN_OBSERVED_CLOSED = "children_observed_closed"


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
    EventKind.MODEL_QUESTION: 1,
    EventKind.ENQUEUE_PROPOSED: 1,
    EventKind.RUN_ENQUEUED: 1,
    EventKind.REVISION_PROPOSED: 1,
    EventKind.BUNDLE_REVISED: 1,
    EventKind.SHADOW_REJECTED: 1,
    EventKind.POLICY_FROZEN: 1,
    EventKind.ARTIFACT_SUBMITTED: 1,
    EventKind.REVIEW_BRIEF_ISSUED: 1,
    EventKind.PARKED: 1,
    EventKind.TURN_ENDED: 1,
    EventKind.AUTHOR_EMPTY: 1,
    EventKind.HUMAN_ANSWER: 1,
    EventKind.MODEL_RULING: 1,
    EventKind.HUMAN_DIRECTION: 1,
    EventKind.HUMAN_ITEM_DISMISSED: 1,
    EventKind.PROMPT_ITEM: 1,
    EventKind.ARTIFACT_ADVANCED: 1,
    EventKind.SLICE_FILED: 1,
    EventKind.FILING_COMPLETE: 1,
    EventKind.SLICE_CLOSED: 1,
    EventKind.SLICE_REOPENED: 1,
    EventKind.CHILDREN_OBSERVED_CLOSED: 1,
}

MECHANISM_VERSION = 1
