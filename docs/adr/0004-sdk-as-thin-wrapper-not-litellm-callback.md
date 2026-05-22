# ADR-0004: SDK wraps LiteLLM explicitly, not via `success_callback`

- **Status:** Superseded by ADR-0011
- **Date:** 2026-05-21

## Context

LiteLLM exposes `litellm.success_callback` and `failure_callback` lists. Registering a function there would automatically capture every LLM call and let us emit logs from inside LiteLLM's pipeline. It's tempting because it's less code.

Two problems:
1. **Hides our work from the reviewer.** The whole point of the SDK is to demonstrate that we understand the instrumentation boundary. If our SDK file is `litellm.success_callback.append(send_log)`, there's nothing to read.
2. **Less control.** Callback timing, error semantics, and access to streaming intermediate state vary by LiteLLM version. Wrapping ourselves makes those decisions visible and testable.

## Decision

The SDK calls `litellm.completion(...)` and `litellm.acompletion(...)` directly, with its own try/except, timing, token extraction, and event construction. We do **not** use LiteLLM's callback mechanism.

## Consequences

- **+** Reviewer can read `sdk/client.py` and see exactly how latency, tokens, and errors are captured.
- **+** Streaming logic (TTFT capture, final-event emission) is in our code, not buried in LiteLLM internals.
- **+** Testable: we can mock LiteLLM and verify the SDK's metadata extraction directly.
- **−** Slightly more code than a one-line callback registration.
- **−** If LiteLLM adds new useful fields (e.g. cached-token counts), we have to thread them through ourselves.
