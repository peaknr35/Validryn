"""Exact retrieval: turn a captured failure into a bounded, hash-verified evidence bundle.

Deterministic and read-only. It parses source with `ast` and never imports or
executes repository code. No search index, embeddings, or model is involved.
"""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .db import Database
from .errors import FTAError, StateError
from .execution import CaseResult, ExecutionResult
from .hashing import sha256_file
from .local_backend import node_matches
from .models import AttemptStatus, EvidenceBundle, EvidenceItem
from .stages import Recorder, load_current_attempt, require_pristine_worktree, stage_guard
from .worktree import worktree_inventory

MAX_EVIDENCE_ITEMS = 30
MAX_LINES_PER_ITEM = 80
MAX_TOTAL_CONTENT_BYTES = 200_000
MAX_SOURCE_FILE_BYTES = 1_000_000
CONTEXT_LINES = 8

EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".tox",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    "__pycache__",
}

_KIND_ORDER = {"test_definition": 0, "traceback_frame": 1, "symbol_definition": 2, "exact_match": 3}
_FAILING_OUTCOMES = {"failed", "error"}
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class RetrievalError(FTAError):
    """No trustworthy evidence bundle could be produced."""


def _content_sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


class SourceReader:
    """Reads text files from inside the worktree and nowhere else."""

    def __init__(self, worktree: Path) -> None:
        self.root = worktree.resolve()
        self.warnings: list[str] = []
        self.inspected: set[str] = set()
        self._lines: dict[str, list[str] | None] = {}
        self._trees: dict[str, ast.Module | None] = {}

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def exists(self, relative: str) -> bool:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or PureWindowsPath(relative).drive:
            return False
        return self.root.joinpath(*pure.parts).is_file()

    def lines(self, relative: str) -> list[str] | None:
        relative = relative.replace("\\", "/")
        if relative not in self._lines:
            self._lines[relative] = self._read(relative)
        return self._lines[relative]

    def _read(self, relative: str) -> list[str] | None:
        pure = PurePosixPath(relative)
        windows = PureWindowsPath(relative)
        if pure.is_absolute() or windows.is_absolute() or windows.drive or ".." in pure.parts:
            self._warn(f"Ignored a path that is not repository-relative: {relative}")
            return None
        if EXCLUDED_DIRECTORIES.intersection(pure.parts):
            return None
        try:
            real = self.root.joinpath(*pure.parts).resolve(strict=True)
        except OSError:
            self._warn(f"Ignored a missing file: {relative}")
            return None
        # resolve() follows symlinks, so a link pointing out of the worktree fails here.
        if self.root not in real.parents:
            self._warn(f"Ignored a path that resolves outside the worktree: {relative}")
            return None
        if EXCLUDED_DIRECTORIES.intersection(real.relative_to(self.root).parts):
            return None
        if not real.is_file():
            return None
        if real.stat().st_size > MAX_SOURCE_FILE_BYTES:
            self._warn(f"Ignored an oversized file: {relative}")
            return None
        data = real.read_bytes()
        if b"\0" in data:
            self._warn(f"Ignored a binary file: {relative}")
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self._warn(f"Ignored a file that is not UTF-8 text: {relative}")
            return None
        self.inspected.add(relative)
        return text.splitlines()

    def tree(self, relative: str) -> ast.Module | None:
        relative = relative.replace("\\", "/")
        if relative not in self._trees:
            lines = self.lines(relative)
            parsed = None
            if lines is not None:
                try:
                    parsed = ast.parse("\n".join(lines) + "\n")
                except (SyntaxError, ValueError):
                    self._warn(f"Could not parse Python source: {relative}")
            self._trees[relative] = parsed
        return self._trees[relative]


@dataclass
class _Candidate:
    kind: str
    path: str
    start: int
    end: int
    reason: str
    node_id: str | None = None
    symbol: str | None = None
    failure_lines: set[int] = field(default_factory=set)
    truncated: bool = False


def _span(node: ast.AST) -> tuple[int, int]:
    decorators = [decorator.lineno for decorator in getattr(node, "decorator_list", [])]
    return min([node.lineno, *decorators]), node.end_lineno


def _find_nested(tree: ast.Module, names: list[str]) -> ast.AST | None:
    body, found = tree.body, None
    for name in names:
        found = next(
            (node for node in body if isinstance(node, _DEFINITIONS) and node.name == name), None
        )
        if found is None:
            return None
        body = found.body
    return found


