# Risks and Tradeoffs

Each row is a known weakness, what mitigates it, and what we accept.

| Risk | Mitigation | What we accept |
|---|---|---|
| SDK fire-and-forget can lose logs on hard crash | Bounded in-memory queue with backoff retry; flush on shutdown via `atexit`; explicit `client.close()` | Logs are observability data, not source of truth. Small loss window is acceptable in exchange for never blocking user latency. (ADR-0009) |
| Postgres won't scale past ~10M log rows/day | Daily partitions, indexes designed for prune-friendly queries; `LogStore` interface pre-wired for ClickHouse swap | Demo scale is fine. Migration is a DI change + backfill, not a refactor. (ADR-0002) |
| PII regex misses non-standard formats | Documented; structure supports plugging in Microsoft Presidio or a model-based redactor as a second pass | Takehome scope. Better than nothing; failure mode is clearly bounded and visible. (ADR-0006) |
| Dashboard could query raw logs at query time | Pre-aggregated `metrics_minute` rolled by a dedicated consumer; dashboard touches only the rollup | Sub-minute granularity is lost in the dashboard. Available via direct `inference_logs` query for debugging. (ADR-0010) |
| Redis Streams loses messages on broker crash | Consumer groups + `XACK` after persistence + `XCLAIM` on timeout + dead-letter stream `inference.dead` | Single-broker demo; production HA needs Redis Cluster or migration to Kafka via the `Bus` interface. (ADR-0003) |
| Streaming responses race with logging | One log per stream completion (success / error / cancelled), with TTFT + total latency | Mid-stream telemetry is not captured per-token (would 100x event volume for little signal). (ADR-0007) |
| Schema drift between SDK and ingestion | `schema_version` field; Pydantic is strict on required fields, lenient on additional ("ignore extra") | Forward-compat: old SDKs work after ingestion adds fields. Back-compat: old ingestion ignores new SDK fields. |
| Provider quirks (token counting, error shapes) | LiteLLM normalizes most of this. Where it can't, we record `null` rather than fabricate values | Some `completion_tokens` may be missing for niche providers; dashboard renders these as "unknown" not "0". |
| Soft FK from `inference_logs.message_id` to `messages.id` can dangle if a conversation is deleted | Intentional. App data can be deleted; observability data must outlive it for audit purposes | Orphan logs are a feature, not a bug. They're queryable via `metadata_jsonb` and `created_at`. |
| Single Postgres is a SPOF for demo | Acknowledged. Demo only. | Production deploy would split app data and logs to separate clusters with different HA policies. |
| `raw_payload_jsonb` bloats the table | Today we write previews + jsonb; rows can be large. Mitigation: don't index jsonb in v1; toast handles compression. Long-term: S3 path. | OK at demo scale. The whole point of `raw_payload_uri` existing today is to make this trivially fixable. (ADR-0002) |
| Ingestion API becomes a bottleneck | Stateless; horizontally scalable behind a load balancer. Heavy work (PII regex, validation) is CPU-bound but cheap | Single instance handles 100s of RPS in demo. Scale-out is a config change. |
| Worker lag during traffic spike | Bounded retry + back-pressure via Redis stream length cap (`MAXLEN ~`) | If logs are arriving faster than we can write, oldest are dropped at the stream level rather than backing up forever. Surfaced as a metric. |

---

## What we would do with more time (README-bound)

1. **ClickHouse + S3 migration** — the seams are ready, this is the natural next step.
2. **Auth + multi-tenancy** — currently single-tenant. Adding an `org_id` to every event + row is mechanical.
3. **Replay & eval** — once `raw_payload` lives in S3, deterministic replay against a held-out test set falls out.
4. **Cost dashboard** — a `cost-calculator` consumer that joins `usage` with a `model_prices` table → `cost_usd` column.
5. **Cancel / list / resume frontend** — wire `AbortController` through SSE → log a `cancelled` status; UI for listing conversations is a 1-day add.
6. **k8s** — Helm chart with separate StatefulSets for PG/Redis, Deployments for stateless services, HPA on ingestion-api and workers.
7. **PII redaction upgrade** — swap regex for Microsoft Presidio or a small model; keep the same interface.
8. **Tracing** — OpenTelemetry across SDK → ingestion → worker → DB. Spans correlate with `inference_id`.
