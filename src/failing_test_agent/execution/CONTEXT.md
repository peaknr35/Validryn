# execution, running Pytest under limits

One job: run one Pytest command and return a structured, bounded, per-test result.

| Module | Holds |
|---|---|
| `contracts.py` | `ExecutionRequest`, `ExecutionResult`, `CaseResult`, `FailureLocation`, and the `ExecutionBackend` interface. |
| `backends.py` | `select_backend`: the only place a backend is chosen. Enforces consent for local and no fallback from Docker. |
| `local_backend.py` | `TrustedLocalBackend`: runs Pytest on this machine, unsandboxed. Also classifies exit codes and cross-checks them against the report. |
| `docker_backend.py` | `DockerBackend`: detects Docker; `run` is not implemented. |
| `process_tree.py` | Start a child so its whole process tree can be ended (Job Object on Windows, process group elsewhere). |
| `environment.py` | The per-command environment hash, comparable between a baseline command and its patched twin. |
| `pytest_reporter.py` | A Pytest plugin loaded inside the test process. Writes per-test outcomes and traceback locations as JSON. |

## Things that are easy to break

- `pytest_reporter.py` runs inside the target's process and may import only the standard library and Pytest. Its file hash is part of the environment hash, so any edit changes that hash.
- The plugin is loaded by its dotted name, `failing_test_agent.execution.pytest_reporter`. Moving the file means changing `INJECTED_PYTEST_ARGS` in `local_backend.py`.
- The report comes from the same process as repository code, so it is never trusted alone: `classify` rejects a report that disagrees with the exit code.
- A new backend must set `name` and `sandboxed` truthfully; both are written into run records.

Rules explained for people: `workflow/_shared/execution-safety.md`.
