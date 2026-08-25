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
import subprocess
import sys

from kernel.authz import NotAuthorized
from kernel.effects import EffectClass, UncertainEffect, perform
from kernel.ownership import OwnershipLost
from kernel.store import Store

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
    r = subprocess.run(intent["argv"], capture_output=True, text=True)
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
