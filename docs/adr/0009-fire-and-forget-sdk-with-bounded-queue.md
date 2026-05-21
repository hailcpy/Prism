# ADR-0009: SDK emission is fire-and-forget with bounded queue

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

When the user-facing chat call returns, we have to decide how much we make them wait on the log being durably stored:

1. **Synchronous emit** — `await ingestion.post(event)` before returning to caller. Pros: never lose a log. Cons: ingestion latency + failures directly degrade user experience. If ingestion is down, chat is down.
2. **Fire-and-forget with bounded queue** — push event onto an in-memory queue, return immediately, background thread flushes. Pros: user latency decoupled. Cons: events can be lost on process crash, queue can overflow under sustained outage.
3. **Local durable queue (SQLite/disk)** — like (2) but survives crashes. Pros: best of both. Cons: complicates the SDK, adds file I/O, requires cleanup logic.

Logs are observability data, not source of truth. The user's chat experience is the product; the log is a derivative. Sacrificing the product for the derivative is the wrong tradeoff.

## Decision

Fire-and-forget with a bounded in-memory queue (default `queue_max=10_000`). Background flusher thread POSTs batches to ingestion every `flush_interval_ms` (default 200ms) or when batch hits 100 events.

On queue overflow: drop oldest, increment a `dropped_events` counter, log a warning. The `on_drop="raise"` option is available but defaults to `"log"`.

Graceful shutdown drains the queue via `client.close()`, also registered through `atexit`.

We explicitly choose **not** to implement local durable queueing in v1.

## Consequences

- **+** User-facing chat latency is decoupled from ingestion latency.
- **+** Ingestion downtime degrades observability gracefully; chat keeps working.
- **+** Simple SDK code; no file system dependencies.
- **−** Crash window: events in the queue at SIGKILL are lost. Quantified: at peak ~50 events/second, the worst case loss is ~10 events (200ms flush interval). Acceptable for observability.
- **−** Sustained ingestion outage (> queue capacity) drops oldest events. Surfaced as a metric; ops can scale up `queue_max` or trigger investigation.
- **−** A future v2 with disk-backed queue is non-trivial work. The interface (`sink="http" | "noop" | "stdout"`) is extensible enough to add `sink="disk"` later.
