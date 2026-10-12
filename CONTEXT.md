# The pipeline on one screen

One run investigates one failing test in one repository at one commit. A run has attempts; each attempt owns a clean detached Git worktree and moves through the stages below. Only Python code changes an attempt's state.

## Stages

| # | Stage | Command | Attempt state after | Writes | Status |
|---|---|---|---|---|---|
| 01 | create | `fta run create` | `created` | `manifest.json`, `events.jsonl`, worktree | built |
| 02 | capture | `fta attempt capture` | `captured` | `baseline-target.json`, `baseline-regression.json`, `baseline.json` | built |
| 03 | retrieve | `fta attempt retrieve` | `retrieved` | `evidence.json`, `evidence.md` | built |
| 04 | diagnose | `fta attempt diagnose` | `diagnosed` | `diagnosis.raw.txt`, `diagnosis.json`, `diagnosis.md` | built, fake provider only |
| 05 | propose | | `patched` | | planned |
| 06 | verify | | `verified` | | planned |
| 07 | review | | `approved` / `rejected` | | planned |
| 08 | record | | | | planned |

Each built stage has a contract in `workflow/NN_<stage>/CONTEXT.md`. Planned stages are described only in `workflow/planned.md`.

A stage that is running holds an in-progress state (`capturing`, `retrieving`, `diagnosing`). It ends in its success state, or in `failed` or `interrupted`. A failed or interrupted attempt cannot yet be retried; start a new run.

## Reading a run

Run data lives under `FTA_HOME` (default `.fta/`), never in Git.

```text
.fta/
├─ agent.db                      runs, attempts, events, artifact hashes (SQLite)
└─ runs/<run_id>/
   ├─ manifest.json              what was asked: repo, base commit, test command
   ├─ events.jsonl               everything that happened, in order
   └─ attempts/001/
      ├─ worktree/               the repository at the base commit
      └─ artifacts/              one or more files per completed stage
```

To see where an attempt stands without the database, list `attempts/001/artifacts/`: the files present tell you which stages finished (see the "Writes" column above). The last line of `events.jsonl` tells you how the latest stage ended.

The `.md` artifacts (`evidence.md`, `diagnosis.md`) are for people. The `.json` artifacts are what the next stage reads, and each is checked against its recorded hash before use, so editing one by hand makes the next stage refuse it.

## Human checkpoints

Read each stage's output before running the next; every contract says what to look for. Today nothing forces that pause: the stages will run back to back if asked. The enforced human decision is stage 07 (review), which is not built yet.

## Where the rules are

Rules shared by every stage are in `workflow/_shared/`. Load one only when the stage contract points to it.
