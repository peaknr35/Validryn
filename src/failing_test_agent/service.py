from __future__ import annotations

import json
import shlex
import shutil
import uuid
from pathlib import Path
from typing import Callable

from .atomic import atomic_write_json, atomic_write_text
from .db import Database, insert_artifact, insert_event
from .errors import CommandValidationError
from .hashing import sha256_file
from .models import CreatedRun, RunEvent, RunManifest
from .worktree import (
    create_detached_worktree,
    head_commit,
    remove_worktree,
    repository_root,
    require_clean_repository,
)

WorktreeCreator = Callable[[Path, Path, str], None]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def parse_test_command(command: str) -> list[str]:
    argv = shlex.split(command)
    allowed = bool(argv) and (
        argv[0] == "pytest"
        or (len(argv) >= 3 and argv[:3] == ["python", "-m", "pytest"])
    )
    if not allowed:
        raise CommandValidationError(
            "V1 accepts only `pytest ...` or `python -m pytest ...`."
        )
    return argv


def create_run(
    repo_path: Path,
    test_command: str,
    home: Path,
    worktree_creator: WorktreeCreator | None = None,
    regression_command: str | None = None,
) -> CreatedRun:
    repo = repository_root(repo_path)
    require_clean_repository(repo)
    base_commit = head_commit(repo)
    test_argv = parse_test_command(test_command)
    regression_argv = parse_test_command(regression_command) if regression_command else None
    run_id = _new_id("run")
    attempt_id = _new_id("attempt")
    run_dir = home.resolve() / "runs" / run_id
    attempt_dir = run_dir / "attempts" / "001"
    worktree_path = attempt_dir / "worktree"
    manifest_path = run_dir / "manifest.json"
    events_path = run_dir / "events.jsonl"
    db = Database(home.resolve() / "agent.db")
    creator = worktree_creator or create_detached_worktree
    worktree_created = False

    try:
        creator(repo, worktree_path, base_commit)
        worktree_created = True
        manifest = RunManifest(
            run_id=run_id,
            attempt_id=attempt_id,
            repository_path=str(repo),
            base_commit=base_commit,
            test_argv=test_argv,
            regression_argv=regression_argv,
        )
        run_event = RunEvent(
            event_id=_new_id("event"),
            run_id=run_id,
            event_type="run.created",
            payload={"base_commit": base_commit},
            created_at=manifest.created_at,
        )
        attempt_event = RunEvent(
            event_id=_new_id("event"),
            run_id=run_id,
            attempt_id=attempt_id,
            event_type="attempt.created",
            payload={"attempt_number": 1, "base_commit": base_commit},
            created_at=manifest.created_at,
        )
        manifest_payload = manifest.model_dump(mode="json")
        event_lines = "".join(
            event.model_dump_json() + "\n" for event in (run_event, attempt_event)
        )
        atomic_write_json(manifest_path, manifest_payload)
        atomic_write_text(events_path, event_lines)
        manifest_hash = sha256_file(manifest_path)
        timestamp = manifest.created_at.isoformat()

        db.initialize()
        with db.transaction() as connection:
            connection.execute(
                """INSERT INTO runs
                (id, repo_path, base_commit, test_argv_json, regression_argv_json,
                 status, current_attempt_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    str(repo),
                    base_commit,
                    json.dumps(test_argv),
                    json.dumps(regression_argv) if regression_argv else None,
                    "active",
                    attempt_id,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO attempts
                (id, run_id, attempt_number, base_commit, status,
                 worktree_path, is_current, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    attempt_id,
                    run_id,
                    1,
                    base_commit,
                    "created",
                    str(worktree_path),
                    timestamp,
                    timestamp,
                ),
            )
            for event in (run_event, attempt_event):
                insert_event(connection, event)
            insert_artifact(
                connection,
                artifact_id=_new_id("artifact"),
                run_id=run_id,
                attempt_id=attempt_id,
                kind="manifest",
                path=manifest_path,
                sha256=manifest_hash,
                created_at=timestamp,
            )
    except BaseException:
        if worktree_created or worktree_path.exists():
            remove_worktree(repo, worktree_path)
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    return CreatedRun(
        run_id=run_id,
        attempt_id=attempt_id,
        base_commit=base_commit,
        worktree_path=worktree_path,
        manifest_path=manifest_path,
    )
