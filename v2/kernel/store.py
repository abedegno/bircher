"""The only module that touches SQLite.

The engine is an explicitly reversible decision (spec, "Reversible, and not
worth arguing about now"); confining every SQLite construct here is what keeps
it reversible.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.canon import canonical_bytes
from kernel.events import MECHANISM_VERSION, SCHEMA_VERSIONS
from kernel.ids import new_id, now_us

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class _SystemClock:
    @staticmethod
    def now_us() -> int:
        return now_us()


@dataclass(frozen=True)
class Fact:
    seq: int
    id: str
    run_id: str
    kind: str
    schema_version: int
    mechanism_version: int
    causal_command_id: str | None
    actor: str
    observed_at_us: int
    payload: dict


class Store:
    def __init__(self, conn: sqlite3.Connection, clock: Any) -> None:
        self._conn, self._clock = conn, clock

    @classmethod
    def open(cls, path: Path | str, clock: Any = None) -> Store:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.executescript(_SCHEMA)
        return cls(conn, clock or _SystemClock())

    def append_fact(
        self,
        *,
        run_id: str,
        kind: str,
        actor: str,
        causal_command_id: str | None,
        payload: dict,
    ) -> str:
        schema_version = SCHEMA_VERSIONS[kind]  # KeyError on an undeclared kind
        fid = new_id("fact")
        self._conn.execute(
            "INSERT INTO facts (id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                fid,
                run_id,
                kind,
                schema_version,
                MECHANISM_VERSION,
                causal_command_id,
                actor,
                self._clock.now_us(),
                canonical_bytes(payload).decode("utf-8"),
            ),
        )
        return fid

    def put_blob(self, content_hash: str, data: bytes) -> None:
        """Insert an immutable blob. Idempotent: identical bytes hash the same,
        so a repeated write is a no-op rather than a conflict."""
        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts (hash, bytes) VALUES (?,?)",
            (content_hash, data),
        )

    def facts_for(self, run_id: str) -> list[Fact]:
        rows = self._conn.execute(
            "SELECT seq, id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json"
            " FROM facts WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [Fact(*r[:-1], payload=json.loads(r[-1])) for r in rows]
