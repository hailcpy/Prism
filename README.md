# Prism

A lightweight inference logging + chat system: a Next.js chatbot, a Python SDK that wraps LLM calls, an event-driven ingestion pipeline, and dashboards.

---

## Setup

One command from a fresh clone:

```bash
make up
```

That runs `scripts/bootstrap_env.py` (copies `.env.example` → `.env`, generates `REDIS_PASSWORD` and `PRISM_CREDS_KEY`) and then `docker compose up -d --build`. Provider API keys are added through the in-app **Settings** UI — no env-var editing required.

| Surface | URL |
|---|---|
| Chatbot UI | http://localhost:3001 |
| Metrics dashboard | http://localhost:3001/metrics |
| Chatbot API docs | http://localhost:8100/docs |
| Ingestion API docs | http://localhost:8101/docs |

Other useful targets: `make down`, `make logs`, `make psql`, `make check` (lint + typecheck + tests).

---

## Capabilities

Everything in the assignment's bonus list:

| Bonus | Where it lives |
|---|---|
| Multi-provider support | OpenAI / Anthropic / Gemini via LiteLLM (`sdk/`) — picker on every chat turn |
| Streaming responses | SSE end-to-end; tokens stream through chatbot-api into the UI |
| Latency + throughput + error dashboards | `/metrics` page reads `metrics_minute` rollups; configurable dashboards under `/dashboards` |
| Docker Compose one-command setup | `make up` |
| Event-based architecture | Redis Streams (`inference.logged`) with two independent consumer groups |
| PII redaction | At the ingestion trust boundary, before anything hits the bus |
| Self-hosted k8s deploy | Stateless services + named-volume datastores — compose maps 1:1 to Deployments + StatefulSets; HPA-friendly. The Helm chart is the only piece not in the repo (see _Future improvements_). |
| Frontend | Next.js app with **cancel** (in-flight `AbortController` → server `cancelled` status), **list conversations** (sidebar), and **resume conversation** (clicking any item rehydrates message history and continues streaming) |

---

## Architecture overview

Five processes plus the UI. Chat traffic and observability traffic share no synchronous path — the SDK is fire-and-forget over HTTP, and the only place the two meet is a soft FK column.

```mermaid
flowchart LR
  UI[Next.js Chatbot UI<br/>cancel · list · resume]
  CAPI[Chatbot API<br/>FastAPI + prism-sdk]
  LLM[LiteLLM → OpenAI / Anthropic / Gemini]
  ING[Ingestion API<br/>PII redaction · validation]
  R[(Redis Streams<br/>inference.logged)]
  W[log-writer<br/>cg-writer]
  M[metrics-roller<br/>cg-roller]
  REC[metrics-reconciler<br/>cron]
  PG[(Postgres<br/>app · logs · rollups)]

  UI -- SSE --> CAPI
  CAPI -- stream --> LLM
  CAPI -. fire-and-forget .-> ING
  ING -- XADD --> R
  R --> W --> PG
  R --> M --> PG
  REC -- recompute from raw logs --> PG
```

- **Chatbot API** is the only thing that talks to LiteLLM; the SDK is in-process inside it.
- **Ingestion API** is the trust boundary — PII redaction and validation happen here, before publish. Nothing past the bus ever sees an unredacted prompt or raw payload.
- **Two consumer groups** (`cg-writer`, `cg-roller`) read the same stream independently; adding a third (cost calculator, deep PII scan, eval sampler) is a new consumer, not a writer change. That's the event-driven story.
- **metrics-reconciler** is a cron that recomputes the last N minutes from `inference_logs` and `UPSERT`-replaces them into `metrics_minute`. The roller is the hot path; the reconciler is the source of truth. Both are idempotent.

More detail in [`docs/architecture.md`](docs/architecture.md).

---

## Schema design decisions

The whole schema is organized around one invariant: **app data and observability data must never join on the write path.**

| Group | Tables | Workload | Today | Tomorrow |
|---|---|---|---|---|
| A. App data | `conversations`, `messages`, `provider_credentials` | OLTP, mutable, low volume | Postgres | Postgres (unchanged) |
| B. Inference logs | `inference_logs` (daily-partitioned), `tool_invocations` | OLAP, append-only, high volume | Postgres | ClickHouse + S3 for raw payloads |
| C. Rollups | `metrics_minute` | Read-optimized, denormalized | Postgres | ClickHouse materialized view |

Concrete decisions and why:

