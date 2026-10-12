# 03_retrieve, gather the source that explains the failure

One job: turn the captured failure into a small bundle of exact source snippets, each with an ID and a hash.

Command: `fta attempt retrieve RUN_ID ATTEMPT_ID`

## Inputs

- Working: `artifacts/baseline-target.json` (hash re-checked); the attempt's worktree.
- Reference: `../_shared/trust-boundaries.md`, `../_shared/artifact-rules.md`.

Needs: attempt `captured`; worktree clean and at the base commit.

Do NOT load: `baseline-regression.json` (other failures are not this run's subject); anything outside the worktree; no model, search index or embeddings are involved.

## Process

1. Take the failing target test and its traceback locations from the baseline result. Tests are not re-run.
2. Find the test function with `ast` and take its full source.
3. For each traceback frame inside the repository, take the enclosing function, or a few lines either side if it is long.
4. Resolve local symbols the test uses (imports, module attributes, helpers next to the test) to their definitions by mapping module names to files. Nothing is imported.
5. Fold a snippet that sits inside another into the larger one, keeping the traceback line numbers.
6. Sort by kind, path and line; assign `ev_001`, `ev_002`, …; apply the limits.

Skipped silently or with a warning: absolute paths, `..`, links resolving outside the worktree, `.git`, virtual environments, binary and oversized files.

Limits: 30 items, 80 lines per item, 200 KB total, 1 MB per source file.

The evidence kind `exact_match` is reserved for lexical search and is not produced by this strategy (`exact-v1`).

## Outputs

- `artifacts/evidence.json`: the bundle the next stage reads.
- `artifacts/evidence.md`: the same content for a person.
- Attempt `retrieved`, or `failed` if the failing test cannot be located.

## Human check

Open `evidence.md`. It should contain the failing test and the code you would expect to look at first. Read any warnings: an unresolved symbol means the bundle may be missing the code that matters.

## Code and tests

`src/failing_test_agent/stages/retrieve.py` · `tests/test_retrieval.py`
