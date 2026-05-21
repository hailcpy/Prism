# ADR-0003: Redis Streams as event bus

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

"Event-based architecture" is an in-scope bonus. The pattern we want: ingestion API publishes one event, multiple independent consumers (log-writer, metrics-roller, future PII scanner, future cost calculator) subscribe. We need:

- Consumer groups (so each consumer type makes independent progress).
- At-least-once delivery with retry on failure.
- Easy local dev — one container, no ZooKeeper, no Schema Registry.
- An interface clean enough to swap to Kafka later if we outgrow it.

Options:
1. **Redis Streams** — built into Redis (we already need Redis for cache/sessions). Consumer groups, `XACK`/`XCLAIM`, dead-letter via separate streams.
2. **Kafka via Redpanda** — single-binary Kafka-compatible broker. More "real" but heavier.
3. **No bus — sync HTTP only** — simpler, but loses the event-driven bonus and tightly couples ingestion latency to DB write latency.

## Decision

Redis Streams. Wrap producer + consumer behind a `Bus` interface so swapping to Kafka is mechanical.

## Consequences

- **+** One container, one dependency, trivial Compose.
- **+** Consumer groups give us the multi-subscriber story the bonus is asking for.
- **+** `XACK` + `XCLAIM` + dead-letter stream is enough for correctness at demo scale.
- **+** `Bus` interface keeps the door open for Kafka without code-wide changes.
- **−** Redis is a SPOF unless clustered. Acceptable for demo; production would either cluster Redis or migrate to Kafka.
- **−** Redis Streams' partitioning story is weaker than Kafka's. If we ever need ordered per-key processing across many shards, we'll have to migrate.
