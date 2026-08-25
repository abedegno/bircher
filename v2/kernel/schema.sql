PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS facts (
  seq                INTEGER PRIMARY KEY AUTOINCREMENT,  -- total order, clock-independent
  id                 TEXT    NOT NULL UNIQUE,
  run_id             TEXT    NOT NULL,
  kind               TEXT    NOT NULL,
  schema_version     INTEGER NOT NULL,
  mechanism_version  INTEGER NOT NULL,
  causal_command_id  TEXT,
  actor              TEXT    NOT NULL,
  observed_at_us     INTEGER NOT NULL,   -- UTC microseconds since epoch
  payload_json       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_by_run ON facts(run_id, seq);

-- Append-only, enforced by the database rather than by convention.
CREATE TRIGGER IF NOT EXISTS facts_no_update BEFORE UPDATE ON facts
BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS facts_no_delete BEFORE DELETE ON facts
BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;

CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  state         TEXT NOT NULL,
  base_repo     TEXT NOT NULL,
  base_sha      TEXT NOT NULL,
  created_at_us INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  hash  TEXT PRIMARY KEY,
  bytes BLOB NOT NULL
);
CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
