"""The kernel's command layer gains its first caller outside the tests."""
import json
import pathlib

import pytest

from kernel.artifacts import put_artifact
from kernel.cli import main
from kernel.dispatch import Role, dispatch
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


def test_the_effect_subcommand_still_works(db, tmp_path):
    g = _gen(db, "claude", Role.IMPLEMENTER)
    witness = tmp_path / "ran"
    rc = main(["effect", "--db", db, "--run-id", "r", "--generation", str(g),
               "--class", "comment", "--idempotency-key", "k",
               "--", "gh", "pr", "comment", "1", "--repo", "o/r",
               "--body", "hi"])
    # Refused or failed at execution is fine here; what must not happen is a
    # usage error, which would mean the subcommand split broke the adapter.
    assert rc != 2
