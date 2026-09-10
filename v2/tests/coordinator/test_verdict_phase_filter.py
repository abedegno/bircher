"""Every back-half consumer of `review_verdict` reads the implementation
phase only.

Since Task 7 the fact carries `phase` (`spec`, `plan` or `implementation`).
The repair loop's allowance (`observe.revisions_used`), its durability check
(`observe.revision_confirmed`), the recovery decision (`recover.decide`) and
the projection must all ignore a spec- or plan-phase verdict -- a spec-phase
FAIL must not spend the implementation's revision allowance, and a stale spec
verdict must never be read as the newest word on an implementation review it
was never about. `kernel.projection.is_front_verdict` is the one predicate;
this exercises it through every consumer, not just at its own definition.
"""

import pytest

from coordinator.observe import revisions_used
from coordinator.recover import decide
from kernel.projection import is_front_verdict, project


class _F:
    def __init__(self, kind, payload, seq=0):
        self.kind, self.payload, self.seq = kind, payload, seq
        self.actor, self.id, self.run_id = "x", f"f{seq}", "r"
        self.schema_version = self.mechanism_version = 1
        self.causal_command_id = None
        self.observed_at_us = seq


def _journal():
    """A spec-phase FAIL followed by an implementation PASS (spec §8)."""
    return [
        _F("run_started", {"base_sha": "0" * 40, "base_repo": "o/r", "state": "queued"}, 1),
        _F("review_verdict", {"verdict": "request_revision", "phase": "spec", "epoch": 0,
                              "artifact_hash": "a" * 64, "ruling": "review_ruling",
                              "reviewer_identity": "codex", "binding_hash": "b" * 64}, 2),
        _F("transition_performed", {"to": "queued", "via": "record_review"}, 3),
        _F("review_verdict", {"verdict": "accept", "phase": "implementation", "epoch": 0,
                              "artifact_hash": "c" * 64, "ruling": "review_ruling",
                              "reviewer_identity": "codex", "binding_hash": "d" * 64}, 4),
        _F("transition_performed", {"to": "reviewing", "via": "record_review"}, 5),
    ]


def test_revisions_used_counts_implementation_verdicts_only():
    assert revisions_used(_journal()) == 0
    facts = _journal() + [_F("review_verdict", {"verdict": "request_revision", "phase": "implementation"}, 6)]
    assert revisions_used(facts) == 1


@pytest.mark.parametrize("phase", ["slices", "spec", "plan"])
def test_a_front_verdict_of_any_phase_is_skipped(phase):
    """§8: `is_front_verdict` true for a `slices` verdict, and
    `revisions_used` not spending a repair round on one. A one-piece run that
    needed two slice-plan rounds must not arrive in its back half with its
    repair allowance already spent (Task 3 rule (g))."""
    assert is_front_verdict({"phase": phase})
    assert revisions_used([_F("review_verdict",
                              {"verdict": "request_revision", "phase": phase}, 1)]) == 0


def test_a_v1_verdict_without_phase_still_counts():
    """Journals written before this plan carry no phase: they are back-half."""
    assert revisions_used([_F("review_verdict", {"verdict": "request_revision"}, 1)]) == 1


def test_projection_separates_front_verdicts():
    st = project(_journal())
    assert [v["phase"] for v in st.verdicts] == ["implementation"]
    assert [v["phase"] for v in st.front_verdicts] == ["spec"]


def test_recover_reads_the_implementation_verdict():
    """`decide`'s `Action` is `(do, why)` (`recover.py:30`), not `(kind, reason)`.

    On the shared journal the implementation accept is already the newest
    `review_verdict` fact by sequence, so filtering does not change which
    verdict `decide` calls "latest" here -- the filter's own effect is
    covered by the case below, where the ONLY verdict in the journal is the
    stale spec FAIL. Without `is_front_verdict` (`recover.py:170`), `decide`
    reads that FAIL as an implementation `request_revision` and dispatches an
    implementer for a run that has not even reached implementation review;
    with the filter it correctly finds no implementation verdict at all.
    """
    action = decide(_journal(), current_binding_hash="d" * 64)
    assert action.do == "merge"
    assert "spec" not in action.why

    spec_only = [
        _F("run_started", {"base_sha": "0" * 40, "base_repo": "o/r", "state": "spec_submitted"}, 1),
        _F("review_verdict", {"verdict": "request_revision", "phase": "spec", "epoch": 0,
                              "artifact_hash": "a" * 64, "ruling": "review_ruling",
                              "reviewer_identity": "codex", "binding_hash": "b" * 64}, 2),
        _F("transition_performed", {"to": "queued", "via": "record_review"}, 3),
    ]
    stale = decide(spec_only, current_binding_hash="d" * 64)
    assert stale.do != "dispatch_implementer"
