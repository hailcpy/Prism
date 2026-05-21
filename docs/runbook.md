# Runbook

How to run, operate, and verify Prism locally. Read alongside [`architecture.md`](architecture.md).

## Prerequisites

- Docker + Docker Compose
- Python tooling: `uv`
- Node.js + npm
- Provider API keys (at least one): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

## Quick start

```bash
cp .env.example .env
# edit .env to fill in at least one provider key
make up
# open http://localhost:3000 (chatbot UI)
# open http://localhost:3000/metrics (dashboard)
```

`make up` is equivalent to `docker compose up -d --build`.

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | one of | — | Used by LiteLLM for `gpt-*` models |
| `ANTHROPIC_API_KEY` | one of | — | Used by LiteLLM for `claude-*` models |
| `GEMINI_API_KEY` | one of | — | Used by LiteLLM for `gemini-*` models |
| `DATABASE_URL` | yes | `postgres://prism:prism@postgres:5432/prism` | Wired by Compose |
| `REDIS_URL` | yes | `redis://redis:6379/0` | Wired by Compose |
| `INGESTION_URL` | yes | `http://ingestion:8001` | What the SDK posts to |
| `PRISM_KEEP_RAW` | no | `false` | If `true`, ingestion redacts and persists the full `raw_payload` (debug only — logs a warning at startup). When `false`, `raw_payload` is dropped at ingestion; only redacted previews persist. See ADR-0006. |
| `PRISM_LOG_LEVEL` | no | `INFO` | Standard Python log level |
| `PARTITION_RETENTION_DAYS` | no | `30` | partition-cron drops older partitions |

## Make targets

| Target | What it does |
|---|---|
| `make install-dev` | Install Python workspace packages and dev tools using `uv sync --all-packages --dev` |
| `make up` | Build + start all services |
| `make down` | Stop + remove containers |
| `make logs` | Tail logs from all services |
| `make logs SERVICE=ingestion-api` | Tail a single service |
| `make psql` | Open a psql shell into the Postgres container |
| `make redis-cli` | Open redis-cli |
| `make seed` | Insert a few sample conversations + logs for dashboard demo |
| `make lint` | Run Ruff for Python and Next ESLint for the web app |
| `make format` | Format Python with Ruff and web files with Prettier |
| `make format-check` | Verify Python and web formatting without rewriting files |
| `make typecheck` | Run Python `ty` and TypeScript `tsc --noEmit` |
| `make test` | Run pytest |
| `make check` | Run the full local quality gate: lint, format-check, typecheck, test |
| `make demo` | `make up` + open UI and dashboard in browser |

## Quality checks

Run this before declaring code changes done:

```bash
make check
```

The Python workspace is managed by `uv`; the root `uv.lock` is the canonical Python lockfile. The web app uses `package-lock.json`. Use `make format` to apply Ruff and Prettier formatting before running the full check.

## Services and ports

| Service | Port | Health endpoint |
|---|---|---|
| `chatbot-ui` | 3000 | `/` |
| `chatbot-api` | 8000 | `/healthz` |
| `ingestion-api` | 8001 | `/healthz` |
| `postgres` | 5432 | `pg_isready` via Compose healthcheck |
| `redis` | 6379 | `PING` via Compose healthcheck |
| `log-writer` | — | Logs heartbeat every 30s |
| `metrics-roller` | — | Logs heartbeat every 30s |
| `metrics-reconciler` | — | Logs after each run (every 5 min) |
| `partition-cron` | — | Logs after each run |

## End-to-end smoke / demo script

This is also the verification script and the Loom outline.

1. `make up` → wait for all services to report healthy in `make logs`.
2. Open the chatbot UI. Have a 3-turn streaming conversation against `gpt-4o`.
3. Switch model to `claude-sonnet-4-6`. Have another 3-turn conversation.
4. `make psql`, then `SELECT count(*), model FROM inference_logs GROUP BY model;` — expect rows split across both models.
5. `curl http://localhost:8000/v1/metrics?from=...` — returns rollup rows for both models.
6. Open dashboard — latency / throughput / errors charts populated for both models.
7. **Failure test:** `docker compose stop ingestion-api`. Send 5 more chat messages. Chatbot continues to work (SDK queues events). `docker compose start ingestion-api`. Within 5s, queue drains; new rows in `inference_logs`.
8. **PII test:** send `"my email is foo@example.com and SSN 123-45-6789"`. `SELECT prompt_preview FROM inference_logs ORDER BY created_at DESC LIMIT 1;` — preview is redacted.
9. **Restart test:** `make down && make up`. Existing conversations load in the UI. No data loss.

All 9 steps pass → ready to submit.

## Common operations

**Inspect the event stream**
```
make redis-cli
> XLEN inference.logged
> XRANGE inference.logged - + COUNT 5
> XINFO GROUPS inference.logged
```

**Replay the dead-letter stream**
```
> XLEN inference.dead
> XRANGE inference.dead - + COUNT 10
# manually re-XADD any salvageable entries to inference.logged
```

**Force a metrics roll**
The roller flushes on 60s windows. To trigger immediately, `docker compose restart metrics-roller` — pending in-memory state is flushed on graceful shutdown.

**Drop a stuck partition**
```
DROP TABLE inference_logs_20260301;   -- partition for 2026-03-01
```
Partition-cron will not recreate past partitions.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Chatbot returns 5xx | Provider key missing or invalid | Check `.env`; `make logs SERVICE=chatbot-api` |
| Dashboard empty | metrics-roller hasn't flushed yet (60s window) or no traffic | Wait, or check `SELECT * FROM metrics_minute ORDER BY minute_bucket DESC LIMIT 5;` |
| `inference_logs` empty but chat works | Ingestion or writer down | `docker compose ps`, then `make logs SERVICE=ingestion-api` and `SERVICE=log-writer` |
| Redis stream growing without bound | Writer consumer lag | Check writer heartbeat; restart if hung; inspect `XINFO GROUPS inference.logged` for pending entries |
