# fixtures, sample inputs

One job: small, known inputs for tests and for trying the pipeline by hand.

| Path | Holds |
|---|---|
| `broken_calculator/` | A tiny Python project whose `divide` multiplies. `tests/test_calculator.py::test_divide` fails. Plain files, deliberately not a Git repository. |
| `diagnoses/broken_calculator.json` | A valid canned diagnosis for that project, for the fake provider. Uses `{{evidence_bundle_sha256}}` where the bundle hash belongs. |

## Using them

`broken_calculator/` cannot be passed to `fta run create` directly, because it is not its own repository and a nested one must not be committed here. `scripts/init-fixture.ps1` copies it to `.fta/fixtures/broken_calculator` and initialises Git there.

## Adding a fixture

- A new sample project goes in its own folder beside `broken_calculator/`.
- Canned provider output goes in a folder named for the stage that consumes it (`diagnoses/`, later `patches/`), named after the project it belongs to.
- Do not fix the bug in `broken_calculator/`. Tests depend on it failing.
