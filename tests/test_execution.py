from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from failing_test_agent.execution.backends import select_backend
from failing_test_agent.execution.docker_backend import DockerBackend
from failing_test_agent.core.errors import BackendUnavailableError, UnsafeExecutionConsentError
from failing_test_agent.execution.contracts import ExecutionRequest
from failing_test_agent.execution.local_backend import (
    BoundedReader,
    PytestReport,
    TrustedLocalBackend,
    classify,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "broken_calculator"


def make_request(project: Path, argv: list[str], **overrides: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "execution_id": f"exec_{uuid.uuid4().hex}",
        "run_id": "run_test",
        "attempt_id": "attempt_test",
        "phase": "baseline",
        "command_kind": "target",
        "argv": argv,
        "worktree_root": project,
        "working_directory": project,
        "timeout_seconds": 60,
        "termination_grace_seconds": 0.5,
    }
    values.update(overrides)
    return ExecutionRequest(**values)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
        ).stdout
        return f'"{pid}"' in listing
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_passing_target_produces_per_node_results(project: Path) -> None:
    (project / "test_ok.py").write_text(
        "import pytest\n\n"
        "def test_one():\n    assert True\n\n"
        "@pytest.mark.skip(reason='not now')\n"
        "def test_two():\n    assert False\n"
    )
    result = TrustedLocalBackend().run(make_request(project, ["pytest", "test_ok.py"]))

    assert result.status == "passed"
    assert result.exit_code == 0
    by_node = {case.node_id: case for case in result.test_cases}
    assert by_node["test_ok.py::test_one"].outcome == "passed"
    assert by_node["test_ok.py::test_one"].executed is True
    assert by_node["test_ok.py::test_two"].outcome == "skipped"
    assert by_node["test_ok.py::test_two"].executed is False
    assert result.intended_tests_executed is True
    assert not list(project.rglob(".pytest_cache"))
    assert not list(project.rglob("__pycache__"))


def test_failing_fixture_target_is_classified_as_tests_failed(tmp_path: Path) -> None:
    # A copy, so the tracked fixture stays plain files.
    repo = tmp_path / "broken_calculator"
    shutil.copytree(FIXTURE, repo)
    result = TrustedLocalBackend().run(
        make_request(repo, ["pytest", "tests/test_calculator.py::test_divide"])
    )

    assert result.status == "tests_failed"
    assert result.exit_code == 1
    assert result.intended_node_ids == ["tests/test_calculator.py::test_divide"]
    assert result.intended_tests_executed is True
    (case,) = result.test_cases
    assert case.node_id == "tests/test_calculator.py::test_divide"
    assert case.outcome == "failed"
    assert "assert" in (case.message or "")


def test_nonexistent_node_id_does_not_count_as_executed(project: Path) -> None:
    (project / "test_ok.py").write_text("def test_one():\n    assert True\n")
    result = TrustedLocalBackend().run(
        make_request(project, ["pytest", "test_ok.py::test_missing"])
    )

    assert result.status == "collection_error"
    assert result.intended_tests_executed is False
    assert result.test_cases == []


def test_skipped_or_deselected_intended_test_does_not_count_as_executed(project: Path) -> None:
    (project / "test_skip.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.skip(reason='weakened')\n"
        "def test_target():\n    assert False\n\n"
        "def test_other():\n    assert True\n"
    )
    backend = TrustedLocalBackend()
    skipped = backend.run(make_request(project, ["pytest", "test_skip.py::test_target"]))
    deselected = backend.run(
        make_request(project, ["pytest", "test_skip.py", "-k", "other", "test_skip.py::test_target"])
    )

    assert skipped.exit_code == 0
    assert skipped.intended_tests_executed is False
    assert deselected.intended_tests_executed is False


def test_collection_failure_is_distinguished_from_test_failure(project: Path) -> None:
    (project / "test_broken.py").write_text("def test_one(:\n    pass\n")
    result = TrustedLocalBackend().run(make_request(project, ["pytest", "test_broken.py"]))

    assert result.status == "collection_error"
    assert result.intended_tests_executed is False


def test_no_tests_collected_is_reported(project: Path) -> None:
    (project / "test_empty.py").write_text("VALUE = 1\n")
    result = TrustedLocalBackend().run(make_request(project, ["pytest", "test_empty.py"]))

    assert result.status == "no_tests_collected"
    assert result.intended_tests_executed is False


