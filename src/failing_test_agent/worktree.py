from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import DirtyRepositoryError, RepositoryError, WorktreeCreationError


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def repository_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    result = _git(resolved, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise RepositoryError(f"Not a Git repository: {resolved}")
    return Path(result.stdout.strip()).resolve()


def require_clean_repository(repo: Path) -> None:
    result = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.stdout.strip():
        raise DirtyRepositoryError(
            "Repository contains uncommitted or untracked changes. "
            "Commit, stash, or discard them before creating a run."
        )


def head_commit(repo: Path) -> str:
    result = _git(repo, "rev-parse", "HEAD")
    return result.stdout.strip()


def worktree_inventory(worktree: Path) -> dict[str, str]:
    """Tracked changes plus untracked and ignored files; `git diff` alone misses the latter."""
    diff = _git(worktree, "diff", "--binary", "--no-ext-diff", "HEAD")
    status = _git(
        worktree, "status", "--porcelain=v2", "--untracked-files=all", "--ignored=matching"
    )
    return {"diff": diff.stdout, "status": status.stdout}


def create_detached_worktree(repo: Path, destination: Path, commit: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _git(
        repo,
        "worktree",
        "add",
        "--detach",
        str(destination),
        commit,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeCreationError(result.stderr.strip() or "Git worktree creation failed")
    actual = head_commit(destination)
    if actual != commit:
        remove_worktree(repo, destination)
        raise WorktreeCreationError(
            f"Worktree commit mismatch: expected {commit}, received {actual}"
        )


def remove_worktree(repo: Path, destination: Path) -> None:
    if destination.exists():
        _git(repo, "worktree", "remove", "--force", str(destination), check=False)
        shutil.rmtree(destination, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)
