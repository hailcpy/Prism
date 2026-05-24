from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prism_infra.models import (
    ConversationCost,
    InferenceEvent,
    LogsQuery,
    MetricsQuery,
    MetricsRow,
    ToolInvocationEvent,
)


class InMemoryLogStore:
    def __init__(self) -> None:
        self.logs: list[InferenceEvent] = []
        self.tool_events: list[ToolInvocationEvent] = []

    def write_logs_batch(self, events: list[InferenceEvent]) -> None:
        seen = {event.inference_id for event in self.logs}
        for event in events:
            if event.inference_id in seen:
                continue
            self.logs.append(event)
            seen.add(event.inference_id)

    def write_tool_events_batch(self, events: list[ToolInvocationEvent]) -> None:
        seen = {event.tool_invocation_id for event in self.tool_events}
        for event in events:
            if event.tool_invocation_id in seen:
                continue
            self.tool_events.append(event)
            seen.add(event.tool_invocation_id)

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]:
        buckets: dict[tuple[datetime, str, str], list[InferenceEvent]] = defaultdict(list)
        for event in self.logs:
            created = event.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if not (query.start <= created < query.end):
                continue
            if query.models and event.model not in query.models:
                continue
            if query.providers and event.provider not in query.providers:
                continue
            bucket = created.astimezone(UTC).replace(second=0, microsecond=0)
            buckets[(bucket, event.model, event.provider)].append(event)
        return sorted(
            [_build_metrics_row(b, m, p, evts) for (b, m, p), evts in buckets.items()],
            key=lambda r: (r.minute_bucket, r.model, r.provider),
        )

    def get_conversation_cost(self, conversation_id: str) -> ConversationCost:
        matches = [e for e in self.logs if e.conversation_id == conversation_id]
        return ConversationCost(
            conversation_id=conversation_id,
            calls=len(matches),
            prompt_tokens=sum(e.usage.prompt_tokens or 0 for e in matches),
            completion_tokens=sum(e.usage.completion_tokens or 0 for e in matches),
            cached_prompt_tokens=sum(e.usage.cached_prompt_tokens or 0 for e in matches),
            reasoning_tokens=sum(e.usage.reasoning_tokens or 0 for e in matches),
            cost_usd=sum(e.cost_usd or 0.0 for e in matches),
        )

    def get_log_percentile(
        self,
        *,
        start: datetime,
        end: datetime,
        percentile: float,
        column: str = "latency_ms",
        models: tuple[str, ...] = (),
        providers: tuple[str, ...] = (),
    ) -> float:
        values = [
            v
            for _, _, v in self._collect_percentile_inputs(
                start=start,
                end=end,
                column=column,
                models=models,
                providers=providers,
                by_bucket=False,
                bucket_seconds=60,
                group_by=None,
            )
        ]
        if not values:
            return 0.0
        return _percentile_cont(values, percentile)

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
    ) -> list[tuple[datetime | None, str | None, float]]:
        if group_by is not None and group_by not in {"model", "provider"}:
            raise ValueError("group_by must be one of: model, provider")
        groups: dict[tuple[datetime | None, str | None], list[int]] = {}
        for bucket, grp, value in self._collect_percentile_inputs(
            start=start,
            end=end,
            column=column,
            models=models,
            providers=providers,
            by_bucket=by_bucket,
            bucket_seconds=bucket_seconds,
            group_by=group_by,
        ):
            groups.setdefault((bucket, grp), []).append(value)
        rows = [
            (bucket, grp, _percentile_cont(values, percentile))
            for (bucket, grp), values in groups.items()
        ]
        return sorted(rows, key=lambda r: (r[0] or datetime.min.replace(tzinfo=UTC), r[1] or ""))

    def _collect_percentile_inputs(
        self,
        *,
        start: datetime,
        end: datetime,
        column: str,
        models: tuple[str, ...],
        providers: tuple[str, ...],
        by_bucket: bool,
        bucket_seconds: int,
        group_by: str | None,
    ) -> list[tuple[datetime | None, str | None, int]]:
        if column not in {"latency_ms", "ttft_ms"}:
            raise ValueError("column must be latency_ms or ttft_ms")
        out: list[tuple[datetime | None, str | None, int]] = []
        for event in self.logs:
            created = event.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if not (start <= created < end):
                continue
            if event.status != "ok":
                continue
            value = event.latency_ms if column == "latency_ms" else event.ttft_ms
            if value is None:
                continue
            if models and event.model not in models:
                continue
            if providers and event.provider not in providers:
                continue
            bucket = None
            if by_bucket:
                epoch = int(created.timestamp())
                bucket = datetime.fromtimestamp((epoch // bucket_seconds) * bucket_seconds, tz=UTC)
            grp = None
            if group_by == "model":
                grp = event.model
            elif group_by == "provider":
                grp = event.provider
            out.append((bucket, grp, value))
        return out

    def get_metric_dimensions(self) -> tuple[list[str], list[str]]:
        models = sorted({e.model for e in self.logs if e.model})
        providers = sorted({e.provider for e in self.logs if e.provider})
        return models, providers

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
    ttfts = sorted(e.ttft_ms for e in events if e.ttft_ms is not None)
    n = len(latencies)

    def pct_disc(values: list[int], q: float) -> int:
        if not values:
            return 0
        idx = max(0, min(len(values) - 1, int(-(-len(values) * q // 1)) - 1))
        return values[idx]

    return MetricsRow(
        minute_bucket=bucket,
        model=model,
        provider=provider,
        count=n,
        error_count=sum(1 for e in events if e.status != "ok"),
        latency_p50_ms=pct_disc(latencies, 0.50),
        latency_p95_ms=pct_disc(latencies, 0.95),
        ttft_p50_ms=pct_disc(ttfts, 0.50) if ttfts else None,
        ttft_p95_ms=pct_disc(ttfts, 0.95) if ttfts else None,
        prompt_tokens_sum=sum(e.usage.prompt_tokens or 0 for e in events),
        completion_tokens_sum=sum(e.usage.completion_tokens or 0 for e in events),
        cost_usd_sum=sum(e.cost_usd or 0.0 for e in events),
    )


def _percentile_cont(values: list[int], percentile: float) -> float:
    if not 0.0 < percentile < 1.0:
        raise ValueError("percentile must be in (0, 1)")
    if not values:
        return 0.0
    values = sorted(values)
    rank = percentile * (len(values) - 1)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (rank - low)


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
