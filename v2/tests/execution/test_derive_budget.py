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


#: `_net_run` puts every bounded call through `_clamp_int "$cap" 300 1 3600`,
#: and `_clamp_int` returns its DEFAULT for an out-of-range value. So a budget
#: above 3600 does not merely get truncated -- it collapses to 300 SECONDS.
NET_RUN_CEILING = 3600


def _floor(wait: int, rmax: int, rwait: int) -> int:
    return min(wait + rmax * (rwait + 20) + 120, NET_RUN_CEILING)


def test_the_default_budget_covers_the_default_inner_budgets():
    assert _budget() == _floor(1500, 2, 900) == 3460


def test_the_budget_follows_the_ci_wait():
    assert _budget(BIRCHER_CI_WAIT="3000") == _floor(3000, 2, 900)


def test_the_budget_follows_the_rerun_count():
    """The knob that made the old constant wrong: each rerun adds real time."""
    assert _budget(BIRCHER_CI_RERUN_MAX="10") == _floor(1500, 10, 900)
    assert _budget(BIRCHER_CI_RERUN_MAX="0") == _floor(1500, 0, 900)


def test_the_budget_follows_the_rerun_wait():
    assert _budget(BIRCHER_CI_RERUN_WAIT="1800") == _floor(1500, 2, 1800)


