from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from prism_sdk import PrismClient, __version__, _iso, _ms_between

try:
    from strands.hooks import (
        AfterToolCallEvent,
        BeforeToolCallEvent,
        HookProvider,
        HookRegistry,
    )
except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
    raise ImportError(
        "prism_sdk.strands requires the optional Strands dependency. "
        "Install strands-agents before importing this module."
    ) from exc


class PrismStrandsHooks(HookProvider):
    def __init__(self, client: PrismClient) -> None:
        self._client = client
        self._starts: dict[str, tuple[str, datetime]] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        self._starts[_tool_call_key(event.tool_use)] = (str(uuid.uuid4()), datetime.now(UTC))

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        end = datetime.now(UTC)
        tool_invocation_id, start = self._starts.pop(
            _tool_call_key(event.tool_use), (str(uuid.uuid4()), end)
        )
        invocation_state = event.invocation_state or {}
        exception = getattr(event, "exception", None)
        status = "error" if exception is not None or _result_is_error(event.result) else "ok"

        self._client.enqueue(
            {
                "schema_version": "1.0",
                "event_type": "tool_invocation",
                "tool_invocation_id": tool_invocation_id,
                "conversation_id": invocation_state.get("prism_conversation_id"),
                "inference_id": invocation_state.get("prism_inference_id"),
                "tool_name": _tool_name(event.tool_use),
                "arguments_preview": _preview(_tool_arguments(event.tool_use)),
                "result_preview": _preview(event.result),
                "status": status,
                "error": _error_payload(exception),
                "ts_start": _iso(start),
                "ts_end": _iso(end),
                "latency_ms": _ms_between(start, end),
                "metadata": invocation_state.get("prism_metadata") or {},
                "sdk_version": __version__,
            }
        )


def _tool_call_key(tool_use: Any) -> str:
    value = _get(tool_use, "toolUseId", None) or _get(tool_use, "tool_use_id", None)
    if value:
        return str(value)
    return str(uuid.uuid4())


def _tool_name(tool_use: Any) -> str:
    return str(_get(tool_use, "name", "unknown"))


def _tool_arguments(tool_use: Any) -> Any:
    return _get(tool_use, "input", {})


def _result_is_error(result: Any) -> bool:
    return _get(result, "status", None) == "error"


def _error_payload(exception: Exception | None) -> dict[str, str] | None:
    if exception is None:
        return None
    return {"type": type(exception).__name__, "message": str(exception)}


def _preview(value: Any) -> str:
    if isinstance(value, str):
        return value[:500]
    try:
        return json.dumps(value, default=str, separators=(",", ":"))[:500]
    except TypeError:
        return str(value)[:500]


def _get(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
