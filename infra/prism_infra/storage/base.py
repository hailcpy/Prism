from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from prism_infra.models import (
    ConversationCost,
    InferenceEvent,
    LogsQuery,
    MetricsQuery,
    MetricsRow,
    ToolInvocationEvent,
)


class LogStore(Protocol):
    def write_logs_batch(self, events: list[InferenceEvent]) -> None: ...

    def write_tool_events_batch(self, events: list[ToolInvocationEvent]) -> None: ...

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]: ...

    def get_log_percentile(
        self,
        *,
        start: datetime,
        end: datetime,
        percentile: float,
        column: str = "latency_ms",
        models: tuple[str, ...] = (),
        providers: tuple[str, ...] = (),
    ) -> float: ...

    def get_log_percentile_series(
        self,
        *,
        start: datetime,
        end: datetime,
        percentile: float,
        column: str = "latency_ms",
        models: tuple[str, ...] = (),
        providers: tuple[str, ...] = (),
        by_bucket: bool = False,
        bucket_seconds: int = 60,
        group_by: str | None = None,
    ) -> list[tuple[datetime | None, str | None, float]]: ...

    def get_logs(self, query: LogsQuery) -> list[InferenceEvent]: ...

    def get_conversation_cost(self, conversation_id: str) -> ConversationCost: ...

    def get_metric_dimensions(self) -> tuple[list[str], list[str]]: ...


class RawPayloadStore(Protocol):
    def put(
        self, inference_id: str, payload: dict[str, Any] | list[Any]
    ) -> tuple[str | None, Any | None]: ...

    def get(self, uri_or_jsonb: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]: ...
