from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prism_infra.models import InferenceEvent, LogsQuery, MetricsQuery, MetricsRow


class InMemoryLogStore:
    def __init__(self) -> None:
        self.logs: list[InferenceEvent] = []
        self.metrics: dict[tuple[str, str, str], MetricsRow] = {}

    def write_logs_batch(self, events: list[InferenceEvent]) -> None:
        seen = {event.inference_id for event in self.logs}
        for event in events:
            if event.inference_id in seen:
                continue
            self.logs.append(event)
            seen.add(event.inference_id)

    def upsert_metrics(self, rows: list[MetricsRow]) -> None:
        for row in rows:
            key = (row.minute_bucket.isoformat(), row.model, row.provider)
            self.metrics[key] = row

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]:
        return [
            row
            for row in self.metrics.values()
            if query.start <= row.minute_bucket < query.end
            and (not query.models or row.model in query.models)
            and (not query.providers or row.provider in query.providers)
        ]

    def get_logs(self, query: LogsQuery) -> list[InferenceEvent]:
        rows = [
            event
            for event in self.logs
            if event.created_at is not None
            and query.start <= event.created_at < query.end
            and (query.model is None or event.model == query.model)
            and (query.provider is None or event.provider == query.provider)
            and (query.status is None or event.status == query.status)
        ]
        return sorted(rows, key=lambda event: event.created_at, reverse=True)[: query.limit]


class JsonbRawPayloadStore:
    def put(
        self, inference_id: str, payload: dict[str, Any] | list[Any]
    ) -> tuple[str | None, dict[str, Any] | list[Any]]:
        return None, payload

    def get(self, uri_or_jsonb: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
        if isinstance(uri_or_jsonb, str):
            raise ValueError("JsonbRawPayloadStore cannot read URI payloads")
        return uri_or_jsonb


class LocalRawPayloadStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self, inference_id: str, payload: dict[str, Any] | list[Any]
    ) -> tuple[str, dict[str, Any] | list[Any] | None]:
        path = self.root / f"{inference_id}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return path.as_uri(), None

    def get(self, uri_or_jsonb: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
        if not isinstance(uri_or_jsonb, str):
            return uri_or_jsonb
        if not uri_or_jsonb.startswith("file://"):
            raise ValueError("LocalRawPayloadStore only supports file:// URIs")
        return json.loads(Path(uri_or_jsonb.removeprefix("file://")).read_text(encoding="utf-8"))
