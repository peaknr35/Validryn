from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failing_test_agent.cli import app
from failing_test_agent.core.errors import StateError
from failing_test_agent.core.hashing import sha256_file
from failing_test_agent.core.models import CreatedRun, Diagnosis, EvidenceBundle
from failing_test_agent.core.worktree import worktree_inventory
from failing_test_agent.execution.local_backend import TrustedLocalBackend
from failing_test_agent.stages import apply as apply_stage
from failing_test_agent.stages.apply import apply_patch, restore_worktree
from failing_test_agent.stages.capture import capture_baseline
from failing_test_agent.stages.create import create_run
from failing_test_agent.stages.diagnose import diagnose
from failing_test_agent.stages.patch_policy import PatchPolicy, parse_diff
from failing_test_agent.stages.propose import ProposalContext, propose_patch, validate_proposal
from failing_test_agent.stages.providers import (
    BUNDLE_HASH_PLACEHOLDER,
    DIAGNOSIS_HASH_PLACEHOLDER,
    FakeDiagnosisProvider,
    FakePatchProvider,
)
from failing_test_agent.stages.retrieve import retrieve_evidence

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "broken_calculator"
CANNED_DIAGNOSIS = ROOT / "fixtures" / "diagnoses" / "broken_calculator.json"
CANNED_PATCH = ROOT / "fixtures" / "patches" / "broken_calculator.json"
TARGET = "tests/test_calculator.py::test_divide"
SOURCE = "src/broken_calculator/__init__.py"
DIAGNOSIS_SHA = "sha256:diagnosis"
GOOD_DIFF = json.loads(CANNED_PATCH.read_text())["diff"]


def fixture_files() -> dict[str, str]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_text()
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def payload(**overrides: object) -> dict:
    base = json.loads(CANNED_PATCH.read_text().replace(DIAGNOSIS_HASH_PLACEHOLDER, DIAGNOSIS_SHA))
    base.update(overrides)
    return base


def context(repo: Path, **overrides: object) -> ProposalContext:
    diagnosis = Diagnosis.model_validate_json(
        CANNED_DIAGNOSIS.read_text().replace(BUNDLE_HASH_PLACEHOLDER, "sha256:bundle")
    )
    values: dict[str, object] = {
        "diagnosis": diagnosis,
        "diagnosis_sha256": DIAGNOSIS_SHA,
        "worktree": repo,
        "base_commit": git(repo, "rev-parse", "HEAD"),
        "known_node_ids": frozenset({TARGET}),
        "evidence_paths": frozenset({SOURCE, "tests/test_calculator.py"}),
    }
    values.update(overrides)
    return ProposalContext(**values)


def codes(repo: Path, proposal: object, **context_overrides: object) -> set[str]:
    raw = proposal if isinstance(proposal, str) else json.dumps(proposal)
    return {problem.code for problem in validate_proposal(raw, context(repo, **context_overrides))[1]}


def on_path(path: str, body: str = "@@ -1,2 +1,2 @@\n def divide(a: float, b: float) -> float:\n-    return a * b\n+    return a / b\n") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{body}"


@pytest.fixture
def repo(make_repo) -> Path:
    return make_repo(fixture_files())


def test_canned_patch_passes_policy(repo: Path) -> None:
    proposal, problems, warnings, files = validate_proposal(json.dumps(payload()), context(repo))

    assert problems == [] and warnings == []
    assert [(change.path, change.added, change.removed) for change in files] == [(SOURCE, 1, 1)]
    assert files[0].changes == [("-", "    return a * b"), ("+", "    return a / b")]
    assert proposal.addresses_claim_id == "c2"


