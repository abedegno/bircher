"""Every direct `python -m coordinator.cli` call in the batch scripts sets
PYTHONPATH, or goes through `_coordinator`, which does.

Found live (2026-09-26): three calls bypassed `_coordinator` without it. From
the wave's working directory the module does not import, the call exits 1,
and each site hid that behind `2>/dev/null` with an empty fallback. Two of
them were the back-half park's read-back and its notice text, so every
back-half park concluded it had not been recorded and posted no notice on
the issue; the third blanked the reason on every front-half parked row.
"""

import pathlib

BATCH = pathlib.Path(__file__).resolve().parents[3] / "batch"


def _logical_commands():
    for path in sorted(BATCH.rglob("*.sh")):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("#") or "-m coordinator.cli" not in line:
                continue
            start = i
            while start > 0 and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            yield path, i + 1, " ".join(l.strip() for l in lines[start:i + 1])


def test_every_direct_coordinator_call_sets_pythonpath():
    bare = [f"{p.relative_to(BATCH.parent)}:{n}" for p, n, cmd in _logical_commands()
            if "PYTHONPATH=" not in cmd]
    assert not bare, (
        "these call `python -m coordinator.cli` without PYTHONPATH; from the wave's "
        f"cwd the module does not import. Route them through `_coordinator`: {bare}")


def test_the_guard_can_see_direct_calls():
    # A guard that matched nothing would pass vacuously.
    assert sum(1 for _ in _logical_commands()) >= 5
