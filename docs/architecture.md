# Architecture Notes

Operator-facing notes that go deeper than the top-level [`README.md`](../README.md). Four sections: ingestion flow, logging strategy, scaling considerations, failure handling assumptions. The README is the entry point; this file is the only doc under `docs/`.

---

## 1. Ingestion flow

The hot path of an LLM call:

```
Chatbot UI ──SSE──► Chatbot API ──LiteLLM──► Provider
                         │
                         │  (prism-sdk, in-process)
                         ▼
                    bounded queue
                         │
                         │  fire-and-forget HTTP, batched
                         ▼
                  Ingestion API  ──► PII redact ──► validate ──► XADD
                                                                   │
                                                  inference.logged │ (Redis Stream)
                                                                   │
                                                                   ▼
                                                             log-writer (cg-writer)
                                                             batch 1000 or 5s
                                                             INSERT ... ON CONFLICT DO NOTHING
                                                                   │
                                                                   ▼
                                                             inference_logs (TimescaleDB hypertable)
                                                                   │
                                                                   ▼
                                                             metrics_minute (continuous aggregate)
                                                             auto-refreshed every 5 min
                                                             real-time merge for recent data
```

Key properties:

1. **Caller never blocks on logging.** The chatbot API returns the SSE stream regardless of what the SDK queue or ingestion is doing.
2. **Trust boundary is the ingestion API.** PII redaction (regex over `prompt_preview`, `response_preview`, and every string inside `raw_payload`) and Pydantic validation happen *before* `XADD`. The bus and downstream workers never see unredacted text.
3. **Mixed-validity batches always return `202`** with `accepted` count and a `rejected: [{index, reason}]` array. Only fully malformed bodies return `4xx`.
4. **One consumer group, one stream.** `cg-writer` consumes events and inserts into the hypertable. Metrics aggregation is handled by TimescaleDB continuous aggregates — no separate consumer needed. Adding a new consumer (cost, deep PII scan, eval sampler) is a deployment change, not a code change to the writer.
5. **Continuous aggregate is the source of truth for rollups.** TimescaleDB materializes additive metrics (counts, sums, costs) per minute bucket and auto-refreshes every 5 minutes. With `materialized_only = false`, queries transparently merge materialized data with recent unmaterialized rows for real-time results.
6. **Percentile metrics bypass the aggregate.** `percentile_cont()` is not partializable, so percentile queries (latency p50/p95, TTFT p50/p95) run directly against the raw hypertable. TimescaleDB chunk exclusion keeps these fast.

### Ingestion HTTP contract (summary)

Full schema in code; the contract a reader needs to know:

- **Endpoint:** `POST /v1/events:batch`, body `{events: InferenceEvent[]}`. Soft limit 100/req, hard 500.
- **Event identity:** caller-generated `id` (uuid7 recommended). `inference_logs` PK is `(id, created_at)` with `ON CONFLICT DO NOTHING`, so duplicate IDs are idempotent — safe for SDK replay after timeouts.
- **Required fields:** `id`, `ts_start`, `ts_end`, `model`, `provider`, `status`, `latency_ms`, `schema_version`. Everything else is optional.
- **Status taxonomy:** `ok | error | timeout | cancelled`.
- **Responses:** `202` with `{accepted: int, rejected: [{index, reason}], stream_ids: [...]}` on partial validity. `400` only for un-parseable bodies. `503` when Redis is unreachable — SDK retries with backoff.
- **Retry after partial accept:** the SDK retries only the events the server `rejected` (it has the indexes). Already-accepted events are not re-sent; if they are, the DB dedupes on `(id, created_at)`.

### Operational defaults

| Knob | Default | Range we've validated |
|---|---|---|
| Redis stream cap | `MAXLEN ~ 1_000_000` on `XADD` | OK to 5M on a single broker with default `maxmemory` |
| Writer batch | 1000 events or 5s | 100–5000 / 1–30s |
| Hypertable chunk interval | 1 day | 12h–7d depending on row volume and available memory |
| CAGG refresh interval | 5 min, covers last 15 min | refresh 1–15 min; longer = more catch-up after outage |
| CAGG end offset | 1 min | 30s–5 min; shorter = fresher materialized data |
| Compression policy | 7 days | 1–30d; compressed chunks are read-only |
| Retention policy | 30 days | 7–90d |
| `XCLAIM` visibility timeout | 30s | 10–120s |
| Writer DLQ threshold | 5 retries → `inference.dead` | 3–10 |
| SDK `flush_interval_ms` | 200ms | 50–1000ms (smaller = lower loss on `kill -9`, more HTTP overhead) |
| SDK queue cap | 10 000 events | 1k–100k

