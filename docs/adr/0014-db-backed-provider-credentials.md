# ADR-0014: DB-backed provider credentials with Fernet at rest

- **Status:** Accepted
- **Date:** 2026-05-22

## Context

The current chatbot credential path is brittle and weakens the product impression:

- Provider keys are entered in the browser, stored in `localStorage`, and sent on every request as `x-prism-*` headers.
- The backend also falls back to process environment keys during model discovery, so credential ownership is split between browser state and server env.
- There is no first-class validation, no saved credential lifecycle, and no place to show last test status.
- The project has no auth or user model in v1, so any credential store must match the existing single-tenant shape instead of pretending to be multi-tenant.

This is acceptable for an early local demo, but it is not the right architecture for an interview-visible full-stack product.

## Decision

Provider credentials move into Postgres as single-tenant app data. Secrets are encrypted at rest with Fernet using a deployment-provided `PRISM_CREDS_KEY`.

The new `provider_credentials` table stores one encrypted JSON envelope per credential:

- `provider` and `name` identify the credential.
- `secrets_enc` stores Fernet ciphertext over provider-specific secret fields.
- `metadata` stores non-secret provider fields such as AWS region.
- `is_default`, `last_tested_at`, `last_test_ok`, and scrubbed `last_test_error` support the Settings UI.

The chatbot API exposes a credential management router under `/v1/credentials` plus `/v1/providers`. Returned credentials are always redacted. The UI owns add/edit/test/delete/default workflows through a Settings page; the chat UI no longer stores provider keys in `localStorage` or sends `x-prism-*` headers.

Credential validation and model discovery are routed through a small provider registry and LiteLLM shim:

- Use explicit LiteLLM arguments such as `api_key=...` or `litellm_params=...` instead of mutating `os.environ`.
- Use direct `boto3.client(...)` arguments for Bedrock validation.
- Never write unsanitized provider errors to DB or UI.
- Resolve the active credential before creating chat messages so missing credentials return a clean `400` without orphan user or assistant rows.

This remains a local single-tenant feature. There is no authentication or per-user authorization boundary in v1.

## Consequences

- **+** Provider keys leave browser storage and are not replayed on every inference request.
- **+** The user gets a real Settings workflow: add, test, save, mark default, delete.
- **+** Chat/model discovery has one credential source of truth.
- **+** The encrypted JSON envelope handles providers with multiple secret fields, especially Bedrock.
- **−** `PRISM_CREDS_KEY` becomes a required operational secret once credentials are enabled. If it is lost, stored credentials cannot be decrypted.
- **−** Fernet gives confidentiality and integrity, but key rotation is not automatic. The v1 migration path is to add key-versioned `MultiFernet` and re-encrypt rows in a later ADR.
- **−** Without auth, anyone who can reach the local API can manage credentials. This is accepted for the single-tenant demo and must be revisited before network-exposed deployment.
- **−** Live credential tests can call provider APIs and may be slow or rate-limited. Tests must use mocks unless explicitly marked integration.

## Implementation Notes

- `PRISM_CREDS_KEY` must be a base64 Fernet key, generated with:
  `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
- Do not generate this key automatically at container boot; an ephemeral key would make existing credentials undecryptable after restart.
- Store only length-capped, scrubbed validation errors in `last_test_error`.
- Default selection must be transactional: unset the old provider default and set the new one in one transaction.
- Add a simple `status` field for messages, or persist status in `metadata_jsonb`, so cancelled partial assistant messages remain visible.
