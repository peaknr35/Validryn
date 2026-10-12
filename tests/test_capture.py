from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failing_test_agent.capture import capture_baseline
from failing_test_agent.cli import app
from failing_test_agent.db import Database
from failing_test_agent.docker_backend import DockerBackend
from failing_test_agent.errors import StateError
from failing_test_agent.execution import ExecutionRequest, ExecutionResult
from failing_test_agent.hashing import sha256_file
from failing_test_agent.local_backend import TrustedLocalBackend
from failing_test_agent.models import CreatedRun
from failing_test_agent.liveness import process_start_token
from failing_test_agent.service import create_run
from failing_test_agent.stages import reconcile_lost_attempts

TARGET = "pytest test_calculator.py::test_divide"


def new_run(repo: Path, home: Path, test: str = TARGET, regression: str | None = None) -> CreatedRun:
    return create_run(repo, test, home, regression_command=regression)


def attempt_status(home: Path, attempt_id: str) -> str:
    connection = sqlite3.connect(home / "agent.db")
    try:
        return connection.execute(
            "SELECT status FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
    finally:
        connection.close()


def event_types(home: Path, run_id: str) -> list[str]:
    connection = sqlite3.connect(home / "agent.db")
    try:
        rows = connection.execute(
            "SELECT event_type FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def test_capture_confirms_target_fails_on_base_commit(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.outcome == "captured"
    assert capture.sandboxed is False
    assert capture.target.status == "tests_failed"
    assert capture.target_failure_confirmed is True
    assert capture.target.failing_node_ids == ["test_calculator.py::test_divide"]
    assert capture.regression is None
    assert capture.regression_status == "not_configured"
    assert capture.worktree_unchanged is True
    assert attempt_status(home, run.attempt_id) == "captured"

    expected_events = [
        "run.created",
        "attempt.created",
        "attempt.capturing",
        "execution.completed",
        "attempt.captured",
    ]
    assert event_types(home, run.run_id) == expected_events
    jsonl = (run.manifest_path.parent / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["event_type"] for line in jsonl] == expected_events

    artifacts = run.worktree_path.parent / "artifacts"
    stored = json.loads((artifacts / "baseline.json").read_text())
    assert stored["target"]["result_sha256"] == sha256_file(artifacts / "baseline-target.json")
    ExecutionResult.model_validate_json((artifacts / "baseline-target.json").read_text())
    connection = sqlite3.connect(home / "agent.db")
    recorded = dict(
        connection.execute(
            "SELECT kind, sha256 FROM artifacts WHERE attempt_id = ?", (run.attempt_id,)
        ).fetchall()
    )
    connection.close()
    assert recorded["baseline_capture"] == sha256_file(artifacts / "baseline.json")
    assert recorded["baseline_target_result"] == stored["target"]["result_sha256"]


def test_capture_records_regression_baseline_per_test(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home, regression="pytest")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.outcome == "captured"
    assert capture.regression_status == "captured"
    assert capture.regression.status == "tests_failed"
    # The suite includes the target; it must not be counted as a pre-existing regression failure.
    assert capture.regression.failing_node_ids == []
    assert capture.regression.excluded_target_node_ids == ["test_calculator.py::test_divide"]
    assert (
        capture.regression.command_environment_sha256
        != capture.target.command_environment_sha256
    )
    result = ExecutionResult.model_validate_json(
        (run.worktree_path.parent / "artifacts" / "baseline-regression.json").read_text()
    )
    assert {case.node_id: case.outcome for case in result.test_cases} == {
        "test_calculator.py::test_divide": "failed",
        "test_calculator.py::test_add": "passed",
    }


def test_regression_baseline_keeps_failures_other_than_the_target(
    make_repo, tmp_path: Path
) -> None:
    repo = make_repo(
        {
            "test_mixed.py": (
                "def test_target():\n    assert False\n\n"
                "def test_already_broken():\n    assert False\n\n"
                "def test_fine():\n    assert True\n"
            )
        }
    )
    home = tmp_path / "home"
    run = new_run(repo, home, test="pytest test_mixed.py::test_target", regression="pytest")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.regression.failing_node_ids == ["test_mixed.py::test_already_broken"]
    assert capture.regression.excluded_target_node_ids == ["test_mixed.py::test_target"]


def test_killed_stage_is_recovered_as_interrupted(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()

    def set_owner(pid: int, token: str | None) -> None:
        connection = sqlite3.connect(home / "agent.db")
        connection.execute(
            "UPDATE attempts SET status = 'capturing', stage_pid = ?, stage_process_started = ?"
            " WHERE id = ?",
            (pid, token, run.attempt_id),
        )
        connection.commit()
        connection.close()

    # Owned by this live process: left alone.
    set_owner(os.getpid(), process_start_token(os.getpid()))
    assert reconcile_lost_attempts(home) == []
    assert attempt_status(home, run.attempt_id) == "capturing"

    # Same PID but a different start time means the PID was reused by another process.
    set_owner(os.getpid(), "not-this-process")
    assert reconcile_lost_attempts(home) == [run.attempt_id]
    assert attempt_status(home, run.attempt_id) == "interrupted"

    set_owner(dead.pid, "gone")
    assert reconcile_lost_attempts(home) == [run.attempt_id]
    assert attempt_status(home, run.attempt_id) == "interrupted"
    assert event_types(home, run.run_id)[-1] == "attempt.interrupted"
    last = (run.manifest_path.parent / "events.jsonl").read_text().splitlines()[-1]
    assert json.loads(last)["payload"]["reason"] == "process_lost"


def test_cli_recovers_lost_attempts_on_startup(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)
    connection = sqlite3.connect(home / "agent.db")
    connection.execute("UPDATE attempts SET status = 'capturing' WHERE id = ?", (run.attempt_id,))
    connection.commit()
    connection.close()

    result = CliRunner().invoke(
        app, ["attempt", "capture", run.run_id, run.attempt_id], env={"FTA_HOME": str(home)}
    )

    assert "marked interrupted" in result.output
    assert attempt_status(home, run.attempt_id) == "interrupted"


def test_target_that_passes_on_baseline_fails_the_attempt(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home, test="pytest test_calculator.py::test_add")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.outcome == "failed"
    assert capture.target_failure_confirmed is False
    assert "passes on the base commit" in capture.failure_reason
    assert attempt_status(home, run.attempt_id) == "failed"
    assert event_types(home, run.run_id)[-1] == "attempt.failed"


def test_missing_target_test_fails_the_attempt(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home, test="pytest test_calculator.py::test_missing")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.outcome == "failed"
    assert capture.target.status == "collection_error"
    assert attempt_status(home, run.attempt_id) == "failed"


def test_other_failures_do_not_confirm_the_requested_test(make_repo, tmp_path: Path) -> None:
    repo = make_repo(
        {
            "test_mixed.py": (
                "def test_target():\n    assert True\n\n"
                "def test_unrelated():\n    assert False\n"
            )
        }
    )
    home = tmp_path / "home"
    run = new_run(repo, home, test="pytest test_mixed.py::test_target test_mixed.py")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.target.status == "tests_failed"
    assert capture.target_failure_confirmed is False
    assert capture.outcome == "failed"


def test_execution_that_changes_the_worktree_fails_the_attempt(make_repo, tmp_path: Path) -> None:
    repo = make_repo(
        {
            "test_writer.py": (
                "from pathlib import Path\n\n"
                "def test_writes():\n"
                "    Path('leftover.txt').write_text('x')\n"
                "    assert False\n"
            )
        }
    )
    home = tmp_path / "home"
    run = new_run(repo, home, test="pytest test_writer.py::test_writes")

    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert capture.target_failure_confirmed is True
    assert capture.worktree_unchanged is False
    assert capture.outcome == "failed"
    assert "changed files" in capture.failure_reason


def test_attempt_cannot_be_captured_twice(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)
    capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())
    events_before = event_types(home, run.run_id)

    with pytest.raises(StateError, match="needs a `created` attempt"):
        capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())

    assert attempt_status(home, run.attempt_id) == "captured"
    assert event_types(home, run.run_id) == events_before


def test_unknown_run_or_modified_worktree_is_refused(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)

    with pytest.raises(StateError, match="Run not found"):
        capture_baseline(home, "run_missing", run.attempt_id, TrustedLocalBackend())

    (run.worktree_path / "calculator.py").write_text("def divide(a, b):\n    return a / b\n")
    with pytest.raises(StateError, match="not clean"):
        capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())
    assert attempt_status(home, run.attempt_id) == "created"


