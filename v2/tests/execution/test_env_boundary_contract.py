"""Every environment variable the coordinator reads must actually cross the
bash/Python boundary.

THE CLASS, not an instance. Five defects in this migration were the same
shape: `run-queue.sh` ASSIGNS a variable (`VAR=value`) rather than EXPORTING
it, a subprocess therefore sees nothing, and Python silently uses its default.
Four were found one at a time by live runs -- `RECOVERY_REVIEWER` (which ended
cross-vendor independence by defaulting the reviewer to the implementer's own
vendor), `REPO`, `SERVER`, `BUNDLE_DIR`. The fifth, `MAIN_CI_POLL_INTERVAL`,
was found by a cross-vendor review on 2026-08-30 -- AFTER a commit message had
claimed "every shell global is passed explicitly, not inherited" and after a
field-by-field live acceptance found nine of ten scorecard fields identical.

None of these is visible to a unit test that injects its dependencies, and the
happy-path acceptance could not see them either: every one of them only changes
behaviour on a slow, failing or retried path.

So the guard is an ENUMERATION with named, reasoned exemptions rather than N
per-variable assertions: it fails when someone adds variable N+1.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SHELL = [REPO_ROOT / "batch" / "run-queue.sh"] + \
        sorted((REPO_ROOT / "batch" / "lib").glob("*.sh"))
PY_DIRS = [REPO_ROOT / "v2" / "coordinator", REPO_ROOT / "v2" / "kernel"]

#: How each variable the coordinator reads legitimately reaches it.
#:
#: `operator`  - never assigned in shell; both languages read the SAME exported
#:               operator environment, or both fall back to the same default.
#: `self`      - the coordinator sets it itself (not a boundary crossing).
#:
#: A variable that is ASSIGNED in shell without `export` belongs in NEITHER
#: category: it cannot reach Python, and the fix is to pass it as an argument
#: (as `--repo`, `--server`, `--bundle-dir`, `--reviewer` and `--poll-interval`
#: all are) rather than to add an entry here.
CONTRACT = {
    "BIRCHER_CI_RERUN_MAX": "operator",
    "BIRCHER_CI_RERUN_WAIT": "operator",
    "BIRCHER_CI_WAIT": "operator",
    "BIRCHER_CI_IGNORE_CHECKS": "operator",
    "BIRCHER_KERNEL_MODE": "operator",
    "BIRCHER_REVIEW_LOG": "operator",
    "MAIN_BRANCH": "operator",
    "BIRCHER_GH_REPO": "self",
}

_READ = re.compile(
    r'(?:environ\.get|environ\[|getenv|_int)\(\s*["\']([A-Z][A-Z0-9_]*)["\']')


def _vars_python_reads() -> set[str]:
    found = set()
    for d in PY_DIRS:
        for f in d.rglob("*.py"):
            found |= set(_READ.findall(f.read_text()))
    return found


def _shell_text() -> str:
    return "\n".join(p.read_text() for p in SHELL if p.exists())


def _assigned_without_export(var: str, text: str) -> bool:
    """True if shell sets this name but never exports it.

    Deliberately ignores `${VAR:-default}` reads: those are the operator
    pattern, where shell consumes the same environment Python does.
    """
    assigned = re.search(rf"^[ \t]*{var}=", text, re.M)
    exported = re.search(rf"^[ \t]*export[ \t]+(?:[A-Za-z_]+[ \t]+)*{var}\b",
                         text, re.M)
    return bool(assigned) and not bool(exported)


def test_no_variable_python_reads_is_assigned_but_unexported():
    """The defect itself: shell sets it, Python cannot see it, nothing fails."""
    text = _shell_text()
    broken = sorted(v for v in _vars_python_reads()
                    if _assigned_without_export(v, text))
    assert not broken, (
        "these are ASSIGNED in shell without `export`, so the coordinator "
        "subprocess reads its own default and the shell's value is silently "
        f"discarded -- pass them as CLI arguments instead: {broken}")


def test_every_variable_python_reads_is_declared_in_the_contract():
    """Direction one: a NEW environment read must be classified."""
    undeclared = sorted(_vars_python_reads() - set(CONTRACT))
    assert not undeclared, (
        "the coordinator reads these from the environment but the contract "
        "does not say how they cross the boundary -- classify each as "
        f"'operator' or 'self', or pass it as an argument: {undeclared}")


def test_the_contract_has_not_rotted():
    """Direction two: an entry whose read has gone must be dropped.

    Without this the table keeps blessing names nothing reads, and the next
    variable to take one of those names inherits a classification nobody
    granted it.
    """
    stale = sorted(set(CONTRACT) - _vars_python_reads())
    assert not stale, f"declared but no longer read; drop them: {stale}"


def test_the_enumeration_can_still_see():
    """A guard that finds nothing is indistinguishable from a clean tree."""
    assert len(_vars_python_reads()) >= 8
