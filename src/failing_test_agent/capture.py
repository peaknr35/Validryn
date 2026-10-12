from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from .atomic import atomic_append_text, atomic_write_text
from .db import Database, insert_artifact, insert_event
from .errors import StateError
from .execution import ExecutionBackend, ExecutionRequest, ExecutionResult
from .hashing import sha256_file
from .local_backend import node_matches
from .models import (
    ATTEMPT_TRANSITIONS,
    AttemptStatus,
    BaselineCapture,
    CommandCapture,
    RunEvent,
    RunStatus,
    utc_now,
)
from .worktree import head_commit, worktree_inventory

_FAILING_OUTCOMES = {"failed", "error"}
# A regression baseline with one of these statuses still lists per-test outcomes to compare against.
_COMPARABLE_STATUSES = {"passed", "tests_failed"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _transition(
    connection: sqlite3.Connection,
    attempt_id: str,
    current: AttemptStatus,
    new: AttemptStatus,
) -> None:
    if new not in ATTEMPT_TRANSITIONS.get(current, set()):
        raise StateError(f"Attempt transition {current} -> {new} is not allowed.")
    # The WHERE clause makes the check and the change one step, so two
    # processes cannot both move the same attempt.
    updated = connection.execute(
        "UPDATE attempts SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
        (new, utc_now().isoformat(), attempt_id, current),
    )
    if updated.rowcount != 1:
        raise StateError(f"Attempt {attempt_id} is not in state `{current}`.")


def _failing_nodes(result: ExecutionResult) -> list[str]:
    return [case.node_id for case in result.test_cases if case.outcome in _FAILING_OUTCOMES]


def _target_verdict(result: ExecutionResult) -> tuple[bool, str | None]:
    """Whether the requested test ran on the base commit and failed there."""
    if result.status == "passed":
        return False, "The target test passes on the base commit; there is no failure to investigate."
    if result.status != "tests_failed":
        reason = f"The target command did not produce a test result: {result.status}."
        return False, f"{reason} {result.detail}" if result.detail else reason
    if not result.intended_tests_executed:
        return False, "The requested test did not run on the base commit."
    failing = _failing_nodes(result)
    for intended in result.intended_node_ids:
        if not any(node_matches(node, intended) for node in failing):
            return False, f"The requested test did not fail on the base commit: {intended}"
    return True, None


class _Recorder:
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
        transition: tuple[AttemptStatus, AttemptStatus] | None = None,
    ) -> tuple[Path, str] | None:
        written: tuple[Path, str] | None = None
        if artifact:
            kind, file_name, content = artifact
            path = self.artifacts_dir / file_name
            atomic_write_text(path, content)
            written = (path, sha256_file(path))
            payload = {**payload, "artifact": file_name, "sha256": written[1]}
        event = RunEvent(
            event_id=_new_id("event"),
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            event_type=event_type,
            payload=payload,
        )
        with self.db.transaction() as connection:
            if transition:
                _transition(connection, self.attempt_id, *transition)
            if artifact and written:
                insert_artifact(
                    connection,
                    artifact_id=_new_id("artifact"),
                    run_id=self.run_id,
                    attempt_id=self.attempt_id,
                    kind=artifact[0],
                    path=written[0],
                    sha256=written[1],
                    created_at=event.created_at.isoformat(),
                )
            insert_event(connection, event)
        atomic_append_text(self.events_path, event.model_dump_json() + "\n")
        return written


