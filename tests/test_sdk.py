from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

import prism_sdk
from prism_sdk.strands import PrismStrandsHooks


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


def test_metadata_helper_can_carry_explicit_provider() -> None:
    meta = prism_sdk.metadata(conversation_id="c1", message_id="m1", provider="bedrock")

    assert meta["prism"]["provider"] == "bedrock"


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


class _ToolEvent:
    def __init__(self, *, result=None, exception=None) -> None:
        self.tool_use = {
            "toolUseId": "toolu_1",
            "name": "web_search",
            "input": {"query": "foo@example.com"},
        }
        self.invocation_state = {
            "prism_conversation_id": "c1",
            "prism_inference_id": "00000000-0000-7000-8000-000000000001",
            "prism_metadata": {"source": "test"},
        }
        self.result = result or {"status": "success", "content": [{"text": "ok"}]}
        self.exception = exception


def _kwargs(metadata_value) -> dict:
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "ping"}],
        "metadata": metadata_value,
    }


def test_strands_hooks_emit_tool_invocation_event() -> None:
    client = _CapturingClient()
    hooks = PrismStrandsHooks(client)
    event = _ToolEvent()

    hooks.before_tool_call(cast(BeforeToolCallEvent, event))
    hooks.after_tool_call(cast(AfterToolCallEvent, event))

    assert len(client.captured) == 1
    captured = client.captured[0]
    assert captured["event_type"] == "tool_invocation"
    assert captured["conversation_id"] == "c1"
    assert captured["inference_id"] == "00000000-0000-7000-8000-000000000001"
    assert captured["tool_name"] == "web_search"
    assert captured["arguments_preview"] == '{"query":"foo@example.com"}'
    assert captured["status"] == "ok"
    assert captured["latency_ms"] >= 0


def test_strands_hooks_emit_tool_errors() -> None:
    client = _CapturingClient()
    hooks = PrismStrandsHooks(client)
    event = _ToolEvent(exception=RuntimeError("boom"))

    hooks.before_tool_call(cast(BeforeToolCallEvent, event))
    hooks.after_tool_call(cast(AfterToolCallEvent, event))

    captured = client.captured[0]
    assert captured["status"] == "error"
    assert captured["error"] == {"type": "RuntimeError", "message": "boom"}


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


def test_callback_prefers_explicit_prism_provider_over_model_fallback() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c1", message_id="m1", provider="bedrock"))
    kwargs["model"] = "gpt-4o"

    cb.log_success_event(kwargs, {"choices": [{"message": {"content": "ok"}}]}, start, end)

    assert client.captured[0]["provider"] == "bedrock"


def test_callback_uses_litellm_provider_when_metadata_has_none() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c1", message_id="m1"))
    kwargs["litellm_params"] = {
        "metadata": kwargs.pop("metadata"),
        "custom_llm_provider": "bedrock",
    }

    cb.log_success_event(kwargs, {"choices": [{"message": {"content": "ok"}}]}, start, end)

    assert client.captured[0]["provider"] == "bedrock"


def test_callback_classifies_bedrock_models_as_bedrock_provider() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c1", message_id="m1"))
    kwargs["model"] = (
        "bedrock/converse/"
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/hnxtndg2c380"
    )

    cb.log_success_event(kwargs, {"choices": [{"message": {"content": "ok"}}]}, start, end)

    assert client.captured[0]["provider"] == "bedrock"


def test_callback_restores_bedrock_prefix_when_litellm_reports_converse_arn() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c1", message_id="m1"))
    kwargs["model"] = (
        "converse/arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/hnxtndg2c380"
    )

    cb.log_success_event(kwargs, {"choices": [{"message": {"content": "ok"}}]}, start, end)

    assert client.captured[0]["model"].startswith("bedrock/converse/arn:aws:bedrock:")
    assert client.captured[0]["provider"] == "bedrock"


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


def test_callback_captures_cost_and_token_breakdown() -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    response = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens_details": {"reasoning_tokens": 8},
        },
    }
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c", message_id="m"))
    kwargs["response_cost"] = 0.00123

    cb.log_success_event(kwargs, response, start, end)

    event = client.captured[0]
    assert event["cost_usd"] == 0.00123
    assert event["usage"]["cached_prompt_tokens"] == 40
    assert event["usage"]["reasoning_tokens"] == 8


def test_callback_falls_back_to_completion_cost(monkeypatch) -> None:
    client = _CapturingClient()
    cb = prism_sdk.PrismCallback(client)
    monkeypatch.setattr(prism_sdk.litellm, "completion_cost", lambda **_: 0.0042)

    start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=10)
    response = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1}}
    kwargs = _kwargs(prism_sdk.metadata(conversation_id="c", message_id="m"))

    cb.log_success_event(kwargs, response, start, end)

    assert client.captured[0]["cost_usd"] == 0.0042


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
