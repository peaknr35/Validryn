# 04_diagnose, accept a diagnosis only if it holds up

One job: take a provider's proposed explanation of the failure and record it only if it passes every deterministic check.

Command: `fta attempt diagnose RUN_ID ATTEMPT_ID --provider fake --input FILE`

## Inputs

- Working: `artifacts/evidence.json` (hash re-checked); the provider's raw text.
- Reference: `references/diagnosis-rules.md` (the checks and their error codes); `../_shared/trust-boundaries.md`.

Needs: attempt `retrieved`.

Do NOT load: the worktree or the baseline results. A diagnosis may rely only on the evidence bundle, which is why every claim must cite evidence IDs.

## Process

1. Ask the provider for raw text. Today the only provider is `fake`, which returns the contents of `--input`. In that file, `{{evidence_bundle_sha256}}` is replaced with this attempt's bundle hash.
2. Save the raw text as received, before judging it.
3. Parse and validate it against the rules.
4. If any rule fails, fail the attempt with reason `invalid_diagnosis` and the list of error codes.
5. Otherwise write the canonical and readable versions.

## Outputs

- `artifacts/diagnosis.raw.txt`: always written.
- `artifacts/diagnosis.json` and `artifacts/diagnosis.md`: only for a valid diagnosis.
- Attempt `diagnosed`, or `failed`.

## Human check

Open `diagnosis.md`. The checks confirm that citations and quotes are real; they do not confirm the reasoning is right. Judge whether the root-cause claim actually follows from the quoted evidence, and whether an abstention would have been more honest.

## Code, tests and examples

`src/failing_test_agent/stages/diagnose.py` · `tests/test_diagnosis.py` · canned input: `fixtures/diagnoses/broken_calculator.json`
