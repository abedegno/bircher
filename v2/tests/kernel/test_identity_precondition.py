"""The architectural fact the identity substrate actually rests on.

`dispatch()` takes its actor as a caller-supplied string, so identity is
unforgeable only because a model session cannot reach the kernel database.
Round 6 found that claim stated as "an assigned identity cannot be forged at
all" -- false -- and, worse, resting on two settings no docstring mentioned
and no test covered. Widen one of them and every other test stays green while
the substrate is gone.
"""

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ADAPTER = REPO_ROOT / "batch" / "lib" / "effect-adapter.sh"
BUNDLES = sorted((REPO_ROOT / "agents").glob("*/config.yaml"))


def test_there_are_bundles_to_check():
    """A glob that matches nothing proves nothing."""
    assert BUNDLES, "no agent bundles found"


def test_the_kernel_database_path_has_no_default():
    """`${BIRCHER_KERNEL_DB:?}` -- required, never defaulted. A default would
    put the database at a predictable path, and a predictable path can be
    inside a session's writable root."""
    text = ADAPTER.read_text()
    assert "BIRCHER_KERNEL_DB:?" in text, (
        "the kernel database path is no longer required; a default makes its "
        "location predictable and therefore reachable"
    )
    assert not re.search(r"BIRCHER_KERNEL_DB:-", text), (
        "BIRCHER_KERNEL_DB has acquired a default value"
    )


def test_no_bundle_grants_a_writable_root_outside_its_worktree():
    """Landlock `write_paths` is what stops a session writing the kernel
    database. `.` is the worktree; anything absolute or parent-relative widens
    the session's reach beyond it.
    """
    offenders = []
    for cfg in BUNDLES:
        doc = yaml.safe_load(cfg.read_text()) or {}
        sandbox = ((doc.get("os_env") or {}).get("sandbox") or {})
        for path in sandbox.get("write_paths") or []:
            if str(path).startswith(("/", "~")) or ".." in str(path):
                offenders.append(f"{cfg.parent.name}: {path}")
    assert not offenders, (
        "these bundles can write outside their worktree, so the kernel "
        f"database is no longer out of reach by construction: {offenders}"
    )


def test_the_v2_bundle_confines_writes_to_its_worktree():
    """The positive case, stated rather than implied by the absence of an
    offender: an empty or missing write_paths would pass the test above."""
    cfg = REPO_ROOT / "agents" / "v2_implementer" / "config.yaml"
    doc = yaml.safe_load(cfg.read_text())
    sandbox = doc["os_env"]["sandbox"]
    assert sandbox["type"] == "linux_landlock"
    assert sandbox["write_paths"] == ["."]
