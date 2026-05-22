# ADR-0013: Custom dashboards are constrained — whitelisted widgets, no user SQL

- **Status:** Accepted
- **Date:** 2026-05-22

## Context

Phase 8 introduces user-saved dashboards. Cost tracking already shipped in `6be2a04` (`metrics_minute.cost_usd_sum`, `inference_logs.cost_usd`, `GET /v1/conversations/:id/cost`), so the building blocks for cost/usage/latency dashboards exist. The remaining design question is **how much expressive power do we give the dashboard layer**.

Three points on the spectrum were on the table:

1. **Full user SQL.** Dashboards store a SQL string; the server executes it against the read replica. Maximum power, minimum scope discipline. Unbounded query cost, an injection surface that has to be defended explicitly, and a contract that's married to Postgres SQL — directly opposed to the ClickHouse migration seam (ADR-0002, ADR-0005).
2. **User-defined metrics on a constrained DSL.** Some product-aware DSL (predicates + aggregations) compiled server-side. Solves SQL injection but reintroduces unbounded query cost and a parser/validator we'd have to maintain. Overkill for a takehome and still couples the contract to current storage.
3. **Whitelisted metric vocabulary.** Dashboards are a layout of widgets; each widget picks a metric kind from a fixed list and an optional set of dimension filters (model/provider/time range). The server is the only thing that knows how to resolve a metric kind to a query. The dashboard payload contains zero query language.

Constraint context: this is a takehome with no auth in v1 (`owner_id` is `NULL`); we cannot rely on user trust boundaries. The metrics that anyone would actually demo on day one — cost, count, error rate, latency p50/p95, token usage, top-spend conversations — are a small finite set, all already covered by `metrics_minute` plus one narrow drill-down into `inference_logs`.

## Decision

Custom dashboards in v1 are **constrained to a whitelisted metric vocabulary and a fixed widget catalog**, with no user-supplied query language.

### Whitelisted metric kinds (v1)

- `cost_usd_sum`
- `count`
- `error_rate`
- `latency_p50_ms`
- `latency_p95_ms`
- `prompt_tokens_sum`
- `completion_tokens_sum`
- `top_conversations_by_cost`

All read from `metrics_minute` except `top_conversations_by_cost`, which is the only sanctioned drill-down into `inference_logs` (grouped by `conversation_id`, hard `LIMIT` default 10). Dimension filters are restricted to `{model, provider, from, to}` and validated against an allow-list before any SQL is built.

### Widget catalog (v1)

`timeseries`, `bignum`, `table`, `pie`. `(widget_kind, metric_kind)` pairs are validated server-side (e.g. `top_conversations_by_cost` requires `kind=table`; `bignum` requires a scalar-producing metric kind).

### Storage

A single `dashboards` table in Group A (app data): `(id, name, owner_id NULL, layout_jsonb, created_at, updated_at)`. No DB-level FK to `inference_logs` or `metrics_minute` — per ADR-0008, dashboards refer to observability data only through the whitelisted-metric vocabulary, never via joins.

### Explicitly out of scope in v1

- User-supplied SQL or user-defined metrics.
- Alerts, thresholds, notifications.
- Real-time auto-refresh / websockets (manual refresh + configurable poll is the v1 model).
- Multi-tenant authorization (`owner_id` is `NULL` in v1).

### Resolution path

`WidgetResolver` is the single server-side component that maps `(metric_kind, filters)` to a concrete `LogStore.get_metrics(...)` call (or the one drill-down query). The dashboard layout never touches storage directly.

## Consequences

- **+** Query cost is bounded by construction — every metric kind has a known, indexed query shape against `metrics_minute` (or a `LIMIT`-bounded drill-down).
- **+** Zero SQL-injection surface. The dashboard payload is `{kind, metric_kind, filters}`; nothing is templated into SQL.
- **+** Contract is storage-agnostic. When `metrics_minute` moves to a ClickHouse materialized view (ADR-0002), only `WidgetResolver`'s `LogStore` implementation changes; widgets and saved dashboards are untouched.
- **+** Small, finite surface to test: every `(widget_kind, metric_kind)` pair has a deterministic resolver path.
- **−** Users cannot build arbitrary slices of the data — e.g. cost grouped by an arbitrary metadata tag is not possible in v1. Future ADR can extend the vocabulary; widening it is additive and backward-compatible.
- **−** Two layers of validation (layout writer enforces shape, `WidgetResolver` enforces whitelist on read). The duplication is intentional — the resolver is the security boundary; the layout writer is UX.
- **−** `top_conversations_by_cost` is the one place we read raw `inference_logs` from the dashboard path. Bounded by `LIMIT` and the existing `(conversation_id, created_at)` index; flagged here so future-us remembers it's the sanctioned exception, not the start of a pattern.