- **No FKs across groups.** `inference_logs.message_id` is a soft reference. Any group can move to a different store without unwinding constraints, and deleting an app-side conversation does not nuke audit history.
- **Daily range-partitioned logs.** `inference_logs` is `PARTITION BY RANGE (created_at)`; PK is `(id, created_at)` because Postgres requires the partition key in every unique index. A `partition-cron` creates tomorrow's partition nightly and drops anything past the retention window.
- **`raw_payload_uri` exists on day one** alongside `raw_payload_jsonb`. Writes go to jsonb today; readers check `if uri: fetch_from(uri) else: read_jsonb`. The S3 switch is a write-path change with zero reader churn.
- **Three timestamps per log row.** `ts_start`/`ts_end` come from the SDK (authoritative for latency, subject to client clock skew); `created_at` is set by the ingestion API and is what we partition + dashboard on (consistent server clock).
- **Rollups are REPLACE, not increment.** The roller's UPSERT writes the full bucket value, so worker crash + replay produces the same row, not a doubled one. The reconciler can overwrite without coordination.
- **Pre-aggregated metrics for the dashboard.** The `/metrics` page reads `metrics_minute`, not raw logs. Cross-bucket percentile widgets (which can't be averaged from per-minute percentiles without lying) read raw logs through a typed `LogStore` method that maps to `percentile_cont` today and `quantile()` on ClickHouse later.
- **Storage interfaces, not direct drivers.** All log access goes through `LogStore` / `RawPayloadStore` / `Bus` interfaces. No `psycopg`/`redis-py`/`boto3` imports outside `infra/storage/` and `infra/bus/`. The interfaces buy "no caller refactor" on migration — they don't paper over real query-semantic differences (PG `ON CONFLICT` → ClickHouse `ReplacingMergeTree`'s eventual dedupe; exact `percentile_cont` → approximate `quantile()`; Redis Streams' `XCLAIM` → Kafka offsets). The cutover is a design exercise, not a DI swap.
- **Provider credentials encrypted at rest.** Fernet over a stable `PRISM_CREDS_KEY`; API responses never include decrypted secrets; no browser `localStorage`.

---

## Tradeoffs and future improvements

Everything below is a known limit we shipped on purpose — with the mitigation that's already in the code and the next step we'd take.

| Area | What we accept today | Why it's fine here | What we'd do next |
|---|---|---|---|
| **Single Postgres for app + logs + rollups** | One DB is a SPOF | Demo scale; interfaces hide the storage | Split logs → ClickHouse, raw payloads → S3 via the existing `LogStore` swap |
| **SDK is fire-and-forget, no on-disk spool** | Bounded queue + `atexit` flush; `kill -9` loses ≤200ms of events | Logs are observability, not source of truth; never block user latency | Local disk spool + replay-on-restart |
| **PII regex** (email/phone/SSN/credit-card) | Misses non-standard formats | Better than nothing; failure is bounded and visible | Plug Microsoft Presidio behind the same `Redactor` interface (`PRISM_REDACTOR=presidio`) |
| **Redis Streams capped with `MAXLEN ~ 1_000_000`** | During a long worker outage Redis silently trims | Bounded memory > unbounded backlog at demo scale | Pair with object spool / Kafka via the `Bus` interface; expose trim count as a metric. Note: Redis Cluster does *not* automatically scale one logical stream — sharding `inference.logged.{0..N}` by key is the prerequisite, not a free win |
| **Rollups are eventually-consistent** | Late events bypass the in-memory aggregator | The reconciler corrects within 5 min | Tighten reconciler window; promote it to the only writer once ClickHouse lands |
| **Single `metrics-roller` per bucket** | REPLACE-shaped UPSERT is only safe with one owner per `(bucket, model, provider)` | One roller easily handles demo load | Shard the stream by `(model, provider)` / `hash(conversation_id)` and run one roller per shard — naive horizontal scale-out would silently drop partial aggregates until the reconciler catches up |
| **Streaming logs once at completion**, not per-token | No mid-stream stall detection, no inter-token throughput, no partial-output debug on cancels | Per-token logging would 100× event volume; TTFT + total latency cover the perceived-UX signal | Sampled per-token capture as a *new* event type + table (not an extension of `inference_logs`) |
Add `org_id` to every log/credential/rollup row + an auth layer. Mechanical at the schema level, but `metrics_minute`'s PK and every dashboard query also need the tenant column |
| **`PRISM_KEEP_RAW=true`** persists redacted raw payloads in jsonb | TOAST compression handles it at demo scale | The `raw_payload_uri` column is already wired | Flip writes to S3, keep `raw_payload_jsonb` NULL |
| **Shallow healthchecks** | `/healthz` returns `ok` without probing deps | OK for demo | Expose `XPENDING`/lag, dropped-event counters, ingestion-rejection alarms, SLO tracking |
| **Tracing** | Structured logs only | Sufficient to debug the demo | OpenTelemetry across SDK → ingestion → worker → DB, correlated by `inference_id` |

See [`docs/architecture.md`](docs/architecture.md) for ingestion flow, logging strategy, scaling considerations, and failure handling assumptions.
