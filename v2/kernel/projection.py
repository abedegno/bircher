"""Rebuild current run state from facts.

Facts are the truth; this is derived. The spec does not require full event
sourcing everywhere -- it requires that immutable facts and mutable derived
state stay distinguishable, which is only true if the projection is checked
against the aggregate rather than assumed to match it.

Unknown kinds are skipped rather than raising, so a fact written by a newer
mechanism version does not break replay of the ones this version understands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kernel.events import EventKind

# A `review_verdict` carries a `phase` (Task 7): "spec", "plan" or
# "implementation". The back-half -- the repair loop's allowance, its
# durability check, and recovery -- must never act on a spec or plan verdict,
# or a spec-phase FAIL would spend the implementation's revision allowance.
# A `slices` verdict is the front half's too: a one-piece run that needed two
# slice-plan rounds must not arrive in its back half with its repair allowance
# spent (shaping spec §2).
FRONT_PHASES = ("slices", "spec", "plan")


def is_front_verdict(payload: dict) -> bool:
    """A review_verdict of the slices, spec or plan phase. A verdict with no
    phase was written before the front half existed and is the
    implementation's."""
    return payload.get("phase") in FRONT_PHASES


@dataclass
class RunState:
    run_id: str
    state: str
    base_sha: str
    artifacts: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    front_verdicts: list = field(default_factory=list)


def project(facts) -> RunState | None:
    st: RunState | None = None
    for f in facts:
        if f.kind == EventKind.RUN_STARTED:
            st = RunState(
                run_id=f.run_id,
                state=f.payload.get("state", "queued"),
                base_sha=f.payload["base_sha"],
            )
        elif st is None:
            # A fact before the run started cannot be applied to anything.
            # Inventing a RunState here would fabricate a run that never began.
            continue
        elif f.kind == EventKind.TRANSITION:
            st.state = f.payload["to"]
        elif f.kind == EventKind.ARTIFACT_CREATED:
            st.artifacts.append(f.payload["artifact_hash"])
        elif f.kind == EventKind.REVIEW_VERDICT:
            if is_front_verdict(f.payload):
                st.front_verdicts.append(f.payload)
            else:
                st.verdicts.append(f.payload)
    return st