---

## 2. Logging strategy

What we log, where, and why exactly that.

- **One log event per LLM call.** Streamed calls emit a single event at stream completion, carrying `ts_start`, `ts_end`, `ttft_ms`, `latency_ms`, token counts, and final status (`ok` / `error` / `timeout` / `cancelled`). What this gives up, explicitly: mid-stream stall detection, inter-token throughput curves, partial-output debugging on `cancelled` streams, and detecting provider-side degradation that recovers before completion. We accept that loss because per-token capture would 100× event volume, and TTFT + total latency cover the perceived-UX signal. Per-token capture is a *new* event type and table (see future improvements), not an extension of `inference_logs`.
- **Three timestamps on every row:** `ts_start`/`ts_end` are SDK-observed (authoritative for latency); `created_at` is set by the ingestion API (authoritative for chunking and dashboard time-range queries — no client clock-skew surprises). Tradeoff: during an SDK-retry or worker backlog, an event lands in a *later* `created_at` chunk than when the call actually happened, so the dashboard's per-minute view is skewed during incidents. For incident forensics, query `inference_logs` by `ts_start` directly; the rollup is the operational signal, not the audit trail.
- **Soft FK to messages.** `inference_logs.message_id` links to the assistant message, but is not a DB constraint. Deleting a conversation does not nuke audit history; observability outlives app data on purpose. The cost is explicit: orphan log rows after deletion, no DB-level cascade, and GDPR / CCPA "right to erasure" requests must run as an application-level job that scans logs by `conversation_id` / `message_id` (and `metadata_jsonb`) — not a `DELETE CASCADE`. We accept this for audit fidelity; production deployments would add a `purge-by-subject` worker.
- **Redaction at ingest, not at the SDK.** The SDK is dumb — it captures and ships. Centralizing redaction at the trust boundary means there is exactly one place to audit, and the redactor implementation can be swapped (regex → Presidio → model-based) behind the `Redactor` interface without touching every caller.
- **Raw payloads — exact contract.** Default is `PRISM_KEEP_RAW=false`: ingestion redacts previews, then drops `raw_payload` entirely before publish. `raw_payload_jsonb` stays `NULL`. With `PRISM_KEEP_RAW=true` (debug only, loud startup warning), the redacted payload is published and persisted to `raw_payload_jsonb`. Readers always check `if raw_payload_uri: fetch_from(uri) else: read_jsonb` — so flipping writes to S3 later is a write-path change with zero reader churn. Retention follows the hypertable retention policy (default 30d).
- **Pre-trust-boundary surfaces still see raw text.** Redaction is at ingest, which means the SDK process, the HTTP request body, the ingestion handler's request-scope memory, and any crash dump from either process can contain unredacted prompts/responses. Mitigations the operator owns: TLS between SDK and ingestion (compose binds to `127.0.0.1` for the demo); access control on host logs and core dumps; never enable framework request-body logging on the ingestion API.
- **Rollups are pre-aggregated via continuous aggregate.** The `/metrics` dashboard reads `metrics_minute`, a TimescaleDB continuous aggregate over `inference_logs`. Additive metrics (counts, sums, costs) are materialized; percentile queries run directly against the raw hypertable using `percentile_cont`.
- **Dedupe is structural.** `inference_logs` PK is `(id, created_at)` with `INSERT ... ON CONFLICT DO NOTHING`. `XCLAIM` retries are no-ops at the DB.

---

## 3. Scaling considerations

What breaks first, and what to do when it does.

| Layer | Today | First bottleneck | Lever |
|---|---|---|---|
| Chatbot API | Single container, stateless | CPU on SSE fan-out around ~1k concurrent streams | Horizontal scale-out behind an L7 LB; sticky-by-conversation only needed for cancel routing |
| Ingestion API | Single container, stateless | PII regex CPU (cheap; ~100s RPS per core) | Horizontal scale-out; regex is embarrassingly parallel |
| Redis Streams | Single broker, single stream `inference.logged`, `MAXLEN ~ 1_000_000` | Memory if workers stall for hours; a single stream is one logical key, so vertical scale only | Shard the stream first (`inference.logged.{0..N}` keyed by `hash(conversation_id)`); only then does Redis Cluster help. Kafka was the design target — partitions/offsets/keys reshape consumer-group code, so the `Bus` swap is non-trivial, not a drop-in |
| `log-writer` | Single consumer in `cg-writer` | Insert throughput at ~10M rows/day on one PG | Add consumers to the same group (Redis Streams distributes pending entries); inserts pipeline linearly because `(id, created_at)` ON CONFLICT makes them commutative |
| `inference_logs` (TimescaleDB) | Hypertable with 1-day chunks, compression after 7d, retention 30d | ~10M rows/day before query times tail off; compression extends this significantly | ClickHouse via `LogStore` swap; `raw_payload_uri` already exists for the S3 cutover |
| `metrics_minute` | Continuous aggregate; auto-refreshed every 5 min with real-time merge | Doesn't break at demo scale | ClickHouse materialized view when the logs move |
| Dashboard reads | Additive metrics from CAGG; percentiles from raw hypertable with chunk exclusion | Percentile queries over large time ranges | ClickHouse `quantile()` when logs move |

