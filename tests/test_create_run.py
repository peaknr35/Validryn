from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failing_test_agent.cli import app
from failing_test_agent.core.errors import DirtyRepositoryError, WorktreeCreationError
from failing_test_agent.stages.create import create_run


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_cli_creates_run_attempt_worktree_manifest_and_events(
    clean_repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "fta-home"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "create",
            "--repo",
            str(clean_repo),
            "--test",
            "pytest test_calculator.py::test_divide",
        ],
        env={"FTA_HOME": str(home)},
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    manifest_path = Path(output["manifest_path"])
    worktree = Path(output["worktree_path"])
    manifest = json.loads(manifest_path.read_text())
    base_commit = git(clean_repo, "rev-parse", "HEAD")

    assert manifest["run_id"] == output["run_id"]
    assert manifest["attempt_id"] == output["attempt_id"]
    assert manifest["base_commit"] == base_commit
    assert manifest["run_status"] == "active"
    assert manifest["attempt_status"] == "created"
    assert git(worktree, "rev-parse", "HEAD") == base_commit
    detached = subprocess.run(
        ["git", "-C", str(worktree), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert detached.returncode == 1
    assert detached.stdout == ""

    events = [json.loads(line) for line in (manifest_path.parent / "events.jsonl").read_text().splitlines()]
    assert [event["event_type"] for event in events] == [
        "run.created",
        "attempt.created",
    ]

    connection = sqlite3.connect(home / "agent.db")
    connection.row_factory = sqlite3.Row
    run = connection.execute("SELECT * FROM runs").fetchone()
    attempt = connection.execute("SELECT * FROM attempts").fetchone()
    db_events = connection.execute("SELECT event_type FROM events ORDER BY sequence").fetchall()
    connection.close()

    assert run["status"] == "active"
    assert run["current_attempt_id"] == attempt["id"]
    assert attempt["attempt_number"] == 1
    assert attempt["is_current"] == 1
    assert [row["event_type"] for row in db_events] == [
        "run.created",
        "attempt.created",
    ]


def test_dirty_repository_is_rejected_without_changing_checkout(
    clean_repo: Path, tmp_path: Path
) -> None:
    dirty_file = clean_repo / "notes.txt"
    dirty_file.write_text("uncommitted\n")
    before_head = git(clean_repo, "rev-parse", "HEAD")
    before_status = git(clean_repo, "status", "--porcelain=v1", "--untracked-files=all")

    with pytest.raises(DirtyRepositoryError):
        create_run(
            clean_repo,
            "pytest test_calculator.py::test_divide",
            tmp_path / "fta-home",
        )

    assert git(clean_repo, "rev-parse", "HEAD") == before_head
    assert git(clean_repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert dirty_file.read_text() == "uncommitted\n"
    assert not (tmp_path / "fta-home" / "runs").exists()


def test_failed_worktree_creation_leaves_no_active_run_or_attempt(
    clean_repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "fta-home"

    def fail_worktree(repo: Path, destination: Path, commit: str) -> None:
        destination.mkdir(parents=True)
        raise WorktreeCreationError("injected failure")

    with pytest.raises(WorktreeCreationError, match="injected failure"):
        create_run(
            clean_repo,
            "pytest test_calculator.py::test_divide",
            home,
            worktree_creator=fail_worktree,
        )

    assert not (home / "runs").exists() or not any((home / "runs").iterdir())
    if (home / "agent.db").exists():
        connection = sqlite3.connect(home / "agent.db")
        run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        attempt_count = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        connection.close()
        assert run_count == 0
        assert attempt_count == 0
