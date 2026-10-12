# Validryn

**Intelligence you can inspect, verify, and trust.**

Validryn is a workbench for auditable AI-assisted software investigation. It turns failing tests into reproducible investigation runs with isolated attempts, structured evidence, deterministic verification, and human approval.

> **Project status:** Early V1 development. Validryn currently creates runs, executes Pytest through a trusted-local backend, captures a baseline on the unpatched commit, and retrieves a source-linked evidence bundle. It does not yet diagnose failures, generate patches, or execute untrusted repository code in a security sandbox.

## Why Validryn

Most coding agents optimize for producing an answer or patch quickly. Validryn is being designed around a different question:

> Can every important result be reproduced, inspected, verified, and tied to the exact evidence and human decision that produced it?

The project separates responsibilities deliberately:

- Models may propose diagnoses and patches.
- Deterministic Python code controls state transitions and validation.
- Execution backends run explicitly allowed commands.
- Artifacts preserve evidence, outputs, and history.
- Humans approve or reject exact, hash-bound results.
- Only validated experiences may later become reusable memory.

## Current Scope

The first workflow focuses on one measurable task:

```text
failing Pytest target
        ↓
capture repository snapshot
        ↓
gather relevant evidence
        ↓
propose diagnosis and patch
        ↓
verify target and regressions
        ↓
human review
        ↓
record a trustworthy episode
```

Validryn is intentionally building this task loop before adding long-term memory, semantic retrieval, autonomous learning, or multi-agent coordination.

## Implemented

**Run creation** (`fta run create`)

- Creates a run from a clean Git repository and records the exact base commit.
- Creates attempt 1 in a detached Git worktree.
- Writes a versioned run manifest and an event history, atomically.
- Persists runs, attempts, events, and artifact hashes in SQLite in one transaction.
- Rejects dirty repositories without modifying the developer's checkout.
- Cleans up incomplete state when worktree creation fails.

**Test execution** (`ExecutionBackend`, `TrustedLocalBackend`)

- A wall-clock timeout and cancellation from another thread, both ending the whole process tree (a Job Object on Windows, the process group elsewhere).
- Captured stdout and stderr capped at configured byte limits; the excess is discarded, not stored.
- A minimal environment: only the variables Python needs to start, plus the ones the request names.
- Per-test results from a bundled Pytest plugin, cross-checked against Pytest's exit code. A report that disagrees with the exit code yields `execution_error`.
- `intended_tests_executed`, true only when each requested node ID was collected and its body ran; skipped, deselected, and missing tests do not count.
- A per-command environment hash, comparable between a baseline command and its patched twin.

**Baseline capture** (`fta attempt capture`)

- Runs the target test, and the regression command if one is configured, on the unpatched base commit.
- Confirms the requested test actually ran and failed there.
- Records which tests already fail before any patch exists. The target test is listed separately and left out of that set, so fixing the target is never counted as fixing a regression.
- Checks that execution left the worktree unchanged.
- Moves the attempt through an allowed-transition table and records hashed artifacts and events.

**Exact retrieval** (`fta attempt retrieve`)

- Reads the recorded baseline result (after re-checking its hash) instead of re-running tests.
- Extracts the failing test, the traceback frames inside the repository, and the local symbols the test uses, located with Python's `ast`. Repository code is parsed, never imported or run.
- Writes `evidence.json` and a readable `evidence.md`. Each item has an ID, a repository-relative path, a line range, the exact text, and a hash of that text.
- Is deterministic: the bundle holds no timestamp, and equal inputs give equal evidence.
- Is bounded: caps on items, lines per item, total bytes, and source file size, with a `truncated` flag.
- Never reads outside the worktree: absolute paths, `..`, links that resolve elsewhere, `.git`, virtual environments, and binary files are skipped.

**Diagnosis with a fake provider** (`fta attempt diagnose`)

- A provider returns raw text; today the only provider reads a canned file. A real model will later fit the same slot and pass through the same checks.
- The text must parse as JSON and match a strict schema: claims typed as fact, hypothesis, conflict, or unknown, each citing evidence IDs. Unknown fields are refused.
- The diagnosis must name the hash of this attempt's evidence bundle, cite only evidence IDs that exist, give every fact at least one citation, and quote only text that appears word for word in the evidence it cites.
- It must either name a root-cause claim or abstain with an `unknown` claim saying what is missing.
- The raw output is always kept and hashed. A valid diagnosis is also stored in canonical form with a readable copy. An invalid one fails the attempt with one error code per broken rule.

**Recovery**

