"""Stand-in providers that return canned text where a model will later sit."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.models import Diagnosis, EvidenceBundle

BUNDLE_HASH_PLACEHOLDER = "{{evidence_bundle_sha256}}"
DIAGNOSIS_HASH_PLACEHOLDER = "{{diagnosis_sha256}}"


class FakeFileProvider:
    """Returns the contents of a file.

    The hash a proposal must name is unique to its attempt, so a canned file
    writes a placeholder and this provider fills it in. That convenience
    belongs to the fake only: a real provider must have no substitution step,
    or a model's wrong hash could be silently corrected. `provenance` records
    exactly what was substituted.
    """

    name = "fake"

    def __init__(self, input_path: Path) -> None:
        self.input_path = input_path
        self._provenance: dict[str, object] = {}

    def _render(self, substitutions: dict[str, str]) -> str:
        data = self.input_path.read_bytes()
        raw = data.decode("utf-8", errors="replace")
        used = sorted(placeholder for placeholder in substitutions if placeholder in raw)
        for placeholder, value in substitutions.items():
            raw = raw.replace(placeholder, value)
        self._provenance = {
            "input_file": self.input_path.name,
            "input_sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
            "placeholders_substituted": used,
        }
        return raw

    def provenance(self) -> dict[str, object]:
        """How the last proposal was produced, for the event log."""
        return dict(self._provenance)


class FakeDiagnosisProvider(FakeFileProvider):
    def propose(self, bundle: EvidenceBundle, bundle_sha256: str) -> str:
        return self._render({BUNDLE_HASH_PLACEHOLDER: bundle_sha256})


class FakePatchProvider(FakeFileProvider):
    def propose(self, diagnosis: Diagnosis, diagnosis_sha256: str, bundle: EvidenceBundle) -> str:
        return self._render({DIAGNOSIS_HASH_PLACEHOLDER: diagnosis_sha256})
