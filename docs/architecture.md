# Architecture

## Context

Olive is a takehome for ollive.ai. The brief: build a lightweight inference logging + ingestion system for an LLM application — a chatbot, an SDK that wraps LLM calls, an ingestion pipeline, and a database. The rubric explicitly calls out *schema design and practical tradeoffs*, so the architecture is designed to demonstrate judgment, not just code volume.

### In-scope bonuses
Multi-provider, streaming responses, dashboards, Docker Compose one-command setup, PII redaction, event-based architecture.

### Out-of-scope bonuses
k8s self-hosted deploy, cancel/list/resume frontend.

### Locked decisions (see `adr/` for rationale)
- **Stack:** Python everywhere — FastAPI services, LiteLLM provider abstraction, React/Next.js for chatbot UI. (ADR-0001)
- **Storage:** Single Postgres now, engineered for clean migration to Postgres + ClickHouse + S3 without refactoring callers. (ADR-0002)
- **Event bus:** Redis Streams, behind a `Bus` interface. (ADR-0003)

---

## 1. Product scope

A working slice with these capabilities:

1. **Chatbot UI** — multi-turn chat, short context window, model selector (OpenAI / Anthropic / Gemini via LiteLLM), streaming token-by-token responses.
2. **Python SDK** (`olive-sdk`) — wraps LiteLLM, captures metadata, emits log events fire-and-forget to ingestion.
3. **Ingestion API** — FastAPI; validates SDK payloads, redacts PII, publishes to Redis Streams.
4. **Workers** consuming Redis Streams:
   - `log-writer` — batched inserts into `inference_logs`.
   - `metrics-roller` — 60-second rollups into `metrics_minute` for the dashboard.
5. **Dashboard** — read-only page showing latency p50/p95, throughput, error rate, token usage per model. Reads `metrics_minute`, not raw logs.
6. **Conversations API** — list conversations and messages (chatbot needs this to render history).
7. **Docker Compose** — one command brings the whole system up.

**Explicit non-goals:** auth/multi-tenancy, k8s, eval/replay, prompt management, RAG, tool calling, cost-per-call pricing UI, cancel/resume conversations.

---

## 2. Core entities

| Entity | Purpose | Lives in (today / future) |
|---|---|---|
| `Conversation` | Chat session. Owns messages. Has model/system-prompt defaults. | Postgres `conversations` / unchanged |
| `Message` | One turn (user or assistant) in a conversation. User-visible chat history. | Postgres `messages` / unchanged |
| `InferenceLog` | One LLM call. Latency, tokens, status, model, provider, request/response previews. Append-only. | Postgres `inference_logs` (partitioned) / **ClickHouse** later |
| `RawPayload` | Full request + response JSON. Large, rarely read. | Postgres `inference_logs.raw_payload_jsonb` today / **S3 referenced by `raw_payload_uri`** later |
| `MetricsMinute` | Per-(minute, model, provider) rollup: count, p50/p95 latency, errors, token sums. | Postgres `metrics_minute` / **ClickHouse materialized view** later |
| `PIIRedactionRule` | Regexes + provider hooks (email/phone/SSN/credit-card). Code, not data. | Code / unchanged |

**Critical invariant:** `Conversation` and `Message` are *app data* (mutable, low volume, OLTP). `InferenceLog`, `RawPayload`, `MetricsMinute` are *observability data* (append-only, high volume, OLAP). They never join on the write path. The link from log to message is `inference_logs.message_id` — a **soft FK** (no DB constraint) so future migration of the logs group doesn't require constraint surgery. See ADR-0008.

---

## 3. Service boundaries

Five processes plus the UI:

```
┌──────────────┐      ┌──────────────┐
│  Chatbot UI  │──────│ Chatbot API  │──────► LiteLLM ──► Provider
│ (Next.js)    │ SSE  │ (FastAPI)    │   ▲
└──────────────┘      └──────┬───────┘   │ (olive-sdk wraps this call)
                             │
                             │ (sdk fire-and-forget HTTP)
                             ▼
                      ┌──────────────┐
                      │  Ingestion   │── XADD ──► Redis Streams: inference.logged
                      │   API        │                       │
                      │ (FastAPI)    │            ┌──────────┴──────────┐
                      └──────────────┘            ▼                     ▼
                                            ┌──────────┐         ┌──────────────┐
                                            │log-writer│         │metrics-roller│
                                            │ (cg-w)   │         │   (cg-r)     │
                                            └────┬─────┘         └──────┬───────┘
                                                 ▼                      ▼
                                          inference_logs           metrics_minute
                                            (Postgres)               (Postgres)
```

