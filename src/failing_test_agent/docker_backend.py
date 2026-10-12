from __future__ import annotations

import shutil
import subprocess

from .errors import BackendUnavailableError
from .execution import ExecutionRequest, ExecutionResult


class DockerBackend:
    """Container execution backend. Interface only: `run` is not implemented yet."""

    name = "docker"
    sandboxed = True

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        raise BackendUnavailableError("Docker execution is not implemented yet.")

    def cancel(self, execution_id: str) -> None:
        raise BackendUnavailableError("Docker execution is not implemented yet.")
