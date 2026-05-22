from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ingestion_api.main import app
from log_writer.main import process_messages
from prism_infra.bus import InMemoryBus
from prism_infra.storage import InMemoryLogStore, JsonbRawPayloadStore


def test_ingestion_accepts_redacts_and_publishes(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus
    app.state.raw_payload_store = JsonbRawPayloadStore()

    response = TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert response.json()["rejected"] == []

    event = bus.streams["inference.logged"][0].event
    assert event["prompt_preview"] == "email [EMAIL] ssn [SSN]"
    assert event["response_preview"] == "call [PHONE]"
    assert "raw_payload" not in event
    assert "raw_payload_jsonb" not in event


def test_ingestion_rejects_bad_event_without_rejecting_batch(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus

    response = TestClient(app).post(
        "/v1/events:batch",
        json={"events": [{}, _event_payload()]},
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert response.json()["rejected"][0]["index"] == 0
    assert len(bus.streams["inference.logged"]) == 1


def test_ingestion_keeps_redacted_raw_payload_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PRISM_KEEP_RAW", "true")
    bus = InMemoryBus()
    app.state.bus = bus
    app.state.raw_payload_store = JsonbRawPayloadStore()

    response = TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})

    assert response.status_code == 202
    event = bus.streams["inference.logged"][0].event
    assert event["raw_payload_jsonb"]["request"]["text"] == "pay with [CARD]"


def test_log_writer_processes_stream_messages(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus
    TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})
    messages = bus.consume("inference.logged", "cg-writer", "worker-1")
    store = InMemoryLogStore()

    accepted, rejected = process_messages(messages, bus=bus, store=store)

    assert accepted == 1
    assert rejected == 0
    assert len(store.logs) == 1
    assert store.logs[0].prompt_preview == "email [EMAIL] ssn [SSN]"


def test_ingestion_accepts_tool_invocation_events(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus

    response = TestClient(app).post("/v1/events:batch", json={"events": [_tool_event_payload()]})

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    event = bus.streams["inference.logged"][0].event
    assert event["event_type"] == "tool_invocation"
    assert event["arguments_preview"] == '{"email":"[EMAIL]"}'
    assert event["result_preview"] == "ssn [SSN]"


def test_log_writer_routes_tool_invocation_events(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus
    TestClient(app).post(
        "/v1/events:batch", json={"events": [_event_payload(), _tool_event_payload()]}
    )
    messages = bus.consume("inference.logged", "cg-writer", "worker-1", count=2)
    store = InMemoryLogStore()

    accepted, rejected = process_messages(messages, bus=bus, store=store)

    assert accepted == 2
    assert rejected == 0
    assert len(store.logs) == 1
    assert len(store.tool_events) == 1
    assert store.tool_events[0].tool_name == "web_search"


def test_log_writer_can_process_claimed_pending_messages(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus
    TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})
    bus.consume("inference.logged", "cg-writer", "worker-1")
    claimed = bus.claim_pending("inference.logged", "cg-writer", "worker-2", min_idle_ms=0)
    store = InMemoryLogStore()

    accepted, rejected = process_messages(claimed, bus=bus, store=store)

    assert accepted == 1
    assert rejected == 0
    assert len(store.logs) == 1
    assert bus.claim_pending("inference.logged", "cg-writer", "worker-2", min_idle_ms=0) == []


def test_log_writer_dedupes_republished_events_with_new_created_at(monkeypatch) -> None:
    monkeypatch.delenv("PRISM_KEEP_RAW", raising=False)
    bus = InMemoryBus()
    app.state.bus = bus
    TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})
    TestClient(app).post("/v1/events:batch", json={"events": [_event_payload()]})
    messages = bus.consume("inference.logged", "cg-writer", "worker-1", count=2)
    store = InMemoryLogStore()

    accepted, rejected = process_messages(messages, bus=bus, store=store)

    assert accepted == 2
    assert rejected == 0
    assert len(store.logs) == 1


def test_log_writer_dead_letters_invalid_messages() -> None:
    bus = InMemoryBus()
    store = InMemoryLogStore()
    bus.publish("inference.logged", {"schema_version": "1.0"})
    messages = bus.consume("inference.logged", "cg-writer", "worker-1")

    accepted, rejected = process_messages(messages, bus=bus, store=store)

    assert accepted == 0
    assert rejected == 1
    assert bus.streams["inference.dead"][0].event["dead_reason"].startswith("invalid event:")


def _event_payload() -> dict[str, object]:
    started = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    ended = started + timedelta(milliseconds=42)
    return {
        "schema_version": "1.0",
        "inference_id": "01935b3f-0000-7000-8000-000000000001",
        "conversation_id": "01935b3f-0000-7000-8000-000000000002",
        "message_id": "01935b3f-0000-7000-8000-000000000003",
        "model": "gpt-4o",
        "provider": "openai",
        "status": "ok",
        "error": None,
        "ts_start": started.isoformat().replace("+00:00", "Z"),
        "ts_end": ended.isoformat().replace("+00:00", "Z"),
        "latency_ms": 42,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "prompt_preview": "email foo@example.com ssn 123-45-6789",
        "response_preview": "call 415-555-2671",
        "raw_payload": {"request": {"text": "pay with 4111 1111 1111 1111"}},
        "metadata": {"test": True},
        "sdk_version": "0.1.0",
    }


def _tool_event_payload() -> dict[str, object]:
    started = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
    ended = started + timedelta(milliseconds=12)
    return {
        "schema_version": "1.0",
        "event_type": "tool_invocation",
        "tool_invocation_id": "01935b3f-0000-7000-8000-000000000010",
        "conversation_id": "01935b3f-0000-7000-8000-000000000002",
        "inference_id": "01935b3f-0000-7000-8000-000000000001",
        "tool_name": "web_search",
        "arguments_preview": '{"email":"foo@example.com"}',
        "result_preview": "ssn 123-45-6789",
        "status": "ok",
        "error": None,
        "ts_start": started.isoformat().replace("+00:00", "Z"),
        "ts_end": ended.isoformat().replace("+00:00", "Z"),
        "latency_ms": 12,
        "metadata": {"test": True},
        "sdk_version": "0.2.0",
    }
