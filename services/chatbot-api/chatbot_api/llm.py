from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import boto3
import httpx
import litellm

from prism_infra.storage import ProviderCredentialWithSecrets

MAX_PROVIDER_ERROR_LEN = 300


@dataclass(frozen=True)
class ProviderField:
    name: str
    label: str
    required: bool
    default: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    secret_fields: tuple[ProviderField, ...]
    metadata_fields: tuple[ProviderField, ...]


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        label="OpenAI",
        secret_fields=(ProviderField("api_key", "API key", True),),
        metadata_fields=(),
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic",
        secret_fields=(ProviderField("api_key", "API key", True),),
        metadata_fields=(),
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        secret_fields=(ProviderField("api_key", "API key", True),),
        metadata_fields=(),
    ),
    ProviderSpec(
        id="bedrock",
        label="Amazon Bedrock",
        secret_fields=(
            ProviderField("aws_access_key_id", "Access key ID", True),
            ProviderField("aws_secret_access_key", "Secret access key", True),
            ProviderField("aws_session_token", "Session token", False),
        ),
        metadata_fields=(ProviderField("aws_region", "AWS region", True, "us-west-2"),),
    ),
)


def provider_specs() -> tuple[ProviderSpec, ...]:
    return PROVIDERS


def provider_for_model(model: str) -> tuple[str, str]:
    if model.startswith("bedrock/arn:"):
        model = f"bedrock/converse/{model.removeprefix('bedrock/')}"
    _, provider, _, _ = litellm.get_llm_provider(model)
    return model, provider


def litellm_client_args(credential: ProviderCredentialWithSecrets) -> dict[str, Any]:
    provider = credential.provider
    secrets = credential.secrets
    metadata = credential.metadata
    if provider in {"openai", "anthropic", "gemini", "google"}:
        return {"api_key": secrets.get("api_key", "")}
    if provider == "bedrock":
        args: dict[str, Any] = {
            "aws_access_key_id": secrets.get("aws_access_key_id", ""),
            "aws_secret_access_key": secrets.get("aws_secret_access_key", ""),
            "aws_region_name": str(metadata.get("aws_region") or "us-west-2"),
        }
        if secrets.get("aws_session_token"):
            args["aws_session_token"] = secrets["aws_session_token"]
        return args
    return {}


async def discover_models(
    credentials: list[ProviderCredentialWithSecrets],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    models: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    by_provider = {credential.provider: credential for credential in credentials}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for provider in ("openai", "anthropic", "gemini"):
            credential = by_provider.get(provider)
            if not credential:
                continue
            try:
                if provider == "openai":
                    models.extend(await _discover_openai(client, credential.secrets["api_key"]))
                elif provider == "anthropic":
                    models.extend(await _discover_anthropic(client, credential.secrets["api_key"]))
                else:
                    models.extend(await _discover_gemini(client, credential.secrets["api_key"]))
            except Exception as exc:
                errors[provider] = sanitize_provider_error(str(exc))
    bedrock = by_provider.get("bedrock")
    if bedrock:
        try:
            models.extend(_discover_bedrock(bedrock))
        except Exception as exc:
            errors["bedrock"] = sanitize_provider_error(str(exc))
    return _dedupe(models), errors


async def validate_credential(
    provider: str, secrets: dict[str, str], metadata: dict[str, Any]
) -> list[str]:
    if provider == "openai":
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {secrets.get('api_key', '')}"},
            )
            response.raise_for_status()
            return [
                item["id"]
                for item in response.json().get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ][:20]
    if provider == "anthropic":
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": secrets.get("api_key", ""),
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
            return [
                item["id"]
                for item in response.json().get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ][:20]
    if provider == "gemini":
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": secrets.get("api_key", "")},
            )
            response.raise_for_status()
            return [
                str(item.get("name", "")).removeprefix("models/")
                for item in response.json().get("models", [])
                if isinstance(item, dict) and item.get("name")
            ][:20]
    if provider == "bedrock":
        kwargs: dict[str, str] = {
            "region_name": str(metadata.get("aws_region") or "us-west-2"),
            "aws_access_key_id": secrets.get("aws_access_key_id", ""),
            "aws_secret_access_key": secrets.get("aws_secret_access_key", ""),
        }
        if secrets.get("aws_session_token"):
            kwargs["aws_session_token"] = secrets["aws_session_token"]
        client = boto3.client("bedrock", **kwargs)
        response = client.list_foundation_models()
        return [
            str(item["modelId"])
            for item in response.get("modelSummaries", [])
            if isinstance(item, dict) and item.get("modelId")
        ][:20]
    raise ValueError(f"unsupported provider: {provider}")


