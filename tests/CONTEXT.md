# tests, proof that each stage keeps its contract

One job: acceptance tests, written against behaviour a contract promises.

| File | Proves |
|---|---|
| `test_create_run.py` | `workflow/01_create`: run creation, dirty-repository refusal, cleanup after a failed worktree. |
| `test_execution.py` | `src/failing_test_agent/execution`: timeouts, output limits, process-tree cleanup, result classification, environment hashes, backend selection. |
| `test_capture.py` | `workflow/02_capture`, plus recovery of attempts whose process was lost. |
| `test_retrieval.py` | `workflow/03_retrieve`: what is extracted, containment inside the worktree, limits, determinism. |
| `test_diagnosis.py` | `workflow/04_diagnose`: one rejection test per rule, raw output kept, injected instructions inert. |
| `conftest.py` | `make_repo` (build a throwaway Git repository from a dict of files) and `clean_repo`. |

## Running

```powershell
pytest                      # everything, about 80 seconds
pytest tests/test_retrieval.py
```

Two tests skip on this machine by design: the Docker integration test (no Docker) and the symlink test (needs Developer Mode or elevated rights).

## Conventions

- Tests build their own repositories under Pytest's temporary directory. They never touch `.fta/` or the tracked fixtures.
- Tests that run real Pytest subprocesses are slow; test the pure deciding function directly where one exists.
- Every new validation rule gets a test that breaks exactly that rule.
