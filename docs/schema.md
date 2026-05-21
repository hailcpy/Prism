# Schema Design

## Three table groups

| Group | Tables | Workload | Today | Future |
|---|---|---|---|---|
| A. App data | `conversations`, `messages` | OLTP, mutable, low volume | Postgres | Postgres (unchanged) |
| B. Inference logs | `inference_logs` | OLAP, append-only, high volume | Postgres (partitioned) | ClickHouse + S3 (raw payloads) |
| C. Rollups | `metrics_minute` | Read-optimized, denormalized | Postgres | ClickHouse materialized view |

**No DB-level FK across groups.** Links between groups (e.g. `inference_logs.message_id` → `messages.id`) are soft references. This is the load-bearing decision that allows any group to migrate independently. See ADR-0008.

---

## DDL (representative — see `infra/sql/` for canonical)

### Group A — App data

```sql
CREATE TABLE conversations (
  id              UUID PRIMARY KEY,
  user_id         UUID NULL,                    -- nullable; no auth in scope
  model_default   TEXT NOT NULL,
  system_prompt   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_user_updated_idx
  ON conversations (user_id, updated_at DESC);

CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');

CREATE TABLE messages (
  id              UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            message_role NOT NULL,
  content         TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_conv_created_idx
  ON messages (conversation_id, created_at);
```

### Group B — Inference logs

```sql
CREATE TYPE inference_status AS ENUM ('ok', 'error', 'timeout', 'cancelled');

CREATE TABLE inference_logs (
  id                   UUID NOT NULL,             -- uuid7 (time-orderable)
  created_at           TIMESTAMPTZ NOT NULL,      -- ingestion-side wall clock (server time)
  ts_start             TIMESTAMPTZ NOT NULL,      -- SDK-side, request initiation
  ts_end               TIMESTAMPTZ NOT NULL,      -- SDK-side, response complete (or error/cancel)
  conversation_id      UUID,                      -- soft FK
  message_id           UUID,                      -- soft FK; links to the ASSISTANT message
  model                TEXT NOT NULL,
  provider             TEXT NOT NULL,
  status               inference_status NOT NULL,
  error_type           TEXT,
  error_message        TEXT,
  provider_error_code  TEXT,
  latency_ms           INT NOT NULL CHECK (latency_ms >= 0),
  ttft_ms              INT          CHECK (ttft_ms IS NULL OR ttft_ms >= 0),
  prompt_tokens        INT          CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
  completion_tokens    INT          CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
  total_tokens         INT          CHECK (total_tokens IS NULL OR total_tokens >= 0),
  prompt_preview       TEXT,                      -- redacted, <= 500 chars
  response_preview     TEXT,                      -- redacted, <= 500 chars
  raw_payload_uri      TEXT,                      -- 's3://...' when S3 lands; NULL today
  raw_payload_jsonb    JSONB,                     -- present only when PRISM_KEEP_RAW=true; redacted; NULL otherwise
  metadata_jsonb       JSONB NOT NULL DEFAULT '{}'::jsonb,
  sdk_version          TEXT,
  schema_version       TEXT NOT NULL,
  -- Partitioned tables in Postgres REQUIRE the partition key in every unique/PK index.
  -- (id, created_at) is the natural composite: id is unique in practice (uuid7),
  -- and created_at is the partition key.
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX inference_logs_created_idx          ON inference_logs (created_at);
CREATE INDEX inference_logs_model_created_idx    ON inference_logs (model, provider, created_at);
CREATE INDEX inference_logs_conv_created_idx     ON inference_logs (conversation_id, created_at);
CREATE INDEX inference_logs_errors_idx
  ON inference_logs (status, created_at) WHERE status <> 'ok';
```

**Why three timestamps:**
- `ts_start` / `ts_end` — SDK-observed wall clock around the LLM call. Authoritative for latency analysis (`ts_end - ts_start ≈ latency_ms`, modulo clock skew).
- `created_at` — set by the ingestion API when the event is received. Authoritative for partitioning, retention, and dashboard time-range queries (consistent server clock, no SDK clock-skew issues).