REJECTED = {
    "path_traversal": on_path("../outside.py"),
    "absolute_path": "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-a\n+b\n",
    "absolute_path (prefixed)": on_path("/etc/passwd"),
    "absolute_path (drive)": on_path("C:/Windows/win.ini"),
    "git_path": on_path(".git/config"),
    "unsafe_path": on_path("src\\\\broken_calculator\\\\__init__.py"),
    "test_edit": on_path("tests/test_calculator.py"),
    "test_edit (conftest)": on_path("src/conftest.py"),
    "test_edit (pytest.ini)": on_path("pytest.ini"),
    "config_edit": on_path("pyproject.toml"),
    "config_edit (requirements)": on_path("requirements-dev.txt"),
    "config_edit (workflow)": on_path(".github/workflows/ci.yml"),
    "path_not_allowed": on_path("docs/notes.py"),
    "target_missing": on_path("src/broken_calculator/nope.py"),
    "new_file": (
        "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    ),
    "deleted_file": (
        f"diff --git a/{SOURCE} b/{SOURCE}\ndeleted file mode 100644\n"
        f"--- a/{SOURCE}\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
        "-def divide(a: float, b: float) -> float:\n-    return a * b\n"
    ),
    "rename": (
        f"diff --git a/{SOURCE} b/src/broken_calculator/core.py\nsimilarity index 100%\n"
        f"rename from {SOURCE}\nrename to src/broken_calculator/core.py\n"
    ),
    "binary_patch": (
        f"diff --git a/{SOURCE} b/{SOURCE}\nindex 1111111..2222222 100644\n"
        "GIT binary patch\nliteral 3\nKcmZQzU|?7O00031\n\n"
    ),
    "mode_change": f"diff --git a/{SOURCE} b/{SOURCE}\nold mode 100644\nnew mode 100755\n",
    "symlink": (
        "diff --git a/src/link.py b/src/link.py\nnew file mode 120000\n"
        "--- /dev/null\n+++ b/src/link.py\n@@ -0,0 +1 @@\n+/etc/passwd\n"
    ),
    "path_mismatch": (
        f"diff --git a/{SOURCE} b/{SOURCE}\n--- a/{SOURCE}\n+++ b/src/broken_calculator/other.py\n"
        "@@ -1,2 +1,2 @@\n def divide(a: float, b: float) -> float:\n-    return a * b\n+    return a / b\n"
    ),
    "crlf_diff": GOOD_DIFF.replace("\n", "\r\n"),
    "malformed_diff": "please change the star to a slash\n",
    "malformed_diff (short hunk)": on_path(SOURCE, "@@ -1,5 +1,5 @@\n-    return a * b\n+    return a / b\n"),
    "empty_diff": "",
    "duplicate_file": GOOD_DIFF + GOOD_DIFF,
    "too_many_changed_lines": on_path(
        SOURCE,
        "@@ -1,2 +1,302 @@\n def divide(a: float, b: float) -> float:\n     return a * b\n"
        + "+# pad\n" * 300,
    ),
    "diff_too_large": on_path(SOURCE, "@@ -1,2 +1,3 @@\n" + "+" + "x" * 200_000 + "\n"),
}


@pytest.mark.parametrize("label", sorted(REJECTED))
def test_each_policy_rule_rejects_its_own_diff(repo: Path, label: str) -> None:
    expected = {label.split(" (")[0]}
    if label == "symlink":
        expected = {"symlink", "new_file"}

    assert codes(repo, payload(diff=REJECTED[label])) == expected


def test_proposal_level_rules(repo: Path) -> None:
    assert codes(repo, payload(diagnosis_sha256="sha256:other")) == {"diagnosis_hash_mismatch"}
    assert codes(repo, payload(addresses_claim_id="c3")) == {"claim_mismatch"}
    assert codes(repo, payload(expected_effects=["tests/test_x.py::test_made_up"])) == {
        "unknown_expected_effect"
    }
    assert codes(repo, payload(approved=True)) == {"schema_violation"}
    assert codes(repo, payload(rationale="r" * 5000)) == {"text_too_long"}
    assert codes(repo, "{not json") == {"invalid_json"}
    assert codes(repo, "x" * 500_000) == {"output_too_large"}
    assert codes(repo, payload(), policy=PatchPolicy(max_files=0)) == {"too_many_files"}


def test_file_outside_the_evidence_is_a_warning_not_a_rejection(repo: Path) -> None:
    _, problems, warnings, _ = validate_proposal(
        json.dumps(payload()), context(repo, evidence_paths=frozenset())
    )

    assert problems == []
    assert warnings == [f"{SOURCE} is changed but is not in the evidence bundle."]


@pytest.mark.skipif(sys.platform != "win32", reason="Directory junctions exist only on Windows.")
def test_target_reached_through_a_junction_is_rejected(repo: Path, tmp_path: Path) -> None:
    package = repo / "src" / "broken_calculator"
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(package, elsewhere)
    shutil.rmtree(package)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(package), str(elsewhere)], check=True, capture_output=True
    )
    assert git(repo, "status", "--porcelain") == ""  # Git itself does not notice.

    assert codes(repo, payload()) == {"target_is_link"}


