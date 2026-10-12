"""Shared mechanics for attempt stages: preconditions, transitions, recording, recovery."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .atomic import atomic_append_text, atomic_write_text
from .db import Database, insert_artifact, insert_event
from .errors import StateError
from .hashing import sha256_file
from .liveness import process_start_token
from .models import ATTEMPT_TRANSITIONS, AttemptStatus, RunEvent, RunStatus, utc_now
from .worktree import head_commit, worktree_inventory

# States that mean "a process is working on this attempt right now".
IN_PROGRESS = {
    status
    for status, targets in ATTEMPT_TRANSITIONS.items()
    if AttemptStatus.INTERRUPTED in targets
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def transition(
    connection: sqlite3.Connection,
    attempt_id: str,
    current: AttemptStatus,
    new: AttemptStatus,
) -> None:
    if new not in ATTEMPT_TRANSITIONS.get(current, set()):
        raise StateError(f"Attempt transition {current} -> {new} is not allowed.")
    # An in-progress state records which process owns it, so a later run can
    # tell a live stage from one whose process was killed.
    owner = (os.getpid(), process_start_token(os.getpid())) if new in IN_PROGRESS else (None, None)
    # The WHERE clause makes the check and the change one step, so two
    # processes cannot both move the same attempt.
    updated = connection.execute(
        """UPDATE attempts
        SET status = ?, updated_at = ?, stage_pid = ?, stage_process_started = ?
        WHERE id = ? AND status = ?""",
        (new, utc_now().isoformat(), *owner, attempt_id, current),
    )
    if updated.rowcount != 1:
        raise StateError(f"Attempt {attempt_id} is not in state `{current}`.")


class Recorder:
    """Writes artifacts and events for one attempt: file first, then one DB transaction."""

    def __init__(self, db: Database, run_id: str, attempt_id: str, attempt_dir: Path) -> None:
        self.db = db
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.artifacts_dir = attempt_dir / "artifacts"
        self.events_path = attempt_dir.parent.parent / "events.jsonl"

    def record(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        artifact: tuple[str, str, str] | None = None,
        transition_to: tuple[AttemptStatus, AttemptStatus] | None = None,
    ) -> str | None:
        """Record one event; returns the SHA-256 of the artifact (kind, file name, content) if given."""
        digest: str | None = None
        path: Path | None = None
        if artifact:
            kind, file_name, content = artifact
            path = self.artifacts_dir / file_name
            atomic_write_text(path, content)
            digest = sha256_file(path)
            payload = {**payload, "artifact": file_name, "sha256": digest}
        event = RunEvent(
            event_id=new_id("event"),
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            event_type=event_type,
            payload=payload,
        )
        with self.db.transaction() as connection:
            if transition_to:
                transition(connection, self.attempt_id, *transition_to)
            if artifact and path and digest:
                insert_artifact(
                    connection,
                    artifact_id=new_id("artifact"),
                    run_id=self.run_id,
                    attempt_id=self.attempt_id,
                    kind=artifact[0],
                    path=path,
                    sha256=digest,
                    created_at=event.created_at.isoformat(),
                )
            insert_event(connection, event)
        atomic_append_text(self.events_path, event.model_dump_json() + "\n")
        return digest


def load_current_attempt(
    db: Database, run_id: str, attempt_id: str, required: AttemptStatus
) -> tuple[sqlite3.Row, sqlite3.Row]:
    """The run and attempt rows, if the attempt is current, the run active, and the state as required."""
    connection = db.connect()
    try:
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        attempt = connection.execute(
            "SELECT * FROM attempts WHERE id = ? AND run_id = ?", (attempt_id, run_id)
        ).fetchone()
    finally:
        connection.close()
    if run is None:
        raise StateError(f"Run not found: {run_id}")
    if attempt is None:
        raise StateError(f"Attempt {attempt_id} not found in run {run_id}.")
    if run["status"] != RunStatus.ACTIVE:
        raise StateError(f"Run {run_id} is `{run['status']}`, not active.")
    if not attempt["is_current"]:
        raise StateError(f"Attempt {attempt_id} is not the current attempt.")
    if attempt["status"] != required:
        raise StateError(
            f"Attempt {attempt_id} is `{attempt['status']}`; this step needs a `{required}` attempt."
        )
    return run, attempt


def require_pristine_worktree(attempt: sqlite3.Row) -> tuple[Path, dict[str, str]]:
    """The worktree path and its inventory, if it still is the recorded base commit, unmodified."""
    worktree = Path(attempt["worktree_path"])
    if not worktree.is_dir():
        raise StateError(f"Worktree is missing: {worktree}")
    if head_commit(worktree) != attempt["base_commit"]:
        raise StateError("Worktree HEAD no longer matches the recorded base commit.")
    inventory = worktree_inventory(worktree)
    if inventory["diff"] or inventory["status"]:
        raise StateError("Worktree is not clean; it no longer matches the base commit.")
    return worktree, inventory


@contextmanager
def stage_guard(recorder: Recorder, in_progress: AttemptStatus) -> Iterator[None]:
    """Never leave an attempt looking like a stage is still running after it has died."""
    try:
        yield
    except BaseException as exc:
        final = (
            AttemptStatus.INTERRUPTED
            if isinstance(exc, KeyboardInterrupt)
            else AttemptStatus.FAILED
        )
        try:
            recorder.record(
                f"attempt.{final}",
                {"reason": f"{type(exc).__name__}: {exc}", "stage": str(in_progress)},
                transition_to=(in_progress, final),
            )
        except StateError:
            pass
        raise


def reconcile_lost_attempts(home: Path) -> list[str]:
    """Mark attempts whose stage process no longer exists as interrupted; returns their IDs.

    Ctrl+C is handled by `stage_guard`. This covers a process that was killed
    outright and so never got the chance to record anything.
    """
    db = Database(home.resolve() / "agent.db")
    if not db.path.exists():
        return []
    db.initialize()
    connection = db.connect()
    try:
        placeholders = ",".join("?" for _ in IN_PROGRESS)
        rows = connection.execute(
            f"SELECT * FROM attempts WHERE status IN ({placeholders})",
            [str(status) for status in sorted(IN_PROGRESS)],
        ).fetchall()
    finally:
        connection.close()
    recovered = []
    for row in rows:
        pid = row["stage_pid"]
        token = process_start_token(pid) if pid is not None else None
        if token is not None and token == row["stage_process_started"]:
            continue
        recorder = Recorder(db, row["run_id"], row["id"], Path(row["worktree_path"]).parent)
        try:
            recorder.record(
                "attempt.interrupted",
                {"reason": "process_lost", "stage": row["status"], "pid": pid},
                transition_to=(AttemptStatus(row["status"]), AttemptStatus.INTERRUPTED),
            )
        except StateError:
            continue
        recovered.append(row["id"])
    return recovered
