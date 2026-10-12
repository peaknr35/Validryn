from __future__ import annotations

import json
from pathlib import Path

from ..core.db import Database
from ..execution.contracts import ExecutionBackend, ExecutionRequest, ExecutionResult
from ..execution.local_backend import node_matches
from ..core.models import AttemptStatus, BaselineCapture, CommandCapture
from ..core.lifecycle import (
    Recorder,
    load_current_attempt,
    new_id,
    require_pristine_worktree,
    stage_guard,
)
from ..core.worktree import worktree_inventory

_FAILING_OUTCOMES = {"failed", "error"}
# A regression baseline with one of these statuses still lists per-test outcomes to compare against.
_COMPARABLE_STATUSES = {"passed", "tests_failed"}


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


def _is_target_node(node_id: str, target: ExecutionResult) -> bool:
    if target.intended_node_ids:
        return any(node_matches(node_id, intended) for intended in target.intended_node_ids)
    return any(case.node_id == node_id for case in target.test_cases)


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
    run, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.CREATED)
    worktree, before = require_pristine_worktree(attempt)
    base_commit = attempt["base_commit"]

    recorder = Recorder(db, run_id, attempt_id, worktree.parent)
    recorder.record(
        "attempt.capturing",
        {"backend": backend.name, "sandboxed": backend.sandboxed},
        transition_to=(AttemptStatus.CREATED, AttemptStatus.CAPTURING),
    )

    def execute(command_kind: str, argv: list[str]) -> tuple[ExecutionResult, str, str]:
        result = backend.run(
            ExecutionRequest(
                execution_id=new_id("exec"),
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
        digest = recorder.record(
            "execution.completed",
            {"phase": "baseline", "command_kind": command_kind, "status": result.status},
            artifact=(f"baseline_{command_kind}_result", file_name, result.model_dump_json(indent=2)),
        )
        return result, file_name, digest

    def summarize(
        command_kind: str,
        argv: list[str],
        result: ExecutionResult,
        file_name: str,
        digest: str,
        target: ExecutionResult | None = None,
    ) -> CommandCapture:
        failing = _failing_nodes(result)
        excluded: list[str] = []
        if target is not None:
            excluded = [
                case.node_id for case in result.test_cases if _is_target_node(case.node_id, target)
            ]
            failing = [node for node in failing if node not in excluded]
        return CommandCapture(
            command_kind=command_kind,
            argv=argv,
            status=result.status,
            exit_code=result.exit_code,
            detail=result.detail,
            intended_node_ids=result.intended_node_ids,
            intended_tests_executed=result.intended_tests_executed,
            failing_node_ids=failing,
            excluded_target_node_ids=excluded,
            command_environment_sha256=result.command_environment_sha256,
            result_artifact=file_name,
            result_sha256=digest,
        )

    with stage_guard(recorder, AttemptStatus.CAPTURING):
        target_argv = json.loads(run["test_argv_json"])
        target_result, target_file, target_digest = execute("target", target_argv)
        confirmed, reason = _target_verdict(target_result)

        regression: CommandCapture | None = None
        regression_status = "not_configured"
        if run["regression_argv_json"]:
            regression_argv = json.loads(run["regression_argv_json"])
            regression_result, regression_file, regression_digest = execute(
                "regression", regression_argv
            )
            regression = summarize(
                "regression",
                regression_argv,
                regression_result,
                regression_file,
                regression_digest,
                target=target_result,
            )
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
            target=summarize("target", target_argv, target_result, target_file, target_digest),
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
            transition_to=(AttemptStatus.CAPTURING, final),
        )
        return capture
