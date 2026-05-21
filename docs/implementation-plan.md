# Prism — LLM Inference Logging & Ingestion System

## Context

This is a takehome for **ollive.ai** (Fullstack Engineer). The brief is to build a lightweight inference logging + ingestion system for an LLM application, in four parts: a chatbot, an SDK that wraps LLM calls and captures metadata, an ingestion pipeline, and a database. The grading rubric explicitly calls out *schema design and practical tradeoffs* — so the architecture must show judgment, not just code volume.

The "guaranteed interview" bonus list (multi-provider, streaming, dashboards, Docker Compose, event-based architecture, PII redaction, k8s, frontend cancel/list/resume) is partially in scope: we commit to **multi-provider, streaming, dashboards, Docker Compose, PII redaction, and event-based architecture**. k8s and cancel/resume frontend are explicitly **out of scope** to keep the takehome shippable.

### Locked decisions (from clarification)
- **Stack:** Python everywhere — FastAPI for services, LiteLLM for provider abstraction, React/Next.js only for the chatbot UI.
- **Storage:** Single Postgres now, **but engineered so the OLTP/OLAP/blob split (Postgres + ClickHouse + S3) can drop in without refactoring callers**. This is a hard constraint, not a nice-to-have.
- **Event bus:** Redis Streams (with a `Bus` abstraction so Kafka can swap in later).
- **Bonuses in scope:** multi-provider, streaming, dashboards, Docker Compose, PII redaction, event-based architecture.
- **Bonuses out of scope:** k8s deploy, cancel/list/resume frontend.

---

## Where these docs live in the repo

The user asked for the architecture stored in `docs/` with an ADR log. Implementation phase will create:

```
docs/
├── README.md                      # index / how to read these docs
├── architecture.md                # sections 1–8 below, the canonical overview
├── api-contracts.md               # SDK + ingestion HTTP/event schemas
├── schema.md                      # ER diagram, table DDL rationale, partition strategy
├── risks-and-tradeoffs.md         # section 9 below
├── runbook.md                     # how to run locally, env vars, common ops
└── adr/
    ├── README.md                  # ADR index + template
    ├── 0001-python-fastapi-litellm.md
    ├── 0002-single-postgres-with-clean-migration-path.md
    ├── 0003-redis-streams-as-event-bus.md
    ├── 0004-sdk-as-thin-wrapper-not-litellm-callback.md
    ├── 0005-log-sink-abstraction.md
    ├── 0006-pii-redaction-at-ingest-boundary.md
    ├── 0007-streaming-final-event-logging.md
    ├── 0008-conversation-message-log-three-table-split.md
    ├── 0009-fire-and-forget-sdk-emission-with-bounded-queue.md
    └── 0010-metrics-rollup-via-consumer-not-query-time.md
```

ADR format follows the standard Michael Nygard template: **Status, Context, Decision, Consequences**. Each ADR is short (one page max). The point isn't ceremony — it's that future-you (or a reviewer) can read why a choice was made without re-deriving the tradeoff from code.

---

## 1. Product scope

A working slice with these capabilities:

1. **Chatbot UI** — multi-turn chat with short context window, model selector (OpenAI / Anthropic / Gemini via LiteLLM), streaming responses rendered token-by-token.
2. **Python SDK** (`prism-sdk`) — wraps LiteLLM, captures metadata, emits log events fire-and-forget to ingestion.
3. **Ingestion API** — FastAPI service; validates SDK payloads, redacts PII, publishes to Redis Streams.
4. **Workers** (consume Redis Streams):
   - `log-writer` — batched inserts into `inference_logs`.
   - `metrics-roller` — 1-minute rollups into `metrics_minute` for the dashboard.
5. **Dashboard** — read-only page showing latency p50/p95, throughput, error rate, token usage per model. Backed by `metrics_minute`, not raw logs.
6. **Conversations API** — list conversations + messages (chatbot already needs this to render history).
7. **Docker Compose** — one command brings the whole system up.

