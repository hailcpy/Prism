from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from prism_infra.models import MetricsQuery, MetricsRow
from prism_infra.storage import (
    DashboardStore,
    LogStore,
    PostgresDashboardStore,
    PostgresLogStore,
    get_top_conversations_by_cost,
)

WidgetKind = Literal["timeseries", "bignum", "table", "pie"]
MetricKind = Literal[
    "cost_usd_sum",
    "count",
    "error_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "prompt_tokens_sum",
    "completion_tokens_sum",
    "top_conversations_by_cost",
]
GroupBy = Literal["model", "provider"]

METRICS_MINUTE_KINDS: set[str] = {
    "cost_usd_sum",
    "count",
    "error_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "prompt_tokens_sum",
    "completion_tokens_sum",
}


class WidgetFilters(BaseModel):
    model: list[str] = Field(default_factory=list)
    provider: list[str] = Field(default_factory=list)


class WidgetOptions(BaseModel):
    group_by: GroupBy | None = None
    limit: int = Field(default=10, ge=1, le=50)


class Widget(BaseModel):
    kind: WidgetKind
    metric_kind: MetricKind
    title: str | None = None
    filters: WidgetFilters = Field(default_factory=WidgetFilters)
    options: WidgetOptions = Field(default_factory=WidgetOptions)

    @field_validator("metric_kind")
    @classmethod
    def _validate_pair(cls, value: MetricKind, info: Any) -> MetricKind:
        return value


class WidgetCell(BaseModel):
    i: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=24)
    widget: Widget


class DashboardLayout(BaseModel):
    cells: list[WidgetCell]


class DashboardBody(BaseModel):
    id: str
    name: str
    owner_id: str | None
    layout: DashboardLayout
    created_at: datetime
    updated_at: datetime


class DashboardSummary(BaseModel):
    id: str
    name: str
    updated_at: datetime


class ListDashboardsResponse(BaseModel):
    dashboards: list[DashboardSummary]


class CreateDashboardRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    layout: DashboardLayout = Field(default_factory=lambda: DashboardLayout(cells=[]))


class UpdateDashboardRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    layout: DashboardLayout | None = None


