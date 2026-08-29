"""The coordinator's command line, mirroring `kernel.cli`.

A seam, and a temporary one. It exists because `batch/run-queue.sh` is still
the process that runs, and bash reaches Python through a subprocess. As the
coordinator moves into this package the callers become Python and this module
gets thinner, not thicker -- if it grows a subcommand per bash caller, the
migration has stalled and turned into an API.
"""

from __future__ import annotations

import argparse
import sys

from coordinator.observe import ci_history, classify

RC_OK = 0
RC_USAGE = 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-coordinator")
    subs = p.add_subparsers(dest="mode", required=True)

    h = subs.add_parser("ci-history")
    h.add_argument("--repo", required=True)
    h.add_argument("--branch", required=True)

    c = subs.add_parser("classify")
    # Empty is meaningful for --pr: "no PR at timeout" is an outcome, not an
    # error, so this is deliberately not `required`.
    c.add_argument("--pr", default="")
    c.add_argument("--ci", required=True)
    c.add_argument("--verdict", default="")
    c.add_argument("--reviewer", required=True)

    a = p.parse_args(argv)

    if a.mode == "ci-history":
        r = ci_history(a.repo, a.branch)
        # The pipe form is what the shell callers parse. It is a wire format
        # for one consumer, not a public interface: `resubmissions` is empty
        # rather than a number when unknown, exactly as the bash version was,
        # so `unknown|` cannot be mistaken for `false|0`.
        print(f"{r.ci_first}|{'' if r.resubmissions is None else r.resubmissions}",
              end="")
        return RC_OK

    o = classify(a.pr or None, a.ci, a.verdict or None, reviewer=a.reviewer)
    print(f"{o.outcome}|{o.review}|{o.ci}|{o.note}", end="")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