The seams are the load-bearing part. `LogStore` / `RawPayloadStore` / `Bus` interfaces are wired through the codebase today; no business logic imports `psycopg`, `redis-py`, or `boto3` directly. **But the seams don't hide query semantics:** the PG→ClickHouse swap also means moving from `ON CONFLICT DO NOTHING` to `ReplacingMergeTree` (eventual dedupe, not immediate), from `percentile_cont` to `quantile()` (approximate by default), and rewriting backfill tooling. PG→Kafka similarly reshapes the bus around offsets/partitions/keys. The interfaces buy us "no caller refactor"; they do not buy us "no design work."

---

## 4. Failure handling assumptions

What's expected to fail, what we do about it, and what we explicitly accept as the cost.

| Failure | Behavior | Assumption we're making |
|---|---|---|
| **Provider 5xx / timeout** | SDK records `status=error` or `timeout` with `error_type` + `provider_error_code`. Chatbot API surfaces the error to the UI and persists the partial assistant message with `status='error'`. | Provider errors are first-class observability data, not exceptions to swallow. |
| **User cancels mid-stream** | UI aborts the `fetch`; chatbot API cancels the LiteLLM call; partial content is saved with `status='cancelled'`; one log event emitted with `status=cancelled` and `latency_ms` = time to cancel. | Cancelled streams are interesting (UX latency signal), not noise. |
| **Ingestion API down** | SDK gets a connect/5xx error; retains the event in its bounded in-memory queue and retries with backoff. User-facing chat is unaffected. | Brief ingestion outages are recoverable in-memory; long outages overflow the queue and drop oldest. Logs are observability, not source of truth. |
| **Redis down** | Ingestion API returns `503`; SDK retries. No partial state — events are either fully published or fully retried. | Redis is in the critical observability path; the data plane (chat) is unaffected. |
| **`log-writer` crash** | Pending entries `XCLAIM`ed by another consumer after visibility timeout. `INSERT ... ON CONFLICT (id, created_at) DO NOTHING` makes retries no-ops. After 5 retries → `inference.dead` + alert log line. | Duplicates are structurally impossible at the DB; the writer is safe to restart at any time. |
| **CAGG refresh lag** | If the background refresh job falls behind, `materialized_only = false` ensures queries still merge materialized data with recent raw rows. Dashboard reads are always consistent, just slower for the unmaterialized window. | Real-time merge covers the gap; refresh catches up on its next run. |
| **Late events** (arriving after a chunk was compressed) | Events for chunks older than the compression policy (7d) will fail to INSERT. `ON CONFLICT DO NOTHING` means this is silent. | At demo scale, 7 days of uncompressed data is generous. Late events beyond that are acceptably rare. |
| **Postgres down** | Writer backs off and retries; ingestion API keeps publishing to Redis (bus is decoupled from the sink). | The bus absorbs DB outages up to `MAXLEN`; the dashboard goes stale but the chat keeps working. |
| **Long worker / DB outage exceeding `MAXLEN`** | Redis silently trims oldest events. Trim count is *not* yet exposed as a metric — known gap. | Bounded memory beats unbounded backlog at demo scale. Production fix: pair Redis with an object spool or move to Kafka via the `Bus` interface. |
| **PII regex false negative** | Unredacted text reaches the DB. | Mitigated by the `Redactor` swap to Presidio; the failure mode is bounded to text fields and never includes full raw payloads in the default config. |
| **`PRISM_CREDS_KEY` lost / rotated** | Saved provider credentials become undecryptable; operator must delete and re-enter. | We don't auto-generate ephemeral keys at boot — that would silently brick credentials across restarts. |
| **No auth on any API** | Compose binds every port to `127.0.0.1`. | Single-tenant local demo by design. `127.0.0.1` is a *network* boundary, not a *process* boundary: every local process and any browser session on the host can reach the credential-adjacent endpoints (`/v1/credentials`, chat APIs) and pull decrypted-at-use provider secrets. Do not run on a shared/multi-user host, and do not expose to the public internet without an auth layer in front. |
