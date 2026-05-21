from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import litellm

__version__ = "0.1.0"

log = logging.getLogger("prism-sdk")

Message = Mapping[str, Any]
Sink = Literal["http", "noop", "stdout"]


class PrismClient:
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
        self.chat = _Chat(self)

        if sink == "http":
            self._thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._thread.start()
        atexit.register(self.close)

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

    def _enqueue(self, event: dict[str, Any]) -> None:
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


class _Chat:
    def __init__(self, client: PrismClient) -> None:
        self.completions = _Completions(client)


class _Completions:
    def __init__(self, client: PrismClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        conversation_id: str,
        message_id: str,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        inference_id = str(uuid.uuid4())
        ts_start = datetime.now(UTC)
        monotonic_start = time.monotonic()
        try:
            response = litellm.completion(model=model, messages=list(messages), **kwargs)
            ts_end = datetime.now(UTC)
            content = _response_content(response)
            self._client._enqueue(
                _event(
                    inference_id=inference_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    model=model,
                    status="ok",
                    ts_start=ts_start,
                    ts_end=ts_end,
                    latency_ms=_elapsed_ms(monotonic_start),
                    messages=messages,
                    response_preview=content,
                    usage=_response_usage(response),
                    metadata=metadata or {},
                )
            )
            return response
        except Exception as exc:
            ts_end = datetime.now(UTC)
            self._client._enqueue(
                _event(
                    inference_id=inference_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    model=model,
                    status="error",
                    ts_start=ts_start,
                    ts_end=ts_end,
                    latency_ms=_elapsed_ms(monotonic_start),
                    messages=messages,
                    response_preview=None,
                    usage={},
                    metadata=metadata or {},
                    error={
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "provider_code": None,
                    },
                )
            )
            raise


def _event(
    *,
    inference_id: str,
    conversation_id: str,
    message_id: str,
    model: str,
    status: str,
    ts_start: datetime,
    ts_end: datetime,
    latency_ms: int,
    messages: Sequence[Message],
    response_preview: str | None,
    usage: dict[str, int | None],
    metadata: dict[str, Any],
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "inference_id": inference_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "model": model,
        "provider": _provider_from_model(model),
        "status": status,
        "error": error,
        "ts_start": _iso(ts_start),
        "ts_end": _iso(ts_end),
        "latency_ms": latency_ms,
        "ttft_ms": None,
        "usage": usage,
        "prompt_preview": _prompt_preview(messages),
        "response_preview": response_preview[:500] if response_preview else None,
        "raw_payload": None,
        "metadata": metadata,
        "sdk_version": __version__,
    }


def _elapsed_ms(monotonic_start: float) -> int:
    return max(0, round((time.monotonic() - monotonic_start) * 1000))


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _prompt_preview(messages: Sequence[Message]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            return str(content)[:500] if content is not None else None
    return None


def _provider_from_model(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("claude") or lowered.startswith("anthropic/"):
        return "anthropic"
    if lowered.startswith("gemini") or lowered.startswith("google/"):
        return "google"
    if lowered.startswith("azure/"):
        return "azure"
    return "openai"


def _response_content(response: Any) -> str:
    choice = _get(_get(response, "choices", [{}])[0], "message", {})
    return str(_get(choice, "content", "") or "")


def _response_usage(response: Any) -> dict[str, int | None]:
    usage = _get(response, "usage", {}) or {}
    return {
        "prompt_tokens": _get(usage, "prompt_tokens", None),
        "completion_tokens": _get(usage, "completion_tokens", None),
        "total_tokens": _get(usage, "total_tokens", None),
    }


def _get(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)