def test_target_that_is_a_committed_symlink_is_rejected(make_repo, tmp_path: Path) -> None:
    repo = make_repo(fixture_files())
    real = repo / "src" / "broken_calculator" / "real.py"
    real.write_text("def divide(a: float, b: float) -> float:\n    return a * b\n")
    link = repo / "src" / "broken_calculator" / "linked.py"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Creating symlinks needs Developer Mode or elevated rights on this machine.")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add link")

    assert codes(repo, payload(diff=on_path("src/broken_calculator/linked.py"))) == {"target_is_link"}


def test_every_error_code_is_documented() -> None:
    stages = ROOT / "src" / "failing_test_agent" / "stages"
    source = (stages / "patch_policy.py").read_text() + (stages / "propose.py").read_text()
    used = set(re.findall(r'(?:Problem|DiffError)\(\s*"(\w+)"', source))
    used |= {"binary_patch", "symlink", "submodule", "rename", "mode_change", "test_edit", "config_edit"}
    documented = (ROOT / "workflow" / "05_propose" / "references" / "patch-policy.md").read_text()

    assert used and not sorted(code for code in used if f"`{code}`" not in documented)


# ---- stages -----------------------------------------------------------------


def diagnosed_run(repo: Path, home: Path, diagnosis: Path = CANNED_DIAGNOSIS) -> CreatedRun:
    run = create_run(repo, f"pytest {TARGET}", home, regression_command="pytest")
    capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())
    retrieve_evidence(home, run.run_id, run.attempt_id)
    assert diagnose(home, run.run_id, run.attempt_id, FakeDiagnosisProvider(diagnosis)).valid
    return run


def attempt_status(home: Path, attempt_id: str) -> str:
    connection = sqlite3.connect(home / "agent.db")
    try:
        return connection.execute(
            "SELECT status FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
    finally:
        connection.close()


def events(home: Path, run_id: str) -> list[dict]:
    connection = sqlite3.connect(home / "agent.db")
    try:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
    finally:
        connection.close()
    return [{"event_type": row[0], **json.loads(row[1])} for row in rows]


def canned_patch(tmp_path: Path, **overrides: object) -> Path:
    data = json.loads(CANNED_PATCH.read_text())
    data.update(overrides)
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(data))
    return path


def is_clean(worktree: Path) -> bool:
    inventory = worktree_inventory(worktree)
    return not inventory["diff"] and not inventory["status"]


