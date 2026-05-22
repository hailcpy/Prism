CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');

CREATE TYPE inference_status AS ENUM ('ok', 'error', 'timeout', 'cancelled');

CREATE TYPE tool_invocation_status AS ENUM ('ok', 'error');

CREATE TABLE conversations (
  id UUID PRIMARY KEY,
  user_id UUID NULL,
  model_default TEXT NOT NULL,
  system_prompt TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_user_updated_idx
  ON conversations (user_id, updated_at DESC);

CREATE TABLE messages (
  id UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role message_role NOT NULL,
  content TEXT NOT NULL,
  metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_conv_created_idx
  ON messages (conversation_id, created_at);

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
  prompt_preview TEXT,
  response_preview TEXT,
  raw_payload_uri TEXT,
  raw_payload_jsonb JSONB,
  metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
  sdk_version TEXT,
  schema_version TEXT NOT NULL,
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE inference_logs_default PARTITION OF inference_logs DEFAULT;

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
) PARTITION BY RANGE (created_at);

CREATE TABLE tool_invocations_default PARTITION OF tool_invocations DEFAULT;

CREATE INDEX tool_invocations_created_idx ON tool_invocations (created_at);
CREATE INDEX tool_invocations_tool_created_idx ON tool_invocations (tool_name, created_at);
CREATE INDEX tool_invocations_inference_created_idx
  ON tool_invocations (inference_id, created_at);
CREATE INDEX tool_invocations_errors_idx
  ON tool_invocations (status, created_at) WHERE status <> 'ok';

CREATE TABLE metrics_minute (
  minute_bucket TIMESTAMPTZ NOT NULL,
  model TEXT NOT NULL,
  provider TEXT NOT NULL,
  count INT NOT NULL,
  error_count INT NOT NULL,
  latency_p50_ms INT NOT NULL,
  latency_p95_ms INT NOT NULL,
  prompt_tokens_sum BIGINT NOT NULL,
  completion_tokens_sum BIGINT NOT NULL,
  PRIMARY KEY (minute_bucket, model, provider)
);

CREATE INDEX metrics_minute_bucket_idx ON metrics_minute (minute_bucket DESC);

CREATE OR REPLACE FUNCTION ensure_inference_logs_partition(partition_day DATE)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  partition_name TEXT := format('inference_logs_%s', to_char(partition_day, 'YYYYMMDD'));
  from_ts TEXT := partition_day::TEXT;
  to_ts TEXT := (partition_day + 1)::TEXT;
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF inference_logs FOR VALUES FROM (%L) TO (%L)',
    partition_name,
    from_ts,
    to_ts
  );
END;
$$;

CREATE OR REPLACE FUNCTION ensure_tool_invocations_partition(partition_day DATE)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  partition_name TEXT := format('tool_invocations_%s', to_char(partition_day, 'YYYYMMDD'));
  from_ts TEXT := partition_day::TEXT;
  to_ts TEXT := (partition_day + 1)::TEXT;
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF tool_invocations FOR VALUES FROM (%L) TO (%L)',
    partition_name,
    from_ts,
    to_ts
  );
END;
$$;

SELECT ensure_inference_logs_partition(CURRENT_DATE);
SELECT ensure_inference_logs_partition(CURRENT_DATE + 1);
SELECT ensure_tool_invocations_partition(CURRENT_DATE);
SELECT ensure_tool_invocations_partition(CURRENT_DATE + 1);
