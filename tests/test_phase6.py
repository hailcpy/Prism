from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chatbot_api.main import _get_log_store, app
from prism_infra.models import (
    InferenceEvent,
    InferenceStatus,
    LogsQuery,
    MetricsQuery,
    Usage,
)
from prism_infra.storage import InMemoryLogStore


def _event(
    *,
    inference_id: str,
    bucket: datetime,
    model: str = "gpt-4o",
    provider: str = "openai",
    status: InferenceStatus = "ok",
    latency_ms: int = 100,
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 5,
    offset_seconds: float = 0.0,
) -> InferenceEvent:
    created = bucket + timedelta(seconds=offset_seconds)
    return InferenceEvent(
        schema_version="1.0",
        inference_id=inference_id,
        conversation_id=None,
        message_id=None,
        model=model,
        provider=provider,
        status=status,
        ts_start=created,
        ts_end=created + timedelta(milliseconds=latency_ms),
        latency_ms=latency_ms,
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(prompt_tokens or 0) + (completion_tokens or 0),
        ),
        created_at=created,
    )


def test_get_metrics_aggregates_from_logs() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    store = InMemoryLogStore()
    events = [
        _event(
            inference_id=f"00000000-0000-7000-8000-{i:012d}",
            bucket=bucket,
            latency_ms=latency,
            status="error" if latency >= 400 else "ok",
            offset_seconds=i,
        )
        for i, latency in enumerate([100, 200, 300, 400])
    ]
    store.write_logs_batch(events)

    rows = store.get_metrics(
        MetricsQuery(start=bucket - timedelta(minutes=1), end=bucket + timedelta(minutes=1))
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.count == 4
    assert row.error_count == 1
    assert row.latency_p50_ms == 200
    assert row.latency_p95_ms == 400


def test_metrics_endpoint_returns_buckets() -> None:
    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    store = InMemoryLogStore()
    store.write_logs_batch(
        [
            _event(
                inference_id="00000000-0000-7000-8000-000000000001",
                bucket=bucket,
                model="gpt-4o",
                provider="openai",
                latency_ms=120,
                prompt_tokens=100,
                completion_tokens=50,
            ),
            _event(
                inference_id="00000000-0000-7000-8000-000000000002",
                bucket=bucket,
                model="claude",
                provider="anthropic",
                latency_ms=200,
                prompt_tokens=100,
                completion_tokens=50,
            ),
        ]
    )
    app.state.log_store = store
    try:
        client = TestClient(app)
        from_ts = (bucket - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        to_ts = (bucket + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        response = client.get(f"/v1/metrics?from={from_ts}&to={to_ts}")
        assert response.status_code == 200
        body = response.json()
        assert len(body["buckets"]) == 2

        response = client.get(f"/v1/metrics?from={from_ts}&to={to_ts}&model=gpt-4o")
        models = {b["model"] for b in response.json()["buckets"]}
        assert models == {"gpt-4o"}

        bad = client.get(f"/v1/metrics?from={from_ts}&to={to_ts}&interval=5m")
        assert bad.status_code == 400
    finally:
        app.state.log_store = None


def test_metrics_endpoint_log_store_helper_caches() -> None:
    app.state.log_store = InMemoryLogStore()
    try:
        assert _get_log_store(app) is app.state.log_store
    finally:
        app.state.log_store = None


def test_metrics_query_unused_logs_query() -> None:
    assert LogsQuery is not None


def test_conversation_cost_endpoint_aggregates_inference_logs(monkeypatch) -> None:
    from chatbot_api.main import _get_store
    from prism_infra.models import Usage

    bucket = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    log_store = InMemoryLogStore()
    log_store.write_logs_batch(
        [
            InferenceEvent(
                schema_version="1.0",
                inference_id="00000000-0000-7000-8000-000000000001",
                conversation_id="conv-1",
                message_id=None,
                model="gpt-4o",
                provider="openai",
                status="ok",
                ts_start=bucket,
                ts_end=bucket + timedelta(milliseconds=100),
                latency_ms=100,
                usage=Usage(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cached_prompt_tokens=2,
                    reasoning_tokens=1,
                ),
                cost_usd=0.001,
                created_at=bucket,
            ),
            InferenceEvent(
                schema_version="1.0",
                inference_id="00000000-0000-7000-8000-000000000002",
                conversation_id="conv-1",
                message_id=None,
                model="gpt-4o",
                provider="openai",
                status="ok",
                ts_start=bucket,
                ts_end=bucket + timedelta(milliseconds=100),
                latency_ms=100,
                usage=Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
                cost_usd=0.003,
                created_at=bucket,
            ),
        ]
    )

    class FakeStore:
        def get_conversation(self, conversation_id: str) -> Any:
            return object() if conversation_id == "conv-1" else None

    app.state.log_store = log_store
    app.state.chat_store = FakeStore()
    try:
        client = TestClient(app)
        response = client.get("/v1/conversations/conv-1/cost")
        assert response.status_code == 200
        body = response.json()
        assert body["calls"] == 2
        assert body["prompt_tokens"] == 30
        assert body["completion_tokens"] == 15
        assert body["cached_prompt_tokens"] == 2
        assert body["reasoning_tokens"] == 1
        assert body["cost_usd"] == pytest.approx(0.004)

        missing = client.get("/v1/conversations/conv-x/cost")
        assert missing.status_code == 404

        assert _get_store(app) is app.state.chat_store
    finally:
        app.state.log_store = None
        app.state.chat_store = None
