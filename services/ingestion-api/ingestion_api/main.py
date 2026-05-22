from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError

from ingestion_api.redaction import RegexRedactor
from prism_infra.bus import Bus, RedisStreamsBus
from prism_infra.events import event_to_wire, tool_event_to_wire
from prism_infra.models import ErrorInfo, InferenceEvent, ToolErrorInfo, ToolInvocationEvent, Usage
from prism_infra.storage import JsonbRawPayloadStore, RawPayloadStore

log = logging.getLogger("ingestion-api")

EVENT_STREAM = "inference.logged"
MAX_BATCH_SIZE = 500
MAX_EVENT_BYTES = 256 * 1024
BATCH_BODY = Body(...)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    message: str
    provider_code: str | None = None


class UsageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class EventBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str
    inference_id: UUID
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    model: str
    provider: str
    status: Literal["ok", "error", "timeout", "cancelled"]
    error: ErrorBody | None = None
    ts_start: datetime
    ts_end: datetime
    latency_ms: int = Field(ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    usage: UsageBody = Field(default_factory=UsageBody)
    prompt_preview: str | None = Field(default=None, max_length=500)
    response_preview: str | None = Field(default=None, max_length=500)
    raw_payload: dict[str, Any] | list[Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    sdk_version: str | None = None


class ToolErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    message: str


class ToolEventBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str
    event_type: Literal["tool_invocation"]
    tool_invocation_id: UUID
    conversation_id: UUID | None = None
    inference_id: UUID | None = None
    tool_name: str
    arguments_preview: str = Field(max_length=500)
    result_preview: str | None = Field(default=None, max_length=500)
    status: Literal["ok", "error"]
    error: ToolErrorBody | None = None
    ts_start: datetime
    ts_end: datetime
    latency_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sdk_version: str | None = None


class RejectedEvent(BaseModel):
    index: int
    reason: str


class BatchResponse(BaseModel):
    accepted: int
    rejected: list[RejectedEvent]
    stream_ids: list[str]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if _keep_raw():
        log.warning("PRISM_KEEP_RAW=true; raw payloads will be redacted and stored for debugging")
    yield


app = FastAPI(title="prism-ingestion-api", version="0.1.0", lifespan=lifespan)
app.state.bus = None
app.state.raw_payload_store = None
app.state.redactor = RegexRedactor()


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/events:batch", status_code=202, response_model=BatchResponse)
def ingest_batch(request: Request, body: Any = BATCH_BODY) -> BatchResponse:
    events = _extract_events(body)
    bus = _get_bus(request.app)
    raw_payload_store = _get_raw_payload_store(request.app)
    redactor: RegexRedactor = request.app.state.redactor

    accepted = 0
    rejected: list[RejectedEvent] = []
    stream_ids: list[str] = []

    for index, raw_event in enumerate(events):
        try:
            _enforce_event_size(raw_event)
            event = _sanitize_any_event(raw_event, redactor, raw_payload_store)
            stream_ids.append(bus.publish(EVENT_STREAM, event))
            accepted += 1
        except ValidationError as exc:
            rejected.append(RejectedEvent(index=index, reason=_validation_reason(exc)))
        except ValueError as exc:
            rejected.append(RejectedEvent(index=index, reason=str(exc)))
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="event bus unavailable") from exc

    return BatchResponse(accepted=accepted, rejected=rejected, stream_ids=stream_ids)


def _extract_events(body: Any) -> list[Any]:
    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        raise HTTPException(status_code=400, detail="request body must contain an events array")
    events = body["events"]
    if len(events) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"batch size exceeds {MAX_BATCH_SIZE}")
    return events


def _enforce_event_size(raw_event: Any) -> None:
    size = len(json.dumps(raw_event, separators=(",", ":"), default=str).encode("utf-8"))
    if size > MAX_EVENT_BYTES:
        raise ValueError(f"event exceeds {MAX_EVENT_BYTES} bytes")


