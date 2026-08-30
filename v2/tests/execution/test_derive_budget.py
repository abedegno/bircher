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
    for name in ("_clamp_int", "_ci_policy", "_derive_budget"):
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
    for name in ("_clamp_int", "_ci_policy", "_derive_budget"):
        out.append(re.search(rf"^{name}\(\) \{{.*?^\}}", src, re.S | re.M).group(0))
    r = subprocess.run(["bash", "-c", "\n".join(out) + "\n_derive_budget\n"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "BIRCHER_DERIVE_TIMEOUT": "1800"})
    assert r.stdout.strip() == "1800", "an explicit value must be honoured"
    assert "below the 5300s" in r.stderr, (
        "a value that cannot cover the other knobs must warn: "
        f"stderr was {r.stderr!r}")


# --- one policy, two consumers, identical numbers ---------------------------
# Fourth cross-review round. `_derive_budget` clamped these settings while the
# Python CLI re-parsed the RAW environment with a bare `int()`. One malformed
# operator value therefore produced two answers: bash computed a budget from
# the default 4, Python raised ValueError, `observe_outcome` read the empty
# result as a crash, and EVERY item escalated. The shell believed it had
# defaulted safely the whole time.

def _policy(**env) -> tuple[int, int, int]:
    """The REAL `_ci_policy`, run with `env`."""
    src = RUN_QUEUE.read_text()
    body = "\n".join(
        re.search(rf"^{n}\(\) \{{.*?^\}}", src, re.S | re.M).group(0)
        for n in ("_clamp_int", "_ci_policy"))
    r = subprocess.run(["bash", "-c", body + "\n_ci_policy\n"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **env})
    assert r.returncode == 0, r.stderr
    return tuple(int(x) for x in r.stdout.split())


def test_a_malformed_setting_is_defaulted_rather_than_propagated():
    """`abc` used to reach Python and raise ValueError."""
    assert _policy(BIRCHER_CI_RERUN_MAX="abc") == (1500, 4, 900)
    assert _policy(BIRCHER_CI_WAIT="not-a-number") == (1500, 4, 900)
    assert _policy(BIRCHER_CI_RERUN_WAIT="") == (1500, 4, 900)


def test_an_out_of_range_setting_is_defaulted():
    """999 reruns would have made Python attempt far more than the budget
    bounding it allowed -- the same divergence in the other direction."""
    assert _policy(BIRCHER_CI_RERUN_MAX="999")[1] == 4
    assert _policy(BIRCHER_CI_RERUN_MAX="-1")[1] == 4
    assert _policy(BIRCHER_CI_WAIT="99999")[0] == 1500


def test_a_valid_setting_passes_through_untouched():
    """Or the clamp would be a constant wearing a validator's clothes."""
    assert _policy(BIRCHER_CI_RERUN_MAX="7")[1] == 7
    assert _policy(BIRCHER_CI_WAIT="600")[0] == 600
    assert _policy(BIRCHER_CI_RERUN_WAIT="120")[2] == 120


def test_the_budget_is_computed_from_the_very_same_numbers():
    """The property that failed: the budget and the values handed to Python
    must come from one resolution, not two."""
    for env in ({"BIRCHER_CI_RERUN_MAX": "abc"},
                {"BIRCHER_CI_RERUN_MAX": "999"},
                {"BIRCHER_CI_WAIT": "600", "BIRCHER_CI_RERUN_MAX": "2"},
                {"BIRCHER_CI_RERUN_WAIT": "1800"}):
        wait, rmax, rwait = _policy(**env)
        assert _budget(**env) == _floor(wait, rmax, rwait), (
            f"budget and policy disagree for {env}")
