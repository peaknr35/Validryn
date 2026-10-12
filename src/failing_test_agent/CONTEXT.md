# failing_test_agent, the code that enforces the pipeline

One job: implement the stage contracts in `workflow/` as deterministic Python.

## Layout

| Path | Holds | Depends on |
|---|---|---|
| `cli.py` | The `fta` command. Parses arguments, calls one stage function, prints a summary. No logic of its own. | everything below |
| `stages/` | One module per pipeline stage. | `core/`, `execution/` |
| `execution/` | Running Pytest: contracts, backends, process control, result reporting. | `core/` |
| `core/` | Shared plumbing: data models, database, hashing, atomic writes, state transitions, Git worktrees. | nothing in this package |

Dependencies point downward only. `core/` must not import from `execution/` or `stages/`.

## Which module implements which stage

| Stage contract | Module | Entry function |
|---|---|---|
| `workflow/01_create` | `stages/create.py` | `create_run` |
| `workflow/02_capture` | `stages/capture.py` | `capture_baseline` |
| `workflow/03_retrieve` | `stages/retrieve.py` | `retrieve_evidence` |
| `workflow/04_diagnose` | `stages/diagnose.py` | `diagnose` |

Open the subpackage `CONTEXT.md` for the folder you are changing; you rarely need more than one.
