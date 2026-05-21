# ADR-0005: Storage and Bus are interfaces from day one

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

ADR-0002 commits us to a single Postgres now but a clean migration to PG + ClickHouse + S3 later. ADR-0003 commits us to Redis Streams now but a clean swap to Kafka if needed. Both promises only hold if callers don't reach into the implementation.

The standard mistake: write `psycopg.execute("INSERT INTO inference_logs ...")` directly in the worker, then need to rewrite every call site at migration time.

## Decision

Three interfaces, defined in v1, with one implementation each today:

1. **`LogStore`** — read and write `inference_logs` and `metrics_minute`.
   - `write_logs_batch(events: list[InferenceEvent]) -> None`
   - `upsert_metrics(rows: list[MetricsRow]) -> None`
   - `get_metrics(query) -> list[MetricsRow]`
   - `get_logs(query) -> list[InferenceLog]`
   - Today: `PostgresLogStore`. Future: `ClickHouseLogStore`.

2. **`RawPayloadStore`** — store/fetch full request+response payloads.
   - `put(inference_id, payload) -> uri | None`
   - `get(uri_or_inference_id) -> payload`
   - Today: `JsonbRawPayloadStore` (writes to `raw_payload_jsonb`, returns `None`). Future: `S3RawPayloadStore`.

3. **`Bus`** — publish/subscribe.
   - `publish(stream, event) -> stream_id`
   - `consume(stream, group, consumer) -> iter[event]`
   - `ack(stream, group, id) -> None`
   - Today: `RedisStreamsBus`. Future: `KafkaBus`.

No code in services/, workers/, or sdk/ touches `psycopg`, `redis-py`, or `boto3` directly. Only the implementations in `infra/storage/` and `infra/bus/` do.

## Consequences

- **+** Migration to CH/S3/Kafka is a DI wiring change in one place.
- **+** Tests can use `InMemoryLogStore` / `InMemoryBus` impls — no Postgres or Redis required for unit tests.
- **+** Forces clean separation between "what we want" (interfaces) and "how we do it" (impls).
- **−** Small upfront tax: defining the interface and the impl instead of just writing the SQL.
- **−** Risk of the interface being shaped wrong for ClickHouse (e.g. row-by-row writes that batch poorly). Mitigation: `write_logs_batch` is batch-first by design, not a `write_one` method.
