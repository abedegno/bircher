"""A validated command NAME is not a validated EXECUTABLE.

Round 7, R7-10 and R7-8 -- both found by codex, and neither reachable by a
stricter argv allowlist, because neither is a property of the argv.

R7-10: `contract.check` validates the token `gh`. `subprocess.run` then
re-resolves that token through PATH. A `gh` earlier in PATH executes instead,
in the kernel's credential domain. The reviewer demonstrated it: a harmless
script named `gh` printed `ROUND7_FAKE_GH_EXECUTED` for an argv the contract
had just accepted.

R7-8: curl reads its config file before its command line, so `.curlrc` can add
URLs and options no contract ever sees. The reviewer demonstrated that too --
the exact real-call argv performed an extra transfer.

Not currently exploitable: the CLI runs inside the container, invoked by the
coordinator, and a model session cannot influence that PATH. This is
defence in depth -- and it is the same shape as the identity precondition, a
property the kernel depends on that lives in the launcher and that nothing
checked.
"""

import pathlib
import stat

import pytest

from kernel.cli import TOOL_DIRS, UnresolvableTool, resolve_command


def test_a_tool_resolves_to_an_absolute_path():
    argv = resolve_command(["git", "status"])
    assert argv[0].startswith("/"), argv
    assert argv[1:] == ["status"]


def test_the_resolved_path_lies_in_an_allowlisted_directory():
    resolved = resolve_command(["git", "status"])[0]
    assert any(resolved.startswith(d + "/") for d in TOOL_DIRS), resolved


def test_a_shadowing_binary_earlier_in_the_path_is_not_used(tmp_path, monkeypatch):
    """R7-10, as the reviewer demonstrated it."""
    fake = tmp_path / "git"
    fake.write_text("#!/bin/sh\necho ROUND7_FAKE\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    resolved = resolve_command(["git", "status"])[0]
    assert str(tmp_path) not in resolved, (
        f"resolution followed PATH into {tmp_path}; a name is not an executable"
    )


def test_an_unknown_tool_is_refused():
    """The allowlist is of TOOLS as well as directories: resolving anything
    the kernel does not run would make the directory list the only limit."""
    with pytest.raises(UnresolvableTool, match="not a tool"):
        resolve_command(["bash", "-c", "echo hi"])


def test_a_tool_that_is_not_installed_is_refused(monkeypatch):
    monkeypatch.setattr("kernel.cli.TOOL_DIRS", ("/nonexistent",))
    with pytest.raises(UnresolvableTool, match="not found"):
        resolve_command(["git", "status"])


def test_an_absolute_path_in_argv0_is_refused():
    """The caller does not get to name the executable, only the tool."""
    with pytest.raises(UnresolvableTool, match="not a tool"):
        resolve_command(["/tmp/evil/gh", "pr", "merge", "1"])


def test_curl_is_given_q_first_so_it_ignores_its_config(tmp_path):
    """R7-8. `-q` must be curl's FIRST argument to suppress .curlrc, so the
    kernel inserts it rather than asking every call site to remember."""
    argv = resolve_command(["curl", "-sf", "-X", "DELETE", "http://s/v1/sessions/1"])
    assert argv[1] == "-q", argv
    assert argv[2:] == ["-sf", "-X", "DELETE", "http://s/v1/sessions/1"]


def test_q_is_not_injected_twice():
    argv = resolve_command(["curl", "-q", "-sf", "http://s/v1/sessions/1"])
    assert argv.count("-q") == 1, argv


def test_other_tools_are_not_given_q():
    """`-q` means something else to git and gh; injecting it everywhere would
    be a different bug."""
    assert "-q" not in resolve_command(["gh", "pr", "view", "1"])[1:2]
