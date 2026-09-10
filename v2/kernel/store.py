"""The only module that touches SQLite.

The engine is an explicitly reversible decision (spec, "Reversible, and not
worth arguing about now"); confining every SQLite construct here is what keeps
it reversible.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
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
        # Set by open(); the executor mints body files beside the db.
        self.path: str | None = None

    @classmethod
    def open(cls, path: Path | str, clock: Any = None) -> Store:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.executescript(_SCHEMA)
        store = cls(conn, clock or _SystemClock())
        store.path = str(path)
        return store

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

    def create_run(self, *, run_id: str, base_repo: str, base_sha: str,
                   state: str = "queued") -> None:
        """Create a run, and record the fact that says so.

        The row alone is not the truth: without a RUN_STARTED fact the
        projection has nothing to build from and returns None, so state is
        rebuildable only in principle. Facts are the truth; the row is
        derived.

        *state* is where the run is born (planning ruling 2): `queued` for a
        v1 enqueue whose spec and plan already exist, `shaping` for a run the
        front half creates from an issue (shaping spec §2).
        """
        from kernel.events import EventKind

        self._conn.execute(
            "INSERT INTO runs (run_id, state, base_repo, base_sha, created_at_us)"
            " VALUES (?,?,?,?,?)",
            (run_id, state, base_repo, base_sha, self._clock.now_us()),
        )
        self.append_fact(
            run_id=run_id, kind=EventKind.RUN_STARTED, actor="kernel",
            causal_command_id=None,
            payload={"base_sha": base_sha, "base_repo": base_repo, "state": state},
        )

    def all_run_ids(self) -> list[str]:
        """Every run, oldest first. The shadow report needs to sweep them all;
        without this the only way to enumerate runs was to already know their
        ids, which a report cannot."""
        return [r[0] for r in self._conn.execute(
            "SELECT run_id FROM runs ORDER BY created_at_us").fetchall()]

    def run_state(self, run_id: str) -> str:
        row = self._conn.execute(
            "SELECT state FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such run: {run_id}")
        return row[0]

    def set_run_state(self, run_id: str, state: str) -> None:
        """Keep the aggregate row in step with the transition facts.

        runs.state was written once as 'queued' and never updated, so a reader
        of the aggregate was misled while the real state lived only in facts.
        """
        self._conn.execute(
            "UPDATE runs SET state = ? WHERE run_id = ?", (state, run_id)
        )

    def run_base_sha(self, run_id: str) -> str:
        row = self._conn.execute(
            "SELECT base_sha FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such run: {run_id}")
        return row[0]

    def run_base_repo(self, run_id: str) -> str:
        row = self._conn.execute(
            "SELECT base_repo FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0]

    def run_owner(self, run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT owner FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else row[0]

    def run_version(self, run_id: str) -> int:
        return int(
            self._conn.execute(
                "SELECT version FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )

    @contextmanager
    def transaction(self):
        """One explicit transaction.

        Store.open uses isolation_level=None (autocommit), so BEGIN must be
        issued by hand. Without this, submit()'s CAS, fact and command-row
        writes commit independently: a crash between them advances the version
        with no command row, and the client's at-least-once retry then gets
        StaleVersion for a command that was accepted -- idempotency failing in
        exactly the crash it exists to survive.
        """
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def bump_version_cas(self, run_id: str, expected_version: int) -> bool:
        """Compare-and-swap the aggregate version. True when it applied."""
        cur = self._conn.execute(
            "UPDATE runs SET version = version + 1 WHERE run_id = ? AND version = ?",
            (run_id, expected_version),
        )
        return cur.rowcount > 0

    def acquire_generation(self, run_id: str, owner: str) -> int | None:
        """Atomically bump and return the fence generation, or None if unknown.

        A single statement: read-and-write in one, so concurrent callers
        serialise and each observes a distinct generation.
        """
        row = self._conn.execute(
            "UPDATE runs SET owner_generation = owner_generation + 1, owner = ?"
            " WHERE run_id = ? RETURNING owner_generation",
            (owner, run_id),
        ).fetchone()
        return None if row is None else int(row[0])

    def current_generation(self, run_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT owner_generation FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else int(row[0])

    def delete_blob(self, content_hash: str) -> None:
        """Drop an artifact. Content-addressed storage is not append-only
        forever: blobs are collected, and an approval must not outlive the
        object it approved."""
        self._conn.execute("DELETE FROM artifacts WHERE hash = ?", (content_hash,))

    def set_current_artifact(self, run_id: str, artifact_hash: str) -> None:
        self._conn.execute(
            "UPDATE runs SET current_artifact_hash = ? WHERE run_id = ?",
            (artifact_hash, run_id),
        )

    def current_artifact(self, run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT current_artifact_hash FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else row[0]

    def record_dispatch(
        self, dispatch_id: str, run_id: str, generation: int, actor: str, role: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO dispatches (id, run_id, generation, actor, role, at_us)"
            " VALUES (?,?,?,?,?,?)",
            (dispatch_id, run_id, generation, actor, role, self._clock.now_us()),
        )

    def dispatch_actor(self, run_id: str, generation: int) -> str | None:
        """The actor dispatched for EXACTLY this generation.

        Exact match, deliberately: falling back to the most recent dispatch is
        how one attempt inherits another attempt's identity.
        """
        row = self._conn.execute(
            "SELECT actor FROM dispatches WHERE run_id = ? AND generation = ?",
            (run_id, generation),
        ).fetchone()
        return None if row is None else row[0]

    def dispatch_role(self, run_id: str, generation: int) -> str | None:
        """The role dispatched for EXACTLY this generation."""
        row = self._conn.execute(
            "SELECT role FROM dispatches WHERE run_id = ? AND generation = ?",
            (run_id, generation),
        ).fetchone()
        return None if row is None else row[0]

    def has_confirmed_effect(self, run_id: str, effect_class: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM effects WHERE run_id = ? AND effect_class = ?"
            " AND state = 'confirmed'",
            (run_id, effect_class),
        ).fetchone() is not None

    def has_artifact(self, content_hash: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM artifacts WHERE hash = ?", (content_hash,)
        ).fetchone() is not None

    def uncertain_effects(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT idempotency_key, effect_class, generation FROM effects"
            # `intended` counts as unresolved: a real process death after
            # journalling never runs the handler that marks it uncertain, so
            # excluding it made such effects impossible to reconcile at all.
            " WHERE run_id = ? AND state IN ('uncertain','intended')"
            " ORDER BY at_us",
            (run_id,),
        ).fetchall()
        return [
            {"idempotency_key": r[0], "effect_class": r[1], "generation": r[2]}
            for r in rows
        ]

    def command_result(self, idempotency_key: str, *, run_id: str) -> dict | None:
        """Look up a prior command result by key WITHIN a run.

        Keyed globally, two genuinely different commands sharing a key
        collided and the second was answered with the first's result.
        """
        row = self._conn.execute(
            "SELECT accepted, result_json, name, request_hash FROM commands"
            " WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "accepted": row[0],
            "result": json.loads(row[1]),
            "name": row[2],
            "request_hash": row[3],
        }

    def record_command(
        self, key: str, run_id: str, name: str, accepted: bool, result: dict,
        *, request_hash: str,
    ) -> None:
        """Record a command outcome with a fingerprint of the REQUEST.

        Storing only the name meant a replay check could not tell the same
        command with a different payload from a genuine retry, and answered
        the former with the earlier result.
        """
        self._conn.execute(
            "INSERT INTO commands (idempotency_key, run_id, name, accepted,"
            " result_json, request_hash, at_us) VALUES (?,?,?,?,?,?,?)",
            (key, run_id, name, int(accepted), json.dumps(result, sort_keys=True),
             request_hash, self._clock.now_us()),
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

    def mark_effect(
        self, idempotency_key: str, state: str, external_object_id, *, run_id: str
    ) -> None:
        """Update an effect's state WITHIN a run.

        Reads and uniqueness were scoped per run while this UPDATE was not, so
        confirming one run's effect also confirmed another run's effect that
        happened to share the key.
        """
        self._conn.execute(
            "UPDATE effects SET state = ?, external_object_id = ?"
            " WHERE idempotency_key = ? AND run_id = ?",
            (state, external_object_id, idempotency_key, run_id),
        )

    def effect_by_key(self, idempotency_key: str, *, run_id: str) -> dict | None:
        """Look up an effect by key WITHIN a run.

        Keyed globally, a confirmed key returned its external object id to any
        caller in any run -- no execution, no fact, no fence.
        """
        row = self._conn.execute(
            "SELECT state, external_object_id, effect_class, intent_json, generation"
            " FROM effects WHERE idempotency_key = ? AND run_id = ?",
            (idempotency_key, run_id),
        ).fetchone()
        # effect_class is the JOURNALLED one, not the retry's. A halt raised on
        # a retry must name the class that is actually uncertain: a retry that
        # declared a different class would otherwise put the wrong resource in
        # front of the operator.
        return None if row is None else {
            "state": row[0], "external_object_id": row[1], "effect_class": row[2],
            "intent": json.loads(row[3]), "generation": row[4],
        }

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
        return [self._fact_from_row(r) for r in rows]

    def _fact_from_row(self, r) -> Fact:
        return Fact(*r[:-1], payload=json.loads(r[-1]))

    def read_blob(self, content_hash: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT bytes FROM artifacts WHERE hash = ?", (content_hash,)
        ).fetchone()
        return None if row is None else bytes(row[0])

    def set_phase_artifact(self, run_id: str, phase: str, content_hash: str) -> None:
        self._conn.execute(
            "INSERT INTO phase_artifacts (run_id, phase, artifact_hash) VALUES (?,?,?)"
            " ON CONFLICT(run_id, phase) DO UPDATE SET artifact_hash = excluded.artifact_hash",
            (run_id, phase, content_hash),
        )

    def phase_artifact(self, run_id: str, phase: str) -> str | None:
        row = self._conn.execute(
            "SELECT artifact_hash FROM phase_artifacts WHERE run_id = ? AND phase = ?",
            (run_id, phase),
        ).fetchone()
        return None if row is None else row[0]

    def facts_of_kind(self, run_id: str, *kinds: str) -> list[Fact]:
        if not kinds:
            return []
        marks = ",".join("?" * len(kinds))
        rows = self._conn.execute(
            "SELECT seq, id, run_id, kind, schema_version, mechanism_version,"
            " causal_command_id, actor, observed_at_us, payload_json FROM facts"
            f" WHERE run_id = ? AND kind IN ({marks}) ORDER BY seq",
            (run_id, *kinds),
        ).fetchall()
        return [self._fact_from_row(r) for r in rows]

    def newest_fact(self, run_id: str, *kinds: str) -> Fact | None:
        facts = self.facts_of_kind(run_id, *kinds)
        return facts[-1] if facts else None

    def effects_for(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT idempotency_key, effect_class, generation, state,"
            " external_object_id, intent_json, at_us FROM effects"
            " WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [{
            "idempotency_key": r[0], "effect_class": r[1], "generation": r[2],
            "state": r[3], "external_object_id": r[4],
            "intent": json.loads(r[5]), "at_us": r[6],
        } for r in rows]

    def dispatches_for(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT generation, actor, role, at_us FROM dispatches"
            " WHERE run_id = ? ORDER BY generation",
            (run_id,),
        ).fetchall()
        return [{"generation": r[0], "actor": r[1], "role": r[2], "at_us": r[3]}
                for r in rows]

    def open_run_ids(self, prefix: str, states: frozenset[str]) -> list[str]:
        if "%" in prefix or "_" in prefix:
            raise ValueError(f"prefix must not contain LIKE wildcards: {prefix!r}")
        if not states:
            return []
        marks = ",".join("?" * len(states))
        rows = self._conn.execute(
            "SELECT run_id FROM runs WHERE run_id LIKE ? AND state IN"
            f" ({marks}) ORDER BY created_at_us",
            (prefix + "-%", *sorted(states)),
        ).fetchall()
        return [r[0] for r in rows]
