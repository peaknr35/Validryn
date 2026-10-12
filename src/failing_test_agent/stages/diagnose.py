"""Diagnosis stage: accept a provider's proposed diagnosis only if it survives deterministic checks.

This is the first place model-shaped output enters the system. The provider
returns raw text; everything after that is parsing and validation in Python.
A diagnosis has no authority: it cannot change state, and nothing it says is
trusted beyond what the checks below confirm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ..core.db import Database
from ..core.errors import StateError
from ..core.hashing import sha256_file
from ..core.models import AttemptStatus, Diagnosis, EvidenceBundle
from ..core.lifecycle import Recorder, load_current_attempt, stage_guard
from .providers import BUNDLE_HASH_PLACEHOLDER, FakeDiagnosisProvider  # noqa: F401 (re-exported)

MAX_RAW_BYTES = 200_000
MAX_CLAIMS = 20
MAX_STATEMENT_CHARS = 1000
MAX_SUMMARY_CHARS = 2000
MAX_QUOTE_CHARS = 500

class DiagnosisProvider(Protocol):
    name: str

    def propose(self, bundle: EvidenceBundle, bundle_sha256: str) -> str:
        """Return the proposed diagnosis as raw text. It is parsed and validated by the caller."""
        ...

    def provenance(self) -> dict[str, object]:
        """How the last proposal was produced; recorded in the event log."""
        ...


@dataclass(frozen=True)
class DiagnosisProblem:
    code: str
    message: str


@dataclass(frozen=True)
class DiagnosisOutcome:
    provider: str
    evidence_bundle_sha256: str
    diagnosis: Diagnosis | None
    problems: list[DiagnosisProblem]

    @property
    def valid(self) -> bool:
        return self.diagnosis is not None and not self.problems


def validate_diagnosis(
    raw: str, bundle: EvidenceBundle, bundle_sha256: str
) -> tuple[Diagnosis | None, list[DiagnosisProblem]]:
    """Every problem found, each with its own code. The diagnosis is usable only if the list is empty."""
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        return None, [DiagnosisProblem("output_too_large", f"Output exceeds {MAX_RAW_BYTES} bytes.")]
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return None, [DiagnosisProblem("invalid_json", f"Output is not JSON: {exc}")]
    try:
        diagnosis = Diagnosis.model_validate(payload)
    except ValidationError as exc:
        return None, [
            DiagnosisProblem(
                "schema_violation",
                f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}",
            )
            for error in exc.errors()
        ]

    problems: list[DiagnosisProblem] = []

    def problem(code: str, message: str) -> None:
        problems.append(DiagnosisProblem(code, message))

    if diagnosis.evidence_bundle_sha256 != bundle_sha256:
        problem("bundle_hash_mismatch", "The diagnosis was written for a different evidence bundle.")
    if len(diagnosis.claims) > MAX_CLAIMS:
        problem("too_many_claims", f"{len(diagnosis.claims)} claims; the limit is {MAX_CLAIMS}.")
    if len(diagnosis.summary) > MAX_SUMMARY_CHARS:
        problem("summary_too_long", f"The summary exceeds {MAX_SUMMARY_CHARS} characters.")

    evidence = {item.evidence_id: item for item in bundle.evidence}
    claims = {}
    for claim in diagnosis.claims:
        label = f"Claim {claim.claim_id}"
        if claim.claim_id in claims:
            problem("duplicate_claim_id", f"{label} appears more than once.")
        claims[claim.claim_id] = claim
        if len(claim.statement) > MAX_STATEMENT_CHARS:
            problem("statement_too_long", f"{label} exceeds {MAX_STATEMENT_CHARS} characters.")
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence:
                problem("unknown_evidence_id", f"{label} cites {evidence_id}, which is not in the bundle.")
        if claim.kind == "fact" and not claim.evidence_ids:
            problem("fact_without_evidence", f"{label} is a fact but cites no evidence.")
        if claim.quote is not None:
            cited = [evidence[i].content for i in claim.evidence_ids if i in evidence]
            if not claim.quote or len(claim.quote) > MAX_QUOTE_CHARS:
                problem("invalid_quote", f"{label} has an empty or oversized quote.")
            elif not any(claim.quote in content for content in cited):
                problem(
                    "quote_not_in_evidence",
                    f"{label} quotes text that is not in the evidence it cites.",
                )

    root = diagnosis.root_cause_claim_id
    if root is not None and root not in claims:
        problem("dangling_root_cause", f"Root cause {root} is not one of the claims.")
    elif root is not None and claims[root].kind == "unknown":
        problem("root_cause_is_unknown", f"Root cause {root} is an `unknown` claim.")
    if diagnosis.abstain:
        if root is not None:
            problem("inconsistent_abstention", "An abstaining diagnosis must not name a root cause.")
        if not any(claim.kind == "unknown" for claim in diagnosis.claims):
            problem(
                "inconsistent_abstention",
                "An abstaining diagnosis must state what is missing in an `unknown` claim.",
            )
    elif root is None:
        problem("missing_root_cause", "A diagnosis must name a root cause or abstain.")
    return diagnosis, problems


def diagnosis_json(diagnosis: Diagnosis) -> str:
    return (
        json.dumps(diagnosis.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def render_markdown(diagnosis: Diagnosis, provider: str) -> str:
    out = [
        "# Diagnosis",
        "",
        f"- Provider: `{provider}`",
        f"- Evidence bundle: `{diagnosis.evidence_bundle_sha256}`",
        f"- Confidence: {diagnosis.confidence}",
        f"- Root cause: {diagnosis.root_cause_claim_id or 'none (abstained)'}",
        "",
        diagnosis.summary,
        "",
        "## Claims",
        "",
    ]
    for claim in diagnosis.claims:
        cites = ", ".join(claim.evidence_ids) or "no evidence cited"
        out.append(f"- **{claim.claim_id}** ({claim.kind}; {cites}): {claim.statement}")
        if claim.quote:
            out.append(f"  - Quote: `{claim.quote}`")
    return "\n".join(out) + "\n"


def diagnose(
    home: Path, run_id: str, attempt_id: str, provider: DiagnosisProvider
) -> DiagnosisOutcome:
    """Ask the provider for a diagnosis of a retrieved attempt and record it if it validates."""
    db = Database(home.resolve() / "agent.db")
    db.initialize()
    _, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.RETRIEVED)

    connection = db.connect()
    try:
        artifact = connection.execute(
            "SELECT path, sha256 FROM artifacts WHERE attempt_id = ? AND kind = ?",
            (attempt_id, "evidence_bundle"),
        ).fetchone()
    finally:
        connection.close()
    if artifact is None or not Path(artifact["path"]).is_file():
        raise StateError("The evidence bundle for this attempt is missing.")
    bundle_sha256 = artifact["sha256"]
    if sha256_file(Path(artifact["path"])) != bundle_sha256:
        raise StateError(
            "evidence.json does not match its recorded hash; it was changed after retrieval."
        )
    bundle = EvidenceBundle.model_validate_json(Path(artifact["path"]).read_text(encoding="utf-8"))

    recorder = Recorder(db, run_id, attempt_id, Path(attempt["worktree_path"]).parent)
    recorder.record(
        "attempt.diagnosing",
        {"provider": provider.name, "evidence_bundle_sha256": bundle_sha256},
        transition_to=(AttemptStatus.RETRIEVED, AttemptStatus.DIAGNOSING),
    )
    with stage_guard(recorder, AttemptStatus.DIAGNOSING):
        raw = provider.propose(bundle, bundle_sha256)
        if not isinstance(raw, str):
            raise TypeError("A diagnosis provider must return text.")
        # Kept exactly as returned, valid or not, so a rejected output can be inspected.
        recorder.record(
            "diagnosis.proposed",
            {"provider": provider.name, **provider.provenance()},
            artifact=("diagnosis_raw", "diagnosis.raw.txt", raw[: MAX_RAW_BYTES * 2]),
        )
        diagnosis, problems = validate_diagnosis(raw, bundle, bundle_sha256)
        if problems or diagnosis is None:
            recorder.record(
                "attempt.failed",
                {
                    "reason": "invalid_diagnosis",
                    "error_codes": sorted({problem.code for problem in problems}),
                    "errors": [f"{problem.code}: {problem.message}" for problem in problems],
                },
                transition_to=(AttemptStatus.DIAGNOSING, AttemptStatus.FAILED),
            )
            return DiagnosisOutcome(provider.name, bundle_sha256, None, problems)
        recorder.record(
            "diagnosis.rendered",
            {},
            artifact=("diagnosis_readable", "diagnosis.md", render_markdown(diagnosis, provider.name)),
        )
        recorder.record(
            "attempt.diagnosed",
            {
                "claims": len(diagnosis.claims),
                "root_cause_claim_id": diagnosis.root_cause_claim_id,
                "abstain": diagnosis.abstain,
            },
            artifact=("diagnosis", "diagnosis.json", diagnosis_json(diagnosis)),
            transition_to=(AttemptStatus.DIAGNOSING, AttemptStatus.DIAGNOSED),
        )
        return DiagnosisOutcome(provider.name, bundle_sha256, diagnosis, [])