**Partition strategy:** daily partitions named `inference_logs_YYYYMMDD`, range-partitioned by `created_at`. A `partition-cron` container creates tomorrow's partition nightly and drops partitions older than the retention window (default 30d; configurable). Partition pruning keeps queries fast as volume grows.

**Dedupe:** the `log-writer` worker writes with `INSERT ... ON CONFLICT (id, created_at) DO NOTHING`, so replays from `XCLAIM`/dead-letter recovery never produce duplicate rows. This is the load-bearing property the metrics reconciler depends on.

### Group C — Rollups

```sql
CREATE TABLE metrics_minute (
  minute_bucket          TIMESTAMPTZ NOT NULL,
  model                  TEXT NOT NULL,
  provider               TEXT NOT NULL,
  count                  INT NOT NULL,
  error_count            INT NOT NULL,
  latency_p50_ms         INT NOT NULL,
  latency_p95_ms         INT NOT NULL,
  prompt_tokens_sum      BIGINT NOT NULL,
  completion_tokens_sum  BIGINT NOT NULL,
  PRIMARY KEY (minute_bucket, model, provider)
);

CREATE INDEX metrics_minute_bucket_idx ON metrics_minute (minute_bucket DESC);
```

**Why precomputed**: dashboard queries are aggregation-heavy and frequent. Running them against the raw partitioned table on every page load is wasteful; ClickHouse migration is then incremental. (ADR-0010)

---

## The migration seam (PG → PG + ClickHouse + S3)

This is the part reviewers should look at carefully.

### Writes
The split between `LogStore` and `RawPayloadStore` is by *responsibility*, not by table:

- `RawPayloadStore.put(inference_id, redacted_payload) -> (uri | None, embedded_jsonb | None)` is called **first**, by the ingestion API (before publish) when `PRISM_KEEP_RAW=true`. It returns *either* a URI to attach to the log row *or* an inline jsonb blob to attach to the log row — never both, never neither.
  - Today (`JsonbRawPayloadStore`): returns `(None, redacted_jsonb)`.
  - Future (`S3RawPayloadStore`): writes the payload to S3 and returns `(s3://..., None)`.
  - Default (raw not kept): the store is not called at all; both columns are `NULL`.
- The returned `(uri, jsonb)` pair is then attached to the `InferenceEvent` as `raw_payload_uri` / `raw_payload_jsonb` and flows through the bus to `log-writer`, which writes the whole row via `LogStore.write_logs_batch()`.
- `metrics_minute` is written only via `LogStore.upsert_metrics()` (REPLACE semantics, see Stage 3/4 in `architecture.md`).

This way `LogStore` always writes a complete row including whatever payload-pointer columns it received, and `RawPayloadStore` owns the *materialization* of the payload (jsonb today, S3 tomorrow) without splitting the row write across two stores.

### Reads
- Every dashboard / debugging query goes through `LogStore.get_metrics(...)` / `LogStore.get_logs(...)`.
- Raw payload reads go through `RawPayloadStore.get(uri_or_jsonb)`. The store transparently reads from S3 if a URI is set, otherwise returns the inline jsonb. Callers don't branch on storage backend.

### What changes on migration day
1. New `ClickHouseLogStore` and `S3RawPayloadStore` implementations land.
2. DI container swaps `PostgresLogStore` → `ClickHouseLogStore`.
3. Backfill job re-reads PG and writes to CH.
4. Cutover. No business logic touched.

### What we explicitly do NOT do today
- Foreign keys from `inference_logs` to `messages`. (Would have to be dropped for migration.)
- Joins between groups in SQL. (All cross-group reads are done in app code or skipped entirely.)
- `ON DELETE CASCADE` from app data to logs. (Logs are immutable history; cascading deletes would corrupt audit semantics anyway.)

---

## Capacity notes

- At ~10k inference logs/day, single PG handles this comfortably for years.
- At ~10M/day, partition pruning still helps, but rollup queries on the hot partition get expensive. That's the migration trigger.
- `metrics_minute` is tiny: ~1440 rows/day per `(model, provider)` pair. Stays in PG indefinitely.
