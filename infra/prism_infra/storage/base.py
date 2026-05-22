from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from prism_infra.models import (
    InferenceEvent,
    LogsQuery,
    MetricsQuery,
    MetricsRow,
    ToolInvocationEvent,
)


class LogStore(Protocol):
    def write_logs_batch(self, events: list[InferenceEvent]) -> None: ...

    def write_tool_events_batch(self, events: list[ToolInvocationEvent]) -> None: ...

    def upsert_metrics(self, rows: list[MetricsRow]) -> None: ...

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]: ...

    def get_logs(self, query: LogsQuery) -> list[InferenceEvent]: ...

    def reconcile_metrics(self, start: datetime, end: datetime) -> int: ...


class RawPayloadStore(Protocol):
    def put(
        self, inference_id: str, payload: dict[str, Any] | list[Any]
    ) -> tuple[str | None, Any | None]: ...

    def get(self, uri_or_jsonb: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]: ...
