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
    ├── 0010-metrics-rollup-via-consumer-not-query-time.md
    ├── 0011-sdk-as-litellm-callback-supersedes-0004.md
    └── 0012-strands-tool-hooks.md
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

`prism-sdk` is a thin Python package built around a **LiteLLM callback** (see **ADR-0011**, which supersedes ADR-0004). The caller still imports `prism_sdk`, but it never wraps the LLM call site — instead it installs a `PrismCallback` on `litellm.callbacks` and the caller invokes `litellm.completion(...)` directly. This is the only way to intercept LLM turns inside an agent runtime (e.g. Strands) that owns the call site.

### Surface

```python
import litellm
from prism_sdk import PrismClient, metadata as prism_metadata

client = PrismClient(
    ingestion_url="http://ingestion:8001",
    sink="http",                  # "http" | "noop" | "stdout"
    flush_interval_ms=200,
    queue_max=10_000,
    on_drop="log",
)
client.install()                  # registers PrismCallback on litellm.callbacks

# Non-streaming
resp = litellm.completion(
    model="gpt-4o",
    messages=[...],
    metadata=prism_metadata(
        conversation_id="...",
        message_id="...",
    ),
)

# Streaming — same call, just stream=True
stream = await litellm.acompletion(
    model="gpt-4o",
    messages=[...],
    stream=True,
    stream_options={"include_usage": True},
    metadata=prism_metadata(conversation_id="...", message_id="..."),
)
async for chunk in stream:
    yield chunk
# PrismCallback fires log_success_event ONCE at stream completion with
# response_obj.usage populated and kwargs["completion_start_time"] for TTFT.
```

### Internal flow (per call)

1. LiteLLM finishes the call (success or failure) and invokes `PrismCallback.log_success_event` / `log_failure_event` with `kwargs`, `response_obj`, `start_time`, `end_time`.
2. Callback reads the `metadata.prism` namespace to recover correlation IDs.
3. Builds one `InferenceEvent`:
   - `latency_ms` from `end_time - start_time`.
   - `ttft_ms` from `kwargs["completion_start_time"] - start_time` when present.
   - `usage` from `response_obj.usage`.
   - `prompt_preview` from last user message; `response_preview` from `choices[0].message.content`.
   - `error` from `kwargs["exception"]` on failure paths.
4. Pushes onto `PrismClient`'s in-memory bounded queue. **Never blocks the caller.**
5. Background flusher thread POSTs batches to ingestion every `flush_interval_ms` or 100 events.
6. If queue full → drop oldest + record `on_drop` action.

### Why a callback instead of a call-site wrapper
A wrapper requires callers to invoke `client.chat.completions.create(...)`. Agent runtimes (Strands `LiteLLMModel`) own the call site and bypass wrappers. The brief explicitly sanctions "SDK, middleware, OR wrapper." Reviewer visibility is preserved by keeping `PrismCallback` small and explicit in one file. See **ADR-0011**.

### Tool calls (agent loops)
`PrismCallback` fires per LLM turn, not per tool execution. Tool traces are captured by a separate `prism_sdk.strands` adapter that subscribes to Strands hooks and pushes `ToolInvocationEvent`s onto the same queue. See **ADR-0012**.

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
| Coupling to LiteLLM callback timing / field semantics (ADR-0011) | Integration tests pin the fields we depend on (`completion_start_time`, `response_obj.usage`, `response_obj.choices[0].message.content`). | If LiteLLM changes callback contract, we get a test failure rather than silent data loss. |
| Strands runtime coupling once Phase 7 lands (ADR-0012) | `prism_sdk.strands` is an optional submodule; nothing breaks if Strands isn't installed. | Tool capture only works under Strands; another runtime would need its own adapter. |

---

## 10. ADR list (write these BEFORE coding the relevant area)

1. **ADR-0001** — Python everywhere (FastAPI + LiteLLM). Why not Node.
2. **ADR-0002** — Single Postgres, with `raw_payload_uri` column + `LogStore` interface to enable PG+CH+S3 split later.
3. **ADR-0003** — Redis Streams as event bus (not Kafka/SQS). Why.
4. **ADR-0004** — ~~SDK is a thin wrapper around LiteLLM~~. **Superseded by ADR-0011.**
5. **ADR-0005** — `LogStore` / `RawPayloadStore` / `Bus` interfaces are required from day one. No direct DB calls from API/worker logic.
6. **ADR-0006** — PII redaction happens at ingestion API boundary, not in SDK and not in workers. Single trust boundary.
7. **ADR-0007** — Streaming responses log **once at completion**, including TTFT, total latency, and final status (ok/error/cancelled). No per-token events.
8. **ADR-0008** — Three table groups (app / logs / rollups) with no DB-level FKs between groups. Enables clean migration of any group.
9. **ADR-0009** — SDK is fire-and-forget with a bounded queue; on overflow, drop oldest. User latency is never sacrificed for logs.
10. **ADR-0010** — Dashboard reads pre-aggregated `metrics_minute`, populated by a dedicated `metrics-roller` consumer. Query-time aggregation is explicitly rejected.
11. **ADR-0011** — SDK captures via a LiteLLM `CustomLogger` callback, not a call-site wrapper. Enables transparent capture inside agent runtimes (Strands) that own the LLM call site.
12. **ADR-0012** — Tool-call traces via Strands hooks (`prism_sdk.strands.PrismStrandsHooks`). Introduces a `ToolInvocationEvent` event type and a `tool_invocations` table.