def sanitize_provider_error(error: str) -> str:
    compact = re.sub(r"\s+", " ", error).strip()
    compact = re.sub(
        r"(sk|api|token|secret)[-_a-zA-Z0-9]{8,}", "[redacted]", compact, flags=re.IGNORECASE
    )
    return compact[:MAX_PROVIDER_ERROR_LEN]


_OPENAI_NON_CHAT_PATTERNS = (
    "tts",
    "transcribe",
    "whisper",
    "embedding",
    "moderation",
    "dall-e",
    "image",
    "audio",
    "realtime",
    "search-preview",
    "computer-use",
    "instruct",
    "davinci",
    "babbage",
    "codex",
)


def _is_openai_chat_model(model_id: str) -> bool:
    if any(pat in model_id for pat in _OPENAI_NON_CHAT_PATTERNS):
        return False
    return model_id.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4"))


async def _discover_openai(client: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    response = await client.get(
        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}
    )
    response.raise_for_status()
    ids = sorted(
        item["id"]
        for item in response.json().get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        and _is_openai_chat_model(item["id"])
    )
    return [
        {
            "id": item,
            "label": _label(item),
            "provider": "openai",
            "source": "discovered",
            "thinking_supported": item.startswith(("o", "gpt-5")),
        }
        for item in ids[:40]
    ]


async def _discover_anthropic(client: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    response = await client.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    response.raise_for_status()
    ids = sorted(
        item["id"]
        for item in response.json().get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )
    return [
        {
            "id": item,
            "label": _label(item),
            "provider": "anthropic",
            "source": "discovered",
            "thinking_supported": "claude" in item and "3-" not in item,
        }
        for item in ids[:40]
    ]


async def _discover_gemini(client: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    response = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models", params={"key": api_key}
    )
    response.raise_for_status()
    result: list[dict[str, Any]] = []
    for item in response.json().get("models", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").removeprefix("models/")
        if name:
            mid = f"gemini/{name}"
            result.append(
                {
                    "id": mid,
                    "label": _label(name),
                    "provider": "gemini",
                    "source": "discovered",
                    "thinking_supported": "2.5" in name,
                }
            )
    return result[:40]


def _discover_bedrock(credential: ProviderCredentialWithSecrets) -> list[dict[str, Any]]:
    kwargs = litellm_client_args(credential)
    client = boto3.client(
        "bedrock",
        region_name=kwargs["aws_region_name"],
        aws_access_key_id=kwargs["aws_access_key_id"],
        aws_secret_access_key=kwargs["aws_secret_access_key"],
        aws_session_token=kwargs.get("aws_session_token"),
    )
    response = client.list_foundation_models()
    result: list[dict[str, Any]] = []
    for summary in response.get("modelSummaries", []):
        model_id = summary.get("modelId")
        if isinstance(model_id, str):
            result.append(
                {
                    "id": f"bedrock/{model_id}",
                    "label": _label(model_id),
                    "provider": "bedrock",
                    "source": "discovered",
                    "thinking_supported": "claude" in model_id and "3-" not in model_id,
                }
            )
    return result[:80]


def _dedupe(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for model in models:
        deduped.setdefault(str(model["id"]), model)
    return sorted(deduped.values(), key=lambda item: (str(item["provider"]), str(item["label"])))


def _label(model_id: str) -> str:
    base = model_id.removeprefix("bedrock/").removeprefix("gemini/").split("/")[-1]
    return base.replace(".", " ").replace("-", " ").replace("_", " ").title()
