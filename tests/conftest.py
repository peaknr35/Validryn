from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "FTA Test")
    git(repo, "config", "user.email", "fta@example.invalid")
    (repo / "calculator.py").write_text("def divide(a, b):\n    return a * b\n")
    (repo / "test_calculator.py").write_text(
        "from calculator import divide\n\ndef test_divide():\n    assert divide(8, 2) == 4\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return repo
