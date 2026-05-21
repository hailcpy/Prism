# ADR-0008: Three table groups, no DB-level FKs across them

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

We have three classes of data with very different workloads:

- **A. App data** — `conversations`, `messages`. OLTP, mutable, low volume, joined frequently.
- **B. Inference logs** — `inference_logs`. OLAP, append-only, high volume, queried analytically.
- **C. Rollups** — `metrics_minute`. Read-optimized, denormalized, written by a single consumer.

The "natural" relational design would put FKs everywhere: `messages.conversation_id → conversations.id`, `inference_logs.message_id → messages.id`, etc.

But ADR-0002 commits us to migrating B (and possibly C) to ClickHouse. ClickHouse doesn't enforce cross-engine FKs. If we put a hard FK from `inference_logs` to `messages` today, we'll have to drop it at migration time — and any code that *relied* on cascade-delete or join semantics will silently break.

## Decision

- **Within a group**, normal FKs and constraints are fine. (e.g. `messages.conversation_id → conversations.id ON DELETE CASCADE`.)
- **Across groups**, links are *soft*: a column of the right type with no constraint. (e.g. `inference_logs.message_id UUID` — no `REFERENCES messages(id)`.)
- **No SQL joins across groups.** Any cross-group correlation happens in application code, which can be moved trivially when the table moves.

## Consequences

- **+** Any group can migrate to a different store without unwinding constraints first.
- **+** Inference logs can outlive their messages (audit-friendly).
- **+** Deletes in app data don't cascade into immutable observability data — which is the right semantic anyway.
- **−** Lose referential integrity guarantees across groups. Orphan rows possible. We accept this as the price of migration-readiness.
- **−** Developers must remember not to write cross-group joins. Mitigated by the `LogStore` interface forcing log access through code paths separate from app-data queries.
