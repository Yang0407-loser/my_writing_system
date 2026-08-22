# ADR 0002: Canonical database and transaction boundary

- Status: Accepted for Foundation implementation
- Date: 2026-08-09

## Context

The legacy writing path distributes mutable state across Redis hashes and
checkpoints, SQLite task history, Chroma, Markdown, and in-process WorldState and
EventGraph objects. None of those stores can atomically define that a document
revision and its project state transition both happened. Foundation requires one
authoritative transaction before projections or client work can safely build on
top.

## Decision

We use SQLAlchemy 2.x synchronous sessions and explicit transaction scopes.
PostgreSQL is the production authority. SQLite is a deliberately limited unit
and migration adapter; passing SQLite tests is not evidence that PostgreSQL
locking or concurrency semantics passed. Alembic is the only schema-migration
mechanism. Runtime code must not call `create_all`, issue ad-hoc `CREATE` or
`ALTER`, or migrate an existing database on application startup.

The canonical transaction may read and write only the relational database. It
must not call Redis, Chroma, an LLM, the filesystem, Markdown export, or legacy
WorldState/EventGraph APIs. One transaction verifies both explicit Heads,
reserves idempotency, writes the immutable revision and complete project-state
version, appends ledger entries, moves both Heads, records the result, and inserts
the outbox projection manifest. Projection happens only after SQL commit.

Foundation v0 uses JSON columns for opaque state snapshots, ledger payloads,
commit results and projection payloads. JSON is an intentional boundary: the v0
compiler preserves unknown fields and does not prematurely create narrative
ontology tables. Domain normalization can evolve behind the immutable Candidate,
StateTransition and Commit contracts.

All canonical repository operations are tenant- and project-scoped. This is an
application query invariant and audit boundary, not a claim of complete tenant
security. Database roles, row-level security, key management, retention and the
broader security platform remain P3B work.

Configuration fails closed outside tests when `CANONICAL_DATABASE_URL` is
missing or when `CANONICAL_COMMIT_MODE` is not one of `legacy`, `canary`, or
`internal_required`. Tests may explicitly select isolated temporary SQLite; the
production default is never an implicit local database.

## Consequences

- Canonical repositories never call `session.commit()`; the service-owned unit
  of work controls the transaction.
- PostgreSQL integration remains a mandatory P1/P2 gate even when SQLite unit
  tests pass.
- Projection lag is explicit and recoverable from outbox rows.
- Existing stores become compatibility projections and may temporarily lag, but
  cannot redefine whether a commit exists.
- Schema changes require reviewed Alembic revisions with upgrade and downgrade
  verification against dedicated test databases.

## Version and operational references

- [SQLAlchemy 2.0 documentation](https://docs.sqlalchemy.org/en/20/intro.html)
- [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/index.html)
- [Psycopg 3 installation](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [uv project locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
