# API Contracts

All HTTP and event-bus contracts. Versioned via URL prefix (`/v1`) for HTTP and `schema_version` field for events.

---

## SDK public API

```python
from olive_sdk import OliveClient

client = OliveClient(
    ingestion_url="http://ingestion:8001",
    api_key=None,                 # reserved for future auth
    sink="http",                  # "http" | "noop" | "stdout"
    flush_interval_ms=200,
    queue_max=10_000,
    on_drop="log",                # "log" | "raise"
)

# Non-streaming
resp = client.chat.completions.create(
    model="gpt-4o",               # any LiteLLM-supported model
    messages=[{"role": "user", "content": "hi"}],
    conversation_id="uuid",       # required
    message_id="uuid",            # required — links inference to a chat message
    metadata={"user_id": "..."},  # arbitrary tags, stored in metadata_jsonb
)
# resp matches the LiteLLM/OpenAI shape

# Streaming
async for chunk in client.chat.completions.stream(
    model="claude-sonnet-4-6",
    messages=[...],
    conversation_id="uuid",
    message_id="uuid",
):
    yield chunk
# SDK emits ONE InferenceEvent at stream completion (success, error, or cancelled).
```

**Lifecycle:** `OliveClient.close()` flushes the in-memory queue synchronously. Also registered via `atexit`.

---

## SDK → Ingestion

### `POST /v1/events:batch`

**Request**

`schema_version` is a per-event field. The batch envelope is intentionally trivial so different SDK versions can coexist in one batch:

```json
{
  "events": [
    {
      "schema_version": "1.0",             // per-event; ingestion uses this to pick the parser
      "inference_id": "01935b3f-...",      // canonical UUIDv7 string (8-4-4-4-12 hex)
      "conversation_id": "uuid",
      "message_id": "uuid",                // ID of the ASSISTANT message this inference produced
      "model": "gpt-4o",                   // raw LiteLLM model id; canonicalized by ingestion
      "provider": "openai",                // canonical set: openai|anthropic|google|...
      "status": "ok",                      // "ok" | "error" | "timeout" | "cancelled"
      "error": null,                       // { type, message, provider_code } if status != ok
      "ts_start": "2026-05-21T10:00:00.123Z",
      "ts_end":   "2026-05-21T10:00:00.965Z",
      "latency_ms": 842,
      "ttft_ms": 120,                      // null for non-streaming
      "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150
      },
      "prompt_preview": "first 500 chars of last user msg",
      "response_preview": "first 500 chars of assistant reply",
      "raw_payload": { "...": "..." },     // OPTIONAL. See "Raw payload policy" below.
      "metadata": { "user_id": "..." },
      "sdk_version": "0.1.0"
    }
  ]
}
```

**Raw payload policy.** The SDK MAY include `raw_payload` (full request + response JSON). The ingestion API treats it as follows:

| `OLIVE_KEEP_RAW` | What happens to `raw_payload` |
|---|---|
| `false` (default) | **Dropped at ingestion before publish.** Never lands on the bus, never stored. Only the redacted previews persist. |
| `true` (debug only) | Every string field inside the payload is passed through the same PII redactor. Then the redacted payload is attached to the event and stored. A loud warning is logged at ingestion startup. |

In both cases, nothing past the bus sees an unredacted prompt, response, or payload. See ADR-0006.

**`message_id` semantics.** Always the ID of the **assistant** message the inference call produces. The chatbot creates the assistant message row up front (status `pending`, content empty), passes its ID to the SDK, and updates the row when the stream completes. If the inference errors before any content is produced, the assistant message row still exists with `status='error'` and empty content.

**Response**

A batch always returns `202 Accepted`. Mixed valid/invalid batches return partial success in one response; there is **no** `422` for per-event validation (that would conflict with partial success semantics).

```json
{
  "accepted": 1,
  "rejected": [],                          // [{ index, reason }] if any per-event validation failed
  "stream_ids": ["1700000000000-0"]        // optional, for SDK-side debugging
}
```

**Errors**

| Code | Meaning | SDK behavior |
|---|---|---|
| 400 | Malformed request body (not JSON, missing `events`, batch too large) | log, drop batch |
| 429 | Server is shedding load | exponential backoff, keep queued |
| 503 | Redis unavailable | exponential backoff, keep queued |

Per-event failures (size > 256 KiB, missing required fields, bad enum value) are reported in `rejected[]` of a `202`, not via a `4xx` status code.

**Constraints**
- Batch size: hard limit 500, soft limit 100. Over hard limit → `400`.
- Max event size: 256 KiB. Larger → reported in `rejected[]`.
- `prompt_preview` / `response_preview` truncated to 500 chars by SDK.

---

## Chatbot UI ↔ Chatbot API

### `POST /v1/conversations`

Create a conversation.

```json
// Request
{ "model_default": "gpt-4o", "system_prompt": "You are..." }

// Response 201
{ "conversation_id": "uuid", "created_at": "..." }
```

### `GET /v1/conversations`

List conversations (most recent first). Pagination via `?cursor=`.

```json
// Response 200
{
  "conversations": [
    { "id": "uuid", "model_default": "gpt-4o", "updated_at": "...", "message_count": 6 }
  ],
  "next_cursor": null
}
```

### `GET /v1/conversations/:id/messages`

```json
// Response 200
{
  "messages": [
    { "id": "uuid", "role": "user", "content": "...", "created_at": "..." },
    { "id": "uuid", "role": "assistant", "content": "...", "created_at": "..." }
  ]
}
```

### `POST /v1/conversations/:id/messages` (SSE)

Send a user message, stream the assistant reply.

**Request**
```json
{ "role": "user", "content": "hello", "model": "gpt-4o" }
```

**Response** — `Content-Type: text/event-stream`

```
event: token
data: {"delta": "Hi"}

event: token
data: {"delta": " there"}

event: done
data: {"message_id": "uuid", "inference_id": "uuid", "usage": {...}}
```

On error mid-stream:
```
event: error
data: {"error": {"type": "...", "message": "..."}}
```

---

## Dashboard

### `GET /v1/metrics`

Query pre-aggregated rollups.

**Query params**
- `from` (ISO8601, default: now-1h)
- `to` (ISO8601, default: now)
- `model` (optional, repeatable)
- `provider` (optional, repeatable)
- `interval` (`1m` only for now)

**Response**
```json
{
  "buckets": [
    {
      "minute_bucket": "2026-05-21T10:00:00Z",
      "model": "gpt-4o",
      "provider": "openai",
      "count": 42,
      "error_count": 1,
      "latency_p50_ms": 700,
      "latency_p95_ms": 1900,
      "prompt_tokens_sum": 4200,
      "completion_tokens_sum": 2100
    }
  ]
}
```

---

## Internal event — Redis Stream `inference.logged`

Same shape as the SDK event **after PII redaction** and any payload relocation. Versioned via `schema_version`. Consumers must tolerate unknown fields (forward-compat) and treat missing optional fields as `null` (back-compat).

Dead-letter stream: `inference.dead` — same payload plus `dead_reason` and `failed_at`.
