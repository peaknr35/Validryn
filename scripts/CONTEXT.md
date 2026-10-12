# scripts, setup helpers

One job: one-off commands a person runs while setting up. Nothing here is imported by the package.

| Script | Does |
|---|---|
| `init-fixture.ps1` | Windows. Copies `fixtures/broken_calculator` to `.fta/fixtures/broken_calculator` and makes the copy a Git repository with one commit. Safe to run twice. |
| `init-fixture.sh` | The same for bash. |

After running one, the copy can be used with `fta run create --repo .\.fta\fixtures\broken_calculator ...`. Full walkthrough: `README.md`, "Getting Started".
