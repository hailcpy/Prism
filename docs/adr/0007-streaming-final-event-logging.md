# ADR-0007: Streaming logs one event at completion, not per-token

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

Streaming responses are an in-scope bonus. We have to decide how often to log:

1. **One event per token chunk.** Most accurate, but 100x event volume for a typical response, and most chunks add no information beyond timing.
2. **One event at stream completion.** Includes total latency, TTFT, final status, token usage. Loses mid-stream telemetry.
3. **Two events: one at first token (TTFT), one at completion.** More wiring, marginal benefit.

The signal we actually care about: TTFT (perceived latency), total duration, token counts, final status (ok / error / cancelled). All of these are knowable at stream end.

## Decision

The SDK emits exactly **one** `InferenceEvent` per LLM call, regardless of streaming. For streaming calls, the event is constructed at stream completion and includes:

- `ttft_ms` — time from request to first token
- `latency_ms` — full duration
- `status` — `ok` | `error` | `cancelled`
- Usage / preview as normal

If a stream is cancelled or errors mid-flight, the event is still emitted with the appropriate status. Cancellation must propagate through `AbortController` (TS) / `asyncio.CancelledError` (Python).

## Consequences

- **+** Log volume scales with conversations, not tokens.
- **+** Schema is uniform between streaming and non-streaming calls.
- **+** Dashboard math (p95 latency, error rate) works identically for both.
- **−** No per-chunk timing if we ever want to debug stalled streams. Mitigation: provider SDKs and OpenTelemetry traces would be better tools for that anyway.
- **−** If the SDK process dies mid-stream, the log is lost. Acceptable per ADR-0009.