def test_valid_patch_is_proposed_then_applied(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = diagnosed_run(repo, home)
    artifacts = run.worktree_path.parent / "artifacts"
    source = run.worktree_path / SOURCE

    proposed = propose_patch(home, run.run_id, run.attempt_id, FakePatchProvider(CANNED_PATCH))

    assert proposed.valid and proposed.warnings == []
    assert attempt_status(home, run.attempt_id) == "proposed"
    assert is_clean(run.worktree_path), "proposing must not touch the worktree"
    stored = json.loads((artifacts / "patch.json").read_text())
    assert stored["diagnosis_sha256"] == sha256_file(artifacts / "diagnosis.json")
    assert "return a / b" in (artifacts / "patch.md").read_text()
    log = events(home, run.run_id)
    recorded = next(event for event in log if event["event_type"] == "patch.proposed")
    assert recorded["input_sha256"] == sha256_file(CANNED_PATCH)
    assert recorded["placeholders_substituted"] == [DIAGNOSIS_HASH_PLACEHOLDER]
    diagnosis_event = next(event for event in log if event["event_type"] == "diagnosis.proposed")
    assert diagnosis_event["placeholders_substituted"] == [BUNDLE_HASH_PLACEHOLDER]
    assert diagnosis_event["input_sha256"] == sha256_file(CANNED_DIAGNOSIS)

    applied = apply_patch(home, run.run_id, run.attempt_id)

    assert applied.applied and applied.files == [SOURCE]
    assert attempt_status(home, run.attempt_id) == "applied"
    assert "return a / b" in source.read_text() and "a * b" not in source.read_text()
    assert applied.applied_diff_sha256 == sha256_file(artifacts / "applied.diff")
    (actual,) = parse_diff((artifacts / "applied.diff").read_text())
    (expected,) = parse_diff(stored["diff"])
    assert (actual.path, actual.changes) == (expected.path, expected.changes)
    assert git(run.worktree_path, "status", "--porcelain").split() == ["M", SOURCE]
    assert git(run.worktree_path, "rev-parse", "HEAD") == run.base_commit, "apply must not commit"
    assert [event["event_type"] for event in events(home, run.run_id)][-2:] == [
        "attempt.applying",
        "attempt.applied",
    ]

    with pytest.raises(StateError, match="needs a `proposed` attempt"):
        apply_patch(home, run.run_id, run.attempt_id)
    with pytest.raises(StateError, match="needs a `diagnosed` attempt"):
        propose_patch(home, run.run_id, run.attempt_id, FakePatchProvider(CANNED_PATCH))


@pytest.mark.parametrize(
    ("overrides", "reason", "code"),
    [
        ({"diff": on_path("tests/test_calculator.py")}, "policy_violation", "test_edit"),
        ({"addresses_claim_id": "c1"}, "policy_violation", "claim_mismatch"),
        ({"status": "applied"}, "invalid_proposal", "schema_violation"),
    ],
)
def test_rejected_proposal_fails_the_attempt_and_keeps_the_raw_output(
    repo: Path, tmp_path: Path, overrides: dict, reason: str, code: str
) -> None:
    home = tmp_path / "home"
    run = diagnosed_run(repo, home)

    outcome = propose_patch(
        home, run.run_id, run.attempt_id, FakePatchProvider(canned_patch(tmp_path, **overrides))
    )

    assert not outcome.valid and outcome.reason == reason
    assert attempt_status(home, run.attempt_id) == "failed"
    last = events(home, run.run_id)[-1]
    assert (last["event_type"], last["reason"], last["error_codes"]) == ("attempt.failed", reason, [code])
    artifacts = run.worktree_path.parent / "artifacts"
    assert (artifacts / "patch.raw.txt").exists() and not (artifacts / "patch.json").exists()
    assert is_clean(run.worktree_path)


def test_patch_that_does_not_apply_fails_and_leaves_the_worktree_clean(
    repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    run = diagnosed_run(repo, home)
    stale = GOOD_DIFF.replace("-    return a * b", "-    return a + b")
    assert propose_patch(
        home, run.run_id, run.attempt_id, FakePatchProvider(canned_patch(tmp_path, diff=stale))
    ).valid

    outcome = apply_patch(home, run.run_id, run.attempt_id)

    assert not outcome.applied and "git apply --check" in outcome.detail
    assert attempt_status(home, run.attempt_id) == "failed"
    last = events(home, run.run_id)[-1]
    assert (last["reason"], last["worktree_restored"]) == ("apply_failed", True)
    assert is_clean(run.worktree_path)
    assert not (run.worktree_path.parent / "artifacts" / "applied.diff").exists()


def test_restore_returns_a_dirty_worktree_to_the_base_commit(repo: Path, tmp_path: Path) -> None:
    run = create_run(repo, f"pytest {TARGET}", tmp_path / "home")
    (run.worktree_path / SOURCE).write_text("broken = True\n")
    (run.worktree_path / "stray.txt").write_text("x")
    (run.worktree_path / "src" / "__pycache__").mkdir()
    assert not is_clean(run.worktree_path)

    assert restore_worktree(run.worktree_path) is True
    assert is_clean(run.worktree_path)
    assert "return a * b" in (run.worktree_path / SOURCE).read_text()
    assert git(repo, "status", "--porcelain") == "", "the developer's checkout is untouched"


def test_abstained_diagnosis_cannot_be_patched(repo: Path, tmp_path: Path) -> None:
    abstained = json.loads(CANNED_DIAGNOSIS.read_text())
    abstained.update(
        abstain=True,
        root_cause_claim_id=None,
        confidence="low",
        claims=[
            abstained["claims"][0],
            {"claim_id": "u1", "kind": "unknown", "statement": "Intended behaviour is not shown.", "evidence_ids": []},
        ],
    )
    canned = tmp_path / "abstain.json"
    canned.write_text(json.dumps(abstained))
    home = tmp_path / "home"
    run = diagnosed_run(repo, home, canned)

    with pytest.raises(StateError, match="abstained"):
        propose_patch(home, run.run_id, run.attempt_id, FakePatchProvider(CANNED_PATCH))
    assert attempt_status(home, run.attempt_id) == "diagnosed"


def test_tampered_proposal_or_dirty_worktree_is_refused_before_applying(
    repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    run = diagnosed_run(repo, home)
    propose_patch(home, run.run_id, run.attempt_id, FakePatchProvider(CANNED_PATCH))
    stored = run.worktree_path.parent / "artifacts" / "patch.json"
    original = stored.read_bytes()

    stored.write_text(stored.read_text().replace("a / b", "a - b"))
    with pytest.raises(StateError, match="does not match its recorded hash"):
        apply_patch(home, run.run_id, run.attempt_id)
    stored.write_bytes(original)

    (run.worktree_path / "stray.txt").write_text("x")
    with pytest.raises(StateError, match="not clean"):
        apply_patch(home, run.run_id, run.attempt_id)
    assert attempt_status(home, run.attempt_id) == "proposed"


class RaisingProvider:
    name = "raising"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def propose(self, diagnosis: Diagnosis, diagnosis_sha256: str, bundle: EvidenceBundle) -> str:
        raise self.error

    def provenance(self) -> dict[str, object]:
        return {}


@pytest.mark.parametrize(
    ("error", "expected"),
    [(KeyboardInterrupt(), "interrupted"), (RuntimeError("boom"), "failed")],
)
def test_death_in_either_stage_leaves_no_stage_in_progress(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, expected: str
) -> None:
    run = diagnosed_run(repo, tmp_path / "a")
    with pytest.raises(type(error)):
        propose_patch(tmp_path / "a", run.run_id, run.attempt_id, RaisingProvider(error))
    assert attempt_status(tmp_path / "a", run.attempt_id) == expected

    run = diagnosed_run(repo, tmp_path / "b")
    propose_patch(tmp_path / "b", run.run_id, run.attempt_id, FakePatchProvider(CANNED_PATCH))
    real_git = apply_stage._git

    def die_after_apply(worktree: Path, *args: str):
        result = real_git(worktree, *args)
        if args[0] == "apply" and "--check" not in args:
            raise error
        return result

    monkeypatch.setattr(apply_stage, "_git", die_after_apply)
    with pytest.raises(type(error)):
        apply_patch(tmp_path / "b", run.run_id, run.attempt_id)
    monkeypatch.undo()

    assert attempt_status(tmp_path / "b", run.attempt_id) == expected
    assert is_clean(run.worktree_path), "a patch applied before the crash must be rolled back"


def test_cli_propose_and_apply(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = diagnosed_run(repo, home)
    runner = CliRunner()
    env = {"FTA_HOME": str(home)}
    ids = [run.run_id, run.attempt_id]

    assert runner.invoke(app, ["attempt", "apply", *ids], env=env).exit_code == 2
    assert runner.invoke(app, ["attempt", "propose", *ids], env=env).exit_code == 2

    proposed = runner.invoke(
        app, ["attempt", "propose", *ids, "--provider", "fake", "--input", str(CANNED_PATCH)], env=env
    )
    assert proposed.exit_code == 0, proposed.output
    assert f"File:                        {SOURCE} (+1 -1)" in proposed.output
    assert "Worktree changed:            NO" in proposed.output

    applied = runner.invoke(app, ["attempt", "apply", *ids], env=env)
    assert applied.exit_code == 0, applied.output
    assert "Patch applied:               YES" in applied.output
    assert "Attempt state:               applied" in applied.output
