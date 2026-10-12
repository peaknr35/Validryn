from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .contracts import ExecutionRequest
from ..core.hashing import sha256_canonical_json

WORKTREE_PLACEHOLDER = "<worktree>"


def normalize_path(path: Path | str) -> str:
    """Resolved, forward-slash path; lower-cased on Windows where case is not significant."""
    text = Path(path).resolve().as_posix()
    return text.lower() if sys.platform == "win32" else text


def _strip_worktree(value: str, worktree_root: Path) -> str:
    # Each attempt has its own worktree directory, so the directory itself must
    # not make two otherwise identical commands look different.
    root = normalize_path(worktree_root)
    candidate = value.replace("\\", "/")
    comparable = candidate.lower() if sys.platform == "win32" else candidate
    index = comparable.find(root)
    while index != -1:
        candidate = candidate[:index] + WORKTREE_PLACEHOLDER + candidate[index + len(root) :]
        comparable = candidate.lower() if sys.platform == "win32" else candidate
        index = comparable.find(root)
    return candidate


def command_environment_identity(
    request: ExecutionRequest,
    *,
    backend: str,
    python_executable: Path | str,
    python_version: str,
    pytest_version: str,
    reporter_plugin_sha256: str,
    selected_environment: dict[str, str],
    runner_policy: dict[str, Any],
) -> dict[str, Any]:
    """Everything that should be equal between a baseline command and its patched twin.

    Compare identities per command pair (baseline target with patched target,
    baseline regression with patched regression); target and regression differ
    by design because their argv differs.
    """
    root = request.worktree_root.resolve()
    relative_cwd = request.working_directory.resolve().relative_to(root).as_posix()
    return {
        "backend": backend,
        "python_executable": normalize_path(python_executable),
        "python_version": python_version,
        "pytest_version": pytest_version,
        "reporter_plugin_sha256": reporter_plugin_sha256,
        "argv": [_strip_worktree(arg, root) for arg in request.argv],
        "working_directory": f"{WORKTREE_PLACEHOLDER}/{relative_cwd}",
        "selected_environment": {
            key: _strip_worktree(value, root)
            for key, value in sorted(selected_environment.items())
        },
        "dependency_manifest_sha256": request.dependency_manifest_sha256,
        "runner_policy_sha256": sha256_canonical_json(runner_policy),
    }


def command_environment_sha256(identity: dict[str, Any]) -> str:
    return sha256_canonical_json(identity)