---

## Phase-wise implementation plan

Each phase ends with something demonstrable. Don't skip the demo step — it forces integration.

### Phase 0 — Repo + docs scaffold (½ day) ✅
- [x] Repo structure (`/sdk`, `/services/chatbot-api`, `/services/ingestion-api`, `/services/workers`, `/web`, `/docs`, `/infra`).
- [x] All ADRs (0001–0010) written as one-pagers.
- [x] `docs/architecture.md`, `docs/api-contracts.md`, `docs/schema.md`, `docs/risks-and-tradeoffs.md`, `docs/runbook.md`.
- [x] Docker Compose skeleton.

### Phase 1 — Storage foundation (½ day) ✅
- [x] Postgres init SQL: three table groups, partition function, indexes (`infra/sql/init.sql`).
- [x] Daily partition cron container.
- [x] `LogStore` / `RawPayloadStore` / `Bus` interfaces + Postgres/Local/Redis impls.
- [x] Smoke tests.

### Phase 2 — Ingestion API + log-writer worker (1 day) ✅
- [x] `POST /v1/events:batch` with Pydantic validation.
- [x] PII redaction at the ingestion boundary.
- [x] Publish to Redis Streams.
- [x] `log-writer` worker: consume, batch, insert.

### Phase 3 + 4 + 5 — SDK + Chatbot (API + UI) + Streaming (combined) ✅
Originally three phases; collapsed because the ADR-0011 pivot to a LiteLLM callback removed the separate streaming surface and folded the SDK rework into the chatbot delivery.

- [x] `PrismClient` + `PrismCallback` (LiteLLM `CustomLogger`); `metadata()` correlation helper.
- [x] Fire-and-forget bounded queue + flusher; `sink` modes `http`/`noop`/`stdout`.
- [x] Chatbot API: `POST /v1/conversations`, `GET /v1/conversations/:id/messages`, `POST /v1/conversations/:id/messages` (SSE), all calling `litellm.acompletion(stream=True, ...)` directly.
- [x] Next.js UI: model selector, message send, history, token-by-token render via SSE.
- [x] ADR-0011 (Accepted) + ADR-0012 (Proposed) committed.
- **Demoable:** real streaming chat session, one `inference_log` row per assistant message with TTFT + total latency.

### Phase 6 — metrics-roller + dashboard (1 day) ✅

- [x] `metrics-roller` worker: second consumer group `cg-roller` on `inference.logged`, 60s tumbling window keyed by `(model, provider)`, idempotent REPLACE `UPSERT` into `metrics_minute`.
- [x] `metrics-reconciler` (out-of-band): periodic catchup pass over `inference_logs` (window 15min, default interval 5min) replacing rollup rows via a single SQL.
- [x] `GET /v1/metrics?from=&to=&model=&provider=` endpoint on chatbot-api reading via `LogStore.get_metrics`.
- [x] Dashboard page at `/metrics`: line charts for latency p50/p95, throughput, error rate, token usage; filter by model/provider; auto-refresh every 15s.
- [x] Smoke test: drive chat traffic, observe rollup rows appear within 60s, dashboard updates (run with `make up` once provider keys are set).
- **Demoable:** dashboard shows live activity as you chat.

### Phase 7 — Strands agent runtime + tool hooks (1 day) ✅
Moves chatbot-api off direct `litellm.acompletion` to a Strands agent loop. Implements ADR-0012 (promotes it from Proposed → Accepted on completion).

- [x] Replace the `litellm.acompletion` call in `services/chatbot-api/chatbot_api/main.py` with a Strands `Agent` using `LiteLLMModel`. The existing `PrismCallback` continues to capture LLM turns transparently.
- [x] Define one or two demo tools (e.g. `now`, `web_search` stub) to exercise the tool path end-to-end.
- [x] `prism_sdk.strands.PrismStrandsHooks`: subscribes to `BeforeToolCallEvent` / `AfterToolCallEvent`, builds `ToolInvocationEvent`, enqueues onto the same `PrismClient` queue.
- [x] Ingestion: extend `/v1/events:batch` to accept both event types, discriminated by `event_type`. Apply redaction to `arguments_preview` / `result_preview`.
- [x] Storage: new `tool_invocations` table (partitioned same as `inference_logs`); `LogStore` gains a `write_tool_events_batch`. Soft FK to `inference_logs.id`.
- [x] `log-writer` worker routes by event_type.
- [x] Resolve the open questions in ADR-0012 (inference_id propagation, large tool-result handling).
- [x] Update ADR-0012 status to **Accepted** with the resolutions inline.
- **Demoable:** chat triggers a tool call; both an `inference_log` row and a correlated `tool_invocations` row land in DB.

### Phase 8 — Polish + submission (½ day)
- [ ] README: setup, architecture overview, schema decisions, tradeoffs, future improvements.
- [ ] Loom demo: chat → log appears → dashboard updates → tool call → tool row appears → kill ingestion → SDK queue resilience → restart → drains.
- [ ] Final pass on all ADRs (any decision that changed during build).

**Estimate remaining from here:** ~½ working day (Phase 8 polish + submission).

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