class DashboardDataResponse(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    widgets: dict[str, Any]

    model_config = {"populate_by_name": True}


router = APIRouter(prefix="/v1/dashboards", tags=["dashboards"])


def _enforce_pair(widget: Widget) -> None:
    if widget.metric_kind == "top_conversations_by_cost" and widget.kind != "table":
        raise HTTPException(
            status_code=422,
            detail=f"metric_kind=top_conversations_by_cost requires kind=table, got {widget.kind}",
        )
    if widget.kind == "pie" and widget.options.group_by is None:
        raise HTTPException(
            status_code=422,
            detail="pie widgets require options.group_by ∈ {model, provider}",
        )


def _validate_layout(layout: DashboardLayout) -> None:
    seen: set[str] = set()
    for cell in layout.cells:
        if cell.i in seen:
            raise HTTPException(status_code=422, detail=f"duplicate cell id: {cell.i}")
        seen.add(cell.i)
        _enforce_pair(cell.widget)


@router.get("", response_model=ListDashboardsResponse)
def list_dashboards(request: Request) -> ListDashboardsResponse:
    store = _get_dashboard_store(request.app)
    return ListDashboardsResponse(
        dashboards=[
            DashboardSummary(id=d.id, name=d.name, updated_at=d.updated_at)
            for d in store.list_dashboards()
        ]
    )


@router.post("", status_code=201, response_model=DashboardBody)
def create_dashboard(request: Request, body: CreateDashboardRequest) -> DashboardBody:
    _validate_layout(body.layout)
    store = _get_dashboard_store(request.app)
    dashboard = store.create_dashboard(
        name=body.name,
        layout=[cell.model_dump(mode="json") for cell in body.layout.cells],
    )
    return _to_body(dashboard)


@router.get("/{dashboard_id}", response_model=DashboardBody)
def get_dashboard(request: Request, dashboard_id: str) -> DashboardBody:
    dashboard = _get_dashboard_store(request.app).get_dashboard(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="dashboard not found")
    return _to_body(dashboard)


@router.put("/{dashboard_id}", response_model=DashboardBody)
def update_dashboard(
    request: Request, dashboard_id: str, body: UpdateDashboardRequest
) -> DashboardBody:
    layout_dump: list[dict[str, Any]] | None = None
    if body.layout is not None:
        _validate_layout(body.layout)
        layout_dump = [cell.model_dump(mode="json") for cell in body.layout.cells]
    dashboard = _get_dashboard_store(request.app).update_dashboard(
        dashboard_id, name=body.name, layout=layout_dump
    )
    if dashboard is None:
        raise HTTPException(status_code=404, detail="dashboard not found")
    return _to_body(dashboard)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(request: Request, dashboard_id: str) -> None:
    if not _get_dashboard_store(request.app).delete_dashboard(dashboard_id):
        raise HTTPException(status_code=404, detail="dashboard not found")


@router.get("/{dashboard_id}/data", response_model=DashboardDataResponse)
def get_dashboard_data(
    request: Request,
    dashboard_id: str,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> DashboardDataResponse:
    dashboard = _get_dashboard_store(request.app).get_dashboard(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="dashboard not found")
    layout = DashboardLayout.model_validate({"cells": dashboard.layout})
    end = to or datetime.now(UTC)
    start = from_ or end - timedelta(hours=1)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    log_store = _get_log_store(request.app)
    database_url = os.environ["DATABASE_URL"]
    widgets_data: dict[str, Any] = {}
    for cell in layout.cells:
        widgets_data[cell.i] = _resolve_widget(
            cell.widget, start=start, end=end, log_store=log_store, database_url=database_url
        )
    return DashboardDataResponse.model_validate({"from": start, "to": end, "widgets": widgets_data})


def _resolve_widget(
    widget: Widget,
    *,
    start: datetime,
    end: datetime,
    log_store: LogStore,
    database_url: str,
) -> dict[str, Any]:
    if widget.metric_kind == "top_conversations_by_cost":
        rows = get_top_conversations_by_cost(
            database_url,
            start=start,
            end=end,
            limit=widget.options.limit,
            models=tuple(widget.filters.model),
            providers=tuple(widget.filters.provider),
        )
        return {
            "kind": "table",
            "columns": [
                "conversation_id",
                "cost_usd",
                "calls",
                "prompt_tokens",
                "completion_tokens",
            ],
            "rows": [
                {
                    "conversation_id": row.conversation_id,
                    "cost_usd": row.cost_usd,
                    "calls": row.calls,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                }
                for row in rows
            ],
        }

    rows = log_store.get_metrics(
        MetricsQuery(
            start=start,
            end=end,
            models=tuple(widget.filters.model),
            providers=tuple(widget.filters.provider),
        )
    )
    metric_kind = cast(str, widget.metric_kind)

    if widget.kind == "timeseries":
        return _resolve_timeseries(rows, metric_kind, widget.options.group_by)
    if widget.kind == "bignum":
        return {"kind": "bignum", "value": _aggregate_scalar(rows, metric_kind)}
    if widget.kind == "pie":
        return _resolve_pie(rows, metric_kind, widget.options.group_by or "model")
    return _resolve_table(rows, metric_kind, widget.options.group_by)


def _row_value(row: MetricsRow, metric_kind: str) -> float:
    if metric_kind == "error_rate":
        return (row.error_count / row.count) if row.count else 0.0
    return float(getattr(row, metric_kind))


def _aggregate_scalar(rows: list[MetricsRow], metric_kind: str) -> float:
    if not rows:
        return 0.0
    if metric_kind in {"latency_p50_ms", "latency_p95_ms"}:
        weighted = sum(_row_value(row, metric_kind) * row.count for row in rows)
        total = sum(row.count for row in rows)
        return (weighted / total) if total else 0.0
    if metric_kind == "error_rate":
        errors = sum(row.error_count for row in rows)
        total = sum(row.count for row in rows)
        return (errors / total) if total else 0.0
    return sum(_row_value(row, metric_kind) for row in rows)


def _resolve_timeseries(
    rows: list[MetricsRow], metric_kind: str, group_by: GroupBy | None
) -> dict[str, Any]:
    series: dict[str, list[dict[str, Any]]] = {}
    if group_by is None:
        bucket_totals: dict[datetime, list[MetricsRow]] = {}
        for row in rows:
            bucket_totals.setdefault(row.minute_bucket, []).append(row)
        points = [
            {"bucket": bucket.isoformat(), "value": _aggregate_scalar(bucket_rows, metric_kind)}
            for bucket, bucket_rows in sorted(bucket_totals.items())
        ]
        series["all"] = points
    else:
        grouped: dict[str, dict[datetime, list[MetricsRow]]] = {}
        for row in rows:
            key = row.model if group_by == "model" else row.provider
            grouped.setdefault(key, {}).setdefault(row.minute_bucket, []).append(row)
        for key, buckets in grouped.items():
            series[key] = [
                {"bucket": bucket.isoformat(), "value": _aggregate_scalar(bucket_rows, metric_kind)}
                for bucket, bucket_rows in sorted(buckets.items())
            ]
    return {"kind": "timeseries", "group_by": group_by, "series": series}


def _resolve_pie(rows: list[MetricsRow], metric_kind: str, group_by: GroupBy) -> dict[str, Any]:
    grouped: dict[str, list[MetricsRow]] = {}
    for row in rows:
        key = row.model if group_by == "model" else row.provider
        grouped.setdefault(key, []).append(row)
    slices = [
        {"label": key, "value": _aggregate_scalar(group_rows, metric_kind)}
        for key, group_rows in grouped.items()
    ]
    slices.sort(key=lambda item: item["value"], reverse=True)
    return {"kind": "pie", "group_by": group_by, "slices": slices}


def _resolve_table(
    rows: list[MetricsRow], metric_kind: str, group_by: GroupBy | None
) -> dict[str, Any]:
    if group_by is None:
        bucket_totals: dict[datetime, list[MetricsRow]] = {}
        for row in rows:
            bucket_totals.setdefault(row.minute_bucket, []).append(row)
        return {
            "kind": "table",
            "columns": ["bucket", "value"],
            "rows": [
                {"bucket": bucket.isoformat(), "value": _aggregate_scalar(bucket_rows, metric_kind)}
                for bucket, bucket_rows in sorted(bucket_totals.items())
            ],
        }
    grouped: dict[str, list[MetricsRow]] = {}
    for row in rows:
        key = row.model if group_by == "model" else row.provider
        grouped.setdefault(key, []).append(row)
    return {
        "kind": "table",
        "columns": [group_by, "value"],
        "rows": [
            {group_by: key, "value": _aggregate_scalar(group_rows, metric_kind)}
            for key, group_rows in sorted(grouped.items())
        ],
    }


def _to_body(dashboard: Any) -> DashboardBody:
    return DashboardBody(
        id=dashboard.id,
        name=dashboard.name,
        owner_id=dashboard.owner_id,
        layout=DashboardLayout.model_validate({"cells": dashboard.layout}),
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


def _get_dashboard_store(app: Any) -> DashboardStore:
    if getattr(app.state, "dashboard_store", None) is None:
        app.state.dashboard_store = PostgresDashboardStore(os.environ["DATABASE_URL"])
    return cast(DashboardStore, app.state.dashboard_store)


def _get_log_store(app: Any) -> LogStore:
    if getattr(app.state, "log_store", None) is None:
        app.state.log_store = PostgresLogStore(os.environ["DATABASE_URL"])
    return cast(LogStore, app.state.log_store)
