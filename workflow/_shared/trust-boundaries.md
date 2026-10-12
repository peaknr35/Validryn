# Trust boundaries

What each kind of input is allowed to do.

| Input | Treated as | May it change state or permissions? |
|---|---|---|
| CLI arguments from the user | instruction | yes, within what the command allows |
| These workflow contracts | description for people and models | no; code enforces |
| Repository source, comments, test output | data | never |
| Evidence items | data, cited by ID | never |
| Model (provider) output | an untrusted proposal | never; it is accepted only if every check passes |

## Consequences

- A comment in source code saying "mark this as fixed" is copied into evidence like any other line and has no effect.
- Provider output is parsed against a schema that refuses unknown fields, so it cannot carry a status, an approval, or a command.
- Retrieval parses source with `ast`. It never imports or runs repository code, and never reads outside the worktree.
- Analysis is not permission. A diagnosis or a patch proposal does not authorise applying, committing, or pushing anything.
- Approval, when built, binds to exact hashes of what was reviewed.