**Why this split:**
- Chatbot API is the only thing that talks to LiteLLM. The SDK is inside it.
- Ingestion API is the **trust boundary**: validation and PII redaction happen here, before anything hits the bus. Nothing past the bus is allowed to see raw PII. (ADR-0006)
- Two workers (not one), because they have different batching windows and failure modes — writer batches by size/time, roller by tumbling window.
- `inference_logs` and `metrics_minute` are written **only** by workers, never by the ingestion API. This is the seam that lets us swap to ClickHouse later by replacing the worker's sink, not the API. (ADR-0005)

---

## 4. SDK public API design

See [`api-contracts.md`](api-contracts.md#sdk-public-api) for the full surface. Headline points:

- Importing `olive_sdk` is the **only** way the chatbot talks to LiteLLM. There is no `import litellm` anywhere outside the SDK.
- Fire-and-forget: the user-facing call never waits for ingestion. Bounded in-memory queue + background flusher. (ADR-0009)
- Streaming emits **one** log event at stream completion, with TTFT + total latency + final status. No per-token events. (ADR-0007)
- We wrap LiteLLM, we do not use LiteLLM's `success_callback`. Visibility of instrumentation is the point. (ADR-0004)

---

## 5. Ingestion pipeline design

### Stage 1 — Ingestion API (`POST /v1/events:batch`)

- Accepts an array of `InferenceEvent`. Soft limit 100/req, hard 500.
- Pydantic validation at the boundary. Mixed valid/invalid batches return a single `202` with an `accepted` count and a `rejected: [{index, reason}]` array. There is no `422` path for batches; only fully malformed requests (not JSON, missing `events`) return `4xx`.
- **PII redaction here, on every text-bearing field.** Email, phone, SSN, credit-card regexes scrub `prompt_preview`, `response_preview`, **and (when present) every string field inside `raw_payload`**. `raw_payload` is then **dropped entirely** before publish unless `OLIVE_KEEP_RAW=true` is explicitly set (debug only; logs a loud warning at startup). Nothing past the bus ever sees an unredacted prompt, response, or raw payload — by construction. (ADR-0006)
- `XADD` each redacted event to Redis stream `inference.logged`.
- Returns `202` with stream IDs and per-event reject reasons.

**Failure modes:**
- Redis down → return `503`; SDK retains the event in its queue and retries with backoff.
- Malformed request body → `400`. Per-event validation failures appear in the `rejected` array of a `202` response (others in the batch are still published).

### Stage 2 — `log-writer` worker

- Consumer group `cg-writer` on `inference.logged`.
- Buffers up to 1000 events or 5s.
- Bulk insert into `inference_logs` using `INSERT ... ON CONFLICT (id, created_at) DO NOTHING`. Replayed or `XCLAIM`ed events become no-ops at the DB; **`inference_logs` is dedupe-safe by primary key**.
- `XACK` after the bulk insert returns. Pending entries are `XCLAIM`ed after visibility timeout and retried — duplicate inserts are harmless thanks to `ON CONFLICT DO NOTHING`.
- After 5 retries → moved to `inference.dead` stream + alert log line.

### Stage 3 — `metrics-roller` worker

The roller is the **hot path** for the dashboard. It is best-effort and explicitly **not** the source of truth; the reconciler in Stage 4 is.

- Consumer group `cg-roller` on the same stream.
- Maintains in-memory tumbling 60s windows keyed by `(minute_bucket, model, provider)`.
- Events stay **un-XACKed** until their window closes (i.e. real-world clock passes `bucket + 60s + grace`, default grace 5s).
- On window close, for each `(model, provider)` in that bucket, the worker writes a **REPLACE** row, not an increment:
  ```sql
  INSERT INTO metrics_minute (minute_bucket, model, provider, count, error_count, latency_p50_ms, latency_p95_ms, prompt_tokens_sum, completion_tokens_sum)
  VALUES (...)
  ON CONFLICT (minute_bucket, model, provider) DO UPDATE SET
    count = EXCLUDED.count,
    error_count = EXCLUDED.error_count,
    latency_p50_ms = EXCLUDED.latency_p50_ms,
    latency_p95_ms = EXCLUDED.latency_p95_ms,
    prompt_tokens_sum = EXCLUDED.prompt_tokens_sum,
    completion_tokens_sum = EXCLUDED.completion_tokens_sum;
  ```
  Because the UPSERT *replaces* (does not increment), a worker crash and replay produces the same row, not a doubled one.
- Then `XACK`s every event that fed that window.
- **Late events** (events arriving after a bucket was closed) are accepted but bypass the in-memory aggregator; they trigger a reconciler run for that bucket via Stage 4.

### Stage 4 — `metrics-reconciler` (source of truth)

A lightweight job (cron container, every 5 min) that recomputes the last *N* closed minute-buckets directly from `inference_logs` and `UPSERT`-replaces them into `metrics_minute`. This is the canonical, idempotent path:

```sql
INSERT INTO metrics_minute (minute_bucket, model, provider, count, error_count, ...)
SELECT date_trunc('minute', created_at), model, provider,
       count(*), count(*) FILTER (WHERE status <> 'ok'),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms),
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms),
       sum(prompt_tokens), sum(completion_tokens)
FROM inference_logs
WHERE created_at >= now() - interval '15 minutes'
GROUP BY 1, 2, 3
ON CONFLICT (minute_bucket, model, provider) DO UPDATE SET ...; -- REPLACE, not add
```

Because the reconciler reads `inference_logs` (which is itself dedupe-safe via Stage 2), the rollups it produces are deterministic regardless of how many times the roller or reconciler ran before. (ADR-0010)

### Why two consumer groups, not one writer
Each consumer owns a side-effect independently. Adding a `pii-deep-scanner`, `cost-calculator`, or `eval-sampler` is a new consumer, not a writer change. This is the actual event-driven story. (ADR-0010)

---

## 6. Storage design

Full DDL and partition strategy in [`schema.md`](schema.md). Headline:

- **Three table groups, even inside one Postgres:**
  - **A. App data** — `conversations`, `messages` (OLTP, mutable).
  - **B. Inference logs** — `inference_logs`, partitioned daily by `created_at` (OLAP, append-only).
  - **C. Rollups** — `metrics_minute` (read-optimized, denormalized).
- **No FKs between groups.** Links are soft (e.g. `inference_logs.message_id`). This means any group can move to a different store without unwinding DB constraints. (ADR-0008)
- **`raw_payload_uri` exists from day one** alongside `raw_payload_jsonb`. Today, writes go to jsonb; readers check `if uri: fetch_from(uri) else: read_jsonb`. When S3 lands, writes flip to the URI column. Readers don't change. (ADR-0002)
- **All log table access goes through a `LogStore` interface.** Today: `PostgresLogStore`. Future: `ClickHouseLogStore` + `S3RawPayloadStore` slot in via DI; business logic doesn't move. (ADR-0005)

---

## 7. API contracts

See [`api-contracts.md`](api-contracts.md) for the full spec. Surfaces:

- **SDK → Ingestion:** `POST /v1/events:batch`
- **Chatbot UI ↔ Chatbot API:**
  - `POST /v1/conversations`
  - `GET /v1/conversations/:id/messages`
  - `POST /v1/conversations/:id/messages` (SSE for streaming)
- **Dashboard:** `GET /v1/metrics?from=&to=&model=&provider=`
- **Internal event:** Redis Stream `inference.logged` (versioned via `schema_version`)

---

## 8. Deployment / dev setup

See [`runbook.md`](runbook.md) for the operator-facing version. `docker compose up` brings up:

- `postgres` (init SQL: schemas, partition function, next-7-day partitions)
- `redis` (Streams + general cache)
- `chatbot-api` (FastAPI + olive-sdk)
- `ingestion-api` (FastAPI)
- `log-writer` worker
- `metrics-roller` worker
- `metrics-reconciler` cron (recomputes recent buckets from `inference_logs`)
- `chatbot-ui` (Next.js)
- `partition-cron` (creates tomorrow's partition nightly)

`.env.example` enumerates every env var. `make up`, `make down`, `make logs`, `make seed`, `make test`, `make demo`.