- Ctrl+C or an error during a stage marks the attempt `interrupted` or `failed`.
- Each in-progress stage records its process ID and start time. Every `fta` command first marks attempts whose process no longer exists as `interrupted` (reason `process_lost`).

## Not Implemented Yet

The following capabilities are planned but should not be considered available:

- Docker-based security isolation (the backend interface exists; `run` is not implemented).
- Lexical or semantic search beyond exact retrieval.
- LLM-generated diagnoses (the stage accepts only canned input) and any patch generation.
- Patch-policy enforcement.
- Patched verification and per-test regression comparison.
- Hash-bound human approval.
- Attempt revision: a failed or interrupted attempt cannot yet be retried within the same run.
- Episodic memory retrieval.
- Lesson proposal, validation, and promotion.

## Architecture

Validryn separates the platform into five conceptual areas:

| Area | Responsibility |
|---|---|
| **Validryn Run** | Reproducible runs, attempts, state transitions, and recovery |
| **Validryn Evidence** | Source-linked evidence bundles and claim provenance |
| **Validryn Review** | Human inspection, approval, rejection, and correction capture |
| **Validryn Memory** | Previous episodes, approved facts, and scoped procedures |
| **Validryn Evaluate** | Regression suites, lesson validation, and system evaluation |

The initial implementation uses:

- Python 3.12+
- Typer for the CLI
- Pydantic for structured contracts
- SQLite for run metadata and event records
- Git worktrees for reproducible repository snapshots
- Pytest for acceptance tests

A Git worktree isolates the source checkout but is **not** a security sandbox. Untrusted tests must eventually run through a restricted execution backend rather than directly on the host.

## Project Layout

```text
validryn/
├── fixtures/
│   └── broken_calculator/
├── scripts/
├── src/
│   └── failing_test_agent/
├── tests/
├── pyproject.toml
└── README.md
```

The Python package and CLI still use the temporary `failing_test_agent` and `fta` names. They will be migrated carefully after the V1 execution contracts are stable.

## Getting Started

### Requirements

- Python 3.12 or newer
- Git

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

### Create a run

The tracked fixture in `fixtures/broken_calculator` is plain files. The init script copies it to `.fta/fixtures/broken_calculator` and makes that copy a Git repository, so the main repository never contains a nested one.

```powershell
.\scripts\init-fixture.ps1
$env:FTA_HOME = "$PWD\.fta"
fta run create `
  --repo .\.fta\fixtures\broken_calculator `
  --test "pytest tests/test_calculator.py::test_divide" `
  --regression "pytest"
```

```bash
bash scripts/init-fixture.sh
export FTA_HOME="$PWD/.fta"
fta run create \
  --repo ./.fta/fixtures/broken_calculator \
  --test "pytest tests/test_calculator.py::test_divide" \
  --regression "pytest"
```

The command prints the run ID, attempt ID, base commit, detached worktree path, and manifest path. `--regression` is optional.

### Capture the baseline

```powershell
fta attempt capture RUN_ID ATTEMPT_ID `
  --backend trusted-local `
  --allow-unsafe-local-execution
```

Without `--backend`, the command selects Docker and stops if Docker is unavailable. A capture moves the attempt from `created` through `capturing` to one of:

- `captured`: the requested test ran on the base commit and failed there, and execution left the worktree unchanged.
- `failed`: the test passed, did not run, could not be collected, timed out, or execution changed files in the worktree. The reason is recorded.
- `interrupted`: the capture was stopped with Ctrl+C.

A captured attempt has three files in `attempts/001/artifacts/`, each hashed and registered in SQLite: `baseline-target.json` and `baseline-regression.json` (full per-test results) and `baseline.json` (the summary). The command exits 0 when captured, 1 when the attempt failed, and 2 when it refused to start.

### Retrieve evidence

```powershell
fta attempt retrieve RUN_ID ATTEMPT_ID
```

This needs a `captured` attempt whose worktree still matches the base commit, and moves it through `retrieving` to `retrieved`. It adds `evidence.json` and `evidence.md` to the attempt's `artifacts/` folder. For the calculator fixture the bundle holds two items: the `test_divide` function and the `divide` implementation it calls.

Symbols that cannot be resolved produce a warning, not a failure. Retrieval fails only when the bundle could not be trusted, for example when the failing test cannot be found in the worktree.

### Diagnose with a canned diagnosis

```powershell
fta attempt diagnose RUN_ID ATTEMPT_ID `
  --provider fake `
  --input .\fixtures\diagnoses\broken_calculator.json
