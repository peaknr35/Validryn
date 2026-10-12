from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    CREATED = "created"
    CAPTURING = "capturing"
    CAPTURED = "captured"
    RETRIEVING = "retrieving"
    RETRIEVED = "retrieved"
    DIAGNOSING = "diagnosing"
    DIAGNOSED = "diagnosed"
    PATCHING = "patching"
    PATCHED = "patched"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    attempt_id: str
    attempt_number: int = 1
    repository_path: str
    base_commit: str
    test_argv: list[str]
    run_status: RunStatus = RunStatus.ACTIVE
    attempt_status: AttemptStatus = AttemptStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    run_id: str
    attempt_id: str | None = None
    event_type: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CreatedRun(BaseModel):
    run_id: str
    attempt_id: str
    base_commit: str
    worktree_path: Path
    manifest_path: Path
