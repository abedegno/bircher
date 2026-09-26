from coordinator import status
from coordinator.cli import main
from kernel.store import Store
from tests.kernel.front import Front


def test_status_lists_open_runs_with_their_park_and_seats(tmp_path, capsys):
    db = tmp_path / "k.db"
    s = Store.open(db)
    Front(s, "i1-open-1").to_specified()
    parked = Front(s, "i2-parked-1")
    parked.park(reason="gate", phase="spec")
    Front(s, "i3-impl-1").to_implementing()
    assert main(["status", "--db", str(db)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == list(status.COLUMNS)
    by_run = {line.split()[0]: line.split() for line in out[1:]}
    assert by_run["i1-open-1"][1:3] == ["specified", "-"]
    assert by_run["i2-parked-1"][2] == "gate"
    assert "/" in by_run["i1-open-1"][5]                 # front half: used/bound
    assert by_run["i3-impl-1"][5] == "-"                 # past the seam: no wall


def test_status_leaves_ended_runs_out_unless_asked(tmp_path, capsys):
    from kernel.dispatch import Role
    db = tmp_path / "k.db"
    s = Store.open(db)
    f = Front(s, "i4-done-1").to_planned()
    g = f._dispatch(Role.IMPLEMENTER, "claude")
    f._cmd(g, "record_run_outcome", {"outcome": "timeout"})
    assert main(["status", "--db", str(db)]) == 0
    assert "i4-done-1" not in capsys.readouterr().out
    assert main(["status", "--db", str(db), "--all"]) == 0
    assert "i4-done-1" in capsys.readouterr().out


def test_status_is_rc_3_without_a_database(tmp_path):
    assert main(["status", "--db", str(tmp_path / "none.db")]) == 3
