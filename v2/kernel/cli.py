"""bircher-effect -- journal and perform one effect from the shell.

The coordinator keeps its orchestration; this is where its authority goes.

The executor runs the real command from the KERNEL's credential domain. That
is the whole point of the seam: no model process holds credentials for a
kernel-owned effect, implementers included.

There is deliberately NO timeout here. The bound stays in `_net_run`, wrapping
this process, because #62's scar is `timeout -k` specifically -- plain SIGTERM
is not a bound against a push stuck in credential negotiation -- and a Python
reimplementation would be a second bound to keep correct. If this process is
killed mid-effect the journal holds `intended`, which M1-3 already treats as
uncertain rather than as a completed replay.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from kernel.authz import NotAuthorized
from kernel.effects import EffectClass, UncertainEffect, perform
from kernel.ownership import OwnershipLost
from kernel.store import Store

#: Directories a kernel tool may live in. The resolved path must be inside
#: one of these -- not merely "first on PATH".
TOOL_DIRS = ("/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin",
             "/usr/local/sbin", "/opt/bin")

#: Tools the kernel performs effects with. An allowlist of TOOLS as well as
#: directories: without it the directory list would be the only limit, and
#: /usr/bin holds a great deal more than three programs.
TOOLS = frozenset({"gh", "git", "curl"})

#: curl reads its config file BEFORE its command line, so `.curlrc` can add
#: URLs and options no contract ever sees. `-q` suppresses that, and it only
#: works as the FIRST argument -- so the kernel inserts it rather than asking
#: every call site to remember.
_CONFIG_SUPPRESS = {"curl": "-q"}


class UnresolvableTool(Exception):
    """The command names something the kernel will not run."""


def resolve_command(argv: list[str]) -> list[str]:
    """Turn a validated argv into an absolute, unambiguous command.

    `contract.check` validates the NAME `gh`. `subprocess.run` then re-resolves
    that name through PATH, so a `gh` earlier in PATH would execute instead --
    in the kernel's credential domain. Validating a command name is not
    validating an executable.
    """
    if not argv:
        raise UnresolvableTool("empty command")
    tool = argv[0]
    if tool not in TOOLS:
        raise UnresolvableTool(
            f"{tool!r} is not a tool the kernel runs; expected one of "
            f"{sorted(TOOLS)}"
        )
    for d in TOOL_DIRS:
        candidate = os.path.join(d, tool)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            rest = argv[1:]
            suppress = _CONFIG_SUPPRESS.get(tool)
            if suppress and suppress not in rest:
                rest = [suppress] + rest
            return [candidate] + rest
    raise UnresolvableTool(
        f"{tool!r} not found in any allowlisted directory: {list(TOOL_DIRS)}"
    )


RC_OK = 0
RC_USAGE = 2
RC_REFUSED = 87
RC_FENCED = 88
RC_UNCERTAIN = 89
RC_FAILED = 90


def _executor(effect_class, intent, idempotency_key):
    """Run the real command. Raising here is what makes an effect uncertain.

    `check=False` plus an explicit raise, rather than `check=True`: the journal
    distinguishes "ran and failed" from "outcome unknown", and letting
    CalledProcessError escape unlabelled would collapse the two.
    """
    r = subprocess.run(resolve_command(list(intent["argv"])),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"{effect_class} failed rc={r.returncode}: {r.stderr.strip()[:200]}"
        )
    return r.stdout.strip() or "ok"


def _add_common(p):
    p.add_argument("--db", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--generation", type=int, required=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-kernel")
    subs = p.add_subparsers(dest="mode", required=True)

    e = subs.add_parser("effect")
    _add_common(e)
    e.add_argument("--class", dest="effect_class", required=True,
                   choices=sorted(EffectClass.ALL))
    e.add_argument("--idempotency-key", required=True)
    e.add_argument("cmd", nargs=argparse.REMAINDER)

    c = subs.add_parser("command")
    _add_common(c)
    c.add_argument("--name", required=True)
    c.add_argument("--payload-json", default="{}")
    c.add_argument("--idempotency-key", default=None)

    a = p.parse_args(argv)
    return _do_effect(a) if a.mode == "effect" else _do_command(a)


def _do_effect(a) -> int:
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("no command given", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    try:
        print(perform(store, a.run_id, a.generation, a.effect_class,
                      a.idempotency_key, {"argv": cmd}, _executor))
        return RC_OK
    except UnresolvableTool as e:
        print(f"unresolvable: {e}", file=sys.stderr)
        return RC_REFUSED
    except NotAuthorized as e:
        # Includes an undispatched generation and a merge whose authorization
        # no longer holds. Distinct from FENCED: the caller may be the current
        # owner and still have no authority for THIS effect.
        print(f"refused: {e}", file=sys.stderr)
        return RC_REFUSED
    except OwnershipLost as e:
        print(f"fenced: {e}", file=sys.stderr)
        return RC_FENCED
    except UncertainEffect as e:
        print(f"uncertain, run halted: {e}", file=sys.stderr)
        return RC_UNCERTAIN
    except RuntimeError as e:
        print(f"failed: {e}", file=sys.stderr)
        return RC_FAILED


def _do_command(a) -> int:
    """Submit one typed command and translate its outcome to an exit code.

    Exit codes: 0 accepted or replayed, 2 usage (unknown command name,
    unparseable or non-object payload), 87 refused (NotAuthorized, a stale
    aggregate version, or any other command-level rejection), 88 fenced
    (superseded generation), 90 failed (a run halted pending reconciliation --
    the same RC _do_effect returns for its own RuntimeError, so the two
    subcommands agree). 89 (uncertain) does not apply here: only an effect
    executor can leave an outcome unconfirmed.
    """
    from kernel.commands import COMMAND_NAMES, Command, StaleVersion, submit

    if a.name not in COMMAND_NAMES:
        print(f"unknown command: {a.name}", file=sys.stderr)
        return RC_USAGE
    try:
        payload = json.loads(a.payload_json)
    except ValueError as exc:
        print(f"payload is not JSON: {exc}", file=sys.stderr)
        return RC_USAGE
    if not isinstance(payload, dict):
        print("payload must be a JSON object", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    # A stable default so a retry of the same stage REPLAYS. Without it every
    # retry is a new command and the same stage records twice.
    key = a.idempotency_key or f"{a.run_id}:{a.name}:{a.generation}"
    try:
        r = submit(store, Command(name=a.name, run_id=a.run_id,
                                  expected_version=store.run_version(a.run_id),
                                  idempotency_key=key, generation=a.generation,
                                  payload=payload))
        print("replayed" if r.replayed else "accepted")
        return RC_OK
    except NotAuthorized as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    except OwnershipLost as exc:
        print(f"fenced: {exc}", file=sys.stderr)
        return RC_FENCED
    except (StaleVersion, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return RC_REFUSED
    except RuntimeError as exc:
        # A halted run is an ordinary reachable state -- any failed effect
        # halts its run unconditionally (kernel.effects, on the first
        # execution failure). submit() raises a bare RuntimeError for it
        # (kernel.commands), same as _do_effect's halt-on-retry path, so both
        # subcommands map it to the same exit code rather than one of them
        # leaking an uncaught traceback.
        print(f"failed: {exc}", file=sys.stderr)
        return RC_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
