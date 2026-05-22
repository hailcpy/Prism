# ADR-0011: SDK captures via LiteLLM callback (supersedes ADR-0004)

- **Status:** Accepted
- **Date:** 2026-05-22
- **Supersedes:** ADR-0004

## Context

ADR-0004 chose call-site wrapping (`client.chat.completions.create`) over LiteLLM's callback hooks, primarily for reviewer visibility. Two things have changed:

1. **The chatbot is moving to an agent runtime (Strands).** Strands owns the LLM call site through its own `LiteLLMModel` provider. A wrapper that requires callers to invoke `client.chat.completions.create(...)` cannot intercept those calls without re-implementing Strands's model layer.
2. **The brief explicitly sanctions middleware.** The assignment says "SDK, middleware, OR wrapper" and "implementation details are flexible." The capture-point choice is not itself a grading axis; the captured metadata, schema design, and tradeoffs are.

ADR-0004's visibility concern is preserved by keeping `PrismCallback` a small, plainly-named class in `prism_sdk` — the reviewer reads one file to see how latency / tokens / errors / TTFT are captured.

## Decision

`prism_sdk` is reorganized around a LiteLLM **callback handler**, not a call-site wrapper.

- A `PrismCallback` class subclasses `litellm.integrations.custom_logger.CustomLogger` and implements `log_success_event`, `log_failure_event`, and the async variants. Each handler builds one `InferenceEvent` and pushes it onto the bounded queue.
- `PrismClient.install()` registers the callback via `litellm.callbacks.append(...)`. The client owns the queue, flusher thread, HTTP transport, sinks, and lifecycle (`close`, `flush`, `atexit`).
- Correlation IDs (`conversation_id`, `message_id`, optional caller-supplied `inference_id`) travel through the LiteLLM `metadata` kwarg under a `"prism"` namespace. A `prism_sdk.metadata(...)` helper builds the value.
- Streaming: LiteLLM fires `log_success_event` once at stream completion with `response_obj` assembled and `usage` populated; `kwargs["completion_start_time"]` gives TTFT. ADR-0007's "one event per call" invariant is preserved.
- Tool calls inside the agent loop are out of scope for this callback (it fires per LLM turn, not per tool execution). Tool traces are captured via a Strands hook adapter — see ADR-0012.

## Consequences

- **+** Works transparently with Strands and any other framework that calls `litellm` directly.
- **+** Less code: streaming wrapper, async iterator session, and chunk-delta parsing are deleted. LiteLLM hands us the assembled response.
- **+** Reviewer visibility preserved via a small explicit `PrismCallback`.
- **−** Correlation IDs travel via `metadata` (less type-safe than explicit kwargs). Mitigated by the `metadata()` helper.
- **−** We inherit LiteLLM's callback timing and field semantics. Mitigated by integration tests pinning the fields we depend on (`completion_start_time`, `response_obj.usage`, `response_obj.choices[0].message.content`).
- **−** Tool-level visibility now requires a separate adapter. Documented as ADR-0012.

## Migration

- Delete `_Chat`, `_Completions`, `StreamSession`, the streaming async iterator, and the `chat.completions.create/stream` surface from `prism_sdk`.
- Replace with `PrismCallback` + `PrismClient.install()`.
- Chatbot API stops calling `client.chat.completions.create`; it calls `litellm.completion(stream=True, metadata=prism_sdk.metadata(...))` directly. SSE endpoint streams chunks to the UI; the callback fires once at stream end.
- Tests: assert the callback enqueues the expected event, not that a wrapper method was called.
