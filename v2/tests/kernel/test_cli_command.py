"""The kernel's command layer gains its first caller outside the tests."""
import json
import pathlib

import pytest

from conftest import valid_argv
from kernel.artifacts import put_artifact
from kernel.cli import RC_REFUSED, main
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, UncertainEffect, perform
from kernel.ids import Clock
from kernel.store import Store


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "k.db"
    s = Store.open(str(p), clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return str(p)


def _gen(db, actor, role):
    s = Store.open(db, clock=Clock(start_us=1))
    return dispatch(s, "r", actor=actor, role=role).generation


def test_a_command_is_accepted(db, capsys):
    s = Store.open(db, clock=Clock(start_us=1))
    spec = put_artifact(s, b"# spec")
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "submit_spec",
               "--payload-json", json.dumps({"spec_sha256": spec})])
    assert rc == 0
    assert Store.open(db, clock=Clock(start_us=1)).run_state("r") == "specified"


def test_an_illegal_command_is_refused(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "request_merge", "--payload-json", "{}"])
    assert rc == 87


def test_an_illegal_command_under_the_real_shadow_default_does_not_claim_success(
    db, monkeypatch, capsys
):
    """The test above passes only because tests/kernel/conftest.py forces
    BIRCHER_KERNEL_MODE=enforce for this whole suite -- it says nothing about
    the mode the CLI actually runs under by default. Clear that override to
    reach the real default (unset -> shadow) and prove the CLI does not print
    "accepted" or exit 0 for a command shadow mode refused: submit() returns
    Result(accepted=False, ...) instead of raising, and _do_command must read
    that field rather than treat "did not raise" as "succeeded".
    """
    monkeypatch.delenv("BIRCHER_KERNEL_MODE", raising=False)
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "request_merge", "--payload-json", "{}"])
    assert rc == RC_REFUSED
    out = capsys.readouterr()
    assert "accepted" not in out.out
    assert "accepted" not in out.err


def test_a_payload_that_is_not_json_is_a_usage_error(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "submit_spec", "--payload-json", "not json"])
    assert rc == 2


def test_an_unknown_command_name_is_a_usage_error(db):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "no_such_command", "--payload-json", "{}"])
    assert rc == 2


def test_the_idempotency_key_defaults_to_run_name_generation(db):
    """A retry of the same stage must replay, not double-record. Without a
    stable default every retry is a new command."""
    s = Store.open(db, clock=Clock(start_us=1))
    spec = put_artifact(s, b"# spec")
    g = _gen(db, "claude", Role.IMPLEMENTER)
    args = ["command", "--db", db, "--run-id", "r", "--generation", str(g),
            "--name", "submit_spec", "--payload-json", json.dumps({"spec_sha256": spec})]
    assert main(args) == 0
    assert main(args) == 0
    s = Store.open(db, clock=Clock(start_us=1))
    accepted = [f for f in s.facts_for("r")
                if f.kind == "command_accepted"
                and f.payload.get("command_name") == "submit_spec"]
    assert len(accepted) == 1


def test_the_effect_subcommand_still_works(db):
    """The subcommand split must not have cost the effect path its route.

    This only proves `main()` still dispatches "effect" to `_do_effect`
    without an argparse usage error -- it calls `kernel.cli.main()` directly
    and never touches `batch/lib/effect-adapter.sh`, so it says nothing about
    the adapter's own invocation. That was checked separately, outside this
    suite: sourcing the adapter with `BIRCHER_EFFECT_MODE=kernel` and a stub
    `python3` confirms the argv it builds still reads `-m kernel.cli effect
    --db ... -- <argv>`, with `effect` landing right after `kernel.cli` and
    every other argument unchanged.
    """
    g = _gen(db, "claude", Role.IMPLEMENTER)
    rc = main(["effect", "--db", db, "--run-id", "r", "--generation", str(g),
               "--class", "comment", "--idempotency-key", "k",
               "--", "gh", "pr", "comment", "1", "--repo", "o/r",
               "--body", "hi"])
    # Refused or failed at execution is fine here; what must not happen is a
    # usage error, which would mean "effect" no longer routes to _do_effect.
    assert rc != 2


def test_a_command_against_a_halted_run_returns_the_failed_exit_code(db):
    """A halted run is an ordinary reachable state -- any failed effect halts
    its run unconditionally, on the first execution failure (kernel.effects).
    `submit()` raises a bare RuntimeError for it (kernel.commands), and that
    used to escape `_do_command` as an uncaught traceback: a fifth,
    undocumented exit code alongside 0/2/87/88.

    The halt is driven the way production reaches it -- a real failing
    effect through `perform()` -- not by writing the reconciliation row
    directly, so this proves the exit code for the state the kernel actually
    produces rather than one this test fabricated.
    """
    g = _gen(db, "claude", Role.IMPLEMENTER)
    s = Store.open(db, clock=Clock(start_us=1))

    def _boom(effect_class, intent, idempotency_key):
        raise RuntimeError("no response")

    with pytest.raises(UncertainEffect):
        perform(s, "r", g, EffectClass.COMMENT, "k-halt",
                valid_argv(EffectClass.COMMENT), _boom)

    rc = main(["command", "--db", db, "--run-id", "r", "--generation", str(g),
               "--name", "submit_spec", "--payload-json", "{}"])
    assert rc == 90