def _enclosing_function(tree: ast.Module, line: int) -> ast.AST | None:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _span(node)
            if start <= line <= end and (best is None or start >= _span(best)[0]):
                best = node
    return best


def _search_roots(reader: SourceReader, test_path: str) -> list[str]:
    """Directories, relative to the worktree, that Python would import local modules from."""
    roots = ["", str(PurePosixPath(test_path).parent)]
    pyproject = reader.lines("pyproject.toml") if reader.exists("pyproject.toml") else None
    if pyproject is not None:
        try:
            options = tomllib.loads("\n".join(pyproject)).get("tool", {}).get("pytest", {})
            roots += [str(entry) for entry in options.get("ini_options", {}).get("pythonpath", [])]
        except (tomllib.TOMLDecodeError, AttributeError, TypeError):
            reader._warn("Could not read Pytest settings from pyproject.toml")
    ini = reader.lines("pytest.ini") if reader.exists("pytest.ini") else None
    if ini is not None:
        parser = configparser.ConfigParser()
        try:
            parser.read_string("\n".join(ini))
            roots += parser.get("pytest", "pythonpath", fallback="").split()
        except configparser.Error:
            reader._warn("Could not read pytest.ini")
    roots.append("src")
    ordered: list[str] = []
    for root in roots:
        root = root.replace("\\", "/").strip("/")
        if root == ".":
            root = ""
        if root not in ordered:
            ordered.append(root)
    return ordered


def _module_file(
    reader: SourceReader, module: str, level: int, importer: str, roots: list[str]
) -> str | None:
    parts = [part for part in module.split(".") if part]
    if level:
        base = list(PurePosixPath(importer).parent.parts)
        if level - 1 > len(base):
            return None
        bases = ["/".join(base[: len(base) - (level - 1)])]
    else:
        bases = roots
    for base in bases:
        stem = "/".join(part for part in [base, *parts] if part)
        package = "/".join(part for part in [stem, "__init__.py"] if part)
        for candidate in [f"{stem}.py", package] if parts else [package]:
            if reader.exists(candidate):
                return candidate
    return None


def _resolve_symbol(
    reader: SourceReader, module_path: str, name: str, roots: list[str], depth: int = 0
) -> tuple[str, int, int] | None:
    """Where `name` is defined, following package re-exports a few levels."""
    tree = reader.tree(module_path)
    if tree is None or depth > 3:
        return None
    for node in tree.body:
        if isinstance(node, _DEFINITIONS) and node.name == name:
            return (module_path, *_span(node))
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return module_path, node.lineno, node.end_lineno
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    source = _module_file(reader, node.module or "", node.level, module_path, roots)
                    if source:
                        return _resolve_symbol(reader, source, alias.name, roots, depth + 1)
    return None


