from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chatbot_api import llm
from prism_infra.storage import ProviderCredential, ProviderCredentialWithSecrets

router = APIRouter(prefix="/v1", tags=["credentials"])


class ProviderFieldBody(BaseModel):
    name: str
    label: str
    required: bool
    default: str | None = None


class ProviderBody(BaseModel):
    id: str
    label: str
    secret_fields: list[ProviderFieldBody]
    metadata_fields: list[ProviderFieldBody]


class ProvidersResponse(BaseModel):
    providers: list[ProviderBody]


class CredentialBody(BaseModel):
    id: str
    provider: str
    name: str
    metadata: dict[str, Any]
    is_default: bool
    last_tested_at: str | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None


class ListCredentialsResponse(BaseModel):
    credentials: list[CredentialBody]


class UpsertCredentialRequest(BaseModel):
    provider: str
    name: str = Field(min_length=1, max_length=120)
    secrets: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class PatchCredentialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    secrets: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None
    is_default: bool | None = None


class ValidateCredentialRequest(BaseModel):
    provider: str
    secrets: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidateCredentialResponse(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None


def register_credentials_router(app_router: APIRouter) -> None:
    app_router.include_router(router)


@router.get("/providers", response_model=ProvidersResponse)
def get_providers() -> ProvidersResponse:
    return ProvidersResponse(
        providers=[
            ProviderBody(
                id=provider.id,
                label=provider.label,
                secret_fields=[
                    ProviderFieldBody(**field.__dict__) for field in provider.secret_fields
                ],
                metadata_fields=[
                    ProviderFieldBody(**field.__dict__) for field in provider.metadata_fields
                ],
            )
            for provider in llm.provider_specs()
        ]
    )


@router.get("/credentials", response_model=ListCredentialsResponse)
def list_credentials(request: Request) -> ListCredentialsResponse:
    store = _store(request)
    rows: list[ProviderCredential] = store.list_credentials()
    return ListCredentialsResponse(credentials=[_to_body(row) for row in rows])


@router.post("/credentials", response_model=CredentialBody)
def create_credential(request: Request, body: UpsertCredentialRequest) -> CredentialBody:
    store = _store(request)
    row = store.create_credential(
        provider=body.provider,
        name=body.name,
        secrets=body.secrets,
        metadata=body.metadata,
        is_default=body.is_default,
    )
    return _to_body(row)


@router.patch("/credentials/{credential_id}", response_model=CredentialBody)
def patch_credential(
    request: Request, credential_id: str, body: PatchCredentialRequest
) -> CredentialBody:
    store = _store(request)
    row = store.update_credential(
        credential_id,
        name=body.name,
        secrets=body.secrets,
        metadata=body.metadata,
        is_default=body.is_default,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return _to_body(row)


@router.delete("/credentials/{credential_id}", status_code=204)
def delete_credential(request: Request, credential_id: str) -> None:
    store = _store(request)
    if not store.delete_credential(credential_id):
        raise HTTPException(status_code=404, detail="credential not found")


@router.post("/credentials/{credential_id}/test", response_model=ValidateCredentialResponse)
async def test_saved_credential(request: Request, credential_id: str) -> ValidateCredentialResponse:
    store = _store(request)
    credential: ProviderCredentialWithSecrets | None = store.get_credential_with_secrets(
        credential_id
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return await _validate_and_record(
        store=store,
        credential_id=credential_id,
        provider=credential.provider,
        secrets=credential.secrets,
        metadata=credential.metadata,
    )


@router.post("/credentials/test", response_model=ValidateCredentialResponse)
async def test_unsaved_credential(body: ValidateCredentialRequest) -> ValidateCredentialResponse:
    try:
        models = await llm.validate_credential(body.provider, body.secrets, body.metadata)
        return ValidateCredentialResponse(ok=True, models=models)
    except Exception as exc:
        return ValidateCredentialResponse(ok=False, error=llm.sanitize_provider_error(str(exc)))


async def _validate_and_record(
    *,
    store: Any,
    credential_id: str,
    provider: str,
    secrets: dict[str, str],
    metadata: dict[str, Any],
) -> ValidateCredentialResponse:
    try:
        models = await llm.validate_credential(provider, secrets, metadata)
        store.set_test_result(credential_id, ok=True, error=None)
        return ValidateCredentialResponse(ok=True, models=models)
    except Exception as exc:
        error = llm.sanitize_provider_error(str(exc))
        store.set_test_result(credential_id, ok=False, error=error)
        return ValidateCredentialResponse(ok=False, error=error)


def _to_body(row: ProviderCredential) -> CredentialBody:
    return CredentialBody(
        id=row.id,
        provider=row.provider,
        name=row.name,
        metadata=row.metadata,
        is_default=row.is_default,
        last_tested_at=row.last_tested_at.isoformat() if row.last_tested_at else None,
        last_test_ok=row.last_test_ok,
        last_test_error=row.last_test_error,
    )


def _store(request: Request) -> Any:
    store = request.app.state.credential_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="credentials unavailable: PRISM_CREDS_KEY is not configured",
        )
    return store
