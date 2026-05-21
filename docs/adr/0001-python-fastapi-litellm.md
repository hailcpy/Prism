# ADR-0001: Python everywhere (FastAPI + LiteLLM)

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

The system has four backend pieces (SDK, chatbot API, ingestion API, workers) plus a frontend. We can pick Python, TypeScript/Node, or split. Constraints:

- Need a provider-agnostic LLM client (multi-provider is an in-scope bonus).
- Need an SDK that's idiomatic to ship as a library.
- Reviewer reads code; uniformity of style matters.
- Frontend has to be web; that part is TS/React regardless.

Options considered:
1. **Python everywhere** — FastAPI services, LiteLLM, Python SDK. React for UI.
2. **TS/Node everywhere** — Next.js full-stack, Vercel AI SDK, npm SDK.
3. **Split** — TS chatbot + UI, Python ingestion.

LiteLLM in Python is the most mature provider-agnostic client today (broadest model coverage, normalized usage fields, mature streaming support). Vercel AI SDK is great for frontend streaming but its multi-provider story is less complete on niche models.

## Decision

Python for all backend (SDK, chatbot API, ingestion API, workers). FastAPI for HTTP services. LiteLLM as the provider abstraction inside the SDK. React/Next.js for the chatbot UI only.

## Consequences

- **+** One language for SDK + services; reviewer reads one style.
- **+** LiteLLM gives multi-provider "for free" inside the SDK.
- **+** Pydantic models double as validation and as documentation of contracts.
- **−** Frontend ↔ backend boundary is now cross-language; types must be hand-kept in sync (acceptable: only a handful of endpoints).
- **−** Python streaming is more painful than Node's; SSE in FastAPI works but is less ergonomic. Mitigated by limiting streaming to one endpoint.