def _symbol_candidates(
    reader: SourceReader, test_path: str, tree: ast.Module, function: ast.AST, node_id: str
) -> list[_Candidate]:
    roots = _search_roots(reader, test_path)
    used = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    attributes = {
        (node.value.id, node.attr)
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    # (symbol as written in the test, module file, name to look up there)
    lookups: list[tuple[str, str, str]] = []
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    imports += [
        node for node in ast.walk(function) if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in imports:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            if isinstance(node, ast.ImportFrom):
                if local not in used:
                    continue
                module = _module_file(reader, node.module or "", node.level, test_path, roots)
                submodule = _module_file(
                    reader, f"{node.module or ''}.{alias.name}", node.level, test_path, roots
                )
                if module:
                    lookups.append((local, module, alias.name))
                if submodule:
                    lookups += [
                        (f"{local}.{attr}", submodule, attr)
                        for owner, attr in attributes
                        if owner == local
                    ]
            elif alias.asname or "." not in alias.name:
                module = _module_file(reader, alias.name, 0, test_path, roots)
                if module:
                    lookups += [
                        (f"{local}.{attr}", module, attr)
                        for owner, attr in attributes
                        if owner == local
                    ]
    # Helpers defined next to the test.
    lookups += [
        (node.name, test_path, node.name)
        for node in tree.body
        if isinstance(node, _DEFINITIONS) and node.name in used and node is not function
    ]

    candidates = []
    for symbol, module, name in sorted(set(lookups)):
        resolved = _resolve_symbol(reader, module, name, roots)
        if resolved is None:
            reader._warn(f"Could not resolve `{symbol}` used by {node_id} in {module}")
            continue
        path, start, end = resolved
        candidates.append(
            _Candidate(
                "symbol_definition",
                path,
                start,
                end,
                "Local symbol used by the failing test",
                node_id=node_id,
                symbol=name,
            )
        )
    return candidates


def _case_candidates(reader: SourceReader, case: CaseResult) -> tuple[list[_Candidate], list[str]]:
    test_path, *names = case.node_id.split("::")
    if not names:
        raise RetrievalError(f"Node ID does not name a test function: {case.node_id}")
    names[-1] = re.sub(r"\[.*\]$", "", names[-1])
    tree = reader.tree(test_path)
    function = _find_nested(tree, names) if tree is not None else None
    if function is None:
        raise RetrievalError(f"Could not locate the failing test in the worktree: {case.node_id}")

    start, end = _span(function)
    candidates = [
        _Candidate(
            "test_definition",
            test_path,
            start,
            end,
            "Definition of the requested failing test",
            node_id=case.node_id,
        )
    ]
    for location in case.failure_locations:
        lines = reader.lines(location.path)
        if lines is None or not 1 <= location.line <= len(lines):
            continue
        frame_tree = reader.tree(location.path) if location.path.endswith(".py") else None
        enclosing = _enclosing_function(frame_tree, location.line) if frame_tree else None
        if enclosing is not None and _span(enclosing)[1] - _span(enclosing)[0] < MAX_LINES_PER_ITEM:
            frame_start, frame_end = _span(enclosing)
        else:
            frame_start = max(1, location.line - CONTEXT_LINES)
            frame_end = min(len(lines), location.line + CONTEXT_LINES)
        candidates.append(
            _Candidate(
                "traceback_frame",
                location.path.replace("\\", "/"),
                frame_start,
                frame_end,
                f"Traceback frame in `{location.function}`",
                node_id=case.node_id,
                failure_lines={location.line},
            )
        )
    symbols = _symbol_candidates(reader, test_path, tree, function, case.node_id)
    return candidates + symbols, [names[-1], *(symbol.symbol for symbol in symbols)]


def build_evidence(
    worktree: Path,
    result: ExecutionResult,
    *,
    run_id: str,
    attempt_id: str,
    base_commit: str,
    baseline_target_sha256: str,
) -> EvidenceBundle:
    """Pure function of the worktree contents and the baseline result."""
    reader = SourceReader(worktree)
    intended = result.intended_node_ids
    cases = sorted(
        (
            case
            for case in result.test_cases
            if case.outcome in _FAILING_OUTCOMES
            and (not intended or any(node_matches(case.node_id, target) for target in intended))
        ),
        key=lambda case: case.node_id,
    )
    if not cases:
        raise RetrievalError("The baseline result contains no failing target test.")

    candidates: list[_Candidate] = []
    query_terms: list[str] = []
    for case in cases:
        found, terms = _case_candidates(reader, case)
        candidates += found
        query_terms += [term for term in terms if term not in query_terms]

    # Stable order, then fold any snippet that lies inside an earlier one into it.
    candidates.sort(key=lambda c: (_KIND_ORDER[c.kind], c.path, c.start, c.end, c.symbol or ""))
    kept: list[_Candidate] = []
    for candidate in candidates:
        if candidate.end - candidate.start + 1 > MAX_LINES_PER_ITEM:
            candidate.end = candidate.start + MAX_LINES_PER_ITEM - 1
            candidate.truncated = True
        container = next(
            (
                other
                for other in kept
                if other.path == candidate.path
                and other.start <= candidate.start
                and candidate.end <= other.end
            ),
            None,
        )
        if container is None:
            kept.append(candidate)
        else:
            container.failure_lines |= candidate.failure_lines

    evidence: list[EvidenceItem] = []
    total_bytes = 0
    truncated = False
    for candidate in kept:
        lines = reader.lines(candidate.path)
        if lines is None:
            continue
        content = "\n".join(lines[candidate.start - 1 : candidate.end]) + "\n"
        size = len(content.encode("utf-8"))
        if len(evidence) >= MAX_EVIDENCE_ITEMS or total_bytes + size > MAX_TOTAL_CONTENT_BYTES:
            truncated = True
            break
        total_bytes += size
        evidence.append(
            EvidenceItem(
                evidence_id=f"ev_{len(evidence) + 1:03d}",
                kind=candidate.kind,
                path=candidate.path,
                start_line=candidate.start,
                end_line=candidate.end,
                content=content,
                content_sha256=_content_sha256(content),
                reason=candidate.reason,
                related_node_id=candidate.node_id,
                symbol=candidate.symbol,
                failure_lines=sorted(
                    line for line in candidate.failure_lines if candidate.start <= line <= candidate.end
                ),
                truncated=candidate.truncated,
            )
        )
    warnings = sorted(reader.warnings)
    if truncated:
        warnings.append("Evidence limits were reached; some snippets were left out.")
    return EvidenceBundle(
        run_id=run_id,
        attempt_id=attempt_id,
        base_commit=base_commit,
        baseline_target_sha256=baseline_target_sha256,
        target_node_ids=[case.node_id for case in cases],
        query_terms=query_terms,
        files_inspected=sorted(reader.inspected),
        evidence=evidence,
        warnings=warnings,
        truncated=truncated,
    )


def bundle_json(bundle: EvidenceBundle) -> str:
    return json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def render_markdown(bundle: EvidenceBundle) -> str:
    out = [
        f"# Evidence for attempt {bundle.attempt_id}",
        "",
        f"- Base commit: `{bundle.base_commit}`",
        f"- Strategy: `{bundle.retrieval_strategy}`",
        f"- Target: {', '.join(f'`{node}`' for node in bundle.target_node_ids)}",
        f"- Items: {len(bundle.evidence)}{' (truncated)' if bundle.truncated else ''}",
        "",
    ]
    for item in bundle.evidence:
        out += [
            f"## {item.evidence_id}: {item.kind}",
            "",
            f"`{item.path}` lines {item.start_line}-{item.end_line}. {item.reason}.",
        ]
        if item.failure_lines:
            out.append(f"Traceback points at line {', '.join(map(str, item.failure_lines))}.")
        fence = "````" if "```" in item.content else "```"
        out += ["", f"{fence}python", item.content.rstrip("\n"), fence, ""]
    if bundle.warnings:
        out += ["## Warnings", "", *(f"- {warning}" for warning in bundle.warnings), ""]
    return "\n".join(out)


def retrieve_evidence(home: Path, run_id: str, attempt_id: str) -> EvidenceBundle:
    """Build and record the evidence bundle for a captured attempt."""
    db = Database(home.resolve() / "agent.db")
    db.initialize()
    _, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.CAPTURED)
    worktree, before = require_pristine_worktree(attempt)

    connection = db.connect()
    try:
        artifact = connection.execute(
            "SELECT path, sha256 FROM artifacts WHERE attempt_id = ? AND kind = ?",
            (attempt_id, "baseline_target_result"),
        ).fetchone()
    finally:
        connection.close()
    if artifact is None or not Path(artifact["path"]).is_file():
        raise StateError("The baseline target result for this attempt is missing.")
    if sha256_file(Path(artifact["path"])) != artifact["sha256"]:
        raise StateError(
            "baseline-target.json does not match its recorded hash; it was changed after capture."
        )

    recorder = Recorder(db, run_id, attempt_id, worktree.parent)
    recorder.record(
        "attempt.retrieving",
        {"strategy": "exact-v1", "baseline_target_sha256": artifact["sha256"]},
        transition_to=(AttemptStatus.CAPTURED, AttemptStatus.RETRIEVING),
    )
    with stage_guard(recorder, AttemptStatus.RETRIEVING):
        result = ExecutionResult.model_validate_json(
            Path(artifact["path"]).read_text(encoding="utf-8")
        )
        bundle = build_evidence(
            worktree,
            result,
            run_id=run_id,
            attempt_id=attempt_id,
            base_commit=attempt["base_commit"],
            baseline_target_sha256=artifact["sha256"],
        )
        if worktree_inventory(worktree) != before:
            raise RetrievalError("The worktree changed during retrieval.")
        recorder.record(
            "evidence.rendered",
            {},
            artifact=("evidence_readable", "evidence.md", render_markdown(bundle)),
        )
        recorder.record(
            "attempt.retrieved",
            {
                "evidence_items": len(bundle.evidence),
                "files_inspected": len(bundle.files_inspected),
                "truncated": bundle.truncated,
            },
            artifact=("evidence_bundle", "evidence.json", bundle_json(bundle)),
            transition_to=(AttemptStatus.RETRIEVING, AttemptStatus.RETRIEVED),
        )
        return bundle
