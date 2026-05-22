-- Idempotent catch-up migrations. Runs on every chatbot-api boot so old
-- Postgres volumes self-heal to the current schema. Every statement here must
-- be safe to execute repeatedly. Add new schema additions here AND in
-- init.sql; init.sql is for fresh volumes (docker-entrypoint-initdb.d),
-- migrations.sql is for everyone else.

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE inference_logs
  ADD COLUMN IF NOT EXISTS cached_prompt_tokens INT,
  ADD COLUMN IF NOT EXISTS reasoning_tokens INT,
  ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION;

ALTER TABLE metrics_minute
  ADD COLUMN IF NOT EXISTS cost_usd_sum DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS dashboards (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  owner_id UUID NULL,
  layout_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dashboards_owner_id_idx ON dashboards (owner_id);
CREATE INDEX IF NOT EXISTS dashboards_updated_at_idx ON dashboards (updated_at DESC);
