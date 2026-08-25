"""The recorded boundary evidence must match the probe that produced it.

An acceptance criterion whose evidence cannot be reproduced is satisfied by
memory. This binds three things together: the probe defines the checks, the
evidence records their results, and neither may drift from the other.

The failure mode being guarded is a check quietly disappearing from the record
because it started failing.
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PROBE = REPO_ROOT / "v2" / "tools" / "capability_probe.sh"
EVIDENCE = REPO_ROOT / "docs" / "design" / "authority-boundary-evidence.md"


def probe_checks() -> set[str]:
    """Check names the probe can emit.

    TWO shapes, and the first version saw only one. Most checks are reported
    indirectly through `_expect_denied <name> ...`, which calls `_report
    "$name"` -- so a scan for `_report <literal>` found four of twelve and
    would have certified an evidence file missing eight of them.
    `test_the_extractors_find_something` is what caught that.
    """
    src = PROBE.read_text()
    direct = set(re.findall(r'_report\s+([a-z_]+)\s+(?:allow|deny)', src))
    routed = set(re.findall(r'^_expect_denied(?:_at_l4)?\s+([a-z_]+)\s', src, re.M))
    return direct | routed


def recorded() -> dict[str, str]:
    return {m.group(1): m.group(2) for m in
            re.finditer(r"^CHECK (\S+)\s+expect=\S+\s+got=\S+\s+(\w+)$",
                        EVIDENCE.read_text(), re.M)}


def test_the_extractors_find_something():
    """A parser that finds nothing reports total compliance."""
    assert len(probe_checks()) >= 10, sorted(probe_checks())
    assert len(recorded()) >= 10, sorted(recorded())


def test_every_probe_check_appears_in_the_evidence():
    """A check missing from the record is a check that may have failed."""
    missing = sorted(probe_checks() - set(recorded()))
    assert not missing, f"the probe emits these and the evidence omits them: {missing}"


def test_the_evidence_records_no_check_the_probe_cannot_emit():
    """The other direction: a recorded result for a check that no longer
    exists is evidence for something nobody runs."""
    extra = sorted(set(recorded()) - probe_checks())
    assert not extra, f"recorded but not in the probe: {extra}"


def test_every_recorded_check_passed():
    failed = {k: v for k, v in recorded().items() if v != "PASS"}
    assert not failed, f"the evidence records non-passing checks: {failed}"


def test_the_control_and_the_planted_positive_are_both_present():
    """M1-1's wording is explicit that both must be demonstrated. Denials
    without a control prove the network is dead; without a planted positive
    they prove the probe ran."""
    r = recorded()
    assert r.get("fetch_allowed") == "PASS"
    assert r.get("planted_positive") == "PASS"


def test_the_evidence_names_the_image_it_ran_against():
    """Evidence that does not say what it ran on cannot be checked for
    staleness -- which is exactly how the original result came to cover an
    image the runner no longer ran."""
    text = EVIDENCE.read_text()
    assert re.search(r"omnigent\s*\|\s*[\d.]+", text), "no omnigent version"
    assert re.search(r"Landlock ABI\s*\|\s*\d", text), "no Landlock ABI"
    assert re.search(r"bircher `[0-9a-f]{7}", text), "no bundle commit"


def test_the_limits_are_stated():
    """A capability probe that does not say what it fails to cover reads as a
    complete boundary proof."""
    text = EVIDENCE.read_text().lower()
    for phrase in ("tcp only", "does not prove", "not an enumerable set"):
        assert phrase in text, f"the evidence does not state: {phrase!r}"
