# 06_apply, put the patch in the worktree and prove what changed

One job: apply the accepted proposal to the attempt's worktree and record the diff that actually resulted.

Command: `fta attempt apply RUN_ID ATTEMPT_ID`

## Inputs

- Working: `artifacts/patch.json` (hash re-checked); the attempt's worktree.
- Reference: `../05_propose/references/patch-policy.md`; `../_shared/trust-boundaries.md`.

Needs: attempt `proposed`; worktree clean and at the base commit.

Do NOT load: the provider. This stage takes no new input from a model; it applies exactly what was recorded.

## Process

1. Check the diff against patch policy again, against the worktree as it is now.
2. Run `git apply --check`, then `git apply`. No fuzz, no partial application, no unsafe paths: the patch applies exactly or not at all.
3. Read the result back with `git diff HEAD` and compare it with the proposal: the same files, the same removed and added lines.
4. Confirm nothing else changed: only those files are modified, and nothing new exists.
5. On any failure, or if the process dies part-way, reset the worktree to the base commit.

This stage runs no tests, and never commits, pushes or touches the developer's own checkout.

## Outputs

- `artifacts/applied.diff`: the diff read back from the worktree. This, not the proposal text, is what review will later approve.
- Attempt `applied`, or `failed` with reason `apply_failed` and the worktree restored.

## Human check

"Matches the proposal" must be YES. If the patch failed to apply, the usual cause is a proposal written against source that differs from the base commit; compare the diff's context lines with `evidence.md`.

## Code and tests

`src/failing_test_agent/stages/apply.py`, `stages/patch_policy.py` · `tests/test_patch.py`
