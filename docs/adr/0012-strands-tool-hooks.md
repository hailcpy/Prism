# ADR-0012: Tool-call traces via Strands hooks

- **Status:** Proposed
- **Date:** 2026-05-22

## Context

With ADR-0011, prism captures every LLM call via a LiteLLM callback. That covers model turns inside the agent loop, but **not** tool invocations — Strands runs tools itself between LLM turns, and LiteLLM never sees them.

Agentic chat is in scope as of this pivot. Tool execution is where the interesting failures live (slow tool, tool returned an error, tool produced unexpected output that caused the next LLM turn to misbehave). We need to log it.

Strands exposes a hook system: `BeforeToolInvocation`, `AfterToolInvocation`, plus message lifecycle events. We can subscribe and emit prism events for each tool call.

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

Storage: new table `tool_invocations` (same partition/index pattern as `inference_logs`). The ingestion API gains a parallel `POST /v1/tool-events:batch` endpoint, or — to keep one ingestion surface — the existing batch endpoint accepts both shapes discriminated by `event_type`. Lean toward the latter; reuses redaction, batching, and the bus.

Soft FK: `tool_invocations.inference_id → inference_logs.id`. Not enforced (ADR-0008).

## Consequences

- **+** Full agent-loop visibility: every LLM turn + every tool call lands in DB, correlated.
- **+** Reuses queue, redaction, bus, worker — only adds a new table and a new event type.
- **+** Strands-specific concerns isolated to one optional submodule.
- **−** Schema growth: new table, new event shape, new dashboard surfaces (tool latency, tool error rate). Out of scope for the initial pivot — defer to a later phase.
- **−** Coupling: prism now knows about Strands. Mitigated by the optional-import pattern; nothing breaks if Strands isn't installed.

## Open questions (resolve before implementing)

- How does the hook know the surrounding `inference_id`? Either the agent runner sets it on a context-var around each LLM turn, or we omit the soft FK on the first cut and add it later.
- Does the redactor need a "tool result" mode (potentially much larger payloads than chat previews)?
- Streaming tool calls (rare): treat the same way as streaming LLM — log once on completion.
