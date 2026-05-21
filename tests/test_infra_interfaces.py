from datetime import UTC, datetime, timedelta
from pathlib import Path

from prism_infra.bus import InMemoryBus
from prism_infra.models import InferenceEvent, LogsQuery, MetricsQuery, MetricsRow, Usage
from prism_infra.storage import InMemoryLogStore, JsonbRawPayloadStore, LocalRawPayloadStore


def test_in_memory_bus_publish_consume_ack() -> None:
    bus = InMemoryBus()

    stream_id = bus.publish("inference.logged", {"inference_id": "abc"})
    messages = bus.consume("inference.logged", "cg-writer", "worker-1")
    bus.ack("inference.logged", "cg-writer", [stream_id])

    assert stream_id == "1-0"
    assert len(messages) == 1
    assert messages[0].event == {"inference_id": "abc"}


def test_raw_payload_store_embeds_jsonb() -> None:
    payload = {"request": {"prompt": "hello"}}
    uri, embedded = JsonbRawPayloadStore().put("inf-1", payload)

    assert uri is None
    assert embedded == payload


def test_local_raw_payload_store_writes_file(tmp_path: Path) -> None:
    payload = {"request": {"prompt": "hello"}}
    store = LocalRawPayloadStore(tmp_path)

    uri, embedded = store.put("inf-1", payload)

    assert uri.startswith("file://")
    assert embedded is None
    assert store.get(uri) == payload


def test_in_memory_log_store_writes_logs_and_metrics() -> None:
    now = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    store = InMemoryLogStore()
    event = InferenceEvent(
        schema_version="1.0",
        inference_id="01935b3f-0000-7000-8000-000000000001",
        conversation_id="01935b3f-0000-7000-8000-000000000002",
        message_id="01935b3f-0000-7000-8000-000000000003",
        model="gpt-4o",
        provider="openai",
        status="ok",
        ts_start=now,
        ts_end=now + timedelta(milliseconds=42),
        latency_ms=42,
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        prompt_preview="hello",
        response_preview="hi",
        created_at=now,
    )
    metric = MetricsRow(
        minute_bucket=now,
        model="gpt-4o",
        provider="openai",
        count=1,
        error_count=0,
        latency_p50_ms=42,
        latency_p95_ms=42,
        prompt_tokens_sum=10,
        completion_tokens_sum=5,
    )

    store.write_logs_batch([event])
    store.upsert_metrics([metric])

    logs = store.get_logs(
        LogsQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    )
    metrics = store.get_metrics(
        MetricsQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    )

    assert logs == [event]
    assert metrics == [metric]
