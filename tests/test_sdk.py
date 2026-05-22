from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import prism_sdk


def test_sdk_version() -> None:
    assert prism_sdk.__version__ == "0.2.0"


def test_metadata_helper_namespaces_under_prism() -> None:
    meta = prism_sdk.metadata(
        conversation_id="c1",
        message_id="m1",
        inference_id="i1",
        extra={"source": "test"},
    )
    assert meta == {
        "prism": {
            "conversation_id": "c1",
            "message_id": "m1",
            "inference_id": "i1",
            "extra": {"source": "test"},
        }
    }


def test_install_registers_callback_and_uninstall_removes_it(monkeypatch) -> None:
    monkeypatch.setattr(prism_sdk.litellm, "callbacks", [])
    client = prism_sdk.PrismClient(sink="noop")
    cb = client.install()
    assert cb in prism_sdk.litellm.callbacks
    # idempotent
    again = client.install()
    assert again is cb
    assert prism_sdk.litellm.callbacks.count(cb) == 1
    client.uninstall()
    assert cb not in prism_sdk.litellm.callbacks


class _CapturingClient(prism_sdk.PrismClient):
    def __init__(self) -> None:
        super().__init__(sink="noop")
        self.captured: list[dict] = []

    def enqueue(self, event):
        self.captured.append(event)


def _kwargs(metadata_value) -> dict:
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "ping"}],
        "metadata": metadata_value,
    }


def test_callback_success_event_builds_inference_event() -> None:
    client = _CapturingClient()
    captured = client.captured
    cb = prism_sdk.PrismCallback(client)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=842)
    response = {
        "choices": [{"message": {"content": "pong"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }
    meta = prism_sdk.metadata(conversation_id="c1", message_id="m1", inference_id="inf-1")

    cb.log_success_event(_kwargs(meta), response, start, end)

    assert len(captured) == 1
    event = captured[0]
    assert event["status"] == "ok"
    assert event["inference_id"] == "inf-1"
    assert event["conversation_id"] == "c1"
    assert event["message_id"] == "m1"
    assert event["model"] == "gpt-4o"
    assert event["latency_ms"] == 842
    assert event["ttft_ms"] is None
    assert event["usage"]["total_tokens"] == 6
    assert event["response_preview"] == "pong"
    assert event["prompt_preview"] == "ping"


def test_callback_failure_event_captures_exception() -> None:
    client = _CapturingClient()
    captured = client.captured
    cb = prism_sdk.PrismCallback(client)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=100)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c", message_id="m"))
    kwargs["exception"] = RuntimeError("boom")

    cb.log_failure_event(kwargs, None, start, end)

    assert len(captured) == 1
    event = captured[0]
    assert event["status"] == "error"
    assert event["error"]["type"] == "RuntimeError"
    assert event["error"]["message"] == "boom"
    assert event["response_preview"] is None


def test_callback_streaming_extracts_ttft_from_completion_start_time() -> None:
    client = _CapturingClient()
    captured = client.captured
    cb = prism_sdk.PrismCallback(client)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    first_token = start + timedelta(milliseconds=120)
    end = start + timedelta(milliseconds=842)

    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c", message_id="m"))
    kwargs["completion_start_time"] = first_token
    response = {"choices": [{"message": {"content": "streamed reply"}}], "usage": {}}

    cb.log_success_event(kwargs, response, start, end)

    assert captured[0]["ttft_ms"] == 120
    assert captured[0]["latency_ms"] == 842


def test_callback_skips_calls_without_prism_metadata() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    response = {"choices": [{"message": {"content": "leaked"}}], "usage": {}}

    cb.log_success_event({"model": "gpt-4o", "messages": []}, response, start, end)
    cb.log_success_event({"model": "gpt-4o", "messages": [], "metadata": {}}, response, start, end)
    cb.log_success_event(
        {"model": "gpt-4o", "messages": [], "metadata": {"prism": {}}},
        response,
        start,
        end,
    )
    cb.log_success_event(
        {
            "model": "gpt-4o",
            "messages": [],
            "metadata": {"prism": {"conversation_id": "c1"}},
        },
        response,
        start,
        end,
    )
    cb.log_failure_event(
        {"model": "gpt-4o", "messages": [], "exception": RuntimeError("boom")},
        None,
        start,
        end,
    )

    assert client.captured == []


def test_async_callback_delegates_to_sync_path() -> None:
    client = _CapturingClient()
    captured = client.captured
    cb = prism_sdk.PrismCallback(client)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c", message_id="m"))
    response = {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    asyncio.run(cb.async_log_success_event(kwargs, response, start, end))

    assert len(captured) == 1
    assert captured[0]["status"] == "ok"
