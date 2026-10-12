"""Pytest plugin that writes per-test results as JSON.

Loaded with ``-p failing_test_agent.pytest_reporter`` inside the process that
runs the target tests, so it uses only the standard library and Pytest. It is inactive
unless ``FTA_PYTEST_REPORT`` names the file to write.

The report is produced in the same process as repository code and is therefore
not tamper-proof; the backend cross-checks it against the process exit code.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

REPORT_ENV = "FTA_PYTEST_REPORT"
REPORT_SCHEMA_VERSION = 1
MAX_MESSAGE_CHARS = 4000
MAX_FAILURE_LOCATIONS = 50


def _message(report) -> str | None:
    text = getattr(report, "longreprtext", "") or ""
    if not text and hasattr(report, "wasxfail"):
        text = str(report.wasxfail)
    return text[:MAX_MESSAGE_CHARS] or None


def _locations(excinfo, rootpath) -> list[dict[str, object]]:
    """Traceback frames whose file is inside the repository, outermost first."""
    root = os.path.realpath(str(rootpath))
    locations: list[dict[str, object]] = []
    for entry in excinfo.traceback:
        try:
            relative = os.path.relpath(os.path.realpath(str(entry.path)), root)
        except (ValueError, TypeError):
            continue
        parts = relative.replace("\\", "/").split("/")
        if parts[0] == ".." or os.path.isabs(relative) or "site-packages" in parts:
            continue
        locations.append(
            {"path": "/".join(parts), "line": int(entry.lineno) + 1, "function": str(entry.name)}
        )
    return locations[-MAX_FAILURE_LOCATIONS:]


class Reporter:
    def __init__(self, path: str) -> None:
        self.path = path
        self.collected: list[str] = []
        self.collection_errors: list[dict[str, object]] = []
        self.cases: dict[str, dict[str, object]] = {}
        self.failures: dict[tuple[str, str], tuple[str, list[dict[str, object]]]] = {}

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collection_errors.append(
                {"node_id": report.nodeid, "message": _message(report)}
            )

    def pytest_collection_finish(self, session) -> None:
        # After deselection, so `-k`/`-m` filtered tests do not count as collected.
        self.collected = [item.nodeid for item in session.items]

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if call.excinfo is not None and report.failed:
            self.failures[report.nodeid, report.when] = (
                call.excinfo.typename,
                _locations(call.excinfo, item.config.rootpath),
            )

    def pytest_runtest_logreport(self, report) -> None:
        case = self.cases.setdefault(
            report.nodeid,
            {
                "node_id": report.nodeid,
                "outcome": "passed",
                "executed": False,
                "duration_seconds": 0.0,
                "message": None,
                "exception_type": None,
                "failure_locations": [],
            },
        )
        failure = self.failures.get((report.nodeid, report.when))
        if failure:
            case["exception_type"], case["failure_locations"] = failure
        case["duration_seconds"] += float(getattr(report, "duration", 0.0) or 0.0)
        xfail = hasattr(report, "wasxfail")
        outcome = None
        if report.when == "call":
            case["executed"] = True
            if report.passed:
                outcome = "xpassed" if xfail else "passed"
            elif report.failed:
                outcome = "failed"
            else:
                outcome = "xfailed" if xfail else "skipped"
        elif report.failed:
            outcome = "error"
        elif report.skipped:
            outcome = "xfailed" if xfail else "skipped"
        if outcome is not None:
            case["outcome"] = outcome
            if outcome != "passed":
                case["message"] = _message(report)

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        payload = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "exit_status": int(exitstatus),
            "collected": self.collected,
            "collection_errors": self.collection_errors,
            "cases": list(self.cases.values()),
        }
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, temp_name = tempfile.mkstemp(prefix=".fta-report.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temp_name, self.path)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise


def pytest_configure(config) -> None:
    path = os.environ.get(REPORT_ENV)
    # xdist workers report through the controller process.
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(Reporter(path), "fta-reporter")