**Explicit non-goals:** auth/multi-tenancy, k8s, eval/replay, prompt management, RAG, tool calling, cost-per-call pricing UI, cancel/resume conversations.

---

## 2. Core entities

| Entity | Purpose | Lives in (today / future) |
|---|---|---|
| `Conversation` | A chat session. Owns messages. Has model/system-prompt defaults. | Postgres `conversations` / unchanged |
| `Message` | One turn (user or assistant) in a conversation. The user-visible chat history. | Postgres `messages` / unchanged |
| `InferenceLog` | One LLM call. Latency, tokens, status, model, provider, request/response previews, links to a message. Append-only. | Postgres `inference_logs` (partitioned) / **ClickHouse** later |
| `RawPayload` | Full request + response JSON. Large, rarely read. | Postgres `inference_logs.raw_jsonb` today / **S3 referenced by `raw_payload_uri`** later |
| `MetricsMinute` | Pre-aggregated per-(minute, model, provider) rollup of count, p50/p95 latency, error count, token sums. | Postgres `metrics_minute` / **ClickHouse materialized view** later |
| `PIIRedactionRule` | (Static config, not a table) — regexes + provider hooks (email/phone/SSN/credit-card). | Code / unchanged |

**Critical invariant:** `Conversation` + `Message` are *app data* (mutable, low volume, OLTP). `InferenceLog` + `RawPayload` + `MetricsMinute` are *observability data* (append-only, high volume, OLAP). They never join on the write path. The only link is `inference_logs.message_id` — a foreign-key-by-convention, not a DB constraint, so the future split doesn't require constraint surgery.

---

## 3. Service boundaries

Five processes, all Python except the chatbot UI:

```
┌──────────────┐      ┌──────────────┐
│  Chatbot UI  │──────│ Chatbot API  │──────► LiteLLM ──► Provider
│ (Next.js)    │ SSE  │ (FastAPI)    │   ▲
└──────────────┘      └──────┬───────┘   │ (prism-sdk wraps this call)
                             │
                             │ (sdk fire-and-forget)
                             ▼
                      ┌──────────────┐
                      │  Ingestion   │──── publish ──► Redis Streams: inference.logged
                      │   API        │                       │
                      │ (FastAPI)    │            ┌──────────┴──────────┐
                      └──────────────┘            ▼                     ▼
                                            ┌──────────┐         ┌──────────────┐
                                            │log-writer│         │metrics-roller│
                                            │ worker   │         │   worker     │
                                            └────┬─────┘         └──────┬───────┘
                                                 ▼                      ▼
                                          inference_logs           metrics_minute
                                            (Postgres)               (Postgres)
```

**Why this split:**
- Chatbot API is the only thing that talks to LiteLLM. SDK is *inside* it.
- Ingestion API is the trust boundary: validation + PII redaction happen here, before anything hits the bus. Nothing past the bus is allowed to see raw PII.
- Two workers, not one, because they have different batching windows (writer: 5s or 1k rows; roller: 60s tumbling window) and different failure modes.
- Postgres tables `inference_logs` and `metrics_minute` are written **only** by the workers, never by the ingestion API directly. This is the seam that lets us swap to ClickHouse later by replacing the worker's sink, not the API.

---

## 4. SDK public API design

`prism-sdk` is a thin Python package. The whole point is that the chatbot imports *this*, never `litellm` or provider SDKs directly.

### Surface

```python
from prism_sdk import PrismClient

client = PrismClient(
    ingestion_url="http://ingestion:8001",
    api_key=None,                 # not used in takehome, reserved for future
    sink="http",                  # "http" | "noop" | "stdout" (testing)
    flush_interval_ms=200,
    queue_max=10_000,
    on_drop="log",                # "log" | "raise"
)

# Non-streaming
resp = client.chat.completions.create(
    model="gpt-4o",               # passed straight to LiteLLM, multi-provider for free
    messages=[...],
    conversation_id="...",        # required — links inference to message
    message_id="...",             # required — links inference to message
    metadata={"user_id": "..."},  # arbitrary tags, indexed loosely
)

# Streaming
async for chunk in client.chat.completions.stream(...):
    yield chunk
# SDK emits ONE inference_log at stream completion, with TTFT + total latency.
```

