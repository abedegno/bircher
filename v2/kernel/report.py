"""What would enforcement have refused?

The input to switching commands from shadow to enforce one at a time. A count
tells you enforcement would break something; the reason tells you what, and
whether the fix is the guard or the wiring.

WHAT `count` COUNTS, and why it is not the number of facts. The coordinator
retries: an advisory `_kernel` call that gets shadow-refused is retried by
run-queue.sh, and each attempt appends its own `shadow_rejected` fact carrying
the SAME `causal_command_id`. Counting facts would answer "how many times did
we notice", when the question this report exists to answer is "how many
distinct commands would enforcement have broken" -- the number that decides
whether a command is safe to enforce. So `count` is distinct
(run_id, causal_command_id) pairs, and the raw fact total is reported
separately as `occurrences`. A large gap between them is itself a signal: the
same refusal is being retried rather than handled.

The pair, not the id alone: idempotency keys are unique within a run, not
across runs -- two runs both using "k3" are two different commands, and
deduping on the bare id would silently merge them into one.
"""

from __future__ import annotations

import argparse
import json

from kernel.events import EventKind
from kernel.store import Store


def shadow_summary(store) -> list[dict]:
    """One row per command name (or effect class), most frequent first."""
    seen: dict[str, dict] = {}
    for run_id in store.all_run_ids():
        for fact in store.facts_for(run_id):
            if fact.kind != EventKind.SHADOW_REJECTED:
                continue
            payload = fact.payload or {}
            name = (payload.get("command_name")
                    or payload.get("effect_class")
                    or "(unknown)")
            row = seen.setdefault(name, {
                "command_name": name,
                "distinct": set(),
                "occurrences": 0,
                "runs": set(),
                "example_reason": payload.get("reason", ""),
            })
            row["occurrences"] += 1
            row["runs"].add(run_id)
            # A fact with no causal id cannot be matched to a request, so it
            # cannot be deduped against one either. Its own fact id keeps it
            # distinct rather than collapsing every such fact into one.
            row["distinct"].add((run_id, fact.causal_command_id or f"#{fact.id}"))
            if not row["example_reason"]:
                row["example_reason"] = payload.get("reason", "")

    rows = [{"command_name": r["command_name"],
             "count": len(r["distinct"]),
             "occurrences": r["occurrences"],
             "runs": len(r["runs"]),
             "example_reason": r["example_reason"]}
            for r in seen.values()]
    # Name breaks ties so the output is stable: an unstable order makes two
    # runs of the same report look like a change.
    return sorted(rows, key=lambda r: (-r["count"], r["command_name"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-kernel-report")
    p.add_argument("--db", required=True)
    a = p.parse_args(argv)
    print(json.dumps(shadow_summary(Store.open(a.db)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
