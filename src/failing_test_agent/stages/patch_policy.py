"""Parse a unified diff and judge it against patch policy, before Git ever sees it.

`git apply` is the tool that applies a patch; it is not the policy. Everything
here works on the diff text and the base commit, and changes nothing.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath


@dataclass(frozen=True)
class PatchPolicy:
    """v1 policy: small edits to existing source files only. Loosen one rule at a time, each with a test."""

    allowed_globs: tuple[str, ...] = ("src/**",)
    max_files: int = 5
    max_changed_lines: int = 200
    max_diff_bytes: int = 100_000


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


class DiffError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class FileChange:
    # From the `diff --git a/X b/Y` line, if there was one.
    git_old: str | None = None
    git_new: str | None = None
    # From the `---` and `+++` lines; None means /dev/null.
    old_path: str | None = None
    new_path: str | None = None
    has_old: bool = False
    has_new: bool = False
    flags: set[str] = field(default_factory=set)
    hunks: int = 0
    # Changed lines in order, as ("-" | "+", text). Context lines are not kept.
    changes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        found = []
        for path in (self.git_old, self.git_new, self.old_path, self.new_path):
            if path is not None and path not in found:
                found.append(path)
        return found

    @property
    def path(self) -> str:
        return self.paths[0] if self.paths else ""

    @property
    def added(self) -> int:
        return sum(1 for tag, _ in self.changes if tag == "+")

    @property
    def removed(self) -> int:
        return sum(1 for tag, _ in self.changes if tag == "-")


_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SPECIAL_MODES = {"120000": "symlink", "160000": "submodule"}


def _side(text: str, prefix: str, change: FileChange) -> str | None:
    text = text.split("\t")[0]
    if text == "/dev/null":
        return None
    if text.startswith('"'):
        raise DiffError("unsafe_path", "Quoted paths are not accepted.")
    if text.startswith(prefix):
        return text[2:]
    change.flags.add("no_prefix")
    return text


def _git_header(rest: str, change: FileChange) -> None:
    if rest.startswith('"'):
        raise DiffError("unsafe_path", "Quoted paths are not accepted.")
    middle = len(rest) // 2
    if len(rest) % 2 == 1 and rest[middle] == " " and rest[2:middle] == rest[middle + 3 :]:
        change.git_old = change.git_new = rest[2:middle]
        return
    match = re.fullmatch(r"a/(\S+) b/(\S+)", rest)
    if not match:
        raise DiffError("malformed_diff", "Unreadable `diff --git` line.")
    change.git_old, change.git_new = match.groups()


def parse_diff(text: str) -> list[FileChange]:
    """Split a unified diff into per-file changes. Raises DiffError on anything it does not understand."""
    files: list[FileChange] = []
    current: FileChange | None = None
    old_left = new_left = 0
    binary = False
    for line in text.split("\n"):
        if old_left or new_left:
            if line.startswith("\\"):
                continue
            tag = line[:1] or " "
            if tag == " ":
                old_left -= 1
                new_left -= 1
            elif tag == "-":
                old_left -= 1
                current.changes.append(("-", line[1:]))
            elif tag == "+":
                new_left -= 1
                current.changes.append(("+", line[1:]))
            else:
                raise DiffError("malformed_diff", "A hunk is shorter than its header says.")
            if old_left < 0 or new_left < 0:
                raise DiffError("malformed_diff", "A hunk does not match its header counts.")
            continue
        if line.startswith("diff --git "):
            current = FileChange()
            files.append(current)
            binary = False
            _git_header(line[len("diff --git ") :], current)
            continue
        if line.startswith("--- ") and (current is None or current.has_new):
            current = FileChange()
            files.append(current)
            binary = False
        if current is None:
            if line.strip():
                raise DiffError("malformed_diff", "Text before the first file header.")
            continue
        if binary or line == "" or line.startswith("\\"):
            continue
        if line.startswith("--- "):
            current.old_path, current.has_old = _side(line[4:], "a/", current), True
        elif line.startswith("+++ "):
            current.new_path, current.has_new = _side(line[4:], "b/", current), True
        elif line.startswith("@@ "):
            match = _HUNK.match(line)
            if not match or not (current.has_old and current.has_new):
                raise DiffError("malformed_diff", "Unreadable hunk header.")
            old_left = int(match.group(2) or 1)
            new_left = int(match.group(4) or 1)
            current.hunks += 1
        elif line.startswith(("old mode ", "new mode ")):
            current.flags.add("mode_change")
        elif line.startswith(("new file mode ", "deleted file mode ")):
            current.flags.add("new_file" if line.startswith("new") else "deleted_file")
            current.flags.update(
                flag for mode, flag in _SPECIAL_MODES.items() if line.endswith(mode)
            )
        elif line.startswith(("rename ", "copy ", "similarity index ", "dissimilarity index ")):
            current.flags.add("rename")
        elif line.startswith("index "):
            current.flags.update(
                flag for mode, flag in _SPECIAL_MODES.items() if line.endswith(" " + mode)
            )
        elif line.startswith(("GIT binary patch", "Binary files ")):
            current.flags.add("binary_patch")
            binary = True
        else:
            raise DiffError("malformed_diff", f"Unexpected line in diff: {line[:60]!r}")
    if old_left or new_left:
        raise DiffError("malformed_diff", "The diff ends in the middle of a hunk.")
    if not files:
        raise DiffError("empty_diff", "The diff changes no files.")
    return files


def _path_problem(path: str) -> Problem | None:
    """The first reason a path cannot be trusted as repository-relative, if any."""
    pure, windows = PurePosixPath(path), PureWindowsPath(path)
    if not path or "\\" in path or any(ord(char) < 32 for char in path):
        return Problem("unsafe_path", f"Path has characters that are not accepted: {path!r}")
    if pure.is_absolute() or windows.is_absolute() or windows.drive:
        return Problem("absolute_path", f"Path is absolute: {path}")
    if ".." in pure.parts:
        return Problem("path_traversal", f"Path leaves the repository: {path}")
    if ".git" in pure.parts:
        return Problem("git_path", f"Path is inside .git: {path}")
    return None


def _protected_kind(path: str) -> str | None:
    pure = PurePosixPath(path)
    name = pure.name
    if (
        {"tests", "test"} & set(pure.parts[:-1])
        or name == "conftest.py"
        or fnmatch.fnmatch(name, "test_*.py")
        or fnmatch.fnmatch(name, "*_test.py")
        or name == "pytest.ini"
    ):
        return "test_edit"
    if (
        name in {"pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "noxfile.py", "Pipfile"}
        or fnmatch.fnmatch(name, "requirements*.txt")
        or name.endswith(".lock")
        or ".github" in pure.parts
    ):
        return "config_edit"
    return None


def _target_problem(worktree: Path, base_commit: str, path: str) -> Problem | None:
    """The file must be an ordinary tracked file at the base commit, reached without any link."""
    listing = subprocess.run(
        ["git", "-C", str(worktree), "ls-tree", base_commit, "--", path],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not listing:
        return Problem("target_missing", f"File does not exist at the base commit: {path}")
    mode = listing.split()[0]
    if mode not in {"100644", "100755"}:
        return Problem("target_is_link", f"Target is not an ordinary file (mode {mode}): {path}")
    root = worktree.resolve()
    expected = root.joinpath(*PurePosixPath(path).parts)
    try:
        real = expected.resolve(strict=True)
    except OSError:
        return Problem("target_missing", f"File is missing from the worktree: {path}")
    # resolve() follows symlinks and junctions; any difference means a link sits on the way.
    if os.path.normcase(str(real)) != os.path.normcase(str(expected)) or not real.is_file():
        return Problem("target_is_link", f"Target is reached through a link: {path}")
    return None


def check_diff(
    diff: str, worktree: Path, base_commit: str, policy: PatchPolicy
) -> tuple[list[FileChange], list[Problem]]:
    """Every policy problem in a diff. An empty list means it may be applied."""
    if "\r" in diff:
        # One defined answer on every machine, instead of whatever Git's line-ending settings do.
        return [], [Problem("crlf_diff", "The diff has CR characters; LF line endings are required.")]
    if len(diff.encode("utf-8")) > policy.max_diff_bytes:
        return [], [Problem("diff_too_large", f"The diff exceeds {policy.max_diff_bytes} bytes.")]
    try:
        files = parse_diff(diff)
    except DiffError as exc:
        return [], [Problem(exc.code, str(exc))]

    problems: list[Problem] = []
    seen: set[str] = set()
    for change in files:
        before = len(problems)
        for path in change.paths:
            problem = _path_problem(path)
            if problem:
                problems.append(problem)
        if len(problems) > before:
            continue
        path = change.path
        if "no_prefix" in change.flags:
            problems.append(Problem("malformed_diff", f"Path lacks its a/ or b/ prefix: {path}"))
            continue
        for flag in ("binary_patch", "symlink", "submodule", "rename", "mode_change"):
            if flag in change.flags:
                problems.append(Problem(flag, f"Not allowed in v1 ({flag.replace('_', ' ')}): {path}"))
        if "new_file" in change.flags or (change.has_old and change.old_path is None):
            problems.append(Problem("new_file", f"Creating files is not allowed: {path}"))
        if "deleted_file" in change.flags or (change.has_new and change.new_path is None):
            problems.append(Problem("deleted_file", f"Deleting files is not allowed: {path}"))
        if len(problems) == before and len(change.paths) > 1:
            problems.append(
                Problem("path_mismatch", f"The diff names different paths for one file: {change.paths}")
            )
        protected = _protected_kind(path)
        if protected:
            problems.append(Problem(protected, f"Protected file may not be patched: {path}"))
        elif not any(fnmatch.fnmatch(path, glob) for glob in policy.allowed_globs):
            problems.append(Problem("path_not_allowed", f"Path is outside the allowed set: {path}"))
        if len(problems) > before:
            continue
        if change.hunks == 0:
            problems.append(Problem("malformed_diff", f"No changes given for {path}"))
            continue
        if path in seen:
            problems.append(Problem("duplicate_file", f"File appears twice in the diff: {path}"))
            continue
        seen.add(path)
        target = _target_problem(worktree, base_commit, path)
        if target:
            problems.append(target)

    if len(files) > policy.max_files:
        problems.append(Problem("too_many_files", f"{len(files)} files; the limit is {policy.max_files}."))
    changed = sum(change.added + change.removed for change in files)
    if changed > policy.max_changed_lines:
        problems.append(
            Problem("too_many_changed_lines", f"{changed} changed lines; the limit is {policy.max_changed_lines}.")
        )
    return files, problems
