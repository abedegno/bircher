"""Every dependency `live_deps` wires must be CALLABLE as `derive` calls it.

THE GAP THIS CLOSES. `derive`'s unit tests inject their own `Deps`, which is
what makes them fast and focused -- and it means NOTHING exercised the objects
`live_deps` actually builds. `wait_ci` was wired without `gh=`, which `poll`
requires and does not default, so it raised TypeError on the one branch it
exists for: a PENDING CI. In production that turns "wait for CI" into a
crashed derivation, an empty tuple, and an escalation for every item whose
checks had not settled.

Nothing saw it. 916 tests passed. The live acceptance passed too, because that
PR's CI had already gone green before the derivation ran, so the branch was
never entered -- "a branch nothing ever enters" looking well tested.

These tests CALL the wired callables rather than inspecting them: the fault
was a missing keyword in an INNER call, which no signature check on the outer
lambda can see.
"""
import pytest

import coordinator.wiring as wiring


@pytest.fixture
def deps(monkeypatch):
    """`live_deps` with only the outside world stubbed."""
    monkeypatch.setattr(wiring, "_gh", lambda argv: "build|pass")
    monkeypatch.setattr(wiring.ci_mod, "required_contexts",
                        lambda repo, **kw: "build")
    monkeypatch.setattr(wiring, "ci_history", lambda repo, br: [])
    return wiring.live_deps("item1", repo="o/r", reviewer="codex",
                            server="http://s", bundle_dir=".", poll_interval=1)


@pytest.mark.parametrize("name,args", [
    ("checks", ("7",)),
    ("head_of", ("7",)),
    ("branch_of", ("7",)),
    ("pr_state", ("7",)),
    ("wait_ci", ("7",)),          # the one that was broken
    ("failure_kind", ("7",)),
    ("history", ("main",)),
    ("discover_by_code", ("i1",)),
    ("discover_by_issue", ("12",)),
])
def test_the_wired_dependency_can_be_called(deps, name, args):
    """A TypeError here is a wiring fault, not a test fault.

    `effect` and `review` are excluded ON PURPOSE and not silently: calling
    them performs a real mutation and spawns a reviewer subprocess. They are
    covered by the routed-effect tests instead.
    """
    fn = getattr(deps, name)
    try:
        fn(*args)
    except TypeError as exc:
        pytest.fail(f"live_deps wired `{name}` so it cannot be called: {exc}")
    except Exception:
        # Any OTHER exception is this stub's business, not a wiring fault.
        pass


def test_wait_ci_returns_a_real_verdict_through_the_wiring():
    """Not merely callable -- it must answer.

    A `pass` here and a TypeError there are both "did not return green", so
    callability alone would be satisfied by a function that always failed.
    """
    import coordinator.wiring as w

    class _P:
        def __init__(self): self.n = 0
        def __call__(self, argv):
            self.n += 1
            return "build|pending" if self.n == 1 else "build|pass"

    gh = _P()
    orig_gh, orig_req = w._gh, w.ci_mod.required_contexts
    try:
        w._gh = gh
        w.ci_mod.required_contexts = lambda repo, **kw: "build"
        d = w.live_deps("i", repo="o/r", reviewer="c", server="s",
                        bundle_dir=".", poll_interval=0)
        assert d.wait_ci("7") == "green"
        assert gh.n >= 2, "it must actually have polled, not answered once"
    finally:
        w._gh, w.ci_mod.required_contexts = orig_gh, orig_req
