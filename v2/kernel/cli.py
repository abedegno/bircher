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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-effect")
    p.add_argument("--db", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--generation", type=int, required=True)
    p.add_argument("--class", dest="effect_class", required=True,
                   choices=sorted(EffectClass.ALL))
    p.add_argument("--idempotency-key", required=True)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
