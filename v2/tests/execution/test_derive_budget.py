"""`_derive_budget` must cover the work it bounds.

A fixed 1800s cap sat over an inner budget of up to
BIRCHER_CI_WAIT (1500) + BIRCHER_CI_RERUN_MAX (4) x (BIRCHER_CI_RERUN_WAIT
(900) + 20s settle) = 5180s. A healthy infra recovery was killed mid-rerun and
surfaced as a crashed derivation, and three documented knobs accepted values
that could never be spent.

Driven by EXTRACTING the real function from run-queue.sh and running it, not
by grepping its text: the defect is arithmetic, and a substring cannot see
arithmetic. Mutating `floor` to a constant 1800 left all 916 other tests
passing -- these are the tests that fail.
"""
import pathlib
import re
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"


def _budget(**env) -> int:
    """Run the REAL `_derive_budget` with `env`, return its answer."""
    src = RUN_QUEUE.read_text()
    out = []
    for name in ("_clamp_int", "_derive_budget"):
        m = re.search(rf"^{name}\(\) \{{.*?^\}}", src, re.S | re.M)
        assert m, f"{name} not found in run-queue.sh"
        out.append(m.group(0))
    script = "\n".join(out) + "\n_derive_budget\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **env})
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def _floor(wait: int, rmax: int, rwait: int) -> int:
    return wait + rmax * (rwait + 20) + 120


def test_the_default_budget_covers_the_default_inner_budgets():
    assert _budget() == _floor(1500, 4, 900) == 5300


def test_the_budget_follows_the_ci_wait():
    assert _budget(BIRCHER_CI_WAIT="3000") == _floor(3000, 4, 900)


def test_the_budget_follows_the_rerun_count():
    """The knob that made the old constant wrong: each rerun adds real time."""
    assert _budget(BIRCHER_CI_RERUN_MAX="10") == _floor(1500, 10, 900)
    assert _budget(BIRCHER_CI_RERUN_MAX="0") == _floor(1500, 0, 900)


def test_the_budget_follows_the_rerun_wait():
    assert _budget(BIRCHER_CI_RERUN_WAIT="1800") == _floor(1500, 4, 1800)


def test_the_budget_is_never_below_what_the_knobs_can_spend():
    """The property, stated directly, across the range each knob allows."""
    for wait in (1, 600, 1500, 7200):
        for rmax in (0, 1, 4, 20):
            for rwait in (1, 900, 7200):
                got = _budget(BIRCHER_CI_WAIT=str(wait),
                              BIRCHER_CI_RERUN_MAX=str(rmax),
                              BIRCHER_CI_RERUN_WAIT=str(rwait))
                spendable = wait + rmax * (rwait + 20)
                assert got >= spendable, (
                    f"budget {got}s cannot cover {spendable}s of legitimate "
                    f"work (wait={wait} rmax={rmax} rwait={rwait})")


def test_an_explicit_operator_value_is_honoured_but_warned_about():
    """Not overridden -- the operator may mean it -- but never silently."""
    src = RUN_QUEUE.read_text()
    out = []
    for name in ("_clamp_int", "_derive_budget"):
        out.append(re.search(rf"^{name}\(\) \{{.*?^\}}", src, re.S | re.M).group(0))
    r = subprocess.run(["bash", "-c", "\n".join(out) + "\n_derive_budget\n"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "BIRCHER_DERIVE_TIMEOUT": "1800"})
    assert r.stdout.strip() == "1800", "an explicit value must be honoured"
    assert "below the 5300s" in r.stderr, (
        "a value that cannot cover the other knobs must warn: "
        f"stderr was {r.stderr!r}")
