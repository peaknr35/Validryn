# Failing Test Agent — Deterministic Foundation

This repository implements only the first V1 acceptance slice:

```bash
fta run create \
  --repo ./fixtures/broken_calculator \
  --test "pytest tests/test_calculator.py::test_divide"
```

It validates a clean Git repository, records `HEAD`, creates attempt 1 in a detached Git worktree, atomically writes `manifest.json` and `events.jsonl`, and commits the run, attempt, events, and manifest artifact to SQLite in one transaction.

It does **not** execute tests, retrieve evidence, call an LLM, generate a patch, or approve changes.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
bash scripts/init-fixture.sh
pytest
```

## Run

```bash
export FTA_HOME="$PWD/.fta"
fta run create \
  --repo ./fixtures/broken_calculator \
  --test "pytest tests/test_calculator.py::test_divide"
```

The command prints the run ID, attempt ID, base commit, detached worktree path, and manifest path.

## Enforced invariants

- Dirty repositories are rejected before application state is created.
- The user's branch and working files are not modified.
- A run is recorded as active only after its detached worktree and artifacts exist.
- Failed worktree creation is cleaned up and cannot leave an active run or attempt.
- Attempt 1 is the only current attempt.
- SQLite foreign keys are enabled on every connection.
- Event history exists in both SQLite and readable JSONL.

## Next implementation order

1. Container execution with read-only source mounts, timeout, bounded output, and cleanup.
2. Baseline target and regression verification using machine-readable Pytest results.
3. Exact retrieval.
4. Fake diagnosis and patch outputs.
5. Host-side patch validation and application.
6. Patched verification with per-node outcome comparison.
7. Hash-bound review and approval.
8. Real model provider.
