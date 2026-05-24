from datetime import UTC, datetime, timedelta
from pathlib import Path

from prism_infra.bus import InMemoryBus
from prism_infra.models import InferenceEvent, LogsQuery, MetricsQuery, Usage
from prism_infra.storage import InMemoryLogStore, JsonbRawPayloadStore, LocalRawPayloadStore


def test_in_memory_bus_publish_consume_ack() -> None:
    bus = InMemoryBus()

    stream_id = bus.publish("inference.logged", {"inference_id": "abc"})
    messages = bus.consume("inference.logged", "cg-writer", "worker-1")
    bus.ack("inference.logged", "cg-writer", [stream_id])

    assert stream_id == "1-0"
    assert len(messages) == 1
    assert messages[0].event == {"inference_id": "abc"}


def test_in_memory_bus_claims_unacked_pending_messages() -> None:
    bus = InMemoryBus()

    stream_id = bus.publish("inference.logged", {"inference_id": "abc"})
    bus.consume("inference.logged", "cg-writer", "worker-1")
    claimed = bus.claim_pending("inference.logged", "cg-writer", "worker-2", min_idle_ms=0)
    bus.ack("inference.logged", "cg-writer", [stream_id])

    assert [message.id for message in claimed] == [stream_id]
    assert bus.claim_pending("inference.logged", "cg-writer", "worker-2", min_idle_ms=0) == []


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
    store.write_logs_batch([event])

    logs = store.get_logs(
        LogsQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    )
    metrics = store.get_metrics(
        MetricsQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    )

    assert logs == [event]
    assert len(metrics) == 1
    assert metrics[0].count == 1
    assert metrics[0].model == "gpt-4o"
    assert metrics[0].prompt_tokens_sum == 10


def test_in_memory_log_store_dedupes_by_inference_id() -> None:
    now = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    store = InMemoryLogStore()
    event = InferenceEvent(
        schema_version="1.0",
        inference_id="01935b3f-0000-7000-8000-000000000001",
        conversation_id=None,
        message_id=None,
        model="gpt-4o",
        provider="openai",
        status="ok",
        ts_start=now,
        ts_end=now + timedelta(milliseconds=42),
        latency_ms=42,
        created_at=now,
    )
    retry = InferenceEvent(
        schema_version=event.schema_version,
        inference_id=event.inference_id,
        conversation_id=event.conversation_id,
        message_id=event.message_id,
        model=event.model,
        provider=event.provider,
        status=event.status,
        ts_start=event.ts_start,
        ts_end=event.ts_end,
        latency_ms=event.latency_ms,
        created_at=now + timedelta(seconds=1),
    )

    store.write_logs_batch([event, retry])

    assert store.logs == [event]
