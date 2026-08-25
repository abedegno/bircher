"""Stable identity and UTC time.

Both are irreversible decisions (spec, "Decisions that must be right in the
first commit"): integrations depend on identity semantics, and ambiguous
historical instants cannot be repaired. Time is integer microseconds since the
Unix epoch -- no timezone, no float, and sortable as an integer.
"""

from __future__ import annotations

import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_us() -> int:
    return time.time_ns() // 1_000


class Clock:
    """Injectable clock. Production passes :func:`now_us`; tests pass this.

    A test that cannot fix time cannot assert on a hash that includes one.
    """

    def __init__(self, start_us: int, step_us: int = 1) -> None:
        self._t, self._step = start_us, step_us

    def now_us(self) -> int:
        t = self._t
        self._t += self._step
        return t
