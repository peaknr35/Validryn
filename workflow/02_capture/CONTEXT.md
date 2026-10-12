# 02_capture, prove the failure on the base commit

One job: run the target test, and the regression command if configured, on the unpatched worktree and record what happened.

Command: `fta attempt capture RUN_ID ATTEMPT_ID --backend trusted-local --allow-unsafe-local-execution [--timeout SECONDS]`

## Inputs

- Working: the attempt's worktree; the test and regression commands stored on the run.
- Reference: `../_shared/execution-safety.md`, `../_shared/artifact-rules.md`.

Needs: attempt `created`, current, run `active`; worktree clean and at the base commit.

Do NOT load: evidence or diagnosis material. Nothing here reads source code.

## Process

1. Select the backend. Refuse if Docker is chosen but unavailable, or local is chosen without consent.
2. Run the target command. Store the full per-test result.
3. Decide whether the failure is confirmed: the requested test was collected, its body ran, and it failed. A pass, a skip, a missing test, a collection error or a timeout does not count.
4. Run the regression command if there is one. Tests that are the target itself are listed separately and left out of the "already failing" set.
5. Check the worktree is unchanged.

## Outputs

- `artifacts/baseline-target.json`: full result of the target command.
- `artifacts/baseline-regression.json`: full result of the regression command, if configured.
- `artifacts/baseline.json`: the summary and verdict.
- Attempt `captured`, or `failed` with a reason.

## Human check

Read the printed summary. "Target fails on baseline" must be YES, and the backend line must match what you intended. If tests other than the target already fail, note them: they are not the patch's fault later.

## Code and tests

`src/failing_test_agent/stages/capture.py`, `execution/` · `tests/test_capture.py`, `tests/test_execution.py`
