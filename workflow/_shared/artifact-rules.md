# Artifact rules

An artifact is a file a stage writes into `attempts/<n>/artifacts/`. These rules are what make a run auditable.

- **File first, then record.** The file is written to a temporary name and renamed into place, then hashed. Its SHA-256 and an event go into SQLite in one transaction.
- **Verify before use.** A stage that reads an earlier artifact recomputes its hash and compares it with the recorded one. A mismatch stops the stage before any state changes.
- **Canonical where it is compared.** `evidence.json` and `diagnosis.json` use sorted keys, two-space indent, LF line endings and one trailing newline, and hold no timestamp, so the same inputs give the same bytes. `manifest.json` and `baseline.json` are records of one event and do carry a `created_at`.
- **Never overwritten.** A stage runs once per attempt. A different result means a new attempt.
- **Readable twin.** Where a person needs to review a JSON artifact, the stage also writes a `.md` version. The `.md` is for reading; the `.json` is what the next stage consumes.
- **Bounded.** Captured output, evidence and model output all have size limits, and say so with a `truncated` flag when a limit is hit.

Events are stored twice: in the `events` table and in `runs/<run_id>/events.jsonl`. The database is written first.

Code: `src/failing_test_agent/core/lifecycle.py` (`Recorder`), `core/atomic.py`, `core/hashing.py`.
