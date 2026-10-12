# The knowledge plane

How Validryn finds the evidence a stage works from: what exists today, and the design it grows into. The design is adapted from "How Cerebras Built Its Enterprise Knowledge Base" (https://www.cerebras.ai/blog/how-we-built-our-knowledge-base). This page summarises the ideas Validryn takes from it; read the article for their system.

Nothing on this page beyond "Today" is built. Each later level has a gate that must be met first.

## The role of this plane

Validryn has three planes. The workflow plane (`workflow/`) says what each stage does. The control plane (`src/failing_test_agent/`) enforces it. The knowledge plane answers one question for a stage: what should it be looking at?

Its output is always the same thing: an evidence bundle. Whatever finds the evidence, the next stage receives items with an ID, a source, a location, exact text and a hash, and can cite nothing else. That contract is already in place (`EvidenceBundle` in `core/models.py`), so better retrieval can be added later without changing diagnosis, patching or review.

## Today: exact retrieval

Stage 03 (`workflow/03_retrieve`) reads one source, the attempt's worktree, and uses no search at all. It follows the failure: the failing test, the traceback frames inside the repository, and the definitions of the local symbols the test uses. It is deterministic, bounded, and never leaves the worktree.

This is enough when the cause is in code the failing test touches. It will miss causes that sit elsewhere: configuration, a distant caller, a past decision recorded only in a discussion.

## Ideas taken from the Cerebras design

**Meet data where it lives.** Knowledge stays in the tools that produce it: code in repositories, discussion in chat, decisions in documents. Each source gets a connector that reads it in place. Nothing is migrated into one system.

**One table, one shape.** Every connector emits rows of the same shape into one store: the text, where it came from, when, and metadata. Anything in the store is queryable the same way. For Validryn the equivalent is that every source ends up as evidence items of one schema.

**No single way of searching is trusted.** Different methods fail differently, so several run side by side:

| Method | Finds | Misses |
|---|---|---|
| Exact / full-text | literal error strings, flag names, identifiers | anything worded differently |
| Embedding similarity | the same idea in different words | exact tokens, which it blurs |
| Term rarity | short items built around a rare term | nothing by itself; it is a weight |
| Recency | the newer of two otherwise equal answers | nothing by itself; it is a weight |

**Fuse, then rerank, then expand.** Each method returns its own ranked list. The lists are combined by rank, so an item that several methods agree on beats one that a single method ranks first. Duplicates are merged back to their source and each source is capped. A small model then scores the shortlist against the actual question. Only after that is surrounding context added back to the winners.

**Normalise before embedding.** A raw conversation is a poor thing to embed. It is first distilled into a consistent record (the question, a summary, the resolution, the systems and code it mentions), and that record is embedded. Important single messages inside long threads are also indexed on their own, with the thread topic attached, if they pass a quality threshold.

**Code is chunked along its structure.** Split at classes first, then methods, then smaller blocks, so each chunk is a meaningful unit. Re-index only what a commit changed.

**Retrieval tools stay simple.** Each tool does one kind of search and returns raw evidence rows, with no model inside. Whatever is orchestrating (a planner, or an agent) decides which tools to call and assembles the answer.

**Scope by project.** Searching everything stops being useful as the corpus grows. A project is a named set of sources; a query runs inside one by default. A source can belong to several projects without being copied.

## How those ideas map onto Validryn

| Cerebras concept | Validryn form | State |
|---|---|---|
| Evidence rows in a common shape | `EvidenceItem`, cited by ID | built |
| Retrieval primitive with no model inside | stage 03, strategy `exact-v1` | built |
| Exact search over code | evidence kind `exact_match` (ripgrep) | reserved, not produced |
| Connector per source | one module per source emitting evidence items | not built |
| Full-text index | SQLite FTS5 over finished episodes and chosen documents | not built |
| Embeddings, fusion, reranking | a later retrieval strategy alongside `exact-v1` | not built |
| Planner choosing tools | a query-plan step before retrieval | not built |
| Projects | a named source set recorded on the run | not built |

## What Validryn adds that a knowledge base does not need

A knowledge base answers questions. Validryn acts on what it finds, so the rules are stricter:

- **Retrieved text is data, never instructions.** A message or comment may contain text aimed at the model. It can be cited; it cannot change what a stage does (`workflow/_shared/trust-boundaries.md`).
- **Every item is pinned to a version.** Evidence from a repository carries the commit; evidence from any future source will need its own version or content hash, so a later reader can tell if the source has changed.
- **Access is decided before retrieval, not after.** When sources with different permissions exist, restricted content must be filtered out before it reaches a reranker or a model. A project scope improves relevance; it is not authorisation.
- **Determinism is kept where it is possible.** Exact retrieval gives the same bundle for the same inputs. Ranked retrieval will not, so its bundle must record the strategy, the queries and the index version that produced it.

## Levels and their gates

Add a level only when the one below it has a documented miss: a real failure whose cause the evidence did not contain.

| Level | Adds | Gate |
|---|---|---|
| 1 | Traceback frames, `ast` definitions, exact symbols | built |
| 2 | Ripgrep lexical search and neighbouring context | exact retrieval missed relevant code |
| 3 | Full-text index over finished episodes and chosen documents | the task loop works end to end, and recall across runs is needed |
| 4 | Embeddings and rank fusion | reworded queries regularly miss evidence that exists |
| 5 | Reranking, connectors for other sources, project scopes | several sources exist and simple retrieval is noisy |
| 6 | Query planning and parallel tool calls | several sources are shown to help on the same tasks |

Every level is measured against the one before it on the same tasks: does the bundle now contain the evidence that was needed, and did the fix rate improve, not merely the amount retrieved.

## Where memory fits

Finished attempts become episodes (stage 09, planned). Episodes are the first new source the knowledge plane will read, at level 3. A lesson drawn from episodes is a separate thing: it is proposed, tested on held-out cases and approved by a person before it may guide later work. See `architecture.md`, "Learning Loop".
