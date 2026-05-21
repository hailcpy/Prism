# Olive Docs

Architecture, contracts, and decision log for the Olive LLM inference logging system.

## How to read these docs

Start here, then go to whichever doc matches your question:

| If you want to... | Read |
|---|---|
| Understand the system end-to-end | [`architecture.md`](architecture.md) |
| Integrate the SDK or call the ingestion API | [`api-contracts.md`](api-contracts.md) |
| Understand the database schema | [`schema.md`](schema.md) |
| Know why a choice was made | [`adr/`](adr/) — chronological decision log |
| Know what we deliberately *didn't* do | [`risks-and-tradeoffs.md`](risks-and-tradeoffs.md) |
| Run it locally / operate it | [`runbook.md`](runbook.md) |
| Know what to build, in what order | [`implementation-plan.md`](implementation-plan.md) |

## Conventions

- **ADRs are immutable once accepted.** If a decision changes, write a new ADR that supersedes the old one and update the old one's status to `Superseded by ADR-XXXX`. Don't edit history.
- **Architecture diagrams use ASCII** so they live in the same diff as the prose. Mermaid is fine where it adds value but ASCII is the default.
- **Every non-obvious choice gets an ADR.** If you're tempted to write a paragraph in the architecture doc explaining *why*, that paragraph belongs in an ADR instead.
