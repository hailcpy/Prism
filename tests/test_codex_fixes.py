"""Regression tests for the codex-review-fixes pass.

Each test below pins a behavior surfaced by the adversarial review so a
future change can't quietly regress it.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import prism_sdk
from ingestion_api.main import app as ingestion_app
from prism_infra.bus import InMemoryBus
from prism_infra.models import InferenceEvent, Usage
from prism_infra.storage import InMemoryLogStore, JsonbRawPayloadStore

# --------------------------------------------------------------------------- #
# C1: PrismClient shutdown must be bounded when ingestion is unreachable.
# --------------------------------------------------------------------------- #


class _BoomTransport(httpx.BaseTransport):
    """Always return 503 — exercises the retryable branch in _emit_batch."""

    def __init__(self) -> None:
        self.hits = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.hits += 1
        return httpx.Response(503, json={"detail": "down"})


def test_close_returns_promptly_when_ingestion_503(monkeypatch) -> None:
    transport = _BoomTransport()
    original_client_cls = httpx.Client
    monkeypatch.setattr(
        prism_sdk.httpx,
        "Client",
        lambda *a, **kw: original_client_cls(transport=transport, base_url="http://x"),
    )
    client = prism_sdk.PrismClient(sink="http", flush_interval_ms=5)
    for i in range(50):
        client.enqueue({"i": i})
    started = time.monotonic()
    client.close()
    elapsed = time.monotonic() - started
    # The pre-fix hang would loop forever; bounded by max_rounds=100 batches
    # of at most 100 events each, with the transport call returning quickly.
    assert elapsed < 5, f"close() took {elapsed:.2f}s — shutdown should be bounded"
    assert transport.hits > 0


def test_partial_rejection_response_is_logged(monkeypatch, caplog) -> None:
    class _PartialTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                202,
                json={
                    "accepted": 1,
                    "rejected": [{"index": 0, "reason": "bad uuid"}],
                    "stream_ids": ["1-0"],
                },
            )

    transport = _PartialTransport()
    original_client_cls = httpx.Client
    monkeypatch.setattr(
        prism_sdk.httpx,
        "Client",
        lambda *a, **kw: original_client_cls(transport=transport, base_url="http://x"),
    )
    client = prism_sdk.PrismClient(sink="http", flush_interval_ms=5)
    client.enqueue({"x": 1})
    with caplog.at_level(logging.WARNING, logger="prism-sdk"):
        client.close()
    assert any("rejected" in record.getMessage() for record in caplog.records)


def test_metadata_warns_on_non_uuid(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="prism-sdk"):
        prism_sdk.metadata(conversation_id="not-a-uuid", message_id="m1")
    assert any(
        "not a UUID" in record.getMessage() and "conversation_id" in record.getMessage()
        for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# F5: metadata + error.message must be redacted at the ingestion boundary.
# --------------------------------------------------------------------------- #


def _event_with_pii() -> dict[str, object]:
    started = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    ended = started + timedelta(milliseconds=42)
    return {
        "schema_version": "1.0",
        "inference_id": "01935b3f-0000-7000-8000-000000000001",
        "conversation_id": "01935b3f-0000-7000-8000-000000000002",
        "message_id": "01935b3f-0000-7000-8000-000000000003",
        "model": "gpt-4o",
        "provider": "openai",
        "status": "error",
        "error": {
            "type": "ProviderError",
            "message": "billing failed for card 4111 1111 1111 1111",
        },
        "ts_start": started.isoformat().replace("+00:00", "Z"),
        "ts_end": ended.isoformat().replace("+00:00", "Z"),
        "latency_ms": 42,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "prompt_preview": "hi",
        "response_preview": "hi",
        "metadata": {"user_email": "foo@example.com", "nested": {"phone": "415-555-2671"}},
        "sdk_version": "0.2.0",
    }


def test_ingestion_redacts_metadata_and_error_message() -> None:
    bus = InMemoryBus()
    ingestion_app.state.bus = bus
    ingestion_app.state.raw_payload_store = JsonbRawPayloadStore()
    response = TestClient(ingestion_app).post(
        "/v1/events:batch", json={"events": [_event_with_pii()]}
    )
    assert response.status_code == 202
    event = bus.streams["inference.logged"][0].event
    assert event["metadata"]["user_email"] == "[EMAIL]"
    assert event["metadata"]["nested"]["phone"] == "[PHONE]"
    assert "[CARD]" in event["error"]["message"]
    assert "4111" not in event["error"]["message"]


# --------------------------------------------------------------------------- #
# C2: latency / TTFT percentiles from raw logs are correct across buckets.
# --------------------------------------------------------------------------- #


def _make_event(latency_ms: int, ttft_ms: int | None, model: str, when: datetime) -> InferenceEvent:
    return InferenceEvent(
        schema_version="1.0",
        inference_id=f"id-{latency_ms}-{when.isoformat()}",
        conversation_id=None,
        message_id=None,
        model=model,
        provider="openai",
        status="ok",
        ts_start=when,
        ts_end=when + timedelta(milliseconds=latency_ms),
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        created_at=when,
    )


def test_in_memory_log_percentile_matches_true_value() -> None:
    store = InMemoryLogStore()
    base = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    # 100 events across two minutes with wide latency spread; the p95 of the
    # combined set must NOT equal the average of per-minute p95s.
    for i in range(50):
        store.logs.append(_make_event(latency_ms=10 + i, ttft_ms=5 + i, model="m", when=base))
    for i in range(50):
        store.logs.append(
            _make_event(
                latency_ms=2000 + i,
                ttft_ms=1000 + i,
                model="m",
                when=base + timedelta(seconds=70),
            )
        )
    p95 = store.get_log_percentile(
        start=base - timedelta(minutes=1),
        end=base + timedelta(minutes=5),
        percentile=0.95,
    )
    # True p95 lives in the second cluster (>= 2000ms).
    assert p95 >= 2000, f"got {p95}; averaging per-minute p95s would not exceed ~1050"

    ttft_p95 = store.get_log_percentile(
        start=base - timedelta(minutes=1),
        end=base + timedelta(minutes=5),
        percentile=0.95,
        column="ttft_ms",
    )
    assert ttft_p95 >= 1000


def test_in_memory_log_percentile_series_buckets_correctly() -> None:
    store = InMemoryLogStore()
    base = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    for i in range(10):
        store.logs.append(_make_event(latency_ms=100 + i, ttft_ms=50, model="m", when=base))
    rows = store.get_log_percentile_series(
        start=base - timedelta(minutes=1),
        end=base + timedelta(minutes=2),
        percentile=0.5,
        by_bucket=True,
    )
    assert len(rows) == 1
    bucket, group, value = rows[0]
    assert bucket is not None
    assert group is None
    assert 100 <= value <= 110


def test_in_memory_log_percentile_skips_ttft_when_missing() -> None:
    store = InMemoryLogStore()
    base = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    store.logs.append(_make_event(latency_ms=200, ttft_ms=None, model="m", when=base))
    store.logs.append(_make_event(latency_ms=300, ttft_ms=None, model="m", when=base))
    value = store.get_log_percentile(
        start=base - timedelta(minutes=1),
        end=base + timedelta(minutes=2),
        percentile=0.95,
        column="ttft_ms",
    )
    assert value == 0.0


# --------------------------------------------------------------------------- #
# Bootstrap script is idempotent and never clobbers an existing .env.
# --------------------------------------------------------------------------- #


def _load_bootstrap_env_module():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_env",
        Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_env.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_env_is_idempotent(tmp_path, monkeypatch) -> None:
    bootstrap_env = _load_bootstrap_env_module()
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_text("REDIS_PASSWORD=\nPRISM_CREDS_KEY=\nOTHER=keep\n")
    monkeypatch.setattr(bootstrap_env, "EXAMPLE", example)
    monkeypatch.setattr(bootstrap_env, "TARGET", target)

    assert bootstrap_env.main() == 0
    first = target.read_text()
    assert "REDIS_PASSWORD=" in first
    assert "REDIS_PASSWORD=\n" not in first  # filled
    assert "OTHER=keep" in first

    # Second run must leave the file untouched.
    assert bootstrap_env.main() == 0
    assert target.read_text() == first
    sys.modules.pop("bootstrap_env", None)


# --------------------------------------------------------------------------- #
# Roller TTFT aggregation: per-bucket p50/p95 over ttft_ms.
# --------------------------------------------------------------------------- #


def test_roller_window_bucket_computes_ttft_percentiles() -> None:
    from metrics_roller.main import _WindowBucket

    bucket = _WindowBucket(
        minute_bucket=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
        model="m",
        provider="openai",
    )
    base = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    for i in range(20):
        bucket.add(_make_event(latency_ms=100 + i, ttft_ms=10 + i, model="m", when=base))
    bucket.add(_make_event(latency_ms=500, ttft_ms=None, model="m", when=base))
    row = bucket.to_row()
    assert row.ttft_p50_ms is not None and 10 <= row.ttft_p50_ms <= 30
    assert row.ttft_p95_ms is not None and row.ttft_p95_ms >= row.ttft_p50_ms
    # The latency value with no TTFT must still be counted in latency.
    assert row.count == 21


def test_roller_returns_none_ttft_when_no_streaming_events() -> None:
    from metrics_roller.main import _WindowBucket

    bucket = _WindowBucket(
        minute_bucket=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
        model="m",
        provider="openai",
    )
    base = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    bucket.add(_make_event(latency_ms=42, ttft_ms=None, model="m", when=base))
    row = bucket.to_row()
    assert row.ttft_p50_ms is None
    assert row.ttft_p95_ms is None


# --------------------------------------------------------------------------- #
# Reset ingestion app state between tests.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_ingestion_app_state() -> Iterator[None]:
    ingestion_app.state.bus = None
    ingestion_app.state.raw_payload_store = None
    yield
    ingestion_app.state.bus = None
    ingestion_app.state.raw_payload_store = None
