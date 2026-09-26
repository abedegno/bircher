import json

import pytest

from coordinator.cli import RC_PARKED, RC_USAGE, main
from kernel import front
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _common(db):
    return ["--db", str(db), "--run-id", "r-1"]


def test_the_cli_and_the_loop_agree_on_the_exit_codes():
    """`phases` returns `phases.Exit` values straight to the shell, so the
    constants the CLI publishes have to BE those values -- two tables naming
    the same codes eventually disagree, and the caller reads the wrong one."""
    from coordinator import cli, phases
    assert (cli.RC_OK, cli.RC_FAILED, cli.RC_USAGE, cli.RC_PARKED, cli.RC_SUPERSEDED) == (
        phases.Exit.OK, phases.Exit.FAILED, phases.Exit.USAGE, phases.Exit.PARKED,
        phases.Exit.SUPERSEDED)
    # RC_LOOKUP_FAILED (3) is the fallback commands' own and no loop exit.
    assert cli.RC_LOOKUP_FAILED != phases.Exit.SUPERSEDED


def test_phases_requires_a_positive_integer_turn_timeout(tmp_path, capsys):
    db = tmp_path / "k.db"
    Front(Store.open(db), "r-1")
    # The driver's birth shape_round has already spent one author generation
    # (Task 3 rule (b)); "nothing happened" is that list unchanged, not empty.
    born = Store.open(db).dispatches_for("r-1")
    base = ["phases", *_common(db), "--server", "http://srv", "--repo", "o/r", "--issue", "1",
            "--repo-dir", str(tmp_path), "--workspaces-root", str(tmp_path / "ws"), "--bundle-dir", str(tmp_path),
            "--host-id", "h", "--agent-claude", "a", "--agent-codex", "b"]
    assert main(base) == RC_USAGE
    assert main(base + ["--turn-timeout", "abc"]) == RC_USAGE
    assert main(base + ["--turn-timeout", "0"]) == RC_USAGE
    s = Store.open(db)
    assert s.dispatches_for("r-1") == born                     # nothing happened  # Task 3 rule (b)


def _phases_argv(db, tmp_path):
    return ["phases", *_common(db), "--server", "http://srv", "--repo", "o/r", "--issue", "1",
            "--repo-dir", str(tmp_path), "--workspaces-root", str(tmp_path / "ws"),
            "--bundle-dir", str(tmp_path), "--host-id", "h", "--agent-claude", "a",
            "--agent-codex", "b", "--turn-timeout", "1"]


@pytest.mark.parametrize("mode", ["shadow", "enfroce"])
def test_phases_refuses_to_run_outside_enforce_mode(tmp_path, monkeypatch, capsys, mode):
    """The loop's ENTIRE control flow is "a refusal raises": `take_listing`
    catches NotAuthorized to dismiss, `author_round` catches it to choose
    re-author versus stall, `review_round` catches it for `no_brief`. Under
    shadow `shadow_or_raise` records and RETURNS, `_submit` gives back
    `Result(accepted=False)`, and every one of those reads a refusal as a
    success -- a refused `approve` reported "taken", a refused `submit_spec`
    returning "submitted", `front.brief_for(...)` returning None for an
    AttributeError. So the loop does not run at all outside enforce, and it
    refuses before it opens the store or performs anything. A mode that is not
    a mode at all is refused here too, rather than raising out of the first
    guard that happens to ask."""
    db = tmp_path / "k.db"
    Front(Store.open(db), "r-1")
    born = Store.open(db).dispatches_for("r-1")   # the birth shape_round's own seat
    monkeypatch.setenv("BIRCHER_KERNEL_MODE", mode)
    assert main(_phases_argv(db, tmp_path)) == RC_USAGE
    err = capsys.readouterr().err
    assert "phases" in err and mode in err, err
    assert Store.open(db).dispatches_for("r-1") == born         # nothing happened  # Task 3 rule (b)


