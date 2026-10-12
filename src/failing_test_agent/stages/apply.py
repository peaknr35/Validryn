"""Apply stage: put a validated proposal into the attempt worktree and prove what actually changed."""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..core.db import Database
from ..core.lifecycle import (
    Recorder,
    load_current_attempt,
    require_pristine_worktree,
    stage_guard,
)
from ..core.models import AttemptStatus, PatchProposal
from ..core.worktree import worktree_inventory
from .patch_policy import DiffError, PatchPolicy, check_diff, parse_diff
from .propose import _verified_artifact


@dataclass(frozen=True)
class ApplyOutcome:
    applied: bool
    files: list[str]
    applied_diff_sha256: str | None
    # Why the patch was not applied; None when it was.
    detail: str | None


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, encoding="utf-8"
    )


def restore_worktree(worktree: Path) -> bool:
    """Return the attempt's own worktree to the base commit. True if it is clean afterwards."""
    _git(worktree, "reset", "--hard", "--quiet", "HEAD")
    _git(worktree, "clean", "-fdxq")
    inventory = worktree_inventory(worktree)
    return not inventory["diff"] and not inventory["status"]


@contextmanager
def _restore_on_error(worktree: Path) -> Iterator[None]:
    # A crash or Ctrl+C part-way through must not leave a half-patched worktree behind.
    try:
        yield
    except BaseException:
        restore_worktree(worktree)
        raise


def _status_paths(worktree: Path) -> dict[str, str]:
    listing = _git(
        worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"
    ).stdout
    return {line[3:]: line[:2] for line in listing.splitlines() if line}


def apply_patch(home: Path, run_id: str, attempt_id: str) -> ApplyOutcome:
    """Apply the recorded proposal with Git, then check the resulting diff against it."""
    db = Database(home.resolve() / "agent.db")
    db.initialize()
    _, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.PROPOSED)
    worktree, _ = require_pristine_worktree(attempt)
    proposal_path, proposal_sha256 = _verified_artifact(db, attempt_id, "patch_proposal")
    proposal = PatchProposal.model_validate_json(proposal_path.read_text(encoding="utf-8"))

    recorder = Recorder(db, run_id, attempt_id, worktree.parent)
    recorder.record(
        "attempt.applying",
        {"patch_proposal_sha256": proposal_sha256},
        transition_to=(AttemptStatus.PROPOSED, AttemptStatus.APPLYING),
    )

    def fail(detail: str) -> ApplyOutcome:
        restored = restore_worktree(worktree)
        recorder.record(
            "attempt.failed",
            {"reason": "apply_failed", "detail": detail, "worktree_restored": restored},
            transition_to=(AttemptStatus.APPLYING, AttemptStatus.FAILED),
        )
        return ApplyOutcome(False, [], None, detail)

    with stage_guard(recorder, AttemptStatus.APPLYING), _restore_on_error(worktree):
        # Policy is checked again here: the worktree is what gets changed, so it is judged at this moment.
        proposed, problems = check_diff(
            proposal.diff, worktree, attempt["base_commit"], PatchPolicy()
        )
        if problems:
            return fail("; ".join(f"{problem.code}: {problem.message}" for problem in problems))

        handle, name = tempfile.mkstemp(prefix="fta-patch-", suffix=".diff")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(proposal.diff)
            # No --reject, no fuzz, no --unsafe-paths: the patch applies exactly or not at all.
            check = _git(worktree, "apply", "--check", "--whitespace=nowarn", name)
            if check.returncode != 0:
                return fail(f"git apply --check refused the patch: {check.stderr.strip()[:500]}")
            applied = _git(worktree, "apply", "--whitespace=nowarn", name)
            if applied.returncode != 0:
                return fail(f"git apply failed: {applied.stderr.strip()[:500]}")
        finally:
            Path(name).unlink(missing_ok=True)

        # What review will later approve is this diff, read back from the worktree, not the proposal text.
        actual_diff = _git(worktree, "diff", "--no-ext-diff", "--no-color", "HEAD").stdout
        try:
            actual = parse_diff(actual_diff) if actual_diff else []
        except DiffError as exc:
            return fail(f"The applied diff could not be read back: {exc}")
        expected_changes = {change.path: change.changes for change in proposed}
        actual_changes = {change.path: change.changes for change in actual}
        if actual_changes != expected_changes:
            return fail("The diff in the worktree is not the diff that was proposed.")
        status = _status_paths(worktree)
        if set(status) != set(expected_changes) or any(code != " M" for code in status.values()):
            return fail(f"Unexpected changes in the worktree: {sorted(status)}")

        files = sorted(expected_changes)
        digest = recorder.record(
            "attempt.applied",
            {"files": files},
            artifact=("applied_diff", "applied.diff", actual_diff),
            transition_to=(AttemptStatus.APPLYING, AttemptStatus.APPLIED),
        )
        return ApplyOutcome(True, files, digest, None)
