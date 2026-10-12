# 05_propose, accept a patch only if policy allows it

One job: take a provider's proposed patch and record it only if it passes patch policy. Nothing is applied in this stage.

Command: `fta attempt propose RUN_ID ATTEMPT_ID --provider fake --input FILE`

## Inputs

- Working: `artifacts/diagnosis.json`, `artifacts/evidence.json`, `artifacts/baseline-target.json`, `artifacts/baseline-regression.json` (all hash re-checked); the provider's raw text; the worktree, read only.
- Reference: `references/patch-policy.md` (the rules and their error codes); `../_shared/trust-boundaries.md`.

Needs: attempt `diagnosed`, with a diagnosis that names a root cause (an abstention has nothing to patch); worktree clean and at the base commit.

Do NOT load: Git as a judge. The diff is parsed and checked in Python before Git sees it.

## Process

1. Ask the provider for raw text. The `fake` provider returns the contents of `--input`, replacing `{{diagnosis_sha256}}` with this attempt's diagnosis hash. The event records the hash of the input file and which placeholders were filled.
2. Save the raw text as received.
3. Parse the proposal, then parse its diff into a list of changed files.
4. Check the proposal and every file against policy.
5. If anything fails, fail the attempt: `invalid_proposal` when the text is not a readable proposal, `policy_violation` otherwise.
6. Note, as warnings, changed files that are not in the evidence bundle.

## Outputs

- `artifacts/patch.raw.txt`: always written.
- `artifacts/patch.json` and `artifacts/patch.md`: only for an accepted proposal.
- Attempt `proposed`, or `failed`. The worktree is unchanged either way.

## Human check

Open `patch.md` before applying. Policy confirms the patch is small, touches only allowed source files, and leaves tests and configuration alone. It does not confirm the change is correct. Read the diff, the stated risks and any warnings, and ask whether it fixes the diagnosed cause or only makes the test pass.

## Code, tests and examples

`src/failing_test_agent/stages/propose.py`, `stages/patch_policy.py`, `stages/providers.py` · `tests/test_patch.py` · canned input: `fixtures/patches/broken_calculator.json`
