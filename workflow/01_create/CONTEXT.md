# 01_create, open a run on a clean snapshot

One job: record exactly which repository state and which test a run is about, and give attempt 1 its own worktree.

Command: `fta run create --repo PATH --test "pytest path::test" [--regression "pytest"]`

## Inputs

- Working: a Git repository with no uncommitted or untracked changes; the failing test command; optionally a regression command.
- Reference: `../_shared/execution-safety.md` (accepted command forms only).

Do NOT load: other stages' contracts. This stage runs no tests and reads no source.

## Process

1. Resolve the repository root and refuse if it is dirty. Nothing is stashed or modified.
2. Record `HEAD` as the base commit.
3. Create a detached worktree at that commit and confirm it is at that commit.
4. Write `manifest.json` and `events.jsonl`, then insert the run, attempt 1, both events and the manifest hash in one transaction.
5. If any step fails, remove the worktree and run folder so no half-made run remains.

## Outputs

- `runs/<run_id>/manifest.json`
- `runs/<run_id>/events.jsonl` (`run.created`, `attempt.created`)
- `runs/<run_id>/attempts/001/worktree/`
- Run `active`, attempt `created`.

## Human check

Confirm the printed base commit is the commit you meant to investigate, and that the test command names the one failing test.

## Code and tests

`src/failing_test_agent/stages/create.py`, `core/worktree.py` · `tests/test_create_run.py`
