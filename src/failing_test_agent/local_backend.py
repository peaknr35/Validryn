from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

from pydantic import BaseModel, ConfigDict, ValidationError

from . import pytest_reporter
from .environment import command_environment_identity, command_environment_sha256
from .errors import CommandValidationError
from .execution import CaseResult, ExecutionRequest, ExecutionResult, ExecutionStatus
from .hashing import sha256_file
from .process_tree import ProcessTree

# cacheprovider is disabled so a run does not write .pytest_cache into the worktree.
INJECTED_PYTEST_ARGS = ["-p", "failing_test_agent.pytest_reporter", "-p", "no:cacheprovider"]

# Host variables a Python subprocess needs to start. Nothing else is inherited:
# no home directory, profile, or credential variables.
INHERITED_ENVIRONMENT_KEYS = (
    ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP"]
    if sys.platform == "win32"
    else ["PATH", "TMPDIR", "LANG"]
)

FIXED_ENVIRONMENT = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONUTF8": "1",
}

_FAILING_OUTCOMES = {"failed", "error"}


class PytestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    exit_status: int
    collected: list[str]
    collection_errors: list[dict[str, object]]
    cases: list[CaseResult]


class BoundedReader(threading.Thread):
    """Drains a pipe to EOF, keeping at most `limit` bytes and discarding the rest."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self.buffer = bytearray()
        self.truncated = False

    def run(self) -> None:
        while chunk := self._stream.read1(65536):
            room = self._limit - len(self.buffer)
            if room > 0:
                self.buffer += chunk[:room]
            if len(chunk) > room:
                self.truncated = True

    def text(self) -> str:
        return bytes(self.buffer).decode("utf-8", errors="replace")


def pytest_arguments(argv: list[str]) -> list[str]:
    if argv[:1] == ["pytest"]:
        return argv[1:]
    if argv[:3] == ["python", "-m", "pytest"]:
        return argv[3:]
    raise CommandValidationError("V1 accepts only `pytest ...` or `python -m pytest ...`.")


def intended_node_ids(pytest_args: list[str]) -> list[str]:
    return [
        arg.replace("\\", "/")
        for arg in pytest_args
        if "::" in arg and not arg.startswith("-")
    ]


def _matches(node_id: str, intended: str) -> bool:
    # A class or parametrized function is named by a prefix of its node IDs.
    return node_id == intended or node_id.startswith((intended + "[", intended + "::"))


def intended_tests_executed(intended: list[str], cases: list[CaseResult]) -> bool:
    executed = [case.node_id for case in cases if case.executed]
    if not intended:
        return bool(executed)
    return all(any(_matches(node, target) for node in executed) for target in intended)


def classify(
    exit_code: int, report: PytestReport | None, report_problem: str | None
) -> tuple[ExecutionStatus, str | None]:
    """Map Pytest's exit code to a status, refusing to trust a report that disagrees with it."""
    if report is None:
        if exit_code in (0, 1, 2, 5):
            return "execution_error", report_problem or "Pytest report is missing."
        if exit_code == 4:
            return "execution_error", "Pytest rejected the command line before starting a session."
        return "execution_error", report_problem
    if report.exit_status != exit_code:
        return (
            "execution_error",
            f"Report exit status {report.exit_status} disagrees with process exit code {exit_code}.",
        )
    failing = [case for case in report.cases if case.outcome in _FAILING_OUTCOMES]
    if exit_code == 0:
        if failing or not report.cases:
            return "execution_error", "Exit code 0 disagrees with the per-test report."
        return "passed", None
    if exit_code == 1:
        if not failing:
            return "execution_error", "Exit code 1 but the report lists no failing test."
        return "tests_failed", None
    if exit_code == 2:
        if report.collection_errors:
            return "collection_error", None
        return "interrupted", None
    if exit_code == 4:
        return "collection_error", "Pytest could not resolve the requested tests."
    if exit_code == 5:
        return "no_tests_collected", None
    return "execution_error", f"Pytest exit code {exit_code}."


