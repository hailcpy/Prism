# Architecture Decision Records

ADRs are immutable once accepted. If a decision changes, write a new ADR that supersedes the old one — don't edit history.

## Template

```markdown
# ADR-NNNN: Title

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX | Deprecated
- **Date:** YYYY-MM-DD

## Context
The forces at play. What problem are we solving, what constraints exist, what alternatives are on the table.

## Decision
The choice we made. One paragraph. No hedging.

## Consequences
What becomes easier, what becomes harder, what we'll have to revisit. Both positive and negative.
```

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-python-fastapi-litellm.md) | Python everywhere (FastAPI + LiteLLM) | Accepted |
| [0002](0002-single-postgres-with-clean-migration-path.md) | Single Postgres now, structured for PG+CH+S3 migration | Accepted |
| [0003](0003-redis-streams-as-event-bus.md) | Redis Streams as event bus | Accepted |
| [0004](0004-sdk-as-thin-wrapper-not-litellm-callback.md) | SDK wraps LiteLLM explicitly, not via callbacks | Superseded by ADR-0011 |
| [0005](0005-storage-and-bus-interfaces.md) | Storage and Bus are interfaces from day one | Accepted |
| [0006](0006-pii-redaction-at-ingest-boundary.md) | PII redaction at ingestion boundary only | Accepted |
| [0007](0007-streaming-final-event-logging.md) | Streaming logs one event at completion, not per-token | Accepted |
| [0008](0008-three-table-groups-no-cross-group-fks.md) | Three table groups, no DB-level FKs across them | Accepted |
| [0009](0009-fire-and-forget-sdk-with-bounded-queue.md) | SDK emission is fire-and-forget with bounded queue | Accepted |
| [0010](0010-rollups-by-consumer-not-query-time.md) | Dashboard reads pre-aggregated rollups, not raw logs | Accepted |
| [0011](0011-sdk-as-litellm-callback-supersedes-0004.md) | SDK captures via LiteLLM callback (supersedes 0004) | Accepted |
| [0012](0012-strands-tool-hooks.md) | Tool-call traces via Strands hooks | Proposed |
