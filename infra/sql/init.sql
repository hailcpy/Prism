CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
CREATE TYPE message_status AS ENUM ('pending', 'ok', 'error', 'cancelled');

CREATE TYPE inference_status AS ENUM ('ok', 'error', 'timeout', 'cancelled');

CREATE TYPE tool_invocation_status AS ENUM ('ok', 'error');

CREATE TABLE conversations (
  id UUID PRIMARY KEY,
  user_id UUID NULL,
  model_default TEXT NOT NULL,
  system_prompt TEXT,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_user_updated_idx
  ON conversations (user_id, updated_at DESC);

CREATE TABLE messages (
  id UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role message_role NOT NULL,
  status message_status NOT NULL DEFAULT 'ok',
  content TEXT NOT NULL,
  metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_conv_created_idx
  ON messages (conversation_id, created_at);

CREATE TABLE provider_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  secrets_enc BYTEA NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  last_tested_at TIMESTAMPTZ,
  last_test_ok BOOLEAN,
  last_test_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, name)
);

CREATE UNIQUE INDEX provider_credentials_one_default_per_provider
  ON provider_credentials (provider)
  WHERE is_default;

CREATE TABLE inference_logs (
  id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  ts_start TIMESTAMPTZ NOT NULL,
  ts_end TIMESTAMPTZ NOT NULL,
  conversation_id UUID,
  message_id UUID,
  model TEXT NOT NULL,
  provider TEXT NOT NULL,
  status inference_status NOT NULL,
  error_type TEXT,
  error_message TEXT,
  provider_error_code TEXT,
  latency_ms INT NOT NULL CHECK (latency_ms >= 0),
  ttft_ms INT CHECK (ttft_ms IS NULL OR ttft_ms >= 0),
  prompt_tokens INT CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
  completion_tokens INT CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
  total_tokens INT CHECK (total_tokens IS NULL OR total_tokens >= 0),
  cached_prompt_tokens INT CHECK (cached_prompt_tokens IS NULL OR cached_prompt_tokens >= 0),
  reasoning_tokens INT CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
  cost_usd DOUBLE PRECISION CHECK (cost_usd IS NULL OR cost_usd >= 0),
  prompt_preview TEXT,
  response_preview TEXT,
  raw_payload_uri TEXT,
  raw_payload_jsonb JSONB,
  metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
  sdk_version TEXT,
  schema_version TEXT NOT NULL,
  PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('inference_logs', by_range('created_at', INTERVAL '1 day'));

CREATE INDEX inference_logs_created_idx ON inference_logs (created_at);
CREATE INDEX inference_logs_model_created_idx ON inference_logs (model, provider, created_at);
CREATE INDEX inference_logs_conv_created_idx ON inference_logs (conversation_id, created_at);
CREATE INDEX inference_logs_errors_idx
  ON inference_logs (status, created_at) WHERE status <> 'ok';

CREATE TABLE tool_invocations (
  id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  ts_start TIMESTAMPTZ NOT NULL,
  ts_end TIMESTAMPTZ NOT NULL,
  conversation_id UUID,
  inference_id UUID,
  tool_name TEXT NOT NULL,
  status tool_invocation_status NOT NULL,
  error_type TEXT,
  error_message TEXT,
  latency_ms INT NOT NULL CHECK (latency_ms >= 0),
  arguments_preview TEXT NOT NULL,
  result_preview TEXT,
  metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
  sdk_version TEXT,
  schema_version TEXT NOT NULL,
  PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('tool_invocations', by_range('created_at', INTERVAL '1 day'));

CREATE INDEX tool_invocations_created_idx ON tool_invocations (created_at);
CREATE INDEX tool_invocations_tool_created_idx ON tool_invocations (tool_name, created_at);
CREATE INDEX tool_invocations_inference_created_idx
  ON tool_invocations (inference_id, created_at);
CREATE INDEX tool_invocations_errors_idx
  ON tool_invocations (status, created_at) WHERE status <> 'ok';

CREATE MATERIALIZED VIEW metrics_minute
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
  time_bucket('1 minute', created_at) AS minute_bucket,
  model,
  provider,
  count(*)::int AS count,
  count(*) FILTER (WHERE status <> 'ok')::int AS error_count,
  COALESCE(sum(prompt_tokens), 0)::bigint AS prompt_tokens_sum,
  COALESCE(sum(completion_tokens), 0)::bigint AS completion_tokens_sum,
  COALESCE(sum(cost_usd), 0)::double precision AS cost_usd_sum
FROM inference_logs
GROUP BY minute_bucket, model, provider
WITH NO DATA;

SELECT add_continuous_aggregate_policy('metrics_minute',
  start_offset    => INTERVAL '15 minutes',
  end_offset      => INTERVAL '1 minute',
  schedule_interval => INTERVAL '5 minutes');

SELECT add_retention_policy('inference_logs', INTERVAL '30 days');
SELECT add_retention_policy('tool_invocations', INTERVAL '30 days');

ALTER TABLE inference_logs SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'model, provider',
  timescaledb.compress_orderby = 'created_at DESC');
SELECT add_compression_policy('inference_logs', INTERVAL '7 days');

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
