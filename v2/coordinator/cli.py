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

from coordinator.ci import keep_blocking, normalize
from coordinator.observe import ci_history, classify
from coordinator.review import extract_verdict
from coordinator.session import (LookupFailed, item_count, last_assistant_text,
                                 settle, state)

RC_OK = 0
RC_USAGE = 2
RC_LOOKUP_FAILED = 3


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

    st = subs.add_parser("session-state")
    st.add_argument("--server", required=True)
    st.add_argument("--id", required=True, dest="conv_id")

    cn = subs.add_parser("ci-normalize")
    cn.add_argument("--buckets", required=True)

    cb = subs.add_parser("ci-keep-blocking")
    cb.add_argument("--lines", required=True)
    cb.add_argument("--required", default="")

    vd = subs.add_parser("verdict")
    vd.add_argument("--text", required=True)

    se = subs.add_parser("session-settle")
    se.add_argument("--server", required=True)
    se.add_argument("--id", required=True, dest="conv_id")
    se.add_argument("--prev-count", default="")
    se.add_argument("--stable-polls", type=int, default=0)
    se.add_argument("--needed", type=int, default=4)

    la = subs.add_parser("last-assistant-text")
    la.add_argument("--server", required=True)
    la.add_argument("--id", required=True, dest="conv_id")
    la.add_argument("--n", type=int, default=3)

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

    if a.mode == "session-state":
        s_ = state(a.server, a.conv_id)
        print(f"{s_.status}|{s_.error_code}", end="")
        return RC_OK

    if a.mode == "ci-normalize":
        print(normalize(a.buckets), end="")
        return RC_OK

    if a.mode == "ci-keep-blocking":
        print(keep_blocking(a.lines, a.required), end="")
        return RC_OK

    if a.mode == "verdict":
        v = extract_verdict(a.text)
        print(v or "", end="")
        # WARN when a reviewer said SOMETHING that was not a verdict. Silence
        # would leave an operator unable to tell "the reviewer never ran" from
        # "the reviewer rambled", and those need different responses. The first
        # port dropped this and `--self-test` caught it; `extract_verdict` stays
        # pure, so the warning belongs at the boundary, not in the rule.
        if v is None and a.text.strip():
            print("[batch] WARN: review's final line is not a bare verdict "
                  "-> treating as no verdict", file=sys.stderr)
        return RC_OK

    if a.mode == "session-settle":
        # ONE call does the read and the decision, so the shell carries only two
        # loop variables and none of the judgement.
        prev = int(a.prev_count) if a.prev_count.strip().isdigit() else None
        r = settle(state(a.server, a.conv_id).status,
                   item_count(a.server, a.conv_id),
                   prev, a.stable_polls, needed=a.needed)
        print(f"{'' if r.count is None else r.count}|{r.stable_polls}|"
              f"{'yes' if r.settled else 'no'}", end="")
        return RC_OK

    if a.mode == "last-assistant-text":
        try:
            print(last_assistant_text(a.server, a.conv_id, a.n), end="")
        except LookupFailed as exc:
            # NON-ZERO, so the caller can tell "no assistant text" from "could
            # not read the session" -- the distinction the limit check needs.
            print(f"session-items lookup failed: {exc}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        return RC_OK

    o = classify(a.pr or None, a.ci, a.verdict or None, reviewer=a.reviewer)
    print(f"{o.outcome}|{o.review}|{o.ci}|{o.note}", end="")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
