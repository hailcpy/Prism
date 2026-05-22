from __future__ import annotations

import atexit
import logging
import queue
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import litellm
from litellm.integrations.custom_logger import CustomLogger

__version__ = "0.2.0"

log = logging.getLogger("prism-sdk")

Message = Mapping[str, Any]
Sink = Literal["http", "noop", "stdout"]


def metadata(
    *,
    conversation_id: str,
    message_id: str,
    inference_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the value to pass as `metadata=` to `litellm.completion(...)`.

    The chatbot (or any caller) must attach this so prism can correlate the
    captured inference back to a chat message. `extra` ends up verbatim in
    inference_logs.metadata_jsonb.
    """
    return {
        "prism": {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "inference_id": inference_id,
            "extra": extra or {},
        }
    }


class PrismClient:
    """Owns the queue, flusher thread, HTTP transport, and lifecycle.

    Does NOT call litellm itself. Call `install()` to register a
    `PrismCallback` on `litellm.callbacks`; the callback enqueues
    InferenceEvents and this client flushes them to the ingestion API.
    """

    def __init__(
        self,
        *,
        ingestion_url: str = "http://localhost:8001",
        api_key: str | None = None,
        sink: Sink = "http",
        flush_interval_ms: int = 200,
        queue_max: int = 10_000,
        on_drop: Literal["log", "raise"] = "log",
    ) -> None:
        self.ingestion_url = ingestion_url.rstrip("/")
        self.api_key = api_key
        self.sink = sink
        self.flush_interval_s = flush_interval_ms / 1000
        self.on_drop = on_drop
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_max)
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback: PrismCallback | None = None

        if sink == "http":
            self._thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._thread.start()
        atexit.register(self.close)

    def install(self) -> PrismCallback:
        if self._callback is None:
            self._callback = PrismCallback(self)
            litellm.callbacks.append(self._callback)
        return self._callback

    def uninstall(self) -> None:
        if self._callback is not None:
            try:
                litellm.callbacks.remove(self._callback)
            except ValueError:
                pass
        self._callback = None

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.flush()

    def flush(self) -> None:
        batch = self._drain(max_items=100)
        while batch:
            self._emit_batch(batch)
            batch = self._drain(max_items=100)

    def enqueue(self, event: dict[str, Any]) -> None:
        if self.sink == "noop":
            return
        if self.sink == "stdout":
            print(event)
            return
        try:
            self._queue.put_nowait(event)
            return
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            if self.on_drop == "raise":
                raise
            log.warning("prism event queue full; dropped oldest event")
            self._queue.put_nowait(event)

    def _flush_loop(self) -> None:
        while not self._closed.wait(self.flush_interval_s):
            batch = self._drain(max_items=100)
            if batch:
                self._emit_batch(batch)

    def _drain(self, *, max_items: int) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        for _ in range(max_items):
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _emit_batch(self, batch: list[dict[str, Any]]) -> None:
        if self.sink != "http":
            return
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            with httpx.Client(timeout=5) as client:
                response = client.post(
                    f"{self.ingestion_url}/v1/events:batch",
                    json={"events": batch},
                    headers=headers,
                )
                if response.status_code in {429, 503}:
                    self._requeue(batch)
                elif response.status_code >= 400:
                    log.warning(
                        "dropping prism batch after ingestion returned %s", response.status_code
                    )
        except httpx.HTTPError:
            self._requeue(batch)

    def _requeue(self, batch: list[dict[str, Any]]) -> None:
        for event in batch:
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                log.warning("prism event queue full during retry; dropping event")
                break


class PrismCallback(CustomLogger):
    """LiteLLM custom logger that builds InferenceEvents and enqueues them.

    LiteLLM invokes log_success_event / log_failure_event with:
      kwargs       - original call args (model, messages, metadata, exception?)
      response_obj - assembled response (even for streaming)
      start_time, end_time - datetimes; for streaming, kwargs also includes
                             "completion_start_time" (first-token time).
    """

    def __init__(self, client: PrismClient) -> None:
        self._client = client

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        event = _build_event(
            kwargs=kwargs,
            response_obj=response_obj,
            start_time=start_time,
            end_time=end_time,
            status="ok",
            error=None,
        )
        if event is not None:
            self._client.enqueue(event)

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        exc = kwargs.get("exception")
        event = _build_event(
            kwargs=kwargs,
            response_obj=response_obj,
            start_time=start_time,
            end_time=end_time,
            status="error",
            error={
                "type": type(exc).__name__ if exc is not None else "UnknownError",
                "message": str(exc) if exc is not None else "",
                "provider_code": None,
            },
        )
        if event is not None:
            self._client.enqueue(event)

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


def _build_event(
    *,
    kwargs: dict[str, Any],
    response_obj: Any,
    start_time: datetime,
    end_time: datetime,
    status: str,
    error: dict[str, Any] | None,
) -> dict[str, Any] | None:
    prism_meta = ((kwargs.get("litellm_params") or {}).get("metadata") or {}).get("prism")
    if prism_meta is None:
        prism_meta = (kwargs.get("metadata") or {}).get("prism")
    if not isinstance(prism_meta, dict):
        return None
    if not prism_meta.get("conversation_id") or not prism_meta.get("message_id"):
        return None
    completion_start = kwargs.get("completion_start_time")
    ttft_ms = _ms_between(start_time, completion_start) if completion_start else None
    model = kwargs.get("model", "") or ""
    messages = kwargs.get("messages") or []
    inference_id = prism_meta.get("inference_id") or str(uuid.uuid4())

    return {
        "schema_version": "1.0",
        "inference_id": inference_id,
        "conversation_id": prism_meta.get("conversation_id"),
        "message_id": prism_meta.get("message_id"),
        "model": model,
        "provider": _provider_from_model(model),
        "status": status,
        "error": error,
        "ts_start": _iso(_ensure_utc(start_time)),
        "ts_end": _iso(_ensure_utc(end_time)),
        "latency_ms": _ms_between(start_time, end_time),
        "ttft_ms": ttft_ms,
        "usage": _response_usage(response_obj),
        "prompt_preview": _prompt_preview(messages),
        "response_preview": _response_preview(response_obj),
        "raw_payload": None,
        "metadata": prism_meta.get("extra") or {},
        "sdk_version": __version__,
    }


def _ms_between(a: datetime, b: datetime) -> int:
    delta = b - a
    if isinstance(delta, timedelta):
        seconds = delta.total_seconds()
    else:
        seconds = float(delta)
    return max(0, round(seconds * 1000))


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _prompt_preview(messages: Sequence[Message]) -> str | None:
    for message in reversed(list(messages)):
        if _get(message, "role", None) == "user":
            content = _get(message, "content", None)
            return str(content)[:500] if content is not None else None
    return None


def _response_preview(response_obj: Any) -> str | None:
    if response_obj is None:
        return None
    choices = _get(response_obj, "choices", []) or []
    if not choices:
        return None
    message = _get(choices[0], "message", None)
    if message is None:
        delta = _get(choices[0], "delta", None)
        if delta is None:
            return None
        content = _get(delta, "content", None)
    else:
        content = _get(message, "content", None)
    return str(content)[:500] if content else None


def _response_usage(response_obj: Any) -> dict[str, int | None]:
    usage = _get(response_obj, "usage", None) or {}
    return {
        "prompt_tokens": _get(usage, "prompt_tokens", None),
        "completion_tokens": _get(usage, "completion_tokens", None),
        "total_tokens": _get(usage, "total_tokens", None),
    }


def _provider_from_model(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("claude") or lowered.startswith("anthropic/"):
        return "anthropic"
    if lowered.startswith("gemini") or lowered.startswith("google/"):
        return "google"
    if lowered.startswith("azure/"):
        return "azure"
    return "openai"


def _get(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)
