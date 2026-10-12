from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

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
    PROPOSING = "proposing"
    PROPOSED = "proposed"
    APPLYING = "applying"
    APPLIED = "applied"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


# Only the transitions implemented so far; anything else is refused.
ATTEMPT_TRANSITIONS: dict[AttemptStatus, set[AttemptStatus]] = {
    AttemptStatus.CREATED: {AttemptStatus.CAPTURING},
    AttemptStatus.CAPTURING: {
        AttemptStatus.CAPTURED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
    AttemptStatus.CAPTURED: {AttemptStatus.RETRIEVING},
    AttemptStatus.RETRIEVING: {
        AttemptStatus.RETRIEVED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
    AttemptStatus.RETRIEVED: {AttemptStatus.DIAGNOSING},
    AttemptStatus.DIAGNOSING: {
        AttemptStatus.DIAGNOSED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
    AttemptStatus.DIAGNOSED: {AttemptStatus.PROPOSING},
    AttemptStatus.PROPOSING: {
        AttemptStatus.PROPOSED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
    AttemptStatus.PROPOSED: {AttemptStatus.APPLYING},
    AttemptStatus.APPLYING: {
        AttemptStatus.APPLIED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
}


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    attempt_id: str
    attempt_number: int = 1
    repository_path: str
    base_commit: str
    test_argv: list[str]
    regression_argv: list[str] | None = None
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


class CommandCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_kind: Literal["target", "regression"]
    argv: list[str]
    status: str
    exit_code: int | None
    detail: str | None
    intended_node_ids: list[str]
    intended_tests_executed: bool
    failing_node_ids: list[str]
    # Regression only: nodes that are the target itself. They are left out of
    # `failing_node_ids` so a fixed target is never counted as a fixed regression.
    excluded_target_node_ids: list[str] = Field(default_factory=list)
    command_environment_sha256: str
    result_artifact: str
    result_sha256: str


class BaselineCapture(BaseModel):
    """What the base commit does before any patch exists."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    attempt_id: str
    base_commit: str
    backend: str
    sandboxed: bool
    target: CommandCapture
    # The requested test ran on the base commit and failed there.
    target_failure_confirmed: bool
    regression: CommandCapture | None
    regression_status: Literal["captured", "inconclusive", "not_configured"]
    worktree_unchanged: bool
    outcome: Literal["captured", "failed"]
    failure_reason: str | None
    created_at: datetime = Field(default_factory=utc_now)


# `exact_match` is reserved for lexical search hits. Retrieval strategy
# exact-v1 does not produce it; its absence from a bundle is not a bug.
EvidenceKind = Literal["test_definition", "traceback_frame", "symbol_definition", "exact_match"]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    # Repository-relative, forward slashes.
    path: str
    start_line: int
    end_line: int
    # Lines start_line..end_line with LF endings; content_sha256 is over this text.
    content: str
    content_sha256: str
    reason: str
    related_node_id: str | None = None
    symbol: str | None = None
    # Lines inside this snippet that a traceback frame points at.
    failure_lines: list[int] = Field(default_factory=list)
    truncated: bool = False


class EvidenceBundle(BaseModel):
    """Source snippets relevant to a captured failure. Holds no timestamp, so equal inputs give equal bytes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    attempt_id: str
    base_commit: str
    baseline_target_sha256: str
    retrieval_strategy: str = "exact-v1"
    target_node_ids: list[str]
    query_terms: list[str]
    files_inspected: list[str]
    evidence: list[EvidenceItem]
    warnings: list[str]
    truncated: bool = False


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    # fact: stated by cited evidence. hypothesis: inferred. conflict: evidence
    # disagrees. unknown: something the evidence does not show.
    kind: Literal["fact", "hypothesis", "conflict", "unknown"]
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    # Optional exact text copied from one of the cited evidence items.
    quote: str | None = None


class Diagnosis(BaseModel):
    """A proposed explanation of the failure. Proposed by a provider, checked by `validate_diagnosis`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    # Binds the diagnosis to one evidence bundle, so it cannot be reused on different evidence.
    evidence_bundle_sha256: str
    summary: str
    claims: list[Claim]
    root_cause_claim_id: str | None
    confidence: Literal["low", "medium", "high"]
    abstain: bool = False


class PatchProposal(BaseModel):
    """A proposed change. Proposed by a provider, checked by `validate_proposal`, applied by a separate stage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    # Binds the patch to one validated diagnosis.
    diagnosis_sha256: str
    # Must be that diagnosis's root-cause claim.
    addresses_claim_id: str
    rationale: str = Field(min_length=1)
    # Unified diff with a/ and b/ path prefixes and LF line endings.
    diff: str
    # Test node IDs expected to go from failing to passing.
    expected_effects: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class CreatedRun(BaseModel):
    run_id: str
    attempt_id: str
    base_commit: str
    worktree_path: Path
    manifest_path: Path
