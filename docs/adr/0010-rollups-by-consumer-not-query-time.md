# ADR-0010: Dashboard reads pre-aggregated rollups; reconciler is the source of truth

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

The dashboard shows latency p50/p95, throughput, error rate, and token usage per model — over a time range, refreshed periodically. Two basic ways to serve this:

1. **Query-time aggregation.** `GET /v1/metrics` runs `SELECT … GROUP BY date_trunc('minute', …), model` over `inference_logs`. Simple, no extra moving parts. Expensive at scale (especially percentiles), worse as the log table grows.
2. **Pre-aggregated rollups.** A consumer maintains a small denormalized `metrics_minute` table. The dashboard queries the rollup.

Pre-aggregation also reinforces the event-driven story (ADR-0003): one bus, multiple independent consumers. So we want pre-aggregation. **The hard question is making the rollup *correct under replay*.**

Naive approach (rejected): for each event, `UPDATE metrics_minute SET count = count + 1, …`. This is fast but **not idempotent**: a `XCLAIM`'d batch, a worker crash before `XACK`, or a manual stream replay all double-count.

A common "fix" (also rejected): UPSERT with `ON CONFLICT … DO UPDATE SET count = count + EXCLUDED.count`. Same problem in different syntax — incrementing a counter is not idempotent regardless of where the increment lives.

To actually be replay-safe, the rollup row must be **replaceable from a deterministic source** — either deterministic in-memory aggregation followed by a REPLACE write, or recomputation from the canonical log table.

## Decision

A two-track design:

### Track 1 — `metrics-roller` worker (hot path, best-effort)

- Consumer group `cg-roller` on `inference.logged`.
- Maintains in-memory tumbling 60s windows keyed by `(minute_bucket, model, provider)`.
- Events stay **un-XACKed** until their window closes (clock passes `bucket + 60s + grace`).
- On window close, the worker computes the full rollup row for each `(model, provider)` in that bucket **from the in-memory accumulator** and writes:
  ```sql
  INSERT INTO metrics_minute (minute_bucket, model, provider, count, error_count, ...)
  VALUES (...)
  ON CONFLICT (minute_bucket, model, provider) DO UPDATE SET
    count = EXCLUDED.count,                       -- REPLACE, not +=
    error_count = EXCLUDED.error_count,
    latency_p50_ms = EXCLUDED.latency_p50_ms,
    latency_p95_ms = EXCLUDED.latency_p95_ms,
    prompt_tokens_sum = EXCLUDED.prompt_tokens_sum,
    completion_tokens_sum = EXCLUDED.completion_tokens_sum;
  ```
- Then `XACK`s every event that fed that window.
- If the worker crashes mid-window, all events in the open window are re-delivered. The worker rebuilds the accumulator from re-delivered events and writes the same REPLACE row at close. Result is identical regardless of crash count.

### Track 2 — `metrics-reconciler` (canonical, runs every 5 min)

A lightweight cron that recomputes the last 15 minutes of buckets directly from `inference_logs` and REPLACE-UPSERTs into `metrics_minute`:

```sql
INSERT INTO metrics_minute (...)
SELECT date_trunc('minute', created_at), model, provider,
       count(*), count(*) FILTER (WHERE status <> 'ok'),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms),
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms),
       sum(prompt_tokens), sum(completion_tokens)
FROM inference_logs
WHERE created_at >= now() - interval '15 minutes'
GROUP BY 1, 2, 3
ON CONFLICT (minute_bucket, model, provider) DO UPDATE SET ... ;  -- REPLACE
```

This is the **canonical** rollup. `inference_logs` is itself dedupe-safe (the log-writer uses `INSERT … ON CONFLICT (id, created_at) DO NOTHING`), so the reconciler's output is a pure function of unique inserted events. Running it twice, ten times, or after any kind of replay produces the same row.

### Late events

Events that arrive after their window was closed by Track 1 bypass the in-memory aggregator and trigger a reconciler run for the affected bucket. Track 2 also catches them on its next scheduled run regardless.

## Consequences

- **+** Dashboard queries hit a tiny denormalized table; performance is constant regardless of log volume.
- **+** **True idempotence**: Track 1 is idempotent because REPLACE from a deterministic in-memory aggregator yields the same row across crashes. Track 2 is idempotent because it's a pure function of the dedupe-safe log table. There is no path where replay double-counts.
- **+** Track 2 is also the recovery procedure for any roller bug: stop the roller, run the reconciler over the affected range, restart.
- **+** Multi-consumer event-driven story stays clean: roller and reconciler both consume from the same source of truth.
- **−** Two paths to maintain. Mitigation: Track 2 is a single SQL statement in a cron container; minimal code surface.
- **−** Dashboard numbers can lag by ≤65 seconds (window + grace). Acceptable for an ops dashboard.
- **−** In-memory aggregator state is lost on roller restart; events for currently-open windows are re-delivered (because un-XACKed), which is fine. Closed-but-not-yet-acked windows would be replayed too — the REPLACE UPSERT makes that safe.
- **−** Sub-minute granularity is unavailable from `metrics_minute`. Available via direct `inference_logs` query for incident response.
