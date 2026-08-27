"""The halt's two halves, reachable from outside Python.

`kernel.effects.reconcile` could always resolve a halt; nothing outside Python
could ASK it to. A live merge on abedegno/muesli came back uncertain, the run
halted exactly as designed, and the coordinator had no route to the
resolution -- so the run could not be advanced by any path it had. The
capability existed and the door did not, which is a different defect from a
missing capability and reads identically from the outside.
"""
import json

import pytest

from conftest import valid_argv
from kernel.cli import main
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, UncertainEffect, is_halted, perform
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "k.db"
    s = Store.open(p, clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return str(p)


def _halt(db, key="m1", cls=EffectClass.STATUS_CHECK):
    """Drive a real uncertain effect: the executor never answers."""
    s = Store.open(db)
    g = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    with pytest.raises(UncertainEffect):
        perform(s, "r", g, cls, key, valid_argv(cls),
                lambda *a: (_ for _ in ()).throw(TimeoutError("no answer")))
    return s


def _pending(db, capsys):
    assert main(["pending", "--db", db, "--run-id", "r"]) == 0
    return json.loads(capsys.readouterr().out)


def test_pending_reports_the_halt_and_what_to_look_at(db, capsys):
    _halt(db)
    out = _pending(db, capsys)
    assert out["halted"] is True
    assert [e["idempotency_key"] for e in out["pending"]] == ["m1"]
    assert out["pending"][0]["effect_class"] == EffectClass.STATUS_CHECK


def test_pending_reports_the_version_a_resolution_must_be_derived_from(db, capsys):
    _halt(db)
    out = _pending(db, capsys)
    assert out["version"] == Store.open(db).run_version("r")


def test_reconcile_resolves_the_halt(db, capsys):
    _halt(db)
    v = _pending(db, capsys)["version"]
    assert main(["reconcile", "--db", db, "--run-id", "r",
                 "--idempotency-key", "m1", "--resolution", "PR was not merged",
                 "--expected-version", str(v)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is False and out["pending"] == []
    assert is_halted(Store.open(db), "r") is False


def test_a_resolution_derived_from_a_moved_version_is_refused(db, capsys):
    """The CAS is the whole point: a resolution reflects an observation made at
    a moment, and the run moving since means the observation may no longer
    describe it."""
    _halt(db)
    v = _pending(db, capsys)["version"]
    rc = main(["reconcile", "--db", db, "--run-id", "r",
               "--idempotency-key", "m1", "--resolution", "stale",
               "--expected-version", str(v - 1)])
    assert rc != 0
    assert is_halted(Store.open(db), "r") is True, "a refused resolution unhalted the run"


def test_reconciling_something_that_was_never_uncertain_is_refused(db, capsys):
    """mark_effect is a bare UPDATE by key, so an unknown key would otherwise
    succeed silently, consume the version and clear a halt for something that
    was never in doubt."""
    _halt(db)
    v = _pending(db, capsys)["version"]
    rc = main(["reconcile", "--db", db, "--run-id", "r",
               "--idempotency-key", "no-such-effect", "--resolution", "x",
               "--expected-version", str(v)])
    assert rc != 0
    assert is_halted(Store.open(db), "r") is True


def test_the_halt_holds_while_a_SECOND_effect_is_still_unresolved(db, capsys):
    """Unhalting per-resolution would resume a run that still has an unknown
    mutation outstanding -- the exact state the halt exists to prevent."""
    s = _halt(db, key="m1")
    g = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    from kernel.effects import _perform_unhalted
    with pytest.raises(UncertainEffect):
        _perform_unhalted(s, "r", g, EffectClass.COMMENT, "c1",
                          valid_argv(EffectClass.COMMENT),
                          lambda *a: (_ for _ in ()).throw(TimeoutError("no answer")))

    v = _pending(db, capsys)["version"]
    assert main(["reconcile", "--db", db, "--run-id", "r",
                 "--idempotency-key", "m1", "--resolution", "not merged",
                 "--expected-version", str(v)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is True, "the halt lifted with c1 still unresolved"
    assert [e["idempotency_key"] for e in out["pending"]] == ["c1"]
