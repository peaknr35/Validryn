from __future__ import annotations

from .docker_backend import DockerBackend
from .errors import BackendUnavailableError, CommandValidationError, UnsafeExecutionConsentError
from .execution import ExecutionBackend
from .local_backend import TrustedLocalBackend

LOCAL_EXECUTION_WARNING = (
    "Trusted local execution runs repository code with the permissions of your\n"
    "user account and is not a security sandbox.\n\n"
    "Select --backend trusted-local --allow-unsafe-local-execution only for\n"
    "repositories you control."
)


def select_backend(
    name: str = "docker", *, allow_unsafe_local_execution: bool = False
) -> ExecutionBackend:
    """Return the requested backend or fail. There is no fallback from Docker to local."""
    if name == "docker":
        backend = DockerBackend()
        if not backend.available():
            raise BackendUnavailableError(
                f"Docker execution is unavailable.\n\n{LOCAL_EXECUTION_WARNING}"
            )
        return backend
    if name == "trusted-local":
        if not allow_unsafe_local_execution:
            raise UnsafeExecutionConsentError(LOCAL_EXECUTION_WARNING)
        return TrustedLocalBackend()
    raise CommandValidationError(f"Unknown execution backend: {name}")
