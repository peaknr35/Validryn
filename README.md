# Validryn

*Intelligence you can inspect, verify, and trust.*

Validryn is an auditable agent workbench: validated runs, verified evidence, human-approved decisions, and (later) controlled memory. Its first workflow is a failing-test agent, built one deterministic slice at a time. The Python package is `failing_test_agent` and the command is `fta`. Three slices exist so far.

**Run creation.** `fta run create` validates a clean Git repository, records `HEAD`, creates attempt 1 in a detached Git worktree, atomically writes `manifest.json` and `events.jsonl`, and commits the run, attempt, events, and manifest artifact to SQLite in one transaction.

**Test execution.** An `ExecutionBackend` interface with a `TrustedLocalBackend` that runs Pytest with a timeout, bounded output, process-tree cleanup, and machine-readable per-test results.

**Baseline capture.** `fta attempt capture` runs the target test, and the regression command if one is configured, on the unpatched base commit and records what happened.

It does **not** yet retrieve evidence, call an LLM, generate a patch, or approve changes.

## Setup (Windows PowerShell)

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

## Setup (bash)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Create a run

The tracked fixture in `fixtures/broken_calculator` is plain files. The init script copies it to `.fta/fixtures/broken_calculator` and makes that copy a Git repository.

```powershell
.\scripts\init-fixture.ps1
$env:FTA_HOME = "$PWD\.fta"
fta run create `
  --repo .\.fta\fixtures\broken_calculator `
  --test "pytest tests/test_calculator.py::test_divide"
```

```bash
bash scripts/init-fixture.sh
export FTA_HOME="$PWD/.fta"
fta run create \
  --repo ./.fta/fixtures/broken_calculator \
  --test "pytest tests/test_calculator.py::test_divide"
```

The command prints the run ID, attempt ID, base commit, detached worktree path, and manifest path.

Add `--regression "pytest"` to `run create` to configure a regression command for the run.

## Capture the baseline

```powershell
fta attempt capture RUN_ID ATTEMPT_ID `
  --backend trusted-local `
  --allow-unsafe-local-execution
```

Without `--backend`, the command selects Docker and stops if Docker is unavailable. A capture moves the attempt from `created` through `capturing` to one of:

- `captured`: the requested test ran on the base commit and failed there, and execution left the worktree unchanged.
- `failed`: the test passed, did not run, could not be collected, timed out, or execution changed files in the worktree. The reason is recorded.
- `interrupted`: the capture was stopped with Ctrl+C.

A captured attempt has three files in `attempts/001/artifacts/`, each hashed and registered in SQLite: `baseline-target.json` and `baseline-regression.json` (full per-test results) and `baseline.json` (the summary, including which tests already fail before any patch). The command exits 0 when captured, 1 when the attempt failed, and 2 when it refused to start.

If the process is killed outright during a capture, the attempt stays in `capturing`; startup reconciliation for that case is not built yet.

If the project sits on a drive that does not record file ownership (FAT32, exFAT), Git refuses to use repositories there until the folder is listed as safe:

```powershell
git config --global --add safe.directory "D:/path/to/failing-test-agent"
git config --global --add safe.directory "D:/path/to/failing-test-agent/*"
```

## Execution backends

| Backend | Status | Isolation |
|---|---|---|
| `docker` | Interface only; `run` is not implemented | Intended default for unknown repositories |
| `trusted-local` | Implemented | **None.** Not a sandbox |

`trusted-local` runs repository code with the permissions of your user account. It can read your files, use the network, and start processes. `select_backend` returns it only when called with `allow_unsafe_local_execution=True`, and selecting `docker` on a machine without Docker raises an error; there is no fallback from Docker to local.

What `trusted-local` does provide:

- A wall-clock timeout and cancellation from another thread, both ending the whole process tree (a Job Object on Windows, the process group elsewhere).
- Captured stdout and stderr capped at configured byte limits; the excess is discarded, not stored.
- A minimal environment: only the variables Python needs to start, plus the ones the request names.
- Per-test results from a bundled Pytest plugin (`failing_test_agent.pytest_reporter`), cross-checked against Pytest's exit code. A report that disagrees with the exit code yields `execution_error`.
- `intended_tests_executed`, true only when each requested node ID was collected and its body ran; skipped, deselected, and missing tests do not count.
- `command_environment_sha256`, comparable between a baseline command and its patched twin. It ignores the per-attempt worktree path.

## Enforced invariants

- Dirty repositories are rejected before application state is created.
- The user's branch and working files are not modified.
- A run is recorded as active only after its detached worktree and artifacts exist.
- Failed worktree creation is cleaned up and cannot leave an active run or attempt.
- Attempt 1 is the only current attempt.
- SQLite foreign keys are enabled on every connection.
- Event history exists in both SQLite and readable JSONL.
- Local execution requires explicit consent and Docker selection fails closed.
- Attempt states change only through an allowed-transition table, checked inside the database transaction.
- A baseline is captured only on a clean worktree at the recorded base commit, and only for the current attempt of an active run.

## Next implementation order

1. Exact retrieval.
2. Fake diagnosis and patch outputs.
3. Host-side patch validation and application.
4. Patched verification with per-node outcome comparison.
5. Hash-bound review and approval.
6. Real model provider.

Independent of that order: the Docker backend with read-only source mounts, behind the same interface, once Docker is available.
