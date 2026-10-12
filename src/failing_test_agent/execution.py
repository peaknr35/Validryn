from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

CaseOutcome = Literal["passed", "failed", "skipped", "xfailed", "xpassed", "error"]

ExecutionStatus = Literal[
    "passed",
    "tests_failed",
    "collection_error",
    "no_tests_collected",
    "execution_error",
    "interrupted",
    "timed_out",
    "cancelled",
]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    run_id: str
    attempt_id: str
    phase: Literal["baseline", "patched"]
    command_kind: Literal["target", "regression"]
    argv: list[str]
    worktree_root: Path
    working_directory: Path
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 300
    termination_grace_seconds: float = 5
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 1_000_000
    dependency_manifest_sha256: str | None = None

    @model_validator(mode="after")
    def _working_directory_inside_worktree(self) -> ExecutionRequest:
        root = self.worktree_root.resolve()
        cwd = self.working_directory.resolve()
        if cwd != root and root not in cwd.parents:
            raise ValueError("working_directory must be inside worktree_root")
        return self


class FailureLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Relative to the Pytest root directory, forward slashes.
    path: str
    line: int
    function: str


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    outcome: CaseOutcome
    # True only when the test body itself ran (not skipped, not stopped in setup).
    executed: bool = False
    duration_seconds: float | None = None
    message: str | None = None
    exception_type: str | None = None
    # Traceback frames inside the repository, outermost first.
    failure_locations: list[FailureLocation] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    status: ExecutionStatus
    exit_code: int | None
    test_cases: list[CaseResult]
    intended_node_ids: list[str]
    # Every intended node ID was collected and its body ran. With no intended
    # node IDs (a whole-suite command) this is true when at least one test ran.
    intended_tests_executed: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float
    command_environment_sha256: str
    # Why the status was chosen when it is not the plain reading of the exit code.
    detail: str | None = None


class ExecutionBackend(Protocol):
    name: str
    # False means repository code runs with the caller's own permissions.
    sandboxed: bool

    def available(self) -> bool: ...

    def run(self, request: ExecutionRequest) -> ExecutionResult: ...

    def cancel(self, execution_id: str) -> None: ...
