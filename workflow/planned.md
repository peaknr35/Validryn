# Planned stages

Not built. Each gets a folder and a contract when it gets code. Build them in this order; do not skip ahead.

| # | Stage | One job | State after |
|---|---|---|---|
| 07 | verify | Re-run target and regression on the patched worktree in an equivalent environment and compare outcomes per test against the baseline. Report separately: target fixed or not, new failures, failures that were already there, tests that newly pass. The target is counted on its own, as in capture. | `verified` |
| 08 | review | Show the applied diff, evidence, diagnosis and verification, and record a person's approve or reject bound to the exact hashes shown. Approval never commits, pushes or merges. | `approved` / `rejected` |
| 09 | record | Preserve the finished attempt as an immutable episode. | |

Also planned, outside the stage order:

- **Retry within a run.** A failed or rejected attempt should be followed by a new attempt in the same run, not a new run. The data model already allows it: a run can hold many attempts with one marked current, and artifacts are found by attempt and hash, so a new attempt can reuse a verified `evidence.json`. What is missing is only the command (`fta attempt retry --from-stage …`). Build it when real models start producing rejected output.
- **Docker execution backend** behind the existing interface.
- **A real model provider**, only after stages 07 and 08 work end to end with fakes. It must have no placeholder substitution, should use the provider's structured-output mode, and if it ever strips a code fence must do so in the provider adapter, keep the raw text, and log a warning.

Further out, and gated on evidence that the simpler version falls short: the knowledge plane beyond exact retrieval (lexical search, episode memory, hybrid retrieval). See `../docs/knowledge-plane.md` and `../docs/architecture.md`, "Build Gates".
