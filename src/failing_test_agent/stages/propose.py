"""Proposal stage: accept a provider's patch only if it passes patch policy. Nothing is applied here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ..core.db import Database
from ..core.errors import StateError
from ..core.hashing import sha256_file
from ..core.lifecycle import (
    Recorder,
    load_current_attempt,
    require_pristine_worktree,
    stage_guard,
)
from ..core.models import AttemptStatus, Diagnosis, EvidenceBundle, PatchProposal
from ..execution.contracts import ExecutionResult
from .patch_policy import FileChange, PatchPolicy, Problem, check_diff

MAX_RAW_BYTES = 400_000
MAX_TEXT_CHARS = 2000
MAX_LIST_ITEMS = 20


class PatchProvider(Protocol):
    name: str

    def propose(self, diagnosis: Diagnosis, diagnosis_sha256: str, bundle: EvidenceBundle) -> str:
        """Return the proposed patch as raw text. It is parsed and validated by the caller."""
        ...

    def provenance(self) -> dict[str, object]:
        """How the last proposal was produced; recorded in the event log."""
        ...


@dataclass(frozen=True)
class ProposalContext:
    """Everything a proposal is judged against. Built from verified artifacts."""

    diagnosis: Diagnosis
    diagnosis_sha256: str
    worktree: Path
    base_commit: str
    # Test node IDs seen in the baseline results.
    known_node_ids: frozenset[str]
    # Files that appear in the evidence bundle.
    evidence_paths: frozenset[str]
    policy: PatchPolicy = field(default_factory=PatchPolicy)


@dataclass(frozen=True)
class ProposalOutcome:
    provider: str
    proposal: PatchProposal | None
    problems: list[Problem]
    warnings: list[str]
    files: list[FileChange]

    @property
    def valid(self) -> bool:
        return self.proposal is not None and not self.problems

    @property
    def reason(self) -> str | None:
        """`invalid_proposal` when the output could not be read as a proposal, else `policy_violation`."""
        if self.valid:
            return None
        unreadable = {"output_too_large", "invalid_json", "schema_violation"}
        return (
            "invalid_proposal"
            if any(problem.code in unreadable for problem in self.problems)
            else "policy_violation"
        )


def validate_proposal(
    raw: str, context: ProposalContext
) -> tuple[PatchProposal | None, list[Problem], list[str], list[FileChange]]:
    """Every problem found, each with its own code, plus warnings for a reviewer."""
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        return None, [Problem("output_too_large", f"Output exceeds {MAX_RAW_BYTES} bytes.")], [], []
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return None, [Problem("invalid_json", f"Output is not JSON: {exc}")], [], []
    try:
        proposal = PatchProposal.model_validate(payload)
    except ValidationError as exc:
        return (
            None,
            [
                Problem(
                    "schema_violation",
                    f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}",
                )
                for error in exc.errors()
            ],
            [],
            [],
        )

    problems: list[Problem] = []
    if proposal.diagnosis_sha256 != context.diagnosis_sha256:
        problems.append(
            Problem("diagnosis_hash_mismatch", "The patch was written for a different diagnosis.")
        )
    if proposal.addresses_claim_id != context.diagnosis.root_cause_claim_id:
        problems.append(
            Problem(
                "claim_mismatch",
                f"The patch addresses {proposal.addresses_claim_id}, not the diagnosed root cause "
                f"{context.diagnosis.root_cause_claim_id}.",
            )
        )
    texts = [proposal.rationale, *proposal.risks, *proposal.expected_effects]
    if (
        any(len(text) > MAX_TEXT_CHARS for text in texts)
        or len(proposal.risks) > MAX_LIST_ITEMS
        or len(proposal.expected_effects) > MAX_LIST_ITEMS
    ):
        problems.append(Problem("text_too_long", "Rationale, risks or expected effects exceed their limits."))
    for node_id in proposal.expected_effects:
        if node_id not in context.known_node_ids:
            problems.append(
                Problem("unknown_expected_effect", f"Not a test seen in the baseline: {node_id}")
            )

    files, diff_problems = check_diff(
        proposal.diff, context.worktree, context.base_commit, context.policy
    )
    problems += diff_problems
    warnings = [
        f"{change.path} is changed but is not in the evidence bundle."
        for change in files
        if not diff_problems and change.path not in context.evidence_paths
    ]
    return proposal, problems, warnings, files


def proposal_json(proposal: PatchProposal) -> str:
    return (
        json.dumps(proposal.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def render_markdown(proposal: PatchProposal, files: list[FileChange], warnings: list[str]) -> str:
    out = [
        "# Patch proposal",
        "",
        f"- Diagnosis: `{proposal.diagnosis_sha256}`",
        f"- Addresses claim: {proposal.addresses_claim_id}",
        "",
        proposal.rationale,
        "",
        "## Files",
        "",
        *(f"- `{change.path}` (+{change.added} -{change.removed})" for change in files),
        "",
        "## Expected to start passing",
        "",
        *(f"- `{node}`" for node in proposal.expected_effects or ["(none stated)"]),
        "",
        "## Risks",
        "",
        *(f"- {risk}" for risk in proposal.risks or ["(none stated)"]),
        "",
    ]
    if warnings:
        out += ["## Warnings", "", *(f"- {warning}" for warning in warnings), ""]
    out += ["## Diff", "", "```diff", proposal.diff.rstrip("\n"), "```", ""]
    return "\n".join(out)


def _verified_artifact(db: Database, attempt_id: str, kind: str, required: bool = True) -> tuple[Path, str] | None:
    connection = db.connect()
    try:
        row = connection.execute(
            "SELECT path, sha256 FROM artifacts WHERE attempt_id = ? AND kind = ?",
            (attempt_id, kind),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        if required:
            raise StateError(f"This attempt has no recorded `{kind}` artifact.")
        return None
    path = Path(row["path"])
    if not path.is_file() or sha256_file(path) != row["sha256"]:
        raise StateError(f"{path.name} is missing or does not match its recorded hash.")
    return path, row["sha256"]


def propose_patch(
    home: Path, run_id: str, attempt_id: str, provider: PatchProvider
) -> ProposalOutcome:
    """Ask the provider for a patch to a diagnosed attempt and record it if policy allows it."""
    db = Database(home.resolve() / "agent.db")
    db.initialize()
    _, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.DIAGNOSED)
    worktree, _ = require_pristine_worktree(attempt)

    diagnosis_path, diagnosis_sha256 = _verified_artifact(db, attempt_id, "diagnosis")
    diagnosis = Diagnosis.model_validate_json(diagnosis_path.read_text(encoding="utf-8"))
    if diagnosis.abstain or diagnosis.root_cause_claim_id is None:
        raise StateError("The diagnosis abstained; there is no root cause to patch.")
    evidence_path, _ = _verified_artifact(db, attempt_id, "evidence_bundle")
    bundle = EvidenceBundle.model_validate_json(evidence_path.read_text(encoding="utf-8"))
    known: set[str] = set()
    for kind in ("baseline_target_result", "baseline_regression_result"):
        found = _verified_artifact(db, attempt_id, kind, required=kind.startswith("baseline_target"))
        if found:
            result = ExecutionResult.model_validate_json(found[0].read_text(encoding="utf-8"))
            known.update(case.node_id for case in result.test_cases)

    context = ProposalContext(
        diagnosis=diagnosis,
        diagnosis_sha256=diagnosis_sha256,
        worktree=worktree,
        base_commit=attempt["base_commit"],
        known_node_ids=frozenset(known),
        evidence_paths=frozenset(item.path for item in bundle.evidence),
    )
    recorder = Recorder(db, run_id, attempt_id, worktree.parent)
    recorder.record(
        "attempt.proposing",
        {"provider": provider.name, "diagnosis_sha256": diagnosis_sha256},
        transition_to=(AttemptStatus.DIAGNOSED, AttemptStatus.PROPOSING),
    )
    with stage_guard(recorder, AttemptStatus.PROPOSING):
        raw = provider.propose(diagnosis, diagnosis_sha256, bundle)
        if not isinstance(raw, str):
            raise TypeError("A patch provider must return text.")
        # Kept as returned, valid or not, so a rejected proposal can be inspected.
        recorder.record(
            "patch.proposed",
            {"provider": provider.name, **provider.provenance()},
            artifact=("patch_raw", "patch.raw.txt", raw[: MAX_RAW_BYTES * 2]),
        )
        proposal, problems, warnings, files = validate_proposal(raw, context)
        outcome = ProposalOutcome(provider.name, proposal if not problems else None, problems, warnings, files)
        if not outcome.valid:
            recorder.record(
                "attempt.failed",
                {
                    "reason": outcome.reason,
                    "error_codes": sorted({problem.code for problem in problems}),
                    "errors": [f"{problem.code}: {problem.message}" for problem in problems],
                },
                transition_to=(AttemptStatus.PROPOSING, AttemptStatus.FAILED),
            )
            return outcome
        recorder.record(
            "patch.rendered",
            {},
            artifact=("patch_readable", "patch.md", render_markdown(proposal, files, warnings)),
        )
        recorder.record(
            "attempt.proposed",
            {
                "files": [change.path for change in files],
                "added_lines": sum(change.added for change in files),
                "removed_lines": sum(change.removed for change in files),
                "warnings": warnings,
            },
            artifact=("patch_proposal", "patch.json", proposal_json(proposal)),
            transition_to=(AttemptStatus.PROPOSING, AttemptStatus.PROPOSED),
        )
        return outcome
