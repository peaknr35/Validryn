from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import RunEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    test_argv_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_attempt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
    base_commit TEXT NOT NULL,
    status TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, attempt_number)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_attempt_per_run
ON attempts(run_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    attempt_id TEXT REFERENCES attempts(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    attempt_id TEXT REFERENCES attempts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def insert_event(connection: sqlite3.Connection, event: RunEvent) -> None:
    connection.execute(
        """INSERT INTO events
        (event_id, run_id, attempt_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            event.event_id,
            event.run_id,
            event.attempt_id,
            event.event_type,
            json.dumps(event.payload, sort_keys=True),
            event.created_at.isoformat(),
        ),
    )


def insert_artifact(
    connection: sqlite3.Connection,
    *,
    artifact_id: str,
    run_id: str,
    attempt_id: str | None,
    kind: str,
    path: Path,
    sha256: str,
    created_at: str,
) -> None:
    connection.execute(
        """INSERT INTO artifacts
        (id, run_id, attempt_id, kind, path, sha256, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (artifact_id, run_id, attempt_id, kind, str(path), sha256, created_at),
    )


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            # Columns added after the first release; older databases gain them here.
            for table, column, kind in (
                ("runs", "regression_argv_json", "TEXT"),
                ("attempts", "stage_pid", "INTEGER"),
                ("attempts", "stage_process_started", "TEXT"),
            ):
                existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