class TrustedLocalBackend:
    """Runs Pytest directly on this machine with the permissions of the current user.

    This is NOT a sandbox: repository code can read files, use the network and
    start processes. Use it only for repositories you control. It bounds time
    and captured output and cleans up the process tree; it isolates nothing.
    """

    name = "trusted-local"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}

    def available(self) -> bool:
        return importlib.util.find_spec("pytest") is not None

    def cancel(self, execution_id: str) -> None:
        with self._lock:
            event = self._cancel_events.get(execution_id)
        if event is not None:
            event.set()

    def _identity_sha256(self, request: ExecutionRequest, selected: dict[str, str]) -> str:
        identity = command_environment_identity(
            request,
            backend=self.name,
            python_executable=sys.executable,
            python_version=platform.python_version(),
            pytest_version=importlib.metadata.version("pytest"),
            reporter_plugin_sha256=sha256_file(Path(pytest_reporter.__file__)),
            selected_environment=selected,
            runner_policy={
                "injected_pytest_args": INJECTED_PYTEST_ARGS,
                "inherited_environment_keys": INHERITED_ENVIRONMENT_KEYS,
                "timeout_seconds": request.timeout_seconds,
                "termination_grace_seconds": request.termination_grace_seconds,
                "max_stdout_bytes": request.max_stdout_bytes,
                "max_stderr_bytes": request.max_stderr_bytes,
            },
        )
        return command_environment_sha256(identity)

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        pytest_args = pytest_arguments(request.argv)
        intended = intended_node_ids(pytest_args)
        selected = {**FIXED_ENVIRONMENT, **request.environment}
        environment_sha256 = self._identity_sha256(request, selected)

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[request.execution_id] = cancel_event

        # The report lives outside the worktree so the run leaves the source tree untouched.
        report_dir = Path(tempfile.mkdtemp(prefix="fta-exec-"))
        report_path = report_dir / "report.json"
        env = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
        env.update(selected)
        env[pytest_reporter.REPORT_ENV] = str(report_path)
        command = [sys.executable, "-m", "pytest", *INJECTED_PYTEST_ARGS, *pytest_args]

        stopped: ExecutionStatus | None = None
        started = time.monotonic()
        try:
            tree = ProcessTree(command, request.working_directory, env)
            process = tree.process
            stdout = BoundedReader(process.stdout, request.max_stdout_bytes)
            stderr = BoundedReader(process.stderr, request.max_stderr_bytes)
            stdout.start()
            stderr.start()
            try:
                deadline = started + request.timeout_seconds
                while process.poll() is None:
                    if cancel_event.wait(0.05):
                        stopped = "cancelled"
                    elif time.monotonic() >= deadline:
                        stopped = "timed_out"
                    if stopped:
                        tree.request_stop()
                        try:
                            process.wait(timeout=request.termination_grace_seconds)
                        except Exception:
                            pass
                        break
            finally:
                # Always, even after a normal exit: nothing the tests started may outlive the run.
                tree.kill()
                process.wait()
                stdout.join(timeout=10)
                stderr.join(timeout=10)
                tree.close()
            duration = time.monotonic() - started
            exit_code = process.returncode

            report: PytestReport | None = None
            report_problem: str | None = None
            if report_path.exists():
                try:
                    report = PytestReport.model_validate(json.loads(report_path.read_text("utf-8")))
                except (ValueError, ValidationError) as exc:
                    report_problem = f"Pytest report is invalid: {exc}"
        finally:
            with self._lock:
                self._cancel_events.pop(request.execution_id, None)
            shutil.rmtree(report_dir, ignore_errors=True)

        cases = report.cases if report else []
        if stopped:
            status, detail, exit_code = stopped, None, None
        else:
            status, detail = classify(exit_code, report, report_problem)
        return ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            exit_code=exit_code,
            test_cases=cases,
            intended_node_ids=intended,
            intended_tests_executed=(
                status in ("passed", "tests_failed") and intended_tests_executed(intended, cases)
            ),
            stdout=stdout.text(),
            stderr=stderr.text(),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            duration_seconds=duration,
            command_environment_sha256=environment_sha256,
            detail=detail,
        )
