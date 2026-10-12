# stages, one module per pipeline stage

One job: each module implements exactly one stage contract from `workflow/`.

| Module | Contract | Reads | Writes |
|---|---|---|---|
| `create.py` | `workflow/01_create` | a clean Git repository | manifest, events, worktree |
| `capture.py` | `workflow/02_capture` | the worktree, via an execution backend | baseline results and summary |
| `retrieve.py` | `workflow/03_retrieve` | baseline target result, worktree source | evidence bundle |
| `diagnose.py` | `workflow/04_diagnose` | evidence bundle, provider text | raw, canonical and readable diagnosis |

## The shape of a stage function

```python
run, attempt = load_current_attempt(db, run_id, attempt_id, AttemptStatus.<required>)
# verify inputs (hashes, worktree) and raise StateError before changing anything
recorder.record("attempt.<doing>", {...}, transition_to=(<required>, <doing>))
with stage_guard(recorder, <doing>):
    # do the work
    recorder.record("attempt.<done>", {...}, artifact=(kind, file_name, text),
                    transition_to=(<doing>, <done>))
```

The helpers come from `core/lifecycle.py`. `create.py` predates them and does its own transaction.

## Rules

- A stage reads earlier results from artifacts, after re-checking their hashes. It does not call another stage's functions to recompute them.
- Keep the part that decides (for example `build_evidence`, `validate_diagnosis`) a pure function of its inputs, separate from the part that records. That is what the tests exercise directly.
- When a stage changes, update its contract in `workflow/` in the same change.