### Internal flow (per call)

1. Generate `inference_id` (uuid7 for time-orderable).
2. Start monotonic timer; capture `ts_start`.
3. Call LiteLLM; capture `ts_first_token` if streaming.
4. On success: capture tokens (LiteLLM exposes `usage`), response preview (first 500 chars).
5. On error: capture exception type, message, provider-side error code if available.
6. Build `InferenceEvent` (see API contracts §7).
7. Push onto in-memory bounded queue. **Never block the caller.**
8. Background flusher thread POSTs batches to ingestion every `flush_interval_ms` or when batch hits 100 events.
9. If queue full → drop oldest + record `on_drop` action. (Logs are observability — never sacrifice user latency for them.)

### Why a wrapper instead of LiteLLM's callback hook
LiteLLM has `success_callback` / `failure_callback` hooks. Using them would hide the implementation. We deliberately wrap so the reviewer sees latency capture, error capture, payload extraction, and emission as explicit code in our SDK. See **ADR-0004**.

---

## 5. Ingestion pipeline design

### Stage 1 — Ingestion API (`POST /v1/events:batch`)

- Accepts an array of `InferenceEvent`. Soft limit 100/req, hard 500.
- Schema validation via Pydantic — reject malformed events at the boundary, return per-event error array (partial success allowed).
- **PII redaction here** (not in SDK, not in workers): regexes for email/phone/SSN/credit-card on `prompt_preview`, `response_preview`. Original is dropped *unless* `PRISM_KEEP_RAW=true` (off by default; for debugging only).
- Publish each redacted event to Redis Stream `inference.logged` with `XADD`.
- Return 202 with stream IDs (so SDK can log them if needed for debugging; not used for retry semantics).

**Failure modes:**
- Redis down → return 503, SDK keeps event in its queue, retries with backoff.
- Validation error → 422 with details for that event, others still published.

### Stage 2 — `log-writer` worker

- Consumer group `cg-writer` on `inference.logged`.
- Buffers up to 1000 events or 5s, whichever first.
- Bulk insert into `inference_logs` (Postgres `COPY` or `executemany`).
- `XACK` after successful insert. Failed batch → `XCLAIM` after visibility timeout; retried.
- After N retries (default 5) → moved to `inference.dead` stream + alert log line.

### Stage 3 — `metrics-roller` worker

- Same stream, different consumer group `cg-roller`.
- Tumbling 60s window, keyed by `(model, provider)`.
- At window close, `UPSERT` one row into `metrics_minute` per key.
- Idempotent on `(minute_bucket, model, provider)` so re-processing is safe.

### Why two consumer groups, not one writer
This is the heart of the event-driven story. Each consumer owns a side-effect independently. Tomorrow we can add a `pii-deep-scanner`, a `cost-calculator`, an `eval-sampler` consumer — none touch the writer. See **ADR-0010**.

---

## 6. Storage design

### Three table groups, even in single Postgres

**Group A — App data (OLTP, mutable, low volume)**
```
conversations(id, user_id_nullable, model_default, system_prompt, created_at, updated_at)
messages(id, conversation_id FK, role enum, content text, created_at)
```