class RaisingBackend:
    name = "raising"
    sandboxed = False

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def available(self) -> bool:
        return True

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        raise self.error

    def cancel(self, execution_id: str) -> None:
        pass


@pytest.mark.parametrize(
    ("error", "expected"),
    [(KeyboardInterrupt(), "interrupted"), (RuntimeError("boom"), "failed")],
)
def test_capture_that_dies_does_not_leave_attempt_capturing(
    clean_repo: Path, tmp_path: Path, error: BaseException, expected: str
) -> None:
    home = tmp_path / "home"
    run = new_run(clean_repo, home)

    with pytest.raises(type(error)):
        capture_baseline(home, run.run_id, run.attempt_id, RaisingBackend(error))

    assert attempt_status(home, run.attempt_id) == expected
    assert event_types(home, run.run_id)[-1] == f"attempt.{expected}"


def test_cli_capture_requires_backend_choice_and_consent(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DockerBackend, "available", lambda self: False)
    home = tmp_path / "home"
    run = new_run(clean_repo, home)
    runner = CliRunner()
    env = {"FTA_HOME": str(home)}
    base = ["attempt", "capture", run.run_id, run.attempt_id]

    default = runner.invoke(app, base, env=env)
    assert default.exit_code == 2
    assert "Docker execution is unavailable" in default.output

    no_consent = runner.invoke(app, [*base, "--backend", "trusted-local"], env=env)
    assert no_consent.exit_code == 2
    assert "not a security sandbox" in no_consent.output
    assert attempt_status(home, run.attempt_id) == "created"

    consented = runner.invoke(
        app, [*base, "--backend", "trusted-local", "--allow-unsafe-local-execution"], env=env
    )
    assert consented.exit_code == 0, consented.output
    assert "NOT SANDBOXED" in consented.output
    assert "Target fails on baseline:   YES" in consented.output
    assert attempt_status(home, run.attempt_id) == "captured"


def test_existing_database_gains_regression_column(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE runs (id TEXT PRIMARY KEY, repo_path TEXT NOT NULL,
        base_commit TEXT NOT NULL, test_argv_json TEXT NOT NULL, status TEXT NOT NULL,
        current_attempt_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    connection.commit()
    connection.close()

    Database(path).initialize()
    Database(path).initialize()

    connection = sqlite3.connect(path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
    connection.close()
    assert columns.count("regression_argv_json") == 1
