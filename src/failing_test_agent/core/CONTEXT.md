# core, shared plumbing

One job: the pieces every stage relies on, with no knowledge of any particular stage.

| Module | Holds |
|---|---|
| `models.py` | Every data shape: run and attempt statuses, `ATTEMPT_TRANSITIONS`, manifest, events, baseline capture, evidence bundle, diagnosis. Start here to learn the data. |
| `lifecycle.py` | How a stage behaves: precondition checks, state transitions, the `Recorder` (artifact + event in one transaction), `stage_guard` (failure handling), recovery of attempts whose process died. |
| `db.py` | SQLite schema, connections, small insert helpers, column migrations. |
| `worktree.py` | Git operations: clean check, base commit, detached worktree, change inventory. |
| `atomic.py` | Write-then-rename file writes. |
| `hashing.py` | SHA-256 of files and of canonical JSON. |
| `liveness.py` | Identify a process by PID and start time, on Windows and POSIX. |
| `errors.py` | Exception types. All derive from `FTAError`, which the CLI turns into exit code 2. |

## Rules

- Nothing here imports from `execution/` or `stages/`.
- A new attempt state is added in `models.py` (`AttemptStatus` and `ATTEMPT_TRANSITIONS`) and nowhere else.
- A new database column goes in both the `SCHEMA` text and the migration list in `db.py`.

Rules explained for people: `workflow/_shared/state-machine.md`, `workflow/_shared/artifact-rules.md`.