def _sanitize_any_event(
    raw_event: Any,
    redactor: RegexRedactor,
    raw_payload_store: RawPayloadStore,
) -> dict[str, Any]:
    if isinstance(raw_event, dict) and raw_event.get("event_type") == "tool_invocation":
        return tool_event_to_wire(
            _sanitize_tool_event(ToolEventBody.model_validate(raw_event), redactor)
        )
    return event_to_wire(
        _sanitize_event(EventBody.model_validate(raw_event), redactor, raw_payload_store)
    )


def _sanitize_event(
    body: EventBody,
    redactor: RegexRedactor,
    raw_payload_store: RawPayloadStore,
) -> InferenceEvent:
    raw_payload_uri = None
    raw_payload_jsonb = None
    if _keep_raw() and body.raw_payload is not None:
        redacted_payload = redactor.redact_json(body.raw_payload)
        raw_payload_uri, raw_payload_jsonb = raw_payload_store.put(
            str(body.inference_id), redacted_payload
        )

    return InferenceEvent(
        schema_version=body.schema_version,
        inference_id=str(body.inference_id),
        conversation_id=str(body.conversation_id) if body.conversation_id else None,
        message_id=str(body.message_id) if body.message_id else None,
        model=body.model,
        provider=body.provider,
        status=body.status,
        error=_error_from_body(body.error),
        ts_start=body.ts_start,
        ts_end=body.ts_end,
        latency_ms=body.latency_ms,
        ttft_ms=body.ttft_ms,
        usage=Usage(
            prompt_tokens=body.usage.prompt_tokens,
            completion_tokens=body.usage.completion_tokens,
            total_tokens=body.usage.total_tokens,
        ),
        prompt_preview=redactor.redact_text(body.prompt_preview),
        response_preview=redactor.redact_text(body.response_preview),
        raw_payload_uri=raw_payload_uri,
        raw_payload_jsonb=raw_payload_jsonb,
        metadata=body.metadata,
        sdk_version=body.sdk_version,
        created_at=datetime.now(UTC),
    )


def _sanitize_tool_event(body: ToolEventBody, redactor: RegexRedactor) -> ToolInvocationEvent:
    return ToolInvocationEvent(
        schema_version=body.schema_version,
        tool_invocation_id=str(body.tool_invocation_id),
        conversation_id=str(body.conversation_id) if body.conversation_id else None,
        inference_id=str(body.inference_id) if body.inference_id else None,
        tool_name=body.tool_name,
        arguments_preview=redactor.redact_text(body.arguments_preview) or "",
        result_preview=redactor.redact_text(body.result_preview),
        status=body.status,
        error=_tool_error_from_body(body.error),
        ts_start=body.ts_start,
        ts_end=body.ts_end,
        latency_ms=body.latency_ms,
        metadata=body.metadata,
        sdk_version=body.sdk_version,
        created_at=datetime.now(UTC),
    )


def _error_from_body(error: ErrorBody | None) -> ErrorInfo | None:
    if error is None:
        return None
    return ErrorInfo(type=error.type, message=error.message, provider_code=error.provider_code)


def _tool_error_from_body(error: ToolErrorBody | None) -> ToolErrorInfo | None:
    if error is None:
        return None
    return ToolErrorInfo(type=error.type, message=error.message)


def _validation_reason(exc: ValidationError) -> str:
    first_error = exc.errors()[0]
    location = ".".join(str(part) for part in first_error["loc"])
    return f"{location}: {first_error['msg']}"


def _get_bus(app: FastAPI) -> Bus:
    if app.state.bus is None:
        app.state.bus = RedisStreamsBus(os.environ["REDIS_URL"])
    return app.state.bus


def _get_raw_payload_store(app: FastAPI) -> RawPayloadStore:
    if app.state.raw_payload_store is None:
        app.state.raw_payload_store = JsonbRawPayloadStore()
    return app.state.raw_payload_store


def _keep_raw() -> bool:
    return os.getenv("PRISM_KEEP_RAW", "false").lower() in {"1", "true", "yes", "on"}
