from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import pytest

CALCULATOR_FILES = {
    "calculator.py": (
        "def divide(a, b):\n    return a * b\n\n\ndef add(a, b):\n    return a + b\n"
    ),
    "test_calculator.py": (
        "from calculator import add, divide\n\n"
        "def test_divide():\n    assert divide(8, 2) == 4\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
    ),
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[dict[str, str]], Path]:
    def build(files: dict[str, str], name: str = "repo") -> Path:
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "FTA Test")
        git(repo, "config", "user.email", "fta@example.invalid")
        for relative, content in files.items():
            (repo / relative).write_text(content)
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "fixture")
        return repo

    return build


@pytest.fixture
def clean_repo(make_repo: Callable[[dict[str, str]], Path]) -> Path:
    return make_repo(CALCULATOR_FILES)
