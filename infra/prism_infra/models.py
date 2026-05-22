from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

InferenceStatus = Literal["ok", "error", "timeout", "cancelled"]
ToolInvocationStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class ErrorInfo:
    type: str
    message: str
    provider_code: str | None = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class InferenceEvent:
    schema_version: str
    inference_id: str
    conversation_id: str | None
    message_id: str | None
    model: str
    provider: str
    status: InferenceStatus
    ts_start: datetime
    ts_end: datetime
    latency_ms: int
    ttft_ms: int | None = None
    usage: Usage = field(default_factory=Usage)
    error: ErrorInfo | None = None
    prompt_preview: str | None = None
    response_preview: str | None = None
    raw_payload_uri: str | None = None
    raw_payload_jsonb: dict[str, Any] | list[Any] | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sdk_version: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ToolErrorInfo:
    type: str
    message: str


@dataclass(frozen=True)
class ToolInvocationEvent:
    schema_version: str
    tool_invocation_id: str
    conversation_id: str | None
    inference_id: str | None
    tool_name: str
    status: ToolInvocationStatus
    ts_start: datetime
    ts_end: datetime
    latency_ms: int
    arguments_preview: str
    result_preview: str | None = None
    error: ToolErrorInfo | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sdk_version: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class MetricsRow:
    minute_bucket: datetime
    model: str
    provider: str
    count: int
    error_count: int
    latency_p50_ms: int
    latency_p95_ms: int
    prompt_tokens_sum: int
    completion_tokens_sum: int
    cost_usd_sum: float = 0.0


@dataclass(frozen=True)
class MetricsQuery:
    start: datetime
    end: datetime
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationCost:
    conversation_id: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    reasoning_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class LogsQuery:
    start: datetime
    end: datetime
    model: str | None = None
    provider: str | None = None
    status: InferenceStatus | None = None
    limit: int = 100