def capture_baseline(
    home: Path,
    run_id: str,
    attempt_id: str,
    backend: ExecutionBackend,
    timeout_seconds: float = 300,
) -> BaselineCapture:
    """Run the target (and configured regression) command on the unpatched base commit."""
    db = Database(home.resolve() / "agent.db")
    db.initialize()
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
    if attempt["status"] != AttemptStatus.CREATED:
        raise StateError(
            f"Attempt {attempt_id} is `{attempt['status']}`; only a `created` attempt can be captured."
        )

    worktree = Path(attempt["worktree_path"])
    base_commit = attempt["base_commit"]
    if not worktree.is_dir():
        raise StateError(f"Worktree is missing: {worktree}")
    if head_commit(worktree) != base_commit:
        raise StateError("Worktree HEAD no longer matches the recorded base commit.")
    before = worktree_inventory(worktree)
    if before["diff"] or before["status"]:
        raise StateError("Worktree is not clean; a baseline must be captured on the base commit.")

    recorder = _Recorder(db, run_id, attempt_id, worktree.parent)
    recorder.record(
        "attempt.capturing",
        {"backend": backend.name, "sandboxed": backend.sandboxed},
        transition=(AttemptStatus.CREATED, AttemptStatus.CAPTURING),
    )

    def execute(command_kind: str, argv: list[str]) -> tuple[ExecutionResult, CommandCapture]:
        result = backend.run(
            ExecutionRequest(
                execution_id=_new_id("exec"),
                run_id=run_id,
                attempt_id=attempt_id,
                phase="baseline",
                command_kind=command_kind,
                argv=argv,
                worktree_root=worktree,
                working_directory=worktree,
                timeout_seconds=timeout_seconds,
            )
        )
        file_name = f"baseline-{command_kind}.json"
        _, digest = recorder.record(
            "execution.completed",
            {"phase": "baseline", "command_kind": command_kind, "status": result.status},
            artifact=(f"baseline_{command_kind}_result", file_name, result.model_dump_json(indent=2)),
        )
        return result, CommandCapture(
            command_kind=command_kind,
            argv=argv,
            status=result.status,
            exit_code=result.exit_code,
            detail=result.detail,
            intended_node_ids=result.intended_node_ids,
            intended_tests_executed=result.intended_tests_executed,
            failing_node_ids=_failing_nodes(result),
            command_environment_sha256=result.command_environment_sha256,
            result_artifact=file_name,
            result_sha256=digest,
        )

    try:
        target_result, target = execute("target", json.loads(run["test_argv_json"]))
        confirmed, reason = _target_verdict(target_result)

        regression: CommandCapture | None = None
        regression_status = "not_configured"
        if run["regression_argv_json"]:
            _, regression = execute("regression", json.loads(run["regression_argv_json"]))
            regression_status = (
                "captured" if regression.status in _COMPARABLE_STATUSES else "inconclusive"
            )

        unchanged = worktree_inventory(worktree) == before
        if not unchanged:
            reason = "Test execution changed files in the worktree."
        captured = confirmed and unchanged
        capture = BaselineCapture(
            run_id=run_id,
            attempt_id=attempt_id,
            base_commit=base_commit,
            backend=backend.name,
            sandboxed=backend.sandboxed,
            target=target,
            target_failure_confirmed=confirmed,
            regression=regression,
            regression_status=regression_status,
            worktree_unchanged=unchanged,
            outcome="captured" if captured else "failed",
            failure_reason=None if captured else reason,
        )
        final = AttemptStatus.CAPTURED if captured else AttemptStatus.FAILED
        recorder.record(
            f"attempt.{final}",
            {"reason": capture.failure_reason} if capture.failure_reason else {},
            artifact=("baseline_capture", "baseline.json", capture.model_dump_json(indent=2)),
            transition=(AttemptStatus.CAPTURING, final),
        )
        return capture
    except BaseException as exc:
        # Never leave an attempt looking like it is still capturing.
        final = (
            AttemptStatus.INTERRUPTED
            if isinstance(exc, KeyboardInterrupt)
            else AttemptStatus.FAILED
        )
        try:
            recorder.record(
                f"attempt.{final}",
                {"reason": f"{type(exc).__name__}: {exc}"},
                transition=(AttemptStatus.CAPTURING, final),
            )
        except StateError:
            pass
        raise
