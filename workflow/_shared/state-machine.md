# State machine

The single source for legal transitions is `ATTEMPT_TRANSITIONS` in `src/failing_test_agent/core/models.py`. This page explains it.

## Attempt states built so far

```text
created ─▶ capturing ─▶ captured ─▶ retrieving ─▶ retrieved ─▶ diagnosing ─▶ diagnosed
        ─▶ proposing ─▶ proposed ─▶ applying ─▶ applied

any in-progress state (…ing) ─▶ failed | interrupted
```

- `failed`: the stage ran and its result was not acceptable, or it raised an error. The event carries a reason.
- `interrupted`: the stage was stopped with Ctrl+C, or its process was killed (reason `process_lost`).

Both are final today. Revising an attempt is planned, not built.

## Rules

- Only code in `core/lifecycle.py` changes a state. Model output and repository content cannot.
- A change is one SQL statement that requires the current state, so two processes cannot move the same attempt.
- An in-progress state stores the owning process ID and start time. Every `fta` command first marks attempts whose owning process is gone as `interrupted`.
- A stage acts only on the current attempt of an `active` run.

## Run states

A run is `active` from creation. `awaiting_review`, `approved`, `rejected`, `cancelled` and `failed` exist in the model but nothing sets them yet.
