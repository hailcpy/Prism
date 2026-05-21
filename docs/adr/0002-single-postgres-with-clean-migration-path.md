# ADR-0002: Single Postgres now, structured for PG + ClickHouse + S3 later

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

Inference logs are append-only, high-volume, query-aggregation-heavy. The textbook answer is a columnar OLAP store (ClickHouse / BigQuery) with raw payloads in object storage and OLTP data in Postgres. The takehome rubric explicitly grades on schema design and tradeoffs.

Doing the full PG + ClickHouse + S3 split today would:
- Triple infra surface (three stores, batched-insert worker, S3 client).
- Increase the chance of half-finished pieces in a takehome window.
- Be hard to demo reliably without a long warmup.

Doing only Postgres today, with no thought to the future, would:
- Force a real refactor when the migration eventually happens (constraints, joins, callers all touching the wrong abstractions).
- Fail to demonstrate the judgment the rubric is asking about.

## Decision

Ship a single Postgres for demo. **Engineer it so the PG + ClickHouse + S3 split is a drop-in later**, with these specific constraints:

1. All log-table I/O goes through `LogStore` / `RawPayloadStore` interfaces (see ADR-0005). No callers touch SQL directly.
2. `inference_logs.raw_payload_uri` column exists from day one alongside `raw_payload_jsonb`. Today, writes use jsonb and `uri` is NULL; tomorrow writes use S3 and jsonb is NULL. Readers check `if uri: fetch_from(uri) else: read_jsonb` from day one.
3. No DB-level foreign keys between app-data tables and log/rollup tables (see ADR-0008).
4. No SQL joins between groups. Cross-group correlation is done in app code (or skipped entirely).

## Consequences

- **+** Single PG keeps demo simple, fast to bring up, deterministic.
- **+** Migration to CH+S3 is a DI change + a backfill job, not a refactor of callers.
- **+** Demonstrates the actual scoring axis (judgment about future scale) without paying for it today.
- **−** A small amount of "useless" code lives in v1 (the `uri` column is always NULL, the `RawPayloadStore` abstraction wraps a trivial jsonb write).
- **−** Reviewer might mistake the over-abstraction for premature engineering; the README must explain why explicitly.
