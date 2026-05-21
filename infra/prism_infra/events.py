from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prism_infra.models import ErrorInfo, InferenceEvent, Usage


def event_to_wire(event: InferenceEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": event.schema_version,
        "inference_id": event.inference_id,
        "conversation_id": event.conversation_id,
        "message_id": event.message_id,
        "model": event.model,
        "provider": event.provider,
        "status": event.status,
        "error": _error_to_wire(event.error),
        "ts_start": _datetime_to_wire(event.ts_start),
        "ts_end": _datetime_to_wire(event.ts_end),
        "latency_ms": event.latency_ms,
        "ttft_ms": event.ttft_ms,
        "usage": {
            "prompt_tokens": event.usage.prompt_tokens,
            "completion_tokens": event.usage.completion_tokens,
            "total_tokens": event.usage.total_tokens,
        },
        "prompt_preview": event.prompt_preview,
        "response_preview": event.response_preview,
        "raw_payload_uri": event.raw_payload_uri,
        "raw_payload_jsonb": event.raw_payload_jsonb,
        "metadata": event.metadata,
        "sdk_version": event.sdk_version,
        "created_at": _datetime_to_wire(event.created_at) if event.created_at else None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def event_from_wire(payload: dict[str, Any]) -> InferenceEvent:
    error_payload = payload.get("error")
    error = None
    if isinstance(error_payload, dict):
        error = ErrorInfo(
            type=str(error_payload.get("type", "")),
            message=str(error_payload.get("message", "")),
            provider_code=error_payload.get("provider_code"),
        )

    usage_payload = payload.get("usage")
    usage = usage_payload if isinstance(usage_payload, dict) else {}

    return InferenceEvent(
        schema_version=str(payload["schema_version"]),
        inference_id=str(payload["inference_id"]),
        conversation_id=payload.get("conversation_id"),
        message_id=payload.get("message_id"),
        model=str(payload["model"]),
        provider=str(payload["provider"]),
        status=payload["status"],
        error=error,
        ts_start=_datetime_from_wire(payload["ts_start"]),
        ts_end=_datetime_from_wire(payload["ts_end"]),
        latency_ms=int(payload["latency_ms"]),
        ttft_ms=payload.get("ttft_ms"),
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        ),
        prompt_preview=payload.get("prompt_preview"),
        response_preview=payload.get("response_preview"),
        raw_payload_uri=payload.get("raw_payload_uri"),
        raw_payload_jsonb=payload.get("raw_payload_jsonb"),
        metadata=payload.get("metadata") or {},
        sdk_version=payload.get("sdk_version"),
        created_at=(
            _datetime_from_wire(payload["created_at"]) if payload.get("created_at") else None
        ),
    )


def _error_to_wire(error: ErrorInfo | None) -> dict[str, str | None] | None:
    if error is None:
        return None
    return {
        "type": error.type,
        "message": error.message,
        "provider_code": error.provider_code,
    }


def _datetime_to_wire(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_wire(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
