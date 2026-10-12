# docs, background and direction

One job: explain why Validryn is shaped the way it is and where it is going. Nothing here is needed to run or change a built stage.

| File | Holds | Read when |
|---|---|---|
| `architecture.md` | The whole design: knowledge plane, workflow plane, control plane, the learning loop, and the build gates. | deciding what to build next |
| `knowledge-plane.md` | How evidence is found: exact retrieval today, and the Cerebras-style design (connectors, several search methods, fusion, reranking, project scopes) it grows into, with a gate for each level. | changing retrieval, or adding a source or search method |

## Sources the design draws on

- Interpretable Context Methodology: Van Clief and McDermott, arXiv:2603.16021. The origin of the `workflow/` layout and the per-folder `CONTEXT.md` files.
- "How Cerebras Built Its Enterprise Knowledge Base": https://www.cerebras.ai/blog/how-we-built-our-knowledge-base. Summarised and mapped onto Validryn in `knowledge-plane.md`. The article itself is not copied into this repository.

## Note

`architecture.md` shows a proposed folder tree that differs from the one actually used (for example `workflows/failing-test/stages/`). The real layout is described in the root `CLAUDE.md`.
