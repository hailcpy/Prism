from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
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

    def reconcile_metrics(self, start: datetime, end: datetime) -> int:
        buckets: dict[tuple[datetime, str, str], list[InferenceEvent]] = defaultdict(list)
        for event in self.logs:
            created = event.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if not (start <= created < end):
                continue
            bucket = created.astimezone(UTC).replace(second=0, microsecond=0)
            buckets[(bucket, event.model, event.provider)].append(event)
        rows = [
            _build_metrics_row(bucket, model, provider, events)
            for (bucket, model, provider), events in buckets.items()
        ]
        self.upsert_metrics(rows)
        return len(rows)

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


def _build_metrics_row(
    bucket: datetime, model: str, provider: str, events: list[InferenceEvent]
) -> MetricsRow:
    latencies = sorted(e.latency_ms for e in events)
    n = len(latencies)

    def pct(q: float) -> int:
        if not latencies:
            return 0
        idx = max(0, min(n - 1, int(-(-n * q // 1)) - 1))
        return latencies[idx]

    return MetricsRow(
        minute_bucket=bucket,
        model=model,
        provider=provider,
        count=n,
        error_count=sum(1 for e in events if e.status != "ok"),
        latency_p50_ms=pct(0.50),
        latency_p95_ms=pct(0.95),
        prompt_tokens_sum=sum(e.usage.prompt_tokens or 0 for e in events),
        completion_tokens_sum=sum(e.usage.completion_tokens or 0 for e in events),
    )


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