```

This needs a `retrieved` attempt and moves it through `diagnosing` to `diagnosed`, or to `failed` (reason `invalid_diagnosis`) when a check rejects the output. It writes `diagnosis.raw.txt`, and for a valid diagnosis `diagnosis.json` and `diagnosis.md`. The command exits 0 when diagnosed, 1 when the diagnosis was rejected, and 2 when it refused to start.

An evidence bundle's hash is unique to its attempt, so a canned file writes `{{evidence_bundle_sha256}}` where the hash belongs and the fake provider fills it in.

### Drives without file ownership

If the project sits on a FAT32 or exFAT drive, Git refuses to use repositories there until the folder is listed as safe:

```powershell
git config --global --add safe.directory "D:/path/to/validryn"
git config --global --add safe.directory "D:/path/to/validryn/*"
```

## Development

Run the acceptance suite:

```bash
pytest
```

The remaining V1 increments, in order:

1. Fake patch proposal with host-side patch-policy validation and application.
2. Patched verification with per-test outcome comparison.
3. Hash-bound review and approval.
4. Real model provider.

The design behind this order is in [docs/architecture.md](docs/architecture.md).

The Docker backend is implemented against the existing contract as soon as a container runtime is available.

Validryn must never silently fall back from isolated execution to unsafe local execution.

## Design Principles

- **Evidence before assertion:** Important findings should point to inspectable source material.
- **Reproducibility before autonomy:** Each attempt begins from a known repository snapshot.
- **Deterministic enforcement:** Application code—not model text—enforces permissions and transitions.
- **Fresh stage context:** Workflow stages receive only their declared inputs and references.
- **Human-controlled action:** Analysis never implies permission to merge, deploy, or communicate externally.
- **Exact approvals:** A decision applies only to the reviewed commit, diff, evidence, and verification bundle.
- **Learning by promotion:** A successful attempt is an episode, not automatically a universal lesson.
- **Reversibility:** Attempts, decisions, memories, and promoted lessons retain history and support rollback.

## Roadmap

- [x] Deterministic run and attempt creation
- [x] Clean-repository enforcement
- [x] Detached-worktree snapshot
- [x] Atomic manifests and event records
- [x] Transactional SQLite persistence
- [x] Execution backend contracts
- [x] Trusted local fixture runner
- [x] Machine-readable Pytest results
- [x] Baseline verification
- [x] Exact evidence retrieval
- [x] Recovery of attempts whose process was killed
- [x] Fake diagnosis stage with evidence-bound validation
- [ ] Restricted Docker execution
- [ ] Fake diagnosis and patch stages
- [ ] Patch validation and policy enforcement
- [ ] Patched-target and per-test regression comparison
- [ ] Hash-bound review and approval
- [ ] Real model integration
- [ ] Episodic memory retrieval
- [ ] Validated lesson promotion

## Security

Validryn is experimental software and should not currently execute code from untrusted repositories.

The only working backend, `trusted-local`, runs repository code with the permissions of your user account. It bounds time and captured output and cleans up the process tree, but it isolates nothing: that code can read your files, use the network, and start processes. It is returned only with explicit consent (`--allow-unsafe-local-execution`), and selecting Docker on a machine without Docker is an error rather than a fallback.

A detached Git worktree provides reproducibility and protects the primary checkout from ordinary modifications, but it does not prevent repository code from reading credentials, accessing the network, spawning processes, or modifying files elsewhere on the host. The planned Docker backend will enforce network, filesystem, capability, process, memory, CPU, and timeout restrictions.

If a vulnerability is discovered, do not open a public issue containing exploit details. Contact the maintainer privately through the repository owner's GitHub profile until a dedicated security policy and contact channel are published.

## Contributing

Validryn is in early architectural development. Small, test-backed contributions are preferred over broad framework changes.

Before opening a pull request:

- Keep changes within the current milestone.
- Add or update acceptance tests.
- Preserve deterministic behavior.
- Do not introduce automatic execution, deployment, or self-modifying workflows.
- Explain any state, schema, security, or compatibility implications.

A formal contributing guide and code of conduct will be added before broader community contribution begins.

## License

No license has been selected yet. Until a license file is added, the source is publicly visible but no open-source permissions are granted.

## Vision

Validryn is intended to become an evidence-to-action workbench where AI-assisted work remains inspectable at every stage:

```text
knowledge retrieval
        +
versioned workflow contracts
        +
deterministic execution
        +
evidence lineage
        +
human approval
        +
validated memory
```

The long-term goal is not an agent that merely acts independently. It is a system that becomes more useful without becoming less accountable.
