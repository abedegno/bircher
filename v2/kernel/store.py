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

    def create_run(self, *, run_id: str, base_repo: str, base_sha: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, state, base_repo, base_sha, created_at_us)"
            " VALUES (?,?,?,?,?)",
            (run_id, "queued", base_repo, base_sha, self._clock.now_us()),
        )

    def run_version(self, run_id: str) -> int:
        return int(
            self._conn.execute(
                "SELECT version FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )

    def command_result(self, idempotency_key: str, *, run_id: str) -> dict | None:
        """Look up a prior command result by key WITHIN a run.

        Keyed globally, two genuinely different commands sharing a key
        collided and the second was answered with the first's result.
        """
        row = self._conn.execute(
            "SELECT accepted, result_json, name FROM commands"
            " WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        if row is None:
            return None
        return {"accepted": row[0], "result": json.loads(row[1]), "name": row[2]}

    def record_command(
        self, key: str, run_id: str, name: str, accepted: bool, result: dict
    ) -> None:
        self._conn.execute(
            "INSERT INTO commands (idempotency_key, run_id, name, accepted,"
            " result_json, at_us) VALUES (?,?,?,?,?,?)",
            (key, run_id, name, int(accepted), json.dumps(result, sort_keys=True),
             self._clock.now_us()),
        )

    def journal_intent(
        self, effect_id, run_id, generation, effect_class, idempotency_key, intent
    ) -> None:
        self._conn.execute(
            "INSERT INTO effects (id, run_id, generation, effect_class,"
            " idempotency_key, state, external_object_id, intent_json, at_us)"
            " VALUES (?,?,?,?,?,'intended',NULL,?,?)",
            (effect_id, run_id, generation, effect_class, idempotency_key,
             json.dumps(intent, sort_keys=True), self._clock.now_us()),
        )

    def mark_effect(self, idempotency_key: str, state: str, external_object_id) -> None:
        self._conn.execute(
            "UPDATE effects SET state = ?, external_object_id = ?"
            " WHERE idempotency_key = ?",
            (state, external_object_id, idempotency_key),
        )

    def effect_by_key(self, idempotency_key: str, *, run_id: str) -> dict | None:
        """Look up an effect by key WITHIN a run.

        Keyed globally, a confirmed key returned its external object id to any
        caller in any run -- no execution, no fact, no fence.
        """
        row = self._conn.execute(
            "SELECT state, external_object_id FROM effects"
            " WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        return None if row is None else {"state": row[0], "external_object_id": row[1]}

    def effect_state(self, idempotency_key: str, *, run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM effects WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        return None if row is None else row[0]

    def set_reconciliation(self, run_id: str, evidence: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reconciliation (run_id, evidence_json, at_us)"
            " VALUES (?,?,?)",
            (run_id, json.dumps(evidence, sort_keys=True), self._clock.now_us()),
        )

    def reconciliation_evidence(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT evidence_json FROM reconciliation WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def clear_reconciliation(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM reconciliation WHERE run_id = ?", (run_id,))

    def last_confirmed(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT effect_class, external_object_id, at_us FROM effects"
            " WHERE run_id = ? AND state = 'confirmed' ORDER BY at_us DESC LIMIT 10",
            (run_id,),
        ).fetchall()
        return [
            {"effect_class": r[0], "external_object_id": r[1], "at_us": r[2]} for r in rows
        ]

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
