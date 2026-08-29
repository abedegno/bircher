"""What mode a wave actually launches in.

Untested until 2026-08-29, and the gap was not cosmetic: NOTHING in the
deployment set `BIRCHER_EFFECT_MODE` -- not the runner's environment, not a
scheduled task, not an env file -- so the adapter's `deny` default applied and
a launched wave would have refused every effect and done nothing. An operator
reads that as a broken runner, not as a policy.

These tests execute the real `launch.sh` with a stub `run-queue.sh` that
records the environment it was handed. Structural greps would pass on a script
that computed the mode and forgot to pass it -- which is precisely the bug
worth catching, since the variable's absence is silent.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LAUNCH = REPO_ROOT / "batch" / "launch.sh"


@pytest.fixture
def fake_repo(tmp_path):
    """A tree shaped like the repo, with run-queue.sh replaced by a recorder."""
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "launch.sh").write_text(LAUNCH.read_text())
    (batch / "launch.sh").chmod(0o755)
    out = tmp_path / "env.txt"
    (batch / "run-queue.sh").write_text(
        f'#!/usr/bin/env bash\n'
        f'{{ echo "MODE=${{BIRCHER_EFFECT_MODE:-<unset>}}"\n'
        f'  echo "SOURCE=${{BIRCHER_SOURCE:-<unset>}}"\n'
        f'  echo "ARGS=$*"; }} > "{out}"\n'
        f'echo "stub runner started"\n'
    )
    (batch / "run-queue.sh").chmod(0o755)
    return tmp_path, out


def _launch(fake_repo, *args):
    tmp, out = fake_repo
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIRCHER_")}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    r = subprocess.run(["bash", str(tmp / "batch" / "launch.sh"),
                        "--foreground", *args],
                       capture_output=True, text=True, cwd=str(tmp), env=env)
    recorded = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    ) if out.exists() else {}
    return r, recorded


def test_a_wave_launches_in_kernel_mode_by_default(fake_repo):
    """The whole point. Unset used to mean `deny`, which means an inert run."""
    _r, rec = _launch(fake_repo)
    assert rec.get("MODE") == "kernel", rec


def test_the_mode_actually_REACHES_the_runner(fake_repo):
    """Computing it and forgetting to export it would leave `deny` in force
    while the banner claimed otherwise -- a silent failure, since the variable's
    absence produces no error."""
    _r, rec = _launch(fake_repo)
    assert rec.get("MODE") not in (None, "<unset>"), rec


def test_an_explicit_env_var_still_wins(fake_repo):
    tmp, out = fake_repo
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIRCHER_")}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env["BIRCHER_EFFECT_MODE"] = "legacy"
    subprocess.run(["bash", str(tmp / "batch" / "launch.sh"), "--foreground"],
                   capture_output=True, text=True, cwd=str(tmp), env=env)
    rec = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    assert rec.get("MODE") == "legacy", rec


def test_the_mode_flag_overrides_the_default(fake_repo):
    """`--mode legacy` exists so bisecting a suspected kernel fault does not
    need an env var nobody remembers."""
    _r, rec = _launch(fake_repo, "--mode", "legacy")
    assert rec.get("MODE") == "legacy", rec


def test_the_mode_flag_is_not_passed_through_as_a_runner_argument(fake_repo):
    """`--mode` is consumed by launch.sh. Leaking it into run-queue.sh's argv
    would make it an unrecognised argument on the runner's own dispatch."""
    _r, rec = _launch(fake_repo, "--mode", "legacy")
    assert "--mode" not in rec.get("ARGS", ""), rec


def test_unrecognised_arguments_still_pass_through(fake_repo):
    """The documented contract: anything launch.sh does not claim goes to the
    runner untouched."""
    _r, rec = _launch(fake_repo, "--recover-pr", "i99")
    assert "--recover-pr" in rec.get("ARGS", ""), rec
    assert "i99" in rec.get("ARGS", ""), rec


@pytest.mark.skipif(shutil.which("setsid") is None,
                    reason="the detached path needs setsid; absent on macOS, "
                           "present on the Linux runner where waves actually launch")
def test_the_banner_states_the_mode(fake_repo):
    """An operator must never have to infer which mode a wave is in.

    Exercises the DETACHED path, which is the one a real wave uses -- the
    foreground path above is a debugging affordance. Skipped rather than
    adapted: a version that tested the foreground banner would pass on macOS
    while saying nothing about the launch that actually happens.
    """
    tmp, _out = fake_repo
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIRCHER_")}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    r = subprocess.run(["bash", str(tmp / "batch" / "launch.sh")],
                       capture_output=True, text=True, cwd=str(tmp), env=env)
    assert "effect-mode=kernel" in r.stdout, (r.stdout, r.stderr)
