"""The Python effect path, against the design's five acceptance criteria.

`docs/superpowers/specs/2026-08-29-coordinator-effect-path-design.md`.
"""
import pathlib
import re
import subprocess

import pytest

from coordinator.effect_mode import DENY, KERNEL, LEGACY, MODES, UnknownMode, effect_mode
from coordinator.effects import EffectDenied, NotDispatched, perform_effect
from kernel.dispatch import dispatch
from kernel.events import EventKind
from kernel.ids import Clock
from kernel.store import Store

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ADAPTER = REPO_ROOT / "batch" / "lib" / "effect-adapter.sh"

#: A contract-legal ref_update. `echo` is NOT usable here -- the argv contract
#: refuses it, correctly -- so kernel-mode tests push to a LOCAL BARE REPO:
#: contract-legal, harmless, and it exercises the real command path rather than
#: a stand-in the contract would never see in production.
ARGV = ["git", "push", "origin", "abc123:refs/heads/probe"]

_GIT_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _git(wt, *args):
    r = subprocess.run(["git", "-C", str(wt), *args], capture_output=True,
                       text=True, env=_GIT_ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def pushable(tmp_path):
    """A work repo with a bare `origin` on disk, and the oid to push."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   env=_GIT_ENV)
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    (wt / "f").write_text("x")
    _git(wt, "add", "f")
    _git(wt, "commit", "-qm", "c")
    _git(wt, "remote", "add", "origin", str(bare))
    return wt, _git(wt, "rev-parse", "HEAD")


def _dispatched(tmp_path):
    db = tmp_path / "kernel.db"
    store = Store.open(db, clock=Clock(start_us=1))
    store.create_run(run_id="r", base_repo="o/r", base_sha="ab" * 20)
    gen = dispatch(store, "r", actor="codex", role="implementer").generation
    return db, {"BIRCHER_KERNEL_DB": str(db), "BIRCHER_RUN_ID": "r",
                "BIRCHER_GENERATION": str(gen)}


# --- mode vocabulary ---------------------------------------------------------

def test_the_default_is_deny():
    assert effect_mode({}) == DENY


@pytest.mark.parametrize("mode", MODES)
def test_every_named_mode_is_accepted(mode):
    assert effect_mode({"BIRCHER_EFFECT_MODE": mode}) == mode


def test_an_unrecognised_mode_RAISES_rather_than_defaulting():
    """A typo meaning `deny` would look like a working boundary while
    performing nothing; one meaning `legacy` would perform everything and
    journal none of it. Both are worse than stopping."""
    with pytest.raises(UnknownMode):
        effect_mode({"BIRCHER_EFFECT_MODE": "kernal"})


def test_an_empty_value_is_the_default_not_an_error():
    assert effect_mode({"BIRCHER_EFFECT_MODE": ""}) == DENY


# --- criterion 2: deny performs nothing --------------------------------------

def test_under_deny_nothing_happens_and_it_says_so(tmp_path):
    db, env = _dispatched(tmp_path)
    env["BIRCHER_EFFECT_MODE"] = DENY
    with pytest.raises(EffectDenied):
        perform_effect("ref_update", "k1", ARGV, env=env)
    facts = [f.kind for f in Store.open(db).facts_for("r")]
    assert EventKind.EFFECT_INTENDED not in facts, (
        "deny must not journal a refusal: the operator asked for the database "
        "to be left alone")


# --- criterion 3: legacy journals nothing and never opens the kernel ---------

def test_under_legacy_the_kernel_is_never_opened(tmp_path):
    """Pointed at a database that does not exist. `legacy` is the tool for
    diagnosing a suspected kernel fault, so it must work when the kernel does
    not."""
    env = {"BIRCHER_EFFECT_MODE": LEGACY,
           "BIRCHER_KERNEL_DB": "/nonexistent/nowhere/kernel.db"}
    out = perform_effect("ref_update", "k1", ["echo", "done"], env=env)
    assert out == "done"
    assert not pathlib.Path("/nonexistent/nowhere/kernel.db").exists()


def test_legacy_needs_no_run_or_generation(tmp_path):
    assert perform_effect("comment", "k", ["echo", "x"],
                          env={"BIRCHER_EFFECT_MODE": LEGACY}) == "x"


def test_a_failing_legacy_effect_raises_rather_than_returning_empty(tmp_path):
    with pytest.raises(RuntimeError):
        perform_effect("comment", "k", ["false"],
                       env={"BIRCHER_EFFECT_MODE": LEGACY})


# --- criterion 1: kernel mode journals ---------------------------------------

def test_under_kernel_the_effect_is_journalled(tmp_path, pushable, monkeypatch):
    wt, oid = pushable
    db, env = _dispatched(tmp_path)
    env["BIRCHER_EFFECT_MODE"] = KERNEL
    env["PATH"] = _GIT_ENV["PATH"]
    # From INSIDE the worktree. `git -C <dir> push` reads as signature `git`
    # and the contract refuses it -- `-C` is exactly the redirect it exists to
    # deny -- which is why publish_cmd cds too.
    monkeypatch.chdir(wt)
    perform_effect("ref_update", "k1",
                   ["git", "push", "origin", f"{oid}:refs/heads/probe"], env=env)

    facts = [f.kind for f in Store.open(db).facts_for("r")]
    assert EventKind.EFFECT_INTENDED in facts
    assert EventKind.EFFECT_CONFIRMED in facts


def test_the_ref_actually_moves_and_the_id_is_recorded(tmp_path, pushable, monkeypatch):
    """Not just journalled -- PERFORMED. A path that recorded intent and
    confirmation without running anything would pass every fact-shaped
    assertion above."""
    wt, oid = pushable
    db, env = _dispatched(tmp_path)
    env["BIRCHER_EFFECT_MODE"] = KERNEL
    env["PATH"] = _GIT_ENV["PATH"]
    # From INSIDE the worktree. `git -C <dir> push` reads as signature `git`
    # and the contract refuses it -- `-C` is exactly the redirect it exists to
    # deny -- which is why publish_cmd cds too.
    monkeypatch.chdir(wt)
    perform_effect("ref_update", "k1",
                   ["git", "push", "origin", f"{oid}:refs/heads/probe"], env=env)

    landed = _git(wt, "ls-remote", "origin", "refs/heads/probe")
    assert landed.split()[0] == oid, "the ref did not move"
    confirmed = next(f for f in Store.open(db).facts_for("r")
                     if f.kind == EventKind.EFFECT_CONFIRMED)
    # `git push` prints to stderr, so stdout is empty and the executor records
    # "ok" -- the id is the fact that it succeeded, not a URL.
    assert confirmed.payload["external_object_id"] == "ok"


def test_a_replayed_key_does_not_execute_twice(tmp_path, pushable, monkeypatch):
    """Idempotency is the kernel's, not this path's -- asserted so a future
    change here cannot quietly bypass it. The SECOND call names a different
    ref: if it executed, that ref would exist."""
    wt, oid = pushable
    db, env = _dispatched(tmp_path)
    env["BIRCHER_EFFECT_MODE"] = KERNEL
    env["PATH"] = _GIT_ENV["PATH"]
    # From INSIDE the worktree. `git -C <dir> push` reads as signature `git`
    # and the contract refuses it -- `-C` is exactly the redirect it exists to
    # deny -- which is why publish_cmd cds too.
    monkeypatch.chdir(wt)
    perform_effect("ref_update", "same",
                   ["git", "push", "origin", f"{oid}:refs/heads/first"], env=env)
    perform_effect("ref_update", "same",
                   ["git", "push", "origin", f"{oid}:refs/heads/second"], env=env)

    assert _git(wt, "ls-remote", "origin", "refs/heads/first")
    assert _git(wt, "ls-remote", "origin", "refs/heads/second") == "", (
        "the replay EXECUTED: a repeated idempotency key must return the "
        "recorded result without acting")


# --- criterion 5: an undispatched effect is refused --------------------------

@pytest.mark.parametrize("missing", ["BIRCHER_RUN_ID", "BIRCHER_GENERATION",
                                     "BIRCHER_KERNEL_DB"])
def test_kernel_mode_refuses_without_a_run_to_bind_to(tmp_path, missing):
    """Mirrors the adapter's `${VAR:?}`. Journalling against no attempt would
    defeat the fence that exists so a late result cannot claim one."""
    _db, env = _dispatched(tmp_path)
    env["BIRCHER_EFFECT_MODE"] = KERNEL
    del env[missing]
    with pytest.raises(NotDispatched):
        perform_effect("ref_update", "k1", ARGV, env=env)


# --- criterion 4: the two entry points agree ---------------------------------

def test_the_bash_adapter_implements_exactly_these_modes():
    """CLOSES THE DIVERGENCE. The switch cannot be shared -- `legacy` must work
    without the kernel -- so this asserts the two entry points still mean the
    same three things.
    """
    src = ADAPTER.read_text()
    arms = set(re.findall(r"^\s{4}(\w+)\)", src, re.M))
    assert arms == set(MODES), f"adapter arms {sorted(arms)} != {sorted(MODES)}"


def test_the_bash_adapter_defaults_to_the_same_mode():
    src = ADAPTER.read_text()
    m = re.search(r'BIRCHER_EFFECT_MODE:-(\w+)\}', src)
    assert m, "the adapter no longer defaults BIRCHER_EFFECT_MODE"
    assert m.group(1) == DENY, (
        f"adapter defaults to {m.group(1)!r}, Python to {DENY!r} -- one entry "
        "point would perform effects the other refuses")


def test_the_adapter_still_refuses_an_unknown_mode():
    assert "unknown BIRCHER_EFFECT_MODE" in ADAPTER.read_text()
