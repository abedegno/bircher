"""Criterion 2: every effect branch is driven and the adapter denies it.

Coverage evidence, NOT the authority-boundary proof -- that is M1-1's
end-to-end capability test. What is shown here is that the seam behaves as
specified when exercised; what it cannot show is that a model session has no
other route to the same mutation, because a shell-level test says nothing
about a separate process with an absolute path, another HTTP client or a
language runtime.
"""

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ADAPTER = REPO_ROOT / "batch" / "lib" / "effect-adapter.sh"

#: The six classes the coordinator can request. `credential_lifecycle` and
#: `session_control` are not routed from bash and are covered in
#: test_provider_control.py.
CLASSES = ["ref_update", "pull_request", "merge", "status_check", "comment",
           "issue_or_label", "revert_or_recovery"]

RC_DENIED, RC_BADMODE = 87, 2


def _run(script: str, mode: str | None = None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if mode is not None:
        env["BIRCHER_EFFECT_MODE"] = mode
    return subprocess.run(
        ["bash", "-c", f'. "{ADAPTER}"\n{script}'],
        capture_output=True, text=True, env=env,
    )


@pytest.mark.parametrize("cls", CLASSES)
def test_deny_mode_refuses_every_effect_class(cls):
    r = _run(f'_effect {cls} key - true', mode="deny")
    assert r.returncode == RC_DENIED, f"{cls} was not denied (rc={r.returncode})"


@pytest.mark.parametrize("cls", CLASSES)
def test_denial_does_not_execute_the_command(cls, tmp_path):
    """A denial that still runs the command is not a denial. The witness is a
    filesystem side effect, not the return code -- a refusal reported after
    the mutation landed reports the same code as one reported before it."""
    witness = tmp_path / f"ran-{cls}"
    _run(f'_effect {cls} key - touch "{witness}"', mode="deny")
    assert not witness.exists(), f"{cls}: the command executed despite denial"


def test_an_unset_mode_fails_closed():
    """An unset variable must not silently restore v1 authority."""
    r = _run('_effect comment k - true')
    assert r.returncode == RC_DENIED, "unset BIRCHER_EFFECT_MODE did not fail closed"


def test_an_unset_mode_does_not_execute(tmp_path):
    witness = tmp_path / "ran"
    _run(f'_effect comment k - touch "{witness}"')
    assert not witness.exists()


def test_an_unknown_mode_is_refused_rather_than_defaulting():
    r = _run('_effect comment k - true', mode="yolo")
    assert r.returncode == RC_BADMODE


def test_an_unknown_mode_does_not_execute(tmp_path):
    """The dangerous half of an unknown mode: falling through to `"$@"`."""
    witness = tmp_path / "ran"
    _run(f'_effect comment k - touch "{witness}"', mode="typo")
    assert not witness.exists()


def test_legacy_mode_executes_the_command(tmp_path):
    """The control. Without it, an adapter that refused everything in every
    mode would pass every test above."""
    witness = tmp_path / "ran"
    r = _run(f'_effect comment k - touch "{witness}"', mode="legacy")
    assert r.returncode == 0, r.stderr
    assert witness.exists()


def test_kernel_mode_refuses_to_run_without_a_run_id():
    """`${VAR:?}` on every kernel-mode variable: an unset run id must abort,
    not perform the effect against a default."""
    r = _run('_effect comment k - true', mode="kernel")
    assert r.returncode != 0
    assert "BIRCHER_KERNEL_DB" in r.stderr or "BIRCHER_RUN_ID" in r.stderr


def test_kernel_mode_does_not_execute_without_a_run_id(tmp_path):
    witness = tmp_path / "ran"
    _run(f'_effect comment k - touch "{witness}"', mode="kernel")
    assert not witness.exists()


def test_a_cap_reaches_net_run_in_legacy_mode(tmp_path):
    """The bound is the scar. A cap that is not `-` must be handed to
    _net_run, or routing the call silently unbounds it."""
    log = tmp_path / "cap"
    r = _run(f'_net_run() {{ printf "%s" "$1" > "{log}"; shift; "$@"; }}\n'
             f'_effect ref_update k 42 true', mode="legacy")
    assert r.returncode == 0, r.stderr
    assert log.read_text() == "42"


def test_a_dash_cap_bypasses_net_run(tmp_path):
    """`-` means unbounded, which is what v1 does at the sites never wrapped.
    Silently substituting a default would hide them."""
    log = tmp_path / "cap"
    r = _run(f'_net_run() {{ printf "called" > "{log}"; shift; "$@"; }}\n'
             f'_effect ref_update k - true', mode="legacy")
    assert r.returncode == 0, r.stderr
    assert not log.exists()


# --- what legacy mode costs, pinned ------------------------------------------

def test_legacy_mode_bypasses_the_entire_kernel_path(tmp_path):
    """Legacy runs the command directly: no contract check, no executable
    resolution, no journal, no authorization.

    That is its PURPOSE -- it exists to bisect against v1 behaviour -- but the
    cost was written down nowhere and asserted nowhere, so a reader could not
    tell a deliberate escape hatch from an oversight. This test pins it, so
    the day someone narrows or removes legacy, the change is visible.

    The witness is a command NO contract admits: `sh -c`, which is not even in
    the launcher's tool allowlist.
    """
    witness = tmp_path / "ran"
    r = _run(f'_effect comment k - sh -c "touch {witness}"', mode="legacy")
    assert r.returncode == 0, r.stderr
    assert witness.exists(), "legacy did not execute -- this test has stopped pinning anything"


def test_kernel_mode_would_refuse_that_same_command():
    """The other half. Without this, the test above documents a bypass without
    showing there is anything to bypass."""
    from kernel.contract import ContractViolation, check
    from kernel.effects import EffectClass

    with pytest.raises(ContractViolation):
        check(EffectClass.COMMENT, ["sh", "-c", "touch /tmp/x"])


def test_the_self_test_runs_in_legacy_mode_deliberately():
    """`run-queue.sh --self-test` sets BIRCHER_EFFECT_MODE=legacy, so a green
    self-test says nothing about the kernel boundary. Pinned because the two
    are easily confused: one exercises v1 orchestration, the other the v2
    authority path."""
    text = (REPO_ROOT / "batch" / "run-queue.sh").read_text()
    assert 'BIRCHER_EFFECT_MODE="${BIRCHER_EFFECT_MODE:-legacy}"' in text
