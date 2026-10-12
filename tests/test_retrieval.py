from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failing_test_agent.stages import retrieve as retrieval
from failing_test_agent.stages.capture import capture_baseline
from failing_test_agent.cli import app
from failing_test_agent.core.errors import StateError
from failing_test_agent.execution.contracts import CaseResult, ExecutionResult, FailureLocation
from failing_test_agent.core.hashing import sha256_file
from failing_test_agent.execution.local_backend import TrustedLocalBackend
from failing_test_agent.core.models import CreatedRun, EvidenceBundle
from failing_test_agent.stages.retrieve import build_evidence, bundle_json, retrieve_evidence
from failing_test_agent.stages.create import create_run

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "broken_calculator"
FIXTURE_TARGET = "pytest tests/test_calculator.py::test_divide"


def fixture_files() -> dict[str, str]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_text()
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def captured_run(repo: Path, home: Path, test: str) -> CreatedRun:
    run = create_run(repo, test, home)
    capture = capture_baseline(home, run.run_id, run.attempt_id, TrustedLocalBackend())
    assert capture.outcome == "captured", capture.failure_reason
    return run


def attempt_status(home: Path, attempt_id: str) -> str:
    connection = sqlite3.connect(home / "agent.db")
    try:
        return connection.execute(
            "SELECT status FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
    finally:
        connection.close()


def event_types(home: Path, run_id: str) -> list[str]:
    connection = sqlite3.connect(home / "agent.db")
    try:
        rows = connection.execute(
            "SELECT event_type FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def failed_result(node_id: str, locations: list[tuple[str, int, str]] = ()) -> ExecutionResult:
    return ExecutionResult(
        execution_id="exec_test",
        status="tests_failed",
        exit_code=1,
        test_cases=[
            CaseResult(
                node_id=node_id,
                outcome="failed",
                executed=True,
                failure_locations=[
                    FailureLocation(path=path, line=line, function=function)
                    for path, line, function in locations
                ],
            )
        ],
        intended_node_ids=[node_id],
        intended_tests_executed=True,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_seconds=0.1,
        command_environment_sha256="sha256:env",
    )


def build(worktree: Path, result: ExecutionResult) -> EvidenceBundle:
    return build_evidence(
        worktree,
        result,
        run_id="run_test",
        attempt_id="attempt_test",
        base_commit="0" * 40,
        baseline_target_sha256="sha256:baseline",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "test_it.py").write_text("def test_it():\n    assert False\n")
    return root


def test_calculator_fixture_yields_test_and_implementation(make_repo, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = captured_run(make_repo(fixture_files()), home, FIXTURE_TARGET)

    bundle = retrieve_evidence(home, run.run_id, run.attempt_id)

    assert attempt_status(home, run.attempt_id) == "retrieved"
    assert bundle.target_node_ids == ["tests/test_calculator.py::test_divide"]
    assert bundle.query_terms == ["test_divide", "divide"]
    assert bundle.warnings == []
    by_kind = {item.kind: item for item in bundle.evidence}
    assert [item.evidence_id for item in bundle.evidence] == ["ev_001", "ev_002"]
    test_item = by_kind["test_definition"]
    assert test_item.path == "tests/test_calculator.py"
    assert "def test_divide" in test_item.content
    # The traceback frame is inside the test, so it is folded into that snippet.
    assert test_item.failure_lines == [5]
    implementation = by_kind["symbol_definition"]
    assert implementation.path == "src/broken_calculator/__init__.py"
    assert implementation.symbol == "divide"
    assert "return a * b" in implementation.content

    # Every range resolves to exactly the stored content, and every hash is right.
    for item in bundle.evidence:
        assert not Path(item.path).is_absolute() and ".." not in item.path
        lines = (run.worktree_path / item.path).read_text().splitlines()
        assert "\n".join(lines[item.start_line - 1 : item.end_line]) + "\n" == item.content
        digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        assert item.content_sha256 == f"sha256:{digest}"

    assert event_types(home, run.run_id)[-3:] == [
        "attempt.retrieving",
        "evidence.rendered",
        "attempt.retrieved",
    ]
    jsonl = (run.manifest_path.parent / "events.jsonl").read_text().splitlines()
    assert json.loads(jsonl[-1])["event_type"] == "attempt.retrieved"
    artifacts = run.worktree_path.parent / "artifacts"
    assert (artifacts / "evidence.json").read_text() == bundle_json(bundle)
    assert "ev_002" in (artifacts / "evidence.md").read_text()
    connection = sqlite3.connect(home / "agent.db")
    recorded = dict(
        connection.execute(
            "SELECT kind, sha256 FROM artifacts WHERE attempt_id = ?", (run.attempt_id,)
        ).fetchall()
    )
    connection.close()
    assert recorded["evidence_bundle"] == sha256_file(artifacts / "evidence.json")
    assert recorded["evidence_readable"] == sha256_file(artifacts / "evidence.md")
    assert bundle.baseline_target_sha256 == recorded["baseline_target_result"]


def test_equivalent_inputs_produce_identical_evidence(make_repo, tmp_path: Path) -> None:
    repo = make_repo(fixture_files())
    bundles = []
    for name in ("home_a", "home_b"):
        run = captured_run(repo, tmp_path / name, FIXTURE_TARGET)
        bundles.append(retrieve_evidence(tmp_path / name, run.run_id, run.attempt_id))

    first, second = (bundle.model_dump(mode="json") for bundle in bundles)
    for key in ("evidence", "query_terms", "target_node_ids", "files_inspected", "warnings"):
        assert first[key] == second[key]

    result = failed_result("tests/test_calculator.py::test_divide")
    worktree = Path(run.worktree_path)
    assert bundle_json(build(worktree, result)) == bundle_json(build(worktree, result))


def test_only_a_captured_attempt_can_be_retrieved(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    created = create_run(clean_repo, "pytest test_calculator.py::test_divide", home)
    with pytest.raises(StateError, match="needs a `captured` attempt"):
        retrieve_evidence(home, created.run_id, created.attempt_id)
    assert attempt_status(home, created.attempt_id) == "created"

    capture_baseline(home, created.run_id, created.attempt_id, TrustedLocalBackend())
    retrieve_evidence(home, created.run_id, created.attempt_id)
    with pytest.raises(StateError, match="needs a `captured` attempt"):
        retrieve_evidence(home, created.run_id, created.attempt_id)
    assert attempt_status(home, created.attempt_id) == "retrieved"


def test_tampered_baseline_is_detected(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = captured_run(clean_repo, home, "pytest test_calculator.py::test_divide")
    baseline = run.worktree_path.parent / "artifacts" / "baseline-target.json"
    baseline.write_text(baseline.read_text().replace("failed", "passed"))

    with pytest.raises(StateError, match="does not match its recorded hash"):
        retrieve_evidence(home, run.run_id, run.attempt_id)
    assert attempt_status(home, run.attempt_id) == "captured"


def test_dirty_or_moved_worktree_is_rejected(clean_repo: Path, tmp_path: Path, make_repo) -> None:
    home = tmp_path / "home"
    run = captured_run(clean_repo, home, "pytest test_calculator.py::test_divide")
    source = run.worktree_path / "calculator.py"
    original = source.read_bytes()

    source.write_text("def divide(a, b):\n    return a / b\n")
    with pytest.raises(StateError, match="not clean"):
        retrieve_evidence(home, run.run_id, run.attempt_id)
    source.write_bytes(original)

    (run.worktree_path / "untracked.txt").write_text("x")
    with pytest.raises(StateError, match="not clean"):
        retrieve_evidence(home, run.run_id, run.attempt_id)
    (run.worktree_path / "untracked.txt").unlink()

    connection = sqlite3.connect(home / "agent.db")
    connection.execute(
        "UPDATE attempts SET base_commit = ? WHERE id = ?", ("f" * 40, run.attempt_id)
    )
    connection.commit()
    connection.close()
    with pytest.raises(StateError, match="no longer matches the recorded base commit"):
        retrieve_evidence(home, run.run_id, run.attempt_id)
    assert attempt_status(home, run.attempt_id) == "captured"


def test_retrieval_leaves_the_worktree_unchanged(clean_repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = captured_run(clean_repo, home, "pytest test_calculator.py::test_divide")
    before = sorted(path.name for path in run.worktree_path.iterdir())

    retrieve_evidence(home, run.run_id, run.attempt_id)

    assert sorted(path.name for path in run.worktree_path.iterdir()) == before


@pytest.mark.parametrize(
    ("error", "expected"),
    [(KeyboardInterrupt(), "interrupted"), (RuntimeError("boom"), "failed")],
)
def test_retrieval_that_dies_does_not_leave_attempt_retrieving(
    clean_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: str,
) -> None:
    home = tmp_path / "home"
    run = captured_run(clean_repo, home, "pytest test_calculator.py::test_divide")

    def explode(*args: object, **kwargs: object) -> EvidenceBundle:
        raise error

    monkeypatch.setattr(retrieval, "build_evidence", explode)
    with pytest.raises(type(error)):
        retrieve_evidence(home, run.run_id, run.attempt_id)

    assert attempt_status(home, run.attempt_id) == expected
    assert event_types(home, run.run_id)[-1] == f"attempt.{expected}"


def test_frame_in_implementation_and_overlap_are_deduplicated(project: Path) -> None:
    (project / "lib.py").write_text(
        "def helper(value):\n"
        "    if value:\n"
        "        raise ValueError('bad')\n"
        "    return value\n"
    )
    (project / "test_it.py").write_text(
        "from lib import helper\n\n\ndef test_it():\n    helper(1)\n"
    )
    bundle = build(
        project,
        failed_result(
            "test_it.py::test_it", [("test_it.py", 5, "test_it"), ("lib.py", 3, "helper")]
        ),
    )

    # test + helper: the test frame folds into the test, the symbol into the frame.
    assert [(item.kind, item.path) for item in bundle.evidence] == [
        ("test_definition", "test_it.py"),
        ("traceback_frame", "lib.py"),
    ]
    assert bundle.evidence[0].failure_lines == [5]
    assert bundle.evidence[1].failure_lines == [3]
    assert bundle.evidence[1].content.startswith("def helper")


def test_paths_outside_the_worktree_never_become_evidence(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.py"
    outside.write_text("TOKEN = 'do-not-read'\n")
    locations = [
        (str(outside), 1, "leak"),
        ("../secret.py", 1, "leak"),
        (".git/config", 1, "leak"),
        (".venv/lib/site-packages/dep.py", 1, "dep"),
    ]
    (project / ".venv" / "lib" / "site-packages").mkdir(parents=True)
    (project / ".venv" / "lib" / "site-packages" / "dep.py").write_text("X = 1\n")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]\n")

    bundle = build(project, failed_result("test_it.py::test_it", locations))

    assert [item.path for item in bundle.evidence] == ["test_it.py"]
    assert "do-not-read" not in bundle_json(bundle)
    assert any("not repository-relative" in warning for warning in bundle.warnings)


def test_symlink_cannot_escape_the_worktree(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.py"
    outside.write_text("TOKEN = 'do-not-read'\n")
    try:
        (project / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks needs Developer Mode or elevated rights on this machine.")

    bundle = build(project, failed_result("test_it.py::test_it", [("link.py", 1, "leak")]))

    assert [item.path for item in bundle.evidence] == ["test_it.py"]
    assert "do-not-read" not in bundle_json(bundle)
    assert any("outside the worktree" in warning for warning in bundle.warnings)


@pytest.mark.skipif(sys.platform != "win32", reason="Directory junctions exist only on Windows.")
def test_junction_cannot_escape_the_worktree(project: Path, tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.py").write_text("TOKEN = 'do-not-read'\n")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(project / "linkdir"), str(outside_dir)],
        check=True,
        capture_output=True,
    )
    assert (project / "linkdir" / "secret.py").exists()

    bundle = build(
        project, failed_result("test_it.py::test_it", [("linkdir/secret.py", 1, "leak")])
    )

    assert [item.path for item in bundle.evidence] == ["test_it.py"]
    assert "do-not-read" not in bundle_json(bundle)
    assert any("outside the worktree" in warning for warning in bundle.warnings)


def test_binary_and_oversized_files_are_ignored(project: Path) -> None:
    (project / "blob.py").write_bytes(b"x = 1\n\0\0\0")
    (project / "huge.py").write_text("x = 1\n" * 200_000)

    bundle = build(
        project,
        failed_result("test_it.py::test_it", [("blob.py", 1, "f"), ("huge.py", 1, "f")]),
    )

    assert [item.path for item in bundle.evidence] == ["test_it.py"]
    assert any("binary" in warning for warning in bundle.warnings)
    assert any("oversized" in warning for warning in bundle.warnings)


def test_evidence_is_bounded(project: Path) -> None:
    body = "".join(f"    value_{i} = {i}\n" for i in range(300))
    (project / "test_it.py").write_text(f"def test_it():\n{body}    assert False\n")
    for index in range(60):
        (project / f"mod_{index:02d}.py").write_text(f"def f():\n    return {index}\n")
    locations = [(f"mod_{index:02d}.py", 2, "f") for index in range(60)]

    bundle = build(project, failed_result("test_it.py::test_it", locations))

    assert len(bundle.evidence) == retrieval.MAX_EVIDENCE_ITEMS
    assert bundle.truncated is True
    assert bundle.evidence[0].truncated is True
    assert bundle.evidence[0].end_line == retrieval.MAX_LINES_PER_ITEM
    total = sum(len(item.content.encode("utf-8")) for item in bundle.evidence)
    assert total <= retrieval.MAX_TOTAL_CONTENT_BYTES


def test_unresolved_symbol_is_a_warning_not_a_failure(project: Path) -> None:
    (project / "lib.py").write_text("OTHER = 1\n")
    (project / "test_it.py").write_text(
        "import os\nfrom lib import missing\n\n\ndef test_it():\n    assert missing(os.sep)\n"
    )

    bundle = build(project, failed_result("test_it.py::test_it"))

    assert [item.kind for item in bundle.evidence] == ["test_definition"]
    assert any("Could not resolve `missing`" in warning for warning in bundle.warnings)


def test_class_methods_modules_and_reexports_are_resolved(project: Path) -> None:
    package = project / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .core import compute\n")
    (package / "core.py").write_text("def compute(x):\n    return x + 1\n")
    (project / "tools.py").write_text("LIMIT = 3\n\n\ndef scale(x):\n    return x * LIMIT\n")
    (project / "test_it.py").write_text(
        "import tools\nfrom pkg import compute\n\n\n"
        "class TestGroup:\n"
        "    def test_it(self):\n"
        "        assert compute(tools.scale(1)) == 0\n"
    )

    bundle = build(project, failed_result("test_it.py::TestGroup::test_it[case-1]"))

    found = {(item.path, item.symbol) for item in bundle.evidence if item.symbol}
    assert found == {("pkg/core.py", "compute"), ("tools.py", "scale")}
    assert bundle.evidence[0].content.startswith("    def test_it(self):")


def test_cli_retrieve_reports_the_bundle(make_repo, tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = captured_run(make_repo(fixture_files()), home, FIXTURE_TARGET)

    result = CliRunner().invoke(
        app, ["attempt", "retrieve", run.run_id, run.attempt_id], env={"FTA_HOME": str(home)}
    )

    assert result.exit_code == 0, result.output
    assert "Retrieval strategy:          exact-v1" in result.output
    assert "Target node:                 tests/test_calculator.py::test_divide" in result.output
    assert "Repository files inspected:  3" in result.output
    assert "Evidence items captured:     2" in result.output
    assert "Attempt state:               retrieved" in result.output