def test_approve_grant_direct_revise_and_parked(tmp_path, capsys):
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "r-1", labels=())
    f.author_round(SPEC_BYTES); f.review_round("accept")
    assert main(["parked", *_common(db)]) == 1
    from kernel.dispatch import Role
    g = f._dispatch(Role.OPERATOR, "runner")
    f._cmd(g, "park", {"reason": "gate", "session_id": None, "cursor_item_id": None,
                       "findings_hash": None, "verdict": None, "reviewer": None})
    assert main(["parked", *_common(db)]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "gate"
    assert main(["approve", *_common(db), "--phase", "plan"]) == 1        # wrong phase: refused
    assert main(["approve", *_common(db), "--phase", "spec"]) == 0
    assert s.run_state("r-1") == "specified"
    text = tmp_path / "d.txt"; text.write_text("use sqlite")
    assert main(["direct", *_common(db), "--phase", "plan", "--text", str(text)]) == 0
    assert s.newest_fact("r-1", EventKind.HUMAN_DIRECTION).payload["text"] == "use sqlite"
    f.author_round(b"# Plan\n\n### Task 1: x\n")
    findings = tmp_path / "f.txt"; findings.write_text("no tests")
    assert main(["revise", *_common(db), "--phase", "plan", "--findings", str(findings)]) == 0
    assert s.run_state("r-1") == "specified"
    assert main(["grant-round", *_common(db)]) == 1                        # no current park


def test_cancel_records_cancelled_and_retires(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "r-1")
    stopped = []
    monkeypatch.setattr("coordinator.phases.retire", lambda ctx: stopped.append(ctx.run_id) or [])
    assert main(["cancel", *_common(db), "--server", "http://srv"]) == 0
    # No pull request, so nothing is owed to a later pass: the stop IS the
    # end (2026-09-26). Waves never sweep a front-half cancelled run, so a
    # run left at `cancelled` here stayed open forever.
    assert s.run_state("r-1") == "ended" and stopped == ["r-1"]
    ends = [f for f in s.facts_of_kind("r-1", EventKind.COMMAND_ACCEPTED)
            if f.payload.get("command_name") == "record_run_outcome"]
    assert [e.payload["payload"]["outcome"] for e in ends] == ["escalated"]


def test_cancel_finishes_a_run_an_earlier_cancel_left_open(tmp_path, monkeypatch):
    """The four legacy runs: cancelled by the old `cancel`, never ended."""
    from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "r-1").to_specified()
    execute_as_human(s, Command(name="cancel_run", run_id="r-1", expected_version=s.run_version("r-1"),
                                idempotency_key="old-cancel", generation=HUMAN_GENERATION, payload={}))
    monkeypatch.setattr("coordinator.phases.retire", lambda ctx: [])
    assert main(["cancel", *_common(db), "--server", "http://srv"]) == 0
    assert s.run_state("r-1") == "ended"


def test_cancel_leaves_a_run_with_a_pull_request_to_the_wave(tmp_path, monkeypatch):
    """With implementation output the resume path owns the ending (closed-loop
    spec §2): it reads the PR and writes the issue back. Unchanged."""
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "r-1").to_implementing()                     # records output
    monkeypatch.setattr("coordinator.phases.retire", lambda ctx: [])
    assert main(["cancel", *_common(db), "--server", "http://srv"]) == 0
    assert s.run_state("r-1") == "cancelled"


def test_cancel_that_cannot_retire_leaves_the_run_for_the_sweep(tmp_path, monkeypatch):
    """Retiring fails on a halted run with sessions it cannot stop. The run
    stays `cancelled` and the command says so; the journal sweep queues every
    cancelled run, and the resume path records the terminal fact."""
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "r-1").to_specified()

    def cannot(ctx):
        raise RuntimeError("run r-1 is halted; these sessions could not be stopped: ['s1']")
    monkeypatch.setattr("coordinator.phases.retire", cannot)
    assert main(["cancel", *_common(db), "--server", "http://srv"]) == 1
    assert s.run_state("r-1") == "cancelled"
