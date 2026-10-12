# docs, background and direction

One job: explain why Validryn is shaped the way it is. Nothing here is needed to run or change a stage.

| File | Holds |
|---|---|
| `architecture.md` | The long-term design: a knowledge plane (Cerebras-style retrieval), a workflow plane (ICM stage contracts) and a control plane (this code). Includes the build gates that decide when each further capability may be added. |

## Sources the design draws on

- Interpretable Context Methodology: Van Clief and McDermott, arXiv:2603.16021. The origin of the `workflow/` layout and the per-folder `CONTEXT.md` files.
- "How Cerebras Built Its Enterprise Knowledge Base": https://www.cerebras.ai/blog/how-we-built-our-knowledge-base. The model for retrieval beyond exact extraction, not yet built.

## Note

`architecture.md` shows a proposed folder tree that differs from the one actually used (for example `workflows/failing-test/stages/`). The real layout is described in the root `CLAUDE.md`.