def test_hanging_subprocess_is_terminated_after_timeout(project: Path) -> None:
    (project / "test_hang.py").write_text(
        "import time\n\ndef test_hang():\n    time.sleep(120)\n"
    )
    started = time.monotonic()
    result = TrustedLocalBackend().run(
        make_request(project, ["pytest", "test_hang.py"], timeout_seconds=3)
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert result.intended_tests_executed is False
    assert time.monotonic() - started < 30


def test_stdout_and_stderr_are_truncated_at_byte_limits(project: Path) -> None:
    (project / "test_noisy.py").write_text(
        "import sys\n\n"
        "def test_noisy():\n"
        "    sys.stdout.write('o' * 300_000)\n"
        "    sys.stderr.write('e' * 300_000)\n"
    )
    result = TrustedLocalBackend().run(
        make_request(
            project,
            ["pytest", "-s", "test_noisy.py"],
            max_stdout_bytes=1000,
            max_stderr_bytes=500,
        )
    )

    assert result.status == "passed"
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 1000
    assert len(result.stderr.encode("utf-8")) <= 500


def test_bounded_reader_does_not_retain_the_full_stream() -> None:
    reader = BoundedReader(io.BufferedReader(io.BytesIO(b"x" * 1_000_000)), limit=100)
    reader.start()
    reader.join()

    assert reader.truncated is True
    assert len(reader.buffer) == 100
    assert not any(
        isinstance(value, (bytes, bytearray)) and len(value) > 100
        for value in vars(reader).values()
    )


def test_cancellation_terminates_the_child_process_tree(project: Path, tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    (project / "test_spawn.py").write_text(
        "import os, subprocess, sys, time\n\n"
        "def test_spawn():\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "    with open(os.environ['PID_FILE'], 'w') as handle:\n"
        "        handle.write(str(child.pid))\n"
        "    time.sleep(120)\n"
    )
    backend = TrustedLocalBackend()
    request = make_request(
        project, ["pytest", "test_spawn.py"], environment={"PID_FILE": str(pid_file)}
    )
    results = []
    worker = threading.Thread(target=lambda: results.append(backend.run(request)))
    worker.start()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (pid_file.exists() and pid_file.read_text()):
        time.sleep(0.05)
    grandchild = int(pid_file.read_text())
    assert pid_alive(grandchild)

    backend.cancel(request.execution_id)
    worker.join(timeout=30)

    assert not worker.is_alive()
    assert results[0].status == "cancelled"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and pid_alive(grandchild):
        time.sleep(0.1)
    assert not pid_alive(grandchild)


def test_environment_hashes_match_per_command_pair(tmp_path: Path) -> None:
    source = "def test_one():\n    assert True\n"
    hashes = {}
    for attempt in ("001", "002"):
        root = tmp_path / "attempts" / attempt / "worktree"
        root.mkdir(parents=True)
        (root / "test_ok.py").write_text(source)
        phase = "baseline" if attempt == "001" else "patched"
        backend = TrustedLocalBackend()
        hashes[attempt, "target"] = backend.run(
            make_request(root, ["pytest", "test_ok.py::test_one"], phase=phase)
        ).command_environment_sha256
        hashes[attempt, "regression"] = backend.run(
            make_request(root, ["pytest"], phase=phase, command_kind="regression")
        ).command_environment_sha256

    assert hashes["001", "target"] == hashes["002", "target"]
    assert hashes["001", "regression"] == hashes["002", "regression"]
    assert hashes["001", "target"] != hashes["001", "regression"]


def test_environment_hash_changes_with_selected_environment(project: Path) -> None:
    (project / "test_ok.py").write_text("def test_one():\n    assert True\n")
    backend = TrustedLocalBackend()
    plain = backend.run(make_request(project, ["pytest", "test_ok.py"]))
    changed = backend.run(
        make_request(project, ["pytest", "test_ok.py"], environment={"APP_MODE": "x"})
    )

    assert plain.command_environment_sha256 != changed.command_environment_sha256


def test_host_environment_is_not_inherited(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FTA_TEST_SECRET", "do-not-leak")
    (project / "test_env.py").write_text(
        "import os\n\ndef test_env():\n    assert 'FTA_TEST_SECRET' not in os.environ\n"
    )
    result = TrustedLocalBackend().run(make_request(project, ["pytest", "test_env.py"]))

    assert result.status == "passed"


def test_report_that_disagrees_with_exit_code_is_not_trusted() -> None:
    passing = {"node_id": "t.py::test_a", "outcome": "passed", "executed": True}
    forged = PytestReport(
        schema_version=1, exit_status=0, collected=["t.py::test_a"], collection_errors=[],
        cases=[passing],
    )

    assert classify(1, forged, None)[0] == "execution_error"
    assert classify(0, None, None)[0] == "execution_error"
    assert classify(0, forged, None) == ("passed", None)
    relabelled = forged.model_copy(update={"exit_status": 1})
    assert classify(1, relabelled, None)[0] == "execution_error"


def test_working_directory_must_be_inside_worktree(project: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside worktree_root"):
        make_request(project, ["pytest"], working_directory=tmp_path)


def test_local_execution_requires_explicit_unsafe_consent() -> None:
    with pytest.raises(UnsafeExecutionConsentError, match="not a security sandbox"):
        select_backend("trusted-local")

    backend = select_backend("trusted-local", allow_unsafe_local_execution=True)
    assert isinstance(backend, TrustedLocalBackend)


def test_docker_selection_fails_closed_when_docker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DockerBackend, "available", lambda self: False)

    # Consent to local execution must not turn into a silent fallback.
    with pytest.raises(BackendUnavailableError, match="Docker execution is unavailable"):
        select_backend("docker", allow_unsafe_local_execution=True)
    with pytest.raises(BackendUnavailableError):
        select_backend()


@pytest.mark.skipif(
    not DockerBackend().available(),
    reason="Docker is not installed or not running; DockerBackend.run is not implemented yet.",
)
def test_docker_backend_runs_target_in_container(tmp_path: Path) -> None:
    repo = tmp_path / "broken_calculator"
    shutil.copytree(FIXTURE, repo)
    result = DockerBackend().run(
        make_request(repo, ["pytest", "tests/test_calculator.py::test_divide"])
    )

    assert result.status == "tests_failed"
