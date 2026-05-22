# ADR-0012: Tool-call traces via Strands hooks

- **Status:** Accepted
- **Date:** 2026-05-22

## Context

With ADR-0011, prism captures every LLM call via a LiteLLM callback. That covers model turns inside the agent loop, but **not** tool invocations — Strands runs tools itself between LLM turns, and LiteLLM never sees them.

Agentic chat is in scope as of this pivot. Tool execution is where the interesting failures live (slow tool, tool returned an error, tool produced unexpected output that caused the next LLM turn to misbehave). We need to log it.

Strands exposes a hook system: `BeforeToolCallEvent`, `AfterToolCallEvent`, plus message lifecycle events. We can subscribe and emit prism events for each tool call. Older Strands docs used `BeforeToolInvocationEvent` / `AfterToolInvocationEvent`; those names are compatibility aliases, not the implementation target.

## Decision

Ship a `prism_sdk.strands` submodule (only imported if Strands is installed) exposing `PrismStrandsHooks` — a hook provider that subscribes to tool invocation events and pushes `ToolInvocationEvent` records onto the same bounded queue as inference events.

Tool events are a **new event type**, not a piggyback on `InferenceEvent`:

```
ToolInvocationEvent:
  schema_version: "1.0"
  event_type: "tool_invocation"
  tool_invocation_id: uuid
  conversation_id: uuid          # from hook context
  inference_id: uuid | null      # the LLM call that requested this tool, if known
  tool_name: str
  arguments_preview: str         # redacted, first 500 chars of JSON args
  result_preview: str | null     # redacted, first 500 chars of stringified result
  status: "ok" | "error"
  error: { type, message } | null
  ts_start, ts_end, latency_ms
  metadata: { ... }
```

Storage: new table `tool_invocations` (same partition/index pattern as `inference_logs`). The existing ingestion endpoint accepts both shapes, discriminated by `event_type`. This keeps one redaction, batching, bus, retry, and worker surface.

Soft FK: `tool_invocations.inference_id → inference_logs.id`. Not enforced (ADR-0008).

Open questions resolved:

- `inference_id` propagation: chatbot-api creates the assistant-message `inference_id` before invoking Strands and passes it through `Agent.stream_async(..., invocation_state={...})`. `PrismStrandsHooks` reads `prism_conversation_id` and `prism_inference_id` from that invocation state.
- Large tool results: v1 stores only `arguments_preview` and `result_preview`, each capped at 500 chars and redacted at ingestion. Full tool payload storage is deferred; it would follow the same `RawPayloadStore` pattern if needed.
- Streaming tool calls: tool hooks emit once in `AfterToolCallEvent`, matching ADR-0007's one-event-on-completion rule for streaming LLM calls.

## Consequences

- **+** Full agent-loop visibility: every LLM turn + every tool call lands in DB, correlated.
- **+** Reuses queue, redaction, bus, worker — only adds a new table and a new event type.
- **+** Strands-specific concerns isolated to one optional submodule.
- **−** Schema growth: new table, new event shape, new dashboard surfaces (tool latency, tool error rate). Out of scope for the initial pivot — defer to a later phase.
- **−** Coupling: prism now knows about Strands. Mitigated by the optional-import pattern; nothing breaks if Strands isn't installed.
