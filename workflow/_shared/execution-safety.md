# Execution safety

Running tests runs the repository's code. These rules govern how.

## Backends

| Backend | State | Isolation |
|---|---|---|
| `docker` | interface only; cannot run anything yet | intended default for repositories you do not control |
| `trusted-local` | working | none |

`trusted-local` runs code with the permissions of the current user. It can read files, use the network and start processes. Use it only on repositories you control.

## Rules

- Local execution requires both `--backend trusted-local` and `--allow-unsafe-local-execution`.
- Selecting `docker` when Docker is unavailable is an error. There is no fallback to local.
- Every record of an execution states which backend ran and whether it was sandboxed.
- A Git worktree keeps the developer's checkout untouched. It is not a security boundary.
- Only `pytest ...` and `python -m pytest ...` commands are accepted.

## What every execution is held to

- A wall-clock timeout, after which the whole process tree is ended.
- Captured output capped in bytes; the excess is discarded.
- A minimal environment: no inherited home, profile or credential variables.
- No bytecode or Pytest cache written into the worktree.
- A check afterwards that the worktree is unchanged.

Code: `src/failing_test_agent/execution/`.