def test_the_budget_covers_the_knobs_unless_the_ceiling_forbids_it():
    """The honest property, in two halves.

    Below the ceiling the budget must cover everything the knobs can spend.
    ABOVE it, it cannot -- a single bounded call tops out at
    `NET_RUN_CEILING` -- so the budget equals the ceiling and the helper warns.
    Claiming the first half unconditionally is what produced a 300s bound.
    """
    for wait in (1, 600, 1500, 7200):
        for rmax in (0, 1, 4, 20):
            for rwait in (1, 900, 7200):
                got = _budget(BIRCHER_CI_WAIT=str(wait),
                              BIRCHER_CI_RERUN_MAX=str(rmax),
                              BIRCHER_CI_RERUN_WAIT=str(rwait))
                spendable = wait + rmax * (rwait + 20)
                if spendable + 120 <= NET_RUN_CEILING:
                    assert got >= spendable, (
                        f"budget {got}s cannot cover {spendable}s of legitimate "
                        f"work (wait={wait} rmax={rmax} rwait={rwait})")
                else:
                    assert got == NET_RUN_CEILING, (
                        f"over the ceiling the budget must BE the ceiling, got {got}")


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
    assert "below the 3460s" in r.stderr, (
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
    assert _policy(BIRCHER_CI_RERUN_MAX="abc") == (1500, 2, 900)
    assert _policy(BIRCHER_CI_WAIT="not-a-number") == (1500, 2, 900)
    assert _policy(BIRCHER_CI_RERUN_WAIT="") == (1500, 2, 900)


def test_an_out_of_range_setting_is_defaulted():
    """999 reruns would have made Python attempt far more than the budget
    bounding it allowed -- the same divergence in the other direction."""
    assert _policy(BIRCHER_CI_RERUN_MAX="999")[1] == 2
    assert _policy(BIRCHER_CI_RERUN_MAX="-1")[1] == 2
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


# --- the budget must be a bound the runner will actually honour --------------

def test_the_budget_never_exceeds_what_a_bounded_call_accepts():
    """The defect this file did not catch the first time.

    `_net_run` clamps its cap with `_clamp_int "$cap" 300 1 3600`, and
    `_clamp_int` returns its DEFAULT when the value is out of range. Handing it
    5300 therefore produced a 300-SECOND bound -- six times SHORTER than the
    1800s it replaced -- and a live muesli item escalated on it while the log
    reported the 5300s that had been asked for.

    A budget above the ceiling is not a long timeout. It is a very short one.
    """
    for env in ({}, {"BIRCHER_CI_WAIT": "7200"},
                {"BIRCHER_CI_RERUN_MAX": "20"},
                {"BIRCHER_CI_RERUN_WAIT": "7200"},
                {"BIRCHER_CI_WAIT": "7200", "BIRCHER_CI_RERUN_MAX": "20",
                 "BIRCHER_CI_RERUN_WAIT": "7200"}):
        got = _budget(**env)
        assert got <= NET_RUN_CEILING, (
            f"budget {got}s exceeds the {NET_RUN_CEILING}s ceiling for {env}; "
            "_net_run would silently collapse it to its 300s default")
        assert got >= 1, got


def test_the_ceiling_matches_what_net_run_actually_clamps_to():
    """Read from the SOURCE, not copied. If `_net_run`'s clamp changes, this
    fails rather than leaving a stale constant that reads as verified."""
    src = RUN_QUEUE.read_text()
    m = re.search(r'cap=\$\(_clamp_int "\$cap" (\d+) (\d+) (\d+)\)', src)
    assert m, "could not find _net_run's clamp; the guard below is unanchored"
    assert int(m.group(3)) == NET_RUN_CEILING, (
        f"_net_run now clamps to {m.group(3)}s but this file assumes "
        f"{NET_RUN_CEILING}s")


def test_an_explicit_override_is_clamped_to_the_ceiling_too():
    """The same collapse, one branch over -- found by cross-review.

    The computed budget was clamped to the ceiling, but an explicit
    BIRCHER_DERIVE_TIMEOUT was returned RAW. So `BIRCHER_DERIVE_TIMEOUT=5300`
    still reached `_net_run`, still exceeded 3600, and still came back as the
    300s DEFAULT -- recreating in the override path the exact production
    failure that had just been fixed in the computed path.
    """
    # An UNUSABLE override falls back to the computed budget, which is what
    # `_clamp_int` does with an out-of-range value: it returns the default it
    # was given, and the default here is the floor. The important property is
    # the one below -- nothing reachable exceeds the ceiling.
    for bad in ("5300", "99999", "abc", "0", "-1"):
        got = _budget(BIRCHER_DERIVE_TIMEOUT=bad)
        assert 1 <= got <= NET_RUN_CEILING, f"{bad!r} yielded {got}"
    # A usable value still passes through untouched.
    assert _budget(BIRCHER_DERIVE_TIMEOUT="900") == 900


def test_no_reachable_budget_value_collapses_to_the_net_run_default():
    """The property behind both instances, stated once.

    Any value `_derive_budget` can return must survive `_net_run`'s clamp
    unchanged. If it does not, the effective bound is 300 seconds and every
    caller silently gets a timeout far shorter than the one it asked for.
    """
    candidates = [{}, {"BIRCHER_DERIVE_TIMEOUT": "5300"},
                  {"BIRCHER_DERIVE_TIMEOUT": "99999"},
                  {"BIRCHER_DERIVE_TIMEOUT": "abc"},
                  {"BIRCHER_CI_WAIT": "7200", "BIRCHER_CI_RERUN_MAX": "20",
                   "BIRCHER_CI_RERUN_WAIT": "7200"}]
    for env in candidates:
        got = _budget(**env)
        assert 1 <= got <= NET_RUN_CEILING, (
            f"{env} yields {got}s, which _net_run would replace with its 300s "
            "default")


def test_the_shipped_default_fits_and_warns_about_nothing():
    """A DEFAULT MUST BE DELIVERABLE.

    With `BIRCHER_CI_RERUN_MAX=4` the advertised budget was 5300s while a
    single bounded call tops out at 3600s, so the third and fourth reruns could
    never complete: the enclosing process was killed and the item escalated
    while the knob claimed four were available. Every run logged a warning
    saying so, which is a config the system knows is wrong and ships anyway.

    This is NOT a claim that reruns 3-4 are useless -- 26 scorecard rows
    contain exactly one rerun, far too few to conclude that. It is only that
    the shipped default must be one the runner can honour.
    """
    import re as _re
    src = RUN_QUEUE.read_text()
    body = "\n".join(_re.search(rf"^{n}\(\) \{{.*?^\}}", src, _re.S | _re.M).group(0)
                     for n in ("_clamp_int", "_ci_policy", "_derive_budget"))
    r = subprocess.run(["bash", "-c", body + "\n_derive_budget\n"],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "3460"
    assert "WARN" not in r.stderr, (
        f"the shipped default must not warn about itself: {r.stderr!r}")


def test_an_operator_choosing_more_than_fits_is_still_warned():
    """The warning stays for an EXPLICIT choice. Silently narrowing what an
    operator asked for is how the 300s collapse went unnoticed."""
    import re as _re
    src = RUN_QUEUE.read_text()
    body = "\n".join(_re.search(rf"^{n}\(\) \{{.*?^\}}", src, _re.S | _re.M).group(0)
                     for n in ("_clamp_int", "_ci_policy", "_derive_budget"))
    r = subprocess.run(["bash", "-c", body + "\n_derive_budget\n"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "BIRCHER_CI_RERUN_MAX": "4"})
    assert r.stdout.strip() == str(NET_RUN_CEILING)
    assert "WARN" in r.stderr