**Group B — Inference logs (OLAP, append-only, high volume)**
```
inference_logs(
  id uuid7 PK,
  conversation_id uuid,         -- soft FK, not enforced
  message_id uuid,              -- soft FK, not enforced
  model text,
  provider text,
  status text,                  -- ok | error | timeout
  error_type text NULL,
  latency_ms int,
  ttft_ms int NULL,             -- for streaming
  prompt_tokens int,
  completion_tokens int,
  total_tokens int,
  prompt_preview text,          -- redacted, first 500 chars
  response_preview text,        -- redacted, first 500 chars
  raw_payload_uri text NULL,    -- today: NULL or local FS path; future: s3://...
  raw_payload_jsonb jsonb NULL, -- today: full payload; future: NULL (in S3)
  metadata_jsonb jsonb,         -- arbitrary tags from SDK
  created_at timestamptz,
  PARTITION BY RANGE (created_at)   -- daily partitions
)
```
**Indexes:** `(created_at)`, `(model, created_at)`, `(conversation_id, created_at)`, `(status, created_at) WHERE status != 'ok'`.

**Group C — Rollups (read-optimized, denormalized)**
```
metrics_minute(
  minute_bucket timestamptz,
  model text,
  provider text,
  count int,
  error_count int,
  latency_p50_ms int,
  latency_p95_ms int,
  prompt_tokens_sum bigint,
  completion_tokens_sum bigint,
  PRIMARY KEY (minute_bucket, model, provider)
)
```

### The migration seam (the whole reason for this design)
Every read of `inference_logs` and `metrics_minute` goes through a thin `LogStore` interface (`get_logs`, `get_metrics`, `write_logs_batch`, `upsert_metrics`). Today there's one impl: `PostgresLogStore`. Tomorrow we add `ClickHouseLogStore` and `S3RawPayloadStore`, change one DI wiring line, and *no business logic moves*. See **ADR-0005**.

`raw_payload_uri` exists from day one, even though today we write the JSON to `raw_payload_jsonb` instead. When S3 lands, the column flips: jsonb becomes NULL, URI becomes `s3://...`. Readers already check `if uri: fetch_s3 else: use_jsonb` from day one. See **ADR-0002**.

---

## 7. API contracts

Documented in full in `docs/api-contracts.md`. Sketch:

### SDK → Ingestion (`POST /v1/events:batch`)
```json
{
  "events": [
    {
      "inference_id": "uuid7",
      "conversation_id": "uuid",
      "message_id": "uuid",
      "model": "gpt-4o",
      "provider": "openai",
      "status": "ok",
      "error": null,
      "ts_start": "2026-05-21T...",
      "ts_end": "2026-05-21T...",
      "latency_ms": 842,
      "ttft_ms": 120,
      "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
      "prompt_preview": "...",
      "response_preview": "...",
      "raw_payload": {...},
      "metadata": {...},
      "sdk_version": "0.1.0"
    }
  ]
}
```
Response: `202 { "accepted": N, "rejected": [{index, reason}] }`.

### Chatbot UI ↔ Chatbot API
- `POST /v1/conversations` → `{conversation_id}`
- `GET /v1/conversations/:id/messages` → `[Message]`
- `POST /v1/conversations/:id/messages` (SSE) → streams assistant tokens; closes with a `done` event containing `inference_id`.

### Dashboard
- `GET /v1/metrics?from=&to=&model=&provider=` → array of `metrics_minute` rows.

### Internal event (Redis Stream `inference.logged`)
Same shape as the SDK event after PII redaction. Versioned via `schema_version` field so consumers can evolve independently.

---

## 8. Deployment / dev setup

`docker compose up` brings everything up:

- `postgres` (with init SQL: schemas, partition function, daily partitions for next 7d)
- `redis` (Streams + general cache)
- `chatbot-api` (FastAPI + prism-sdk)
- `ingestion-api` (FastAPI)
- `log-writer` (Python worker)
- `metrics-roller` (Python worker)
- `chatbot-ui` (Next.js dev server)
- `partition-cron` (lightweight container that creates tomorrow's partition nightly)

`.env.example` documents every var: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `INGESTION_URL`, `PRISM_KEEP_RAW`, etc.

Make targets: `make up`, `make down`, `make logs`, `make seed`, `make test`, `make demo` (opens UI + dashboard tabs).

---

## 9. Risks and tradeoffs

| Risk | Mitigation | What we accept |
|---|---|---|
| SDK fire-and-forget can lose logs on crash | Bounded in-memory queue + retry; flush on shutdown via atexit hook | Logs are observability; some loss is acceptable in exchange for never blocking user latency. See ADR-0009. |
| Postgres won't scale past ~10M log rows | Daily partitions, indexes designed for prune-friendly queries; ClickHouse path pre-wired via `LogStore` | Demo scale is fine. Migration is a one-line DI change. |
| PII regex misses non-standard formats | Documented limitation; structure allows plugging in Presidio / a model-based redactor as a second pass | Takehome scope; better than nothing, clearly bounded. |
| Dashboard queries hot logs table at query time | Pre-aggregated `metrics_minute` rolled by consumer, queries only hit rollup | Loss of sub-minute granularity in dashboard. |
| Redis Streams loses messages on broker crash | Consumer groups + XACK + claim-on-timeout + dead-letter stream | Single-broker demo; HA would need Redis Cluster or move to Kafka. |
| Streaming responses + logging race | Log is emitted **only on stream completion** with TTFT + total latency. Cancelled streams emit a `status: cancelled` log. See ADR-0007. | Mid-stream telemetry is not logged per-token (would be overkill). |
| Schema drift between SDK and ingestion | `schema_version` in event, Pydantic on ingestion side strict on required fields, lenient on additional | Forward-compat: old SDKs work; back-compat: old ingestion ignores new fields. |
| Provider SDK quirks (token counting differences) | LiteLLM normalizes `usage`. Where it can't, we record `null` rather than guessing. | Some completion_tokens may be missing for niche providers; surfaced in dashboard as "unknown". |

---

## 10. ADR list (write these BEFORE coding the relevant area)

1. **ADR-0001** — Python everywhere (FastAPI + LiteLLM). Why not Node.
2. **ADR-0002** — Single Postgres, with `raw_payload_uri` column + `LogStore` interface to enable PG+CH+S3 split later.
3. **ADR-0003** — Redis Streams as event bus (not Kafka/SQS). Why.
4. **ADR-0004** — SDK is a thin wrapper around LiteLLM, **not** a LiteLLM callback. Visibility of instrumentation is a feature.
5. **ADR-0005** — `LogStore` / `RawPayloadStore` / `Bus` interfaces are required from day one. No direct DB calls from API/worker logic.
6. **ADR-0006** — PII redaction happens at ingestion API boundary, not in SDK and not in workers. Single trust boundary.
7. **ADR-0007** — Streaming responses log **once at completion**, including TTFT, total latency, and final status (ok/error/cancelled). No per-token events.
8. **ADR-0008** — Three table groups (app / logs / rollups) with no DB-level FKs between groups. Enables clean migration of any group.
9. **ADR-0009** — SDK is fire-and-forget with a bounded queue; on overflow, drop oldest. User latency is never sacrificed for logs.
10. **ADR-0010** — Dashboard reads pre-aggregated `metrics_minute`, populated by a dedicated `metrics-roller` consumer. Query-time aggregation is explicitly rejected.

---

## Phase-wise implementation plan

Each phase ends with something demonstrable. Don't skip the demo step — it forces integration.

### Phase 0 — Repo + docs scaffold (½ day)
- [ ] Create repo structure (`/sdk`, `/services/chatbot-api`, `/services/ingestion-api`, `/services/workers`, `/web`, `/docs`, `/infra`).
- [ ] Write all 10 ADRs as one-pagers (skeleton with Decision + Consequences).
- [ ] Write `docs/architecture.md`, `docs/api-contracts.md`, `docs/schema.md`, `docs/risks-and-tradeoffs.md`, `docs/runbook.md` from this plan.
- [ ] Docker Compose skeleton with empty service containers that boot.
- **Demoable:** `docker compose up` shows all services healthy.

### Phase 1 — Storage foundation (½ day)
- [ ] Postgres init SQL: three table groups, partition function, indexes.
- [ ] Daily partition cron container.
- [ ] `LogStore` / `RawPayloadStore` / `Bus` interfaces defined. `PostgresLogStore`, `LocalRawPayloadStore`, `RedisStreamsBus` impls.
- [ ] Smoke tests for each interface.
- **Demoable:** psql into Postgres, see tables; tests pass.

### Phase 2 — Ingestion API + log-writer worker (1 day)
- [ ] `POST /v1/events:batch` with Pydantic validation.
- [ ] PII redaction module (email/phone/SSN/credit-card regexes).
- [ ] Publish to Redis Streams.
- [ ] `log-writer` worker: consume, batch, insert.
- [ ] curl-driven integration test: post 100 events → see them in `inference_logs`.
- **Demoable:** end-to-end log flow without a chatbot.

### Phase 3 — SDK (1 day)
- [ ] `PrismClient` with `chat.completions.create` (non-streaming first).
- [ ] LiteLLM call, metadata capture, fire-and-forget queue + flusher.
- [ ] Unit tests with `sink="noop"` and mocked LiteLLM.
- [ ] Integration test against running ingestion.
- **Demoable:** a tiny Python script calls OpenAI + Anthropic + Gemini, dashboard query shows all 3.

### Phase 4 — Chatbot (API + UI) non-streaming (1 day)
- [ ] FastAPI service with conversations + messages endpoints.
- [ ] Next.js UI: model selector, send message, render history.
- [ ] Wires through prism-sdk on every LLM call.
- **Demoable:** real chat session, with logs landing in DB.

### Phase 5 — Streaming (½ day)
- [ ] SDK streaming path with TTFT capture + single completion event.
- [ ] Chatbot API SSE endpoint.
- [ ] UI token-by-token render.
- **Demoable:** streaming chat with one log row per response showing TTFT and total latency.

### Phase 6 — metrics-roller + dashboard (1 day)
- [ ] `metrics-roller` worker, 60s windows, idempotent upsert.
- [ ] `GET /v1/metrics` endpoint.
- [ ] Dashboard page: line charts for latency p50/p95, throughput, error rate, token usage. Filter by model/provider.
- **Demoable:** dashboard shows live activity as you chat.

### Phase 7 — Polish + submission (½ day)
- [ ] README: setup, architecture overview, schema decisions, tradeoffs, future improvements.
- [ ] Loom demo: chat → log appears → dashboard updates → kill ingestion → see SDK queue resilience → restart → drains.
- [ ] Final pass on all ADRs (any decision that changed during build).
- [ ] Submit to work@ollive.ai.

**Total estimate:** ~5.5 working days. Buffer of ~1 day for the things that will inevitably bite (LiteLLM streaming quirks per provider, partition edge cases, Docker networking on Mac).

---

## Verification (how to know it works)

End-to-end smoke (this is the demo script too):

1. `docker compose up` → all services healthy in `make logs`.
2. Open chatbot UI; have a 3-turn streaming conversation against GPT-4o.
3. Switch model to Claude; have another 3-turn conversation.
4. `psql`: `SELECT count(*), model FROM inference_logs GROUP BY model` → expect 6 rows split across models.
5. Hit `GET /v1/metrics?from=now-10m` → returns rollup rows for both models.
6. Open dashboard → latency/throughput charts show both models.
7. **Failure test:** `docker compose stop ingestion-api`. Send 5 more chat messages. Chatbot still works (logs queue in SDK). `docker compose start ingestion-api`. Within 5s, queue drains; new rows in `inference_logs`.
8. **PII test:** send a message containing a fake email + SSN. `SELECT prompt_preview FROM inference_logs ORDER BY created_at DESC LIMIT 1` → confirm redacted.
9. **Restart test:** restart all containers; existing conversations still load in UI; no data loss in DB.

All 9 steps pass → ready to submit.
