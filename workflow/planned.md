# Planned stages

Not built. Each gets a folder and a contract when it gets code. Build them in this order; do not skip ahead.

| # | Stage | One job | State after |
|---|---|---|---|
| 05 | propose | Accept a patch proposal (fake provider first) and apply it on the host only if it passes patch policy: allowed paths, no test or config edits, no renames, deletions, symlinks or mode changes, size limits. Check the resulting diff, not only the proposed one. | `patched` |
| 06 | verify | Re-run target and regression in an equivalent environment and compare outcomes per test against the baseline. Keep "patch applied", "target now passes" and "regressions" as separate facts. | `verified` |
| 07 | review | Show the diff, evidence, diagnosis and verification, and record a person's approve or reject bound to the exact hashes shown. Approval never commits, pushes or merges. | `approved` / `rejected` |
| 08 | record | Preserve the finished attempt as an immutable episode. | |

Also planned, outside the stage order:

- Docker execution backend behind the existing interface.
- Attempt revision, so a failed or rejected attempt can be followed by a fresh one in the same run.
- A real model provider, only after stages 05 to 07 work end to end with fakes.

Further out, and gated on evidence that the simpler version falls short: lexical search, episode memory, lesson promotion, hybrid retrieval. See `../docs/architecture.md`, "Build Gates".
