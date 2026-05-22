from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import HTTPException

from chatbot_api.dashboards import (
    DashboardLayout,
    Widget,
    WidgetCell,
    WidgetFilters,
    WidgetOptions,
    _aggregate_scalar,
    _enforce_pair,
    _resolve_pie,
    _resolve_timeseries,
    _validate_layout,
)
from prism_infra.models import MetricsRow


def _widget(
    kind: str = "timeseries",
    metric_kind: str = "cost_usd_sum",
    group_by: str | None = "model",
) -> Widget:
    return Widget(
        kind=cast(Any, kind),
        metric_kind=cast(Any, metric_kind),
        title=None,
        filters=WidgetFilters(),
        options=WidgetOptions(group_by=cast(Any, group_by)),
    )


def _cell(i: str, widget: Widget) -> WidgetCell:
    return WidgetCell(i=i, x=0, y=0, w=4, h=2, widget=widget)


def test_enforce_pair_top_conversations_requires_table() -> None:
    widget = _widget(kind="bignum", metric_kind="top_conversations_by_cost", group_by=None)
    with pytest.raises(HTTPException) as excinfo:
        _enforce_pair(widget)
    assert excinfo.value.status_code == 422


def test_enforce_pair_pie_requires_group_by() -> None:
    widget = _widget(kind="pie", metric_kind="cost_usd_sum", group_by=None)
    with pytest.raises(HTTPException) as excinfo:
        _enforce_pair(widget)
    assert excinfo.value.status_code == 422


def test_enforce_pair_accepts_valid_combo() -> None:
    _enforce_pair(_widget(kind="timeseries", metric_kind="cost_usd_sum"))
    _enforce_pair(_widget(kind="table", metric_kind="top_conversations_by_cost", group_by=None))


def test_validate_layout_rejects_duplicate_ids() -> None:
    widget = _widget()
    layout = DashboardLayout(cells=[_cell("a", widget), _cell("a", widget)])
    with pytest.raises(HTTPException) as excinfo:
        _validate_layout(layout)
    assert excinfo.value.status_code == 422


def test_aggregate_scalar_sums_cost() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    rows = [
        MetricsRow(
            minute_bucket=bucket,
            model="gpt-4o",
            provider="openai",
            count=2,
            error_count=0,
            latency_p50_ms=100,
            latency_p95_ms=200,
            prompt_tokens_sum=10,
            completion_tokens_sum=5,
            cost_usd_sum=0.5,
        ),
        MetricsRow(
            minute_bucket=bucket + timedelta(minutes=1),
            model="claude",
            provider="anthropic",
            count=3,
            error_count=1,
            latency_p50_ms=150,
            latency_p95_ms=250,
            prompt_tokens_sum=20,
            completion_tokens_sum=10,
            cost_usd_sum=1.25,
        ),
    ]
    assert _aggregate_scalar(rows, "cost_usd_sum") == pytest.approx(1.75)
    assert _aggregate_scalar(rows, "count") == 5
    assert _aggregate_scalar(rows, "error_rate") == pytest.approx(0.2)


def test_aggregate_scalar_weights_latency_by_count() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    rows = [
        MetricsRow(
            minute_bucket=bucket,
            model="a",
            provider="p",
            count=1,
            error_count=0,
            latency_p50_ms=100,
            latency_p95_ms=100,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
        ),
        MetricsRow(
            minute_bucket=bucket,
            model="b",
            provider="p",
            count=3,
            error_count=0,
            latency_p50_ms=200,
            latency_p95_ms=200,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
        ),
    ]
    # weighted average: (100*1 + 200*3) / 4 = 175
    assert _aggregate_scalar(rows, "latency_p50_ms") == pytest.approx(175.0)


def test_resolve_timeseries_groups_by_model() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    rows = [
        MetricsRow(
            minute_bucket=bucket,
            model="gpt-4o",
            provider="openai",
            count=1,
            error_count=0,
            latency_p50_ms=0,
            latency_p95_ms=0,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
            cost_usd_sum=0.5,
        ),
        MetricsRow(
            minute_bucket=bucket,
            model="claude",
            provider="anthropic",
            count=1,
            error_count=0,
            latency_p50_ms=0,
            latency_p95_ms=0,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
            cost_usd_sum=0.75,
        ),
    ]
    result = _resolve_timeseries(rows, "cost_usd_sum", "model")
    assert result["kind"] == "timeseries"
    assert set(result["series"]) == {"gpt-4o", "claude"}
    assert result["series"]["claude"][0]["value"] == pytest.approx(0.75)


def test_resolve_pie_sorts_by_value() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    rows = [
        MetricsRow(
            minute_bucket=bucket,
            model="small",
            provider="x",
            count=1,
            error_count=0,
            latency_p50_ms=0,
            latency_p95_ms=0,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
            cost_usd_sum=0.1,
        ),
        MetricsRow(
            minute_bucket=bucket,
            model="big",
            provider="x",
            count=1,
            error_count=0,
            latency_p50_ms=0,
            latency_p95_ms=0,
            prompt_tokens_sum=0,
            completion_tokens_sum=0,
            cost_usd_sum=2.0,
        ),
    ]
    result = _resolve_pie(rows, "cost_usd_sum", "model")
    slices = result["slices"]
    assert [s["label"] for s in slices] == ["big", "small"]
