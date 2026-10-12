# Patch policy

What a patch proposal must look like and what gets it rejected. The enforcing code is `validate_proposal` in `src/failing_test_agent/stages/propose.py` and `check_diff` in `stages/patch_policy.py`; if this page and that code disagree, the code is what runs. A test checks that every error code in the code appears on this page.

Version 1 allows one thing: small modifications to existing text files under `src/`. Each restriction below is meant to be loosened one at a time, with its own test.

## Shape

```json
{
  "schema_version": 1,
  "diagnosis_sha256": "sha256:…",
  "addresses_claim_id": "c2",
  "rationale": "…",
  "diff": "diff --git a/src/pkg/mod.py b/src/pkg/mod.py\n--- a/src/pkg/mod.py\n+++ b/src/pkg/mod.py\n@@ …",
  "expected_effects": ["tests/test_mod.py::test_it"],
  "risks": ["…"]
}
```

The diff is a unified diff with `a/` and `b/` path prefixes and LF line endings.

## Proposal checks

| Code | Rejected when |
|---|---|
| `output_too_large` | the raw text exceeds 400,000 bytes |
| `invalid_json` | the text is not JSON |
| `schema_violation` | a field is missing, mistyped, or not in the schema |
| `diagnosis_hash_mismatch` | the patch names a different diagnosis |
| `claim_mismatch` | `addresses_claim_id` is not the diagnosis's root cause |
| `unknown_expected_effect` | an expected effect is not a test seen in the baseline |
| `text_too_long` | rationale, a risk or an effect exceeds 2,000 characters, or a list has more than 20 entries |

The first three give the failure reason `invalid_proposal`; everything else on this page gives `policy_violation`.

## Diff checks

| Code | Rejected when |
|---|---|
| `crlf_diff` | the diff contains CR characters |
| `diff_too_large` | the diff exceeds 100,000 bytes |
| `empty_diff` | the diff changes no files |
| `malformed_diff` | the diff cannot be parsed, a hunk does not match its header, or a path lacks its `a/` or `b/` prefix |
| `unsafe_path` | a path is quoted, or has backslashes or control characters |
| `absolute_path` | a path is absolute or has a drive letter |
| `path_traversal` | a path contains `..` |
| `git_path` | a path is inside `.git` |
| `path_mismatch` | the `a/` and `b/` sides name different files |
| `test_edit` | the file is under `tests/` or `test/`, is `conftest.py`, `pytest.ini`, `test_*.py` or `*_test.py` |
| `config_edit` | the file is `pyproject.toml`, `setup.cfg`, `setup.py`, `tox.ini`, `noxfile.py`, `Pipfile`, `requirements*.txt`, a `*.lock` file, or under `.github/` |
| `path_not_allowed` | the file is not under `src/` |
| `new_file` | the diff creates a file |
| `deleted_file` | the diff deletes a file |
| `rename` | the diff renames or copies a file |
| `mode_change` | the diff changes a file mode |
| `binary_patch` | the diff is a binary patch |
| `symlink` | the diff creates or changes a symbolic link |
| `submodule` | the diff changes a submodule |
| `duplicate_file` | the same file appears twice |
| `target_missing` | the file does not exist at the base commit |
| `target_is_link` | the file is a symbolic link at the base commit, or is reached through a link or junction in the worktree |
| `too_many_files` | more than 5 files |
| `too_many_changed_lines` | more than 200 added plus removed lines |

Why tests and configuration are off limits: the cheapest way for a patch to make a failing test pass is to weaken the test or change how it runs.

## Warnings

A changed file that does not appear in the evidence bundle is recorded as a warning, not a rejection. A reviewer should ask why the patch reaches code the diagnosis never looked at.

All problems found are reported together, not just the first.
