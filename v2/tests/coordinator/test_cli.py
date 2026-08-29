"""The wire format the shell callers parse.

Pinned because a change here is silent at the call site: bash splits on `|`
and a field that moves is absorbed by its neighbour.
"""
import json

from coordinator.cli import main


def test_ci_history_prints_the_pipe_form(capsys, monkeypatch):
    import coordinator.cli as mod
    monkeypatch.setattr(mod, "ci_history",
                        lambda repo, branch: __import__("coordinator.observe",
                                                        fromlist=["CiHistory"])
                        .CiHistory("true", 2))
    assert main(["ci-history", "--repo", "o/r", "--branch", "b"]) == 0
    assert capsys.readouterr().out == "true|2"


def test_an_unknown_history_prints_an_EMPTY_second_field(capsys, monkeypatch):
    """`unknown|` must not become `unknown|0`. Zero resubmissions is a claim;
    the empty field is the absence of one."""
    import coordinator.cli as mod
    from coordinator.observe import CiHistory
    monkeypatch.setattr(mod, "ci_history", lambda repo, branch: CiHistory())
    assert main(["ci-history", "--repo", "o/r", "--branch", "b"]) == 0
    assert capsys.readouterr().out == "unknown|"


def test_classify_prints_four_fields(capsys):
    assert main(["classify", "--pr", "42", "--ci", "green",
                 "--verdict", "PASS", "--reviewer", "codex"]) == 0
    out = capsys.readouterr().out
    assert out.split("|")[:3] == ["ready", "codex:pass", "green"]
    assert len(out.split("|")) == 4


def test_an_absent_pr_is_a_timeout_not_a_usage_error(capsys):
    """`--pr` is deliberately optional: no PR at timeout is an outcome."""
    assert main(["classify", "--ci", "na", "--reviewer", "codex"]) == 0
    assert capsys.readouterr().out.split("|")[0] == "timeout"
