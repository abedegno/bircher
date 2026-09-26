"""What every open run is doing, from the journal alone (`coordinator.cli
status`). Answering "is the pipeline stuck?" used to take a scorecard grep,
a wave log and a hand-written query against the kernel database."""
from __future__ import annotations

import datetime

from kernel import back, front
from kernel.authz import BACK_HALF_DERIVING_STATES

COLUMNS = ("run", "state", "parked", "pr", "rounds", "seats", "last_activity")


def rows(store, *, include_ended: bool = False) -> list[tuple[str, ...]]:
    out = []
    for rid in store.all_run_ids():
        state = store.run_state(rid)
        if state == "ended" and not include_ended:
            continue
        park = front.current_park(store, rid)
        facts = store.facts_for(rid)
        last = (datetime.datetime.fromtimestamp(facts[-1].observed_at_us / 1e6, datetime.UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ") if facts else "-")
        # Seats bound the front half only (kernel.dispatch); past the seam a
        # used/bound pair would read as a wall that is not there.
        seats = "-" if state in BACK_HALF_DERIVING_STATES or state == "ended" else \
            f"{front.seats_used(store, rid)}/{front.seat_bound(store, rid)}"
        out.append((rid, state, park.payload.get("reason", "?") if park else "-",
                    back.latest_pr(store, rid) or "-", str(back.repair_rounds(store, rid)), seats, last))
    return out


def render(table: list[tuple[str, ...]]) -> str:
    lines = [COLUMNS, *table]
    widths = [max(len(r[i]) for r in lines) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in lines)
