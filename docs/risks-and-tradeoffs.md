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
| Stored provider credentials add secret-management risk | Encrypt with Fernet using a stable `PRISM_CREDS_KEY`; never return secrets from API; scrub validation errors; no browser `localStorage` or per-request credential headers after Phase 9 | Local single-tenant demo only. No auth boundary exists yet, so the API must not be exposed publicly. Key rotation is future work. (ADR-0014) |
| Losing `PRISM_CREDS_KEY` makes saved credentials undecryptable | Document key generation and require operators to keep it stable across restarts | We do not auto-generate ephemeral keys at boot. Recovery from lost key means deleting/re-entering credentials. |
| Soft FK from `inference_logs.message_id` to `messages.id` can dangle if a conversation is deleted | Intentional. App data can be deleted; observability data must outlive it for audit purposes | Orphan logs are a feature, not a bug. They're queryable via `metadata_jsonb` and `created_at`. |
| Single Postgres is a SPOF for demo | Acknowledged. Demo only. | Production deploy would split app data and logs to separate clusters with different HA policies. |
| `raw_payload_jsonb` bloats the table | Today we write previews + jsonb; rows can be large. Mitigation: don't index jsonb in v1; toast handles compression. Long-term: S3 path. | OK at demo scale. The whole point of `raw_payload_uri` existing today is to make this trivially fixable. (ADR-0002) |
| Ingestion API becomes a bottleneck | Stateless; horizontally scalable behind a load balancer. Heavy work (PII regex, validation) is CPU-bound but cheap | Single instance handles 100s of RPS in demo. Scale-out is a config change. |
| Worker lag during traffic spike | Bounded retry + back-pressure via Redis stream length cap (`MAXLEN ~ 1_000_000`) on `XADD` | Trades durability for bounded memory: during a long worker/DB outage Redis silently trims unprocessed events to keep the stream under the cap. The system fails *bounded-memory but lossy*. Production should pair this with object spool / Kafka for durable replay and expose the trim count as a metric. |
| SDK on-disk durability — process crash loses queued events | `atexit` + `prism_client.close()` from chatbot-api lifespan flushes on graceful shutdown. No disk spool; `flush_interval_ms` (200ms default) is the loss window on `kill -9` | Logs are observability; ADR-0009 explicitly trades on-disk durability for never blocking user latency. Production would add a local spool. |
| `Bus` abstraction is Redis-shaped (consumer groups, stream IDs, `XCLAIM`-on-timeout) | True. The seam gives a *worker-side* swap (different stream impl, same consumer-loop code) | A real Kafka/Pulsar migration would re-shape the interface around offsets/partitions/keys. The abstraction is honest about its scope, not a universal queue. |
| Bignum latency p50/p95 widgets used to average per-minute percentiles, which is mathematically wrong | `LogStore.get_log_percentile` computes the true percentile across the window directly from `inference_logs` via `percentile_cont` (ClickHouse swap maps onto `quantile()`). Per-minute *timeseries* still come from the rollup — that's exactly what the y-axis shows | Timeseries widgets plot per-bucket percentile and are correct under that contract. Cross-bucket aggregations always use the raw-log path. |
| Metrics-roller idempotency across multiple workers | Roller UPSERT is keyed on `(minute_bucket, model, provider)` so concurrent writes for the same bucket converge. `metrics-reconciler` periodically replaces rollup rows from raw logs to correct any late-event drift | Per-second granularity is not promised; bucket arithmetic is eventually-consistent within the reconciler interval. |
| No per-tenant/per-provider rate limits, budget enforcement, or response caching | Out of scope for the takehome. Hook points: `PrismClient` flusher (SDK-side), ingestion middleware (server-side), `LogStore` reads (budget gating) | Demo is single-tenant. Production would gate at all three layers. |
| Healthchecks are shallow; no queue-lag/dropped-event/worker-heartbeat metrics | `/healthz` returns `ok` without probing deps | Out of scope. Real ops would expose `XPENDING`/lag, dropped-event counters, ingestion-rejection alarms, and SLO tracking. |
| No auth on chatbot/ingestion/credential APIs | Bound to `127.0.0.1` in compose; demo is single-tenant by design (`docs/implementation-plan.md` §1) | Do not expose the API to the public internet without an auth layer. |

---

## What we would do with more time (README-bound)

1. **ClickHouse + S3 migration** — the seams are ready, this is the natural next step.
2. **Auth + multi-tenancy** — currently single-tenant. Adding an `org_id` to every event + row is mechanical.
3. **Replay & eval** — once `raw_payload` lives in S3, deterministic replay against a held-out test set falls out.
4. **Cost dashboard** — a `cost-calculator` consumer that joins `usage` with a `model_prices` table → `cost_usd` column.
5. **Auth + credential ownership** — Phase 9 keeps credentials single-tenant. Production needs org/user ownership, audit logs, and key rotation.
6. **k8s** — Helm chart with separate StatefulSets for PG/Redis, Deployments for stateless services, HPA on ingestion-api and workers.
7. **PII redaction upgrade** — swap regex for Microsoft Presidio or a small model; keep the same interface.
8. **Tracing** — OpenTelemetry across SDK → ingestion → worker → DB. Spans correlate with `inference_id`.
