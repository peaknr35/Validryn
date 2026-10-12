# workflow, the pipeline as contracts

One job: say, for each stage, what it reads, what it does, what it writes, and what a person checks.

These files are the readable definition of the pipeline. The Python code in `src/failing_test_agent/stages/` is what enforces it. Nothing in this folder is executed.

## What is here

| Path | Holds |
|---|---|
| `_shared/` | Rules every stage obeys. Stable across runs. |
| `01_create/` … `04_diagnose/` | One contract per built stage. The number is the order. |
| `planned.md` | Stages not built yet. They get a folder when they get code. |

## The stage pattern

Every built stage follows the same six steps, and a new stage must too:

1. Check preconditions and refuse without changing anything if they fail (wrong state, tampered input, dirty worktree).
2. Move the attempt into an in-progress state and record which process owns it.
3. Do the work.
4. Write each artifact to disk, hash it, then record the hash and an event in one database transaction.
5. Move the attempt to the success state, or to `failed` with a reason.
6. If the process dies, the attempt ends `interrupted` or `failed`, never stuck in progress.

## Adding a stage

1. Move its description out of `planned.md` into a new `NN_<stage>/CONTEXT.md`, using an existing contract as the template.
2. Add its states to `ATTEMPT_TRANSITIONS` in `src/failing_test_agent/core/models.py`.
3. Add `src/failing_test_agent/stages/<stage>.py`, a CLI command in `cli.py`, and `tests/test_<stage>.py`.
4. Update the stage table in the root `CONTEXT.md`.
