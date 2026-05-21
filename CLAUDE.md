# Olive — repo guidance for Claude Code

## Spec lives in `docs/`. Read it before changing code.

Read in this order at the start of any new session:

1. `docs/README.md` — index
2. `docs/architecture.md` — 8-section overview
3. `docs/adr/README.md` and ADRs `0001`–`0010` — load-bearing decisions with rationale
4. `docs/schema.md` — DDL and the PG → PG+ClickHouse+S3 migration seam
5. `docs/api-contracts.md` — SDK + HTTP + Redis stream schemas
6. `docs/runbook.md` — env vars, services, smoke script
7. `docs/implementation-plan.md` — the 8-phase build plan

The docs are authoritative. If code and docs disagree, the docs win until proven otherwise.

## Ground rules

- **ADRs are immutable once accepted.** If a decision changes, write a new ADR that supersedes the old one. Don't edit history.
- **Docs are the contract, not the code.** If you find a contradiction between the docs and what you're about to implement, **stop and ask** — fix the doc first, then implement. Don't silently fix it in code; that creates drift no reviewer can audit.
- **Run the Phase N smoke check from `docs/implementation-plan.md` before declaring a phase done.** Each phase ends with a demoable artifact — produce it.
- **The migration seam is non-negotiable.** All log-table I/O goes through `LogStore` / `RawPayloadStore` / `Bus` interfaces (ADR-0005). No `psycopg`/`redis-py`/`boto3` imports outside the `infra/storage/` and `infra/bus/` modules.
- **No cross-group SQL joins.** App data ↔ inference logs ↔ rollups are linked by soft FKs only (ADR-0008). Correlate in app code if you need to.
- **Don't over-engineer ahead of the plan.** Each phase has a defined scope; resist adding "while I'm here" features. If something seems missing, it's probably deferred to a later phase or out of scope by design (k8s, frontend cancel/list/resume).

## Phase-by-phase cadence

Implementation proceeds phase by phase, one session per phase for Phases 0–3, then one session for Phases 4–7. At the end of each phase: commit, run the phase's smoke check, then start the next phase in a fresh session.

## Model guidance for this repo

- **Boilerplate** (Docker Compose, SQL DDL, FastAPI skeletons, UI scaffolding) — Sonnet 4.6 is fine.
- **SDK, ingestion, workers, idempotent UPSERTs, streaming** — use Opus 4.7. These carry the load-bearing decisions from the ADRs.
- **Specific debugging** — Opus 4.7.

## Style

- Default to writing no comments. Comment only when the *why* is non-obvious (a constraint, an invariant, a workaround). Don't explain *what* — names should do that.
- Don't add features, refactors, or abstractions beyond what the current phase requires.
- Don't add error handling for cases that can't happen. Trust internal code; validate only at system boundaries.
