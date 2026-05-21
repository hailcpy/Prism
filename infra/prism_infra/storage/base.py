from __future__ import annotations

from typing import Any, Protocol

from prism_infra.models import InferenceEvent, LogsQuery, MetricsQuery, MetricsRow


class LogStore(Protocol):
    def write_logs_batch(self, events: list[InferenceEvent]) -> None: ...

    def upsert_metrics(self, rows: list[MetricsRow]) -> None: ...

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]: ...

    def get_logs(self, query: LogsQuery) -> list[InferenceEvent]: ...


class RawPayloadStore(Protocol):
    def put(
        self, inference_id: str, payload: dict[str, Any] | list[Any]
    ) -> tuple[str | None, Any | None]: ...

    def get(self, uri_or_jsonb: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]: ...
