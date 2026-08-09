# ADR 0001: Foundation public contract baseline

- Status: Accepted
- Date: 2026-08-09

## Context

Foundation verification found two kinds of false regression signal: the public
signature snapshot for `Writer.run` predated an already deployed optional
parameter, and archived Writer Boundary experiments hashed the mutable live
`app/utils/llm_client.py` file instead of retaining the bytes they actually ran
against. A fresh Git worktree also demonstrated that several experiment pins
depended on untracked `outputs/` artifacts.

## Decision

`Writer.run(..., rag_metadata_provider=None)` is part of the frozen public
contract. It is an optional, read-only metadata callback with signature
`Callable[[int, int], dict | None] | None`; its default remains `None`. The
contract snapshot may add this final parameter only. The names, order,
annotations, and defaults of every other parameter remain unchanged.

Writer Boundary R3.4/R3.5/R3.6 source pins must resolve to immutable,
version-controlled fixtures rather than live application code or generated
runtime output. The pinned `llm_client.py` bytes are recovered from Git commit
`ee46ab5607e13ee62b7763b0c6c84fc936d703ab` and retain the manifest SHA-256
`2bfd086bcf00f1275accfe37b9b881fb8be64edb6279805d18678c9033a67c39`.
Existing manifest hashes are not rewritten.

Private Scene Reality task state remains outside Git. Compatibility with the
current prompt template is provided by an empty
`anti_ai_expression_constraints` fixture value, which preserves the archived
experiment's prompt semantics.

## Consequences

- Live `llm_client.py` evolution no longer invalidates historical experiments.
- The R3.4/R3.5/R3.6 tests can run in a clean worktree without pre-existing
  generated output directories.
- Future public `Writer.run` changes require an explicit ADR and a deliberate
  contract snapshot update.
- Historical source hashes remain evidence of the original bytes, not hashes of
  whatever implementation happens to be live today.
