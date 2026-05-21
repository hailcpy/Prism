# ADR-0006: PII redaction at ingestion boundary; raw payload dropped by default

- **Status:** Accepted
- **Date:** 2026-05-21

## Context

PII redaction is an in-scope bonus. Two questions to settle:

**1. Where does redaction run?**

1. **In the SDK** — redact before the network. Pro: raw PII never leaves the producing process. Con: every SDK consumer ships its own redactor; rule updates require a release of every app.
2. **In the ingestion API** — redact at the trust boundary. Pro: single place to update rules; all downstream consumers inherit safety. Con: raw PII transits one network hop before redaction (acceptable inside a private network, especially with TLS).
3. **In each worker** — redact at the storage boundary. Pro: differentiated policies per sink. Con: PII is on the event bus in cleartext; any new consumer can read it. Unsafe.

**2. What happens to `raw_payload`?**

The SDK MAY attach a full request+response JSON blob for debugging and replay. This is the highest-risk PII surface: prompts and responses can contain anything. We can:

1. Always drop it at ingestion. Cleanest privacy story; loses debug/replay capability entirely.
2. Always store it, redacted. Highest debug value; biggest blast radius if redaction misses something.
3. Drop it by default, store it only when an explicit debug flag is set, and only after redaction. Privacy-safe in normal operation; debug capability available under a deliberate config change.

## Decision

Redaction runs **in the ingestion API**, before any `XADD` to Redis Streams. Nothing past the bus is allowed to see unredacted previews or payloads — by construction.

Raw payload handling:

| `PRISM_KEEP_RAW` env var | Behavior |
|---|---|
| **`false` (default)** | `raw_payload` is dropped at ingestion **before publish**. Never lands on the bus, never stored. Only redacted `prompt_preview` and `response_preview` persist. |
| `true` (debug only) | Every string field inside `raw_payload` is passed through the same regex redactor. The redacted payload is then attached to the event and persisted to `raw_payload_jsonb` (today) or written to S3 (future). Ingestion startup logs a loud warning. |

The redactor is a regex module today (email, phone, SSN, credit-card). It is invoked via a `Redactor` interface so it can be swapped for Microsoft Presidio or a model-based redactor later without touching call sites.

## Consequences

- **+** "Nothing past the bus sees raw PII" is now a true invariant in the default config — provably, by inspection of the ingestion code.
- **+** Single place to update rules; SDK consumers don't redeploy when rules change.
- **+** Debug/replay capability is preserved via an explicit, loudly-flagged opt-in.
- **+** Centralized rule means we can also centralize a "PII detected" counter for audit.
- **−** Raw PII transits one network hop (SDK → ingestion) inside the private network. Production deploy would mandate TLS / mTLS; not in scope for the takehome.
- **−** Regex misses non-standard formats (international phone numbers, custom IDs). Documented limitation; the `Redactor` interface allows upgrading to Presidio without code changes elsewhere.
- **−** When `PRISM_KEEP_RAW=true` is enabled, the safety story degrades from "no raw PII downstream" to "no *un-redacted* raw PII downstream." If the redactor misses a pattern, that miss is now stored. The flag is debug-only for this reason.
